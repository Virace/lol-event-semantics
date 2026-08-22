"""把 clean completed dataset run 原子提升为 approved-local。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.dataset_runs import (
    DatasetRunError,
    PromotionConflictError,
    PromotionLockedError,
    promote_completed_run,
    read_approved_pointer,
)
from lol_event_semantics.review_decisions import ReviewDecisionError, load_decision_set


@dataclass(frozen=True, slots=True)
class PromoteArgs:
    """promotion CLI 参数。"""

    runs_root: Path
    run_id: str
    expected_previous_digest: str | None
    observe_current: bool
    decisions: Path | None


def main(argv: list[str] | None = None) -> int:
    """执行带 lock/CAS 的 approved-local pointer promotion。"""

    args = parse_args(argv)
    expected = args.expected_previous_digest
    try:
        approval = load_decision_set(args.decisions) if args.decisions is not None else None
        if args.observe_current:
            current = read_approved_pointer(args.runs_root, required=False)
            expected = str(current["content_digest"]) if current is not None else None
        pointer = promote_completed_run(
            args.runs_root,
            args.run_id,
            expected_previous_digest=expected,
            approval=approval,
        )
    except PromotionConflictError as exc:
        print(f"promotion conflict: {exc}", file=sys.stderr)
        return 3
    except PromotionLockedError as exc:
        print(f"promotion locked: {exc}", file=sys.stderr)
        return 4
    except (DatasetRunError, ReviewDecisionError, OSError) as exc:
        print(f"promotion rejected: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(pointer, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def parse_args(argv: list[str] | None = None) -> PromoteArgs:
    """解析 promotion CLI 参数。"""

    parser = argparse.ArgumentParser(description="Promote a clean completed dataset run to approved-local.")
    parser.add_argument("--runs-root", type=Path, default=Path("dist/runs"), help="Transactional dataset runs root.")
    parser.add_argument("--run-id", required=True, help="Completed run id to promote.")
    parser.add_argument(
        "--decisions",
        type=Path,
        help="Candidate-bound dataset approval exported by the Review Site; required while manual review is pending.",
    )
    parser.add_argument(
        "--expected-previous-digest",
        help="CAS precondition: 64-char digest, or 'none' when no pointer must exist. Omit to observe current first.",
    )
    namespace = parser.parse_args(argv)

    raw_expected = namespace.expected_previous_digest
    observe_current = raw_expected is None
    expected: str | None = None
    if raw_expected is not None and raw_expected.lower() != "none":
        if len(raw_expected) != 64 or any(char not in "0123456789abcdef" for char in raw_expected):
            parser.error("--expected-previous-digest must be a lowercase SHA-256 digest or 'none'")
        expected = raw_expected

    return PromoteArgs(
        runs_root=namespace.runs_root,
        run_id=namespace.run_id,
        expected_previous_digest=expected,
        observe_current=observe_current,
        decisions=namespace.decisions,
    )


if __name__ == "__main__":
    raise SystemExit(main())
