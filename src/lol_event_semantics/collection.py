"""按同轮来源采集 raw，复用已校验的实体结果并保留中断进度。"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any

from .advisory_lock import advisory_file_lock
from .collection_discovery import CollectionTarget, discover_targets
from .collection_sources import prepare_catalog
from .raw_events import is_excluded_event_name
from .source_manifest import build_manifest_source
from .wad_source import DEFAULT_REGION, WadSourceError, extract_event_names, open_extractor, resolve_pair

SCHEMA_VERSION = 1
EXTRACTION_VERSION = "bank-unit-events.v1"


def collect_live(
    output_root: Path,
    *,
    region: str = DEFAULT_REGION,
    champions: tuple[str, ...] | None = None,
    maps: tuple[str, ...] | None = None,
    progress: Callable[[str], None] = print,
) -> Path:
    """采集当前 live 来源，返回可独立审查的轮次目录。

    只写采集目录，不合并累计语料，不修改冻结档案或批准状态。
    同一输出根的执行互斥；相同来源、请求与解析器复用同一轮次。
    """

    output_root.mkdir(parents=True, exist_ok=True)
    with advisory_file_lock(output_root / ".collection.lock"):
        cache_root = output_root / "cache"
        progress("解析当前 GAME/LCU 来源")
        pair = resolve_pair(region, work_dir=cache_root / "resolver")
        progress(f"来源版本：GAME {pair.game_version} / LCU {pair.lcu_version}")
        catalog = prepare_catalog(cache_root, pair)
        extractor = open_extractor(pair.game_url, cache_root / "game" / pair.game_id)
        progress("根据来源目录与实际 WAD 索引发现英雄、皮肤和地图 BIN")
        targets = discover_targets(extractor, catalog, champions=champions, maps=maps)
        source = build_manifest_source(
            game_version=pair.game_version,
            game_manifest_id=pair.game_id,
            lcu_version=pair.lcu_version,
            lcu_manifest_id=pair.lcu_id,
            region=pair.region,
        )
        source["catalog"] = {
            "revision": catalog.name,
            "metadata_sha256": file_digest(catalog / "metadata.json"),
        }
        source["collection_scope"] = {
            "champions": "all" if champions is None else list(champions),
            "maps": "all" if maps is None else list(maps),
        }
        run_root = collect_targets(extractor, targets, source, output_root, progress=progress)
        write_json(output_root / "latest.json", {"schema_version": SCHEMA_VERSION, "run": run_root.name})
        return run_root


def collect_targets(
    extractor: Any,
    targets: Sequence[CollectionTarget],
    source: Mapping[str, Any],
    output_root: Path,
    *,
    progress: Callable[[str], None] = print,
) -> Path:
    """采集请求集合；每个实体完成后固化摘要，失败不会声明来源完成。

    调用方负责同一输出根的互斥。缓存以 WAD 内容、BIN 清单与解析器身份
    为键，跨轮复用前仍校验内容摘要；缺失或损坏的缓存重新采集。
    """

    if not targets:
        raise ValueError("采集范围没有有效目标")
    parser_digest = extraction_digest()
    rows = []
    seen: set[str] = set()
    for target in targets:
        if target.group not in {"champions", "maps"} or not target.entity.isdecimal():
            raise ValueError("采集目标必须使用 champions/maps 与数字实体 ID")
        if target.group == "maps" and target.entity == "22":
            raise ValueError("TFT 地图不能进入采集目标")
        key = f"{target.group}/{target.entity}"
        if key in seen or not target.bin_paths:
            raise ValueError(f"采集目标重复或没有 BIN：{key}")
        seen.add(key)
        row = asdict(target)
        row["wad_digest"] = extractor.manifest.files[target.wad_path].hexdigest()
        row["cache_key"] = digest_json({"target": row, "parser": parser_digest})
        rows.append(row)
    plan = {"schema_version": SCHEMA_VERSION, "source": dict(source), "parser_digest": parser_digest, "targets": rows}
    run_id = digest_json(plan)[:24]
    run_root = output_root / "rounds" / run_id
    run_root.mkdir(parents=True, exist_ok=True)
    state_path = run_root / "collection-report.json"
    previous = read_object(state_path)
    completed = previous.get("completed", {})
    completed = completed if isinstance(completed, dict) else {}
    if digest_json(read_object(run_root / "collection-plan.json")) == digest_json(plan) and completed_run_is_valid(
        run_root, previous, rows, source
    ):
        progress(f"已完成轮次校验通过，复用 {len(rows)} 个实体：{run_id}")
        return run_root
    write_json(run_root / "collection-plan.json", plan)
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "status": "running",
        "target_count": len(rows),
        "completed": {},
        "extraction_version": EXTRACTION_VERSION,
    }
    pending_source = dict(source)
    pending_source["sources"] = {"raw-bins": "pending"}
    write_json(run_root / "source-manifest.json", pending_source)
    write_json(state_path, report)
    current_key = ""
    try:
        for index, (target, row) in enumerate(zip(targets, rows, strict=True), 1):
            current_key = f"{target.group}/{target.entity}"
            raw_path = run_root / "raw" / f"{current_key}.txt"
            entry = completed.get(current_key)
            payload = verified_payload(raw_path, entry, row["cache_key"], len(target.bin_paths))
            origin = "resume"
            cache_dir = output_root / "cache" / "entities"
            cache_path = cache_dir / f"{row['cache_key']}.txt"
            cache_meta = cache_dir / f"{row['cache_key']}.json"
            if payload is None:
                payload = verified_payload(cache_path, read_object(cache_meta), row["cache_key"], len(target.bin_paths))
                origin = "cache"
            if payload is None:
                events, filtered = extract_target(extractor, target)
                payload = ("\n".join(events) + ("\n" if events else "")).encode("utf-8")
                entry = {
                    "cache_key": row["cache_key"],
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "events": len(events),
                    "excluded_tft_events": filtered,
                    "bins": len(target.bin_paths),
                }
                write_bytes(cache_path, payload)
                write_json(cache_meta, entry)
                origin = "download"
            elif origin == "cache":
                entry = read_object(cache_meta)
            if not isinstance(entry, dict):
                raise ValueError(f"采集结果缺少完整元数据：{current_key}")
            write_bytes(raw_path, payload)
            report["completed"][current_key] = {**entry, "origin": origin}
            write_json(state_path, report)
            progress(f"[{index}/{len(rows)}] {current_key} ({target.alias}): {entry['events']} events [{origin}]")
        report["status"] = "completed"
        report["event_count"] = sum(item["events"] for item in report["completed"].values())
        report["bin_count"] = sum(item["bins"] for item in report["completed"].values())
        report["excluded_tft_events"] = sum(item["excluded_tft_events"] for item in report["completed"].values())
        report["raw_files"] = {f"{key}.txt": value["sha256"] for key, value in report["completed"].items()}
        write_json(state_path, report)
        final_source = dict(source)
        final_source["sources"] = {"raw-bins": "ok"}
        final_source["collection"] = {"run_id": run_id, "raw_files": report["raw_files"]}
        write_json(run_root / "source-manifest.json", final_source)
        return run_root
    except BaseException as exc:
        report["status"] = "failed"
        report["failed_target"] = current_key
        report["error_type"] = type(exc).__name__
        write_json(state_path, report)
        raise


def completed_run_is_valid(
    run_root: Path, report: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], source: Mapping[str, Any]
) -> bool:
    """只读核验完成轮次，避免正常重跑或核验中断使已完成结果降级。"""

    if report.get("status") != "completed" or report.get("run_id") != run_root.name:
        return False
    completed = report.get("completed")
    if not isinstance(completed, dict) or len(completed) != len(rows):
        return False
    raw_files: dict[str, str] = {}
    totals = {"event_count": 0, "bin_count": 0, "excluded_tft_events": 0}
    for row in rows:
        key = f"{row['group']}/{row['entity']}"
        entry = completed.get(key)
        if verified_payload(run_root / "raw" / f"{key}.txt", entry, row["cache_key"], len(row["bin_paths"])) is None:
            return False
        if not isinstance(entry, dict):
            return False
        raw_files[f"{key}.txt"] = entry["sha256"]
        totals["event_count"] += entry["events"]
        totals["bin_count"] += entry["bins"]
        totals["excluded_tft_events"] += entry["excluded_tft_events"]
    if report.get("target_count") != len(rows) or report.get("raw_files") != raw_files:
        return False
    if any(report.get(key) != value for key, value in totals.items()):
        return False
    expected_source = dict(source)
    expected_source["sources"] = {"raw-bins": "ok"}
    expected_source["collection"] = {"run_id": run_root.name, "raw_files": raw_files}
    return read_object(run_root / "source-manifest.json") == expected_source


def extract_target(extractor: Any, target: CollectionTarget) -> tuple[list[str], int]:
    """分批提取已由 WAD 索引确认存在的 BIN；缺失任何请求即失败。"""

    events: set[str] = set()
    excluded: set[str] = set()
    for offset in range(0, len(target.bin_paths), 100):
        paths = target.bin_paths[offset : offset + 100]
        blobs = extractor.extract_files({target.wad_path: list(paths)}).get(target.wad_path, {})
        for path in paths:
            payload = blobs.get(path)
            if not isinstance(payload, bytes):
                raise ValueError(f"WAD 索引中的 BIN 未成功提取：{target.wad_path} :: {path}")
            try:
                names = extract_event_names(payload, strict=True)
            except WadSourceError as exc:
                raise WadSourceError(f"事件提取失败：{target.wad_path} :: {path}：{exc}") from exc
            for event in names:
                if not event or "\n" in event or "\r" in event:
                    raise ValueError(f"BIN 含无法无损写入逐行语料的事件名：{path}")
                (excluded if is_excluded_event_name(event) else events).add(event)
    return sorted(events), len(excluded)


def verified_payload(path: Path, entry: object, cache_key: str, bin_count: int) -> bytes | None:
    """只复用身份及内容摘要均匹配的采集结果。"""

    if not isinstance(entry, dict) or entry.get("cache_key") != cache_key or not path.is_file():
        return None
    if any(
        type(entry.get(field)) is not int or entry[field] < 0 for field in ("events", "bins", "excluded_tft_events")
    ):
        return None
    if entry["bins"] != bin_count:
        return None
    payload = path.read_bytes()
    if hashlib.sha256(payload).hexdigest() != entry.get("sha256"):
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if text and not text.endswith("\n"):
        return None
    events = text[:-1].split("\n") if text else []
    if len(events) != entry["events"] or events != sorted(set(events)):
        return None
    if any(not event or "\r" in event or is_excluded_event_name(event) for event in events):
        return None
    return payload


def extraction_digest() -> str:
    """把影响原名抽取和过滤的源码纳入缓存身份，防止升级后复用旧结果。"""

    root = Path(__file__).parent
    return digest_json(
        {
            "sources": {name: file_digest(root / name) for name in ("collection.py", "wad_source.py", "raw_events.py")},
            "league-tools": version("league-tools"),
        }
    )


def file_digest(path: Path) -> str:
    """返回文件字节的 SHA-256。"""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def digest_json(value: object) -> str:
    """计算不受对象键顺序影响的内容摘要。"""

    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_object(path: Path) -> dict[str, Any]:
    """读取可丢弃的进度或缓存索引；损坏时重新采集。"""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def write_json(path: Path, value: object) -> None:
    """原子写出可读的 UTF-8 JSON。"""

    write_bytes(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"))


def write_bytes(path: Path, payload: bytes) -> None:
    """同目录写入、同步后原子替换，避免中断留下半份文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".pending")
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
