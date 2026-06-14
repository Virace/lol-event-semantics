"""事件翻译数据模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class RenderStyle(StrEnum):
    """翻译输出风格。"""

    BRIEF = "brief"
    DEFAULT = "default"
    VERBOSE = "verbose"


class ScopeKind(StrEnum):
    """事件作用域。"""

    AUTO = "auto"
    CHAMPION = "champion"
    MAP = "map"
    UNKNOWN = "unknown"


class Confidence(StrEnum):
    """解析置信度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    FAILED = "failed"


@dataclass(slots=True)
class EventContext:
    """事件解析上下文。"""

    scope: ScopeKind = ScopeKind.AUTO
    owner_id: int | None = None
    source_path: Path | None = None


@dataclass(slots=True)
class Owner:
    """事件主体。"""

    kind: str = "unknown"
    id: int | None = None
    alias: str | None = None
    name: str | None = None
    display_name: str | None = None
    source: str | None = None


@dataclass(slots=True)
class Variant:
    """主体变体信息。"""

    skin_id: int | None = None
    skin_key: str | None = None
    skin_name: str | None = None
    summon_key: str | None = None
    summon_name: str | None = None
    form_name: str | None = None
    chroma_name: str | None = None
    legacy_alias: str | None = None


@dataclass(slots=True)
class Action:
    """动作语义。"""

    token: str | None = None
    key: str | None = None
    name: str | None = None
    hotkey: str | None = None
    category: str | None = None


@dataclass(slots=True)
class Phase:
    """阶段语义。"""

    token: str | None = None
    key: str | None = None
    name: str | None = None


@dataclass(slots=True)
class EventMeaning:
    """结构化事件语义。"""

    raw_name: str
    family: str = "unknown"
    verb: str | None = None
    asset_kind: str | None = None
    scope: ScopeKind = ScopeKind.UNKNOWN
    owner: Owner = field(default_factory=Owner)
    variant: Variant = field(default_factory=Variant)
    action: Action = field(default_factory=Action)
    phase: Phase = field(default_factory=Phase)
    modifiers: list[str] = field(default_factory=list)
    confidence: Confidence = Confidence.FAILED
    notes: list[str] = field(default_factory=list)
