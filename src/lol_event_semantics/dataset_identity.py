"""V1 normalized 数据集的确定性输入与内容身份。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .advisory_lock import AdvisoryLockUnavailableError, advisory_file_lock
from .raw_events import is_excluded_event_name
from .reference_collection import read_yaml

TREE_DIGEST_VERSION = "lol-event-semantics.tree-sha256.v1"
CONTENT_DIGEST_VERSION = "lol-event-semantics.content-digest.v1"
INPUT_IDENTITY_VERSION = "lol-event-semantics.input-identity.v1"
REVIEW_DECISION_IDENTITY_VERSION = "lol-event-semantics.review-decision-identity.v1"
REVIEW_DECISION_SOURCE_NAME = "review-decisions.v1.json"
REVIEW_TRANSACTION_DIR_NAME = ".review-source-transaction"
REVIEW_TRANSACTION_LOCK_NAME = ".review-source-transaction.lock"
REVIEW_DECISION_IDENTITY_FIELDS = (
    "decision_schema_version",
    "decision_id",
    "target_type",
    "target_id",
    "target_content_hash",
    "action",
    "payload",
)
FROZEN_PROFILE_SCHEMA_VERSION = 1
_IGNORED_TREE_PARTS = frozenset({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
_IGNORED_TREE_SUFFIXES = frozenset({".pyc", ".pyo"})
_VERSION_PREFIX = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.|$)")
_PAYLOAD_TOP_LEVEL_FILES = frozenset(
    {"normalized-events.jsonl", "pattern-catalog.json", "quality-summary.json", "entity-summary.json"}
)


@dataclass(frozen=True, slots=True)
class TreeDigest:
    """规范化目录树摘要及规模统计。"""

    digest: str
    file_count: int
    byte_count: int

    def to_dict(self) -> dict[str, object]:
        """返回可直接写入 manifest 的稳定字典。"""

        return {
            "algorithm": TREE_DIGEST_VERSION,
            "digest": self.digest,
            "file_count": self.file_count,
            "byte_count": self.byte_count,
        }


@dataclass(frozen=True, slots=True)
class GeneratorState:
    """生成器 Git revision 与工作树状态。"""

    commit: str
    dirty: bool

    def to_dict(self) -> dict[str, object]:
        """返回 manifest 形式。"""

        return {"commit": self.commit, "dirty": self.dirty}


def canonical_json_bytes(value: object) -> bytes:
    """把 JSON 值编码为无空白、排序键、UTF-8 的规范字节。"""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_file(path: Path) -> str:
    """返回文件原始字节的 SHA-256。"""

    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path) -> TreeDigest:
    """按相对 POSIX 路径和原始文件字节计算目录树摘要。"""

    if not root.is_dir():
        raise FileNotFoundError(f"identity tree root 不存在或不是目录: {root}")
    return paths_digest(root, [path for path in root.rglob("*") if _is_identity_file(path)])


def rules_tree_digest(rules_root: Path) -> TreeDigest:
    """摘要语义规则树，review 审计 envelope 由独立 identity 规范化。"""

    if (rules_root / REVIEW_TRANSACTION_DIR_NAME).exists():
        raise ValueError("rules tree 存在未收口的 review source transaction")
    source = review_decision_source_path(rules_root)
    lock = (rules_root / REVIEW_TRANSACTION_LOCK_NAME).resolve()
    paths = [path for path in rules_root.rglob("*") if _is_identity_file(path) and path.resolve() not in {source, lock}]
    return paths_digest(rules_root, paths)


def review_decision_source_path(rules_root: Path) -> Path:
    """返回 rules 内不可经 symlink 逃逸的固定 review source。"""

    declared = rules_root / REVIEW_DECISION_SOURCE_NAME
    if declared.is_symlink():
        raise ValueError("review decision source 不得是 symlink")
    resolved_rules = rules_root.resolve()
    resolved = declared.resolve()
    try:
        resolved.relative_to(resolved_rules)
    except ValueError as exc:
        raise ValueError("review decision source 必须位于 rules root 内") from exc
    return resolved


def paths_digest(root: Path, paths: Iterable[Path]) -> TreeDigest:
    """对位于同一逻辑根下的一组文件计算规范化摘要。"""

    root = root.resolve()
    entries: list[tuple[str, Path]] = []
    for path in paths:
        resolved = path.resolve()
        if not resolved.is_file() or not _is_identity_file(resolved):
            continue
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"identity file 不在逻辑根内: {resolved} root={root}") from exc
        entries.append((relative, resolved))

    entries.sort(key=lambda item: item[0])
    digest = hashlib.sha256()
    digest.update(canonical_json_bytes({"algorithm": TREE_DIGEST_VERSION}))
    digest.update(b"\n")
    byte_count = 0
    for relative, path in entries:
        size = path.stat().st_size
        byte_count += size
        digest.update(canonical_json_bytes({"path": relative, "bytes": size, "sha256": sha256_file(path)}))
        digest.update(b"\n")

    return TreeDigest(digest.hexdigest(), len(entries), byte_count)


def generator_tree_digest(repo_root: Path) -> TreeDigest:
    """摘要真正影响候选生成的源码、项目配置与依赖锁文件。"""

    roots = (repo_root / "src", repo_root / "scripts")
    files: list[Path] = [repo_root / "pyproject.toml", repo_root / "uv.lock"]
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"generator identity root 不存在: {root}")
        files.extend(path for path in root.rglob("*") if _is_identity_file(path))
    return paths_digest(repo_root, files)


def contract_assets_digest(contract_assets_root: Path) -> TreeDigest:
    """摘要 writer 实际复制的 schema/examples，不混入质量基线等旁路数据。"""

    files: list[Path] = []
    for name in ("schema", "examples"):
        root = contract_assets_root / name
        if not root.is_dir():
            raise FileNotFoundError(f"contract assets root 不存在: {root}")
        files.extend(path for path in root.rglob("*") if _is_identity_file(path))
    corpus = contract_assets_root / "accepted-corpus.v1.json"
    if not corpus.is_file():
        raise FileNotFoundError(f"contract accepted corpus 不存在: {corpus}")
    files.append(corpus)
    return paths_digest(contract_assets_root, files)


def generator_state(repo_root: Path) -> GeneratorState:
    """读取当前 Git commit，并记录候选是否由 dirty 工作树生成。"""

    commit = _git(repo_root, "rev-parse", "HEAD").strip()
    dirty = bool(_git(repo_root, "status", "--porcelain", "--untracked-files=all").strip())
    return GeneratorState(commit=commit, dirty=dirty)


def load_frozen_profile(path: Path) -> dict[str, Any]:
    """读取并校验冻结验收档案的基本结构。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != FROZEN_PROFILE_SCHEMA_VERSION:
        raise ValueError(f"不支持的 frozen profile schema: {path}")
    required = ("profile_id", "corpus_scope", "base_game_version", "expected_raw_unique_count")
    missing = [name for name in required if not data.get(name)]
    if missing:
        raise ValueError(f"frozen profile 缺少字段: {', '.join(missing)}")
    return data


