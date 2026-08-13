"""文本规则与覆写读取。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@dataclass(slots=True)
class PhaseRule:
    """阶段规则。"""

    key: str
    name: str


@dataclass(slots=True)
class LegacyAlias:
    """历史别名覆写。"""

    owner_alias: str | None = None
    skin_en: str | None = None
    skin_name: str | None = None
    form_name: str | None = None
    note: str | None = None


@dataclass(slots=True)
class ActionAlias:
    """动作别名规则。"""

    name: str | None = None
    hotkey: str | None = None
    category: str | None = None
    spell: str | None = None
    modifiers: tuple[str, ...] = ()
    note: str | None = None


@dataclass(slots=True)
class ContextualAlias:
    """需要 owner 或模式上下文才能成立的别名规则。"""

    scope: str
    owner: str
    token: str
    kind: str
    label: str
    confidence: str = "medium"
    source: str = "manual_contextual_alias"
    source_path: str = "rules/contextual_aliases.yaml"
    spell: str | None = None
    component: str | None = None
    mode: str | None = None
    mode_label: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """返回 JSON 友好的字典。"""

        return {
            "scope": self.scope,
            "owner": self.owner,
            "token": self.token,
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "source": self.source,
            "source_path": self.source_path,
            "spell": self.spell,
            "component": self.component,
            "mode": self.mode,
            "mode_label": self.mode_label,
            "note": self.note,
        }


@dataclass(slots=True)
class ResidualModifier:
    """技能主干命中后 residual 中的结构化修饰语。"""

    token: str
    key: str
    kind: str
    label: str
    confidence: str = "medium"
    source: str = "manual_residual_modifier"
    display: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """返回 JSON 友好的字典。"""

        return {
            "raw": self.token,
            "key": self.key,
            "kind": self.kind,
            "label": self.label,
            "confidence": self.confidence,
            "source": self.source,
            "display": self.display,
            "note": self.note,
        }


@dataclass(slots=True)
class ExternalEntity:
    """没有官方结构源时的外部实体词条。"""

    token: str
    kind: str
    label: str
    confidence: str = "medium"
    source: str = "rules/external_entities.yaml"
    trust: str = "manual_untrusted"
    note: str | None = None

    def to_metadata(self) -> dict[str, str | None]:
        """返回实体 metadata。"""

        return {
            "trust": self.trust,
            "confidence": self.confidence,
            "note": self.note,
        }


@dataclass(slots=True)
class AttackModifierSpell:
    """确认会改写普攻的英雄技能槽白名单条目。"""

    champion: str
    spell_key: str
    note: str | None = None


@dataclass(slots=True)
class TrustedSpellResidual:
    """经人工证据确认的 owner + 单字母槽位 residual。"""

    champion: str
    spell_key: str
    residual: str
    note: str | None = None


@dataclass(slots=True)
class LowTrustEventRule:
    """整条事件名级别的低信任解释规则。"""

    rule_id: str
    raw_event_name: str
    category: str
    label_zh: str
    confidence: str = "medium"
    trust: str = "low"
    source: str = "manual_low_trust_event_rule"
    source_path: str = "rules/low_trust_events.yaml"
    category_zh: str | None = None
    note: str | None = None
    evidence: str | None = None
    suggestion: str | None = None

    def to_metadata(self) -> dict[str, str | None]:
        """返回语义 slot 中保存的规则元数据。"""

        return {
            "rule_id": self.rule_id,
            "raw_event_name": self.raw_event_name,
            "category": self.category,
            "category_zh": self.category_zh,
            "label_zh": self.label_zh,
            "confidence": self.confidence,
            "trust": self.trust,
            "source": self.source,
            "source_path": self.source_path,
            "note": self.note,
            "evidence": self.evidence,
            "suggestion": self.suggestion,
        }


@dataclass(slots=True)
class RuleBook:
    """规则集合。"""

    canonical_tokens: dict[str, str] = field(default_factory=dict)
    word_labels: dict[str, str] = field(default_factory=dict)
    phases: dict[str, PhaseRule] = field(default_factory=dict)
    modifiers: dict[str, str] = field(default_factory=dict)
    map_actions: dict[str, str] = field(default_factory=dict)
    map_tokens: dict[str, str] = field(default_factory=dict)
    legacy_aliases: dict[str, LegacyAlias] = field(default_factory=dict)
    global_actions: dict[str, ActionAlias] = field(default_factory=dict)
    champion_actions: dict[str, dict[str, ActionAlias]] = field(default_factory=dict)
    contextual_aliases: dict[tuple[str, str, str], ContextualAlias] = field(default_factory=dict)
    residual_modifiers: dict[str, ResidualModifier] = field(default_factory=dict)
    external_entities: dict[str, ExternalEntity] = field(default_factory=dict)
    low_trust_event_rules: dict[str, LowTrustEventRule] = field(default_factory=dict)
    attack_modifier_spells: dict[tuple[str, str], AttackModifierSpell] = field(default_factory=dict)
    trusted_spell_residuals: dict[tuple[str, str, str], TrustedSpellResidual] = field(default_factory=dict)

    @classmethod
    def load(cls, rules_root: Path, overrides_root: Path) -> RuleBook:
        """从规则目录与覆写目录读取数据。"""

        normalization = read_yaml(rules_root / "normalization.yaml")
        phase_data = read_yaml(rules_root / "phases.yaml")
        map_data = read_yaml(rules_root / "map_actions.yaml")
        action_data = read_yaml(rules_root / "action_aliases.yaml")
        contextual_data = read_yaml(rules_root / "contextual_aliases.yaml")
        contextual_fragments = read_contextual_fragments(rules_root)
        residual_data = read_yaml(rules_root / "residual_modifiers.yaml")
        external_entity_data = read_yaml(rules_root / "external_entities.yaml")
        low_trust_event_data = read_yaml(rules_root / "low_trust_events.yaml")
        spell_constraint_data = read_yaml(rules_root / "spell_match_constraints.yaml")
        legacy_data = read_yaml(overrides_root / "legacy_aliases.yaml")

        return cls(
            canonical_tokens={
                key.lower(): str(value) for key, value in normalization.get("canonical_tokens", {}).items()
            },
            word_labels={str(key): str(value) for key, value in normalization.get("word_labels", {}).items()},
            phases={
                str(key).lower(): PhaseRule(key=str(value["key"]), name=str(value["name"]))
                for key, value in phase_data.get("phases", {}).items()
            },
            modifiers={str(key).lower(): str(value) for key, value in phase_data.get("modifiers", {}).items()},
            map_actions={str(key): str(value) for key, value in map_data.get("actions", {}).items()},
            map_tokens={str(key): str(value) for key, value in map_data.get("token_labels", {}).items()},
            legacy_aliases={
                str(key).lower(): LegacyAlias(
                    owner_alias=value.get("owner_alias"),
                    skin_en=value.get("skin_en"),
                    skin_name=value.get("skin_name"),
                    form_name=value.get("form_name"),
                    note=value.get("note"),
                )
                for key, value in legacy_data.get("aliases", {}).items()
            },
            global_actions={
                str(key): build_action_alias(value) for key, value in action_data.get("global", {}).items()
            },
            champion_actions={
                str(owner): {str(key): build_action_alias(value) for key, value in rows.items()}
                for owner, rows in action_data.get("champions", {}).items()
            },
            contextual_aliases=build_contextual_aliases(contextual_data, contextual_fragments),
            residual_modifiers=build_residual_modifiers(residual_data),
            external_entities=build_external_entities(external_entity_data),
            low_trust_event_rules=build_low_trust_event_rules(
                low_trust_event_data,
                rules_root / "low_trust_events.yaml",
            ),
            attack_modifier_spells=build_attack_modifier_spells(spell_constraint_data),
            trusted_spell_residuals=build_trusted_spell_residuals(spell_constraint_data),
        )

    def normalize_token(self, token: str) -> str:
        """返回标准化 token。"""

        return self.canonical_tokens.get(token.lower(), token)

    def phase_for(self, token: str) -> PhaseRule | None:
        """按 token 查找阶段规则。"""

        return self.phases.get(token.lower())

    def modifier_for(self, token: str) -> str | None:
        """按 token 查找修饰词。"""

        return self.modifiers.get(token.lower())

    def map_action_for(self, token: str) -> str | None:
        """按完整 key 查找地图动作。"""

        return self.map_actions.get(token)

    def map_token_for(self, token: str) -> str | None:
        """按单个 token 查找地图片段标签。"""

        return self.map_tokens.get(token) or self.map_tokens.get(token.lower()) or self.map_tokens.get(token.title())

    def legacy_for(self, token: str) -> LegacyAlias | None:
        """按历史别名查找覆写。"""

        return self.legacy_aliases.get(token.lower())

    def word_for(self, token: str) -> str | None:
        """按片段查找中文标签。"""

        return self.word_labels.get(token) or self.word_labels.get(token.lower()) or self.word_labels.get(token.title())

    def action_for(self, owner_alias: str | None, token: str) -> ActionAlias | None:
        """按主体 alias 和 token 查找动作别名。"""

        if owner_alias is not None:
            owner_rows = self.champion_actions.get(owner_alias)

            if owner_rows is not None:
                hit = owner_rows.get(token)

                if hit is not None:
                    return hit

        return self.global_actions.get(token)

    def contextual_alias_for(self, scope: str, owner: str | None, token: str) -> ContextualAlias | None:
        """按上下文查找受限别名。"""

        if owner is None:
            return None

        return self.contextual_aliases.get((scope, rule_key(owner), rule_key(token)))

    def residual_modifier_for(self, token: str) -> ResidualModifier | None:
        """按 residual 片段查找修饰语。"""

        return self.residual_modifiers.get(rule_key(token))

    def residual_modifier_terms(self) -> list[tuple[str, ResidualModifier]]:
        """返回按长度降序排列的 residual 修饰语 term。"""

        return sorted(self.residual_modifiers.items(), key=lambda item: len(item[0]), reverse=True)

    def external_entity_for(self, token: str) -> ExternalEntity | None:
        """按 token 查找外部实体词条。"""

        return self.external_entities.get(rule_key(token))

    def low_trust_event_for(self, raw_event_name: str) -> LowTrustEventRule | None:
        """按完整事件名查找低信任解释规则。"""

        return self.low_trust_event_rules.get(event_rule_key(raw_event_name))

    def attack_modifier_spell_for(self, champion: str | None, spell_key: str) -> AttackModifierSpell | None:
        """按英雄和技能槽查找普攻改写白名单条目。"""

        if champion is None:
            return None

        return self.attack_modifier_spells.get((rule_key(champion), str(spell_key).lower()))

    def trusted_spell_residual_for(
        self,
        champion: str | None,
        spell_key: str,
        residual: str,
    ) -> TrustedSpellResidual | None:
        """按英雄、技能槽和完整 residual 查询人工证据。"""

        if champion is None:
            return None

        return self.trusted_spell_residuals.get((rule_key(champion), str(spell_key).lower(), rule_key(residual)))


def read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件。"""

    if not path.is_file():
        return {}

    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        return data

    return {}


