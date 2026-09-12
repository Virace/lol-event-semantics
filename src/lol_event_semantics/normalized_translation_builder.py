"""normalized translation frame 构建器。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from .decomposition_model import EventDecompositionRecord, SemanticProfile
from .display_tier import is_akshan_revive_sfx
from .display_labels import (
    DisplayLabels,
    get_display_labels,
    get_reference_map_names,
    map_system_entity_label,
    use_display_sources,
)
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


@dataclass(frozen=True, slots=True)
class VoiceQualifierResolution:
    """voice_event qualifier 的单一解析结果，供 action 与 argument 共用。"""

    key: str
    zh_cn: str
    en_us: str
    token: str | None
    confidence: str
    fallback: bool
    source: str
    action_mode: str
    value: str | None = None


class ChampionLabelEntry(dict[str, str]):
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
BUFF_CAST_PHASES = {"on_buff_cast", "buff_cast", "buffcast"}
VOICE_EVENT_FAMILIES = {
    "attack": "攻击",
    "taunt": "嘲讽",
    "joke": "玩笑",
    "laugh": "大笑",
    "dance": "跳舞",
    "dance_response": "舞蹈回应",
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
        "dance_response",
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
    ("dance", "in"): ("进入跳舞", "dance enter"),
    # P4 self-action 残差：这些 token 都直接限定已解析动作段，不是新的动作族。
    # 措辞复用 phase/action 既有语义；原 token 仍保存在 qualifier argument。
    ("dance", "intro"): ("舞蹈引入", "dance intro"),
    ("dance", "leadin"): ("舞蹈引入", "dance lead in"),
    ("dance", "slower"): ("慢速舞蹈", "slower dance"),
    ("dance", "spam"): ("连续舞蹈", "repeated dance"),
    ("dance", "struggle"): ("挣扎舞蹈", "struggle dance"),
    ("joke", "a"): ("玩笑A段", "joke segment A"),
    ("joke", "b"): ("玩笑B段", "joke segment B"),
    ("joke", "in"): ("玩笑进入", "joke enter"),
    ("joke", "intro"): ("玩笑引入", "joke intro"),
    ("joke", "main"): ("玩笑主体段", "joke main segment"),
    ("recall", "improved"): ("强化回城", "improved recall"),
    ("recall", "leadin"): ("回城引入", "recall lead in"),
    ("recall", "leadout"): ("回城引出", "recall lead out"),
    ("recall", "old"): ("旧版回城", "old recall"),
    ("recall", "winddown"): ("回城收尾", "recall wind down"),
    ("ping", "enemymissing"): ("敌人消失", "enemy missing"),
    ("recall", "cancel"): ("取消回城", "cancel recall"),
    ("use_item", "ward"): ("使用守卫", "use ward"),
    ("use_item", "group2dward"): ("使用守卫", "use ward"),
    ("use_item", "potion"): ("使用药水", "use potion"),
    ("use_item", "healthpotion"): ("使用药水", "use potion"),
    ("shop", "open"): ("进店", "shop enter"),
    ("shop", "close"): ("离店", "shop leave"),
    ("level_up", "six"): ("升至6级", "level up to 6"),
    ("level_up", "eleven"): ("升至11级", "level up to 11"),
    ("level_up", "fifteen"): ("升至15级", "level up to 15"),
    ("level_up", "eighteen"): ("升至18级", "level up to 18"),
}
VOICE_QUALIFIER_CANONICAL_KEYS = {
    ("use_item", "group2dward"): "ward",
    ("use_item", "healthpotion"): "potion",
}
# move 距离特例：semantic_slots.distance=long → 远距离移动（SPEC 措辞表）。
VOICE_MOVE_LONG_PHRASE = ("远距离移动", "long distance move")
# 无信息增量的通用 qualifier（General/Standard 等）以及已由 asset_kind /
# dimension_token 表达的纯技术后缀，不产生 qualifier argument。
GENERIC_ACTION_QUALIFIERS = frozenset({"3d", "general", "generic", "loop", "sfx", "standard"})
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
    "first_blood": ("第一滴血", "first blood"),
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
    "epic_objective": ("史诗级野怪", "epic objective"),
}
TIMING_WORDS = {"first": ("首次", "first"), "second": ("再次", "second")}
RELATION_WORDS = {"ally": ("队友", "ally"), "enemy": ("敌方", "enemy")}
KILL_METHOD_SPELL_KEYS = frozenset({"Q", "W", "E", "R"})
COUNTERPART_FALLBACK_REASON = "missing_counterpart_zh_label"
PARTNER_FALLBACK_REASON = "missing_partner_zh_label"
# 批次4 dialogue 家族：base_trigger 家族中文核心词。move/attack/taunt/
# taunt_response 与 VOICE_EVENT_FAMILIES、first_encounter 与
# INTERACTION_TRIGGER_WORDS、kill 与 kill_trigger_argument 的既有措辞逐字
# 一致（dialogue 括号只放家族词，不再追加语音亚词，单独立表避免误带后缀）；
# spell/first_move/heal 为批次4 SPEC 补词。表外家族不硬造词条：无 turn 时
# 无括号，信息仍无损保留在 semantic.slots.base_trigger。
DIALOGUE_BASE_TRIGGER_WORDS = {
    "move": ("移动", "move"),
    "attack": ("攻击", "attack"),
    "kill": ("击杀", "kill"),
    "taunt": ("嘲讽", "taunt"),
    "first_encounter": ("初遇", "first encounter"),
    "taunt_response": ("嘲讽回应", "taunt response"),
    "spell": ("技能", "spell"),
    "first_move": ("首次移动", "first move"),
    "heal": ("治疗", "heal"),
}
# 批次5a 播报员超族：4 个 primary_family 与 slots.system_voice_kind=announcer
# 双条件分派（防未来 family/kind 单边漂移）；tutorial/game_mode/emote_voice
# 同为 system_voice 家族但 kind 不同，天然被第二门排除。
ANNOUNCER_EVENT_FAMILIES = frozenset({"announcer", "audio_scope", "announcer_persona", "system_voice"})
# 播报员事件 token 的 On 时机前缀（OnChampionKill…）：余部非空且首字母大写才剥。
ANNOUNCER_ON_PREFIX_PATTERN = re.compile(r"On(?=[A-Z])")
# partner raw 尾缀 SkinNN 时按 (champion, SkinNN) 查皮肤 zh 全名。
PARTNER_SKIN_ALIAS_PATTERN = re.compile(r"(?P<champion>[A-Za-z][A-Za-z0-9]*?)(?P<skin>Skin[0-9]+)", re.IGNORECASE)
CHAMPION_BANK_SOURCE_PATTERN = re.compile(r"(?:^|[\\/])champions[\\/](?P<id>[0-9]+)\.txt$", re.IGNORECASE)
# partner raw 尾缀 Near/Far 是距离触发条件（同 2D/3D 的技术 token，Xayah/Rakan
# 对话链 690/812 条带此形态），不是对话对象名的一部分：剥离后按 champion 重查，
# 限定词存 provenance.proximity，原 token 无损保留在 argument.token。
PARTNER_PROXIMITY_PATTERN = re.compile(r"(?P<alias>[A-Za-z][A-Za-z0-9]*?)(?P<proximity>Near|Far)")
# ownerless 事件（Play_vo_Kill3DPenta 等）的 owner 段是事件体本身，champion
# label 会残留维度/壳 token；这类主体进不了用户文案，保守放弃建 frame。
TECHNICAL_CHAMPION_LABEL_MARKERS = ("3D", "2D", "_", "Play")
# 批次5b media_base：动作 token 词尾 2D/3D 剥离，沿 skin_action_state 拆解口径
# （decomposition_profiles.parse_terminal_dimension_action：仅整词尾，避免误伤
# 词中/词首的 3D）；无词尾维度的小写形态（Base_death）原样通过。
MEDIA_BASE_ACTION_DIMENSION_PATTERN = re.compile(r"(?P<action>[A-Za-z]+)(?P<dimension>[23]D)", re.IGNORECASE)
# skin_named_state 中只有逐项审阅过的命名状态才能进入用户文案。Jhin 的
# passive_idle 由 owner-scoped P 技能规则承接；这里处理无标准 sfx 壳、但
# 已由专用 profile 确认主体与皮肤的 JinxSkin60 slotmachine 资源。
SKIN_NAMED_SFX_EFFECTS = {
    "slotmachine": ("slot_machine", "皮肤老虎机", "skin slot machine"),
}
SKIN_NAMED_SFX_QUALIFIERS = {
    "anomaly": "异常",
    "chemtech": "炼金科技",
    "hextech": "海克斯科技",
}
SYSTEM_SFX_LIFECYCLE_TOKENS = frozenset({"buffactivate", "buffdeactivate", "loop"})
ABILITY_SEQUENCE_QUALIFIER_WORDS = {
    "aoe": "范围效果",
    "beam": "光束",
    "clone": "分身",
}
ABILITY_SEQUENCE_RICH_PHASES = frozenset({"leadin", "leadout", "loop", "winddown", "windup"})
# Arena/ARAM 增益系事件：reference_entity.kind 三家族与 cue 家族标记段双条件
# 分派（防未来单边漂移）。cue_path 首段标记之后一段是增益/道具 Code；标记
# 同时归一出 provenance 的对象类别（Item→道具，ARAM/Augment→增益）。
MODE_AUGMENT_REFERENCE_KINDS = frozenset({"augment_system", "game_mode", "item_system"})
MODE_AUGMENT_CUE_MARKERS = {"aram": "augment", "augment": "augment", "item": "item"}
MODE_AUGMENT_MODE_LABELS = {"aram": ("极地大乱斗", "ARAM")}
MODE_AUGMENT_TARGET_QUALIFIERS = {
    "player": ("自身", "self"),
    "others": ("其他玩家", "other players"),
}
MODE_AUGMENT_ABILITY_SEGMENT_PATTERN = re.compile(r"(?P<owner>[A-Z][A-Za-z0-9]*?)(?P<spell_key>[QWERP])$")
MODE_AUGMENT_BORROWED_SPELL_PATTERN = re.compile(
    r"(?P<owner>[A-Z][A-Za-z0-9]*?)(?P<spell_key>[QWERP])(?P<residual>[A-Z][A-Za-z0-9]+)"
)
# 系统/道具域标记不是模式名，模式前缀候选跳过不查 T3.4 词条表。
MODE_AUGMENT_NON_MODE_MARKERS = frozenset({"augment", "item"})
COMPONENT_CONTEXT_MODIFIER_PATTERN = re.compile(
    r"^(?:(?P<qualifier>Empowered)(?P<phase>Start|End)|"
    r"(?P<context_phase>Detonate|Loop|CloudHit|HitDirect|HitBig|Active|Inactive)(?:[0-9]+)?)$",
    re.IGNORECASE,
)
COMPONENT_CONTEXT_PHASE_ALIASES = {
    "active": "start",
    "cloudhit": "hit",
    "hitbig": "hit",
    "hitdirect": "hit",
    "inactive": "end",
}
COMPONENT_QUALIFIER_WORDS = {"empowered": ("强化", "empowered")}
CHAMPION_ACTION_SFX_FAMILIES = frozenset(
    {
        "attack",
        "dance",
        "death",
        "homeguard",
        "idle",
        "joke",
        "laugh",
        "recall",
        "respawn",
        "taunt",
        "winddown",
    }
)


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
    display_scope = (
        use_display_sources(active_context.display_labels or get_display_labels(), active_context.reference_map_names)
        if context is not None
        else nullcontext()
    )
    with display_scope:
        translation = build_translation_frame(record, active_context)
    if translation is None:
        return None

    attach_context_argument(translation, record)
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

    # 批次4：dialogue_chain profile（primary_family=dialogue 同源，双条件防
    # 未来 profile/family 单边漂移）整链走 champion_dialogue_event。
    if record.profile.semantic_profile is SemanticProfile.DIALOGUE_CHAIN or record.profile.primary_family == "dialogue":
        return build_champion_dialogue_event(record, context)

    # 批次5a：播报员超族（family 与 system_voice_kind 双条件防单边漂移）
    # 整域走 announcer_event，表外词干由 builder 返回 None 维持 legacy 展示。
    if (
        record.profile.primary_family in ANNOUNCER_EVENT_FAMILIES
        and record.semantic_slots.get("system_voice_kind") == "announcer"
    ):
        return build_announcer_event(record, context)

    if record.profile.primary_family == "move" and record.profile.semantic_profile is SemanticProfile.QUALIFIED_ACTION:
        # Move2DFirst:无交互对象的首次移动台词(qualified_action),
        # 与 interaction 的 move 共用同一措辞(「首次移动语音」)。
        return build_champion_interaction_cue(record, context)

    mode_system_sfx = build_mode_system_sfx_event(record, context)
    if mode_system_sfx is not None:
        return mode_system_sfx

    system_sfx = build_reviewed_system_sfx_event(record, context)
    if system_sfx is not None:
        return system_sfx

    item_sfx = build_reviewed_item_sfx_event(record, context)
    if item_sfx is not None:
        return item_sfx

    borrowed_ability = build_borrowed_ability_event(record, context)
    if borrowed_ability is not None:
        return borrowed_ability

    mode_unit_attack = build_mode_unit_attack_event(record)
    if mode_unit_attack is not None:
        return mode_unit_attack

    builders = {
        "skin_recall_state": build_skin_recall_state,
        "skin_idle_state": build_skin_idle_state,
        "skin_action_state": build_skin_action_state,
        "skin_named_state": build_skin_named_sfx_effect,
        # 批次5b：media_base 兜底桶里的英雄动作状态（Base_Dance3D_buffactivate 等）
        # 复用 skin_action_state frame；表外动作词/系统 UI 音由 builder 返回 None
        # 维持 legacy 展示。
        "media_base": build_media_base_action_state,
        "gameplay_phase_control": build_gameplay_phase_control,
        # 批次6d：map 家族当前只有商店老板语音可承接（其余 map 记录此前也
        # 无 frame，返回 None 维持 legacy，与旧行为一致）。
        "map": build_shop_voice_event,
    }
    builder = builders.get(record.profile.primary_family)
    if builder is not None:
        return builder(record, context)

    attack_kind = record.semantic_slots.get("attack_kind")
    if record_has_champion_subject(record) and (
        record.profile.primary_family in {"basic_attack", "crit_attack"}
        or attack_kind in {"basic_attack", "crit_attack"}
    ):
        attack = build_champion_attack_event(record, context)
        if attack is not None:
            return attack

    if record.profile.primary_family == "execute_action":
        return build_champion_action_event(record, context)

    if record.profile.primary_family == "contextual_action":
        return build_champion_action_event(record, context)

    ability_sequence = build_champion_ability_sequence_event(record, context)
    if ability_sequence is not None:
        return ability_sequence

    ability_stage = build_champion_ability_stage_event(record, context)
    if ability_stage is not None:
        return ability_stage

    ability_rank = build_champion_ability_rank_event(record, context)
    if ability_rank is not None:
        return ability_rank

    # 既有 5 个情绪动作家族维持原分派位置；批次3 家族不在此处接，
    # 让 ability builder 先接走带技能槽位的同名记录（见 SELF_ACTION_VOICE_FAMILIES）。
    if (
        record.profile.primary_family in VOICE_EVENT_FAMILIES
        and record.profile.primary_family not in SELF_ACTION_VOICE_FAMILIES
        and record.shell.asset_kind != "sfx"
    ):
        return build_voice_event(record, context)

    ability_state = build_ability_state_change(record, context)
    if ability_state is not None:
        return ability_state

    ability_cast = build_ability_cast_event(record, context)
    if ability_cast is not None:
        return ability_cast

    ability_component = build_champion_ability_component_event(record, context)
    if ability_component is not None:
        return ability_component

    sfx_effect = build_champion_sfx_effect_event(record, context)
    if sfx_effect is not None:
        return sfx_effect

    # 增益系分派必须在 ability builder 之后：借用英雄技能的增益记录
    # （Oathsworn_KalistaRMis 等）由 ability frame 承接，不被增益 frame 抢占。
    mode_augment = build_mode_augment_event(record, context)
    if mode_augment is not None:
        return mode_augment

    if (
        record.shell.asset_kind == "sfx"
        and record.profile.semantic_profile is SemanticProfile.SELF_ACTION
        and record.profile.primary_family in CHAMPION_ACTION_SFX_FAMILIES
    ):
        action_event = build_champion_action_event(record, context)
        if action_event is not None:
            return action_event

    return build_self_action_voice_event(record, context)


def record_has_champion_subject(record: EventDecompositionRecord) -> bool:
    """判断事件是否确实以英雄为主体，避免地图单位套用英雄 frame。"""

    if record.owner.kind == "champion":
        namespace = record.semantic_slots.get("context_namespace") or record.semantic_slots.get("mode_context")
        namespace_key = string_or_none(namespace.get("key")) if isinstance(namespace, dict) else None
        owner_alias = record.owner.alias or record.owner.raw_owner_token
        namespace_owner_keys = {rule_key(namespace_key), "gamemode"} if namespace_key is not None else set()
        if owner_alias is None or rule_key(owner_alias) not in namespace_owner_keys:
            return True

    for key in ("subject", "mode_actor"):
        value = record.semantic_slots.get(key)
        if isinstance(value, dict) and value.get("kind") == "champion":
            return True

    return False


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


def attach_context_argument(translation: dict[str, object], record: EventDecompositionRecord) -> None:
    """把已声明的模式/机制命名空间统一附加到所有 translation frame。"""

    if not isinstance(record.semantic_slots.get("context_namespace"), dict):
        return
    context = context_namespace_argument(record)
    if context is None:
        return
    arguments = translation.get("arguments")
    if isinstance(arguments, dict):
        arguments.setdefault("context", context)


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
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))

    if phase is None or (component is None and slot is None):
        return None

    rank = spell_rank_argument(slots)
    stage = ability_stage_argument(slots.get("spell_stage"))
    if rank is not None and component is not None:
        strip_component_rank_suffix(component)

    arguments: dict[str, dict[str, object]] = {}
    for item in [
        context_namespace_argument(record),
        champion_argument(record, context),
        skin_argument(record, context),
        slot,
        component,
        rank,
        stage,
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


SPELL_RANK_ALIAS_PATTERN = re.compile(r"Rank(\d+)$")


def spell_rank_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """构建技能等级 argument，并把显式 Rank 与普通数字变体隔离。"""

    raw: str | None = None
    rank_value = slots.get("spell_rank")
    if slots.get("variant_kind") == "spell_rank" and isinstance(rank_value, int) and rank_value > 0:
        rank = rank_value
        source = "semantic_slots.spell_rank_explicit"
    else:
        contextual_alias = slots.get("contextual_alias")
        if not isinstance(contextual_alias, dict):
            return None

        raw = string_or_none(contextual_alias.get("raw"))
        if raw is None:
            return None

        match = SPELL_RANK_ALIAS_PATTERN.search(raw)
        if match is None:
            return None

        rank = int(match.group(1))
        if rank_value != rank:
            return None
        source = "semantic_slots.spell_rank"

    provenance: dict[str, object] = {"source": source}
    if raw is not None:
        provenance["alias_raw"] = raw

    return argument_to_dict(
        TranslationArgument(
            role="rank",
            labels={"zh_CN": f"{rank}级", "en_US": f"rank {rank}"},
            confidence="high",
            provenance=provenance,
            key=str(rank),
            value=str(rank),
        )
    )


def strip_component_rank_suffix(component: dict[str, object]) -> None:
    """rank argument 在场时去掉组件 label 的等级词尾避免叠字（万箭齐发等级2级）。

    Args:
        component: 已构建的 ability_component argument（原地修改 labels）。
    """

    labels = component.get("labels")
    if not isinstance(labels, dict):
        return

    zh = labels.get("zh_CN")
    if isinstance(zh, str) and zh.endswith("等级") and len(zh) > len("等级"):
        labels["zh_CN"] = zh[: -len("等级")]

    en = labels.get("en_US")
    if isinstance(en, str) and en.endswith(" Rank") and len(en) > len(" Rank"):
        labels["en_US"] = en[: -len(" Rank")]


def is_ability_state_change_candidate(record: EventDecompositionRecord) -> bool:
    """判断记录是否应构建 ability_state_change frame。

    Args:
        record: 拆解记录。

    Returns:
        属于技能/状态启动结束语义时返回 True。
    """

    slots = record.semantic_slots
    # generic_sfx_effect 描述的是一段已审阅的音效/模式机制效果，而不是英雄
    # 技能组件。即使原始事件使用 buffactivate/deactivate 作为生命周期尾段，
    # 也必须交给 champion_sfx_effect_event 渲染为“音效开始/结束”；否则
    # ability_component_argument 会把同一个别名误装成技能组件，重新产生
    # “状态生效/结束”这类丢失音效语义的文案。
    if any(
        isinstance(alias, dict) and alias.get("kind") == "generic_sfx_effect"
        for alias in (slots.get("contextual_alias"), slots.get("sfx_alias"))
    ):
        return False

    phase = slots.get("phase") or phase_from_cue_path(slots)
    if state_phase_key(phase) is not None:
        return True

    if record.profile.primary_family == "champion_status_state":
        return True

    # 技能组件只说明“哪个技能部分”，不能改变事件阶段。cast/hit/launch
    # 应继续由 ability_cast_event 承接；无标准阶段的组件则由
    # champion_ability_component_event 承接。仅显式状态相位可以进入本 frame。
    return False


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
    action = apply_action_form(action_argument("recall_sfx", "skin_recall_state"), slots)
    if phase is None or action is None:
        return None

    return build_frame(
        record,
        "skin_recall_state",
        [
            context_namespace_argument(record),
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
    raw_action = slots.get("action")
    action_key = f"{raw_action}_sfx" if isinstance(raw_action, str) and record.shell.asset_kind == "sfx" else raw_action
    action = apply_action_form(action_argument(action_key, "skin_action_state"), slots)
    qualifier = champion_action_qualifier_argument(slots)
    phase = phase_argument(slots.get("phase"), "skin_action_state")

    if action is None or phase is None:
        return None

    return build_frame(
        record,
        "skin_action_state",
        [
            context_namespace_argument(record),
            champion_argument(record, context),
            skin_argument(record, context),
            action,
            qualifier,
            phase,
        ],
    )


def build_champion_attack_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建英雄普通攻击/暴击 frame，技能关联不参与主文案。"""

    slots = record.semantic_slots
    champion = champion_argument(record, context)
    attack = attack_argument(slots, fallback_kind=record.profile.primary_family)
    qualifier = reviewed_alias_qualifier_argument(slots, ("attack_component", "contextual_alias"))
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "champion_attack_event")
    if champion is None or attack is None:
        return None

    return build_frame(
        record,
        "champion_attack_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            attack,
            qualifier,
            phase,
        ],
    )


