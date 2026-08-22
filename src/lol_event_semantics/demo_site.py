"""从 normalized contract 生成英雄、地图与 Review 静态演示站。"""

from __future__ import annotations

import json
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
from .normalized_lookup import iter_normalized_rows

__all__ = ["DemoBuildSummary", "DemoSiteError", "build_demo_site"]

DEMO_SCHEMA_VERSION = 2
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
MAP_KEY_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
DATA_DRAGON_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")
SKIN_TOKEN_PATTERN = re.compile(r"Skin0*(\d+)\Z", re.IGNORECASE)
RAW_SKIN_PATTERN = re.compile(r"Skin0*(\d+)", re.IGNORECASE)
CHROMA_NAME_PATTERN = re.compile(r"(?P<base>.+?)\s+\([^()]+\)\Z")
MODE_ENTITY_WRAPPERS = frozenset({"boss", "curse", "enemy", "hud", "npc"})
SFX_EVENT_PATTERN = re.compile(r"^(?:play|stop|mute|unmute)_(?:sfx|sxf)_", re.IGNORECASE)
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
REVIEW_CODES = frozenset(code for _key, _label, codes in REVIEW_LAYERS for code in codes)


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
    """生成可由普通静态 HTTP 服务托管的英雄事件与残差审阅站。"""

    normalized_root = normalized_root.resolve()
    validation_json = validation_json.resolve()
    reference_root = reference_root.resolve()
    output_root = output_root.resolve()
    _ensure_replaceable_output(output_root)

    manifest = _read_json_object(normalized_root / "manifest.json")
    reference_manifest = _read_yaml_object(reference_root / "manifest.yaml")
    dataset_id = _required_text(manifest, "dataset_id", "normalized manifest")
    content_digest = _required_text(manifest, "content_digest", "normalized manifest")
    expected_record_count = _required_int(manifest, "record_count", "normalized manifest")
    validation = _read_json_object(validation_json)
    _validate_validation_source(
        normalized_root=normalized_root,
        validation_json=validation_json,
        validation=validation,
        expected_record_count=expected_record_count,
    )
    issues_by_raw = _review_issues_by_raw(validation)
    data_dragon_version = _data_dragon_version(reference_manifest)
    base_game_version = str(_mapping(manifest.get("content_identity")).get("base_game_version") or "unknown")

    champions = _load_champion_references(reference_root / "champions")
    if not champions:
        raise DemoSiteError(f"no base champion references found: {reference_root / 'champions'}")
    champion_by_key = {champion.alias.casefold(): champion for champion in champions}
    compact_aliases = _compact_champion_aliases(champions)
    map_catalog = _load_map_references(reference_root / "maps")
    events_by_alias: dict[str, list[dict[str, Any]]] = {champion.alias: [] for champion in champions}
    map_events_by_key: dict[str, list[dict[str, Any]]] = defaultdict(list)
    map_profiles: dict[str, dict[str, Any]] = {}

    source_record_count = 0
    unassigned_record_count = 0
    residual_records: list[dict[str, Any]] = []
    unassigned_records: list[dict[str, Any]] = []
    matched_issue_raws: set[str] = set()
    for _path, _line_number, row in iter_normalized_rows(normalized_root):
        source_record_count += 1
        raw = _required_text(row, "raw_event_name", "normalized record")
        projected_event = _project_event(row, map_catalog=map_catalog)
        projected_map = _mapping(projected_event.get("map"))
        map_key = str(projected_map.get("key") or "base")
        if map_key != "base":
            if MAP_KEY_PATTERN.fullmatch(map_key) is None:
                raise DemoSiteError(f"unsafe projected map key: {map_key!r}")
            map_events_by_key[map_key].append(projected_event)
            existing_profile = map_profiles.get(map_key)
            if existing_profile is not None and existing_profile != projected_map:
                raise DemoSiteError(f"conflicting projected map profile: {map_key}")
            map_profiles[map_key] = dict(projected_map)
        review_issues = issues_by_raw.get(raw, ())
        if review_issues:
            residual_records.append(_project_review_record(row, map_catalog=map_catalog, issues=review_issues))
            matched_issue_raws.add(raw)
        champion_alias = _resolve_record_champion(row, champion_by_key, compact_aliases)
        if champion_alias is None:
            unassigned_record_count += 1
            unassigned_records.append(_project_review_record(row, map_catalog=map_catalog))
            continue
        events_by_alias[champion_alias].append(projected_event)

    if source_record_count != expected_record_count:
        raise DemoSiteError(
            f"normalized record count mismatch: manifest={expected_record_count} stream={source_record_count}"
        )
    missing_issue_raws = sorted(set(issues_by_raw).difference(matched_issue_raws), key=str.casefold)
    if missing_issue_raws:
        preview = ", ".join(missing_issue_raws[:3])
        raise DemoSiteError(f"validation issues do not match normalized records: {preview}")

    residual_records.sort(key=_review_record_sort_key)
    unassigned_records.sort(key=_review_record_sort_key)

    skins_by_alias = _load_skin_references(reference_root / "skins", champions)
    champion_documents: list[dict[str, Any]] = []
    champion_index: list[dict[str, Any]] = []
    map_documents: list[dict[str, Any]] = []
    map_index: list[dict[str, Any]] = []
    global_mode_counts: Counter[str] = Counter()

    for champion in sorted(champions, key=lambda item: (item.en_name.casefold(), item.alias.casefold())):
        events = sorted(
            events_by_alias[champion.alias],
            key=lambda event: (str(event["raw_event_name"]).casefold(), str(event["record_id"])),
        )
        skins = _build_skin_payload(champion, skins_by_alias.get(champion.alias, ()), data_dragon_version)
        document, index_entry = _build_champion_documents(
            champion,
            skins,
            events,
            dataset_id=dataset_id,
            content_digest=content_digest,
            data_dragon_version=data_dragon_version,
        )
        champion_documents.append(document)
        champion_index.append(index_entry)
        global_mode_counts.update(str(event["mode"]["key"]) for event in events)

    for map_key in sorted(map_profiles, key=lambda key: _map_sort_key(map_profiles[key])):
        events = sorted(
            map_events_by_key[map_key],
            key=lambda event: (str(event["raw_event_name"]).casefold(), str(event["record_id"])),
        )
        document, index_entry = _build_map_documents(
            map_profiles[map_key],
            events,
            dataset_id=dataset_id,
            content_digest=content_digest,
        )
        map_documents.append(document)
        map_index.append(index_entry)

    assigned_event_count = source_record_count - unassigned_record_count
    map_event_count = sum(len(events) for events in map_events_by_key.values())
    site_index = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "product": {
            "name": "峡谷事件语义图鉴",
            "name_en": "Rift Event Atlas",
            "description": "按英雄、皮肤、模式与显式地图浏览音频事件原名和中文语义。",
        },
        "dataset": {
            "dataset_id": dataset_id,
            "content_digest": content_digest,
            "base_game_version": base_game_version,
            "data_dragon_version": data_dragon_version,
        },
        "counts": {
            "source_records": source_record_count,
            "champion_events": assigned_event_count,
            "map_events": map_event_count,
            "unassigned_records": unassigned_record_count,
            "champions": len(champions),
            "maps": len(map_documents),
        },
        "modes": _mode_summaries(global_mode_counts),
        "review": {
            "data_path": "data/review/index.json",
            "residual_records": len(residual_records),
            "unassigned_records": len(unassigned_records),
        },
        "champions": champion_index,
        "maps": map_index,
    }
    review_index = _build_review_index(
        dataset_id=dataset_id,
        content_digest=content_digest,
        residual_records=residual_records,
        unassigned_records=unassigned_records,
    )
    _write_site_output(
        output_root,
        site_index,
        champion_documents,
        map_documents,
        review_index=review_index,
        residual_records=residual_records,
        unassigned_records=unassigned_records,
    )
    return DemoBuildSummary(
        output_root=output_root,
        dataset_id=dataset_id,
        champion_count=len(champions),
        event_count=assigned_event_count,
        map_count=len(map_documents),
        map_event_count=map_event_count,
        unassigned_event_count=unassigned_record_count,
        review_record_count=len(residual_records),
    )


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


