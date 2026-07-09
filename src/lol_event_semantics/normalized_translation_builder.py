"""normalized translation frame 构建器。"""

from __future__ import annotations

import re
from typing import Any

from .decomposition_model import EventDecompositionRecord
from .normalized_translation_model import (
    FRAME_VERSION,
    TRANSLATION_SCHEMA_VERSION,
    TranslationArgument,
    TranslationBuildContext,
    argument_to_dict,
)

STATE_ACTIVATE_PHASES = {"on_buff_activate", "buff_activate", "buffactivate", "activate"}
STATE_DEACTIVATE_PHASES = {"on_buff_deactivate", "buff_deactivate", "buffdeactivate", "deactivate"}
VOICE_EVENT_FAMILIES = {
    "attack": "攻击",
    "taunt": "嘲讽",
    "joke": "玩笑",
    "laugh": "大笑",
    "dance": "跳舞",
}
# 壳动词只有偏离默认播放语义的才需要出现在文案里（信息无损：stop/mute 不可反转为播放）。
SHELL_PLAYBACK_VERBS = {
    "stop": ("停止", "stopped"),
    "mute": ("静音", "muted"),
}
# sfx/vo/mus 是音频类型标志：动作措辞必须跟随 asset kind，未识别时保守按语音处理。
ASSET_KIND_WORDS = {
    "vo": ("语音", "voice"),
    "sfx": ("音效", "sfx"),
    "mus": ("音乐", "music"),
}


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
    """按 primary family 分发到具体 frame builder。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        translation frame 字典；未覆盖的语义返回 None。
    """

    builders = {
        "skin_recall_state": build_skin_recall_state,
        "skin_idle_state": build_skin_idle_state,
        "skin_action_state": build_skin_action_state,
        "gameplay_phase_control": build_gameplay_phase_control,
    }
    builder = builders.get(record.profile.primary_family)
    if builder is not None:
        return builder(record, context)

    if record.profile.primary_family in VOICE_EVENT_FAMILIES:
        return build_voice_event(record, context)

    ability_state = build_ability_state_change(record, context)
    if ability_state is not None:
        return ability_state

    return build_ability_cast_event(record, context)


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

    action = voice_action_argument(record.profile.primary_family, record.shell.asset_kind)
    if action is None:
        return None

    return build_frame(
        record,
        "voice_event",
        [
            champion_argument(record, context),
            skin_argument(record, context),
            action,
        ],
    )


def voice_action_argument(primary_family: str, asset_kind: object = None) -> dict[str, object] | None:
    """构建语音/音效动作 argument。

    Args:
        primary_family: 拆解 profile 的主家族。
        asset_kind: 事件外壳音频类型（vo/sfx/mus）。

    Returns:
        action 参数；不属于语音动作家族时返回 None。
    """

    zh = VOICE_EVENT_FAMILIES.get(primary_family)
    if zh is None:
        return None

    zh_word, en_word = asset_kind_words(asset_kind)
    return argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": f"{zh}{zh_word}", "en_US": f"{primary_family.replace('_', ' ')} {en_word}"},
            confidence="high",
            provenance={"source": "profile.primary_family", "frame_type": "voice_event"},
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

    labels = locale_labels((context or TranslationBuildContext()).champion_labels.get(alias), label or alias)
    return argument_to_dict(
        TranslationArgument(
            role="champion",
            labels=labels,
            confidence="high",
            provenance={"source": source},
            alias=alias,
            omit_when_context_known=("champion",),
        )
    )


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