def build_champion_ability_sequence_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建 Q+E、R+Q 等紧凑技能组合 frame，并保留数字分段。"""

    slots = record.semantic_slots
    sequence = ability_sequence_argument(slots.get("spell_sequence"))
    champion = champion_argument(record, context)
    if sequence is None or champion is None:
        return None

    stage = ability_stage_argument(
        slots.get("spell_sequence_stage"),
        source="semantic_slots.spell_sequence_stage",
    )
    component = ability_component_argument(slots)
    qualifier = ability_sequence_qualifier_argument(slots)
    phase = phase_argument(
        ability_sequence_phase(slots),
        "champion_ability_sequence_event",
    )
    return build_frame(
        record,
        "champion_ability_sequence_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            sequence,
            component,
            stage,
            qualifier,
            phase,
        ],
    )


def ability_sequence_qualifier_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """从组合技 cue 中提取已审阅的范围、分身与光束修饰语。"""

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list):
        return None
    rows = []
    seen: set[str] = set()
    for token in cue_path:
        if not isinstance(token, str):
            continue
        key = rule_key(token)
        label = ABILITY_SEQUENCE_QUALIFIER_WORDS.get(key)
        if label is None or key in seen:
            continue
        rows.append({"key": key, "label": label, "raw": token})
        seen.add(key)
    return structured_qualifier_argument(
        rows,
        confidence="high",
        source="semantic_slots.cue_path",
    )


def ability_sequence_phase(slots: dict[str, object]) -> object:
    """组合技优先保留 loop/leadin 等信息量更高的尾部生命周期。"""

    cue_phase = phase_from_cue_path(slots)
    if isinstance(cue_phase, str) and cue_phase.lower() in ABILITY_SEQUENCE_RICH_PHASES:
        return cue_phase
    return slots.get("phase") or cue_phase


def build_champion_ability_stage_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建只有技能键与段数、没有已知触发相位的英雄音频 frame。"""

    slots = record.semantic_slots
    if slots.get("phase") is not None or phase_from_cue_path(slots) is not None:
        return None

    champion = champion_argument(record, context)
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))
    component = ability_component_argument(slots)
    stage = ability_stage_argument(slots.get("spell_stage"))
    if champion is None or slot is None or stage is None:
        return None

    return build_frame(
        record,
        "champion_ability_stage_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            slot,
            component,
            stage,
        ],
    )


def build_champion_ability_rank_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建只有技能等级、没有动作相位的英雄音频 frame。"""

    slots = record.semantic_slots
    if slots.get("variant_kind") != "spell_rank":
        return None
    if slots.get("phase") is not None or phase_from_cue_path(slots) is not None:
        return None

    champion = champion_argument(record, context)
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))
    rank = spell_rank_argument(slots)
    if champion is None or slot is None or rank is None:
        return None

    return build_frame(
        record,
        "champion_ability_rank_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            slot,
            rank,
        ],
    )


def build_champion_ability_component_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建只有技能槽位与已知组件、没有标准相位的英雄音频 frame。"""

    if record.profile.semantic_profile is not SemanticProfile.SPELL_EVENT:
        return None

    slots = record.semantic_slots
    contextual_alias = slots.get("contextual_alias")
    sfx_alias = slots.get("sfx_alias")
    if not any(
        isinstance(alias, dict) and alias.get("kind") == "champion_spell_component"
        for alias in (contextual_alias, sfx_alias)
    ):
        return None

    champion = champion_argument(record, context)
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))
    component = ability_component_argument(slots)
    stage = ability_stage_argument(slots.get("spell_stage"))
    raw_phase = slots.get("phase") or phase_from_cue_path(slots)
    phase = phase_argument(raw_phase, "champion_ability_component_event")
    if phase is None:
        phase = phase_argument(component_context_phase(slots), "champion_ability_component_event")
    qualifier = reviewed_alias_qualifier_argument(slots, ("contextual_alias", "sfx_alias"))
    if qualifier is None:
        qualifier = component_context_qualifier_argument(slots)
    if champion is None or slot is None or component is None:
        return None

    return build_frame(
        record,
        "champion_ability_component_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            slot,
            component,
            stage,
            qualifier,
            phase,
        ],
    )


def build_champion_action_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建英雄动作 frame，用具体动作而非“状态启动”描述。"""

    slots = record.semantic_slots
    champion = champion_argument(record, context)
    slot_action = slots.get("action")
    action_key = slot_action if isinstance(slot_action, str) else f"{record.profile.primary_family}_sfx"
    sfx_action = (
        sfx_self_action_argument(record.profile.primary_family, slots)
        if record.shell.asset_kind == "sfx" and record.profile.primary_family in VOICE_EVENT_FAMILIES
        else None
    )
    action = apply_action_form(
        action_component_argument(slots) or sfx_action or action_argument(action_key, "champion_action_event"),
        slots,
    )
    qualifier = champion_action_qualifier_argument(slots)
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "champion_action_event")
    if champion is None or action is None:
        return None

    return build_frame(
        record,
        "champion_action_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            action,
            qualifier or (voice_qualifier_argument(record) if sfx_action is not None else None),
            phase,
        ],
    )


def build_champion_sfx_effect_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建已人工审阅的英雄公共 SFX 效果 frame。"""

    slots = record.semantic_slots
    if (
        record.shell.asset_kind != "sfx"
        or slots.get("sfx_scope") not in {"champion_sfx", "mode_sfx"}
        or not record_has_champion_subject(record)
    ):
        return None

    champion = champion_argument(record, context)
    effect = sfx_effect_argument(slots)
    qualifier = sfx_effect_qualifier_argument(slots)
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "champion_sfx_effect_event")
    if champion is None or effect is None:
        return None

    return build_frame(
        record,
        "champion_sfx_effect_event",
        [
            context_namespace_argument(record),
            champion,
            skin_argument(record, context),
            effect,
            qualifier,
            phase,
        ],
    )


