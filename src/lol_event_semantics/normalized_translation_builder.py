"""normalized translation frame 构建器。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from .decomposition_model import EventDecompositionRecord, SemanticProfile
from .display_labels import DisplayLabels, get_display_labels, get_reference_map_names
from .normalized_translation_model import (
    FRAME_VERSION,
    TRANSLATION_SCHEMA_VERSION,
    TranslationArgument,
    TranslationBuildContext,
    argument_to_dict,
)
from .rules import rule_key

# 英雄 label 来源标记：reference names 为权威来源，皮肤全名投票是缺名时的低可信回填。
LABEL_SOURCE_REFERENCE = "reference_names"
LABEL_SOURCE_SKIN_VOTE = "skin_label_vote"
CHAMPION_ZH_VOTE_FALLBACK_REASON = "champion_zh_label_from_skin_vote"


class ChampionLabelEntry(dict):
    """带 label 来源与名称多形式的英雄多语言 label 映射。

    dict 本体仍是 locale 到主 label 的映射，兼容
    ``TranslationBuildContext.champion_labels`` 的既有类型；附加属性只做
    additive 数据（信息无损列全），不改变主 label 语义。

    注意：经 ``dict()`` / ``{**entry}`` 复制只保留 label 映射，会丢弃
    ``label_sources`` / ``name_forms`` 附加属性；需保留时用 ``copy.copy``
    或直接传对象本身。

    Attributes:
        label_sources: locale 到 label 来源（reference_names / skin_label_vote）的映射。
        name_forms: 名称形式（title/nicknames）到 locale 到值列表的映射。
    """

    __slots__ = ("label_sources", "name_forms")

    def __init__(
        self,
        labels: Mapping[str, str] | None = None,
        *,
        label_sources: Mapping[str, str] | None = None,
        name_forms: Mapping[str, Mapping[str, list[str]]] | None = None,
    ) -> None:
        """初始化 label 映射与附加名称数据。

        Args:
            labels: locale 到主 label 的映射。
            label_sources: locale 到 label 来源的映射。
            name_forms: 名称形式到 locale 到值列表的映射。
        """

        super().__init__(labels or {})
        self.label_sources = dict(label_sources or {})
        self.name_forms = {
            form: {locale: list(values) for locale, values in locales.items()}
            for form, locales in (name_forms or {}).items()
        }

STATE_ACTIVATE_PHASES = {"on_buff_activate", "buff_activate", "buffactivate", "activate"}
STATE_DEACTIVATE_PHASES = {"on_buff_deactivate", "buff_deactivate", "buffdeactivate", "deactivate"}
VOICE_EVENT_FAMILIES = {
    "attack": "攻击",
    "taunt": "嘲讽",
    "joke": "玩笑",
    "laugh": "大笑",
    "dance": "跳舞",
    # 批次3（2026-07-18 SPEC 措辞表）：self_action/qualified_action 高频家族。
    # 这些家族仅 vo 记录建 frame（见 SELF_ACTION_VOICE_FAMILIES 分派收口），
    # legacy 短文案路径（normalized_output）对它们跳过本表，非 vo 兜底记录
    # 维持 family_label 措辞与 Tier3 口径，display 零漂移。
    "move": "移动",
    "death": "死亡",
    "buy_item": "购买装备",
    "recall": "回城",
    "respawn": "重生",
    "taunt_response": "嘲讽回应",
    "ping": "信号",
    "joke_response": "玩笑回应",
    "use_item": "使用装备",
    "shop": "商店",
    "level_up": "升级",
    "assist": "协助",
}
# 批次3 家族集合：与既有 5 个情绪动作家族分派顺序不同——须让 ability builder
# 先接走带技能槽位的同名记录（GravesMove=E 技能、KarthusDeathDefiedBuff=P 被动），
# 兜底为 None 后才由 voice_event 收口。
SELF_ACTION_VOICE_FAMILIES = frozenset(
    {
        "move",
        "death",
        "buy_item",
        "recall",
        "respawn",
        "taunt_response",
        "ping",
        "joke_response",
        "use_item",
        "shop",
        "level_up",
        "assist",
    }
)
# 批次3 qualifier 特例（SPEC 逐字措辞）：(family, qualifier 小写) → (zh, en) 核心词整体替换。
VOICE_QUALIFIER_PHRASES = {
    ("recall", "cancel"): ("取消回城", "cancel recall"),
    ("use_item", "ward"): ("使用守卫", "use ward"),
    ("use_item", "potion"): ("使用药水", "use potion"),
    ("use_item", "healthpotion"): ("使用药水", "use potion"),
    ("shop", "open"): ("打开商店", "open shop"),
    ("shop", "close"): ("关闭商店", "close shop"),
    ("level_up", "six"): ("升至6级", "level up to 6"),
    ("level_up", "eleven"): ("升至11级", "level up to 11"),
    ("level_up", "fifteen"): ("升至15级", "level up to 15"),
    ("level_up", "eighteen"): ("升至18级", "level up to 18"),
}
# move 距离特例：semantic_slots.distance=long → 远距离移动（SPEC 措辞表）。
VOICE_MOVE_LONG_PHRASE = ("远距离移动", "long distance move")
# 无信息增量的通用 qualifier（General/Standard 等），不产生 qualifier argument。
GENERIC_ACTION_QUALIFIERS = frozenset({"general", "generic", "standard"})
# 壳动词只有偏离默认播放语义的才需要出现在文案里（信息无损：stop/mute/unmute
# 都不可反转为播放）。本表是壳动词中文措辞的单一来源：playback argument 与
# normalized_output.playback_verb_suffix（legacy 短文案后缀）共用，两条路径措辞
# 逐字节一致。unmute 目前只出现在 legacy 路径（无 translation frame 覆盖的记录）。
SHELL_PLAYBACK_VERBS = {
    "stop": ("停止", "stopped"),
    "mute": ("静音", "muted"),
    "unmute": ("取消静音", "unmuted"),
}
# sfx/vo/mus 是音频类型标志：动作措辞必须跟随 asset kind，未识别时保守按语音处理。
# 本表是资产类型中文措辞的单一来源：asset_kind_words 与
# normalized_output.audio_kind_label（legacy 短文案）共用。
ASSET_KIND_WORDS = {
    "vo": ("语音", "voice"),
    "sfx": ("音效", "sfx"),
    "mus": ("音乐", "music"),
}
# T3.2 counterpart frames：interaction/kill_or_score 本批承接的 trigger 家族。
# nearby_event/interactive/recall/encounter 与 objective_destroy 本批返回 None
# 不建 frame（SPEC 裁决；objective_destroy 无中文措辞，不硬造，见任务报告）。
INTERACTION_CUE_FAMILIES = frozenset({"first_encounter", "attack", "move", "taunt", "assist"})
KILL_OR_DESTROY_FAMILIES = frozenset({"kill"})
# 家族核心词（主代理拍板措辞，「语音」等亚词按 asset kind 追加）。
INTERACTION_TRIGGER_WORDS = {
    "first_encounter": ("初遇", "first encounter"),
    "attack": ("攻击", "attack"),
    "taunt": ("嘲讽", "taunt"),
    "assist": ("协助", "assist"),
}
KILL_MILESTONE_WORDS = {
    "first": ("首次击杀", "first kill"),
    "first_blood": ("一血", "first blood"),
    "double": ("双杀", "double kill"),
    "triple": ("三杀", "triple kill"),
    "quadra": ("四杀", "quadra kill"),
    "penta": ("五杀", "penta kill"),
    "ace": ("团灭", "ace"),
    "assist": ("助攻", "kill assist"),
}
OBJECTIVE_STEAL_WORDS = {
    "dragon": ("巨龙", "dragon"),
    "baron": ("纳什男爵", "baron"),
    "epic_objective": ("史诗野怪", "epic objective"),
}
TIMING_WORDS = {"first": ("首次", "first"), "second": ("再次", "second")}
RELATION_WORDS = {"ally": ("队友", "ally"), "enemy": ("敌方", "enemy")}
KILL_METHOD_SPELL_KEYS = frozenset({"Q", "W", "E", "R"})
COUNTERPART_FALLBACK_REASON = "missing_counterpart_zh_label"
# ownerless 事件（Play_vo_Kill3DPenta 等）的 owner 段是事件体本身，champion
# label 会残留维度/壳 token；这类主体进不了用户文案，保守放弃建 frame。
TECHNICAL_CHAMPION_LABEL_MARKERS = ("3D", "2D", "_", "Play")


def build_translation(
    record: EventDecompositionRecord,
    context: TranslationBuildContext | None = None,
) -> dict[str, object] | None:
    """根据拆解记录构建 normalized translation frame。

    Args:
        record: 已完成语义归一与 reference enrichment 的拆解记录。
        context: translation 构建上下文。

    Returns:
        translation frame 字典；未覆盖的语义返回 None。
    """

    active_context = context or TranslationBuildContext()
    translation = build_translation_frame(record, active_context)
    if translation is None:
        return None

    attach_playback_argument(translation, record)
    return translation


def build_translation_frame(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """按 semantic profile 与 primary family 分发到具体 frame builder。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        translation frame 字典；未覆盖的语义返回 None。
    """

    # profile 分派必须在 family 分派之前：attack/taunt 家族同时出现在
    # interaction 与 self_action 两类 profile 中，按 family 先分派会被
    # VOICE_EVENT_FAMILIES 抢走 counterpart 语义（T3.2 SPEC 裁决）。
    if record.profile.semantic_profile is SemanticProfile.INTERACTION:
        return build_champion_interaction_cue(record, context)

    if record.profile.semantic_profile is SemanticProfile.KILL_OR_SCORE:
        return build_kill_or_destroy_event(record, context)

    if (
        record.profile.primary_family == "move"
        and record.profile.semantic_profile is SemanticProfile.QUALIFIED_ACTION
    ):
        # Move2DFirst:无交互对象的首次移动台词(qualified_action),
        # 与 interaction 的 move 共用同一措辞(「首次移动语音」)。
        return build_champion_interaction_cue(record, context)

    builders = {
        "skin_recall_state": build_skin_recall_state,
        "skin_idle_state": build_skin_idle_state,
        "skin_action_state": build_skin_action_state,
        "gameplay_phase_control": build_gameplay_phase_control,
    }
    builder = builders.get(record.profile.primary_family)
    if builder is not None:
        return builder(record, context)

    # 既有 5 个情绪动作家族维持原分派位置；批次3 家族不在此处接，
    # 让 ability builder 先接走带技能槽位的同名记录（见 SELF_ACTION_VOICE_FAMILIES）。
    if (
        record.profile.primary_family in VOICE_EVENT_FAMILIES
        and record.profile.primary_family not in SELF_ACTION_VOICE_FAMILIES
    ):
        return build_voice_event(record, context)

    ability_state = build_ability_state_change(record, context)
    if ability_state is not None:
        return ability_state

    ability_cast = build_ability_cast_event(record, context)
    if ability_cast is not None:
        return ability_cast

    return build_self_action_voice_event(record, context)


def attach_playback_argument(translation: dict[str, object], record: EventDecompositionRecord) -> None:
    """把 Stop/Mute 壳动词作为 playback argument 附加到 frame。

    Args:
        translation: 已构建的 translation frame。
        record: 拆解记录。
    """

    playback = playback_argument(record.shell.verb)
    if playback is None:
        return

    arguments = translation.get("arguments")
    if isinstance(arguments, dict):
        arguments["playback"] = playback


def playback_argument(verb: object) -> dict[str, object] | None:
    """构建播放控制 argument。

    Args:
        verb: 事件外壳动词。

    Returns:
        playback 参数；默认播放语义（play 等）返回 None。
    """

    if not isinstance(verb, str):
        return None

    labels = SHELL_PLAYBACK_VERBS.get(verb.lower())
    if labels is None:
        return None

    zh, en = labels
    return argument_to_dict(
        TranslationArgument(
            role="playback",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "shell.verb"},
            key=verb.lower(),
        )
    )


def build_ability_state_change(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建技能或组件状态变化 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        ability_state_change frame；不满足触发条件时返回 None。
    """

    slots = record.semantic_slots
    if not is_ability_state_change_candidate(record):
        return None

    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "ability_state_change")
    component = ability_component_argument(slots)
    slot = ability_slot_argument(slots.get("spell_key") or slots.get("spell_key_hint"))

    if phase is None or (component is None and slot is None):
        return None

    arguments: dict[str, dict[str, object]] = {}
    for item in [
        champion_argument(record, context),
        skin_argument(record, context),
        slot,
        component,
        phase,
    ]:
        if item is not None:
            arguments[str(item["role"])] = item

    return {
        "schema_version": TRANSLATION_SCHEMA_VERSION,
        "frame_type": "ability_state_change",
        "frame_version": FRAME_VERSION,
        "frame_confidence": record.confidence.value,
        "arguments": arguments,
        "provenance": {
            "builder": "normalized_translation_builder",
            "semantic_profile": record.profile.semantic_profile.value,
            "primary_family": record.profile.primary_family,
        },
    }


