"""把本轮 raw 事件名差集合并进累计 raw/events。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.raw_update import RawUpdateMetadata, update_raw_events
from lol_event_semantics.update_pipeline import run_local_update_pipeline, run_online_update_pipeline


def main(argv: list[str] | None = None) -> int:
    """运行 raw 累计更新。"""

    args = parse_args(argv)

    if args.mode == "local":
        result = run_local_update_pipeline(
            reference_root=args.reference_root,
            raw_root=args.raw_root,
            state_file=args.state_file,
            game_data_root=args.game_data_root,
            data_dragon_root=args.data_dragon_root,
            raw_input_root=args.raw_input_root or args.input_root,
            game_version=args.game_version,
            data_dragon_version=args.data_dragon_version,
            report_root=args.report_root,
            languages=tuple(args.languages),
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.mode == "online":
        result = run_online_update_pipeline(
            reference_root=args.reference_root,
            raw_root=args.raw_root,
            state_file=args.state_file,
            raw_input_root=args.raw_input_root or args.input_root,
            game_version=args.game_version,
            data_dragon_version=args.data_dragon_version,
            report_root=args.report_root,
            cache_root=args.cache_root,
            languages=tuple(args.languages),
            game_data_base_url=args.game_data_base_url,
            data_dragon_base_url=args.data_dragon_base_url,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    metadata = RawUpdateMetadata(
        game_version=args.game_version,
        manifest_major_version=args.manifest_major_version,
        game_manifest_id=args.game_manifest_id,
        game_manifest_url=args.game_manifest_url,
        data_dragon_version=args.data_dragon_version,
        game_data_snapshot_metadata_sha256=args.game_data_snapshot_metadata_sha256,
        data_dragon_champion_summary_sha256=args.data_dragon_champion_summary_sha256,
    )
    result = update_raw_events(
        args.raw_root,
        args.raw_input_root or args.input_root,
        state_file=args.state_file,
        metadata=metadata,
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Merge incoming raw event names into cumulative raw/events.")
    parser.add_argument(
        "--mode",
        choices=["raw", "local", "online"],
        default="raw",
        help="Update mode. raw only merges event txt input; local/online also import reference data.",
    )
    parser.add_argument("--raw-root", type=Path, required=True, help="Cumulative raw/events root.")
    parser.add_argument("--input-root", type=Path, help="Incoming round event txt root.")
    parser.add_argument("--raw-input-root", type=Path, help="Incoming round event txt root for local/online mode.")
    parser.add_argument("--reference-root", type=Path, help="Internal reference collection root for local/online mode.")
    parser.add_argument("--game-data-root", type=Path, help="Local GAME data root for local mode.")
    parser.add_argument("--data-dragon-root", type=Path, help="Local Data Dragon root for local mode.")
    parser.add_argument("--report-root", type=Path, help="Update report root for local/online mode.")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".temp") / "reference-sources",
        help="Ignored cache root for online source subsets.",
    )
    parser.add_argument("--state-file", type=Path, help="Optional raw/update-state.json path.")
    parser.add_argument("--game-version", help="Source GAME version label.")
    parser.add_argument("--manifest-major-version", help="GAME manifest major version.")
    parser.add_argument("--game-manifest-id", help="GAME manifest id.")
    parser.add_argument("--game-manifest-url", help="GAME manifest URL.")
    parser.add_argument("--data-dragon-version", help="Data Dragon version.")
    parser.add_argument("--game-data-snapshot-metadata-sha256", help="GAME data snapshot metadata hash.")
    parser.add_argument("--data-dragon-champion-summary-sha256", help="Data Dragon champion summary hash.")
    parser.add_argument(
        "--game-data-base-url",
        default="https://raw.githubusercontent.com/Virace/lol-game-data-snapshot/main",
        help="Online GAME data snapshot root that contains global/{locale}/v1.",
    )
    parser.add_argument(
        "--data-dragon-base-url",
        default="https://ddragon.leagueoflegends.com",
        help="Data Dragon service base URL.",
    )
    parser.add_argument(
        "--languages",
        nargs="+",
        default=["en_US", "zh_CN"],
        choices=["en_US", "zh_CN"],
        help="Reference languages for local/online mode.",
    )
    namespace = parser.parse_args(argv)
    validate_args(parser, namespace)
    return namespace


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """校验 mode 对应的必填参数。"""

    raw_input_root = args.raw_input_root or args.input_root

    if raw_input_root is None:
        parser.error("--input-root or --raw-input-root is required")

    if args.mode == "raw":
        return

    required = {
        "--reference-root": args.reference_root,
        "--state-file": args.state_file,
        "--game-version": args.game_version,
        "--report-root": args.report_root,
    }
    if args.mode == "local":
        required.update(
            {
                "--game-data-root": args.game_data_root,
                "--data-dragon-root": args.data_dragon_root,
                "--data-dragon-version": args.data_dragon_version,
            }
        )
    missing = [name for name, value in required.items() if value is None]

    if missing:
        parser.error(f"{', '.join(missing)} required when --mode {args.mode}")


if __name__ == "__main__":
    raise SystemExit(main())
