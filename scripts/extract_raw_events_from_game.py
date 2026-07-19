"""从本地 GAME WAD/BIN 抽取 raw event round 输入。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from lol_event_semantics.game_manifest_wads import ManifestBinRequest, extract_round_from_game_manifest
from lol_event_semantics.game_wad_events import extract_round_from_event_files, extract_round_from_wads


def main(argv: list[str] | None = None) -> int:
    """运行 raw event round 抽取。"""

    args = parse_args(argv)
    if args.mode == "event-files":
        result = extract_round_from_event_files(args.input_root, args.output_root)
    elif args.mode == "wads":
        result = extract_round_from_wads(
            output_root=args.output_root,
            requests=load_local_requests(args.request_file),
            wad_paths=args.wad,
        )
    else:
        result = extract_round_from_game_manifest(
            manifest_url=args.game_manifest_url,
            output_root=args.output_root,
            cache_root=args.cache_root,
            requests=load_manifest_requests(args.request_file),
        )

    print(json.dumps(result_to_dict(result), ensure_ascii=False, indent=2))
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Extract raw event round txt files from local GAME WAD/BIN sources.")
    parser.add_argument(
        "--mode",
        choices=["wads", "event-files", "manifest"],
        default="wads",
        help="Extraction input mode. wads reads local WAD/BIN; manifest uses remote GAME manifest; event-files normalizes JSON payloads.",
    )
    parser.add_argument("--output-root", type=Path, required=True, help="Round txt output root.")
    parser.add_argument("--request-file", type=Path, help="JSON request file for WAD/BIN extraction.")
    parser.add_argument("--wad", type=Path, action="append", default=[], help="Local WAD file path; can be repeated.")
    parser.add_argument("--input-root", type=Path, help="Existing event payload root for event-files mode.")
    parser.add_argument("--game-manifest-url", help="Remote GAME manifest URL for manifest mode.")
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path(".temp") / "game-manifest",
        help="Ignored cache root for manifest mode.",
    )
    namespace = parser.parse_args(argv)
    validate_args(parser, namespace)
    return namespace


def validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """校验抽取模式对应的必填参数。"""

    if args.mode == "event-files":
        if args.input_root is None:
            parser.error("--input-root required when --mode event-files")
        return

    if args.mode == "manifest":
        missing = []
        if args.request_file is None:
            missing.append("--request-file")
        if args.game_manifest_url is None:
            missing.append("--game-manifest-url")

        if missing:
            parser.error(f"{', '.join(missing)} required when --mode manifest")
        return

    missing = []
    if args.request_file is None:
        missing.append("--request-file")
    if not args.wad:
        missing.append("--wad")

    if missing:
        parser.error(f"{', '.join(missing)} required when --mode wads")


def load_local_requests(path: Path) -> dict[tuple[str, str], list[str]]:
    """读取 WAD/BIN 抽取请求表。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Request file must be a JSON object")

    requests: dict[tuple[str, str], list[str]] = {}
    for group, rows in payload.items():
        if group not in {"champions", "maps"} or not isinstance(rows, dict):
            continue

        for entity, bin_paths in rows.items():
            if not isinstance(entity, str) or not isinstance(bin_paths, list):
                continue

            requests[(group, entity)] = [str(bin_path) for bin_path in bin_paths if isinstance(bin_path, str)]

    return requests


def load_manifest_requests(path: Path) -> tuple[ManifestBinRequest, ...]:
    """读取远端 GAME manifest WAD/BIN 抽取请求表。"""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Request file must be a JSON object")

    requests: list[ManifestBinRequest] = []
    for group, rows in payload.items():
        if group not in {"champions", "maps"} or not isinstance(rows, dict):
            continue

        for entity, row in rows.items():
            if not isinstance(entity, str) or not isinstance(row, dict):
                continue

            wad_path = row.get("wad")
            bin_paths = row.get("bins")
            if not isinstance(wad_path, str) or not isinstance(bin_paths, list):
                continue

            requests.append(
                ManifestBinRequest(
                    group=group,
                    entity=entity,
                    wad_path=wad_path,
                    bin_paths=tuple(str(bin_path) for bin_path in bin_paths if isinstance(bin_path, str)),
                )
            )

    return tuple(requests)


def result_to_dict(result) -> dict[str, object]:
    """把抽取结果转为 JSON 友好结构。"""

    return {
        "files": result.files,
        "events": result.events,
        "warnings": [{"kind": warning.kind, "path": warning.path} for warning in result.warnings],
    }


if __name__ == "__main__":
    raise SystemExit(main())
