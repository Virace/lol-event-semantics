"""生成本地事件语义审核静态站。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.review_site import read_jsonl_records, write_review_site


@dataclass(slots=True)
class ReviewSiteArgs:
    """审核站生成参数。"""

    inputs: list[Path]
    output: Path
    max_examples: int


def main(argv: list[str] | None = None) -> int:
    """运行审核站生成流程。"""

    args = parse_args(argv)
    records = read_jsonl_records(args.inputs)
    payload = write_review_site(records, args.output, max_examples=args.max_examples)
    group_count = payload["summary"]["group_total"]
    print(f"records={len(records)} groups={group_count} output={args.output}")
    return 0


def parse_args(argv: list[str] | None = None) -> ReviewSiteArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build a local static review site for event decomposition artifacts.")
    parser.add_argument(
        "--input",
        dest="inputs",
        type=Path,
        action="append",
        required=True,
        help="Input JSONL decomposition file. Can be provided multiple times.",
    )
    parser.add_argument("--output", type=Path, required=True, help="Output HTML path, for example dist/16.5/review-site/index.html.")
    parser.add_argument("--max-examples", type=int, default=8, help="Maximum examples embedded per review group.")
    namespace = parser.parse_args(argv)

    return ReviewSiteArgs(
        inputs=list(namespace.inputs),
        output=namespace.output,
        max_examples=namespace.max_examples,
    )


if __name__ == "__main__":
    raise SystemExit(main())
