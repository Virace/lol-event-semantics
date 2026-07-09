"""本地一键数据刷新的步骤串联逻辑。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

# 单个刷新步骤：步骤名 + 传给子进程的 argv。
RefreshStep = tuple[str, list[str]]

# runner 约定：接收步骤 argv，返回进程退出码。
StepRunner = Callable[[str, list[str]], int]


@dataclass(slots=True)
class RefreshStepResult:
    """单个步骤的执行结果。"""

    name: str
    argv: list[str]
    exit_code: int
    blocking: bool


@dataclass(slots=True)
class RefreshResult:
    """整条刷新链的执行结果。"""

    results: list[RefreshStepResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否全链成功（无阻断步骤失败）。"""

        return self.failed_step is None

    @property
    def failed_step(self) -> RefreshStepResult | None:
        """返回第一个阻断性失败的步骤结果，没有则为 None。"""

        for result in self.results:
            if result.blocking and result.exit_code != 0:
                return result

        return None


def run_refresh_steps(
    steps: Sequence[RefreshStep],
    runner: StepRunner,
    *,
    non_blocking: frozenset[str] = frozenset(),
) -> RefreshResult:
    """按序执行刷新步骤，遇到阻断步骤失败即短路。

    Args:
        steps: 有序的 (步骤名, argv) 列表。
        runner: 执行单个步骤并返回退出码的可调用对象，便于测试注入。
        non_blocking: 失败时不短路、仅告警继续的步骤名集合。

    Returns:
        含各步骤结果的 RefreshResult；阻断步骤非 0 退出后不再执行后续步骤。
    """

    result = RefreshResult()

    for name, argv in steps:
        blocking = name not in non_blocking
        exit_code = runner(name, list(argv))
        result.results.append(
            RefreshStepResult(name=name, argv=list(argv), exit_code=exit_code, blocking=blocking)
        )

        # 阻断步骤失败即短路，后续步骤不执行；非阻断步骤失败仅记录并继续。
        if blocking and exit_code != 0:
            break

    return result
