"""隔离 dataset run、不可变终态与 approved-local 原子指针。"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .dataset_identity import (
    content_digest,
    describe_payload_files,
    generator_state,
    sha256_file,
    unmanaged_payload_paths,
)
from .full_refresh import RefreshResult

RUN_SCHEMA_VERSION = 1
COMPLETION_SCHEMA_VERSION = 1
POINTER_SCHEMA_VERSION = 1
APPROVED_POINTER_NAME = "approved-local.json"
PROMOTION_LOCK_NAME = "approved-local.lock"


@dataclass(frozen=True, slots=True)
class DatasetRun:
    """位于 staging 的单次隔离运行。"""

    runs_root: Path
    run_id: str
    staging_root: Path

    @property
    def work_root(self) -> Path:
        """返回本 run 的 decomposition/update 临时根。"""

        return self.staging_root / "work"

    @property
    def normalized_root(self) -> Path:
        """返回本 run 的 normalized candidate 根。"""

        return self.staging_root / "normalized"

    @property
    def report_root(self) -> Path:
        """返回本 run 的验证与指标报告根。"""

        return self.staging_root / "reports"


@dataclass(frozen=True, slots=True)
class CompletedDatasetRun:
    """已从 staging 固化到 completed 的不可变运行。"""

    runs_root: Path
    run_id: str
    root: Path
    dataset_id: str
    content_digest: str
    quality_gate: str

    @property
    def normalized_root(self) -> Path:
        """返回该 completed run 的 normalized 数据根。"""

        return self.root / "normalized"


class DatasetRunError(RuntimeError):
    """dataset run 生命周期或终态不合法。"""


class PromotionConflictError(DatasetRunError):
    """approved pointer 与调用方观察到的前态不一致。"""


class PromotionLockedError(DatasetRunError):
    """另一 promotion 正持有独占锁。"""


def create_dataset_run(
    runs_root: Path,
    *,
    repo_root: Path,
    command: Sequence[str],
    now: datetime | None = None,
) -> DatasetRun:
    """原子创建唯一 staging run，并以 O_EXCL 写入 immutable run.json。"""

    try:
        state = generator_state(repo_root)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DatasetRunError(f"无法读取 generator Git 状态: {repo_root}: {exc}") from exc

    runs_root = runs_root.resolve()
    staging_parent = runs_root / ".staging"
    (runs_root / "completed").mkdir(parents=True, exist_ok=True)
    (runs_root / "failed").mkdir(parents=True, exist_ok=True)
    staging_parent.mkdir(parents=True, exist_ok=True)

    created_at = now or datetime.now(UTC)
    for _attempt in range(16):
        run_id = _new_run_id(created_at)
        staging_root = staging_parent / run_id
        try:
            staging_root.mkdir()
        except FileExistsError:
            continue

        run = DatasetRun(runs_root=runs_root, run_id=run_id, staging_root=staging_root)
        document = {
            "schema_version": RUN_SCHEMA_VERSION,
            "run_id": run_id,
            "status": "staging",
            "created_at": created_at.isoformat(),
            "repo_root": str(repo_root.resolve()),
            "generator_state_at_start": state.to_dict(),
            "command": list(command),
            "paths": {
                "work": str(run.work_root),
                "normalized": str(run.normalized_root),
                "reports": str(run.report_root),
            },
        }
        run_path = staging_root / "run.json"
        try:
            _write_json_exclusive(run_path, document)
        except OSError:
            try:
                run_path.unlink(missing_ok=True)
                staging_root.rmdir()
            except OSError:
                pass
            raise
        return run

    raise DatasetRunError("连续 16 次无法创建唯一 dataset run id")


def complete_dataset_run(
    run: DatasetRun,
    result: RefreshResult,
    *,
    elapsed_seconds: float,
    promotion_eligibility: Mapping[str, object] | None = None,
    now: datetime | None = None,
) -> CompletedDatasetRun:
    """写 completion.json 后把 staging run 同卷固化为 completed。"""

    _require_staging_run(run)
    if not result.ok:
        raise DatasetRunError("阻断步骤失败的 run 不能进入 completed")

    manifest = _read_json_object(run.normalized_root / "manifest.json")
    dataset_id = _required_string(manifest, "dataset_id", "normalized manifest")
    content_digest = _required_digest(manifest, "content_digest", "normalized manifest")
    report_files = _describe_report_files(run.report_root)
    destination = run.runs_root / "completed" / run.run_id
    if destination.exists():
        raise DatasetRunError(f"completed run 已存在，拒绝覆盖: {destination}")
    non_blocking_failures = [step.name for step in result.results if not step.blocking and step.exit_code != 0]
    quality_gate = "passed" if not non_blocking_failures else "passed_with_non_blocking_failures"
    eligibility = (
        dict(promotion_eligibility)
        if promotion_eligibility is not None
        else {
            "eligible": False,
            "gate_version": None,
            "reason": "absolute quality gates not evaluated",
        }
    )
    if not isinstance(eligibility.get("eligible"), bool):
        raise DatasetRunError("promotion_eligibility.eligible 必须是 bool")
    completed_at = now or datetime.now(UTC)
    document = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "run_id": run.run_id,
        "status": "completed",
        "completed_at": completed_at.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "dataset_id": dataset_id,
        "content_digest": content_digest,
        "normalized_manifest_sha256": sha256_file(run.normalized_root / "manifest.json"),
        "generator_state": _manifest_generator_state(manifest),
        "quality_gate": quality_gate,
        "promotion_eligibility": eligibility,
        "non_blocking_failures": non_blocking_failures,
        "steps": _step_documents(result),
        "reports": report_files,
    }
    completion_path = run.staging_root / "completion.json"
    _write_json_exclusive(completion_path, document)

    try:
        run.staging_root.rename(destination)
    except OSError:
        try:
            completion_path.unlink()
        except OSError as rollback_error:
            raise DatasetRunError(
                f"completed rename 失败且无法回滚 completion marker: {run.staging_root}"
            ) from rollback_error
        raise
    _fsync_directory(destination.parent)
    return CompletedDatasetRun(
        runs_root=run.runs_root,
        run_id=run.run_id,
        root=destination,
        dataset_id=dataset_id,
        content_digest=content_digest,
        quality_gate=quality_gate,
    )


def fail_dataset_run(
    run: DatasetRun,
    *,
    result: RefreshResult | None,
    error_code: str,
    message: str,
    elapsed_seconds: float,
    now: datetime | None = None,
) -> Path:
    """为失败 run 写唯一 failure.json，并固化到 failed 目录保留诊断。"""

    _require_staging_run(run)
    destination = run.runs_root / "failed" / run.run_id
    if destination.exists():
        raise DatasetRunError(f"failed run 已存在，拒绝覆盖: {destination}")
    failed_at = now or datetime.now(UTC)
    document = {
        "schema_version": COMPLETION_SCHEMA_VERSION,
        "run_id": run.run_id,
        "status": "failed",
        "failed_at": failed_at.isoformat(),
        "elapsed_seconds": elapsed_seconds,
        "error_code": error_code,
        "message": message,
        "steps": _step_documents(result) if result is not None else [],
    }
    failure_path = run.staging_root / "failure.json"
    _write_json_exclusive(failure_path, document)

    try:
        run.staging_root.rename(destination)
    except OSError:
        try:
            failure_path.unlink()
        except OSError as rollback_error:
            raise DatasetRunError(
                f"failed rename 失败且无法回滚 failure marker: {run.staging_root}"
            ) from rollback_error
        raise
    _fsync_directory(destination.parent)
    return destination


def load_completed_run(runs_root: Path, run_id: str) -> CompletedDatasetRun:
    """读取并校验 completed run、manifest、completion 与报告摘要闭包。"""

    _validate_run_id(run_id)
    runs_root = runs_root.resolve()
    root = runs_root / "completed" / run_id
    completion = _read_json_object(root / "completion.json")
    manifest = _read_json_object(root / "normalized" / "manifest.json")
    if completion.get("schema_version") != COMPLETION_SCHEMA_VERSION or completion.get("status") != "completed":
        raise DatasetRunError(f"completion.json 状态无效: {root}")
    if completion.get("run_id") != run_id:
        raise DatasetRunError("completion run_id 与目录不一致")

    dataset_id = _required_string(manifest, "dataset_id", "normalized manifest")
    content_digest = _required_digest(manifest, "content_digest", "normalized manifest")
    if completion.get("dataset_id") != dataset_id or completion.get("content_digest") != content_digest:
        raise DatasetRunError("completion 与 normalized manifest 身份不一致")
    if completion.get("normalized_manifest_sha256") != sha256_file(root / "normalized" / "manifest.json"):
        raise DatasetRunError("normalized manifest 已在 completion 后被修改")
    if completion.get("generator_state") != _manifest_generator_state(manifest):
        raise DatasetRunError("completion 与 normalized manifest 的 generator state 不一致")
    _verify_report_files(root / "reports", completion.get("reports"))
    quality_gate = _required_string(completion, "quality_gate", "completion")
    return CompletedDatasetRun(
        runs_root=runs_root,
        run_id=run_id,
        root=root,
        dataset_id=dataset_id,
        content_digest=content_digest,
        quality_gate=quality_gate,
    )


def promote_completed_run(
    runs_root: Path,
    run_id: str,
    *,
    expected_previous_digest: str | None,
    now: datetime | None = None,
) -> dict[str, object]:
    """在独占锁内用 CAS 与原子替换提升一个 clean completed run。"""

    runs_root = runs_root.resolve()
    with promotion_lock(runs_root):
        # 候选校验与 pointer CAS 必须处于同一锁边界，避免校验后、替换前被修改。
        completed = load_completed_run(runs_root, run_id)
        completion = _read_json_object(completed.root / "completion.json")
        manifest = _read_json_object(completed.normalized_root / "manifest.json")
        _verify_normalized_payload(completed.normalized_root, manifest)
        generator = _manifest_generator_state(manifest)
        if generator["dirty"] is not False:
            raise DatasetRunError("dirty generator candidate 不得 promotion")
        if completed.quality_gate != "passed":
            raise DatasetRunError(f"quality gate 未完全通过，不得 promotion: {completed.quality_gate}")
        eligibility = completion.get("promotion_eligibility")
        if not isinstance(eligibility, dict) or eligibility.get("eligible") is not True:
            reason = eligibility.get("reason") if isinstance(eligibility, dict) else "missing attestation"
            raise DatasetRunError(f"candidate 尚无 promotion eligibility: {reason}")
        if completion.get("non_blocking_failures") != []:
            raise DatasetRunError("含 non-blocking failure 的 candidate 不得 promotion")

        current = read_approved_pointer(runs_root, required=False)
        actual_previous = _pointer_digest(current)
        if actual_previous != expected_previous_digest:
            raise PromotionConflictError(
                f"approved pointer 已变化: expected={expected_previous_digest!r} actual={actual_previous!r}"
            )

        promoted_at = now or datetime.now(UTC)
        pointer: dict[str, object] = {
            "schema_version": POINTER_SCHEMA_VERSION,
            "status": "approved_local",
            "run_id": run_id,
            "dataset_id": completed.dataset_id,
            "content_digest": completed.content_digest,
            "generator_commit": generator["commit"],
            "relative_dataset_path": f"completed/{run_id}/normalized",
            "promoted_at": promoted_at.isoformat(),
            "previous_content_digest": actual_previous,
        }
        _replace_json_atomically(runs_root / APPROVED_POINTER_NAME, pointer)
        return pointer


def read_approved_pointer(runs_root: Path, *, required: bool = True) -> dict[str, Any] | None:
    """读取 approved-local 指针；读取方只会看到旧文件或完整新文件。"""

    path = runs_root / APPROVED_POINTER_NAME
    if not path.is_file():
        if required:
            raise DatasetRunError(f"尚无 approved-local pointer: {path}")
        return None
    pointer = _read_json_object(path)
    if pointer.get("schema_version") != POINTER_SCHEMA_VERSION or pointer.get("status") != "approved_local":
        raise DatasetRunError(f"approved-local pointer 状态无效: {path}")
    return pointer


def resolve_approved_dataset(runs_root: Path) -> Path:
    """解析 approved-local 指针并复核 completed/manifest 身份。"""

    runs_root = runs_root.resolve()
    pointer = read_approved_pointer(runs_root)
    assert pointer is not None
    run_id = _required_string(pointer, "run_id", "approved pointer")
    relative = _required_string(pointer, "relative_dataset_path", "approved pointer")
    expected_relative = f"completed/{run_id}/normalized"
    if relative != expected_relative:
        raise DatasetRunError("approved pointer dataset path 与 run_id 不一致")
    completed = load_completed_run(runs_root, run_id)
    if pointer.get("dataset_id") != completed.dataset_id or pointer.get("content_digest") != completed.content_digest:
        raise DatasetRunError("approved pointer 与 completed run 身份不一致")
    dataset_root = (runs_root / Path(relative)).resolve()
    try:
        dataset_root.relative_to(runs_root)
    except ValueError as exc:
        raise DatasetRunError("approved pointer 路径逃逸 runs root") from exc
    return dataset_root


@contextmanager
def promotion_lock(runs_root: Path) -> Iterator[None]:
    """用 O_EXCL lock file 串行 promotion，锁存在时 fail-closed。"""

    runs_root.mkdir(parents=True, exist_ok=True)
    lock_path = runs_root / PROMOTION_LOCK_NAME
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise PromotionLockedError(f"promotion lock 已存在: {lock_path}") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            file.write(f"pid={os.getpid()}\n")
            file.flush()
            os.fsync(file.fileno())
        yield
    finally:
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _describe_report_files(report_root: Path) -> list[dict[str, object]]:
    required = {"normalized-validation.json", "quality-metrics.json"}
    if (report_root / "quality-gates.json").is_file():
        required.update({"quality-diff.json", "quality-gates.json"})
    actual_paths = sorted(
        (path for path in report_root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(report_root).as_posix(),
    )
    actual_names = {path.relative_to(report_root).as_posix() for path in actual_paths}
    missing = sorted(required - actual_names)
    if missing:
        raise DatasetRunError(f"completed run 缺少必需报告: {', '.join(missing)}")

    files: list[dict[str, object]] = []
    for path in actual_paths:
        relative = path.relative_to(report_root).as_posix()
        files.append({"path": relative, "bytes": path.stat().st_size, "sha256": sha256_file(path)})
    return files


def _verify_normalized_payload(normalized_root: Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    identity = manifest.get("content_identity")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise DatasetRunError("normalized manifest files 形状无效")
    if not isinstance(identity, dict):
        raise DatasetRunError("normalized manifest content_identity 形状无效")
    actual_files = describe_payload_files(normalized_root)
    unmanaged = unmanaged_payload_paths(actual_files)
    if unmanaged:
        raise DatasetRunError(f"normalized candidate 含未管理文件: {', '.join(unmanaged)}")
    if files != actual_files:
        raise DatasetRunError("normalized candidate payload 已在 validation 后变化")
    expected_digest = content_digest(identity, files)
    if manifest.get("content_digest") != expected_digest:
        raise DatasetRunError("normalized candidate content_digest 无效")
    if manifest.get("dataset_id") != f"local-v1-{expected_digest[:16]}":
        raise DatasetRunError("normalized candidate dataset_id 无效")


def _verify_report_files(report_root: Path, value: object) -> None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise DatasetRunError("completion reports 形状无效")
    if value != _describe_report_files(report_root):
        raise DatasetRunError("completion reports 已过期或被修改")


def _manifest_generator_state(manifest: Mapping[str, Any]) -> dict[str, object]:
    identity = manifest.get("content_identity")
    input_identity = identity.get("input_identity") if isinstance(identity, dict) else None
    state = input_identity.get("generator_state") if isinstance(input_identity, dict) else None
    commit = state.get("commit") if isinstance(state, dict) else None
    dirty = state.get("dirty") if isinstance(state, dict) else None
    if not isinstance(commit, str) or not isinstance(dirty, bool):
        raise DatasetRunError("normalized manifest 缺少合法 generator state")
    return {"commit": commit, "dirty": dirty}


def _step_documents(result: RefreshResult | None) -> list[dict[str, object]]:
    if result is None:
        return []
    return [
        {
            "name": step.name,
            "argv": step.argv,
            "exit_code": step.exit_code,
            "blocking": step.blocking,
        }
        for step in result.results
    ]


def _write_json_exclusive(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def _replace_json_atomically(path: Path, document: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    try:
        _write_json_exclusive(temp, document)
        os.replace(temp, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetRunError(f"无法读取 JSON object: {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise DatasetRunError(f"JSON 必须是 object: {path}")
    return document


def _required_string(document: Mapping[str, Any], key: str, source: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise DatasetRunError(f"{source} 缺少 {key}")
    return value


def _required_digest(document: Mapping[str, Any], key: str, source: str) -> str:
    value = _required_string(document, key, source)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise DatasetRunError(f"{source} 的 {key} 不是 SHA-256")
    return value


def _pointer_digest(pointer: Mapping[str, Any] | None) -> str | None:
    if pointer is None:
        return None
    return _required_digest(pointer, "content_digest", "approved pointer")


def _require_staging_run(run: DatasetRun) -> None:
    expected_parent = (run.runs_root / ".staging").resolve()
    root = run.staging_root.resolve()
    if root.parent != expected_parent or root.name != run.run_id or not root.is_dir():
        raise DatasetRunError(f"run 不在预期 staging root: {root}")
    _validate_run_id(run.run_id)


def _validate_run_id(run_id: str) -> None:
    if (
        not run_id
        or run_id in {".", ".."}
        or Path(run_id).name != run_id
        or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_." for char in run_id)
    ):
        raise DatasetRunError(f"run_id 含非法字符: {run_id!r}")


def _new_run_id(now: datetime) -> str:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{secrets.token_hex(5)}"