def build_mode_system_sfx_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建已审阅模式系统实体的 SFX frame，拒绝表外 cue 猜译。"""

    slots = record.semantic_slots
    mode_entity = slots.get("mode_entity")
    if (
        record.shell.asset_kind != "sfx"
        or slots.get("sfx_scope") != "mode_sfx"
        or record_has_champion_subject(record)
        or not isinstance(slots.get("mode_context"), dict)
        or not isinstance(mode_entity, dict)
    ):
        return None

    effect = mode_system_sfx_effect_argument(mode_entity, context.map_system_entity_labels)
    if effect is None:
        return None

    phase = phase_argument(
        slots.get("phase") or phase_from_cue_path(slots),
        "mode_system_sfx_event",
    )
    return build_frame(
        record,
        "mode_system_sfx_event",
        [
            context_namespace_argument(record),
            effect,
            phase,
        ],
    )


def build_reviewed_system_sfx_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建已审阅 HUD/Base、HUD 技能提示与 Gameplay 系统音效 frame。

    只接受 ``display_labels.system_sfx.exact`` 中的完整相对路径；Base、Gameplay
    是资源分组壳，末尾 buffactivate/buffdeactivate/loop 是生命周期。表外路径
    返回 ``None``，避免把任意工程 token 逐词硬译成看似稳定的中文。无 Base
    壳的 HUD 路径只承接已被 spell profile 识别的技能提示，不借此推广普通
    Tier3 HUD 工程音。
    """

    if record.shell.asset_kind != "sfx" or any(
        isinstance(record.semantic_slots.get(slot_name), dict)
        and record.semantic_slots[slot_name].get("kind") == "champion"
        for slot_name in ("subject", "mode_actor")
    ):
        return None

    raw_segments = reviewed_system_sfx_segments(record)
    if raw_segments is None:
        return None
    normalized_segments = [rule_key(token) for token in raw_segments]
    if any(not segment for segment in normalized_segments):
        return None

    active_labels = context.display_labels or get_display_labels()
    raw_phase = None
    core_segments = normalized_segments
    exact_key = "/".join(core_segments)
    zh_label = active_labels.system_sfx_exact.get(exact_key)
    if zh_label is None and normalized_segments[-1] in SYSTEM_SFX_LIFECYCLE_TOKENS:
        candidate_segments = normalized_segments[:-1]
        candidate_key = "/".join(candidate_segments)
        candidate_label = active_labels.system_sfx_exact.get(candidate_key)
        if candidate_label is not None:
            core_segments = candidate_segments
            exact_key = candidate_key
            zh_label = candidate_label
            raw_phase = normalized_segments[-1]
    if zh_label is None:
        return None

    display_label = zh_label if zh_label.endswith(("音效", "提示音", "音乐")) else f"{zh_label}音效"
    effect = argument_to_dict(
        TranslationArgument(
            role="effect",
            labels={
                "zh_CN": display_label,
                "en_US": f"{' '.join(core_segments).title()} SFX",
            },
            confidence="high",
            provenance={
                "source": "display_labels.system_sfx.exact",
                "cue_path": exact_key,
            },
            key="_".join(core_segments),
            token="/".join(raw_segments),
        )
    )
    context_argument = system_sfx_context_argument(record, active_labels)
    phase = phase_argument(raw_phase, "system_sfx_event")
    return build_frame(
        record,
        "system_sfx_event",
        [context_argument, effect, phase],
    )


def reviewed_system_sfx_segments(record: EventDecompositionRecord) -> list[str] | None:
    """返回可进入精确系统音效词表的相对路径段。"""

    family = record.profile.primary_family
    if family in {"media_base", "gameplay_system"}:
        cue_path = record.semantic_slots.get("cue_path")
        if not isinstance(cue_path, list):
            return None

        marker = "base" if family == "media_base" else "gameplay"
        marker_index = next(
            (index for index, token in enumerate(cue_path) if isinstance(token, str) and rule_key(token) == marker),
            None,
        )
        if marker_index is None:
            return None

        relative_path = cue_path[marker_index + 1 :]
        raw_segments = [token for token in relative_path if isinstance(token, str)]
        return raw_segments if raw_segments and len(raw_segments) == len(relative_path) else None

    raw_owner = record.owner.raw_owner_token or record.owner.alias
    if family != "spell" or rule_key(raw_owner or "") != "hud":
        return None

    body_segments = record.shell.body.split("_")
    if len(body_segments) < 2 or rule_key(body_segments[0]) != "hud":
        return None
    return body_segments[1:]


def build_reviewed_item_sfx_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """用 reference 装备名与精确动作白名单承接装备音效生命周期。

    纯数字 owner 继续承接单一生命周期及同形态的装备 Spell/OnCast。owner
    后缀或生命周期后的余段只有命中 ``装备 ID/动作`` 精确白名单时才进入
    effect；未知组合返回 ``None``，避免用装备总名掩盖尚未审阅的信息。
    """

    exact = build_exact_item_sfx_event(record, context)
    if exact is not None:
        return exact

    if (
        record.shell.asset_kind != "sfx"
        or record.profile.primary_family not in {"phase_event", "spell"}
        or record_has_champion_subject(record)
    ):
        return None

    raw_owner = record.owner.raw_owner_token or record.owner.alias
    if raw_owner is None:
        return None

    owner_match = re.fullmatch(r"(?P<item_id>\d+)(?P<action>[A-Za-z][A-Za-z0-9]*)?", raw_owner)
    if owner_match is None:
        return None

    item_id = int(owner_match.group("item_id"))
    action_token = owner_match.group("action")

    cue_path = record.semantic_slots.get("cue_path")
    if cue_path is None:
        if action_token is not None:
            return None
    else:
        if not isinstance(cue_path, list) or not all(isinstance(token, str) for token in cue_path):
            return None
        if action_token is not None:
            if len(cue_path) != 1:
                return None
        elif len(cue_path) == 2:
            action_token = cue_path[1]
        elif len(cue_path) != 1:
            return None

    names = context.reference_item_names.get(item_id)
    if not isinstance(names, Mapping):
        return None
    zh_label = string_or_none(names.get("zh_CN"))
    en_label = string_or_none(names.get("en_US"))
    if zh_label is None or en_label is None:
        return None

    phase = phase_argument(record.semantic_slots.get("phase"), "system_sfx_event")
    if phase is None:
        return None

    action_key: str | None = None
    action_labels: tuple[str, str] | None = None
    if action_token is not None:
        action_key = rule_key(action_token)
        active_labels = context.display_labels or get_display_labels()
        action_labels = active_labels.system_sfx_item_action.get(f"{item_id}/{action_key}")
        if action_labels is None:
            return None

    effect_key = f"item_{item_id}_sfx"
    effect_zh = f"{zh_label}音效"
    effect_en = f"{en_label} SFX"
    provenance: dict[str, object] = {"source": "reference/items", "item_id": item_id}
    if action_key is not None and action_labels is not None:
        action_zh, action_en = action_labels
        effect_key = f"item_{item_id}_{action_key}_sfx"
        effect_zh = f"{zh_label}{action_zh}音效"
        effect_en = f"{en_label} {action_en} SFX"
        provenance.update(
            {
                "action_key": action_key,
                "action_source": "display_labels.system_sfx.item_action",
            }
        )

    effect = argument_to_dict(
        TranslationArgument(
            role="effect",
            labels={"zh_CN": effect_zh, "en_US": effect_en},
            confidence="high",
            provenance=provenance,
            key=effect_key,
            token=action_token or raw_owner,
        )
    )
    return build_frame(record, "system_sfx_event", [effect, phase])


def build_exact_item_sfx_event(
    record: EventDecompositionRecord, context: TranslationBuildContext
) -> dict[str, object] | None:
    """按完整原始事件名应用带来源的装备审阅决策，不泛化阶段含义。"""

    labels = context.display_labels or get_display_labels()
    row = labels.system_sfx_item_event.get(record.raw_event_name)
    if row is None:
        return None
    item_id = row.get("item_id")
    if not isinstance(item_id, int):
        return None
    names = context.reference_item_names.get(item_id)
    if names is None:
        return None
    provenance = dict(row.get("provenance") or {})
    provenance["rule"] = "display_labels.system_sfx.item_event"
    provenance["item_id"] = item_id
    confidence = str(row.get("confidence") or "medium")
    effect = argument_to_dict(
        TranslationArgument(
            role="effect",
            key=str(row["key"]),
            token=record.raw_event_name,
            labels={locale: str(row[locale]) for locale in ("zh_CN", "en_US")},
            confidence=confidence,
            provenance=provenance,
        )
    )
    item = argument_to_dict(
        TranslationArgument(
            role="context",
            key=f"item_{item_id}",
            labels=dict(names),
            confidence="high",
            provenance={"source": "reference/items", "item_id": item_id},
            omit_when_context_known=("item",),
        )
    )
    frame = build_frame(record, "system_sfx_event", [item, effect])
    frame["frame_confidence"] = confidence
    return frame


def system_sfx_context_argument(
    record: EventDecompositionRecord,
    labels: DisplayLabels,
) -> dict[str, object] | None:
    """从封闭 owner 词表构建系统音效上下文，表外工程名合法缺席。"""

    raw_owner = record.owner.alias or record.owner.raw_owner_token
    if raw_owner is None:
        return None
    owner_key = rule_key(raw_owner)
    zh_label = labels.system_sfx_context.get(owner_key)
    if zh_label is None:
        return None
    return argument_to_dict(
        TranslationArgument(
            role="context",
            labels={"zh_CN": zh_label, "en_US": raw_owner},
            confidence="high",
            provenance={"source": "display_labels.system_sfx.context"},
            key=owner_key,
            token=raw_owner,
        )
    )


