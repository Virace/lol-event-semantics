"""从 normalized contract 生成实体与语义状态正交的只读演示站。"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ruamel.yaml import YAML

from .mode_namespace import MODE_NAMESPACES, split_mode_entity_alias
from .consumption import project_record
from .dataset_identity import (
    canonical_json_bytes,
    content_digest as calculate_content_digest,
    describe_payload_files,
    paths_digest,
    semantic_document_identity,
    sha256_file,
)
from .normalized_lookup import iter_normalized_rows

__all__ = ["DemoBuildSummary", "DemoSiteError", "build_demo_site"]

DEMO_SCHEMA_VERSION = 3
ENTITY_CATEGORIES = {
    "champion": "英雄",
    "item": "装备",
    "map": "地图",
    "mode_system": "模式实体",
    "system": "公共系统",
    "unknown": "未知实体",
}
GENERATED_MARKER_NAME = ".rift-event-atlas-generated"
GENERATED_MARKER_VALUE = "rift-event-atlas-static-site.v1\n"
ASSET_FILENAMES = ("index.html", "styles.css", "app.js", "_headers", "site.webmanifest")
BASE_MODE = {"key": "base", "label": "通用 / 未指定", "prefix": None}
BASE_MAP = {
    "key": "base",
    "id": None,
    "alias": None,
    "label": "通用 / 未指定",
    "en_name": None,
}
CHAMPION_ALIAS_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
DATA_DRAGON_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")
SKIN_TOKEN_PATTERN = re.compile(r"Skin0*(\d+)\Z", re.IGNORECASE)
RAW_SKIN_PATTERN = re.compile(r"Skin0*(\d+)", re.IGNORECASE)
CHROMA_NAME_PATTERN = re.compile(r"(?P<base>.+?)\s+\([^()]+\)\Z")
REVIEW_LAYERS: tuple[tuple[str, str, frozenset[str]], ...] = (
    ("contract_shape", "合同结构", frozenset({"translation_frame_required_role_missing"})),
    ("coverage", "支持覆盖", frozenset({"translation_missing_for_supported_frame"})),
    (
        "semantic_projection",
        "语义投影",
        frozenset(
            {
                "action_phase_translation_missing",
                "display_drops_parsed_slot",
                "sfx_effect_translation_missing",
                "spell_component_modifier_translation_missing",
                "spell_phase_translation_missing",
                "spell_sequence_translation_missing",
            }
        ),
    ),
    (
        "review_evidence",
        "低证据审阅",
        frozenset(
            {
                "low_confidence_output",
                "low_trust_rule_in_output",
                "translation_argument_low_confidence",
                "translation_label_fallback",
                "unknown_count_in_output",
                "unknown_entity_output",
            }
        ),
    ),
)


class DemoSiteError(ValueError):
    """演示站输入或安全输出边界无效。"""


@dataclass(frozen=True, slots=True)
class DemoBuildSummary:
    """一次演示站构建的可审计摘要。"""

    output_root: Path
    dataset_id: str
    champion_count: int
    event_count: int
    map_count: int
    map_event_count: int
    unassigned_event_count: int
    review_record_count: int


@dataclass(frozen=True, slots=True)
class ChampionReference:
    """演示站所需的基础英雄参考。"""

    alias: str
    champion_id: int
    en_name: str
    zh_name: str
    zh_title: str | None
    nicknames: tuple[str, ...]
    search_terms: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkinReference:
    """演示站所需的皮肤参考。"""

    number: int
    token: str
    en_name: str
    zh_name: str


@dataclass(frozen=True, slots=True)
class MapReference:
    """演示站所需的地图参考。"""

    map_id: int
    alias: str
    en_name: str
    zh_name: str


@dataclass(frozen=True, slots=True)
class MapReferenceCatalog:
    """按地图 ID 与别名索引的只读参考集合。"""

    by_id: Mapping[int, MapReference]
    by_alias: Mapping[str, MapReference]


def build_demo_site(
    *,
    normalized_root: Path,
    validation_json: Path,
    reference_root: Path,
    output_root: Path,
) -> DemoBuildSummary:
    """生成按实体惰性加载、统一筛选语义反馈状态的静态站。"""

    normalized_root = normalized_root.resolve()
    validation_json = validation_json.resolve()
    reference_root = reference_root.resolve()
    output_root = output_root.resolve()
    _ensure_replaceable_output(output_root)
    manifest = _read_json_object(normalized_root / "manifest.json")
    reference_manifest = _read_yaml_object(reference_root / "manifest.yaml")
    _validate_input_identity(normalized_root, reference_root, manifest, reference_manifest)
    dataset_id = _required_text(manifest, "dataset_id", "normalized manifest")
    content_digest = _required_text(manifest, "content_digest", "normalized manifest")
    expected_count = _required_int(manifest, "record_count", "normalized manifest")
    validation = _read_json_object(validation_json)
    _validate_validation_source(
        normalized_root=normalized_root,
        validation_json=validation_json,
        validation=validation,
        expected_record_count=expected_count,
        dataset_id=dataset_id,
        content_digest=content_digest,
    )
    validation_digest = sha256_file(validation_json)
    generator_digest = paths_digest(
        Path(__file__).parent,
        [
            *Path(__file__).parent.rglob("*.py"),
            *(Path(__file__).with_name("demo_assets") / name for name in ASSET_FILENAMES),
        ],
    ).digest
    demo_id = (
        "demo-v3-"
        + hashlib.sha256(
            canonical_json_bytes(
                {
                    "dataset": content_digest,
                    "validation": validation_digest,
                    "schema": DEMO_SCHEMA_VERSION,
                    "generator": generator_digest,
                }
            )
        ).hexdigest()[:20]
    )
    identity = {"dataset_id": dataset_id, "content_digest": content_digest, "demo_id": demo_id}
    issues_by_record = _review_issues_by_record(validation)
    version = _data_dragon_version(reference_manifest)
    champions = _load_champion_references(reference_root / "champions")
    champion_by_key = {champion.alias.casefold(): champion for champion in champions}
    skins = _load_skin_references(reference_root / "skins", champions)
    map_catalog = _load_map_references(reference_root / "maps")
    names = _load_entity_names(reference_root)
    events_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    profiles: dict[str, dict[str, Any]] = {}
    matched_issues: set[str] = set()
    legacy_raw_seen: set[str] = set()
    total = 0
    for _path, _line, row in iter_normalized_rows(normalized_root):
        total += 1
        raw = _required_text(row, "raw_event_name", "normalized record")
        entity = _mapping(row.get("entity"))
        if not _mapping(entity.get("attribution")):
            raise DemoSiteError("entity.attribution is required; regenerate normalized data before building the demo")
        profile = _entity_profile(entity, champion_by_key, names, map_catalog, version)
        key = profile["key"]
        profiles[key] = profile
        event = _project_event(row, map_catalog=map_catalog, source_index=_line)
        issue_key = "record:" + str(row.get("record_id") or "")
        legacy_key = "raw:" + raw
        if legacy_key in issues_by_record:
            if raw in legacy_raw_seen:
                raise DemoSiteError("ambiguous legacy validation issue; regenerate validation with record_id")
            legacy_raw_seen.add(raw)
        issues = (*issues_by_record.get(issue_key, ()), *issues_by_record.get(legacy_key, ()))
        if issues:
            matched_issues.update((issue_key, legacy_key))
        event["issues"] = [dict(issue) for issue in issues]
        event["layers"] = _issue_layers(issues)
        reasons = []
        if entity.get("kind") == "unknown" or _mapping(entity.get("attribution")).get("basis") == "unresolved":
            reasons.append("unresolved_entity")
        if event["render_status"] != "rendered":
            reasons.append("incomplete_render")
        if event["warning_count"]:
            reasons.append("record_warning")
        if issues:
            reasons.append("validator_issue")
        event["feedback_reasons"] = reasons
        event["semantic_status"] = "feedback" if reasons else "determined"
        events_by_key[key].append(event)
    if total != expected_count:
        raise DemoSiteError(f"normalized record count mismatch: manifest={expected_count} stream={total}")
    if missing := sorted(set(issues_by_record) - matched_issues):
        raise DemoSiteError(f"validation issues do not match normalized records: {', '.join(missing[:3])}")

    documents = []
    entries = []
    category_counts: Counter[str] = Counter()
    feedback_count = 0
    for key in sorted(profiles, key=lambda value: (profiles[value]["kind"], profiles[value]["alias"].casefold())):
        profile = profiles[key]
        events = sorted(events_by_key[key], key=_review_record_sort_key)
        status_counts = Counter(event["semantic_status"] for event in events)
        support_counts = Counter(event["support_level"] for event in events)
        category_counts[profile["kind"]] += len(events)
        feedback_count += status_counts["feedback"]
        base = champion_by_key.get(profile["alias"].casefold()) if profile["kind"] == "champion" else None
        skin_payload = _build_skin_payload(base, skins.get(base.alias, ()), version) if base else []
        stats = {
            "events": len(events),
            "statuses": dict(status_counts),
            "support": dict(support_counts),
            "rendered": sum(event["render_status"] == "rendered" for event in events),
        }
        path = "data/entities/" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24] + ".json"
        documents.append(
            {
                "schema_version": DEMO_SCHEMA_VERSION,
                "dataset": identity,
                "entity": profile,
                "stats": stats,
                "skins": skin_payload,
                "events": events,
                "data_path": path,
            }
        )
        entries.append({**profile, "stats": stats, "data_path": path, "skin_count": len(skin_payload)})
    site_index = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "product": {"name": "峡谷事件语义图鉴", "name_en": "Rift Event Atlas"},
        "dataset": {
            **identity,
            "base_game_version": _mapping(manifest.get("content_identity")).get("base_game_version", "unknown"),
            "data_dragon_version": version,
            "validation_digest": validation_digest,
            "generator_digest": generator_digest,
        },
        "status_policy": {
            "determined": "中文已生成且没有记录警告、校验问题或归属问题；不代表 Stable 或人工批准。",
            "feedback": "含归属、渲染或校验问题，需要进一步反馈。",
        },
        "counts": {"source_records": total, "feedback_records": feedback_count, **dict(category_counts)},
        "categories": [
            {"key": key, "label": label, "event_count": category_counts[key]}
            for key, label in ENTITY_CATEGORIES.items()
            if category_counts[key]
        ],
        "entities": entries,
    }
    _write_site_output(output_root, site_index, documents)
    return DemoBuildSummary(
        output_root=output_root,
        dataset_id=dataset_id,
        champion_count=sum(entry["kind"] == "champion" for entry in entries),
        event_count=category_counts["champion"],
        map_count=sum(entry["kind"] == "map" for entry in entries),
        map_event_count=category_counts["map"],
        unassigned_event_count=category_counts["unknown"],
        review_record_count=feedback_count,
    )


def _validate_input_identity(
    normalized_root: Path,
    reference_root: Path,
    manifest: Mapping[str, Any],
    reference_manifest: Mapping[str, Any],
) -> None:
    """验证实际 payload 与参考输入，拒绝同条数内容替换及跨版本展示词条。"""

    files = manifest.get("files")
    identity = _mapping(manifest.get("content_identity"))
    if not isinstance(files, list) or files != describe_payload_files(normalized_root):
        raise DemoSiteError("normalized payload does not match manifest files")
    if manifest.get("content_digest") != calculate_content_digest(identity, files):
        raise DemoSiteError("normalized content_digest does not match payload identity")
    inputs = _mapping(_mapping(identity.get("input_identity")).get("inputs"))
    paths = [
        path
        for path in reference_root.rglob("*")
        if path.is_file()
        and path != reference_root / "manifest.yaml"
        and path.suffix.lower() not in {".pyc", ".pyo"}
        and not set(path.parts).intersection({"__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"})
    ]
    if inputs.get("reference") != paths_digest(reference_root, paths).to_dict():
        raise DemoSiteError("reference content does not match normalized input identity")
    if inputs.get("reference_manifest") != semantic_document_identity(reference_manifest, excluded_keys={"updated_at"}):
        raise DemoSiteError("reference manifest does not match normalized input identity")


def _load_entity_names(reference_root: Path) -> dict[str, str]:
    """读取非英雄实体已有中文名称，不承担归属判断。"""

    names = {}
    for path in sorted((reference_root / "items").glob("*.yaml")):
        entity = _mapping(_read_yaml_object(path).get("entity"))
        values = _localized_values(_mapping(entity.get("names")), "zh_CN")
        if values:
            names[f"item:{entity.get('id')}"] = values[0]
    return names


def _entity_profile(
    entity: Mapping[str, Any],
    champions: Mapping[str, ChampionReference],
    names: Mapping[str, str],
    maps: MapReferenceCatalog,
    version: str,
) -> dict[str, Any]:
    """仅用统一归属及显式基础英雄关系构建浏览实体。"""

    kind = str(entity.get("kind") or "unknown")
    if kind in {"mode_unit", "mode_boss"}:
        kind = "mode_system"
    alias = str(entity.get("alias") or "unknown")
    if kind not in ENTITY_CATEGORIES:
        raise DemoSiteError(f"unsupported entity kind: {kind}")
    base = _mapping(entity.get("base_champion"))
    champion = champions.get(str(base.get("alias") or alias).casefold()) if kind == "champion" else None
    if champion:
        alias = champion.alias
    key = f"{kind}:{alias}"
    profile = {"key": key, "kind": kind, "alias": alias, "label": names.get(key, alias), "search_terms": [alias]}
    if champion:
        profile.update(
            {
                "label": champion.zh_name,
                "title": champion.zh_title,
                "en_name": champion.en_name,
                "search_terms": list(champion.search_terms),
                "portrait_url": _portrait_url(alias, version),
            }
        )
    if kind == "map":
        reference = maps.by_alias.get(alias.casefold())
        if reference is None and alias.casefold().startswith("map") and alias[3:].isdigit():
            reference = maps.by_id.get(int(alias[3:]))
        if reference:
            profile.update({"label": reference.zh_name, "en_name": reference.en_name})
    if kind == "unknown":
        profile["label"] = f"未知实体 · {alias}"
    return profile


def _load_champion_references(root: Path) -> list[ChampionReference]:
    """读取并校验基础英雄参考。"""

    if not root.is_dir():
        raise DemoSiteError(f"champion reference root not found: {root}")

    champions: list[ChampionReference] = []
    seen_aliases: set[str] = set()
    for path in sorted(root.glob("*.yaml"), key=lambda item: item.name.casefold()):
        document = _read_yaml_object(path)
        entity = _mapping(document.get("entity"))
        if entity.get("type") != "champion":
            continue
        alias = _required_text(entity, "alias", str(path))
        if split_mode_entity_alias(alias) is not None:
            continue
        champion_id = _required_int(entity, "id", str(path))
        if champion_id <= 0:
            continue
        if CHAMPION_ALIAS_PATTERN.fullmatch(alias) is None:
            raise DemoSiteError(f"unsafe champion alias in {path}: {alias!r}")
        alias_key = alias.casefold()
        if alias_key in seen_aliases:
            raise DemoSiteError(f"duplicate base champion alias: {alias}")
        seen_aliases.add(alias_key)

        names = _mapping(entity.get("names"))
        en_names = _localized_values(names, "en_US")
        zh_names = _localized_values(names, "zh_CN")
        if not en_names or not zh_names:
            raise DemoSiteError(f"champion is missing en_US/zh_CN names: {path}")
        zh_titles = _localized_values(_mapping(entity.get("title")), "zh_CN")
        nicknames = _localized_values(_mapping(entity.get("nicknames")), "zh_CN")
        aliases = _localized_values(_mapping(entity.get("search_alias")), "zh_CN")
        search_terms = _unique_texts((alias, *en_names, *zh_names, *zh_titles, *nicknames, *aliases))
        champions.append(
            ChampionReference(
                alias=alias,
                champion_id=champion_id,
                en_name=en_names[0],
                zh_name=zh_names[0],
                zh_title=zh_titles[0] if zh_titles else None,
                nicknames=tuple(nicknames),
                search_terms=tuple(search_terms),
            )
        )
    return champions


def _load_skin_references(
    root: Path,
    champions: list[ChampionReference],
) -> dict[str, tuple[SkinReference, ...]]:
    """按基础英雄读取皮肤参考。"""

    result: dict[str, tuple[SkinReference, ...]] = {}
    for champion in champions:
        champion_root = root / champion.alias
        skins: dict[int, SkinReference] = {}
        if champion_root.is_dir():
            for path in sorted(champion_root.glob("*.yaml"), key=lambda item: item.name.casefold()):
                document = _read_yaml_object(path)
                entity = _mapping(document.get("entity"))
                if entity.get("type") != "skin" or entity.get("champion_alias") != champion.alias:
                    continue
                number = _required_int(entity, "skin_number", str(path))
                if number <= 0:
                    continue
                names = _mapping(entity.get("names"))
                en_names = _localized_values(names, "en_US")
                zh_names = _localized_values(names, "zh_CN")
                skin = SkinReference(
                    number=number,
                    token=str(entity.get("skin_token") or f"Skin{number:02d}"),
                    en_name=en_names[0] if en_names else f"Skin {number}",
                    zh_name=zh_names[0] if zh_names else f"皮肤 {number}",
                )
                existing = skins.get(number)
                if existing is not None and existing != skin:
                    raise DemoSiteError(f"conflicting skin number for {champion.alias}: {number}")
                skins[number] = skin
        result[champion.alias] = tuple(skins[number] for number in sorted(skins))
    return result


def _load_map_references(root: Path) -> MapReferenceCatalog:
    """读取地图参考，并构建 ID 与别名索引。

    Args:
        root: `reference/maps` 目录。

    Returns:
        已验证且可按 ID、别名查询的地图参考。

    Raises:
        DemoSiteError: 目录缺失，或地图 ID/别名重复、无效。
    """

    if not root.is_dir():
        raise DemoSiteError(f"map reference root not found: {root}")

    by_id: dict[int, MapReference] = {}
    by_alias: dict[str, MapReference] = {}
    for path in sorted(root.glob("*.yaml"), key=lambda item: item.name.casefold()):
        document = _read_yaml_object(path)
        entity = _mapping(document.get("entity"))
        if entity.get("type") != "map":
            continue
        map_id = _required_int(entity, "id", str(path))
        alias = _required_text(entity, "alias", str(path))
        if map_id < 0:
            raise DemoSiteError(f"invalid map id in {path}: {map_id}")
        if CHAMPION_ALIAS_PATTERN.fullmatch(alias) is None:
            raise DemoSiteError(f"unsafe map alias in {path}: {alias!r}")
        names = _mapping(entity.get("names"))
        en_names = _localized_values(names, "en_US")
        zh_names = _localized_values(names, "zh_CN")
        if not en_names or not zh_names:
            raise DemoSiteError(f"map is missing en_US/zh_CN names: {path}")
        reference = MapReference(
            map_id=map_id,
            alias=alias,
            en_name=en_names[0],
            zh_name=zh_names[0],
        )
        alias_key = alias.casefold()
        if map_id in by_id:
            raise DemoSiteError(f"duplicate map id: {map_id}")
        if alias_key in by_alias:
            raise DemoSiteError(f"duplicate map alias: {alias}")
        by_id[map_id] = reference
        by_alias[alias_key] = reference
    return MapReferenceCatalog(by_id=by_id, by_alias=by_alias)


def _validate_validation_source(
    *,
    normalized_root: Path,
    validation_json: Path,
    validation: Mapping[str, Any],
    expected_record_count: int,
    dataset_id: str,
    content_digest: str,
) -> None:
    """验证 normalized 与 validator 报告来自同一个标准 dataset run。"""

    if validation_json.parent.parent != normalized_root.parent:
        raise DemoSiteError("normalized root and validation report must belong to the same dataset run")
    summary = _mapping(validation.get("summary"))
    if summary.get("dataset_id") != dataset_id or summary.get("content_digest") != content_digest:
        raise DemoSiteError("validation identity does not match normalized manifest; regenerate the validation report")
    reported_root = Path(_required_text(summary, "normalized_root", "validation summary"))
    if reported_root.name != normalized_root.name or reported_root.parent.name != normalized_root.parent.name:
        raise DemoSiteError("validation report does not describe the same dataset run")
    record_count = _required_int(summary, "record_count", "validation summary")
    if record_count != expected_record_count:
        raise DemoSiteError(
            f"validation record count mismatch: manifest={expected_record_count} validation={record_count}"
        )
    if _required_int(summary, "error_count", "validation summary") != 0:
        raise DemoSiteError("validation report contains hard errors; repair normalized data before building the demo")
    if not isinstance(validation.get("issues"), list):
        raise DemoSiteError("validation report issues must be an array")


def _review_issues_by_record(validation: Mapping[str, Any]) -> dict[str, tuple[dict[str, str], ...]]:
    """按记录身份关联问题；旧报告仅在原名唯一时允许回退。"""

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    issues = validation.get("issues")
    assert isinstance(issues, list)
    for index, value in enumerate(issues):
        if not isinstance(value, dict):
            raise DemoSiteError(f"validation issue {index} must be an object")
        code = str(value.get("code") or "")
        raw = str(value.get("raw_event_name") or "")
        if not raw:
            continue
        record_id = str(value.get("record_id") or "")
        key = "record:" + record_id if record_id else "raw:" + raw
        grouped[key].append(
            {
                "code": code,
                "severity": str(value.get("severity") or "warning"),
                "message": str(value.get("message") or ""),
                "path": str(value.get("path") or ""),
            }
        )
    return {
        raw: tuple(sorted(values, key=lambda issue: (issue["code"], issue["path"], issue["message"])))
        for raw, values in grouped.items()
    }


def _issue_layers(issues: Sequence[Mapping[str, str]]) -> list[str]:
    """按固定展示顺序返回一条记录的问题层级。"""

    issue_codes = {str(issue.get("code") or "") for issue in issues}
    return [key for key, _label, codes in REVIEW_LAYERS if issue_codes.intersection(codes)]


def _review_record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    """返回事件分片的确定性排序键。"""

    return (str(record.get("raw_event_name") or "").casefold(), str(record.get("record_id") or ""))


def _project_event(
    row: Mapping[str, Any],
    *,
    map_catalog: MapReferenceCatalog,
    source_index: int,
) -> dict[str, Any]:
    """把 normalized 记录投影为演示站事件。

    Args:
        row: normalized 记录。
        map_catalog: 用于本地化显式地图槽位的参考索引。
        source_index: 在审计单流中的一基行号。

    Returns:
        所有实体与语义状态共用的事件投影。
    """

    display = _mapping(row.get("display"))
    entity = _mapping(row.get("entity"))
    semantic = _mapping(row.get("semantic"))
    slots = _mapping(semantic.get("slots"))
    semantic_frame = _mapping(row.get("semantic_frame"))
    warnings = row.get("warnings")
    consumer = project_record(row, source_index)
    return {
        "record_id": str(row.get("record_id") or ""),
        "raw_event_name": _required_text(row, "raw_event_name", "normalized record"),
        "zh_cn_short": _optional_text(display.get("zh_cn_short")),
        "zh_cn_detail": _optional_text(display.get("zh_cn_detail")),
        "label": consumer["label"],
        "label_basis": consumer["state"][-1],
        "context_flags": consumer["flags"],
        "full_label": consumer["full_label"],
        "display_tier": display.get("tier"),
        "asset_kind": str(row.get("asset_kind") or "unknown"),
        "entity": dict(entity),
        "event_id": row.get("event_id"),
        "frame_type": _optional_text(semantic_frame.get("frame_type")),
        "primary_family": _optional_text(semantic.get("primary_family")),
        "support_level": str(row.get("support_level") or "experimental"),
        "review_status": str(row.get("review_status") or "pending"),
        "render_status": str(row.get("render_status") or "missing"),
        "mode": _project_mode(slots.get("mode_context")),
        "map": _project_map(slots.get("map"), map_catalog),
        "skin_number": _record_skin_number(row, slots, semantic_frame) if entity.get("kind") == "champion" else None,
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
        "warnings": warnings if isinstance(warnings, list) else [],
    }


def _project_mode(value: object) -> dict[str, str | None]:
    """把模式上下文投影为稳定展示元数据。"""

    mode = _mapping(value)
    key = str(mode.get("key") or "").casefold()
    for namespace in MODE_NAMESPACES:
        if key == namespace.key:
            return {"key": namespace.key, "label": namespace.label, "prefix": namespace.prefix}
    if not key:
        return dict(BASE_MODE)
    return {
        "key": key,
        "label": str(mode.get("label") or key),
        "prefix": None,
    }


def _project_map(value: object, catalog: MapReferenceCatalog) -> dict[str, Any]:
    """只投影显式结构化地图槽位，并优先采用参考中文名。

    Args:
        value: normalized `semantic.slots.map` 值。
        catalog: 可按 ID 或别名查询的地图参考。

    Returns:
        稳定地图元数据；没有显式地图槽位时返回通用占位。
    """

    map_value = _mapping(value)
    if map_value.get("kind") != "map":
        return dict(BASE_MAP)

    raw_id = map_value.get("id")
    map_id = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else None
    raw_alias = map_value.get("alias")
    alias = raw_alias if isinstance(raw_alias, str) and raw_alias else None
    raw_key = map_value.get("key")
    key = raw_key.casefold() if isinstance(raw_key, str) and raw_key else None
    if key is None and map_id is not None:
        key = f"map{map_id}"
    if key is None and alias is not None:
        key = alias.casefold()
    if key is None:
        return dict(BASE_MAP)

    reference_by_id = catalog.by_id.get(map_id) if map_id is not None else None
    reference_by_alias = catalog.by_alias.get(alias.casefold()) if alias is not None else None
    if reference_by_id is not None and reference_by_alias is not None and reference_by_id != reference_by_alias:
        raise DemoSiteError(f"conflicting map id and alias: id={map_id} alias={alias}")
    reference = reference_by_id or reference_by_alias
    if reference is not None:
        canonical_key = f"map{reference.map_id}"
        if re.fullmatch(r"map\d+", key) is not None and key != canonical_key:
            raise DemoSiteError(f"conflicting map key and reference: key={key} expected={canonical_key}")
        key = canonical_key
    normalized_label = _optional_text(map_value.get("label"))
    return {
        "key": key,
        "id": reference.map_id if reference is not None else map_id,
        "alias": reference.alias if reference is not None else alias,
        "label": reference.zh_name if reference is not None else normalized_label or alias or key,
        "en_name": reference.en_name if reference is not None else normalized_label,
    }


def _record_skin_number(
    row: Mapping[str, Any],
    slots: Mapping[str, Any],
    semantic_frame: Mapping[str, Any],
) -> int:
    """从结构化证据或原始名称解析皮肤编号。"""

    translation_arguments = _mapping(_mapping(row.get("translation")).get("arguments"))
    frame_arguments = _mapping(semantic_frame.get("arguments"))
    owner_context = _mapping(slots.get("owner_context"))
    for value in (
        _mapping(frame_arguments.get("skin")).get("token"),
        _mapping(translation_arguments.get("skin")).get("token"),
        owner_context.get("skin_token"),
    ):
        if isinstance(value, str) and (match := SKIN_TOKEN_PATTERN.fullmatch(value)) is not None:
            return int(match.group(1))
    raw_event_name = str(row.get("raw_event_name") or "")
    match = RAW_SKIN_PATTERN.search(raw_event_name)
    return int(match.group(1)) if match is not None else 0


def _build_skin_payload(
    champion: ChampionReference,
    skins: tuple[SkinReference, ...],
    data_dragon_version: str,
) -> list[dict[str, Any]]:
    """构建基础造型与已知皮肤的图片元数据。"""

    base_splash = _splash_url(champion.alias, 0)
    payload = [
        {
            "number": 0,
            "token": "Base",
            "en_name": champion.en_name,
            "zh_name": f"基础造型 · {champion.zh_name}",
            "splash_url": base_splash,
            "splash_skin_number": 0,
            "fallback_url": base_splash,
        }
    ]
    numbers_by_name: dict[str, list[int]] = {}
    for skin in skins:
        numbers_by_name.setdefault(_skin_name_key(skin.en_name), []).append(skin.number)
    for skin in skins:
        splash_number = skin.number
        match = CHROMA_NAME_PATTERN.fullmatch(skin.en_name)
        if match is not None:
            parent_numbers = numbers_by_name.get(_skin_name_key(match.group("base")), [])
            if len(parent_numbers) == 1:
                splash_number = parent_numbers[0]
        payload.append(
            {
                "number": skin.number,
                "token": skin.token,
                "en_name": skin.en_name,
                "zh_name": skin.zh_name,
                "splash_url": _splash_url(champion.alias, splash_number),
                "splash_skin_number": splash_number,
                "fallback_url": base_splash,
            }
        )
    return payload


def _skin_name_key(value: str) -> str:
    """折叠皮肤名空白与大小写，用于色彩变体匹配主皮肤。"""

    return " ".join(value.split()).casefold()


def _write_site_output(
    output_root: Path,
    site_index: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
) -> None:
    """在受控暂存目录完成分片，再替换带生成器标记的站点。"""

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}-", dir=output_root.parent))
    try:
        asset_root = Path(__file__).with_name("demo_assets")
        for filename in ASSET_FILENAMES:
            shutil.copyfile(asset_root / filename, staging_root / filename)
        (staging_root / ".nojekyll").write_text("", encoding="utf-8")
        (staging_root / "data" / "entities").mkdir(parents=True)
        _write_json(staging_root / "data" / "index.json", site_index, pretty=False)
        for document in documents:
            path = str(document["data_path"])
            if re.fullmatch(r"data/entities/[a-f0-9]{24}\.json", path) is None:
                raise DemoSiteError(f"unsafe entity output path: {path}")
            _write_json(
                staging_root / path, {key: value for key, value in document.items() if key != "data_path"}, pretty=False
            )
        (staging_root / GENERATED_MARKER_NAME).write_text(GENERATED_MARKER_VALUE, encoding="utf-8")
        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(staging_root, output_root)
    except Exception:
        # 保留失败现场供检查，不自动清理任务临时产物。
        raise


def _ensure_replaceable_output(output_root: Path) -> None:
    """拒绝替换不属于演示站生成器的目录。"""

    if not output_root.exists():
        return
    if output_root.is_symlink() or not output_root.is_dir():
        raise DemoSiteError(f"output root is not a generated directory: {output_root}")
    marker = output_root / GENERATED_MARKER_NAME
    if not marker.is_file() or marker.read_text(encoding="utf-8") != GENERATED_MARKER_VALUE:
        raise DemoSiteError(f"output root is missing the generated marker: {output_root}")


def _data_dragon_version(reference_manifest: Mapping[str, Any]) -> str:
    """读取并校验 Data Dragon 三段版本号。"""

    source_versions = _mapping(reference_manifest.get("source_versions"))
    data_dragon = _mapping(source_versions.get("data_dragon"))
    version = _required_text(data_dragon, "version", "reference manifest data_dragon")
    if DATA_DRAGON_VERSION_PATTERN.fullmatch(version) is None:
        raise DemoSiteError(f"invalid Data Dragon version: {version!r}")
    return version


def _portrait_url(alias: str, version: str) -> str:
    """构建版本化英雄头像 URL。"""

    return f"https://ddragon.leagueoflegends.com/cdn/{quote(version)}/img/champion/{quote(alias)}.png"


def _splash_url(alias: str, skin_number: int) -> str:
    """构建英雄皮肤原画 URL。"""

    return f"https://ddragon.leagueoflegends.com/cdn/img/champion/splash/{quote(alias)}_{skin_number}.jpg"


def _localized_values(value: Mapping[str, Any], locale: str) -> list[str]:
    """读取指定 locale 的去重非空值。"""

    raw_values = value.get(locale)
    if not isinstance(raw_values, list):
        return []
    return _unique_texts(
        str(item.get("value"))
        for item in raw_values
        if isinstance(item, Mapping) and isinstance(item.get("value"), str) and item.get("value")
    )


def _unique_texts(values: Any) -> list[str]:
    """按大小写不敏感顺序去重文本。"""

    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _read_json_object(path: Path) -> dict[str, Any]:
    """读取必须为对象的 UTF-8 JSON。"""

    if not path.is_file():
        raise DemoSiteError(f"JSON input not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoSiteError(f"invalid JSON input: {path}") from exc
    if not isinstance(value, dict):
        raise DemoSiteError(f"JSON input must contain an object: {path}")
    return value


def _read_yaml_object(path: Path) -> dict[str, Any]:
    """安全读取必须为对象的 UTF-8 YAML。"""

    if not path.is_file():
        raise DemoSiteError(f"YAML input not found: {path}")
    yaml = YAML(typ="safe")
    try:
        value = yaml.load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise DemoSiteError(f"invalid YAML input: {path}") from exc
    if not isinstance(value, dict):
        raise DemoSiteError(f"YAML input must contain an object: {path}")
    return value


def _write_json(path: Path, value: object, *, pretty: bool) -> None:
    """确定性写入 UTF-8 JSON。"""

    text = json.dumps(
        value,
        ensure_ascii=False,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )
    path.write_text(text + "\n", encoding="utf-8")


def _mapping(value: object) -> Mapping[str, Any]:
    """把未知值安全收窄为只读映射。"""

    return value if isinstance(value, Mapping) else {}


def _required_text(value: Mapping[str, Any], key: str, context: str) -> str:
    """读取必需的非空文本字段。"""

    text = value.get(key)
    if not isinstance(text, str) or not text:
        raise DemoSiteError(f"{context} is missing non-empty {key}")
    return text


def _required_int(value: Mapping[str, Any], key: str, context: str) -> int:
    """读取排除布尔值的必需整数字段。"""

    number = value.get(key)
    if isinstance(number, bool) or not isinstance(number, int):
        raise DemoSiteError(f"{context} is missing integer {key}")
    return number


def _optional_text(value: object) -> str | None:
    """把未知值收窄为可选非空文本。"""

    return value if isinstance(value, str) and value else None
