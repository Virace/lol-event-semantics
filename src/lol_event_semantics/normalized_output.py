"""对外 normalized 数据生成。"""

from __future__ import annotations

import json
import re
import shutil
from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Mapping

from .decomposition_model import EventDecompositionRecord, SemanticProfile
from .dataset_identity import CONTENT_DIGEST_VERSION, content_digest, describe_payload_files, unmanaged_payload_paths
from .display_labels import (
    get_reference_item_names,
    item_names_from_collection,
    load_display_labels,
    load_reference_item_names,
    map_names_from_collection,
)
from .display_tier import classify_display_tier
from .normalized_contract import (
    apply_normalized_contract,
    build_public_pattern_catalog,
    merge_equivalent_decomposition_records,
)
from .normalized_translation_builder import (
    LABEL_SOURCE_REFERENCE,
    LABEL_SOURCE_SKIN_VOTE,
    SELF_ACTION_VOICE_FAMILIES,
    SHELL_PLAYBACK_VERBS,
    VOICE_EVENT_FAMILIES,
    ChampionLabelEntry,
    asset_kind_words,
    build_translation,
)
from .normalized_translation_model import TranslationBuildContext
from .normalized_translation_renderer import build_compat_display
from .pattern_catalog import PatternPolicy
from .reference_collection import load_reference_collection, read_yaml
from .rules import RuleBook, rule_key
from .tiered_quality import build_quality_summary, load_accepted_corpus

SCHEMA_VERSION = 2
PATTERN_CATALOG_SCHEMA_VERSION = 1
TECHNICAL_DISPLAY_TOKENS = ("Play_vo", "Play_sfx", "cast3D", "2D", "3D", "voProcessing")
DEFAULT_REFERENCE_ROOT = Path("reference")
DEFAULT_OVERRIDES_ROOT = Path("overrides")
DEFAULT_RULES_ROOT = Path("rules")
# rules/contextual/sfx 中承载“实体自身”中文词条分片的保留 owner 值，
# 与 HA.yaml/SRU.yaml 里描述实体内部子 token 的 owner=HA/SRU 互不冲突。
MAP_SYSTEM_ENTITY_OWNER = "entity"
ENTITY_ATTRIBUTION_FILENAME = "entity_attribution.yaml"
ENTITY_ATTRIBUTION_DECISIONS = {"keep", "champion", "downgrade"}
ENTITY_DOWNGRADE_KINDS = {"unknown", "map_or_system"}
# 英雄音频 bank 文件名就是英雄数字 id（raw/events/champions/<id>.txt）。
CHAMPION_SOURCE_FILE_PATTERN = re.compile(r"[\\/]champions[\\/]([0-9]+)\.txt$", re.IGNORECASE)
# 剥离 alias 尾部 SkinNN（含尾随残段）后再尝试精确匹配英雄。
SKIN_SUFFIX_ALIAS_PATTERN = re.compile(r"^(?P<base>[A-Za-z][A-Za-z0-9]*?)skin[0-9]+[A-Za-z0-9]*$", re.IGNORECASE)
# 前后缀挂靠的最小 alias 长度，避免 Vi 这类超短名误挂。
AFFIX_PREFIX_MIN_LENGTH = 3
AFFIX_SUFFIX_MIN_LENGTH = 4


def build_normalized_record(
    record: EventDecompositionRecord,
    *,
    translation_context: TranslationBuildContext | None = None,
    display_context: NormalizedDisplayContext | None = None,
) -> dict[str, object]:
    """把拆解记录转换为对外 normalized 记录。

    Args:
        record: 已完成 profile/reference enrichment 的拆解记录。
        translation_context: translation 构建上下文。
        display_context: legacy display 所需的显式 reference/rules 索引。

    Returns:
        可直接写入 JSONL 的 normalized 记录。
    """

    translation = build_translation(record, translation_context or TranslationBuildContext())
    legacy_display = {
        "zh_cn_short": build_short_display(record, display_context=display_context),
        "zh_cn_detail": build_detail_display(record, display_context=display_context),
        "debug_label": build_debug_label(record),
    }
    display: dict[str, object] = dict(
        build_compat_display(
            translation,
            legacy_display=legacy_display,
            raw_event_name=record.raw_event_name,
        )
    )
    tier = classify_display_tier(record, has_translation=translation is not None)
    display["tier"] = tier
    if tier == 3:
        # Tier3 纯技术事件退出短文案：不再伪造“<alias>(音效|语音)?事件”这类
        # 兜底用户文案；zh_cn_detail/debug_label 继续保留既有生成逻辑，
        # 不因此丢失任何已解析 token（无损原则）。
        display["zh_cn_short"] = None
    normalized: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "raw_event_name": record.raw_event_name,
        "game_version": record.game_version,
        "entity": entity_key(record),
        "semantic": {
            "profile": record.profile.semantic_profile.value,
            "primary_family": record.profile.primary_family,
            "slots": record.semantic_slots,
            "confidence": record.confidence.value,
            "requires_context": record.profile.requires_context,
        },
        "display": display,
        "quality": {
            "unknown_count": len(record.unknowns),
            "candidate_count": len(record.candidates),
            "diagnostics": list(record.diagnostics),
        },
        "source_paths": list(record.source_paths),
    }

    if translation is not None:
        normalized["translation"] = translation

    return normalized


def entity_key(record: EventDecompositionRecord) -> dict[str, str]:
    """返回 normalized 实体键。

    Args:
        record: 拆解记录。

    Returns:
        包含实体类型和别名的稳定键。
    """

    subject = resolved_entity_subject(record)
    if subject is not None:
        kind, alias = subject
    else:
        kind = record.owner.kind if record.owner.kind != "unknown" else record.scope
        alias = record.owner.alias or record.owner.raw_owner_token or record.scope or "unknown"

    if kind == "map_or_system":
        item = item_entity_from_alias(alias)
        if item is not None:
            return item

    return {"kind": sanitize_entity_kind(kind), "alias": sanitize_entity_alias(alias)}


ITEM_ALIAS_PATTERN = re.compile(r"^(\d+)(?:\D.*)?$")


def item_entity_from_alias(alias: str, known_item_ids: Iterable[int] | None = None) -> dict[str, str] | None:
    """把 map/系统域的数字（或数字+状态尾缀）别名解析为装备实体。

    装备以数字 id 为规范身份；尾缀（Shield/Buff/Active 等）是装备效果的
    状态语义，不进实体身份，原始别名保留在 raw_alias（信息无损）。仅当
    数字部分能在 reference 装备表中查到时才归入装备域（宁缺毋错），表外
    数字维持原实体分类。

    Args:
        alias: map/系统域实体别名。
        known_item_ids: 可注入的已知装备 id 集合；缺省读取进程内缓存的
            reference 装备名索引。

    Returns:
        装备实体键；不是装备别名时返回 None。
    """

    match = ITEM_ALIAS_PATTERN.match(alias)
    if match is None:
        return None

    item_id = int(match.group(1))
    ids = known_item_ids if known_item_ids is not None else get_reference_item_names()
    if item_id not in ids:
        return None

    entity = {"kind": "item", "alias": str(item_id)}
    if alias != str(item_id):
        entity["raw_alias"] = alias

    return entity


def item_entity_label(
    alias: str,
    item_names: Mapping[int, Mapping[str, str]] | None = None,
) -> str | None:
    """把装备别名渲染成 reference 装备名（zh 优先，en 兜底）。

    Args:
        alias: map/系统域实体别名。
        item_names: 可选的显式装备名索引；缺省保留旧 helper 行为。

    Returns:
        装备名；别名不是可认领装备时返回 None。
    """

    names = item_names if item_names is not None else get_reference_item_names()
    entity = item_entity_from_alias(alias, names)
    if entity is None:
        return None

    labels = names[int(entity["alias"])]
    return labels.get("zh_CN") or labels.get("en_US")