def build_borrowed_ability_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建终极魔典借用英雄大招 frame，保留模式与被借用英雄。"""

    slots = record.semantic_slots
    mode_context = slots.get("mode_context")
    if not isinstance(mode_context, dict) or mode_context.get("key") != "ultimate_spellbook":
        return None

    champion = champion_argument(record, context)
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "borrowed_ability_event")
    if champion is None or slot is None or phase is None:
        return None

    return build_frame(
        record,
        "borrowed_ability_event",
        [
            context_namespace_argument(record),
            champion,
            slot,
            phase,
        ],
    )


def build_mode_unit_attack_event(record: EventDecompositionRecord) -> dict[str, object] | None:
    """构建模式单位攻击 frame，实体与 BasicAttack/CritAttack 分角色保存。"""

    slots = record.semantic_slots
    mode_entity = slots.get("mode_entity")
    if not isinstance(mode_entity, dict) or mode_entity.get("kind") != "mode_unit":
        return None

    entity = mode_unit_argument(mode_entity)
    attack = attack_argument(slots)
    phase = phase_argument(slots.get("phase") or phase_from_cue_path(slots), "mode_unit_attack_event")
    if entity is None or attack is None:
        return None

    return build_frame(
        record,
        "mode_unit_attack_event",
        [
            context_namespace_argument(record),
            entity,
            attack,
            phase,
        ],
    )


def build_media_base_action_state(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """把 media_base 兜底桶里的英雄动作状态接入 skin_action_state frame（批次5b/6a）。

    双条件门禁：sfx_scope=champion_sfx（排除 hud_base 等系统 UI 音，其
    owner_context 可能带伪 champion alias）且 owner_context.kind=champion。
    动作 token 取 cue_path 中 Base 段（大小写两态）之后一段，剥词尾 2D/3D
    后按 skin_action_state 既有动作词表（display_labels.action，单一来源）
    解析；相位依次取 slots.phase、cue 尾段相位门禁（MEDIA_BASE_TAIL_PHASES）、
    已审修饰尾段下的整段音效（overall）。姿势、音层、段位和编号变体必须先
    由拆解层写入 action_qualifiers；仍含表外尾段或表外动作词时不建 frame——
    防错优先，严禁英文 fallback 进文案。
    ``Base`` 分组词不进文案，cue_path 无损留在 semantic.slots。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        skin_action_state frame；不满足门禁或缺动作/相位时返回 None。
    """

    slots = record.semantic_slots
    if slots.get("sfx_scope") != "champion_sfx":
        return None

    owner_context = slots.get("owner_context")
    if (
        not isinstance(owner_context, dict)
        or owner_context.get("kind") != "champion"
        or string_or_none(owner_context.get("alias")) is None
    ):
        return None

    cue = media_base_action_cue(slots)
    if cue is None:
        return None
    token, tail = cue

    action_word, dimension = strip_terminal_dimension(token)
    action_key = action_word.lower()
    active_labels = labels if labels is not None else get_display_labels()
    words = active_labels.action.get(action_key)
    if words is None:
        return None
    if not media_base_tail_is_reviewed(tail, slots):
        return None

    qualifier = champion_action_qualifier_argument(slots)
    phase = phase_argument(slots.get("phase"), "skin_action_state", labels=labels)
    if phase is None:
        phase = media_base_tail_phase(tail, labels=active_labels)
    if phase is None and qualifier is not None:
        phase = media_base_tail_phase((), labels=active_labels)
    champion = champion_argument(record, context)
    if phase is None or champion is None or has_technical_champion_label(champion):
        return None

    zh, en = words
    action = argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={
                "source": "semantic_slots.cue_path",
                "frame_type": "skin_action_state",
                "trigger_family": "media_base",
                "dimension_token": dimension,
            },
            key=action_key,
            token=token,
        )
    )
    return build_frame(
        record,
        "skin_action_state",
        [
            champion,
            skin_argument(record, context),
            action,
            qualifier,
            phase,
        ],
    )


def build_skin_named_sfx_effect(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """把已审阅皮肤命名音频状态接入具体效果 frame。

    这类旧资源可能使用 ``Play_jinx_...`` 而没有 ``sfx`` 壳，因此只依赖
    ``skin_named_state`` 专用 profile 的封闭 state/variant 词表，不以
    ``shell.asset_kind`` 为门禁，也绝不把表外状态或变体直译进中文。
    """

    slots = record.semantic_slots
    state_key = string_or_none(slots.get("state_key"))
    words = SKIN_NAMED_SFX_EFFECTS.get(state_key or "")
    if words is None:
        return None

    component_key, zh_label, en_label = words
    champion = champion_argument(record, context)
    if champion is None or has_technical_champion_label(champion):
        return None

    effect = argument_to_dict(
        TranslationArgument(
            role="effect",
            labels={"zh_CN": f"{zh_label}音效", "en_US": f"{en_label} sfx"},
            confidence="high",
            provenance={"source": "semantic_slots.state_key"},
            key=component_key,
            token=state_key,
        )
    )

    qualifier = None
    raw_variant = string_or_none(slots.get("state_variant"))
    if raw_variant is not None:
        variant_key = raw_variant.lower()
        variant_label = SKIN_NAMED_SFX_QUALIFIERS.get(variant_key)
        if variant_label is None:
            return None
        qualifier = structured_qualifier_argument(
            [{"key": variant_key, "label": variant_label, "raw": raw_variant}],
            confidence="high",
            source="semantic_slots.state_variant",
        )

    phase = phase_argument(slots.get("phase"), "champion_sfx_effect_event")
    return build_frame(
        record,
        "champion_sfx_effect_event",
        [
            champion,
            skin_argument(record, context),
            effect,
            qualifier,
            phase,
        ],
    )


MEDIA_BASE_TAIL_PHASES = frozenset({"leadin", "leadout", "windup", "winddown", "loop"})


def media_base_tail_is_reviewed(
    tail: tuple[object, ...],
    slots: Mapping[str, Any],
) -> bool:
    """确认动作尾段均为可渲染阶段或已结构化修饰语。"""

    qualifier_rows = slots.get("action_qualifiers")
    reviewed = (
        {str(row.get("raw") or "").lower() for row in qualifier_rows if isinstance(row, dict) and row.get("raw")}
        if isinstance(qualifier_rows, list)
        else set()
    )
    for token in tail:
        if not isinstance(token, str):
            return False
        lowered = token.lower()
        if lowered in reviewed or lowered in MEDIA_BASE_TAIL_PHASES:
            continue
        if phase_key(token, "skin_action_state") is not None:
            continue
        return False
    return True


def media_base_action_cue(slots: Mapping[str, Any]) -> tuple[str, tuple[object, ...]] | None:
    """取 media_base cue_path 中 Base 分组段之后的动作 token 与其后尾段。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        ``(动作 token, 尾段元组)``；cue_path 缺失、无 Base 段或其后无 token 时
        返回 None。尾段原样保留（不过滤非字符串），由调用方门禁判定。
    """

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list):
        return None

    for index, segment in enumerate(cue_path):
        if isinstance(segment, str) and segment.lower() == "base":
            candidate = cue_path[index + 1] if index + 1 < len(cue_path) else None
            if not isinstance(candidate, str) or not candidate:
                return None
            return candidate, tuple(cue_path[index + 2 :])

    return None


def media_base_tail_phase(
    tail: tuple[object, ...],
    *,
    labels: DisplayLabels,
) -> dict[str, object] | None:
    """把动作 token 后的 cue 尾段解析为 phase argument（批次6a）。

    尾段中的相位词按 MEDIA_BASE_TAIL_PHASES 门禁；无尾段合成 key=overall
    的整段音效相位。其余片段是否已被结构化由调用方先行校验。

    Args:
        tail: 动作 token 之后的 cue_path 尾段。
        labels: 展示词表。

    Returns:
        phase argument；尾段不在门禁内时返回 None。
    """

    token: str | None
    if not tail:
        key = "overall"
        token = None
    else:
        phase_tokens = [item for item in tail if isinstance(item, str) and item.lower() in MEDIA_BASE_TAIL_PHASES]
        if not phase_tokens:
            return None
        token = phase_tokens[-1]
        key = token.lower()

    zh_label, en_label = labels.phase.get(key, (key, key))
    return argument_to_dict(
        TranslationArgument(
            role="phase",
            labels={"zh_CN": zh_label, "en_US": en_label},
            confidence="high",
            provenance={
                "source": "semantic_slots.cue_path",
                "frame_type": "skin_action_state",
                "trigger_family": "media_base",
            },
            key=key,
            token=token,
        )
    )


def strip_terminal_dimension(token: str) -> tuple[str, str | None]:
    """剥离动作 token 词尾的 2D/3D 维度标记（仅整词尾）。

    Args:
        token: 原始动作 token（Dance3D / death 等）。

    Returns:
        ``(动作词, 维度标记大写或 None)``；无词尾维度时原样返回。
    """

    match = MEDIA_BASE_ACTION_DIMENSION_PATTERN.fullmatch(token)
    if match is None:
        return token, None

    return match.group("action"), match.group("dimension").upper()


def build_shop_voice_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建商店老板语音 frame（批次6d）。

    门禁：owner 为 Shopkeeper 且 slots.shop_action 在措辞表内；subject 按
    champion_labels → SkinNN → counterpart 词表（地区/阵营）→ shop 词表
    （装备/条件）链解析，kind=general（General）视为无特定对象；链全
    miss 时不建 frame 维持 legacy——防错优先，严禁英文 fallback 进文案。
    店主内部代号（Viking/Hermit 等）无官方中文名，无损留 semantic.slots
    不进 arguments（zh label 技术 token 会被 validation 判 error）。
    前缀（2026-07-19 审计修正）：map12 现为三图轮换的 "Random Map"（嚎哭
    深渊/屠夫之桥/灵魂莲华主题共用），「嚎哭深渊」对后两者构成误导，改用
    模式级前缀「极地大乱斗」（queue 450 官方名，对三套主题店主均成立）；
    map id 无损留 semantic.slots.map。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        shop_voice_event frame；不满足门禁或对象不可解析时返回 None。
    """

    slots = record.semantic_slots
    if record.owner.alias != "Shopkeeper":
        return None

    action_key = string_or_none(slots.get("shop_action"))
    if action_key is None:
        return None

    active_labels = labels if labels is not None else get_display_labels()
    words = active_labels.shop_action.get(action_key)
    if words is None:
        return None

    subject = None
    subject_slot = slots.get("shop_subject")
    if isinstance(subject_slot, dict) and subject_slot.get("kind") != "general":
        subject = shop_subject_argument(subject_slot, context, active_labels)
        if subject is None:
            return None

    zh, en = words
    action = argument_to_dict(
        TranslationArgument(
            role="shop_action",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.shop_action"},
            key=action_key,
        )
    )

    map_slot = slots.get("map")
    map_id = map_slot.get("id") if isinstance(map_slot, dict) else None
    mode = None
    if map_id == 12:
        mode = argument_to_dict(
            TranslationArgument(
                role="mode",
                labels={"zh_CN": "极地大乱斗", "en_US": "ARAM"},
                confidence="high",
                provenance={"source": "semantic_slots.map", "map_id": map_id, "queue": 450},
                key="aram",
                omit_when_context_known=("mode",),
            )
        )
    return build_frame(
        record,
        "shop_voice_event",
        [
            mode,
            action,
            subject,
        ],
    )


def shop_subject_argument(
    subject_slot: Mapping[str, Any],
    context: TranslationBuildContext,
    labels: DisplayLabels,
) -> dict[str, object] | None:
    """解析商店老板语音的对象 token（批次6d）。

    解析链与 dialogue partner 同构：champion_labels（Ziggs→吉格斯）→
    SkinNN 皮肤全名（OlafSkin03）→ counterpart 词表（Zaun/Yordle/
    SpiritBlossom 地区阵营）→ shop 词表（装备名/条件触发）。

    Args:
        subject_slot: shop_subject 槽位（unresolved 实体 dict）。
        context: translation 构建上下文。
        labels: 展示词表。

    Returns:
        subject 参数；全链未命中返回 None（不硬造中文）。
    """

    raw = string_or_none(subject_slot.get("raw"))
    if raw is None:
        return None

    zh: str | None = None
    en = raw
    kind = "unresolved"
    source = "shop_subject"

    entry = shop_champion_label_entry(raw, context)
    if isinstance(entry, Mapping):
        zh = string_or_none(entry.get("zh_CN"))
        if zh is not None:
            en = string_or_none(entry.get("en_US")) or raw
            kind, source = "champion", "champion_labels"

    if zh is None:
        zh = partner_skin_zh_label(raw, context)
        if zh is not None:
            kind, source = "skin", "skin_labels"

    if zh is None:
        zh = counterpart_vocab_zh(raw, raw, labels=labels)
        if zh is not None:
            kind, source = "faction_or_region", "display_labels.counterpart"

    if zh is None:
        zh = labels.shop_subject.get(raw)
        if zh is not None:
            kind, source = "shop_vocab", "display_labels.shop.subject"

    if zh is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="shop_subject",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": source, "subject_kind": kind},
            token=raw,
        )
    )


def shop_champion_label_entry(raw: str, context: TranslationBuildContext) -> Mapping[str, object] | None:
    """按 shop 对象 token 查英雄 label 条目（批次6d，别名兜底仅限本链）。

    商店 cue 的英雄 token 存在三类既有形态漂移：大小写（Reksai↔RekSai）、
    英文显示名（Wukong↔alias MonkeyKing）、无空格全名（RenataGlasc↔alias
    Renata）。依次按精确 alias → alias 大小写不敏感 → en_US 显示名紧凑
    匹配解析；其余 frame 的 champion 解析不受影响。

    Args:
        raw: shop_subject 原始 token。
        context: translation 构建上下文。

    Returns:
        champion label 条目；未命中返回 None。
    """

    entry = context.champion_label(raw)
    if isinstance(entry, Mapping):
        return entry

    lowered = raw.lower()
    for alias, candidate in context.champion_labels.items():
        if alias.lower() == lowered and isinstance(candidate, Mapping):
            return candidate

    target = re.sub(r"[^a-z0-9]", "", lowered)
    if not target:
        return None
    for candidate in context.champion_labels.values():
        if not isinstance(candidate, Mapping):
            continue
        en = string_or_none(candidate.get("en_US"))
        if en is not None and re.sub(r"[^a-z0-9]", "", en.lower()) == target:
            return candidate

    return None


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

    action = apply_action_form(
        voice_action_argument(record.profile.primary_family, record.shell.asset_kind, record.semantic_slots),
        record.semantic_slots,
    )
    if action is None:
        return None

    arguments = [
        champion_argument(record, context),
        skin_argument(record, context),
        action,
        companion_argument(record, context),
        phase_argument(
            record.semantic_slots.get("phase") or phase_from_cue_path(record.semantic_slots),
            "voice_event",
        ),
    ]
    # qualifier 始终保留为结构化角色：已核验值参与 pattern signature，
    # 未核验值统一归入低置信待审组。动作标签负责用户可见表达，qualifier
    # 本身不在 voice_event 模板中重复渲染。
    arguments.append(voice_qualifier_argument(record))

    return build_frame(record, "voice_event", arguments)


