"""端到端 raw/reference 本地更新编排。"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .data_dragon_source import DATA_DRAGON_BASE_URL, Fetcher
from .dataset_identity import load_frozen_profile, validate_frozen_inputs, version_series
from .game_data_source import GAME_DATA_SNAPSHOT_BASE_URL
from .online_sources import prepare_online_reference_sources
from .raw_update import RawUpdateMetadata, RawUpdateResult, update_raw_events
from .reference_ingestion import import_local_reference
from .source_manifest import load_source_manifest, validate_portable_source_manifest
from .tencent_hero_list_source import TENCENT_HERO_LIST_URL


@dataclass(frozen=True, slots=True)
class UpdatePipelineResult:
    """端到端更新结果。"""

    changed: bool
    reference_changed: bool
    raw_update: RawUpdateResult

    def to_dict(self) -> dict[str, object]:
        """返回 JSON 友好的结果。"""

        return {
            "changed": self.changed,
            "reference_changed": self.reference_changed,
            "raw_update": self.raw_update.to_dict(),
        }


def run_local_update_pipeline(
    *,
    reference_root: Path | str,
    raw_root: Path | str,
    state_file: Path | str,
    game_data_root: Path | str,
    data_dragon_root: Path | str,
    raw_input_root: Path | str,
    game_version: str,
    data_dragon_version: str,
    report_root: Path | str,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    hero_list_root: Path | str | None = None,
    overrides_root: Path | str | None = None,
    sources_manifest_path: Path | str | None = None,
    frozen_profile_path: Path | str | None = None,
) -> UpdatePipelineResult:
    """运行本地 reference 导入和 raw 差集合并。

    Args:
        reference_root: 累计 reference 目标目录。
        raw_root: 累计 raw events 目标目录。
        state_file: raw 更新游标目标文件。
        game_data_root: 本地 GAME data 输入目录。
        data_dragon_root: 本地 Data Dragon 输入目录。
        raw_input_root: 当轮 raw events 输入目录。
        game_version: 当轮 GAME 版本。
        data_dragon_version: 当轮 Data Dragon 版本。
        report_root: 更新报告目标目录。
        languages: 要导入的 reference 语言。
        hero_list_root: 可选腾讯英雄列表输入目录。
        overrides_root: 可选人工 reference overrides 目录。
        sources_manifest_path: 可选的当轮来源 manifest；本地模式必须与 profile 成对提供。
        frozen_profile_path: 可选的冻结验收 profile；本地模式必须与 manifest 成对提供。

    Returns:
        在 staging 中完成并已原子提交的更新结果。

    Raises:
        TypeError: frozen preflight 路径未成对提供。
        ValueError: reference、raw 或 frozen preflight 输入不满足约束。
    """

    frozen_paths = _resolve_frozen_paths(sources_manifest_path, frozen_profile_path)
    reference_target = Path(reference_root)
    raw_target = Path(raw_root)
    state_target = Path(state_file)
    report_target = Path(report_root)

    staging_parent = common_existing_parent([reference_target, raw_target, state_target, report_target])
    with tempfile.TemporaryDirectory(prefix=".lol-event-update-", dir=staging_parent) as temp:
        temp_root = Path(temp)
        temp_reference = temp_root / "reference"
        temp_raw = temp_root / "raw" / "events"
        temp_state = temp_root / "raw" / "update-state.json"
        temp_report = temp_root / "report"

        copy_tree_if_exists(reference_target, temp_reference)
        copy_tree_if_exists(raw_target, temp_raw)

        before_reference = snapshot_tree(temp_reference)
        collection = import_local_reference(
            reference_root=temp_reference,
            game_data_root=game_data_root,
            data_dragon_root=data_dragon_root,
            game_version=game_version,
            data_dragon_version=data_dragon_version,
            languages=languages,
            hero_list_root=hero_list_root,
            overrides_root=overrides_root,
        )
        collection.write()
        reference_changed = snapshot_tree(temp_reference) != before_reference
        raw_result = update_raw_events(
            temp_raw,
            raw_input_root,
            state_file=temp_state,
            metadata=RawUpdateMetadata(
                game_version=game_version,
                manifest_major_version=major_version(game_version),
                data_dragon_version=data_dragon_version,
            ),
        )
        result = UpdatePipelineResult(
            changed=reference_changed or raw_result.changed,
            reference_changed=reference_changed,
            raw_update=raw_result,
        )

        write_update_report(temp_report, result, game_version, data_dragon_version)
        if frozen_paths is not None:
            sources_manifest, frozen_profile = frozen_paths
            validate_frozen_inputs(
                load_frozen_profile(frozen_profile),
                raw_root=temp_raw,
                sources_manifest_path=sources_manifest,
                reference_manifest_path=temp_reference / "manifest.yaml",
            )
        commit_staged_outputs(
            [
                StagedOutput(temp_reference, reference_target, is_dir=True),
                StagedOutput(temp_raw, raw_target, is_dir=True),
                StagedOutput(temp_state, state_target, is_dir=False),
                StagedOutput(temp_report, report_target, is_dir=True),
            ],
            temp_root / "backup",
        )
        return result


def run_online_update_pipeline(
    *,
    reference_root: Path | str,
    raw_root: Path | str,
    state_file: Path | str,
    raw_input_root: Path | str,
    game_version: str,
    report_root: Path | str,
    cache_root: Path | str,
    data_dragon_version: str | None = None,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    game_data_base_url: str = GAME_DATA_SNAPSHOT_BASE_URL,
    data_dragon_base_url: str = DATA_DRAGON_BASE_URL,
    hero_list_url: str = TENCENT_HERO_LIST_URL,
    overrides_root: Path | str | None = None,
    fetcher: Fetcher | None = None,
    sources_manifest_path: Path | str | None = None,
    frozen_profile_path: Path | str | None = None,
) -> UpdatePipelineResult:
    """运行在线 source 准备、本地 reference 导入和 raw 差集合并。

    Args:
        reference_root: 累计 reference 目标目录。
        raw_root: 累计 raw events 目标目录。
        state_file: raw 更新游标目标文件。
        raw_input_root: 当轮 raw events 输入目录。
        game_version: 当轮 GAME 版本。
        report_root: 更新报告目标目录。
        cache_root: 在线 reference 子集缓存目录。
        data_dragon_version: 可选 Data Dragon 版本；缺省时在线解析。
        languages: 要导入的 reference 语言。
        game_data_base_url: GAME data snapshot 基础 URL。
        data_dragon_base_url: Data Dragon 基础 URL。
        hero_list_url: 腾讯英雄列表 URL。
        overrides_root: 可选人工 reference overrides 目录。
        fetcher: 可注入的在线字节读取器。
        sources_manifest_path: 当轮可移植远端来源 manifest；在线模式必填。
        frozen_profile_path: 冻结验收 profile；在线模式必填。

    Returns:
        在线输入准备完成后由本地 staging pipeline 提交的更新结果。

    Raises:
        TypeError: 未提供完整 frozen provenance 路径。
        ValueError: 在线来源、reference、raw 或 frozen preflight 输入不满足约束。
    """

    frozen_paths = _resolve_frozen_paths(sources_manifest_path, frozen_profile_path)
    if frozen_paths is None:
        raise TypeError("在线更新必须提供 sources_manifest_path 与 frozen_profile_path")
    sources_manifest, frozen_profile = frozen_paths
    profile = load_frozen_profile(frozen_profile)
    source_evidence = validate_portable_source_manifest(
        load_source_manifest(sources_manifest),
        profile,
    )
    base_game_version = str(source_evidence["base_game_version"])
    _require_frozen_series("online game_version", game_version, base_game_version)
    if data_dragon_version is not None:
        _require_frozen_series("online data_dragon_version", data_dragon_version, base_game_version)
    sources = prepare_online_reference_sources(
        cache_root=cache_root,
        game_version=game_version,
        languages=languages,
        game_data_base_url=game_data_base_url,
        data_dragon_base_url=data_dragon_base_url,
        data_dragon_version=data_dragon_version,
        hero_list_url=hero_list_url,
        fetcher=fetcher,
    )
    return run_local_update_pipeline(
        reference_root=reference_root,
        raw_root=raw_root,
        state_file=state_file,
        game_data_root=sources.game_data_root,
        data_dragon_root=sources.data_dragon_root,
        raw_input_root=raw_input_root,
        game_version=game_version,
        data_dragon_version=sources.data_dragon_version,
        report_root=report_root,
        languages=languages,
        hero_list_root=sources.hero_list_root,
        overrides_root=overrides_root,
        sources_manifest_path=sources_manifest,
        frozen_profile_path=frozen_profile,
    )


def _resolve_frozen_paths(
    sources_manifest_path: Path | str | None,
    frozen_profile_path: Path | str | None,
) -> tuple[Path, Path] | None:
    """校验并解析可选的成对 frozen preflight 路径。

    Args:
        sources_manifest_path: 当轮来源 manifest 路径。
        frozen_profile_path: 冻结验收 profile 路径。

    Returns:
        两个参数均缺省时返回 ``None``，否则返回规范化的 ``Path`` 对。

    Raises:
        TypeError: 只提供其中一个路径。
    """

    if (sources_manifest_path is None) != (frozen_profile_path is None):
        raise TypeError("sources_manifest_path 与 frozen_profile_path 必须同时提供")
    if sources_manifest_path is None or frozen_profile_path is None:
        return None
    return Path(sources_manifest_path), Path(frozen_profile_path)


def _require_frozen_series(label: str, value: str, base_game_version: str) -> None:
    """要求联网请求版本与冻结档案属于同一 major.minor 系列。"""

    expected = version_series(base_game_version)
    actual = version_series(value)
    if actual != expected:
        raise ValueError(f"{label} 版本系列不匹配: expected={expected} actual={value}")


@dataclass(frozen=True, slots=True)
class StagedOutput:
    """单个 staging 输出提交项。"""

    source: Path
    target: Path
    is_dir: bool


def write_update_report(
    report_root: Path,
    result: UpdatePipelineResult,
    game_version: str,
    data_dragon_version: str,
) -> None:
    """写出本地 update report。"""

    report_root.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["source_versions"] = {
        "game_version": game_version,
        "data_dragon_version": data_dragon_version,
    }
    report_root.joinpath("update-report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def snapshot_tree(root: Path) -> dict[str, str]:
    """读取目录快照，用于判断是否产生内容变化。"""

    if not root.is_dir():
        return {}

    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")

    return snapshot


def copy_tree_if_exists(source: Path, target: Path) -> None:
    """如果源目录存在，则复制到临时目录。"""

    if source.is_dir():
        shutil.copytree(source, target)


def commit_staged_outputs(items: list[StagedOutput], backup_root: Path) -> None:
    """提交 staging 输出；失败时恢复原目标。"""

    backups: list[tuple[Path, Path, bool]] = []

    try:
        backup_root.mkdir(parents=True, exist_ok=True)

        for index, item in enumerate(items):
            backup = backup_root / str(index)
            backup_existing_target(item.target, backup, item.is_dir)
            backups.append((item.target, backup, item.is_dir))
            replace_output(item.source, item.target, item.is_dir)
    except Exception:
        restore_backups(reversed(backups))
        raise


def backup_existing_target(target: Path, backup: Path, is_dir: bool) -> None:
    """把已有目标移动到备份位置。"""

    if target.exists():
        backup.parent.mkdir(parents=True, exist_ok=True)

        if is_dir:
            shutil.move(str(target), str(backup))
        else:
            shutil.move(str(target), str(backup))


def replace_output(source: Path, target: Path, is_dir: bool) -> None:
    """用 staging 输出替换目标。"""

    if is_dir and not source.is_dir():
        return

    if not is_dir and not source.is_file():
        return

    target.parent.mkdir(parents=True, exist_ok=True)

    if is_dir:
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def restore_backups(backups: Iterable[tuple[Path, Path, bool]]) -> None:
    """恢复已备份的目标。"""

    for target, backup, is_dir in backups:
        if target.exists():
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()

        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))


def common_existing_parent(paths: list[Path]) -> Path:
    """返回适合放 staging 的共同父目录。"""

    resolved = [path.resolve() for path in paths]

    try:
        common = Path(os.path.commonpath([str(path.parent if path.suffix else path) for path in resolved]))
    except ValueError:
        common = resolved[0].parent

    common.mkdir(parents=True, exist_ok=True)
    return common


def major_version(version: str) -> str:
    """提取 major.minor 版本。"""

    parts = str(version).split(".")
    if len(parts) < 2:
        return str(version)
    return ".".join(parts[:2])