def validate_frozen_inputs(
    profile: Mapping[str, Any],
    *,
    raw_root: Path,
    sources_manifest_path: Path,
    reference_manifest_path: Path,
) -> dict[str, object]:
    """验证冻结 16.14 输入身份，并返回被显式接受的例外。"""

    base_version = str(profile["base_game_version"])
    expected_series = version_series(base_version)
    sources = _read_json_object(sources_manifest_path)
    reference = read_yaml(reference_manifest_path)

    _require_series("sources.game_version", sources.get("game_version"), expected_series)
    source_versions = reference.get("source_versions")
    if not isinstance(source_versions, dict):
        raise ValueError("reference manifest 缺少 source_versions")
    _require_series(
        "reference.source_versions.game_data.main_version",
        _nested(source_versions, "game_data", "main_version"),
        expected_series,
    )
    _require_series(
        "reference.source_versions.data_dragon.version",
        _nested(source_versions, "data_dragon", "version"),
        expected_series,
    )
    _require_series(
        "reference.source_versions.tencent_101.version",
        _nested(source_versions, "tencent_101", "version"),
        expected_series,
    )

    raw_unique_count = count_unique_raw_events(raw_root)
    expected_count = int(profile["expected_raw_unique_count"])
    if raw_unique_count != expected_count:
        raise ValueError(f"冻结 raw 唯一事件数不匹配: expected={expected_count} actual={raw_unique_count}")

    lobby = sources.get("lobby_snapshot")
    if not isinstance(lobby, dict):
        raise ValueError("sources manifest 缺少 lobby_snapshot")
    _require_series("sources.lobby_snapshot.game_version", lobby.get("game_version"), expected_series)

    exceptions: list[dict[str, str]] = []
    lcu_version = sources.get("lcu_version")
    if lcu_version is None:
        exception = profile.get("lcu_version_exception")
        manifest_id = lobby.get("lcu_manifest_id")
        if (
            not isinstance(exception, dict)
            or exception.get("allow_null") is not True
            or not isinstance(manifest_id, str)
            or not manifest_id.strip()
        ):
            raise ValueError("LCU version 缺失且 frozen profile 未提供可验证的显式例外")
        exceptions.append(
            {
                "code": "lcu_version_unavailable_with_lobby_manifest_evidence",
                "reason": str(exception.get("reason") or "frozen source did not expose an LCU version"),
                "evidence": f"lobby_snapshot.lcu_manifest_id={manifest_id}",
            }
        )
    else:
        _require_series("sources.lcu_version", lcu_version, expected_series)

    return {
        "profile_id": str(profile["profile_id"]),
        "corpus_scope": str(profile["corpus_scope"]),
        "base_game_version": base_version,
        "raw_unique_count": raw_unique_count,
        "accepted_exceptions": exceptions,
    }


