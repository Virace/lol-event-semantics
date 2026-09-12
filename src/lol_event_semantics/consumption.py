"""从完整审计数据派生按用途选择的中文消费包。"""

from __future__ import annotations

import gzip
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from .dataset_identity import content_digest, describe_payload_files
from .normalized_lookup import iter_normalized_rows
from .normalized_translation_model import RenderOptions
from .normalized_translation_renderer import render_translation

SCHEMA_VERSION = 1
PROFILES = ("web", "software", "lookup")
ROW_COLUMNS = ("raw", "label", "flags", "skin", "state", "source_index")
STATE_COLUMNS = ("asset_kind", "support_level", "render_status", "review_status", "label_basis")
FLAGS = {
    "raw_contains_skin": 1,
    "champion_context_omitted": 2,
    "skin_context_omitted": 4,
    "preview_label": 8,
    "needs_review": 16,
    "unresolved_entity": 32,
}
SKIN_TOKEN = re.compile(r"Skin(\d+)", re.IGNORECASE)
ASSET_ROOT = Path(__file__).with_name("consumer_assets")


def canonical_bytes(value: object) -> bytes:
    """以固定 UTF-8 编码生成可复核的消费合同字节。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_source(root: Path) -> dict[str, Any]:
    """复核审计 payload 闭包与内容身份，拒绝被修改的候选。"""

    manifest = read_object(root / "manifest.json")
    files = describe_payload_files(root)
    identity = manifest.get("content_identity")
    if manifest.get("schema_version") != 2 or not isinstance(identity, dict):
        raise ValueError("consumption requires a normalized V2 dataset")
    if manifest.get("files") != files:
        raise ValueError("source payload files or hashes differ from manifest")
    digest = content_digest(identity, files)
    if manifest.get("content_digest") != digest or manifest.get("dataset_id") != f"local-v1-{digest[:16]}":
        raise ValueError("source dataset identity is invalid")
    return manifest


def project_record(row: Mapping[str, Any], source_index: int) -> dict[str, Any]:
    """生成保留触发含义的中文消费记录，只省略可证明的主体上下文。

    未解析事件保留现有预览并明确标记，不用泛化文字伪装成功翻译。
    交互对象、借用技能的英雄和其他条件不属于可省略的主体。
    """

    raw = row.get("raw_event_name")
    entity = row.get("entity")
    if not isinstance(raw, str) or not raw or not isinstance(entity, dict):
        raise ValueError(f"invalid source record at {source_index}")
    kind, alias = entity.get("kind"), entity.get("alias")
    if not isinstance(kind, str) or not isinstance(alias, str):
        raise ValueError(f"invalid entity at {source_index}")
    owner: dict[str, Any] = {"kind": kind, "alias": alias}
    base = entity.get("base_champion")
    if isinstance(base, dict) and isinstance(base.get("alias"), str):
        owner["base_champion"] = base["alias"]
    flags = FLAGS["raw_contains_skin"] if SKIN_TOKEN.search(raw) else 0
    translation = row.get("translation")
    contexts: set[str] = set()
    skin_number: int | None = None
    label: str | None = None
    full_label: str | None = None
    label_basis = "missing"
    if isinstance(translation, dict):
        args = translation.get("arguments")
        args = args if isinstance(args, dict) else {}
        champion = args.get("champion")
        champion = champion if isinstance(champion, dict) else {}
        champion_alias = champion.get("alias") or champion.get("key")
        owner_aliases = {alias.casefold(), str(owner.get("base_champion", "")).casefold()}
        is_subject = (
            kind == "champion" and isinstance(champion_alias, str) and champion_alias.casefold() in owner_aliases
        )
        if is_subject:
            contexts.add("champion")
            skin = args.get("skin")
            if isinstance(skin, dict):
                token = str(skin.get("token") or skin.get("key") or "")
                match = SKIN_TOKEN.fullmatch(token)
                if match:
                    skin_number = int(match.group(1))
                    contexts.add("skin")
        full = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        core = render_translation(
            translation, RenderOptions(locale="zh_CN", mode="default", known_context=frozenset(contexts))
        )
        full_label = full.text or None
        label = core.text or None
        if "champion" in full.used_roles and "champion" not in core.used_roles:
            flags |= FLAGS["champion_context_omitted"]
        if "skin" in full.used_roles and "skin" not in core.used_roles:
            flags |= FLAGS["skin_context_omitted"]
        if label:
            label_basis = "translation"
    if label is None:
        display = row.get("display")
        display = display if isinstance(display, dict) else {}
        preview = display.get("zh_cn_short") or display.get("zh_cn_detail")
        if isinstance(preview, str) and preview.strip():
            label = preview
            full_label = full_label or preview
            label_basis = "preview"
            flags |= FLAGS["preview_label"]
    attribution = entity.get("attribution")
    unresolved = kind == "unknown" or isinstance(attribution, dict) and attribution.get("basis") == "unresolved"
    if unresolved:
        flags |= FLAGS["unresolved_entity"]
    if unresolved or row.get("render_status") not in {"rendered", "not_applicable"} or label_basis != "translation":
        flags |= FLAGS["needs_review"]
    state = [str(row.get(key) or "unknown") for key in STATE_COLUMNS[:-1]] + [label_basis]
    return {
        "raw_event_name": raw,
        "entity": owner,
        "label": label,
        "flags": flags,
        "skin": skin_number,
        "state": state,
        "source_index": source_index,
        "full_label": full_label,
        "record_id": row.get("record_id"),
        "event_id": row.get("event_id"),
    }


def common_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """返回所有消费 profile 必须无损保留的公共投影。"""

    return {key: row[key] for key in ("raw_event_name", "entity", "label", "flags", "skin", "state", "source_index")}


def projection_digest(rows: Iterable[Mapping[str, Any]]) -> str:
    """按源记录顺序计算公共消费投影摘要。"""

    digest = hashlib.sha256()
    for row in sorted(rows, key=lambda value: int(value["source_index"])):
        digest.update(canonical_bytes(common_record(row)))
        digest.update(b"\n")
    return digest.hexdigest()


def build_consumption(
    normalized_root: Path,
    output_root: Path,
    *,
    profiles: Iterable[str] = PROFILES,
    support_levels: Iterable[str] = (),
    asset_kinds: Iterable[str] = (),
    entities: Iterable[str] = (),
) -> dict[str, Any]:
    """从同一审计数据生成独立消费包，不覆盖任何已有输出。

    filters 只改变显式选择范围，默认保留全部记录。manifest 最后写入，
    未完成目录不构成有效产物；出错时保留诊断文件供人工处理。
    """

    selected_profiles = sorted(set(profiles))
    if not selected_profiles or set(selected_profiles).difference(PROFILES):
        raise ValueError(f"profiles must be selected from {PROFILES}")
    filters = {
        "support_levels": sorted(set(support_levels)),
        "asset_kinds": sorted(set(asset_kinds)),
        "entities": sorted(set(entities)),
    }
    if set(filters["support_levels"]).difference({"stable", "experimental", "unsupported"}):
        raise ValueError("unknown support level filter")
    if set(filters["asset_kinds"]).difference({"vo", "sfx", "control", "unknown"}):
        raise ValueError("unknown asset kind filter")
    if output_root.exists():
        raise ValueError(f"output already exists; choose a new directory: {output_root}")
    source = verify_source(normalized_root)
    records: list[dict[str, Any]] = []
    total = 0
    for _path, line_number, row in iter_normalized_rows(normalized_root):
        total += 1
        if matches_filters(row, filters):
            records.append(project_record(row, line_number))
    if total != source.get("record_count"):
        raise ValueError(f"source record count mismatch: {total}")
    source_info = {key: source[key] for key in ("dataset_id", "content_digest", "record_count")}
    shared = {
        "schema_version": SCHEMA_VERSION,
        "locale": "zh_CN",
        "source": source_info,
        "filters": filters,
        "record_count": len(records),
        "projection_digest": projection_digest(records),
        "flags": FLAGS,
        "state_columns": list(STATE_COLUMNS),
        "text_contract": "trigger-explanation.zh_CN.v1",
        "audit_locator": "source.content_digest + normalized-events.jsonl one-based source_index",
    }
    output_root.mkdir(parents=True)
    products = []
    for profile in selected_profiles:
        root = output_root / profile
        root.mkdir()
        if profile == "web":
            details = write_web(records, root)
        elif profile == "software":
            details = write_software(records, root)
        else:
            details = write_lookup(records, root)
        write_json(root / "schema.json", read_object(ASSET_ROOT / "schema.json"))
        shutil.copyfile(ASSET_ROOT / "consumer.py", root / "consumer.py")
        manifest = seal_package(root, {**shared, "profile": profile, **details})
        products.append(
            {
                "profile": profile,
                "path": f"{profile}/manifest.json",
                "package_id": manifest["package_id"],
                "record_count": manifest["record_count"],
                "bytes": sum(file["bytes"] for file in manifest["files"] if not file["path"].endswith(".gz")),
                "gzip_bytes": sum(file["bytes"] for file in manifest["files"] if file["path"].endswith(".gz")),
            }
        )
    result = {**shared, "products": products}
    write_json(output_root / "manifest.json", result)
    return result


def matches_filters(row: Mapping[str, Any], filters: Mapping[str, list[str]]) -> bool:
    """按公开选择器筛选，不隐式过滤 Experimental 或缺解释的事件。"""

    entity = row.get("entity") or {}
    entity_key = f"{entity.get('kind')}:{entity.get('alias')}"
    return (
        (not filters["support_levels"] or row.get("support_level") in filters["support_levels"])
        and (not filters["asset_kinds"] or row.get("asset_kind") in filters["asset_kinds"])
        and (not filters["entities"] or entity_key in filters["entities"])
    )


def pack_rows(records: list[dict[str, Any]]) -> dict[str, Any]:
    """将重复文案和状态字典编码，原名及逐记录定位仍完整保留。"""

    labels = sorted({row["label"] for row in records if row["label"] is not None})
    states = sorted({tuple(row["state"]) for row in records})
    label_ids = {label: index for index, label in enumerate(labels)}
    state_ids = {state: index for index, state in enumerate(states)}
    return {
        "columns": list(ROW_COLUMNS),
        "labels": labels,
        "states": [list(state) for state in states],
        "rows": [
            [
                row["raw_event_name"],
                label_ids.get(row["label"]),
                row["flags"],
                row["skin"],
                state_ids[tuple(row["state"])],
                row["source_index"],
            ]
            for row in records
        ],
    }


def write_web(records: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """写出按实体加载的网页包及按原名分桶的可选路由索引。"""

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        groups[canonical_bytes(row["entity"]).decode("utf-8")].append(row)
    shards: list[dict[str, Any]] = []
    routes: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for key, rows in sorted(groups.items()):
        entity = rows[0]["entity"]
        shard_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
        path = f"entities/{shard_id}.json"
        write_json(root / path, {"schema_version": SCHEMA_VERSION, "entity": entity, **pack_rows(rows)})
        index = len(shards)
        shards.append({"entity": entity, "path": path, "record_count": len(rows)})
        for raw in sorted({row["raw_event_name"] for row in rows}):
            bucket = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:2]
            routes[bucket].setdefault(raw, []).append(index)
    for bucket, names in sorted(routes.items()):
        write_json(root / f"routes/{bucket}.json", names)
    shutil.copyfile(ASSET_ROOT / "client.mjs", root / "client.mjs")
    return {
        "shards": shards,
        "routing": {
            "algorithm": "sha256-utf8-prefix",
            "prefix_length": 2,
            "path": "routes/{bucket}.json",
            "buckets": sorted(routes),
        },
    }


def write_software(records: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """写出具名字段与完整主体文案，供桌面软件直接导入。"""

    with (root / "events.jsonl").open("wb") as handle:
        for row in records:
            handle.write(canonical_bytes(row) + b"\n")
    return {"path": "events.jsonl"}


def write_lookup(records: list[dict[str, Any]], root: Path) -> dict[str, Any]:
    """写出单文件原名索引，同名记录始终保留数组而不是覆盖。"""

    owners = sorted({canonical_bytes(row["entity"]).decode("utf-8") for row in records})
    owner_ids = {owner: index for index, owner in enumerate(owners)}
    packed = pack_rows(records)
    events: dict[str, list[list[Any]]] = defaultdict(list)
    for row, encoded in zip(records, packed.pop("rows"), strict=True):
        owner_id = owner_ids[canonical_bytes(row["entity"]).decode("utf-8")]
        events[row["raw_event_name"]].append([*encoded[1:], owner_id])
    packed["columns"] = [*ROW_COLUMNS[1:], "entity"]
    write_json(
        root / "lookup.json",
        {
            "schema_version": SCHEMA_VERSION,
            **packed,
            "entities": [json.loads(owner) for owner in owners],
            "events": events,
        },
    )
    return {"path": "lookup.json"}


def write_json(path: Path, value: object) -> None:
    """以紧凑确定性格式写入一个新生成文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_bytes(value) + b"\n")


