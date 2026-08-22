"""校验 P4 Stable 绝对门禁并生成 support diff。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from lol_event_semantics.dataset_runs import DatasetRunError, read_approved_pointer, resolve_approved_dataset
from lol_event_semantics.tiered_quality import build_gate_report, build_support_diff


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    summary = read_object(args.normalized_root / "quality-summary.json")
    validation = read_object(args.validation_json)
    previous_root = approved_root(args.runs_root)
    support_diff = build_support_diff(args.normalized_root, previous_root)
    report = build_gate_report(summary, validation, support_diff)
    write_json(support_diff, args.support_diff_json)
    write_json(report, args.output_json)
    print(
        f"tiered-quality passed={report['passed']} stable={report['checks']['stable_product_gates']} "
        f"validation={report['checks']['global_validation']} support_diff={report['checks']['support_diff']}"
    )
    return 0 if report["passed"] is True else 1


def approved_root(runs_root: Path) -> Path | None:
    """返回当前 approved dataset；无 pointer 时表示首个候选。"""

    try:
        if read_approved_pointer(runs_root, required=False) is None:
            return None
        return resolve_approved_dataset(runs_root)
    except DatasetRunError as exc:
        raise ValueError(f"approved dataset 无法解析: {exc}") from exc


def read_object(path: Path) -> dict[str, Any]:
    """读取 JSON object。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 必须是 object: {path}")
    return payload


def write_json(document: dict[str, Any], path: Path) -> None:
    """以稳定格式写出 run report。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 CLI 参数。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--normalized-root", type=Path, required=True)
    parser.add_argument("--validation-json", type=Path, required=True)
    parser.add_argument("--runs-root", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--support-diff-json", type=Path, required=True)
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
