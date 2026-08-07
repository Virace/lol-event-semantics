"""normalized 事件名查询与渲染辅助。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .normalized_translation_model import RenderOptions
from .normalized_translation_renderer import render_translation


def lookup_normalized_events(
    normalized_root: Path,
    raw_event_names: Iterable[str],
    *,
    render_options: RenderOptions | None = None,
    include_record: bool = False,
) -> list[dict[str, object]]:
    """按 raw event name 查询 normalized 记录。

    Args:
        normalized_root: normalized 输出根目录。
        raw_event_names: 要查询的原始事件名，按精确大小写匹配。
        render_options: translation 渲染参数。
        include_record: 是否在结果中包含完整 normalized 记录。

    Returns:
        查询结果列表，每个查询项包含 matched 和 matches。
    """

    queries = unique_non_empty(raw_event_names)
    matches_by_raw: dict[str, list[dict[str, object]]] = {query: [] for query in queries}
    active_options = render_options or RenderOptions()

    for shard_path, line_number, row in iter_normalized_rows(normalized_root):
        raw = string_or_none(row.get("raw_event_name"))
        if raw not in matches_by_raw:
            continue

        matches_by_raw[raw].append(
            build_match_info(
                row,
                normalized_root,
                shard_path,
                line_number,
                active_options,
                include_record=include_record,
            )
        )

    return [
        {
            "raw_event_name": query,
            "matched": bool(matches_by_raw[query]),
            "matches": matches_by_raw[query],
        }
        for query in queries
    ]


def iter_normalized_rows(normalized_root: Path) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    """优先遍历 V2 单流，缺失时回落 legacy by-entity JSONL。

    Args:
        normalized_root: normalized 输出根目录。

    Yields:
        `(shard_path, line_number, row)`。

    Raises:
        ValueError: JSONL 行不是 object 或无法解析。
    """

    normalized_stream = normalized_root / "normalized-events.jsonl"
    if normalized_stream.is_file():
        yield from _iter_jsonl_rows(normalized_stream)
        return

    by_entity_root = normalized_root / "by-entity"
    if not by_entity_root.exists():
        return

    for shard_path in sorted(by_entity_root.rglob("*.jsonl")):
        yield from _iter_jsonl_rows(shard_path)


def _iter_jsonl_rows(path: Path) -> Iterable[tuple[Path, int, dict[str, Any]]]:
    """读取一个 normalized JSONL 文件并报告精确行号错误。"""

    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 解析失败：{exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number} JSONL 记录必须是 object。")
            yield path, line_number, row


def build_match_info(
    row: dict[str, Any],
    normalized_root: Path,
    shard_path: Path,
    line_number: int,
    render_options: RenderOptions,
    *,
    include_record: bool = False,
) -> dict[str, object]:
    """构造单条命中记录的查询信息。

    Args:
        row: normalized 记录。
        normalized_root: normalized 输出根目录。
        shard_path: 命中的 JSONL 分片路径。
        line_number: 命中行号。
        render_options: translation 渲染参数。
        include_record: 是否包含完整原始记录。

    Returns:
        面向 CLI/Web 的命中摘要。
    """

    translation = row.get("translation")
    result: dict[str, object] = {
        "path": shard_path.relative_to(normalized_root).as_posix(),
        "line": line_number,
        "record_id": row.get("record_id"),
        "raw_event_name": row.get("raw_event_name"),
        "source_scope": row.get("source_scope"),
        "provenance": row.get("provenance") if isinstance(row.get("provenance"), dict) else None,
        "asset_kind": row.get("asset_kind"),
        "pattern_id": row.get("pattern_id"),
        "support_level": row.get("support_level"),
        "render_status": row.get("render_status"),
        "review_status": row.get("review_status"),
        "entity": row.get("entity") if isinstance(row.get("entity"), dict) else None,
        "semantic": summarize_semantic(row.get("semantic")),
        "display": row.get("display") if isinstance(row.get("display"), dict) else None,
        "quality": row.get("quality") if isinstance(row.get("quality"), dict) else None,
        "source_paths": row.get("source_paths") if isinstance(row.get("source_paths"), list) else [],
        "translation": summarize_translation(translation, render_options.locale),
        "render": render_lookup_translation(translation, render_options),
    }

    if include_record:
        result["record"] = row

    return result


def render_lookup_translation(translation: Any, options: RenderOptions) -> dict[str, object] | None:
    """渲染查询命中的 translation。

    Args:
        translation: normalized 记录中的 translation 字段。
        options: renderer 参数。

    Returns:
        renderer 输出摘要；缺 translation 时返回 None。
    """

    if not isinstance(translation, dict):
        return None

    rendered = render_translation(translation, options)
    return {
        "locale": options.locale,
        "mode": options.mode,
        "known_context": sorted(options.known_context),
        "text": rendered.text,
        "frame_type": rendered.frame_type,
        "confidence": rendered.confidence,
        "used_roles": list(rendered.used_roles),
        "omitted_roles": list(rendered.omitted_roles),
        "diagnostics": list(rendered.diagnostics),
    }


def summarize_translation(translation: Any, locale: str) -> dict[str, object] | None:
    """返回 translation 的紧凑摘要。

    Args:
        translation: normalized 记录中的 translation 字段。
        locale: 优先展示的语言。

    Returns:
        translation 摘要；缺 translation 时返回 None。
    """

    if not isinstance(translation, dict):
        return None

    arguments = translation.get("arguments")
    return {
        "schema_version": translation.get("schema_version"),
        "frame_type": translation.get("frame_type"),
        "frame_version": translation.get("frame_version"),
        "frame_confidence": translation.get("frame_confidence"),
        "arguments": summarize_arguments(arguments, locale),
        "provenance": translation.get("provenance") if isinstance(translation.get("provenance"), dict) else None,
    }


def summarize_arguments(arguments: Any, locale: str) -> dict[str, object]:
    """返回 translation arguments 的紧凑摘要。

    Args:
        arguments: translation.arguments 字段。
        locale: 优先展示的语言。

    Returns:
        role 到参数摘要的映射。
    """

    if not isinstance(arguments, dict):
        return {}

    result: dict[str, object] = {}
    for role, argument in arguments.items():
        if not isinstance(role, str) or not isinstance(argument, dict):
            continue

        result[role] = summarize_argument(argument, locale)

    return result


def summarize_argument(argument: dict[str, Any], locale: str) -> dict[str, object]:
    """返回单个 translation argument 的紧凑摘要。

    Args:
        argument: translation argument。
        locale: 优先展示的语言。

    Returns:
        参数摘要。
    """

    labels = argument.get("labels")
    result: dict[str, object] = {
        "role": argument.get("role"),
        "label": preferred_label(labels, locale),
        "confidence": argument.get("confidence"),
        "fallback": argument.get("fallback") is True,
        "fallback_reason": argument.get("fallback_reason"),
        "provenance": argument.get("provenance") if isinstance(argument.get("provenance"), dict) else None,
    }
    for key in ("key", "value", "alias", "token"):
        value = argument.get(key)
        if isinstance(value, str) and value:
            result[key] = value

    omit_when_known = argument.get("omit_when_context_known")
    if isinstance(omit_when_known, list):
        result["omit_when_context_known"] = [str(item) for item in omit_when_known]

    return result


def summarize_semantic(semantic: Any) -> dict[str, object] | None:
    """返回 semantic 字段摘要。

    Args:
        semantic: normalized 记录中的 semantic 字段。

    Returns:
        semantic 摘要；字段无效时返回 None。
    """

    if not isinstance(semantic, dict):
        return None

    return {
        "profile": semantic.get("profile"),
        "primary_family": semantic.get("primary_family"),
        "confidence": semantic.get("confidence"),
        "requires_context": semantic.get("requires_context"),
        "slots": semantic.get("slots") if isinstance(semantic.get("slots"), dict) else {},
    }


def preferred_label(labels: Any, locale: str) -> str | None:
    """按语言优先级选择 label。

    Args:
        labels: argument.labels 字段。
        locale: 优先语言。

    Returns:
        可展示 label；不存在时返回 None。
    """

    if not isinstance(labels, dict):
        return None

    for key in (locale, "zh_CN", "en_US"):
        value = labels.get(key)
        if isinstance(value, str) and value:
            return value

    return None


def unique_non_empty(values: Iterable[str]) -> list[str]:
    """返回去重后的非空字符串列表，并保留输入顺序。

    Args:
        values: 字符串序列。

    Returns:
        去重后的字符串列表。
    """

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not value or value in seen:
            continue

        seen.add(value)
        result.append(value)

    return result


def string_or_none(value: Any) -> str | None:
    """读取非空字符串。

    Args:
        value: 待读取值。

    Returns:
        非空字符串；否则返回 None。
    """

    if isinstance(value, str) and value:
        return value

    return None
