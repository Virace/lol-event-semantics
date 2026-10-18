"""可移植来源清单的构建与冻结档案校验。"""

from __future__ import annotations

import json
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping

from .dataset_identity import version_series

SOURCE_MANIFEST_SCHEMA_VERSION = 1
REMOTE_SOURCE_MODES = frozenset({"manifest", "remote"})
_MACHINE_LOCAL_FIELDS = frozenset({"downloaded_at", "game_dir", "generated_at", "local_wads"})


def build_manifest_source(
    *,
    game_version: str,
    game_manifest_id: str,
    lcu_version: str,
    lcu_manifest_id: str,
    region: str | None = None,
) -> dict[str, object]:
    """构建 manifest 采集模式的可移植来源清单。"""

    normalized_game_version = _required_text("game_version", game_version)
    normalized_game_manifest_id = _required_text("game_manifest_id", game_manifest_id)
    normalized_manifest_id = _required_text("lcu_manifest_id", lcu_manifest_id)
    normalized_lcu_version = _required_text("lcu_version", lcu_version)
    normalized_region = _optional_text(region)
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "mode": "manifest",
        "region": normalized_region,
        "game_version": normalized_game_version,
        "lcu_version": normalized_lcu_version,
        "game_manifest": {
            "id": normalized_game_manifest_id,
            "version": normalized_game_version,
        },
        "lobby_snapshot": {
            "game_version": normalized_game_version,
            "lcu_manifest_id": normalized_manifest_id,
        },
        "sources": {
            "raw-bins": "ok",
        },
    }


def load_source_manifest(path: Path) -> dict[str, Any]:
    """读取 JSON 来源清单并要求对象根。"""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"source manifest 根必须是对象: {path}")
    return data


def normalize_portable_source_manifest(manifest: Mapping[str, Any]) -> dict[str, object]:
    """从采集游标投影出不含机器路径和运行时间的来源身份。"""

    if manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("不支持的 source manifest schema_version")

    lobby = manifest.get("lobby_snapshot")
    lobby = lobby if isinstance(lobby, Mapping) else {}
    game_manifest = manifest.get("game_manifest")
    game_manifest = game_manifest if isinstance(game_manifest, Mapping) else {}
    return {
        "schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "mode": manifest.get("mode"),
        "region": manifest.get("region"),
        "game_version": manifest.get("game_version"),
        "lcu_version": manifest.get("lcu_version"),
        "game_manifest": {
            "id": game_manifest.get("id"),
            "version": game_manifest.get("version"),
        },
        "lobby_snapshot": {
            "game_version": lobby.get("game_version"),
            "lcu_manifest_id": lobby.get("lcu_manifest_id"),
        },
        "sources": manifest.get("sources"),
    }


