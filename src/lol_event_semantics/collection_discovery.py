"""从同轮大厅目录和 GAME WAD 目录发现可采集的英雄、皮肤与地图。"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from league_tools import WAD  # type: ignore[import-untyped]

from .game_manifest_wads import normalize_wad_path

_CHAMPION_WAD = re.compile(r"^data/final/champions/([a-z0-9_]+)\.wad\.client$", re.IGNORECASE)
_MAP_WAD = re.compile(
    r"^data/final/maps/shipping/(?:(common|map\d+)/)?(common|map\d+)\.wad\.client$",
    re.IGNORECASE,
)
_ALIAS = re.compile(r"^[A-Za-z0-9_]+$")


class CollectionDiscoveryError(ValueError):
    """当前目录无法证明采集范围完整。"""


@dataclass(frozen=True, slots=True)
class CollectionTarget:
    """一个实体的实际 BIN 清单；entity 是 raw round 使用的数字 ID。"""

    group: Literal["champions", "maps"]
    entity: str
    alias: str
    wad_path: str
    bin_paths: tuple[str, ...]


def discover_targets(
    extractor: Any,
    snapshot_root: Path,
    *,
    champions: tuple[str, ...] | None = None,
    maps: tuple[str, ...] | None = None,
) -> list[CollectionTarget]:
    """将同轮 LCU 目录与 GAME WAD 内实际条目相交，生成确定性的采集清单。

    Args:
        extractor: riotmanifest WADExtractor，提供 manifest.files 和 get_wad_header。
        snapshot_root: 快照仓库根目录或包含目录 JSON 的 global/default/v1 目录。
        champions: 英雄别名或数字 ID；None 表示全部实际 WAD，空元组关闭英雄采集。
        maps: 地图数字 ID、MapNN 或 Common；None 表示全部非 TFT 地图，空元组关闭。

    Returns:
        按 group、数字实体 ID 排序的清单，不包含不存在于当前 WAD 的炫彩候选。

    Raises:
        CollectionDiscoveryError: 当前 WAD 无目录身份、请求不存在，或必要 BIN 缺失。

    调用方负责先核对快照与 GAME/LCU 属于同轮版本。本函数不下载快照、写入文件，
    也不依赖累计 reference；读取远程 WAD 目录时由提取器按需获取文件头。
    """
    root = _catalog_root(snapshot_root)
    champion_wads, map_wads = _index_wads(extractor)
    targets: list[CollectionTarget] = []
    if champions != ():
        targets.extend(_discover_champions(extractor, root, champion_wads, champions))
    if maps != ():
        targets.extend(_discover_maps(extractor, root, map_wads, maps))
    return sorted(targets, key=lambda target: (target.group, int(target.entity)))


def _catalog_root(root: Path) -> Path:
    """接受快照根目录与直接的默认语言目录。"""
    if (root / "champion-summary.json").is_file():
        return root
    nested = root / "global" / "default" / "v1"
    if nested.is_dir():
        return nested
    raise CollectionDiscoveryError("快照缺少 global/default/v1 目录或 champion-summary.json")


def _read_json(path: Path) -> Any:
    """读取目录 JSON，并保留原始读取错误链。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CollectionDiscoveryError(f"无法读取采集目录：{path.name}") from exc


