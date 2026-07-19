"""Data Dragon 在线按需下载。"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Callable, Any


DATA_DRAGON_BASE_URL = "https://ddragon.leagueoflegends.com"

type Fetcher = Callable[[str], bytes]


class DataDragonSourceError(ValueError):
    """Data Dragon source 解析或下载失败。"""


def download_data_dragon_subset(
    *,
    output_root: Path | str,
    game_version: str,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    base_url: str = DATA_DRAGON_BASE_URL,
    fetcher: Fetcher | None = None,
) -> str:
    """按需下载 Data Dragon champion summary/detail。"""

    fetch = fetcher or fetch_bytes
    base = base_url.rstrip("/")
    versions = read_json_bytes(fetch(f"{base}/api/versions.json"))
    version = select_data_dragon_version(versions, game_version)
    root = Path(output_root)

    for language in languages:
        summary_url = f"{base}/cdn/{version}/data/{language}/champion.json"
        summary_path = root / version / "data" / language / "champion.json"
        summary = read_or_fetch_json(summary_path, summary_url, fetch)

        aliases = champion_aliases(summary)
        full_detail_aliases = set()
        try:
            full_url = f"{base}/cdn/{version}/data/{language}/championFull.json"
            full_path = root / version / "data" / language / "championFull.json"
            full = read_or_fetch_json(full_path, full_url, fetch)
            full_detail_aliases = write_champion_details_from_full(root, version, language, aliases, full)
        except (OSError, TimeoutError, ValueError):
            full_detail_aliases = set()

        for alias in aliases:
            if alias in full_detail_aliases:
                continue

            detail_url = f"{base}/cdn/{version}/data/{language}/champion/{alias}.json"
            detail_path = root / version / "data" / language / "champion" / f"{alias}.json"
            read_or_fetch_json(detail_path, detail_url, fetch)

    return version


def select_data_dragon_version(versions: list[Any], game_version: str) -> str:
    """选择匹配 GAME 主版本的 Data Dragon 版本。"""

    target = main_version(game_version)

    for version in versions:
        if isinstance(version, str) and main_version(version) == target:
            return version

    raise DataDragonSourceError(f"No Data Dragon version matched main version {target}")


def champion_aliases(summary: Any) -> list[str]:
    """从 champion summary 中读取 alias 列表。"""

    data = summary.get("data") if isinstance(summary, dict) else None

    if not isinstance(data, dict):
        raise DataDragonSourceError("Data Dragon champion summary missing data mapping")

    return sorted(alias for alias, row in data.items() if isinstance(alias, str) and isinstance(row, dict))


def read_json_bytes(payload: bytes) -> Any:
    """从 bytes 解码 JSON。"""

    return json.loads(payload.decode("utf-8"))


def read_or_fetch_json(path: Path, url: str, fetcher: Fetcher) -> Any:
    """优先读取本地缓存，不存在时下载并写入 JSON。"""

    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))

    payload = read_json_bytes(fetcher(url))
    write_json_bytes(path, payload)
    return payload


def write_champion_details_from_full(
    root: Path,
    version: str,
    language: str,
    aliases: list[str],
    payload: Any,
) -> set[str]:
    """把 championFull payload 拆成逐英雄 detail 文件。"""

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise DataDragonSourceError("Data Dragon championFull missing data mapping")

    written = set()
    for alias in aliases:
        row = data.get(alias)
        if not isinstance(row, dict):
            continue

        detail_path = root / version / "data" / language / "champion" / f"{alias}.json"
        write_json_bytes(detail_path, {"data": {alias: row}})
        written.add(alias)

    return written


def write_json_bytes(path: Path, payload: Any) -> None:
    """稳定写出 JSON payload。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_bytes(url: str) -> bytes:
    """使用 urllib 下载 bytes。"""

    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def main_version(version: str) -> str:
    """提取 major.minor 主版本。"""

    parts = str(version).split(".")

    if len(parts) < 2:
        raise DataDragonSourceError(f"Version must contain at least major.minor: {version}")

    return ".".join(parts[:2])