def companion_argument(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建同行英雄 argument（批次6c，With{Champion}[Near] 前置条件）。

    precondition.kind=nearby_champion 且 counterpart 经 champion_labels 解析
    出中文名才建（宁缺毋错，表外不放英文进 zh 文案）。措辞「{名}在附近」
    （2026-07-19 审计修正：机制为距离触发，官方 wiki 按 nearby/far away
    分类；「在场」易误读为"参与本局"。WithXayah 无 Near 尾缀的合舞机制
    同样要求靠近，统一用「在附近」；proximity 原值无损留 provenance）。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        companion 参数；无前置条件或中文名不可解析时返回 None。
    """

    precondition = record.semantic_slots.get("precondition")
    if not isinstance(precondition, dict) or precondition.get("kind") != "nearby_champion":
        return None

    counterpart = precondition.get("counterpart")
    raw = string_or_none(counterpart.get("raw")) if isinstance(counterpart, dict) else None
    if raw is None:
        return None

    entry = context.champion_label(raw)
    if not isinstance(entry, Mapping):
        return None
    zh = string_or_none(entry.get("zh_CN"))
    if zh is None:
        return None

    en = string_or_none(entry.get("en_US")) or raw
    return argument_to_dict(
        TranslationArgument(
            role="companion",
            labels={"zh_CN": f"{zh}在附近", "en_US": f"{en} nearby"},
            confidence="high",
            provenance={
                "source": "semantic_slots.precondition",
                "proximity": string_or_none(precondition.get("proximity")),
            },
            alias=raw,
            token=string_or_none(precondition.get("raw")),
        )
    )


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
    """构建 voice_event 的结构化 qualifier argument（数据角色，不重复渲染）。

    Args:
        record: 拆解记录。

    Returns:
        qualifier 参数；无可保留修饰时返回 None。
    """

    resolution = resolve_voice_qualifier(record.profile.primary_family, record.semantic_slots)
    if resolution is None:
        return None
    return argument_to_dict(
        TranslationArgument(
            role="qualifier",
            labels={"zh_CN": resolution.zh_cn, "en_US": resolution.en_us},
            confidence=resolution.confidence,
            provenance={"source": resolution.source, "rendered_via": "action"},
            key=resolution.key,
            value=resolution.value,
            token=resolution.token,
            fallback=resolution.fallback,
            fallback_reason="unreviewed_voice_qualifier" if resolution.fallback else None,
        )
    )


def resolve_voice_qualifier(
    primary_family: str,
    slots: Mapping[str, Any],
) -> VoiceQualifierResolution | None:
    """统一解析 voice qualifier，避免 action 与结构化 argument 发生漂移。"""

    raw = string_or_none(slots.get("qualifier"))
    movement_context = slots.get("movement_context")
    spell_state = slots.get("spell_state")

    if primary_family == "move" and isinstance(movement_context, Mapping):
        context_raw = string_or_none(movement_context.get("raw"))
        known = _known_voice_qualifier(context_raw)
        if known is not None:
            return known
        return _unreviewed_voice_qualifier(
            zh_cn="待审移动修饰",
            en_us=f"unreviewed movement qualifier ({context_raw})" if context_raw else "unreviewed movement qualifier",
            token=context_raw,
            source="semantic_slots.movement_context",
        )

    if primary_family == "move" and isinstance(spell_state, Mapping):
        state_raw = string_or_none(spell_state.get("raw"))
        return _unreviewed_voice_qualifier(
            zh_cn="待审技能状态",
            en_us=f"unreviewed spell state ({state_raw})" if state_raw else "unreviewed spell state",
            token=state_raw,
            source="semantic_slots.spell_state",
            value=string_or_none(spell_state.get("spell_key")),
        )

    if primary_family == "move" and voice_move_distance(slots) is not None:
        return VoiceQualifierResolution(
            key="long",
            zh_cn=VOICE_MOVE_LONG_PHRASE[0],
            en_us=VOICE_MOVE_LONG_PHRASE[1],
            token=raw,
            confidence="high",
            fallback=False,
            source="semantic_slots.distance",
            action_mode="replace",
        )

    if raw is None or raw.lower() in GENERIC_ACTION_QUALIFIERS:
        return None

    lowered = raw.lower()
    phrase = VOICE_QUALIFIER_PHRASES.get((primary_family, lowered))
    if phrase is not None:
        return VoiceQualifierResolution(
            key=VOICE_QUALIFIER_CANONICAL_KEYS.get((primary_family, lowered), lowered),
            zh_cn=phrase[0],
            en_us=phrase[1],
            token=raw,
            confidence="high",
            fallback=False,
            source="semantic_slots.qualifier",
            action_mode="replace",
        )

    known = _known_voice_qualifier(raw)
    if known is not None:
        return known

    if has_technical_qualifier_token(raw):
        return _unreviewed_voice_qualifier(
            zh_cn="待审技术修饰",
            en_us=f"unreviewed technical qualifier ({raw})",
            token=raw,
            source="semantic_slots.qualifier",
        )

    return _unreviewed_voice_qualifier(
        zh_cn=f"待审修饰（{raw}）",
        en_us=f"unreviewed qualifier ({raw})",
        token=raw,
        source="semantic_slots.qualifier",
    )


def _known_voice_qualifier(raw: str | None) -> VoiceQualifierResolution | None:
    """从核验词表解析可直接展示的 qualifier。"""

    if raw is None:
        return None
    words = get_display_labels().voice_qualifier.get(raw.lower())
    if words is None:
        return None
    return VoiceQualifierResolution(
        key=raw.lower(),
        zh_cn=words[0],
        en_us=words[1],
        token=raw,
        confidence="high",
        fallback=False,
        source="display_labels.voice_qualifier",
        action_mode="prefix",
    )


def _unreviewed_voice_qualifier(
    *,
    zh_cn: str,
    en_us: str,
    token: str | None,
    source: str,
    value: str | None = None,
) -> VoiceQualifierResolution:
    """构造统一的低置信 qualifier，canonical key 不携带参数值。"""

    return VoiceQualifierResolution(
        key="unreviewed",
        zh_cn=zh_cn,
        en_us=en_us,
        token=token,
        confidence="low",
        fallback=True,
        source=source,
        action_mode="prefix",
        value=value,
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
            companion_argument(record, context),
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
            companion_argument(record, context),
        ],
    )


def build_champion_dialogue_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> dict[str, object] | None:
    """构建英雄对话链 frame（dialogue_chain profile，批次4/6c）。

    partner 为必需角色：chat_partner 缺失时回落 precondition 同行英雄
    （WithSennaNear 前置条件对话链，双人组语境下同行者即对话对象，批次6c）；
    两者都缺则不建 frame，维持 legacy 展示，不硬造对话对象。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。

    Returns:
        champion_dialogue_event frame；缺主体或对话对象时返回 None。
    """

    champion = champion_argument(record, context)
    partner = dialogue_partner_argument(record, context)
    if champion is None or partner is None or has_technical_champion_label(champion):
        return None

    slots = record.semantic_slots
    base_trigger = dialogue_base_trigger_argument(slots.get("base_trigger"))
    append_dialogue_trigger_counterpart(base_trigger, partner, record, context)
    return build_frame(
        record,
        "champion_dialogue_event",
        [
            champion,
            skin_argument(record, context),
            partner,
            base_trigger,
            dialogue_turn_argument(slots.get("chat_turn")),
            dialogue_line_variant_argument(slots),
        ],
    )


def append_dialogue_trigger_counterpart(
    base_trigger: dict[str, object] | None,
    partner: dict[str, object],
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> None:
    """三方对话链把 base_trigger 的第三方英雄并入家族词 label（批次6c）。

    WithLucianNear_FirstEncounter3D01Thresh_Chat02 类：底事件 counterpart
    解析为英雄且既非 owner 也非 partner 时并入（「初遇」→「初遇锤石」）；
    自指（Kayle 链的 FirstEncounter3DKayle01）、partner 同名（Morgana 链）
    与非英雄 token（Yordle/Standard/BasicHit*）保持原词不动。

    Args:
        base_trigger: 已构建的 base_trigger argument（原地修改 labels）。
        partner: 已构建的 partner argument。
        record: 拆解记录。
        context: translation 构建上下文。
    """

    if base_trigger is None:
        return

    provenance = base_trigger.get("provenance")
    counterpart_raw = string_or_none(provenance.get("counterpart_raw")) if isinstance(provenance, dict) else None
    if counterpart_raw is None:
        return

    entry = context.champion_label(counterpart_raw)
    if not isinstance(entry, Mapping):
        return
    zh = string_or_none(entry.get("zh_CN"))
    if zh is None:
        return

    owner_alias = record.owner.alias if record.owner is not None else None
    partner_labels = partner.get("labels")
    partner_en = partner_labels.get("en_US") if isinstance(partner_labels, dict) else None
    partner_names = {
        value.lower()
        for value in (
            string_or_none(partner.get("alias")),
            string_or_none(partner.get("token")),
            string_or_none(partner_en),
        )
        if value is not None
    }
    lowered = counterpart_raw.lower()
    if (owner_alias is not None and lowered == owner_alias.lower()) or lowered in partner_names:
        return

    labels = base_trigger.get("labels")
    if not isinstance(labels, dict):
        return

    zh_label = string_or_none(labels.get("zh_CN"))
    en_label = string_or_none(labels.get("en_US"))
    en = string_or_none(entry.get("en_US")) or counterpart_raw
    if zh_label is not None:
        labels["zh_CN"] = f"{zh_label}{zh}"
    if en_label is not None:
        labels["en_US"] = f"{en_label} {en}"


def dialogue_partner_argument(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建 dialogue partner argument，复用 counterpart 解析链取中文名。

    chat_partner 由 decomposition 以 kind=unresolved_entity 原样带出（未
    enrich），因此按 raw 依次查：champion_labels（raw=Morgana 直接当
    champion alias；RakanNear/XayahFar 剥离 Near/Far 距离限定词后重查）→
    skin_labels（raw 尾缀 SkinNN）→ 外置词表 → 英文 fallback，不硬造中文。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        partner 参数；无 chat_partner 或 kind=general（无特定对象）时返回 None。
    """

    partner = record.semantic_slots.get("chat_partner")
    partner_source = "semantic_slots.chat_partner"
    precondition_proximity = None
    if not isinstance(partner, dict) or not partner:
        # 批次6c：WithSennaNear 前置条件对话链无 chat_partner，双人组语境下
        # 同行英雄即对话对象；proximity 无损进 provenance。
        precondition = record.semantic_slots.get("precondition")
        if isinstance(precondition, dict) and precondition.get("kind") == "nearby_champion":
            counterpart = precondition.get("counterpart")
            if isinstance(counterpart, dict) and counterpart:
                partner = counterpart
                partner_source = "semantic_slots.precondition"
                precondition_proximity = string_or_none(precondition.get("proximity"))
    if not isinstance(partner, dict) or not partner:
        return None

    kind = string_or_none(partner.get("kind"))
    if kind == "general":
        return None

    alias = string_or_none(partner.get("alias"))
    key = string_or_none(partner.get("key"))
    raw = string_or_none(partner.get("raw"))
    english = string_or_none(partner.get("label")) or raw or alias or key
    if english is None:
        return None

    lookup = raw or english
    zh = None
    zh_source = "fallback"
    confidence = "low"
    fallback = False
    fallback_reason = None
    proximity = None

    entry = context.champion_label(lookup)
    if isinstance(entry, Mapping):
        zh = string_or_none(entry.get("zh_CN"))
    if zh is None:
        match = PARTNER_PROXIMITY_PATTERN.fullmatch(lookup)
        if match is not None:
            stripped_entry = context.champion_label(match.group("alias"))
            stripped_zh = string_or_none(stripped_entry.get("zh_CN")) if isinstance(stripped_entry, Mapping) else None
            if stripped_zh is not None:
                entry = stripped_entry
                zh = stripped_zh
                english = match.group("alias")
                proximity = match.group("proximity").lower()
    if zh is not None:
        zh_source, confidence = "champion_labels", "high"
        if isinstance(entry, ChampionLabelEntry) and entry.label_sources.get("zh_CN") == LABEL_SOURCE_SKIN_VOTE:
            fallback = True
            fallback_reason = CHAMPION_ZH_VOTE_FALLBACK_REASON
    else:
        zh = partner_skin_zh_label(lookup, context)
        if zh is not None:
            zh_source, confidence = "skin_labels", "high"
        else:
            zh = counterpart_vocab_zh(lookup, english, labels=labels)
            if zh is not None:
                zh_source, confidence = "display_labels.counterpart", "medium"

    if zh is None:
        fallback = True
        fallback_reason = PARTNER_FALLBACK_REASON

    provenance: dict[str, object] = {
        "source": partner_source,
        "partner_kind": kind,
        "zh_source": zh_source,
    }
    if proximity is None:
        proximity = precondition_proximity
    if proximity is not None:
        provenance["proximity"] = proximity

    return argument_to_dict(
        TranslationArgument(
            role="partner",
            labels={"zh_CN": zh or english, "en_US": english},
            confidence=confidence,
            provenance=provenance,
            alias=alias,
            key=key,
            token=raw,
            fallback=fallback,
            fallback_reason=fallback_reason,
        )
    )


def partner_skin_zh_label(raw: str, context: TranslationBuildContext) -> str | None:
    """按 partner raw 的 `{Champion}SkinNN` 形式查皮肤 zh 全名。

    Args:
        raw: chat_partner 原始 token。
        context: translation 构建上下文。

    Returns:
        皮肤 zh 全名；raw 不含皮肤号或 reference 缺该皮肤时返回 None。
    """

    match = PARTNER_SKIN_ALIAS_PATTERN.fullmatch(raw)
    if match is None:
        return None

    labels = context.skin_label(match.group("champion"), normalize_skin_token(match.group("skin")))
    if not isinstance(labels, Mapping):
        return None

    return string_or_none(labels.get("zh_CN"))


def dialogue_base_trigger_argument(base_trigger: object) -> dict[str, object] | None:
    """构建 dialogue 底事件家族 argument（括号家族词 + 无损数据）。

    counterpart_raw/dimension_token 无损存 provenance，原始 atom 存 token，
    都不进文案；家族不在措辞表时不建 argument（不硬造词条，信息仍无损
    保留在 semantic.slots.base_trigger）。

    Args:
        base_trigger: base_trigger 槽位值。

    Returns:
        base_trigger 参数；无底事件或家族无词条时返回 None。
    """

    if not isinstance(base_trigger, dict):
        return None

    family = string_or_none(base_trigger.get("family"))
    words = DIALOGUE_BASE_TRIGGER_WORDS.get(family or "")
    if family is None or words is None:
        return None

    zh, en = words
    return argument_to_dict(
        TranslationArgument(
            role="base_trigger",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={
                "source": "semantic_slots.base_trigger",
                "counterpart_raw": string_or_none(base_trigger.get("counterpart_raw")),
                "dimension_token": string_or_none(base_trigger.get("dimension_token")),
            },
            key=family,
            token=string_or_none(base_trigger.get("raw")),
        )
    )


def dialogue_turn_argument(chat_turn: object) -> dict[str, object] | None:
    """构建对话轮次 argument（第N句）。

    Args:
        chat_turn: chat_turn 槽位值。

    Returns:
        turn 参数；无轮次时返回 None。
    """

    if isinstance(chat_turn, bool) or not isinstance(chat_turn, int):
        return None

    return argument_to_dict(
        TranslationArgument(
            role="turn",
            labels={"zh_CN": f"第{chat_turn}句", "en_US": f"line {chat_turn}"},
            confidence="high",
            provenance={"source": "semantic_slots.chat_turn"},
            key=str(chat_turn),
            value=str(chat_turn),
        )
    )


def dialogue_line_variant_argument(slots: Mapping[str, Any]) -> dict[str, object] | None:
    """构建底事件台词变体 argument（数据角色，不进渲染模板）。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        line_variant 参数；无变体号时返回 None。
    """

    variant = slots.get("line_variant")
    base_trigger = slots.get("base_trigger")
    if variant is None and isinstance(base_trigger, dict):
        variant = base_trigger.get("line_variant")

    if isinstance(variant, bool) or not isinstance(variant, int):
        return None

    return argument_to_dict(
        TranslationArgument(
            role="line_variant",
            labels={"zh_CN": str(variant), "en_US": str(variant)},
            confidence="high",
            provenance={"source": "semantic_slots.line_variant"},
            key=str(variant),
            value=str(variant),
        )
    )


def build_announcer_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建播报员事件 frame（批次5a）。

    事件 token 取 cue_path 末段（NexusBlitz/Core 等分组前缀无损留在
    slots.cue_path）；剥 On 时机前缀后先整词查事件词表，未命中再按队伍
    尾缀剥一个队伍词重查。表外词干返回 None 维持 legacy「播报员事件」
    展示——严禁英文 fallback frame（已量化会把 fallback_label 从 533 推到
    1400+ 冲垮质量棘轮）。announcer/mode 语音包信息无损在 slots，本批
    不进 arguments（第一批语音包无核验中文名）。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        announcer_event frame；缺事件 token 或词表未命中时返回 None。
    """

    del context  # 播报员域无 champion/skin 主体，不消费构建上下文。
    token = announcer_cue_token(record.semantic_slots)
    if token is None:
        return None

    active_labels = labels if labels is not None else get_display_labels()
    stem = token[2:] if ANNOUNCER_ON_PREFIX_PATTERN.match(token) else token
    event_zh = active_labels.announcer_event.get(stem)
    team = None
    subject = None
    if event_zh is None:
        split = split_announcer_team_suffix(stem, active_labels.announcer_team)
        if split is not None:
            stripped_stem, team = split
            event_zh = active_labels.announcer_event.get(stripped_stem)
            if event_zh is not None:
                stem = stripped_stem
            else:
                # 批次6e：{事件}{You|Hero}{队伍} 三态（语义经字符串表核验），
                # 先剥队伍再剥主体；主体在场时队伍并入主体词（友方英雄），
                # 不再单独出 team argument。
                subject_split = announcer_subject_stem(stripped_stem, team[0], active_labels)
                team = None
                if subject_split is not None:
                    stem, event_zh, subject = subject_split

    if event_zh is None:
        return None

    event_argument = argument_to_dict(
        TranslationArgument(
            role="event",
            labels={"zh_CN": event_zh, "en_US": stem},
            confidence="high",
            provenance={"source": "semantic_slots.cue_path"},
            key=stem,
            token=token,
        )
    )
    team_argument = None
    if team is not None:
        team_key, team_zh, team_raw = team
        team_argument = argument_to_dict(
            TranslationArgument(
                role="team",
                labels={"zh_CN": team_zh, "en_US": team_key},
                confidence="high",
                provenance={"source": "semantic_slots.cue_path"},
                key=team_key,
                token=team_raw,
            )
        )

    subject_argument = None
    if subject is not None:
        subject_key, subject_zh, subject_token = subject
        subject_argument = argument_to_dict(
            TranslationArgument(
                role="subject",
                labels={"zh_CN": subject_zh, "en_US": ANNOUNCER_SUBJECT_EN.get(subject_key, subject_key)},
                confidence="high",
                provenance={"source": "semantic_slots.cue_path"},
                key=subject_key,
                token=subject_token,
            )
        )

    return build_frame(
        record,
        "announcer_event",
        [
            announcer_mode_argument(record.semantic_slots, active_labels),
            event_argument,
            team_argument,
            subject_argument,
        ],
    )


ANNOUNCER_PERSONA_PREFIX_PATTERN = re.compile(r"^(?:Female|Male)\d+")


def announcer_mode_argument(slots: Mapping[str, Any], labels: DisplayLabels) -> dict[str, object] | None:
    """构建播报语音包/模式前缀 argument（批次6e mode role）。

    在 cue_path 事件 token 之前的包名段中查 announcer.mode 词表（先剥
    Female1/Male1 人声前缀，如 Female1Worlds23）；表外包名（RhaastKayn/
    Maverick 等内部代号或未核验名）不出 argument，事件本体照常建 frame，
    包名无损留 cue_path。

    Args:
        slots: 拆解记录 semantic slots。
        labels: 展示词表。

    Returns:
        mode 参数；无包名段或表外时返回 None。
    """

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list) or len(cue_path) < 2:
        return None

    for segment in cue_path[:-1]:
        if not isinstance(segment, str) or not segment:
            continue
        candidate = ANNOUNCER_PERSONA_PREFIX_PATTERN.sub("", segment) or segment
        zh = labels.announcer_mode.get(candidate)
        if zh is not None:
            return argument_to_dict(
                TranslationArgument(
                    role="mode",
                    labels={"zh_CN": zh, "en_US": candidate},
                    confidence="high",
                    provenance={"source": "semantic_slots.cue_path"},
                    key=candidate,
                    token=segment,
                    omit_when_context_known=("mode",),
                )
            )

    return None


ANNOUNCER_SUBJECT_SUFFIX_PATTERN = re.compile(r"^(?P<stem>.+?)(?P<subject>You|Hero)$")
ANNOUNCER_SUBJECT_EN = {
    "You": "you",
    "HeroYourTeam": "ally champion",
    "HeroEnemyTeam": "enemy champion",
    "HeroBlue": "blue side champion",
    "HeroRed": "red side champion",
}


def announcer_subject_stem(
    stripped_stem: str,
    team_key: str,
    labels: DisplayLabels,
) -> tuple[str, str, tuple[str, str, str]] | None:
    """剥 ``{事件}{You|Hero}`` 主体尾缀（批次6e，语义经字符串表核验）。

    门禁：You 仅与 YourTeam 组合（语料无 YouEnemyTeam，出现即防错拒绝）；
    Shutdown 主体方向（终结者还是被终结者）字符串表无法区分，排除待音频
    原声核对；主体词从 announcer.subject 词表取，表外组合不建。

    Args:
        stripped_stem: 已剥队伍尾缀的事件词干（RampageYou 等）。
        team_key: 已剥出的队伍键（YourTeam/EnemyTeam/Blue/Red…）。
        labels: 展示词表。

    Returns:
        ``(事件词干, 事件中文, (主体键, 主体中文, 主体 token))``；门禁不满足
        时返回 None。
    """

    match = ANNOUNCER_SUBJECT_SUFFIX_PATTERN.fullmatch(stripped_stem)
    if match is None:
        return None

    stem = match.group("stem")
    subject_token = match.group("subject")
    if stem == "Shutdown":
        return None

    event_zh = labels.announcer_event.get(stem)
    if event_zh is None:
        return None

    if subject_token == "You":
        if team_key != "YourTeam":
            return None
        subject_key = "You"
    else:
        subject_key = f"Hero{team_key}"

    subject_zh = labels.announcer_subject.get(subject_key)
    if subject_zh is None:
        return None

    return stem, event_zh, (subject_key, subject_zh, subject_token)


def announcer_cue_token(slots: Mapping[str, Any]) -> str | None:
    """取播报员事件 token（cue_path 末段）。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        cue_path 末段字符串；cue_path 缺失、为空或末段非字符串时返回 None。
    """

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list) or not cue_path:
        return None

    return string_or_none(cue_path[-1])


def split_announcer_team_suffix(
    stem: str,
    team_vocab: Mapping[str, str],
) -> tuple[str, tuple[str, str, str]] | None:
    """按队伍词表剥一个大小写不敏感的尾缀。

    Args:
        stem: 已剥 On 前缀的事件词干。
        team_vocab: 队伍尾缀规范写法到中文方位词的映射。

    Returns:
        ``(剥后词干, (队伍规范键, zh, 原始尾缀写法))``；无可剥尾缀或剥后
        词干为空时返回 None。
    """

    if not team_vocab:
        return None

    # 长键优先，避免未来收录互为尾缀的队伍词时误剥短键。
    alternation = "|".join(sorted((re.escape(key) for key in team_vocab), key=len, reverse=True))
    match = re.search(rf"({alternation})$", stem, flags=re.IGNORECASE)
    if match is None or match.start() == 0:
        return None

    raw_suffix = match.group(1)
    canonical = next(key for key in team_vocab if key.lower() == raw_suffix.lower())
    return stem[: match.start()], (canonical, team_vocab[canonical], raw_suffix)


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
    english = string_or_none(counterpart.get("label")) or raw or alias or key
    if english is None:
        return None

    zh = None
    zh_source = "fallback"
    confidence = "low"
    fallback = False
    fallback_reason = None

    if kind == "champion":
        entry = context.champion_label(alias or "")
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
            champion_zh = counterpart_skin_champion_zh_label(counterpart, context)
            if champion_zh is not None:
                zh = f"{champion_zh}（未收录皮肤）"
                zh_source, confidence = "champion_labels_missing_skin", "medium"
                fallback = True
                fallback_reason = "missing_counterpart_skin_label"
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

    labels = context.skin_label(champion_alias, normalize_skin_token(skin_token))
    if not isinstance(labels, Mapping):
        return None

    return string_or_none(labels.get("zh_CN"))


def counterpart_skin_champion_zh_label(
    counterpart: dict[str, object],
    context: TranslationBuildContext,
) -> str | None:
    """皮肤词条缺失时返回其基础英雄中文名，不暴露 SkinNN。"""

    metadata = counterpart.get("metadata")
    if not isinstance(metadata, dict):
        return None
    champion_alias = string_or_none(metadata.get("champion_alias"))
    entry = context.champion_label(champion_alias)
    if not isinstance(entry, Mapping):
        return None
    return string_or_none(entry.get("zh_CN"))


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
    action_key = primary_family
    confidence = "high"
    qualifier = resolve_voice_qualifier(primary_family, active_slots)
    if qualifier is not None:
        if qualifier.action_mode == "replace":
            zh_core, en_core = qualifier.zh_cn, qualifier.en_us
        else:
            zh_core = f"{qualifier.zh_cn}{zh_core}"
            en_core = f"{qualifier.en_us} {en_core}"
        consumed_qualifier = qualifier.token or qualifier.key
        confidence = qualifier.confidence

    if (
        primary_family == "shop"
        and isinstance(consumed_qualifier, str)
        and consumed_qualifier.lower() in {"open", "close"}
    ):
        # Open/Close 是相反的玩家状态；不能继续共用 shop key，否则模式签名
        # 会把进店和离店合并成同一语义模式。
        action_key = f"shop_{consumed_qualifier.lower()}"

    provenance: dict[str, object] = {"source": "profile.primary_family", "frame_type": "voice_event"}
    if consumed_qualifier is not None:
        provenance["qualifier"] = consumed_qualifier

    zh_word, en_word = asset_kind_words(asset_kind)
    return argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": f"{zh_core}{zh_word}", "en_US": f"{en_core} {en_word}"},
            confidence=confidence,
            provenance=provenance,
            key=action_key,
        )
    )


def sfx_self_action_argument(
    primary_family: str,
    slots: Mapping[str, Any],
) -> dict[str, object] | None:
    """构建 SFX 自身动作，并把已审 qualifier 无损并入 canonical action。"""

    base = action_argument(f"{primary_family}_sfx", "champion_action_event")
    if base is None:
        return None

    qualifier = resolve_voice_qualifier(primary_family, slots)
    if qualifier is None:
        return base

    labels = base.get("labels")
    if not isinstance(labels, dict):
        return base

    if qualifier.action_mode == "replace":
        zh_word, en_word = asset_kind_words("sfx")
        labels["zh_CN"] = f"{qualifier.zh_cn}{zh_word}"
        labels["en_US"] = f"{qualifier.en_us} {en_word}"
    else:
        labels["zh_CN"] = f"{qualifier.zh_cn}{string_or_none(labels.get('zh_CN')) or ''}"
        labels["en_US"] = f"{qualifier.en_us} {string_or_none(labels.get('en_US')) or ''}".strip()

    base["key"] = f"{primary_family}_{qualifier.key}_sfx"
    base["confidence"] = qualifier.confidence
    base["fallback"] = qualifier.fallback
    if qualifier.fallback:
        base["fallback_reason"] = "unreviewed_voice_qualifier"
    provenance = base.get("provenance")
    if isinstance(provenance, dict):
        provenance["qualifier"] = qualifier.token or qualifier.key
    return base


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

    if record.owner.alias == "UltBook" or is_akshan_revive_sfx(record):
        return None

    slots = record.semantic_slots
    raw_phase = slots.get("phase") or phase_from_cue_path(slots)
    if (
        isinstance(raw_phase, str)
        and raw_phase.lower() in BUFF_CAST_PHASES
        and record.profile.semantic_profile is SemanticProfile.MAP_OR_SYSTEM
    ):
        # 模式增益借用英雄技能名不等于英雄本人施法；buff_cast 在该域仍需
        # 专门词条核验，不能因 champion SFX 的分段修复被泛化成“技能释放”。
        return None
    slot = ability_slot_argument(resolved_spell_key_for_translation(record))
    component = ability_component_argument(slots)
    rank = spell_rank_argument(slots)
    stage = ability_stage_argument(slots.get("spell_stage"))
    phase = phase_argument(raw_phase, "ability_cast_event")
    if rank is not None and component is not None:
        strip_component_rank_suffix(component)

    # 仅真无相位（slots.phase 为空）时合成；词表外相位（missile_cast/stun 等）
    # 有明确时机但措辞未核验，标「整段音效」语义失准，维持 legacy 待考证。
    if phase is None and slots.get("phase") is None and not prefer_reviewed_component_frame(slots):
        phase = sfx_spell_phase_argument(record)

    if slot is None or phase is None:
        return None

    return build_frame(
        record,
        "ability_cast_event",
        [
            context_namespace_argument(record),
            champion_argument(record, context),
            skin_argument(record, context),
            slot,
            component,
            rank,
            stage,
            phase,
        ],
    )


def build_mode_augment_event(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    *,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建 Arena/ARAM 增益系事件 frame。

    门禁：slots.reference_entity.kind 属增益系三家族
    （augment_system/game_mode/item_system），且 cue_path 为
    ``[家族标记, Code, …]`` 形态（ARAM/Augment/Item 标记之后一段是
    增益/道具 Code）。augment 角色取外置词表（display_labels.augments）
    的客户端官方双语名，词表外 Code 不建 frame 维持 legacy——宁缺毋错，
    严禁英文 fallback 进文案。mode 角色把 owner/cue 的模式标记经已审
    词条解析为中文（Cherry→斗魂竞技场、ARAM→极地大乱斗），表外标记
    合法缺席。相位
    依次取既有 phase 映射、无相位 sfx 合成（cue 尾段相位词或整段音效）；
    已审 ``buff_cast`` 映射为释放，其他词表外相位维持 legacy 待考证。
    借用英雄技能的同域记录通常由 ability builder 先接走；当增益名本身也
    必须保留时，本 frame 追加 champion / ability_slot / qualifier 角色，
    不再只显示英雄槽位而丢掉增益与听者目标。

    Args:
        record: 拆解记录。
        context: translation 构建上下文。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        mode_augment_event frame；不满足门禁、词表外 Code 或相位不可承接时
        返回 None。
    """

    slots = record.semantic_slots
    reference_entity = slots.get("reference_entity")
    kind = reference_entity.get("kind") if isinstance(reference_entity, dict) else None
    if kind not in MODE_AUGMENT_REFERENCE_KINDS:
        return None

    cue = mode_augment_cue(slots)
    if cue is None:
        return None
    marker, code = cue

    active_labels = labels if labels is not None else get_display_labels()
    words = active_labels.augments.get(code)
    if words is None:
        return None

    phase = phase_argument(slots.get("phase"), "mode_augment_event", labels=labels)
    if phase is None:
        if slots.get("phase") is not None:
            return None
        phase = sfx_spell_phase_argument(record, frame_type="mode_augment_event", labels=labels)
        if phase is None:
            return None

    zh, en = words
    augment = argument_to_dict(
        TranslationArgument(
            role="augment",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={
                "source": "display_labels.augments",
                "reference_kind": MODE_AUGMENT_CUE_MARKERS[marker.lower()],
                "code": code,
            },
            key=code,
            token=code,
        )
    )
    champion, ability_slot, qualifier = mode_augment_borrowed_ability_arguments(record, context)
    return build_frame(
        record,
        "mode_augment_event",
        [
            mode_augment_mode_argument(record, labels=context.map_system_entity_labels),
            augment,
            champion,
            ability_slot,
            qualifier,
            phase,
        ],
    )


def mode_augment_borrowed_ability_arguments(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
) -> tuple[dict[str, object] | None, dict[str, object] | None, dict[str, object] | None]:
    """解析增益 cue 中完整的借用英雄技能与听者目标。

    只接受 ``Champion{QWERP}`` 精确段和 ``player|others`` 封闭目标词表；
    普通增益的阶段段不会误命中。目标不存在时允许 qualifier 缺席，原始
    cue_path 仍完整保留。
    """

    cue_path = record.semantic_slots.get("cue_path")
    if not isinstance(cue_path, list) or len(cue_path) < 3:
        return None, None, None
    spell_segment = cue_path[2]
    if not isinstance(spell_segment, str):
        return None, None, None
    match = MODE_AUGMENT_ABILITY_SEGMENT_PATTERN.fullmatch(spell_segment)
    if match is None:
        return None, None, None

    champion = champion_argument(record, context)
    slot = ability_slot_argument(record.semantic_slots.get("spell_key"))
    if champion is None or slot is None:
        return None, None, None
    if str(champion.get("alias") or "").lower() != match.group("owner").lower():
        return None, None, None
    if str(slot.get("key") or "").lower() != match.group("spell_key").lower():
        return None, None, None

    qualifier = None
    for token in cue_path[3:]:
        if not isinstance(token, str):
            continue
        words = MODE_AUGMENT_TARGET_QUALIFIERS.get(token.lower())
        if words is None:
            continue
        zh_label, en_label = words
        qualifier = argument_to_dict(
            TranslationArgument(
                role="qualifier",
                labels={"zh_CN": zh_label, "en_US": en_label},
                confidence="high",
                provenance={"source": "semantic_slots.cue_path", "kind": "listener_target"},
                key=token.lower(),
                token=token,
            )
        )
        break
    return champion, slot, qualifier


def mode_augment_cue(slots: Mapping[str, Any]) -> tuple[str, str] | None:
    """取增益系 cue_path 的家族标记段与其后的增益/道具 Code 段。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        ``(家族标记, Code)``；cue_path 缺失、首段不是 ARAM/Augment/Item
        标记或其后无 Code 段时返回 None。Code 之后的尾段（相位/子组件）
        无损留在 slots，由相位解析与 legacy 展示各自消费。
    """

    cue_path = slots.get("cue_path")
    if not isinstance(cue_path, list) or len(cue_path) < 2:
        return None

    marker = cue_path[0]
    if not isinstance(marker, str) or marker.lower() not in MODE_AUGMENT_CUE_MARKERS:
        return None

    code = cue_path[1]
    if not isinstance(code, str) or not code:
        return None

    return marker, code


def mode_augment_mode_argument(
    record: EventDecompositionRecord,
    *,
    labels: Mapping[str, str] | None = None,
) -> dict[str, object] | None:
    """构建增益系事件的模式前缀 argument（T3.4 头部词条表）。

    候选依次为 owner alias 与 cue 首段家族标记；Augment/Item 是系统/道具
    域标记而非模式名，跳过不查。ARAM 使用同仓库商店语音契约已核验的
    “极地大乱斗”，其他表外标记合法缺席，模式信息无损留在 cue_path。

    Args:
        record: 拆解记录。
        labels: 可选的显式 map/system 实体词条；缺省保留旧 helper 行为。

    Returns:
        mode 参数；候选均不在词条表内时返回 None。
    """

    cue_path = record.semantic_slots.get("cue_path")
    cue_head = cue_path[0] if isinstance(cue_path, list) and cue_path else None
    for candidate in (record.owner.alias, cue_head):
        if not isinstance(candidate, str) or not candidate:
            continue
        if candidate.lower() in MODE_AUGMENT_NON_MODE_MARKERS:
            continue

        reviewed_words = MODE_AUGMENT_MODE_LABELS.get(candidate.lower())
        if reviewed_words is not None:
            zh, en = reviewed_words
            return argument_to_dict(
                TranslationArgument(
                    role="mode",
                    labels={"zh_CN": zh, "en_US": en},
                    confidence="high",
                    provenance={"source": "mode_augment.reviewed_mode_labels"},
                    key=candidate,
                    token=candidate,
                    omit_when_context_known=("mode",),
                )
            )

        zh = map_system_entity_label(candidate, labels)
        if zh is None:
            continue

        source = "owner.alias" if candidate == record.owner.alias else "semantic_slots.cue_path"
        return argument_to_dict(
            TranslationArgument(
                role="mode",
                labels={"zh_CN": zh, "en_US": candidate},
                confidence="high",
                provenance={"source": source},
                key=candidate,
                token=candidate,
                omit_when_context_known=("mode",),
            )
        )

    return None


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

    argument_map = {str(argument["role"]): argument for argument in arguments if argument is not None}
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


def context_namespace_argument(record: EventDecompositionRecord) -> dict[str, object] | None:
    """构建模式/机制上下文 argument；上下文永不替代 champion 主体。"""

    namespace = record.semantic_slots.get("context_namespace")
    if not isinstance(namespace, dict):
        namespace = record.semantic_slots.get("mode_context")
    if not isinstance(namespace, dict):
        return None

    key = string_or_none(namespace.get("key"))
    label = string_or_none(namespace.get("label"))
    if key is None or label is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="context",
            labels={"zh_CN": label, "en_US": key.replace("_", " ")},
            confidence="high",
            provenance={
                "source": string_or_none(namespace.get("source")) or "semantic_slots.context_namespace",
                "kind": string_or_none(namespace.get("kind")) or "mode",
            },
            key=key,
            token=key,
        )
    )


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

    active_context = context or TranslationBuildContext()
    canonical_alias = canonical_champion_alias_for_record(record, active_context, alias)
    if canonical_alias is not None and canonical_alias != alias:
        alias = canonical_alias
        label = canonical_alias
        source = "translation_context.champion_identity"

    entry = active_context.champion_label(alias)
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

    champion_alias = champion_alias_for_skin(record, context)
    token = skin_token_for_record(record)

    if champion_alias is None or token is None:
        return None

    labels = context.skin_label(champion_alias, token)
    fallback = labels is None
    display_labels = locale_labels(labels, token) if labels is not None else {"zh_CN": "未收录皮肤", "en_US": token}
    return argument_to_dict(
        TranslationArgument(
            role="skin",
            labels=display_labels,
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
    zh_cn = "被动技能" if value == "p" else f"{value.upper()}技能"
    en_us = "passive ability" if value == "p" else f"{value.upper()} ability"
    return argument_to_dict(
        TranslationArgument(
            role="ability_slot",
            labels={"zh_CN": zh_cn, "en_US": en_us},
            confidence="high",
            provenance={"source": "semantic_slots.spell_key"},
            key=value,
            value=value,
        )
    )


def ability_stage_argument(
    stage: object,
    *,
    source: str = "semantic_slots.spell_stage",
) -> dict[str, object] | None:
    """构建技能分段 argument；与技能等级 rank 严格分离。"""

    if not isinstance(stage, int) or stage <= 0:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="ability_stage",
            labels={"zh_CN": f"第{stage}段", "en_US": f"stage {stage}"},
            confidence="high",
            provenance={"source": source},
            key=str(stage),
            value=str(stage),
        )
    )


