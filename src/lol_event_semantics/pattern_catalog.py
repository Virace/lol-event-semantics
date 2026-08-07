"""从 normalized 记录生成确定性的 translation pattern 盘点报告。"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ruamel.yaml import YAML

from .decomposition_lexer import parse_shell
from .normalized_translation_model import FRAME_REGISTRY, RenderOptions
from .normalized_translation_renderer import render_translation

REPORT_SCHEMA_VERSION = "translation-pattern-report.v1"
POLICY_SCHEMA_VERSION = "translation-pattern-policy.v1"
SIGNATURE_SCHEMA_VERSION = "pattern-signature.v1"
PATTERN_ID_PREFIX = "pattern-v1-"
UNTRUSTED_CONFIDENCE = {"low", "failed", "unknown"}
PARAMETERIZED_ENTITY_ROLES = {
    "augment",
    "champion",
    "companion",
    "counterpart",
    "item",
    "map",
    "mode",
    "partner",
    "shop_subject",
    "skin",
    "subject",
}


@dataclass(frozen=True, slots=True)
class FramePolicy:
    """单个 Stable 候选 frame 的盘点策略。"""

    support_level: str
    proposed_support_level: str
    review_status: str
    signature_roles: tuple[str, ...]
    semantic_profiles: frozenset[str]


@dataclass(frozen=True, slots=True)
class PatternPolicy:
    """translation pattern 盘点策略。"""

    schema_version: str
    signature_version: str
    example_limit: int
    default_support_level: str
    default_review_status: str
    stable_asset_kind: str
    stable_frames: Mapping[str, FramePolicy]
    sfx_support_level: str
    sfx_review_status: str


@dataclass(frozen=True, slots=True)
class RecordAssessment:
    """单条 normalized 记录的 Stable 候选评估结果。"""

    blocking_reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    zh_cn_default: str
    zh_cn_main: str


def load_pattern_policy(path: Path) -> PatternPolicy:
    """加载并校验 pattern inventory 策略。

    Args:
        path: YAML 策略路径。

    Returns:
        已校验的不可变策略。

    Raises:
        ValueError: 策略结构、版本或 signature role 不合法。
    """

    yaml = YAML(typ="safe")
    payload = yaml.load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("translation pattern policy 必须是 mapping。")

    schema_version = _required_string(payload, "schema_version")
    signature_version = _required_string(payload, "signature_version")
    if schema_version != POLICY_SCHEMA_VERSION:
        raise ValueError(f"不支持的 policy schema_version: {schema_version}")
    if signature_version != SIGNATURE_SCHEMA_VERSION:
        raise ValueError(f"不支持的 signature_version: {signature_version}")

    example_limit = payload.get("example_limit", 3)
    if not isinstance(example_limit, int) or isinstance(example_limit, bool) or example_limit < 1:
        raise ValueError("example_limit 必须是正整数。")

    defaults = _required_mapping(payload, "defaults")
    default_support_level = _required_string(defaults, "support_level")
    default_review_status = _required_string(defaults, "review_status")
    if default_support_level != "experimental" or default_review_status != "pending":
        raise ValueError("policy defaults 必须保持 experimental/pending。")
    stable = _required_mapping(payload, "stable_proposal")
    sfx = _required_mapping(payload, "sfx_candidate")
    stable_asset_kind = _required_string(stable, "asset_kind")
    if stable_asset_kind != "vo":
        raise ValueError("Stable proposal 的 asset_kind 必须固定为 vo。")
    sfx_support_level = _required_string(sfx, "support_level")
    sfx_review_status = _required_string(sfx, "review_status")
    if sfx_support_level != "experimental" or sfx_review_status != "pending":
        raise ValueError("SFX candidate 必须保持 experimental/pending。")
    frame_payloads = _required_mapping(stable, "frames")
    frames: dict[str, FramePolicy] = {}
    for frame_type, raw_frame in frame_payloads.items():
        if not isinstance(frame_type, str) or not frame_type:
            raise ValueError("stable frame 名称必须是非空字符串。")
        if frame_type not in FRAME_REGISTRY:
            raise ValueError(f"stable frame 未在 FRAME_REGISTRY 注册: {frame_type}")
        if not isinstance(raw_frame, dict):
            raise ValueError(f"stable frame {frame_type} 必须是 mapping。")

        roles = raw_frame.get("signature_roles", [])
        if not isinstance(roles, list) or not all(isinstance(role, str) and role for role in roles):
            raise ValueError(f"stable frame {frame_type} 的 signature_roles 必须是字符串列表。")
        entity_roles = sorted(set(roles).intersection(PARAMETERIZED_ENTITY_ROLES))
        if entity_roles:
            raise ValueError(f"stable frame {frame_type} 不得把实体角色写入 signature_roles: {','.join(entity_roles)}")
        semantic_profiles = raw_frame.get("semantic_profiles")
        if not isinstance(semantic_profiles, list) or not all(
            isinstance(profile, str) and profile for profile in semantic_profiles
        ):
            raise ValueError(f"stable frame {frame_type} 的 semantic_profiles 必须是非空字符串列表。")
        if not semantic_profiles:
            raise ValueError(f"stable frame {frame_type} 的 semantic_profiles 不得为空。")

        support_level = _required_string(raw_frame, "support_level")
        proposed_support_level = _required_string(raw_frame, "proposed_support_level")
        review_status = _required_string(raw_frame, "review_status")
        if support_level != "experimental" or proposed_support_level != "stable" or review_status != "pending":
            raise ValueError(f"stable frame {frame_type} 必须保持 experimental -> stable / pending。")

        frames[frame_type] = FramePolicy(
            support_level=support_level,
            proposed_support_level=proposed_support_level,
            review_status=review_status,
            signature_roles=tuple(sorted(set(roles))),
            semantic_profiles=frozenset(semantic_profiles),
        )

    return PatternPolicy(
        schema_version=schema_version,
        signature_version=signature_version,
        example_limit=example_limit,
        default_support_level=default_support_level,
        default_review_status=default_review_status,
        stable_asset_kind=stable_asset_kind,
        stable_frames=frames,
        sfx_support_level=sfx_support_level,
        sfx_review_status=sfx_review_status,
    )


def load_normalized_records(root: Path) -> list[dict[str, Any]]:
    """从 normalized root 读取全部实体 JSONL 记录。

    Args:
        root: normalized 输出根目录。

    Returns:
        按相对文件名与行号读取的记录列表。

    Raises:
        ValueError: 根目录、JSONL 行或记录结构不合法。
    """

    if not root.is_dir():
        raise ValueError(f"normalized root 不存在或不是目录: {root}")

    by_entity = root / "by-entity"
    if not by_entity.is_dir():
        raise ValueError(f"normalized root 缺少 by-entity 目录: {root}")

    records: list[dict[str, Any]] = []
    for path in sorted(by_entity.rglob("*.jsonl")):
        relative = path.relative_to(root).as_posix()
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"normalized JSONL 无效: {relative}:{line_number}") from exc
            if not isinstance(payload, dict):
                raise ValueError(f"normalized 记录必须是 object: {relative}:{line_number}")
            records.append(payload)

    if not records:
        raise ValueError(f"normalized root 没有可盘点记录: {root}")

    return records


def canonical_pattern_signature(record: Mapping[str, Any], policy: PatternPolicy) -> dict[str, Any]:
    """构造不含计数、示例、路径和时间的 canonical signature v1。

    Args:
        record: normalized 记录。
        policy: pattern 策略。

    Returns:
        可稳定 JSON 序列化的 signature。
    """

    raw_event_name = _string_or_none(record.get("raw_event_name")) or ""
    asset_kind = parse_shell(raw_event_name).asset_kind or "unknown"
    semantic = _mapping_or_empty(record.get("semantic"))
    slots = _mapping_or_empty(semantic.get("slots"))
    translation = _mapping_or_empty(record.get("translation"))
    frame_type = _string_or_none(translation.get("frame_type")) or "missing"
    frame_version = _stable_scalar(translation.get("frame_version"), missing="missing")
    arguments = _mapping_or_empty(translation.get("arguments"))
    roles = sorted(
        str(role) for role, argument in arguments.items() if isinstance(role, str) and isinstance(argument, dict)
    )

    signature: dict[str, Any] = {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "asset_kind": asset_kind,
        "semantic_profile": _string_or_none(semantic.get("profile")) or "unknown",
        "primary_family": _string_or_none(semantic.get("primary_family")) or "unknown",
        "frame_type": frame_type,
        "frame_version": frame_version,
        "trigger_family": _string_or_none(slots.get("trigger_family")) or "unknown",
        "roles": roles,
    }
    frame_policy = policy.stable_frames.get(frame_type)
    if frame_policy is not None and frame_policy.signature_roles:
        signature["argument_keys"] = {role: _argument_key(arguments.get(role)) for role in frame_policy.signature_roles}

    return signature


def pattern_id_for_signature(signature: Mapping[str, Any]) -> str:
    """计算 canonical JSON 的完整 SHA-256 pattern id。

    Args:
        signature: canonical pattern signature。

    Returns:
        `pattern-v1-<hex>` 标识符。
    """

    digest = hashlib.sha256(_canonical_json(signature).encode("utf-8")).hexdigest()
    return f"{PATTERN_ID_PREFIX}{digest}"


def build_pattern_report(records: Iterable[Mapping[str, Any]], policy: PatternPolicy) -> dict[str, Any]:
    """按 canonical signature 聚合 normalized 记录并生成报告。

    Args:
        records: normalized 记录迭代器。
        policy: pattern 策略。

    Returns:
        确定性、版本化的 JSON 报告对象。
    """

    grouped: dict[str, list[tuple[dict[str, Any], Mapping[str, Any]]]] = defaultdict(list)
    signatures: dict[str, dict[str, Any]] = {}
    for record in records:
        signature = canonical_pattern_signature(record, policy)
        pattern_id = pattern_id_for_signature(signature)
        signatures[pattern_id] = signature
        grouped[pattern_id].append((signature, record))

    patterns = [
        _build_pattern(pattern_id, signatures[pattern_id], grouped[pattern_id], policy)
        for pattern_id in sorted(grouped)
    ]
    stable_proposals = sorted(
        (pattern for pattern in patterns if pattern["eligibility"]["stable"]),
        key=_review_sort_key,
    )
    sfx_candidates = [pattern for pattern in patterns if pattern["signature"]["asset_kind"] == "sfx"]
    accepted_corpus_candidates = [_accepted_corpus_candidate(pattern) for pattern in stable_proposals]
    blocking_reasons = _sum_named_counts(patterns, "blocking_reasons")
    warning_counts = _sum_named_counts(patterns, "warning_counts")
    support_matrix = _build_support_matrix(patterns, policy)

    record_count = sum(int(pattern["counts"]["record_count"]) for pattern in patterns)
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "pattern_signature_version": SIGNATURE_SCHEMA_VERSION,
        "summary": {
            "record_count": record_count,
            "pattern_count": len(patterns),
            "stable_proposal_pattern_count": len(stable_proposals),
            "stable_proposal_record_count": sum(int(pattern["counts"]["record_count"]) for pattern in stable_proposals),
            "sfx_candidate_pattern_count": len(sfx_candidates),
            "blocking_pattern_count": sum(bool(pattern["blocking_reasons"]) for pattern in patterns),
            "warning_pattern_count": sum(bool(pattern["warning_counts"]) for pattern in patterns),
        },
        "counts": {
            "records_by_asset_kind": _record_counts_by_signature(patterns, "asset_kind"),
            "records_by_frame_type": _record_counts_by_signature(patterns, "frame_type"),
            "patterns_by_support_level": _pattern_counts_by_status(patterns, "support_level"),
        },
        "blocking_reasons": blocking_reasons,
        "warning_counts": warning_counts,
        "support_matrix": support_matrix,
        "accepted_corpus_candidates": accepted_corpus_candidates,
        "stable_proposals": stable_proposals,
        "sfx_candidates": sfx_candidates,
        "patterns": patterns,
    }


def render_pattern_markdown(report: Mapping[str, Any]) -> str:
    """把 pattern JSON 报告渲染为确定性 Markdown。

    Args:
        report: `build_pattern_report` 返回的报告。

    Returns:
        以换行结尾的 Markdown 文本。
    """

    summary = _mapping_or_empty(report.get("summary"))
    lines = [
        "# Translation Pattern Inventory",
        "",
        f"- Report schema: `{report.get('report_schema_version', '')}`",
        f"- Signature schema: `{report.get('pattern_signature_version', '')}`",
        f"- Records: {summary.get('record_count', 0)}",
        f"- Patterns: {summary.get('pattern_count', 0)}",
        f"- Stable proposals: {summary.get('stable_proposal_pattern_count', 0)}",
        f"- SFX candidates: {summary.get('sfx_candidate_pattern_count', 0)}",
        "",
    ]
    lines.extend(_markdown_support_matrix(report.get("support_matrix")))
    lines.extend(_markdown_top_sfx_candidates(report.get("sfx_candidates"), limit=20))
    lines.extend(_markdown_count_table("Blocking reasons", report.get("blocking_reasons")))
    lines.extend(_markdown_count_table("Warning counts", report.get("warning_counts")))
    lines.extend(_markdown_accepted_candidates(report.get("accepted_corpus_candidates")))
    return "\n".join(lines).rstrip() + "\n"


def write_pattern_report(report: Mapping[str, Any], output_json: Path, output_md: Path) -> None:
    """分别写出 JSON 与 Markdown pattern 报告。

    Args:
        report: pattern 报告对象。
        output_json: JSON 输出路径。
        output_md: Markdown 输出路径。
    """

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output_md.write_text(render_pattern_markdown(report), encoding="utf-8")


def _build_pattern(
    pattern_id: str,
    signature: Mapping[str, Any],
    rows: list[tuple[dict[str, Any], Mapping[str, Any]]],
    policy: PatternPolicy,
) -> dict[str, Any]:
    """聚合一个 canonical signature 下的全部记录。"""

    assessments = [(record, _assess_record(record, policy)) for _signature, record in rows]
    blocking = Counter(reason for _record, result in assessments for reason in result.blocking_reasons)
    warnings = Counter(warning for _record, result in assessments for warning in result.warnings)
    eligible_count = sum(not result.blocking_reasons for _record, result in assessments)
    status = _pattern_status(signature, policy)
    stable = (
        signature.get("asset_kind") == policy.stable_asset_kind
        and signature.get("frame_type") in policy.stable_frames
        and eligible_count == len(assessments)
    )
    examples = [
        _representative_example(record, result)
        for record, result in sorted(assessments, key=lambda item: _record_sort_key(item[0]))[: policy.example_limit]
    ]
    frame_type = _string_or_none(signature.get("frame_type"))
    required_roles = list(FRAME_REGISTRY[frame_type].required_roles) if frame_type in FRAME_REGISTRY else []
    return {
        "pattern_id": pattern_id,
        "signature": dict(signature),
        "support_level": status["support_level"],
        "proposed_support_level": status["proposed_support_level"],
        "review_status": status["review_status"],
        "required_roles": required_roles,
        "eligibility": {
            "stable": stable,
            "eligible_record_count": eligible_count,
        },
        "counts": {
            "record_count": len(assessments),
            "blocked_record_count": len(assessments) - eligible_count,
        },
        "blocking_reasons": dict(sorted(blocking.items())),
        "warning_counts": dict(sorted(warnings.items())),
        "representative_examples": examples,
    }


def _assess_record(record: Mapping[str, Any], policy: PatternPolicy) -> RecordAssessment:
    """评估一条记录是否满足 Stable proposal 的全部硬条件。"""

    blockers: list[str] = []
    warnings: list[str] = []
    raw = _string_or_none(record.get("raw_event_name")) or ""
    asset_kind = parse_shell(raw).asset_kind or "unknown"
    if asset_kind != "vo":
        blockers.append("asset_kind_not_vo")

    quality = record.get("quality")
    unknown_count = quality.get("unknown_count") if isinstance(quality, dict) else None
    if not isinstance(unknown_count, int) or isinstance(unknown_count, bool):
        blockers.append("quality_unknown_count_invalid")
    elif unknown_count > 0:
        blockers.append("quality_unknowns_present")

    entity = record.get("entity")
    if not isinstance(entity, dict) or (_string_or_none(entity.get("kind")) or "unknown").lower() == "unknown":
        blockers.append("unknown_entity")

    translation = record.get("translation")
    if not isinstance(translation, dict):
        return RecordAssessment(tuple(sorted(set((*blockers, "missing_translation")))), (), "", "")

    frame_type = _string_or_none(translation.get("frame_type"))
    if frame_type is None:
        blockers.append("missing_frame_type")
    elif frame_type not in FRAME_REGISTRY:
        blockers.append("unsupported_frame_type")
    elif frame_type not in policy.stable_frames:
        blockers.append("frame_not_stable_candidate")
    else:
        semantic = record.get("semantic")
        semantic_profile = (
            _string_or_none(semantic.get("profile")) if isinstance(semantic, dict) else None
        ) or "unknown"
        if semantic_profile not in policy.stable_frames[frame_type].semantic_profiles:
            blockers.append("semantic_profile_not_selected")

    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
        blockers.append("missing_arguments")

    required_roles = FRAME_REGISTRY[frame_type].required_roles if frame_type in FRAME_REGISTRY else ()
    for role in required_roles:
        argument = arguments.get(role)
        if not isinstance(argument, dict):
            blockers.append(f"missing_required_role:{role}")
            continue
        confidence = (_string_or_none(argument.get("confidence")) or "unknown").lower()
        if confidence in UNTRUSTED_CONFIDENCE:
            blockers.append(f"untrusted_required_role:{role}")

    for role, argument in arguments.items():
        if not isinstance(role, str) or not isinstance(argument, dict):
            continue
        if argument.get("fallback") is True:
            blockers.append(f"fallback_argument:{role}")
        labels = argument.get("labels")
        zh_cn = labels.get("zh_CN") if isinstance(labels, dict) else None
        if not isinstance(zh_cn, str) or not zh_cn.strip():
            blockers.append(f"missing_zh_cn_label:{role}")

    rendered: dict[str, Any] = {}
    for mode in ("default", "main"):
        result = render_translation(dict(translation), RenderOptions(locale="zh_CN", mode=mode))
        rendered[mode] = result
        if not result.text.strip():
            blockers.append(f"empty_zh_cn_{mode}")
        for diagnostic in result.diagnostics:
            if diagnostic.startswith("missing_role:"):
                role = diagnostic.partition(":")[2]
                if role not in required_roles:
                    warnings.append(f"renderer_optional_missing_role:{role}")
                    continue
            blockers.append(f"renderer_diagnostic:{diagnostic}")

    diagnostics = quality.get("diagnostics") if isinstance(quality, dict) else None
    if isinstance(diagnostics, list):
        warnings.extend(f"quality_diagnostic:{_diagnostic_code(item)}" for item in diagnostics)

    return RecordAssessment(
        tuple(sorted(set(blockers))),
        tuple(sorted(set(warnings))),
        rendered["default"].text,
        rendered["main"].text,
    )


def _pattern_status(signature: Mapping[str, Any], policy: PatternPolicy) -> dict[str, str | None]:
    """按资产类型与 frame 返回策略状态。"""

    asset_kind = signature.get("asset_kind")
    frame_type = signature.get("frame_type")
    if asset_kind == policy.stable_asset_kind and isinstance(frame_type, str):
        frame_policy = policy.stable_frames.get(frame_type)
        if frame_policy is not None:
            return {
                "support_level": frame_policy.support_level,
                "proposed_support_level": frame_policy.proposed_support_level,
                "review_status": frame_policy.review_status,
            }
    if asset_kind == "sfx":
        return {
            "support_level": policy.sfx_support_level,
            "proposed_support_level": None,
            "review_status": policy.sfx_review_status,
        }
    return {
        "support_level": policy.default_support_level,
        "proposed_support_level": None,
        "review_status": policy.default_review_status,
    }


def _representative_example(record: Mapping[str, Any], result: RecordAssessment) -> dict[str, str]:
    """提取不含本地路径的代表样例。"""

    return {
        "raw_event_name": _string_or_none(record.get("raw_event_name")) or "",
        "zh_cn_default": result.zh_cn_default,
        "zh_cn_main": result.zh_cn_main,
    }


def _record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    """返回与输入顺序无关的记录排序键。"""

    return (_string_or_none(record.get("raw_event_name")) or "", _canonical_json(record))


def _argument_key(argument: object) -> str | None:
    """读取 policy 允许进入 signature 的 translation argument key。"""

    if not isinstance(argument, dict):
        return None
    return _string_or_none(argument.get("key"))


def _canonical_json(value: Mapping[str, Any]) -> str:
    """返回用于签名或排序的 canonical JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    """读取必需 mapping 字段。"""

    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"policy 字段 {key} 必须是 mapping。")
    return value


