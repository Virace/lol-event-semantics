"""把本地外部数据源导入内部 reference 合集。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .reference_collection import (
    ReferenceCollection,
    ReferenceEntityInput,
    ReferenceItemInput,
    ReferenceMapInput,
    ReferenceSkinInput,
    ReferenceSpellInput,
    TextSnapshotInput,
    canonical_key,
    load_reference_collection,
    read_yaml,
)
from .tencent_hero_list_source import TENCENT_SOURCE, load_hero_list, parse_hero_entries


OVERRIDE_SOURCE = "override"
OVERRIDE_NICKNAMES_FILE = "champion_nicknames.yaml"


GAME_LOCALES = {"en_US": "default", "zh_CN": "zh_cn"}
SPELL_SLOTS = ("Q", "W", "E", "R")

# DDragon / game_data 的中文 name/title 语义与英文相反：
#   en_US: name=本名、title=称号修饰语；zh_CN: name=称号、title=俗名（本名）。
# 因此英雄本名（含中文俗名）统一从 name/title 中按语言取，称号单独进 entity.title。
CHAMPION_NAME_FIELD = {"en_US": "name", "zh_CN": "title"}  # → entity.names（本名/俗名）
CHAMPION_TITLE_FIELD = {"zh_CN": "name"}  # → entity.title（称号）


@dataclass(frozen=True, slots=True)
class LocalReferenceImportArgs:
    """本地 reference 导入参数。"""

    reference_root: Path
    game_data_root: Path
    data_dragon_root: Path
    game_version: str
    data_dragon_version: str
    languages: tuple[str, ...] = ("en_US", "zh_CN")


def import_local_reference(
    reference_root: Path | str,
    game_data_root: Path | str,
    data_dragon_root: Path | str,
    game_version: str,
    data_dragon_version: str,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
    hero_list_root: Path | str | None = None,
    overrides_root: Path | str | None = None,
) -> ReferenceCollection:
    """从本地 GAME data、Data Dragon、可选 hero_list 与人工 overrides 导入内部合集。"""

    assert_main_versions_match(game_version, data_dragon_version)
    collection = load_reference_collection(reference_root)
    collection.languages = tuple(languages)
    collection.set_source_version("game_data", main_version=main_version(game_version))
    collection.set_source_version("data_dragon", version=data_dragon_version)

    import_game_data(collection, Path(game_data_root), game_version, languages)
    import_data_dragon(collection, Path(data_dragon_root), data_dragon_version, languages)
    if hero_list_root is not None:
        import_hero_list(collection, hero_list_root, languages=languages)
    if overrides_root is not None:
        import_nickname_overrides(collection, overrides_root, languages=languages)
    return collection


def import_game_data(
    collection: ReferenceCollection,
    root: Path,
    game_version: str,
    languages: tuple[str, ...],
) -> None:
    """导入本地 GAME data 中的英雄、地图、皮肤系列和技能基础信息。"""

    default_base = game_data_base(root, "en_US")
    summary = read_json(default_base / "champion-summary.json")
    game_languages = available_game_data_languages(root, languages)

    if not isinstance(summary, list):
        raise ValueError(f"GAME champion-summary.json must be a list: {default_base}")

    localized_summaries = {
        language: rows_by_id(read_json(game_data_base(root, language) / "champion-summary.json"))
        for language in game_languages
    }
    localized_maps = {
        language: rows_by_id(read_json(game_data_base(root, language) / "maps.json"))
        for language in game_languages
    }

    import_game_maps(collection, localized_maps, game_version, game_languages)
    import_game_skinlines(collection, root, game_version, game_languages)
    import_game_items(collection, root, game_version, game_languages)

    for row in summary:
        if not isinstance(row, dict):
            continue

        champion_id = parse_int(row.get("id"))
        alias = row.get("alias")

        if champion_id is None or not isinstance(alias, str):
            continue

        rows = {
            language: localized_summaries.get(language, {}).get(champion_id, {})
            for language in game_languages
        }
        names, title = champion_name_and_title(rows, alias, game_languages)
        collection.merge_champion(
            ReferenceEntityInput(
                id=champion_id,
                alias=alias,
                names=names,
                title=title or None,
                source="game_data",
                version=game_version,
            )
        )
        import_game_champion_details(collection, root, champion_id, alias, game_version, game_languages)


def import_game_maps(
    collection: ReferenceCollection,
    localized_maps: dict[str, dict[int, dict[str, Any]]],
    game_version: str,
    languages: tuple[str, ...],
) -> None:
    """导入 GAME data 地图列表。"""

    default_rows = localized_maps.get("en_US") or {}
    for map_id, row in sorted(default_rows.items()):
        alias = str(row.get("mapStringId") or f"Map{map_id}")
        names = {
            language: str(localized_maps.get(language, {}).get(map_id, {}).get("name") or alias)
            for language in languages
        }
        collection.merge_map(
            ReferenceMapInput(
                id=map_id,
                alias=alias,
                names=names,
                source="game_data",
                version=game_version,
            )
        )


def import_game_items(
    collection: ReferenceCollection,
    root: Path,
    game_version: str,
    languages: tuple[str, ...],
) -> None:
    """导入 GAME data 装备列表；旧缓存缺 items.json 时整体跳过。"""

    localized_items: dict[str, dict[int, dict[str, Any]]] = {}
    for language in languages:
        path = game_data_base(root, language) / "items.json"
        if path.is_file():
            localized_items[language] = rows_by_id(read_json(path))

    default_rows = localized_items.get("en_US") or {}
    for item_id, row in sorted(default_rows.items()):
        names: dict[str, str] = {}
        for language in languages:
            name = localized_items.get(language, {}).get(item_id, {}).get("name")
            if isinstance(name, str) and name:
                names[language] = name

        if not names:
            continue

        collection.merge_item(
            ReferenceItemInput(
                id=item_id,
                names=names,
                source="game_data",
                version=game_version,
            )
        )


def import_game_skinlines(
    collection: ReferenceCollection,
    root: Path,
    game_version: str,
    languages: tuple[str, ...],
) -> None:
    """导入 GAME data 皮肤系列列表。"""

    localized_skinlines: dict[str, dict[int, dict[str, Any]]] = {}
    for language in languages:
        path = game_data_base(root, language) / "skinlines.json"
        if path.is_file():
            localized_skinlines[language] = rows_by_id(read_json(path))

    default_rows = localized_skinlines.get("en_US") or {}
    for skinline_id, row in sorted(default_rows.items()):
        name = row.get("name")

        if not isinstance(name, str) or not name.strip():
            continue

        names = {}
        for language, rows in sorted(localized_skinlines.items()):
            value = rows.get(skinline_id, {}).get("name")

            if isinstance(value, str) and value.strip():
                names[language] = value.strip()

        collection.merge_skinline(
            ReferenceEntityInput(
                id=skinline_id,
                alias=f"Skinline{skinline_id}",
                names=names,
                source="game_data",
                version=game_version,
            )
        )


def import_game_champion_details(
    collection: ReferenceCollection,
    root: Path,
    champion_id: int,
    alias: str,
    game_version: str,
    languages: tuple[str, ...],
) -> None:
    """导入 GAME data 英雄详情中的技能名和描述。"""

    details = {}
    for language in languages:
        detail_path = game_data_base(root, language) / "champions" / f"{champion_id}.json"
        if detail_path.is_file():
            details[language] = read_json(detail_path)

    default_detail = details.get("en_US")

    if not isinstance(default_detail, dict):
        return

    import_game_champion_skins(collection, details, champion_id, alias, game_version, languages)

    passive_names = {
        language: nested_str(details.get(language), "passive", "name")
        for language in languages
    }
    collection.merge_spell(
        alias,
        ReferenceSpellInput(
            slot="P",
            id=f"{alias}Passive",
            names={language: value for language, value in passive_names.items() if value},
            source="game_data",
            version=game_version,
        ),
    )

    passive_descriptions = {
        language: nested_str(details.get(language), "passive", "description")
        for language in languages
    }
    for language, value in passive_descriptions.items():
        if value:
            collection.merge_description(
                alias,
                "P",
                TextSnapshotInput(
                    language=language,
                    value=value,
                    source="game_data",
                    version=game_version,
                ),
            )

    localized_spells = {language: list_value(details.get(language), "spells") for language in languages}
    for index, slot in enumerate(SPELL_SLOTS):
        default_spell = item_at(localized_spells.get("en_US") or [], index)
        if not isinstance(default_spell, dict):
            continue

        spell_id = str(default_spell.get("spellKey") or slot).upper()
        names = {
            language: str(item_at(localized_spells.get(language) or [], index).get("name"))
            for language in languages
            if isinstance(item_at(localized_spells.get(language) or [], index), dict)
            and item_at(localized_spells.get(language) or [], index).get("name")
        }
        collection.merge_spell(
            alias,
            ReferenceSpellInput(
                slot=slot,
                id=f"{alias}{spell_id}",
                names=names,
                source="game_data",
                version=game_version,
            ),
        )

        for language in languages:
            spell_row = item_at(localized_spells.get(language) or [], index)
            if isinstance(spell_row, dict) and isinstance(spell_row.get("description"), str):
                collection.merge_description(
                    alias,
                    slot,
                    TextSnapshotInput(
                        language=language,
                        value=str(spell_row["description"]),
                        source="game_data",
                        version=game_version,
                    ),
                )


def import_game_champion_skins(
    collection: ReferenceCollection,
    details: dict[str, Any],
    champion_id: int,
    alias: str,
    version: str,
    languages: tuple[str, ...],
    source: str = "game_data",
) -> None:
    """导入英雄详情中的具体皮肤名称。"""

    skins_by_language = {
        language: skins_by_number(list_value(details.get(language), "skins"), champion_id)
        for language in languages
    }
    default_skins = skins_by_language.get("en_US") or {}

    for skin_number, default_skin in sorted(default_skins.items()):
        if skin_number == 0 or default_skin.get("isBase") is True:
            continue

        skin_id = parse_int(default_skin.get("id"))
        if skin_id is None:
            continue

        names = {}
        for language, rows in skins_by_language.items():
            name = rows.get(skin_number, {}).get("name")
            if isinstance(name, str) and name.strip():
                names[language] = name.strip()

        skin_token = f"Skin{skin_number:02d}"
        collection.merge_skin(
            ReferenceSkinInput(
                champion_alias=alias,
                champion_id=champion_id,
                skin_number=skin_number,
                skin_token=skin_token,
                id=skin_id,
                alias=f"{alias}{skin_token}",
                names=names,
                skinlines=skinlines_from_game_skin(default_skin),
                source=source,
                version=version,
            )
        )


def import_data_dragon(
    collection: ReferenceCollection,
    root: Path,
    version: str,
    languages: tuple[str, ...],
) -> None:
    """导入本地 Data Dragon 英雄摘要和详情。"""

    summaries = {
        language: data_dragon_summary(root, version, language)
        for language in languages
    }
    default_summary = summaries.get("en_US") or {}

    for alias, row in sorted(default_summary.items()):
        if not isinstance(row, dict):
            continue

        champion_id = parse_int(row.get("key"))
        if champion_id is None:
            continue

        rows = {language: summaries.get(language, {}).get(alias, {}) for language in languages}
        names, title = champion_name_and_title(rows, alias, languages)
        collection.merge_champion(
            ReferenceEntityInput(
                id=champion_id,
                alias=alias,
                names=names,
                title=title or None,
                source="data_dragon",
                version=version,
            )
        )
        import_data_dragon_champion_details(collection, root, version, alias, languages)


def import_data_dragon_champion_details(
    collection: ReferenceCollection,
    root: Path,
    version: str,
    alias: str,
    languages: tuple[str, ...],
) -> None:
    """导入单个 Data Dragon 英雄详情。"""

    details = {
        language: data_dragon_champion(root, version, language, alias)
        for language in languages
    }
    default_detail = details.get("en_US")

    if isinstance(default_detail, dict):
        champion_id = parse_int(default_detail.get("key"))
        if champion_id is not None:
            import_game_champion_skins(
                collection,
                details,
                champion_id,
                alias,
                version,
                languages,
                source="data_dragon",
            )

    passive_names = {
        language: nested_str(details.get(language), "passive", "name")
        for language in languages
    }
    collection.merge_spell(
        alias,
        ReferenceSpellInput(
            slot="P",
            id=f"{alias}Passive",
            names={language: value for language, value in passive_names.items() if value},
            source="data_dragon",
            version=version,
        ),
    )

    for language in languages:
        passive_description = nested_str(details.get(language), "passive", "description")
        if passive_description:
            collection.merge_description(
                alias,
                "P",
                TextSnapshotInput(
                    language=language,
                    value=passive_description,
                    source="data_dragon",
                    version=version,
                ),
            )

    localized_spells = {language: list_value(details.get(language), "spells") for language in languages}
    for index, slot in enumerate(SPELL_SLOTS):
        default_spell = item_at(localized_spells.get("en_US") or [], index)
        if not isinstance(default_spell, dict):
            continue

        spell_id = str(default_spell.get("id") or f"{alias}{slot}")
        names = {
            language: str(item_at(localized_spells.get(language) or [], index).get("name"))
            for language in languages
            if isinstance(item_at(localized_spells.get(language) or [], index), dict)
            and item_at(localized_spells.get(language) or [], index).get("name")
        }
        collection.merge_spell(
            alias,
            ReferenceSpellInput(
                slot=slot,
                id=spell_id,
                names=names,
                source="data_dragon",
                version=version,
            ),
        )

        for language in languages:
            spell_row = item_at(localized_spells.get(language) or [], index)
            if isinstance(spell_row, dict) and isinstance(spell_row.get("description"), str):
                collection.merge_description(
                    alias,
                    slot,
                    TextSnapshotInput(
                        language=language,
                        value=str(spell_row["description"]),
                        source="data_dragon",
                        version=version,
                    ),
                )


def import_hero_list(
    collection: ReferenceCollection,
    hero_list_root: Path | str,
    *,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
) -> None:
    """把腾讯 hero_list 名称体系并入已存在英雄。

    按 ``heroId`` → ``entity.id`` join，绕开 alias 大小写漂移；hero_list 中存在
    但 reference 未收录的英雄直接跳过，不创建新实体。
    """

    if "zh_CN" not in languages:
        return

    entries = parse_hero_entries(load_hero_list(Path(hero_list_root) / "hero_list.json"))
    if not entries:
        return

    alias_by_id = champion_alias_by_id(collection)
    collection.set_source_version(TENCENT_SOURCE, version=entries[0].version)

    for entry in entries:
        alias_key = alias_by_id.get(entry.hero_id)
        if alias_key is None:
            continue

        collection.merge_champion(
            ReferenceEntityInput(
                id=entry.hero_id,
                alias=alias_key,
                names={"zh_CN": entry.common_name} if entry.common_name else {},
                title={"zh_CN": entry.title} if entry.title else None,
                nicknames={"zh_CN": list(entry.nicknames)} if entry.nicknames else None,
                search_alias={"zh_CN": list(entry.search_alias)} if entry.search_alias else None,
                source=TENCENT_SOURCE,
                version=entry.version,
            )
        )


def import_nickname_overrides(
    collection: ReferenceCollection,
    overrides_root: Path | str,
    *,
    languages: tuple[str, ...] = ("en_US", "zh_CN"),
) -> None:
    """把人工俗称兜底并入已存在英雄。

    用于补 hero_list keywords 缺失的常用俗称（如 Irelia 刀妹）。按 alias 大小写
    不敏感匹配已存在英雄；reference 中不存在的 alias 直接跳过，不创建新实体。
    """

    path = Path(overrides_root) / OVERRIDE_NICKNAMES_FILE
    if not path.is_file():
        return

    payload = read_yaml(path)
    nicknames_by_alias = payload.get("nicknames")
    if not isinstance(nicknames_by_alias, dict):
        return

    version = str(payload.get("version") or "curated")
    applied = 0
    for alias, by_language in sorted(nicknames_by_alias.items()):
        alias_key = canonical_key(collection.champions, str(alias))
        if alias_key is None or not isinstance(by_language, dict):
            continue

        nicknames = {
            language: [value.strip() for value in values if isinstance(value, str) and value.strip()]
            for language, values in by_language.items()
            if language in languages and isinstance(values, list)
        }
        if not any(nicknames.values()):
            continue

        collection.merge_champion(
            ReferenceEntityInput(
                id=collection.champions[alias_key]["entity"]["id"],
                alias=alias_key,
                names={},
                nicknames=nicknames,
                source=OVERRIDE_SOURCE,
                version=version,
            )
        )
        applied += 1

    if applied:
        collection.set_source_version(OVERRIDE_SOURCE, version=version)


def champion_alias_by_id(collection: ReferenceCollection) -> dict[int, str]:
    """构建 entity.id → reference alias key 映射。"""

    mapping: dict[int, str] = {}
    for alias_key, champion in collection.champions.items():
        entity = champion.get("entity")
        if isinstance(entity, dict) and isinstance(entity.get("id"), int):
            mapping[entity["id"]] = alias_key
    return mapping


def champion_name_and_title(
    rows_by_language: dict[str, dict[str, Any]],
    alias: str,
    languages: tuple[str, ...],
) -> tuple[dict[str, str], dict[str, str]]:
    """按中英文 name/title 语义产出 (本名, 称号) 两个语言字典。"""

    names: dict[str, str] = {}
    title: dict[str, str] = {}
    for language in languages:
        row = rows_by_language.get(language) or {}
        name_value = stripped_text(row.get(CHAMPION_NAME_FIELD.get(language, "name")))
        if name_value:
            names[language] = name_value

        title_field = CHAMPION_TITLE_FIELD.get(language)
        if title_field:
            title_value = stripped_text(row.get(title_field))
            if title_value:
                title[language] = title_value

    names.setdefault("en_US", alias)
    return names, title


def stripped_text(value: Any) -> str | None:
    """返回去空白的非空字符串，否则 None。"""

    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def game_data_base(root: Path, language: str) -> Path:
    """返回 GAME data 指定语言的 v1 根目录。"""

    locale = GAME_LOCALES.get(language)
    if locale is None:
        raise ValueError(f"Unsupported reference language: {language}")
    return root / "global" / locale / "v1"


def available_game_data_languages(root: Path, languages: tuple[str, ...]) -> tuple[str, ...]:
    """返回本地 GAME data 实际可导入的语言。"""

    available = []
    for language in languages:
        base = game_data_base(root, language)
        if (base / "champion-summary.json").is_file() and (base / "maps.json").is_file():
            available.append(language)

    if "en_US" not in available:
        raise FileNotFoundError(f"GAME data default locale is required: {game_data_base(root, 'en_US')}")

    return tuple(available)


def data_dragon_summary(root: Path, version: str, language: str) -> dict[str, Any]:
    """读取 Data Dragon champion summary。"""

    path = root / version / "data" / language / "champion.json"
    payload = read_json(path)
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError(f"Data Dragon champion.json did not contain data mapping: {path}")
    return data


def data_dragon_champion(root: Path, version: str, language: str, alias: str) -> dict[str, Any]:
    """读取 Data Dragon 单英雄详情。"""

    path = root / version / "data" / language / "champion" / f"{alias}.json"
    payload = read_json(path)
    data = payload.get("data") if isinstance(payload, dict) else None
    row = data.get(alias) if isinstance(data, dict) else None
    if not isinstance(row, dict):
        raise ValueError(f"Data Dragon champion detail did not contain {alias}: {path}")
    return row


def assert_main_versions_match(game_version: str, data_dragon_version: str) -> None:
    """校验 GAME 和 Data Dragon 主版本一致。"""

    if main_version(game_version) != main_version(data_dragon_version):
        raise ValueError(
            "Data Dragon main version must match GAME main version: "
            f"{data_dragon_version} != {game_version}"
        )


def main_version(version: str) -> str:
    """提取版本前两段作为主版本。"""

    parts = str(version).split(".")
    if len(parts) < 2:
        raise ValueError(f"Version must contain at least major.minor: {version}")
    return ".".join(parts[:2])


def rows_by_id(value: Any) -> dict[int, dict[str, Any]]:
    """把 JSON 行按 id 建索引。"""

    if not isinstance(value, list):
        return {}

    rows: dict[int, dict[str, Any]] = {}
    for row in value:
        if not isinstance(row, dict):
            continue

        id_value = parse_int(row.get("id"))
        if id_value is not None:
            rows[id_value] = row
    return rows


def skins_by_number(rows: list[Any], champion_id: int) -> dict[int, dict[str, Any]]:
    """把皮肤行按 SkinNN 编号建索引。"""

    skins: dict[int, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue

        skin_number = skin_number_from_game_skin(row, champion_id)
        if skin_number is not None:
            skins[skin_number] = row

    return skins


def skin_number_from_game_skin(row: dict[str, Any], champion_id: int) -> int | None:
    """从 GAME data 皮肤行解析 SkinNN 编号。"""

    skin_number = parse_int(row.get("num"))
    if skin_number is not None:
        return skin_number

    skin_id = parse_int(row.get("id"))
    if skin_id is not None:
        skin_number = skin_id - champion_id * 1000
        if skin_number >= 0:
            return skin_number

    for key in ["splashPath", "uncenteredSplashPath", "tilePath", "loadScreenPath"]:
        value = row.get(key)
        if not isinstance(value, str):
            continue

        match = re.search(r"/Skin([0-9]+)/", value, flags=re.IGNORECASE)
        if match is not None:
            return int(match.group(1))

    return None


def skinlines_from_game_skin(row: dict[str, Any]) -> list[dict[str, Any]]:
    """从 GAME data 皮肤行提取皮肤系列引用。"""

    skinlines = []
    for item in row.get("skinLines") or []:
        if not isinstance(item, dict):
            continue

        skinline_id = parse_int(item.get("id"))
        if skinline_id is not None:
            skinlines.append({"id": skinline_id})

    return skinlines


def read_json(path: Path) -> Any:
    """读取 JSON 文件。"""

    if not path.is_file():
        raise FileNotFoundError(f"Reference source file does not exist: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def nested_str(value: Any, first: str, second: str) -> str | None:
    """读取二级字符串字段。"""

    if not isinstance(value, dict):
        return None

    nested = value.get(first)
    if not isinstance(nested, dict) or not isinstance(nested.get(second), str):
        return None
    return str(nested[second])


def list_value(value: Any, key: str) -> list[Any]:
    """读取列表字段。"""

    if not isinstance(value, dict) or not isinstance(value.get(key), list):
        return []
    return list(value[key])


def item_at(rows: list[Any], index: int) -> Any:
    """安全读取列表元素。"""

    if index < 0 or index >= len(rows):
        return None
    return rows[index]


def parse_int(value: Any) -> int | None:
    """宽松解析整数。"""

    if isinstance(value, int):
        return value

    if isinstance(value, str) and value.isdigit():
        return int(value)

    return None