def _compact_champion_aliases(champions: Sequence[ChampionReference]) -> dict[str, str]:
    """构建 canonical alias 与英文展示名的无歧义紧凑索引。"""

    result: dict[str, str] = {}
    for champion in champions:
        for value in (champion.alias, champion.en_name):
            key = _compact(value)
            if not key:
                continue
            existing = result.get(key)
            if existing is not None and existing != champion.alias:
                raise DemoSiteError(f"ambiguous compact champion alias {value!r}: {existing!r} and {champion.alias!r}")
            result[key] = champion.alias
    return result


def _resolve_record_champion(
    row: Mapping[str, Any],
    champion_by_key: Mapping[str, ChampionReference],
    compact_aliases: Mapping[str, str],
) -> str | None:
    """按结构化证据和受控模式 token 解析基础英雄。"""

    semantic = _mapping(row.get("semantic"))
    slots = _mapping(semantic.get("slots"))
    semantic_frame = _mapping(row.get("semantic_frame"))
    frame_arguments = _mapping(semantic_frame.get("arguments"))
    translation = _mapping(row.get("translation"))
    translation_arguments = _mapping(translation.get("arguments"))
    candidates = (
        frame_arguments.get("champion"),
        translation_arguments.get("champion"),
        slots.get("mode_actor"),
        slots.get("subject"),
        row.get("entity"),
        slots.get("owner_context"),
    )
    unresolved: list[str] = []
    for candidate in candidates:
        for value in _entity_candidate_values(candidate):
            champion = champion_by_key.get(value.casefold())
            if champion is not None:
                return champion.alias
            unresolved.append(value)

    for value in unresolved:
        match = _match_compact_champion_alias(value, compact_aliases, allow_short_edge_match=True)
        if match is not None:
            return match

    for value in _mode_raw_entity_tokens(row, slots):
        match = _match_compact_champion_alias(value, compact_aliases, allow_short_edge_match=False)
        if match is not None:
            return match
    return None


