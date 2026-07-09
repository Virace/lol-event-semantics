"""输出 normalized 质量指标报告。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.quality_metrics import collect_quality_metrics


@dataclass(slots=True)
class ReportQualityMetricsArgs:
    """质量指标报告参数。"""

    normalized_root: Path
    validation_json: Path
    output_json: Path


def main(argv: list[str] | None = None) -> int:
    """统计质量指标并写出 JSON 报告。"""

    args = parse_args(argv)
    metrics = collect_quality_metrics(args.normalized_root, args.validation_json)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print(
        "quality_metrics "
        f"records={metrics['record_count']} "
        f"translation={metrics['translation']['record_count']} "
        f"fallback={metrics['fallback_label']['record_count']} "
        f"residual_english={metrics['residual_english']['record_count']} "
        f"errors={metrics['validation']['error_count']} "
        f"warnings={metrics['validation']['warning_count']}"
    )

    return 0


def parse_args(argv: list[str] | None = None) -> ReportQualityMetricsArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Report quality metrics for normalized event semantic outputs.")
    parser.add_argument(
        "--normalized-root",
        type=Path,
        required=True,
        help="Normalized output root, for example dist/normalized.",
    )
    parser.add_argument(
        "--validation-json",
        type=Path,
        required=True,
        help="Normalized validation report JSON path.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        required=True,
        help="Output metrics JSON path.",
    )
    namespace = parser.parse_args(argv)

    return ReportQualityMetricsArgs(
        normalized_root=namespace.normalized_root,
        validation_json=namespace.validation_json,
        output_json=namespace.output_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
