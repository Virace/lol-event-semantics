"""PostToolUse 钩子：编辑 src/tests 下的模块后跑对应单元测试。

映射规则：
- `src/lol_event_semantics/<mod>.py` -> `tests/test_<mod>.py`
- `tests/test_<mod>.py` -> 直接跑该测试模块

找不到对应测试文件时静默退出（exit 0），不打扰正常编辑流。
测试失败时 exit 2，把失败输出反馈给 Claude。

注意：定向到单个测试模块，但个别大测试文件可能较慢。
如需临时关闭，删除 .claude/settings.json 里对应的 PostToolUse 配置即可。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import PurePath

PACKAGE_PREFIX = "src/lol_event_semantics/"
TESTS_PREFIX = "tests/"


def _project_relative(file_path: str, root: str) -> str | None:
    """把 file_path 规范化为相对项目根的 posix 路径。"""

    try:
        target = PurePath(file_path)
        if target.is_absolute():
            rel = os.path.relpath(str(target), root)
        else:
            rel = str(target)
    except (ValueError, OSError):
        return None

    posix = rel.replace("\\", "/").lstrip("./")
    if posix.startswith("../"):
        return None
    return posix


def _test_module(rel: str) -> str | None:
    """根据被编辑文件推导要运行的 unittest 模块路径，无对应测试返回 None。"""

    if rel.startswith(PACKAGE_PREFIX) and rel.endswith(".py"):
        stem = rel[len(PACKAGE_PREFIX):-len(".py")]
        if "/" in stem or stem.startswith("_"):
            return None
        return f"tests.test_{stem}"

    if rel.startswith(TESTS_PREFIX) and rel.endswith(".py"):
        name = rel[len(TESTS_PREFIX):-len(".py")]
        if name.startswith("test_"):
            return f"tests.{name}"

    return None


def main() -> int:
    """读取 PostToolUse 负载并按需运行对应测试。"""

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        return 0

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = payload.get("tool_input", {}).get("file_path")
    if not isinstance(file_path, str) or not file_path:
        return 0

    rel = _project_relative(file_path, project_dir)
    if rel is None:
        return 0

    module = _test_module(rel)
    if module is None:
        return 0

    # 确认对应测试文件存在，避免对没有测试的模块空跑。
    test_file = PurePath(project_dir) / (module.replace(".", "/") + ".py")
    if not os.path.isfile(str(test_file)):
        return 0

    result = subprocess.run(
        ["uv", "run", "python", "-m", "unittest", module],
        cwd=project_dir,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        sys.stderr.write(f"`{module}` 单元测试未通过，请检查本次改动：\n")
        sys.stderr.write(result.stdout[-4000:])
        sys.stderr.write(result.stderr[-4000:])
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