def _match_compact_champion_alias(
    value: str,
    compact_aliases: Mapping[str, str],
    *,
    allow_short_edge_match: bool,
) -> str | None:
    """返回唯一最长的紧凑英雄别名匹配。"""

    compact_value = _compact(value)
    matches = {
        alias
        for compact_alias, alias in compact_aliases.items()
        if compact_alias
        and (
            compact_value == compact_alias
            or (
                allow_short_edge_match
                and (compact_value.startswith(compact_alias) or compact_value.endswith(compact_alias))
            )
            or (
                len(compact_alias) >= 4
                and (compact_value.startswith(compact_alias) or compact_value.startswith(f"fn{compact_alias}"))
            )
        )
    }
    if not matches:
        return None
    longest = max(len(_compact(alias)) for alias in matches)
    longest_matches = sorted(alias for alias in matches if len(_compact(alias)) == longest)
    return longest_matches[0] if len(longest_matches) == 1 else None


def _mode_raw_entity_tokens(row: Mapping[str, Any], slots: Mapping[str, Any]) -> tuple[str, ...]:
    """提取已确认模式前缀后的受控实体 token。"""

    mode_key = str(_mapping(slots.get("mode_context")).get("key") or "").casefold()
    namespace = next((item for item in MODE_NAMESPACES if item.key == mode_key), None)
    if namespace is None:
        return ()

    raw_event_name = row.get("raw_event_name")
    if not isinstance(raw_event_name, str):
        return ()
    parts = raw_event_name.split("_")
    for index, part in enumerate(parts):
        if part.casefold() == namespace.prefix.casefold():
            following = tuple(token for token in parts[index + 1 :] if token)
            if not following:
                return ()
            if following[0].casefold() in MODE_ENTITY_WRAPPERS and len(following) > 1:
                return following[:2]
            return following[:1]
    return ()


def _entity_candidate_values(value: object) -> tuple[str, ...]:
    """从结构化实体候选中提取可比较文本。"""

    if isinstance(value, str):
        return (value,) if value else ()
    candidate = _mapping(value)
    return tuple(text for key in ("alias", "raw", "label") if isinstance((text := candidate.get(key)), str) and text)


