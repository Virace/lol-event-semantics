"""本地一键数据刷新：decompose→build→validate→metrics→ratchet 全链串联。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.full_refresh import RefreshStep, StepRunner, run_refresh_steps

# 仓库内脚本目录，用于组装各步骤的入口脚本路径。
SCRIPTS_DIR = Path(__file__).resolve().parent

# 与 CI（T6.1）对齐的固定步骤名，失败时按名上报。
STEP_UPDATE = "update"
STEP_DECOMPOSE = "decompose"
STEP_BUILD = "build"
STEP_VALIDATE = "validate"
STEP_RATCHET = "ratchet"
STEP_METRICS = "metrics"


@dataclass(slots=True)
class FullRefreshArgs:
    """一键刷新脚本参数。"""

    raw_root: Path
    reference_root: Path
    output_root: Path
    work_root: Path
    baseline: Path
    report_root: Path
    update_first: bool
    game_version: str | None
    game_data_base_url: str | None
    allow_ratchet_regression: bool


def main(argv: list[str] | None = None, *, runner: StepRunner | None = None) -> int:
    """组装并执行本地刷新链，遇阻断步骤失败即短路并报名。

    Args:
        argv: 命令行参数，None 时读 sys.argv。
        runner: 步骤执行器，便于测试注入；默认走 subprocess。
    """

    args = parse_args(argv)
    steps = build_steps(args)
    non_blocking = frozenset({STEP_RATCHET}) if args.allow_ratchet_regression else frozenset()

    result = run_refresh_steps(steps, runner or run_step, non_blocking=non_blocking)

    for step in result.results:
        if not step.blocking and step.exit_code != 0:
            print(f"WARNING: step {step.name} regressed (exit {step.exit_code}); continuing")

    failed = result.failed_step
    if failed is not None:
        print(f"FAILED at step: {failed.name} (exit {failed.exit_code})")
        return failed.exit_code

    print(f"full refresh succeeded: {' -> '.join(step.name for step in result.results)}")
    return 0


def run_step(name: str, argv: list[str]) -> int:
    """执行单个步骤子进程并返回退出码。"""

    print(f"==> {name}: {' '.join(argv)}")
    completed = subprocess.run(argv, check=False)
    return completed.returncode


def build_steps(args: FullRefreshArgs) -> list[RefreshStep]:
    """按序组装刷新步骤及各自 argv。"""

    # report_root 下派生 validation 与 metrics 的固定报告路径，与 CI 口径一致。
    validation_json = args.report_root / "normalized-validation.json"
    metrics_json = args.report_root / "quality-metrics.json"

    steps: list[RefreshStep] = []

    if args.update_first:
        steps.append(
            (
                STEP_UPDATE,
                script_argv(
                    "update_raw_events.py",
                    "--mode",
                    "online",
                    "--reference-root",
                    str(args.reference_root),
                    "--raw-root",
                    str(args.raw_root),
                    "--state-file",
                    str(args.raw_root.parent / "update-state.json"),
                    "--raw-input-root",
                    str(args.work_root / "raw-round"),
                    "--game-version",
                    str(args.game_version),
                    "--game-data-base-url",
                    str(args.game_data_base_url),
                    "--report-root",
                    str(args.report_root / "update-report"),
                ),
            )
        )

    steps.append(
        (
            STEP_DECOMPOSE,
            script_argv(
                "decompose_events.py",
                "--raw-root",
                str(args.raw_root),
                "--reference-root",
                str(args.reference_root),
                "--output-root",
                str(args.work_root),
                "--report-root",
                str(args.report_root),
            ),
        )
    )
    steps.append(
        (
            STEP_BUILD,
            script_argv(
                "build_normalized_outputs.py",
                "--decomposition-root",
                str(args.work_root),
                "--output-root",
                str(args.output_root),
                "--reference-version",
                "cumulative",
            ),
        )
    )
    steps.append(
        (
            STEP_VALIDATE,
            script_argv(
                "validate_normalized_outputs.py",
                "--normalized-root",
                str(args.output_root),
                "--output-json",
                str(validation_json),
            ),
        )
    )
    # metrics 置于 ratchet 之前：与 CI（metrics if:always 在 ratchet 前）对齐，
    # 使 ratchet 阻断失败时 metrics JSON 已产出，兑现 CI 本地等价物定位。
    steps.append(
        (
            STEP_METRICS,
            script_argv(
                "report_quality_metrics.py",
                "--normalized-root",
                str(args.output_root),
                "--validation-json",
                str(validation_json),
                "--output-json",
                str(metrics_json),
            ),
        )
    )
    # ratchet 作为可非阻断的最后一步：--allow-ratchet-regression 时仅告警不短路。
    steps.append(
        (
            STEP_RATCHET,
            script_argv(
                "check_quality_ratchet.py",
                "--baseline",
                str(args.baseline),
                "--normalized-root",
                str(args.output_root),
                "--validation-json",
                str(validation_json),
            ),
        )
    )

    return steps


def script_argv(script_name: str, *script_args: str) -> list[str]:
    """构造 [python, scripts/<name>, *args] 形式的子进程 argv。"""

    return [sys.executable, str(SCRIPTS_DIR / script_name), *script_args]


def parse_args(argv: list[str] | None = None) -> FullRefreshArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Run the local decompose->build->validate->metrics->ratchet refresh chain.",
    )
    parser.add_argument("--raw-root", type=Path, default=Path("raw/events"), help="Cumulative raw events root.")
    parser.add_argument("--reference-root", type=Path, default=Path("reference"), help="Internal reference root.")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("dist/normalized"),
        help="Normalized output root.",
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path("dist/work/event-decomposition"),
        help="Intermediate decomposition output root.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("data/quality-baseline.json"),
        help="Quality ratchet baseline JSON.",
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=Path("dist/reports/runs/latest"),
        help="Report root for decomposition, validation, and metrics reports.",
    )
    parser.add_argument(
        "--update-first",
        action="store_true",
        help="Run the online raw/reference update before the core chain (needs --game-version and --game-data-base-url).",
    )
    parser.add_argument("--game-version", help="Source GAME version label for --update-first.")
    parser.add_argument("--game-data-base-url", help="Online GAME data snapshot root for --update-first.")
    parser.add_argument(
        "--allow-ratchet-regression",
        action="store_true",
        help="Treat quality ratchet failure as a non-blocking warning and continue to metrics.",
    )
    namespace = parser.parse_args(argv)

    if namespace.update_first and (namespace.game_version is None or namespace.game_data_base_url is None):
        parser.error("--update-first requires --game-version and --game-data-base-url")

    return FullRefreshArgs(
        raw_root=namespace.raw_root,
        reference_root=namespace.reference_root,
        output_root=namespace.output_root,
        work_root=namespace.work_root,
        baseline=namespace.baseline,
        report_root=namespace.report_root,
        update_first=namespace.update_first,
        game_version=namespace.game_version,
        game_data_base_url=namespace.game_data_base_url,
        allow_ratchet_regression=namespace.allow_ratchet_regression,
    )


if __name__ == "__main__":
    raise SystemExit(main())
