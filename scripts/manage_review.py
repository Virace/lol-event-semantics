"""构建、应用和验证 V1 产品审核决定。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.product_review import build_review_bundle, write_review_site
from lol_event_semantics.review_decisions import (
    ReviewDecisionError,
    apply_source_review,
    load_decision_set,
    load_decision_source,
    load_review_bundle,
    verify_source_review,
)


@dataclass(frozen=True, slots=True)
class BuildArgs:
    """review bundle 构建参数。"""

    normalized_root: Path
    support_diff: Path
    decision_source: Path
    rules_root: Path
    output_root: Path


@dataclass(frozen=True, slots=True)
class ApplyArgs:
    """review decision 应用参数。"""

    bundle: Path
    decisions: Path
    decision_source: Path
    rules_root: Path
    accepted_corpus: Path


@dataclass(frozen=True, slots=True)
class VerifyArgs:
    """已应用决定验证参数。"""

    bundle: Path
    decision_source: Path
    rules_root: Path
    accepted_corpus: Path
    output: Path | None


ReviewArgs = BuildArgs | ApplyArgs | VerifyArgs


def main(argv: list[str] | None = None) -> int:
    """执行 review build/apply/verify 子命令。"""

    try:
        args = parse_args(argv)
        if isinstance(args, BuildArgs):
            bundle = build_review_bundle(
                args.normalized_root,
                args.support_diff,
                args.decision_source,
                args.rules_root,
            )
            bundle_path, site_path = write_review_site(bundle, args.output_root)
            print(
                f"review bundle built: phase={bundle['phase']} targets={bundle['required_target_count']} "
                f"bundle={bundle_path} site={site_path}"
            )
            return 0
        if isinstance(args, ApplyArgs):
            result = apply_source_review(
                load_review_bundle(args.bundle),
                load_decision_set(args.decisions),
                source_path=args.decision_source,
                corpus_path=args.accepted_corpus,
            )
            print(
                f"review decisions applied: decisions={result['decision_count']} "
                f"patterns={result['pattern_count']} digest={result['decision_set_digest']}"
            )
            return 0

        bundle = load_review_bundle(args.bundle)
        source = load_decision_source(args.decision_source)
        corpus = _read_object(args.accepted_corpus)
        result = verify_source_review(bundle, source, corpus)
        if args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(
            f"review decisions verified: decisions={result['decision_count']} "
            f"corpus={result['accepted_corpus_entry_count']} digest={result['decision_set_digest']}"
        )
        return 0
    except (OSError, json.JSONDecodeError, ReviewDecisionError) as exc:
        print(f"review command rejected: {exc}", file=sys.stderr)
        return 2


def parse_args(argv: list[str] | None = None) -> ReviewArgs:
    """解析 review 管理子命令。"""

    parser = argparse.ArgumentParser(description="Build, apply, or verify versioned product review decisions.")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build", help="Build a candidate-bound review bundle and static site.")
    build.add_argument("--normalized-root", type=Path, required=True)
    build.add_argument("--support-diff", type=Path, required=True)
    build.add_argument("--decision-source", type=Path, default=Path("rules/review-decisions.v1.json"))
    build.add_argument("--rules-root", type=Path, default=Path("rules"))
    build.add_argument("--output-root", type=Path, required=True)

    apply = commands.add_parser("apply", help="Validate and atomically apply accepted source review decisions.")
    apply.add_argument("--bundle", type=Path, required=True)
    apply.add_argument("--decisions", type=Path, required=True)
    apply.add_argument("--decision-source", type=Path, default=Path("rules/review-decisions.v1.json"))
    apply.add_argument("--rules-root", type=Path, default=Path("rules"))
    apply.add_argument("--accepted-corpus", type=Path, default=Path("data/accepted-corpus.v1.json"))

    verify = commands.add_parser("verify", help="Verify committed decisions and corpus against a rebuilt bundle.")
    verify.add_argument("--bundle", type=Path, required=True)
    verify.add_argument("--decision-source", type=Path, default=Path("rules/review-decisions.v1.json"))
    verify.add_argument("--rules-root", type=Path, default=Path("rules"))
    verify.add_argument("--accepted-corpus", type=Path, default=Path("data/accepted-corpus.v1.json"))
    verify.add_argument("--output", type=Path)

    namespace = parser.parse_args(argv)
    expected_decision_source = (namespace.rules_root / "review-decisions.v1.json").resolve()
    if namespace.decision_source.resolve() != expected_decision_source:
        parser.error("--decision-source must be <rules-root>/review-decisions.v1.json")
    if namespace.command == "build":
        return BuildArgs(
            normalized_root=namespace.normalized_root,
            support_diff=namespace.support_diff,
            decision_source=namespace.decision_source,
            rules_root=namespace.rules_root,
            output_root=namespace.output_root,
        )
    if namespace.command == "apply":
        return ApplyArgs(
            bundle=namespace.bundle,
            decisions=namespace.decisions,
            decision_source=namespace.decision_source,
            rules_root=namespace.rules_root,
            accepted_corpus=namespace.accepted_corpus,
        )
    return VerifyArgs(
        bundle=namespace.bundle,
        decision_source=namespace.decision_source,
        rules_root=namespace.rules_root,
        accepted_corpus=namespace.accepted_corpus,
        output=namespace.output,
    )


def _read_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReviewDecisionError(f"JSON 根必须是 object: {path}")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
