"""生成只读、确定性的 normalized translation pattern 盘点报告。"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.pattern_catalog import (
    build_pattern_report,
    load_normalized_records,
    load_pattern_policy,
    write_pattern_report,
)


@dataclass(frozen=True, slots=True)
class ReportTranslationPatternsArgs:
    """translation pattern 报告命令参数。"""

    normalized_root: Path
    policy: Path
    output_json: Path
    output_md: Path


def main(argv: list[str] | None = None) -> int:
    """读取 normalized 数据并写出 JSON/Markdown inventory。"""

    args = parse_args(argv)
    try:
        policy = load_pattern_policy(args.policy)
        records = load_normalized_records(args.normalized_root)
        report = build_pattern_report(records, policy)
        write_pattern_report(report, args.output_json, args.output_md)
    except (OSError, ValueError) as exc:
        print(f"translation_patterns error: {exc}", file=sys.stderr)
        return 1
    summary = report["summary"]
    print(
        "translation_patterns "
        f"records={summary['record_count']} "
        f"patterns={summary['pattern_count']} "
        f"stable_proposals={summary['stable_proposal_pattern_count']} "
        f"sfx_candidates={summary['sfx_candidate_pattern_count']}"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> ReportTranslationPatternsArgs:
    """解析 translation pattern inventory 命令参数。"""

    parser = argparse.ArgumentParser(description="Report deterministic translation pattern inventory.")
    parser.add_argument(
        "--normalized-root",
        type=Path,
        required=True,
        help="Normalized output root, for example dist/normalized.",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        default=Path("rules/translation_patterns.yaml"),
        help="Pattern policy YAML path.",
    )
    parser.add_argument("--output-json", type=Path, required=True, help="Output inventory JSON path.")
    parser.add_argument("--output-md", type=Path, required=True, help="Output inventory Markdown path.")
    namespace = parser.parse_args(argv)
    return ReportTranslationPatternsArgs(
        normalized_root=namespace.normalized_root,
        policy=namespace.policy,
        output_json=namespace.output_json,
        output_md=namespace.output_md,
    )


if __name__ == "__main__":
    raise SystemExit(main())
