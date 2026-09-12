"""比较两个本地 normalized 数据集的身份连续性与迁移。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.identity_migration import build_identity_report, load_identity_rows


def main(argv: list[str] | None = None) -> int:
    """写出审计报告；存在歧义返回 1，输入无效返回 2。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-root", required=True, type=Path)
    parser.add_argument("--current-root", required=True, type=Path)
    parser.add_argument("--output-json", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = build_identity_report(load_identity_rows(args.previous_root), load_identity_rows(args.current_root))
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError) as exc:
        print(f"identity comparison failed: {exc}")
        return 2
    print(
        f"identity comparison matched={report['matched_count']} changes={len(report['changes'])} ambiguous={len(report['ambiguous'])}"
    )
    return 1 if report["ambiguous"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