def resolved_entity_subject(record: EventDecompositionRecord) -> tuple[str, str] | None:
    """返回已解析出的真实实体主体。

    Args:
        record: 拆解记录。

    Returns:
        `(kind, alias)`；未解析出主体时返回 None。
    """

    subject = record.semantic_slots.get("subject")
    if isinstance(subject, dict):
        kind = str(subject.get("kind") or record.owner.kind or record.scope or "unknown")
        alias = subject.get("alias") or subject.get("name") or subject.get("label")
        if alias:
            return (kind, str(alias))

    subject_hint = record.semantic_slots.get("subject_hint")
    if subject_hint:
        return ("champion", str(subject_hint))

    owner_context = record.semantic_slots.get("owner_context")
    if isinstance(owner_context, dict):
        kind = str(owner_context.get("kind") or record.owner.kind or record.scope or "unknown")
        alias = owner_context.get("alias") or owner_context.get("raw_owner_token")
        if alias:
            return (kind, str(alias))

    return None


def sanitize_entity_kind(kind: str | None) -> str:
    """清洗实体类型。

    Args:
        kind: 原始实体类型。

    Returns:
        可用于目录分组的实体类型。
    """

    value = (kind or "unknown").strip().lower().replace(" ", "_")
    return value or "unknown"


def sanitize_entity_alias(alias: str | None) -> str:
    """清洗实体别名。

    Args:
        alias: 原始实体别名。

    Returns:
        可用于文件名的实体别名。
    """

    value = (alias or "unknown").strip()
    value = value.replace("\\", "_").replace("/", "_").replace(":", "_")
    return value or "unknown"


def build_short_display(
    record: EventDecompositionRecord,
    *,
    display_context: NormalizedDisplayContext | None = None,
) -> str:
    """生成普通用户短展示文案。

    Args:
        record: 拆解记录。
        display_context: legacy display 所需的显式 reference/rules 索引。

    Returns:
        面向下游用户的短中文说明，Stop/Mute 壳动词以停止/静音收尾。
    """

    return (
        f"{build_short_display_core(record, display_context=display_context)}{playback_verb_suffix(record.shell.verb)}"
    )


def playback_verb_suffix(verb: object) -> str:
    """返回壳动词对应的中文文案后缀。

    措辞唯一来源是 translation builder 的 SHELL_PLAYBACK_VERBS，legacy 路径
    只取 zh 侧，保证两条展示路径逐字节一致。

    Args:
        verb: 事件外壳动词。

    Returns:
        stop/mute/unmute 返回停止/静音/取消静音；默认播放语义返回空串。
    """

    if not isinstance(verb, str):
        return ""

    labels = SHELL_PLAYBACK_VERBS.get(verb.lower())
    return labels[0] if labels is not None else ""


def build_short_display_core(
    record: EventDecompositionRecord,
    *,
    display_context: NormalizedDisplayContext | None = None,
) -> str:
    """按事件族生成不含壳动词后缀的短文案主体。

    Args:
        record: 拆解记录。
        display_context: legacy display 所需的显式 reference/rules 索引。

    Returns:
        短中文说明主体。
    """

    slots = record.semantic_slots
    owner = display_owner(record, display_context=display_context)
    spell_key = slots.get("spell_key") or slots.get("spell_key_hint")
    phase = slots.get("phase")

    if mode_key(slots.get("mode_context")) == "ultimate_spellbook" and spell_key == "r":
        champion = display_subject(slots) or owner
        return compact_display(f"终极魔典 {champion}大招{phase_label(phase)}")

    if record.profile.primary_family == "skin_idle_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        variant = skin_idle_variant_label(slots)

        if variant is not None:
            return compact_display(f"{owner}{skin} {variant}")

        component = skin_idle_component_label(slots.get("idle_component"), spell_key)
        return compact_display(f"{owner}{skin} {component}待机{skin_idle_phase_label(phase)}")

    if record.profile.primary_family == "skin_action_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        action = skin_action_label(slots.get("action"))
        return compact_display(f"{owner}{skin} {action}{skin_action_phase_label(phase)}")

    if record.profile.primary_family == "skin_audio_transition":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        return compact_display(
            f"{owner}{skin} 音乐风格{slots.get('genre_index')}过渡{transition_phase_label(slots.get('transition_phase'))}"
        )

    if record.profile.primary_family == "skin_named_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        state = skin_named_state_label(slots)
        return compact_display(f"{owner}{skin} {state}{skin_action_phase_label(phase)}")

    if record.profile.primary_family == "champion_status_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        state = champion_status_label(slots.get("status_key"))
        return compact_display(f"{owner}{skin} {state}{skin_action_phase_label(phase)}")

    if record.profile.primary_family == "gameplay_phase_control":
        event = gameplay_event_label(slots.get("gameplay_event"))
        action = phase_action_label(slots.get("phase_action"))
        return compact_display(f"Map{slots.get('map_id')} {event}{action}")

    modifier = first_spell_modifier(slots)
    if modifier is not None and modifier.get("display"):
        return compact_display(f"{owner}{modifier['display']}")

    if spell_key:
        return compact_display(f"{owner} {str(spell_key).upper()} 技能{phase_label(phase)}")

    if record.profile.primary_family == "move":
        return compact_display(f"{owner}移动")

    voice_action = VOICE_EVENT_FAMILIES.get(record.profile.primary_family)
    if voice_action is not None and record.profile.primary_family not in SELF_ACTION_VOICE_FAMILIES:
        # 与 builder 的 voice_event frame 同措辞：动作词 + asset kind 亚词
        # （嘲讽语音/攻击音效）。此前该 legacy 路径缺资产类型字样；当前全量
        # 数据中这五个家族均有 translation frame 覆盖，本分支仅作兜底。
        # 批次3 家族刻意跳过本分支：它们仅 vo 记录建 frame，剩余非 vo 兜底
        # 记录（Tier3 sfx 死亡/回城/重生等）维持 family_label 既有措辞，
        # 避免 display 漂移（SPEC 硬门：范围外记录 display 零变化）。
        return compact_display(f"{owner}{voice_action}{audio_kind_label(record.shell.asset_kind)}")

    if record.profile.primary_family == "emote":
        return compact_display(f"{owner}表情{audio_kind_label(record.shell.asset_kind)}")

    if record.profile.primary_family == "skin_recall_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        return compact_display(f"{owner}{skin} 回城{skin_recall_phase_label(phase)}")

    if record.profile.semantic_profile == SemanticProfile.MAP_OR_SYSTEM:
        return compact_display(f"{owner}{map_system_fallback_label(record)}")

    return compact_display(f"{owner}{family_label(record.profile.primary_family)}")


def build_detail_display(
    record: EventDecompositionRecord,
    *,
    display_context: NormalizedDisplayContext | None = None,
) -> str:
    """生成详细展示文案。

    Args:
        record: 拆解记录。
        display_context: legacy display 所需的显式 reference/rules 索引。

    Returns:
        比短文案略完整的中文说明。
    """

    short = build_short_display(record, display_context=display_context)
    slots = record.semantic_slots
    details: list[str] = []

    if record.owner.skin_token:
        details.append(f"皮肤 {record.owner.skin_token}")

    mode = mode_label(slots.get("mode_context"))
    if mode and mode not in short:
        details.append(mode)

    if details:
        return compact_display(f"{short}（{'，'.join(details)}）")

    return short