def _index_wads(extractor: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """只索引真实基础 WAD，忽略音频语言包和 TFT 英雄容器。"""
    champions: dict[str, Any] = {}
    maps: dict[str, Any] = {}
    for file in extractor.manifest.files.values():
        path = normalize_wad_path(file.name)
        if path is None:
            continue
        champion = _CHAMPION_WAD.fullmatch(path)
        if champion:
            alias = champion.group(1).lower()
            if not alias.startswith("tft"):
                _add_wad(champions, alias, file)
            continue
        map_match = _MAP_WAD.fullmatch(path)
        if map_match:
            directory, alias = map_match.groups()
            if directory is not None and directory.lower() != alias.lower():
                raise CollectionDiscoveryError(f"地图 WAD 目录与文件身份不一致：{path}")
            entity = "0" if alias.lower() == "common" else str(int(alias[3:]))
            _add_wad(maps, entity, file)
    return champions, maps


def _add_wad(index: dict[str, Any], key: str, file: Any) -> None:
    """拒绝同一实体的多个基础 WAD，避免以遍历顺序选择来源。"""
    if key in index:
        raise CollectionDiscoveryError(f"实体 {key} 对应多个基础 WAD")
    index[key] = file


def _entity_id(row: dict[str, Any], *, allow_zero: bool = False) -> int:
    """校验来源目录中的整数身份，拒绝布尔值和负数。"""
    value = row.get("id")
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise CollectionDiscoveryError(f"来源目录包含无效数字 ID：{value!r}")
    return value


def _discover_champions(
    extractor: Any, root: Path, wads: dict[str, Any], requested: tuple[str, ...] | None
) -> list[CollectionTarget]:
    """以当轮英雄目录和皮肤目录生成当前实际英雄 WAD 的请求。"""
    summary = _read_json(root / "champion-summary.json")
    if not isinstance(summary, list):
        raise CollectionDiscoveryError("champion-summary.json 必须为数组")
    catalog: dict[str, dict[str, Any]] = {}
    identifiers: set[int] = set()
    for row in summary:
        if not isinstance(row, dict):
            raise CollectionDiscoveryError("英雄目录条目必须为对象")
        if row.get("id") == -1:
            continue
        identifier = _entity_id(row)
        alias = row.get("alias")
        if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
            raise CollectionDiscoveryError(f"英雄 {identifier} 的 alias 无效")
        key = alias.lower()
        if key in catalog or identifier in identifiers:
            raise CollectionDiscoveryError(f"英雄目录身份重复：{alias}")
        catalog[key] = row
        identifiers.add(identifier)
    selected = _select_wads(wads, requested, {str(row["id"]): key for key, row in catalog.items()}, "英雄")
    unknown = sorted(set(selected) - catalog.keys())
    if unknown:
        raise CollectionDiscoveryError(f"当前英雄 WAD 未在同轮目录中收录：{', '.join(unknown)}")
    skins = _read_json(root / "skins.json")
    if not isinstance(skins, dict) or any(not isinstance(row, dict) for row in skins.values()):
        raise CollectionDiscoveryError("skins.json 必须为皮肤对象字典")
    targets = []
    for key in sorted(selected):
        row = catalog[key]
        identifier, alias = row["id"], row["alias"]
        details = root / "champions" / f"{identifier}.json"
        if details.is_file():
            detail = _read_json(details)
            if not isinstance(detail, dict) or detail.get("id") != identifier or detail.get("alias") != alias:
                raise CollectionDiscoveryError(f"英雄详情与目录身份不一致：{alias}")
            entries = detail.get("skins")
        else:
            entries = [
                skin for skin in skins.values() if type(skin.get("id")) is int and skin["id"] // 1000 == identifier
            ]
        primary, chromas = _skin_indices(entries, identifier, alias)
        paths = {index: f"data/characters/{key}/skins/skin{index}.bin" for index in primary | chromas}
        present = _present_paths(extractor, selected[key], paths.values())
        missing = [paths[index] for index in sorted(primary) if paths[index] not in present]
        if missing:
            raise CollectionDiscoveryError(f"英雄 {alias} 必要皮肤 BIN 缺失：{', '.join(missing)}")
        targets.append(
            CollectionTarget(
                "champions",
                str(identifier),
                alias,
                selected[key].name,
                tuple(paths[index] for index in sorted(paths) if paths[index] in present),
            )
        )
    return targets


def _skin_indices(entries: Any, identifier: int, alias: str) -> tuple[set[int], set[int]]:
    """读取实际皮肤及嵌套炫彩编号，不施加皮肤数量探测上限。"""
    if not isinstance(entries, list) or not entries:
        raise CollectionDiscoveryError(f"英雄 {alias} 缺少同轮皮肤目录")
    primary: set[int] = set()
    chromas: set[int] = set()
    for skin in entries:
        if not isinstance(skin, dict):
            raise CollectionDiscoveryError(f"英雄 {alias} 皮肤条目无效")
        primary.add(_skin_index(skin, identifier, alias))
        variants = skin.get("chromas") or []
        if not isinstance(variants, list):
            raise CollectionDiscoveryError(f"英雄 {alias} 炫彩目录无效")
        for chroma in variants:
            if not isinstance(chroma, dict):
                raise CollectionDiscoveryError(f"英雄 {alias} 炫彩条目无效")
            chromas.add(_skin_index(chroma, identifier, alias))
    if 0 not in primary:
        raise CollectionDiscoveryError(f"英雄 {alias} 皮肤目录缺少基础皮肤")
    return primary, chromas


def _skin_index(skin: dict[str, Any], identifier: int, alias: str) -> int:
    """按 LCU 的英雄 ID × 1000 编码提取皮肤索引并校验归属。"""
    skin_id = _entity_id(skin)
    if skin_id // 1000 != identifier:
        raise CollectionDiscoveryError(f"皮肤 {skin_id} 与英雄 {alias} 的数字身份不一致")
    return skin_id - identifier * 1000


def _discover_maps(
    extractor: Any, root: Path, wads: dict[str, Any], requested: tuple[str, ...] | None
) -> list[CollectionTarget]:
    """按官方地图类型排除 TFT，并保留 Common 公共事件源。"""
    rows = _read_json(root / "maps.json")
    if not isinstance(rows, list):
        raise CollectionDiscoveryError("maps.json 必须为数组")
    catalog: dict[str, dict[str, Any]] = {}
    excluded = {"22"}
    for row in rows:
        if not isinstance(row, dict):
            raise CollectionDiscoveryError("地图目录条目必须为对象")
        entity = str(_entity_id(row, allow_zero=True))
        if entity in catalog:
            raise CollectionDiscoveryError(f"地图目录身份重复：{entity}")
        catalog[entity] = row
        if str(row.get("mapStringId", "")).lower().startswith("tft"):
            excluded.add(entity)
    available = {entity: file for entity, file in wads.items() if entity not in excluded}
    aliases = {("common" if entity == "0" else f"map{entity}"): entity for entity in available}
    selected = _select_wads(available, requested, aliases, "非 TFT 地图")
    unknown = sorted(set(selected) - catalog.keys())
    if unknown:
        raise CollectionDiscoveryError(f"当前地图 WAD 未在同轮目录中收录：{', '.join(unknown)}")
    targets = []
    for entity, file in selected.items():
        alias = "Common" if entity == "0" else f"Map{entity}"
        inner = f"data/maps/shipping/{alias.lower()}/{alias.lower()}.bin"
        if inner not in _present_paths(extractor, file, (inner,)):
            raise CollectionDiscoveryError(f"地图 {alias} 的必要 BIN 缺失：{inner}")
        targets.append(CollectionTarget("maps", entity, alias, file.name, (inner,)))
    return targets


def _select_wads(
    available: dict[str, Any], requested: tuple[str, ...] | None, aliases: dict[str, str], label: str
) -> dict[str, Any]:
    """解析显式选择；未知选择不退化为空采集。"""
    if requested is None:
        if not available:
            raise CollectionDiscoveryError(f"当前 GAME manifest 未发现任何{label} WAD")
        return available
    selected: dict[str, Any] = {}
    for value in requested:
        key = value.lower()
        key = aliases.get(key, key)
        if key not in available:
            raise CollectionDiscoveryError(f"当前 GAME manifest 未发现请求的{label}：{value}")
        selected[key] = available[key]
    return selected


def _present_paths(extractor: Any, file: Any, paths: Iterable[str]) -> set[str]:
    """用实际 WAD 目录的路径哈希证明候选存在，不读取完整 WAD。"""
    header = extractor.get_wad_header(file)
    hashes = {section.path_hash for section in header.files}
    return {path for path in paths if WAD.get_hash(path) in hashes}
