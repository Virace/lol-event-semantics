"""构建或规范化 workflow 本轮使用的可移植来源清单。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.dataset_identity import load_frozen_profile
from lol_event_semantics.source_manifest import (
    build_manifest_source,
    load_source_manifest,
    normalize_portable_source_manifest,
    validate_portable_source_manifest,
    write_source_manifest,
)


def main(argv: list[str] | None = None) -> int:
    """构建或读取来源清单，验证后写出规范副本。"""

    args = parse_args(argv)
    manifest = (
        normalize_portable_source_manifest(load_source_manifest(args.input))
        if args.input is not None
        else build_manifest_source(
            game_version=args.game_version,
            game_manifest_id=args.game_manifest_id,
            lcu_version=args.lcu_version,
            lcu_manifest_id=args.lcu_manifest_id,
            region=args.region,
        )
    )
    evidence = validate_portable_source_manifest(manifest, load_frozen_profile(args.frozen_profile))
    write_source_manifest(args.output, manifest)
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析 build/input 两种来源模式。"""

    parser = argparse.ArgumentParser(description="Prepare a portable source manifest for one update run.")
    parser.add_argument("--input", type=Path, help="Existing portable source manifest from the producer run.")
    parser.add_argument("--game-version", help="GAME version for manifest acquisition mode.")
    parser.add_argument("--game-manifest-id", help="GAME manifest id used for raw extraction.")
    parser.add_argument("--lcu-version", help="Matching LCU version for manifest acquisition mode.")
    parser.add_argument("--lcu-manifest-id", help="Lobby snapshot LCU manifest id.")
    parser.add_argument("--region", help="Optional acquisition region.")
    parser.add_argument("--frozen-profile", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.input is not None:
        conflicting = [
            args.game_version,
            args.game_manifest_id,
            args.lcu_version,
            args.lcu_manifest_id,
            args.region,
        ]
        if any(value is not None for value in conflicting):
            parser.error("--input cannot be combined with manifest acquisition fields")
        return args

    missing = [
        name
        for name, value in (
            ("--game-version", args.game_version),
            ("--game-manifest-id", args.game_manifest_id),
            ("--lcu-version", args.lcu_version),
            ("--lcu-manifest-id", args.lcu_manifest_id),
        )
        if value is None
    ]
    if missing:
        parser.error(f"{', '.join(missing)} required when --input is omitted")
    return args


if __name__ == "__main__":
    raise SystemExit(main())
