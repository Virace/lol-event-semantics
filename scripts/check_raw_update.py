"""检查本轮 raw 事件名是否有新增差集。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.raw_update import RawUpdateMetadata, check_raw_update


def main(argv: list[str] | None = None) -> int:
    """运行 raw 差集检查。"""

    args = parse_args(argv)
    result = check_raw_update(
        args.raw_root,
        args.input_root,
        state_file=args.state_file,
        metadata=metadata_from_args(args),
    )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Check incoming raw event names against cumulative raw/events.")
    parser.add_argument("--raw-root", type=Path, required=True, help="Cumulative raw/events root.")
    parser.add_argument("--input-root", type=Path, required=True, help="Incoming round event txt root.")
    parser.add_argument("--state-file", type=Path, help="Optional raw/update-state.json path for metadata checks.")
    parser.add_argument("--game-version", help="Source GAME version label.")
    parser.add_argument("--manifest-major-version", help="GAME manifest major version.")
    parser.add_argument("--game-manifest-id", help="GAME manifest id.")
    parser.add_argument("--game-manifest-url", help="GAME manifest URL.")
    parser.add_argument("--data-dragon-version", help="Data Dragon version.")
    parser.add_argument("--game-data-snapshot-metadata-sha256", help="GAME data snapshot metadata hash.")
    parser.add_argument("--data-dragon-champion-summary-sha256", help="Data Dragon champion summary hash.")
    return parser.parse_args(argv)


def metadata_from_args(args: argparse.Namespace) -> RawUpdateMetadata | None:
    """从 CLI 参数构造可选来源 metadata。"""

    values = {
        "game_version": args.game_version,
        "manifest_major_version": args.manifest_major_version,
        "game_manifest_id": args.game_manifest_id,
        "game_manifest_url": args.game_manifest_url,
        "data_dragon_version": args.data_dragon_version,
        "game_data_snapshot_metadata_sha256": args.game_data_snapshot_metadata_sha256,
        "data_dragon_champion_summary_sha256": args.data_dragon_champion_summary_sha256,
    }

    if not any(value is not None for value in values.values()):
        return None

    return RawUpdateMetadata(**values)


if __name__ == "__main__":
    raise SystemExit(main())
