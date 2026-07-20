"""lol-game-data-snapshot 在线按需下载。"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .reference_ingestion import GAME_LOCALES


GAME_DATA_SNAPSHOT_BASE_URL = "https://raw.githubusercontent.com/Virace/lol-game-data-snapshot/main"

type Fetcher = Callable[[str], bytes]


class GameDataSourceError(ValueError):
    """GAME data snapshot source 解析或下载失败。"""


# 快照仓库（lol-game-data-snapshot）的远程目录名：英文为 default、中文为
# zh_CN（大写），与本地缓存布局的 GAME_LOCALES（zh_cn 小写）分离维护。
SNAPSHOT_REMOTE_LOCALES = {"en_US": "default", "zh_CN": "zh_CN"}


def download_game_data_snapshot_subset(
    *,
    output_root: Path | str,
    game_version: str,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    base_url: str = GAME_DATA_SNAPSHOT_BASE_URL,
    fetcher: Fetcher | None = None,
) -> None:
    """按需下载 GAME data 的 champion summary、地图、皮肤系列和英雄详情。"""

    fetch = fetcher or fetch_bytes
    base = base_url.rstrip("/")
    root = Path(output_root)

    for language in languages:
        locale = game_locale(language)
        # 快照仓库远程目录大小写敏感（GitHub raw）：中文目录实为 zh_CN，
        # 与本地缓存布局的 zh_cn 小写不同；仅远程 URL 做大小写修正，
        # 本地缓存与下游读取布局保持既有 GAME_LOCALES 映射不变。
        remote_locale = SNAPSHOT_REMOTE_LOCALES.get(language, locale)
        remote_base = f"{base}/global/{remote_locale}/v1"
        local_base = root / "global" / locale / "v1"

        summary = read_json_bytes(fetch(f"{remote_base}/champion-summary.json"))
        maps = read_json_bytes(fetch(f"{remote_base}/maps.json"))
        skinlines = read_json_bytes(fetch(f"{remote_base}/skinlines.json"))
        write_json_bytes(local_base / "champion-summary.json", summary)
        write_json_bytes(local_base / "maps.json", maps)
        write_json_bytes(local_base / "skinlines.json", skinlines)

        for champion_id in champion_ids(summary):
            detail = read_json_bytes(fetch(f"{remote_base}/champions/{champion_id}.json"))
            write_json_bytes(local_base / "champions" / f"{champion_id}.json", detail)


def game_locale(language: str) -> str:
    """把 reference language 映射为 GAME data locale。"""

    locale = GAME_LOCALES.get(language)
    if locale is None:
        raise GameDataSourceError(f"Unsupported reference language: {language}")
    return locale


def champion_ids(summary: Any) -> list[int]:
    """从 GAME champion summary 中读取英雄 id。"""

    if not isinstance(summary, list):
        raise GameDataSourceError("GAME champion-summary.json must be a list")

    ids: list[int] = []
    for row in summary:
        if not isinstance(row, dict):
            continue

        try:
            champion_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        # 占位条目（id<0，如 -1=None）无英雄详情文件，快照仓库亦不存，跳过。
        if champion_id < 0:
            continue
        ids.append(champion_id)

    return sorted(set(ids))


def read_json_bytes(payload: bytes) -> Any:
    """从 bytes 解码 JSON。"""

    return json.loads(payload.decode("utf-8"))


def write_json_bytes(path: Path, payload: Any) -> None:
    """稳定写出 JSON payload。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_bytes(url: str) -> bytes:
    """使用共享重试 fetcher 下载 bytes（见 http_fetch 模块）。"""

    from .http_fetch import fetch_bytes as fetch_with_retry

    return fetch_with_retry(url)
