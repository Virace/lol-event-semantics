"""normalized 展示分层（Tier）分类器。

北极星：短展示文案（`display.zh_cn_short`）只回答“这个音频什么时候触发”，
质量优先于覆盖率。普通攻击、暴击、待机和回城等都有明确玩家触发语义；只有
材质开关、音频总线控制等纯工程事件不生成短文案。硬造“<英雄名>音效事件”
这类兜底文案违反该原则，因此需要
先给每条记录打 Tier（1/2/3），再让 Tier3 记录退出短文案。

Tier 口径（对齐 .temp/sdd/task-t31-brief.md 校准数字，逐类核对已解析记录后
得出，不是拍脑袋）：

- Tier1：玩家可感知的台词/动作——vo 资产，以及普通攻击/暴击、待机、死亡、
  重生、回城、家园卫士、收招等明确 SFX 动作；已被 translation frame 覆盖的
  同族记录即使个别条目暂缺 frame 也不得静默降级。
- Tier2：存疑但保留现状——有真实命名语义（技能名、道具名、模式名等）但展示
  价值不确定的 sfx，例如与已覆盖 cast 冗余的 spell sfx。
- Tier3：纯技术——未分类兜底 sfx、音频总线（switch、RTPC、事件开关等
  audio_control）里的**非 vo**实例、system_sfx 标记的纯 UI 反馈音效、Wwise
  参数/开关控制壳动词（即便资产段恰好带 vo 标记）。
"""

from __future__ import annotations

from typing import Final

from .decomposition_model import EventDecompositionRecord

# 已由 translation frame 承接的高价值家族：即便个别记录因翻译构建缺口暂时
# 没有 translation 对象（如 champion_status_state 部分记录），也按同族对待，
# 不能让单条记录的构建缺口连带丢失短文案。
TIER1_COVERED_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "ability_cast_event",
        "ability_state_change",
        "basic_attack",
        "champion_status_state",
        "crit_attack",
        "death",
        "gameplay_phase_control",
        "homeguard",
        "idle",
        "recall",
        "respawn",
        "skin_action_state",
        "skin_audio_transition",
        "skin_idle_state",
        "skin_named_state",
        "skin_recall_state",
        "voice_event",
        "winddown",
    }
)

# `sfx` 是未能归类的兜底桶（正是“硬造音效事件”文案的源头），
# `audio_control`/`switch` 是 Wwise 总线、RTPC、参数开关控制。这些家族里混有
# 少量 vo 资产真实台词，vo 资产仍按台词判 Tier1。
TIER3_TECHNICAL_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "audio_control",
        "sfx",
        "switch",
    }
)

# Wwise 参数/开关类壳动词：即便资产段恰好被解析成 vo/sfx，动词本身已表明这是
# 控制信号（切换素材、设置参数）而非播放台词，统一按纯技术处理。
TIER3_CONTROL_VERBS: Final[frozenset[str]] = frozenset({"set", "setstate", "switch"})

# 地图纯氛围发声器的 cue 类别词（cue_path 第二段）：大龙坑锁链、泉水树木等
# 环境循环声，无玩家事件语义（T3.5 处置决策，20_DECISIONS 2026-07-17）。
# 塔摧毁等有玩家语义的 map 家族记录 cue_path 第二段是具体对象名（如
# ChaosTurret），不在本集合内；语义存疑的子类（SRX/Crepe 等）保守保留 Tier2。
MAP_AMBIENCE_CUE_TOKENS: Final[frozenset[str]] = frozenset({"ambience", "bloom", "emitter"})


def is_akshan_revive_sfx(record: EventDecompositionRecord) -> bool:
    """判断记录是否属于已核验的 Akshan 复活音效簇。

    Args:
        record: 已完成语义拆解的事件记录。

    Returns:
        owner、effect 与 phase 均精确命中当前已核验簇时返回 True。
    """

    slots = record.semantic_slots
    verb = record.shell.verb
    return (
        record.profile.primary_family == "sfx"
        and record.shell.asset_kind == "sfx"
        and isinstance(verb, str)
        and verb.lower() in {"mute", "play"}
        and record.owner.alias == "Akshan"
        and slots.get("sfx_scope") == "champion_sfx"
        and slots.get("phase") in {"buff_cast", "revive"}
        and slots.get("effect_head") == "AkshanWRevive"
        and slots.get("effect_base") == "WRevive"
        and slots.get("effect_residual") == "Revive"
    )


def classify_display_tier(record: EventDecompositionRecord, *, has_translation: bool) -> int:
    """按语义槽位/家族/资产类型给 normalized 记录打展示分层。

    分类依据全部来自记录已解析出的字段（profile.primary_family、
    shell.asset_kind/verb、semantic_slots），不解析原始事件名字符串；
    边界拿不准时返回 2（保留短文案现状），不误杀潜在有价值的台词。

    Args:
        record: 已完成 profile/reference enrichment 的拆解记录。
        has_translation: 该记录是否已生成 translation frame。

    Returns:
        展示分层：1（高价值台词/动作）、2（存疑，保留短文案）、
        3（纯技术，短文案退出）。
    """

    primary_family = record.profile.primary_family
    asset_kind = record.shell.asset_kind
    verb = record.shell.verb
    slots = record.semantic_slots

    if has_translation or primary_family in TIER1_COVERED_FAMILIES:
        return 1

    # Akshan W 的复活音效已由产品语义核验；仅凭通用 spell_key_hint 不能放行，
    # 否则 RenektonExecute/JayceRangedAttack 等普通词首会被误展示为 E/R 技能。
    if is_akshan_revive_sfx(record):
        return 2

    if primary_family in TIER3_TECHNICAL_FAMILIES and asset_kind != "vo":
        return 3

    # 地图环境发声器：map 家族里 cue 类别为 Emitter/Bloom/Ambience 的纯氛围
    # 循环声。vo 例外与其他家族一致（当前语料该子集无 vo 实例，防御未来语料）。
    if primary_family == "map" and asset_kind != "vo":
        cue_path = slots.get("cue_path")
        if (
            isinstance(cue_path, (list, tuple))
            and len(cue_path) > 1
            and str(cue_path[1]).lower() in MAP_AMBIENCE_CUE_TOKENS
        ):
            return 3

    # 放在两个“非 vo”判定之后：vo 资产命中控制类壳动词（如 Yuumi 附体状态
    # 切换事件 Switch_vo_Yuumi_Base_Attached）仍是控制信号而非台词，即使
    # 资产段被解析成 vo 也不应该被上面的例外放行到 Tier1。
    if isinstance(verb, str) and verb.lower() in TIER3_CONTROL_VERBS:
        return 3

    # system_sfx：reference 匹配阶段自己打上的“非英雄专属、系统/UI 反馈音”标记
    # （如商店按钮点击、菜单开合），与家族无关，凡命中即技术噪声。
    if slots.get("sfx_scope") == "system_sfx":
        return 3

    if asset_kind == "mus":
        return 3

    # media_base 是“Base 皮肤形态”的动作兜底桶，混杂了未能归类到
    # skin_action_state 的 dance/joke/death/taunt/laugh/recall（champion_sfx，
    # 与已覆盖同族记录同价值）和纯 UI 反馈音（已被上面的 system_sfx 分支拦截）。
    if primary_family == "media_base" and slots.get("sfx_scope") == "champion_sfx":
        return 1

    if asset_kind == "vo":
        return 1

    return 2