def _mapping_or_empty(value: object) -> Mapping[str, Any]:
    """把 mapping 安全规整为字符串键视图或空 mapping。"""

    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items()}


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    """读取必需非空字符串字段。"""

    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"policy 字段 {key} 必须是非空字符串。")
    return value


def _string_or_none(value: object) -> str | None:
    """把非空字符串安全规整为字符串或 None。"""

    return value if isinstance(value, str) and value else None


def _stable_scalar(value: object, *, missing: str) -> str | int:
    """保留 canonical signature 可接受的稳定标量，否则返回缺失标记。"""

    if isinstance(value, bool):
        return missing
    if isinstance(value, (str, int)) and value != "":
        return value
    return missing


def _diagnostic_code(value: object) -> str:
    """把 quality diagnostic 规整为稳定计数键。"""

    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict):
        code = value.get("code")
        if isinstance(code, str) and code:
            return code
    return "unknown"


def _sum_named_counts(patterns: list[dict[str, Any]], key: str) -> dict[str, int]:
    """合并各 pattern 的命名计数。"""

    counter: Counter[str] = Counter()
    for pattern in patterns:
        values = pattern.get(key)
        if isinstance(values, dict):
            counter.update({str(name): int(count) for name, count in values.items()})
    return dict(sorted(counter.items()))