def validate_portable_source_manifest(
    manifest: Mapping[str, Any],
    profile: Mapping[str, Any],
) -> dict[str, object]:
    """验证远端来源清单可移植且属于当前冻结档案。"""

    if manifest.get("schema_version") != SOURCE_MANIFEST_SCHEMA_VERSION:
        raise ValueError("不支持的 source manifest schema_version")

    mode = manifest.get("mode")
    if mode not in REMOTE_SOURCE_MODES:
        raise ValueError("workflow source manifest 的 mode 必须是 manifest 或 remote，不能复用 local 来源")

    local_fields = sorted(_MACHINE_LOCAL_FIELDS.intersection(manifest))
    if local_fields:
        raise ValueError(f"source manifest 含机器或运行期字段: {', '.join(local_fields)}")

    base_version = _required_text("frozen profile base_game_version", profile.get("base_game_version"))
    expected_series = version_series(base_version)
    game_version = _required_text("source manifest game_version", manifest.get("game_version"))
    _require_series("source manifest game_version", game_version, expected_series)

    game_manifest = manifest.get("game_manifest")
    if not isinstance(game_manifest, Mapping):
        raise ValueError("source manifest 缺少 game_manifest")
    game_manifest_id = _required_text("source manifest game_manifest.id", game_manifest.get("id"))
    game_manifest_version = _required_text("source manifest game_manifest.version", game_manifest.get("version"))
    _require_series("source manifest game_manifest.version", game_manifest_version, expected_series)

    lobby = manifest.get("lobby_snapshot")
    if not isinstance(lobby, Mapping):
        raise ValueError("source manifest 缺少 lobby_snapshot")
    lobby_local_fields = sorted(_MACHINE_LOCAL_FIELDS.intersection(lobby))
    if lobby_local_fields:
        raise ValueError(f"source manifest lobby_snapshot 含运行期字段: {', '.join(lobby_local_fields)}")
    lobby_version = _required_text("source manifest lobby game_version", lobby.get("game_version"))
    _require_series("source manifest lobby game_version", lobby_version, expected_series)
    lcu_manifest_id = _required_text("source manifest lcu_manifest_id", lobby.get("lcu_manifest_id"))

    normalized_lcu_version = _required_text("source manifest lcu_version", manifest.get("lcu_version"))
    _require_series("source manifest lcu_version", normalized_lcu_version, expected_series)

    sources = manifest.get("sources")
    if not isinstance(sources, Mapping) or not sources:
        raise ValueError("source manifest sources 必须是非空对象")
    incomplete = sorted(str(name) for name, status in sources.items() if status != "ok")
    if incomplete:
        raise ValueError(f"source manifest 含未完成来源: {', '.join(incomplete)}")

    return {
        "profile_id": _required_text("frozen profile profile_id", profile.get("profile_id")),
        "base_game_version": base_version,
        "mode": str(mode),
        "game_version": game_version,
        "game_manifest_id": game_manifest_id,
        "lcu_manifest_id": lcu_manifest_id,
    }


def write_source_manifest(path: Path, manifest: Mapping[str, Any]) -> None:
    """以稳定、可提交的 JSON 形式写出来源清单。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(dict(manifest), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def validate_collection_raw(manifest: Mapping[str, Any], raw_root: Path) -> None:
    """核验本轮 raw 文件闭包，拒绝与采集来源清单不一致的导入。

    Args:
        manifest: 采集器成功轮次的可移植来源清单。
        raw_root: 待导入的本轮 raw 根目录，不是累计语料。

    Raises:
        ValueError: 身份、路径、文件闭包或摘要不符合采集证明。
    """

    collection = manifest.get("collection")
    if not isinstance(collection, Mapping) or not isinstance(collection.get("run_id"), str):
        raise ValueError("source manifest 缺少 collection 身份")
    if not re.fullmatch(r"[0-9a-f]{24}", collection["run_id"]):
        raise ValueError("source manifest collection.run_id 无效")
    files = collection.get("raw_files")
    if not isinstance(files, Mapping) or not files:
        raise ValueError("source manifest 缺少 collection.raw_files")
    for path, digest in files.items():
        if (
            not isinstance(path, str)
            or not re.fullmatch(r"(?:champions|maps)/[0-9]+\.txt", path)
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError("source manifest collection.raw_files 路径或摘要无效")
    actual = {path.relative_to(raw_root).as_posix() for path in raw_root.rglob("*.txt")}
    if actual != set(files):
        raise ValueError("本轮 raw 文件集合与采集清单不一致")
    for path, digest in files.items():
        target = raw_root / path
        if target.is_symlink() or not target.resolve().is_relative_to(raw_root.resolve()):
            raise ValueError("本轮 raw 文件不得通过符号链接逃逸输入目录")
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ValueError(f"本轮 raw SHA-256 与采集清单不一致：{path}")


def _require_series(label: str, value: str, expected: str) -> None:
    if version_series(value) != expected:
        raise ValueError(f"{label} 版本系列不匹配: expected={expected} actual={value}")


def _required_text(label: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} 必须是非空字符串")
    return value.strip()


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
