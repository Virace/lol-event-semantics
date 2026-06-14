"""从 WAD 或 CommunityDragon 准备 rcp-be-lol-game-data JSON。"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from league_tools import WAD

COMMUNITYDRAGON_BASE_URL = "https://raw.communitydragon.org/latest"
PLUGIN_PREFIX = "plugins/rcp-be-lol-game-data"
CATALOG_FILES = [
    "champion-summary.json",
    "maps.json",
    "items.json",
    "summoner-spells.json",
    "universes.json",
    "skinlines.json",
    "queues.json",
    "game-mode-mutators.json",
]


@dataclass(slots=True)
class ExtractArgs:
    """game-data JSON 提取参数。"""

    output_root: Path
    wad_paths: list[Path] = field(default_factory=list)
    locales: list[str] = field(default_factory=lambda: ["default"])
    download_missing: bool = False
    base_url: str = COMMUNITYDRAGON_BASE_URL


def main(argv: list[str] | None = None) -> int:
    """运行 JSON 准备流程。"""

    args = parse_args(argv)
    paths = catalog_paths(args.locales)
    extracted = extract_paths_from_wads(args.wad_paths, paths, args.output_root)
    downloaded = download_missing_paths(paths, extracted, args) if args.download_missing else set()
    summary_paths = sorted(extracted.union(downloaded))

    for locale in args.locales:
        summary = read_extracted_json(args.output_root, game_data_path(locale, "champion-summary.json"))

        if not isinstance(summary, list):
            continue

        champion_paths = champion_detail_paths(summary, locale)
        extracted.update(extract_paths_from_wads(args.wad_paths, champion_paths, args.output_root))

        if args.download_missing:
            downloaded.update(download_missing_paths(champion_paths, extracted, args))

    total = len(extracted.union(downloaded))
    print(f"output_root={args.output_root}")
    print(f"catalog_paths={len(paths)} summaries={len(summary_paths)} total_files={total}")
    return 0


def parse_args(argv: list[str] | None = None) -> ExtractArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Extract rcp-be-lol-game-data JSON files from WAD archives.")
    parser.add_argument("--output-root", type=Path, required=True, help="Directory receiving plugin-layout JSON files.")
    parser.add_argument("--wad", type=Path, action="append", default=[], help="WAD file path; can be passed multiple times.")
    parser.add_argument("--locale", action="append", default=None, help="Locale under global/<locale>/v1; default: default.")
    parser.add_argument("--download-missing", action="store_true", help="Download missing JSON files from CommunityDragon.")
    parser.add_argument("--base-url", default=COMMUNITYDRAGON_BASE_URL, help="CommunityDragon raw base URL.")
    namespace = parser.parse_args(argv)

    return ExtractArgs(
        output_root=namespace.output_root,
        wad_paths=namespace.wad,
        locales=namespace.locale or ["default"],
        download_missing=namespace.download_missing,
        base_url=namespace.base_url.rstrip("/"),
    )


def catalog_paths(locales: list[str]) -> list[str]:
    """生成基础目录 JSON 的 WAD/CommunityDragon 相对路径。"""

    return [
        game_data_path(locale, file_name)
        for locale in locales
        for file_name in CATALOG_FILES
    ]


def champion_detail_paths(rows: list[dict[str, Any]], locale: str) -> list[str]:
    """根据 champion-summary 行生成英雄详情路径。"""

    paths: list[str] = []

    for row in rows:
        champion_id = row.get("id")

        if isinstance(champion_id, str) and champion_id.isdigit():
            champion_id = int(champion_id)

        if not isinstance(champion_id, int) or champion_id < 0:
            continue

        paths.append(game_data_path(locale, f"champions/{champion_id}.json"))

    return paths


def game_data_path(locale: str, file_name: str) -> str:
    """生成插件内 game-data JSON 相对路径。"""

    return f"{PLUGIN_PREFIX}/global/{locale}/v1/{file_name}"


def communitydragon_url(relative_path: str, base_url: str = COMMUNITYDRAGON_BASE_URL) -> str:
    """生成 CommunityDragon raw URL。"""

    return f"{base_url.rstrip('/')}/{relative_path.lstrip('/')}"


def extract_paths_from_wads(
    wad_paths: list[Path],
    relative_paths: list[str],
    output_root: Path,
) -> set[str]:
    """从多个 WAD 中按已知路径提取文件。"""

    extracted: set[str] = set()

    for wad_path in wad_paths:
        if not wad_path.is_file():
            continue

        wad = WAD(wad_path)

        for relative_path in relative_paths:
            if relative_path in extracted:
                continue

            try:
                results = wad.extract([relative_path], out_dir=output_root)
            except (KeyError, FileNotFoundError, ValueError):
                continue

            if any(result is not None for result in results):
                extracted.add(relative_path)

    return extracted


def download_missing_paths(
    relative_paths: list[str],
    existing: set[str],
    args: ExtractArgs,
) -> set[str]:
    """从 CommunityDragon 下载 WAD 未命中的 JSON。"""

    downloaded: set[str] = set()

    for relative_path in relative_paths:
        if relative_path in existing:
            continue

        target = args.output_root / relative_path
        url = communitydragon_url(relative_path, args.base_url)

        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                content = response.read()
        except (urllib.error.URLError, TimeoutError):
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        downloaded.add(relative_path)

    return downloaded


def read_extracted_json(output_root: Path, relative_path: str) -> Any:
    """读取已提取或已下载的 JSON。"""

    path = output_root / relative_path

    if not path.is_file():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    raise SystemExit(main())
