"""导入本地外部数据源到内部 reference 合集。"""

from __future__ import annotations

import argparse
from pathlib import Path

from lol_event_semantics.online_sources import prepare_online_reference_sources
from lol_event_semantics.reference_ingestion import import_local_reference
from lol_event_semantics.tencent_hero_list_source import TENCENT_HERO_LIST_URL


def main(argv: list[str] | None = None) -> int:
    """运行 reference 数据导入命令。"""

    args = parse_args(argv)
    game_data_root = args.game_data_root
    data_dragon_root = args.data_dragon_root
    data_dragon_version = args.data_dragon_version
    hero_list_root = args.hero_list_root

    if args.online:
        sources = prepare_online_reference_sources(
            cache_root=args.cache_root,
            game_version=args.game_version,
            languages=tuple(args.languages),
            game_data_base_url=args.game_data_base_url,
            data_dragon_base_url=args.data_dragon_base_url,
            data_dragon_version=args.data_dragon_version,
            hero_list_url=args.hero_list_url,
        )
        game_data_root = sources.game_data_root
        data_dragon_root = sources.data_dragon_root
        data_dragon_version = sources.data_dragon_version
        hero_list_root = sources.hero_list_root

    collection = import_local_reference(
        reference_root=args.reference_root,
        game_data_root=game_data_root,
        data_dragon_root=data_dragon_root,
        game_version=args.game_version,
        data_dragon_version=data_dragon_version,
        languages=tuple(args.languages),
        hero_list_root=hero_list_root,
        overrides_root=args.overrides_root,
    )
    collection.write()
    print(f"reference: {args.reference_root}")
    print(f"champions: {len(collection.champions)}")
    print(f"maps: {len(collection.maps)}")
    print(f"skinlines: {len(collection.skinlines)}")
    print(f"skins: {len(collection.skins)}")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Import local GAME data and Data Dragon into the internal reference collection."
    )
    parser.add_argument("--online", action="store_true", help="Download online source subsets before import.")
    parser.add_argument("--reference-root", type=Path, required=True, help="Internal reference output root.")
    parser.add_argument("--game-data-root", type=Path, help="Local GAME data root.")
    parser.add_argument("--data-dragon-root", type=Path, help="Local Data Dragon root.")
    parser.add_argument(
        "--hero-list-root",
        type=Path,
        help="Local hero_list root (contains hero_list.json); online mode downloads it automatically.",
    )
    parser.add_argument(
        "--overrides-root",
        type=Path,
        help="Overrides root containing champion_nicknames.yaml for manual nickname supplements.",
    )
    parser.add_argument("--game-version", required=True, help="GAME version, for example 16.12.")
    parser.add_argument("--data-dragon-version", help="Data Dragon version, for example 16.12.1.")
    parser.add_argument(
        "--hero-list-url",
        default=TENCENT_HERO_LIST_URL,
        help="Tencent hero_list.js URL used in online mode.",
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".temp") / "reference-sources",
        help="Ignored cache root for online source subsets.",
    )
    parser.add_argument(
        "--game-data-base-url",
        default="https://raw.githubusercontent.com/Virace/lol-game-data-snapshot/main",
        help="Online GAME data snapshot root that contains global/{locale}/v1.",
    )
    parser.add_argument(
        "--data-dragon-base-url",
        default="https://ddragon.leagueoflegends.com",
        help="Data Dragon service base URL.",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=["en_US", "zh_CN"],
        choices=["en_US", "zh_CN"],
        help="Reference languages to import.",
    )
    namespace = parser.parse_args(argv)
    validate_args(parser, namespace)
    return namespace


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """校验本地和在线模式的必填参数。"""

    if args.online:
        return

    required = {
        "--game-data-root": args.game_data_root,
        "--data-dragon-root": args.data_dragon_root,
        "--data-dragon-version": args.data_dragon_version,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error(f"{', '.join(missing)} required unless --online is used")


if __name__ == "__main__":
    raise SystemExit(main())