def _validate_validation_source(
    *,
    normalized_root: Path,
    validation_json: Path,
    validation: Mapping[str, Any],
    expected_record_count: int,
) -> None:
    """验证 normalized 与 validator 报告来自同一个标准 dataset run。"""

    if validation_json.parent.parent != normalized_root.parent:
        raise DemoSiteError("normalized root and validation report must belong to the same dataset run")
    summary = _mapping(validation.get("summary"))
    reported_root = Path(_required_text(summary, "normalized_root", "validation summary"))
    if reported_root.name != normalized_root.name or reported_root.parent.name != normalized_root.parent.name:
        raise DemoSiteError("validation report does not describe the same dataset run")
    record_count = _required_int(summary, "record_count", "validation summary")
    if record_count != expected_record_count:
        raise DemoSiteError(
            f"validation record count mismatch: manifest={expected_record_count} validation={record_count}"
        )
    if not isinstance(validation.get("issues"), list):
        raise DemoSiteError("validation report issues must be an array")


def _review_issues_by_raw(validation: Mapping[str, Any]) -> dict[str, tuple[dict[str, str], ...]]:
    """按 raw event 汇总需要进入 SFX 人工审阅队列的 validator 问题。"""

    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    issues = validation.get("issues")
    assert isinstance(issues, list)
    for index, value in enumerate(issues):
        if not isinstance(value, dict):
            raise DemoSiteError(f"validation issue {index} must be an object")
        code = str(value.get("code") or "")
        raw = str(value.get("raw_event_name") or "")
        if code not in REVIEW_CODES or SFX_EVENT_PATTERN.match(raw) is None:
            continue
        grouped[raw].append(
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


def _project_review_record(
    row: Mapping[str, Any],
    *,
    map_catalog: MapReferenceCatalog,
    issues: Sequence[Mapping[str, str]] = (),
) -> dict[str, Any]:
    """投影审阅所需的最小记录，不复制完整 normalized payload。

    Args:
        row: normalized 记录。
        map_catalog: 用于本地化显式地图槽位的参考索引。
        issues: 与记录匹配的 validator 问题。

    Returns:
        可由 Review 页面直接消费的轻量记录。
    """

    raw = _required_text(row, "raw_event_name", "normalized record")
    record_id = _required_text(row, "record_id", f"normalized record {raw}")
    entity = _mapping(row.get("entity"))
    semantic = _mapping(row.get("semantic"))
    slots = _mapping(semantic.get("slots"))
    display = _mapping(row.get("display"))
    translation = _mapping(row.get("translation"))
    semantic_frame = _mapping(row.get("semantic_frame"))
    projected_issues = [dict(issue) for issue in issues]
    projected_map = _project_map(slots.get("map"), map_catalog)
    return {
        "record_id": record_id,
        "raw_event_name": raw,
        "locator": {"record_id": record_id, "raw_event_name": raw},
        "asset_kind": str(row.get("asset_kind") or "unknown"),
        "entity": {
            "kind": str(entity.get("kind") or "unknown"),
            "alias": str(entity.get("alias") or "unknown"),
        },
        "map": projected_map,
        "review_scope": _review_scope(projected_map),
        "family": str(semantic.get("primary_family") or "unknown"),
        "profile": str(semantic.get("profile") or "unknown"),
        "frame_type": str(translation.get("frame_type") or semantic_frame.get("frame_type") or "missing"),
        "support_level": str(row.get("support_level") or "experimental"),
        "review_status": str(row.get("review_status") or "pending"),
        "render_status": str(row.get("render_status") or "missing"),
        "zh_cn_short": _optional_text(display.get("zh_cn_short")),
        "zh_cn_detail": _optional_text(display.get("zh_cn_detail")),
        "reason_code": "validator_issue" if projected_issues else "no_safe_champion_assignment",
        "layers": _issue_layers(projected_issues),
        "issues": projected_issues,
    }


def _issue_layers(issues: Sequence[Mapping[str, str]]) -> list[str]:
    """按固定展示顺序返回一条记录所属的 Review layer。"""

    issue_codes = {str(issue.get("code") or "") for issue in issues}
    return [key for key, _label, codes in REVIEW_LAYERS if issue_codes.intersection(codes)]


def _review_record_sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
    """返回 Review 分片的确定性排序键。"""

    return (str(record.get("raw_event_name") or "").casefold(), str(record.get("record_id") or ""))


def _review_scope(map_value: Mapping[str, Any]) -> str:
    """按显式地图槽位把 Review 记录划入英雄或地图入口。"""

    return "map" if str(map_value.get("key") or "base") != "base" else "champion"


def _build_review_index(
    *,
    dataset_id: str,
    content_digest: str,
    residual_records: Sequence[Mapping[str, Any]],
    unassigned_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """构建残差层、过滤 facet 与队列文件的轻量索引。"""

    issue_counts: Counter[str] = Counter()
    issue_records: Counter[str] = Counter()
    for record in residual_records:
        codes = [str(_mapping(issue).get("code") or "") for issue in record.get("issues", [])]
        issue_counts.update(codes)
        issue_records.update(set(codes))

    layers = []
    for key, label, codes in REVIEW_LAYERS:
        scope_record_counts = {
            scope: sum(
                key in record.get("layers", []) and record.get("review_scope") == scope for record in residual_records
            )
            for scope in ("champion", "map")
        }
        scope_issue_counts = {
            scope: sum(
                str(_mapping(issue).get("code") or "") in codes
                for record in residual_records
                if record.get("review_scope") == scope
                for issue in record.get("issues", [])
            )
            for scope in ("champion", "map")
        }
        layers.append(
            {
                "key": key,
                "label": label,
                "issue_count": sum(issue_counts[code] for code in codes),
                "record_count": sum(key in record.get("layers", []) for record in residual_records),
                "scope_issue_counts": scope_issue_counts,
                "scope_record_counts": scope_record_counts,
            }
        )
    combined_records = [*residual_records, *unassigned_records]
    scopes = [
        {
            "key": scope,
            "label": label,
            "residual_records": sum(record.get("review_scope") == scope for record in residual_records),
            "unassigned_records": sum(record.get("review_scope") == scope for record in unassigned_records),
        }
        for scope, label in (("champion", "英雄"), ("map", "地图"))
    ]
    return {
        "schema_version": DEMO_SCHEMA_VERSION,
        "dataset": {"dataset_id": dataset_id, "content_digest": content_digest},
        "counts": {
            "residual_issues": sum(issue_counts.values()),
            "residual_records": len(residual_records),
            "unassigned_records": len(unassigned_records),
        },
        "scopes": scopes,
        "queues": [
            {
                "key": "residual",
                "label": "SFX 残差",
                "record_count": len(residual_records),
                "scope_counts": _review_scope_counts(residual_records),
                "data_path": "data/review/residuals.json",
            },
            {
                "key": "unassigned",
                "label": "未归属记录",
                "record_count": len(unassigned_records),
                "scope_counts": _review_scope_counts(unassigned_records),
                "data_path": "data/review/unassigned.json",
            },
        ],
        "layers": layers,
        "codes": [
            {"key": code, "issue_count": count, "record_count": issue_records[code]}
            for code, count in sorted(issue_counts.items(), key=lambda item: (-item[1], item[0]))
        ],
        "families": _facet_counts(combined_records, "family"),
        "entities": _entity_counts(combined_records),
        "statuses": _facet_counts(combined_records, "review_status"),
    }


def _review_scope_counts(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """返回 Review 队列在英雄与地图入口中的互斥记录数。"""

    counts = Counter(str(record.get("review_scope") or "champion") for record in records)
    return {scope: counts.get(scope, 0) for scope in ("champion", "map")}


def _facet_counts(records: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
    """汇总一个平坦 Review facet 的记录数。"""

    counts = Counter(str(record.get(key) or "unknown") for record in records)
    return [
        {"key": value, "record_count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _entity_counts(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """汇总 Review 记录的实体 alias。"""

    counts = Counter(str(_mapping(record.get("entity")).get("alias") or "unknown") for record in records)
    return [
        {"key": value, "record_count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].casefold()))
    ]


def _project_event(
    row: Mapping[str, Any],
    *,
    map_catalog: MapReferenceCatalog,
) -> dict[str, Any]:
    """把 normalized 记录投影为演示站事件。

    Args:
        row: normalized 记录。
        map_catalog: 用于本地化显式地图槽位的参考索引。

    Returns:
        英雄事件表消费的稳定投影。
    """

    display = _mapping(row.get("display"))
    entity = _mapping(row.get("entity"))
    semantic = _mapping(row.get("semantic"))
    slots = _mapping(semantic.get("slots"))
    semantic_frame = _mapping(row.get("semantic_frame"))
    warnings = row.get("warnings")
    return {
        "record_id": str(row.get("record_id") or ""),
        "raw_event_name": _required_text(row, "raw_event_name", "normalized record"),
        "zh_cn_short": _optional_text(display.get("zh_cn_short")),
        "zh_cn_detail": _optional_text(display.get("zh_cn_detail")),
        "display_tier": display.get("tier"),
        "asset_kind": str(row.get("asset_kind") or "unknown"),
        "entity": {
            "kind": str(entity.get("kind") or "unknown"),
            "alias": str(entity.get("alias") or "unknown"),
        },
        "frame_type": _optional_text(semantic_frame.get("frame_type")),
        "primary_family": _optional_text(semantic.get("primary_family")),
        "support_level": str(row.get("support_level") or "experimental"),
        "review_status": str(row.get("review_status") or "pending"),
        "render_status": str(row.get("render_status") or "missing"),
        "mode": _project_mode(slots.get("mode_context")),
        "map": _project_map(slots.get("map"), map_catalog),
        "skin_number": _record_skin_number(row, slots, semantic_frame),
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
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


def _build_champion_documents(
    champion: ChampionReference,
    skins: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
    dataset_id: str,
    content_digest: str,
    data_dragon_version: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建单英雄数据分片及其轻量索引项。"""

    support_counts = Counter(str(event["support_level"]) for event in events)
    asset_counts = Counter(str(event["asset_kind"]) for event in events)
    mode_counts = Counter(str(event["mode"]["key"]) for event in events)
    rendered_count = sum(event["render_status"] == "rendered" for event in events)
    profile = {
        "id": champion.champion_id,
        "alias": champion.alias,
        "en_name": champion.en_name,
        "zh_name": champion.zh_name,
        "zh_title": champion.zh_title,
        "nicknames": list(champion.nicknames),
        "search_terms": list(champion.search_terms),
        "portrait_url": _portrait_url(champion.alias, data_dragon_version),
        "base_splash_url": _splash_url(champion.alias, 0),
    }
    stats = {
        "events": len(events),
        "rendered": rendered_count,
        "missing_labels": len(events) - rendered_count,
        "support": dict(sorted(support_counts.items())),
        "asset_kinds": dict(sorted(asset_counts.items())),
        "modes": dict(sorted(mode_counts.items())),
    }
    document = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "dataset": {"dataset_id": dataset_id, "content_digest": content_digest},
        "champion": profile,
        "stats": stats,
        "modes": _mode_summaries(mode_counts),
        "skins": skins,
        "events": events,
    }
    index_entry = {
        **profile,
        "event_count": len(events),
        "stable_count": support_counts.get("stable", 0),
        "experimental_count": support_counts.get("experimental", 0),
        "mode_counts": dict(sorted(mode_counts.items())),
        "skin_count": len(skins),
        "data_path": f"data/champions/{champion.alias}.json",
    }
    return document, index_entry


def _build_map_documents(
    map_profile: Mapping[str, Any],
    events: list[dict[str, Any]],
    *,
    dataset_id: str,
    content_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """构建单地图数据分片及其轻量索引项。"""

    key = _required_text(map_profile, "key", "map profile")
    if key == "base" or MAP_KEY_PATTERN.fullmatch(key) is None:
        raise DemoSiteError(f"unsafe map output key: {key!r}")
    support_counts = Counter(str(event["support_level"]) for event in events)
    asset_counts = Counter(str(event["asset_kind"]) for event in events)
    mode_counts = Counter(str(event["mode"]["key"]) for event in events)
    rendered_count = sum(event["render_status"] == "rendered" for event in events)
    profile = dict(map_profile)
    stats = {
        "events": len(events),
        "rendered": rendered_count,
        "missing_labels": len(events) - rendered_count,
        "support": dict(sorted(support_counts.items())),
        "asset_kinds": dict(sorted(asset_counts.items())),
        "modes": dict(sorted(mode_counts.items())),
    }
    document = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "dataset": {"dataset_id": dataset_id, "content_digest": content_digest},
        "map": profile,
        "stats": stats,
        "modes": _mode_summaries(mode_counts),
        "events": events,
    }
    index_entry = {
        **profile,
        "event_count": len(events),
        "stable_count": support_counts.get("stable", 0),
        "experimental_count": support_counts.get("experimental", 0),
        "mode_counts": dict(sorted(mode_counts.items())),
        "data_path": f"data/maps/{key}.json",
    }
    return document, index_entry


def _map_sort_key(map_profile: Mapping[str, Any]) -> tuple[bool, int, str, str]:
    """优先按显式 Map ID，再按本地化名称与 key 排序。"""

    raw_id = map_profile.get("id")
    map_id = raw_id if isinstance(raw_id, int) and not isinstance(raw_id, bool) else 0
    return (
        raw_id is None,
        map_id,
        str(map_profile.get("label") or "").casefold(),
        str(map_profile.get("key") or "").casefold(),
    )


def _mode_summaries(counts: Mapping[str, int]) -> list[dict[str, Any]]:
    """按固定模式顺序构建非空计数摘要。"""

    known = {namespace.key: namespace for namespace in MODE_NAMESPACES}
    ordered_keys = ["base", *(namespace.key for namespace in MODE_NAMESPACES)]
    ordered_keys.extend(sorted(key for key in counts if key not in {*ordered_keys}))
    result: list[dict[str, Any]] = []
    for key in ordered_keys:
        count = int(counts.get(key, 0))
        if count == 0:
            continue
        if key == "base":
            metadata: dict[str, str | None] = dict(BASE_MODE)
        elif key in known:
            namespace = known[key]
            metadata = {"key": namespace.key, "label": namespace.label, "prefix": namespace.prefix}
        else:
            metadata = {"key": key, "label": key, "prefix": None}
        result.append({**metadata, "event_count": count})
    return result


def _write_site_output(
    output_root: Path,
    site_index: Mapping[str, Any],
    champion_documents: Sequence[Mapping[str, Any]],
    map_documents: Sequence[Mapping[str, Any]],
    *,
    review_index: Mapping[str, Any],
    residual_records: Sequence[Mapping[str, Any]],
    unassigned_records: Sequence[Mapping[str, Any]],
) -> None:
    """在临时目录完成站点写入后原子替换输出。"""

    output_root.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=f".{output_root.name}-", dir=output_root.parent))
    try:
        asset_root = Path(__file__).with_name("demo_assets")
        for filename in ASSET_FILENAMES:
            source = asset_root / filename
            if not source.is_file():
                raise DemoSiteError(f"demo asset is missing: {source}")
            shutil.copyfile(source, staging_root / filename)
        (staging_root / ".nojekyll").write_text("", encoding="utf-8")

        data_root = staging_root / "data"
        champion_root = data_root / "champions"
        map_root = data_root / "maps"
        review_root = data_root / "review"
        champion_root.mkdir(parents=True)
        map_root.mkdir(parents=True)
        review_root.mkdir(parents=True)
        _write_json(data_root / "index.json", site_index, pretty=True)
        for document in champion_documents:
            alias = _required_text(_mapping(document.get("champion")), "alias", "champion document")
            if CHAMPION_ALIAS_PATTERN.fullmatch(alias) is None:
                raise DemoSiteError(f"unsafe champion output alias: {alias!r}")
            _write_json(champion_root / f"{alias}.json", document, pretty=False)
        for document in map_documents:
            key = _required_text(_mapping(document.get("map")), "key", "map document")
            if key == "base" or MAP_KEY_PATTERN.fullmatch(key) is None:
                raise DemoSiteError(f"unsafe map output key: {key!r}")
            _write_json(map_root / f"{key}.json", document, pretty=False)
        dataset = _mapping(review_index.get("dataset"))
        dataset_id = _required_text(dataset, "dataset_id", "review index dataset")
        _write_json(review_root / "index.json", review_index, pretty=True)
        _write_json(
            review_root / "residuals.json",
            {"schema_version": DEMO_SCHEMA_VERSION, "dataset_id": dataset_id, "records": residual_records},
            pretty=False,
        )
        _write_json(
            review_root / "unassigned.json",
            {"schema_version": DEMO_SCHEMA_VERSION, "dataset_id": dataset_id, "records": unassigned_records},
            pretty=False,
        )
        (staging_root / GENERATED_MARKER_NAME).write_text(GENERATED_MARKER_VALUE, encoding="utf-8")

        if output_root.exists():
            shutil.rmtree(output_root)
        os.replace(staging_root, output_root)
    except Exception:
        if staging_root.exists():
            shutil.rmtree(staging_root)
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


def _compact(value: str) -> str:
    """把别名压缩为仅含小写 ASCII 字母数字的键。"""

    return re.sub(r"[^a-z0-9]+", "", value.casefold())


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
