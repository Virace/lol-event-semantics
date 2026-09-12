"""验证一个消费 profile 的文件闭包与源投影。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.consumption import validate_consumption


def main(argv: list[str] | None = None) -> int:
    """可选复算原审计数据，检查消费压缩没有损失声明的语义。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--normalized-root", type=Path)
    args = parser.parse_args(argv)
    try:
        result = validate_consumption(args.root, args.normalized_root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"consumption validation failed: {exc}")
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