def ability_component_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """构建技能组件 argument。

    Args:
        slots: 拆解记录 semantic slots。

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
            raw_label = component_label_zh(status_key, None, None)
            source = "semantic_slots.status_key"

    if component_key is None:
        return None

    if source == "semantic_slots.idle_component" and raw_label:
        zh_label = raw_label
    else:
        zh_label = component_label_zh(component_key, raw_label, slots.get("spell_key"))
    return argument_to_dict(
        TranslationArgument(
            role="ability_component",
            labels={"zh_CN": zh_label, "en_US": component_key.replace("_", " ").title()},
            confidence=confidence,
            provenance={"source": source},
            key=component_key,
        )
    )


def phase_argument(phase: object, frame_type: str) -> dict[str, object] | None:
    """构建翻译层 phase argument。

    Args:
        phase: profile 或 reference 写入的阶段值。
        frame_type: 当前 frame 类型。

    Returns:
        phase 参数；不支持的阶段返回 None。
    """

    key = phase_key(phase, frame_type)
    if key is None:
        return None

    zh_labels = {
        "state_activate": "状态启动",
        "state_deactivate": "状态结束",
        "cast": "释放",
        "in": "进入",
        "out": "退出",
        "segment": "片段",
        "leadin": "引入",
        "leadout": "引出",
        "winddown": "收尾",
        "hit": "命中",
        "phase_select": "阶段选择",
        "missile_launch": "弹道发射",
        "launch": "发射",
        "end": "结束",
        "start": "开始",
        "land": "落地",
        "detonate": "引爆",
        "charge": "蓄力",
        "fullcharge": "满蓄力",
        "stop": "停止",
        "miss": "落空",
        "catch": "抓取",
        "return": "返回",
        "ready": "就绪",
        "swap": "切换",
        "reset": "重置",
    }
    en_labels = {
        "state_activate": "state started",
        "state_deactivate": "state ended",
        "cast": "cast",
        "in": "in",
        "out": "out",
        "segment": "segment",
        "leadin": "lead in",
        "leadout": "lead out",
        "winddown": "wind down",
        "hit": "hit",
        "phase_select": "phase select",
        "missile_launch": "missile launch",
        "launch": "launch",
        "end": "end",
        "start": "start",
        "land": "land",
        "detonate": "detonate",
        "charge": "charge",
        "fullcharge": "full charge",
        "stop": "stop",
        "miss": "miss",
        "catch": "catch",
        "return": "return",
        "ready": "ready",
        "swap": "swap",
        "reset": "reset",
    }
    return argument_to_dict(
        TranslationArgument(
            role="phase",
            labels={"zh_CN": zh_labels[key], "en_US": en_labels[key]},
            confidence="high",
            provenance={"source": "semantic_slots.phase", "frame_type": frame_type, "raw_phase": phase},
            key=key,
        )
    )


def action_argument(action: object, frame_type: str) -> dict[str, object] | None:
    """构建动作 argument。

    Args:
        action: 原始动作键。
        frame_type: 当前 frame 类型。

    Returns:
        action 参数；没有动作时返回 None。
    """

    if not isinstance(action, str) or not action:
        return None

    labels = {
        "recall": ("回城", "recall"),
        "taunt": ("嘲讽", "taunt"),
        "death": ("死亡", "death"),
        "joke": ("玩笑", "joke"),
        "laugh": ("大笑", "laugh"),
        "respawn": ("重生", "respawn"),
    }
    zh, en = labels.get(action.lower(), (action, action))
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


def map_argument(map_id: object) -> dict[str, object] | None:
    """构建 map argument。

    Args:
        map_id: 地图 ID。

    Returns:
        map 参数；无地图 ID 时返回 None。
    """

    if not isinstance(map_id, int):
        return None

    return argument_to_dict(
        TranslationArgument(
            role="map",
            labels={"zh_CN": f"Map{map_id}", "en_US": f"Map{map_id}"},
            confidence="medium",
            provenance={"source": "semantic_slots.map_id"},
            key=f"map{map_id}",
            value=str(map_id),
        )
    )


def gameplay_event_argument(event: object) -> dict[str, object] | None:
    """构建玩法事件 argument。

    Args:
        event: 玩法事件键。

    Returns:
        gameplay_event 参数；无事件时返回 None。
    """

    if not isinstance(event, str) or not event:
        return None

    labels = {
        "first_blood": ("一血", "first blood"),
    }
    zh, en = labels.get(event.lower(), (event, event))
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


def component_label_zh(component_key: str, raw_label: str | None, spell_key: object) -> str:
    """返回技能组件中文标签。

    Args:
        component_key: 组件规范键。
        raw_label: contextual alias 中的原始中文标签。
        spell_key: 当前技能键位。

    Returns:
        去掉冗余技能槽位后的中文组件标签。
    """

    if raw_label:
        label = raw_label.strip()
        if isinstance(spell_key, str) and spell_key:
            label = re.sub(rf"^{re.escape(spell_key.upper())}\s*", "", label, flags=re.IGNORECASE)
        if label:
            return label

    labels = {
        "passive_defense": "被动防御",
        "slow_stun": "减速/眩晕",
        "rapid_reload": "快速装填",
        "animal_threshold": "斯嘎尔阈值",
    }
    return labels.get(component_key, component_key.replace("_", " "))


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
