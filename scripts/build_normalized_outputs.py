"""构建对外 normalized 输出。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.dataset_identity import build_input_identity
from lol_event_semantics.normalized_output import read_decomposition_root, write_normalized_outputs
from lol_event_semantics.pattern_catalog import load_pattern_policy


@dataclass(slots=True)
class BuildNormalizedArgs:
    """normalized 构建参数。"""

    decomposition_root: Path
    output_root: Path
    repo_root: Path
    raw_root: Path
    reference_version: str
    reference_root: Path
    rules_root: Path
    overrides_root: Path
    pattern_policy: Path
    contract_assets_root: Path
    sources_manifest: Path
    frozen_profile: Path
    report_root: Path | None


def main(argv: list[str] | None = None) -> int:
    """运行 normalized 输出构建。"""

    args = parse_args(argv)
    records = read_decomposition_root(args.decomposition_root)
    input_identity = build_input_identity(
        repo_root=args.repo_root,
        raw_root=args.raw_root,
        reference_root=args.reference_root,
        rules_root=args.rules_root,
        overrides_root=args.overrides_root,
        contract_assets_root=args.contract_assets_root,
        sources_manifest_path=args.sources_manifest,
        frozen_profile_path=args.frozen_profile,
    )
    write_normalized_outputs(
        records,
        args.output_root,
        reference_version=args.reference_version,
        reference_root=args.reference_root,
        rules_root=args.rules_root,
        overrides_root=args.overrides_root,
        input_identity=input_identity,
        pattern_policy=load_pattern_policy(args.pattern_policy),
        contract_assets_root=args.contract_assets_root,
    )

    print(f"records={len(records)} output={args.output_root}")
    return 0


def parse_args(argv: list[str] | None = None) -> BuildNormalizedArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Build consumer-facing normalized event semantic outputs.")
    parser.add_argument(
        "--decomposition-root",
        type=Path,
        required=True,
        help="Directory containing champions.jsonl and maps.jsonl from decompose_events.py.",
    )
    parser.add_argument("--output-root", type=Path, required=True, help="Output root, for example dist/normalized.")
    parser.add_argument("--repo-root", type=Path, required=True, help="Repository root used for generator identity.")
    parser.add_argument("--raw-root", type=Path, required=True, help="Raw event root used for frozen input identity.")
    parser.add_argument(
        "--reference-version",
        default="cumulative",
        help="Optional metadata label for the reference collection used by this run. Defaults to cumulative.",
    )
    parser.add_argument("--reference-root", type=Path, required=True, help="Explicit reference collection root.")
    parser.add_argument("--rules-root", type=Path, required=True, help="Explicit rules root.")
    parser.add_argument("--overrides-root", type=Path, required=True, help="Explicit overrides root.")
    parser.add_argument(
        "--pattern-policy",
        type=Path,
        default=Path("rules/translation_patterns.yaml"),
        help="Versioned translation pattern and promotion policy.",
    )
    parser.add_argument(
        "--contract-assets-root",
        type=Path,
        default=Path("data"),
        help="Root containing committed schema/ and examples/ contract assets.",
    )
    parser.add_argument("--sources-manifest", type=Path, required=True, help="Frozen source acquisition manifest JSON.")
    parser.add_argument("--frozen-profile", type=Path, required=True, help="Committed frozen release profile JSON.")
    parser.add_argument(
        "--report-root",
        type=Path,
        help="Reserved report root for future normalized QA reports. Currently unused.",
    )
    namespace = parser.parse_args(argv)

    return BuildNormalizedArgs(
        decomposition_root=namespace.decomposition_root,
        output_root=namespace.output_root,
        repo_root=namespace.repo_root,
        raw_root=namespace.raw_root,
        reference_version=namespace.reference_version,
        reference_root=namespace.reference_root,
        rules_root=namespace.rules_root,
        overrides_root=namespace.overrides_root,
        pattern_policy=namespace.pattern_policy,
        contract_assets_root=namespace.contract_assets_root,
        sources_manifest=namespace.sources_manifest,
        frozen_profile=namespace.frozen_profile,
        report_root=namespace.report_root,
    )


if __name__ == "__main__":
    raise SystemExit(main())
