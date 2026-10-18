"""将在线 reference 请求绑定到采集轮次的同一大厅快照。"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from pathlib import Path

from .collection_sources import RAW_URL
from .dataset_identity import version_series
from .http_fetch import fetch_bytes
from .game_data_source import champion_ids, SNAPSHOT_REMOTE_LOCALES


def bind_reference_snapshot(
    source: Mapping[str, Any], *, fetcher: Callable[[str], bytes] | None = None
) -> tuple[str, Callable[[str], bytes]]:
    """核验采集快照身份，返回固定提交地址和逐文件校验的读取器。

    Args:
        source: 带 catalog、GAME/LCU 版本和 LCU manifest 的采集来源清单。
        fetcher: 可注入的原始 HTTP 读取器；不省略任何摘要校验。

    Returns:
        固定快照 URL 和拒绝未声明、越界或损坏文件的读取器。

    Raises:
        ValueError: 快照身份、版本、文件清单或响应摘要不匹配。
    """

    catalog = source.get("catalog")
    if not isinstance(catalog, Mapping):
        raise ValueError("reference 来源缺少采集 catalog 身份")
    revision = catalog.get("revision")
    digest = catalog.get("metadata_sha256")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("reference catalog revision 必须是完整提交 SHA")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("reference catalog 缺少 metadata SHA-256")
    base = f"{RAW_URL}/{revision}"
    fetch = fetcher or fetch_bytes
    payload = fetch(f"{base}/metadata.json")
    if hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("reference metadata SHA-256 与采集轮次不一致")
    metadata = json.loads(payload)
    if not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1:
        raise ValueError("reference metadata schemaVersion 无效")
    version = metadata.get("gameVersion")
    if not isinstance(version, str) or any(
        version_series(version) != version_series(str(source.get(key))) for key in ("game_version", "lcu_version")
    ):
        raise ValueError("reference metadata 与采集 GAME/LCU 版本不一致")
    lobby = source.get("lobby_snapshot")
    if (
        not isinstance(lobby, Mapping)
        or not lobby.get("lcu_manifest_id")
        or metadata.get("lcuManifestId") != lobby["lcu_manifest_id"]
    ):
        raise ValueError("reference metadata 与采集 LCU manifest 不一致")
    records = metadata.get("files")
    if not isinstance(records, list):
        raise ValueError("reference metadata 缺少 files")
    files: dict[str, tuple[int, str]] = {}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("reference metadata files 条目无效")
        path, size, sha = row.get("path"), row.get("bytes"), row.get("sha256")
        if not isinstance(path, str) or not re.fullmatch(
            r"global/(?:default|zh_CN)/v1/(?:[a-z0-9-]+|champions/[0-9]+)\.json", path
        ):
            continue
        if (
            path in files
            or type(size) is not int
            or size <= 0
            or not isinstance(sha, str)
            or not re.fullmatch(r"[0-9a-f]{64}", sha)
        ):
            raise ValueError("reference metadata files 长度、摘要或唯一性无效")
        files[path] = (size, sha)

    def verified_fetch(url: str) -> bytes:
        """仅返回本快照清单中长度和摘要均匹配的原始字节。"""

        prefix = f"{base}/"
        path = url.removeprefix(prefix)
        if url == f"{base}/metadata.json":
            return payload
        if not url.startswith(prefix) or path not in files:
            raise ValueError("reference 请求不属于已核验快照清单")
        data = fetch(url)
        size, sha = files[path]
        if len(data) != size or hashlib.sha256(data).hexdigest() != sha:
            raise ValueError(f"reference 文件长度或 SHA-256 不符：{path}")
        return data

    return base, verified_fetch


def validate_local_reference_snapshot(
    source: Mapping[str, Any], root: Path, *, languages: tuple[str, ...] = ("en_US", "zh_CN")
) -> None:
    """离线复核保留原始字节的 reference 缓存，拒绝跨轮或篡改输入。

    本地语言目录沿用 zh_cn；快照 URL 中仍使用原始的 zh_CN。
    只读取目标缓存，不进行联网或修改。
    """

    catalog = source.get("catalog")
    if not isinstance(catalog, Mapping):
        raise ValueError("reference 来源缺少采集 catalog 身份")
    base = f"{RAW_URL}/{catalog.get('revision')}"

    def local_fetch(url: str) -> bytes:
        """把已知快照请求投影到受限的本地缓存布局。"""

        prefix = f"{base}/"
        relative = url.removeprefix(prefix).replace("global/zh_CN/", "global/zh_cn/")
        target = root / relative
        if not url.startswith(prefix) or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError("reference 本地快照路径越界")
        return target.read_bytes()

    _, verified = bind_reference_snapshot(source, fetcher=local_fetch)
    for language in languages:
        locale = SNAPSHOT_REMOTE_LOCALES.get(language)
        if locale is None:
            raise ValueError("reference 本地快照语言无效")
        prefix = f"{base}/global/{locale}/v1"
        summary = json.loads(verified(f"{prefix}/champion-summary.json"))
        for name in ("maps.json", "items.json", "skinlines.json"):
            verified(f"{prefix}/{name}")
        for identifier in champion_ids(summary):
            verified(f"{prefix}/champions/{identifier}.json")
    for path in root.joinpath("global").rglob("*.json"):
        relative = path.relative_to(root).as_posix().replace("global/zh_cn/", "global/zh_CN/")
        verified(f"{base}/{relative}")