def is_ability_state_change_candidate(record: EventDecompositionRecord) -> bool:
    """判断记录是否应构建 ability_state_change frame。

    Args:
        record: 拆解记录。

    Returns:
        属于技能/状态启动结束语义时返回 True。
    """

    slots = record.semantic_slots
    phase = slots.get("phase") or phase_from_cue_path(slots)
    if state_phase_key(phase) is not None:
        return True

    if record.profile.primary_family == "champion_status_state":
        return True

    contextual_alias = slots.get("contextual_alias")
    return isinstance(contextual_alias, dict) and contextual_alias.get("component") is not None


def build_skin_recall_state(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建皮肤回城状态 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        skin_recall_state frame；缺关键字段时返回 None。
    """

    slots = record.semantic_slots
    phase = phase_argument(slots.get("phase"), "skin_recall_state")
    action = action_argument("recall", "skin_recall_state")
    if phase is None or action is None:
        return None

    return build_frame(
        record,
        "skin_recall_state",
        [
            champion_argument(record, context),
            skin_argument(record, context),
            action,
            phase,
        ],
    )


def build_skin_idle_state(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建皮肤待机状态 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        skin_idle_state frame；缺关键字段时返回 None。
    """

    slots = record.semantic_slots
    variant = variant_argument(slots, "skin_idle_state")
    component = None if variant is not None else ability_component_argument(slots)
    slot = None if variant is not None or component is not None else ability_slot_argument(slots.get("spell_key"))
    phase = phase_argument(slots.get("phase"), "skin_idle_state")

    if variant is None and component is None and slot is None:
        return None

    return build_frame(
        record,
        "skin_idle_state",
        [
            champion_argument(record, context),
            skin_argument(record, context),
            slot,
            component,
            variant,
            phase,
        ],
    )


def build_skin_action_state(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建皮肤动作状态 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        skin_action_state frame；缺关键字段时返回 None。
    """

    slots = record.semantic_slots
    action = action_argument(slots.get("action"), "skin_action_state")
    phase = phase_argument(slots.get("phase"), "skin_action_state")

    if action is None or phase is None:
        return None

    return build_frame(
        record,
        "skin_action_state",
        [
            champion_argument(record, context),
            skin_argument(record, context),
            action,
            phase,
        ],
    )


def build_gameplay_phase_control(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建玩法阶段控制 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        gameplay_phase_control frame；缺关键字段时返回 None。
    """

    slots = record.semantic_slots
    map_arg = map_argument(slots.get("map_id"))
    gameplay = gameplay_event_argument(slots.get("gameplay_event"))
    phase = phase_argument(slots.get("phase_action"), "gameplay_phase_control")

    if map_arg is None or gameplay is None or phase is None:
        return None

    return build_frame(record, "gameplay_phase_control", [map_arg, gameplay, phase])


def build_voice_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建英雄语音/情绪动作 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        voice_event frame；无法识别动作家族时返回 None。
    """

    action = voice_action_argument(record.profile.primary_family, record.shell.asset_kind, record.semantic_slots)
    if action is None:
        return None

    arguments = [
        champion_argument(record, context),
        skin_argument(record, context),
        action,
    ]
    if record.profile.primary_family in SELF_ACTION_VOICE_FAMILIES:
        # 批次3：未被核心词消化的 qualifier 无损进 arguments（qualifier role
        # 不在 voice_event 渲染模板中，任何模式都不进文案；既有 5 家族的
        # frame 内容保持不变，不做范围外扩充）。
        arguments.append(voice_qualifier_argument(record))

    return build_frame(record, "voice_event", arguments)


def build_self_action_voice_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """批次3 self_action 家族的 voice_event 分派收口。

    仅承接 vo 资产的 self_action/qualified_action 记录：非 vo 记录维持
    Tier3/兜底展示口径（display_tier 的 TIER3_IF_NOT_VO_FAMILIES 判定），
    带技能槽位的同名记录已被 ability builder 先接走。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        voice_event frame；不属于批次3 承接范围时返回 None。
    """

    if record.profile.primary_family not in SELF_ACTION_VOICE_FAMILIES:
        return None

    if record.shell.asset_kind != "vo":
        return None

    if record.profile.semantic_profile not in {SemanticProfile.SELF_ACTION, SemanticProfile.QUALIFIED_ACTION}:
        return None

    return build_voice_event(record, context)


def voice_qualifier_argument(record: EventDecompositionRecord) -> dict[str, object] | None:
    """构建批次3 家族的无损 qualifier argument（数据角色，不进渲染模板）。

    取值优先级：move 的 movement_context/spell_state 结构化槽位 → 通用
    qualifier 字符串。通用 qualifier（General/Standard 等）、已被核心词
    特例消化的 qualifier（Cancel/Ward/Open/级别词等）与含技术 token 的
    值（如 Group2DWard）不产生 argument；后者信息仍无损保留在
    semantic.slots.qualifier，不进用户可见 label（validation 对 zh label
    的技术 token 判 error）。

    Args:
        record: 拆解记录。

    Returns:
        qualifier 参数；无可保留修饰时返回 None。
    """

    slots = record.semantic_slots
    family = record.profile.primary_family

    if family == "move":
        movement_context = slots.get("movement_context")
        if isinstance(movement_context, dict):
            label = string_or_none(movement_context.get("label")) or string_or_none(movement_context.get("raw"))
            if label is not None and not has_technical_qualifier_token(label):
                return argument_to_dict(
                    TranslationArgument(
                        role="qualifier",
                        labels={"zh_CN": label, "en_US": label},
                        confidence="high",
                        provenance={
                            "source": "semantic_slots.movement_context",
                            "context_kind": string_or_none(movement_context.get("kind")),
                        },
                        key=string_or_none(movement_context.get("key")),
                        token=string_or_none(movement_context.get("raw")),
                    )
                )

        spell_state = slots.get("spell_state")
        if isinstance(spell_state, dict):
            raw = string_or_none(spell_state.get("raw"))
            if raw is not None and not has_technical_qualifier_token(raw):
                return argument_to_dict(
                    TranslationArgument(
                        role="qualifier",
                        labels={"zh_CN": raw, "en_US": raw},
                        confidence="medium",
                        provenance={"source": "semantic_slots.spell_state"},
                        key=raw.lower(),
                        value=string_or_none(spell_state.get("spell_key")),
                        token=raw,
                    )
                )

    qualifier = string_or_none(slots.get("qualifier"))
    if qualifier is None:
        return None

    lowered = qualifier.lower()
    if lowered in GENERIC_ACTION_QUALIFIERS:
        return None

    if (family, lowered) in VOICE_QUALIFIER_PHRASES or (family == "move" and voice_move_distance(slots) is not None):
        # 已被 action 核心词整体表达，不重复输出。
        return None

    if has_technical_qualifier_token(qualifier):
        return None

    return argument_to_dict(
        TranslationArgument(
            role="qualifier",
            labels={"zh_CN": qualifier, "en_US": qualifier},
            confidence="high",
            provenance={"source": "semantic_slots.qualifier"},
            key=lowered,
            token=qualifier,
        )
    )


def voice_move_distance(slots: Mapping[str, Any]) -> str | None:
    """读取 move 家族的距离修饰。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        "long"；无距离修饰时返回 None。
    """

    distance = slots.get("distance")
    if isinstance(distance, str) and distance.lower() == "long":
        return "long"

    return None


def has_technical_qualifier_token(value: str) -> bool:
    """判断 qualifier 值是否残留技术 token（与 validation 展示口径对齐）。

    Args:
        value: qualifier 原始值。

    Returns:
        含维度/下划线/壳 token（大小写不敏感）时返回 True。
    """

    upper = value.upper()
    return "_" in value or any(marker.upper() in upper for marker in TECHNICAL_CHAMPION_LABEL_MARKERS)


def build_champion_interaction_cue(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建英雄交互语音 frame（interaction profile）。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        champion_interaction_cue frame；本批未承接的家族或缺主体时返回 None。
    """

    family = record.profile.primary_family
    if family not in INTERACTION_CUE_FAMILIES:
        return None

    champion = champion_argument(record, context)
    trigger = interaction_trigger_argument(record)
    if champion is None or trigger is None or has_technical_champion_label(champion):
        return None

    slots = record.semantic_slots
    return build_frame(
        record,
        "champion_interaction_cue",
        [
            champion,
            skin_argument(record, context),
            trigger,
            counterpart_argument(record, context),
            timing_argument(slots.get("timing")),
            relation_argument(slots.get("relation")),
        ],
    )


def build_kill_or_destroy_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建击杀/摧毁事件 frame（kill_or_score profile）。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        kill_or_destroy_event frame；本批未承接的家族或缺主体时返回 None。
    """

    family = record.profile.primary_family
    if family not in KILL_OR_DESTROY_FAMILIES:
        return None

    champion = champion_argument(record, context)
    trigger = kill_trigger_argument(record)
    if champion is None or trigger is None or has_technical_champion_label(champion):
        return None

    slots = record.semantic_slots
    return build_frame(
        record,
        "kill_or_destroy_event",
        [
            champion,
            skin_argument(record, context),
            trigger,
            counterpart_argument(record, context),
            kill_milestone_argument(slots.get("milestone"), record.shell.asset_kind),
            kill_method_argument(slots.get("kill_method")),
            objective_steal_argument(slots.get("objective_steal")),
            qualifier_argument(slots.get("qualifier")),
            counter_argument(slots.get("counter")),
        ],
    )


def has_technical_champion_label(champion: dict[str, object]) -> bool:
    """判断 champion argument 的 zh label 是否残留技术 token。

    Args:
        champion: champion argument 字典。

    Returns:
        label 含维度/壳 token（3D/2D/下划线等）时返回 True。
    """

    labels = champion.get("labels")
    label = labels.get("zh_CN") if isinstance(labels, dict) else None
    if not isinstance(label, str):
        return True

    return any(marker in label for marker in TECHNICAL_CHAMPION_LABEL_MARKERS)


def interaction_trigger_argument(record: EventDecompositionRecord) -> dict[str, object] | None:
    """构建 interaction 家族 trigger argument（核心短语，不含 counterpart）。

    Args:
        record: 拆解记录。

    Returns:
        trigger 参数；不属于本批承接家族时返回 None。
    """

    family = record.profile.primary_family
    zh_word, en_word = asset_kind_words(record.shell.asset_kind)

    if family == "move":
        timing = string_or_none(record.semantic_slots.get("timing"))
        timing_zh, timing_en = TIMING_WORDS.get(timing or "", ("", ""))
        zh = f"{timing_zh}移动{zh_word}"
        en = f"{timing_en} move {en_word}".strip()
    else:
        words = INTERACTION_TRIGGER_WORDS.get(family)
        if words is None:
            return None

        zh = f"{words[0]}{zh_word}"
        en = f"{words[1]} {en_word}"

    return argument_to_dict(
        TranslationArgument(
            role="trigger",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.trigger_family", "frame_type": "champion_interaction_cue"},
            key=family,
        )
    )


def kill_trigger_argument(record: EventDecompositionRecord) -> dict[str, object] | None:
    """构建 kill 家族 trigger argument（核心短语，不含 counterpart）。

    milestone 存在时替换击杀核心词；objective_steal 渲染「抢夺 {对象} 」。
    kill_method（含 QWER 单字母键）不进本短语，由 renderer 在 default/debug/
    audit 模式前置（「R技能击杀语音」/「REmpowered 击杀语音」），main 短文案
    保持零拉丁字母（residual_english 棘轮口径）。

    Args:
        record: 拆解记录。

    Returns:
        trigger 参数。
    """

    slots = record.semantic_slots
    zh_word, en_word = asset_kind_words(record.shell.asset_kind)

    milestone = string_or_none(slots.get("milestone"))
    milestone_words = KILL_MILESTONE_WORDS.get(milestone or "")
    steal_words = objective_steal_words(slots.get("objective_steal"))
    if milestone_words is not None:
        zh, en = f"{milestone_words[0]}{zh_word}", f"{milestone_words[1]} {en_word}"
    elif steal_words is not None:
        zh, en = f"抢夺 {steal_words[0]} {zh_word}", f"steal {steal_words[1]} {en_word}"
    else:
        zh, en = f"击杀{zh_word}", f"kill {en_word}"

    return argument_to_dict(
        TranslationArgument(
            role="trigger",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.trigger_family", "frame_type": "kill_or_destroy_event"},
            key=record.profile.primary_family,
        )
    )


def kill_method_spell_letter(kill_method: object) -> str | None:
    """从 kill_method 槽位归一出单字母技能键。

    Args:
        kill_method: kill_method 槽位值。

    Returns:
        QWER 单字母；raw 非单字母（REmpowered/Ultimate/QW 等）时返回 None。
    """

    if not isinstance(kill_method, dict):
        return None

    raw = string_or_none(kill_method.get("raw"))
    if raw is not None and raw.upper() in KILL_METHOD_SPELL_KEYS:
        return raw.upper()

    return None


def objective_steal_words(steal: object) -> tuple[str, str] | None:
    """返回 objective_steal 对象的中英文措辞。

    Args:
        steal: objective_steal 槽位值。

    Returns:
        `(zh, en)` 措辞；objective 不在措辞表时返回 None（不硬造）。
    """

    if not isinstance(steal, dict):
        return None

    return OBJECTIVE_STEAL_WORDS.get(string_or_none(steal.get("objective")) or "")


def counterpart_argument(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建 counterpart argument，按 SPEC 解析链取中文名。

    解析顺序：kind=champion 走 ``context.champion_labels``（俗名体系）；
    kind=skin 用皮肤 zh 全名（含英雄名，无歧义）；其他 kind 查外置词表
    （label 精确匹配优先，rule_key 归一 key 兜底）；未命中直接用英文
    label/raw，不硬造中文。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        counterpart 参数；无 counterpart 或 kind=general（无特定对象）时返回 None。
    """

    counterpart = record.semantic_slots.get("counterpart")
    if not isinstance(counterpart, dict) or not counterpart:
        return None

    kind = string_or_none(counterpart.get("kind"))
    if kind == "general":
        return None

    alias = string_or_none(counterpart.get("alias"))
    key = string_or_none(counterpart.get("key"))
    raw = string_or_none(counterpart.get("raw"))
    english = (
        string_or_none(counterpart.get("label"))
        or raw
        or alias
        or key
    )
    if english is None:
        return None

    zh = None
    zh_source = "fallback"
    confidence = "low"
    fallback = False
    fallback_reason = None

    if kind == "champion":
        entry = context.champion_labels.get(alias or "")
        zh = string_or_none(entry.get("zh_CN")) if isinstance(entry, Mapping) else None
        if zh is not None:
            zh_source, confidence = "champion_labels", "high"
            if isinstance(entry, ChampionLabelEntry) and entry.label_sources.get("zh_CN") == LABEL_SOURCE_SKIN_VOTE:
                fallback = True
                fallback_reason = CHAMPION_ZH_VOTE_FALLBACK_REASON
    elif kind == "skin":
        zh = counterpart_skin_zh_label(counterpart, context)
        if zh is not None:
            zh_source, confidence = "skin_labels", "high"
    else:
        zh = counterpart_vocab_zh(key, english, labels=labels)
        if zh is not None:
            zh_source, confidence = "display_labels.counterpart", "medium"

    if zh is None:
        fallback = True
        fallback_reason = COUNTERPART_FALLBACK_REASON

    return argument_to_dict(
        TranslationArgument(
            role="counterpart",
            labels={"zh_CN": zh or english, "en_US": english},
            confidence=confidence,
            provenance={
                "source": "semantic_slots.counterpart",
                "counterpart_kind": kind,
                "counterpart_source": string_or_none(counterpart.get("source")),
                "zh_source": zh_source,
            },
            alias=alias,
            key=key,
            token=raw,
            fallback=fallback,
            fallback_reason=fallback_reason,
        )
    )


def counterpart_skin_zh_label(counterpart: dict[str, object], context: TranslationBuildContext) -> str | None:
    """按 metadata 的英雄 alias 与 skin token 查皮肤 zh 全名。

    Args:
        counterpart: counterpart 槽位字典。
        context: translation 构建上下文。

    Returns:
        皮肤 zh 全名；reference 缺该皮肤时返回 None。
    """

    metadata = counterpart.get("metadata")
    if not isinstance(metadata, dict):
        return None

    champion_alias = string_or_none(metadata.get("champion_alias"))
    skin_token = string_or_none(metadata.get("skin_token"))
    if champion_alias is None or skin_token is None:
        return None

    labels = context.skin_labels.get((champion_alias, normalize_skin_token(skin_token)))
    if not isinstance(labels, Mapping):
        return None

    return string_or_none(labels.get("zh_CN"))


def counterpart_vocab_zh(
    key: str | None,
    label: str | None,
    *,
    labels: DisplayLabels | None = None,
) -> str | None:
    """查 counterpart 外置词表（第一批官方译名）。

    Args:
        key: 规则引擎归一 key。
        label: 英文 label。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        中文词条；未命中返回 None（fallback 直出英文，不硬造）。
    """

    vocab = (labels if labels is not None else get_display_labels()).counterpart
    if label is not None and label in vocab:
        return vocab[label]

    if key is not None:
        normalized = rule_key(key)
        for vocab_label, zh in vocab.items():
            if rule_key(vocab_label) == normalized:
                return zh

    return None


def kill_milestone_argument(milestone: object, asset_kind: object) -> dict[str, object] | None:
    """构建击杀里程碑 argument（数据角色，文案由 trigger 短语承载）。

    Args:
        milestone: milestone 槽位值。
        asset_kind: 事件外壳音频类型。

    Returns:
        milestone 参数；无里程碑或措辞表未收录时返回 None。
    """

    key = string_or_none(milestone)
    words = KILL_MILESTONE_WORDS.get(key or "")
    if key is None or words is None:
        return None

    zh_word, en_word = asset_kind_words(asset_kind)
    return argument_to_dict(
        TranslationArgument(
            role="milestone",
            labels={"zh_CN": f"{words[0]}{zh_word}", "en_US": f"{words[1]} {en_word}"},
            confidence="high",
            provenance={"source": "semantic_slots.milestone"},
            key=key,
        )
    )


def kill_method_argument(kill_method: object) -> dict[str, object] | None:
    """构建击杀方式 argument。

    Args:
        kill_method: kill_method 槽位值。

    Returns:
        kill_method 参数；无击杀方式时返回 None。
    """

    if not isinstance(kill_method, dict):
        return None

    raw = string_or_none(kill_method.get("raw"))
    if raw is None:
        return None

    letter = kill_method_spell_letter(kill_method)
    zh = f"{letter}技能" if letter is not None else raw
    return argument_to_dict(
        TranslationArgument(
            role="kill_method",
            labels={"zh_CN": zh, "en_US": raw},
            confidence="medium",
            provenance={"source": "semantic_slots.kill_method"},
            key=string_or_none(kill_method.get("kind")),
            value=string_or_none(kill_method.get("spell_key")),
            token=raw,
        )
    )


def objective_steal_argument(steal: object) -> dict[str, object] | None:
    """构建抢夺目标 argument。

    Args:
        steal: objective_steal 槽位值。

    Returns:
        objective_steal 参数；无抢夺目标时返回 None。
    """

    if not isinstance(steal, dict):
        return None

    objective = string_or_none(steal.get("objective"))
    raw = string_or_none(steal.get("raw"))
    if objective is None and raw is None:
        return None

    words = objective_steal_words(steal)
    zh = words[0] if words is not None else (objective or raw or "")
    en = words[1] if words is not None else (objective or raw or "")
    return argument_to_dict(
        TranslationArgument(
            role="objective_steal",
            labels={"zh_CN": zh, "en_US": en},
            confidence="medium",
            provenance={"source": "semantic_slots.objective_steal"},
            key=objective,
            token=raw,
        )
    )


def qualifier_argument(qualifier: object) -> dict[str, object] | None:
    """构建 qualifier argument（general 等修饰，数据角色）。

    Args:
        qualifier: qualifier 槽位值。

    Returns:
        qualifier 参数；无修饰时返回 None。
    """

    value = string_or_none(qualifier)
    if value is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="qualifier",
            labels={"zh_CN": value, "en_US": value},
            confidence="high",
            provenance={"source": "semantic_slots.qualifier"},
            key=value,
        )
    )


