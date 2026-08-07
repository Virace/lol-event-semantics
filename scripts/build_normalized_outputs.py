"""构建对外 normalized 输出。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.normalized_output import read_decomposition_root, write_normalized_outputs
from lol_event_semantics.pattern_catalog import load_pattern_policy


@dataclass(slots=True)
class BuildNormalizedArgs:
    """normalized 构建参数。"""

    decomposition_root: Path
    output_root: Path
    reference_version: str
    pattern_policy: Path
    report_root: Path | None


def main(argv: list[str] | None = None) -> int:
    """运行 normalized 输出构建。"""

    args = parse_args(argv)
    records = read_decomposition_root(args.decomposition_root)
    write_normalized_outputs(
        records,
        args.output_root,
        reference_version=args.reference_version,
        pattern_policy=load_pattern_policy(args.pattern_policy),
    )

    print(f"records={len(records)} output={args.output_root}")
    return 0


def parse_args(argv: list[str] | None = None) -> BuildNormalizedArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build consumer-facing normalized event semantic outputs.")
    parser.add_argument(
        "--decomposition-root",
        type=Path,
        required=True,
        help="Directory containing champions.jsonl and maps.jsonl from decompose_events.py.",
    )
    parser.add_argument("--output-root", type=Path, required=True, help="Output root, for example dist/normalized.")
    parser.add_argument(
        "--reference-version",
        default="cumulative",
        help="Optional metadata label for the reference collection used by this run. Defaults to cumulative.",
    )
    parser.add_argument(
        "--pattern-policy",
        type=Path,
        default=Path("rules/translation_patterns.yaml"),
        help="Versioned translation pattern and promotion policy.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        help="Reserved report root for future normalized QA reports. Currently unused.",
    )
    namespace = parser.parse_args(argv)

    return BuildNormalizedArgs(
        decomposition_root=namespace.decomposition_root,
        output_root=namespace.output_root,
        reference_version=namespace.reference_version,
        pattern_policy=namespace.pattern_policy,
        report_root=namespace.report_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