def _record_counts_by_signature(patterns: list[dict[str, Any]], key: str) -> dict[str, int]:
    """按 signature 字段汇总记录数。"""

    counter: Counter[str] = Counter()
    for pattern in patterns:
        signature = pattern["signature"]
        counter[str(signature.get(key, "unknown"))] += int(pattern["counts"]["record_count"])
    return dict(sorted(counter.items()))


def _pattern_counts_by_status(patterns: list[dict[str, Any]], key: str) -> dict[str, int]:
    """按 pattern 状态汇总 pattern 数。"""

    counter = Counter(str(pattern.get(key, "unknown")) for pattern in patterns)
    return dict(sorted(counter.items()))


def _build_support_matrix(patterns: list[dict[str, Any]], policy: PatternPolicy) -> dict[str, Any]:
    """按 frame 汇总支持状态、阻断量与 VO eligible coverage。"""

    frame_types = {str(_pattern_signature(pattern).get("frame_type", "missing")) for pattern in patterns}.union(
        policy.stable_frames
    )
    frames = [_frame_support_row(frame_type, patterns, policy) for frame_type in sorted(frame_types)]
    vo_patterns = [pattern for pattern in patterns if _pattern_signature(pattern).get("asset_kind") == "vo"]
    eligible_vo = [pattern for pattern in vo_patterns if _pattern_is_stable(pattern)]
    vo_record_count = sum(_pattern_record_count(pattern) for pattern in vo_patterns)
    eligible_record_count = sum(_pattern_record_count(pattern) for pattern in eligible_vo)
    return {
        "frames": frames,
        "vo_eligible_coverage": {
            "total_pattern_count": len(vo_patterns),
            "total_record_count": vo_record_count,
            "eligible_stable_pattern_count": len(eligible_vo),
            "eligible_stable_record_count": eligible_record_count,
            "pattern_ratio": _safe_ratio(len(eligible_vo), len(vo_patterns)),
            "record_ratio": _safe_ratio(eligible_record_count, vo_record_count),
        },
    }


