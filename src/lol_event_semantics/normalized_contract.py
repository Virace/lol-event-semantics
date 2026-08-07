"""normalized V2 公共记录字段与确定性身份。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Iterable, Mapping

from .decomposition_lexer import parse_shell
from .decomposition_model import EventDecompositionRecord
from .normalized_translation_model import FRAME_REGISTRY, RenderOptions
from .normalized_translation_renderer import render_translation
from .pattern_catalog import (
    PatternPolicy,
    build_pattern_report,
    canonical_pattern_signature,
    pattern_id_for_signature,
)

RECORD_ID_ALGORITHM_VERSION = "lol-event-semantics.record-id.v1"
RECORD_ID_PREFIX = "record-v1-"
CONTROL_ASSET_KINDS = {"control", "rtpc", "state", "switch"}
WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:/")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    """不含本机绝对路径的来源身份与公开 provenance。"""

    domains: tuple[str, ...]
    canonical_assets: tuple[str, ...]
    provenance_paths: tuple[str, ...]

    @property
    def scope(self) -> str:
        """返回可查询的稳定来源范围。"""

        return "+".join(self.domains) if self.domains else "unknown"

    def to_provenance(self) -> dict[str, list[str]]:
        """返回 normalized record 使用的公开 provenance。"""

        return {
            "source_domains": list(self.domains),
            "canonical_assets": list(self.canonical_assets),
            "source_paths": list(self.provenance_paths),
        }


def merge_equivalent_decomposition_records(
    records: Iterable[EventDecompositionRecord],
) -> list[EventDecompositionRecord]:
    """合并仅因输出分片重叠而重复的等价拆解记录。

    Args:
        records: 从一个或多个 decomposition JSONL 读取的记录。

    Returns:
        保持首次出现顺序的去重记录；等价记录的 source_paths 取稳定并集。

    Raises:
        ValueError: SHA-256 摘要发生不可解释碰撞。
    """

    merged: list[EventDecompositionRecord] = []
    positions: dict[str, int] = {}
    canonical_payloads: dict[str, str] = {}
    for record in records:
        canonical_payload = _decomposition_identity_payload(record)
        digest = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()
        position = positions.get(digest)
        if position is None:
            positions[digest] = len(merged)
            canonical_payloads[digest] = canonical_payload
            record.source_paths = sorted(set(record.source_paths))
            merged.append(record)
            continue
        if canonical_payloads[digest] != canonical_payload:
            raise ValueError(f"decomposition identity digest collision: {digest}")
        existing = merged[position]
        existing.source_paths = sorted(set((*existing.source_paths, *record.source_paths)))
    return merged


def source_identity_from_paths(source_paths: Iterable[str]) -> SourceIdentity:
    """从来源路径提取不受工作目录影响的 domain、asset 与 provenance。"""

    domains: set[str] = set()
    canonical_assets: set[str] = set()
    provenance_paths: set[str] = set()
    for source_path in source_paths:
        provenance_path, domain, canonical_asset = _canonical_source_path(str(source_path))
        provenance_paths.add(provenance_path)
        domains.add(domain)
        if canonical_asset is not None:
            canonical_assets.add(canonical_asset)
    if not domains:
        domains.add("unknown")
    return SourceIdentity(
        domains=tuple(sorted(domains)),
        canonical_assets=tuple(sorted(canonical_assets)),
        provenance_paths=tuple(sorted(provenance_paths)),
    )


def canonical_record_identity(record: Mapping[str, Any]) -> list[object]:
    """返回 record_id v1 的 canonical identity tuple。

    tuple 只包含不可变 raw、来源域/资产和最终 owner，不包含数组输入顺序、
    本机绝对路径、显示文本或生成时间。
    """

    raw_event_name = str(record.get("raw_event_name") or "")
    entity = record.get("entity")
    owner = entity if isinstance(entity, dict) else {}
    owner_kind = str(owner.get("kind") or "unknown")
    owner_alias = str(owner.get("alias") or "unknown")
    source_paths = record.get("source_paths")
    source_identity = source_identity_from_paths(source_paths if isinstance(source_paths, list) else [])
    return [
        RECORD_ID_ALGORITHM_VERSION,
        raw_event_name,
        list(source_identity.domains),
        [owner_kind, owner_alias],
        list(source_identity.canonical_assets),
    ]


def record_id_for_normalized_record(record: Mapping[str, Any]) -> str:
    """计算完整 SHA-256 normalized record id。"""

    payload = json.dumps(
        canonical_record_identity(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{RECORD_ID_PREFIX}{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def apply_normalized_contract(rows: list[dict[str, object]], policy: PatternPolicy) -> dict[str, Any]:
    """给兼容行增量写入 V2 identity、support、render 与 review 字段。

    Args:
        rows: 已完成最终 entity canonicalization 的 normalized 行。
        policy: P1 冻结的 pattern 与 promotion policy。

    Raises:
        ValueError: 两条公开记录得到相同 record_id，或 pattern report 缺项。

    Returns:
        与写入行使用同一批数据计算的确定性 pattern inventory report。
    """

    pattern_report = build_pattern_report(rows, policy)
    patterns = {
        str(pattern["pattern_id"]): pattern
        for pattern in pattern_report["patterns"]
        if isinstance(pattern, dict) and pattern.get("pattern_id")
    }
    seen_record_ids: dict[str, str] = {}
    for row in rows:
        pattern_id = pattern_id_for_signature(canonical_pattern_signature(row, policy))
        pattern = patterns.get(pattern_id)
        if pattern is None:
            raise ValueError(f"normalized pattern report 缺少 pattern_id: {pattern_id}")

        record_id = record_id_for_normalized_record(row)
        raw_event_name = str(row.get("raw_event_name") or "")
        previous_raw = seen_record_ids.get(record_id)
        if previous_raw is not None:
            raise ValueError(
                "normalized record_id collision: "
                f"{record_id} raw_event_name={raw_event_name!r} previous={previous_raw!r}"
            )
        seen_record_ids[record_id] = raw_event_name

        source_paths = row.get("source_paths")
        source_identity = source_identity_from_paths(source_paths if isinstance(source_paths, list) else [])
        asset_kind = normalized_asset_kind(raw_event_name)
        rendered_labels, render_status = _rendered_labels_and_status(row, asset_kind)
        translation = row.get("translation")

        row["record_id"] = record_id
        row["record_id_algorithm"] = RECORD_ID_ALGORITHM_VERSION
        row["source_scope"] = source_identity.scope
        row["provenance"] = source_identity.to_provenance()
        row["source_paths"] = list(source_identity.provenance_paths)
        row["asset_kind"] = asset_kind
        row["pattern_id"] = pattern_id
        row["support_level"] = str(pattern.get("support_level") or "experimental")
        row["review_status"] = str(pattern.get("review_status") or "pending")
        row["semantic_frame"] = _semantic_frame(translation)
        row["rendered_labels"] = rendered_labels
        row["render_status"] = render_status
        row["warnings"] = _record_warnings(row)
    return pattern_report


def build_public_pattern_catalog(
    pattern_report: Mapping[str, Any],
    policy: PatternPolicy,
) -> dict[str, object]:
    """从 P1 inventory 生成面向消费者的确定性 Pattern Catalog。"""

    patterns = pattern_report.get("patterns")
    public_patterns = (
        [_public_pattern(pattern, policy) for pattern in patterns if isinstance(pattern, dict)]
        if isinstance(patterns, list)
        else []
    )
    public_patterns.sort(key=lambda pattern: str(pattern["pattern_id"]))
    return {
        "schema_version": 1,
        "pattern_signature_version": pattern_report.get("pattern_signature_version"),
        "decision_id": policy.promotion_decisions.decision_id,
        "pattern_count": len(public_patterns),
        "patterns": public_patterns,
    }


def normalized_asset_kind(raw_event_name: str) -> str:
    """把 lexer asset kind 规整为公共四值枚举。"""

    asset_kind = (parse_shell(raw_event_name).asset_kind or "unknown").lower()
    return public_asset_kind(asset_kind)


def public_asset_kind(asset_kind: str) -> str:
    """把内部 asset namespace 规整为公共四值枚举。"""

    asset_kind = asset_kind.lower()
    if asset_kind in {"vo", "sfx"}:
        return asset_kind
    if asset_kind in CONTROL_ASSET_KINDS:
        return "control"
    return "unknown"


def _public_pattern(pattern: Mapping[str, Any], policy: PatternPolicy) -> dict[str, object]:
    """把 inventory pattern 投影为公共 Pattern Catalog 条目。"""

    signature = pattern.get("signature")
    signature_mapping = signature if isinstance(signature, dict) else {}
    frame_type = str(signature_mapping.get("frame_type") or "missing")
    frame = FRAME_REGISTRY.get(frame_type)
    required_roles = list(frame.required_roles) if frame is not None else []
    template_roles = (
        {role for locale_templates in frame.templates.values() for roles in locale_templates.values() for role in roles}
        if frame is not None
        else set()
    )
    optional_roles = sorted(template_roles.difference(required_roles))
    examples = pattern.get("representative_examples")
    example_event_names = (
        [
            str(example.get("raw_event_name"))
            for example in examples
            if isinstance(example, dict) and example.get("raw_event_name")
        ]
        if isinstance(examples, list)
        else []
    )
    counts = pattern.get("counts")
    count_mapping = counts if isinstance(counts, dict) else {}
    warning_counts = pattern.get("warning_counts")
    return {
        "pattern_id": str(pattern.get("pattern_id") or ""),
        "signature": dict(signature_mapping),
        "asset_kind": public_asset_kind(str(signature_mapping.get("asset_kind") or "unknown")),
        "frame_type": frame_type,
        "frame_version": signature_mapping.get("frame_version"),
        "required_roles": required_roles,
        "optional_roles": optional_roles,
        "support_level": str(pattern.get("support_level") or policy.default_support_level),
        "zh_CN_template": _public_frame_templates(frame_type, "zh_CN"),
        "en_US_template": _public_frame_templates(frame_type, "en_US"),
        "provenance_policy": {
            "stable_requires_no_fallback": True,
            "required_role_min_confidence": "medium",
            "formal_locale": "zh_CN",
        },
        "example_event_names": example_event_names,
        "record_count": int(count_mapping.get("record_count") or 0),
        "warning_counts": dict(warning_counts) if isinstance(warning_counts, dict) else {},
        "review_status": str(pattern.get("review_status") or policy.default_review_status),
        "introduced_in": "pattern-signature.v1",
        "superseded_by": None,
    }


def _public_frame_templates(frame_type: str, locale: str) -> dict[str, str] | None:
    """把 frame role 顺序编码为公共符号模板。"""

    frame = FRAME_REGISTRY.get(frame_type)
    if frame is None:
        return None
    templates = frame.templates.get(locale)
    if templates is None:
        return None
    return {mode: " ".join(f"{{{role}}}" for role in roles) for mode, roles in sorted(templates.items())}


def _decomposition_identity_payload(record: EventDecompositionRecord) -> str:
    """返回忽略 source_paths 的 decomposition 等价性 payload。"""

    payload = record.to_dict()
    payload["source_paths"] = []
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _canonical_source_path(source_path: str) -> tuple[str, str, str | None]:
    """把来源路径规整为仓库相对 provenance 与 canonical asset identity。"""

    normalized = source_path.replace("\\", "/")
    parts = [part for part in PurePosixPath(normalized).parts if part not in {"", ".", "/"}]
    folded = [part.casefold() for part in parts]
    marker = next(
        (index for index in range(len(parts) - 1) if folded[index : index + 2] == ["raw", "events"]),
        None,
    )
    if marker is not None:
        relative_parts = parts[marker:]
    elif normalized.startswith("/") or WINDOWS_ABSOLUTE_PATH.match(normalized):
        relative_parts = parts[-1:] or ["unknown"]
    else:
        relative_parts = parts or ["unknown"]

    provenance_path = "/".join(relative_parts)
    relative_folded = [part.casefold() for part in relative_parts]
    if len(relative_parts) >= 4 and relative_folded[:2] == ["raw", "events"]:
        domain = relative_folded[2]
        asset_path = PurePosixPath(*relative_parts[3:]).with_suffix("").as_posix().casefold()
        canonical_asset = f"{domain}:{asset_path}" if asset_path else None
        return provenance_path, domain, canonical_asset
    return provenance_path, "unknown", None


def _semantic_frame(translation: object) -> dict[str, object] | None:
    """从兼容 translation 派生正式 semantic_frame 视图。"""

    if not isinstance(translation, dict):
        return None
    return {
        "frame_type": translation.get("frame_type"),
        "frame_version": translation.get("frame_version"),
        "arguments": translation.get("arguments") if isinstance(translation.get("arguments"), dict) else {},
    }


def _rendered_labels_and_status(
    row: Mapping[str, object],
    asset_kind: str,
) -> tuple[dict[str, dict[str, str]], str]:
    """渲染 zh_CN 正式视图并返回四值 render_status。"""

    translation = row.get("translation")
    if not isinstance(translation, dict):
        status = "not_applicable" if asset_kind in {"control", "unknown"} else "missing"
        return {}, status

    default = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
    main = render_translation(translation, RenderOptions(locale="zh_CN", mode="main"))
    labels = {"zh_CN": {"default": default.text, "main": main.text}}
    if not default.text.strip() or not main.text.strip():
        return labels, "missing"
    arguments = translation.get("arguments")
    has_fallback = isinstance(arguments, dict) and any(
        isinstance(argument, dict) and argument.get("fallback") is True for argument in arguments.values()
    )
    diagnostics = (*default.diagnostics, *main.diagnostics)
    if has_fallback or any(_render_diagnostic_requires_fallback(item, translation) for item in diagnostics):
        return labels, "fallback"
    return labels, "rendered"


def _render_diagnostic_requires_fallback(diagnostic: str, translation: Mapping[str, object]) -> bool:
    """判断 renderer diagnostic 是否代表实质 fallback。"""

    if not diagnostic.startswith("missing_role:"):
        return True
    role = diagnostic.partition(":")[2]
    frame_type = translation.get("frame_type")
    if not isinstance(frame_type, str) or frame_type not in FRAME_REGISTRY:
        return True
    return role in FRAME_REGISTRY[frame_type].required_roles


def _record_warnings(row: Mapping[str, object]) -> list[str]:
    """把现有 quality diagnostics 投影为稳定去重 warning 列表。"""

    quality = row.get("quality")
    diagnostics = quality.get("diagnostics") if isinstance(quality, dict) else None
    if not isinstance(diagnostics, list):
        return []
    return sorted({str(item) for item in diagnostics if str(item)})
