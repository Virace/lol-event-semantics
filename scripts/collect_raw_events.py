"""从当前 live 来源自动采集英雄、皮肤与地图 raw round。"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 严格解析会临时启用底层诊断；默认只显示告警，允许调用方显式选择日志级别。
os.environ.setdefault("LOGURU_LEVEL", "WARNING")

from lol_event_semantics.collection import collect_live
from lol_event_semantics.wad_source import DEFAULT_REGION


def selection(value: str) -> tuple[str, ...] | None:
    """解析 all、none 或逗号分隔的实体选择。"""

    if value == "all":
        return None
    if value == "none":
        return ()
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    if not items:
        raise argparse.ArgumentTypeError("范围不能为空；使用 all、none 或逗号分隔的名字/ID")
    return items


def main(argv: list[str] | None = None) -> int:
    """运行采集；失败返回非零，重复相同命令可续跑。"""

    parser = argparse.ArgumentParser(description="采集 live GAME 事件，自动发现请求并按实体续跑。")
    parser.add_argument("--output-root", type=Path, default=Path("dist/collection"))
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--champions", type=selection, default=None, help="all、none 或英雄别名/数字 ID")
    parser.add_argument("--maps", type=selection, default=None, help="all、none 或地图数字 ID")
    args = parser.parse_args(argv)
    if args.champions == () and args.maps == ():
        parser.error("英雄与地图不能同时为 none")
    try:
        root = collect_live(args.output_root, region=args.region, champions=args.champions, maps=args.maps)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"采集失败：{exc}", file=sys.stderr)
        return 1
    print(f"采集完成：{root}")
    print(f"raw 输入：{root / 'raw'}")
    print(f"来源清单：{root / 'source-manifest.json'}")
    print("采集结果尚未合并或批准；新版本仍须建立并审阅对应 frozen profile。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