def _frame_support_row(
    frame_type: str,
    patterns: list[dict[str, Any]],
    policy: PatternPolicy,
) -> dict[str, Any]:
    """生成单个 frame 的 support matrix 行。"""

    frame_patterns = [pattern for pattern in patterns if _pattern_signature(pattern).get("frame_type") == frame_type]
    vo_patterns = [pattern for pattern in frame_patterns if _pattern_signature(pattern).get("asset_kind") == "vo"]
    sfx_patterns = [pattern for pattern in frame_patterns if _pattern_signature(pattern).get("asset_kind") == "sfx"]
    eligible_vo = [pattern for pattern in vo_patterns if _pattern_is_stable(pattern)]
    blocked = [pattern for pattern in frame_patterns if bool(pattern.get("blocking_reasons"))]
    blocked_vo = [pattern for pattern in vo_patterns if not _pattern_is_stable(pattern)]
    frame_policy = policy.stable_frames.get(frame_type)
    vo_record_count = sum(_pattern_record_count(pattern) for pattern in vo_patterns)
    eligible_record_count = sum(_pattern_record_count(pattern) for pattern in eligible_vo)
    return {
        "frame_type": frame_type,
        "support_level": frame_policy.support_level if frame_policy is not None else policy.default_support_level,
        "proposed_support_level": frame_policy.proposed_support_level if frame_policy is not None else None,
        "review_status": frame_policy.review_status if frame_policy is not None else policy.default_review_status,
        "total_pattern_count": len(frame_patterns),
        "total_record_count": sum(_pattern_record_count(pattern) for pattern in frame_patterns),
        "vo_pattern_count": len(vo_patterns),
        "vo_record_count": vo_record_count,
        "eligible_stable_pattern_count": len(eligible_vo),
        "eligible_stable_record_count": eligible_record_count,
        "blocked_pattern_count": len(blocked),
        "blocked_record_count": sum(_pattern_record_count(pattern) for pattern in blocked),
        "blocked_vo_pattern_count": len(blocked_vo),
        "blocked_vo_record_count": sum(_pattern_record_count(pattern) for pattern in blocked_vo),
        "sfx_candidate_pattern_count": len(sfx_patterns),
        "sfx_candidate_record_count": sum(_pattern_record_count(pattern) for pattern in sfx_patterns),
        "vo_eligible_pattern_coverage": _safe_ratio(len(eligible_vo), len(vo_patterns)),
        "vo_eligible_record_coverage": _safe_ratio(eligible_record_count, vo_record_count),
    }