def counter_argument(counter: object) -> dict[str, object] | None:
    """构建计数器 argument；raw 提得出数字时 value 存数字供渲染尾缀。

    Args:
        counter: counter 槽位值。

    Returns:
        counter 参数；无计数器时返回 None。
    """

    if not isinstance(counter, dict):
        return None

    raw = string_or_none(counter.get("raw"))
    kind = string_or_none(counter.get("kind"))
    if raw is None and kind is None:
        return None

    digits = re.search(r"[0-9]+", raw or "")
    zh = string_or_none(counter.get("label")) or raw or kind or ""
    return argument_to_dict(
        TranslationArgument(
            role="counter",
            labels={"zh_CN": zh, "en_US": raw or kind or ""},
            confidence="medium",
            provenance={"source": "semantic_slots.counter"},
            key=kind,
            value=digits.group(0) if digits is not None else None,
            token=raw,
        )
    )


def timing_argument(timing: object) -> dict[str, object] | None:
    """构建交互时机 argument（first→首次 / second→再次，数据角色）。

    Args:
        timing: timing 槽位值。

    Returns:
        timing 参数；无时机时返回 None。
    """

    value = string_or_none(timing)
    if value is None:
        return None

    zh, en = TIMING_WORDS.get(value, (value, value))
    return argument_to_dict(
        TranslationArgument(
            role="timing",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.timing"},
            key=value,
        )
    )