def read_object(path: Path) -> dict[str, Any]:
    """读取 JSON object，拒绝非对象输入。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def seal_package(root: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """生成可选 gzip 传输副本并在最后固化文件闭包与包身份。"""

    files = sorted(path for path in root.rglob("*") if path.is_file())
    for path in files:
        path.with_name(path.name + ".gz").write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    manifest = {**metadata, "files": describe_files(root)}
    digest = hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    manifest["package_id"] = f"consumption-v1-{digest}"
    write_json(root / "manifest.json", manifest)
    return manifest


def describe_files(root: Path) -> list[dict[str, Any]]:
    """记录消费文件闭包，不把 manifest 自身包含在摘要中。"""

    return [
        {
            "path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file() and path != root / "manifest.json"
    ]


def verify_package(root: Path) -> dict[str, Any]:
    """复核消费包的路径边界、逐文件摘要和 manifest 身份。"""

    manifest = read_object(root / "manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION or manifest.get("profile") not in PROFILES:
        raise ValueError("unsupported consumption package")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("package files must be an array")
    for file in files:
        if not isinstance(file, dict) or not isinstance(file.get("path"), str):
            raise ValueError("invalid package file")
        path = PurePosixPath(file["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in file["path"] or ":" in file["path"]:
            raise ValueError("unsafe package path")
        if not (root / file["path"]).resolve().is_relative_to(root.resolve()):
            raise ValueError("package file escapes root")
    if files != describe_files(root):
        raise ValueError("package payload hashes or file closure mismatch")
    unsigned = {key: value for key, value in manifest.items() if key != "package_id"}
    expected = "consumption-v1-" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    if manifest.get("package_id") != expected:
        raise ValueError("package identity mismatch")
    for file in files:
        if file["path"].endswith(".gz"):
            compressed = root / file["path"]
            original = compressed.with_name(compressed.name[:-3])
            if gzip.decompress(compressed.read_bytes()) != original.read_bytes():
                raise ValueError("gzip content differs from uncompressed payload")
    return manifest


def unpack_rows(data: Mapping[str, Any], entity: Mapping[str, Any]) -> list[dict[str, Any]]:
    """解码网页字典行并检查索引边界。"""

    if data.get("columns") != list(ROW_COLUMNS):
        raise ValueError("unknown row columns")
    labels, states, rows = data.get("labels"), data.get("states"), data.get("rows")
    if not isinstance(labels, list) or not all(isinstance(label, str) for label in labels):
        raise ValueError("invalid labels dictionary")
    if not isinstance(states, list) or not isinstance(rows, list):
        raise ValueError("invalid states or rows")
    result = []
    for encoded in rows:
        if not isinstance(encoded, list) or len(encoded) != len(ROW_COLUMNS):
            raise ValueError("invalid compact row")
        raw, label_id, flags, skin, state_id, source_index = encoded
        if label_id is not None and (type(label_id) is not int or not 0 <= label_id < len(labels)):
            raise ValueError("label id out of range")
        if type(state_id) is not int or not 0 <= state_id < len(states):
            raise ValueError("state id out of range")
        if not isinstance(raw, str) or not raw or type(flags) is not int or not 0 <= flags < 64:
            raise ValueError("invalid raw or flags")
        if (
            type(source_index) is not int
            or source_index < 1
            or skin is not None
            and (type(skin) is not int or skin < 0)
        ):
            raise ValueError("invalid source index or skin")
        state = states[state_id]
        if (
            not isinstance(state, list)
            or len(state) != len(STATE_COLUMNS)
            or not all(isinstance(x, str) for x in state)
        ):
            raise ValueError("invalid state")
        result.append(
            {
                "raw_event_name": raw,
                "entity": dict(entity),
                "label": labels[label_id] if label_id is not None else None,
                "flags": flags,
                "skin": skin,
                "state": state,
                "source_index": source_index,
            }
        )
    return result


def read_package_records(root: Path, manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    """读取任一 profile 的公共投影，验证网页路由没有丢失或误指。"""

    profile = manifest["profile"]
    if profile == "software":
        return [json.loads(line) for line in (root / "events.jsonl").read_text(encoding="utf-8").splitlines() if line]
    if profile == "lookup":
        data = read_object(root / "lookup.json")
        if data.get("columns") != [*ROW_COLUMNS[1:], "entity"]:
            raise ValueError("unknown lookup columns")
        result = []
        for raw, entries in data["events"].items():
            for values in entries:
                if not isinstance(values, list) or len(values) != 6:
                    raise ValueError("invalid lookup entry")
                owner_id = values[-1]
                if type(owner_id) is not int or not 0 <= owner_id < len(data["entities"]):
                    raise ValueError("entity id out of range")
                result.extend(
                    unpack_rows(
                        {**data, "columns": list(ROW_COLUMNS), "rows": [[raw, *values[:-1]]]},
                        data["entities"][owner_id],
                    )
                )
        return result
    result = []
    expected_routes: dict[str, dict[str, list[int]]] = defaultdict(dict)
    for index, shard in enumerate(manifest["shards"]):
        path = shard["path"]
        if path not in {file["path"] for file in manifest["files"]}:
            raise ValueError("shard is outside file closure")
        data = read_object(root / path)
        if data.get("entity") != shard["entity"]:
            raise ValueError("shard entity mismatch")
        rows = unpack_rows(data, shard["entity"])
        if len(rows) != shard["record_count"]:
            raise ValueError("shard record count mismatch")
        result.extend(rows)
        for raw in sorted({row["raw_event_name"] for row in rows}):
            bucket = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:2]
            expected_routes[bucket].setdefault(raw, []).append(index)
    if manifest["routing"].get("buckets") != sorted(expected_routes):
        raise ValueError("routing bucket coverage mismatch")
    for bucket, expected in expected_routes.items():
        if read_object(root / f"routes/{bucket}.json") != expected:
            raise ValueError("raw routing mismatch")
    return result


def validate_consumption(root: Path, normalized_root: Path | None = None) -> dict[str, Any]:
    """验证独立消费包，必要时逐记录复算源投影，证明解释和状态没有丢失。"""

    manifest = verify_package(root)
    records = read_package_records(root, manifest)
    indices = [row["source_index"] for row in records]
    if len(records) != manifest["record_count"] or len(set(indices)) != len(indices):
        raise ValueError("record coverage has duplicates or missing records")
    if any(index > manifest["source"]["record_count"] for index in indices):
        raise ValueError("source index exceeds source dataset")
    if projection_digest(records) != manifest["projection_digest"]:
        raise ValueError("public projection differs from declared digest")
    if normalized_root is not None:
        source = verify_source(normalized_root)
        if any(source.get(key) != value for key, value in manifest["source"].items()):
            raise ValueError("consumption package belongs to another source dataset")
        expected = [
            project_record(row, line)
            for _path, line, row in iter_normalized_rows(normalized_root)
            if matches_filters(row, manifest["filters"])
        ]
        if len(expected) != len(records) or projection_digest(expected) != manifest["projection_digest"]:
            raise ValueError("consumption projection loses or changes source information")
        if manifest["profile"] == "software" and sorted(records, key=lambda row: row["source_index"]) != expected:
            raise ValueError("software fields differ from source projection")
    return {
        "verified": True,
        "package_id": manifest["package_id"],
        "profile": manifest["profile"],
        "record_count": len(records),
        "source_checked": normalized_root is not None,
        "support_counts": dict(Counter(row["state"][1] for row in records)),
    }
