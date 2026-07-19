"""校验对外 normalized 输出。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.normalized_validation import validate_normalized_root


@dataclass(slots=True)
class ValidateNormalizedArgs:
    """normalized 校验参数。"""

    normalized_root: Path
    output_json: Path | None
    fail_on_warning: bool


def main(argv: list[str] | None = None) -> int:
    """运行 normalized 输出校验。"""

    args = parse_args(argv)
    report = validate_normalized_root(args.normalized_root)

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    print(
        "normalized_validation "
        f"records={report.record_count} entities={report.entity_count} "
        f"errors={report.error_count} warnings={report.warning_count}"
    )

    if report.error_count > 0:
        return 1

    if args.fail_on_warning and report.warning_count > 0:
        return 2

    return 0


def parse_args(argv: list[str] | None = None) -> ValidateNormalizedArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Validate consumer-facing normalized event semantic outputs.")
    parser.add_argument(
        "--normalized-root",
        type=Path,
        required=True,
        help="Normalized output root, for example dist/normalized.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        help="Optional JSON validation report path.",
    )
    parser.add_argument(
        "--fail-on-warning",
        action="store_true",
        help="Return exit code 2 when quality warnings are present.",
    )
    namespace = parser.parse_args(argv)

    return ValidateNormalizedArgs(
        normalized_root=namespace.normalized_root,
        output_json=namespace.output_json,
        fail_on_warning=namespace.fail_on_warning,
    )


if __name__ == "__main__":
    raise SystemExit(main())
