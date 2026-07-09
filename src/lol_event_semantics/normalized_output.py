"""对外 normalized 数据生成。"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .decomposition_model import EventDecompositionRecord
from .normalized_translation_builder import build_translation
from .normalized_translation_model import TranslationBuildContext
from .normalized_translation_renderer import build_compat_display
from .reference_collection import load_reference_collection, read_yaml

SCHEMA_VERSION = 1
TECHNICAL_DISPLAY_TOKENS = ("Play_vo", "Play_sfx", "cast3D", "2D", "3D", "voProcessing")
DEFAULT_REFERENCE_ROOT = Path("reference")
DEFAULT_OVERRIDES_ROOT = Path("overrides")
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
) -> dict[str, object]:
    """把拆解记录转换为对外 normalized 记录。

    Args:
        record: 已完成 profile/reference enrichment 的拆解记录。
        translation_context: translation 构建上下文。

    Returns:
        可直接写入 JSONL 的 normalized 记录。
    """

    translation = build_translation(record, translation_context or TranslationBuildContext())
    legacy_display = {
        "zh_cn_short": build_short_display(record),
        "zh_cn_detail": build_detail_display(record),
        "debug_label": build_debug_label(record),
    }
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
        "display": build_compat_display(
            translation,
            legacy_display=legacy_display,
            raw_event_name=record.raw_event_name,
        ),
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

    return {"kind": sanitize_entity_kind(kind), "alias": sanitize_entity_alias(alias)}


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


def build_short_display(record: EventDecompositionRecord) -> str:
    """生成普通用户短展示文案。

    Args:
        record: 拆解记录。

    Returns:
        面向下游用户的短中文说明，Stop/Mute 壳动词以停止/静音收尾。
    """

    return f"{build_short_display_core(record)}{playback_verb_suffix(record.shell.verb)}"


def playback_verb_suffix(verb: object) -> str:
    """返回壳动词对应的中文文案后缀。

    Args:
        verb: 事件外壳动词。

    Returns:
        stop/mute 返回停止/静音；默认播放语义返回空串。
    """

    if not isinstance(verb, str):
        return ""

    labels = {"stop": "停止", "mute": "静音", "unmute": "取消静音"}
    return labels.get(verb.lower(), "")


def build_short_display_core(record: EventDecompositionRecord) -> str:
    """按事件族生成不含壳动词后缀的短文案主体。

    Args:
        record: 拆解记录。

    Returns:
        短中文说明主体。
    """

    slots = record.semantic_slots
    owner = display_owner(record)
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
        return compact_display(f"{owner}{skin} 音乐风格{slots.get('genre_index')}过渡{transition_phase_label(slots.get('transition_phase'))}")

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

    if record.profile.primary_family == "attack":
        return compact_display(f"{owner}攻击")

    if record.profile.primary_family == "emote":
        return compact_display(f"{owner}表情{audio_kind_label(record.shell.asset_kind)}")

    if record.profile.primary_family == "skin_recall_state":
        skin_token = slots.get("skin_token")
        skin = f" {skin_token}" if skin_token else ""
        return compact_display(f"{owner}{skin} 回城{skin_recall_phase_label(phase)}")

    return compact_display(f"{owner}{family_label(record.profile.primary_family)}")


def build_detail_display(record: EventDecompositionRecord) -> str:
    """生成详细展示文案。

    Args:
        record: 拆解记录。

    Returns:
        比短文案略完整的中文说明。
    """

    short = build_short_display(record)
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


def display_owner(record: EventDecompositionRecord) -> str:
    """返回适合展示的 owner 名称。

    Args:
        record: 拆解记录。

    Returns:
        owner 展示名称。
    """

    subject = display_subject(record.semantic_slots)
    if subject:
        return subject

    return record.owner.alias or record.owner.raw_owner_token or "未知实体"


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

    Args:
        asset_kind: 事件外壳音频类型（vo/sfx/mus）。

    Returns:
        音频类型措辞；未识别时按语音处理。
    """

    if isinstance(asset_kind, str):
        labels = {"sfx": "音效", "mus": "音乐"}
        return labels.get(asset_kind.lower(), "语音")

    return "语音"


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
        "first_blood": "一血",
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
) -> None:
    """写出 normalized 目录。

    Args:
        records: 拆解记录集合。
        output_root: normalized 输出目录。
        reference_version: 本次生成使用的参考版本。
    """

    clear_generated_outputs(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    reference_aliases = load_reference_entity_aliases(DEFAULT_REFERENCE_ROOT)
    resolver = load_champion_entity_resolver(DEFAULT_REFERENCE_ROOT, DEFAULT_OVERRIDES_ROOT)
    translation_context = build_translation_context(DEFAULT_REFERENCE_ROOT)
    pending_rows: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for record in records:
        normalized = build_normalized_record(record, translation_context=translation_context)
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

    shard_paths = assign_entity_shard_paths(grouped.keys())
    for (kind, alias), rows in sorted(grouped.items()):
        path = output_root / shard_paths[(kind, alias)]
        write_normalized_jsonl(rows, path)

    write_manifest(grouped, output_root / "manifest.json", reference_version)
    write_entity_summary(grouped, output_root / "entity-summary.json", shard_paths)


def build_translation_context(reference_root: Path = DEFAULT_REFERENCE_ROOT) -> TranslationBuildContext:
    """创建 normalized translation 构建上下文。

    Args:
        reference_root: 仓库 reference 合集根目录。

    Returns:
        包含 reference label 的 translation 构建上下文。
    """

    if not reference_root.exists():
        return TranslationBuildContext()

    collection = load_reference_collection(reference_root)
    champion_labels = {
        str(row.get("entity", {}).get("alias")): reference_labels(row.get("entity", {}))
        for row in collection.champions.values()
        if isinstance(row.get("entity"), dict) and row.get("entity", {}).get("alias")
    }
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
    return TranslationBuildContext(champion_labels=champion_labels, skin_labels=skin_labels)


def infer_champion_labels_from_skin_labels(
    champion_labels: dict[str, dict[str, str]],
    skin_labels: dict[tuple[str, str], dict[str, str]],
) -> None:
    """从中文皮肤全名中反推英雄中文名。

    Data Dragon zh_CN champion `name` 是称号，`title` 才是英雄名；当前
    reference 尚未保存 title，因此先从“皮肤名 英雄名”格式回填英雄名。

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

        label = max(sorted(counts), key=lambda value: counts[value])
        champion_labels.setdefault(champion_alias, {})["zh_CN"] = label


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

    for name in ("manifest.json", "entity-summary.json"):
        path = output_root / name
        if path.exists():
            path.unlink()

    by_entity = output_root / "by-entity"
    if not by_entity.exists():
        return

    for path in by_entity.rglob("*.jsonl"):
        path.unlink()

    for path in sorted((item for item in by_entity.rglob("*") if item.is_dir()), reverse=True):
        if not any(path.iterdir()):
            path.rmdir()

    if not any(by_entity.iterdir()):
        by_entity.rmdir()


def write_normalized_jsonl(rows: list[dict[str, object]], output_path: Path) -> None:
    """写出单个实体 JSONL 分片。

    Args:
        rows: normalized 记录。
        output_path: JSONL 输出路径。
    """

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in sorted(rows, key=lambda item: str(item["raw_event_name"])):
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def write_manifest(
    grouped: dict[tuple[str, str], list[dict[str, object]]],
    output_path: Path,
    reference_version: str,
) -> None:
    """写出 normalized manifest。

    Args:
        grouped: 按实体分组后的 normalized 记录。
        output_path: manifest 输出路径。
        reference_version: 本次生成使用的参考版本。
    """

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "reference_version": reference_version,
        "entity_count": len(grouped),
        "record_count": sum(len(rows) for rows in grouped.values()),
        "contains_full_raw_event_index": False,
        "outputs": ["manifest.json", "entity-summary.json", "by-entity/"],
    }
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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

    return records
