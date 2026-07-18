"""normalized translation 数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


TRANSLATION_SCHEMA_VERSION = 1
FRAME_VERSION = 1
SUPPORTED_FRAME_TYPES = (
    "ability_state_change",
    "skin_recall_state",
    "skin_idle_state",
    "skin_action_state",
    "gameplay_phase_control",
    "voice_event",
    "ability_cast_event",
    "kill_or_destroy_event",
    "champion_interaction_cue",
    "champion_dialogue_event",
    "announcer_event",
)
REQUIRED_FRAME_ROLES: dict[str, tuple[str, ...]] = {
    "ability_state_change": ("phase",),
    "skin_recall_state": ("champion", "skin", "action", "phase"),
    "skin_idle_state": ("champion", "skin"),
    "skin_action_state": ("champion", "skin", "action", "phase"),
    "gameplay_phase_control": ("map", "gameplay_event", "phase"),
    "voice_event": ("action",),
    "ability_cast_event": ("ability_slot", "phase"),
    "kill_or_destroy_event": ("champion", "trigger"),
    "champion_interaction_cue": ("champion", "trigger"),
    "champion_dialogue_event": ("champion", "partner"),
    "announcer_event": ("event",),
}


@dataclass(slots=True)
class TranslationBuildContext:
    """translation 构建上下文。

    Attributes:
        default_locale: 默认输出语言。
        fallback_locale: 默认语言缺失时使用的回退语言。
        champion_labels: 英雄 alias 到多语言 label 的映射。
        skin_labels: `(champion_alias, skin_token)` 到多语言 label 的映射。
    """

    default_locale: str = "zh_CN"
    fallback_locale: str = "en_US"
    champion_labels: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    skin_labels: Mapping[tuple[str, str], Mapping[str, str]] = field(default_factory=dict)


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
