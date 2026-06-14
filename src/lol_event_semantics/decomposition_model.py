"""事件名结构化拆解数据模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class DecompositionConfidence(StrEnum):
    """拆解结果置信度。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    FAILED = "failed"


class SemanticProfile(StrEnum):
    """事件语义结构类型。"""

    SELF_ACTION = "self_action"
    QUALIFIED_ACTION = "qualified_action"
    INTERACTION = "interaction"
    KILL_OR_SCORE = "kill_or_score"
    SPELL_EVENT = "spell_event"
    DIALOGUE_CHAIN = "dialogue_chain"
    ITEM_OR_SHOP = "item_or_shop"
    MAP_OR_SYSTEM = "map_or_system"
    TECHNICAL_CONTROL = "technical_control"
    UNKNOWN_PROFILE = "unknown_profile"


class UnknownKind(StrEnum):
    """未知项类型。"""

    UNPARSED_FRAGMENT = "unparsed_fragment"
    UNKNOWN_HEAD_TOKEN = "unknown_head_token"
    UNKNOWN_TAIL_TOKEN = "unknown_tail_token"
    AMBIGUOUS_ENTITY = "ambiguous_entity"
    AMBIGUOUS_NUMBER = "ambiguous_number"
    AMBIGUOUS_SPELL_ALIAS = "ambiguous_spell_alias"
    REQUIRES_CHAMPION_MECHANIC_CONTEXT = "requires_champion_mechanic_context"
    REQUIRES_MANUAL_RULE = "requires_manual_rule"
    UNSUPPORTED_DIMENSION_TOKEN = "unsupported_dimension_token"
    NESTED_TRIGGER_UNRESOLVED = "nested_trigger_unresolved"


@dataclass(slots=True)
class EventShell:
    """事件名外壳。"""

    verb: str | None = None
    asset_kind: str | None = None
    body: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventShell:
        """从 JSON 字典还原事件外壳。

        Args:
            data: `EventShell.to_dict()` 生成的字典。

        Returns:
            还原后的事件外壳对象。
        """

        return cls(
            verb=data.get("verb"),
            asset_kind=data.get("asset_kind"),
            body=str(data.get("body") or ""),
        )


@dataclass(slots=True)
class EventOwnerRef:
    """事件归属主体。"""

    kind: str = "unknown"
    id: int | None = None
    alias: str | None = None
    skin_token: str | None = None
    form_token: str | None = None
    raw_owner_token: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventOwnerRef:
        """从 JSON 字典还原事件归属主体。

        Args:
            data: `EventOwnerRef.to_dict()` 生成的字典。

        Returns:
            还原后的事件归属主体。
        """

        return cls(
            kind=str(data.get("kind") or "unknown"),
            id=data.get("id") if isinstance(data.get("id"), int) else None,
            alias=data.get("alias"),
            skin_token=data.get("skin_token"),
            form_token=data.get("form_token"),
            raw_owner_token=data.get("raw_owner_token"),
        )


@dataclass(slots=True)
class LexicalPart:
    """chunk 内部的词法片段。"""

    kind: str
    raw: str
    value: str | int | None = None

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LexicalPart:
        """从 JSON 字典还原词法片段。

        Args:
            data: `LexicalPart.to_dict()` 生成的字典。

        Returns:
            还原后的词法片段。
        """

        value = data.get("value")

        return cls(
            kind=str(data.get("kind") or ""),
            raw=str(data.get("raw") or ""),
            value=value if isinstance(value, str | int) or value is None else str(value),
        )


@dataclass(slots=True)
class LexicalAtom:
    """一个原始 chunk 的拆解结果。"""

    raw: str
    normalized: str
    chunk_index: int
    parts: list[LexicalPart] = field(default_factory=list)
    source: str = "pattern"
    confidence: DecompositionConfidence = DecompositionConfidence.MEDIUM

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        data = asdict(self)
        data["confidence"] = self.confidence.value
        data["parts"] = [part.to_dict() for part in self.parts]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LexicalAtom:
        """从 JSON 字典还原词法原子。

        Args:
            data: `LexicalAtom.to_dict()` 生成的字典。

        Returns:
            还原后的词法原子。
        """

        return cls(
            raw=str(data.get("raw") or ""),
            normalized=str(data.get("normalized") or ""),
            chunk_index=int(data.get("chunk_index") or 0),
            parts=[LexicalPart.from_dict(part) for part in list(data.get("parts") or [])],
            source=str(data.get("source") or "pattern"),
            confidence=DecompositionConfidence(data.get("confidence") or DecompositionConfidence.MEDIUM.value),
        )


