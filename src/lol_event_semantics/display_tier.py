"""normalized 展示分层（Tier）分类器。

北极星：短展示文案（`display.zh_cn_short`）只回答“这个音频什么时候触发”，
质量优先于覆盖率。纯技术事件（普攻/暴击/待机/材质开关/音频总线控制）没有
可展示的语义内容，硬造“<英雄名>音效事件”这类兜底文案违反该原则，因此需要
先给每条记录打 Tier（1/2/3），再让 Tier3 记录退出短文案。

Tier 口径（对齐 .temp/sdd/task-t31-brief.md 校准数字，逐类核对已解析记录后
得出，不是拍脑袋）：

- Tier1：玩家可感知的台词/动作——vo 资产的记录（first_encounter、kill、
  move、dialogue、buy_item、announcer、taunt/joke/laugh/dance 等），以及已被
  translation frame 覆盖的家族（含个别翻译构建暂缺口的同族记录）。
- Tier2：存疑但保留现状——有真实命名语义（技能名、道具名、模式名等）但展示
  价值不确定的 sfx，例如与已覆盖 cast 冗余的 spell sfx。
- Tier3：纯技术——普攻/暴击/待机(idle)/未分类兜底 sfx/音频总线（switch、
  RTPC、事件开关等 audio_control）、以及结构上与 idle 完全同构（仅有
  dimension_token/qualifier/trigger_family，无专属命名内容）的死亡/重生/
  归返守卫/收招类通用状态 sfx、system_sfx 标记的纯 UI 反馈音效。
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
        "champion_status_state",
        "gameplay_phase_control",
        "skin_action_state",
        "skin_audio_transition",
        "skin_idle_state",
        "skin_named_state",
        "skin_recall_state",
        "voice_event",
    }
)

# 家族本身即“纯技术”：普攻/暴击/待机是玩法机制副产物，`sfx` 是未能归类的
# 兜底桶（正是“硬造音效事件”文案的源头），`audio_control`/`switch` 是 Wwise
# 总线、RTPC、参数开关控制，均不携带玩家可感知的专属语义。
TIER3_TECHNICAL_FAMILIES: Final[frozenset[str]] = frozenset(
    {
        "audio_control",
        "basic_attack",
        "crit_attack",
        "idle",
        "sfx",
        "switch",
    }
)

# 这些家族的语义槽位与 idle 完全同构（仅 dimension_token/qualifier/
# trigger_family，没有 reference_entity/effect_head 这类专属命名内容）。
# vo 资产是台词，判 Tier1；sfx 及其余非 vo 资产只是通用状态提示音，判 Tier3。
TIER3_IF_NOT_VO_FAMILIES: Final[frozenset[str]] = frozenset(
    {"death", "homeguard", "recall", "respawn", "winddown"}
)

# Wwise 参数/开关类壳动词：即便资产段恰好被解析成 vo/sfx，动词本身已表明这是
# 控制信号（切换素材、设置参数）而非播放台词，统一按纯技术处理。
TIER3_CONTROL_VERBS: Final[frozenset[str]] = frozenset({"set", "setstate", "switch"})


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

    if primary_family in TIER3_TECHNICAL_FAMILIES:
        return 3

    if primary_family in TIER3_IF_NOT_VO_FAMILIES and asset_kind != "vo":
        return 3

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