def build_action_alias(data: Any) -> ActionAlias:
    """构造动作别名对象。"""

    if not isinstance(data, dict):
        return ActionAlias()

    modifiers = data.get("modifiers") or ()

    if not isinstance(modifiers, (list, tuple)):
        modifiers = ()

    return ActionAlias(
        name=data.get("name"),
        hotkey=data.get("hotkey"),
        category=data.get("category"),
        spell=data.get("spell"),
        modifiers=tuple(str(item) for item in modifiers),
        note=data.get("note"),
    )


def build_contextual_aliases(
    data: dict[str, Any],
    fragments: list[tuple[Path, dict[str, Any]]] | None = None,
) -> dict[tuple[str, str, str], ContextualAlias]:
    """构造受限上下文别名索引。"""

    result: dict[tuple[str, str, str], ContextualAlias] = {}
    legacy_aliases: dict[tuple[str, str, str], ContextualAlias] = {}
    fragment_aliases: dict[tuple[str, str, str], ContextualAlias] = {}
    sections = {
        "champions": "champion",
        "modes": "mode",
        "sfx": "sfx",
    }

    for section, scope in sections.items():
        owners = data.get(section, {})

        if not isinstance(owners, dict):
            continue

        for owner, rows in owners.items():
            if not isinstance(rows, dict):
                continue

            for token, value in rows.items():
                if not isinstance(value, dict):
                    continue

                alias = make_contextual_alias(
                    scope=scope,
                    owner=str(owner),
                    token=str(token),
                    value=value,
                    source_path="rules/contextual_aliases.yaml",
                )
                legacy_aliases[(scope, rule_key(owner), rule_key(token))] = alias

    for path, fragment_data in fragments or []:
        for key, alias in build_contextual_alias_fragment(path, fragment_data).items():
            add_contextual_alias(fragment_aliases, key, alias)

    result.update(legacy_aliases)

    for key, alias in fragment_aliases.items():
        existing = result.get(key)

        if existing is not None and contextual_alias_payload(existing) != contextual_alias_payload(alias):
            raise ValueError(
                f"重复 contextual alias 且内容不同: key={key}, old={existing.source_path}, new={alias.source_path}"
            )

        result[key] = alias

    return result