def ability_sequence_argument(sequence: object) -> dict[str, object] | None:
    """构建技能组合 argument；顺序与重复槽位均按原解析结果保留。"""

    if not isinstance(sequence, list) or len(sequence) < 2:
        return None
    normalized = [str(item).lower() for item in sequence]
    if any(item not in {"q", "w", "e", "r", "p"} for item in normalized):
        return None

    label = "+".join(item.upper() for item in normalized)
    argument = argument_to_dict(
        TranslationArgument(
            role="ability_sequence",
            labels={"zh_CN": f"{label}组合技", "en_US": f"{label} ability combo"},
            confidence="medium",
            provenance={"source": "semantic_slots.spell_sequence"},
            key="_".join(normalized),
            value="+".join(normalized),
        )
    )
    argument["sequence"] = normalized
    return argument


def resolved_spell_key_for_translation(record: EventDecompositionRecord) -> object:
    """返回允许进入用户 translation frame 的技能槽位。

    reference/rule 解析出的 ``spell_key`` 优先。owner-prefix hint 允许承接
    ``CaitlynWSnare`` 等大写内部词根和 ``AkaliRb`` 单字母变体，但再次
    拒绝拆走普通 PascalCase 词首后留下的小写长 residual。
    """

    spell_key = record.semantic_slots.get("spell_key")
    if spell_key is not None:
        return spell_key
    hint = record.semantic_slots.get("spell_key_hint")
    if hint is not None:
        residual = record.semantic_slots.get("alias_residual") or record.semantic_slots.get("effect_residual")
        if isinstance(residual, str) and len(residual) > 1 and residual[0].islower():
            return None
        return hint

    return mode_augment_spell_key_hint(record.semantic_slots)


