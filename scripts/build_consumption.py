"""从本地审计数据导出网页、软件和简单查询消费包。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.consumption import PROFILES, build_consumption
from lol_event_semantics.dataset_runs import resolve_approved_dataset


def main(argv: list[str] | None = None) -> int:
    """选择本地候选或批准数据，写入新的消费输出目录。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-root", type=Path, help="显式本地候选；省略时读取 approved-local")
    parser.add_argument("--runs-root", type=Path, default=Path("dist/runs"))
    parser.add_argument("--output-root", type=Path, required=True, help="必须尚不存在的输出目录")
    parser.add_argument("--profile", choices=PROFILES, action="append", help="可重复；默认生成全部 profile")
    parser.add_argument("--support-level", choices=("stable", "experimental", "unsupported"), action="append")
    parser.add_argument("--asset-kind", choices=("vo", "sfx", "control", "unknown"), action="append")
    parser.add_argument("--entity", action="append", help="精确 kind:alias，可重复")
    args = parser.parse_args(argv)
    try:
        root = args.normalized_root or resolve_approved_dataset(args.runs_root)
        result = build_consumption(
            root,
            args.output_root,
            profiles=args.profile or PROFILES,
            support_levels=args.support_level or (),
            asset_kinds=args.asset_kind or (),
            entities=args.entity or (),
        )
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"consumption build failed: {exc}")
        return 2
    print(json.dumps({"record_count": result["record_count"], "products": result["products"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
