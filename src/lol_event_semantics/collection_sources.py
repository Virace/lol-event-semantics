"""按固定快照提交获取并核验事件采集需要的大厅目录。"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import uuid4

from .http_fetch import fetch_bytes
from .wad_source import ManifestPair, main_version

SNAPSHOT_URL = "https://github.com/Virace/lol-game-data-snapshot.git"
RAW_URL = "https://raw.githubusercontent.com/Virace/lol-game-data-snapshot"
CATALOG_PATHS = (
    "global/default/v1/champion-summary.json",
    "global/default/v1/skins.json",
    "global/default/v1/maps.json",
)
CHAMPION_PATH = re.compile(r"global/default/v1/champions/[0-9]+\.json")


@dataclass(frozen=True, slots=True)
class _File:
    """快照声明的单份目录身份。"""

    path: str
    size: int
    digest: str


def _resolve_revision() -> str:
    """读取公开快照 main 当前提交，不创建或更新本地 Git 仓库。"""

    try:
        result = subprocess.run(
            ["git", "ls-remote", SNAPSHOT_URL, "refs/heads/main"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("无法读取采集目录快照提交，请检查 git 和 GitHub 连接") from exc
    if result.returncode:
        raise RuntimeError("无法读取采集目录快照提交，请检查 GitHub 连接")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != "refs/heads/main":
        raise ValueError("采集目录快照 main 未返回唯一提交")
    return rows[0][0]


def _read_json(data: bytes, path: str) -> object:
    """解析目录 JSON，并保留可定位到文件的异常链。"""

    try:
        return cast(object, json.loads(data))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"采集目录不是有效 JSON：{path}") from exc


def _metadata_files(data: bytes, pair: ManifestPair) -> list[_File]:
    """校验同轮版本证据，并仅接纳固定目录内带摘要的 JSON 文件。"""

    metadata = _read_json(data, "metadata.json")
    if not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1:
        raise ValueError("采集目录 metadata schemaVersion 无效")
    version = metadata.get("gameVersion")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+(?:\.[0-9]+)*", version):
        raise ValueError("采集目录 metadata 缺少有效 gameVersion")
    if main_version(version) != main_version(pair.game_version) or main_version(version) != main_version(
        pair.lcu_version
    ):
        raise ValueError(
            f"采集目录与本轮 manifest 版本不一致：catalog={version} game={pair.game_version} lcu={pair.lcu_version}"
        )
    if not pair.lcu_id or metadata.get("lcuManifestId") != pair.lcu_id:
        raise ValueError("采集目录 lcuManifestId 与本轮 LCU manifest 不一致或缺失")
    records = metadata.get("files")
    if not isinstance(records, list):
        raise ValueError("采集目录 metadata 缺少 files 清单")
    files: dict[str, _File] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("采集目录 metadata.files 条目必须是对象")
        path = record.get("path")
        if not isinstance(path, str):
            raise ValueError("采集目录 metadata.files 条目缺少 path")
        if path not in CATALOG_PATHS and not CHAMPION_PATH.fullmatch(path):
            continue
        if path in files:
            raise ValueError(f"采集目录 metadata.files 路径重复：{path}")
        size, digest = record.get("bytes"), record.get("sha256")
        if (
            type(size) is not int
            or size <= 0
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise ValueError(f"采集目录 metadata.files 缺少有效长度或 SHA-256：{path}")
        files[path] = _File(path, size, digest)
    for path in CATALOG_PATHS:
        if path not in files:
            raise ValueError(f"采集目录 metadata.files 缺少必要文件：{path}")
    if not any(CHAMPION_PATH.fullmatch(path) for path in files):
        raise ValueError("采集目录 metadata.files 缺少英雄详情")
    return [files[path] for path in sorted(files)]


def _matches(data: bytes, file: _File) -> bool:
    """将实际字节与快照声明的长度和摘要比对。"""

    return len(data) == file.size and hashlib.sha256(data).hexdigest() == file.digest


def _validate_json(data: bytes, file: _File) -> None:
    """校验真实目录使用的顶层结构，拒绝摘要自洽但不可读取的内容。"""

    value = _read_json(data, file.path)
    expected = list if file.path.endswith(("/champion-summary.json", "/maps.json")) else dict
    if not isinstance(value, expected):
        raise ValueError(f"采集目录 JSON 顶层结构无效：{file.path}")


def _write_bytes(path: Path, data: bytes) -> None:
    """原子替换单个缓存文件；写入失败时保留临时字节供诊断。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f".{path.name}.{uuid4().hex}.pending")
    pending.write_bytes(data)
    pending.replace(path)


def prepare_catalog(
    cache_root: Path,
    pair: ManifestPair,
    *,
    fetcher: Callable[[str], bytes] = fetch_bytes,
    revision: str | None = None,
) -> Path:
    """准备与本轮 manifest 对应的最小大厅目录及所有英雄详情。

    Args:
        cache_root: 采集器缓存根；按快照提交隔离子目录。
        pair: 本轮已经配对的 GAME/LCU manifest，必须携带 LCU 标识。
        fetcher: HTTP 字节获取边界，默认复用项目重试逻辑。
        revision: 调用方已解析的完整提交；省略时读取当前 main。

    Returns:
        包含 metadata、三个目录及英雄详情的快照结构根目录。

    Raises:
        ValueError: 来源版本、摘要、JSON 或必要文件证据不完整。
        RuntimeError: Git 或 HTTP 来源不可达。

    每次重新获取固定提交的 metadata，不以可变本地副本作为摘要权威。
    各文件在复用前重新校验；失败不会删除旧缓存，也不会返回完整目录。
    """

    commit = revision if revision is not None else _resolve_revision()
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("采集目录必须绑定完整 Git 提交 SHA")
    base_url = f"{RAW_URL}/{commit}"
    metadata = fetcher(f"{base_url}/metadata.json")
    files = _metadata_files(metadata, pair)
    root = cache_root / "catalogs" / commit

    def prepare_file(file: _File) -> None:
        """复核缓存，必要时下载并原子替换一份目录文件。"""

        target = root / file.path
        if target.is_file():
            data = target.read_bytes()
            if _matches(data, file):
                _validate_json(data, file)
                return
        data = fetcher(f"{base_url}/{file.path}")
        if not _matches(data, file):
            raise ValueError(f"采集目录长度或 SHA-256 不符：{file.path}")
        _validate_json(data, file)
        _write_bytes(target, data)

    with ThreadPoolExecutor(max_workers=6) as executor:
        list(executor.map(prepare_file, files))
    _write_bytes(root / "metadata.json", metadata)
    return root