def mode_augment_spell_key_hint(slots: Mapping[str, Any]) -> str | None:
    """从增益 Code 后的显式英雄技能段提取借用技能键位。

    仅接受 owner 前缀、单字母槽位与大写开头的实质组件 residual 三段
    连续出现的形态；普通 PascalCase 词（如 ``UltimateBackup``）不进入
    ability frame。

    Args:
        slots: 拆解记录 semantic slots。

    Returns:
        小写技能键位；cue 结构或技能段不满足强边界时返回 None。
    """

    reference_entity = slots.get("reference_entity")
    kind = reference_entity.get("kind") if isinstance(reference_entity, dict) else None
    cue_path = slots.get("cue_path")
    if (
        kind not in MODE_AUGMENT_REFERENCE_KINDS
        or mode_augment_cue(slots) is None
        or not isinstance(cue_path, list)
        or len(cue_path) < 3
    ):
        return None

    spell_segment = cue_path[2]
    if not isinstance(spell_segment, str):
        return None

    match = MODE_AUGMENT_BORROWED_SPELL_PATTERN.fullmatch(spell_segment)
    if match is None:
        return None

    return match.group("spell_key").lower()


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

    sfx_alias = slots.get("sfx_alias")
    if (
        component_key is None
        and isinstance(sfx_alias, dict)
        and sfx_alias.get("kind") in {"generic_sfx_effect", "champion_spell_component"}
        and string_or_none(slots.get("spell_key")) in {"p", "q", "w", "e", "r"}
    ):
        component_key = string_or_none(sfx_alias.get("component"))
        raw_label = string_or_none(sfx_alias.get("label"))
        source = string_or_none(sfx_alias.get("source")) or "semantic_slots.sfx_alias"
        confidence = string_or_none(sfx_alias.get("confidence")) or confidence

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


def has_reviewed_spell_component(slots: dict[str, object]) -> bool:
    """判断 slots 是否包含人工审阅的英雄技能组件。"""

    return any(
        isinstance(alias, dict) and alias.get("kind") == "champion_spell_component"
        for alias in (slots.get("contextual_alias"), slots.get("sfx_alias"))
    )


def prefer_reviewed_component_frame(slots: dict[str, object]) -> bool:
    """判断无标准相位时是否应保留组件 frame，而非合成“整段音效”。"""

    if not has_reviewed_spell_component(slots):
        return False
    if component_context_phase(slots) is not None or component_context_qualifier_argument(slots) is not None:
        return True

    for alias in (slots.get("contextual_alias"), slots.get("sfx_alias")):
        if (
            isinstance(alias, dict)
            and alias.get("kind") == "champion_spell_component"
            and alias.get("confidence") == "high"
        ):
            return True
    return False


def action_component_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """构建 owner-scoped 皮肤/角色动作组件 argument。"""

    component = slots.get("action_component")
    if not isinstance(component, dict):
        contextual_alias = slots.get("contextual_alias")
        if isinstance(contextual_alias, dict) and contextual_alias.get("kind") == "champion_action_component":
            component = contextual_alias
        else:
            return None

    component_key = string_or_none(component.get("component"))
    zh_label = string_or_none(component.get("label"))
    if component_key is None or zh_label is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="action",
            labels={"zh_CN": zh_label, "en_US": component_key.replace("_", " ").title()},
            confidence=string_or_none(component.get("confidence")) or "medium",
            provenance={"source": string_or_none(component.get("source")) or "semantic_slots.action_component"},
            key=component_key,
            token=string_or_none(component.get("raw")),
        )
    )


def sfx_effect_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """把已审阅 ``generic_sfx_effect`` 别名转换为效果 argument。"""

    alias = slots.get("sfx_alias")
    if not isinstance(alias, dict) or alias.get("kind") != "generic_sfx_effect":
        return None

    component_key = string_or_none(alias.get("component"))
    zh_label = string_or_none(alias.get("label"))
    if component_key is None or zh_label is None:
        return None

    display_label = zh_label if zh_label.endswith(("音效", "音乐")) else f"{zh_label}音效"
    return argument_to_dict(
        TranslationArgument(
            role="effect",
            labels={"zh_CN": display_label, "en_US": f"{component_key.replace('_', ' ').title()} SFX"},
            confidence=string_or_none(alias.get("confidence")) or "medium",
            provenance={"source": string_or_none(alias.get("source")) or "semantic_slots.sfx_alias"},
            key=component_key,
            token=string_or_none(alias.get("raw")),
        )
    )


def sfx_effect_qualifier_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """把规则白名单中的 SFX 中间修饰语建成可渲染 argument。"""

    return reviewed_alias_qualifier_argument(slots, ("sfx_alias",))


def champion_action_qualifier_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """构建英雄动作中的已审别名或分组 cue 修饰语。"""

    alias_qualifier = reviewed_alias_qualifier_argument(
        slots,
        ("action_component", "contextual_alias"),
    )
    if alias_qualifier is not None:
        return alias_qualifier

    qualifier_rows = slots.get("action_qualifiers")
    if not isinstance(qualifier_rows, list) or not qualifier_rows:
        return None
    return structured_qualifier_argument(
        qualifier_rows,
        confidence="high",
        source="semantic_slots.action_qualifiers",
    )


def reviewed_alias_qualifier_argument(
    slots: dict[str, object],
    alias_slot_names: tuple[str, ...],
) -> dict[str, object] | None:
    """把 contextual alias 白名单修饰语建成结构化 qualifier。"""

    alias = None
    qualifier_rows = None
    for slot_name in alias_slot_names:
        candidate = slots.get(slot_name)
        candidate_rows = candidate.get("qualifiers") if isinstance(candidate, dict) else None
        if isinstance(candidate_rows, list) and candidate_rows:
            alias = candidate
            qualifier_rows = candidate_rows
            break
    if not isinstance(alias, dict) or not isinstance(qualifier_rows, list):
        return None

    return structured_qualifier_argument(
        qualifier_rows,
        confidence=string_or_none(alias.get("confidence")) or "medium",
        source=string_or_none(alias.get("source")) or "semantic_slots.sfx_alias",
    )


def structured_qualifier_argument(
    qualifier_rows: list[object],
    *,
    confidence: str,
    source: str,
) -> dict[str, object] | None:
    """把已结构化的 qualifier 行合并为单个可渲染参数。"""

    keys: list[str] = []
    labels: list[str] = []
    tokens: list[str] = []
    for row in qualifier_rows:
        if not isinstance(row, dict):
            continue
        key = string_or_none(row.get("key"))
        label = string_or_none(row.get("label"))
        if key is None or label is None:
            continue
        keys.append(key)
        labels.append(label)
        token = string_or_none(row.get("raw"))
        if token is not None:
            tokens.append(token)

    if not keys:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="qualifier",
            labels={
                "zh_CN": f"（{'、'.join(labels)}）",
                "en_US": f" ({', '.join(labels)})",
            },
            confidence=confidence,
            provenance={"source": source},
            key="+".join(keys),
            token="+".join(tokens) if tokens else None,
        )
    )


