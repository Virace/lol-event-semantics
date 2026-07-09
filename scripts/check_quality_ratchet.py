"""质量棘轮门禁：当前指标对基线不得恶化。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.quality_metrics import collect_quality_metrics, compare_quality_metrics


@dataclass(slots=True)
class CheckQualityRatchetArgs:
    """质量棘轮门禁参数。"""

    baseline: Path
    normalized_root: Path
    validation_json: Path
    update_baseline: bool


def main(argv: list[str] | None = None) -> int:
    """采集当前指标并与基线执行棘轮比较。

    Returns:
        0=通过；1=存在恶化项；2=用法或文件错误。
    """

    args = parse_args(argv)

    if not args.baseline.exists():
        print(f"baseline not found: {args.baseline}")
        print("run scripts/report_quality_metrics.py first to create the baseline file")
        return 2
    if not args.normalized_root.exists():
        print(f"normalized root not found: {args.normalized_root}")
        return 2
    if not args.validation_json.exists():
        print(f"validation report not found: {args.validation_json}")
        return 2

    # 基线损坏 / normalized 数据损坏 / validation 口径漂移都属于文件错误（exit 2）。
    # 基线侧错误单独带路径上报，与 validation/shard 侧错误区分开。
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        if not isinstance(baseline, dict):
            raise ValueError("必须是 JSON object")
    except (ValueError, OSError) as exc:
        print(f"quality ratchet baseline error: {args.baseline}: {exc}")
        return 2

    try:
        current = collect_quality_metrics(args.normalized_root, args.validation_json)
    except (ValueError, OSError) as exc:
        print(f"quality ratchet input error: {exc}")
        return 2

    result = compare_quality_metrics(baseline, current)

    for check in result["checks"]:
        print(
            f"[{check['verdict'].upper():<7}] {check['metric']}: "
            f"baseline={check['baseline']} current={check['current']}"
        )

    if result["passed"]:
        print("quality ratchet passed")
        # 受门禁项出现改善时提示收紧基线，避免棘轮停留在旧水位。
        improved = [
            check["metric"]
            for check in result["checks"]
            if check["verdict"] == "ok" and check["current"] < check["baseline"]
        ]
        if improved:
            print(f"improvement detected: {', '.join(improved)}; rerun with --update-baseline to tighten the baseline")
    else:
        print(f"quality ratchet FAILED: {', '.join(result['failed_metrics'])}")

    if args.update_baseline:
        if not result["passed"]:
            print("WARNING: updating baseline despite worsened metrics; this loosens the ratchet by human decision.")
        # 基线写失败属于文件错误（exit 2），不得裸退成 exit 1 被误读为指标恶化。
        try:
            write_baseline(args.baseline, current)
        except OSError as exc:
            print(f"quality ratchet baseline write error: {args.baseline}: {exc}")
            return 2
        print(f"baseline updated: {args.baseline}")

    return 0 if result["passed"] else 1


def write_baseline(baseline_path: Path, metrics: dict[str, object]) -> None:
    """用当前指标重写基线文件，格式与 report_quality_metrics 输出一致。

    Args:
        baseline_path: 基线 JSON 路径。
        metrics: 当前指标 dict。
    """

    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def parse_args(argv: list[str] | None = None) -> CheckQualityRatchetArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Check quality metrics against the ratchet baseline.")
    parser.add_argument(
        "--baseline",
        type=Path,
        required=True,
        help="Baseline metrics JSON, for example data/quality-baseline.json.",
    )
    parser.add_argument(
        "--normalized-root",
        type=Path,
        required=True,
        help="Normalized output root, for example dist/normalized.",
    )
    parser.add_argument(
        "--validation-json",
        type=Path,
        required=True,
        help="Normalized validation report JSON path.",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Rewrite the baseline file with current metrics (allowed even on failure, with a warning).",
    )
    namespace = parser.parse_args(argv)

    return CheckQualityRatchetArgs(
        baseline=namespace.baseline,
        normalized_root=namespace.normalized_root,
        validation_json=namespace.validation_json,
        update_baseline=namespace.update_baseline,
    )


if __name__ == "__main__":
    raise SystemExit(main())
