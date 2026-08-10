"""Source-free verifier and raw-event lookup for a normalized dataset."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

CONTENT_DIGEST_VERSION = "lol-event-semantics.content-digest.v1"
V1_STABLE_DECISION_ID = "v1-initial-vo-2026-08-07"
V1_STABLE_PATTERN_COUNT = 86
V1_STABLE_RECORD_COUNT = 5061
V1_STABLE_PATTERN_IDS_DIGEST = "e1954528a33188c23ede9d633cd8c5828c3c588fa7862dc2261fd4abec798912"
REQUIRED_PAYLOAD_PATHS = frozenset(
    {
        "examples/consumer.py",
        "normalized-events.jsonl",
        "pattern-catalog.json",
        "quality-summary.json",
        "schema/manifest.schema.json",
        "schema/normalized-event.schema.json",
        "schema/pattern-catalog.schema.json",
        "schema/quality-summary.schema.json",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--query")
    parser.add_argument("--expect", choices=("any", "miss", "stable", "experimental", "unsupported"), default="any")
    args = parser.parse_args()
    try:
        result = verify_and_query(args.root, args.query)
        _check_expectation(result["matches"], args.expect)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"verified": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def verify_and_query(root: Path, raw_event_name: str | None) -> dict[str, Any]:
    """Validate schemas, payload identity and stream while performing an exact lookup."""

    manifest = _read_object(root / "manifest.json")
    _verify_files(root, manifest)
    _verify_content_digest(manifest)
    schemas = {
        "manifest": _read_object(root / "schema" / "manifest.schema.json"),
        "record": _read_object(root / "schema" / "normalized-event.schema.json"),
        "catalog": _read_object(root / "schema" / "pattern-catalog.schema.json"),
        "quality": _read_object(root / "schema" / "quality-summary.schema.json"),
    }
    for name, schema in schemas.items():
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            raise ValueError(f"{name} schema is not draft 2020-12")
    _validate(manifest, schemas["manifest"], "manifest")
    catalog = _read_object(root / "pattern-catalog.json")
    quality = _read_object(root / "quality-summary.json")
    _validate(catalog, schemas["catalog"], "pattern-catalog")
    _validate(quality, schemas["quality"], "quality-summary")
    catalog_ids = {
        pattern["pattern_id"]
        for pattern in catalog.get("patterns", [])
        if isinstance(pattern, dict) and isinstance(pattern.get("pattern_id"), str)
    }
    _verify_metadata_identity(manifest, catalog, quality, catalog_ids)

    count = 0
    support_counts: Counter[str] = Counter()
    review_counts: Counter[str] = Counter()
    pattern_record_counts: Counter[str] = Counter()
    matches: list[dict[str, Any]] = []
    previous_id = ""
    with (root / "normalized-events.jsonl").open("r", encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            _validate(row, schemas["record"], f"normalized-events:{line_number}")
            record_id = row["record_id"]
            if record_id <= previous_id:
                raise ValueError(f"record stream is not strictly ordered at line {line_number}")
            previous_id = record_id
            if row["pattern_id"] not in catalog_ids:
                raise ValueError(f"record references missing pattern at line {line_number}")
            count += 1
            support_counts[str(row["support_level"])] += 1
            review_counts[str(row["review_status"])] += 1
            pattern_record_counts[str(row["pattern_id"])] += 1
            if raw_event_name is not None and row.get("raw_event_name") == raw_event_name:
                matches.append(_query_projection(row))
    if count != manifest.get("record_count"):
        raise ValueError(f"record_count mismatch: manifest={manifest.get('record_count')} actual={count}")
    _verify_stream_identity(manifest, catalog, quality, support_counts, review_counts, pattern_record_counts)
    return {
        "verified": True,
        "dataset_id": manifest.get("dataset_id"),
        "content_digest": manifest.get("content_digest"),
        "record_count": count,
        "query": raw_event_name,
        "matches": matches,
    }


def _verify_files(root: Path, manifest: Mapping[str, Any]) -> None:
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("manifest.files must be an array")
    paths: list[str] = []
    for entry in files:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("manifest.files entry is invalid")
        paths.append(entry["path"])
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("manifest.files paths must be sorted and unique")
    path_set = set(paths)
    missing_required = sorted(REQUIRED_PAYLOAD_PATHS.difference(path_set))
    if missing_required:
        raise ValueError(f"manifest.files is missing required payload paths: {','.join(missing_required)}")
    root = root.resolve(strict=True)
    for entry in files:
        assert isinstance(entry, dict) and isinstance(entry.get("path"), str)
        path = _payload_path(root, entry["path"])
        if not path.is_file():
            raise ValueError(f"payload file is missing: {entry['path']}")
        if path.stat().st_size != entry.get("bytes"):
            raise ValueError(f"payload byte size mismatch: {entry['path']}")
        if _sha256(path) != entry.get("sha256"):
            raise ValueError(f"payload sha256 mismatch: {entry['path']}")
    actual_paths: set[str] = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"payload symlinks are not allowed: {path.relative_to(root).as_posix()}")
        relative = path.relative_to(root).as_posix()
        if path.is_file() and relative != "manifest.json":
            actual_paths.add(relative)
    if actual_paths != path_set:
        missing = sorted(path_set.difference(actual_paths))
        unlisted = sorted(actual_paths.difference(path_set))
        raise ValueError(f"manifest.files payload closure mismatch: missing={missing} unlisted={unlisted}")


def _payload_path(root: Path, relative: str) -> Path:
    logical = PurePosixPath(relative)
    if (
        not relative
        or "\\" in relative
        or logical.is_absolute()
        or any(part in {"", ".", ".."} for part in logical.parts)
        or ":" in logical.parts[0]
    ):
        raise ValueError(f"unsafe payload path: {relative}")
    path = (root / Path(*logical.parts)).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"payload path escapes root: {relative}") from exc
    return path


def _verify_content_digest(manifest: Mapping[str, Any]) -> None:
    if manifest.get("content_digest_algorithm") != CONTENT_DIGEST_VERSION:
        raise ValueError("unsupported content digest algorithm")
    payload = {
        "algorithm": CONTENT_DIGEST_VERSION,
        "content_identity": manifest.get("content_identity"),
        "files": manifest.get("files"),
    }
    actual = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    if actual != manifest.get("content_digest"):
        raise ValueError("content digest mismatch")
    if manifest.get("dataset_id") != f"local-v1-{actual[:16]}":
        raise ValueError("dataset id does not match content digest")


def _verify_metadata_identity(
    manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    quality: Mapping[str, Any],
    catalog_ids: set[str],
) -> None:
    patterns = catalog.get("patterns")
    pattern_list = patterns if isinstance(patterns, list) else []
    if len(catalog_ids) != len(pattern_list):
        raise ValueError("pattern catalog contains duplicate or invalid pattern ids")
    expected_pattern_count = manifest.get("pattern_count")
    if expected_pattern_count != catalog.get("pattern_count") or expected_pattern_count != len(pattern_list):
        raise ValueError("pattern_count identity mismatch")

    identity = manifest.get("content_identity")
    if not isinstance(identity, dict):
        raise ValueError("manifest content_identity is invalid")
    review = identity.get("review")
    decision_id = review.get("decision_id") if isinstance(review, dict) else None
    if decision_id != catalog.get("decision_id") or decision_id != quality.get("decision_id"):
        raise ValueError("decision identity mismatch")
    if identity.get("quality_status") == "not_evaluated":
        raise ValueError("source-free consumer requires an evaluated quality summary")
    if identity.get("quality_status") != quality.get("status"):
        raise ValueError("quality status identity mismatch")
    if identity.get("quality_gate_version") != quality.get("gate_version"):
        raise ValueError("quality gate identity mismatch")
    if identity.get("quality_status") == "passed" and not _v1_stable_scope_matches(quality):
        raise ValueError("passed quality summary does not match the frozen V1 Stable scope")

    global_quality = quality.get("global")
    if not isinstance(global_quality, dict):
        raise ValueError("quality global summary is invalid")
    if global_quality.get("record_count") != manifest.get("record_count"):
        raise ValueError("quality record_count identity mismatch")
    if global_quality.get("pattern_count") != manifest.get("pattern_count"):
        raise ValueError("quality pattern_count identity mismatch")
    support = global_quality.get("support")
    pattern_support = Counter(
        str(pattern.get("support_level")) for pattern in pattern_list if isinstance(pattern, dict)
    )
    if not isinstance(support, dict) or support.get("pattern_counts") != dict(sorted(pattern_support.items())):
        raise ValueError("catalog support counts do not match quality summary")


def _v1_stable_scope_matches(quality: Mapping[str, Any]) -> bool:
    contract = quality.get("scope_contract")
    return isinstance(contract, dict) and (
        quality.get("decision_id") == V1_STABLE_DECISION_ID
        and contract.get("matched") is True
        and contract.get("decision_id") == V1_STABLE_DECISION_ID
        and contract.get("expected_pattern_count") == V1_STABLE_PATTERN_COUNT
        and contract.get("actual_pattern_count") == V1_STABLE_PATTERN_COUNT
        and contract.get("expected_record_count") == V1_STABLE_RECORD_COUNT
        and contract.get("actual_record_count") == V1_STABLE_RECORD_COUNT
        and contract.get("expected_pattern_ids_digest") == V1_STABLE_PATTERN_IDS_DIGEST
        and contract.get("actual_pattern_ids_digest") == V1_STABLE_PATTERN_IDS_DIGEST
    )


def _verify_stream_identity(
    manifest: Mapping[str, Any],
    catalog: Mapping[str, Any],
    quality: Mapping[str, Any],
    support_counts: Counter[str],
    review_counts: Counter[str],
    pattern_record_counts: Counter[str],
) -> None:
    identity = manifest["content_identity"]
    expected_support = dict(sorted(support_counts.items()))
    expected_review = dict(sorted(review_counts.items()))
    if identity.get("support_level_counts") != expected_support:
        raise ValueError("stream support counts do not match manifest identity")
    review = identity.get("review")
    if not isinstance(review, dict) or review.get("record_counts") != expected_review:
        raise ValueError("stream review counts do not match manifest identity")
    global_quality = quality["global"]
    support = global_quality.get("support")
    if not isinstance(support, dict) or support.get("record_counts") != expected_support:
        raise ValueError("stream support counts do not match quality summary")

    patterns = catalog.get("patterns")
    assert isinstance(patterns, list)
    for pattern in patterns:
        assert isinstance(pattern, dict)
        pattern_id = str(pattern["pattern_id"])
        if pattern.get("record_count") != pattern_record_counts.get(pattern_id, 0):
            raise ValueError(f"catalog record_count mismatch: {pattern_id}")


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> None:
    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        matches = 0
        for candidate in one_of:
            try:
                _validate(value, candidate, path)
            except ValueError:
                continue
            matches += 1
        if matches != 1:
            raise ValueError(f"{path} does not match exactly one schema")
        return
    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(value, expected_type):
        raise ValueError(f"{path} has invalid type")
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} does not match const")
    if isinstance(schema.get("enum"), list) and value not in schema["enum"]:
        raise ValueError(f"{path} is not in enum")
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            raise ValueError(f"{path} is too short")
        if isinstance(schema.get("pattern"), str) and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"{path} does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            raise ValueError(f"{path} is below minimum")
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            raise ValueError(f"{path} has too few items")
        if schema.get("uniqueItems") is True:
            encoded = [_canonical_bytes(item) for item in value]
            if len(encoded) != len(set(encoded)):
                raise ValueError(f"{path} contains duplicate items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]")
    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            missing = [field for field in required if field not in value]
            if missing:
                raise ValueError(f"{path} is missing required fields: {','.join(missing)}")
        properties = schema.get("properties")
        property_map = properties if isinstance(properties, dict) else {}
        if schema.get("additionalProperties") is False:
            extras = sorted(set(value).difference(property_map))
            if extras:
                raise ValueError(f"{path} has extra fields: {','.join(extras)}")
        for field, field_schema in property_map.items():
            if field in value and isinstance(field_schema, dict):
                _validate(value[field], field_schema, f"{path}.{field}")


def _matches_type(value: Any, expected: Any) -> bool:
    choices = expected if isinstance(expected, list) else [expected]
    return any(
        (choice == "null" and value is None)
        or (choice == "object" and isinstance(value, dict))
        or (choice == "array" and isinstance(value, list))
        or (choice == "string" and isinstance(value, str))
        or (choice == "integer" and isinstance(value, int) and not isinstance(value, bool))
        or (choice == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
        or (choice == "boolean" and isinstance(value, bool))
        for choice in choices
    )


def _query_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    labels = row.get("rendered_labels")
    zh_cn = labels.get("zh_CN") if isinstance(labels, dict) else None
    return {
        "record_id": row.get("record_id"),
        "raw_event_name": row.get("raw_event_name"),
        "pattern_id": row.get("pattern_id"),
        "support_level": row.get("support_level"),
        "review_status": row.get("review_status"),
        "zh_CN": zh_cn,
    }


def _check_expectation(matches: object, expected: str) -> None:
    rows = matches if isinstance(matches, list) else []
    if expected == "any":
        return
    if expected == "miss":
        if rows:
            raise ValueError("query unexpectedly matched")
        return
    if len(rows) != 1 or rows[0].get("support_level") != expected:
        raise ValueError(f"query did not produce one {expected} match")


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
