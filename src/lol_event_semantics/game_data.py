"""外部 game-data 读取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class SpellData:
    """英雄技能信息。"""

    key: str
    name_en: str
    name: str


@dataclass(slots=True)
class SkinData:
    """英雄皮肤信息。"""

    id: int
    key: str
    name_en: str
    name: str
    is_base: bool


@dataclass(slots=True)
class ChampionData:
    """英雄语义信息。"""

    id: int
    alias: str
    name_en: str
    name: str
    title_en: str
    title: str
    passive_en: str
    passive: str
    spells: dict[str, SpellData]
    skins: tuple[SkinData, ...]

    def skin_by_key(self, key: str) -> SkinData | None:
        """按皮肤 key 查找皮肤。"""

        for skin in self.skins:
            if skin.key.lower() == key.lower():
                return skin

        return None

    def skin_by_name_en(self, name: str) -> SkinData | None:
        """按英文皮肤名查找皮肤。"""

        needle = compact(name)

        for skin in self.skins:
            if compact(skin.name_en) == needle:
                return skin

        return None


@dataclass(slots=True)
class MapData:
    """地图语义信息。"""

    id: int
    name: str


@dataclass(slots=True)
class EntityTerm:
    """外部实体词条。"""

    key: str
    label: str
    kind: str
    source: str


@dataclass(slots=True)
class GameData:
    """外部 game-data 访问器。"""

    root: Path
    locale: str = "zh_cn"
    fallback_locale: str = "default"
    _champions: dict[int, ChampionData] = field(default_factory=dict)
    _maps: dict[int, MapData] = field(default_factory=dict)
    _alias_index: dict[str, int] = field(default_factory=dict)
    _terms: dict[str, EntityTerm] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """校验根目录并建立英雄 alias 索引。"""

        if not self.root.is_dir():
            raise FileNotFoundError(f"Game-data root does not exist: {self.root}")

        self._build_alias_index()

    def champion(self, id: int) -> ChampionData:
        """读取单个英雄语义信息。"""

        cached = self._champions.get(id)

        if cached is not None:
            return cached

        default_data = self._load_json(self._champion_path(id, self.fallback_locale))
        locale_data = self._load_json(self._champion_path(id, self.locale))
        record = build_champion(default_data, locale_data)
        self._champions[id] = record

        return record

    def find_champion(self, token: str) -> ChampionData | None:
        """从事件 token 推断英雄。"""

        if not token:
            return None

        needle = compact(token)
        best: tuple[int, int] | None = None

        for alias_key, champion_id in self._alias_index.items():
            if not (
                needle == alias_key
                or needle.startswith(alias_key)
                or needle.endswith(alias_key)
            ):
                continue

            score = len(alias_key)

            if best is None or score > best[1]:
                best = (champion_id, score)

        if best is None:
            return None

        return self.champion(best[0])

    def map(self, id: int) -> MapData | None:
        """读取单个地图语义信息。"""

        if not self._maps:
            self._build_map_index()

        return self._maps.get(id)

    def find_term(self, token: str) -> EntityTerm | None:
        """按英文 token 查找外部实体词条。"""

        if not token:
            return None

        if not self._terms:
            self._build_term_index()

        return self._terms.get(compact(token))

    def _build_alias_index(self) -> None:
        """建立英雄 alias 索引。"""

        summary = self._load_json(self._summary_path(self.fallback_locale))

        for row in summary:
            if not isinstance(row, dict):
                continue

            id = row.get("id")
            alias = row.get("alias")

            if not isinstance(id, int) or id < 0 or not isinstance(alias, str):
                continue

            self._alias_index[compact(alias)] = id

    def _build_map_index(self) -> None:
        """建立地图索引。"""

        rows = self._load_json(self._map_path(self.locale))

        for row in rows:
            if not isinstance(row, dict):
                continue

            id = row.get("id")
            name = row.get("name")

            if isinstance(id, int) and isinstance(name, str):
                self._maps[id] = MapData(id=id, name=name)

    def _build_term_index(self) -> None:
        """建立外部实体词条索引。"""

        for champion_id in sorted(self._alias_index.values()):
            champion = self.champion(champion_id)
            self._register_term(champion.alias, champion.title, "champion", f"champions/{champion_id}.json")
            self._register_term(champion.name_en, champion.title, "champion", f"champions/{champion_id}.json")
            self._register_term(champion.passive_en, champion.passive, "passive", f"champions/{champion_id}.json")

            for spell in champion.spells.values():
                self._register_term(spell.name_en, spell.name, "spell", f"champions/{champion_id}.json")

            for skin in champion.skins:
                self._register_term(skin.name_en, skin.name, "skin", f"champions/{champion_id}.json")

        for row_en, row_zh in iter_paired_rows(
            self._load_json(self.root / "global" / self.fallback_locale / "v1" / "items.json"),
            self._load_json(self.root / "global" / self.locale / "v1" / "items.json"),
            "id",
        ):
            self._register_term(row_en.get("name"), row_zh.get("name"), "item", "items.json")

        for row_en, row_zh in iter_paired_rows(
            self._load_json(self.root / "global" / self.fallback_locale / "v1" / "summoner-spells.json"),
            self._load_json(self.root / "global" / self.locale / "v1" / "summoner-spells.json"),
            "id",
        ):
            self._register_term(row_en.get("name"), row_zh.get("name"), "summoner_spell", "summoner-spells.json")

        for row_en, row_zh in iter_paired_rows(
            self._load_json(self.root / "global" / self.fallback_locale / "v1" / "maps.json"),
            self._load_json(self.root / "global" / self.locale / "v1" / "maps.json"),
            "id",
        ):
            self._register_term(row_en.get("name"), row_zh.get("name"), "map", "maps.json")

        for row_en, row_zh in iter_paired_rows(
            self._load_json(self.root / "global" / self.fallback_locale / "v1" / "universes.json"),
            self._load_json(self.root / "global" / self.locale / "v1" / "universes.json"),
            "id",
        ):
            self._register_term(row_en.get("name"), row_zh.get("name"), "universe", "universes.json")

        for row_en, row_zh in iter_paired_rows(
            self._load_json(self.root / "global" / self.fallback_locale / "v1" / "skinlines.json"),
            self._load_json(self.root / "global" / self.locale / "v1" / "skinlines.json"),
            "id",
        ):
            self._register_term(row_en.get("name"), row_zh.get("name"), "skinline", "skinlines.json")

        self._scan_term_files()

    def _register_term(self, source: Any, label: Any, kind: str, origin: str) -> None:
        """注册外部实体词条。"""

        if not isinstance(source, str) or not isinstance(label, str):
            return

        if not re.search(r"[A-Za-z]", source):
            return

        key = compact(source)

        if not key or key in self._terms:
            return

        self._terms[key] = EntityTerm(key=key, label=label, kind=kind, source=origin)

    def _scan_term_files(self) -> None:
        """扫描更多可配对的名称字段。"""

        default_root = self.root / "global" / self.fallback_locale / "v1"
        locale_root = self.root / "global" / self.locale / "v1"

        for path in sorted(default_root.glob("*.json")):
            other = locale_root / path.name

            if not other.is_file():
                continue

            default_data = self._load_json(path)
            locale_data = self._load_json(other)
            register_named_pairs(default_data, locale_data, self, path.name)

    def _summary_path(self, locale: str) -> Path:
        """返回英雄摘要路径。"""

        return self.root / "global" / locale / "v1" / "champion-summary.json"

    def _champion_path(self, id: int, locale: str) -> Path:
        """返回英雄详情路径。"""

        return self.root / "global" / locale / "v1" / "champions" / f"{id}.json"

    def _map_path(self, locale: str) -> Path:
        """返回地图列表路径。"""

        return self.root / "global" / locale / "v1" / "maps.json"

    def _load_json(self, path: Path) -> Any:
        """读取 JSON 文件。"""

        if not path.is_file():
            raise FileNotFoundError(f"Game-data file does not exist: {path}")

        return json.loads(path.read_text(encoding="utf-8"))


def build_champion(default_data: dict[str, Any], locale_data: dict[str, Any]) -> ChampionData:
    """构造英雄数据对象。"""

    return ChampionData(
        id=int(locale_data["id"]),
        alias=str(default_data["alias"]),
        name_en=str(default_data["name"]),
        name=str(locale_data["name"]),
        title_en=str(default_data.get("title") or default_data["name"]),
        title=str(locale_data.get("title") or locale_data["name"]),
        passive_en=str(default_data["passive"]["name"]),
        passive=str(locale_data["passive"]["name"]),
        spells=build_spells(default_data, locale_data),
        skins=build_skins(default_data, locale_data),
    )


def build_spells(
    default_data: dict[str, Any],
    locale_data: dict[str, Any],
) -> dict[str, SpellData]:
    """构造技能映射。"""

    spells: dict[str, SpellData] = {}

    for default_row, locale_row in zip(default_data.get("spells", []), locale_data.get("spells", []), strict=False):
        key = str(locale_row["spellKey"]).lower()
        spells[key] = SpellData(
            key=key,
            name_en=str(default_row["name"]),
            name=str(locale_row["name"]),
        )

    return spells


def build_skins(
    default_data: dict[str, Any],
    locale_data: dict[str, Any],
) -> tuple[SkinData, ...]:
    """构造皮肤映射。"""

    locale_skins = {int(row["id"]): row for row in locale_data.get("skins", [])}
    skins: list[SkinData] = []

    for default_row in default_data.get("skins", []):
        skin_id = int(default_row["id"])
        locale_row = locale_skins.get(skin_id, default_row)
        path = str(default_row.get("splashPath") or default_row.get("loadScreenPath") or "")

        skins.append(
            SkinData(
                id=skin_id,
                key=skin_key_from_path(path),
                name_en=str(default_row["name"]),
                name=str(locale_row["name"]),
                is_base=bool(default_row.get("isBase")),
            )
        )

    return tuple(skins)


def skin_key_from_path(path: str) -> str:
    """从资源路径提取皮肤 key。"""

    match = re.search(r"/Skins/(Base|Skin\d+)/", path, re.IGNORECASE)

    if match is None:
        return "Unknown"

    token = match.group(1)

    if token.lower() == "base":
        return "Base"

    digits = re.search(r"(\d+)", token)

    if digits is None:
        return token

    return f"Skin{digits.group(1).zfill(2)}"


def compact(text: str) -> str:
    """压缩字符串，便于匹配。"""

    return re.sub(r"[^a-z0-9]+", "", text.lower())


def iter_paired_rows(
    default_rows: Any,
    locale_rows: Any,
    key: str,
):
    """按主键遍历默认与本地化行。"""

    if not isinstance(default_rows, list) or not isinstance(locale_rows, list):
        return []

    locale_index = {
        row.get(key): row
        for row in locale_rows
        if isinstance(row, dict) and row.get(key) is not None
    }

    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []

    for row in default_rows:
        if not isinstance(row, dict):
            continue

        row_key = row.get(key)
        other = locale_index.get(row_key)

        if isinstance(other, dict):
            pairs.append((row, other))

    return pairs


def register_named_pairs(
    default_node: Any,
    locale_node: Any,
    data: GameData,
    source: str,
) -> None:
    """递归注册默认与本地化节点中的名称字段。"""

    if isinstance(default_node, dict) and isinstance(locale_node, dict):
        shared = set(default_node).intersection(locale_node)

        for key in shared:
            left = default_node[key]
            right = locale_node[key]

            if isinstance(left, str) and isinstance(right, str) and is_name_key(key):
                data._register_term(left, right, "catalog", source)
                continue

            register_named_pairs(left, right, data, source)

        return

    if isinstance(default_node, list) and isinstance(locale_node, list):
        for left, right in zip(default_node, locale_node, strict=False):
            register_named_pairs(left, right, data, source)


def is_name_key(key: str) -> bool:
    """判断字段是否像名称字段。"""

    token = re.sub(r"[^a-z0-9]+", "", key.lower())

    return token in {
        "name",
        "shortname",
        "speciesname",
        "title",
        "mapnamebase",
        "mapnameoverride",
    } or token.endswith("name")