def build_debug_label(record: EventDecompositionRecord) -> str:
    """生成调试标签。

    Args:
        record: 拆解记录。

    Returns:
        保留原始拆解 token 的调试说明。
    """

    atoms = " / ".join(atom.raw for atom in record.lexical_atoms)
    if atoms:
        return f"{record.raw_event_name} :: {atoms}"

    return record.raw_event_name


def display_owner(
    record: EventDecompositionRecord,
    *,
    display_context: NormalizedDisplayContext | None = None,
) -> str:
    """返回适合展示的 owner 名称。

    map/系统域记录没有具体 subject 时：可认领的装备别名（数字 id，含
    状态尾缀形态）渲染成 reference 装备名；其余优先用 T3.4 头部词条表把
    owner 英文 alias（Announcer/Env/Cherry 等）渲染成人工核对的中文名；
    表外别名保留英文，不新造翻译。

    Args:
        record: 拆解记录。
        display_context: legacy display 所需的显式 reference/rules 索引。

    Returns:
        owner 展示名称。
    """

    subject = display_subject(record.semantic_slots)
    if subject:
        return subject

    alias = record.owner.alias or record.owner.raw_owner_token or "未知实体"

    if record.owner.kind == "map_or_system":
        item_label = item_entity_label(
            alias,
            display_context.item_names if display_context is not None else None,
        )
        if item_label is not None:
            return item_label

        label = map_system_entity_label(
            alias,
            display_context.map_system_entity_labels if display_context is not None else None,
        )
        if label is not None:
            return label

    return alias


def map_system_entity_label(
    alias: str,
    labels: Mapping[str, str] | None = None,
) -> str | None:
    """把 map/系统域实体英文别名渲染成人工核对的中文词条。

    Args:
        alias: map/系统域实体的原始英文别名（owner.alias）。
        labels: 可选的显式规则词条索引；缺省保留旧 helper 行为。

    Returns:
        中文词条；别名不在 T3.4 头部词条表内时返回 None，调用方原样保留
        英文 alias（宁可保留英文，不硬造翻译——仓库红线）。
    """

    active_labels = labels if labels is not None else get_map_system_entity_labels()
    return active_labels.get(rule_key(alias))


def load_map_system_entity_labels(rules_root: Path, overrides_root: Path) -> dict[str, str]:
    """读取 map/系统域实体级中文词条表（T3.4 头部词条表）。

    词条来自 rules/contextual/sfx 下 owner="entity" 的分片，key 为规则
    引擎归一后的实体别名（rule_key），value 为人工核对的中文名。

    Args:
        rules_root: 仓库规则根目录。
        overrides_root: 人工 overrides 根目录（RuleBook.load 需要，与
            champion 实体归一使用的路径一致）。

    Returns:
        规范化实体别名到中文词条的映射；分片缺失时返回空表。
    """

    rulebook = RuleBook.load(rules_root, overrides_root)
    entity_owner_key = rule_key(MAP_SYSTEM_ENTITY_OWNER)
    return {
        token: alias.label
        for (scope, owner, token), alias in rulebook.contextual_aliases.items()
        if scope == "sfx" and owner == entity_owner_key
    }


@lru_cache(maxsize=1)
def get_map_system_entity_labels() -> dict[str, str]:
    """返回进程内缓存的 map/系统域实体词条表。

    Returns:
        从默认规则目录加载的词条映射；同一进程内只读一次规则目录。
    """

    return load_map_system_entity_labels(DEFAULT_RULES_ROOT, DEFAULT_OVERRIDES_ROOT)


def display_subject(slots: dict[str, Any]) -> str | None:
    """从语义槽中读取主体名称。

    Args:
        slots: 语义槽。

    Returns:
        主体别名或名称；没有主体时返回 None。
    """

    subject = slots.get("subject")
    if isinstance(subject, dict):
        alias = subject.get("alias") or subject.get("name")
        if alias:
            return str(alias)

    subject_hint = slots.get("subject_hint")
    if subject_hint:
        return str(subject_hint)

    champion = slots.get("borrowed_champion")
    if champion:
        return str(champion)

    return None


def first_spell_modifier(slots: dict[str, Any]) -> dict[str, Any] | None:
    """返回第一个技能修饰符。

    Args:
        slots: 语义槽。

    Returns:
        技能修饰符字典；没有时返回 None。
    """

    modifier = slots.get("spell_modifier")
    if isinstance(modifier, dict):
        return modifier

    modifiers = slots.get("residual_modifiers")
    if isinstance(modifiers, list) and modifiers and isinstance(modifiers[0], dict):
        return modifiers[0]

    return None


def audio_kind_label(asset_kind: object) -> str:
    """返回音频类型中文措辞。

    措辞唯一来源是 translation builder 的 ASSET_KIND_WORDS（经 asset_kind_words
    读取），legacy 路径只取 zh 侧，保证两条展示路径逐字节一致。

    Args:
        asset_kind: 事件外壳音频类型（vo/sfx/mus）。

    Returns:
        音频类型措辞；未识别时按语音处理。
    """

    return asset_kind_words(asset_kind)[0]


def phase_label(phase: object) -> str:
    """返回阶段中文短标签。

    Args:
        phase: profile 或 reference 写入的阶段值。

    Returns:
        中文阶段标签。
    """

    labels = {
        "cast": "释放",
        "hit": "命中",
        "activate": "触发",
        "deactivate": "结束",
        "buffactivate": "触发",
        "buffdeactivate": "结束",
        "missilelaunch": "发射",
        "missilecast": "发射",
    }

    if phase is None:
        return ""

    return labels.get(str(phase).lower(), "")


def skin_recall_phase_label(phase: object) -> str:
    """返回皮肤回城状态中文短标签。"""

    labels = {
        "leadin": "引入",
        "leadout": "引出",
        "winddown": "收尾",
        "buff_activate": "状态启动",
        "buff_deactivate": "状态结束",
        "efforts": "努力声段",
    }

    if phase is None:
        return ""

    return labels.get(str(phase).lower(), "")


def skin_idle_component_label(component: object, spell_key: object) -> str:
    """返回皮肤待机片段组件展示名。"""

    if isinstance(component, dict):
        kind = component.get("kind")
        key = component.get("key")

        if kind == "weapon" and key == "powpow":
            return "Q 武器"

        if kind == "spell" and key:
            return f"{str(key).upper()} 技能"

    if spell_key:
        return f"{str(spell_key).upper()} 技能"

    return "特殊"


def skin_idle_variant_label(slots: dict[str, Any]) -> str | None:
    """返回 numbered idle 片段展示名。"""

    variant = slots.get("idle_variant")

    if not isinstance(variant, dict):
        return None

    index = variant.get("index")
    segment = slots.get("segment_index")

    if index is None:
        return None

    if segment is not None:
        return f"待机{index}片段{segment}"

    return f"待机{index}"


def skin_idle_phase_label(phase: object) -> str:
    """返回皮肤待机状态阶段中文短标签。"""

    labels = {
        "in": "进入",
        "out": "退出",
    }

    if phase is None:
        return ""

    return labels.get(str(phase).lower(), "")


def transition_phase_label(phase: object) -> str:
    """返回过渡阶段中文短标签。"""

    labels = {
        "leadin": "引入",
        "leadout": "引出",
        "winddown": "收尾",
    }

    if phase is None:
        return ""

    return labels.get(str(phase).lower(), "")


