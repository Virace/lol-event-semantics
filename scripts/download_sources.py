"""数据源一键下载（仓库初始化/更新入口）。

按 SPEC（2026-07-20 数据源体系）落地四源：

- ``stringtable``：游戏内字符串表（S1，zh_CN+en_US，GAME WAD 局部提取）
- ``lobby``：大厅 JSON maps/queues/skinlines/skins（S2，来源为
  lol-game-data-snapshot 快照仓库——约 6 小时更新一次，metadata.json 带
  gameVersion/manifest 对照信息；不走 WAD 解包）
- ``raw-bins``：英雄皮肤 bin（S6，事件名明文来源，GAME WAD 局部提取）
- ``rst-hashes``：CommunityDragon RST key 字典（S4，下载后按本地表瘦身）

stringtable/raw-bins 支持两种获取模式：默认**远程**（riotmanifest 按 live
manifest 局部下载，全量偏慢待优化）；提供 ``--game-dir``（本地游戏安装
根目录，如 WeGame 的 ``英雄联盟``）时走**本地解包**（league-tools 直读
``Game/DATA/FINAL`` 下的 WAD，远快于下载且不需要联网/riotmanifest；
版本取 ``Game/content-metadata.json``）。

用法示例：
    uv run python scripts/download_sources.py --sources stringtable,lobby,rst-hashes
    uv run python scripts/download_sources.py --sources raw-bins --champions Ahri,Ashe
    uv run python scripts/download_sources.py --game-dir "D:/Games/Tencent/WeGameApps/英雄联盟" --sources stringtable,raw-bins --champions all --emit-raw-round .temp/raw-events-round

游标（manifest.json）记录各源版本；同版本重跑自动跳过（--force 覆盖）。
手工放置文件遵循同一目录 layout 即被识别（游标缺失仅告警）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from lol_event_semantics.http_fetch import fetch_bytes
from lol_event_semantics.source_sync import (
    RST_HASHES_URL,
    champion_aliases,
    champion_numeric_id,
    copy_lobby_from_clone,
    ensure_snapshot_clone,
    trim_rst_hashes,
    verify_corpus,
)
from lol_event_semantics.wad_source import (
    DEFAULT_REGION,
    download_champion_bins,
    download_stringtables,
    extract_event_names,
    local_champion_bins,
    local_game_version,
    local_stringtables,
    main_version,
    open_extractor,
    resolve_pair,
)

ALL_SOURCES = ("stringtable", "lobby", "raw-bins", "rst-hashes")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="按 manifest 一键下载数据源。")
    parser.add_argument("--sources", default=",".join(ALL_SOURCES), help="逗号分隔的源子集")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--game-dir",
        type=Path,
        default=None,
        help="本地游戏安装根目录（含 Game/DATA/FINAL）；提供后 stringtable/raw-bins 走本地解包",
    )
    parser.add_argument("--locales", default="zh_CN,en_US", help="逗号分隔语言")
    parser.add_argument("--output-root", type=Path, default=Path("dist/sources"))
    parser.add_argument("--reference-root", type=Path, default=Path("reference"))
    parser.add_argument("--champions", default="all", help="raw-bins 的英雄集：all 或逗号分隔别名")
    parser.add_argument("--force", action="store_true", help="忽略游标强制重下")
    parser.add_argument(
        "--verify-corpus",
        action="store_true",
        help="raw-bins 验证模式：提取事件名与 raw/events 既有语料比对，不写语料",
    )
    parser.add_argument(
        "--emit-raw-round",
        type=Path,
        default=None,
        help="raw-bins 附加产出：把提取的事件名按轮次布局（champions/{id}.txt）写入该目录，"
        "供 update_raw_events --raw-input-root 消费",
    )
    return parser.parse_args(argv)


def load_cursor(path: Path) -> dict:
    """读取版本游标；缺失返回空。"""

    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def main(argv: list[str] | None = None) -> int:
    """入口：解析 manifest 对并按选定源下载。"""

    sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args(argv)
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    unknown = set(sources) - set(ALL_SOURCES)
    if unknown:
        print(f"未知源：{sorted(unknown)}（可用：{ALL_SOURCES}）")
        return 2
    locales = tuple(l.strip() for l in args.locales.split(",") if l.strip())

    root = args.output_root
    root.mkdir(parents=True, exist_ok=True)
    cursor_path = root / "manifest.json"
    cursor = load_cursor(cursor_path)

    # 双模式：本地模式不做任何 manifest 网络解析；远程模式按需惰性解析。
    pair = None
    if args.game_dir is not None:
        base_game_version = local_game_version(args.game_dir)
        print(f"本地模式：{args.game_dir}（版本 {base_game_version or '未知'}）")
    else:
        pair = resolve_pair(args.region)
        base_game_version = pair.game_version
        print(f"manifest 对：game={pair.game_version} lcu={pair.lcu_version} region={pair.region}")
    same_version = (
        base_game_version is not None and cursor.get("game_version") == base_game_version
    )

    work_dir = root / "work"
    game_extractor = None
    done: dict[str, str] = dict(cursor.get("sources", {}))

    for source in sources:
        # raw-bins 按英雄集请求不吃版本跳过；lobby 幂等判据是快照 generatedAt（分支内判断）。
        if (
            same_version
            and not args.force
            and done.get(source) == "ok"
            and source not in ("raw-bins", "lobby")
        ):
            print(f"[skip] {source}：版本未变（--force 覆盖）")
            continue

        if source == "stringtable":
            if args.game_dir is not None:
                results = local_stringtables(args.game_dir, locales, root / "stringtable")
            else:
                game_extractor = game_extractor or open_extractor(pair.game_url, work_dir)
                results = download_stringtables(game_extractor, locales, root / "stringtable")
            for locale, path in results.items():
                print(f"[ok] stringtable {locale} -> {path} ({path.stat().st_size} bytes)")
            done[source] = "ok"

        elif source == "lobby":
            clone = ensure_snapshot_clone(root)
            meta = json.loads((clone / "metadata.json").read_text(encoding="utf-8"))
            previous = cursor.get("lobby_snapshot") or {}
            if (
                not args.force
                and done.get("lobby") == "ok"
                and previous.get("generated_at") == meta.get("generatedAt")
            ):
                print(f"[skip] lobby：快照未更新（{meta.get('generatedAt')}，--force 覆盖）")
                continue
            results = copy_lobby_from_clone(clone, meta, locales, root / "lobby")
            snapshot_version = str(meta.get("gameVersion", ""))
            cursor["lobby_snapshot"] = {
                "game_version": snapshot_version,
                "generated_at": meta.get("generatedAt"),
                "lcu_manifest_id": meta.get("lcuManifestId"),
            }
            note = ""
            if (
                snapshot_version
                and base_game_version
                and main_version(snapshot_version) != main_version(base_game_version)
            ):
                note = f"（警告：快照 {snapshot_version} 与游戏 {base_game_version} 主版本不一致，快照可能滞后）"
            print(f"[ok] lobby JSON {len(results)} 个文件（快照 {snapshot_version}, {meta.get('generatedAt')}）{note}")
            done[source] = "ok"

        elif source == "rst-hashes":
            if not any((root / "stringtable").glob("Global.*-lol.stringtable")):
                print("[err] rst-hashes 需先有 stringtable（瘦身依赖本地表交集）")
                return 1
            raw_dict = fetch_bytes(RST_HASHES_URL)
            total, kept = trim_rst_hashes(
                raw_dict, root / "stringtable", root / "rst-hashes" / "hashes.rst.xxh3.trimmed.txt"
            )
            print(f"[ok] rst-hashes 字典 {total} 行 → 瘦身 {kept} 行")
            done[source] = "ok"

        elif source == "raw-bins":
            if args.game_dir is None:
                game_extractor = game_extractor or open_extractor(pair.game_url, work_dir)
            champs = (
                champion_aliases(args.reference_root)
                if args.champions == "all"
                else [c.strip() for c in args.champions.split(",") if c.strip()]
            )
            out_root = root / "raw-bins" / "champions"
            for champ in champs:
                if args.game_dir is not None:
                    bins = local_champion_bins(args.game_dir, champ, out_root)
                else:
                    bins = download_champion_bins(game_extractor, champ, out_root)
                line = f"[ok] raw-bins {champ}: {len(bins)} 个 skin bin"
                numeric = champion_numeric_id(args.reference_root, champ)
                if args.verify_corpus:
                    corpus = Path("raw/events/champions") / f"{numeric}.txt"
                    line += f" | verify({numeric}): {verify_corpus(bins, corpus)}"
                if args.emit_raw_round is not None:
                    if numeric is None:
                        line += " | round: 跳过（无数字 id）"
                    else:
                        names: dict[str, None] = {}
                        for path in bins:
                            for event in extract_event_names(path.read_bytes()):
                                names.setdefault(event, None)
                        if not names:
                            # 皮肤 bin 无音频事件的条目（如模式变体）不产出空轮次文件。
                            line += " | round: 跳过（无事件）"
                        else:
                            target = args.emit_raw_round / "champions" / f"{numeric}.txt"
                            target.parent.mkdir(parents=True, exist_ok=True)
                            # newline="\n"：Windows 下防 write_text 把 LF 转 CRLF。
                            target.write_text(
                                "\n".join(sorted(names)) + "\n", encoding="utf-8", newline="\n"
                            )
                            line += f" | round: {target}（{len(names)} 条）"
                print(line)
            done[source] = "ok"

    cursor_path.write_text(
        json.dumps(
            {
                "mode": "local" if args.game_dir is not None else "remote",
                "game_dir": str(args.game_dir) if args.game_dir is not None else None,
                "region": pair.region if pair is not None else None,
                "game_version": base_game_version,
                "lcu_version": pair.lcu_version if pair is not None else None,
                "downloaded_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "rst_hashes_url": RST_HASHES_URL,
                "lobby_snapshot": cursor.get("lobby_snapshot"),
                "sources": done,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"游标已写入 {cursor_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
