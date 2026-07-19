"""normalized translation 展示词表加载器。

把 ``normalized_translation_builder.py`` 中原本内联的 phase / action /
ability_component / gameplay_event 展示词条外置到 ``rules/display_labels.yaml``
统一维护，并对 ``reference/maps`` 的地图英文真名做进程内缓存，避免每条
记录都重复文件 IO。词表缺词条时由调用方（builder）回落到既有 key/token
兜底，本模块只负责“有则给、无则不给”，不在此处伪造标签。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .reference_collection import load_reference_collection

DEFAULT_DISPLAY_LABELS_PATH = Path("rules/display_labels.yaml")
DEFAULT_REFERENCE_ROOT = Path("reference")


@dataclass(frozen=True, slots=True)
class DisplayLabels:
    """展示词表集合，供 translation builder 按角色取用。

    Attributes:
        phase: phase 规范键到 ``(zh_CN, en_US)`` 展示词的映射。
        action: action 原值（小写）到 ``(zh_CN, en_US)`` 展示词的映射。
        ability_component: ability_component 规范键到中文展示词的映射；
            英文由调用方从 key 派生，不在此维护。
        gameplay_event: gameplay_event 原值（小写）到 ``(zh_CN, en_US)`` 展示词的映射。
        maps: 地图 id 到人工核对中文名的映射（宁缺毋错，只收录高置信项）。
        counterpart: counterpart 英文 label 到官方中文词条的映射
            （T3.2 第一批，宁缺毋错，表外一律 fallback 直出英文）。
        announcer_event: 播报员事件英文词干到中文词条的映射
            （批次5a 第一批，宁缺毋错，表外词干不建 frame）。
        announcer_team: 播报员队伍尾缀规范写法到中文方位词的映射
            （匹配大小写不敏感，由 builder 负责）。
        shop_action: 商店老板动作词到 ``(zh_CN, en_US)`` 展示词的映射
            （批次6d，表外动作词不建 frame）。
        shop_subject: 商店老板对象 token（装备/条件）到中文词条的映射
            （批次6d，宁缺毋错，表外对象不建 frame；英雄/皮肤/地区阵营
            走各自解析链不入此表）。
    """

    phase: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    action: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    ability_component: Mapping[str, str] = field(default_factory=dict)
    gameplay_event: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    maps: Mapping[int, str] = field(default_factory=dict)
    counterpart: Mapping[str, str] = field(default_factory=dict)
    announcer_event: Mapping[str, str] = field(default_factory=dict)
    announcer_team: Mapping[str, str] = field(default_factory=dict)
    shop_action: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    shop_subject: Mapping[str, str] = field(default_factory=dict)


def parse_display_labels(data: Mapping[str, Any]) -> DisplayLabels:
    """把已解析的展示词表 dict 转换为 ``DisplayLabels``。

    Args:
        data: 已从 YAML 解析出的原始 dict，或测试直接注入的等价 dict。

    Returns:
        结构化后的展示词表；畸形词条被跳过而不是抛错。
    """

    # announcer/shop 段是嵌套分节（各自两张子表共用一个域），与其余平铺分节不同。
    announcer = data.get("announcer")
    announcer = announcer if isinstance(announcer, Mapping) else {}
    shop = data.get("shop")
    shop = shop if isinstance(shop, Mapping) else {}
    return DisplayLabels(
        phase=_parse_pair_section(data.get("phase")),
        action=_parse_pair_section(data.get("action")),
        ability_component=_parse_scalar_section(data.get("ability_component")),
        gameplay_event=_parse_pair_section(data.get("gameplay_event")),
        maps=_parse_map_section(data.get("maps")),
        counterpart=_parse_scalar_section(data.get("counterpart")),
        announcer_event=_parse_scalar_section(announcer.get("event")),
        announcer_team=_parse_scalar_section(announcer.get("team")),
        shop_action=_parse_pair_section(shop.get("action")),
        shop_subject=_parse_scalar_section(shop.get("subject")),
    )


def _parse_pair_section(rows: Any) -> dict[str, tuple[str, str]]:
    """解析 ``{key: {zh_CN, en_US}}`` 形式的分节。

    Args:
        rows: 分节原始值。

    Returns:
        key 到 ``(zh_CN, en_US)`` 元组的映射；缺字段的词条被跳过。
    """

    result: dict[str, tuple[str, str]] = {}
    if not isinstance(rows, Mapping):
        return result

    for key, value in rows.items():
        if not isinstance(value, Mapping):
            continue

        zh = value.get("zh_CN")
        en = value.get("en_US")
        if isinstance(zh, str) and isinstance(en, str):
            result[str(key)] = (zh, en)

    return result


def _parse_scalar_section(rows: Any) -> dict[str, str]:
    """解析 ``{key: 中文标签}`` 形式的分节。

    Args:
        rows: 分节原始值。

    Returns:
        key 到中文标签字符串的映射。
    """

    result: dict[str, str] = {}
    if not isinstance(rows, Mapping):
        return result

    for key, value in rows.items():
        if isinstance(value, str):
            result[str(key)] = value

    return result


def _parse_map_section(rows: Any) -> dict[int, str]:
    """解析 ``{地图id: 中文名}`` 形式的 maps 分节。

    Args:
        rows: 分节原始值。

    Returns:
        地图 id（int）到人工核对中文名的映射；非法 key 被跳过。
    """

    result: dict[int, str] = {}
    if not isinstance(rows, Mapping):
        return result

    for key, value in rows.items():
        if not isinstance(value, str):
            continue

        try:
            map_id = int(key)
        except (TypeError, ValueError):
            continue

        result[map_id] = value

    return result


def load_display_labels(source: Path | str | Mapping[str, Any] | None = None) -> DisplayLabels:
    """从 YAML 路径或已注入字典加载展示词表。

    Args:
        source: 为 ``None`` 时读取默认 ``rules/display_labels.yaml``；为
            ``Path``/``str`` 时读取该文件；为 dict 时直接按注入内容解析
            （供测试注入替代词表，不做缓存）。

    Returns:
        结构化展示词表。

    Raises:
        ValueError: 指定路径的 YAML 未解码为 mapping。
    """

    if source is None:
        source = DEFAULT_DISPLAY_LABELS_PATH

    if isinstance(source, Mapping):
        return parse_display_labels(source)

    path = Path(source)
    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))
    if data is None:
        data = {}
    if not isinstance(data, Mapping):
        raise ValueError(f"display labels YAML did not decode to a mapping: {path}")

    return parse_display_labels(data)


@lru_cache(maxsize=1)
def get_display_labels() -> DisplayLabels:
    """返回进程内缓存的默认展示词表。

    Returns:
        从默认 ``rules/display_labels.yaml`` 加载的展示词表；同一进程内只读一次文件。
    """

    return load_display_labels(DEFAULT_DISPLAY_LABELS_PATH)


def load_reference_map_names(reference_root: Path | str = DEFAULT_REFERENCE_ROOT) -> dict[int, str]:
    """读取 ``reference/maps`` 的地图英文真名，按 ``entity.id`` 建索引。

    Args:
        reference_root: 仓库内部 reference 合集根目录。

    Returns:
        地图 id 到 ``entity.names.en_US`` 首个 value 的映射；缺失该语言的
        地图不出现在返回结果中。
    """

    collection = load_reference_collection(reference_root)
    names: dict[int, str] = {}
    for row in collection.maps.values():
        entity = row.get("entity")
        if not isinstance(entity, dict):
            continue

        map_id = entity.get("id")
        if not isinstance(map_id, int):
            continue

        value = _first_en_name(entity)
        if value is not None:
            names[map_id] = value

    return names


def _first_en_name(entity: Mapping[str, Any]) -> str | None:
    """读取 reference entity 的 ``names.en_US`` 首个 value。

    Args:
        entity: reference 地图实体字典。

    Returns:
        首个英文真名；无该语言词条时返回 ``None``。
    """

    names = entity.get("names")
    if not isinstance(names, Mapping):
        return None

    rows = names.get("en_US")
    if not isinstance(rows, list):
        return None

    for row in rows:
        if isinstance(row, Mapping) and isinstance(row.get("value"), str):
            return row["value"]

    return None


@lru_cache(maxsize=1)
def get_reference_map_names() -> dict[int, str]:
    """返回进程内缓存的 reference 地图英文真名索引。

    Returns:
        地图 id 到英文真名的映射；同一进程内只读一次 reference 目录。
    """

    return load_reference_map_names(DEFAULT_REFERENCE_ROOT)
