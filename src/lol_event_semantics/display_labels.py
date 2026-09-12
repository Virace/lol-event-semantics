"""normalized translation 展示词表加载器。

把 ``normalized_translation_builder.py`` 中原本内联的 phase / action /
ability_component / gameplay_event 展示词条外置到 ``rules/display_labels.yaml``
统一维护，并对 ``reference/maps`` 的地图英文真名做进程内缓存，避免每条
记录都重复文件 IO。词表缺词条时由调用方（builder）回落到既有 key/token
兜底，本模块只负责“有则给、无则不给”，不在此处伪造标签。
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from .reference_collection import load_reference_collection
from .rules import RuleBook, rule_key

DEFAULT_DISPLAY_LABELS_PATH = Path("rules/display_labels.yaml")
DEFAULT_REFERENCE_ROOT = Path("reference")
DEFAULT_RULES_ROOT = Path("rules")
DEFAULT_OVERRIDES_ROOT = Path("overrides")
_ACTIVE_DISPLAY_LABELS: ContextVar[DisplayLabels | None]
_ACTIVE_REFERENCE_MAP_NAMES: ContextVar[Mapping[int, str] | None]


def map_system_entity_label(alias: str, labels: Mapping[str, str] | None = None) -> str | None:
    """读取人工核对的系统实体词条；显式空索引不回落默认规则。"""

    active = labels if labels is not None else get_map_system_entity_labels()
    return active.get(rule_key(alias))


def load_map_system_entity_labels(rules_root: Path, overrides_root: Path) -> dict[str, str]:
    """从本次构建的规则读取 owner=entity 的系统实体词条。"""

    rulebook = RuleBook.load(rules_root, overrides_root)
    return {
        token: alias.label
        for (scope, owner, token), alias in rulebook.contextual_aliases.items()
        if scope == "sfx" and owner == rule_key("entity")
    }


@lru_cache(maxsize=1)
def get_map_system_entity_labels() -> dict[str, str]:
    """为未传上下文的兼容调用缓存默认系统实体词条。"""

    return load_map_system_entity_labels(DEFAULT_RULES_ROOT, DEFAULT_OVERRIDES_ROOT)


@dataclass(frozen=True, slots=True)
class DisplayLabels:
    """展示词表集合，供 translation builder 按角色取用。

    Attributes:
        phase: phase 规范键到 ``(zh_CN, en_US)`` 展示词的映射。
        action: action 原值（小写）到 ``(zh_CN, en_US)`` 展示词的映射。
        attack: attack 规范键到 ``(zh_CN, en_US)`` 展示词的映射。
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
        announcer_subject: 播报主体键（You/Hero{队伍}）到中文主体词的映射
            （批次6e，You/Hero 三态语义经字符串表核验）。
        announcer_mode: 播报语音包/模式包名段到中文前缀词的映射
            （批次6e mode role，表外包名不出 mode argument）。
        shop_action: 商店老板动作词到 ``(zh_CN, en_US)`` 展示词的映射
            （批次6d，表外动作词不建 frame）。
        shop_subject: 商店老板对象 token（装备/条件）到中文词条的映射
            （批次6d，宁缺毋错，表外对象不建 frame；英雄/皮肤/地区阵营
            走各自解析链不入此表）。
        voice_qualifier: 自身动作语音 qualifier 到 ``(zh_CN, en_US)``
            展示词的映射。仅收录已核验的中文；表外项由 builder 明确标记
            为待审，不能伪装成稳定的泛化动作。
        augments: Arena/ARAM 增益与斗魂竞技场道具 Code 到 ``(zh_CN, en_US)``
            官方名的映射（客户端字符串表命名 key 中英同键对照；宁缺毋错，
            表外 Code 不建 frame）。
        system_sfx_exact: 已审阅 HUD/Gameplay cue 相对路径到中文效果名的
            精确映射；路径由 builder 统一小写并移除分组壳及尾部生命周期。
        system_sfx_context: 系统音效 owner/mode 工程名到用户可见中文上下文；
            表外 owner 合法缺席，不把工程名带进中文。
        system_sfx_item_action: ``装备 ID/动作词干`` 到双语动作标签的精确映射；
            只承接用户审阅过的组合，不把某个装备的动作泛化到其他数字 owner。
        system_sfx_item_event: 完整 raw 事件名到装备审阅决策，保留推断来源与置信度。
    """

    phase: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    action: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    attack: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    ability_component: Mapping[str, str] = field(default_factory=dict)
    gameplay_event: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    maps: Mapping[int, str] = field(default_factory=dict)
    counterpart: Mapping[str, str] = field(default_factory=dict)
    announcer_event: Mapping[str, str] = field(default_factory=dict)
    announcer_team: Mapping[str, str] = field(default_factory=dict)
    announcer_subject: Mapping[str, str] = field(default_factory=dict)
    announcer_mode: Mapping[str, str] = field(default_factory=dict)
    shop_action: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    shop_subject: Mapping[str, str] = field(default_factory=dict)
    voice_qualifier: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    augments: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    system_sfx_exact: Mapping[str, str] = field(default_factory=dict)
    system_sfx_context: Mapping[str, str] = field(default_factory=dict)
    system_sfx_item_action: Mapping[str, tuple[str, str]] = field(default_factory=dict)
    system_sfx_item_event: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


_ACTIVE_DISPLAY_LABELS = ContextVar("active_display_labels", default=None)
_ACTIVE_REFERENCE_MAP_NAMES = ContextVar("active_reference_map_names", default=None)


@contextmanager
def use_display_sources(
    labels: DisplayLabels,
    reference_map_names: Mapping[int, str],
) -> Iterator[None]:
    """在当前构建调用链内注入显式 rules/reference 展示索引。"""

    labels_token = _ACTIVE_DISPLAY_LABELS.set(labels)
    maps_token = _ACTIVE_REFERENCE_MAP_NAMES.set(reference_map_names)
    try:
        yield
    finally:
        _ACTIVE_REFERENCE_MAP_NAMES.reset(maps_token)
        _ACTIVE_DISPLAY_LABELS.reset(labels_token)


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
    system_sfx = data.get("system_sfx")
    system_sfx = system_sfx if isinstance(system_sfx, Mapping) else {}
    item_events = system_sfx.get("item_event")
    item_events = item_events if isinstance(item_events, Mapping) else {}
    return DisplayLabels(
        phase=_parse_pair_section(data.get("phase")),
        action=_parse_pair_section(data.get("action")),
        attack=_parse_pair_section(data.get("attack")),
        ability_component=_parse_scalar_section(data.get("ability_component")),
        gameplay_event=_parse_pair_section(data.get("gameplay_event")),
        maps=_parse_map_section(data.get("maps")),
        counterpart=_parse_scalar_section(data.get("counterpart")),
        announcer_event=_parse_scalar_section(announcer.get("event")),
        announcer_team=_parse_scalar_section(announcer.get("team")),
        announcer_subject=_parse_scalar_section(announcer.get("subject")),
        announcer_mode=_parse_scalar_section(announcer.get("mode")),
        shop_action=_parse_pair_section(shop.get("action")),
        shop_subject=_parse_scalar_section(shop.get("subject")),
        voice_qualifier=_parse_pair_section(data.get("voice_qualifier")),
        augments=_parse_pair_section(data.get("augments")),
        system_sfx_exact=_parse_scalar_section(system_sfx.get("exact")),
        system_sfx_context=_parse_scalar_section(system_sfx.get("context")),
        system_sfx_item_action=_parse_pair_section(system_sfx.get("item_action")),
        system_sfx_item_event={
            str(key): dict(value) for key, value in item_events.items() if isinstance(value, Mapping)
        },
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


def get_display_labels() -> DisplayLabels:
    """返回进程内缓存的默认展示词表。

    Returns:
        从默认 ``rules/display_labels.yaml`` 加载的展示词表；同一进程内只读一次文件。
    """

    active = _ACTIVE_DISPLAY_LABELS.get()
    return active if active is not None else _get_default_display_labels()


@lru_cache(maxsize=1)
def _get_default_display_labels() -> DisplayLabels:
    """加载并缓存默认 rules 展示词表。"""

    return load_display_labels(DEFAULT_DISPLAY_LABELS_PATH)


def load_reference_map_names(reference_root: Path | str = DEFAULT_REFERENCE_ROOT) -> dict[int, str]:
    """读取 ``reference/maps`` 的地图英文真名，按 ``entity.id`` 建索引。

    Args:
        reference_root: 仓库内部 reference 合集根目录。

    Returns:
        地图 id 到 ``entity.names.en_US`` 首个 value 的映射；缺失该语言的
        地图不出现在返回结果中。
    """

    return map_names_from_collection(load_reference_collection(reference_root))


def map_names_from_collection(collection: Any) -> dict[int, str]:
    """从 reference 合集提取地图 id → 英文真名索引。"""

    names: dict[int, str] = {}
    for row in collection.maps.values():
        entity = row.get("entity")
        if not isinstance(entity, dict):
            continue

        map_id = entity.get("id")
        if not isinstance(map_id, int):
            continue

        value = _first_name(entity, "en_US")
        if value is not None:
            names[map_id] = value

    return names


def load_reference_item_names(reference_root: Path | str = DEFAULT_REFERENCE_ROOT) -> dict[int, dict[str, str]]:
    """读取 ``reference/items`` 的装备名，按 ``entity.id`` 建双语索引。

    Args:
        reference_root: 仓库内部 reference 合集根目录。

    Returns:
        装备 id 到 ``{language: 首个名称 value}`` 的映射；无任何语言词条的
        装备不出现在返回结果中。
    """

    return item_names_from_collection(load_reference_collection(reference_root))


def item_names_from_collection(collection: Any) -> dict[int, dict[str, str]]:
    """从 reference 合集提取装备 id → 双语名称索引。"""

    names: dict[int, dict[str, str]] = {}
    for row in collection.items.values():
        entity = row.get("entity")
        if not isinstance(entity, dict):
            continue

        item_id = entity.get("id")
        if not isinstance(item_id, int):
            continue

        labels = {
            language: value for language in ("zh_CN", "en_US") if (value := _first_name(entity, language)) is not None
        }
        if labels:
            names[item_id] = labels

    return names


@lru_cache(maxsize=1)
def get_reference_item_names() -> dict[int, dict[str, str]]:
    """返回进程内缓存的默认装备双语名索引。

    与地图真名索引共享同一次默认合集加载，避免重复解析全量 reference。
    """

    return item_names_from_collection(_default_reference_collection())


@lru_cache(maxsize=1)
def _default_reference_collection() -> Any:
    """进程内缓存的默认 reference 合集（供展示索引共用，只读约定）。"""

    return load_reference_collection(DEFAULT_REFERENCE_ROOT)


def _first_name(entity: Mapping[str, Any], language: str) -> str | None:
    """读取 reference entity 指定语言 names 的首个 value。

    Args:
        entity: reference 实体字典。
        language: 语言键。

    Returns:
        首个名称 value；无该语言词条时返回 ``None``。
    """

    names = entity.get("names")
    if not isinstance(names, Mapping):
        return None

    rows = names.get(language)
    if not isinstance(rows, list):
        return None

    for row in rows:
        value = row.get("value") if isinstance(row, Mapping) else None
        if isinstance(value, str):
            return value

    return None


def get_reference_map_names() -> dict[int, str]:
    """返回进程内缓存的 reference 地图英文真名索引。

    与装备名索引共享同一次默认合集加载；同一进程内只读一次 reference 目录。
    """

    active = _ACTIVE_REFERENCE_MAP_NAMES.get()
    if active is not None:
        return active if isinstance(active, dict) else dict(active)
    return _get_default_reference_map_names()


@lru_cache(maxsize=1)
def _get_default_reference_map_names() -> dict[int, str]:
    """加载并缓存默认 reference 地图英文名索引。"""

    return map_names_from_collection(_default_reference_collection())