def build_input_identity(
    *,
    repo_root: Path,
    raw_root: Path,
    reference_root: Path,
    rules_root: Path,
    overrides_root: Path,
    contract_assets_root: Path,
    sources_manifest_path: Path,
    frozen_profile_path: Path,
) -> dict[str, object]:
    """预检冻结输入并构建完整、无时间与绝对路径的输入身份。"""

    profile = load_frozen_profile(frozen_profile_path)
    frozen = validate_frozen_inputs(
        profile,
        raw_root=raw_root,
        sources_manifest_path=sources_manifest_path,
        reference_manifest_path=reference_root / "manifest.yaml",
    )
    state = generator_state(repo_root)
    sources_manifest = _read_json_object(sources_manifest_path)
    reference_manifest = read_yaml(reference_root / "manifest.yaml")
    reference_files = [
        path
        for path in reference_root.rglob("*")
        if _is_identity_file(path) and path.resolve() != (reference_root / "manifest.yaml").resolve()
    ]
    try:
        with advisory_file_lock(rules_root / REVIEW_TRANSACTION_LOCK_NAME):
            rules_identity = rules_tree_digest(rules_root).to_dict()
            review_identity = review_decision_identity(_read_json_object(review_decision_source_path(rules_root)))
    except AdvisoryLockUnavailableError as exc:
        raise ValueError("另一 review source transaction 正在运行") from exc
    inputs = {
        "raw": tree_digest(raw_root).to_dict(),
        "reference": paths_digest(reference_root, reference_files).to_dict(),
        "reference_manifest": semantic_document_identity(reference_manifest, excluded_keys={"updated_at"}),
        "rules": rules_identity,
        "review_decisions": review_identity,
        "overrides": tree_digest(overrides_root).to_dict(),
        "contract_assets": contract_assets_digest(contract_assets_root).to_dict(),
        "generator": generator_tree_digest(repo_root).to_dict(),
        "sources_manifest": source_manifest_identity(sources_manifest),
        "frozen_profile": {
            "algorithm": "sha256",
            "digest": sha256_file(frozen_profile_path),
        },
    }
    return {
        "schema": INPUT_IDENTITY_VERSION,
        "profile": frozen,
        "generator_state": state.to_dict(),
        "inputs": inputs,
    }


def review_decision_identity(document: Mapping[str, Any]) -> dict[str, object]:
    """提取并复算长期 review decision source 的稳定内容身份。"""

    schema_version = document.get("schema_version")
    decision_set_id = document.get("decision_set_id")
    status = document.get("status")
    decisions = document.get("decisions")
    if schema_version != 1:
        raise ValueError("review decision source schema_version 必须为 1")
    if not isinstance(decision_set_id, str) or not decision_set_id:
        raise ValueError("review decision source decision_set_id 无效")
    if status not in {"pending", "accepted"}:
        raise ValueError("review decision source status 无效")
    if not isinstance(decisions, list) or any(not isinstance(item, dict) for item in decisions):
        raise ValueError("review decision source decisions 必须是 object array")
    digest = review_decision_set_digest(decisions)
    if document.get("decision_set_digest") != digest:
        raise ValueError("review decision source digest 与 canonical decisions 不一致")
    if status == "pending" and decisions:
        raise ValueError("pending review decision source 不得包含决定")
    return {
        "algorithm": REVIEW_DECISION_IDENTITY_VERSION,
        "digest": digest,
        "decision_set_id": decision_set_id,
        "status": status,
    }


