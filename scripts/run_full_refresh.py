"""本地一键数据刷新：decompose→build→validate→metrics→ratchet 全链串联。"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter

from lol_event_semantics.dataset_runs import (
    DatasetRun,
    DatasetRunError,
    complete_dataset_run,
    create_dataset_run,
    fail_dataset_run,
)
from lol_event_semantics.full_refresh import RefreshResult, RefreshStep, StepRunner, run_refresh_steps

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

    repo_root: Path
    raw_root: Path
    reference_root: Path
    rules_root: Path
    overrides_root: Path
    contract_assets_root: Path
    pattern_policy: Path
    sources_manifest: Path
    frozen_profile: Path
    runs_root: Path
    baseline: Path
    update_first: bool
    game_version: str | None
    game_data_base_url: str | None
    allow_ratchet_regression: bool


@dataclass(frozen=True, slots=True)
class RefreshPaths:
    """绑定到单个 staging run 的隔离输出路径。"""

    output_root: Path
    work_root: Path
    report_root: Path


def main(argv: list[str] | None = None, *, runner: StepRunner | None = None) -> int:
    """组装并执行本地刷新链，遇阻断步骤失败即短路并报名。

    Args:
        argv: 命令行参数，None 时读 sys.argv。
        runner: 步骤执行器，便于测试注入；默认走 subprocess。
    """

    args = parse_args(argv)
    command_args = sys.argv[1:] if argv is None else argv
    try:
        run = create_dataset_run(
            args.runs_root,
            repo_root=args.repo_root,
            command=["python", "scripts/run_full_refresh.py", *command_args],
        )
    except (OSError, DatasetRunError) as exc:
        print(f"FAILED to create dataset run: {exc}")
        return 2

    paths = RefreshPaths(
        output_root=run.normalized_root,
        work_root=run.work_root,
        report_root=run.report_root,
    )
    steps = build_steps(args, paths)
    non_blocking = frozenset({STEP_RATCHET}) if args.allow_ratchet_regression else frozenset()
    started = perf_counter()
    try:
        result = run_refresh_steps(steps, runner or run_step, non_blocking=non_blocking)
    except Exception as exc:
        failed_root = record_failed_run(
            run,
            result=None,
            error_code="runner_exception",
            message=str(exc),
            elapsed_seconds=perf_counter() - started,
        )
        print(f"FAILED with exception; diagnostics={failed_root}: {exc}")
        return 2

    for step in result.results:
        if not step.blocking and step.exit_code != 0:
            print(f"WARNING: step {step.name} regressed (exit {step.exit_code}); continuing")

    failed = result.failed_step
    if failed is not None:
        failed_root = record_failed_run(
            run,
            result=result,
            error_code="step_failed",
            message=f"{failed.name} exited with {failed.exit_code}",
            elapsed_seconds=perf_counter() - started,
        )
        print(f"FAILED at step: {failed.name} (exit {failed.exit_code}); diagnostics={failed_root}")
        return failed.exit_code

    try:
        completed = complete_dataset_run(
            run,
            result,
            elapsed_seconds=perf_counter() - started,
        )
    except (OSError, DatasetRunError) as exc:
        failed_root = record_failed_run(
            run,
            result=result,
            error_code="completion_failed",
            message=str(exc),
            elapsed_seconds=perf_counter() - started,
        )
        print(f"FAILED to complete dataset run; diagnostics={failed_root}: {exc}")
        return 2

    print(
        f"full refresh completed candidate: {' -> '.join(step.name for step in result.results)} "
        f"run_id={completed.run_id} dataset_id={completed.dataset_id} root={completed.root}"
    )
    return 0


def record_failed_run(
    run: DatasetRun,
    *,
    result: RefreshResult | None,
    error_code: str,
    message: str,
    elapsed_seconds: float,
) -> Path:
    """尽力固化失败诊断；失败本身仍以明确异常终止。"""

    try:
        return fail_dataset_run(
            run,
            result=result,
            error_code=error_code,
            message=message,
            elapsed_seconds=elapsed_seconds,
        )
    except (OSError, DatasetRunError) as exc:
        print(f"WARNING: failed to finalize run diagnostics: {exc}")
        return run.staging_root


def run_step(name: str, argv: list[str]) -> int:
    """执行单个步骤子进程并返回退出码。"""

    print(f"==> {name}: {' '.join(argv)}")
    completed = subprocess.run(argv, check=False)
    return completed.returncode


def build_steps(args: FullRefreshArgs, paths: RefreshPaths) -> list[RefreshStep]:
    """按序组装刷新步骤及各自 argv。"""

    # report_root 下派生 validation 与 metrics 的固定报告路径，与 CI 口径一致。
    validation_json = paths.report_root / "normalized-validation.json"
    metrics_json = paths.report_root / "quality-metrics.json"

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
                    str(paths.work_root / "raw-round"),
                    "--game-version",
                    str(args.game_version),
                    "--game-data-base-url",
                    str(args.game_data_base_url),
                    "--report-root",
                    str(paths.report_root / "update-report"),
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
                "--rules-root",
                str(args.rules_root),
                "--overrides-root",
                str(args.overrides_root),
                "--output-root",
                str(paths.work_root),
                "--report-root",
                str(paths.report_root),
            ),
        )
    )
    steps.append(
        (
            STEP_BUILD,
            script_argv(
                "build_normalized_outputs.py",
                "--decomposition-root",
                str(paths.work_root),
                "--output-root",
                str(paths.output_root),
                "--repo-root",
                str(args.repo_root),
                "--raw-root",
                str(args.raw_root),
                "--reference-version",
                "cumulative",
                "--reference-root",
                str(args.reference_root),
                "--rules-root",
                str(args.rules_root),
                "--overrides-root",
                str(args.overrides_root),
                "--pattern-policy",
                str(args.pattern_policy),
                "--contract-assets-root",
                str(args.contract_assets_root),
                "--sources-manifest",
                str(args.sources_manifest),
                "--frozen-profile",
                str(args.frozen_profile),
            ),
        )
    )
    steps.append(
        (
            STEP_VALIDATE,
            script_argv(
                "validate_normalized_outputs.py",
                "--normalized-root",
                str(paths.output_root),
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
                str(paths.output_root),
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
                str(paths.output_root),
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
    parser.add_argument("--repo-root", type=Path, default=Path("."), help="Repository root for generator identity.")
    parser.add_argument("--raw-root", type=Path, default=Path("raw/events"), help="Cumulative raw events root.")
    parser.add_argument("--reference-root", type=Path, default=Path("reference"), help="Internal reference root.")
    parser.add_argument("--rules-root", type=Path, default=Path("rules"), help="Versioned rules root.")
    parser.add_argument("--overrides-root", type=Path, default=Path("overrides"), help="Manual overrides root.")
    parser.add_argument(
        "--contract-assets-root",
        type=Path,
        default=Path("data"),
        help="Committed schema/examples contract root.",
    )
    parser.add_argument(
        "--pattern-policy",
        type=Path,
        default=Path("rules/translation_patterns.yaml"),
        help="Versioned translation pattern policy.",
    )
    parser.add_argument(
        "--sources-manifest",
        type=Path,
        default=Path("dist/sources/manifest.json"),
        help="Frozen source acquisition manifest.",
    )
    parser.add_argument(
        "--frozen-profile",
        type=Path,
        default=Path("data/frozen-profile.v1.json"),
        help="Committed frozen release profile.",
    )
    parser.add_argument("--runs-root", type=Path, default=Path("dist/runs"), help="Transactional dataset runs root.")
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("data/quality-baseline.json"),
        help="Quality ratchet baseline JSON.",
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
        repo_root=namespace.repo_root,
        raw_root=namespace.raw_root,
        reference_root=namespace.reference_root,
        rules_root=namespace.rules_root,
        overrides_root=namespace.overrides_root,
        contract_assets_root=namespace.contract_assets_root,
        pattern_policy=namespace.pattern_policy,
        sources_manifest=namespace.sources_manifest,
        frozen_profile=namespace.frozen_profile,
        runs_root=namespace.runs_root,
        baseline=namespace.baseline,
        update_first=namespace.update_first,
        game_version=namespace.game_version,
        game_data_base_url=namespace.game_data_base_url,
        allow_ratchet_regression=namespace.allow_ratchet_regression,
    )


if __name__ == "__main__":
    raise SystemExit(main())
