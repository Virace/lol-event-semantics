"""normalized 事件名查询 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.dataset_runs import DatasetRunError, resolve_approved_dataset
from lol_event_semantics.normalized_lookup import lookup_normalized_events
from lol_event_semantics.normalized_translation_model import RenderOptions


@dataclass(slots=True)
class LookupNormalizedEventArgs:
    """normalized 事件查询参数。"""

    normalized_root: Path | None
    runs_root: Path
    event_names: list[str]
    locale: str
    mode: str
    known_context: frozenset[str]
    fallback: str
    json_output: bool
    include_record: bool
    allow_missing: bool


def main(argv: list[str] | None = None) -> int:
    """运行 normalized 事件查询。

    Args:
        argv: 可选命令行参数。

    Returns:
        退出码；全部命中为 0，缺失事件默认为 1，输入目录错误为 2。
    """

    args = parse_args(argv)
    try:
        normalized_root = args.normalized_root or resolve_approved_dataset(args.runs_root)
    except DatasetRunError as exc:
        print(f"approved normalized dataset not available: {exc}", file=sys.stderr)
        return 2

    normalized_stream = normalized_root / "normalized-events.jsonl"
    by_entity_root = normalized_root / "by-entity"
    if not normalized_stream.is_file() and not by_entity_root.exists():
        print(f"normalized dataset not found: {normalized_root}", file=sys.stderr)
        return 2

    render_options = RenderOptions(
        locale=args.locale,
        mode=args.mode,
        known_context=args.known_context,
        fallback=args.fallback,
    )
    results = lookup_normalized_events(
        normalized_root,
        args.event_names,
        render_options=render_options,
        include_record=args.include_record,
    )
    payload = {
        "normalized_root": str(normalized_root),
        "locale": args.locale,
        "mode": args.mode,
        "known_context": sorted(args.known_context),
        "fallback": args.fallback,
        "query_count": len(args.event_names),
        "results": results,
    }

    if args.json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_human_results(payload)

    if args.allow_missing or all(bool(result["matched"]) for result in results):
        return 0

    return 1


def parse_args(argv: list[str] | None = None) -> LookupNormalizedEventArgs:
    """解析命令行参数。

    Args:
        argv: 可选命令行参数。

    Returns:
        解析后的查询参数。
    """

    parser = argparse.ArgumentParser(
        description="Lookup raw event names in the approved-local dataset and render translation text.",
    )
    parser.add_argument(
        "events",
        nargs="*",
        help="Raw event names to lookup. Matching is exact and case-sensitive.",
    )
    parser.add_argument(
        "--event",
        dest="event_options",
        action="append",
        default=[],
        help="Raw event name to lookup. Can be provided multiple times.",
    )
    parser.add_argument(
        "--events-file",
        type=Path,
        help="Optional UTF-8 text file containing one raw event name per line.",
    )
    parser.add_argument(
        "--normalized-root",
        type=Path,
        help="Explicit normalized root. Omit to resolve dist/runs/approved-local.json.",
    )
    parser.add_argument(
        "--runs-root", type=Path, default=Path("dist/runs"), help="Dataset runs root for approved lookup."
    )
    parser.add_argument("--locale", default="zh_CN", help="Render locale. Defaults to zh_CN.")
    parser.add_argument(
        "--mode",
        default="default",
        choices=("default", "main", "debug", "audit"),
        help="Render mode. Use main with known context for page-level UI text.",
    )
    parser.add_argument(
        "--known-context",
        action="append",
        default=[],
        help="Known context roles, comma-separated or repeatable. Example: champion,skin.",
    )
    parser.add_argument(
        "--fallback",
        default="label",
        choices=("label", "token", "raw"),
        help="Renderer fallback strategy for missing labels.",
    )
    parser.add_argument("--json", dest="json_output", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument(
        "--include-record", action="store_true", help="Include the full normalized record in JSON output."
    )
    parser.add_argument(
        "--allow-missing", action="store_true", help="Return exit code 0 even when some events are missing."
    )
    namespace = parser.parse_args(argv)

    event_names = collect_event_names(namespace.events, namespace.event_options, namespace.events_file)
    if not event_names:
        parser.error("provide at least one event name via positional args, --event, or --events-file")

    return LookupNormalizedEventArgs(
        normalized_root=namespace.normalized_root,
        runs_root=namespace.runs_root,
        event_names=event_names,
        locale=namespace.locale,
        mode=namespace.mode,
        known_context=frozenset(parse_known_context(namespace.known_context)),
        fallback=namespace.fallback,
        json_output=namespace.json_output,
        include_record=namespace.include_record,
        allow_missing=namespace.allow_missing,
    )


def collect_event_names(events: list[str], event_options: list[str], events_file: Path | None) -> list[str]:
    """收集待查询事件名。

    Args:
        events: 位置参数事件名。
        event_options: `--event` 参数事件名。
        events_file: 可选事件名文件。

    Returns:
        去重前的事件名列表，空行和注释行会被忽略。
    """

    result = [item.strip() for item in [*events, *event_options] if item.strip()]
    if events_file is None:
        return result

    for line in events_file.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#"):
            result.append(text)

    return result


def parse_known_context(values: list[str]) -> list[str]:
    """解析 known context 参数。

    Args:
        values: CLI 输入值，可重复或逗号分隔。

    Returns:
        去重后的 context role 列表。
    """

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in value.split(","):
            role = item.strip()
            if not role or role in seen:
                continue

            seen.add(role)
            result.append(role)

    return result


def print_human_results(payload: dict[str, object]) -> None:
    """打印人类可读查询结果。

    Args:
        payload: 查询结果 payload。
    """

    known_context = payload.get("known_context")
    known_context_text = ",".join(str(item) for item in known_context) if isinstance(known_context, list) else ""
    print(
        "lookup_normalized_event "
        f"root={payload['normalized_root']} locale={payload['locale']} "
        f"mode={payload['mode']} known_context={known_context_text}"
    )
    results = payload.get("results")
    if not isinstance(results, list):
        return

    for result in results:
        if not isinstance(result, dict):
            continue

        print_query_result(result)


def print_query_result(result: dict[str, object]) -> None:
    """打印单个事件名查询结果。

    Args:
        result: 单个事件名查询结果。
    """

    raw = result.get("raw_event_name")
    if not result.get("matched"):
        print(f"\n[MISS] {raw}")
        return

    matches = result.get("matches")
    if not isinstance(matches, list):
        return

    for index, match in enumerate(matches, start=1):
        if not isinstance(match, dict):
            continue

        print_match(raw, index, match)


def print_match(raw_event_name: object, index: int, match: dict[str, object]) -> None:
    """打印单条命中详情。

    Args:
        raw_event_name: 查询事件名。
        index: 当前命中序号。
        match: 命中摘要。
    """

    print(f"\n[MATCH {index}] {raw_event_name}")
    print(f"  path: {match.get('path')}:{match.get('line')}")
    print_entity(match.get("entity"))
    print_semantic(match.get("semantic"))
    print_display(match.get("display"))
    print_render(match.get("render"))
    print_translation(match.get("translation"))


def print_entity(entity: object) -> None:
    """打印实体信息。

    Args:
        entity: entity 字段。
    """

    if isinstance(entity, dict):
        print(f"  entity: {entity.get('kind')}/{entity.get('alias')}")


def print_semantic(semantic: object) -> None:
    """打印语义匹配信息。

    Args:
        semantic: semantic 摘要。
    """

    if not isinstance(semantic, dict):
        return

    print(
        "  semantic: "
        f"profile={semantic.get('profile')} "
        f"family={semantic.get('primary_family')} "
        f"confidence={semantic.get('confidence')} "
        f"requires_context={semantic.get('requires_context')}"
    )


def print_display(display: object) -> None:
    """打印兼容 display 信息。

    Args:
        display: display 字段。
    """

    if not isinstance(display, dict):
        return

    print(f"  display.short: {display.get('zh_cn_short')}")
    print(f"  display.detail: {display.get('zh_cn_detail')}")
    print(f"  display.debug: {display.get('debug_label')}")


def print_render(render: object) -> None:
    """打印 renderer 输出。

    Args:
        render: renderer 输出摘要。
    """

    if not isinstance(render, dict):
        print("  render: <no translation>")
        return

    print(f"  render: text={render.get('text')} frame={render.get('frame_type')} confidence={render.get('confidence')}")
    print(f"  render.used_roles: {','.join(render.get('used_roles') or [])}")
    print(f"  render.omitted_roles: {','.join(render.get('omitted_roles') or [])}")
    diagnostics = render.get("diagnostics") or []
    if diagnostics:
        print(f"  render.diagnostics: {','.join(diagnostics)}")


def print_translation(translation: object) -> None:
    """打印 translation 摘要。

    Args:
        translation: translation 摘要。
    """

    if not isinstance(translation, dict):
        return

    print(f"  translation: frame={translation.get('frame_type')} confidence={translation.get('frame_confidence')}")
    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        return

    print("  arguments:")
    for role, argument in arguments.items():
        if not isinstance(argument, dict):
            continue

        extras = []
        for key in ("key", "value", "alias", "token"):
            value = argument.get(key)
            if value is not None:
                extras.append(f"{key}={value}")

        suffix = f" ({', '.join(extras)})" if extras else ""
        fallback = " fallback" if argument.get("fallback") else ""
        print(f"    - {role}: {argument.get('label')}{suffix} confidence={argument.get('confidence')}{fallback}")


if __name__ == "__main__":
    raise SystemExit(main())
