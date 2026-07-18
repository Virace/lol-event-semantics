"""normalized 最终产物二次校验。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .decomposition_lexer import parse_shell
from .normalized_translation_model import (
    REQUIRED_FRAME_ROLES,
    SUPPORTED_FRAME_TYPES,
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
#（unmute 的「取消静音」含「静音」，与 mute 共用同一判据）。
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
    shard_rows: dict[str, list[dict[str, Any]]] = {}

    for path in sorted((root / "by-entity").rglob("*.jsonl")) if (root / "by-entity").exists() else []:
        rel_path = relative_path(root, path)
        shard_rows[rel_path] = read_jsonl_records(path, root, issues)

    validate_manifest(root, manifest, summary, shard_rows, issues)
    validate_summary(root, summary, shard_rows, issues)

    seen_raw_names: dict[str, str] = {}
    summary_paths_by_entity = summary_entity_paths(summary)
    for rel_path, rows in shard_rows.items():
        for row_index, row in enumerate(rows, start=1):
            row_path = f"{rel_path}:{row_index}"
            validate_record(row, row_path, rel_path, summary_paths_by_entity, seen_raw_names, issues)

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

    if manifest.get("schema_version") != 1:
        issues.append(error("manifest_schema_version_invalid", "manifest.json", "manifest schema_version 必须为 1。"))

    if manifest.get("contains_full_raw_event_index") is not False:
        issues.append(error("manifest_full_index_flag_invalid", "manifest.json", "manifest 必须声明不包含完整 raw_event_name 索引。"))

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

    for required in ["manifest.json", "entity-summary.json", "by-entity/"]:
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
    issues: list[NormalizedValidationIssue],
) -> None:
    """校验单条 normalized 记录。"""

    raw = string_or_none(row.get("raw_event_name"))
    if raw is None:
        issues.append(error("record_raw_event_name_missing", row_path, "记录缺少 raw_event_name。"))

    for key in ["schema_version", "game_version", "entity", "semantic", "display", "quality", "source_paths"]:
        if key not in row:
            issues.append(error("record_required_field_missing", row_path, f"记录缺少 {key}。", raw))

    if row.get("schema_version") != 1:
        issues.append(error("record_schema_version_invalid", row_path, "记录 schema_version 必须为 1。", raw))

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
    elif is_supported_translation_candidate(row):
        issues.append(warning("translation_missing_for_supported_frame", row_path, "supported frame 缺少 translation。", raw))

    if raw is not None:
        if raw in seen_raw_names:
            issues.append(warning("duplicate_raw_event_name", row_path, f"raw_event_name 已在 {seen_raw_names[raw]} 出现。", raw))
        else:
            seen_raw_names[raw] = row_path


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
        issues.append(warning("suspicious_champion_alias", row_path, f"champion alias 看起来不是最终英雄实体：{alias}。", raw))

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
        issues.append(warning("unknown_count_in_output", row_path, f"记录仍包含 {unknown_count} 个 unknown fragment。", raw))

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
        issues.append(error("translation_schema_version_invalid", row_path, "translation.schema_version 必须为 1。", raw))

    frame_type = string_or_none(translation.get("frame_type"))
    if frame_type is None or frame_type not in SUPPORTED_FRAME_TYPES:
        issues.append(error("translation_frame_type_unknown", row_path, f"未知 translation frame_type：{frame_type}。", raw))

    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        issues.append(error("translation_arguments_invalid", row_path, "translation.arguments 必须是 object。", raw))
        return

    validate_translation_arguments(arguments, row_path, issues, raw)

    if frame_type in REQUIRED_FRAME_ROLES:
        for role in REQUIRED_FRAME_ROLES[frame_type]:
            if not isinstance(arguments.get(role), dict):
                issues.append(
                    error(
                        "translation_frame_required_role_missing",
                        row_path,
                        f"{frame_type} 缺少必需 role：{role}。",
                        raw,
                    )
                )

    if frame_type in SUPPORTED_FRAME_TYPES:
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
            issues.append(error("translation_arguments_invalid", argument_path, "translation argument 必须以字符串 role 映射到 object。", raw))
            continue

        role = string_or_none(argument.get("role"))
        if role is None:
            issues.append(error("translation_argument_role_missing", argument_path, "translation argument 缺少 role。", raw))
        elif role != argument_key:
            issues.append(error("translation_arguments_invalid", argument_path, f"argument role={role} 与 key={argument_key} 不一致。", raw))

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
    has_fallback_value = any(string_or_none(argument.get(key)) is not None for key in ("token", "key", "value", "alias", "raw"))

    if not has_label and not (fallback and has_fallback_value):
        issues.append(error("translation_argument_label_or_fallback_missing", argument_path, "argument 缺少可展示 label 或 fallback 值。", raw))

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
            issues.append(warning("translation_fallback_reason_missing", argument_path, "fallback argument 缺少 fallback_reason。", raw))


def validate_translation_argument_quality(
    argument: dict[str, Any],
    argument_path: str,
    issues: list[NormalizedValidationIssue],
    raw: str | None,
) -> None:
    """校验 argument 置信度和来源。"""

    confidence = string_or_none(argument.get("confidence"))
    if confidence in {None, "low", "failed", "unknown"}:
        issues.append(warning("translation_argument_low_confidence", argument_path, f"argument confidence={confidence}。", raw))

    provenance = argument.get("provenance")
    if not isinstance(provenance, dict) or not provenance:
        issues.append(warning("translation_argument_provenance_missing", argument_path, "argument 缺少 provenance。", raw))


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
            issues.append(warning("translation_render_context_too_sparse", row_path, f"{render_key} 渲染结果为空。", raw))
            continue

        if has_technical_display_token(result.text):
            issues.append(error("technical_token_in_rendered_user_text", row_path, f"{render_key} 残留技术 token：{result.text}", raw))

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