def relation_argument(relation: object) -> dict[str, object] | None:
    """构建交互关系 argument（ally→队友 / enemy→敌方）。

    Args:
        relation: relation 槽位值。

    Returns:
        relation 参数；无关系时返回 None。
    """

    value = string_or_none(relation)
    if value is None:
        return None

    zh, en = RELATION_WORDS.get(value, (value, value))
    return argument_to_dict(
        TranslationArgument(
            role="relation",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.relation"},
            key=value,
        )
    )


def voice_action_argument(
    primary_family: str,
    asset_kind: object = None,
    slots: Mapping[str, Any] | None = None,
) -> dict[str, object] | None:
    """构建语音/音效动作 argument。

    批次3 qualifier 特例：move 的 distance=long 与 recall/use_item/shop/
    level_up 的特定 qualifier 按 SPEC 措辞表整体替换核心词（远距离移动/
    取消回城/使用守卫/打开商店/升至6级等），并在 provenance 记录消化的
    qualifier 值。

    Args:
        primary_family: 拆解 profile 的主家族。
        asset_kind: 事件外壳音频类型（vo/sfx/mus）。
        slots: 拆解记录 semantic slots；仅批次3 qualifier 特例需要。

    Returns:
        action 参数；不属于语音动作家族时返回 None。
    """

    zh_core = VOICE_EVENT_FAMILIES.get(primary_family)
    if zh_core is None:
        return None

    en_core = primary_family.replace("_", " ")
    active_slots = slots or {}
    consumed_qualifier = None

    if primary_family == "move" and voice_move_distance(active_slots) is not None:
        zh_core, en_core = VOICE_MOVE_LONG_PHRASE
        consumed_qualifier = "long"
    else:
        qualifier = string_or_none(active_slots.get("qualifier"))
        phrase = VOICE_QUALIFIER_PHRASES.get((primary_family, qualifier.lower())) if qualifier is not None else None
        if phrase is not None:
            zh_core, en_core = phrase
            consumed_qualifier = qualifier

    provenance: dict[str, object] = {"source": "profile.primary_family", "frame_type": "voice_event"}
    if consumed_qualifier is not None:
        provenance["qualifier"] = consumed_qualifier

    zh_word, en_word = asset_kind_words(asset_kind)
    return argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": f"{zh_core}{zh_word}", "en_US": f"{en_core} {en_word}"},
            confidence="high",
            provenance=provenance,
            key=primary_family,
        )
    )