def skin_named_state_label(slots: dict[str, Any]) -> str:
    """返回皮肤命名状态展示名。"""

    key = slots.get("state_key")

    if key == "slotmachine":
        variant = slotmachine_variant_label(slots.get("state_variant"))
        return f"老虎机{variant}"

    if key == "passive_idle":
        return "被动待机"

    return str(key or "状态")


def slotmachine_variant_label(variant: object) -> str:
    """返回 slotmachine 变体展示名。"""

    labels = {
        "anomaly": "异常",
        "chemtech": "炼金科技",
        "hextech": "海克斯科技",
    }

    if variant is None:
        return ""

    return labels.get(str(variant).lower(), str(variant))


def skin_action_label(action: object) -> str:
    """返回皮肤动作状态的动作中文名。"""

    labels = {
        "death": "死亡",
        "joke": "玩笑",
        "laugh": "大笑",
        "respawn": "重生",
        "taunt": "嘲讽",
    }

    if action is None:
        return "动作"

    return labels.get(str(action).lower(), str(action))


def skin_action_phase_label(phase: object) -> str:
    """返回皮肤动作状态阶段中文短标签。"""

    labels = {
        "buff_activate": "状态启动",
        "buff_deactivate": "状态结束",
        "cast": "释放",
        "activate": "启动",
        "deactivate": "结束",
    }

    if phase is None:
        return ""

    return labels.get(str(phase).lower(), "")


def map_system_fallback_label(record: EventDecompositionRecord) -> str:
    """返回 map/系统域兜底短文案的核心后缀。

    优先渲染已解析的阶段/通用后缀（释放/命中/状态启动/循环等，见
    map_system_phase_label，复用既有阶段词表不新造措辞）；识别到独立
    loop token（如 homeguard_..._loop）但未归一进 phase 槽位时按同一份
    既有措辞渲染“循环”；都没有命中时回落到事件族标签，与其它域兜底路径
    一致，不丢失已解析信息。

    Args:
        record: 拆解记录。

    Returns:
        兜底短文案后缀。
    """

    phase_text = map_system_phase_label(record.semantic_slots.get("phase"))
    if phase_text:
        return phase_text

    if any(atom.raw.lower() == "loop" for atom in record.lexical_atoms):
        return "循环"

    return family_label(record.profile.primary_family)


def map_system_phase_label(phase: object) -> str:
    """返回 map/系统域兜底短文案可渲染的阶段中文词。

    先复用既有阶段词表：phase_label 的 cast/hit/发射类词，以及
    skin_action_phase_label 的 buff_activate/buff_deactivate（状态启动/
    状态结束）；再补齐这两个词表缺的下划线归一键形（missile_launch/
    missile_cast/on_buff_activate/on_buff_deactivate/loop），指向同一份
    既有中文措辞（rules/phases.yaml 的 loop→循环），不新造翻译。

    Args:
        phase: 语义槽中的阶段值。

    Returns:
        中文阶段词；未命中任何已有词表时返回空串。
    """

    if phase is None:
        return ""

    key = str(phase).lower()
    direct = phase_label(key) or skin_action_phase_label(key)
    if direct:
        return direct

    underscored_aliases = {
        "missile_launch": "发射",
        "missile_cast": "发射",
        "on_buff_activate": "状态启动",
        "on_buff_deactivate": "状态结束",
        "loop": "循环",
    }
    return underscored_aliases.get(key, "")


def champion_status_label(status_key: object) -> str:
    """返回英雄状态展示名。"""

    labels = {
        "slow_stun": "减速/眩晕",
        "rapid_reload": "快速装填",
        "animal_threshold": "斯嘎尔阈值",
    }

    if status_key is None:
        return "状态"

    return labels.get(str(status_key).lower(), str(status_key))


def gameplay_event_label(event: object) -> str:
    """返回玩法事件展示名。"""

    labels = {
        "first_blood": "第一滴血",
    }

    if event is None:
        return "玩法事件"

    return labels.get(str(event).lower(), str(event))


def phase_action_label(action: object) -> str:
    """返回阶段动作展示名。"""

    labels = {
        "select": "阶段选择",
    }

    if action is None:
        return ""

    return labels.get(str(action).lower(), str(action))


def family_label(primary_family: str) -> str:
    """返回事件族中文标签。

    Args:
        primary_family: profile 的 primary family。

    Returns:
        中文事件族标签。
    """

    labels = {
        "basic_attack": "普通攻击",
        "crit_attack": "暴击攻击",
        "death": "死亡",
        "dance": "跳舞",
        "joke": "玩笑",
        "laugh": "大笑",
        "taunt": "嘲讽",
        "recall": "回城",
        "winddown": "收招",
        "spell": "技能事件",
        "voice": "语音事件",
        "sfx": "音效事件",
        "movement": "移动事件",
        "kill": "击杀事件",
        "system": "系统事件",
        "skin_action_state": "皮肤动作",
        "skin_audio_transition": "皮肤音频过渡",
        "skin_idle_state": "皮肤待机",
        "skin_named_state": "皮肤状态",
        "skin_recall_state": "皮肤回城",
        "champion_status_state": "英雄状态",
        "gameplay_phase_control": "玩法阶段",
        "unknown": "事件",
    }

    return labels.get(primary_family, "事件")


def mode_key(value: object) -> str | None:
    """读取模式键。

    Args:
        value: `mode_context` 原始值。

    Returns:
        模式键；没有时返回 None。
    """

    if isinstance(value, dict):
        key = value.get("key") or value.get("mode")
        return str(key) if key else None

    return str(value) if value else None


def mode_label(value: object) -> str | None:
    """读取模式中文标签。

    Args:
        value: `mode_context` 原始值。

    Returns:
        模式中文标签；没有时返回 None。
    """

    if isinstance(value, dict):
        label = value.get("label") or value.get("mode_label")
        return str(label) if label else None

    if value == "ultimate_spellbook":
        return "终极魔典"

    return None


def compact_display(text: str) -> str:
    """清理展示文案中的技术 token。

    Args:
        text: 原始展示文案。

    Returns:
        清理后的展示文案。
    """

    result = text
    for token in TECHNICAL_DISPLAY_TOKENS:
        result = result.replace(token, "")

    return " ".join(result.split())