def _accepted_corpus_candidate(pattern: Mapping[str, Any]) -> dict[str, Any]:
    """从 eligible Stable pattern 的首个确定性样例生成待审 corpus 候选。"""

    signature = _mapping_or_empty(pattern.get("signature"))
    examples = pattern.get("representative_examples")
    example = _mapping_or_empty(examples[0]) if isinstance(examples, list) and examples else {}
    roles = signature.get("roles")
    expected_roles = [str(role) for role in roles] if isinstance(roles, list) else []
    argument_keys = _mapping_or_empty(signature.get("argument_keys"))
    required_roles = pattern.get("required_roles")
    return {
        "pattern_id": _string_or_none(pattern.get("pattern_id")) or "",
        "frame_type": _string_or_none(signature.get("frame_type")) or "missing",
        "signature": dict(signature),
        "required_roles": list(required_roles) if isinstance(required_roles, list) else [],
        "expected_arguments": {
            "roles": expected_roles,
            "signature_keys": dict(sorted((str(key), value) for key, value in argument_keys.items())),
        },
        "raw_event_name": _string_or_none(example.get("raw_event_name")) or "",
        "zh_cn_default": _string_or_none(example.get("zh_cn_default")) or "",
        "zh_cn_main": _string_or_none(example.get("zh_cn_main")) or "",
        "review_status": "pending",
    }


