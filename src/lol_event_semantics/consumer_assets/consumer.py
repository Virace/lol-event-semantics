"""仅使用标准库验证和查询消费包，可复制到没有项目源码的环境运行。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

ROW_COLUMNS = ["raw", "label", "flags", "skin", "state", "source_index"]
STATE_COLUMNS = ["asset_kind", "support_level", "render_status", "review_status", "label_basis"]
FLAGS = {
    "raw_contains_skin": 1,
    "champion_context_omitted": 2,
    "skin_context_omitted": 4,
    "preview_label": 8,
    "needs_review": 16,
    "unresolved_entity": 32,
}
STATE_VALUES = (
    {"vo", "sfx", "control", "unknown"},
    {"stable", "experimental", "unsupported", "unknown"},
    {"rendered", "fallback", "missing", "not_applicable", "unknown"},
    {"not_required", "pending", "accepted", "rejected", "superseded", "unknown"},
    {"translation", "preview", "missing"},
)
COMMON_KEYS = ("raw_event_name", "entity", "label", "flags", "skin", "state", "source_index")


def canonical_bytes(value: object) -> bytes:
    """生成合同指定的紧凑、排序 UTF-8 JSON 字节。"""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def read_object(path: Path) -> dict[str, Any]:
    """读取 JSON 对象，拒绝非对象输入。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def check_path(root: Path, name: str) -> Path:
    """约束相对文件路径与实际解析目标均位于消费包内。"""

    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(c in name for c in "\\:")
    ):
        raise ValueError("unsafe package path")
    target = root / name
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("package file escapes root")
    return target


def check_entity(entity: object) -> None:
    """验证实体身份，不推断别名或基础英雄关系。"""

    if not isinstance(entity, dict) or any(
        not isinstance(entity.get(key), str) or not entity[key] for key in ("kind", "alias")
    ):
        raise ValueError("invalid entity")
    if "base_champion" in entity and not isinstance(entity["base_champion"], str):
        raise ValueError("invalid base champion")


def check_record(row: dict[str, Any]) -> None:
    """验证公共投影的类型和索引；语义判定仍属于上游。"""

    if any(key not in row for key in COMMON_KEYS):
        raise ValueError("incomplete public record")
    check_entity(row["entity"])
    if not isinstance(row["raw_event_name"], str) or not row["raw_event_name"]:
        raise ValueError("invalid raw event name")
    if row["label"] is not None and (not isinstance(row["label"], str) or not row["label"]):
        raise ValueError("invalid label; missing explanations must be null")
    if type(row["flags"]) is not int or not 0 <= row["flags"] < 64:
        raise ValueError("invalid flags")
    if row["skin"] is not None and (type(row["skin"]) is not int or row["skin"] < 0):
        raise ValueError("invalid skin")
    if type(row["source_index"]) is not int or row["source_index"] < 1:
        raise ValueError("invalid source index")
    state = row["state"]
    if (
        not isinstance(state, list)
        or len(state) != len(STATE_COLUMNS)
        or not all(isinstance(value, str) for value in state)
    ):
        raise ValueError("invalid state")
    if any(value not in allowed for value, allowed in zip(state, STATE_VALUES, strict=True)):
        raise ValueError("unknown state value")


def decode_rows(data: dict[str, Any], entity: dict[str, Any]) -> list[dict[str, Any]]:
    """解码字典行，越界和未知列均作为合同错误。"""

    check_entity(entity)
    if data.get("columns") != ROW_COLUMNS:
        raise ValueError("unknown row columns")
    labels, states, encoded = data.get("labels"), data.get("states"), data.get("rows")
    if not isinstance(labels, list) or not all(isinstance(label, str) and label for label in labels):
        raise ValueError("invalid labels")
    if not isinstance(states, list) or not isinstance(encoded, list):
        raise ValueError("invalid states or rows")
    rows = []
    for values in encoded:
        if not isinstance(values, list) or len(values) != len(ROW_COLUMNS):
            raise ValueError("invalid compact row")
        raw, label_id, flags, skin, state_id, source_index = values
        if label_id is not None and (type(label_id) is not int or not 0 <= label_id < len(labels)):
            raise ValueError("label index out of range")
        if type(state_id) is not int or not 0 <= state_id < len(states):
            raise ValueError("state index out of range")
        row = {
            "raw_event_name": raw,
            "entity": entity,
            "label": None if label_id is None else labels[label_id],
            "flags": flags,
            "skin": skin,
            "state": states[state_id],
            "source_index": source_index,
        }
        check_record(row)
        rows.append(row)
    return rows