def asset_kind_words(asset_kind: object) -> tuple[str, str]:
    """返回音频类型对应的中英文措辞。

    Args:
        asset_kind: 事件外壳音频类型。

    Returns:
        `(zh, en)` 措辞；未识别时按语音处理。
    """

    if isinstance(asset_kind, str):
        words = ASSET_KIND_WORDS.get(asset_kind.lower())
        if words is not None:
            return words

    return ASSET_KIND_WORDS["vo"]


def build_ability_cast_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建普通技能施放 frame。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        ability_cast_event frame；缺技能槽位或阶段、或属于借用大招时返回 None。
    """

    if record.owner.alias == "UltBook":
        return None

    slots = record.semantic_slots
    slot = ability_slot_argument(slots.get("spell_key") or slots.get("spell_key_hint"))
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "ability_cast_event")

    if slot is None or phase is None:
        return None

    return build_frame(
        record,
        "ability_cast_event",
        [
            champion_argument(record, context),
            skin_argument(record, context),
            slot,
            phase,
        ],
    )


def build_frame(
    record: EventDecompositionRecord,
    frame_type: str,
    arguments: list[dict[str, object] | None],
) -> dict[str, object]:
    """组装通用 translation frame。

    Args:
        record: 拆解记录。
        frame_type: translation frame 类型。
        arguments: frame 参数列表。

    Returns:
        JSON 友好的 translation frame 字典。
    """

    argument_map = {
        str(argument["role"]): argument
        for argument in arguments
        if argument is not None
    }
    return {
        "schema_version": TRANSLATION_SCHEMA_VERSION,
        "frame_type": frame_type,
        "frame_version": FRAME_VERSION,
        "frame_confidence": record.confidence.value,
        "arguments": argument_map,
        "provenance": {
            "builder": "normalized_translation_builder",
            "semantic_profile": record.profile.semantic_profile.value,
            "primary_family": record.profile.primary_family,
        },
    }


def champion_argument(
    record: EventDecompositionRecord,
    context: TranslationBuildContext | None = None,
) -> dict[str, object] | None:
    """构建 champion argument。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        champion 参数；无法定位英雄时返回 None。
    """

    subject = record.semantic_slots.get("subject")
    alias = None
    label = None
    source = "record"

    if isinstance(subject, dict):
        alias = string_or_none(subject.get("alias")) or string_or_none(subject.get("key"))
        label = string_or_none(subject.get("label")) or alias
        source = string_or_none(subject.get("source")) or source

    if alias is None:
        alias = string_or_none(record.semantic_slots.get("subject_hint")) or record.owner.alias
        label = alias

    if alias is None:
        return None

    # owner 解析漏拆皮肤号时（如 GalioSKin04 大小写 typo），champion 文案不得携带 SkinNN。
    cleaned_alias = strip_embedded_skin_token(alias)
    if cleaned_alias != alias:
        if label == alias:
            label = cleaned_alias
        alias = cleaned_alias

    entry = (context or TranslationBuildContext()).champion_labels.get(alias)
    labels = locale_labels(entry, label or alias)
    provenance: dict[str, object] = {"source": source}
    fallback = False
    fallback_reason = None

    if isinstance(entry, ChampionLabelEntry) and entry.label_sources:
        provenance["label_sources"] = dict(entry.label_sources)
        # reference 缺 zh 名时的皮肤投票回填是低可信 label，必须留 fallback 痕迹。
        if entry.label_sources.get("zh_CN") == LABEL_SOURCE_SKIN_VOTE:
            fallback = True
            fallback_reason = CHAMPION_ZH_VOTE_FALLBACK_REASON

    argument = argument_to_dict(
        TranslationArgument(
            role="champion",
            labels=labels,
            confidence="high",
            provenance=provenance,
            alias=alias,
            fallback=fallback,
            fallback_reason=fallback_reason,
            omit_when_context_known=("champion",),
        )
    )
    name_forms = champion_name_forms(entry)
    if name_forms:
        argument["name_forms"] = name_forms
    return argument


def champion_name_forms(entry: object) -> dict[str, dict[str, list[str]]]:
    """读取 entry 携带的名称多形式（称号/俗称）。

    Args:
        entry: champion_labels 中的 label entry。

    Returns:
        名称形式到 locale 到值列表的映射；无数据时返回空 dict。
    """

    if not isinstance(entry, ChampionLabelEntry):
        return {}

    return {
        form: {locale: list(values) for locale, values in locales.items()}
        for form, locales in entry.name_forms.items()
        if locales
    }


def skin_argument(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建 skin argument，缺 label 时保留 token fallback。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        skin 参数；没有皮肤 token 时返回 None。
    """

    champion_alias = champion_alias_for_skin(record)
    token = skin_token_for_record(record)

    if champion_alias is None or token is None:
        return None

    labels = context.skin_labels.get((champion_alias, token))
    fallback = labels is None
    return argument_to_dict(
        TranslationArgument(
            role="skin",
            labels=locale_labels(labels, token),
            confidence="high" if not fallback else "low",
            provenance={"source": "translation_context" if not fallback else "skin_token"},
            token=token,
            alias=f"{champion_alias}{token}",
            fallback=fallback,
            fallback_reason=None if not fallback else "missing_skin_label",
            omit_when_context_known=("skin",),
        )
    )