def write_normalized_outputs(
    records: Iterable[EventDecompositionRecord],
    output_root: Path,
    *,
    reference_version: str,
    reference_root: Path,
    rules_root: Path,
    overrides_root: Path,
    input_identity: Mapping[str, object],
    reference_aliases: dict[tuple[str, str], str] | None = None,
    resolver: "ChampionEntityResolver | None" = None,
    translation_context: TranslationBuildContext | None = None,
    pattern_policy: PatternPolicy,
    contract_assets_root: Path,
) -> None:
    """写出 normalized 目录。

    Args:
        records: 拆解记录集合。
        output_root: normalized 输出目录。
        reference_version: 本次生成使用的参考版本。
        reference_root: 本次构建实际使用的 reference 根目录。
        rules_root: 本次构建实际使用的 rules 根目录。
        overrides_root: 本次构建实际使用的 overrides 根目录。
        input_identity: 已通过冻结预检的完整输入身份。
        reference_aliases: 可选的预加载实体归一 alias 映射；缺省时从显式
            reference_root 加载。供进程内多次调用复用，避免重复解析全量合集。
        resolver: 可选的预构建 champion 实体解析器；缺省时从显式根加载。
        translation_context: 可选的预构建 translation 上下文；缺省时从显式
            reference/rules/overrides 根加载。
        pattern_policy: 已从显式路径加载的 P1 pattern/promotion policy。
        contract_assets_root: 提交入库的 JSON Schema 与 examples 根目录。
    """

    clear_generated_outputs(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    if reference_aliases is None:
        reference_aliases = load_reference_entity_aliases(reference_root)
    if resolver is None:
        resolver = load_champion_entity_resolver(reference_root, overrides_root)
    if translation_context is None:
        translation_context = build_translation_context(
            reference_root,
            rules_root=rules_root,
            overrides_root=overrides_root,
        )
    display_context = NormalizedDisplayContext(
        item_names=translation_context.reference_item_names or load_reference_item_names(reference_root),
        map_system_entity_labels=(
            translation_context.map_system_entity_labels or load_map_system_entity_labels(rules_root, overrides_root)
        ),
    )
    pending_rows: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for record in records:
        normalized = build_normalized_record(
            record,
            translation_context=translation_context,
            display_context=display_context,
        )
        entity = normalized["entity"]
        if not isinstance(entity, dict):
            continue
        kind = str(entity["kind"])
        alias = str(entity["alias"])

        if kind == "champion":
            resolution = resolve_champion_entity(alias, record.source_paths, resolver)
            kind, resolved = resolution.kind, resolution.alias
            # 信息无损：只要归一改变了 alias，就把原始 alias 留在记录里。
            if resolved != alias:
                entity["raw_alias"] = alias
            entity["kind"] = kind
            entity["alias"] = resolved
            if resolution.diagnostic is not None:
                append_quality_diagnostic(normalized, resolution.diagnostic)
            pending_rows[(kind, resolved.casefold())].append(normalized)
            continue

        canonical = reference_aliases.get((kind, alias.casefold()), alias)
        pending_rows[(kind, canonical.casefold())].append(normalized)

    grouped = canonicalize_entity_groups(pending_rows, reference_aliases)
    all_rows = [row for rows in grouped.values() for row in rows]
    pattern_report = apply_normalized_contract(
        all_rows,
        pattern_policy,
    )
    pattern_catalog = build_public_pattern_catalog(pattern_report, pattern_policy)
    accepted_corpus = load_accepted_corpus(contract_assets_root / "accepted-corpus.v1.json")
    quality_summary = build_quality_summary(all_rows, pattern_report, pattern_catalog, accepted_corpus)

    shard_paths = assign_entity_shard_paths(grouped.keys())
    for (kind, alias), rows in sorted(grouped.items()):
        path = output_root / shard_paths[(kind, alias)]
        write_normalized_jsonl(rows, path)

    write_normalized_event_stream(all_rows, output_root / "normalized-events.jsonl")
    write_json_document(pattern_catalog, output_root / "pattern-catalog.json")
    write_json_document(quality_summary, output_root / "quality-summary.json")
    write_contract_assets(contract_assets_root, output_root)
    write_entity_summary(grouped, output_root / "entity-summary.json", shard_paths)
    write_manifest(
        grouped,
        output_root / "manifest.json",
        reference_version,
        pattern_catalog=pattern_catalog,
        quality_summary=quality_summary,
        input_identity=input_identity,
    )


def build_translation_context(
    reference_root: Path = DEFAULT_REFERENCE_ROOT,
    *,
    rules_root: Path = DEFAULT_RULES_ROOT,
    overrides_root: Path = DEFAULT_OVERRIDES_ROOT,
) -> TranslationBuildContext:
    """创建 normalized translation 构建上下文。

    Args:
        reference_root: 仓库 reference 合集根目录。
        rules_root: 仓库 rules 根目录。
        overrides_root: 仓库 overrides 根目录。

    Returns:
        包含 reference label 的 translation 构建上下文。
    """

    display_labels = load_display_labels(rules_root / "display_labels.yaml")
    map_system_labels = load_map_system_entity_labels(rules_root, overrides_root)
    if not reference_root.exists():
        return TranslationBuildContext(
            display_labels=display_labels,
            map_system_entity_labels=map_system_labels,
        )

    collection = load_reference_collection(reference_root)
    reference_map_names = map_names_from_collection(collection)
    reference_item_names = item_names_from_collection(collection)
    champion_labels: dict[str, ChampionLabelEntry] = {}
    for row in collection.champions.values():
        entity = row.get("entity")
        if not isinstance(entity, dict) or not entity.get("alias"):
            continue

        labels = reference_labels(entity)
        # 主 label 直取 reference names（zh=俗名，en=官方名），并留来源与名称多形式。
        champion_labels[str(entity["alias"])] = ChampionLabelEntry(
            labels,
            label_sources={language: LABEL_SOURCE_REFERENCE for language in labels},
            name_forms=reference_name_forms(entity),
        )
    skin_labels: dict[tuple[str, str], dict[str, str]] = {}
    for row in collection.skins.values():
        entity = row.get("entity")
        if not isinstance(entity, dict):
            continue

        champion_alias = entity.get("champion_alias")
        skin_token = entity.get("skin_token")
        if not isinstance(champion_alias, str) or not isinstance(skin_token, str):
            continue

        skin_labels[(champion_alias, skin_token)] = reference_labels(entity)

    infer_champion_labels_from_skin_labels(champion_labels, skin_labels)
    return TranslationBuildContext(
        champion_labels=champion_labels,
        skin_labels=skin_labels,
        display_labels=display_labels,
        reference_map_names=reference_map_names,
        reference_item_names=reference_item_names,
        map_system_entity_labels=map_system_labels,
    )


def infer_champion_labels_from_skin_labels(
    champion_labels: dict[str, ChampionLabelEntry],
    skin_labels: dict[tuple[str, str], dict[str, str]],
) -> None:
    """从中文皮肤全名投票回填缺失的英雄中文名。

    reference `names.zh_CN` 已是权威俗名来源，本函数只在 reference 缺 zh 名
    时才用“皮肤名 英雄名”众数投票补位，并在 entry 上标记低可信来源。

    Args:
        champion_labels: 待更新的英雄 label 映射。
        skin_labels: 具体皮肤 label 映射。
    """

    candidates: dict[str, dict[str, int]] = defaultdict(dict)
    for (champion_alias, _skin_token), labels in skin_labels.items():
        suffix = chinese_name_suffix(labels.get("zh_CN"))
        if suffix is None:
            continue

        counts = candidates.setdefault(champion_alias, {})
        counts[suffix] = counts.get(suffix, 0) + 1

    for champion_alias, counts in candidates.items():
        if not counts:
            continue

        entry = champion_labels.setdefault(champion_alias, ChampionLabelEntry())
        if entry.get("zh_CN"):
            continue

        entry["zh_CN"] = max(sorted(counts), key=lambda value: counts[value])
        if isinstance(entry, ChampionLabelEntry):
            entry.label_sources["zh_CN"] = LABEL_SOURCE_SKIN_VOTE


def chinese_name_suffix(value: str | None) -> str | None:
    """提取中文皮肤名末尾的英雄名片段。"""

    if not value or " " not in value:
        return None

    pieces = value.split(" ")
    # 官方皮肤名可能把间隔号写成独立空格段（奥瑞利安 · 索尔），向前合并成整名，避免截出半个名字。
    start = len(pieces) - 1
    while start >= 2 and pieces[start - 1] == "·":
        start -= 2

    if start <= 0:
        return None

    suffix = "".join(pieces[start:]).strip()
    if not suffix or len(suffix) > 8:
        return None

    if any("\u4e00" <= char <= "\u9fff" for char in suffix):
        return suffix

    return None


def reference_labels(entity: dict[str, Any]) -> dict[str, str]:
    """从 reference entity.names 读取多语言 label。

    Args:
        entity: reference entity 字典。

    Returns:
        语言到首个 label value 的映射。
    """

    names = entity.get("names")
    if not isinstance(names, dict):
        return {}

    labels = {}
    for language, rows in names.items():
        if not isinstance(language, str) or not isinstance(rows, list):
            continue

        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("value"), str):
                labels[language] = row["value"]
                break

    return labels