def _pattern_signature(pattern: Mapping[str, Any]) -> Mapping[str, Any]:
    """安全读取 pattern signature。"""

    return _mapping_or_empty(pattern.get("signature"))


def _pattern_record_count(pattern: Mapping[str, Any]) -> int:
    """安全读取 pattern record_count。"""

    value = _mapping_or_empty(pattern.get("counts")).get("record_count")
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _pattern_is_stable(pattern: Mapping[str, Any]) -> bool:
    """判断 pattern 是否为 eligible Stable proposal。"""

    return _mapping_or_empty(pattern.get("eligibility")).get("stable") is True


def _review_sort_key(pattern: Mapping[str, Any]) -> tuple[str, str, str]:
    """按 frame、canonical signature、pattern id 组织人工 Review 顺序。"""

    signature = _pattern_signature(pattern)
    return (
        _string_or_none(signature.get("frame_type")) or "missing",
        _canonical_json(signature),
        _string_or_none(pattern.get("pattern_id")) or "",
    )


def _safe_ratio(numerator: int, denominator: int) -> float:
    """返回确定性六位小数比例，零分母返回 0。"""

    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 6)


def _markdown_support_matrix(value: object) -> list[str]:
    """渲染前置 frame/support/coverage 汇总。"""

    matrix = _mapping_or_empty(value)
    coverage = _mapping_or_empty(matrix.get("vo_eligible_coverage"))
    lines = [
        "## Support matrix",
        "",
        "VO eligible coverage: "
        f"patterns {coverage.get('eligible_stable_pattern_count', 0)}/{coverage.get('total_pattern_count', 0)} "
        f"({coverage.get('pattern_ratio', 0)}), records "
        f"{coverage.get('eligible_stable_record_count', 0)}/{coverage.get('total_record_count', 0)} "
        f"({coverage.get('record_ratio', 0)}).",
        "",
        "| Frame | Support | Proposed | All patterns | All records | VO patterns | VO records | Eligible VO patterns | Eligible VO records | Blocked VO patterns | Blocked VO records | SFX patterns | VO pattern coverage |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    frames = matrix.get("frames")
    if isinstance(frames, list):
        for raw_row in frames:
            row = _mapping_or_empty(raw_row)
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(item)
                    for item in (
                        row.get("frame_type", ""),
                        row.get("support_level", ""),
                        row.get("proposed_support_level") or "-",
                        row.get("total_pattern_count", 0),
                        row.get("total_record_count", 0),
                        row.get("vo_pattern_count", 0),
                        row.get("vo_record_count", 0),
                        row.get("eligible_stable_pattern_count", 0),
                        row.get("eligible_stable_record_count", 0),
                        row.get("blocked_vo_pattern_count", 0),
                        row.get("blocked_vo_record_count", 0),
                        row.get("sfx_candidate_pattern_count", 0),
                        row.get("vo_eligible_pattern_coverage", 0),
                    )
                )
                + " |"
            )
    return [*lines, ""]