def read_records(root: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """读取一个 profile，并复核网页路由的完整对应关系。"""

    files = {entry["path"] for entry in manifest["files"]}

    def payload(name: str) -> Path:
        """只访问已核验闭包中的文件。"""

        if name not in files:
            raise ValueError("data path outside manifest file closure")
        return check_path(root, name)

    profile = manifest["profile"]
    if profile == "software":
        if manifest.get("path") != "events.jsonl":
            raise ValueError("unsupported software path")
        rows = []
        with payload(manifest["path"]).open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("invalid software record")
                check_record(row)
                rows.append(row)
        return rows
    if profile == "lookup":
        if manifest.get("path") != "lookup.json":
            raise ValueError("unsupported lookup path")
        data = read_object(payload(manifest["path"]))
        if data.get("schema_version") != 1 or data.get("columns") != [*ROW_COLUMNS[1:], "entity"]:
            raise ValueError("unsupported lookup contract")
        if not isinstance(data.get("entities"), list) or not isinstance(data.get("events"), dict):
            raise ValueError("invalid lookup dictionaries")
        rows = []
        for raw, entries in data["events"].items():
            if not isinstance(entries, list) or not entries:
                raise ValueError("invalid lookup candidates")
            for values in entries:
                if not isinstance(values, list) or len(values) != 6:
                    raise ValueError("invalid lookup candidate")
                entity_id = values[-1]
                if type(entity_id) is not int or not 0 <= entity_id < len(data["entities"]):
                    raise ValueError("entity index out of range")
                rows.extend(
                    decode_rows(
                        {**data, "columns": ROW_COLUMNS, "rows": [[raw, *values[:-1]]]}, data["entities"][entity_id]
                    )
                )
        return rows
    routing = manifest.get("routing", {})
    if (
        routing.get("algorithm") != "sha256-utf8-prefix"
        or routing.get("prefix_length") != 2
        or routing.get("path") != "routes/{bucket}.json"
    ):
        raise ValueError("unsupported routing contract")
    if not isinstance(manifest.get("shards"), list):
        raise ValueError("invalid shards")
    rows = []
    expected: dict[str, dict[str, list[int]]] = defaultdict(dict)
    seen_paths: set[str] = set()
    seen_entities: set[bytes] = set()
    for index, shard in enumerate(manifest["shards"]):
        entity_key = canonical_bytes(shard["entity"])
        if shard["path"] in seen_paths or entity_key in seen_entities:
            raise ValueError("duplicate shard")
        seen_paths.add(shard["path"])
        seen_entities.add(entity_key)
        data = read_object(payload(shard["path"]))
        if data.get("schema_version") != 1 or data.get("entity") != shard["entity"]:
            raise ValueError("shard entity or version mismatch")
        decoded = decode_rows(data, shard["entity"])
        if len(decoded) != shard["record_count"]:
            raise ValueError("shard count mismatch")
        rows.extend(decoded)
        for raw in sorted({row["raw_event_name"] for row in decoded}):
            bucket = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:2]
            expected[bucket].setdefault(raw, []).append(index)
    if routing.get("buckets") != sorted(expected):
        raise ValueError("routing bucket coverage mismatch")
    for bucket, names in expected.items():
        if read_object(payload(f"routes/{bucket}.json")) != names:
            raise ValueError("raw routing mismatch")
    return rows


def verify_package(root: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """验证全部文件、包身份及公共投影；不声称复算上游语义。

    Returns:
        已验证的 manifest 和解码记录。source 定位由摘要和一基行号组成。
    """

    manifest = read_object(root / "manifest.json")
    if manifest.get("schema_version") != 1 or manifest.get("profile") not in {"web", "software", "lookup"}:
        raise ValueError("unsupported consumption package")
    if manifest.get("flags") != FLAGS or manifest.get("state_columns") != STATE_COLUMNS:
        raise ValueError("unknown flag or state contract")
    if manifest.get("locale") != "zh_CN" or manifest.get("text_contract") != "trigger-explanation.zh_CN.v1":
        raise ValueError("unsupported text contract")
    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("invalid source")
    digest = source.get("content_digest")
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError("invalid source digest")
    if (
        source.get("dataset_id") != f"local-v1-{digest[:16]}"
        or type(source.get("record_count")) is not int
        or source["record_count"] < 0
    ):
        raise ValueError("inconsistent source identity")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("invalid file manifest")
    declared = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("invalid file entry")
        path = check_path(root, entry["path"])
        data = path.read_bytes()
        declared.append({"path": entry["path"], "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    actual_names = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path != root / "manifest.json"
    )
    if files != declared or sorted(entry["path"] for entry in files) != actual_names:
        raise ValueError("payload hashes or file closure mismatch")
    unsigned = {key: value for key, value in manifest.items() if key != "package_id"}
    expected = "consumption-v1-" + hashlib.sha256(canonical_bytes(unsigned)).hexdigest()
    if manifest.get("package_id") != expected:
        raise ValueError("package identity mismatch")
    for entry in files:
        if entry["path"].endswith(".gz"):
            original = entry["path"][:-3]
            if (
                original not in actual_names
                or gzip.decompress((root / entry["path"]).read_bytes()) != (root / original).read_bytes()
            ):
                raise ValueError("gzip differs from original payload")
    rows = read_records(root, manifest)
    indices = [row["source_index"] for row in rows]
    if (
        type(manifest.get("record_count")) is not int
        or len(rows) != manifest["record_count"]
        or len(set(indices)) != len(indices)
    ):
        raise ValueError("record coverage mismatch")
    if any(index > source["record_count"] for index in indices):
        raise ValueError("source index exceeds source dataset")
    projection = hashlib.sha256()
    for row in sorted(rows, key=lambda value: value["source_index"]):
        projection.update(canonical_bytes({key: row[key] for key in COMMON_KEYS}) + b"\n")
    if projection.hexdigest() != manifest.get("projection_digest"):
        raise ValueError("public projection digest mismatch")
    return manifest, rows


def query(root: Path, raw: str) -> dict[str, Any]:
    """验证后返回全部同名候选；空结果与完整性异常有明确区别。"""

    manifest, rows = verify_package(root)
    matches = [
        dict(row, source={**manifest["source"], "path": "normalized-events.jsonl", "source_index": row["source_index"]})
        for row in rows
        if row["raw_event_name"] == raw
    ]
    return {"status": "FOUND" if matches else "MISS", "records": matches}


def main() -> int:
    """提供无需项目安装的整包验证与事件查询入口。"""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="consumption profile directory")
    parser.add_argument("raw", nargs="?", help="exact raw event name")
    args = parser.parse_args()
    try:
        if args.raw is not None:
            result = query(args.root, args.raw)
        else:
            manifest, rows = verify_package(args.root)
            result = {
                "verified": True,
                "package_id": manifest["package_id"],
                "profile": manifest["profile"],
                "record_count": len(rows),
                "source_checked": False,
            }
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"consumption verification failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