# 名称多形式列全备齐（北极星）：称号与俗称随 champion argument 无损带出。
NAME_FORM_FIELDS = ("title", "nicknames")


def reference_name_forms(entity: dict[str, Any]) -> dict[str, dict[str, list[str]]]:
    """从 reference entity 读取称号与俗称等名称多形式。

    Args:
        entity: reference entity 字典。

    Returns:
        名称形式到语言到值列表的映射；无数据的形式与语言不出现。
    """

    forms: dict[str, dict[str, list[str]]] = {}
    for form in NAME_FORM_FIELDS:
        rows_by_language = entity.get(form)
        if not isinstance(rows_by_language, dict):
            continue

        languages: dict[str, list[str]] = {}
        for language, rows in rows_by_language.items():
            if not isinstance(language, str) or not isinstance(rows, list):
                continue

            values = [
                row["value"]
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("value"), str) and row["value"]
            ]
            if values:
                languages[language] = values

        if languages:
            forms[form] = languages

    return forms


@lru_cache(maxsize=4)
def load_reference_entity_aliases(reference_root: Path) -> dict[tuple[str, str], str]:
    """读取 reference 中可用于最终实体归一的 alias。

    Args:
        reference_root: 仓库 reference 合集根目录。

    Returns:
        `(kind, alias.casefold())` 到 canonical alias 的映射。
    """

    if not reference_root.exists():
        return {}

    collection = load_reference_collection(reference_root)
    aliases: dict[tuple[str, str], str] = {}

    add_reference_aliases(aliases, "champion", collection.champions.values())
    add_reference_aliases(aliases, "map", collection.maps.values())
    add_reference_aliases(aliases, "skinline", collection.skinlines.values())
    add_reference_aliases(aliases, "map_or_system", collection.maps.values())

    return aliases


def add_reference_aliases(
    aliases: dict[tuple[str, str], str],
    kind: str,
    rows: Iterable[dict[str, Any]],
) -> None:
    """把 reference 实体 alias 加入归一映射。"""

    for row in rows:
        entity = row.get("entity")
        if not isinstance(entity, dict):
            continue

        alias = entity.get("alias")
        if not alias:
            continue

        value = sanitize_entity_alias(str(alias))
        aliases[(kind, value.casefold())] = value


@dataclass(frozen=True)
class NormalizedDisplayContext:
    """normalized legacy display 所需的显式只读索引。"""

    item_names: Mapping[int, Mapping[str, str]]
    map_system_entity_labels: Mapping[str, str]


@dataclass(frozen=True)
class ChampionEntityResolver:
    """champion 实体归一所需的 reference 与人工决策索引。"""

    aliases: dict[str, str]
    ids: dict[int, str]
    manual: dict[str, dict[str, str]]


@dataclass(frozen=True)
class ChampionEntityResolution:
    """champion 实体归一结果；diagnostic 记录挂靠与 bank 证据的矛盾。"""

    kind: str
    alias: str
    diagnostic: str | None = None


@lru_cache(maxsize=4)
def load_champion_entity_resolver(reference_root: Path, overrides_root: Path) -> ChampionEntityResolver:
    """构建 champion 实体归一解析器。

    Args:
        reference_root: 仓库 reference 合集根目录。
        overrides_root: 人工 overrides 根目录。

    Returns:
        含英雄 alias / id 索引与人工决策的解析器。
    """

    aliases: dict[str, str] = {}
    ids: dict[int, str] = {}

    if reference_root.exists():
        collection = load_reference_collection(reference_root)
        for row in collection.champions.values():
            entity = row.get("entity")
            if not isinstance(entity, dict) or not entity.get("alias"):
                continue

            canonical = sanitize_entity_alias(str(entity["alias"]))
            aliases[canonical.casefold()] = canonical
            if isinstance(entity.get("id"), int):
                ids[entity["id"]] = canonical

    manual = load_entity_attribution_overrides(overrides_root)
    for key, row in manual.items():
        if row["decision"] == "champion" and str(row.get("champion", "")).casefold() not in aliases:
            raise ValueError(
                f"entity_attribution.yaml alias {row['alias']} 指向的英雄不在 reference 中：{row.get('champion')}"
            )

        if key in aliases:
            raise ValueError(f"entity_attribution.yaml alias 与 reference 英雄冲突：{row['alias']}")

    return ChampionEntityResolver(aliases=aliases, ids=ids, manual=manual)


@lru_cache(maxsize=4)
def load_entity_attribution_overrides(overrides_root: Path) -> dict[str, dict[str, str]]:
    """读取人工实体归属决策表并做 fail-fast 校验。

    Args:
        overrides_root: 人工 overrides 根目录。

    Returns:
        casefold alias 到决策行的映射。
    """

    path = overrides_root / ENTITY_ATTRIBUTION_FILENAME
    if not path.is_file():
        return {}

    data = read_yaml(path)
    rows = data.get("champion_aliases")
    if not isinstance(rows, list):
        raise ValueError(f"entity_attribution.yaml 缺少 champion_aliases 列表：{path}")

    decisions: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(f"champion_aliases 条目必须是 mapping：{row!r}")

        alias = str(row.get("alias") or "")
        decision = row.get("decision")
        if not alias or decision not in ENTITY_ATTRIBUTION_DECISIONS:
            raise ValueError(f"champion_aliases 条目缺少合法 alias/decision：{row!r}")

        if not str(row.get("note") or "").strip():
            raise ValueError(f"champion_aliases 条目必须带 note 说明证据：{alias}")

        if decision == "champion" and not str(row.get("champion") or "").strip():
            raise ValueError(f"decision=champion 的条目必须指定 champion：{alias}")

        if decision == "downgrade" and row.get("kind") not in ENTITY_DOWNGRADE_KINDS:
            raise ValueError(f"decision=downgrade 的条目 kind 必须是 unknown/map_or_system：{alias}")

        key = alias.casefold()
        if key in decisions:
            raise ValueError(f"champion_aliases 存在重复 alias：{alias}")

        decisions[key] = {str(k): str(v) for k, v in row.items()}

    return decisions


def resolve_champion_entity(
    alias: str,
    source_paths: list[str],
    resolver: ChampionEntityResolver,
) -> ChampionEntityResolution:
    """把 champion 实体 alias 归一到 reference 英雄或诚实降级。

    归一链：reference 精确匹配 → 人工决策 → SkinNN 剥离 → 英雄名前后缀
    挂靠（宠物/召唤物/皮肤别名）→ 单一英雄 bank 文件 id 回退；全部
    失败时保留原 alias。挂靠结论与唯一 bank 文件 id 结论矛盾时不改变
    优先级，只附带 diagnostic 供 validation 以 warning 暴露。

    Args:
        alias: 实体 alias。
        source_paths: 记录来源路径。
        resolver: champion 实体归一解析器。

    Returns:
        归一结果（kind/alias/可选 diagnostic）。
    """

    key = alias.casefold()
    canonical = resolver.aliases.get(key)
    if canonical is not None:
        return ChampionEntityResolution("champion", canonical)

    manual = resolver.manual.get(key)
    if manual is not None:
        if manual["decision"] == "keep":
            return ChampionEntityResolution("champion", alias)

        if manual["decision"] == "champion":
            return ChampionEntityResolution("champion", resolver.aliases[manual["champion"].casefold()])

        return ChampionEntityResolution(manual["kind"], alias)

    stripped = SKIN_SUFFIX_ALIAS_PATTERN.match(alias)
    if stripped is not None:
        canonical = resolver.aliases.get(stripped.group("base").casefold())
        if canonical is not None:
            return ChampionEntityResolution("champion", canonical)

    canonical = champion_by_alias_affix(key, resolver)
    if canonical is not None:
        return ChampionEntityResolution(
            "champion",
            canonical,
            affix_source_conflict_diagnostic(alias, canonical, source_paths, resolver),
        )

    canonical = champion_by_source_file(source_paths, resolver)
    if canonical is not None:
        return ChampionEntityResolution("champion", canonical)

    return ChampionEntityResolution("champion", alias)