def _markdown_top_sfx_candidates(value: object, *, limit: int) -> list[str]:
    """按记录量展示有限的 SFX candidate 代表摘要。"""

    raw_candidates = value if isinstance(value, list) else []
    candidates = [candidate for candidate in raw_candidates if isinstance(candidate, dict)]
    ordered = sorted(
        candidates,
        key=lambda pattern: (
            -_pattern_record_count(pattern),
            _string_or_none(pattern.get("pattern_id")) or "",
        ),
    )[:limit]
    lines = [
        "## Top SFX candidates",
        "",
        f"Representative summary only; showing at most {limit} candidates. The JSON report retains the full SFX list.",
        "",
    ]
    if not ordered:
        return [*lines, "None.", ""]
    lines.extend(
        [
            "| Pattern ID | Frame | Profile | Records | Representative raw | Support | Review |",
            "| --- | --- | --- | ---: | --- | --- | --- |",
        ]
    )
    for pattern in ordered:
        signature = _pattern_signature(pattern)
        examples = pattern.get("representative_examples")
        example = _mapping_or_empty(examples[0]) if isinstance(examples, list) and examples else {}
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(item)
                for item in (
                    pattern.get("pattern_id", ""),
                    signature.get("frame_type", ""),
                    signature.get("semantic_profile", ""),
                    _pattern_record_count(pattern),
                    example.get("raw_event_name", ""),
                    pattern.get("support_level", "experimental"),
                    pattern.get("review_status", "pending"),
                )
            )
            + " |"
        )
    return [*lines, ""]


