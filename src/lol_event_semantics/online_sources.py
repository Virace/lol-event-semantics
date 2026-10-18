"""在线 reference source 准备编排。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .data_dragon_source import DATA_DRAGON_BASE_URL, Fetcher, download_data_dragon_subset
from .game_data_source import GAME_DATA_SNAPSHOT_BASE_URL, download_game_data_snapshot_subset
from .tencent_hero_list_source import TENCENT_HERO_LIST_URL, download_hero_list


@dataclass(frozen=True, slots=True)
class OnlineReferenceSources:
    """在线 source 下载后的本地缓存路径。"""

    game_data_root: Path
    data_dragon_root: Path
    data_dragon_version: str
    hero_list_root: Path
    hero_list_version: str


def prepare_online_reference_sources(
    *,
    cache_root: Path | str,
    game_version: str,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    game_data_base_url: str = GAME_DATA_SNAPSHOT_BASE_URL,
    data_dragon_base_url: str = DATA_DRAGON_BASE_URL,
    data_dragon_version: str | None = None,
    hero_list_url: str = TENCENT_HERO_LIST_URL,
    fetcher: Fetcher | None = None,
    game_data_fetcher: Fetcher | None = None,
) -> OnlineReferenceSources:
    """下载在线最小子集，并整理成现有本地导入 layout。

    game_data_fetcher 用于已绑定采集身份的 GAME 读取器；存在时保留原始
    JSON 与 metadata 供 local 入口复核。其他来源仍使用通用 fetcher。
    """

    root = Path(cache_root)
    game_data_root = root / "game-data"
    data_dragon_root = root / "data-dragon"
    hero_list_root = root / "hero-list"

    download_game_data_snapshot_subset(
        output_root=game_data_root,
        game_version=game_version,
        languages=languages,
        base_url=game_data_base_url,
        fetcher=game_data_fetcher or fetcher,
        preserve_source_bytes=game_data_fetcher is not None,
    )
    if game_data_fetcher is not None:
        (game_data_root / "metadata.json").write_bytes(
            game_data_fetcher(f"{game_data_base_url.rstrip('/')}/metadata.json")
        )
    selected_data_dragon_version = download_data_dragon_subset(
        output_root=data_dragon_root,
        game_version=data_dragon_version or game_version,
        languages=languages,
        base_url=data_dragon_base_url,
        fetcher=fetcher,
    )
    hero_list_version = download_hero_list(
        output_root=hero_list_root,
        url=hero_list_url,
        fetcher=fetcher,
    )
    return OnlineReferenceSources(
        game_data_root=game_data_root,
        data_dragon_root=data_dragon_root,
        data_dragon_version=selected_data_dragon_version,
        hero_list_root=hero_list_root,
        hero_list_version=hero_list_version,
    )
