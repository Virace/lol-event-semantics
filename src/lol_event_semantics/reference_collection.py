"""仓库内部 reference 精简合集。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


SCHEMA_VERSION = 2
DESCRIPTION_AUTO_MERGE_THRESHOLD = 0.97
SUPPORTED_LANGUAGES = ("en_US", "zh_CN")


@dataclass(frozen=True, slots=True)
class ReferenceEntityInput:
    """待合并的实体基础信息。

    ``title`` / ``nicknames`` / ``search_alias`` 是英雄专用的名称体系扩展，
    分别承载称号、玩家俗称和拼音搜索别名；地图、皮肤系列复用本类型时留空。
    """

    id: int
    alias: str
    names: dict[str, str]
    source: str
    version: str
    title: dict[str, str] | None = None
    nicknames: dict[str, list[str]] | None = None
    search_alias: dict[str, list[str]] | None = None


@dataclass(frozen=True, slots=True)
class ReferenceSpellInput:
    """待合并的技能基础信息。"""

    slot: str
    id: str
    names: dict[str, str]
    source: str
    version: str


@dataclass(frozen=True, slots=True)
class ReferenceMapInput:
    """待合并的地图基础信息。"""

    id: int
    alias: str
    names: dict[str, str]
    source: str
    version: str


@dataclass(frozen=True, slots=True)
class ReferenceItemInput:
    """待合并的装备基础信息。

    装备以数字 id 为规范身份（跨语言、跨版本稳定），不设字符串 alias。
    """

    id: int
    names: dict[str, str]
    source: str
    version: str


@dataclass(frozen=True, slots=True)
class ReferenceSkinInput:
    """待合并的具体皮肤基础信息。"""

    champion_alias: str
    champion_id: int
    skin_number: int
    skin_token: str
    id: int
    alias: str
    names: dict[str, str]
    skinlines: list[dict[str, Any]]
    source: str
    version: str


@dataclass(frozen=True, slots=True)
class TextSnapshotInput:
    """待合并的长文本快照。"""

    language: str
    value: str
    source: str
    version: str


@dataclass(slots=True)
class ReferenceCollection:
    """仓库维护的累计 reference 合集。"""

    root: Path
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES
    champions: dict[str, dict[str, Any]] = field(default_factory=dict)
    maps: dict[str, dict[str, Any]] = field(default_factory=dict)
    skinlines: dict[str, dict[str, Any]] = field(default_factory=dict)
    skins: dict[str, dict[str, Any]] = field(default_factory=dict)
    items: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_versions: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: str | None = None
    _dirty: bool = False

    def __post_init__(self) -> None:
        """标准化根目录和语言顺序。"""

        self.root = Path(self.root)
        self.languages = tuple(self.languages)

    def set_source_version(self, source: str, **metadata: Any) -> None:
        """记录一个外部来源的版本或 hash 信息。"""

        current = self.source_versions.setdefault(source, {})
        for key, value in sorted(metadata.items()):
            if current.get(key) != value:
                self._dirty = True
            current[key] = value

    def merge_champion(self, item: ReferenceEntityInput) -> None:
        """合并一个英雄实体。"""

        alias_key = canonical_key(self.champions, item.alias) or item.alias
        champion = self.champions.setdefault(
            alias_key,
            {
                "schema_version": SCHEMA_VERSION,
                "entity": {
                    "type": "champion",
                    "id": item.id,
                    "alias": item.alias,
                    "names": {},
                },
                "spells": [],
            },
        )
        self._dirty = set_if_changed(champion["entity"], "id", item.id) or self._dirty
        self._dirty = set_if_changed(champion["entity"], "alias", item.alias) or self._dirty
        self._dirty = set_if_changed(champion["entity"], "type", "champion") or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                champion["entity"].setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

        self._merge_name_system(champion["entity"], item)

    def _merge_name_system(self, entity: dict[str, Any], item: ReferenceEntityInput) -> None:
        """合并英雄名称体系扩展：称号、俗称、拼音搜索别名。"""

        for language, value in sorted((item.title or {}).items()):
            if not value:
                continue
            self._dirty = add_sourced_value(
                entity.setdefault("title", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

        for field_name, values_by_language in (
            ("nicknames", item.nicknames),
            ("search_alias", item.search_alias),
        ):
            for language, values in sorted((values_by_language or {}).items()):
                clean = [value for value in values if value]
                if not clean:
                    continue
                bucket = entity.setdefault(field_name, {}).setdefault(language, [])
                for value in clean:
                    self._dirty = add_sourced_value(bucket, value, item.source, item.version) or self._dirty

    def merge_spell(self, champion_alias: str, item: ReferenceSpellInput) -> None:
        """合并一个英雄技能。"""

        champion = self.require_champion(champion_alias)
        spell = find_or_create_spell(champion, item.slot)
        self._dirty = set_if_changed(spell, "slot", item.slot) or self._dirty
        self._dirty = add_sourced_value(spell.setdefault("ids", []), item.id, item.source, item.version) or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                spell.setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

    def merge_map(self, item: ReferenceMapInput) -> None:
        """合并一个地图实体。"""

        map_entity = self.maps.setdefault(
            item.alias,
            {
                "schema_version": SCHEMA_VERSION,
                "entity": {
                    "type": "map",
                    "id": item.id,
                    "alias": item.alias,
                    "names": {},
                },
            },
        )
        self._dirty = set_if_changed(map_entity["entity"], "id", item.id) or self._dirty
        self._dirty = set_if_changed(map_entity["entity"], "alias", item.alias) or self._dirty
        self._dirty = set_if_changed(map_entity["entity"], "type", "map") or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                map_entity["entity"].setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

    def merge_item(self, item: ReferenceItemInput) -> None:
        """合并一个装备实体（以数字 id 字符串为分组键）。"""

        key = str(item.id)
        item_entity = self.items.setdefault(
            key,
            {
                "schema_version": SCHEMA_VERSION,
                "entity": {
                    "type": "item",
                    "id": item.id,
                    "alias": key,
                    "names": {},
                },
            },
        )
        self._dirty = set_if_changed(item_entity["entity"], "id", item.id) or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                item_entity["entity"].setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

    def merge_skinline(self, item: ReferenceEntityInput) -> None:
        """合并一个皮肤系列实体。"""

        skinline = self.skinlines.setdefault(
            item.alias,
            {
                "schema_version": SCHEMA_VERSION,
                "entity": {
                    "type": "skinline",
                    "id": item.id,
                    "alias": item.alias,
                    "names": {},
                },
            },
        )
        self._dirty = set_if_changed(skinline["entity"], "id", item.id) or self._dirty
        self._dirty = set_if_changed(skinline["entity"], "alias", item.alias) or self._dirty
        self._dirty = set_if_changed(skinline["entity"], "type", "skinline") or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                skinline["entity"].setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

    def merge_skin(self, item: ReferenceSkinInput) -> None:
        """合并一个具体皮肤实体。"""

        key = canonical_key(self.skins, skin_reference_key(item.champion_alias, item.skin_token))
        if key is None:
            key = skin_reference_key(item.champion_alias, item.skin_token)

        skin = self.skins.setdefault(
            key,
            {
                "schema_version": SCHEMA_VERSION,
                "entity": {
                    "type": "skin",
                    "champion_alias": item.champion_alias,
                    "champion_id": item.champion_id,
                    "skin_number": item.skin_number,
                    "skin_token": item.skin_token,
                    "id": item.id,
                    "alias": item.alias,
                    "names": {},
                },
                "skinlines": [],
                "aliases": [],
            },
        )
        entity = skin["entity"]
        self._dirty = set_if_changed(entity, "type", "skin") or self._dirty
        self._dirty = set_if_changed(entity, "champion_alias", item.champion_alias) or self._dirty
        self._dirty = set_if_changed(entity, "champion_id", item.champion_id) or self._dirty
        self._dirty = set_if_changed(entity, "skin_number", item.skin_number) or self._dirty
        self._dirty = set_if_changed(entity, "skin_token", item.skin_token) or self._dirty
        self._dirty = set_if_changed(entity, "id", item.id) or self._dirty
        self._dirty = set_if_changed(entity, "alias", item.alias) or self._dirty

        for language, value in sorted(item.names.items()):
            self._dirty = add_sourced_value(
                entity.setdefault("names", {}).setdefault(language, []),
                value,
                item.source,
                item.version,
            ) or self._dirty

        aliases = skin.setdefault("aliases", [])
        for alias in [item.alias, f"{item.champion_alias}{item.skin_token}", f"{item.champion_alias}Skin{item.skin_number}"]:
            self._dirty = add_string_value(aliases, alias) or self._dirty

        skinline_rows = skin.setdefault("skinlines", [])
        for skinline in item.skinlines:
            self._dirty = add_sourced_skinline(skinline_rows, skinline, item.source, item.version) or self._dirty

    def merge_description(self, champion_alias: str, slot: str, item: TextSnapshotInput) -> None:
        """合并技能描述长文本快照。"""

        champion = self.require_champion(champion_alias)
        spell = find_or_create_spell(champion, slot)
        descriptions = spell.setdefault("descriptions", {})
        language_data = descriptions.setdefault(item.language, {"snapshots": []})
        snapshots = language_data.setdefault("snapshots", [])
        value_hash = text_hash(item.value)

        for snapshot in snapshots:
            if snapshot["hash"] == value_hash:
                self._dirty = set_last_seen_if_newer(snapshot, item.version) or self._dirty
                self._dirty = add_source(snapshot.setdefault("sources", []), item.source, item.version) or self._dirty
                return

        if snapshots:
            previous = snapshots[-1]
            similarity = text_similarity(previous["value"], item.value)
            if similarity >= DESCRIPTION_AUTO_MERGE_THRESHOLD:
                self._dirty = set_last_seen_if_newer(previous, item.version) or self._dirty
                self._dirty = set_if_changed(previous, "similarity_to_latest", round(similarity, 6)) or self._dirty
                self._dirty = add_source(previous.setdefault("sources", []), item.source, item.version) or self._dirty
                return
        else:
            similarity = None

        snapshots.append(
            {
                "hash": value_hash,
                "value": item.value,
                "first_seen": item.version,
                "last_seen": item.version,
                "similarity_to_previous": round(similarity, 6) if similarity is not None else None,
                "review_status": "accepted" if similarity is None else "needs_review",
                "sources": [{"source": item.source, "first_seen": item.version, "last_seen": item.version}],
            }
        )
        self._dirty = True

    def require_champion(self, alias: str) -> dict[str, Any]:
        """返回已存在英雄；不存在时抛出明确错误。"""

        alias_key = canonical_key(self.champions, alias)
        if alias_key is not None:
            return self.champions[alias_key]

        try:
            return self.champions[alias]
        except KeyError as exc:
            raise KeyError(f"Champion must be merged before nested data: {alias}") from exc

    def to_manifest(self) -> dict[str, Any]:
        """生成合集 manifest。"""

        if self.updated_at is None or self._dirty:
            self.updated_at = current_timestamp()

        return {
            "schema_version": SCHEMA_VERSION,
            "updated_at": self.updated_at,
            "languages": list(self.languages),
            "source_versions": sort_mapping(self.source_versions),
            "entities": {
                "champions": len(self.champions),
                "maps": len(self.maps),
                "skinlines": len(self.skinlines),
                "skins": len(self.skins),
                "items": len(self.items),
            },
        }

    def write(self) -> None:
        """把合集稳定写入磁盘。"""

        self.root.mkdir(parents=True, exist_ok=True)
        write_yaml(self.root / "manifest.yaml", self.to_manifest())
        write_entity_group(self.root / "champions", self.champions)
        write_entity_group(self.root / "maps", self.maps)
        write_entity_group(self.root / "skinlines", self.skinlines)
        write_entity_group(self.root / "skins", self.skins)
        write_entity_group(self.root / "items", self.items)
        self._dirty = False


def load_reference_collection(root: Path | str) -> ReferenceCollection:
    """从磁盘读取 reference 合集。"""

    base = Path(root)
    manifest = read_yaml(base / "manifest.yaml") if (base / "manifest.yaml").is_file() else {}
    collection = ReferenceCollection(
        base,
        languages=tuple(manifest.get("languages") or SUPPORTED_LANGUAGES),
        source_versions=dict(manifest.get("source_versions") or {}),
        updated_at=manifest.get("updated_at"),
    )
    collection.champions = read_entity_group(base / "champions")
    collection.maps = read_entity_group(base / "maps")
    collection.skinlines = read_entity_group(base / "skinlines")
    collection.skins = read_entity_group(base / "skins", key_from_path=True)
    collection.items = read_entity_group(base / "items")
    return collection


def canonical_key(rows: dict[str, Any], key: str) -> str | None:
    """按大小写不敏感规则查找已有 key。"""

    if key in rows:
        return key

    normalized = key.casefold()
    for existing in rows:
        if existing.casefold() == normalized:
            return existing

    return None


def skin_reference_key(champion_alias: str, skin_token: str) -> str:
    """返回具体皮肤 reference 分组 key。"""

    return f"{champion_alias}/{skin_token}"


def find_or_create_spell(champion: dict[str, Any], slot: str) -> dict[str, Any]:
    """查找或创建指定技能槽。"""

    spells = champion.setdefault("spells", [])
    for spell in spells:
        if spell.get("slot") == slot:
            return spell

    spell = {"slot": slot, "ids": [], "names": {}}
    spells.append(spell)
    spells.sort(key=lambda row: str(row.get("slot") or ""))
    return spell


def set_if_changed(row: dict[str, Any], key: str, value: Any) -> bool:
    """仅在值变化时写入字段。"""

    if row.get(key) == value:
        return False

    row[key] = value
    return True


def add_sourced_value(rows: list[dict[str, Any]], value: str, source: str, version: str) -> bool:
    """把带来源的值加入数组，已有值只补来源。"""

    for row in rows:
        if row.get("value") == value:
            return add_source(row.setdefault("sources", []), source, version)

    rows.append({"value": value, "sources": [{"source": source, "first_seen": version, "last_seen": version}]})
    rows.sort(key=lambda row: str(row.get("value") or ""))
    return True


def add_string_value(rows: list[str], value: str) -> bool:
    """向字符串集合追加唯一值。"""

    if value in rows:
        return False

    rows.append(value)
    rows.sort()
    return True


def add_sourced_skinline(rows: list[dict[str, Any]], value: dict[str, Any], source: str, version: str) -> bool:
    """向皮肤系列集合追加唯一值，并记录来源。"""

    normalized = {
        key: value[key]
        for key in ["id", "alias", "name"]
        if key in value and value[key] not in (None, "")
    }
    if not normalized:
        return False

    for row in rows:
        if row.get("id") == normalized.get("id") and row.get("alias") == normalized.get("alias"):
            return add_source(row.setdefault("sources", []), source, version)

    normalized["sources"] = [{"source": source, "first_seen": version, "last_seen": version}]
    rows.append(normalized)
    rows.sort(key=lambda row: (row.get("id") is None, row.get("id") or 0, str(row.get("alias") or "")))
    return True


def add_source(rows: list[dict[str, Any]], source: str, version: str) -> bool:
    """在来源数组中记录一次来源确认，按来源折叠为版本区间。

    每个来源仅保留一行 ``{source, first_seen, last_seen}``，再次确认时只
    滚动区间端点——来源行数不随游戏版本数增长（入库数据的硬约束）。
    历史逐版本条目（``{source, version}``）在触碰时就地折叠为区间形式。

    Returns:
        是否发生了改动。
    """

    changed = fold_source_rows(rows)
    for row in rows:
        if row.get("source") == source:
            changed = set_first_seen_if_older(row, version) or changed
            changed = set_last_seen_if_newer(row, version) or changed
            return changed

    rows.append({"source": source, "first_seen": version, "last_seen": version})
    rows.sort(key=lambda row: str(row.get("source") or ""))
    return True


def fold_source_rows(rows: list[dict[str, Any]]) -> bool:
    """把来源数组就地折叠为每来源一行的区间形式。

    兼容历史逐版本条目（``{source, version}``）：同来源多行合并为一行，
    ``first_seen``/``last_seen`` 取所有已见版本的最小/最大值。已是折叠
    形式时不做改动。

    Returns:
        是否发生了改动。
    """

    folded: dict[str, dict[str, Any]] = {}
    for row in rows:
        source = str(row.get("source") or "")
        target = folded.setdefault(source, {"source": source})
        for key in ("version", "first_seen", "last_seen"):
            value = row.get(key)
            if value is None:
                continue
            version = str(value)
            if "first_seen" not in target or version_sort_key(version) < version_sort_key(str(target["first_seen"])):
                target["first_seen"] = version
            if "last_seen" not in target or version_sort_key(version) > version_sort_key(str(target["last_seen"])):
                target["last_seen"] = version

    new_rows = sorted(folded.values(), key=lambda row: str(row.get("source") or ""))
    if new_rows == rows:
        return False

    rows[:] = new_rows
    return True


def set_first_seen_if_older(row: dict[str, Any], version: str) -> bool:
    """仅当版本更早时回拨区间的 first_seen。"""

    current = row.get("first_seen")
    if current is not None and version_sort_key(str(version)) >= version_sort_key(str(current)):
        return False

    row["first_seen"] = version
    return True


def set_last_seen_if_newer(row: dict[str, Any], version: str) -> bool:
    """仅当版本向前推进时更新区间的 last_seen。"""

    current = row.get("last_seen")
    if current is not None and version_sort_key(str(version)) <= version_sort_key(str(current)):
        return False

    row["last_seen"] = version
    return True


def version_sort_key(version: str) -> tuple[int, tuple[int, ...] | str]:
    """返回稳定版本排序键，优先支持 dotted numeric 版本。"""

    parts = version.split(".")
    if parts and all(part.isdigit() for part in parts):
        return (1, tuple(int(part) for part in parts))

    return (0, version)


def text_hash(value: str) -> str:
    """计算文本快照 hash。"""

    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def text_similarity(left: str, right: str) -> float:
    """计算两段文本的确定性相似度。"""

    return SequenceMatcher(None, left, right).ratio()


def write_entity_group(root: Path, rows: dict[str, dict[str, Any]]) -> None:
    """写出一个实体分组目录。"""

    root.mkdir(parents=True, exist_ok=True)
    expected = {Path(f"{key}.yaml") for key in rows}
    for path in sorted(root.rglob("*.yaml")):
        if path.relative_to(root) not in expected:
            path.unlink()

    for key, value in sorted(rows.items()):
        write_yaml(root / f"{key}.yaml", stable_entity(value))

    remove_empty_directories(root)


def read_entity_group(root: Path, *, key_from_path: bool = False) -> dict[str, dict[str, Any]]:
    """读取一个实体分组目录。"""

    if not root.is_dir():
        return {}

    items: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob("*.yaml")):
        data = read_yaml(path)
        if key_from_path:
            alias = path.relative_to(root).with_suffix("").as_posix()
        else:
            alias = data.get("entity", {}).get("alias") or path.stem
        items[str(alias)] = data
    return items


def stable_entity(data: dict[str, Any]) -> dict[str, Any]:
    """返回稳定排序后的实体数据。"""

    entity = dict(data)
    if "spells" in entity:
        entity["spells"] = sorted(entity["spells"], key=lambda row: str(row.get("slot") or ""))
    if "aliases" in entity:
        entity["aliases"] = sorted(entity["aliases"])
    if "skinlines" in entity:
        entity["skinlines"] = sorted(
            entity["skinlines"],
            key=lambda row: (row.get("id") is None, row.get("id") or 0, str(row.get("alias") or "")),
        )
    return entity


def remove_empty_directories(root: Path) -> None:
    """清理分组写入后留下的空子目录。"""

    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        try:
            path.rmdir()
        except OSError:
            pass


def sort_mapping(value: dict[str, Any]) -> dict[str, Any]:
    """递归排序普通 mapping，降低重复写入 diff。"""

    sorted_value: dict[str, Any] = {}
    for key in sorted(value):
        item = value[key]
        if isinstance(item, dict):
            sorted_value[key] = sort_mapping(item)
        else:
            sorted_value[key] = item
    return sorted_value


def read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件。"""

    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"Reference YAML did not decode to a dict: {path}")
    return data


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    """稳定写出 YAML 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    with path.open("w", encoding="utf-8", newline="\n") as file:
        yaml.dump(data, file)


def current_timestamp() -> str:
    """返回 UTC 更新时间。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