def _markdown_accepted_candidates(value: object) -> list[str]:
    """渲染全部待人工 Review 的 Stable proposal corpus 候选明细。"""

    candidates = value if isinstance(value, list) else []
    lines = ["## Stable proposal details", ""]
    if not candidates:
        return [*lines, "None.", ""]
    lines.extend(
        [
            "All rows remain pending review; none are accepted corpus entries.",
            "",
            "| Pattern ID | Frame | Signature family | Required roles | Expected arguments | Representative raw | zh_CN default | zh_CN main | Review |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for raw_candidate in candidates:
        candidate = _mapping_or_empty(raw_candidate)
        signature = _mapping_or_empty(candidate.get("signature"))
        expected = _mapping_or_empty(candidate.get("expected_arguments"))
        roles = expected.get("roles")
        role_text = ", ".join(str(role) for role in roles) if isinstance(roles, list) else ""
        signature_keys = _mapping_or_empty(expected.get("signature_keys"))
        key_text = ", ".join(
            f"{key}={value if value is not None else 'missing'}" for key, value in signature_keys.items()
        )
        expected_text = f"roles: {role_text or '-'}; keys: {key_text or '-'}"
        required_roles = candidate.get("required_roles")
        required_role_text = ", ".join(str(role) for role in required_roles) if isinstance(required_roles, list) else ""
        signature_family = "/".join(
            str(signature.get(key, "unknown")) for key in ("semantic_profile", "primary_family", "trigger_family")
        )
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(item)
                for item in (
                    candidate.get("pattern_id", ""),
                    candidate.get("frame_type", ""),
                    signature_family,
                    required_role_text,
                    expected_text,
                    candidate.get("raw_event_name", ""),
                    candidate.get("zh_cn_default", ""),
                    candidate.get("zh_cn_main", ""),
                    candidate.get("review_status", "pending"),
                )
            )
            + " |"
        )
    return [*lines, ""]


def _markdown_count_table(title: str, value: object) -> list[str]:
    """渲染命名计数表。"""

    counts = value if isinstance(value, dict) else {}
    lines = [f"## {title}", ""]
    if not counts:
        return [*lines, "None.", ""]
    lines.extend(["| Name | Count |", "| --- | ---: |"])
    lines.extend(f"| {_markdown_cell(name)} | {count} |" for name, count in sorted(counts.items()))
    return [*lines, ""]


def _markdown_cell(value: object) -> str:
    """转义 Markdown 表格单元格。"""

    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
