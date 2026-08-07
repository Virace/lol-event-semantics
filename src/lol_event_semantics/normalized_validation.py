"""normalized 最终产物二次校验。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .dataset_identity import (
    CONTENT_DIGEST_VERSION,
    INPUT_IDENTITY_VERSION,
    content_digest,
    describe_payload_files,
    unmanaged_payload_paths,
)
from .decomposition_lexer import parse_shell
from .normalized_contract import (
    RECORD_ID_ALGORITHM_VERSION,
    record_id_for_normalized_record,
    source_identity_from_paths,
)
from .normalized_translation_model import (
    FRAME_REGISTRY,
    TRANSLATION_SCHEMA_VERSION,
    RenderOptions,
)
from .normalized_translation_renderer import render_translation
from .quality_metrics import is_fallback_label

TECHNICAL_DISPLAY_PATTERNS = (
    "Play_",
    "Stop_",
    "OnCast",
    "OnHit",
    "OnBuffActivate",
    "OnBuffDeactivate",
    "buffactivate",
    "buffdeactivate",
    "missilelaunch",
    "voProcessing",
    "2D",
    "3D",
)
TRANSLATION_REQUIRED_FIELDS = ("schema_version", "frame_type", "frame_version", "frame_confidence", "arguments")
SUPPORTED_TRANSLATION_PRIMARY_FAMILIES = {
    "skin_recall_state",
    "skin_idle_state",
    "skin_action_state",
    "gameplay_phase_control",
    "champion_status_state",
    "attack",
    "taunt",
    "joke",
    "laugh",
    "dance",
    "spell",
    "first_encounter",
    "kill",
    "move",
    "assist",
    # 批次3 self_action 家族（voice_event frame 承接；move/assist 已在上方）。
    "death",
    "buy_item",
    "recall",
    "respawn",
    "taunt_response",
    "ping",
    "joke_response",
    "use_item",
    "shop",
    "level_up",
    # 批次4 dialogue 家族（champion_dialogue_event frame 承接）。
    "dialogue",
    # 批次5a 播报员超族（announcer_event frame 承接；表外事件词干不建 frame，
    # translation_missing_for_supported_frame warning 因此上涨属已知接受）。
    "announcer",
    "audio_scope",
    "announcer_persona",
    "system_voice",
    # 批次5b media_base 动作状态路由（skin_action_state frame 承接；系统 UI 音、
    # 表外动作词与无相位记录不建 frame，warning 上涨属已知接受，同 5a 口径）。
    "media_base",
}
# display 无损性校验：detail 必须体现这三个壳动词的中文语义
# （unmute 的「取消静音」含「静音」，与 mute 共用同一判据）。
DISPLAY_DROP_SHELL_VERBS = {"stop", "mute", "unmute"}
# 交互对象类槽位键（考察 dist/normalized 全量 slots 键得出）：
# counterpart 来自 interaction/attack 家族，chat_partner 来自 chat 家族。
INTERACTION_PARTNER_SLOT_KEYS = ("counterpart", "chat_partner")
SUSPICIOUS_CHAMPION_ALIASES = {
    "aggro",
    "alert",
    "enemy",
    "ally",
    "neutral",
    "play",
    "sfx",
    "skin",
    "unknown",
}
SUPPORTED_MANIFEST_SCHEMA_VERSIONS = {1, 2}
SUPPORTED_RECORD_SCHEMA_VERSIONS = {1, 2}
RECORD_ID_PATTERN = re.compile(r"^record-v1-[0-9a-f]{64}$")
PATTERN_ID_PATTERN = re.compile(r"^pattern-v1-[0-9a-f]{64}$")
V2_RECORD_REQUIRED_FIELDS = {
    "record_id",
    "record_id_algorithm",
    "source_scope",
    "provenance",
    "asset_kind",
    "pattern_id",
    "support_level",
    "semantic_frame",
    "rendered_labels",
    "render_status",
    "review_status",
    "warnings",
}
V2_REQUIRED_OUTPUTS = {
    "manifest.json",
    "normalized-events.jsonl",
    "pattern-catalog.json",
    "entity-summary.json",
    "by-entity/",
    "schema/",
    "examples/",
}


@dataclass(slots=True)
class NormalizedValidationIssue:
    """normalized 校验问题。"""

    severity: str
    code: str
    path: str
    message: str
    raw_event_name: str | None = None

    def to_dict(self) -> dict[str, object]:
        """返回 JSON 友好的字典。"""

        return asdict(self)


@dataclass(slots=True)
class NormalizedValidationReport:
    """normalized 校验报告。"""

    normalized_root: str
    entity_count: int
    record_count: int
    error_count: int
    warning_count: int
    issues: list[NormalizedValidationIssue]

    def to_dict(self) -> dict[str, object]:
        """返回 JSON 友好的报告。"""

        return {
            "summary": {
                "normalized_root": self.normalized_root,
                "entity_count": self.entity_count,
                "record_count": self.record_count,
                "error_count": self.error_count,
                "warning_count": self.warning_count,
            },
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_normalized_root(root: Path) -> NormalizedValidationReport:
    """校验 normalized 输出根目录。

    Args:
        root: normalized 输出目录。

    Returns:
        校验报告，硬错误和质量警告分开统计。
    """

    issues: list[NormalizedValidationIssue] = []
    manifest = read_json_object(root / "manifest.json", issues)
    summary = read_json_array(root / "entity-summary.json", issues)
    manifest_schema_version = manifest.get("schema_version")
    shard_rows: dict[str, list[dict[str, Any]]] = {}

    for path in sorted((root / "by-entity").rglob("*.jsonl")) if (root / "by-entity").exists() else []:
        rel_path = relative_path(root, path)
        shard_rows[rel_path] = read_jsonl_records(path, root, issues)

    validate_manifest(root, manifest, summary, shard_rows, issues)
    validate_summary(root, summary, shard_rows, issues)

    seen_raw_names: dict[str, str] = {}
    seen_record_ids: dict[str, str] = {}
    summary_paths_by_entity = summary_entity_paths(summary)
    for rel_path, rows in shard_rows.items():
        for row_index, row in enumerate(rows, start=1):
            row_path = f"{rel_path}:{row_index}"
            validate_record(
                row,
                row_path,
                rel_path,
                summary_paths_by_entity,
                seen_raw_names,
                seen_record_ids,
                issues,
            )

    if manifest_schema_version == 2:
        validate_v2_dataset(root, manifest, shard_rows, issues)

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")

    return NormalizedValidationReport(
        normalized_root=str(root),
        entity_count=len(summary),
        record_count=sum(len(rows) for rows in shard_rows.values()),
        error_count=error_count,
        warning_count=warning_count,
        issues=issues,
    )


def read_json_object(path: Path, issues: list[NormalizedValidationIssue]) -> dict[str, Any]:
    """读取 JSON object。"""

    if not path.is_file():
        issues.append(error("missing_file", str(path), "缺少必需 JSON 文件。"))
        return {}

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(error("invalid_json", str(path), f"JSON 解析失败：{exc}"))
        return {}

    if not isinstance(value, dict):
        issues.append(error("invalid_json_shape", str(path), "文件内容必须是 JSON object。"))
        return {}

    return value


def read_json_array(path: Path, issues: list[NormalizedValidationIssue]) -> list[dict[str, Any]]:
    """读取 JSON object 数组。"""

    if not path.is_file():
        issues.append(error("missing_file", str(path), "缺少必需 JSON 文件。"))
        return []

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issues.append(error("invalid_json", str(path), f"JSON 解析失败：{exc}"))
        return []

    if not isinstance(value, list):
        issues.append(error("invalid_json_shape", str(path), "文件内容必须是 JSON array。"))
        return []

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if isinstance(item, dict):
            rows.append(item)
        else:
            issues.append(error("invalid_summary_row", f"{path}:{index}", "summary 行必须是 object。"))

    return rows


def read_jsonl_records(path: Path, root: Path, issues: list[NormalizedValidationIssue]) -> list[dict[str, Any]]:
    """读取 normalized JSONL 分片。"""

    rows: list[dict[str, Any]] = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            issues.append(error("blank_jsonl_line", f"{relative_path(root, path)}:{index}", "JSONL 不应包含空行。"))
            continue

        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(error("invalid_jsonl", f"{relative_path(root, path)}:{index}", f"JSONL 解析失败：{exc}"))
            continue

        if isinstance(item, dict):
            rows.append(item)
        else:
            issues.append(error("invalid_record_shape", f"{relative_path(root, path)}:{index}", "记录必须是 object。"))

    return rows


def validate_manifest(
    root: Path,
    manifest: dict[str, Any],
    summary: list[dict[str, Any]],
    shard_rows: dict[str, list[dict[str, Any]]],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 manifest 与实际分片一致。"""

    actual_record_count = sum(len(rows) for rows in shard_rows.values())
    actual_entity_count = len(shard_rows)

    schema_version = manifest.get("schema_version")
    if schema_version not in SUPPORTED_MANIFEST_SCHEMA_VERSIONS:
        issues.append(
            error(
                "manifest_schema_version_invalid",
                "manifest.json",
                f"不支持的 manifest schema_version: {schema_version!r}。",
            )
        )

    expected_full_index = schema_version == 2
    if (
        schema_version in SUPPORTED_MANIFEST_SCHEMA_VERSIONS
        and manifest.get("contains_full_raw_event_index") is not expected_full_index
    ):
        issues.append(
            error(
                "manifest_full_index_flag_invalid",
                "manifest.json",
                f"manifest v{schema_version} contains_full_raw_event_index 必须为 {str(expected_full_index).lower()}。",
            )
        )

    if manifest.get("record_count") != actual_record_count:
        issues.append(
            error(
                "manifest_record_count_mismatch",
                "manifest.json",
                f"manifest record_count={manifest.get('record_count')}，实际 JSONL 行数={actual_record_count}。",
            )
        )

    if manifest.get("entity_count") != actual_entity_count:
        issues.append(
            error(
                "manifest_entity_count_mismatch",
                "manifest.json",
                f"manifest entity_count={manifest.get('entity_count')}，实际分片数={actual_entity_count}。",
            )
        )

    if len(summary) != actual_entity_count:
        issues.append(error("summary_entity_count_mismatch", "entity-summary.json", "summary 行数与实际分片数不一致。"))

    required_outputs = (
        V2_REQUIRED_OUTPUTS if schema_version == 2 else {"manifest.json", "entity-summary.json", "by-entity/"}
    )
    for required in sorted(required_outputs):
        if required not in (manifest.get("outputs") or []):
            issues.append(error("manifest_output_missing", "manifest.json", f"outputs 缺少 {required}。"))

    if not (root / "by-entity").exists():
        issues.append(error("missing_by_entity_root", "by-entity", "缺少 by-entity 输出目录。"))