def review_decision_set_digest(decisions: Iterable[Mapping[str, object]]) -> str:
    """仅对决定语义求摘要，排除 reviewer、rationale 和界面时间。"""

    normalized = [{field: item.get(field) for field in REVIEW_DECISION_IDENTITY_FIELDS} for item in decisions]
    ordered = sorted(
        normalized,
        key=lambda item: (
            str(item.get("target_type") or ""),
            str(item.get("target_id") or ""),
            str(item.get("decision_id") or ""),
        ),
    )
    return hashlib.sha256(canonical_json_bytes(ordered)).hexdigest()


def describe_payload_files(output_root: Path) -> list[dict[str, object]]:
    """枚举 manifest 之外的确定性 payload 文件及摘要。"""

    entries: list[dict[str, object]] = []
    for path in sorted(
        (
            item
            for item in output_root.rglob("*")
            if item.is_file() and item.relative_to(output_root).as_posix() != "manifest.json"
        ),
        key=lambda item: item.relative_to(output_root).as_posix(),
    ):
        relative = path.relative_to(output_root).as_posix()
        entry: dict[str, object] = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        if path.suffix == ".jsonl":
            with path.open("rb") as file:
                entry["record_count"] = sum(1 for line in file if line.strip())
        entries.append(entry)
    return entries


def unmanaged_payload_paths(files: Sequence[Mapping[str, object]]) -> list[str]:
    """返回不属于 normalized V2 writer 管理集合的文件路径。"""

    unmanaged: list[str] = []
    for entry in files:
        path = entry.get("path")
        if not isinstance(path, str) or not _is_managed_payload_path(path):
            unmanaged.append(str(path))
    return sorted(unmanaged)


def source_manifest_identity(manifest: Mapping[str, Any]) -> dict[str, object]:
    """提取 source manifest 中与内容来源有关、与机器/运行无关的身份。"""

    lobby = manifest.get("lobby_snapshot")
    lobby = lobby if isinstance(lobby, dict) else {}
    semantic = {
        "mode": manifest.get("mode"),
        "region": manifest.get("region"),
        "game_version": manifest.get("game_version"),
        "lcu_version": manifest.get("lcu_version"),
        "lobby_snapshot": {
            "game_version": lobby.get("game_version"),
            "lcu_manifest_id": lobby.get("lcu_manifest_id"),
        },
        "sources": manifest.get("sources"),
    }
    return semantic_document_identity(semantic)


def semantic_document_identity(
    document: Mapping[str, Any],
    *,
    excluded_keys: set[str] | None = None,
) -> dict[str, object]:
    """对去除明确运行元数据后的结构化文档计算规范摘要。"""

    excluded = excluded_keys or set()
    semantic = {key: value for key, value in document.items() if key not in excluded}
    return {
        "algorithm": "canonical-json-sha256.v1",
        "digest": hashlib.sha256(canonical_json_bytes(semantic)).hexdigest(),
        "identity": semantic,
    }


def content_digest(content_identity: Mapping[str, object], files: Sequence[Mapping[str, object]]) -> str:
    """对输入身份、产品身份和有序文件摘要计算数据集内容摘要。"""

    payload = {
        "algorithm": CONTENT_DIGEST_VERSION,
        "content_identity": content_identity,
        "files": list(files),
    }
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def version_series(value: str) -> str:
    """从完整版本号中提取 major.minor。"""

    match = _VERSION_PREFIX.match(value)
    if match is None:
        raise ValueError(f"无法解析 major.minor 版本: {value!r}")
    return f"{match.group('major')}.{match.group('minor')}"


def count_unique_raw_events(raw_root: Path) -> int:
    """按 decomposition 的范围口径统计唯一 raw_event_name。"""

    names: set[str] = set()
    for path in sorted(raw_root.rglob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not is_excluded_event_name(value):
                names.add(value)
    return len(names)


def _is_identity_file(path: Path) -> bool:
    return (
        path.is_file()
        and path.suffix.lower() not in _IGNORED_TREE_SUFFIXES
        and not any(part in _IGNORED_TREE_PARTS for part in path.parts)
    )


def _is_managed_payload_path(path: str) -> bool:
    return (
        path in _PAYLOAD_TOP_LEVEL_FILES
        or (path.startswith("by-entity/") and path.endswith(".jsonl"))
        or path.startswith("schema/")
        or path.startswith("examples/")
    )


def _require_series(name: str, value: object, expected: str) -> None:
    if not isinstance(value, str) or version_series(value) != expected:
        raise ValueError(f"{name} 必须属于 {expected}: actual={value!r}")


def _nested(value: Mapping[str, Any], first: str, second: str) -> object:
    nested = value.get(first)
    return nested.get(second) if isinstance(nested, dict) else None


def _read_json_object(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"JSON 必须是 object: {path}")
    return data


def _git(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout
