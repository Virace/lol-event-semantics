"""PreToolUse 钩子：拦截对不可变源数据与生成物的直接编辑。

对应 AGENTS.md 约束：
- `raw/` 为不可变源数据，只能由 update_raw_events 等脚本累积写入。
- `dist/` 为生成产物，应由 build_* 脚本重写，不应手改。

仅拦截 Claude 的 Edit/Write/MultiEdit 工具，不影响脚本通过 Bash 写入这些目录。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import PurePath

# 受保护的顶层目录前缀（posix 风格，相对项目根）。
PROTECTED_PREFIXES = ("raw/", "dist/")


def _project_relative(file_path: str) -> str | None:
    """把工具传入的 file_path 规范化为相对项目根的 posix 路径。

    无法定位项目根或路径在项目根之外时返回 None（视为不拦截）。
    """

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return None

    try:
        root = PurePath(project_dir)
        target = PurePath(file_path)
        # file_path 可能是绝对路径，也可能已是相对路径。
        if target.is_absolute():
            rel = os.path.relpath(str(target), str(root))
        else:
            rel = str(target)
    except (ValueError, OSError):
        return None

    posix = rel.replace("\\", "/")
    if posix.startswith("../"):
        # 在项目根之外，不归本钩子管。
        return None
    return posix.lstrip("./")


def main() -> int:
    """读取 stdin 的 PreToolUse 负载，命中受保护路径时阻断。"""

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = payload.get("tool_input", {}).get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0

    rel = _project_relative(file_path)
    if rel is None:
        return 0

    for prefix in PROTECTED_PREFIXES:
        if rel == prefix.rstrip("/") or rel.startswith(prefix):
            sys.stderr.write(
                f"已阻断对受保护路径 `{rel}` 的直接编辑。\n"
                "AGENTS.md 约定：raw/ 为不可变源数据，dist/ 为生成产物。\n"
                "请改动 rules/ 或 overrides/ 等源输入，再用 scripts/ 下的 update_*/build_* 脚本重新生成。\n"
            )
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
