"""按关键词检索客户端 RST 字符串表，输出中英同键对照。

用法示例：
    uv run python scripts/search_stringtable.py 厄塔汗 通告员
    uv run python scripts/search_stringtable.py "first blood" --limit 5

默认读取 dist/sources/stringtable/ 下的 Global.zh_CN-lol.stringtable 与
Global.en_US-lol.stringtable（scripts/download_sources.py 产物），中英按
key hash 对齐；关键词同时在两侧
原文中匹配（大小写不敏感），并给出两侧独立命中计数，可用于核证
「客户端是否用过某个词」。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lol_event_semantics.rst_stringtable import load


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="检索客户端 RST 字符串表（中英同键对照）。")
    parser.add_argument("keywords", nargs="+", help="要检索的关键词（中英文均可）")
    parser.add_argument(
        "--localized-root", type=Path, default=Path("dist/sources/stringtable"), help="stringtable 所在目录"
    )
    parser.add_argument("--limit", type=int, default=8, help="每个关键词最多展示的对照条数")
    return parser.parse_args()


def main() -> int:
    """入口：加载中英两表并逐关键词输出命中计数与对照样例。"""

    # Windows 控制台默认 GBK，强制 UTF-8 避免中文乱码。
    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    zh = load(args.localized_root / "Global.zh_CN-lol.stringtable")
    en = load(args.localized_root / "Global.en_US-lol.stringtable")
    print(f"zh 条目 {len(zh.entries)} / en 条目 {len(en.entries)} (hash_bits={zh.hash_bits})")

    for keyword in args.keywords:
        needle = keyword.lower()
        hashes = [h for h, s in zh.entries.items() if needle in s.lower()]
        hashes += [h for h, s in en.entries.items() if needle in s.lower() and h not in set(hashes)]
        print(f"\n== {keyword}: zh {sum(1 for s in zh.entries.values() if needle in s.lower())} 处"
              f" / en {sum(1 for s in en.entries.values() if needle in s.lower())} 处")
        for h in hashes[: args.limit]:
            print(f"  zh: {zh.entries.get(h, '<无同键>')}")
            print(f"  en: {en.entries.get(h, '<无同键>')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
