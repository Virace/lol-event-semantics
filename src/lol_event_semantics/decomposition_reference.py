"""事件名拆解参考数据源接入。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .decomposition_model import (
    DecompositionConfidence,
    EventDecompositionRecord,
    EventProfile,
    LexicalAtom,
    SemanticProfile,
    UnknownFragment,
    UnknownKind,
)
from .game_data import compact
from .match_audit import MatchAuditEvent, record_match
from .mode_namespace import ModeNamespace, mode_namespace_for_token, split_mode_entity_alias
from .reference_collection import load_reference_collection
from .rules import ContextualAlias, LowTrustEventRule, RuleBook


SKIN_CONTEXT_PREFIXES = {
    "Ally": "ally",
    "Enemy": "enemy",
    "Near": "near",
}

OWNER_FORM_SUFFIXES = {
    "Dragon": "dragon",
}

NON_SPELL_SELF_ACTION_FAMILIES = frozenset(
    {
        "assist",
        "attack",
        "basic_attack",
        "buy_item",
        "contextual_action",
        "crit_attack",
        "dance",
        "dance_response",
        "death",
        "game_end",
        "heal",
        "homeguard",
        "idle",
        "joke",
        "joke_response",
        "laugh",
        "level_up",
        "move",
        "ping",
        "recall",
        "respawn",
        "shop",
        "taunt",
        "taunt_response",
        "use_item",
        "winddown",
    }
)

MODE_ACTOR_ATTACK_PATTERN = re.compile(
    r"(?P<attack>BasicAttack|CritAttack|RangedAttack|MeleeAttack)(?P<variant>[0-9]*)$",
    re.IGNORECASE,
)
MODE_ACTOR_SLOT_PATTERN = re.compile(r"^(?P<spell_key>[PQWER])(?P<residual>(?:[A-Z0-9][A-Za-z0-9]*)?)$")
MODE_ACTOR_SLOT_DENY_RESIDUALS = frozenset({"tpc"})
GROUPED_ACTION_DIMENSION_PATTERN = re.compile(r"(?P<action>[A-Za-z]+)(?:[23]D)$", re.IGNORECASE)
GROUPED_ACTION_ALIASES = {
    "dance": "dance",
    "death": "death",
    "homeguard": "homeguard",
    "idle": "idle",
    "joke": "joke",
    "laugh": "laugh",
    "recall": "recall",
    "recallin": "recall",
    "respawn": "respawn",
    "respown": "respawn",
    "taunt": "taunt",
    "winddown": "winddown",
}
GROUPED_ACTION_QUALIFIER_LABELS = {
    "a": "A段",
    "anim": "动画音轨",
    "b": "B段",
    "body": "身体音效层",
    "buttons": "按键",
    "camera": "镜头",
    "close": "收起",
    "crystal": "水晶音效层",
    "flash": "闪光",
    "gun": "枪械音效层",
    "gunshot": "枪声",
    "ignite": "点火",
    "lp": "低通音频层",
    "music": "音乐",
    "oneoffs": "独立触发",
    "open": "展开",
}
GROUPED_ACTION_PHASE_TOKENS = frozenset(
    {
        "buffactivate",
        "buffdeactivate",
        "cast",
        "deactivate",
        "end",
        "in",
        "leadin",
        "leadout",
        "loop",
        "out",
        "start",
        "winddown",
        "windup",
    }
)


@dataclass(slots=True)
class ReferenceEntity:
    """外部参考数据中的实体。"""

    key: str
    kind: str
    label: str
    source: str
    id: int | None = None
    alias: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return asdict(self)


@dataclass(slots=True)
class ReferenceSpell:
    """英雄技能或被动的参考词条。"""

    champion_key: str
    spell_key: str
    name: str
    source: str
    terms: dict[str, str] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return asdict(self)


@dataclass(slots=True)
class SpellTermMatch:
    """技能 term 命中的细节。"""

    term: str
    source: str
    residual: str = ""
    match_type: str = "exact"
    stage: int | None = None
    keyword_hits: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ReferenceIndex:
    """game-data 与内置常识词条索引。"""

    entities: dict[str, ReferenceEntity] = field(default_factory=dict)
    maps_by_id: dict[int, ReferenceEntity] = field(default_factory=dict)
    champions_by_alias: dict[str, ReferenceEntity] = field(default_factory=dict)
    champions_by_id: dict[int, ReferenceEntity] = field(default_factory=dict)
    skins_by_alias: dict[str, ReferenceEntity] = field(default_factory=dict)
    skins_by_champion_token: dict[tuple[str, str], ReferenceEntity] = field(default_factory=dict)
    champion_spells_by_alias: dict[str, list[ReferenceSpell]] = field(default_factory=dict)
    rules: RuleBook | None = None

    @classmethod
    def builtins(
        cls,
        rules_root: Path | str | None = None,
        overrides_root: Path | str | None = None,
    ) -> "ReferenceIndex":
        """只加载内置常识词条。"""

        index = cls(rules=load_rulebook(rules_root, overrides_root))
        index.add_builtin_entities()
        index.add_external_entities()
        return index

    @classmethod
    def from_game_data_root(
        cls,
        root: Path | str,
        rules_root: Path | str | None = None,
        overrides_root: Path | str | None = None,
        dragontail_root: Path | str | None = None,
    ) -> "ReferenceIndex":
        """从 rcp-be-lol-game-data 和可选 dragontail 根目录构建参考索引。"""

        base = normalize_game_data_root(Path(root))
        index = cls(rules=load_rulebook(rules_root, overrides_root))
        index.add_builtin_entities()
        index.add_external_entities()
        index.load_maps(base)
        index.load_skinlines(base)
        index.load_champion_summary(base)
        index.load_champion_details(base)

        if dragontail_root is not None:
            index.load_dragontail(normalize_dragontail_root(Path(dragontail_root)))

        return index

    @classmethod
    def from_reference_collection(
        cls,
        root: Path | str,
        rules_root: Path | str | None = None,
        overrides_root: Path | str | None = None,
    ) -> "ReferenceIndex":
        """从仓库内部 reference 合集构建参考索引。"""

        collection = load_reference_collection(root)
        index = cls(rules=load_rulebook(rules_root, overrides_root))
        index.add_builtin_entities()
        index.add_external_entities()
        index.load_reference_collection_maps(collection.maps)
        index.load_reference_collection_skinlines(collection.skinlines)
        index.load_reference_collection_champions(collection.champions)
        index.load_reference_collection_skins(collection.skins)
        return index

    def load_reference_collection_maps(self, rows: dict[str, dict[str, Any]]) -> None:
        """读取内部 reference maps 分组。"""

        for row in rows.values():
            entity_data = row.get("entity")

            if not isinstance(entity_data, dict):
                continue

            id_value = parse_int(str(entity_data.get("id") or ""))

            if id_value is None:
                continue

            label = first_reference_label(entity_data)
            entity = ReferenceEntity(
                key=f"map{id_value}",
                kind="map",
                label=label or f"Map {id_value}",
                source="reference/maps",
                id=id_value,
                alias=string_or_none(entity_data.get("alias")),
                metadata={"reference_alias": entity_data.get("alias")},
            )
            self.maps_by_id[id_value] = entity
            self.entities[entity.key] = entity

    def load_reference_collection_skinlines(self, rows: dict[str, dict[str, Any]]) -> None:
        """读取内部 reference skinlines 分组。"""

        for row in rows.values():
            entity_data = row.get("entity")

            if not isinstance(entity_data, dict):
                continue

            label = first_reference_label(entity_data)

            if label is None:
                continue

            entity = ReferenceEntity(
                key=compact(label),
                kind="skinline",
                label=label,
                source="reference/skinlines",
                id=parse_int(str(entity_data.get("id") or "")),
                alias=string_or_none(entity_data.get("alias")),
                metadata={"reference_alias": entity_data.get("alias")},
            )

            for alias in skinline_aliases(label):
                self.entities.setdefault(alias, entity)

    def load_reference_collection_champions(self, rows: dict[str, dict[str, Any]]) -> None:
        """读取内部 reference champions 分组。"""

        for row in rows.values():
            entity_data = row.get("entity")

            if not isinstance(entity_data, dict):
                continue

            alias = string_or_none(entity_data.get("alias"))

            if alias is None:
                continue

            id_value = parse_int(str(entity_data.get("id") or ""))
            label = first_reference_label(entity_data) or alias
            entity = ReferenceEntity(
                key=compact(alias),
                kind="champion",
                label=label,
                source="reference/champions",
                id=id_value,
                alias=alias,
            )
            self.add_champion_entity(entity)
            spells = reference_collection_spells(row, entity)

            if spells:
                self.champion_spells_by_alias[entity.key] = spells

    def load_reference_collection_skins(self, rows: dict[str, dict[str, Any]]) -> None:
        """读取内部 reference skins 分组。"""

        for row in rows.values():
            entity_data = row.get("entity")

            if not isinstance(entity_data, dict):
                continue

            alias = string_or_none(entity_data.get("alias"))
            champion_alias = string_or_none(entity_data.get("champion_alias"))
            skin_token = string_or_none(entity_data.get("skin_token"))

            if alias is None or champion_alias is None or skin_token is None:
                continue

            skin_number = parse_int(str(entity_data.get("skin_number") or ""))
            entity = ReferenceEntity(
                key=compact(alias),
                kind="skin",
                label=first_reference_label(entity_data) or alias,
                source="reference/skins",
                id=parse_int(str(entity_data.get("id") or "")),
                alias=alias,
                metadata={
                    "champion_alias": champion_alias,
                    "champion_id": parse_int(str(entity_data.get("champion_id") or "")),
                    "skin_number": skin_number,
                    "skin_token": skin_token,
                    "reference_alias": alias,
                    "skinlines": row.get("skinlines") if isinstance(row.get("skinlines"), list) else [],
                },
            )
            aliases = [entity.key, alias, f"{champion_alias}{skin_token}"]

            if skin_number is not None:
                aliases.append(f"{champion_alias}Skin{skin_number}")

            for item in row.get("aliases") or []:
                if isinstance(item, str):
                    aliases.append(item)

            self.add_skin_entity(entity, aliases)

    def resolve_token(self, token: str, raw_event_name: str = "") -> ReferenceEntity | None:
        """根据完整事件上下文解析单个 token。"""

        if not token:
            return None

        map_match = re.fullmatch(r"Map([0-9]+)", token, re.IGNORECASE)

        if map_match is not None:
            map_id = int(map_match.group(1))
            return self.maps_by_id.get(map_id) or map_id_fallback_entity(map_id)

        announcer = parse_announcer_token(token)

        if announcer is not None:
            return announcer

        if compact(token) == "ew" and "emote" in raw_event_name.lower():
            return ReferenceEntity(
                key="ew",
                kind="emote_wheel",
                label="Emote Wheel",
                source="builtin:emotes",
                metadata={"abbreviation": "EW"},
            )

        entity = self.entities.get(compact(token))

        if entity is not None:
            return entity

        canonical = self.resolve_canonical_token(token)

        if canonical is not None:
            return canonical

        legacy = self.resolve_legacy_owner_alias(token)

        if legacy is not None:
            return legacy

        return self.resolve_missing_skin_token(token)

    def resolve_canonical_token(self, token: str) -> ReferenceEntity | None:
        """按规范化 token 回退解析实体。"""

        if self.rules is None:
            return None

        canonical = self.rules.normalize_token(token)

        if canonical == token:
            return None

        return self.entities.get(compact(canonical))

    def resolve_legacy_owner_alias(self, token: str) -> ReferenceEntity | None:
        """按人工维护的历史 owner alias 回退到基础英雄。"""

        if self.rules is None:
            return None

        legacy = self.rules.legacy_for(token)

        if legacy is None or legacy.owner_alias is None:
            return None

        entity = self.champions_by_alias.get(compact(legacy.owner_alias))

        if entity is None:
            return None

        return ReferenceEntity(
            key=compact(token),
            kind=entity.kind,
            label=entity.label,
            source="overrides/legacy_aliases.yaml",
            id=entity.id,
            alias=entity.alias,
            metadata={
                "legacy_alias": token,
                "owner_alias": legacy.owner_alias,
                "skin_en": legacy.skin_en,
                "skin_name": legacy.skin_name,
                "form_name": legacy.form_name,
                "form_key": legacy.form_key,
                "note": legacy.note,
            },
        )

    def resolve_missing_skin_token(self, token: str) -> ReferenceEntity | None:
        """在精确皮肤数据缺失时按英雄别名和 SkinNN 推断皮肤目标。"""

        skinline = self.resolve_skinline_skin_suffix(token)

        if skinline is not None:
            return skinline

        return self.resolve_missing_skin_number_token(token)

    def resolve_skinline_skin_suffix(self, token: str) -> ReferenceEntity | None:
        """解析 CovenSkin 这类把 Skin 当类别后缀的皮肤线 token。"""

        if not token.lower().endswith("skin"):
            return None

        entity = self.entities.get(compact(token[:-4]))

        if entity is not None and entity.kind == "skinline":
            return entity

        return None

    def resolve_missing_skin_number_token(self, token: str) -> ReferenceEntity | None:
        """解析当前皮肤表没有覆盖的英雄 SkinNN 目标。"""

        raw_token = token
        context_prefix: str | None = None

        for prefix, relation in SKIN_CONTEXT_PREFIXES.items():
            if not token.lower().startswith(prefix.lower()):
                continue

            remainder = token[len(prefix) :]

            if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*Skin[0-9]+", remainder, re.IGNORECASE):
                token = remainder
                context_prefix = relation
                break

        match = re.fullmatch(r"(?P<champion>[A-Za-z][A-Za-z0-9]*?)Skin(?P<number>[0-9]+)", token, re.IGNORECASE)

        if match is None:
            return None

        champion = self.find_champion_alias(match.group("champion"))

        if champion is None:
            return None

        skin_number = int(match.group("number"))
        skin_token = f"Skin{skin_number:02d}"
        registered = self.resolve_champion_skin(champion.alias or champion.label, skin_token)

        if registered is not None:
            if context_prefix is None:
                return registered

            metadata = dict(registered.metadata)
            metadata["context_prefix"] = context_prefix
            metadata["raw_token"] = raw_token
            return ReferenceEntity(
                key=compact(raw_token),
                kind=registered.kind,
                label=registered.label,
                source=registered.source,
                id=registered.id,
                alias=raw_token,
                metadata=metadata,
            )

        metadata: dict[str, Any] = {
            "champion_alias": champion.alias,
            "champion_id": champion.id,
            "skin_number": skin_number,
            "skin_token": skin_token,
            "raw_token": raw_token,
            "trust": "missing_current_reference",
            "note": "当前 champion detail skins 列表缺失，按英雄别名和 SkinNN 结构推断。",
        }

        if context_prefix is not None:
            metadata["context_prefix"] = context_prefix

        return ReferenceEntity(
            key=compact(raw_token),
            kind="skin",
            label=f"{champion.label} {skin_token}",
            source="skin_token_fallback",
            alias=raw_token,
            metadata=metadata,
        )

    def resolve_champion_skin(self, champion_alias: str, skin_token: str) -> ReferenceEntity | None:
        """按英雄 alias 和 SkinNN token 查找具体皮肤实体。"""

        return self.skins_by_champion_token.get((compact(champion_alias), compact(skin_token)))

    def find_champion_alias(self, token: str, *, allow_short_prefix: bool = False) -> ReferenceEntity | None:
        """从 token 中按最长安全前缀匹配英雄 alias。"""

        needle = compact(token)
        best: ReferenceEntity | None = None

        for key, entity in self.champions_by_alias.items():
            if needle == key:
                return entity

            if not needle.startswith(key):
                continue

            if len(key) < 4 and not allow_short_prefix:
                continue

            if best is None or len(key) > len(best.key):
                best = entity

        return best

    def add_builtin_entities(self) -> None:
        """注册 game-data 不一定覆盖但事件名常出现的实体。"""

        builtins = [
            ReferenceEntity("tutorial", "tutorial", "Tutorial", "builtin:system"),
            ReferenceEntity("riftherald", "neutral_monster", "Rift Herald", "builtin:monster"),
            ReferenceEntity("encounters", "encounter_system", "Encounters", "builtin:system"),
            ReferenceEntity("augment", "augment_system", "Augment", "builtin:arena"),
            ReferenceEntity("weapon", "augment_weapon", "Weapon Augment", "builtin:arena"),
            ReferenceEntity("augmentweapon", "augment_weapon", "Weapon Augment", "builtin:arena"),
            ReferenceEntity("recall", "action", "Recall", "builtin:action", metadata={"action": "recall"}),
            ReferenceEntity("aram", "game_mode", "ARAM", "builtin:mode"),
            ReferenceEntity("global", "audio_scope", "Global", "builtin:media"),
            ReferenceEntity("base", "media_base", "Base", "builtin:media"),
            ReferenceEntity("stinger", "music_stinger", "Stinger", "builtin:media"),
            ReferenceEntity("phase", "music_phase", "Phase", "builtin:media"),
            ReferenceEntity("gameplay", "gameplay_system", "Gameplay", "builtin:system"),
            ReferenceEntity("item", "item_system", "Item", "builtin:system"),
            ReferenceEntity("emote", "emote_system", "Emote", "builtin:emotes"),
            ReferenceEntity("oncast", "phase_event", "OnCast", "builtin:phase"),
            ReferenceEntity("onbuffactivate", "phase_event", "OnBuffActivate", "builtin:phase"),
            ReferenceEntity("buffactivate", "phase_event", "buffactivate", "builtin:phase"),
        ]

        for entity in builtins:
            self.entities[entity.key] = entity

    def add_external_entities(self) -> None:
        """注册没有可信官方结构源的人工实体词条。"""

        if self.rules is None:
            return

        for entity in self.rules.external_entities.values():
            ref = ReferenceEntity(
                key=compact(entity.token),
                kind=entity.kind,
                label=entity.label,
                source=entity.source,
                metadata=entity.to_metadata(),
            )
            self.entities[ref.key] = ref

    def add_champion_entity(self, entity: ReferenceEntity) -> None:
        """注册英雄实体，并按稳定优先级解决别名冲突。

        规范 alias 优先；同为展示名或兼容别名时，基础英雄优先于扩展模式实体。
        """

        aliases = dedupe_preserve_order(
            [
                entity.key,
                compact(entity.alias or ""),
                compact(entity.label),
                *champion_extra_aliases(entity),
            ]
        )

        for alias in aliases:
            if not alias:
                continue

            champion = self.champions_by_alias.get(alias)
            if champion is None or champion_alias_rank(entity, alias) < champion_alias_rank(champion, alias):
                champion = entity
                self.champions_by_alias[alias] = champion
            self.entities[alias] = champion

        if entity.id is not None:
            self.champions_by_id[entity.id] = entity

    def add_skin_entity(self, entity: ReferenceEntity, aliases: list[str]) -> None:
        """注册皮肤实体的后置反查别名。"""

        self.skins_by_alias.setdefault(entity.key, entity)

        champion_alias = entity.metadata.get("champion_alias")
        skin_token = entity.metadata.get("skin_token")
        if isinstance(champion_alias, str) and isinstance(skin_token, str):
            self.skins_by_champion_token.setdefault((compact(champion_alias), compact(skin_token)), entity)

        for alias in aliases:
            key = compact(alias)

            if not key:
                continue

            self.entities.setdefault(key, entity)
            self.skins_by_alias.setdefault(key, entity)

    def load_maps(self, base: Path) -> None:
        """读取 maps.json。"""

        rows = read_json_if_exists(base / "global" / "default" / "v1" / "maps.json")

        if not isinstance(rows, list):
            return

        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), int):
                continue

            name = str(row.get("name") or f"Map {row['id']}")
            entity = ReferenceEntity(
                key=f"map{row['id']}",
                kind="map",
                label=name,
                source="maps.json",
                id=int(row["id"]),
                metadata={"map_string_id": row.get("mapStringId")},
            )
            self.maps_by_id[entity.id or 0] = entity
            self.entities[entity.key] = entity

    def load_skinlines(self, base: Path) -> None:
        """读取 skinlines.json 中的皮肤线实体。"""

        rows = read_json_if_exists(base / "global" / "default" / "v1" / "skinlines.json")

        if not isinstance(rows, list):
            return

        for row in rows:
            if not isinstance(row, dict):
                continue

            name = str(row.get("name") or "").strip()

            if not name:
                continue

            entity = ReferenceEntity(
                key=compact(name),
                kind="skinline",
                label=name,
                source="skinlines.json",
                id=row.get("id") if isinstance(row.get("id"), int) else None,
                metadata={"description": row.get("description")},
            )

            for alias in skinline_aliases(name):
                self.entities.setdefault(alias, entity)

    def load_champion_summary(self, base: Path) -> None:
        """读取 champion-summary.json 中的英雄 alias。"""

        rows = read_json_if_exists(base / "global" / "default" / "v1" / "champion-summary.json")

        if not isinstance(rows, list):
            return

        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("alias"), str):
                continue

            alias = str(row["alias"])
            label = str(row.get("name") or alias)
            entity = ReferenceEntity(
                key=compact(alias),
                kind="champion",
                label=label,
                source="champion-summary.json",
                id=row.get("id") if isinstance(row.get("id"), int) else None,
                alias=alias,
            )
            self.add_champion_entity(entity)

    def load_champion_details(self, base: Path) -> None:
        """读取 champions/*.json 中的技能名和素材名。"""

        champion_root = base / "global" / "default" / "v1" / "champions"

        if not champion_root.is_dir():
            return

        for path in sorted(champion_root.glob("*.json")):
            row = read_json_if_exists(path)

            if not isinstance(row, dict):
                continue

            entity = self.resolve_champion_detail_entity(row, path)

            if entity is None:
                continue

            self.load_champion_skins(row, entity, f"champions/{path.name}")
            spells = load_spells_from_champion_detail(row, entity, f"champions/{path.name}")

            if spells:
                self.champion_spells_by_alias[entity.key] = spells

    def load_dragontail(self, base: Path) -> None:
        """读取 dragontail champion 数据中的官方技能 id。"""

        for row, source in iter_dragontail_champions(base):
            entity = self.resolve_dragontail_champion_entity(row, source)

            if entity is None:
                continue

            spells = self.champion_spells_by_alias.setdefault(entity.key, [])
            merge_dragontail_spells(spells, row, entity, source)

    def resolve_dragontail_champion_entity(self, row: dict[str, Any], source: str) -> ReferenceEntity | None:
        """从 dragontail champion 行解析或补建英雄实体。"""

        alias = str(row.get("id") or "")
        row_id = parse_int(str(row.get("key") or ""))

        if row_id is not None and row_id in self.champions_by_id:
            return self.champions_by_id[row_id]

        if alias:
            entity = self.champions_by_alias.get(compact(alias))

            if entity is not None:
                return entity

        if not alias:
            return None

        entity = ReferenceEntity(
            key=compact(alias),
            kind="champion",
            label=str(row.get("name") or alias),
            source=source,
            id=row_id,
            alias=alias,
        )
        self.add_champion_entity(entity)
        return entity

    def load_champion_skins(self, row: dict[str, Any], champion: ReferenceEntity, source: str) -> None:
        """读取 champion detail 内的 skins 列表作为后置目标反查源。"""

        skins = row.get("skins")

        if not isinstance(skins, list):
            return

        for skin in skins:
            if not isinstance(skin, dict):
                continue

            skin_number = skin_number_from_row(skin, champion)

            if skin_number is None:
                continue

            skin_token = f"Skin{skin_number:02d}"
            raw_alias = f"{champion.alias or champion.label}{skin_token}"
            label = str(skin.get("name") or raw_alias)
            entity = ReferenceEntity(
                key=compact(raw_alias),
                kind="skin",
                label=label,
                source=source,
                id=skin.get("id") if isinstance(skin.get("id"), int) else None,
                alias=raw_alias,
                metadata={
                    "champion_alias": champion.alias,
                    "champion_id": champion.id,
                    "skin_number": skin_number,
                    "skin_token": skin_token,
                    "skin_lines": skin.get("skinLines"),
                },
            )
            self.add_skin_entity(
                entity,
                [
                    raw_alias,
                    f"{champion.alias or champion.label}Skin{skin_number}",
                    label,
                ],
            )

    def resolve_champion_detail_entity(self, row: dict[str, Any], path: Path) -> ReferenceEntity | None:
        """从 champion detail 解析或补建英雄实体。"""

        row_id = row.get("id") if isinstance(row.get("id"), int) else parse_int(path.stem)
        alias = str(row.get("alias") or "")

        if not alias and row_id is not None and row_id in self.champions_by_id:
            return self.champions_by_id[row_id]

        if not alias:
            return None

        key = compact(alias)
        entity = self.champions_by_alias.get(key)

        if entity is not None:
            return entity

        label = str(row.get("name") or alias)
        entity = ReferenceEntity(
            key=key,
            kind="champion",
            label=label,
            source=f"champions/{path.name}",
            id=row_id,
            alias=alias,
        )
        self.add_champion_entity(entity)
        return entity


def enrich_with_reference(
    record: EventDecompositionRecord,
    reference: ReferenceIndex | None,
) -> EventDecompositionRecord:
    """用参考数据补充语义 slot 和候选解释。"""

    if reference is None:
        return record

    add_token_candidates(record, reference)
    enrich_unknown_profile(record, reference)
    resolve_ambiguous_entities(record, reference)
    add_sfx_cue_champion_context(record, reference)
    # 命名空间与真实演员必须先于别名/技能推断建立；Ruby、OdysseyAugments
    # 等前缀只是上下文，不能占用英雄 owner。
    add_mode_namespace_candidate(record, reference)
    add_mode_champion_actor_candidate(record, reference)
    add_owner_form_context(record, reference)
    add_grouped_champion_action_context(record, reference)
    add_contextual_alias_candidates(record, reference)
    apply_tail_modifier_rules(record, reference)
    apply_kill_spell_method(record, reference)
    add_mode_attack_candidate(record, reference)
    add_ultimate_spellbook_spell_candidate(record, reference)
    add_inferred_champion_spell_candidate(record, reference)
    add_spell_combo_candidate(record, reference)
    add_composite_owner_candidate(record, reference)
    apply_low_trust_event_rule(record, reference)
    from .decomposition_profiles import fill_spell_context

    fill_spell_context(record)
    resolve_ambiguous_entities(record, reference)
    add_augment_borrowed_ability(record, reference)
    return record


def add_augment_borrowed_ability(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """以完整增益 cue 和英雄参考保存借用技能，模式主体不改写为英雄。"""

    slots = record.semantic_slots
    entity = slots.get("reference_entity")
    cue = slots.get("cue_path")
    if (
        not isinstance(entity, dict)
        or entity.get("kind") not in {"augment_system", "game_mode", "item_system"}
        or not isinstance(cue, list)
        or len(cue) < 3
        or str(cue[0]).lower() not in {"aram", "augment", "item"}
    ):
        return
    raw = str(cue[2])
    for subject in champion_spell_subjects(raw, None, reference):
        alias = subject.alias or ""
        if not alias or not raw.casefold().startswith(alias.casefold()):
            continue
        match = MODE_ACTOR_SLOT_PATTERN.fullmatch(raw[len(alias) :])
        if match is None:
            continue
        spell_key = match.group("spell_key").lower()
        if spell_for_key(reference, subject, spell_key) is None:
            continue
        slots["borrowed_ability"] = {
            "raw": raw,
            "subject": subject.to_dict(),
            "spell_key": spell_key,
            "residual": match.group("residual"),
            "source": "reference_champion_and_explicit_slot",
        }
        spell_match = slots.get("spell_match")
        stage = slots.get("spell_stage")
        if (
            isinstance(spell_match, dict)
            and spell_match.get("raw") == raw
            and slots.get("spell_key") == spell_key
            and isinstance(stage, int)
            and stage > 0
        ):
            slots["borrowed_ability"]["stage"] = stage
            if spell_match.get("match_source") == "spell_key_stage" and spell_match.get("residual") == "":
                slots["borrowed_ability"]["residual"] = ""
        return


def add_sfx_cue_champion_context(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
) -> None:
    """从 HUD/UI 外壳内已核验的皮肤 token 恢复真实英雄主体。

    ``Play_sfx_hud_LuxSkin07_...`` 一类名字把 ``hud`` 放在 owner 位置，
    但真正归属仍由 cue path 中可在参考表精确解析的 ``ChampionSkinNN``
    决定。这里只处理原本被判为系统 SFX 的 HUD/UI 外壳，并要求命中正式
    skin 实体，避免把普通系统 token 或名字前缀猜成英雄。
    """

    if record.shell.asset_kind != "sfx" or record.semantic_slots.get("sfx_scope") != "system_sfx":
        return

    owner = compact(record.owner.raw_owner_token or record.owner.alias or "")
    if owner not in {"hud", "ui"}:
        return

    cue_path = record.semantic_slots.get("cue_path")
    if not isinstance(cue_path, list):
        return

    for token in cue_path:
        if not isinstance(token, str):
            continue
        skin = reference.resolve_token(token, record.raw_event_name)
        if skin is None or skin.kind != "skin":
            continue

        if set_cue_skin_champion_context(record, reference, skin, raw_token=token):
            return

    # 部分 HUD 事件把 ``MissFortuneSkin16`` 拆成相邻的
    # ``MissFortune``/``Skin16`` 两段。只有两段相邻、前段精确命中英雄且
    # (英雄, SkinNN) 能在皮肤参考表中反查时才恢复，避免把任意系统词和
    # 孤立 SkinNN 拼成角色。
    for index, token in enumerate(cue_path[:-1]):
        skin_token = cue_path[index + 1]
        if not isinstance(token, str) or not isinstance(skin_token, str):
            continue
        if re.fullmatch(r"Skin[0-9]+", skin_token, re.IGNORECASE) is None:
            continue
        champion = reference.resolve_token(token, record.raw_event_name)
        if champion is None or champion.kind != "champion" or champion.alias is None:
            continue
        skin = reference.resolve_champion_skin(champion.alias, skin_token)
        if skin is None:
            continue
        if set_cue_skin_champion_context(
            record,
            reference,
            skin,
            raw_token=f"{token}_{skin_token}",
        ):
            return


def set_cue_skin_champion_context(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
    skin: ReferenceEntity,
    *,
    raw_token: str,
) -> bool:
    """把已精确解析的 cue 皮肤实体写回英雄主体与皮肤上下文。"""

    champion_alias = skin.metadata.get("champion_alias")
    skin_token = skin.metadata.get("skin_token")
    if not isinstance(champion_alias, str) or not isinstance(skin_token, str):
        return False

    champion = reference.champions_by_alias.get(compact(champion_alias))
    if champion is None:
        return False

    record.semantic_slots["subject"] = champion.to_dict()
    record.semantic_slots["subject_hint"] = champion.alias or champion.label
    record.semantic_slots["skin_token"] = skin_token
    record.semantic_slots["sfx_scope"] = "champion_sfx"
    record.semantic_slots["cue_skin_context"] = {
        "raw": raw_token,
        "skin_token": skin_token,
        "source": skin.source,
    }
    return True


def add_token_candidates(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """为所有词法 atom 添加参考候选。"""

    for atom in record.lexical_atoms:
        entity = reference.resolve_token(atom.raw, record.raw_event_name)

        if entity is not None:
            append_candidate(record, entity_candidate(record, atom.raw, entity, confidence="high"))

        for part in atom.parts:
            if part.kind not in {"residue_token", "trigger_token"}:
                continue

            entity = reference.resolve_token(part.raw, record.raw_event_name)

            if entity is not None:
                append_candidate(record, entity_candidate(record, part.raw, entity, confidence="high"))


def enrich_unknown_profile(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """用参考候选把明确系统类未知 profile 转成结构化 profile。"""

    if is_switch_record(record):
        set_switch_profile(record)
        return

    pre_context = record.semantic_slots.get("pre_context")
    if (
        record.shell.asset_kind == "vo"
        and compact(record.owner.alias or "") == "shyvana"
        and isinstance(pre_context, dict)
        and pre_context.get("raw") == "Dragon"
        and record.profile.primary_family in {"attack", "joke", "kill", "taunt"}
    ):
        # 希瓦娜的 Dragon_<动作> 是施动者的龙形态，不能按同名野怪
        # 覆盖已经拆解出的攻击、表情或击杀动作。
        form = {
            "raw": "Dragon",
            "kind": "champion_form_qualifier",
            "component": "dragon_form",
            "label": "龙形态",
            "source": "owner_scoped_leading_voice_context",
            "confidence": "high",
        }
        record.semantic_slots["owner_form"] = form
        record.semantic_slots["action_form"] = form
        return

    first_token = first_interesting_token(record)

    if first_token is None:
        return

    entity = reference.resolve_token(first_token, record.raw_event_name)

    if entity is None:
        return

    if entity.kind == "map":
        set_map_profile(record, entity)
        fill_shopkeeper_voice_slots(record)
    elif entity.kind == "announcer":
        set_announcer_profile(record, entity)
    elif entity.kind == "action":
        set_action_profile(record, entity)
    elif entity.kind in {
        "tutorial",
        "neutral_monster",
        "encounter_system",
        "augment_system",
        "augment_weapon",
        "emote_wheel",
        "announcer_persona",
        "game_mode",
        "audio_scope",
        "media_base",
        "music_stinger",
        "music_phase",
        "gameplay_system",
        "item_system",
        "emote_system",
        "phase_event",
    }:
        set_system_profile(record, entity)

    if is_switch_record(record):
        set_switch_profile(record)


def resolve_ambiguous_entities(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把已由参考数据确认的实体从 ambiguous unknown 中移除。"""

    for unknown in list(record.unknowns):
        if unknown.kind is not UnknownKind.AMBIGUOUS_ENTITY:
            continue

        if (
            unknown.location == "semantic_slots.spell_context"
            and re.fullmatch(r"Skin[0-9]+", unknown.raw, re.IGNORECASE)
            and compact(unknown.raw) == compact(record.owner.skin_token or "")
        ):
            subject = owner_champion_entity(record, reference)
            skin = reference.resolve_champion_skin(subject.alias or subject.key, unknown.raw) if subject else None
            if skin is not None:
                set_slot_value(
                    record.semantic_slots,
                    unknown.location,
                    {**skin.to_dict(), "context_scope": "speaker_skin"},
                )
                record.unknowns.remove(unknown)
                continue

        structured = structured_counterpart_for_token(unknown.raw, record, reference)

        if structured is not None:
            set_slot_value(record.semantic_slots, unknown.location, structured)
            append_candidate(
                record,
                {
                    "kind": "structured_counterpart",
                    "raw_event_name": record.raw_event_name,
                    "raw": unknown.raw,
                    "counterpart": structured,
                    "confidence": "medium",
                    "evidence": "full event name plus reference entity index",
                },
            )
            record.unknowns.remove(unknown)
            continue

        entity = reference.resolve_token(unknown.raw, record.raw_event_name)

        if entity is None:
            continue

        set_slot_entity(record.semantic_slots, unknown.location, entity)
        record.unknowns.remove(unknown)


def add_contextual_alias_candidates(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """按 owner 或模式上下文应用人工审核过的别名规则。"""

    if reference.rules is None:
        return

    if apply_champion_contextual_alias(record, reference):
        return

    if apply_sfx_contextual_alias(record, reference):
        return

    apply_mode_contextual_alias(record, reference)


def add_owner_form_context(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把已审阅的复合 owner 形态保存在主体和动作语义中。"""

    owner_candidates = dedupe_preserve_order(
        [
            record.owner.alias or "",
            record.owner.raw_owner_token or "",
        ]
    )
    legacy_owner = None
    owner = ""
    for owner in owner_candidates:
        if not owner:
            continue
        legacy_owner = reference.resolve_legacy_owner_alias(owner)
        if legacy_owner is not None:
            break

    if legacy_owner is None:
        return

    form_name = legacy_owner.metadata.get("form_name")
    form_key = legacy_owner.metadata.get("form_key")
    if not isinstance(form_name, str) or not isinstance(form_key, str):
        return

    form_payload: dict[str, str | None] = {
        "raw": owner,
        "kind": "champion_form_qualifier",
        "label": form_name,
        "component": form_key,
        "source": "overrides/legacy_aliases.yaml",
        "confidence": "high",
        "note": str(legacy_owner.metadata.get("note") or "") or None,
    }
    record.semantic_slots.setdefault("subject", legacy_owner.to_dict())
    record.semantic_slots.setdefault("owner_form", form_payload)
    if record.profile.semantic_profile is SemanticProfile.SELF_ACTION:
        record.semantic_slots.setdefault("action_form", form_payload)

    action = normalize_trigger_family(record.profile.primary_family)
    if record.shell.asset_kind != "sfx" or action not in {"dance", "joke", "laugh", "recall", "taunt"}:
        return

    phase = first_form_action_phase_value(record)
    if phase is None:
        return

    if action == "recall" and record.owner.skin_token is None:
        # skin_recall_state 的契约要求真实 SkinNN；基础形态 owner 没有皮肤
        # 角色，继续使用 recall/champion_action_event，但补回明确生命周期。
        record.semantic_slots["phase"] = phase
        record.semantic_slots["recall_action"] = "recall"
        return

    primary_family = "skin_recall_state" if action == "recall" else "skin_action_state"
    record.profile = EventProfile(
        primary_family=primary_family,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="resolved reviewed form owner self-action lifecycle",
    )
    record.semantic_slots["trigger_family"] = primary_family
    record.semantic_slots["subject_hint"] = legacy_owner.alias or legacy_owner.label
    record.semantic_slots["skin_token"] = record.owner.skin_token
    record.semantic_slots["action"] = action
    record.semantic_slots["phase"] = phase
    if action == "recall":
        record.semantic_slots["recall_action"] = "recall"


def add_grouped_champion_action_context(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
) -> None:
    """把 ``Emote/Base/<动作>/<尾段>`` 拆成动作、阶段与结构化修饰语。

    Emote/Base 只是资源分组，不是实体或用户可见动作。这里只处理已确认的
    champion_sfx，并使用封闭动作/修饰语词表；表外动作继续留在原 profile，
    防止把系统 UI 或任意英文 token 猜成英雄动作。
    """

    if (
        record.shell.asset_kind != "sfx"
        or record.semantic_slots.get("sfx_scope") != "champion_sfx"
        or record.profile.primary_family not in {"emote_system", "media_base"}
    ):
        return

    owner_context = record.semantic_slots.get("owner_context")
    owner_alias = str(owner_context.get("alias") or "") if isinstance(owner_context, dict) else ""
    subject = reference.champions_by_alias.get(compact(owner_alias))
    if subject is None:
        subject = reference.resolve_legacy_owner_alias(owner_alias)
    if subject is None:
        existing_subject = record.semantic_slots.get("subject")
        if isinstance(existing_subject, dict):
            subject_alias = str(existing_subject.get("alias") or "")
            subject = reference.champions_by_alias.get(compact(subject_alias))
    if subject is None:
        return

    cue_path = record.semantic_slots.get("cue_path")
    if not isinstance(cue_path, list):
        return
    expected_group = "emote" if record.profile.primary_family == "emote_system" else "base"
    action_index = None
    for index, token in enumerate(cue_path[:-1]):
        if isinstance(token, str) and compact(token) == expected_group:
            action_index = index + 1
            break
    if action_index is None:
        return

    raw_action = cue_path[action_index]
    if not isinstance(raw_action, str):
        return
    dimension_match = GROUPED_ACTION_DIMENSION_PATTERN.fullmatch(raw_action)
    action_word = dimension_match.group("action") if dimension_match is not None else raw_action
    action = GROUPED_ACTION_ALIASES.get(compact(action_word))
    if action is None:
        return

    tail = [token for token in cue_path[action_index + 1 :] if isinstance(token, str)]
    qualifiers = grouped_action_qualifier_payloads(tail)
    record.semantic_slots["grouped_action"] = {
        "group": expected_group,
        "raw": raw_action,
        "key": action,
        "dimension_token": dimension_match.group(0)[-2:].upper() if dimension_match is not None else None,
        "source": "grouped_champion_sfx_cue",
    }
    if qualifiers:
        record.semantic_slots["action_qualifiers"] = qualifiers

    if record.profile.primary_family == "media_base":
        return

    if compact(action_word) == "recallin":
        phase = "in"
    elif action == "winddown":
        # winddown 在这里是动作名本身，不得再次当作尾段阶段吞掉后续
        # buffactivate；真正生命周期仍由其余 phase atom 决定。
        phase = first_phase_value(record)
    else:
        phase = first_form_action_phase_value(record)
    if phase is not None:
        record.semantic_slots["phase"] = phase

    record.profile = EventProfile(
        primary_family=action,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="resolved champion Emote group action cue",
    )
    record.semantic_slots["trigger_family"] = action
    record.semantic_slots["subject"] = subject.to_dict()
    record.semantic_slots["subject_hint"] = subject.alias or subject.label
    if action == "recall":
        record.semantic_slots["recall_action"] = "recall"
    clear_unknowns_by_keys(record, {expected_group, raw_action, *tail})


def grouped_action_qualifier_payloads(tokens: list[str]) -> list[dict[str, str]]:
    """提取动作 cue 中已审阅的姿势、音层、等级与编号修饰语。"""

    qualifiers: list[dict[str, str]] = []
    seen: set[str] = set()
    for token in tokens:
        key = compact(token)
        label = GROUPED_ACTION_QUALIFIER_LABELS.get(key)
        if label is None:
            match = re.fullmatch(r"pose(?P<number>[0-9]+)", key)
            if match is not None:
                label = f"姿势{int(match.group('number'))}"
            else:
                match = re.fullmatch(r"lv(?P<number>[0-9]+)", key)
                if match is not None:
                    label = f"{int(match.group('number'))}级"
                else:
                    match = re.fullmatch(r"buffactivate(?P<number>[0-9]+)", key)
                    if match is not None:
                        label = f"激活变体{int(match.group('number'))}"
                    elif re.fullmatch(r"0*[1-9][0-9]*", key) is not None:
                        label = f"{int(key)}号变体"
        if label is None or key in GROUPED_ACTION_PHASE_TOKENS or key in seen:
            continue
        qualifiers.append({"key": key, "label": label, "raw": token})
        seen.add(key)
    return qualifiers


def apply_sfx_contextual_alias(record: EventDecompositionRecord, reference: ReferenceIndex) -> bool:
    """应用公共 SFX 效果别名。"""

    if reference.rules is None:
        return False

    effect_base = record.semantic_slots.get("effect_base")

    if not isinstance(effect_base, str):
        return False

    owner_context = record.semantic_slots.get("owner_context")
    subject = record.semantic_slots.get("subject")
    raw_owner = record.owner.raw_owner_token or record.owner.alias
    legacy_owner = reference.resolve_legacy_owner_alias(raw_owner) if raw_owner else None
    lookup_owners = dedupe_preserve_order(
        [
            record.owner.raw_owner_token or "",
            record.owner.alias or "",
            str(owner_context.get("alias") or "") if isinstance(owner_context, dict) else "",
            str(subject.get("alias") or "") if isinstance(subject, dict) else "",
            legacy_owner.alias if legacy_owner is not None and legacy_owner.alias is not None else "",
        ]
    )
    rule = None
    matched_token = effect_base
    for owner in lookup_owners:
        if not owner:
            continue
        lookup_tokens = contextual_alias_effect_tokens(record)
        for atom in record.lexical_atoms:
            lookup_tokens.extend(contextual_alias_lookup_tokens(atom, owner))
        for token in dedupe_preserve_order(lookup_tokens):
            candidate = reference.rules.contextual_alias_for("sfx", owner, token)
            if candidate is None or not champion_contextual_alias_matches_record(record, candidate):
                continue
            rule = candidate
            matched_token = token
            break
        if rule is not None:
            break

    if rule is None:
        rule = reference.rules.contextual_alias_for("sfx", "global", effect_base)
        matched_token = effect_base
        if rule is not None and not champion_contextual_alias_matches_record(record, rule):
            rule = None

    if rule is None:
        return False

    record.semantic_slots["sfx_alias"] = {
        "raw": matched_token,
        "kind": rule.kind,
        "label": rule.label,
        "spell": rule.spell,
        "component": rule.component,
        "source": rule.source,
        "confidence": rule.confidence,
        "note": rule.note,
        "qualifiers": contextual_alias_qualifier_payloads(record, rule),
    }
    append_candidate(record, contextual_alias_candidate(record, matched_token, rule, subject=None))
    audit_contextual_alias_hit(record, rule, matched_token)
    record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
    if rule.kind == "generic_sfx_effect":
        # EvolveSelf 一类普通英文词会被通用词首启发式暂记为 E 技能提示。
        # 人工 owner-scoped 规则已经确认它是角色音效后，应同时撤销该弱提示
        # 与对应残留，避免语义层继续伪装成 spell_event。
        record.profile = EventProfile(
            primary_family="sfx",
            semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
            requires_context=False,
            reason="resolved reviewed owner-scoped generic SFX effect",
        )
        record.semantic_slots["trigger_family"] = "sfx_event"
        record.semantic_slots.pop("spell_key_hint", None)
        record.semantic_slots.pop("effect_residual", None)
    elif rule.kind == "champion_spell_component" and rule.spell in {"p", "q", "w", "e", "r"}:
        spell_subject = legacy_owner
        if spell_subject is None:
            for owner in lookup_owners:
                spell_subject = reference.champions_by_alias.get(compact(owner))
                if spell_subject is not None:
                    break
        if spell_subject is not None:
            record.profile = EventProfile(
                primary_family="spell",
                semantic_profile=SemanticProfile.SPELL_EVENT,
                requires_context=True,
                reason="resolved owner-scoped SFX spell component",
            )
            record.semantic_slots["trigger_family"] = "spell"
            record.semantic_slots["subject"] = spell_subject.to_dict()
            record.semantic_slots["spell_key"] = rule.spell
            record.semantic_slots.pop("spell_key_hint", None)
    clear_contextual_unknowns(record, matched_token, rule)
    return True


def backfill_skin_token_from_owner(record: EventDecompositionRecord, owner_alias: str) -> None:
    """从复合 owner alias 中提取皮肤段回填 skin_token。

    仅在 record 尚无 skin_token 时回填，避免覆盖 lexer 已解析的值。
    """
    if record.semantic_slots.get("skin_token"):
        return
    match = re.search(r"Skin[0-9]+", owner_alias)
    if match is not None:
        record.semantic_slots["skin_token"] = match.group(0)


def apply_champion_contextual_alias(record: EventDecompositionRecord, reference: ReferenceIndex) -> bool:
    """应用英雄上下文别名。"""

    subject_slot = record.semantic_slots.get("subject")
    owner_alias = (
        str(subject_slot.get("alias") or subject_slot.get("key") or "")
        if isinstance(subject_slot, dict)
        else record.owner.alias
    )

    if not owner_alias:
        return False

    subject, lookup_owners, owner_form = resolve_contextual_owner(owner_alias, reference)

    if subject is None:
        return False

    for index, atom in enumerate(record.lexical_atoms):
        for lookup_owner in lookup_owners:
            for token in contextual_alias_lookup_tokens(atom, lookup_owner):
                rule = (
                    reference.rules.contextual_alias_for("champion", lookup_owner, token) if reference.rules else None
                )

                if rule is None:
                    continue

                if not champion_contextual_alias_matches_record(record, rule):
                    continue

                if (
                    record.profile.semantic_profile is SemanticProfile.INTERACTION
                    and rule.kind == "champion_action_component"
                ):
                    # FirstEncounter2DLunari 一类事件里，尾部 token 是交互对象；
                    # 同名角色动作规则不得反向覆盖已经成立的 interaction。
                    continue

                if owner_form is not None:
                    record.semantic_slots["owner_form"] = {
                        "raw": owner_alias,
                        "base_owner": lookup_owner,
                        "form": owner_form,
                        "source": "owner_suffix_heuristic",
                    }
                    backfill_skin_token_from_owner(record, owner_alias)

                append_candidate(record, contextual_alias_candidate(record, atom.raw, rule, subject))
                audit_contextual_alias_hit(record, rule, atom.raw)
                set_champion_contextual_alias_profile(record, subject, atom, index, rule)
                return True

    if not all(is_phase_like_atom(atom) for atom in record.lexical_atoms):
        return False

    for token in contextual_alias_effect_tokens(record):
        for lookup_owner in lookup_owners:
            rule = reference.rules.contextual_alias_for("champion", lookup_owner, token) if reference.rules else None

            if rule is None:
                continue

            if not champion_contextual_alias_matches_record(record, rule):
                continue

            if (
                record.profile.semantic_profile is SemanticProfile.INTERACTION
                and rule.kind == "champion_action_component"
            ):
                continue

            if owner_form is not None:
                record.semantic_slots["owner_form"] = {
                    "raw": owner_alias,
                    "base_owner": lookup_owner,
                    "form": owner_form,
                    "source": "owner_suffix_heuristic",
                }
                backfill_skin_token_from_owner(record, owner_alias)

            atom = LexicalAtom(
                raw=token,
                normalized=token,
                chunk_index=-1,
                parts=[],
                source="semantic:effect_base",
                confidence=DecompositionConfidence.MEDIUM,
            )
            append_candidate(record, contextual_alias_candidate(record, token, rule, subject))
            audit_contextual_alias_hit(record, rule, token)
            set_champion_contextual_alias_profile(record, subject, atom, -1, rule)
            return True

    return False


def champion_contextual_alias_matches_record(
    record: EventDecompositionRecord,
    rule: ContextualAlias,
) -> bool:
    """限定英雄别名只在规则声明的资产类型和模式内生效。"""

    if (
        compact(rule.owner) == "viktor"
        and rule.component == "permanent_ability_augment"
        and record.shell.verb != "play"
    ):
        return False

    if rule.asset_kind is not None:
        asset_kind = record.shell.asset_kind
        if asset_kind is None or compact(asset_kind) != compact(rule.asset_kind):
            return False

    if rule.required_tokens:
        cue_path = record.semantic_slots.get("cue_path")
        available_tokens = (
            {compact(token) for token in cue_path if isinstance(token, str)}
            if isinstance(cue_path, list)
            else {compact(atom.raw) for atom in record.lexical_atoms}
        )
        if any(token not in available_tokens for token in rule.required_tokens):
            return False

    if rule.mode is None:
        return True

    expected_mode = compact(rule.mode)
    for slot_name in ("context_namespace", "mode_context"):
        context = record.semantic_slots.get(slot_name)
        if isinstance(context, dict) and compact(str(context.get("key") or "")) == expected_mode:
            return True

    return False


def resolve_contextual_owner(
    owner_alias: str, reference: ReferenceIndex
) -> tuple[ReferenceEntity | None, list[str], str | None]:
    """解析 contextual alias 可用的英雄 owner 与形态后缀。"""

    subject = reference.champions_by_alias.get(compact(owner_alias))

    if subject is not None:
        return subject, [owner_alias], None

    legacy_subject = reference.resolve_legacy_owner_alias(owner_alias)

    if legacy_subject is not None and legacy_subject.alias is not None:
        form_name = None

        if legacy_subject.metadata:
            form_name = legacy_subject.metadata.get("form_name")

        return legacy_subject, [legacy_subject.alias, owner_alias], form_name

    for suffix, form in OWNER_FORM_SUFFIXES.items():
        if not compact(owner_alias).endswith(compact(suffix)):
            continue

        base_owner = owner_alias[: -len(suffix)]

        if not base_owner:
            continue

        subject = reference.champions_by_alias.get(compact(base_owner))

        if subject is not None:
            return subject, [base_owner, owner_alias], form

    return None, [], None


def contextual_alias_lookup_tokens(atom: LexicalAtom, owner_alias: str) -> list[str]:
    """返回上下文别名查找 token，包含去 owner、residue 与去数字尾缀形态。"""

    raw_tokens = [atom.raw]
    residue = part_raw(atom, "residue_token")

    if residue is not None:
        raw_tokens.append(residue)

    tokens: list[str] = []

    for raw in raw_tokens:
        stripped = strip_owner_alias(raw, owner_alias)
        tokens.extend([raw, stripped])

    tokens.extend(re.sub(r"[0-9]+$", "", token) for token in list(tokens))
    return dedupe_preserve_order([token for token in tokens if token])


def contextual_alias_effect_tokens(record: EventDecompositionRecord) -> list[str]:
    """返回被 owner 槽吞掉的 SFX 效果词候选。"""

    tokens = [
        str(record.semantic_slots.get("effect_base") or ""),
        str(record.semantic_slots.get("effect_head") or ""),
        record.owner.raw_owner_token or "",
        record.owner.alias or "",
    ]
    tokens.extend(re.sub(r"[0-9]+$", "", token) for token in list(tokens))
    return dedupe_preserve_order([token for token in tokens if token])


def apply_mode_contextual_alias(record: EventDecompositionRecord, reference: ReferenceIndex) -> bool:
    """应用模式上下文别名。"""

    owner = record.owner.raw_owner_token or record.owner.alias

    if owner is None or reference.rules is None:
        return False

    for index, atom in enumerate(record.lexical_atoms):
        rule = reference.rules.contextual_alias_for("mode", owner, atom.raw)

        if rule is None:
            continue

        append_candidate(record, contextual_alias_candidate(record, atom.raw, rule, subject=None))
        audit_contextual_alias_hit(record, rule, atom.raw)
        set_mode_contextual_alias_profile(record, atom, index, rule)
        return True

    for context_index, context_atom in enumerate(record.lexical_atoms):
        for index, atom in enumerate(record.lexical_atoms[context_index + 1 :], start=context_index + 1):
            rule = reference.rules.contextual_alias_for("mode", context_atom.raw, atom.raw)

            if rule is None:
                continue

            append_candidate(record, contextual_alias_candidate(record, atom.raw, rule, subject=None))
            audit_contextual_alias_hit(record, rule, atom.raw)
            set_mode_contextual_alias_profile(record, atom, index, rule)
            return True

    return False


def add_mode_champion_actor_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """识别 Ruby_Brand_P 这类模式前缀下的英雄演员。"""

    owner = record.owner.raw_owner_token or record.owner.alias

    if compact(owner or "") != "ruby" or not record.lexical_atoms:
        return

    actor_atom = record.lexical_atoms[0]
    actor = reference.resolve_token(actor_atom.raw, record.raw_event_name)

    if actor is None or actor.kind != "champion":
        return

    append_candidate(
        record,
        {
            "kind": "mode_champion_actor",
            "raw_event_name": record.raw_event_name,
            "raw": f"{owner}_{actor_atom.raw}",
            "mode": "ruby",
            "mode_label": "末日人机",
            "actor": actor.to_dict(),
            "confidence": "medium",
            "evidence": "Ruby mode owner followed by champion alias in full event name",
            "meaning": f"{actor.alias or actor.label} acts as a Ruby/Doom Bots champion actor.",
        },
    )
    record.semantic_slots["mode_context"] = {
        "key": "ruby",
        "label": "末日人机",
        "source": "mode_actor_convention",
    }
    record.semantic_slots["mode_actor"] = actor.to_dict()

    if len(record.lexical_atoms) < 2:
        clear_unknowns_by_keys(record, {actor.key, compact(actor_atom.raw)})
        return

    spell_atom = record.lexical_atoms[1]
    spell_key = part_value(spell_atom, "spell_key_token")

    if spell_key not in {"p", "q", "w", "e", "r"}:
        clear_unknowns_by_keys(record, {actor.key, compact(actor_atom.raw)})
        return

    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason="resolved Ruby mode champion actor followed by spell key",
    )
    record.semantic_slots["trigger_family"] = "spell"
    record.semantic_slots["subject"] = actor.to_dict()
    record.semantic_slots["spell_key"] = spell_key

    spell_variant = first_number(spell_atom)

    if spell_variant is not None:
        record.semantic_slots["spell_variant"] = spell_variant
        record.semantic_slots["spell_stage"] = spell_variant

    unresolved = unresolved_context_after(record, 1)

    if unresolved:
        record.semantic_slots["unresolved_context"] = unresolved

    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    backfill_phase(record)
    clear_unknowns_by_keys(record, {actor.key, compact(actor_atom.raw), compact(spell_atom.raw), spell_key})


def add_mode_attack_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把模式 map 资产中的英雄或已审阅模式单位攻击恢复为攻击事件。"""

    if not isinstance(record.semantic_slots.get("mode_context"), dict):
        return

    actor = owner_champion_entity(record, reference)

    matched_atom: LexicalAtom | None = None
    match: re.Match[str] | None = None
    for atom in record.lexical_atoms:
        candidate = MODE_ACTOR_ATTACK_PATTERN.search(atom.raw)
        if candidate is not None:
            matched_atom = atom
            match = candidate
            break

    if matched_atom is None or match is None:
        return

    attack_token = match.group("attack").lower()
    attack_kind = "crit_attack" if attack_token == "critattack" else "basic_attack"
    attack_form = {
        "rangedattack": "ranged",
        "meleeattack": "melee",
    }.get(attack_token)
    variant_text = match.group("variant")
    component_prefix = matched_atom.raw[: match.start()] or None
    unit_rule = None
    if actor is None and component_prefix and reference.rules is not None:
        unit_rule = reference.rules.contextual_alias_for("sfx", "entity", component_prefix)
    if actor is None and (unit_rule is None or unit_rule.kind != "map_system_entity"):
        return

    actor_alias = (actor.alias or actor.label) if actor is not None else None
    entity_alias = actor_alias or (unit_rule.token if unit_rule is not None else component_prefix)
    attack_context = component_prefix
    if attack_context and actor_alias and attack_context.casefold().startswith(actor_alias.casefold()):
        attack_context = attack_context[len(actor_alias) :] or None
    if actor is None:
        attack_context = None

    record.profile = EventProfile(
        primary_family=attack_kind,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="resolved strong attack cue for mode actor or reviewed mode unit",
    )
    for key in (
        "spell_key",
        "spell_key_hint",
        "spell_name",
        "spell_rank",
        "spell_stage",
        "spell_variant",
    ):
        record.semantic_slots.pop(key, None)
    record.semantic_slots.update(
        {
            "trigger_family": attack_kind,
            "attack_kind": attack_kind,
            "attack_form": attack_form,
            "attack_variant": int(variant_text) if variant_text else None,
            "attack_component_raw": component_prefix,
            "attack_context": attack_context,
            "attack_raw": matched_atom.raw,
            "related_spell_key": None,
            "asset_kind": record.shell.asset_kind,
        }
    )
    if actor is not None:
        record.semantic_slots["subject"] = actor.to_dict()
        record.semantic_slots["subject_hint"] = actor_alias
    else:
        record.semantic_slots.pop("subject", None)
        record.semantic_slots.pop("subject_hint", None)
        assert unit_rule is not None
        record.semantic_slots["mode_entity"] = {
            "kind": "mode_unit",
            "raw": component_prefix,
            "alias": unit_rule.token,
            "label": unit_rule.label,
            "component": unit_rule.component,
            "source": unit_rule.source,
            "confidence": unit_rule.confidence,
        }
    phase = first_phase_value(record)
    if phase is not None:
        record.semantic_slots["phase"] = phase
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.HIGH)
    candidate: dict[str, Any] = {
        "kind": "mode_champion_attack" if actor is not None else "mode_unit_attack",
        "raw_event_name": record.raw_event_name,
        "raw": matched_atom.raw,
        "attack_kind": attack_kind,
        "confidence": "high",
        "evidence": "declared mode context with actor or reviewed unit followed by strong attack cue",
        "meaning": f"{entity_alias} performs a {attack_kind.replace('_', ' ')} in mode context.",
    }
    if actor is not None:
        candidate["subject"] = actor.to_dict()
    elif unit_rule is not None:
        candidate["mode_entity"] = dict(record.semantic_slots["mode_entity"])
    append_candidate(record, candidate)
    clear_unknowns_by_keys(record, {compact(matched_atom.raw), attack_token})


def add_mode_namespace_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """为已确认前缀补充统一的模式或机制上下文。"""

    match = record_mode_namespace(record)

    if match is None:
        return

    namespace, raw, position = match
    existing_namespace = record.semantic_slots.get("context_namespace")

    if isinstance(existing_namespace, dict) and existing_namespace.get("key") not in {None, namespace.key}:
        return

    if not isinstance(existing_namespace, dict):
        record.semantic_slots["context_namespace"] = {
            "key": namespace.key,
            "label": namespace.label,
            "kind": namespace.kind,
            "source": "namespace_convention",
        }

    existing_context = record.semantic_slots.get("mode_context")
    if namespace.key == "guests_of_honor" and position == "owner" and record.lexical_atoms:
        # 只保存原始嘉宾 token，不把 NPC、角色组件或未知词转成英雄身份。
        record.semantic_slots["context_namespace"]["guest_token"] = record.lexical_atoms[0].raw
        cue_path = [atom.raw for atom in record.lexical_atoms[1:]]
        record.semantic_slots["context_namespace"]["cue_path"] = cue_path
        cue_raw = "_".join(cue_path)
        location = "semantic_slots.context_namespace.cue_path"
        if cue_raw and not any(unknown.location == location for unknown in record.unknowns):
            record.unknowns.append(
                UnknownFragment(
                    kind=UnknownKind.UNPARSED_FRAGMENT,
                    raw=cue_raw,
                    location=location,
                    blocking_reason="Guest-of-Honor scenario requires exact review",
                    severity="medium",
                )
            )
    if namespace.kind == "mode" and not isinstance(existing_context, dict):
        record.semantic_slots["mode_context"] = {
            "key": namespace.key,
            "label": namespace.label,
            "source": "mode_namespace_convention",
        }

    actor, actor_role = mode_actor_for_record(record, reference, namespace)

    existing_actor = record.semantic_slots.get("mode_actor")
    if actor is not None and (not isinstance(existing_actor, dict) or not existing_actor.get("alias")):
        resolved_actor = actor.to_dict()
        if isinstance(existing_actor, dict) and existing_actor.get("raw"):
            resolved_actor["raw"] = existing_actor["raw"]
        record.semantic_slots["mode_actor"] = resolved_actor

    if actor is not None and "subject" not in record.semantic_slots:
        record.semantic_slots["subject"] = actor.to_dict()

    if actor_role is not None:
        record.semantic_slots["mode_actor_role"] = actor_role

    add_mode_system_entity(record, namespace, actor)

    candidate: dict[str, Any] = {
        "kind": "mode_namespace",
        "raw_event_name": record.raw_event_name,
        "raw": raw,
        "position": position,
        "mode": namespace.key,
        "mode_label": namespace.label,
        "context_kind": namespace.kind,
        "confidence": "high",
        "evidence": "declared mode namespace prefix",
        "meaning": f"{raw} identifies the {namespace.label} namespace.",
    }

    if actor is not None:
        candidate["actor"] = actor.to_dict()

    if actor_role is not None:
        candidate["actor_role"] = actor_role

    append_candidate(record, candidate)
    clear_unknowns_by_keys(record, {raw, namespace.prefix})


def add_mode_system_entity(
    record: EventDecompositionRecord,
    namespace: ModeNamespace,
    actor: ReferenceEntity | None,
) -> None:
    """把无英雄演员的模式事件首个业务 cue 记录为真实系统实体。"""

    if namespace.kind != "mode" or actor is not None or "mode_entity" in record.semantic_slots:
        return

    cue_path = record.semantic_slots.get("cue_path")
    if not isinstance(cue_path, list):
        return

    ignored = {
        compact(namespace.key),
        compact(namespace.prefix),
        "gamemode",
    }
    raw_entity = next(
        (cue for cue in cue_path if isinstance(cue, str) and cue.strip() and compact(cue) not in ignored),
        None,
    )
    if raw_entity is None:
        return

    record.semantic_slots["mode_entity"] = {
        "kind": "mode_system",
        "raw": raw_entity,
        "alias": raw_entity,
        "source": "mode_namespace_cue",
        "confidence": "medium",
    }


def record_mode_namespace(record: EventDecompositionRecord) -> tuple[ModeNamespace, str, str] | None:
    """从 owner 或词法限定词中查找模式前缀。"""

    owner = record.owner.raw_owner_token or record.owner.alias

    if owner:
        namespace = mode_namespace_for_token(owner)

        if namespace is not None:
            return namespace, owner, "owner"

    for atom in record.lexical_atoms:
        namespace = mode_namespace_for_token(atom.raw)

        if namespace is not None and not namespace.owner_only:
            return namespace, atom.raw, "qualifier"

    return None


def mode_actor_for_record(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
    namespace: ModeNamespace,
) -> tuple[ReferenceEntity | None, str | None]:
    """解析命名空间事件中的基础英雄演员。"""

    if not namespace.resolve_actor:
        return None, None

    if namespace.key == "ruby":
        for index, atom in enumerate(record.lexical_atoms[:-1]):
            if compact(atom.raw) != "boss":
                continue
            actor_atom = record.lexical_atoms[index + 1]
            actor = reference.resolve_token(actor_atom.raw, record.raw_event_name)
            if actor is None or actor.kind != "champion":
                actor = prefixed_mode_actor(actor_atom.raw, reference)
            if actor is not None and actor.kind == "champion":
                return actor, "boss"

    if namespace.key == "strawberry":
        for index, atom in enumerate(record.lexical_atoms[:-1]):
            marker = compact(atom.raw)
            if marker not in {"boss", "enemy", "npc"}:
                continue

            actor_atom = record.lexical_atoms[index + 1]
            actor = reference.resolve_token(actor_atom.raw, record.raw_event_name)
            if actor is None:
                actor = reference.find_champion_alias(actor_atom.raw)

            if actor is not None and actor.kind == "champion":
                return actor, marker

    # Ruby_XerathArcanopulse2 / OdysseyAugments_yasuoqe1 这类事件把
    # 英雄与后续 cue 粘在同一 atom；按最长安全英雄前缀恢复真实演员。
    for atom in record.lexical_atoms:
        actor = reference.find_champion_alias(atom.raw)

        if actor is not None:
            return actor, namespace.actor_role

        actor = short_mode_actor_from_semantic_cue(atom.raw, reference)
        if actor is not None:
            return actor, namespace.actor_role

        actor = prefixed_mode_actor(atom.raw, reference)
        if actor is not None:
            return actor, namespace.actor_role

    for owner in [record.owner.alias, record.owner.raw_owner_token]:
        if not owner:
            continue

        actor = reference.resolve_token(owner, record.raw_event_name)

        if actor is not None and actor.kind == "skin":
            champion_alias = actor.metadata.get("champion_alias")
            actor = reference.resolve_token(str(champion_alias or ""), record.raw_event_name)

        if actor is not None and actor.kind == "champion":
            return actor, namespace.actor_role

    subject = record.semantic_slots.get("subject")

    if isinstance(subject, dict):
        actor = reference.resolve_token(str(subject.get("alias") or subject.get("label") or ""), record.raw_event_name)

        if actor is not None and actor.kind == "champion":
            return actor, namespace.actor_role

    return None, None


SHORT_MODE_ACTOR_CUE_PREFIXES = (
    "basicattack",
    "critattack",
    "rangedattack",
    "meleeattack",
    "q",
    "w",
    "e",
    "r",
    "p",
    "dance",
    "laugh",
    "taunt",
    "recall",
    "death",
    "idle",
    "respawn",
)


def short_mode_actor_from_semantic_cue(token: str, reference: ReferenceIndex) -> ReferenceEntity | None:
    """仅在短英雄别名后紧跟明确语义 cue 时接受前缀匹配。

    全局短前缀仍保持禁用，避免 ``Vi`` 误吸附 ``VillainLab``；这里已经有
    Jade/Ruby/Strawberry/OdysseyAugments 命名空间证据，再用攻击、技能槽位或
    动作 cue 做第二道门禁，覆盖 ``LuxBasicAttack`` 等真实组合。
    """

    actor = reference.find_champion_alias(token, allow_short_prefix=True)
    if actor is None or len(actor.key) >= 4:
        return None
    compact_token = compact(token)
    if not compact_token.startswith(actor.key):
        return None
    suffix = compact_token[len(actor.key) :]
    if not suffix or not suffix.startswith(SHORT_MODE_ACTOR_CUE_PREFIXES):
        return None
    return actor


MODE_ACTOR_ENGINEERING_PREFIXES = ("fn",)


def prefixed_mode_actor(token: str, reference: ReferenceIndex) -> ReferenceEntity | None:
    """剥离已核验的模式工程前缀后解析真实英雄，如 FNVeigar。"""

    compact_token = compact(token)
    for prefix in MODE_ACTOR_ENGINEERING_PREFIXES:
        if not compact_token.startswith(prefix) or len(compact_token) == len(prefix):
            continue
        actor = reference.find_champion_alias(compact_token[len(prefix) :])
        if actor is not None:
            return actor
    return None


def apply_tail_modifier_rules(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把无交互对象的尾部 residue 解析为修饰语。"""

    if reference.rules is None:
        return

    raw_residue = str(record.semantic_slots.get("raw_residue") or "")

    if not raw_residue:
        return

    modifier = reference.rules.residual_modifier_for(raw_residue)

    if modifier is None:
        return

    trigger_family = str(record.semantic_slots.get("trigger_family") or record.profile.primary_family)

    if trigger_family not in {"move"}:
        return

    record.profile = EventProfile(
        primary_family=trigger_family,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="resolved action tail modifier from residual rules",
    )
    record.semantic_slots["tail_modifiers"] = [modifier.to_dict()]
    audit_residual_modifier_hit(record, modifier.to_dict())
    record.confidence = max_confidence(record.confidence, confidence_value(modifier.confidence))
    clear_unknowns_by_keys(record, {compact(raw_residue)})


def apply_kill_spell_method(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把 Kill3DR / Kill3DREmpowered 这类目标改成击杀方式。"""

    if record.profile.semantic_profile is not SemanticProfile.KILL_OR_SCORE:
        return

    counterpart = record.semantic_slots.get("counterpart")

    if not isinstance(counterpart, dict):
        return

    raw = str(counterpart.get("raw") or "")
    match = re.fullmatch(r"([QWERP])([A-Za-z0-9]*)", raw)

    if match is None:
        return

    owner = owner_champion_entity(record, reference)

    if owner is None:
        return

    spell_key = match.group(1).lower()
    residual = match.group(2)
    spell = spell_for_key(reference, owner, spell_key)

    if spell is None:
        return

    modifiers, unresolved = parse_residual_modifiers(residual, reference.rules)

    if residual and unresolved:
        return

    record.semantic_slots["counterpart"] = None
    record.semantic_slots["kill_method"] = {
        "kind": "spell",
        "spell_key": spell_key,
        "spell_name": spell.name,
        "raw": raw,
        "residual": residual,
        "residual_modifiers": modifiers,
    }
    for modifier in modifiers:
        audit_residual_modifier_hit(record, modifier)
    record.profile.requires_context = True
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknowns_by_keys(record, {compact(raw)})


def add_spell_combo_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """识别 LeBlanc RW/RQ/RE 这类需要英雄机制解释的技能组合。"""

    if (
        record.profile.semantic_profile is SemanticProfile.SELF_ACTION
        and record.profile.primary_family in NON_SPELL_SELF_ACTION_FAMILIES
    ):
        # Dance3D_ee / Taunt_* 一类动作尾段常含短音效层缩写；已经由强
        # trigger 确认的动作不能再被纯字母 Q/W/E/R 序列覆盖为组合技。
        return

    owner = owner_champion_entity(record, reference)

    for atom in record.lexical_atoms:
        subject = owner or reference.find_champion_alias(atom.raw)
        token = strip_owner_alias(atom.raw, subject.alias if subject is not None else record.owner.alias)
        sequence, sequence_modifiers = parse_spell_sequence_details(token)

        if subject is None or len(sequence) < 2:
            continue

        append_candidate(
            record,
            {
                "kind": "champion_spell_combo",
                "raw_event_name": record.raw_event_name,
                "raw": atom.raw,
                "subject": subject.to_dict(),
                "spell_sequence": sequence,
                "spell_sequence_modifiers": sequence_modifiers,
                "meaning": spell_combo_meaning(subject.alias or subject.label, sequence),
                "confidence": "medium",
                "evidence": "champion alias plus compact spell-key sequence in full event name",
            },
        )
        set_spell_combo_profile(record, subject, atom.raw, sequence, sequence_modifiers)


def add_inferred_champion_spell_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """从英雄技能名、素材名和 owner 上下文推测技能事件。"""

    if is_switch_record(record) or record.profile.primary_family in {"basic_attack", "crit_attack"}:
        return

    owner = owner_champion_entity(record, reference)
    best_candidate: dict[str, Any] | None = None

    for atom in record.lexical_atoms:
        for subject in champion_spell_subjects(atom.raw, owner, reference):
            candidate = infer_spell_candidate_for_subject(record, reference, atom.raw, subject, owner)

            if candidate is None:
                continue

            if best_candidate is None or int(candidate["score"]) > int(best_candidate["score"]):
                best_candidate = candidate

    if best_candidate is None:
        best_candidate = mode_actor_slot_spell_candidate(record, reference, owner)
    if best_candidate is None:
        return

    contextual_alias = record.semantic_slots.get("contextual_alias")
    if (
        isinstance(contextual_alias, dict)
        and contextual_alias.get("kind") == "champion_spell_component"
        and contextual_alias.get("source") == "manual_contextual_alias"
        and record.semantic_slots.get("state_key") == "passive_idle"
        and best_candidate.get("spell_key") == record.semantic_slots.get("spell_key")
    ):
        return

    append_candidate(record, best_candidate)
    set_inferred_spell_profile(record, best_candidate)


def mode_actor_slot_spell_candidate(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
    subject: ReferenceEntity | None,
) -> dict[str, Any] | None:
    """以已解析模式演员为边界，识别独立大写 Q/W/E/R/P 技能前缀。"""

    if subject is None or not isinstance(record.semantic_slots.get("mode_context"), dict):
        return None

    actor_alias = subject.alias or subject.label
    for atom in record.lexical_atoms:
        relative = atom.raw
        if actor_alias and relative.casefold().startswith(actor_alias.casefold()):
            relative = relative[len(actor_alias) :]
        elif compact(relative) == subject.key:
            continue

        match = MODE_ACTOR_SLOT_PATTERN.fullmatch(relative)
        if match is None:
            continue

        spell_key = match.group("spell_key").lower()
        residual_raw = match.group("residual") or ""
        if residual_raw.casefold() in MODE_ACTOR_SLOT_DENY_RESIDUALS:
            continue

        spell = spell_for_key(reference, subject, spell_key)
        if spell is None:
            continue

        stage = mode_slot_stage(residual_raw)
        term_match = SpellTermMatch(
            term=spell_key,
            source="mode_actor_slot_prefix",
            residual=compact(residual_raw),
            match_type="prefix" if residual_raw else "exact",
            stage=stage,
        )
        candidate = inferred_spell_candidate(
            record,
            subject,
            spell,
            atom.raw,
            term_match,
            90,
            ["mode_context", "resolved_mode_actor", "explicit_slot_prefix"],
        )
        attach_residual_modifier_analysis(candidate, residual_raw, reference.rules)
        return candidate

    return None


def mode_slot_stage(residual: str) -> int | None:
    """只从技能键后的紧邻数字/英文序数读取模式技能段数。"""

    match = re.match(r"^(?P<stage>[123])(?:$|[A-Z])", residual)
    if match is not None:
        return int(match.group("stage"))

    word_match = re.match(r"^(?P<stage>One|Two|Three)(?:$|[A-Z])", residual)
    if word_match is None:
        return None
    return {"one": 1, "two": 2, "three": 3}[word_match.group("stage").lower()]


def add_ultimate_spellbook_spell_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """识别 UltBook_<Champion>R_OnCast 这类终极魔典借用大招。"""

    if compact(record.owner.alias or record.owner.raw_owner_token or "") != "ultbook":
        return

    if not record.lexical_atoms:
        return

    spell_atom = record.lexical_atoms[0]
    match = re.fullmatch(r"(?P<champion>[A-Za-z][A-Za-z0-9]*)R", spell_atom.raw)

    if match is None:
        return

    subject = reference.find_champion_alias(match.group("champion"))

    if subject is None:
        return

    spell = spell_for_key(reference, subject, "r")
    spell_name = spell.name if spell is not None else "R"
    append_candidate(
        record,
        {
            "kind": "ultimate_spellbook_spell",
            "raw_event_name": record.raw_event_name,
            "raw": spell_atom.raw,
            "mode": "ultimate_spellbook",
            "mode_label": "终极魔典",
            "subject": subject.to_dict(),
            "spell_key": "r",
            "spell_name": spell_name,
            "spell_kind": "ultimate",
            "confidence": "high" if spell is not None else "medium",
            "evidence": spell.source if spell is not None else "ultimate_spellbook_event_name",
            "meaning": f"Ultimate Spellbook borrowed {subject.alias or subject.label} R.",
        },
    )
    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason="resolved ultimate spellbook borrowed ultimate voice",
    )
    record.semantic_slots["trigger_family"] = "spell"
    record.semantic_slots["mode_context"] = {
        "key": "ultimate_spellbook",
        "label": "终极魔典",
        "source": "ultimate_spellbook_event_name",
    }
    record.semantic_slots["subject"] = subject.to_dict()
    record.semantic_slots["spell_key"] = "r"
    record.semantic_slots["spell_name"] = spell_name
    record.semantic_slots["spell_kind"] = "ultimate"
    record.semantic_slots["source"] = "ultimate_spellbook_event_name"
    phase = first_phase_value(record)

    if phase is not None:
        record.semantic_slots["phase"] = phase

    record.confidence = max_confidence(
        record.confidence, DecompositionConfidence.HIGH if spell is not None else DecompositionConfidence.MEDIUM
    )
    clear_unknowns_by_keys(record, {"ultbook", compact(spell_atom.raw), "r"})


def add_composite_owner_candidate(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """识别 Ruby_Brand 这类前缀加英雄的组合 owner。"""

    if not record.owner.alias or not record.lexical_atoms:
        return

    action_form = record.semantic_slots.get("action_form")
    if isinstance(action_form, dict) and action_form.get("kind") == "champion_form_qualifier":
        # Gnar_GnarBig_Dance 中的 GnarBig 是已审阅形态修饰，不是第二个
        # 英雄 actor；组合 owner 回填不得覆盖已经成立的动作生命周期。
        return

    first_atom = record.lexical_atoms[0]
    entity = reference.resolve_token(first_atom.raw, record.raw_event_name)

    if entity is None or entity.kind != "champion":
        return

    append_candidate(
        record,
        {
            "kind": "composite_owner",
            "raw_event_name": record.raw_event_name,
            "raw": f"{record.owner.raw_owner_token}_{first_atom.raw}",
            "prefix": record.owner.raw_owner_token,
            "entity": entity.to_dict(),
            "meaning": f"{record.owner.raw_owner_token}_{entity.alias or entity.label} looks like a composite actor or bot variant.",
            "confidence": "medium",
            "evidence": "owner token is followed by a champion alias in the full event name",
        },
    )
    set_composite_owner_profile(record, entity)


def apply_low_trust_event_rule(record: EventDecompositionRecord, reference: ReferenceIndex) -> None:
    """把已审阅但仍低信任的整句事件名从低置信噪声中移出。"""

    if record.confidence is not DecompositionConfidence.LOW:
        return

    if record.unknowns:
        return

    if reference.rules is None:
        return

    rule = reference.rules.low_trust_event_for(record.raw_event_name)

    if rule is None:
        return

    record.semantic_slots["low_trust_rule"] = rule.to_metadata()
    record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
    audit_low_trust_event_rule_hit(record, rule)


def owner_champion_entity(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
) -> ReferenceEntity | None:
    """解析 owner 对应的基础英雄，允许 NidaleeCougar 这类形态后缀。"""

    for slot_name in ["subject", "mode_actor"]:
        value = record.semantic_slots.get(slot_name)
        if not isinstance(value, dict):
            continue

        alias = value.get("alias") or value.get("key") or value.get("label")
        if alias:
            entity = reference.find_champion_alias(str(alias))
            if entity is not None:
                return entity

    owner_alias = record.owner.alias

    if owner_alias is None:
        return None

    return reference.champions_by_alias.get(compact(owner_alias)) or reference.find_champion_alias(owner_alias)


SHOPKEEPER_ACTION_PATTERN = re.compile(
    r"^(?P<action>Open|Purchase|Close|Browsing|Ambient)(?P<dimension>2D|3D)?(?P<subject>[A-Z0-9].*)?$"
)


def fill_shopkeeper_voice_slots(record: EventDecompositionRecord) -> None:
    """解析商店老板语音的动作/店主/对象槽位（批次6d）。

    形态 ``Play_vo_Shopkeeper_Map12_{店主内部代号…}_{Action}[2D|3D][Subject]``：
    尾 atom 拆动作词 + 维度 + 对象 token（unresolved 原样带出，翻译层按
    champion/皮肤/counterpart 词表/shop 词表链解析），中段 atom 合并为店主
    内部代号（Viking/Hermit/TwilightGhost 等无官方中文名，不进文案）。
    表外尾形态不写槽位，维持既有 map profile 展示。

    Args:
        record: 已被 set_map_profile 处理的拆解记录。
    """

    if record.owner.alias != "Shopkeeper" or len(record.lexical_atoms) < 2:
        return

    action_atom = record.lexical_atoms[-1]
    match = SHOPKEEPER_ACTION_PATTERN.fullmatch(action_atom.raw)
    if match is None:
        return

    from .decomposition_profiles import unresolved_counterpart

    record.semantic_slots["shop_action"] = match.group("action").lower()
    dimension = match.group("dimension")
    if dimension is not None:
        record.semantic_slots["dimension_token"] = dimension
    subject = match.group("subject")
    if subject:
        record.semantic_slots["shop_subject"] = unresolved_counterpart(subject)

    persona_atoms = [atom.raw for atom in record.lexical_atoms[1:-1]]
    if persona_atoms:
        record.semantic_slots["shop_persona"] = {"raw": "_".join(persona_atoms), "kind": "shopkeeper_persona"}

    record.profile.reason = f"{record.profile.reason}; parsed shopkeeper voice action"


def set_map_profile(record: EventDecompositionRecord, entity: ReferenceEntity) -> None:
    """设置地图或系统 profile。"""

    record.profile = EventProfile(
        primary_family="map",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=False,
        reason="resolved map id from reference data",
    )
    record.semantic_slots.setdefault("trigger_family", "map_or_system")
    record.semantic_slots["map"] = entity.to_dict()
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknown_head(record, entity.key)


def set_announcer_profile(record: EventDecompositionRecord, entity: ReferenceEntity) -> None:
    """设置通告员 profile。"""

    record.profile = EventProfile(
        primary_family="announcer",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=False,
        reason="resolved announcer token from full event name",
    )
    record.semantic_slots["trigger_family"] = "announcer"
    announcer = entity.to_dict()
    announcer.update(entity.metadata)
    record.semantic_slots["announcer"] = announcer
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknown_head(record, entity.key)


def set_system_profile(record: EventDecompositionRecord, entity: ReferenceEntity) -> None:
    """设置系统或玩法实体 profile。"""

    record.profile = EventProfile(
        primary_family=entity.kind,
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=False,
        reason=f"resolved {entity.kind} from reference data",
    )
    record.semantic_slots["trigger_family"] = entity.kind
    record.semantic_slots["reference_entity"] = entity.to_dict()
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknown_head(record, entity.key)


def set_action_profile(record: EventDecompositionRecord, entity: ReferenceEntity) -> None:
    """设置内置动作 profile。"""

    action = str(entity.metadata.get("action") or entity.key)
    record.profile = EventProfile(
        primary_family=action,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="resolved action token from full event name",
    )
    record.semantic_slots["trigger_family"] = action
    record.semantic_slots["reference_entity"] = entity.to_dict()
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknown_head(record, entity.key)


def set_spell_combo_profile(
    record: EventDecompositionRecord,
    subject: ReferenceEntity,
    raw: str,
    sequence: list[str],
    sequence_modifiers: list[dict[str, Any]] | None = None,
) -> None:
    """把紧凑技能组合提升为 spell_event。"""

    if record.profile.semantic_profile is SemanticProfile.UNKNOWN_PROFILE or (
        record.profile.primary_family == "sfx"
        and record.semantic_slots.get("sfx_scope") in {"champion_sfx", "mode_sfx"}
    ):
        record.profile = EventProfile(
            primary_family="spell",
            semantic_profile=SemanticProfile.SPELL_EVENT,
            requires_context=True,
            reason="resolved compact champion spell sequence from full event name",
        )

    record.semantic_slots["trigger_family"] = "spell"
    record.semantic_slots["subject"] = subject.to_dict()
    record.semantic_slots["spell_sequence"] = sequence
    record.semantic_slots["spell_sequence_modifiers"] = sequence_modifiers or []
    if sequence_modifiers is not None and len(sequence_modifiers) == 1:
        stage = sequence_modifiers[0].get("stage")
        if isinstance(stage, int) and stage > 0:
            record.semantic_slots["spell_sequence_stage"] = stage
    record.semantic_slots["requires_champion_mechanic_context"] = True
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    backfill_phase(record)
    clear_unknowns_by_keys(record, {compact(raw)})


def set_inferred_spell_profile(record: EventDecompositionRecord, candidate: dict[str, Any]) -> None:
    """把高分英雄技能候选提升为 spell_event。"""

    score = int(candidate["score"])

    if (
        record.profile.semantic_profile is SemanticProfile.UNKNOWN_PROFILE
        or record.semantic_slots.get("trigger_family") == "sfx_event"
    ):
        record.profile = EventProfile(
            primary_family="spell",
            semantic_profile=SemanticProfile.SPELL_EVENT,
            requires_context=True,
            reason="resolved champion spell from full event name and reference data",
        )

    record.semantic_slots["trigger_family"] = "spell"
    record.semantic_slots["subject"] = candidate["subject"]
    record.semantic_slots["spell_key"] = candidate["spell_key"]
    record.semantic_slots["spell_name"] = candidate["spell_name"]
    stage = candidate.get("stage")
    if isinstance(stage, int):
        record.semantic_slots["spell_stage"] = stage
    record.semantic_slots["spell_match"] = {
        "raw": candidate["raw"],
        "matched_term": candidate["matched_term"],
        "match_source": candidate["match_source"],
        "residual": candidate["residual"],
        "score": score,
        "components": list(candidate["components"]),
    }

    if "residual_modifiers" in candidate:
        record.semantic_slots["residual_modifiers"] = list(candidate["residual_modifiers"])
        for modifier in candidate["residual_modifiers"]:
            audit_residual_modifier_hit(record, modifier)

    if "unresolved_residual" in candidate:
        record.semantic_slots["unresolved_residual"] = candidate["unresolved_residual"]

    record.confidence = max_confidence(
        record.confidence,
        DecompositionConfidence.HIGH if score >= 90 else DecompositionConfidence.MEDIUM,
    )
    backfill_phase(record)
    clear_spell_unknown(record, str(candidate["raw"]))


def set_champion_contextual_alias_profile(
    record: EventDecompositionRecord,
    subject: ReferenceEntity,
    atom: LexicalAtom,
    atom_index: int,
    rule: ContextualAlias,
) -> None:
    """把英雄上下文别名归入技能、攻击或角色动作组件事件。"""

    alias_payload = {
        "raw": atom.raw,
        "kind": rule.kind,
        "label": rule.label,
        "component": rule.component,
        "source": rule.source,
        "confidence": rule.confidence,
        "note": rule.note,
        "qualifiers": contextual_alias_qualifier_payloads(record, rule),
    }
    is_attack_component = rule.kind == "champion_attack_component" or record.profile.primary_family in {
        "basic_attack",
        "crit_attack",
    }

    if rule.kind == "generic_sfx_effect":
        record.profile = EventProfile(
            primary_family="sfx",
            semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
            requires_context=False,
            reason="resolved owner-scoped generic SFX effect",
        )
        for key in (
            "counterpart",
            "spell_key",
            "spell_key_hint",
            "spell_name",
            "spell_rank",
            "spell_stage",
            "spell_variant",
            "variant_kind",
            "related_spell_key",
            "effect_residual",
        ):
            record.semantic_slots.pop(key, None)
        record.semantic_slots["trigger_family"] = "sfx_event"
        if record.shell.asset_kind == "sfx":
            record.semantic_slots.setdefault("asset_kind", "sfx")
            record.semantic_slots.setdefault(
                "sfx_scope",
                "mode_sfx"
                if any(
                    isinstance(record.semantic_slots.get(slot_name), dict)
                    for slot_name in ("context_namespace", "mode_context")
                )
                else "champion_sfx",
            )
        record.semantic_slots["subject"] = subject.to_dict()
        record.semantic_slots["contextual_alias"] = alias_payload
        record.semantic_slots["sfx_alias"] = alias_payload
        record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
        backfill_phase(record)
        clear_contextual_unknowns(record, atom.raw, rule)
        return

    if rule.kind == "champion_form_qualifier":
        set_champion_form_qualified_action_profile(record, subject, atom, atom_index, alias_payload)
        return

    if rule.kind == "champion_action_component":
        record.profile = EventProfile(
            primary_family="contextual_action",
            semantic_profile=SemanticProfile.SELF_ACTION,
            requires_context=False,
            reason="resolved owner-scoped action component",
        )
        for key in (
            "counterpart",
            "spell_key",
            "spell_key_hint",
            "spell_name",
            "spell_rank",
            "spell_stage",
            "spell_variant",
            "variant_kind",
            "related_spell_key",
        ):
            record.semantic_slots.pop(key, None)
        record.semantic_slots["trigger_family"] = "contextual_action"
        record.semantic_slots["subject"] = subject.to_dict()
        record.semantic_slots["contextual_alias"] = alias_payload
        record.semantic_slots["action_component"] = alias_payload
        record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
        backfill_phase(record)
        clear_contextual_unknowns(record, atom.raw, rule)
        return

    if is_attack_component:
        attack_kind = record.semantic_slots.get("attack_kind")
        if attack_kind not in {"basic_attack", "crit_attack"}:
            marker = compact(atom.raw)
            attack_kind = "crit_attack" if "critattack" in marker else "basic_attack"
        record.profile = EventProfile(
            primary_family=str(attack_kind),
            semantic_profile=SemanticProfile.SELF_ACTION,
            requires_context=False,
            reason="resolved owner-scoped attack component",
        )
        record.semantic_slots.pop("spell_key", None)
        record.semantic_slots.pop("spell_key_hint", None)
        record.semantic_slots.pop("spell_name", None)
        record.semantic_slots.pop("spell_rank", None)
        record.semantic_slots["trigger_family"] = attack_kind
        record.semantic_slots["attack_kind"] = attack_kind
        record.semantic_slots["subject"] = subject.to_dict()
        record.semantic_slots["contextual_alias"] = alias_payload
        record.semantic_slots["attack_component"] = alias_payload
        if rule.related_spell is not None:
            record.semantic_slots["related_spell_key"] = rule.related_spell
        variant = first_number(atom)
        if variant is not None:
            record.semantic_slots["attack_variant"] = variant
        record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
        backfill_phase(record)
        clear_contextual_unknowns(record, atom.raw, rule)
        return

    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason="resolved owner-scoped contextual alias",
    )
    record.semantic_slots.pop("counterpart", None)
    record.semantic_slots["trigger_family"] = "spell"
    record.semantic_slots["subject"] = subject.to_dict()
    record.semantic_slots.pop("spell_key_hint", None)
    record.semantic_slots["spell_key"] = rule.spell
    record.semantic_slots["contextual_alias"] = alias_payload
    number = first_number(atom)
    if rule.stage is not None:
        stage = rule.stage
    elif rule.infer_stage:
        stage = number if number is not None else stage_from_term(atom.raw)
    else:
        stage = None

    if stage is not None:
        if re.search(r"Rank[0-9]+$", atom.raw, re.IGNORECASE):
            record.semantic_slots.setdefault("spell_rank", stage)
        else:
            record.semantic_slots.setdefault("spell_stage", stage)

    unresolved = unresolved_context_after(record, atom_index)

    if unresolved:
        record.semantic_slots["unresolved_context"] = unresolved

    record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
    backfill_phase(record)
    clear_contextual_unknowns(record, atom.raw, rule)


def set_champion_form_qualified_action_profile(
    record: EventDecompositionRecord,
    subject: ReferenceEntity,
    atom: LexicalAtom,
    atom_index: int,
    alias_payload: dict[str, str | None],
) -> None:
    """把 ``GnarBig_Dance/Recall`` 一类结构拆成形态修饰与真实动作。"""

    trigger_atom = first_trigger_atom(record, start_index=atom_index + 1)
    trigger = part_value(trigger_atom, "trigger_token") if trigger_atom is not None else None
    if trigger not in {"dance", "joke", "laugh", "recall", "taunt"}:
        return

    action = normalize_trigger_family(trigger)
    phase = first_form_action_phase_value(record)
    if phase is None:
        existing_phase = record.semantic_slots.get("phase")
        phase = existing_phase if isinstance(existing_phase, str) else None

    if action == "recall" and phase is not None:
        primary_family = "skin_recall_state"
    elif phase is not None:
        primary_family = "skin_action_state"
    else:
        primary_family = "contextual_action"

    record.profile = EventProfile(
        primary_family=primary_family,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="resolved owner-scoped form qualifier followed by self action",
    )
    for key in (
        "counterpart",
        "spell_key",
        "spell_key_hint",
        "spell_name",
        "spell_rank",
        "spell_stage",
        "spell_variant",
        "variant_kind",
        "related_spell_key",
        "sfx_alias",
    ):
        record.semantic_slots.pop(key, None)
    record.semantic_slots["trigger_family"] = primary_family
    record.semantic_slots["subject"] = subject.to_dict()
    record.semantic_slots["subject_hint"] = subject.alias or subject.label
    record.semantic_slots["skin_token"] = record.owner.skin_token
    record.semantic_slots["action"] = action
    record.semantic_slots["action_form"] = alias_payload
    record.semantic_slots["contextual_alias"] = alias_payload
    record.semantic_slots["dimension_token"] = part_raw(trigger_atom, "dimension_token")
    if action == "recall":
        record.semantic_slots["recall_action"] = "recall"
    if phase is not None:
        record.semantic_slots["phase"] = phase
    record.confidence = max_confidence(
        record.confidence,
        confidence_value(str(alias_payload.get("confidence") or "medium")),
    )
    clear_unknowns_by_keys(record, {atom.raw, trigger_atom.raw})


def contextual_alias_qualifier_payloads(
    record: EventDecompositionRecord,
    rule: ContextualAlias,
) -> list[dict[str, str]]:
    """从 cue path 提取规则明确审阅过的 SFX 修饰语。

    主效果词和阶段之外的 ``Mute``、``mask2``、``mega`` 等片段不能只
    留在调试字段里。规则以保守白名单声明可展示修饰语；此处保持原始顺序
    和 token，供 translation frame 与完整性校验共同消费。
    """

    if not rule.qualifiers:
        return []

    cue_path = record.semantic_slots.get("cue_path")
    tokens = [item for item in cue_path if isinstance(item, str)] if isinstance(cue_path, list) else []
    if not tokens:
        tokens = [atom.raw for atom in record.lexical_atoms]

    qualifiers: list[dict[str, str]] = []
    seen: set[str] = set()
    for token in tokens:
        key = compact(token)
        label = rule.qualifiers.get(key)
        if label is None or key in seen:
            continue
        qualifiers.append({"key": key, "label": label, "raw": token})
        seen.add(key)
    return qualifiers


def first_form_action_phase_value(record: EventDecompositionRecord) -> str | None:
    """读取形态动作的生命周期尾段，保留 leadin/loop/winddown 等语义。"""

    lifecycle_phases = {
        "leadin": "leadin",
        "leadout": "leadout",
        "loop": "loop",
        "winddown": "winddown",
        "windup": "windup",
    }
    for tail in reversed(record.lexical_atoms):
        phase = lifecycle_phases.get(compact(tail.raw))
        if phase is not None:
            return phase

    return first_phase_value(record)


def set_mode_contextual_alias_profile(
    record: EventDecompositionRecord,
    atom: LexicalAtom,
    atom_index: int,
    rule: ContextualAlias,
) -> None:
    """把模式上下文别名提升为模式实体事件。"""

    trigger_atom = first_trigger_atom(record, start_index=atom_index + 1)
    trigger = part_value(trigger_atom, "trigger_token") if trigger_atom is not None else None
    trigger_family = normalize_trigger_family(trigger) if trigger else "mode_entity"
    semantic_profile = SemanticProfile.SELF_ACTION if trigger else SemanticProfile.MAP_OR_SYSTEM

    record.profile = EventProfile(
        primary_family=trigger_family,
        semantic_profile=semantic_profile,
        requires_context=True,
        reason="resolved mode-scoped contextual alias",
    )
    record.semantic_slots["trigger_family"] = trigger_family
    if trigger_atom is not None:
        phase = first_form_action_phase_value(record)
        if phase is not None:
            record.semantic_slots["phase"] = phase
    record.semantic_slots["mode_context"] = {
        "key": rule.mode or compact(rule.owner),
        "label": rule.mode_label or rule.owner,
        "source": rule.source,
    }
    record.semantic_slots["mode_entity"] = {
        "raw": atom.raw,
        "kind": rule.kind,
        "label": rule.label,
        "component": rule.component,
        "source": rule.source,
        "confidence": rule.confidence,
        "note": rule.note,
    }
    actor = first_context_atom_after(record, atom_index)

    if actor is not None:
        existing_actor = record.semantic_slots.get("mode_actor")
        if isinstance(existing_actor, dict) and existing_actor.get("alias"):
            existing_actor.setdefault("raw", actor.raw)
        else:
            record.semantic_slots["mode_actor"] = {"raw": actor.raw}

    record.confidence = max_confidence(record.confidence, confidence_value(rule.confidence))
    clear_contextual_unknowns(record, atom.raw, rule)


def set_composite_owner_profile(record: EventDecompositionRecord, entity: ReferenceEntity) -> None:
    """把 Ruby_Brand + 后续动作提升成组合 owner 动作。"""

    action_atom = first_trigger_atom(record, start_index=1)

    if action_atom is None:
        return

    trigger = part_value(action_atom, "trigger_token")

    if trigger is None:
        return

    record.profile = EventProfile(
        primary_family=normalize_trigger_family(trigger),
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="resolved composite owner followed by action trigger",
    )
    record.semantic_slots["trigger_family"] = normalize_trigger_family(trigger)
    record.semantic_slots["dimension_token"] = part_raw(action_atom, "dimension_token")
    phase = first_form_action_phase_value(record)
    if phase is not None:
        record.semantic_slots["phase"] = phase
    record.semantic_slots["owner_composition"] = {
        "prefix": record.owner.raw_owner_token,
        "entity": entity.to_dict(),
    }
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    clear_unknown_head(record, entity.key)


def set_switch_profile(record: EventDecompositionRecord) -> None:
    """设置 Switch 控制事件 profile。"""

    record.profile = EventProfile(
        primary_family="switch",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched switch control event from full event structure",
    )
    record.semantic_slots["trigger_family"] = "switch"
    record.semantic_slots["switch_source"] = raw_atom(record, 1)
    record.semantic_slots["switch_target"] = raw_atom(record, 2)
    record.confidence = max_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    record.unknowns = [
        unknown
        for unknown in record.unknowns
        if not (unknown.kind is UnknownKind.UNKNOWN_HEAD_TOKEN and compact(unknown.raw) == "switch")
    ]


def first_interesting_token(record: EventDecompositionRecord) -> str | None:
    """取最适合参考解析的第一个 token。"""

    if not record.lexical_atoms:
        return record.owner.alias

    return record.lexical_atoms[0].raw or record.owner.alias


def parse_announcer_token(token: str) -> ReferenceEntity | None:
    """解析 Female1/Male1 通告员 token。"""

    match = re.fullmatch(r"(Female|Male)([0-9]+)", token, re.IGNORECASE)

    if match is None:
        return None

    gender = match.group(1).lower()
    variant = int(match.group(2))
    return ReferenceEntity(
        key=compact(token),
        kind="announcer",
        label=f"{gender} announcer {variant}",
        source="builtin:announcer",
        metadata={"gender": gender, "variant": variant},
    )


def structured_counterpart_for_token(
    token: str,
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
) -> dict[str, Any] | None:
    """从完整目标 token 中拆出关系、临近条件或英雄组。"""

    team_context = parse_team_context_token(token)

    if team_context is not None:
        return team_context

    relation = parse_relation_entity_token(token, reference)

    if relation is not None:
        return relation

    proximity = parse_proximity_entity_token(token, reference)

    if proximity is not None:
        return proximity

    base_skin = parse_champion_base_token(token, reference)

    if base_skin is not None:
        return base_skin

    themed = parse_themed_entity_token(token, reference)

    if themed is not None:
        return themed

    group = parse_champion_group_token(token, reference)

    if group is not None:
        return group

    return None


def parse_team_context_token(token: str) -> dict[str, Any] | None:
    """解析 AheadAllyTeam / FirstEnemyTeam 这类队伍状态目标。"""

    match = re.fullmatch(r"(?P<state>Ahead|Behind|First)?(?P<relation>Ally|Enemy)Team", token)

    if match is None:
        return None

    state = match.group("state")
    return {
        "raw": token,
        "kind": "team_context",
        "relation": match.group("relation").lower(),
        "team_state": state.lower() if state else None,
    }


def parse_relation_entity_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析 AllyAatrox / ViAlly / AllyNotRakan 这类关系实体。"""

    prefix_match = re.fullmatch(r"(?P<relation>Ally|Enemy)(?P<negated>Not)?(?P<target>[A-Z][A-Za-z0-9]*)", token)

    if prefix_match is not None:
        if re.search(r"Skin[0-9]+", prefix_match.group("target")):
            return None

        target = entity_or_group_for_token(prefix_match.group("target"), reference)

        if target is not None:
            return {
                "raw": token,
                "kind": "related_entity",
                "relation": prefix_match.group("relation").lower(),
                "negated": prefix_match.group("negated") is not None,
                "entity": target,
            }

    suffix_match = re.fullmatch(r"(?P<target>[A-Z][A-Za-z0-9]*)(?P<relation>Ally|Enemy)", token)

    if suffix_match is None:
        return None

    if re.search(r"Skin[0-9]+", suffix_match.group("target")):
        return None

    target = entity_or_group_for_token(suffix_match.group("target"), reference)

    if target is None:
        return None

    return {
        "raw": token,
        "kind": "related_entity",
        "relation": suffix_match.group("relation").lower(),
        "negated": False,
        "entity": target,
    }


def parse_proximity_entity_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析 NearMel 这类临近实体。"""

    match = re.fullmatch(r"Near(?P<target>[A-Z][A-Za-z0-9]*)", token)

    if match is None:
        return None

    target = entity_or_group_for_token(match.group("target"), reference)

    if target is None:
        return None

    return {
        "raw": token,
        "kind": "proximity_entity",
        "proximity": "near",
        "entity": target,
    }


def parse_champion_base_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析 NasusBase 这类指向英雄基础皮肤的目标。"""

    match = re.fullmatch(r"(?P<target>[A-Z][A-Za-z0-9]*)Base", token)

    if match is None:
        return None

    target = reference.resolve_token(match.group("target"))

    if target is None or target.kind != "champion":
        return None

    return {
        "raw": token,
        "kind": "champion_base_skin",
        "champion": target.to_dict(),
        "skin_token": "Base",
        "source": "event_name_heuristic",
    }


def parse_themed_entity_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析 DarkJhin / LightJhin 这类主题加英雄目标。"""

    match = re.fullmatch(r"(?P<theme>Dark|Light)(?P<target>[A-Z][A-Za-z0-9]*)", token)

    if match is None:
        return None

    target = entity_or_group_for_token(match.group("target"), reference)

    if target is None:
        return None

    return {
        "raw": token,
        "kind": "themed_entity",
        "theme": match.group("theme").lower(),
        "entity": target,
        "source": "event_name_heuristic",
    }


def parse_champion_group_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析 GarenOrKatarina / KayleMorgana 这类英雄组合。"""

    if "Or" in token:
        parts = [part for part in token.split("Or") if part]
        members: list[ReferenceEntity] = []

        for part in parts:
            member = reference.resolve_token(part)

            if member is not None and member.kind == "champion":
                members.append(member)
                continue

            concatenated = parse_concatenated_champions(part, reference)

            if not concatenated:
                members = []
                break

            members.extend(concatenated)

        if len(members) >= 2:
            return {
                "raw": token,
                "kind": "champion_group",
                "connector": "or",
                "members": [member.to_dict() for member in members],
            }

    members = parse_concatenated_champions(token, reference)

    if len(members) < 2:
        return None

    return {
        "raw": token,
        "kind": "champion_group",
        "connector": "and",
        "members": [member.to_dict() for member in members],
    }


def entity_or_group_for_token(token: str, reference: ReferenceIndex) -> dict[str, Any] | None:
    """解析单个实体或英雄组 token。"""

    entity = reference.resolve_token(token)

    if entity is not None:
        return entity.to_dict()

    return parse_champion_group_token(token, reference)


def parse_concatenated_champions(token: str, reference: ReferenceIndex) -> list[ReferenceEntity]:
    """按最长英雄别名贪心拆分连续英雄名。"""

    token_key = compact(token)
    aliases = sorted(reference.champions_by_alias.items(), key=lambda item: len(item[0]), reverse=True)
    members: list[ReferenceEntity] = []
    cursor = 0

    while cursor < len(token_key):
        match: tuple[str, ReferenceEntity] | None = None

        for alias, entity in aliases:
            if len(alias) < 3:
                continue

            if token_key.startswith(alias, cursor):
                match = (alias, entity)
                break

        if match is None:
            return []

        alias, entity = match
        members.append(entity)
        cursor += len(alias)

    seen: set[str] = set()
    deduped: list[ReferenceEntity] = []

    for member in members:
        if member.key in seen:
            continue

        seen.add(member.key)
        deduped.append(member)

    return deduped


def map_id_fallback_entity(map_id: int) -> ReferenceEntity:
    """构造当前地图表缺失时的地图 ID 占位实体。"""

    return ReferenceEntity(
        key=f"map{map_id}",
        kind="map",
        label=f"Map {map_id}",
        source="map_id_fallback",
        id=map_id,
        metadata={
            "trust": "missing_current_reference",
            "note": "当前 game-data maps.json 缺失，可能是旧版或活动地图。",
        },
    )


def champion_extra_aliases(entity: ReferenceEntity) -> list[str]:
    """生成少量安全的英雄额外别名。"""

    aliases: list[str] = []
    label_key = compact(entity.label)

    if label_key == "drmundo":
        aliases.append("mundo")
    elif label_key == "masteryi":
        aliases.append("yi")
    elif label_key == "shyvana":
        aliases.append("shyvanna")
    elif label_key == "leesin":
        aliases.append("blindmonk")
    elif label_key == "wukong":
        aliases.append("wuko")

    return aliases


def champion_alias_rank(entity: ReferenceEntity, alias: str) -> tuple[bool, bool, str]:
    """返回同名候选优先级，避免扩展模式实体覆盖基础英雄。

    规范 alias 优先于派生名称；派生名称冲突时，基础英雄优先于模式实体。
    """

    exact = alias in {entity.key, compact(entity.alias or "")}
    mode_entity = split_mode_entity_alias(entity.alias or "") is not None
    return (not exact, mode_entity, entity.key)


def skin_number_from_row(row: dict[str, Any], champion: ReferenceEntity) -> int | None:
    """从皮肤行中解析 SkinNN 编号。"""

    skin_id = row.get("id")

    if isinstance(skin_id, int) and champion.id is not None:
        number = skin_id - champion.id * 1000

        if number >= 0:
            return number

    for key in ["splashPath", "uncenteredSplashPath", "tilePath", "loadScreenPath"]:
        value = row.get(key)

        if not isinstance(value, str):
            continue

        match = re.search(r"/Skin([0-9]+)/", value, flags=re.IGNORECASE)

        if match is not None:
            return int(match.group(1))

    return None


def skinline_aliases(name: str) -> list[str]:
    """生成 skinline 事件名可能使用的紧凑别名。"""

    aliases = {compact(name)}
    without_season = re.sub(r"\bSeason\s+[0-9]+\b", "", name, flags=re.IGNORECASE).strip()

    if without_season:
        aliases.add(compact(without_season))

    if name.endswith("s") and len(name) > 4:
        aliases.add(compact(name[:-1]))

    return [alias for alias in aliases if alias]


def load_spells_from_champion_detail(
    row: dict[str, Any],
    entity: ReferenceEntity,
    source: str,
) -> list[ReferenceSpell]:
    """从单个 champion detail 构建技能参考词条。"""

    spells: list[ReferenceSpell] = []
    passive = row.get("passive")

    if isinstance(passive, dict):
        spell = load_spell_from_row(passive, entity, "p", source, "passive_name")

        if spell is not None:
            spells.append(spell)

    for spell_row in row.get("spells") or []:
        if not isinstance(spell_row, dict):
            continue

        spell_key = str(spell_row.get("spellKey") or "").lower()

        if spell_key not in {"q", "w", "e", "r"}:
            continue

        spell = load_spell_from_row(spell_row, entity, spell_key, source, "spell_name")

        if spell is not None:
            spells.append(spell)

    return spells


def iter_dragontail_champions(base: Path) -> list[tuple[dict[str, Any], str]]:
    """遍历 dragontail 的 championFull 或 champion/*.json 数据。"""

    rows: list[tuple[dict[str, Any], str]] = []
    full = read_json_if_exists(base / "championFull.json")

    if isinstance(full, dict):
        data = full.get("data")

        if isinstance(data, dict):
            for alias, row in sorted(data.items()):
                if isinstance(row, dict):
                    rows.append((row, f"dragontail:championFull.json#{alias}"))

            return rows

    champion_root = base / "champion"

    if not champion_root.is_dir():
        return rows

    for path in sorted(champion_root.glob("*.json")):
        payload = read_json_if_exists(path)

        if not isinstance(payload, dict):
            continue

        data = payload.get("data")

        if not isinstance(data, dict):
            continue

        for alias, row in sorted(data.items()):
            if isinstance(row, dict):
                rows.append((row, f"dragontail:champion/{path.name}#{alias}"))

    return rows


def merge_dragontail_spells(
    spells: list[ReferenceSpell],
    row: dict[str, Any],
    entity: ReferenceEntity,
    source: str,
) -> None:
    """把 dragontail 官方技能 id 合并到现有技能 term。"""

    by_key = {spell.spell_key: spell for spell in spells}
    rows = row.get("spells")

    if not isinstance(rows, list):
        return

    for spell_key, spell_row in zip(["q", "w", "e", "r"], rows, strict=False):
        if not isinstance(spell_row, dict):
            continue

        spell = by_key.get(spell_key)

        if spell is None:
            spell = load_spell_from_row(spell_row, entity, spell_key, source, "spell_name")

            if spell is None:
                continue

            spells.append(spell)
            by_key[spell_key] = spell

        add_dragontail_spell_terms(spell, spell_row, entity)


def add_dragontail_spell_terms(spell: ReferenceSpell, row: dict[str, Any], entity: ReferenceEntity) -> None:
    """加入 dragontail 官方 spell id、图标名和常见内部键位别名。"""

    spell_id = row.get("id")

    if isinstance(spell_id, str):
        add_spell_term(spell.terms, spell_id, "dragontail_spell_id", entity.alias)
        add_spell_term(spell.terms, f"{entity.alias or ''}{spell_id}", "dragontail_spell_id", entity.alias)

    image = row.get("image")

    if isinstance(image, dict):
        full = image.get("full")

        if isinstance(full, str):
            add_spell_term(spell.terms, basename_without_extension(full), "dragontail_image", entity.alias)

    add_spell_key_terms(spell.terms, spell.spell_key, entity.alias)
    add_spell_term(spell.terms, f"{entity.alias or ''}Spell{spell.spell_key.upper()}", "spell_key", entity.alias)
    add_spell_term(spell.terms, f"{entity.alias or ''}{spell.spell_key.upper()}Spell", "spell_key", entity.alias)


def load_spell_from_row(
    row: dict[str, Any],
    entity: ReferenceEntity,
    spell_key: str,
    source: str,
    name_source: str,
) -> ReferenceSpell | None:
    """从技能 JSON 行提取可匹配 term。"""

    name = str(row.get("name") or "").strip()
    terms: dict[str, str] = {}
    add_spell_term(terms, name, name_source, entity.alias)
    add_spell_name_parts(terms, name, name_source, entity.alias)
    add_spell_key_terms(terms, spell_key, entity.alias)

    for alias in champion_extra_aliases(entity):
        add_spell_key_terms(terms, spell_key, alias)

    for key, value in row.items():
        if not isinstance(value, str) or not key.lower().endswith("path"):
            continue

        add_spell_term(terms, basename_without_extension(value), "asset_path", entity.alias)

    keywords = spell_keywords(row, name)
    flags = spell_flags(row)

    if not terms:
        return None

    return ReferenceSpell(
        champion_key=entity.key,
        spell_key=spell_key,
        name=name or spell_key.upper(),
        source=source,
        terms=terms,
        keywords=keywords,
        flags=flags,
    )


def reference_collection_spells(row: dict[str, Any], entity: ReferenceEntity) -> list[ReferenceSpell]:
    """从内部 reference 英雄分组构建技能参考词条。"""

    spells: list[ReferenceSpell] = []
    raw_spells = row.get("spells")

    if not isinstance(raw_spells, list):
        return spells

    for raw_spell in raw_spells:
        if not isinstance(raw_spell, dict):
            continue

        slot = str(raw_spell.get("slot") or "").lower()

        if slot not in {"p", "q", "w", "e", "r"}:
            continue

        name = first_language_value(raw_spell.get("names")) or slot.upper()
        terms: dict[str, str] = {}
        add_spell_term(terms, name, "reference_spell_name", entity.alias)
        add_spell_name_parts(terms, name, "reference_spell_name", entity.alias)
        add_spell_key_terms(terms, slot, entity.alias)

        for id_value in reference_values(raw_spell.get("ids")):
            add_spell_term(terms, id_value, "reference_spell_id", entity.alias)
            add_spell_term(terms, f"{entity.alias or ''}{id_value}", "reference_spell_id", entity.alias)

        keywords = reference_description_keywords(raw_spell, name)
        flags = reference_description_flags(raw_spell)

        if terms:
            spells.append(
                ReferenceSpell(
                    champion_key=entity.key,
                    spell_key=slot,
                    name=name,
                    source="reference/champions",
                    terms=terms,
                    keywords=keywords,
                    flags=flags,
                )
            )

    return spells


def first_reference_label(entity_data: dict[str, Any]) -> str | None:
    """从 entity.names 选择英文优先的标签。"""

    return first_language_value(entity_data.get("names"))


def first_language_value(language_rows: Any) -> str | None:
    """从语言映射中读取 en_US 优先的第一个 value。"""

    if not isinstance(language_rows, dict):
        return None

    for language in ["en_US", "zh_CN", *sorted(language_rows)]:
        rows = language_rows.get(language)

        for value in reference_values(rows):
            return value

    return None


def reference_values(rows: Any) -> list[str]:
    """读取 reference value 数组。"""

    if not isinstance(rows, list):
        return []

    values: list[str] = []
    for row in rows:
        if isinstance(row, dict) and isinstance(row.get("value"), str):
            values.append(row["value"])

    return values


def reference_description_keywords(raw_spell: dict[str, Any], name: str) -> list[str]:
    """从内部 reference 描述快照生成弱匹配关键词。"""

    text_parts = [name]
    descriptions = raw_spell.get("descriptions")

    if isinstance(descriptions, dict):
        for language_data in descriptions.values():
            if not isinstance(language_data, dict):
                continue

            snapshots = language_data.get("snapshots")

            if not isinstance(snapshots, list):
                continue

            for snapshot in snapshots:
                if isinstance(snapshot, dict) and isinstance(snapshot.get("value"), str):
                    text_parts.append(snapshot["value"])

    words: set[str] = set()

    for part in text_parts:
        for word in split_identifier(strip_markup(part)):
            if is_spell_keyword(word):
                words.add(compact(word))

    return sorted(words)


def reference_description_flags(raw_spell: dict[str, Any]) -> list[str]:
    """从内部 reference 描述快照生成机制标记。"""

    text_parts: list[str] = []
    descriptions = raw_spell.get("descriptions")

    if isinstance(descriptions, dict):
        for language_data in descriptions.values():
            if not isinstance(language_data, dict):
                continue

            for snapshot in language_data.get("snapshots") or []:
                if isinstance(snapshot, dict) and isinstance(snapshot.get("value"), str):
                    text_parts.append(snapshot["value"])

    text = " ".join(text_parts).lower()
    flags: list[str] = []

    if "recast" in text:
        flags.append("recast")

    return flags


def string_or_none(value: Any) -> str | None:
    """返回字符串或 None。"""

    return value if isinstance(value, str) else None


def add_spell_term(
    terms: dict[str, str],
    raw: str,
    source: str,
    champion_alias: str | None,
) -> None:
    """加入技能 term，并额外保存去掉英雄名前缀后的版本。"""

    key = compact(raw)

    if not key:
        return

    if len(key) < 3:
        return

    champion_key = compact(champion_alias or "")
    if source in {"spell_name", "reference_spell_name", "passive_name"} and key == champion_key:
        # 例如纳尔 R 的英文名 ``GNAR!`` 归一化后等于英雄 alias。它不能
        # 单独充当技能证据，否则 GnarTransform/GnarHelmet 等任意英雄前缀
        # cue 都会被误判成 R；真实技能仍可由 spell id、键位词和上下文规则命中。
        return

    terms.setdefault(key, source)

    if champion_key and key.startswith(champion_key) and len(key) > len(champion_key):
        stripped_key = key[len(champion_key) :]

        if len(stripped_key) >= 3:
            terms.setdefault(stripped_key, source)


def add_spell_name_parts(
    terms: dict[str, str],
    raw: str,
    source: str,
    champion_alias: str | None,
) -> None:
    """把带斜杠的多段技能名单独加入索引。"""

    for part in re.split(r"[/|]", raw):
        add_spell_term(terms, part, source, champion_alias)


def add_spell_key_terms(terms: dict[str, str], spell_key: str, champion_alias: str | None) -> None:
    """生成英雄名加技能键、阶段号这类内部命名 term。"""

    alias = champion_alias or ""

    if spell_key == "p":
        for raw in [f"{alias}Passive", f"{alias}P", "Passive"]:
            add_spell_term(terms, raw, "passive_key", champion_alias)
        return

    if spell_key not in {"q", "w", "e", "r"}:
        return

    add_spell_term(terms, f"{alias}{spell_key.upper()}", "spell_key", champion_alias)

    for stage, word in {1: "One", 2: "Two", 3: "Three", 4: "Four"}.items():
        add_spell_term(terms, f"{alias}{spell_key.upper()}{stage}", "spell_key_stage", champion_alias)
        add_spell_term(terms, f"{alias}{spell_key.upper()}{word}", "spell_key_stage", champion_alias)


def spell_keywords(row: dict[str, Any], name: str) -> list[str]:
    """从技能名和描述中提取用于弱匹配的关键词。"""

    text_parts = [name]

    for key in ["description", "dynamicDescription"]:
        value = row.get(key)

        if isinstance(value, str):
            text_parts.append(value)

    words: set[str] = set()

    for part in text_parts:
        for word in split_identifier(strip_markup(part)):
            if is_spell_keyword(word):
                words.add(compact(word))

    return sorted(words)


def spell_flags(row: dict[str, Any]) -> list[str]:
    """提取技能描述中的机制标记。"""

    text = " ".join(str(row.get(key) or "") for key in ["description", "dynamicDescription"]).lower()
    flags: list[str] = []

    if "recast" in text:
        flags.append("recast")

    return flags


def strip_markup(text: str) -> str:
    """移除描述中的 HTML 式标签。"""

    return re.sub(r"<[^>]+>", " ", text)


def split_identifier(text: str) -> list[str]:
    """按大小写、数字和分隔符拆出英文词。"""

    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return [word.lower() for word in re.findall(r"[A-Za-z0-9]+", spaced)]


def is_spell_keyword(word: str) -> bool:
    """过滤描述弱匹配中无意义的短词和停用词。"""

    if len(word) < 4:
        return False

    return word not in {
        "ability",
        "active",
        "after",
        "also",
        "amount",
        "bonus",
        "briefly",
        "cast",
        "champion",
        "champions",
        "cooldown",
        "damage",
        "dealing",
        "duration",
        "effect",
        "enemy",
        "enemies",
        "first",
        "from",
        "gains",
        "grants",
        "health",
        "nearby",
        "passive",
        "physical",
        "reduced",
        "seconds",
        "spell",
        "status",
        "target",
        "this",
        "unit",
        "with",
    }


def basename_without_extension(path: str) -> str:
    """取素材路径最后一段的无扩展名文件名。"""

    filename = path.replace("\\", "/").rsplit("/", 1)[-1]

    if "." not in filename:
        return filename

    return filename.rsplit(".", 1)[0]


def champion_spell_subjects(
    atom_raw: str,
    owner: ReferenceEntity | None,
    reference: ReferenceIndex,
) -> list[ReferenceEntity]:
    """按 owner 或英雄前缀挑出可能的技能主体。"""

    if owner is not None and owner.key in reference.champion_spells_by_alias:
        return [owner]

    token_key = compact(atom_raw)
    subjects: list[ReferenceEntity] = []

    for key, entity in reference.champions_by_alias.items():
        if entity.key not in reference.champion_spells_by_alias:
            continue

        if token_key.startswith(key) and len(token_key) > len(key):
            subjects.append(entity)

    subjects.sort(key=lambda entity: len(entity.key), reverse=True)
    return subjects


def spell_for_key(reference: ReferenceIndex, subject: ReferenceEntity, spell_key: str) -> ReferenceSpell | None:
    """按英雄和键位查找技能。"""

    for spell in reference.champion_spells_by_alias.get(subject.key) or []:
        if spell.spell_key == spell_key:
            return spell

    return None


def infer_spell_candidate_for_subject(
    record: EventDecompositionRecord,
    reference: ReferenceIndex,
    atom_raw: str,
    subject: ReferenceEntity,
    owner: ReferenceEntity | None,
) -> dict[str, Any] | None:
    """为某个英雄主体推断单个技能候选。"""

    spells = reference.champion_spells_by_alias.get(subject.key) or []
    token_key = compact(atom_raw)
    stripped_raw = strip_owner_alias(atom_raw, subject.alias)
    stripped_key = compact(stripped_raw)
    owner_matches = owner is not None and owner.key == subject.key
    subject_prefix = token_key.startswith(subject.key) and len(token_key) > len(subject.key)
    best: dict[str, Any] | None = None

    for spell in spells:
        for term, source in spell.terms.items():
            match = spell_term_match(token_key, stripped_key, subject.key, term, source)

            if match is None:
                continue

            manual_residual = manual_trusted_single_key_residual(match, subject, spell, reference.rules)
            if not has_trusted_single_key_residual(match, subject, spell, reference.rules):
                continue

            score, components = score_spell_match(record, spell, match, owner_matches, subject_prefix)
            if manual_residual is not None:
                score = min(100, score + 20)
                components.append("trusted_single_key_residual")

            if score < 70:
                # 裸 Passive 门(Gate②)的哨兵 reason 不落 record，这里显式留审计痕使丢弃可见
                if BARE_PASSIVE_GATE_REASON in components:
                    audit_bare_passive_gate_drop(record, subject, atom_raw)

                continue

            candidate = inferred_spell_candidate(record, subject, spell, atom_raw, match, score, components)
            attach_residual_modifier_analysis(candidate, match.residual, reference.rules)

            if best is None or int(candidate["score"]) > int(best["score"]):
                best = candidate

        keyword_match = description_keyword_match(stripped_raw, spell)

        # BasicAttack/CritAttack 类通用词只能证明攻击动作，不能证明技能槽位。
        if keyword_match is not None and is_generic_attack_keyword_match(keyword_match):
            keyword_match = None

        if keyword_match is not None:
            score, components = score_spell_match(record, spell, keyword_match, owner_matches, subject_prefix)

            if score >= 70:
                candidate = inferred_spell_candidate(record, subject, spell, atom_raw, keyword_match, score, components)
                attach_residual_modifier_analysis(candidate, keyword_match.residual, reference.rules)

                if best is None or int(candidate["score"]) > int(best["score"]):
                    best = candidate

    return best


def spell_term_match(
    token_key: str,
    stripped_key: str,
    subject_key: str,
    term: str,
    source: str,
) -> SpellTermMatch | None:
    """判断事件 token 是否命中某个技能 term，并返回残片。"""

    for candidate in [token_key, stripped_key]:
        if candidate == term:
            return SpellTermMatch(term=term, source=source, stage=stage_from_term(term))

        if can_prefix_match_spell_term(term, source) and candidate.startswith(term):
            residual = candidate[len(term) :]

            if len(residual) >= 3:
                return SpellTermMatch(
                    term=term,
                    source=source,
                    residual=residual,
                    match_type="prefix",
                    stage=stage_from_term(term),
                )

    prefixed = f"{subject_key}{term}"

    if token_key == prefixed:
        return SpellTermMatch(term=term, source=source, stage=stage_from_term(term))

    if (len(prefixed) >= 4 or source in {"spell_key", "passive_key"}) and token_key.startswith(prefixed):
        residual = token_key[len(prefixed) :]

        if len(residual) >= 3:
            return SpellTermMatch(
                term=term,
                source=source,
                residual=residual,
                match_type="prefix",
                stage=stage_from_term(term),
            )

    return None


def can_prefix_match_spell_term(term: str, source: str) -> bool:
    """判断 term 是否允许作为带 residual 的前缀命中。"""

    return len(term) >= 4 or source in {"spell_key", "passive_key"}


def has_trusted_single_key_residual(
    match: SpellTermMatch,
    subject: ReferenceEntity,
    spell: ReferenceSpell,
    rules: RuleBook | None,
) -> bool:
    """限制英雄名加单字母槽位的前缀匹配只接受受控后缀。

    ``LuxP...``、``RenektonE...`` 这类内部名不能仅因首字母与槽位相同
    就归属技能。只有后缀能够被既有 residual modifier 规则完整拆解时，才把
    ``<Champion><Q/W/E/R/P><modifier>`` 视为可信的内部技能命名。
    """

    if match.match_type != "prefix" or match.source not in {"spell_key", "passive_key"}:
        return True

    if match.term != f"{subject.key}{spell.spell_key}":
        return True

    modifiers, unresolved = parse_residual_modifiers(match.residual, rules)
    return (bool(modifiers) and not unresolved) or manual_trusted_single_key_residual(
        match, subject, spell, rules
    ) is not None


def manual_trusted_single_key_residual(
    match: SpellTermMatch,
    subject: ReferenceEntity,
    spell: ReferenceSpell,
    rules: RuleBook | None,
) -> object | None:
    """返回单字母技能 residual 的显式人工证据。"""

    if (
        rules is None
        or match.match_type != "prefix"
        or match.source not in {"spell_key", "passive_key"}
        or match.term != f"{subject.key}{spell.spell_key}"
    ):
        return None
    return rules.trusted_spell_residual_for(subject.alias or subject.key, spell.spell_key, match.residual)


def description_keyword_match(stripped_key: str, spell: ReferenceSpell) -> SpellTermMatch | None:
    """用描述关键词弱匹配视觉化技能名。"""

    if not spell.keywords:
        return None

    token_words = [compact(word) for word in split_identifier(stripped_key) if is_spell_keyword(word)]

    if len(token_words) < 2:
        return None

    keyword_set = set(spell.keywords)
    hits = [word for word in token_words if word in keyword_set]

    if len(hits) < 2 or len(hits) / len(token_words) < 0.65:
        return None

    return SpellTermMatch(
        term="".join(hits),
        source="description_keywords",
        match_type="keyword",
        keyword_hits=hits,
    )


# 普攻类事件 token 拆出的通用词；描述关键词弱匹配只命中这些词时证据不足
# 成员需与事件 token 分词后的词汇对齐（含 attacks/crits 等复数形态），而非技能描述原文词形
GENERIC_ATTACK_KEYWORDS = {"attack", "attacks", "basic", "crit", "crits"}


def is_generic_attack_keyword_match(match: SpellTermMatch) -> bool:
    """判断描述关键词命中是否全部由普攻类通用词构成。"""

    return bool(match.keyword_hits) and all(hit in GENERIC_ATTACK_KEYWORDS for hit in match.keyword_hits)


# 皮肤待机/命名状态事件家族；裸 Passive token 在这些事件里指动画状态而非 P 技能
ANIMATION_STATE_FAMILIES = {"skin_named_state", "skin_idle_state"}

# score_spell_match 压分丢弃裸 Passive 候选时返回的哨兵 reason；调用点据此留审计痕
BARE_PASSIVE_GATE_REASON = "bare_passive_state_token"


def is_bare_passive_state_match(record: EventDecompositionRecord, match: SpellTermMatch) -> bool:
    """判断动画状态事件里的裸 Passive exact 命中。"""

    return (
        match.source == "passive_key"
        and match.term == "passive"
        and not match.residual
        and record.profile.primary_family in ANIMATION_STATE_FAMILIES
    )


def stage_from_term(term: str) -> int | None:
    """从 q1/qone 这类 term 中解析技能阶段。"""

    lowered = term.lower()
    values = {
        "1": 1,
        "one": 1,
        "2": 2,
        "two": 2,
        "3": 3,
        "three": 3,
        "4": 4,
        "four": 4,
    }

    for suffix, stage in values.items():
        if any(lowered.endswith(f"{spell_key}{suffix}") for spell_key in ["q", "w", "e", "r"]):
            return stage

    return None


def score_spell_match(
    record: EventDecompositionRecord,
    spell: ReferenceSpell,
    match: SpellTermMatch,
    owner_matches: bool,
    subject_prefix: bool,
) -> tuple[int, list[str]]:
    """按上下文证据给技能候选打分。"""

    # 动画状态事件里的裸 Passive 是待机/状态动画命名，不足以证明 P 技能，压到阈值下丢弃
    if is_bare_passive_state_match(record, match):
        return 0, [BARE_PASSIVE_GATE_REASON]

    score = 0
    components: list[str] = []

    if owner_matches:
        score += 35
        components.append("owner_context")

    if subject_prefix:
        score += 15
        components.append("champion_prefix")

        if not owner_matches:
            score += 25
            components.append("embedded_subject")

    if match.source == "spell_name":
        score += 45
        components.append("spell_name")
    elif match.source == "passive_name":
        score += 40
        components.append("passive_name")
    elif match.source == "asset_path":
        score += 40
        components.append("asset_path")
    elif match.source == "dragontail_spell_id":
        score += 45
        components.append("dragontail_spell_id")
    elif match.source == "reference_spell_id":
        score += 45
        components.append("reference_spell_id")
    elif match.source == "dragontail_image":
        score += 40
        components.append("dragontail_image")
    elif match.source == "spell_key_stage":
        score += 45
        components.append("spell_key_stage")
    elif match.source == "spell_key":
        score += 35
        components.append("spell_key")
    elif match.source == "passive_key":
        score += 35
        components.append("passive_key")
    elif match.source == "description_keywords":
        score += 30
        components.append("description_keywords")
    else:
        score += 35
        components.append(match.source)

    if match.residual:
        score -= 15
        components.append("residual_fragment")

        residual_hits = residual_keyword_hits(match.residual, spell)

        if residual_hits:
            score += 5
            match.keyword_hits = sorted(set(match.keyword_hits + residual_hits))
            components.append("description_keywords")

    if "recast" in spell.flags and "recast" in match.residual:
        score += 0
        components.append("recast_context")

    if has_phase_context(record):
        score += 10
        components.append("phase_context")

    return max(0, min(score, 100)), dedupe_preserve_order(components)


def residual_keyword_hits(residual: str, spell: ReferenceSpell) -> list[str]:
    """查看 residual 是否落在技能描述关键词中。"""

    keyword_set = set(spell.keywords)
    return [word for word in split_identifier(residual) if is_spell_keyword(word) and compact(word) in keyword_set] + [
        keyword for keyword in keyword_set if len(keyword) >= 4 and keyword in residual
    ]


def inferred_spell_candidate(
    record: EventDecompositionRecord,
    subject: ReferenceEntity,
    spell: ReferenceSpell,
    raw: str,
    match: SpellTermMatch,
    score: int,
    components: list[str],
) -> dict[str, Any]:
    """构造英雄技能推测候选。"""

    spell_key_label = spell.spell_key.upper()
    subject_label = subject.alias or subject.label
    candidate: dict[str, Any] = {
        "kind": "inferred_champion_spell",
        "raw_event_name": record.raw_event_name,
        "raw": raw,
        "subject": subject.to_dict(),
        "spell_key": spell.spell_key,
        "spell_name": spell.name,
        "matched_term": match.term,
        "match_source": match.source,
        "match_type": match.match_type,
        "residual": match.residual,
        "score": score,
        "components": components,
        "keyword_hits": list(match.keyword_hits),
        "meaning": f"{subject_label} {spell_key_label} {spell.name} inferred from {match.source}.",
        "confidence": confidence_from_score(score),
        "evidence": spell.source,
    }

    if match.stage is not None:
        candidate["stage"] = match.stage

    return candidate


def attach_residual_modifier_analysis(
    candidate: dict[str, Any],
    residual: str,
    rules: RuleBook | None,
) -> None:
    """把 residual 片段拆成可审计的结构化修饰语。"""

    if not residual:
        return

    modifiers, unresolved = parse_residual_modifiers(residual, rules)

    if modifiers:
        candidate["residual_modifiers"] = modifiers
        candidate["components"] = dedupe_preserve_order([*candidate["components"], "residual_modifier"])

    candidate["unresolved_residual"] = unresolved


def parse_residual_modifiers(
    residual: str,
    rules: RuleBook | None,
) -> tuple[list[dict[str, Any]], str]:
    """按规则把 residual 从左到右切成修饰语和未解析片段。"""

    if rules is None:
        return [], residual

    key = compact(residual)

    if not key:
        return [], ""

    modifiers: list[dict[str, Any]] = []
    unresolved: list[str] = []
    cursor = 0
    terms = rules.residual_modifier_terms()

    while cursor < len(key):
        hit = next(
            ((term, modifier) for term, modifier in terms if key.startswith(term, cursor)),
            None,
        )

        if hit is None:
            unresolved.append(key[cursor])
            cursor += 1
            continue

        term, modifier = hit
        modifiers.append(modifier.to_dict())
        cursor += len(term)

    return modifiers, "".join(unresolved)


def dedupe_preserve_order(values: list[str]) -> list[str]:
    """保持顺序去重。"""

    result: list[str] = []

    for value in values:
        if value not in result:
            result.append(value)

    return result


def confidence_from_score(score: int) -> str:
    """把数值分转成候选置信度。"""

    if score >= 90:
        return "high"

    if score >= 70:
        return "medium"

    return "low"


def has_phase_context(record: EventDecompositionRecord) -> bool:
    """判断整句里是否有 cast/hit/buff 等阶段证据。"""

    if record.semantic_slots.get("phase"):
        return True

    for atom in record.lexical_atoms:
        if any(part.kind in {"phase_or_state_token", "phase_token", "trigger_token"} for part in atom.parts):
            return True

        raw_key = compact(atom.raw)

        if raw_key.startswith("on") and any(
            phase in raw_key for phase in ["buffactivate", "buffdeactivate", "cast", "hit", "missile", "launch"]
        ):
            return True

    return False


def normalize_game_data_root(root: Path) -> Path:
    """兼容 CommunityDragon 根目录和 WAD 解包目录。"""

    candidates = [
        root,
        root / "plugins" / "rcp-be-lol-game-data",
        root / "rcp-be-lol-game-data",
    ]

    for candidate in candidates:
        if (candidate / "global" / "default" / "v1").is_dir():
            return candidate

    return root


def normalize_dragontail_root(root: Path) -> Path:
    """兼容 dragontail 包根目录、版本目录和 locale 数据目录。"""

    candidates = [
        root,
        root / "data" / "en_US",
    ]

    for version_dir in sorted(root.glob("*")) if root.is_dir() else []:
        candidates.append(version_dir / "data" / "en_US")

    for candidate in candidates:
        if (candidate / "championFull.json").is_file() or (candidate / "champion").is_dir():
            return candidate

    return root


def read_json_if_exists(path: Path) -> Any:
    """读取存在的 JSON 文件；缺失时返回 None。"""

    if not path.is_file():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def parse_int(raw: str) -> int | None:
    """安全解析整数。"""

    try:
        return int(raw)
    except ValueError:
        return None


def append_candidate(record: EventDecompositionRecord, candidate: dict[str, Any]) -> None:
    """去重追加候选解释。"""

    key = json.dumps(candidate, ensure_ascii=False, sort_keys=True)
    existing = {json.dumps(item, ensure_ascii=False, sort_keys=True) for item in record.candidates}

    if key not in existing:
        record.candidates.append(candidate)
        audit_candidate_hit(record, candidate)


def audit_candidate_hit(record: EventDecompositionRecord, candidate: dict[str, Any]) -> None:
    """把候选解释转换为审计命中。"""

    event = audit_event_for_candidate(record, candidate)

    if event is not None:
        record_match(event)


def audit_event_for_candidate(
    record: EventDecompositionRecord,
    candidate: dict[str, Any],
) -> MatchAuditEvent | None:
    """为候选解释构造审计事件。"""

    kind = str(candidate.get("kind") or "")

    if kind == "reference_entity":
        entity = candidate.get("entity")

        if not isinstance(entity, dict):
            return None

        entity_kind = str(entity.get("kind") or "unknown")
        entity_key = str(entity.get("key") or candidate.get("raw") or "unknown")
        source = str(entity.get("source") or candidate.get("evidence") or "reference")
        return MatchAuditEvent(
            matcher_kind="reference",
            rule_id=f"reference:{source}:{entity_kind}:{entity_key}",
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=record.owner.alias or record.owner.raw_owner_token,
            token=str(candidate.get("raw") or entity_key),
        )

    if kind == "inferred_champion_spell":
        subject = candidate.get("subject")
        subject_key = entity_key_from_mapping(subject)
        source = str(candidate.get("evidence") or "champion_spell_reference")
        rule_id = ":".join(
            [
                "inference",
                "champion_spell",
                subject_key,
                str(candidate.get("spell_key") or "unknown"),
                str(candidate.get("match_source") or "unknown"),
            ]
        )
        return MatchAuditEvent(
            matcher_kind="inference",
            rule_id=rule_id,
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=subject_key,
            token=str(candidate.get("raw") or ""),
        )

    if kind == "champion_spell_combo":
        subject = candidate.get("subject")
        subject_key = entity_key_from_mapping(subject)
        sequence = "".join(str(item) for item in candidate.get("spell_sequence") or [])
        source = str(candidate.get("evidence") or "champion_spell_combo")
        return MatchAuditEvent(
            matcher_kind="inference",
            rule_id=f"inference:champion_spell_combo:{subject_key}:{sequence}",
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=subject_key,
            token=str(candidate.get("raw") or ""),
        )

    if kind in {"mode_champion_actor", "mode_namespace", "composite_owner", "structured_counterpart"}:
        source = str(candidate.get("evidence") or kind)
        mode_suffix = f":{candidate.get('mode')}" if kind == "mode_namespace" else ""
        return MatchAuditEvent(
            matcher_kind="inference",
            rule_id=f"inference:{kind}{mode_suffix}",
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=record.owner.alias or record.owner.raw_owner_token,
            token=str(candidate.get("raw") or ""),
        )

    if kind == "ultimate_spellbook_spell":
        subject = candidate.get("subject")
        subject_key = entity_key_from_mapping(subject)
        source = str(candidate.get("evidence") or "ultimate_spellbook_event_name")
        return MatchAuditEvent(
            matcher_kind="inference",
            rule_id=f"inference:ultimate_spellbook:{subject_key}:r",
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=subject_key,
            token=str(candidate.get("raw") or ""),
        )

    return None


def audit_contextual_alias_hit(record: EventDecompositionRecord, rule: ContextualAlias, raw: str) -> None:
    """记录人工上下文别名命中。"""

    record_match(
        MatchAuditEvent(
            matcher_kind="manual_contextual_alias",
            rule_id=f"contextual:{rule.scope}:{rule.owner}:{rule.token}",
            source_path=rule.source_path,
            raw_event_name=record.raw_event_name,
            owner=rule.owner,
            token=raw,
        )
    )


def audit_residual_modifier_hit(record: EventDecompositionRecord, modifier: dict[str, Any]) -> None:
    """记录 residual 修饰语规则命中。"""

    key = str(modifier.get("key") or modifier.get("raw") or "unknown")
    kind = str(modifier.get("kind") or "modifier")
    source = str(modifier.get("source") or "manual_residual_modifier")
    record_match(
        MatchAuditEvent(
            matcher_kind="manual_residual_modifier",
            rule_id=f"residual:{kind}:{key}",
            source_path=source,
            raw_event_name=record.raw_event_name,
            owner=record.owner.alias or record.owner.raw_owner_token,
            token=str(modifier.get("raw") or key),
        )
    )


def audit_low_trust_event_rule_hit(record: EventDecompositionRecord, rule: LowTrustEventRule) -> None:
    """记录整句低信任规则命中。"""

    record_match(
        MatchAuditEvent(
            matcher_kind="manual_low_trust_event_rule",
            rule_id=f"low_trust_event:{rule.rule_id}",
            source_path=rule.source_path,
            raw_event_name=record.raw_event_name,
            owner=record.owner.alias or record.owner.raw_owner_token,
            token=record.raw_event_name,
        )
    )


def audit_bare_passive_gate_drop(record: EventDecompositionRecord, subject: ReferenceEntity, raw: str) -> None:
    """记录动画状态事件里裸 Passive 候选被压分丢弃。"""

    record_match(
        MatchAuditEvent(
            matcher_kind="inference_gate",
            rule_id=f"spell_gate:{BARE_PASSIVE_GATE_REASON}",
            source_path="decomposition_reference.score_spell_match",
            raw_event_name=record.raw_event_name,
            owner=subject.alias or subject.key,
            token=raw,
        )
    )


def entity_key_from_mapping(value: Any) -> str:
    """从候选中的实体字典取稳定 key。"""

    if not isinstance(value, dict):
        return "unknown"

    return str(value.get("key") or value.get("alias") or value.get("label") or "unknown")


def entity_candidate(
    record: EventDecompositionRecord,
    raw: str,
    entity: ReferenceEntity,
    confidence: str,
) -> dict[str, Any]:
    """构造参考实体候选解释。"""

    return {
        "kind": "reference_entity",
        "raw_event_name": record.raw_event_name,
        "raw": raw,
        "entity": entity.to_dict(),
        "meaning": f"{raw} resolves to {entity.kind}: {entity.label}",
        "confidence": confidence,
        "evidence": entity.source,
    }


def contextual_alias_candidate(
    record: EventDecompositionRecord,
    raw: str,
    rule: ContextualAlias,
    subject: ReferenceEntity | None,
) -> dict[str, Any]:
    """构造上下文别名候选解释。"""

    candidate: dict[str, Any] = {
        "kind": "contextual_alias",
        "raw_event_name": record.raw_event_name,
        "raw": raw,
        "alias_kind": rule.kind,
        "label": rule.label,
        "component": rule.component,
        "spell_key": rule.spell,
        "mode": rule.mode,
        "mode_label": rule.mode_label,
        "confidence": rule.confidence,
        "source": rule.source,
        "evidence": rule.source,
        "components": ["owner_context", "contextual_alias"],
        "meaning": f"{rule.owner} {rule.token} resolves to {rule.kind}: {rule.label}",
    }

    if subject is not None:
        candidate["subject"] = subject.to_dict()

    if rule.note is not None:
        candidate["note"] = rule.note

    return candidate


def set_slot_entity(slots: dict[str, Any], location: str, entity: ReferenceEntity) -> None:
    """按 unknown location 回填 slot entity。"""

    set_slot_value(slots, location, entity.to_dict())


def set_slot_value(slots: dict[str, Any], location: str, value: dict[str, Any]) -> None:
    """按 unknown location 回填结构化 slot 值。"""

    if location.endswith("counterpart") and isinstance(slots.get("counterpart"), dict):
        slots["counterpart"] = value
    elif location == "semantic_slots.spell_context" and isinstance(slots.get("spell_context"), dict):
        slots["spell_context"] = {**value, "raw": slots["spell_context"]["raw"]}


def strip_owner_alias(token: str, owner_alias: str | None) -> str:
    """移除 token 开头的 owner alias。"""

    if owner_alias and token.lower().startswith(owner_alias.lower()):
        return token[len(owner_alias) :]

    return token


def parse_spell_sequence(token: str) -> list[str]:
    """解析紧凑技能键序列。"""

    sequence, _ = parse_spell_sequence_details(token)
    return sequence


def parse_spell_sequence_details(token: str) -> tuple[list[str], list[dict[str, Any]]]:
    """解析 Q3W/RW 这类紧凑技能序列和次数修饰。"""

    if re.fullmatch(r"(?:[QWERP][0-9]?){2,4}", token, re.IGNORECASE) is None:
        return [], []

    sequence: list[str] = []
    modifiers: list[dict[str, Any]] = []
    cursor = 0

    while cursor < len(token):
        spell_key = token[cursor].lower()
        cursor += 1
        digits = []

        while cursor < len(token) and token[cursor].isdigit():
            digits.append(token[cursor])
            cursor += 1

        sequence.append(spell_key)

        if digits:
            numeric_modifier = int("".join(digits))
            modifiers.append(
                {
                    "spell_key": spell_key,
                    # 旧 cast_count 字段暂留兼容；stage 是新的权威含义，避免
                    # 把 Q3/W2 这类技能分段误写成施放次数或技能等级。
                    "cast_count": numeric_modifier,
                    "stage": numeric_modifier,
                    "source": "compact_spell_sequence",
                }
            )

    return sequence, modifiers


def spell_combo_meaning(alias: str, sequence: list[str]) -> str:
    """构造技能组合候选说明。"""

    if alias.lower() == "leblanc" and sequence[0] == "r" and len(sequence) == 2:
        return f"{alias} R appears to mimic {sequence[1].upper()}."

    return f"{alias} compact spell sequence {'+'.join(sequence).upper()}."


def is_switch_record(record: EventDecompositionRecord) -> bool:
    """判断是否是 Switch 控制事件。"""

    return any(compact(atom.raw) == "switch" for atom in record.lexical_atoms)


def raw_atom(record: EventDecompositionRecord, index: int) -> str | None:
    """按位置读取原始 atom。"""

    if index >= len(record.lexical_atoms):
        return None

    return record.lexical_atoms[index].raw


def first_trigger_atom(record: EventDecompositionRecord, start_index: int) -> LexicalAtom | None:
    """查找指定位置后的第一个 trigger atom。"""

    for atom in record.lexical_atoms[start_index:]:
        if part_value(atom, "trigger_token") is not None:
            return atom

    return None


def part_value(atom: LexicalAtom, kind: str) -> str | None:
    """读取 atom 中指定 kind 的 value。"""

    for part in atom.parts:
        if part.kind == kind:
            return str(part.value)

    return None


def part_raw(atom: LexicalAtom, kind: str) -> str | None:
    """读取 atom 中指定 kind 的 raw。"""

    for part in atom.parts:
        if part.kind == kind:
            return part.raw

    return None


def first_phase_value(record: EventDecompositionRecord) -> str | None:
    """返回记录中显式事件尾阶段，避免组件状态词抢占。"""

    for atom in reversed(record.lexical_atoms):
        phase = phase_alias_from_atom(atom)

        if phase is not None:
            return phase

    for atom in reversed(record.lexical_atoms):
        phase = part_value(atom, "phase_token")

        if phase is not None:
            return phase

    for atom in reversed(record.lexical_atoms):
        phase = part_value(atom, "phase_or_state_token")

        if phase is not None:
            return phase

    return None


def backfill_phase(record: EventDecompositionRecord) -> None:
    """把 lexer 已识别但 builder 重建 slots 时漏掉的 phase 回填。"""

    phase = first_phase_value(record)

    if phase is not None and "phase" not in record.semantic_slots:
        record.semantic_slots["phase"] = phase


def phase_alias_from_atom(atom: LexicalAtom) -> str | None:
    """把 OnCast 等完整 atom 映射为阶段值。"""

    values = {
        "onbuffactivate": "on_buff_activate",
        "onbuffdeactivate": "on_buff_deactivate",
        "onbuffcast": "on_buff_cast",
        "buffactivate": "buff_activate",
        "buffdeactivate": "buff_deactivate",
        "buffcast": "buff_cast",
        "cast": "cast",
        "hit": "hit",
        "missilelaunch": "missile_launch",
        "onmissilelaunch": "missile_launch",
        "oml": "missile_launch",
        "misslelaunch": "missile_launch",
        "launch": "launch",
        "missilecast": "missile_cast",
        "oncast": "cast",
        "activate": "activate",
        "deactivate": "deactivate",
    }
    return values.get(atom.raw.lower())


def first_number(atom: LexicalAtom) -> int | None:
    """读取 atom 中的第一个数字片段。"""

    for part in atom.parts:
        if part.kind == "number_token" and isinstance(part.value, int):
            return part.value

    return None


def first_context_atom_after(record: EventDecompositionRecord, index: int) -> LexicalAtom | None:
    """查找指定位置之后的第一个非阶段上下文 atom。"""

    for atom in record.lexical_atoms[index + 1 :]:
        if part_value(atom, "trigger_token") is not None:
            continue

        if is_phase_like_atom(atom):
            continue

        return atom

    return None


def unresolved_context_after(record: EventDecompositionRecord, index: int) -> list[str]:
    """收集上下文别名后仍未结构化的非阶段片段。"""

    values: list[str] = []

    for atom in record.lexical_atoms[index + 1 :]:
        if part_value(atom, "trigger_token") is not None:
            continue

        if is_phase_like_atom(atom):
            continue

        values.append(atom.raw)

    return values


def is_phase_like_atom(atom: LexicalAtom) -> bool:
    """判断 atom 是否只是阶段或状态上下文。"""

    raw_key = compact(atom.raw)

    if raw_key in {"2d", "3d", "leadin", "leadout", "loop", "winddown", "windup"}:
        return True

    if raw_key.startswith("on") and any(
        phase in raw_key for phase in ["buffactivate", "buffdeactivate", "cast", "hit", "missile", "launch"]
    ):
        return True

    return any(part.kind in {"phase_or_state_token", "phase_token"} for part in atom.parts)


def normalize_trigger_family(trigger: str) -> str:
    """规范化 trigger family。"""

    names = {
        "basicattack": "basic_attack",
        "critattack": "crit_attack",
    }
    return names.get(trigger, trigger)


def clear_unknown_head(record: EventDecompositionRecord, compact_key: str) -> None:
    """移除已解析的未知头 token。"""

    record.unknowns = [
        unknown
        for unknown in record.unknowns
        if not (unknown.kind is UnknownKind.UNKNOWN_HEAD_TOKEN and compact(unknown.raw) == compact_key)
    ]


def clear_unknowns_by_keys(record: EventDecompositionRecord, compact_keys: set[str]) -> None:
    """移除已经被结构化解释覆盖的未知项。"""

    targets = {compact(key) for key in compact_keys if key}
    record.unknowns = [unknown for unknown in record.unknowns if compact(unknown.raw) not in targets]


def clear_contextual_unknowns(
    record: EventDecompositionRecord,
    raw: str,
    rule: ContextualAlias,
) -> None:
    """移除已由上下文别名解释的未知项。"""

    target_keys = {compact(raw), compact(rule.token)}

    if rule.spell == "p":
        target_keys.add("p")

    if rule.scope == "mode":
        target_keys.add(compact(rule.owner))

    clear_unknowns_by_keys(record, target_keys)


def clear_spell_unknown(record: EventDecompositionRecord, raw: str) -> None:
    """移除已由技能推测解释的未知项。"""

    target = compact(raw)
    resolved_kinds = {
        UnknownKind.UNKNOWN_HEAD_TOKEN,
        UnknownKind.AMBIGUOUS_SPELL_ALIAS,
        UnknownKind.REQUIRES_CHAMPION_MECHANIC_CONTEXT,
    }
    record.unknowns = [
        unknown
        for unknown in record.unknowns
        if not (unknown.kind in resolved_kinds and compact(unknown.raw) == target)
    ]


def max_confidence(
    left: DecompositionConfidence,
    right: DecompositionConfidence,
) -> DecompositionConfidence:
    """取更高置信度。"""

    order = {
        DecompositionConfidence.FAILED: 0,
        DecompositionConfidence.LOW: 1,
        DecompositionConfidence.MEDIUM: 2,
        DecompositionConfidence.HIGH: 3,
    }

    return left if order[left] >= order[right] else right


def confidence_value(raw: str) -> DecompositionConfidence:
    """把规则文本置信度转为模型枚举。"""

    try:
        return DecompositionConfidence(raw)
    except ValueError:
        return DecompositionConfidence.MEDIUM


def load_rulebook(
    rules_root: Path | str | None,
    overrides_root: Path | str | None,
) -> RuleBook:
    """加载默认或指定规则目录。"""

    repo_root = Path(__file__).resolve().parents[2]
    rules_path = Path(rules_root) if rules_root is not None else repo_root / "rules"
    overrides_path = Path(overrides_root) if overrides_root is not None else repo_root / "overrides"
    return RuleBook.load(rules_path, overrides_path)
