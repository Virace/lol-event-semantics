"""事件名中文翻译主线。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .game_data import ChampionData, GameData, compact
from .model import (
    Action,
    Confidence,
    EventContext,
    EventMeaning,
    Owner,
    Phase,
    RenderStyle,
    ScopeKind,
    Variant,
)
from .rules import ActionAlias, RuleBook

VERB_NAMES = {
    "play": "播放",
    "stop": "停止",
    "set": "设置",
    "switch": "切换",
    "setstate": "设置",
    "mute": "静音",
    "unmute": "取消静音",
    "post": "后处理",
}

ASSET_NAMES = {
    "sfx": "音效",
    "vo": "语音",
    "state": "状态",
    "switch": "切换",
    "mus": "音乐",
}

DIRECT_ACTIONS = {
    "BasicAttack": ("basic_attack", "普攻"),
    "BasicAttack2": ("basic_attack_2", "普攻2"),
    "CritAttack": ("crit_attack", "暴击"),
    "Recall": ("recall", "回城"),
    "Recall3D": ("recall", "回城"),
    "Death3D": ("death", "阵亡"),
    "Move2DStandard": ("move", "移动"),
    "Joke3DGeneral": ("joke", "玩笑"),
    "Laugh3DGeneral": ("laugh", "大笑"),
    "Taunt3DGeneral": ("taunt", "嘲讽"),
    "Dance3D": ("dance", "跳舞"),
    "Attack2DGeneral": ("attack", "攻击"),
    "Spell3DPCast": ("spell_cast", "技能施放"),
    "Purchase2D": ("purchase", "购买"),
    "Victory2D": ("victory", "胜利"),
    "Upgrade2D": ("upgrade", "升级"),
    "Winddown3D": ("winddown", "收尾"),
}

KNOWN_SUMMONS = {
    "Tibbers": "提伯斯",
}


@dataclass(slots=True)
class Shell:
    """事件外层骨架。"""

    verb: str | None
    asset_kind: str | None
    body: str


class Translator:
    """事件翻译器。"""

    def __init__(self, data: GameData, rules: RuleBook) -> None:
        """保存 game-data 与规则集合。"""

        self.data = data
        self.rules = rules

    @classmethod
    def from_repo(
        cls,
        game_data_root: Path,
        locale: str = "zh_cn",
        rules_root: Path | None = None,
        overrides_root: Path | None = None,
    ) -> Translator:
        """使用仓库默认规则目录构造翻译器。"""

        repo_root = Path(__file__).resolve().parents[2]
        rules_dir = rules_root or repo_root / "rules"
        overrides_dir = overrides_root or repo_root / "overrides"

        return cls(
            data=GameData(root=game_data_root, locale=locale),
            rules=RuleBook.load(rules_dir, overrides_dir),
        )

    def translate(self, raw_name: str, ctx: EventContext | None = None) -> EventMeaning:
        """解析单个事件名。"""

        context = ctx or EventContext()
        shell = parse_shell(raw_name)

        if shell is None:
            return self._parse_nonstandard(raw_name, context)

        if self._is_map(shell.body, context):
            return self._parse_map(raw_name, shell, context)

        return self._parse_champion(raw_name, shell, context)

    def render(self, meaning: EventMeaning, style: RenderStyle = RenderStyle.DEFAULT) -> str:
        """把结构化语义渲染成文本。"""

        verb = VERB_NAMES.get((meaning.verb or "").lower(), meaning.verb or "解析")
        asset = ASSET_NAMES.get((meaning.asset_kind or "").lower(), meaning.asset_kind or "事件")
        subject = self._subject_text(meaning)
        action = self._action_text(meaning)
        phase = self._phase_text(meaning)
        modifiers = self._display_modifiers(meaning)

        if style is RenderStyle.BRIEF:
            parts = [part for part in [subject, action, phase] if part]
            return " ".join(parts) if parts else meaning.raw_name

        if style is RenderStyle.VERBOSE:
            parts = [part for part in [subject, action, *modifiers, phase] if part]
            text = " ".join(parts) if parts else meaning.raw_name

            if meaning.confidence is not Confidence.HIGH:
                text = f"{text} [{meaning.confidence.value}]"

            return f"{verb}{asset}：{text}"

        parts = [part for part in [subject, action, *filter(is_display_modifier, modifiers), phase] if part]
        text = " ".join(parts) if parts else meaning.raw_name

        return f"{verb}{asset}：{text}"

    def _phase_text(self, meaning: EventMeaning) -> str:
        """返回面向用户的阶段文本。"""

        if meaning.phase.name is None:
            return ""

        modifiers = list(meaning.modifiers)

        if meaning.phase.key == "missile_launch":
            if "飞行物" in modifiers:
                return "飞行物飞出"

            if meaning.action.key and (
                meaning.action.key.startswith("basic_attack")
                or meaning.action.key.startswith("crit_attack")
            ):
                return "弹道飞出"

            return "飞行物飞出"

        if meaning.phase.key == "missile_cast":
            if "飞行物" in modifiers:
                return "飞行物形成"

            if meaning.action.key and (
                meaning.action.key.startswith("basic_attack")
                or meaning.action.key.startswith("crit_attack")
            ):
                return "弹道形成"

            return "飞行物形成"

        if meaning.phase.key == "hit":
            if "飞行物" in modifiers and "小兵" in modifiers:
                return "飞行物命中小兵"

            if "飞行物" in modifiers:
                return "飞行物命中"

        if meaning.phase.key == "buff_cast" and "护盾" in modifiers:
            return "护盾触发"

        if meaning.phase.key == "buff_deactivate" and "护盾" in modifiers:
            return "护盾结束"

        return meaning.phase.name

    def _display_modifiers(self, meaning: EventMeaning) -> list[str]:
        """返回需要展示的修饰词。"""

        modifiers = list(meaning.modifiers)

        if meaning.phase.key in {"missile_launch", "missile_cast"}:
            modifiers = [item for item in modifiers if item != "飞行物"]

        if meaning.phase.key == "hit" and "飞行物" in modifiers:
            modifiers = [item for item in modifiers if item not in {"飞行物", "小兵"}]

        if meaning.phase.key in {"buff_cast", "buff_deactivate"} and "护盾" in modifiers:
            modifiers = [item for item in modifiers if item != "护盾"]

        return modifiers

    def _parse_nonstandard(self, raw_name: str, ctx: EventContext) -> EventMeaning:
        """解析非标准前缀事件。"""

        tokens = [part for part in raw_name.strip("_").split("_") if part]

        if not tokens:
            meaning = EventMeaning(raw_name=raw_name, family="system_misc")
            meaning.action = Action(token=raw_name, key="raw", name=f"未识别动作({raw_name})", category="raw")
            meaning.confidence = Confidence.LOW
            meaning.notes.append("empty token list")
            return meaning

        verb, asset_kind, body_tokens = peel_generic_prefix(tokens)

        if ctx.scope is ScopeKind.MAP or any(re.fullmatch(r"map\d+", token, re.IGNORECASE) for token in tokens):
            return self._parse_generic_map(raw_name, ctx, verb, asset_kind, body_tokens)

        return self._parse_generic(raw_name, ctx, verb, asset_kind, body_tokens)

    def _parse_generic(
        self,
        raw_name: str,
        ctx: EventContext,
        verb: str | None,
        asset_kind: str | None,
        body_tokens: list[str],
    ) -> EventMeaning:
        """解析通用非标准事件。"""

        meaning = EventMeaning(
            raw_name=raw_name,
            family="system_misc",
            verb=verb,
            asset_kind=asset_kind,
            scope=ctx.scope if ctx.scope is not ScopeKind.AUTO else ScopeKind.UNKNOWN,
        )

        owner_result = self._resolve_generic_owner(body_tokens, ctx)

        if owner_result is not None:
            champion, variant, action_tokens, notes = owner_result
            meaning.family = "champion_nonstandard"
            meaning.scope = ScopeKind.CHAMPION
            meaning.owner = Owner(
                kind="champion",
                id=champion.id,
                alias=champion.alias,
                name=champion.name,
                display_name=champion.title,
                source=f"champions/{champion.id}.json",
            )
            meaning.variant = variant
            meaning.notes.extend(notes)

            action, modifiers, action_notes, confidence = self._build_action(champion, variant, "_".join(action_tokens))
            meaning.action = action
            meaning.modifiers.extend(modifiers)
            meaning.notes.extend(action_notes)
            meaning.confidence = confidence

            if meaning.action.name and meaning.confidence is Confidence.LOW:
                meaning.confidence = Confidence.MEDIUM

            return meaning

        label = build_phrase("_".join(body_tokens), self.rules, self.data)
        meaning.action = Action(token=raw_name, key=compact(raw_name), name=label or f"未识别动作({raw_name})", category="system")
        meaning.confidence = Confidence.MEDIUM if label else Confidence.LOW
        meaning.notes.append("non-standard generic parse")

        return meaning

    def _parse_generic_map(
        self,
        raw_name: str,
        ctx: EventContext,
        verb: str | None,
        asset_kind: str | None,
        body_tokens: list[str],
    ) -> EventMeaning:
        """解析地图或系统环境类非标准事件。"""

        meaning = EventMeaning(
            raw_name=raw_name,
            family="system_misc",
            verb=verb,
            asset_kind=asset_kind,
            scope=ScopeKind.MAP if ctx.scope is ScopeKind.MAP else ScopeKind.UNKNOWN,
        )
        map_id = ctx.owner_id

        for token in body_tokens:
            match = re.fullmatch(r"map(\d+)", token, re.IGNORECASE)

            if match is not None:
                map_id = int(match.group(1))
                break

        if map_id is not None:
            map_data = self.data.map(map_id)

            if map_data is not None:
                meaning.owner = Owner(
                    kind="map",
                    id=map_id,
                    alias=f"Map{map_id}",
                    name=map_data.name,
                    display_name=map_data.name,
                    source="maps.json",
                )
            else:
                meaning.owner = Owner(
                    kind="map",
                    id=map_id,
                    alias=f"Map{map_id}",
                    name=f"地图{map_id}",
                    display_name=f"地图{map_id}",
                    source="map-fallback",
                )

        label = build_phrase("_".join(body_tokens), self.rules, self.data)
        meaning.action = Action(token=raw_name, key=compact(raw_name), name=label or f"未识别动作({raw_name})", category="system")
        meaning.confidence = Confidence.MEDIUM if label else Confidence.LOW
        meaning.notes.append("non-standard map/system parse")

        return meaning

        return meaning

    def _parse_champion(self, raw_name: str, shell: Shell, ctx: EventContext) -> EventMeaning:
        """解析英雄事件。"""

        meaning = EventMeaning(
            raw_name=raw_name,
            family="champion_standard",
            verb=shell.verb,
            asset_kind=shell.asset_kind,
            scope=ScopeKind.CHAMPION,
        )
        tokens = normalize_body(shell.body, self.rules, ctx)
        body_tokens, phase, modifiers = split_tail(tokens, self.rules)

        if phase is not None:
            meaning.phase = phase

        meaning.modifiers.extend(modifiers)

        if not body_tokens:
            meaning.action = Action(token=shell.body, key="raw", name=f"未识别动作({shell.body})", category="raw")
            meaning.confidence = Confidence.LOW
            meaning.notes.append("missing body tokens")
            return meaning

        owner_result = self._resolve_owner(body_tokens[0], ctx)

        if owner_result is None:
            meaning.action = Action(token=shell.body, key="raw", name=f"未识别动作({shell.body})", category="raw")
            meaning.confidence = Confidence.FAILED
            meaning.notes.append("owner not resolved")
            return meaning

        champion, variant, head_tail, owner_notes = owner_result
        meaning.owner = Owner(
            kind="champion",
            id=champion.id,
            alias=champion.alias,
            name=champion.name,
            display_name=champion.title,
            source=f"champions/{champion.id}.json",
        )
        meaning.variant = variant
        meaning.notes.extend(owner_notes)

        action_tokens = [part for part in [head_tail, *body_tokens[1:]] if part]
        action_token = "_".join(action_tokens)
        action, extra_modifiers, action_notes, confidence = self._build_action(champion, meaning.variant, action_token)
        meaning.action = action
        meaning.modifiers.extend(extra_modifiers)
        meaning.notes.extend(action_notes)
        meaning.confidence = confidence

        if meaning.phase.name and meaning.action.name and meaning.variant.skin_name:
            meaning.confidence = Confidence.HIGH
        elif meaning.phase.name and meaning.action.name and meaning.owner.name:
            meaning.confidence = max_confidence(meaning.confidence, Confidence.MEDIUM)

        return meaning

    def _parse_map(self, raw_name: str, shell: Shell, ctx: EventContext) -> EventMeaning:
        """解析地图事件。"""

        meaning = EventMeaning(
            raw_name=raw_name,
            family="map_environment",
            verb=shell.verb,
            asset_kind=shell.asset_kind,
            scope=ScopeKind.MAP,
        )
        tokens = normalize_body(shell.body, self.rules, ctx)
        body_tokens, phase, modifiers = split_tail(tokens, self.rules)

        if phase is not None:
            meaning.phase = phase

        meaning.modifiers.extend(modifiers)
        map_id = ctx.owner_id
        cursor = list(body_tokens)

        if cursor and cursor[0].lower() == "env":
            cursor.pop(0)

        for index, token in enumerate(cursor):
            if not re.fullmatch(r"Map(\d+)", token, re.IGNORECASE):
                continue

            map_id = int(re.search(r"(\d+)", token).group(1))
            cursor.pop(index)
            break

        if map_id is None:
            meaning.action = Action(token=shell.body, key="raw", name=f"未识别地图动作({shell.body})", category="raw")
            meaning.confidence = Confidence.FAILED
            meaning.notes.append("map id not resolved")
            return meaning

        map_data = self.data.map(map_id)

        if map_data is None:
            meaning.owner = Owner(
                kind="map",
                id=map_id,
                alias=f"Map{map_id}",
                name=f"地图{map_id}",
                display_name=f"地图{map_id}",
                source="map-fallback",
            )
            meaning.notes.append(f"map {map_id} not found; fallback owner created")
        else:
            meaning.owner = Owner(
                kind="map",
                id=map_data.id,
                alias=f"Map{map_data.id}",
                name=map_data.name,
                display_name=map_data.name,
                source="maps.json",
            )

        if cursor and cursor[0].isupper():
            meaning.notes.append(f"map family={cursor.pop(0)}")

        action_token = "_".join(cursor)
        action_name = self.rules.map_action_for(action_token)

        if action_name is None:
            action_name = build_map_label(cursor, self.rules, self.data)
            meaning.confidence = Confidence.MEDIUM if action_name else Confidence.LOW
            meaning.notes.append("map action built from token labels")
        else:
            meaning.confidence = Confidence.HIGH

        meaning.action = Action(
            token=action_token,
            key=action_token.lower(),
            name=action_name or f"未识别地图动作({action_token})",
            category="map",
        )

        if meaning.phase.name and meaning.confidence is Confidence.LOW and action_name:
            meaning.confidence = Confidence.MEDIUM

        return meaning

    def _resolve_owner(
        self,
        token: str,
        ctx: EventContext,
    ) -> tuple[ChampionData, Variant, str | None, list[str]] | None:
        """解析英雄主体、变体与头部残余。"""

        notes: list[str] = []
        champion = self.data.champion(ctx.owner_id) if ctx.owner_id is not None else self.data.find_champion(token)

        if champion is None:
            return None

        variant = Variant()
        tail = token
        alias = champion.alias

        if tail.lower().startswith(alias.lower()):
            tail = tail[len(alias) :]
            form_tail = take_form(tail)

            if form_tail is not None:
                variant.form_name, tail = form_tail

            skin_match = re.match(r"Skin(\d+)(.*)", tail, re.IGNORECASE)

            if skin_match is not None:
                variant.skin_key = f"Skin{skin_match.group(1).zfill(2)}"
                skin = champion.skin_by_key(variant.skin_key)

                if skin is not None:
                    variant.skin_id = skin.id
                    variant.skin_name = skin.name
                else:
                    notes.append(f"missing skin key={variant.skin_key}")

                tail = skin_match.group(2)

            summon_tail = take_summon(tail)

            if summon_tail is not None:
                summon_key, tail = summon_tail
                variant.summon_key = summon_key
                variant.summon_name = KNOWN_SUMMONS.get(summon_key, summon_key)

                summon_skin = re.match(r"Skin(\d+)(.*)", tail, re.IGNORECASE)

                if summon_skin is not None:
                    variant.skin_key = f"Skin{summon_skin.group(1).zfill(2)}"
                    skin = champion.skin_by_key(variant.skin_key)

                    if skin is not None:
                        variant.skin_id = skin.id
                        variant.skin_name = skin.name
                    else:
                        notes.append(f"missing summon skin key={variant.skin_key}")

                    tail = summon_skin.group(2)

        elif token.lower().endswith(alias.lower()) and compact(token) != compact(alias):
            variant.legacy_alias = token
            legacy = self.rules.legacy_for(token)

            if legacy is not None:
                if legacy.form_name:
                    variant.form_name = legacy.form_name

                if legacy.skin_name:
                    variant.skin_name = legacy.skin_name

                if legacy.skin_en:
                    skin = champion.skin_by_name_en(legacy.skin_en)

                    if skin is not None:
                        variant.skin_id = skin.id
                        variant.skin_key = skin.key
                        variant.skin_name = skin.name
                    else:
                        notes.append(f"missing override skin={legacy.skin_en}")

                if legacy.note:
                    notes.append(legacy.note)

            if variant.skin_name is None:
                guessed = guess_skin(champion, token)

                if guessed is not None:
                    variant.skin_id = guessed.id
                    variant.skin_key = guessed.key
                    variant.skin_name = guessed.name
                    notes.append("legacy alias guessed from english skin")

            tail = None

        else:
            tail = token if compact(token) != compact(alias) else None

        return champion, variant, tail or None, notes

    def _build_action(
        self,
        champion: ChampionData,
        variant: Variant,
        token: str,
    ) -> tuple[Action, list[str], list[str], Confidence]:
        """构造动作语义。"""

        notes: list[str] = []
        modifiers: list[str] = []
        raw = token or ""
        parts = [part for part in raw.split("_") if part]
        head = parts[0] if parts else raw
        tail_parts = parts[1:]
        core = head

        if core.lower() == "base" and tail_parts:
            core = tail_parts[0]
            tail_parts = tail_parts[1:]

        if not raw:
            return (
                Action(token="", key="implicit", name=None, category="state"),
                modifiers,
                notes,
                Confidence.HIGH,
            )

        prefixes = [
            prefix
            for prefix in [
                f"{champion.alias}{variant.summon_key}" if variant.summon_key else None,
                variant.summon_key,
                champion.alias,
            ]
            if prefix
        ]

        for prefix in prefixes:
            if core.lower().startswith(prefix.lower()):
                core = core[len(prefix) :]
                break

        if not core and head:
            core = head

        alias_rule = self._action_alias(champion, raw, core)

        if alias_rule is not None:
            return self._action_from_alias(champion, raw, alias_rule, modifiers, notes)

        numbered = parse_numbered_action(core)

        if numbered is not None:
            key, name = numbered
            modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
            return (
                Action(token=raw, key=key, name=name, category="action"),
                modifiers,
                notes,
                Confidence.HIGH,
                )

        spell_suffix = parse_spell_suffix(core)

        if spell_suffix is not None:
            spell_key, suffix = spell_suffix
            spell = champion.spells.get(spell_key.lower())

            if spell is not None:
                suffix_labels = spell_suffix_labels(suffix)

                if suffix_labels:
                    modifiers.extend(suffix_labels)
                modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
                return (
                    Action(token=raw, key=f"spell_{spell.key}", name=spell.name, hotkey=spell.key.upper(), category="spell"),
                    dedupe(modifiers),
                    notes,
                    Confidence.HIGH,
                )

        english_spell = find_english_spell(champion, core)

        if english_spell is not None:
            modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
            return (
                Action(
                    token=raw,
                    key=f"spell_{english_spell.key}",
                    name=english_spell.name,
                    hotkey=english_spell.key.upper(),
                    category="spell",
                ),
                dedupe(modifiers),
                notes,
                Confidence.HIGH,
            )

        if core in {"Q", "W", "E", "R"}:
            spell = champion.spells.get(core.lower())

            if spell is not None:
                modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
                return (
                    Action(token=raw, key=f"spell_{spell.key}", name=spell.name, hotkey=spell.key.upper(), category="spell"),
                    modifiers,
                    notes,
                    Confidence.HIGH,
                )

        if core.startswith("Passive"):
            if "Primed" in core:
                modifiers.append("就绪")

            modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
            return (
                Action(token=raw, key="passive", name=champion.passive, hotkey="P", category="passive"),
                modifiers,
                notes,
                Confidence.HIGH,
            )

        direct = DIRECT_ACTIONS.get(core)

        if direct is not None:
            key, name = direct
            modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
            return (
                Action(token=raw, key=key, name=name, category="action"),
                modifiers,
                notes,
                Confidence.HIGH,
            )

        special = special_label(core, self.rules, self.data)

        if special is not None:
            modifiers.extend(extra_labels(tail_parts, variant, self.rules, self.data))
            return (
                Action(token=raw, key=compact(raw), name=special, category="derived"),
                dedupe(modifiers),
                notes,
                Confidence.HIGH,
            )

        phrase = build_phrase(core, self.rules, self.data)

        if phrase:
            notes.append("action built from token labels")
            return (
                Action(token=raw, key=compact(raw), name=phrase, category="derived"),
                modifiers,
                notes,
                Confidence.MEDIUM,
            )

        notes.append("action unresolved")
        return (
            Action(token=raw, key="raw", name=f"未识别动作({raw})", category="raw"),
            modifiers,
            notes,
            Confidence.LOW,
        )

    def _subject_text(self, meaning: EventMeaning) -> str:
        """返回主体文本。"""

        parts: list[str] = []
        if meaning.variant.summon_name:
            if meaning.variant.skin_name:
                return f"{meaning.variant.skin_name}的{meaning.variant.summon_name}"

            if meaning.owner.display_name:
                return f"{meaning.owner.display_name}的{meaning.variant.summon_name}"

            return meaning.variant.summon_name

        base_name = meaning.variant.skin_name or meaning.owner.display_name or meaning.owner.name

        if base_name:
            parts.append(base_name)

        if meaning.variant.summon_name and meaning.variant.summon_name not in " ".join(parts):
            parts.append(meaning.variant.summon_name)

        if meaning.variant.form_name:
            parts.append(meaning.variant.form_name)

        if meaning.variant.chroma_name:
            parts.append(meaning.variant.chroma_name)

        if not parts and meaning.variant.legacy_alias:
            parts.append(meaning.variant.legacy_alias)

        return " ".join(parts)

    def _action_text(self, meaning: EventMeaning) -> str:
        """返回动作文本。"""

        if meaning.action.name is None:
            return ""

        if meaning.action.hotkey:
            return f"{meaning.action.name}({meaning.action.hotkey})"

        return meaning.action.name

    def _is_map(self, body: str, ctx: EventContext) -> bool:
        """判断事件是否应按地图线处理。"""

        if ctx.scope is ScopeKind.MAP:
            return True

        return body.lower().startswith("env_map") or bool(re.search(r"map\d+", body, re.IGNORECASE))

    def _action_alias(self, champion: ChampionData, raw: str, core: str) -> ActionAlias | None:
        """返回动作别名规则。"""

        for token in [raw, core]:
            hit = self.rules.action_for(champion.alias, token)

            if hit is not None:
                return hit

        return None

    def _action_from_alias(
        self,
        champion: ChampionData,
        raw: str,
        rule: ActionAlias,
        modifiers: list[str],
        notes: list[str],
    ) -> tuple[Action, list[str], list[str], Confidence]:
        """从动作别名规则构造动作。"""

        modifiers.extend(rule.modifiers)

        if rule.note:
            notes.append(rule.note)

        if rule.spell is not None:
            spell = champion.spells.get(rule.spell.lower())

            if spell is not None:
                return (
                    Action(
                        token=raw,
                        key=f"spell_{spell.key}",
                        name=spell.name,
                        hotkey=spell.key.upper(),
                        category=rule.category or "spell",
                    ),
                    modifiers,
                    notes,
                    Confidence.HIGH,
                )

        return (
            Action(
                token=raw,
                key=compact(raw),
                name=rule.name or f"未识别动作({raw})",
                hotkey=rule.hotkey,
                category=rule.category or "action",
            ),
            modifiers,
            notes,
            Confidence.HIGH if rule.name else Confidence.MEDIUM,
        )

    def _resolve_generic_owner(
        self,
        tokens: list[str],
        ctx: EventContext,
    ) -> tuple[ChampionData, Variant, list[str], list[str]] | None:
        """从非标准事件 token 中寻找主体。"""

        if ctx.owner_id is not None and ctx.scope is ScopeKind.CHAMPION:
            champion = self.data.champion(ctx.owner_id)
            variant = Variant()
            return champion, variant, tokens, []

        for index, token in enumerate(tokens):
            owner_result = self._resolve_owner(token, EventContext(scope=ScopeKind.CHAMPION, owner_id=ctx.owner_id))

            if owner_result is None:
                continue

            champion, variant, tail, notes = owner_result
            action_tokens = [part for part in [tail, *tokens[index + 1 :]] if part]

            if not action_tokens:
                action_tokens = [token]

            return champion, variant, action_tokens, notes

        return None


def parse_shell(raw_name: str) -> Shell | None:
    """解析事件外层骨架。"""

    match = re.match(r"^(Play|Stop|Set|Switch|SetState)_([^_]+)_(.+)$", raw_name)

    if match is None:
        loose = re.match(r"^(mus)_([^_]+)_(.+)$", raw_name, re.IGNORECASE)

        if loose is not None:
            return Shell(verb="play", asset_kind=loose.group(1).lower(), body=f"{loose.group(2)}_{loose.group(3)}")

        state = re.match(r"^(SetState)_(.+)$", raw_name)

        if state is not None:
            return Shell(verb="setstate", asset_kind="state", body=state.group(2))

        champ_switch = re.match(r"^([A-Za-z][A-Za-z0-9]+)_(Switch)_(.+)$", raw_name)

        if champ_switch is not None:
            return Shell(verb="switch", asset_kind="state", body=f"{champ_switch.group(1)}_{champ_switch.group(3)}")

        champ_level = re.match(r"^([A-Za-z][A-Za-z0-9]+)_(Level)_(.+)$", raw_name)

        if champ_level is not None:
            return Shell(verb="set", asset_kind="state", body=f"{champ_level.group(1)}_{champ_level.group(2)}_{champ_level.group(3)}")

        return None

    return Shell(
        verb=match.group(1).lower(),
        asset_kind=match.group(2).lower(),
        body=match.group(3),
    )


def peel_generic_prefix(tokens: list[str]) -> tuple[str | None, str | None, list[str]]:
    """从非标准 token 序列中剥离前缀。"""

    work = list(tokens)
    verb: str | None = None
    asset_kind: str | None = None

    if work and work[0].lower() in {"mute", "unmute", "post"}:
        verb = work.pop(0).lower()

    if work and work[0].lower() in {"mus", "sfx", "vo", "state"}:
        asset_kind = work.pop(0).lower()

    if verb is None and work and work[0].lower() in {"mus", "sfx", "vo"}:
        verb = "play"
        asset_kind = work.pop(0).lower()

    return verb, asset_kind, work


def strip_trailing_skin(token: str) -> str:
    """移除附着在普通 token 末尾的重复皮肤编号。"""

    if token.startswith("Skin"):
        return token

    match = re.match(r"(.+?)Skin\d+$", token, re.IGNORECASE)

    if match is None:
        return token

    return match.group(1)


def normalize_body(body: str, rules: RuleBook, ctx: EventContext) -> list[str]:
    """归一化 body token。"""

    tokens = [part for part in body.split("_") if part]

    if len(tokens) >= 2 and re.fullmatch(r"skin\d+", tokens[1], re.IGNORECASE):
        digits = re.search(r"(\d+)", tokens[1]).group(1).zfill(2)
        tokens = [title_token(tokens[0]) + f"Skin{digits}", *tokens[2:]]

    normalized: list[str] = []

    for token in tokens:
        token = strip_trailing_skin(token)
        fixed = rules.normalize_token(token)
        normalized.append(fixed)

    return normalized


def split_tail(tokens: list[str], rules: RuleBook) -> tuple[list[str], Phase | None, list[str]]:
    """从尾部拆出 phase 与 modifiers。"""

    work = list(tokens)
    phase: Phase | None = None
    modifiers: list[str] = []

    while work:
        token = work[-1]
        phase_rule = rules.phase_for(token)

        if (
            phase is None
            and token.lower() == "loop"
            and len(work) >= 2
            and rules.phase_for(work[-2]) is not None
        ):
            modifiers.insert(0, rules.modifier_for(token) or token)
            work.pop()
            continue

        if phase is None and phase_rule is not None:
            phase = Phase(token=token, key=phase_rule.key, name=phase_rule.name)
            work.pop()
            continue

        modifier = rules.modifier_for(token)

        if modifier is None:
            break

        modifiers.insert(0, modifier)
        work.pop()

    return work, phase, modifiers


def take_summon(token: str) -> tuple[str, str] | None:
    """从头部提取召唤物。"""

    for summon_key in KNOWN_SUMMONS:
        if token.startswith(summon_key):
            return summon_key, token[len(summon_key) :]

    return None


def take_form(token: str) -> tuple[str, str] | None:
    """从头部提取形态信息。"""

    mapping = {
        "Dragon": "龙形态",
        "Human": "人形态",
    }

    for key, label in mapping.items():
        if token.startswith(key):
            return label, token[len(key) :]

    return None


def parse_numbered_action(token: str) -> tuple[str, str] | None:
    """识别带编号的普攻或暴击动作。"""

    match = re.fullmatch(r"(BasicAttack|CritAttack)(\d+)", token)

    if match is None:
        return None

    kind, number = match.groups()

    if kind == "BasicAttack":
        return f"basic_attack_{number}", f"普攻{number}"

    return f"crit_attack_{number}", f"暴击{number}"


def can_match_loose(token: str) -> bool:
    """判断片段是否允许参与宽松匹配。"""

    key = compact(token)

    if len(key) >= 4:
        return True

    return len(token) == 1 and token in {"Q", "W", "E", "R", "P"}


def parse_spell_suffix(token: str) -> tuple[str, str] | None:
    """识别 `QMissile` 一类技能后缀。"""

    match = re.fullmatch(r"([QWERP])([A-Z].+)", token)

    if match is None:
        return None

    return match.group(1), match.group(2)


def spell_suffix_labels(token: str) -> list[str] | None:
    """把技能后缀翻成修饰词。"""

    mapping = {
        "Missile": "飞行物",
        "Descent": "降落",
        "Aoe": "范围",
        "Grab": "拾回",
        "Land": "落地",
        "Move": "位移",
        "Hover": "悬停",
        "Shield": "护盾",
        "Attack": "攻击",
        "AttackMelee": "近战攻击",
        "AttackUpgrade": "强化攻击",
        "Magic": "魔法",
        "Heal": "治疗",
        "Mis": "飞行物",
        "Self": "自身",
        "Enrage": "狂怒",
        "EnrageReady": "狂怒就绪",
        "EnrageSwing": "狂怒挥击",
        "EnrageConeMis": "狂怒锥形飞行物",
        "Blades": "剑刃",
        "BladesUp": "剑刃升起",
        "BladesDown": "剑刃落下",
        "LeadinTail": "回城尾声",
        "Chat": "对话",
    }

    if token in mapping:
        return [mapping[token]]

    if token in {"Cast", "Hit"}:
        return []

    return None


def find_english_spell(champion: ChampionData, token: str):
    """按英文技能名查找技能。"""

    needle = compact(token)

    for spell in champion.spells.values():
        if compact(spell.name_en) == needle:
            return spell

    return None


def dedupe(items: list[str]) -> list[str]:
    """保持顺序去重。"""

    result: list[str] = []

    for item in items:
        if item and item not in result:
            result.append(item)

    return result


def guess_skin(champion: ChampionData, legacy_alias: str):
    """从 legacy alias 猜测皮肤。"""

    spaced = re.sub(r"(?<!^)(?=[A-Z])", " ", legacy_alias)
    return champion.skin_by_name_en(spaced)


def build_phrase(token: str, rules: RuleBook, data: GameData | None) -> str:
    """把 CamelCase token 粗略翻成中文短语。"""

    if data is not None:
        exact = data.find_term(token)

        if exact is not None:
            return exact.label

    parts = split_words(token)
    labels: list[str] = []
    index = 0

    while index < len(parts):
        if index + 1 < len(parts) and parts[index] in {"2", "3"} and parts[index + 1].lower() == "d":
            index += 2
            continue

        if data is not None:
            skin_match = find_skin_match(data, parts, index)

            if skin_match is not None:
                label, width = skin_match

                if not labels or labels[-1] != label:
                    labels.append(label)

                index += width
                continue

        if data is not None:
            matched = find_term_match(data, parts, index)

            if matched is not None:
                label, width = matched

                if not labels or labels[-1] != label:
                    labels.append(label)

                index += width
                continue

        part = parts[index]
        loose = can_match_loose(part)

        label = rules.word_for(part)

        if label is None and data is not None and not loose:
            exact_part = data.find_term(part)

            if exact_part is not None:
                label = exact_part.label

        if label is None and data is not None and loose:
            champion = data.find_champion(part)

            if champion is not None:
                label = champion.title

        if label is None and data is not None and loose:
            term = data.find_term(part)

            if term is not None:
                label = term.label

        if label is None:
            if part.isdigit():
                label = part
            elif len(part) == 1 and part in {"Q", "W", "E", "R", "P"}:
                label = part
            else:
                label = title_token(part)

        if not label:
            index += 1
            continue

        if labels and labels[-1] == label:
            index += 1
            continue

        labels.append(label)
        index += 1

    labels = rewrite_labels(labels)

    return " ".join(labels).strip()


def build_map_label(tokens: list[str], rules: RuleBook, data: GameData | None = None) -> str:
    """从地图 token 生成中文标签。"""

    labels: list[str] = []

    for token in tokens:
        label = rules.map_token_for(token) or rules.word_for(token)

        if label is None:
            label = build_phrase(token, rules, data)

        if label:
            labels.append(label)

    return " ".join(labels).strip()


def rewrite_labels(labels: list[str]) -> list[str]:
    """按常见模式重写标签顺序或词组。"""

    if not labels:
        return labels

    rewritten = [label for label in labels if label not in {"通用", "基础", "Base"}]

    if rewritten == ["移动", "首次"]:
        return ["首次移动"]

    if rewritten == ["移动", "长距离"]:
        return ["长距离移动"]

    if rewritten == ["击杀", "五杀"]:
        return ["五杀"]

    if rewritten == ["击杀", "首次"]:
        return ["首次击杀"]

    if rewritten == ["嘲讽", "回应"]:
        return ["回应嘲讽"]

    if rewritten == ["玩笑", "回应"]:
        return ["回应玩笑"]

    if len(rewritten) >= 2 and rewritten[0] == "首次" and rewritten[1] == "遭遇":
        return ["首次遭遇", *rewritten[2:]]

    return rewritten


def find_term_match(data: GameData, parts: list[str], start: int) -> tuple[str, int] | None:
    """从 token 片段里找最长实体匹配。"""

    for end in range(len(parts), start, -1):
        token = "".join(parts[start:end])

        if len(compact(token)) <= 3:
            continue

        term = data.find_term(token)

        if term is not None:
            return term.label, end - start

    return None


def find_skin_match(data: GameData, parts: list[str], start: int) -> tuple[str, int] | None:
    """从 token 片段里找英雄皮肤匹配。"""

    for end in range(len(parts), start, -1):
        token = "".join(parts[start:end])
        term = data.find_term(token)

        if term is None or term.kind != "champion":
            continue

        if end + 1 >= len(parts) or parts[end] != "Skin" or not parts[end + 1].isdigit():
            continue

        champion = data.find_champion(token)

        if champion is None:
            continue

        skin_key = f"Skin{parts[end + 1].zfill(2)}"
        skin = champion.skin_by_key(skin_key)

        if skin is None:
            continue

        return skin.name, end + 2 - start

    return None


def extra_labels(
    tokens: list[str],
    variant: Variant,
    rules: RuleBook,
    data: GameData | None,
) -> list[str]:
    """把动作尾部附加 token 转成修饰词。"""

    labels: list[str] = []
    index = 0

    while index < len(tokens):
        token = tokens[index]
        if variant.summon_key and token.lower() == variant.summon_key.lower():
            index += 1
            continue

        if token.lower() in {"cast", "hit", "buffactivate", "buffdeactivate", "buffcast", "loop"}:
            index += 1
            continue

        matched = False

        for width in range(min(3, len(tokens) - index), 0, -1):
            joined = "".join(tokens[index : index + width])

            if width == 1:
                special = special_label(joined, rules, data)

                if special is not None:
                    if special:
                        labels.append(special)
                    index += width
                    matched = True
                    break

            suffix = spell_suffix_labels(joined)

            if suffix:
                labels.extend(suffix)
                index += width
                matched = True
                break

        if matched:
            continue

        label = rules.modifier_for(token) or rules.word_for(token)

        if label is None:
            label = build_phrase(token, rules, data)

        if label:
            labels.append(label)

        index += 1

    return labels


def special_label(token: str, rules: RuleBook, data: GameData | None) -> str | None:
    """处理复合语义 token。"""

    chat = re.fullmatch(r"Chat3D(.+)", token)

    if chat is not None:
        name = describe_target(chat.group(1), rules, data)
        return f"对{name}台词" if name else "对话台词"

    chat_slot = re.fullmatch(r"Chat\d+(?:_\d+)?", token)

    if chat_slot is not None:
        return ""

    meet = re.fullmatch(r"FirstEncounter3D(.+)", token)

    if meet is not None:
        name = describe_target(meet.group(1), rules, data)
        return f"首次遭遇{name}" if name else "首次遭遇"

    move = re.fullmatch(r"Move3D(.+)", token)

    if move is not None:
        name = describe_target(move.group(1), rules, data)
        return f"移动{name}" if name else "移动"

    taunt = re.fullmatch(r"Taunt3D(.+)", token)

    if taunt is not None:
        name = describe_target(taunt.group(1), rules, data)
        return f"嘲讽{name}" if name else "嘲讽"

    spell = re.fullmatch(r"Spell3D([QWERP])(.+)", token)

    if spell is not None:
        key = spell.group(1)
        tail = spell.group(2)
        op = parse_spell_event_tail(tail, rules, data)

        if op is not None:
            return f"{key}技能{op}"

        name = build_phrase(tail, rules, data)
        return f"{key}技能{name}" if name else f"{key}技能"

    spell_rank = re.fullmatch(r"Spell2D([QWERP])Rank(\d+)", token)

    if spell_rank is not None:
        key, rank = spell_rank.groups()

        if key == "P":
            return f"被动第{rank}阶"

        return f"{key}技能第{rank}阶"

    return None


def parse_spell_event_tail(token: str, rules: RuleBook, data: GameData | None) -> str | None:
    """把技能事件尾部翻成中文短语。"""

    patterns = [
        ("Hit", "命中"),
        ("Cast", "施放"),
        ("BuffActivate", "效果激活"),
        ("BuffDeactivate", "效果结束"),
        ("BuffCast", "效果触发"),
    ]

    for prefix, label in patterns:
        if not token.startswith(prefix):
            continue

        rest = token[len(prefix) :]
        target = build_phrase(rest, rules, data) if rest else ""

        return f"{label}{target}" if target else label

    return None


def describe_target(token: str, rules: RuleBook, data: GameData | None) -> str:
    """把目标 token 转成显示名，并去掉末尾编号。"""

    clean = re.sub(r"\d+$", "", token)
    return build_phrase(clean, rules, data)


def split_words(token: str) -> list[str]:
    """拆分 CamelCase 与数字片段。"""

    if not token:
        return []

    return re.findall(r"[A-Z]+(?=[A-Z][a-z]|\d|$)|[A-Z]?[a-z]+|\d+", token)


def title_token(token: str) -> str:
    """把 token 转成标题形式。"""

    return token[:1].upper() + token[1:]


def is_display_modifier(token: str) -> bool:
    """判断默认风格是否展示修饰词。"""

    return token not in {"循环"}


def max_confidence(left: Confidence, right: Confidence) -> Confidence:
    """返回更高的置信度。"""

    order = {
        Confidence.FAILED: 0,
        Confidence.LOW: 1,
        Confidence.MEDIUM: 2,
        Confidence.HIGH: 3,
    }

    return left if order[left] >= order[right] else right


def context_from_path(path: Path) -> EventContext:
    """从 raw 文件路径推断事件上下文。"""

    parts = [part.lower() for part in path.parts]

    if "champions" in parts:
        return EventContext(scope=ScopeKind.CHAMPION, owner_id=int(path.stem), source_path=path)

    if "maps" in parts:
        return EventContext(scope=ScopeKind.MAP, owner_id=int(path.stem), source_path=path)

    return EventContext(source_path=path)
