"""从 normalized contract 生成按英雄浏览的静态演示站。"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

from ruamel.yaml import YAML

from .mode_namespace import MODE_NAMESPACES, split_mode_entity_alias
from .normalized_lookup import iter_normalized_rows

__all__ = ["DemoBuildSummary", "DemoSiteError", "build_demo_site"]

DEMO_SCHEMA_VERSION = 1
GENERATED_MARKER_NAME = ".rift-event-atlas-generated"
GENERATED_MARKER_VALUE = "rift-event-atlas-static-site.v1\n"
ASSET_FILENAMES = ("index.html", "styles.css", "app.js")
BASE_MODE = {"key": "base", "label": "通用 / 未指定", "prefix": None}
CHAMPION_ALIAS_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9]*\Z")
DATA_DRAGON_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+\Z")
SKIN_TOKEN_PATTERN = re.compile(r"Skin0*(\d+)\Z", re.IGNORECASE)
RAW_SKIN_PATTERN = re.compile(r"Skin0*(\d+)", re.IGNORECASE)
MODE_ENTITY_WRAPPERS = frozenset({"boss", "curse", "enemy", "hud", "npc"})


class DemoSiteError(ValueError):
    """演示站输入或安全输出边界无效。"""


@dataclass(frozen=True, slots=True)
class DemoBuildSummary:
    """一次演示站构建的可审计摘要。"""

    output_root: Path
    dataset_id: str
    champion_count: int
    event_count: int
    unassigned_event_count: int


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


def build_demo_site(
    *,
    normalized_root: Path,
    reference_root: Path,
    output_root: Path,
) -> DemoBuildSummary:
    """生成可由普通静态 HTTP 服务托管的英雄事件浏览站。"""

    normalized_root = normalized_root.resolve()
    reference_root = reference_root.resolve()
    output_root = output_root.resolve()
    _ensure_replaceable_output(output_root)

    manifest = _read_json_object(normalized_root / "manifest.json")
    reference_manifest = _read_yaml_object(reference_root / "manifest.yaml")
    dataset_id = _required_text(manifest, "dataset_id", "normalized manifest")
    content_digest = _required_text(manifest, "content_digest", "normalized manifest")
    expected_record_count = _required_int(manifest, "record_count", "normalized manifest")
    data_dragon_version = _data_dragon_version(reference_manifest)
    base_game_version = str(_mapping(manifest.get("content_identity")).get("base_game_version") or "unknown")

    champions = _load_champion_references(reference_root / "champions")
    if not champions:
        raise DemoSiteError(f"no base champion references found: {reference_root / 'champions'}")
    champion_by_key = {champion.alias.casefold(): champion for champion in champions}
    compact_aliases = _compact_champion_aliases(champions)
    events_by_alias: dict[str, list[dict[str, Any]]] = {champion.alias: [] for champion in champions}

    source_record_count = 0
    unassigned_record_count = 0
    for _path, _line_number, row in iter_normalized_rows(normalized_root):
        source_record_count += 1
        champion_alias = _resolve_record_champion(row, champion_by_key, compact_aliases)
        if champion_alias is None:
            unassigned_record_count += 1
            continue
        events_by_alias[champion_alias].append(_project_event(row))

    if source_record_count != expected_record_count:
        raise DemoSiteError(
            f"normalized record count mismatch: manifest={expected_record_count} stream={source_record_count}"
        )

    skins_by_alias = _load_skin_references(reference_root / "skins", champions)
    champion_documents: list[dict[str, Any]] = []
    champion_index: list[dict[str, Any]] = []
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
            data_dragon_version=data_dragon_version,
        )
        champion_documents.append(document)
        champion_index.append(index_entry)
        global_mode_counts.update(str(event["mode"]["key"]) for event in events)

    assigned_event_count = source_record_count - unassigned_record_count
    site_index = {
        "schema_version": DEMO_SCHEMA_VERSION,
        "product": {
            "name": "峡谷事件语义图鉴",
            "name_en": "Rift Event Atlas",
            "description": "按英雄、皮肤与模式浏览音频事件原名和中文语义。",
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
            "unassigned_records": unassigned_record_count,
            "champions": len(champions),
        },
        "modes": _mode_summaries(global_mode_counts),
        "champions": champion_index,
    }
    _write_site_output(output_root, site_index, champion_documents)
    return DemoBuildSummary(
        output_root=output_root,
        dataset_id=dataset_id,
        champion_count=len(champions),
        event_count=assigned_event_count,
        unassigned_event_count=unassigned_record_count,
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


def _project_event(row: Mapping[str, Any]) -> dict[str, Any]:
    """把 normalized 记录投影为演示站事件。"""

    display = _mapping(row.get("display"))
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
        "frame_type": _optional_text(semantic_frame.get("frame_type")),
        "primary_family": _optional_text(semantic.get("primary_family")),
        "support_level": str(row.get("support_level") or "experimental"),
        "review_status": str(row.get("review_status") or "pending"),
        "render_status": str(row.get("render_status") or "missing"),
        "mode": _project_mode(slots.get("mode_context")),
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
            "fallback_url": base_splash,
        }
    ]
    for skin in skins:
        payload.append(
            {
                "number": skin.number,
                "token": skin.token,
                "en_name": skin.en_name,
                "zh_name": skin.zh_name,
                "splash_url": _splash_url(champion.alias, skin.number),
                "fallback_url": base_splash,
            }
        )
    return payload


def _build_champion_documents(
    champion: ChampionReference,
    skins: list[dict[str, Any]],
    events: list[dict[str, Any]],
    *,
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

        data_root = staging_root / "data"
        champion_root = data_root / "champions"
        champion_root.mkdir(parents=True)
        _write_json(data_root / "index.json", site_index, pretty=True)
        for document in champion_documents:
            alias = _required_text(_mapping(document.get("champion")), "alias", "champion document")
            if CHAMPION_ALIAS_PATTERN.fullmatch(alias) is None:
                raise DemoSiteError(f"unsafe champion output alias: {alias!r}")
            _write_json(champion_root / f"{alias}.json", document, pretty=False)
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