def read_contextual_fragments(rules_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """读取 contextual 分片 YAML。"""

    contextual_root = rules_root / "contextual"

    if not contextual_root.is_dir():
        return []

    return [(path, read_yaml(path)) for path in sorted(contextual_root.rglob("*.yaml"))]


def build_contextual_alias_fragment(path: Path, data: dict[str, Any]) -> dict[tuple[str, str, str], ContextualAlias]:
    """构造单个 contextual 分片中的别名索引。"""

    scope = str(data.get("scope") or "").strip()
    owner = str(data.get("owner") or "").strip()
    aliases = data.get("aliases") or {}

    if not scope or not owner or not isinstance(aliases, dict):
        return {}

    source_path = rule_source_path(path)
    result: dict[tuple[str, str, str], ContextualAlias] = {}

    for token, value in aliases.items():
        if not isinstance(value, dict):
            continue

        alias = make_contextual_alias(
            scope=scope,
            owner=owner,
            token=str(token),
            value=value,
            source_path=source_path,
        )
        add_contextual_alias(result, (scope, rule_key(owner), rule_key(token)), alias)

    return result


def make_contextual_alias(
    *,
    scope: str,
    owner: str,
    token: str,
    value: dict[str, Any],
    source_path: str,
) -> ContextualAlias:
    """构造 contextual alias 对象。"""

    return ContextualAlias(
        scope=scope,
        owner=owner,
        token=token,
        kind=str(value.get("kind") or "contextual_alias"),
        label=str(value.get("label") or token),
        confidence=str(value.get("confidence") or "medium"),
        source=str(value.get("source") or "manual_contextual_alias"),
        source_path=source_path,
        spell=str(value["spell"]).lower() if value.get("spell") is not None else None,
        component=str(value["component"]) if value.get("component") is not None else None,
        mode=str(value["mode"]) if value.get("mode") is not None else None,
        mode_label=str(value["mode_label"]) if value.get("mode_label") is not None else None,
        note=str(value["note"]) if value.get("note") is not None else None,
    )


def add_contextual_alias(
    result: dict[tuple[str, str, str], ContextualAlias],
    key: tuple[str, str, str],
    alias: ContextualAlias,
) -> None:
    """加入 contextual alias，并禁止同一来源集合内重复 key。"""

    existing = result.get(key)

    if existing is not None:
        raise ValueError(f"重复 contextual alias: key={key}, old={existing.source_path}, new={alias.source_path}")

    result[key] = alias


def contextual_alias_payload(alias: ContextualAlias) -> dict[str, str | None]:
    """返回用于比较语义等价的 contextual alias 内容。"""

    payload = alias.to_dict()
    payload.pop("source_path", None)
    return payload


def rule_source_path(path: Path) -> str:
    """返回从 rules/ 开始的稳定规则路径。"""

    parts = path.as_posix().split("/")

    if "rules" not in parts:
        return path.as_posix()

    return "/".join(parts[parts.index("rules") :])


def build_residual_modifiers(data: dict[str, Any]) -> dict[str, ResidualModifier]:
    """构造 residual 修饰语索引。"""

    result: dict[str, ResidualModifier] = {}
    rows = data.get("modifiers", {})

    if not isinstance(rows, dict):
        return result

    for token, value in rows.items():
        if not isinstance(value, dict):
            continue

        modifier = ResidualModifier(
            token=str(token),
            key=str(value.get("key") or token).lower(),
            kind=str(value.get("kind") or "modifier"),
            label=str(value.get("label") or token),
            confidence=str(value.get("confidence") or "medium"),
            source=str(value.get("source") or "manual_residual_modifier"),
            display=str(value["display"]) if value.get("display") is not None else None,
            note=str(value["note"]) if value.get("note") is not None else None,
        )
        result[rule_key(token)] = modifier

    return result


def build_external_entities(data: dict[str, Any]) -> dict[str, ExternalEntity]:
    """构造外部实体词条索引。"""

    result: dict[str, ExternalEntity] = {}

    for kind, rows in data.get("entities", {}).items():
        if not isinstance(rows, dict):
            continue

        for token, value in rows.items():
            if not isinstance(value, dict):
                continue

            entity = ExternalEntity(
                token=str(token),
                kind=str(value.get("kind") or kind),
                label=str(value.get("label") or token),
                confidence=str(value.get("confidence") or "medium"),
                source=str(value.get("source") or "rules/external_entities.yaml"),
                trust=str(value.get("trust") or "manual_untrusted"),
                note=str(value["note"]) if value.get("note") is not None else None,
            )
            result[rule_key(token)] = entity

    return result


def build_low_trust_event_rules(data: dict[str, Any], path: Path) -> dict[str, LowTrustEventRule]:
    """构造整句低信任事件规则索引。"""

    result: dict[str, LowTrustEventRule] = {}
    rows = data.get("events", [])
    source_path = rule_source_path(path)

    if not isinstance(rows, list):
        return result

    for index, value in enumerate(rows, start=1):
        if not isinstance(value, dict):
            continue

        raw_event_name = str(value.get("raw_event_name") or "").strip()

        if not raw_event_name:
            continue

        rule = LowTrustEventRule(
            rule_id=str(value.get("rule_id") or value.get("id") or f"low_trust_event_{index:04d}"),
            raw_event_name=raw_event_name,
            category=str(value.get("category") or "reviewed_low_trust_event"),
            category_zh=str(value["category_zh"]) if value.get("category_zh") is not None else None,
            label_zh=str(value.get("label_zh") or value.get("translation_zh") or raw_event_name),
            confidence=str(value.get("confidence") or "medium"),
            trust=str(value.get("trust") or "low"),
            source=str(value.get("source") or "manual_low_trust_event_rule"),
            source_path=str(value.get("source_path") or source_path),
            note=str(value["note"]) if value.get("note") is not None else None,
            evidence=str(value["evidence"]) if value.get("evidence") is not None else None,
            suggestion=str(value["suggestion"]) if value.get("suggestion") is not None else None,
        )
        key = event_rule_key(raw_event_name)

        if key in result:
            existing = result[key]
            raise ValueError(f"重复低信任事件规则: raw={raw_event_name}, old={existing.rule_id}, new={rule.rule_id}")

        result[key] = rule

    return result


def build_attack_modifier_spells(data: dict[str, Any]) -> dict[tuple[str, str], AttackModifierSpell]:
    """构造普攻改写技能白名单索引。"""

    result: dict[tuple[str, str], AttackModifierSpell] = {}
    rows = data.get("attack_modifier_spells", {})

    if not isinstance(rows, dict):
        return result

    for champion, slots in rows.items():
        if not isinstance(slots, dict):
            continue

        for spell_key, value in slots.items():
            slot = str(spell_key).lower()

            # 白名单直接决定普攻事件能否展示为技能，槽位写错必须立即暴露
            if slot not in {"p", "q", "w", "e", "r"}:
                raise ValueError(f"非法普攻改写技能槽: champion={champion}, spell_key={spell_key}")

            note = value.get("note") if isinstance(value, dict) else None
            entry = AttackModifierSpell(
                champion=str(champion),
                spell_key=slot,
                note=str(note) if note is not None else None,
            )
            key = (rule_key(champion), slot)

            if key in result:
                existing = result[key]
                raise ValueError(f"重复普攻改写技能白名单: key={key}, old={existing.champion}, new={entry.champion}")

            result[key] = entry

    return result


def build_trusted_spell_residuals(data: dict[str, Any]) -> dict[tuple[str, str, str], TrustedSpellResidual]:
    """构造 owner + 单字母槽位 residual 的人工证据索引。"""

    result: dict[tuple[str, str, str], TrustedSpellResidual] = {}
    rows = data.get("trusted_single_key_residuals", {})

    if not isinstance(rows, dict):
        return result

    for champion, slots in rows.items():
        if not isinstance(slots, dict):
            continue

        for spell_key, residuals in slots.items():
            slot = str(spell_key).lower()
            if slot not in {"p", "q", "w", "e", "r"}:
                raise ValueError(f"非法可信技能 residual 槽: champion={champion}, spell_key={spell_key}")
            if not isinstance(residuals, dict):
                continue

            for residual, value in residuals.items():
                residual_key = rule_key(residual)
                if not residual_key:
                    raise ValueError(f"可信技能 residual 不能为空: champion={champion}, spell_key={spell_key}")
                note = value.get("note") if isinstance(value, dict) else None
                entry = TrustedSpellResidual(
                    champion=str(champion),
                    spell_key=slot,
                    residual=str(residual),
                    note=str(note) if note is not None else None,
                )
                key = (rule_key(champion), slot, residual_key)
                if key in result:
                    raise ValueError(f"重复可信技能 residual: key={key}")
                result[key] = entry

    return result


def rule_key(value: object) -> str:
    """生成规则查找用的保守 key。"""

    return "".join(char for char in str(value).lower() if char.isalnum())


def event_rule_key(value: object) -> str:
    """生成完整事件名规则查找 key。"""

    return str(value).casefold()
