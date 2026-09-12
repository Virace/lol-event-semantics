"""根据显式参考索引归属逻辑实体，来源容器与事件语义分别保留。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

from .decomposition_model import EventDecompositionRecord
from .mode_namespace import split_mode_entity_alias
from .normalized_translation_model import TranslationBuildContext
from .rules import rule_key

SOURCE_PATTERN = re.compile(r"(?:^|[/\\])(champions|maps)[/\\]([0-9]+)\.txt$", re.IGNORECASE)
ITEM_PATTERN = re.compile(r"^(\d+)(?:[A-Za-z][A-Za-z0-9]*)?$")
SKIN_PATTERN = re.compile(r"^([A-Za-z][A-Za-z0-9]*?)skin[0-9]+[A-Za-z0-9]*$", re.IGNORECASE)


@dataclass(frozen=True)
class AttributionContext:
    """一次构建使用的参考身份、名称与人工归属决策。"""

    translation: TranslationBuildContext
    manual: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


def base_champion(alias: str, context: TranslationBuildContext) -> str | None:
    """将模式后缀与 reference alias 或官方名称精确对应，歧义时不建立关系。"""

    split = split_mode_entity_alias(alias)
    if split is None:
        return None
    canonical = context.canonical_champion_alias(split[1])
    if canonical is not None:
        return canonical
    matches = {
        candidate
        for candidate, labels in context.champion_labels.items()
        if split_mode_entity_alias(candidate) is None
        and any(value.casefold() == split[1].casefold() for value in labels.values())
    }
    return next(iter(matches)) if len(matches) == 1 else None


def resolve_entity(record: EventDecompositionRecord, context: AttributionContext) -> dict[str, Any]:
    """返回逻辑实体及证据；同层身份冲突显式降级，绝不以 cue 首段确认实体。

    Args:
        record: 保留原文和来源的拆解记录。
        context: 与翻译、展示共享的本次构建参考上下文。

    Returns:
        带 attribution 的实体；表外数字只保留候选编号，不能确认为装备。
    """

    reference = context.translation
    raw_alias = record.owner.alias or record.owner.raw_owner_token or "unknown"
    evidence: list[dict[str, str]] = []
    sources: dict[str, set[int]] = {"champions": set(), "maps": set()}
    for path in record.source_paths:
        match = SOURCE_PATTERN.search(path)
        if match is not None:
            sources[match[1].lower()].add(int(match[2]))
    for kind, ids in sources.items():
        evidence.extend({"source": "source_asset", "value": f"{kind}:{value}"} for value in sorted(ids))

    candidates = entity_candidates(record)
    exact = {
        alias
        for kind, value, _source in candidates
        if kind == "champion" and (alias := reference.canonical_champion_alias(value)) is not None
    }
    evidence.extend({"source": "reference_champion_alias", "value": value} for value in sorted(exact))

    def result(kind: str, alias: str, basis: str, reason: str | None = None) -> dict[str, Any]:
        """保留原始别名，统一输出可审计归属结构。"""

        attribution: dict[str, Any] = {
            "basis": basis,
            "confidence": "low" if basis == "unresolved" else "high",
            "evidence": list(evidence),
        }
        if reason:
            attribution["reason"] = reason
        entity: dict[str, Any] = {"kind": kind, "alias": alias, "attribution": attribution}
        if raw_alias != alias:
            entity["raw_alias"] = raw_alias
        if kind == "champion" and (base := base_champion(alias, reference)) is not None:
            entity["base_champion"] = {"kind": "champion", "alias": base, "basis": "reference_alias_suffix"}
        return entity

    if sources["champions"]:
        if len(sources["champions"]) != 1:
            return result("unknown", raw_alias, "unresolved", "conflicting_champion_sources")
        source_id = next(iter(sources["champions"]))
        source_alias = reference.champion_ids.get(source_id)
        if source_alias is not None:
            allowed = {source_alias, base_champion(source_alias, reference)}
            if exact - allowed:
                return result("unknown", raw_alias, "unresolved", "source_reference_conflict")
            return result("champion", source_alias, "source_asset")

    if len(exact) > 1:
        return result("unknown", raw_alias, "unresolved", "conflicting_reference_entities")
    if exact:
        return result("champion", next(iter(exact)), "reference_exact")

    # 已审规则可以证明独立模式单位；命名空间自动产生的 mode_entity 不具备该证据。
    units = {
        (kind, alias, source)
        for kind, alias, source in candidates
        if kind in {"mode_unit", "mode_boss"}
        and source
        and source not in {"mode_namespace_cue", "namespace_convention"}
    }
    if len({(kind, alias) for kind, alias, _source in units}) > 1:
        return result("unknown", raw_alias, "unresolved", "conflicting_reference_entities")
    if units:
        kind, alias, source = sorted(units)[0]
        evidence.append({"source": "reviewed_entity_rule", "value": source})
        return result(kind, alias, "reference_exact")

    numeric_alias = record.owner.raw_owner_token or raw_alias
    numeric = ITEM_PATTERN.fullmatch(numeric_alias)
    if numeric is not None:
        item_id = int(numeric[1])
        evidence.append({"source": "raw_owner_numeric_prefix", "value": str(item_id)})
        if item_id in reference.reference_item_names:
            evidence.append({"source": "reference_item_id", "value": str(item_id)})
            return result("item", str(item_id), "reference_item_id")
        entity = result("unknown", str(item_id), "unresolved", "unverified_numeric_owner")
        entity["candidate"] = {"kind": "item", "id": item_id}
        return entity

    map_ids = set(sources["maps"]) - {0}
    map_id = record.semantic_slots.get("map_id")
    raw_map = re.fullmatch(r"map([0-9]+)", raw_alias, re.IGNORECASE)
    if map_id is None and raw_map is not None:
        map_id = int(raw_map[1])
    if not isinstance(map_id, int) and len(map_ids) == 1:
        map_id = next(iter(map_ids))
    if isinstance(map_id, int) and map_id in reference.reference_map_names:
        evidence.append({"source": "reference_map_id", "value": str(map_id)})
        return result("map", str(map_id), "explicit_map")

    # 无来源时仅保留既有已审映射与 reference 可核验的皮肤/附属实体关系。
    for kind, alias, _source in candidates:
        if kind != "champion":
            continue
        manual = context.manual.get(alias.casefold())
        if manual is not None:
            evidence.append({"source": "entity_attribution_override", "value": alias})
            if manual["decision"] == "champion":
                canonical = reference.canonical_champion_alias(manual.get("champion"))
                if canonical:
                    return result("champion", canonical, "reference_exact")
            return result("unknown", alias, "unresolved", "unverified_owner")
        stripped = SKIN_PATTERN.fullmatch(alias)
        if stripped and (canonical := reference.canonical_champion_alias(stripped[1])):
            evidence.append({"source": "reference_skin_owner", "value": canonical})
            return result("champion", canonical, "reference_exact")
        related = {
            canonical
            for canonical in reference.champion_labels
            if len(canonical) < len(alias)
            and (
                (len(canonical) >= 3 and alias.casefold().startswith(canonical.casefold()))
                or (len(canonical) >= 4 and alias.casefold().endswith(canonical.casefold()))
            )
        }
        if len(related) == 1:
            canonical = next(iter(related))
            evidence.append({"source": "candidate_owner_affix", "value": canonical})
            return result("unknown", alias, "unresolved", "unverified_owner_affix")
        if len(related) > 1:
            return result("unknown", alias, "unresolved", "ambiguous_owner_affix")

    labels = reference.map_system_entity_labels
    reviewed_system = rule_key(raw_alias) in labels
    display_labels = reference.display_labels
    if display_labels is not None:
        reviewed_system = reviewed_system or rule_key(raw_alias) in display_labels.system_sfx_context
    namespace = record.semantic_slots.get("context_namespace") or record.semantic_slots.get("mode_context")
    if isinstance(namespace, dict) and namespace.get("key"):
        evidence.append({"source": "semantic_namespace", "value": str(namespace["key"])})
        # namespace 证明模式系统上下文，cue 仍留在语义层，不晋升独立实体。
        return result("system", str(namespace["key"]), "public_system")
    if reviewed_system:
        evidence.append({"source": "reviewed_system_owner", "value": raw_alias})
        return result("system", raw_alias, "public_system")
    reason = "ambiguous_map_sources" if len(map_ids) > 1 else "insufficient_entity_evidence"
    return result("unknown", raw_alias, "unresolved", reason)


def entity_candidates(record: EventDecompositionRecord) -> list[tuple[str, str, str]]:
    """读取结构化实体候选；cue_path 与 mode_namespace_cue 不是身份证据。"""

    candidates: list[tuple[str, str, str]] = []
    for name in ("subject", "mode_actor", "mode_entity"):
        value = record.semantic_slots.get(name)
        if not isinstance(value, dict) or value.get("source") == "mode_namespace_cue":
            continue
        alias = value.get("alias") or value.get("name") or value.get("raw")
        if isinstance(alias, str):
            candidates.append((str(value.get("kind") or "unknown"), alias, str(value.get("source") or "")))
    hint = record.semantic_slots.get("subject_hint")
    if isinstance(hint, str):
        candidates.append(("champion", hint, "subject_hint"))
    if record.owner.alias:
        candidates.append((record.owner.kind, record.owner.alias, "owner"))
    return candidates