def mode_system_sfx_effect_argument(
    mode_entity: Mapping[str, object],
    labels: Mapping[str, str],
) -> dict[str, object] | None:
    """把人工审阅的模式实体名转换为“实体音效” argument。"""

    raw = string_or_none(mode_entity.get("alias")) or string_or_none(mode_entity.get("raw"))
    if raw is None:
        return None

    entity_kind = string_or_none(mode_entity.get("kind"))
    if entity_kind == "mode_boss":
        zh_label = string_or_none(mode_entity.get("label"))
        source = string_or_none(mode_entity.get("source")) or "semantic_slots.mode_entity"
    elif entity_kind == "mode_system":
        zh_label = labels.get(rule_key(raw))
        source = "map_system_entity_labels"
    else:
        return None

    if zh_label is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="effect",
            labels={"zh_CN": f"{zh_label}音效", "en_US": f"{raw} SFX"},
            confidence=string_or_none(mode_entity.get("confidence")) or "medium",
            provenance={"source": source, "entity_kind": entity_kind},
            key=f"{rule_key(raw)}_sfx",
            token=raw,
        )
    )


def mode_unit_argument(mode_entity: Mapping[str, object]) -> dict[str, object] | None:
    """构建人工审阅模式单位实体 argument。"""

    alias = string_or_none(mode_entity.get("alias")) or string_or_none(mode_entity.get("raw"))
    zh_label = string_or_none(mode_entity.get("label"))
    if alias is None or zh_label is None:
        return None

    return argument_to_dict(
        TranslationArgument(
            role="entity",
            labels={"zh_CN": zh_label, "en_US": alias},
            confidence=string_or_none(mode_entity.get("confidence")) or "medium",
            provenance={"source": string_or_none(mode_entity.get("source")) or "semantic_slots.mode_entity"},
            alias=alias,
            token=string_or_none(mode_entity.get("raw")),
            key=string_or_none(mode_entity.get("component")) or rule_key(alias),
        )
    )


def component_context_tokens(slots: dict[str, object]) -> list[str]:
    """返回技能组件别名之后仍有用户语义的上下文 token。"""

    tokens: list[str] = []
    unresolved = slots.get("unresolved_context")
    if isinstance(unresolved, list):
        tokens.extend(item for item in unresolved if isinstance(item, str))
    cue_path = slots.get("cue_path")
    if isinstance(cue_path, list):
        tokens.extend(item for item in cue_path if isinstance(item, str))
    return list(dict.fromkeys(tokens))


def component_context_qualifier_argument(slots: dict[str, object]) -> dict[str, object] | None:
    """把 EmpoweredStart/End 一类复合尾缀中的强化语义建成 qualifier。"""

    for token in component_context_tokens(slots):
        match = COMPONENT_CONTEXT_MODIFIER_PATTERN.fullmatch(token)
        if match is None:
            continue
        qualifier = match.group("qualifier")
        if qualifier is None:
            continue
        key = qualifier.lower()
        words = COMPONENT_QUALIFIER_WORDS.get(key)
        if words is None:
            continue
        return argument_to_dict(
            TranslationArgument(
                role="qualifier",
                labels={"zh_CN": words[0], "en_US": words[1]},
                confidence="high",
                provenance={"source": "semantic_slots.unresolved_context"},
                key=key,
                token=token,
            )
        )
    return None


def component_context_phase(slots: dict[str, object]) -> str | None:
    """从已知组件上下文中恢复开始、结束或循环阶段。"""

    for token in component_context_tokens(slots):
        match = COMPONENT_CONTEXT_MODIFIER_PATTERN.fullmatch(token)
        if match is not None:
            phase = match.group("phase") or match.group("context_phase")
            assert phase is not None
            normalized = phase.lower()
            return COMPONENT_CONTEXT_PHASE_ALIASES.get(normalized, normalized)
        if token.lower() == "loop":
            return "loop"
    return None


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


def attack_argument(
    slots: Mapping[str, Any],
    *,
    fallback_kind: str | None = None,
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """构建普通攻击/暴击 argument，优先展示实体组件与攻击形态。"""

    attack = slots.get("attack_kind")
    if not isinstance(attack, str) and fallback_kind in {"basic_attack", "crit_attack"}:
        attack = fallback_kind
    if not isinstance(attack, str) or not attack:
        return None

    key = attack.lower()
    active_labels = labels if labels is not None else get_display_labels()
    contextual_alias = slots.get("contextual_alias")
    if isinstance(contextual_alias, dict) and contextual_alias.get("kind") == "champion_attack_component":
        component_key = string_or_none(contextual_alias.get("component"))
        component_label = string_or_none(contextual_alias.get("label"))
        if component_key is not None and component_label is not None:
            argument = argument_to_dict(
                TranslationArgument(
                    role="attack",
                    labels={"zh_CN": component_label, "en_US": component_key.replace("_", " ")},
                    confidence=string_or_none(contextual_alias.get("confidence")) or "medium",
                    provenance={"source": contextual_alias.get("source") or "semantic_slots.contextual_alias"},
                    key=component_key,
                )
            )
            add_attack_metadata(argument, slots)
            return argument

    attack_form = string_or_none(slots.get("attack_form"))
    display_key = f"{attack_form}_{key}" if attack_form is not None else key
    words = active_labels.attack.get(display_key) or active_labels.attack.get(key)
    if words is None:
        return None
    zh, en = words
    argument = argument_to_dict(
        TranslationArgument(
            role="attack",
            labels={"zh_CN": zh, "en_US": en},
            confidence="high",
            provenance={"source": "semantic_slots.attack_kind"},
            key=display_key if display_key in active_labels.attack else key,
        )
    )
    add_attack_metadata(argument, slots)
    return argument


def add_attack_metadata(argument: dict[str, object], slots: Mapping[str, Any]) -> None:
    """保留攻击形态/变体元数据，并让变体号进入可见标签。"""

    attack_form = string_or_none(slots.get("attack_form"))
    attack_variant = slots.get("attack_variant")
    related_spell = string_or_none(slots.get("related_spell_key"))
    if attack_form is not None:
        argument["form"] = attack_form
    if isinstance(attack_variant, int) and not isinstance(attack_variant, bool) and attack_variant > 0:
        argument["variant"] = attack_variant
        labels = argument.get("labels")
        if isinstance(labels, dict):
            variant_pattern = rf"(?<!\d){attack_variant}(?!\d)"
            for locale in ("zh_CN", "en_US"):
                label = labels.get(locale)
                if not isinstance(label, str) or re.search(variant_pattern, label):
                    continue
                separator = "" if locale == "zh_CN" else " "
                labels[locale] = f"{label}{separator}{attack_variant}"
    if related_spell is not None:
        argument["related_spell"] = related_spell


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


def apply_action_form(
    argument: dict[str, object] | None,
    slots: dict[str, object],
) -> dict[str, object] | None:
    """把已审阅形态修饰并入动作 canonical key 与中英文标签。"""

    if argument is None:
        return None

    action_form = slots.get("action_form")
    if not isinstance(action_form, dict):
        return argument

    component = string_or_none(action_form.get("component"))
    zh_prefix = string_or_none(action_form.get("label"))
    key = string_or_none(argument.get("key"))
    labels = argument.get("labels")
    if component is None or zh_prefix is None or key is None or not isinstance(labels, dict):
        return argument

    zh_label = string_or_none(labels.get("zh_CN")) or key
    en_label = string_or_none(labels.get("en_US")) or key.replace("_", " ")
    en_prefix = component.replace("_", " ").title()
    labels["zh_CN"] = f"{zh_prefix}{zh_label}"
    labels["en_US"] = f"{en_prefix} {en_label}"
    argument["key"] = f"{component}_{key}"
    argument["form"] = component
    provenance = argument.get("provenance")
    if isinstance(provenance, dict):
        provenance["form_source"] = action_form.get("source") or "semantic_slots.action_form"
    return argument


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
    active_reference_names = reference_map_names if reference_map_names is not None else get_reference_map_names()

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


def champion_alias_for_skin(
    record: EventDecompositionRecord,
    context: TranslationBuildContext | None = None,
) -> str | None:
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
            cleaned = strip_embedded_skin_token(alias)
            if context is not None:
                canonical = canonical_champion_alias_for_record(record, context, cleaned)
                if canonical is not None:
                    return canonical
            return cleaned

    alias = string_or_none(record.semantic_slots.get("subject_hint")) or record.owner.alias
    if alias is None:
        return None

    cleaned = strip_embedded_skin_token(alias)
    if context is not None:
        canonical = canonical_champion_alias_for_record(record, context, cleaned)
        if canonical is not None:
            return canonical
    return cleaned


def canonical_champion_alias_for_record(
    record: EventDecompositionRecord,
    context: TranslationBuildContext,
    candidate_alias: str,
) -> str | None:
    """用精确 alias 或唯一英雄 bank id 还原形态/召唤物 owner 的基础英雄。"""

    exact = context.canonical_champion_alias(candidate_alias)
    if exact is not None:
        return exact

    bank_ids = {
        int(match.group("id"))
        for path in record.source_paths
        if (match := CHAMPION_BANK_SOURCE_PATTERN.search(str(path))) is not None
    }
    aliases = {alias for champion_id in bank_ids if (alias := context.champion_alias_for_id(champion_id)) is not None}
    if len(aliases) == 1:
        return aliases.pop()
    return None


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
    if normalized in {"explode", "explosion"}:
        normalized = "detonate"
    if normalized in BUFF_CAST_PHASES:
        if frame_type in {"champion_attack_event", "mode_unit_attack_event"}:
            return "attack_cast"
        if frame_type in {
            "champion_sfx_effect_event",
            "skin_recall_state",
            "skin_action_state",
            "champion_action_event",
            "voice_event",
            "mode_system_sfx_event",
            "system_sfx_event",
        }:
            return "audio_start"
        if frame_type in {"ability_state_change", "ability_cast_event", "champion_ability_sequence_event"}:
            return "cast"
        if frame_type == "mode_augment_event":
            return "cast"
        return None
    if normalized in STATE_ACTIVATE_PHASES:
        if frame_type in {"ability_state_change", "champion_ability_sequence_event"}:
            return "effect_activate"
        if frame_type in {
            "champion_sfx_effect_event",
            "skin_recall_state",
            "skin_action_state",
            "champion_action_event",
            "voice_event",
            "mode_system_sfx_event",
            "system_sfx_event",
        }:
            return "audio_start"
        if frame_type in {"champion_attack_event", "mode_unit_attack_event"}:
            return "audio_start"
        return "state_activate"
    if normalized in STATE_DEACTIVATE_PHASES:
        if frame_type in {"ability_state_change", "champion_ability_sequence_event"}:
            return "effect_deactivate"
        if frame_type in {
            "champion_sfx_effect_event",
            "skin_recall_state",
            "skin_action_state",
            "champion_action_event",
            "voice_event",
            "mode_system_sfx_event",
            "system_sfx_event",
        }:
            return "audio_end"
        if frame_type in {"champion_attack_event", "mode_unit_attack_event"}:
            return "audio_end"
        return "state_deactivate"
    if frame_type == "champion_ability_sequence_event" and normalized in ABILITY_SEQUENCE_RICH_PHASES:
        return normalized
    if frame_type in {"champion_attack_event", "mode_unit_attack_event"} and normalized == "cast":
        return "attack_cast"
    if frame_type in {
        "champion_sfx_effect_event",
        "skin_recall_state",
        "skin_action_state",
        "champion_action_event",
        "voice_event",
        "mode_system_sfx_event",
        "system_sfx_event",
    }:
        if normalized in {"cast", "start", "on"}:
            return "audio_start"
        if normalized in {"end", "stop", "off"}:
            return "audio_end"
        if normalized in {"leadin", "leadout", "loop", "winddown", "windup"}:
            # 明确的资源尾段比泛化“开始/结束”信息更丰富，直接保留；
            # buffactivate/deactivate 仍走上方 audio_start/audio_end。
            return normalized
    if normalized in {"missile_cast", "missilecast"}:
        return "missile_cast"
    if frame_type == "champion_ability_component_event" and normalized in {"loop", "revive"}:
        return normalized
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
        # 批次5b spell rank 语音：等级档位阶段（1-3 级为实测全集，勿造更高档）。
        "rankone",
        "ranktwo",
        "rankthree",
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
        if normalized == "into":
            return "leadin"
        if normalized in {"explode", "explosion"}:
            return "detonate"
        if normalized in {
            "buffactivate",
            "buffdeactivate",
            "onbuffactivate",
            "onbuffdeactivate",
            "cast",
            "leadin",
            "leadout",
            "loop",
            "winddown",
            "windup",
            "on",
            "off",
        }:
            return normalized

    return None


def sfx_spell_phase_argument(
    record: EventDecompositionRecord,
    *,
    frame_type: str = "ability_cast_event",
    labels: DisplayLabels | None = None,
) -> dict[str, object] | None:
    """为无相位的 sfx 记录合成相位 argument。

    与 media_base 无相位承接同一先例：cue 尾段命中 MEDIA_BASE_TAIL_PHASES
    （引入/引出/起手/收尾/循环）时用该相位词；无可用尾段时合成 key=overall
    的整段音效相位（不硬造具体时机）。仅对 asset_kind=sfx 生效——vo 无相位
    形态（voProcessing 开关等）语义未证实，维持 legacy 不建 frame。

    Args:
        record: 拆解记录。
        frame_type: 调用方 frame 类型，仅记入 provenance。
        labels: 展示词表；为 None 时使用进程内缓存的默认词表（供测试注入替代词表）。

    Returns:
        相位 argument；非 sfx 记录返回 None。
    """

    if record.shell.asset_kind != "sfx":
        return None

    cue_path = record.semantic_slots.get("cue_path")
    tail = cue_path[-1] if isinstance(cue_path, list) and cue_path else None

    if isinstance(tail, str) and tail.lower() in MEDIA_BASE_TAIL_PHASES:
        key = tail.lower()
        token: str | None = tail
    else:
        key = "overall"
        token = None

    active_labels = labels if labels is not None else get_display_labels()
    zh_label, en_label = active_labels.phase.get(key, (key, key))
    return argument_to_dict(
        TranslationArgument(
            role="phase",
            labels={"zh_CN": zh_label, "en_US": en_label},
            confidence="high",
            provenance={
                "source": "semantic_slots.cue_path",
                "frame_type": frame_type,
                "synthesized": "sfx_no_phase",
            },
            key=key,
            token=token,
        )
    )


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