def ability_slot_argument(spell_key: object) -> dict[str, object] | None:
    """构建技能槽位 argument。

    Args:
        spell_key: 语义槽中的技能键位。

    Returns:
        ability_slot 参数；无技能键位时返回 None。
    """

    if not isinstance(spell_key, str) or not spell_key:
        return None

    value = spell_key.lower()
    label = value.upper()
    return argument_to_dict(
        TranslationArgument(
            role="ability_slot",
            labels={"zh_CN": f"{label}技能", "en_US": f"{label} ability"},
            confidence="high",
            provenance={"source": "semantic_slots.spell_key"},
            key=value,
            value=value,
        )
    )


def ability_component_argument(
    slots: dict[str, object],
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建技能组件 argument。

    Args:
        slots: 拆解记录 semantic slots。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        ability_component 参数；无组件时返回 None。
    """

    contextual_alias = slots.get("contextual_alias")
    component_key = None
    raw_label = None
    source = "semantic_slots"
    confidence = "medium"

    if isinstance(contextual_alias, dict):
        component_key = string_or_none(contextual_alias.get("component"))
        raw_label = string_or_none(contextual_alias.get("label"))
        source = string_or_none(contextual_alias.get("source")) or source
        confidence = string_or_none(contextual_alias.get("confidence")) or confidence

    idle_component = slots.get("idle_component")
    if component_key is None and isinstance(idle_component, dict):
        kind = string_or_none(idle_component.get("kind"))
        if kind == "weapon":
            component_key = string_or_none(idle_component.get("key"))
            raw_label = idle_weapon_component_label(slots, idle_component)
            source = "semantic_slots.idle_component"
        elif kind == "idle_variant":
            return None

    if component_key is None:
        status_key = string_or_none(slots.get("status_key"))
        if status_key is not None:
            component_key = status_key
            raw_label = component_label_zh(status_key, None, None, labels=labels)
            source = "semantic_slots.status_key"

    if component_key is None:
        return None

    if source == "semantic_slots.idle_component" and raw_label:
        zh_label = raw_label
    else:
        zh_label = component_label_zh(component_key, raw_label, slots.get("spell_key"), labels=labels)
    return argument_to_dict(
        TranslationArgument(
            role="ability_component",
            labels={"zh_CN": zh_label, "en_US": component_key.replace("_", " ").title()},
            confidence=confidence,
            provenance={"source": source},
            key=component_key,
        )
    )


def phase_argument(
    phase: object,
    frame_type: str,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建翻译层 phase argument。

    Args:
        phase: profile 或 reference 写入的阶段值。
        frame_type: 当前 frame 类型。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        phase 参数；不支持的阶段返回 None。
    """

    key = phase_key(phase, frame_type)
    if key is None:
        return None

    active_labels = labels if labels is not None else get_display_labels()
    # phase_key() 是严格 allowlist，理论上 key 必在词表中；词表被误删词条时
    # 回落为 key 本身，保持“不抛错”而不是 KeyError 崩溃整条记录。
    zh_label, en_label = active_labels.phase.get(key, (key, key))
    return argument_to_dict(
        TranslationArgument(
            role="phase",
            labels={"zh_CN": zh_label, "en_US": en_label},
            confidence="high",
            provenance={"source": "semantic_slots.phase", "frame_type": frame_type, "raw_phase": phase},
            key=key,
        )
    )


def action_argument(
    action: object,
    frame_type: str,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建动作 argument。

    Args:
        action: 原始动作键。
        frame_type: 当前 frame 类型。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        action 参数；没有动作时返回 None。
    """

    if not isinstance(action, str) or not action:
        return None

    active_labels = labels if labels is not None else get_display_labels()
    zh, en = active_labels.action.get(action.lower(), (action, action))
    return argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.action", "frame_type": frame_type},
            key=action.lower(),
        )
    )


def variant_argument(slots: dict[str, object], frame_type: str) -> dict[str, object] | None:
    """构建变体 argument。

    Args:
        slots: 拆解记录 semantic slots。
        frame_type: 当前 frame 类型。

    Returns:
        variant 参数；没有变体时返回 None。
    """

    idle_variant = slots.get("idle_variant")
    if not isinstance(idle_variant, dict):
        return None

    index = idle_variant.get("index")
    if index is None:
        return None

    segment = string_or_none(slots.get("segment_index"))
    zh_label = f"待机{index}片段{segment}" if segment is not None else f"待机{index}"
    en_label = f"idle {index} segment {segment}" if segment is not None else f"idle {index}"
    return argument_to_dict(
        TranslationArgument(
            role="variant",
            labels={"zh_CN": zh_label, "en_US": en_label},
            confidence="medium",
            provenance={"source": "semantic_slots.idle_variant", "frame_type": frame_type},
            key=f"idle_{index}",
            value=segment,
        )
    )


def map_argument(
    map_id: object,
    *,
    labels: DisplayLabels | None = None,
    reference_map_names: Mapping[int, str] | None = None,
) -> dict[str, object] | None:
    """构建 map argument，优先使用真实地图名而不是 Map{id} 占位。

    zh_CN 优先取词表人工核对名；缺失时退到 reference 英文真名；再无则保留
    Map{id}。en_US 直接取 reference 英文真名；缺失时保留 Map{id}。地图中文
    名错误比显示英文名更严重，因此词表只收录高置信 id，宁缺毋错。

    Args:
        map_id: 地图 ID。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。
        reference_map_names: 地图 id 到 reference 英文真名的映射；为 None 时使用
            进程内缓存的默认索引（供测试注入替代数据）。

    Returns:
        map 参数；无地图 ID 时返回 None。
    """

    if not isinstance(map_id, int):
        return None

    active_labels = labels if labels is not None else get_display_labels()
    active_reference_names = (
        reference_map_names if reference_map_names is not None else get_reference_map_names()
    )

    placeholder = f"Map{map_id}"
    reference_name = active_reference_names.get(map_id)
    curated_zh = active_labels.maps.get(map_id)

    if curated_zh is not None:
        zh_label, zh_source = curated_zh, "display_labels.maps"
    elif reference_name is not None:
        zh_label, zh_source = reference_name, "reference_maps"
    else:
        zh_label, zh_source = placeholder, "placeholder"

    if reference_name is not None:
        en_label, en_source = reference_name, "reference_maps"
    else:
        en_label, en_source = placeholder, "placeholder"

    # 只有 zh 来自人工核对词表且 en 有 reference 真名支撑时才算“确认无误”；
    # 其余情形（英文真名回填中文、或彻底未知 id）都要在 provenance 留痕。
    fallback = zh_source != "display_labels.maps" or en_source != "reference_maps"
    fallback_reason = None
    if fallback:
        fallback_reason = (
            "missing_map_label" if zh_source == "placeholder" and en_source == "placeholder" else "map_label_incomplete"
        )

    return argument_to_dict(
        TranslationArgument(
            role="map",
            labels={"zh_CN": zh_label, "en_US": en_label},
            confidence="medium",
            provenance={
                "source": "semantic_slots.map_id",
                "zh_source": zh_source,
                "en_source": en_source,
            },
            key=f"map{map_id}",
            value=str(map_id),
            fallback=fallback,
            fallback_reason=fallback_reason,
        )
    )


def gameplay_event_argument(
    event: object,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建玩法事件 argument。

    Args:
        event: 玩法事件键。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        gameplay_event 参数；无事件时返回 None。
    """

    if not isinstance(event, str) or not event:
        return None

    active_labels = labels if labels is not None else get_display_labels()
    zh, en = active_labels.gameplay_event.get(event.lower(), (event, event))
    return argument_to_dict(
        TranslationArgument(
            role="gameplay_event",
            labels={"zh_CN": zh, "en_US": en},
            confidence="medium",
            provenance={"source": "semantic_slots.gameplay_event"},
            key=event.lower(),
        )
    )


def champion_alias_for_skin(record: EventDecompositionRecord) -> str | None:
    """返回用于查皮肤 label 的英雄 alias。

    Args:
        record: 拆解记录。

    Returns:
        英雄 alias；无法定位时返回 None。
    """

    subject = record.semantic_slots.get("subject")
    if isinstance(subject, dict):
        alias = string_or_none(subject.get("alias"))
        if alias is not None:
            return strip_embedded_skin_token(alias)

    alias = string_or_none(record.semantic_slots.get("subject_hint")) or record.owner.alias
    if alias is None:
        return None

    return strip_embedded_skin_token(alias)


def strip_embedded_skin_token(alias: str) -> str:
    """去掉 alias 尾部内嵌的 SkinNN token。

    Args:
        alias: 原始英雄 alias。

    Returns:
        去掉皮肤号后的 alias；剥离后为空时返回原值。
    """

    cleaned = re.sub(r"skin[0-9]+$", "", alias, flags=re.IGNORECASE)
    return cleaned or alias


def skin_token_for_record(record: EventDecompositionRecord) -> str | None:
    """从记录中提取 SkinNN token。

    Args:
        record: 拆解记录。

    Returns:
        规范化 SkinNN token；没有皮肤信息时返回 None。
    """

    token = string_or_none(record.semantic_slots.get("skin_token")) or record.owner.skin_token
    if token is not None:
        return normalize_skin_token(token)

    candidates = [
        record.owner.raw_owner_token,
        record.owner.alias,
        string_or_none(record.semantic_slots.get("effect_head")),
        string_or_none(record.semantic_slots.get("effect_base")),
    ]
    for candidate in candidates:
        if candidate is None:
            continue

        match = re.search(r"Skin([0-9]+)", candidate, flags=re.IGNORECASE)
        if match is not None:
            return normalize_skin_token(match.group(0))

    return None


def normalize_skin_token(token: str) -> str:
    """规范化 SkinNN token。

    Args:
        token: 原始 skin token。

    Returns:
        两位数字形式的 SkinNN token。
    """

    match = re.fullmatch(r"Skin([0-9]+)", token, flags=re.IGNORECASE)
    if match is None:
        return token

    return f"Skin{int(match.group(1)):02d}"


def component_label_zh(
    component_key: str,
    raw_label: str | None,
    spell_key: object,
    *,
    labels: DisplayLabels | None = None,
) -> str:
    """返回技能组件中文标签。

    Args:
        component_key: 组件规范键。
        raw_label: contextual alias 中的原始中文标签。
        spell_key: 当前技能键位。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        去掉冗余技能槽位后的中文组件标签。
    """

    if raw_label:
        label = raw_label.strip()
        if isinstance(spell_key, str) and spell_key:
            label = re.sub(rf"^{re.escape(spell_key.upper())}\s*", "", label, flags=re.IGNORECASE)
        if label:
            return label

    active_labels = labels if labels is not None else get_display_labels()
    return active_labels.ability_component.get(component_key, component_key.replace("_", " "))


def idle_weapon_component_label(slots: dict[str, object], component: dict[str, object]) -> str:
    """返回待机武器组件中文标签。

    Args:
        slots: 拆解记录 semantic slots。
        component: idle_component 字典。

    Returns:
        待机武器组件中文标签。
    """

    spell_key = string_or_none(slots.get("spell_key"))
    if spell_key is not None:
        return f"{spell_key.upper()}武器"

    label = string_or_none(component.get("label_zh"))
    if label is not None:
        return label

    return string_or_none(component.get("label")) or string_or_none(component.get("key")) or "武器"


def phase_key(phase: object, frame_type: str) -> str | None:
    """把拆解阶段映射为 translation phase key。

    Args:
        phase: 原始阶段值。
        frame_type: 当前 frame 类型。

    Returns:
        translation phase key；不支持时返回 None。
    """

    if phase is None:
        return None

    normalized = str(phase).lower()
    if normalized in STATE_ACTIVATE_PHASES:
        return "state_activate"
    if normalized in STATE_DEACTIVATE_PHASES:
        return "state_deactivate"
    if normalized in {
        "cast",
        "in",
        "out",
        "segment",
        "leadin",
        "leadout",
        "winddown",
        "hit",
        "missile_launch",
        "launch",
        "end",
        "start",
        "land",
        "detonate",
        "charge",
        "fullcharge",
        "stop",
        "miss",
        "catch",
        "return",
        "ready",
        "swap",
        "reset",
    }:
        return normalized
    if frame_type == "gameplay_phase_control" and normalized == "select":
        return "phase_select"

    return None


def state_phase_key(phase: object) -> str | None:
    """把状态阶段映射为启动或结束 key。

    Args:
        phase: 原始阶段值。

    Returns:
        状态阶段 key；不是状态阶段时返回 None。
    """

    if phase is None:
        return None

    normalized = str(phase).lower()
    if normalized in STATE_ACTIVATE_PHASES:
        return "state_activate"
    if normalized in STATE_DEACTIVATE_PHASES:
        return "state_deactivate"

    return None


def phase_from_cue_path(slots: dict[str, object]) -> str | None:
    """从 cue_path 中回退提取阶段。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        阶段 token；无法提取时返回 None。
    """

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list):
        return None

    for item in reversed(cue_path):
        if not isinstance(item, str):
            continue

        normalized = item.lower()
        if normalized in {"buffactivate", "buffdeactivate", "onbuffactivate", "onbuffdeactivate", "cast"}:
            return normalized

    return None


def locale_labels(values: Any, fallback: str) -> dict[str, str]:
    """构建多语言标签。

    Args:
        values: context 中的多语言 label mapping。
        fallback: 缺失时使用的 label。

    Returns:
        至少包含 zh_CN 和 en_US 的 label 字典。
    """

    if isinstance(values, dict):
        labels = {str(key): str(value) for key, value in values.items() if value}
    else:
        labels = {}

    labels.setdefault("zh_CN", labels.get("en_US", fallback))
    labels.setdefault("en_US", labels.get("zh_CN", fallback))
    return labels


def string_or_none(value: object) -> str | None:
    """把非空字符串值安全转为 str。

    Args:
        value: 待读取值。

    Returns:
        非空字符串；否则返回 None。
    """

    if isinstance(value, str) and value:
        return value

    return None