@dataclass(slots=True)
class UnknownFragment:
    """无法确定含义的片段。"""

    kind: UnknownKind
    raw: str
    location: str
    blocking_reason: str
    candidate_meanings: list[str] = field(default_factory=list)
    severity: str = "medium"

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        data = asdict(self)
        data["kind"] = self.kind.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UnknownFragment:
        """从 JSON 字典还原未知片段。

        Args:
            data: `UnknownFragment.to_dict()` 生成的字典。

        Returns:
            还原后的未知片段。
        """

        return cls(
            kind=UnknownKind(data.get("kind") or UnknownKind.UNPARSED_FRAGMENT.value),
            raw=str(data.get("raw") or ""),
            location=str(data.get("location") or ""),
            blocking_reason=str(data.get("blocking_reason") or ""),
            candidate_meanings=[str(item) for item in list(data.get("candidate_meanings") or [])],
            severity=str(data.get("severity") or "medium"),
        )


@dataclass(slots=True)
class EventProfile:
    """事件 profile 判定结果。"""

    primary_family: str = "unknown"
    semantic_profile: SemanticProfile = SemanticProfile.UNKNOWN_PROFILE
    requires_context: bool = False
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        data = asdict(self)
        data["semantic_profile"] = self.semantic_profile.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventProfile:
        """从 JSON 字典还原事件 profile。

        Args:
            data: `EventProfile.to_dict()` 生成的字典。

        Returns:
            还原后的事件 profile。
        """

        return cls(
            primary_family=str(data.get("primary_family") or "unknown"),
            semantic_profile=SemanticProfile(data.get("semantic_profile") or SemanticProfile.UNKNOWN_PROFILE.value),
            requires_context=bool(data.get("requires_context")),
            reason=str(data.get("reason") or ""),
        )


@dataclass(slots=True)
class EventDecompositionRecord:
    """单个事件名的拆解记录。"""

    schema_version: str
    game_version: str
    raw_event_name: str
    scope: str = "unknown"
    source_paths: list[str] = field(default_factory=list)
    shell: EventShell = field(default_factory=EventShell)
    owner: EventOwnerRef = field(default_factory=EventOwnerRef)
    profile: EventProfile = field(default_factory=EventProfile)
    lexical_atoms: list[LexicalAtom] = field(default_factory=list)
    semantic_slots: dict[str, Any] = field(default_factory=dict)
    unknowns: list[UnknownFragment] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    confidence: DecompositionConfidence = DecompositionConfidence.LOW
    diagnostics: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """返回 JSON 友好的字典。"""

        return {
            "schema_version": self.schema_version,
            "game_version": self.game_version,
            "raw_event_name": self.raw_event_name,
            "scope": self.scope,
            "source_paths": list(self.source_paths),
            "shell": self.shell.to_dict(),
            "owner": self.owner.to_dict(),
            "profile": self.profile.to_dict(),
            "lexical_atoms": [atom.to_dict() for atom in self.lexical_atoms],
            "semantic_slots": self.semantic_slots,
            "unknowns": [unknown.to_dict() for unknown in self.unknowns],
            "candidates": list(self.candidates),
            "confidence": self.confidence.value,
            "diagnostics": list(self.diagnostics),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EventDecompositionRecord:
        """从 JSON 字典还原拆解记录。

        Args:
            data: `EventDecompositionRecord.to_dict()` 生成的字典。

        Returns:
            还原后的拆解记录。
        """

        return cls(
            schema_version=str(data.get("schema_version") or ""),
            game_version=str(data.get("game_version") or ""),
            raw_event_name=str(data.get("raw_event_name") or ""),
            scope=str(data.get("scope") or "unknown"),
            source_paths=[str(path) for path in list(data.get("source_paths") or [])],
            shell=EventShell.from_dict(dict(data.get("shell") or {})),
            owner=EventOwnerRef.from_dict(dict(data.get("owner") or {})),
            profile=EventProfile.from_dict(dict(data.get("profile") or {})),
            lexical_atoms=[LexicalAtom.from_dict(atom) for atom in list(data.get("lexical_atoms") or [])],
            semantic_slots=dict(data.get("semantic_slots") or {}),
            unknowns=[UnknownFragment.from_dict(unknown) for unknown in list(data.get("unknowns") or [])],
            candidates=[dict(candidate) for candidate in list(data.get("candidates") or [])],
            confidence=DecompositionConfidence(data.get("confidence") or DecompositionConfidence.LOW.value),
            diagnostics=[str(item) for item in list(data.get("diagnostics") or [])],
        )
