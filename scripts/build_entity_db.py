"""从外部 game-data 构建实体数据库。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.game_data import GameData, iter_paired_rows


@dataclass(slots=True)
class BuildArgs:
    """实体数据库构建参数。"""

    game_data_root: Path
    output_path: Path
    locale: str


def main(argv: list[str] | None = None) -> int:
    """运行实体数据库构建。"""

    args = parse_args(argv)
    data = GameData(root=args.game_data_root, locale=args.locale)
    payload = build_payload(data)

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"output_path: {args.output_path}")
    print(f"terms: {len(payload['terms'])}")
    print(
        "sections:",
        {
            key: len(value)
            for key, value in payload.items()
            if isinstance(value, list)
        },
    )

    return 0


def parse_args(argv: list[str] | None = None) -> BuildArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build a local entity database from external rcp-be-lol-game-data.")
    parser.add_argument("--game-data-root", type=Path, required=True, help="External rcp-be-lol-game-data root.")
    parser.add_argument("--output-path", type=Path, required=True, help="Target JSON path.")
    parser.add_argument("--locale", default="zh_cn", help="Display locale.")
    ns = parser.parse_args(argv)

    return BuildArgs(
        game_data_root=ns.game_data_root,
        output_path=ns.output_path,
        locale=ns.locale,
    )


def build_payload(data: GameData) -> dict[str, object]:
    """构造完整实体数据库。"""

    return {
        "champions": build_champions(data),
        "maps": build_maps(data),
        "items": build_catalog_rows(data, "items.json"),
        "summoner_spells": build_catalog_rows(data, "summoner-spells.json"),
        "universes": build_catalog_rows(data, "universes.json"),
        "skinlines": build_catalog_rows(data, "skinlines.json"),
        "queues": build_catalog_rows(data, "queues.json"),
        "game_mode_mutators": build_game_mode_mutators(data),
        "terms": build_terms(data),
    }


def build_terms(data: GameData) -> list[dict[str, str]]:
    """构造可序列化词条。"""

    if not data._terms:
        data._build_term_index()

    return [
        {
            "key": term.key,
            "label": term.label,
            "kind": term.kind,
            "source": term.source,
        }
        for term in sorted(data._terms.values(), key=lambda item: (item.kind, item.key))
    ]


def build_champions(data: GameData) -> list[dict[str, object]]:
    """构造英雄实体行。"""

    rows: list[dict[str, object]] = []

    for champion_id in sorted(set(data._alias_index.values())):
        champion = data.champion(champion_id)
        rows.append(
            {
                "id": champion.id,
                "alias": champion.alias,
                "name_en": champion.name_en,
                "name": champion.name,
                "title_en": champion.title_en,
                "title": champion.title,
                "passive_en": champion.passive_en,
                "passive": champion.passive,
                "spells": [
                    {
                        "key": spell.key,
                        "name_en": spell.name_en,
                        "name": spell.name,
                    }
                    for spell in champion.spells.values()
                ],
                "skins": [
                    {
                        "id": skin.id,
                        "key": skin.key,
                        "name_en": skin.name_en,
                        "name": skin.name,
                        "is_base": skin.is_base,
                    }
                    for skin in champion.skins
                ],
            }
        )

    return rows


def build_maps(data: GameData) -> list[dict[str, object]]:
    """构造地图实体行。"""

    rows = data._load_json(data.root / "global" / data.locale / "v1" / "maps.json")

    if not isinstance(rows, list):
        return []

    return [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "description": row.get("description"),
            "map_string_id": row.get("mapStringId"),
        }
        for row in rows
        if isinstance(row, dict)
    ]


def build_catalog_rows(data: GameData, file_name: str) -> list[dict[str, object]]:
    """构造通用目录实体行。"""

    default_rows = data._load_json(data.root / "global" / data.fallback_locale / "v1" / file_name)
    locale_rows = data._load_json(data.root / "global" / data.locale / "v1" / file_name)

    rows: list[dict[str, object]] = []

    for row_en, row_zh in iter_paired_rows(default_rows, locale_rows, "id"):
        rows.append(
            {
                "id": row_en.get("id"),
                "name_en": row_en.get("name"),
                "name": row_zh.get("name"),
                "short_name_en": row_en.get("shortName"),
                "short_name": row_zh.get("shortName"),
                "title_en": row_en.get("title"),
                "title": row_zh.get("title"),
            }
        )

    return rows


def build_game_mode_mutators(data: GameData) -> list[dict[str, object]]:
    """构造地图变体实体行。"""

    default_rows = data._load_json(data.root / "global" / data.fallback_locale / "v1" / "game-mode-mutators.json")
    locale_rows = data._load_json(data.root / "global" / data.locale / "v1" / "game-mode-mutators.json")

    rows: list[dict[str, object]] = []

    for row_en, row_zh in zip(default_rows, locale_rows, strict=False):
        if not isinstance(row_en, dict) or not isinstance(row_zh, dict):
            continue

        rows.append(
            {
                "map_id": row_en.get("MapId"),
                "base_name_en": row_en.get("MapNameBase"),
                "base_name": row_zh.get("MapNameBase"),
                "mutators": [
                    {
                        "key": item_en.get("Mutator", {}).get("ExpandedMutator"),
                        "name_en": item_en.get("MapNameOverride"),
                        "name": item_zh.get("MapNameOverride"),
                    }
                    for item_en, item_zh in zip(
                        row_en.get("Mutators", []),
                        row_zh.get("Mutators", []),
                        strict=False,
                    )
                    if isinstance(item_en, dict) and isinstance(item_zh, dict)
                ],
            }
        )

    return rows


if __name__ == "__main__":
    raise SystemExit(main())
