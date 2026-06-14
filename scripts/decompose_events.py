"""构建事件名结构化拆解产物。"""

from __future__ import annotations

import argparse
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference, normalize_dragontail_root
from lol_event_semantics.decomposition_reports import write_jsonl, write_reports
from lol_event_semantics.match_audit import (
    MatchAuditRecorder,
    audit_context,
    summarize_rule_hits,
    summarize_zero_hit_rules,
    write_match_audit_manifest,
    write_match_hit_summary,
    write_match_hit_summary_json,
    write_zero_hit_rules,
)
from lol_event_semantics.raw_events import is_excluded_event_name


@dataclass(slots=True)
class DecomposeArgs:
    """拆解脚本参数。"""

    raw_root: Path
    version: str
    output_root: Path
    report_root: Path
    limit: int | None
    game_data_root: Path | None
    dragontail_root: Path | None
    reference: ReferenceIndex | None = None


def main(argv: list[str] | None = None) -> int:
    """运行事件名拆解。"""

    args = parse_args(argv)
    audit = MatchAuditRecorder()
    records = build_records(args, audit)
    champion_records = [record for record in records if has_source_segment(record.source_paths, "champions")]
    map_records = [record for record in records if has_source_segment(record.source_paths, "maps")]

    write_jsonl(champion_records, args.output_root / "champions.jsonl")
    write_jsonl(map_records, args.output_root / "maps.jsonl")
    write_reports(records, args.report_root)
    write_rule_audit_reports(args, audit, len(records), argv)

    print(
        f"records={len(records)} champions={len(champion_records)} maps={len(map_records)} "
        f"rule_hits={len(audit.events)}"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> DecomposeArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build event-name decomposition artifacts.")
    parser.add_argument("--raw-root", type=Path, required=True, help="Raw events root, for example raw/events.")
    parser.add_argument("--version", required=True, help="Game version label, for example 16.5.")
    parser.add_argument("--output-root", type=Path, required=True, help="Output directory for JSONL artifacts.")
    parser.add_argument("--report-root", type=Path, required=True, help="Output directory for reports.")
    parser.add_argument("--limit", type=int, help="Optional maximum number of unique event names.")
    parser.add_argument("--game-data-root", type=Path, help="Optional extracted rcp-be-lol-game-data root.")
    parser.add_argument("--dragontail-root", type=Path, help="Optional dragontail data root for official champion spell ids.")
    namespace = parser.parse_args(argv)

    return DecomposeArgs(
        raw_root=namespace.raw_root,
        version=namespace.version,
        output_root=namespace.output_root,
        report_root=namespace.report_root,
        limit=namespace.limit,
        game_data_root=namespace.game_data_root,
        dragontail_root=namespace.dragontail_root,
    )


def build_records(args: DecomposeArgs, audit: MatchAuditRecorder | None = None):
    """读取 raw tree 并构建拆解记录。"""

    grouped: dict[str, list[str]] = {}
    reference = (
        ReferenceIndex.from_game_data_root(args.game_data_root, dragontail_root=args.dragontail_root)
        if args.game_data_root is not None
        else ReferenceIndex.builtins()
    )

    if args.game_data_root is None and args.dragontail_root is not None:
        reference.load_dragontail(normalize_dragontail_root(args.dragontail_root))

    args.reference = reference

    for path in sorted(args.raw_root.rglob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            raw_name = line.strip()

            if not raw_name:
                continue

            if is_excluded_event_name(raw_name):
                continue

            grouped.setdefault(raw_name, []).append(str(path))

            if args.limit is not None and len(grouped) >= args.limit:
                break

        if args.limit is not None and len(grouped) >= args.limit:
            break

    records = []

    with audit_context(audit):
        for raw_name, paths in sorted(grouped.items()):
            record = decompose_event_name(raw_name, game_version=args.version, source_path=paths[0])
            record.source_paths = paths
            records.append(enrich_with_reference(normalize_semantics(record), reference))

    return records


def write_rule_audit_reports(
    args: DecomposeArgs,
    audit: MatchAuditRecorder,
    raw_count: int,
    argv: list[str] | None,
) -> None:
    """写出本次规则命中审计报告。"""

    audit_root = args.report_root / "rule-audit"
    hit_rows = summarize_rule_hits(audit.events)
    write_match_hit_summary(hit_rows, audit_root / "match-hit-summary.csv")
    write_match_hit_summary_json(hit_rows, audit_root / "match-hit-summary.json")
    write_zero_hit_rules(
        summarize_zero_hit_rules(args.reference.rules if args.reference is not None else None, audit.events),
        audit_root / "zero-hit-rules.csv",
    )
    write_match_audit_manifest(
        audit_root / "manifest.json",
        input_scope=input_scope(args),
        raw_count=raw_count,
        reference_version=args.version,
        game_data_root=args.game_data_root,
        dragontail_root=args.dragontail_root,
        command=command_summary(argv),
    )


def input_scope(args: DecomposeArgs) -> str:
    """返回本次 raw 输入范围说明。"""

    if args.limit is None:
        return args.raw_root.as_posix()

    return f"{args.raw_root.as_posix()} limit={args.limit}"


def command_summary(argv: list[str] | None) -> str:
    """返回可审计的命令行摘要。"""

    command_args = sys.argv[1:] if argv is None else argv
    suffix = shlex.join([str(item) for item in command_args])

    if not suffix:
        return "python scripts/decompose_events.py"

    return f"python scripts/decompose_events.py {suffix}"


def has_source_segment(source_paths: list[str], segment: str) -> bool:
    """判断 source path 是否包含指定目录段。"""

    needle = f"/{segment}/"

    return any(needle in path.replace("\\", "/") for path in source_paths)


if __name__ == "__main__":
    raise SystemExit(main())