def affix_source_conflict_diagnostic(
    alias: str,
    affix_champion: str,
    source_paths: list[str],
    resolver: ChampionEntityResolver,
) -> str | None:
    """挂靠结论与唯一 bank 文件 id 结论矛盾时生成 diagnostic。

    Args:
        alias: 归一前实体 alias。
        affix_champion: 前后缀挂靠命中的英雄。
        source_paths: 记录来源路径。
        resolver: champion 实体归一解析器。

    Returns:
        矛盾说明；无 bank 结论或两者一致时返回 None。
    """

    source_champion = champion_by_source_file(source_paths, resolver)
    if source_champion is None or source_champion == affix_champion:
        return None

    return f"entity_affix_source_conflict:alias={alias} affix={affix_champion} source_file={source_champion}"


def champion_by_alias_affix(alias_key: str, resolver: ChampionEntityResolver) -> str | None:
    """按最长英雄名前后缀挂靠宠物/召唤物/皮肤别名实体。

    Args:
        alias_key: casefold 后的实体 alias。
        resolver: champion 实体归一解析器。

    Returns:
        命中的 reference 英雄 alias；未命中返回 None。
    """

    best: tuple[int, int, str] | None = None
    for ref_key, canonical in resolver.aliases.items():
        # 余段必须非空，否则应已被精确匹配命中。
        if len(ref_key) >= len(alias_key):
            continue

        is_prefix = len(ref_key) >= AFFIX_PREFIX_MIN_LENGTH and alias_key.startswith(ref_key)
        is_suffix = len(ref_key) >= AFFIX_SUFFIX_MIN_LENGTH and alias_key.endswith(ref_key)
        if not is_prefix and not is_suffix:
            continue

        candidate = (len(ref_key), int(is_prefix), canonical)
        if best is None or candidate > best:
            best = candidate

    return best[2] if best is not None else None


def append_quality_diagnostic(normalized: dict[str, object], diagnostic: str) -> None:
    """把 diagnostic 追加进 normalized 记录的 quality.diagnostics。

    Args:
        normalized: normalized 记录。
        diagnostic: 待追加的 diagnostic 文本。
    """

    quality = normalized.get("quality")
    if isinstance(quality, dict) and isinstance(quality.get("diagnostics"), list):
        quality["diagnostics"].append(diagnostic)


def champion_by_source_file(source_paths: list[str], resolver: ChampionEntityResolver) -> str | None:
    """当记录只来自单一英雄音频 bank 文件时按文件 id 归属。

    同名事件可能同时出现在英雄 bank 与地图 bank；地图路径不构成
    反证，只要求英雄 bank 文件 id 唯一。

    Args:
        source_paths: 记录来源路径。
        resolver: champion 实体归一解析器。

    Returns:
        文件 id 对应的 reference 英雄 alias；id 缺失或不唯一时返回 None。
    """

    file_ids: set[int] = set()
    for path in source_paths:
        match = CHAMPION_SOURCE_FILE_PATTERN.search(str(path))
        if match is not None:
            file_ids.add(int(match.group(1)))

    if len(file_ids) != 1:
        return None

    return resolver.ids.get(file_ids.pop())


def canonicalize_entity_groups(
    pending_rows: dict[tuple[str, str], list[dict[str, object]]],
    reference_aliases: dict[tuple[str, str], str],
) -> dict[tuple[str, str], list[dict[str, object]]]:
    """把大小写变体合并为最终 canonical 实体分组。

    Args:
        pending_rows: 按 `(kind, casefold alias)` 初步合并的 normalized 记录。
        reference_aliases: reference canonical alias 映射。

    Returns:
        按最终 `(kind, canonical alias)` 分组的记录。
    """

    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for (kind, _alias_key), rows in pending_rows.items():
        aliases = [entity_alias_from_normalized(row) for row in rows]
        canonical = canonical_entity_alias(kind, aliases, reference_aliases)

        for row in rows:
            entity = row.get("entity")
            if isinstance(entity, dict):
                original = entity.get("alias")
                # 大小写变体合并同样属于归一，未记录过原值时补 raw_alias。
                if original and original != canonical and "raw_alias" not in entity:
                    entity["raw_alias"] = original
                entity["kind"] = kind
                entity["alias"] = canonical
            grouped[(kind, canonical)].append(row)

    return grouped


def entity_alias_from_normalized(row: dict[str, object]) -> str:
    """读取 normalized 记录中的实体 alias。"""

    entity = row.get("entity")
    if isinstance(entity, dict):
        alias = entity.get("alias")
        if alias:
            return str(alias)

    return "unknown"


def canonical_entity_alias(
    kind: str,
    aliases: list[str],
    reference_aliases: dict[tuple[str, str], str],
) -> str:
    """返回一组大小写变体的最终实体 alias。"""

    for alias in aliases:
        canonical = reference_aliases.get((kind, alias.casefold()))
        if canonical is not None:
            return canonical

    return preferred_entity_alias(aliases)


def preferred_entity_alias(aliases: list[str]) -> str:
    """在没有 reference 时选择稳定且可读的 alias 写法。"""

    candidates = [sanitize_entity_alias(alias) for alias in aliases if alias]
    if not candidates:
        return "unknown"

    def score(alias: str) -> tuple[int, int, int, str]:
        has_lower = any(char.islower() for char in alias)
        has_upper = any(char.isupper() for char in alias)
        starts_upper = alias[:1].isupper()
        return (int(starts_upper), int(has_upper), int(has_lower), alias)

    return max(sorted(set(candidates)), key=score)