def validate_summary(
    root: Path,
    summary: list[dict[str, Any]],
    shard_rows: dict[str, list[dict[str, Any]]],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 entity-summary 与分片一致。"""

    summary_paths: set[str] = set()
    aliases_by_casefold: dict[tuple[str, str], str] = {}
    for index, row in enumerate(summary, start=1):
        row_path = f"entity-summary.json:{index}"
        path_value = row.get("path")

        if not isinstance(path_value, str):
            issues.append(error("summary_path_missing", row_path, "summary 行缺少 path。"))
            continue

        summary_paths.add(path_value)
        if path_value not in shard_rows:
            issues.append(error("summary_path_missing_shard", row_path, f"summary 指向不存在的分片：{path_value}。"))
            continue

        if row.get("record_count") != len(shard_rows[path_value]):
            issues.append(
                error(
                    "summary_record_count_mismatch",
                    row_path,
                    f"summary record_count={row.get('record_count')}，实际分片行数={len(shard_rows[path_value])}。",
                )
            )

        kind = string_or_none(row.get("kind"))
        alias = string_or_none(row.get("alias"))
        if kind is not None and alias is not None:
            alias_key = (kind, alias.casefold())
            existing = aliases_by_casefold.get(alias_key)
            if existing is not None and existing != alias:
                issues.append(
                    error(
                        "case_variant_entity_alias",
                        row_path,
                        f"同一 kind={kind} 下存在只差大小写的实体 alias：{existing} / {alias}。",
                    )
                )
            aliases_by_casefold.setdefault(alias_key, alias)

        if kind is not None and not path_value.startswith(f"by-entity/{plural_entity_kind(kind)}/"):
            issues.append(error("summary_path_kind_mismatch", row_path, f"summary path 与 kind={kind} 不匹配。"))

        if not path_value.endswith(".jsonl"):
            issues.append(error("summary_path_suffix_invalid", row_path, "summary path 必须指向 JSONL 分片。"))

    for rel_path in sorted(set(shard_rows) - summary_paths):
        issues.append(error("orphan_shard", rel_path, "分片没有出现在 entity-summary.json 中。"))


def validate_record(
    row: dict[str, Any],
    row_path: str,
    shard_rel_path: str,
    summary_paths_by_entity: dict[tuple[str, str], str],
    seen_raw_names: dict[str, str],
    seen_record_ids: dict[str, str],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验单条 normalized 记录。"""

    raw = string_or_none(row.get("raw_event_name"))
    if raw is None:
        issues.append(error("record_raw_event_name_missing", row_path, "记录缺少 raw_event_name。"))

    for key in ["schema_version", "game_version", "entity", "semantic", "display", "quality", "source_paths"]:
        if key not in row:
            issues.append(error("record_required_field_missing", row_path, f"记录缺少 {key}。", raw))

    schema_version = row.get("schema_version")
    if schema_version not in SUPPORTED_RECORD_SCHEMA_VERSIONS:
        issues.append(
            error(
                "record_schema_version_invalid",
                row_path,
                f"不支持的记录 schema_version: {schema_version!r}。",
                raw,
            )
        )
    elif schema_version == 2:
        validate_v2_record_fields(row, row_path, seen_record_ids, issues, raw)

    entity = row.get("entity")
    if isinstance(entity, dict):
        validate_record_entity(entity, row_path, shard_rel_path, summary_paths_by_entity, issues, raw)
    else:
        issues.append(error("record_entity_invalid", row_path, "entity 必须是 object。", raw))

    semantic = row.get("semantic")
    if isinstance(semantic, dict):
        validate_record_semantic(semantic, row_path, issues, raw)
    else:
        issues.append(error("record_semantic_invalid", row_path, "semantic 必须是 object。", raw))

    display = row.get("display")
    if isinstance(display, dict):
        validate_record_display(display, row_path, issues, raw)
        validate_display_losslessness(row, row_path, issues, raw)
    else:
        issues.append(error("record_display_invalid", row_path, "display 必须是 object。", raw))

    quality = row.get("quality")
    if isinstance(quality, dict):
        validate_record_quality(quality, row_path, issues, raw)
    else:
        issues.append(error("record_quality_invalid", row_path, "quality 必须是 object。", raw))

    translation = row.get("translation")
    if isinstance(translation, dict):
        validate_record_translation(translation, row_path, row, issues, raw)
    elif translation is not None:
        issues.append(error("translation_arguments_invalid", row_path, "translation 必须是 object。", raw))
    elif is_supported_translation_candidate(row) and not is_tier3_record(row):
        issues.append(
            warning("translation_missing_for_supported_frame", row_path, "supported frame 缺少 translation。", raw)
        )

    if raw is not None:
        if raw in seen_raw_names:
            issues.append(
                warning("duplicate_raw_event_name", row_path, f"raw_event_name 已在 {seen_raw_names[raw]} 出现。", raw)
            )
        else:
            seen_raw_names[raw] = row_path


def validate_v2_record_fields(
    row: dict[str, Any],
    row_path: str,
    seen_record_ids: dict[str, str],
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 normalized record v2 additive contract。"""

    for field in sorted(V2_RECORD_REQUIRED_FIELDS):
        if field not in row:
            issues.append(error("v2_record_required_field_missing", row_path, f"V2 记录缺少 {field}。", raw))

    record_id = string_or_none(row.get("record_id"))
    if record_id is None or RECORD_ID_PATTERN.fullmatch(record_id) is None:
        issues.append(error("record_id_invalid", row_path, "record_id 必须是完整 record-v1 SHA-256。", raw))
    else:
        previous = seen_record_ids.get(record_id)
        if previous is not None:
            issues.append(error("duplicate_record_id", row_path, f"record_id 已在 {previous} 出现。", raw))
        else:
            seen_record_ids[record_id] = row_path
        expected_record_id = record_id_for_normalized_record(row)
        if record_id != expected_record_id:
            issues.append(
                error(
                    "record_id_identity_mismatch",
                    row_path,
                    f"record_id 与 canonical identity 不一致，期望 {expected_record_id}。",
                    raw,
                )
            )

    if row.get("record_id_algorithm") != RECORD_ID_ALGORITHM_VERSION:
        issues.append(error("record_id_algorithm_invalid", row_path, "record_id_algorithm 不受支持。", raw))

    source_scope = string_or_none(row.get("source_scope"))
    if source_scope is None:
        issues.append(error("source_scope_invalid", row_path, "source_scope 必须是非空字符串。", raw))

    provenance = row.get("provenance")
    if not isinstance(provenance, dict):
        issues.append(error("provenance_invalid", row_path, "provenance 必须是 object。", raw))
    else:
        domains = provenance.get("source_domains")
        assets = provenance.get("canonical_assets")
        paths = provenance.get("source_paths")
        if not isinstance(domains, list) or not domains or not all(isinstance(item, str) and item for item in domains):
            issues.append(error("provenance_domains_invalid", row_path, "source_domains 必须是非空字符串数组。", raw))
        if not isinstance(assets, list) or not all(isinstance(item, str) for item in assets):
            issues.append(error("provenance_assets_invalid", row_path, "canonical_assets 必须是字符串数组。", raw))
        if not isinstance(paths, list) or not all(isinstance(item, str) and item for item in paths):
            issues.append(
                error("provenance_paths_invalid", row_path, "provenance.source_paths 必须是字符串数组。", raw)
            )
        elif any(path.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", path) for path in paths):
            issues.append(error("provenance_absolute_path", row_path, "provenance 不得包含本机绝对路径。", raw))
        if isinstance(paths, list) and row.get("source_paths") != paths:
            issues.append(
                error("provenance_source_paths_mismatch", row_path, "source_paths 与 provenance 不一致。", raw)
            )
        if isinstance(paths, list) and all(isinstance(item, str) for item in paths):
            expected_source_identity = source_identity_from_paths(paths)
            if provenance != expected_source_identity.to_provenance():
                issues.append(error("provenance_identity_mismatch", row_path, "provenance 与来源路径身份不一致。", raw))
            if source_scope != expected_source_identity.scope:
                issues.append(
                    error("source_scope_identity_mismatch", row_path, "source_scope 与 provenance 不一致。", raw)
                )

    if row.get("asset_kind") not in {"vo", "sfx", "control", "unknown"}:
        issues.append(error("asset_kind_invalid", row_path, "asset_kind 不属于公共四值枚举。", raw))

    pattern_id = string_or_none(row.get("pattern_id"))
    if pattern_id is None or PATTERN_ID_PATTERN.fullmatch(pattern_id) is None:
        issues.append(error("pattern_id_invalid", row_path, "pattern_id 必须是完整 pattern-v1 SHA-256。", raw))

    if row.get("support_level") not in {"stable", "experimental", "unsupported"}:
        issues.append(error("support_level_invalid", row_path, "support_level 不受支持。", raw))
    if row.get("render_status") not in {"rendered", "fallback", "missing", "not_applicable"}:
        issues.append(error("render_status_invalid", row_path, "render_status 不受支持。", raw))
    if row.get("review_status") not in {"not_required", "pending", "accepted", "rejected", "superseded"}:
        issues.append(error("review_status_invalid", row_path, "review_status 不受支持。", raw))

    semantic_frame = row.get("semantic_frame")
    if semantic_frame is not None and not isinstance(semantic_frame, dict):
        issues.append(error("semantic_frame_invalid", row_path, "semantic_frame 必须是 object 或 null。", raw))
    if not isinstance(row.get("rendered_labels"), dict):
        issues.append(error("rendered_labels_invalid", row_path, "rendered_labels 必须是 object。", raw))
    warnings = row.get("warnings")
    if not isinstance(warnings, list) or not all(isinstance(item, str) for item in warnings):
        issues.append(error("warnings_invalid", row_path, "warnings 必须是字符串数组。", raw))


def validate_v2_dataset(
    root: Path,
    manifest: dict[str, Any],
    shard_rows: dict[str, list[dict[str, Any]]],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 V2 单流、Pattern Catalog、Schema/Example 与兼容分片一致。"""

    required_files = (
        "normalized-events.jsonl",
        "pattern-catalog.json",
        "schema/manifest.schema.json",
        "schema/normalized-event.schema.json",
        "schema/pattern-catalog.schema.json",
        "examples/manifest.v2.json",
        "examples/normalized-event.v2.json",
        "examples/pattern-catalog.v1.json",
    )
    for relative in required_files:
        if not (root / relative).is_file():
            issues.append(error("v2_required_file_missing", relative, "V2 数据集缺少必需文件。"))

    stream_path = root / "normalized-events.jsonl"
    stream_rows = read_jsonl_records(stream_path, root, issues) if stream_path.is_file() else []
    shard_records = [row for rows in shard_rows.values() for row in rows]
    _validate_v2_stream(stream_rows, shard_records, manifest, issues)

    catalog_path = root / "pattern-catalog.json"
    catalog = read_json_object(catalog_path, issues) if catalog_path.is_file() else {}
    _validate_v2_pattern_catalog(catalog, shard_records, manifest, issues)
    _validate_v2_manifest_identity(root, manifest, catalog, shard_records, issues)

    for relative in required_files[2:]:
        path = root / relative
        if not path.is_file():
            continue
        document = read_json_object(path, issues)
        if relative.startswith("schema/") and document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            issues.append(error("json_schema_draft_invalid", relative, "JSON Schema 必须声明 draft 2020-12。"))


def _validate_v2_manifest_identity(
    root: Path,
    manifest: dict[str, Any],
    catalog: dict[str, Any],
    rows: list[dict[str, Any]],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 V2 manifest 的输入身份、payload 摘要和数据集主键。"""

    if manifest.get("content_digest_algorithm") != CONTENT_DIGEST_VERSION:
        issues.append(error("content_digest_algorithm_invalid", "manifest.json", "content digest 算法版本无效。"))

    identity = manifest.get("content_identity")
    if not isinstance(identity, dict):
        issues.append(error("content_identity_invalid", "manifest.json", "content_identity 必须是 object。"))
        return
    input_identity = identity.get("input_identity")
    if not isinstance(input_identity, dict) or input_identity.get("schema") != INPUT_IDENTITY_VERSION:
        issues.append(error("input_identity_invalid", "manifest.json", "input identity 缺失或版本无效。"))
    elif not _valid_input_identity_shape(input_identity):
        issues.append(error("input_identity_shape_invalid", "manifest.json", "input identity 字段或摘要形状无效。"))
    else:
        profile = input_identity["profile"]
        if (
            identity.get("corpus_scope") != profile.get("corpus_scope")
            or identity.get("base_game_version") != profile.get("base_game_version")
        ):
            issues.append(error("content_profile_mismatch", "manifest.json", "content identity 与 frozen profile 不一致。"))

    support_counts = _field_counts(rows, "support_level")
    review_counts = _field_counts(rows, "review_status")
    review = identity.get("review")
    if identity.get("support_level_counts") != support_counts:
        issues.append(error("support_level_counts_mismatch", "manifest.json", "support_level_counts 与记录不一致。"))
    if not isinstance(review, dict) or review.get("record_counts") != review_counts:
        issues.append(error("review_counts_mismatch", "manifest.json", "review record_counts 与记录不一致。"))
    elif review.get("decision_id") != catalog.get("decision_id"):
        issues.append(error("review_decision_id_mismatch", "manifest.json", "review decision_id 与 catalog 不一致。"))
    if identity.get("quality_status") != "not_evaluated":
        issues.append(error("quality_status_invalid", "manifest.json", "P3-C1 quality_status 必须为 not_evaluated。"))

    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(entry, dict) for entry in files):
        issues.append(error("manifest_files_invalid", "manifest.json", "files 必须是文件摘要 object 数组。"))
        return

    actual_files = describe_payload_files(root)
    unmanaged = unmanaged_payload_paths(actual_files)
    if unmanaged:
        issues.append(
            error(
                "unmanaged_payload_file",
                "manifest.json",
                f"normalized root 含未管理 payload 文件: {', '.join(unmanaged)}。",
            )
        )
    if files != actual_files:
        issues.append(error("manifest_files_mismatch", "manifest.json", "files 与实际 payload 文件摘要不一致。"))

    expected_digest = content_digest(identity, files)
    actual_digest = manifest.get("content_digest")
    if actual_digest != expected_digest:
        issues.append(error("content_digest_mismatch", "manifest.json", "content_digest 与内容身份不一致。"))
    expected_dataset_id = f"local-v1-{expected_digest[:16]}"
    if manifest.get("dataset_id") != expected_dataset_id:
        issues.append(error("dataset_id_mismatch", "manifest.json", "dataset_id 与 content_digest 不一致。"))


def _valid_input_identity_shape(identity: dict[str, Any]) -> bool:
    """检查 frozen profile、Git 状态和各输入摘要的最小闭包。"""

    profile = identity.get("profile")
    state = identity.get("generator_state")
    inputs = identity.get("inputs")
    if not isinstance(profile, dict) or not isinstance(state, dict) or not isinstance(inputs, dict):
        return False
    if (
        not isinstance(profile.get("profile_id"), str)
        or not isinstance(profile.get("corpus_scope"), str)
        or not isinstance(profile.get("base_game_version"), str)
        or not isinstance(profile.get("raw_unique_count"), int)
        or not isinstance(profile.get("accepted_exceptions"), list)
    ):
        return False
    if not isinstance(state.get("commit"), str) or not isinstance(state.get("dirty"), bool):
        return False

    required_inputs = {
        "raw",
        "reference",
        "reference_manifest",
        "rules",
        "overrides",
        "contract_assets",
        "generator",
        "sources_manifest",
        "frozen_profile",
    }
    for name in required_inputs:
        value = inputs.get(name)
        digest = value.get("digest") if isinstance(value, dict) else None
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            return False
    return True


def _field_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    """按与 writer 相同的规则统计 manifest 枚举字段。"""

    counts: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "missing")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _validate_v2_stream(
    stream_rows: list[dict[str, Any]],
    shard_rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 normalized-events 单流排序、唯一性与分片等价性。"""

    if manifest.get("normalized_record_schema_version") != 2:
        issues.append(
            error("normalized_record_schema_version_invalid", "manifest.json", "记录 schema version 必须为 2。")
        )
    if manifest.get("dataset_schema_version") != "normalized-dataset.v2":
        issues.append(error("dataset_schema_version_invalid", "manifest.json", "dataset_schema_version 必须为 v2。"))
    if manifest.get("record_count") != len(stream_rows):
        issues.append(
            error("normalized_stream_record_count_mismatch", "normalized-events.jsonl", "单流行数与 manifest 不一致。")
        )

    stream_ids = [string_or_none(row.get("record_id")) for row in stream_rows]
    if any(record_id is None for record_id in stream_ids):
        issues.append(error("normalized_stream_record_id_missing", "normalized-events.jsonl", "单流记录缺 record_id。"))
        return
    ordered_ids = [record_id for record_id in stream_ids if record_id is not None]
    if ordered_ids != sorted(ordered_ids):
        issues.append(
            error("normalized_stream_order_invalid", "normalized-events.jsonl", "单流必须按 record_id 排序。")
        )
    if len(ordered_ids) != len(set(ordered_ids)):
        issues.append(
            error("normalized_stream_record_id_duplicate", "normalized-events.jsonl", "单流含重复 record_id。")
        )

    stream_by_id = {str(row.get("record_id")): row for row in stream_rows if row.get("record_id")}
    shard_by_id = {str(row.get("record_id")): row for row in shard_rows if row.get("record_id")}
    if set(stream_by_id) != set(shard_by_id):
        issues.append(
            error("normalized_stream_identity_mismatch", "normalized-events.jsonl", "单流与 by-entity 身份集合不一致。")
        )
    for record_id in sorted(set(stream_by_id).intersection(shard_by_id)):
        if _canonical_json(stream_by_id[record_id]) != _canonical_json(shard_by_id[record_id]):
            issues.append(
                error(
                    "normalized_stream_row_mismatch",
                    "normalized-events.jsonl",
                    f"record_id={record_id} 与 by-entity 内容不一致。",
                )
            )
            break


def _validate_v2_pattern_catalog(
    catalog: dict[str, Any],
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验 Pattern Catalog 主键、计数和记录引用闭包。"""

    if catalog.get("schema_version") != 1:
        issues.append(
            error("pattern_catalog_schema_version_invalid", "pattern-catalog.json", "catalog schema 必须为 1。")
        )
    if manifest.get("pattern_catalog_schema_version") != 1:
        issues.append(
            error("manifest_pattern_catalog_schema_version_invalid", "manifest.json", "catalog schema 必须为 1。")
        )
    patterns = catalog.get("patterns")
    if not isinstance(patterns, list):
        issues.append(error("pattern_catalog_patterns_invalid", "pattern-catalog.json", "patterns 必须是数组。"))
        return
    if catalog.get("pattern_count") != len(patterns) or manifest.get("pattern_count") != len(patterns):
        issues.append(error("pattern_catalog_count_mismatch", "pattern-catalog.json", "pattern_count 与条目数不一致。"))

    pattern_ids: list[str] = []
    for index, pattern in enumerate(patterns, start=1):
        if not isinstance(pattern, dict):
            issues.append(
                error("pattern_catalog_row_invalid", f"pattern-catalog.json:{index}", "pattern 必须是 object。")
            )
            continue
        pattern_id = string_or_none(pattern.get("pattern_id"))
        if pattern_id is None or PATTERN_ID_PATTERN.fullmatch(pattern_id) is None:
            issues.append(error("pattern_catalog_id_invalid", f"pattern-catalog.json:{index}", "pattern_id 无效。"))
            continue
        pattern_ids.append(pattern_id)
    if pattern_ids != sorted(pattern_ids):
        issues.append(
            error("pattern_catalog_order_invalid", "pattern-catalog.json", "patterns 必须按 pattern_id 排序。")
        )
    if len(pattern_ids) != len(set(pattern_ids)):
        issues.append(error("pattern_catalog_id_duplicate", "pattern-catalog.json", "catalog 含重复 pattern_id。"))

    referenced_ids = {str(row.get("pattern_id")) for row in rows if row.get("pattern_id")}
    missing_ids = sorted(referenced_ids.difference(pattern_ids))
    if missing_ids:
        issues.append(
            error(
                "pattern_catalog_reference_missing",
                "pattern-catalog.json",
                f"记录引用 catalog 不存在的 pattern_id: {missing_ids[0]}。",
            )
        )


def _canonical_json(value: dict[str, Any]) -> str:
    """返回用于兼容双写比较的 canonical JSON。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_record_entity(
    entity: dict[str, Any],
    row_path: str,
    shard_rel_path: str,
    summary_paths_by_entity: dict[tuple[str, str], str],
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验记录实体字段。"""

    kind = string_or_none(entity.get("kind"))
    alias = string_or_none(entity.get("alias"))

    if kind is None or alias is None:
        issues.append(error("record_entity_key_missing", row_path, "entity.kind/entity.alias 不能为空。", raw))
        return

    expected = summary_paths_by_entity.get((kind, alias))
    if expected is not None and expected != shard_rel_path:
        issues.append(error("record_shard_mismatch", row_path, f"记录实体应位于 {expected}。", raw))

    if kind == "champion" and is_suspicious_champion_alias(alias):
        issues.append(
            warning("suspicious_champion_alias", row_path, f"champion alias 看起来不是最终英雄实体：{alias}。", raw)
        )

    if kind == "unknown":
        issues.append(warning("unknown_entity_output", row_path, "最终产物仍包含 unknown 实体分片。", raw))


def validate_record_semantic(
    semantic: dict[str, Any],
    row_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 semantic 字段。"""

    for key in ["profile", "primary_family", "slots", "confidence", "requires_context"]:
        if key not in semantic:
            issues.append(error("semantic_required_field_missing", row_path, f"semantic 缺少 {key}。", raw))

    confidence = semantic.get("confidence")
    if confidence in {"low", "failed"}:
        issues.append(warning("low_confidence_output", row_path, f"最终产物仍包含 {confidence} 置信度。", raw))

    slots = semantic.get("slots")
    if isinstance(slots, dict) and "low_trust_rule" in slots:
        issues.append(warning("low_trust_rule_in_output", row_path, "最终产物仍包含 low_trust_rule。", raw))


def validate_record_display(
    display: dict[str, Any],
    row_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验展示文案字段。"""

    short = string_or_none(display.get("zh_cn_short"))
    detail = string_or_none(display.get("zh_cn_detail"))
    tier = display.get("tier")

    if short is None:
        # Tier3（纯技术事件）合法退出短文案；tier 缺失或非 3 时仍按旧契约
        # 要求 zh_cn_short 非空，不放宽历史保证。
        if tier != 3:
            issues.append(error("display_short_missing", row_path, "display.zh_cn_short 不能为空。", raw))
    elif has_technical_display_token(short):
        issues.append(warning("technical_token_in_display", row_path, f"短展示疑似残留技术 token：{short}", raw))

    if detail is None:
        issues.append(error("display_detail_missing", row_path, "display.zh_cn_detail 不能为空。", raw))
    elif has_technical_display_token(detail):
        issues.append(warning("technical_token_in_detail", row_path, f"详细展示疑似残留技术 token：{detail}", raw))


def validate_display_losslessness(
    row: dict[str, Any],
    row_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验展示 detail 没有静默丢弃已解析的语义槽位。

    判定口径（全部为确定性判据，宁可漏报不可误报）：
    1. 壳动词：normalized 记录不持久化 shell 对象，故从不可变的
       raw_event_name 用生产同源的 decomposition_lexer.parse_shell 重新解析
       壳动词（大小写不敏感）；verb 为 stop/mute/unmute 而 zh_cn_detail 既无
       「停止」也无「静音」时判丢（unmute 的「取消静音」含「静音」，同判据覆盖）。
    2. phase：semantic.slots.phase 非空字符串，且 zh_cn_detail 完全等于纯兜底
       模板 `{entity.alias}{事件|音效事件|语音事件}`（复用 quality_metrics 的
       is_fallback_label，大小写敏感精确相等）时判丢。phase key（如
       on_buff_activate）与中文相位词之间没有机械映射表，无法可靠判断任意
       detail 是否体现相位；纯兜底模板按构造不含任何相位语义，是零误报口径。
       带括注的兜底形态（如「Annie事件（皮肤 Skin01）」）刻意不判，属已知漏报。
       将来扩括注口径时应新增独立谓词，不要原地改 is_fallback_label——它同时
       是 fallback_label.ratio 的棘轮口径。
    3. 交互对象：semantic.slots 的 counterpart / chat_partner 为非空 object，
       且 zh_cn_detail 为纯兜底模板时判丢，判据与 2 相同（英文 alias 与中文
       译名之间同样没有可靠映射可查，不做名字包含类启发式）。

    一条记录只发一条问题，message 列出全部被丢弃的槽位；交互对象槽位附带
    对象名（alias/label/raw 首个非空值）方便人工排查。severity：counterpart
    槽位丢弃且 primary_family 属于已承接 translation 家族时升 error（T3.2
    收紧门禁——覆盖承诺范围内的 counterpart 丢弃是回归）；chat_partner
    槽位丢弃且 primary_family=dialogue 时同模式升 error（批次4 收紧门禁）；
    未承接家族（encounter/recall 等）与 phase/壳动词类维持 warning。

    Args:
        row: normalized 记录。
        row_path: 记录定位路径。
        issues: 问题收集列表。
        raw: 记录的 raw_event_name。
    """

    display = row.get("display")
    detail = string_or_none(display.get("zh_cn_detail")) if isinstance(display, dict) else None
    if detail is None:
        # detail 缺失由 display_detail_missing 硬错误负责，这里不重复报。
        return

    dropped: list[str] = []
    dropped_counterpart = False
    dropped_chat_partner = False

    if raw is not None:
        verb = parse_shell(raw).verb
        if verb in DISPLAY_DROP_SHELL_VERBS and "停止" not in detail and "静音" not in detail:
            dropped.append(f"shell_verb={verb}")

    entity = row.get("entity")
    alias = string_or_none(entity.get("alias")) if isinstance(entity, dict) else None
    semantic = row.get("semantic")
    primary_family = string_or_none(semantic.get("primary_family")) if isinstance(semantic, dict) else None
    slots = semantic.get("slots") if isinstance(semantic, dict) else None
    slots = slots if isinstance(slots, dict) else {}

    if alias is not None and is_fallback_label(detail, alias):
        phase = string_or_none(slots.get("phase"))
        if phase is not None:
            dropped.append(f"phase={phase}")

        for key in INTERACTION_PARTNER_SLOT_KEYS:
            value = slots.get(key)
            if isinstance(value, dict) and value:
                dropped.append(f"{key}={partner_display_name(value)}")
                if key == "counterpart":
                    dropped_counterpart = True
                elif key == "chat_partner":
                    dropped_chat_partner = True

    if dropped:
        make_issue = (
            error
            if (
                (dropped_counterpart and primary_family in SUPPORTED_TRANSLATION_PRIMARY_FAMILIES)
                or (dropped_chat_partner and primary_family == "dialogue")
            )
            else warning
        )
        issues.append(
            make_issue(
                "display_drops_parsed_slot",
                row_path,
                f"展示 detail 丢弃已解析语义槽位：{'，'.join(dropped)}。",
                raw,
            )
        )


def partner_display_name(partner: dict[str, Any]) -> str:
    """返回交互对象槽位的展示名。

    Args:
        partner: counterpart / chat_partner 槽位 object。

    Returns:
        alias/label/raw 中首个非空字符串；全缺时返回 "?"。
    """

    for key in ("alias", "label", "raw"):
        name = string_or_none(partner.get(key))
        if name is not None:
            return name

    return "?"


def validate_record_quality(
    quality: dict[str, Any],
    row_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验质量字段。"""

    unknown_count = quality.get("unknown_count")
    if isinstance(unknown_count, int) and unknown_count > 0:
        issues.append(
            warning("unknown_count_in_output", row_path, f"记录仍包含 {unknown_count} 个 unknown fragment。", raw)
        )

    diagnostics = quality.get("diagnostics")
    if isinstance(diagnostics, list) and diagnostics:
        issues.append(warning("diagnostics_in_output", row_path, "记录仍包含 diagnostics。", raw))


def validate_record_translation(
    translation: dict[str, Any],
    row_path: str,
    row: dict[str, Any],
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 translation contract。"""

    for key in TRANSLATION_REQUIRED_FIELDS:
        if key not in translation:
            issues.append(error("translation_required_field_missing", row_path, f"translation 缺少 {key}。", raw))

    if translation.get("schema_version") != TRANSLATION_SCHEMA_VERSION:
        issues.append(
            error("translation_schema_version_invalid", row_path, "translation.schema_version 必须为 1。", raw)
        )

    # frame 支持范围与必需角色都以 FRAME_REGISTRY 为单一事实源（T4.2）。
    frame_type = string_or_none(translation.get("frame_type"))
    frame_spec = FRAME_REGISTRY.get(frame_type) if frame_type is not None else None
    if frame_spec is None:
        issues.append(
            error("translation_frame_type_unknown", row_path, f"未知 translation frame_type：{frame_type}。", raw)
        )

    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        issues.append(error("translation_arguments_invalid", row_path, "translation.arguments 必须是 object。", raw))
        return

    validate_translation_arguments(arguments, row_path, issues, raw)

    if frame_spec is not None:
        for role in frame_spec.required_roles:
            if not isinstance(arguments.get(role), dict):
                issues.append(
                    error(
                        "translation_frame_required_role_missing",
                        row_path,
                        f"{frame_type} 缺少必需 role：{role}。",
                        raw,
                    )
                )

        validate_translation_render(translation, row_path, row, issues, raw)


def validate_translation_arguments(
    arguments: dict[Any, Any],
    row_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 translation arguments。"""

    for argument_key, argument in arguments.items():
        argument_path = f"{row_path}.translation.arguments.{argument_key}"
        if not isinstance(argument_key, str) or not isinstance(argument, dict):
            issues.append(
                error(
                    "translation_arguments_invalid",
                    argument_path,
                    "translation argument 必须以字符串 role 映射到 object。",
                    raw,
                )
            )
            continue

        role = string_or_none(argument.get("role"))
        if role is None:
            issues.append(
                error("translation_argument_role_missing", argument_path, "translation argument 缺少 role。", raw)
            )
        elif role != argument_key:
            issues.append(
                error(
                    "translation_arguments_invalid",
                    argument_path,
                    f"argument role={role} 与 key={argument_key} 不一致。",
                    raw,
                )
            )

        validate_translation_argument_labels(argument, argument_path, issues, raw)
        validate_translation_argument_quality(argument, argument_path, issues, raw)


def validate_translation_argument_labels(
    argument: dict[str, Any],
    argument_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 argument labels 和 fallback。"""

    labels = argument.get("labels")
    has_label = isinstance(labels, dict) and any(string_or_none(value) is not None for value in labels.values())
    has_locale_label = isinstance(labels, dict) and string_or_none(labels.get("zh_CN")) is not None
    fallback = argument.get("fallback") is True
    has_fallback_value = any(
        string_or_none(argument.get(key)) is not None for key in ("token", "key", "value", "alias", "raw")
    )

    if not has_label and not (fallback and has_fallback_value):
        issues.append(
            error(
                "translation_argument_label_or_fallback_missing",
                argument_path,
                "argument 缺少可展示 label 或 fallback 值。",
                raw,
            )
        )

    if isinstance(labels, dict):
        for locale, label in labels.items():
            if locale == "zh_CN" and isinstance(label, str) and has_technical_display_token(label):
                issues.append(
                    error(
                        "technical_token_in_rendered_user_text",
                        f"{argument_path}.labels.{locale}",
                        f"用户文案 label 残留技术 token：{label}",
                        raw,
                    )
                )

    if has_label and not has_locale_label:
        issues.append(warning("translation_locale_label_missing", argument_path, "argument 缺少 zh_CN label。", raw))

    if fallback:
        issues.append(warning("translation_label_fallback", argument_path, "argument 使用 fallback label。", raw))
        if string_or_none(argument.get("fallback_reason")) is None:
            issues.append(
                warning(
                    "translation_fallback_reason_missing",
                    argument_path,
                    "fallback argument 缺少 fallback_reason。",
                    raw,
                )
            )


def validate_translation_argument_quality(
    argument: dict[str, Any],
    argument_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 argument 置信度和来源。"""

    confidence = string_or_none(argument.get("confidence"))
    if confidence in {None, "low", "failed", "unknown"}:
        issues.append(
            warning("translation_argument_low_confidence", argument_path, f"argument confidence={confidence}。", raw)
        )

    provenance = argument.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        issues.append(
            warning("translation_argument_provenance_missing", argument_path, "argument 缺少 provenance。", raw)
        )


def validate_translation_render(
    translation: dict[str, Any],
    row_path: str,
    row: dict[str, Any],
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """使用 renderer 对 translation 做 smoke 校验。"""

    render_specs = (
        ("zh_CN.default", RenderOptions(locale="zh_CN", mode="default"), "zh_cn_detail"),
        (
            "zh_CN.main",
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
            "zh_cn_short",
        ),
    )
    display = row.get("display")

    for render_key, options, display_key in render_specs:
        try:
            result = render_translation(translation, options)
        except Exception as exc:  # pragma: no cover - 防御异常需要保留原始信息
            issues.append(error("translation_render_failed", row_path, f"{render_key} 渲染失败：{exc}", raw))
            continue

        if "missing_render_template" in result.diagnostics:
            issues.append(error("missing_render_template", row_path, f"{render_key} 缺少渲染模板。", raw))
            continue

        if not result.text:
            issues.append(
                warning("translation_render_context_too_sparse", row_path, f"{render_key} 渲染结果为空。", raw)
            )
            continue

        if has_technical_display_token(result.text):
            issues.append(
                error(
                    "technical_token_in_rendered_user_text",
                    row_path,
                    f"{render_key} 残留技术 token：{result.text}",
                    raw,
                )
            )

        if isinstance(display, dict):
            expected = string_or_none(display.get(display_key))
            if expected is not None and expected != result.text:
                issues.append(
                    error(
                        "translation_display_mismatch",
                        row_path,
                        f"display.{display_key} 与 {render_key} renderer 输出不一致：{expected} != {result.text}",
                        raw,
                    )
                )


def is_tier3_record(row: dict[str, Any]) -> bool:
    """判断记录是否为 Tier3 纯技术展示层级（Tier 口径收口）。

    Tier3 记录按北极星口径只存无损 token、不发短文案、不计翻译 KPI，
    刻意无 translation，不应计入 translation_missing_for_supported_frame。

    Args:
        row: normalized 记录。

    Returns:
        display.tier == 3 时返回 True。
    """

    display = row.get("display")
    return isinstance(display, dict) and display.get("tier") == 3


def is_supported_translation_candidate(row: dict[str, Any]) -> bool:
    """判断缺 translation 的记录是否属于已支持 frame 候选。"""

    semantic = row.get("semantic")
    if not isinstance(semantic, dict):
        return False

    primary_family = string_or_none(semantic.get("primary_family"))
    if primary_family in SUPPORTED_TRANSLATION_PRIMARY_FAMILIES:
        return True

    slots = semantic.get("slots")
    if not isinstance(slots, dict):
        return False

    contextual_alias = slots.get("contextual_alias")
    if isinstance(contextual_alias, dict) and contextual_alias.get("component") is not None:
        return True

    phase = string_or_none(slots.get("phase"))
    if phase is None:
        return False

    lowered = phase.lower()
    return "buff" in lowered or "activate" in lowered or "deactivate" in lowered


def summary_entity_paths(summary: list[dict[str, Any]]) -> dict[tuple[str, str], str]:
    """从 summary 中建立实体到真实分片路径的映射。"""

    result: dict[tuple[str, str], str] = {}

    for row in summary:
        kind = string_or_none(row.get("kind"))
        alias = string_or_none(row.get("alias"))
        path = string_or_none(row.get("path"))

        if kind is None or alias is None or path is None:
            continue

        result[(kind, alias)] = path

    return result


def plural_entity_kind(kind: str) -> str:
    """返回实体类型目录名。"""

    irregular = {
        "champion": "champions",
        "map": "maps",
        "system": "systems",
        "unknown": "unknown",
    }
    return irregular.get(kind, f"{kind}s")


def is_suspicious_champion_alias(alias: str) -> bool:
    """判断 champion alias 是否不像最终英雄实体。"""

    lowered = alias.lower()

    return (
        alias.isdigit()
        or lowered in SUSPICIOUS_CHAMPION_ALIASES
        or re.search(r"skin\d+$", alias, flags=re.IGNORECASE) is not None
    )


def has_technical_display_token(text: str) -> bool:
    """判断展示文案是否残留明显技术 token。"""

    lowered = text.lower()
    if any(token.lower() in lowered for token in TECHNICAL_DISPLAY_PATTERNS):
        return True

    return "_" in text


def string_or_none(value: Any) -> str | None:
    """把非空字符串取出。"""

    if isinstance(value, str) and value.strip():
        return value

    return None


def relative_path(root: Path, path: Path) -> str:
    """返回 POSIX 风格相对路径。"""

    return path.relative_to(root).as_posix()


def error(code: str, path: str, message: str, raw_event_name: str | None = None) -> NormalizedValidationIssue:
    """创建硬错误。"""

    return NormalizedValidationIssue(
        severity="error",
        code=code,
        path=path,
        message=message,
        raw_event_name=raw_event_name,
    )


def warning(code: str, path: str, message: str, raw_event_name: str | None = None) -> NormalizedValidationIssue:
    """创建质量警告。"""

    return NormalizedValidationIssue(
        severity="warning",
        code=code,
        path=path,
        message=message,
        raw_event_name=raw_event_name,
    )
