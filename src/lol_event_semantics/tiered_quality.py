"""Stable 绝对门禁、accepted corpus 与支持等级差异。"""

from __future__ import annotations

import json
import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .normalized_lookup import iter_normalized_rows
from .normalized_translation_model import FRAME_REGISTRY, RenderOptions
from .normalized_translation_renderer import render_translation

__all__ = [
    "GATE_VERSION",
    "QUALITY_SCHEMA_VERSION",
    "StableScope",
    "V1_STABLE_SCOPE",
    "build_gate_report",
    "build_quality_summary",
    "build_support_diff",
    "load_accepted_corpus",
    "stable_scope_contract_passes",
]

QUALITY_SCHEMA_VERSION = 1
GATE_VERSION = "stable-product-gates.v1"
CORPUS_SCHEMA_VERSION = 1
UNTRUSTED_CONFIDENCE = {"low", "failed", "unknown"}
ENGLISH_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
ABILITY_KEYS = frozenset({"P", "Q", "W", "E", "R"})
ABBREVIATIONS = frozenset({"AD", "AOE", "AP", "CC", "DPS", "HP", "MP"})
TECHNICAL_TOKENS = frozenset({"RTPC", "SFX", "UI", "VO"})
PROPER_NOUN_ROLES = frozenset(
    {"champion", "companion", "counterpart", "item", "partner", "shop_subject", "skin", "subject"}
)


@dataclass(frozen=True)
class StableScope:
    """一次已批准 Stable 决策的不可变绝对范围。"""

    decision_id: str
    pattern_count: int
    record_count: int
    pattern_ids_digest: str


V1_STABLE_SCOPE = StableScope(
    decision_id="v1-semantic-fix-2026-08-11",
    pattern_count=91,
    record_count=4127,
    pattern_ids_digest="978aa170afcdfab84833a88fc14efcc32bab63f0de85cba6e2851d886971e9e7",
)