def clear_generated_outputs(output_root: Path) -> None:
    """清理本 writer 管理的旧 generated 文件。

    Args:
        output_root: normalized 输出目录。
    """

    for name in (
        "manifest.json",
        "entity-summary.json",
        "normalized-events.jsonl",
        "pattern-catalog.json",
        "quality-summary.json",
    ):
        path = output_root / name
        if path.exists():
            path.unlink()

    for name in ("schema", "examples"):
        clear_generated_directory(output_root / name)

    by_entity = output_root / "by-entity"
    if by_entity.exists():
        for path in by_entity.rglob("*.jsonl"):
            path.unlink()

        for path in sorted((item for item in by_entity.rglob("*") if item.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()

        if not any(by_entity.iterdir()):
            by_entity.rmdir()


def clear_generated_directory(root: Path) -> None:
    """删除 writer 专属目录中的生成文件并清理空目录。"""

    if not root.exists():
        return
    for path in root.rglob("*"):
        if path.is_file():
            path.unlink()
    for path in sorted((item for item in root.rglob("*") if item.is_dir()), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


def write_normalized_jsonl(rows: list[dict[str, object]], output_path: Path) -> None:
    """写出单个实体 JSONL 分片。

    Args:
        rows: normalized 记录。
        output_path: JSONL 输出路径。
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in sorted(rows, key=lambda item: (str(item["raw_event_name"]), str(item.get("record_id") or ""))):
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def write_normalized_event_stream(rows: list[dict[str, object]], output_path: Path) -> None:
    """按 record_id 写出 V2 全量稳定记录流。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in sorted(rows, key=lambda item: str(item.get("record_id") or "")):
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def write_json_document(document: dict[str, object], output_path: Path) -> None:
    """以确定性格式写出单个 JSON document。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_contract_assets(contract_assets_root: Path, output_root: Path) -> None:
    """把提交入库的 JSON Schema 与 examples 复制到数据集。"""

    required_assets = {
        "schema": (
            "manifest.schema.json",
            "normalized-event.schema.json",
            "pattern-catalog.schema.json",
            "quality-summary.schema.json",
        ),
        "examples": (
            "manifest.v2.json",
            "normalized-event.v2.json",
            "pattern-catalog.v1.json",
            "quality-summary.v1.json",
            "consumer.py",
        ),
    }
    for directory, filenames in required_assets.items():
        source_root = contract_assets_root / directory
        target_root = output_root / directory
        target_root.mkdir(parents=True, exist_ok=True)
        for filename in filenames:
            source = source_root / filename
            if not source.is_file():
                raise ValueError(f"normalized contract asset 不存在: {source}")
            shutil.copyfile(source, target_root / filename)
    corpus = contract_assets_root / "accepted-corpus.v1.json"
    if not corpus.is_file():
        raise ValueError(f"normalized accepted corpus 不存在: {corpus}")
    shutil.copyfile(corpus, output_root / "examples" / "accepted-corpus.v1.json")


def write_manifest(
    grouped: dict[tuple[str, str], list[dict[str, object]]],
    output_path: Path,
    reference_version: str,
    *,
    pattern_catalog: dict[str, object],
    quality_summary: dict[str, object],
    input_identity: Mapping[str, object],
) -> None:
    """写出 normalized manifest。

    Args:
        grouped: 按实体分组后的 normalized 记录。
        output_path: manifest 输出路径。
        reference_version: 本次生成使用的参考版本。
        pattern_catalog: 同批生成的公共 Pattern Catalog。
        quality_summary: 同批生成的确定性分层质量摘要。
        input_identity: 已通过冻结预检的完整输入身份。
    """

    output_root = output_path.parent
    files = describe_payload_files(output_root)
    unmanaged = unmanaged_payload_paths(files)
    if unmanaged:
        raise ValueError(f"normalized output 含 writer 未管理的 payload 文件: {', '.join(unmanaged)}")
    rows = [row for entity_rows in grouped.values() for row in entity_rows]
    support_counts = value_counts(rows, "support_level")
    review_counts = value_counts(rows, "review_status")
    profile = input_identity.get("profile")
    profile = profile if isinstance(profile, Mapping) else {}
    content_identity: dict[str, object] = {
        "input_identity": dict(input_identity),
        "corpus_scope": profile.get("corpus_scope"),
        "base_game_version": profile.get("base_game_version"),
        "review": {
            "decision_id": pattern_catalog.get("decision_id"),
            "record_counts": review_counts,
        },
        "support_level_counts": support_counts,
        "quality_status": quality_summary.get("status"),
        "quality_gate_version": quality_summary.get("gate_version"),
    }
    digest = content_digest(content_identity, files)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_schema_version": "normalized-dataset.v2",
        "normalized_record_schema_version": SCHEMA_VERSION,
        "pattern_catalog_schema_version": PATTERN_CATALOG_SCHEMA_VERSION,
        "dataset_id": f"local-v1-{digest[:16]}",
        "content_digest": digest,
        "content_digest_algorithm": CONTENT_DIGEST_VERSION,
        "content_identity": content_identity,
        "files": files,
        "reference_version": reference_version,
        "entity_count": len(grouped),
        "record_count": sum(len(rows) for rows in grouped.values()),
        "pattern_count": pattern_catalog.get("pattern_count"),
        "contains_full_raw_event_index": True,
        "outputs": [
            "manifest.json",
            "normalized-events.jsonl",
            "pattern-catalog.json",
            "quality-summary.json",
            "entity-summary.json",
            "by-entity/",
            "schema/",
            "examples/",
        ],
    }
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def value_counts(rows: Iterable[Mapping[str, object]], field: str) -> dict[str, int]:
    """稳定统计 normalized 记录中的枚举字段。"""

    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get(field) or "missing")] += 1
    return dict(sorted(counts.items()))


def write_entity_summary(
    grouped: dict[tuple[str, str], list[dict[str, object]]],
    output_path: Path,
    shard_paths: dict[tuple[str, str], str],
) -> None:
    """写出实体汇总。

    Args:
        grouped: 按实体分组后的 normalized 记录。
        output_path: 汇总输出路径。
        shard_paths: 实体分片路径映射。
    """

    summary = [
        {"kind": kind, "alias": alias, "record_count": len(rows), "path": shard_paths[(kind, alias)]}
        for (kind, alias), rows in sorted(grouped.items())
    ]
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def assign_entity_shard_paths(keys: Iterable[tuple[str, str]]) -> dict[tuple[str, str], str]:
    """为实体分配大小写无关唯一的 JSONL 分片路径。

    Args:
        keys: `(kind, alias)` 实体键集合。

    Returns:
        实体键到相对输出路径的映射。
    """

    paths: dict[tuple[str, str], str] = {}
    used_lower_paths: set[str] = set()

    for kind, alias in sorted(keys):
        base = entity_shard_path(kind, alias)
        candidate = base
        suffix = 2

        while candidate.lower() in used_lower_paths:
            candidate = f"by-entity/{plural_entity_kind(kind)}/{alias}__{suffix}.jsonl"
            suffix += 1

        paths[(kind, alias)] = candidate
        used_lower_paths.add(candidate.lower())

    return paths


def entity_shard_path(kind: str, alias: str) -> str:
    """返回实体分片相对路径。

    Args:
        kind: 实体类型。
        alias: 实体别名。

    Returns:
        JSONL 分片相对路径。
    """

    return f"by-entity/{plural_entity_kind(kind)}/{alias}.jsonl"


def plural_entity_kind(kind: str) -> str:
    """返回实体类型目录名。

    Args:
        kind: 实体类型。

    Returns:
        复数目录名。
    """

    irregular = {
        "champion": "champions",
        "map": "maps",
        "system": "systems",
        "unknown": "unknown",
    }

    return irregular.get(kind, f"{kind}s")


def read_decomposition_jsonl(path: Path) -> list[EventDecompositionRecord]:
    """读取拆解 JSONL。

    Args:
        path: JSONL 文件路径。

    Returns:
        拆解记录列表。
    """

    if not path.exists():
        return []

    records: list[EventDecompositionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(EventDecompositionRecord.from_dict(json.loads(line)))

    return records


def read_decomposition_root(decomposition_root: Path) -> list[EventDecompositionRecord]:
    """读取拆解输出目录。

    Args:
        decomposition_root: 包含 `champions.jsonl` 和 `maps.jsonl` 的目录。

    Returns:
        拆解记录列表。
    """

    records: list[EventDecompositionRecord] = []
    for name in ("champions.jsonl", "maps.jsonl"):
        records.extend(read_decomposition_jsonl(decomposition_root / name))

    return merge_equivalent_decomposition_records(records)
