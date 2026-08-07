"""normalized translation 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from .display_labels import DisplayLabels


TRANSLATION_SCHEMA_VERSION = 1
FRAME_VERSION = 1


@dataclass(frozen=True, slots=True)
class FrameSpec:
    """translation frame 注册项。

    Attributes:
        required_roles: frame 必需的语义角色。
        templates: locale 到 mode 到 role 顺序的渲染模板。未显式定义的
            mode 由 renderer 查找时继承 default；en_US 与 zh_CN 逐字相同
            的 frame 不再双写，缺失 locale 回落 zh_CN（见
            normalized_translation_renderer.roles_for_template）。
    """

    required_roles: tuple[str, ...]
    templates: Mapping[str, Mapping[str, tuple[str, ...]]]


# frame 单一注册表：新增 frame 只需在此加一条，
# required_roles 校验（normalized_validation）与模板渲染
# （normalized_translation_renderer）都直接读这里，避免三处散落配置漂移。
#
# 模板维度收敛：debug/audit 与 default 逐字相同，靠 renderer 的
# mode 继承（未显式定义回落 default）不再双写；en_US 与 zh_CN 逐字相同的
# frame 只写 zh_CN（缺失 locale 回落 zh_CN），仅角色集或语序有差异的
# frame（kill_or_destroy_event / champion_interaction_cue /
# announcer_event）保留显式 en_US。
FRAME_REGISTRY: dict[str, FrameSpec] = {
    "ability_state_change": FrameSpec(
        required_roles=("phase",),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "ability_slot", "ability_component", "rank", "phase"),
                "main": ("ability_slot", "ability_component", "rank", "phase"),
            },
        },
    ),
    "skin_recall_state": FrameSpec(
        required_roles=("champion", "skin", "action", "phase"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "action", "phase"),
                "main": ("action", "phase"),
            },
        },
    ),
    "skin_idle_state": FrameSpec(
        required_roles=("champion", "skin"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "variant", "ability_slot", "ability_component", "phase"),
                "main": ("variant", "ability_slot", "ability_component", "phase"),
            },
        },
    ),
    # 批次5b：media_base 路由接入后，基础皮肤形态（Play_sfx_Galio_Base_… 无
    # SkinNN token）合法缺 skin 角色，skin 不再是必需角色；带皮肤号的记录仍
    # 照常产出 skin argument（黄金样例与渲染回归守护）。
    "skin_action_state": FrameSpec(
        required_roles=("champion", "action", "phase"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "action", "phase"),
                "main": ("action", "phase"),
            },
        },
    ),
    "gameplay_phase_control": FrameSpec(
        required_roles=("map", "gameplay_event", "phase"),
        templates={
            "zh_CN": {
                "default": ("map", "gameplay_event", "phase"),
                "main": ("gameplay_event", "phase"),
            },
        },
    ),
    "voice_event": FrameSpec(
        required_roles=("action",),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "action", "companion"),
                "main": ("action", "companion"),
            },
        },
    ),
    "ability_cast_event": FrameSpec(
        required_roles=("ability_slot", "phase"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "ability_slot", "phase"),
                "main": ("ability_slot", "phase"),
            },
        },
    ),
    # en_US 无 counter 角色（变体号只进中文措辞），保留显式英文模板。
    "kill_or_destroy_event": FrameSpec(
        required_roles=("champion", "trigger"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "kill_method", "trigger", "counterpart", "counter", "companion"),
                "main": ("kill_method", "trigger", "counterpart", "counter", "companion"),
            },
            "en_US": {
                "default": ("champion", "skin", "kill_method", "trigger", "counterpart", "companion"),
                "main": ("kill_method", "trigger", "counterpart", "companion"),
            },
        },
    ),
    # en_US 无 relation 角色（在场关系词只进中文措辞），保留显式英文模板。
    "champion_interaction_cue": FrameSpec(
        required_roles=("champion", "trigger"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "trigger", "relation", "counterpart", "companion"),
                "main": ("trigger", "relation", "counterpart", "companion"),
            },
            "en_US": {
                "default": ("champion", "skin", "trigger", "counterpart", "companion"),
                "main": ("trigger", "counterpart", "companion"),
            },
        },
    ),
    "champion_dialogue_event": FrameSpec(
        required_roles=("champion", "partner"),
        templates={
            "zh_CN": {
                "default": ("champion", "skin", "partner", "base_trigger", "turn"),
                "main": ("partner", "base_trigger", "turn"),
            },
        },
    ),
    # en_US 语序不同（event 前 team 后），保留显式英文模板。
    "announcer_event": FrameSpec(
        required_roles=("event",),
        templates={
            "zh_CN": {
                "default": ("mode", "subject", "team", "event"),
                "main": ("subject", "team", "event"),
            },
            "en_US": {
                "default": ("mode", "subject", "event", "team"),
                "main": ("subject", "event", "team"),
            },
        },
    ),
    # 批次6d：subject 合法缺席（Browsing/Ambient/General 无特定对象）。
    "shop_voice_event": FrameSpec(
        required_roles=("shop_action",),
        templates={
            "zh_CN": {
                "default": ("mode", "shop_action", "shop_subject"),
                "main": ("shop_action", "shop_subject"),
            },
        },
    ),
    # Arena/ARAM 增益系事件：augment 为词表命中的增益/道具官方双语名（必需）；
    # mode（斗魂竞技场等模式前缀）走 T3.4 头部词条表，表外标记（ARAM 暂无
    # 词条）合法缺席；phase 由既有相位映射或无相位 sfx 合成承接，词表外
    # 相位的记录不建 frame（见 build_mode_augment_event）。
    "mode_augment_event": FrameSpec(
        required_roles=("augment",),
        templates={
            "zh_CN": {
                "default": ("mode", "augment", "phase"),
                "main": ("augment", "phase"),
            },
        },
    ),
}

# 兼容视图：从注册表派生，供仍按旧名字 import 的调用方使用；
# 新代码应直接读 FRAME_REGISTRY。
SUPPORTED_FRAME_TYPES = tuple(FRAME_REGISTRY)
REQUIRED_FRAME_ROLES: dict[str, tuple[str, ...]] = {
    frame_type: spec.required_roles for frame_type, spec in FRAME_REGISTRY.items()
}


@dataclass(slots=True)
class TranslationBuildContext:
    """translation 构建上下文。

    Attributes:
        default_locale: 默认输出语言。
        fallback_locale: 默认语言缺失时使用的回退语言。
        champion_labels: 英雄 alias 到多语言 label 的映射。
        skin_labels: `(champion_alias, skin_token)` 到多语言 label 的映射。
        display_labels: 从显式 rules 根加载的展示词表。
        reference_map_names: 从显式 reference 根加载的地图英文名索引。
        reference_item_names: 从显式 reference 根加载的装备双语名索引。
        map_system_entity_labels: 从显式 rules/overrides 根加载的系统实体词条。
    """

    default_locale: str = "zh_CN"
    fallback_locale: str = "en_US"
    champion_labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    skin_labels: Mapping[tuple[str, str], Mapping[str, str]] = field(default_factory=dict)
    display_labels: DisplayLabels | None = None
    reference_map_names: Mapping[int, str] = field(default_factory=dict)
    reference_item_names: Mapping[int, Mapping[str, str]] = field(default_factory=dict)
    map_system_entity_labels: Mapping[str, str] = field(default_factory=dict)
    # 大小写折叠索引：事件名 alias 与 reference alias 存在既有拼写分叉
    # （BelVeth/Belveth、LeBlanc/Leblanc、RekSai/Reksai、Orianna/Oriana…），
    # 两侧各是自身来源的事实，不改数据、由查表兼容。惰性构建后缓存。
    _champion_index: dict[str, Mapping[str, str]] | None = field(default=None, init=False, repr=False)
    _skin_index: dict[tuple[str, str], Mapping[str, str]] | None = field(default=None, init=False, repr=False)

    def champion_label(self, alias: object) -> Mapping[str, str] | None:
        """按英雄 alias 取 label，精确未命中时折叠大小写重查。"""

        if not isinstance(alias, str) or not alias:
            return None
        exact = self.champion_labels.get(alias)
        if exact is not None:
            return exact
        if self._champion_index is None:
            self._champion_index = {key.lower(): value for key, value in self.champion_labels.items()}
        return self._champion_index.get(alias.lower())

    def skin_label(self, champion_alias: object, skin_token: object) -> Mapping[str, str] | None:
        """按 (英雄 alias, 皮肤 token) 取 label，精确未命中时折叠大小写重查。"""

        if not isinstance(champion_alias, str) or not isinstance(skin_token, str):
            return None
        exact = self.skin_labels.get((champion_alias, skin_token))
        if exact is not None:
            return exact
        if self._skin_index is None:
            self._skin_index = {
                (champion.lower(), token.lower()): value
                for (champion, token), value in self.skin_labels.items()
            }
        return self._skin_index.get((champion_alias.lower(), skin_token.lower()))


@dataclass(frozen=True, slots=True)
class TranslationArgument:
    """translation frame 参数。

    Attributes:
        role: 参数语义角色。
        labels: 多语言展示标签。
        confidence: 参数置信度。
        provenance: 参数来源信息。
        key: 规范化语义键。
        value: 规范化语义值。
        alias: 实体 alias。
        token: 原始或规范化 token。
        fallback: 是否使用 fallback label。
        fallback_reason: fallback 原因。
        omit_when_context_known: 哪些页面上下文已知时可省略。
    """

    role: str
    labels: dict[str, str]
    confidence: str
    provenance: dict[str, object]
    key: str | None = None
    value: str | None = None
    alias: str | None = None
    token: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None
    omit_when_context_known: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RenderOptions:
    """translation 渲染参数。

    Attributes:
        locale: 目标语言。
        mode: 渲染模式，支持 default、main、debug、audit。
        known_context: 调用方已知的页面上下文角色。
        fallback: label 缺失时使用的回退策略。
    """

    locale: str = "zh_CN"
    mode: str = "default"
    known_context: frozenset[str] = field(default_factory=frozenset)
    fallback: str = "label"


@dataclass(frozen=True, slots=True)
class RenderResult:
    """translation 渲染结果。

    Attributes:
        text: 渲染后的用户文案。
        frame_type: translation frame 类型。
        confidence: frame 置信度。
        used_roles: 实际参与渲染的角色。
        omitted_roles: 因模式或上下文省略的角色。
        diagnostics: 渲染诊断信息。
    """

    text: str
    frame_type: str | None
    confidence: str
    used_roles: tuple[str, ...] = ()
    omitted_roles: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


def argument_to_dict(argument: TranslationArgument) -> dict[str, object]:
    """把 TranslationArgument 转换为 JSON 友好的 dict。

    Args:
        argument: translation 参数对象。

    Returns:
        去掉空值后的稳定字典。
    """

    row: dict[str, object] = {
        "role": argument.role,
        "labels": dict(argument.labels),
        "confidence": argument.confidence,
        "provenance": dict(argument.provenance),
        "fallback": argument.fallback,
    }
    optional_values = {
        "key": argument.key,
        "value": argument.value,
        "alias": argument.alias,
        "token": argument.token,
        "fallback_reason": argument.fallback_reason,
    }
    for key, value in optional_values.items():
        if value is not None:
            row[key] = value

    if argument.omit_when_context_known:
        row["omit_when_context_known"] = list(argument.omit_when_context_known)

    return row