def load_accepted_corpus(path: Path) -> dict[str, Any]:
    """读取并校验版本化 accepted corpus。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != CORPUS_SCHEMA_VERSION:
        raise ValueError(f"不支持的 accepted corpus schema: {path}")
    for field in ("corpus_id", "decision_id", "selection_policy", "review_status"):
        if not isinstance(payload.get(field), str) or not payload[field]:
            raise ValueError(f"accepted corpus 缺少非空字段: {field}")
    entries = payload.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("accepted corpus entries 必须是非空数组")
    ids: list[str] = []
    names: list[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"accepted corpus entry 必须是 object: {index}")
        pattern_id = _required_text(entry, "pattern_id", f"entries[{index}]")
        raw = _required_text(entry, "raw_event_name", f"entries[{index}]")
        _required_text(entry, "zh_cn_default", f"entries[{index}]")
        _required_text(entry, "zh_cn_main", f"entries[{index}]")
        if entry.get("decision_id") != payload["decision_id"]:
            raise ValueError(f"accepted corpus decision_id 不一致: {pattern_id}")
        ids.append(pattern_id)
        names.append(raw)
    if ids != sorted(ids) or len(ids) != len(set(ids)):
        raise ValueError("accepted corpus entries 必须按唯一 pattern_id 排序")
    if len(names) != len(set(names)):
        raise ValueError("accepted corpus raw_event_name 必须唯一")
    return payload


def build_quality_summary(
    rows: Iterable[Mapping[str, Any]],
    pattern_report: Mapping[str, Any],
    catalog: Mapping[str, Any],
    corpus: Mapping[str, Any],
    *,
    stable_scope: StableScope = V1_STABLE_SCOPE,
) -> dict[str, Any]:
    """构造进入数据集内容身份的确定性分层质量摘要。"""

    row_list = [dict(row) for row in rows]
    patterns = _objects(catalog.get("patterns"))
    pattern_map = {str(pattern.get("pattern_id")): pattern for pattern in patterns}
    approved = _objects(pattern_report.get("approved_scope_patterns"))
    approved_ids = {str(pattern.get("pattern_id")) for pattern in approved}
    stable_rows = [row for row in row_list if row.get("support_level") == "stable"]

    violations: Counter[str] = Counter()
    catalog_stable_ids = {
        str(pattern.get("pattern_id")) for pattern in patterns if pattern.get("support_level") == "stable"
    }
    approved_ids_digest = _pattern_ids_digest(approved_ids)
    decision_ids = {
        "catalog": catalog.get("decision_id"),
        "corpus": corpus.get("decision_id"),
        "pattern_report": (
            pattern_report.get("promotion_decision", {}).get("decision_id")
            if isinstance(pattern_report.get("promotion_decision"), dict)
            else None
        ),
    }
    for source, decision_id in decision_ids.items():
        if decision_id != stable_scope.decision_id:
            violations[f"{source}_decision_mismatch"] += 1
    if len(approved_ids) != stable_scope.pattern_count:
        violations["approved_pattern_count_mismatch"] += 1
    if approved_ids_digest != stable_scope.pattern_ids_digest:
        violations["approved_pattern_ids_digest_mismatch"] += 1
    if len(catalog_stable_ids) != stable_scope.pattern_count:
        violations["stable_pattern_count_mismatch"] += 1
    if len(stable_rows) != stable_scope.record_count:
        violations["stable_record_count_mismatch"] += 1
    corpus_entries = _objects(corpus.get("entries"))
    corpus_ids = {str(entry.get("pattern_id")) for entry in corpus_entries}
    if len(corpus_entries) != stable_scope.pattern_count:
        violations["accepted_corpus_entry_count_mismatch"] += 1
    if corpus_ids != approved_ids:
        violations["accepted_corpus_scope_mismatch"] += 1
    for pattern_id in sorted(approved_ids):
        pattern = pattern_map.get(pattern_id)
        if pattern is None:
            violations["approved_pattern_missing"] += 1
        elif pattern.get("support_level") != "stable":
            violations["approved_pattern_not_stable"] += 1
        elif pattern.get("review_status") != "accepted":
            violations["approved_pattern_pending_review"] += 1
    violations["unapproved_stable_pattern"] += len(catalog_stable_ids.difference(approved_ids))

    for row in stable_rows:
        pattern = pattern_map.get(str(row.get("pattern_id")))
        violations.update(_stable_violations(row, pattern))
        classified = _classify_english(row, rerender=True)
        if classified["unresolved"]:
            violations["unresolved_english"] += 1

    corpus_result = _evaluate_corpus(row_list, pattern_map, corpus)
    if not corpus_result["passed"]:
        violations["accepted_corpus_failure"] += int(corpus_result["failed_entry_count"])

    violation_counts = {name: count for name, count in sorted(violations.items()) if count}
    stable_passed = not violation_counts
    support = _tier_counts(row_list, patterns)
    experimental = _experimental_summary(row_list, pattern_report)
    unsupported = _unsupported_summary(row_list)
    stable_english = _english_summary(stable_rows, rerender=True)
    summary = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "gate_version": GATE_VERSION,
        "status": "passed" if stable_passed else "failed",
        "decision_id": catalog.get("decision_id"),
        "scope_contract": {
            "decision_id": stable_scope.decision_id,
            "expected_pattern_count": stable_scope.pattern_count,
            "actual_pattern_count": len(catalog_stable_ids),
            "expected_record_count": stable_scope.record_count,
            "actual_record_count": len(stable_rows),
            "expected_pattern_ids_digest": stable_scope.pattern_ids_digest,
            "actual_pattern_ids_digest": approved_ids_digest,
            "matched": (
                all(value == stable_scope.decision_id for value in decision_ids.values())
                and len(approved_ids) == stable_scope.pattern_count
                and len(catalog_stable_ids) == stable_scope.pattern_count
                and catalog_stable_ids == approved_ids
                and len(stable_rows) == stable_scope.record_count
                and approved_ids_digest == stable_scope.pattern_ids_digest
                and len(corpus_entries) == stable_scope.pattern_count
                and corpus_ids == approved_ids
            ),
        },
        "global": {
            "record_count": len(row_list),
            "pattern_count": len(patterns),
            "support": support,
            "confidence_distribution": _confidence_distribution(row_list),
            "residual_english": _merge_english(
                stable_english,
                experimental["residual_english"],
                unsupported["residual_english"],
            ),
        },
        "stable": {
            "passed": stable_passed,
            "require_zero": True,
            "record_count": len(stable_rows),
            "pattern_count": len(catalog_stable_ids),
            "approved_pattern_count": len(approved_ids),
            "violation_counts": violation_counts,
            "confidence_distribution": _confidence_distribution(stable_rows),
            "residual_english": stable_english,
            "qualifier_coverage": _qualifier_coverage(stable_rows),
        },
        "experimental": experimental,
        "unsupported": unsupported,
        "accepted_corpus": corpus_result,
    }
    return summary


def build_support_diff(current_root: Path, previous_root: Path | None) -> dict[str, Any]:
    """比较当前 candidate 与上一个 approved dataset 的 support 状态。"""

    current_rows = _rows_by_id(current_root)
    current_patterns = _catalog_by_id(current_root)
    if previous_root is None:
        initial = True
        baseline = "none"
        previous_rows: dict[str, dict[str, Any]] = {}
        previous_patterns: dict[str, dict[str, Any]] = {}
    else:
        initial = False
        baseline = _dataset_id(previous_root)
        previous_rows = _rows_by_id(previous_root)
        previous_patterns = _catalog_by_id(previous_root)
    pattern_changes = _support_changes(previous_patterns, current_patterns, initial_stable_only=initial)
    record_changes = _record_support_changes(previous_rows, current_rows, initial_stable_only=initial)
    pattern_downgrades = sorted(
        pattern_id
        for pattern_id, pattern in previous_patterns.items()
        if pattern.get("support_level") == "stable"
        and current_patterns.get(pattern_id, {}).get("support_level") != "stable"
    )
    record_downgrades = sorted(
        record_id
        for record_id, row in previous_rows.items()
        if row.get("support_level") == "stable"
        and (
            current_rows.get(record_id, {}).get("support_level") != "stable"
            or current_rows.get(record_id, {}).get("pattern_id") != row.get("pattern_id")
        )
    )
    passed = not pattern_downgrades and not record_downgrades
    return {
        "schema_version": 1,
        "baseline": baseline,
        "passed": passed,
        "review_required": bool(pattern_changes or record_changes),
        "pattern_changes": pattern_changes,
        "record_changes": record_changes,
        "transition_counts": _transition_counts(pattern_changes, record_changes),
        "stable_pattern_downgrades": pattern_downgrades,
        "stable_record_downgrades": record_downgrades,
        "previous": None if initial else _support_snapshot(previous_rows, previous_patterns),
        "current": _support_snapshot(current_rows, current_patterns),
    }


def build_gate_report(
    summary: Mapping[str, Any],
    validation: Mapping[str, Any],
    support_diff: Mapping[str, Any],
    *,
    stable_scope: StableScope = V1_STABLE_SCOPE,
) -> dict[str, Any]:
    """组合 payload 质量、全局结构验证与 support diff 自动门禁。"""

    validation_summary = validation.get("summary")
    validation_errors = validation_summary.get("error_count") if isinstance(validation_summary, dict) else None
    checks = {
        "stable_product_gates": summary.get("status") == "passed"
        and stable_scope_contract_passes(summary, stable_scope),
        "global_validation": validation_errors == 0,
        "support_diff": support_diff.get("passed") is True,
    }
    automatic_passed = all(checks.values())
    return {
        "schema_version": 1,
        "gate_version": GATE_VERSION,
        "passed": automatic_passed,
        "checks": checks,
        "promotion_eligibility": {
            "eligible": False,
            "gate_version": GATE_VERSION,
            "automatic_gates_passed": automatic_passed,
            "lifecycle_state": "awaiting_manual_review" if automatic_passed else "validated",
            "reason": "manual review pending" if automatic_passed else "automatic quality gates failed",
        },
    }


def stable_scope_contract_passes(
    summary: Mapping[str, Any],
    stable_scope: StableScope = V1_STABLE_SCOPE,
) -> bool:
    """独立复核质量摘要确实命中冻结 Stable 绝对范围。"""

    contract = summary.get("scope_contract")
    if not isinstance(contract, dict):
        return False
    return (
        summary.get("decision_id") == stable_scope.decision_id
        and contract.get("matched") is True
        and contract.get("decision_id") == stable_scope.decision_id
        and contract.get("expected_pattern_count") == stable_scope.pattern_count
        and contract.get("actual_pattern_count") == stable_scope.pattern_count
        and contract.get("expected_record_count") == stable_scope.record_count
        and contract.get("actual_record_count") == stable_scope.record_count
        and contract.get("expected_pattern_ids_digest") == stable_scope.pattern_ids_digest
        and contract.get("actual_pattern_ids_digest") == stable_scope.pattern_ids_digest
    )


def _stable_violations(row: Mapping[str, Any], pattern: Mapping[str, Any] | None) -> Counter[str]:
    violations: Counter[str] = Counter()
    if pattern is None:
        violations["missing_catalog_pattern"] += 1
        return violations
    if pattern.get("support_level") != "stable":
        violations["record_pattern_not_stable"] += 1
    if row.get("review_status") != "accepted" or pattern.get("review_status") != "accepted":
        violations["pending_review"] += 1

    translation = row.get("translation")
    if not isinstance(translation, dict):
        violations["missing_translation"] += 1
        return violations
    frame_type = translation.get("frame_type")
    if not isinstance(frame_type, str) or not frame_type:
        violations["missing_frame"] += 1
        return violations
    if frame_type != pattern.get("frame_type") or frame_type not in FRAME_REGISTRY:
        violations["wrong_frame"] += 1
        return violations
    if frame_type == "ability_cast_event" and _has_unresolved_single_key_spell_residual(row):
        # 单字母槽位前缀后仍有未解释残片时，不能把普通词首字母伪造成技能键。
        # 只阻断这条已证实的错误路径；其他 medium 记录继续在 Review 摘要中可见。
        violations["unresolved_single_key_spell_residual"] += 1
    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        violations["missing_arguments"] += 1
        return violations

    required_roles = pattern.get("required_roles")
    required = required_roles if isinstance(required_roles, list) else []
    for role in required:
        argument = arguments.get(role)
        if not isinstance(argument, dict):
            violations["missing_required_role"] += 1
            continue
        confidence = str(argument.get("confidence") or "unknown").lower()
        if confidence in UNTRUSTED_CONFIDENCE:
            violations["required_low_confidence"] += 1
    for argument in arguments.values():
        if not isinstance(argument, dict):
            violations["invalid_argument"] += 1
            continue
        if argument.get("fallback") is True:
            violations["fallback_argument"] += 1
        labels = argument.get("labels")
        zh_cn = labels.get("zh_CN") if isinstance(labels, dict) else None
        if not isinstance(zh_cn, str) or not zh_cn.strip():
            violations["missing_zh_cn"] += 1

    if row.get("render_status") != "rendered":
        violations["render_failure"] += 1
    for mode in ("default", "main"):
        result = render_translation(translation, RenderOptions(locale="zh_CN", mode=mode))
        if not result.text.strip():
            violations["render_failure"] += 1
        rendered = row.get("rendered_labels")
        zh_cn = rendered.get("zh_CN") if isinstance(rendered, dict) else None
        stored = zh_cn.get(mode) if isinstance(zh_cn, dict) else None
        if stored != result.text:
            violations["rendered_label_mismatch"] += 1
        for diagnostic in result.diagnostics:
            if diagnostic.startswith("missing_role:"):
                role = diagnostic.partition(":")[2]
                if role not in required:
                    continue
            violations["renderer_diagnostic"] += 1
    return violations


def _has_unresolved_single_key_spell_residual(row: Mapping[str, Any]) -> bool:
    """判断记录是否仍依赖带未解析后缀的单字母技能启发式。"""

    semantic = row.get("semantic")
    slots = semantic.get("slots") if isinstance(semantic, dict) else None
    spell_match = slots.get("spell_match") if isinstance(slots, dict) else None
    if not isinstance(spell_match, dict):
        return False
    subject = slots.get("subject")
    subject_key = subject.get("key") if isinstance(subject, dict) else None
    spell_key = slots.get("spell_key")
    matched_term = spell_match.get("matched_term")
    components = spell_match.get("components")
    expected_single_key_term = (
        f"{subject_key}{spell_key}".lower() if isinstance(subject_key, str) and isinstance(spell_key, str) else None
    )
    return (
        slots.get("source") == "event_name_heuristic"
        and spell_match.get("match_source") in {"spell_key", "passive_key"}
        and not (isinstance(components, list) and "trusted_single_key_residual" in components)
        and isinstance(matched_term, str)
        and matched_term.lower() == expected_single_key_term
        and isinstance(slots.get("unresolved_residual"), str)
        and bool(str(slots["unresolved_residual"]).strip())
    )


def _classify_english(row: Mapping[str, Any], *, rerender: bool = False) -> dict[str, list[str]]:
    classified: dict[str, set[str]] = {
        "ability_key": set(),
        "proper_noun": set(),
        "abbreviation": set(),
        "technical": set(),
        "unresolved": set(),
    }
    translation = row.get("translation")
    if not isinstance(translation, dict):
        return {category: [] for category in classified}
    arguments = translation.get("arguments")
    argument_map = arguments if isinstance(arguments, dict) else {}
    entity = row.get("entity")
    alias = str(entity.get("alias") or "") if isinstance(entity, dict) else ""
    texts: list[Any] = []
    if not rerender:
        rendered = row.get("rendered_labels")
        zh_cn = rendered.get("zh_CN") if isinstance(rendered, dict) else None
        texts = [zh_cn.get(mode) for mode in ("default", "main")] if isinstance(zh_cn, dict) else []
    if rerender or not all(isinstance(text, str) for text in texts):
        texts = [
            render_translation(translation, RenderOptions(locale="zh_CN", mode=mode)).text
            for mode in ("default", "main")
        ]
    for text in texts:
        assert isinstance(text, str)
        used_labels = _labels_present_in_text(argument_map, text)
        for token in ENGLISH_TOKEN.findall(text):
            category = _english_category(token, translation, argument_map, used_labels, alias)
            classified[category].add(token)
    # qualifier 等结构化角色不一定进入 default/main 模板；仅扫描渲染结果会
    # 静默遗漏这类中文标签中的英文残留。
    for role, label in _argument_zh_cn_labels(argument_map).items():
        for token in ENGLISH_TOKEN.findall(label):
            category = _english_category(token, translation, argument_map, {role: label}, alias)
            classified[category].add(token)
    return {category: sorted(tokens) for category, tokens in classified.items()}


def _english_category(
    token: str,
    translation: Mapping[str, Any],
    arguments: Mapping[str, Any],
    used_labels: Mapping[str, str],
    alias: str,
) -> str:
    ability = arguments.get("ability_slot")
    if (
        translation.get("frame_type") == "ability_cast_event"
        and token in ABILITY_KEYS
        and isinstance(ability, dict)
        and ability.get("fallback") is not True
        and str(ability.get("confidence") or "unknown").lower() not in UNTRUSTED_CONFIDENCE
        and token in ENGLISH_TOKEN.findall(used_labels.get("ability_slot", ""))
        and str(ability.get("key") or "").upper() == token
    ):
        return "ability_key"
    if token.casefold() in {part.casefold() for part in ENGLISH_TOKEN.findall(alias)}:
        return "proper_noun"
    for role in PROPER_NOUN_ROLES:
        label = used_labels.get(role)
        if label and token.casefold() in {part.casefold() for part in ENGLISH_TOKEN.findall(label)}:
            return "proper_noun"
    if token.upper() in ABBREVIATIONS and token == token.upper():
        return "abbreviation"
    if token.upper() in TECHNICAL_TOKENS and token == token.upper():
        return "technical"
    return "unresolved"


def _labels_present_in_text(arguments: Mapping[str, Any], text: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    text_tokens = {token.casefold() for token in ENGLISH_TOKEN.findall(text)}
    for role, argument in arguments.items():
        raw_labels = argument.get("labels") if isinstance(argument, dict) else None
        value = raw_labels.get("zh_CN") if isinstance(raw_labels, dict) else None
        value_tokens = {token.casefold() for token in ENGLISH_TOKEN.findall(value)} if isinstance(value, str) else set()
        if (
            isinstance(role, str)
            and isinstance(value, str)
            and value
            and (value in text or not text_tokens.isdisjoint(value_tokens))
        ):
            labels[role] = value
    return labels


def _argument_zh_cn_labels(arguments: Mapping[str, Any]) -> dict[str, str]:
    """提取全部结构化 argument 的非空中文标签。"""

    labels: dict[str, str] = {}
    for role, argument in arguments.items():
        raw_labels = argument.get("labels") if isinstance(argument, dict) else None
        value = raw_labels.get("zh_CN") if isinstance(raw_labels, dict) else None
        if isinstance(role, str) and isinstance(value, str) and value:
            labels[role] = value
    return labels


def _evaluate_corpus(
    rows: list[dict[str, Any]],
    patterns: Mapping[str, Mapping[str, Any]],
    corpus: Mapping[str, Any],
) -> dict[str, Any]:
    rows_by_raw: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        rows_by_raw[str(row.get("raw_event_name") or "")].append(row)
    results: list[dict[str, Any]] = []
    for entry in _objects(corpus.get("entries")):
        raw = str(entry.get("raw_event_name") or "")
        matches = rows_by_raw.get(raw, [])
        failures: list[str] = []
        if len(matches) != 1:
            failures.append("record_not_unique")
            selected: Mapping[str, Any] = {}
        else:
            selected = matches[0]
        pattern_id = str(entry.get("pattern_id") or "")
        pattern = patterns.get(pattern_id)
        if selected.get("pattern_id") != pattern_id:
            failures.append("pattern_id_mismatch")
        if pattern is None:
            failures.append("pattern_missing")
        elif pattern.get("support_level") != "stable" or pattern.get("review_status") != "accepted":
            failures.append("pattern_not_stable_accepted")
        elif pattern.get("signature") != entry.get("signature"):
            failures.append("signature_mismatch")
        if pattern is not None and pattern.get("required_roles") != entry.get("required_roles"):
            failures.append("required_roles_mismatch")
        if selected.get("support_level") != "stable" or selected.get("review_status") != "accepted":
            failures.append("record_not_stable_accepted")
        translation = selected.get("translation")
        if not isinstance(translation, dict):
            failures.append("translation_missing")
            actual_default = ""
            actual_main = ""
        else:
            arguments = translation.get("arguments")
            expected_arguments = entry.get("expected_arguments")
            if isinstance(arguments, dict) and isinstance(expected_arguments, dict):
                actual_roles = sorted(str(role) for role, value in arguments.items() if isinstance(value, dict))
                if actual_roles != expected_arguments.get("roles"):
                    failures.append("argument_roles_mismatch")
                signature = pattern.get("signature") if isinstance(pattern, dict) else None
                signature_keys = signature.get("argument_keys") if isinstance(signature, dict) else None
                if signature_keys != expected_arguments.get("signature_keys"):
                    failures.append("signature_keys_mismatch")
            else:
                failures.append("arguments_missing")
            actual_default = render_translation(translation, RenderOptions(locale="zh_CN", mode="default")).text
            actual_main = render_translation(translation, RenderOptions(locale="zh_CN", mode="main")).text
        if actual_default != entry.get("zh_cn_default"):
            failures.append("zh_cn_default_mismatch")
        if actual_main != entry.get("zh_cn_main"):
            failures.append("zh_cn_main_mismatch")
        semantic_frame = selected.get("semantic_frame")
        actual_frame = semantic_frame.get("frame_type") if isinstance(semantic_frame, dict) else None
        if actual_frame != entry.get("frame_type"):
            failures.append("frame_type_mismatch")
        results.append(
            {
                "pattern_id": pattern_id,
                "raw_event_name": raw,
                "passed": not failures,
                "failures": sorted(set(failures)),
            }
        )
    failed = sum(result["passed"] is not True for result in results)
    return {
        "corpus_id": corpus.get("corpus_id"),
        "decision_id": corpus.get("decision_id"),
        "review_status": corpus.get("review_status"),
        "entry_count": len(results),
        "passed_entry_count": len(results) - failed,
        "failed_entry_count": failed,
        "passed": failed == 0,
        "results": results,
    }


def _experimental_summary(rows: list[dict[str, Any]], report: Mapping[str, Any]) -> dict[str, Any]:
    experimental = [row for row in rows if row.get("support_level") == "experimental"]
    warnings = Counter(str(warning) for row in experimental for warning in _warnings(row))
    statuses = Counter(str(row.get("render_status") or "missing") for row in experimental)
    pending = _objects(report.get("pending_proposals"))
    return {
        "blocking": False,
        "record_count": len(experimental),
        "render_status_counts": dict(sorted(statuses.items())),
        "confidence_distribution": _confidence_distribution(experimental),
        "residual_english": _english_summary(experimental),
        "qualifier_coverage": _qualifier_coverage(experimental),
        "top_warnings": [
            {"code": code, "record_count": count}
            for code, count in sorted(warnings.items(), key=lambda item: (-item[1], item[0]))[:20]
        ],
        "promotion_candidate_pattern_count": len(pending),
        "promotion_candidate_record_count": sum(
            int(pattern.get("counts", {}).get("record_count") or 0) for pattern in pending
        ),
    }


def _warnings(row: Mapping[str, Any]) -> list[Any]:
    value = row.get("warnings")
    return value if isinstance(value, list) else []


def _unsupported_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    unsupported = [row for row in rows if row.get("support_level") == "unsupported"]
    kinds = Counter(str(row.get("asset_kind") or "unknown") for row in unsupported)
    return {
        "blocking": False,
        "record_count": len(unsupported),
        "asset_kind_counts": dict(sorted(kinds.items())),
        "confidence_distribution": _confidence_distribution(unsupported),
        "residual_english": _english_summary(unsupported),
        "qualifier_coverage": _qualifier_coverage(unsupported),
    }


def _english_summary(rows: Iterable[Mapping[str, Any]], *, rerender: bool = False) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    tokens_by_category: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for category, tokens in _classify_english(row, rerender=rerender).items():
            if tokens:
                counts[category] += 1
                tokens_by_category[category].update(tokens)
    return {
        "record_counts_by_category": dict(sorted(counts.items())),
        "unique_token_counts_by_category": {
            category: len(tokens) for category, tokens in sorted(tokens_by_category.items())
        },
        "top_tokens_by_category": {
            category: [
                {"token": token, "record_count": count}
                for token, count in sorted(tokens.items(), key=lambda item: (-item[1], item[0]))[:50]
            ]
            for category, tokens in sorted(tokens_by_category.items())
        },
    }


def _merge_english(*summaries: Mapping[str, Any]) -> dict[str, Any]:
    counts: Counter[str] = Counter()
    for summary in summaries:
        raw_counts = summary.get("record_counts_by_category")
        if isinstance(raw_counts, dict):
            counts.update({str(key): int(value) for key, value in raw_counts.items()})
    return {"record_counts_by_category": dict(sorted(counts.items()))}


def _confidence_distribution(rows: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """汇总记录语义与 translation frame 的置信度，保留未知值以便审核。"""

    semantic = Counter()
    frame = Counter()
    for row in rows:
        semantic_data = row.get("semantic")
        translation = row.get("translation")
        semantic[
            str(semantic_data.get("confidence") or "unknown").lower() if isinstance(semantic_data, dict) else "unknown"
        ] += 1
        frame[
            str(translation.get("frame_confidence") or "unknown").lower()
            if isinstance(translation, dict)
            else "unknown"
        ] += 1
    return {"semantic": dict(sorted(semantic.items())), "frame": dict(sorted(frame.items()))}


def _qualifier_coverage(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """汇总可见与不进入模板的 qualifier 标签，暴露其英文残留风险。"""

    record_count = 0
    unresolved_records = 0
    tokens: Counter[str] = Counter()
    for row in rows:
        translation = row.get("translation")
        arguments = translation.get("arguments") if isinstance(translation, dict) else None
        qualifier = arguments.get("qualifier") if isinstance(arguments, dict) else None
        labels = qualifier.get("labels") if isinstance(qualifier, dict) else None
        label = labels.get("zh_CN") if isinstance(labels, dict) else None
        if not isinstance(label, str) or not label:
            continue
        record_count += 1
        unresolved = [
            token
            for token in ENGLISH_TOKEN.findall(label)
            if _english_category(token, translation, arguments, {"qualifier": label}, "") == "unresolved"
        ]
        if unresolved:
            unresolved_records += 1
            tokens.update(unresolved)
    return {
        "record_count": record_count,
        "unresolved_english_record_count": unresolved_records,
        "top_unresolved_tokens": [
            {"token": token, "record_count": count}
            for token, count in sorted(tokens.items(), key=lambda item: (-item[1], item[0]))[:50]
        ],
    }


def _tier_counts(rows: list[dict[str, Any]], patterns: list[dict[str, Any]]) -> dict[str, Any]:
    record_counts = Counter(str(row.get("support_level") or "missing") for row in rows)
    pattern_counts = Counter(str(pattern.get("support_level") or "missing") for pattern in patterns)
    return {
        "record_counts": dict(sorted(record_counts.items())),
        "pattern_counts": dict(sorted(pattern_counts.items())),
    }


def _pattern_ids_digest(pattern_ids: Iterable[str]) -> str:
    payload = json.dumps(sorted(pattern_ids), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _support_changes(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    initial_stable_only: bool,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for pattern_id in sorted(set(previous).union(current)):
        previous_level = _support_level(previous.get(pattern_id))
        current_level = _support_level(current.get(pattern_id))
        if previous_level == current_level:
            continue
        if initial_stable_only and current_level != "stable":
            continue
        changes.append(
            {
                "pattern_id": pattern_id,
                "previous": previous_level,
                "current": current_level,
                "change": _change_kind(previous_level, current_level),
            }
        )
    return changes


def _record_support_changes(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    initial_stable_only: bool,
) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for record_id in sorted(set(previous).union(current)):
        previous_row = previous.get(record_id)
        current_row = current.get(record_id)
        previous_level = _support_level(previous_row)
        current_level = _support_level(current_row)
        previous_pattern_id = _mapping_text(previous_row, "pattern_id")
        current_pattern_id = _mapping_text(current_row, "pattern_id")
        if previous_level == current_level and previous_pattern_id == current_pattern_id:
            continue
        if initial_stable_only and current_level != "stable":
            continue
        changes.append(
            {
                "record_id": record_id,
                "previous": previous_level,
                "current": current_level,
                "previous_pattern_id": previous_pattern_id,
                "current_pattern_id": current_pattern_id,
                "change": _change_kind(previous_level, current_level),
            }
        )
    return changes


def _transition_counts(
    pattern_changes: Iterable[Mapping[str, Any]],
    record_changes: Iterable[Mapping[str, Any]],
) -> dict[str, dict[str, int]]:
    def counts(changes: Iterable[Mapping[str, Any]]) -> dict[str, int]:
        transitions = Counter(
            f"{change.get('previous') or 'none'}->{change.get('current') or 'none'}" for change in changes
        )
        return dict(sorted(transitions.items()))

    return {"patterns": counts(pattern_changes), "records": counts(record_changes)}


def _support_level(value: Mapping[str, Any] | None) -> str | None:
    level = value.get("support_level") if value is not None else None
    return level if isinstance(level, str) else None


def _mapping_text(value: Mapping[str, Any] | None, field: str) -> str | None:
    result = value.get(field) if value is not None else None
    return result if isinstance(result, str) else None


def _change_kind(previous: str | None, current: str | None) -> str:
    if previous is None:
        return "added"
    if current is None:
        return "removed"
    return "changed"


def _rows_by_id(root: Path) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("record_id")): row
        for _path, _line, row in iter_normalized_rows(root)
        if isinstance(row.get("record_id"), str)
    }


def _catalog_by_id(root: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads((root / "pattern-catalog.json").read_text(encoding="utf-8"))
    return {str(pattern.get("pattern_id")): pattern for pattern in _objects(payload.get("patterns"))}


def _support_snapshot(
    rows: Mapping[str, Mapping[str, Any]], patterns: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "record_counts": dict(sorted(Counter(str(row.get("support_level")) for row in rows.values()).items())),
        "pattern_counts": dict(
            sorted(Counter(str(pattern.get("support_level")) for pattern in patterns.values()).items())
        ),
    }


def _dataset_id(root: Path) -> str:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    value = manifest.get("dataset_id") if isinstance(manifest, dict) else None
    return value if isinstance(value, str) and value else root.name


def _objects(value: object) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _required_text(value: Mapping[str, Any], field: str, location: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{location}.{field} 必须是非空字符串")
    return result
