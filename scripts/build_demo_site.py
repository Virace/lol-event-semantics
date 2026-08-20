"""构建按英雄浏览的本地静态演示站。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lol_event_semantics.dataset_runs import DatasetRunError, resolve_approved_dataset
from lol_event_semantics.demo_site import DemoSiteError, build_demo_site


def main(argv: list[str] | None = None) -> int:
    """构建演示站并返回适合 CLI 的退出码。"""

    args = parse_args(argv)
    try:
        normalized_root = args.normalized_root or resolve_approved_dataset(args.runs_root)
        summary = build_demo_site(
            normalized_root=normalized_root,
            reference_root=args.reference_root,
            output_root=args.output_root,
        )
    except (DatasetRunError, DemoSiteError, OSError) as exc:
        print(f"demo site build failed: {exc}", file=sys.stderr)
        return 2

    print(
        "demo site built: "
        f"dataset={summary.dataset_id} champions={summary.champion_count} "
        f"events={summary.event_count} unassigned={summary.unassigned_event_count} "
        f"output={summary.output_root}"
    )
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析演示站构建参数。"""

    parser = argparse.ArgumentParser(description="Build the champion-centric static event semantics demo site.")
    parser.add_argument(
        "--normalized-root",
        type=Path,
        help="Explicit normalized candidate root. Omit to resolve approved-local.",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=Path("dist/runs"),
        help="Dataset runs root used when --normalized-root is omitted.",
    )
    parser.add_argument(
        "--reference-root",
        type=Path,
        default=Path("reference"),
        help="Champion and skin reference root.",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("dist/demo-site"),
        help="Generated static site root.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
