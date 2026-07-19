"""兼容入口：按事件名查询 normalized 输出。"""

from __future__ import annotations

from lol_event_semantics.normalized_lookup_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
