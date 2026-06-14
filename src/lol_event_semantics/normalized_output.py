"""对外 normalized 数据生成。"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .decomposition_model import EventDecompositionRecord

SCHEMA_VERSION = 1
TECHNICAL_DISPLAY_TOKENS = ("Play_vo", "Play_sfx", "cast3D", "2D", "3D", "voProcessing")


def build_normalized_record(record: EventDecompositionRecord) -> dict[str, object]:
    """把拆解记录转换为对外 normalized 记录。

    Args:
        record: 已完成 profile/reference enrichment 的拆解记录。

    Returns:
        可直接写入 JSONL 的 normalized 记录。
    """

    return {
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
        "display": {
            "zh_cn_short": build_short_display(record),
            "zh_cn_detail": build_detail_display(record),
            "debug_label": build_debug_label(record),
        },
        "quality": {
            "unknown_count": len(record.unknowns),
            "candidate_count": len(record.candidates),
            "diagnostics": list(record.diagnostics),
        },
        "source_paths": list(record.source_paths),
    }


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
        面向下游用户的短中文说明。
    """

    slots = record.semantic_slots
    owner = display_owner(record)
    spell_key = slots.get("spell_key") or slots.get("spell_key_hint")
    phase = slots.get("phase")

    if mode_key(slots.get("mode_context")) == "ultimate_spellbook" and spell_key == "r":
        champion = display_subject(slots) or owner
        return compact_display(f"终极魔典 {champion}大招{phase_label(phase)}")

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
        return compact_display(f"{owner}表情语音")

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
    grouped: dict[tuple[str, str], list[dict[str, object]]] = defaultdict(list)

    for record in records:
        normalized = build_normalized_record(record)
        entity = normalized["entity"]
        if not isinstance(entity, dict):
            continue
        grouped[(str(entity["kind"]), str(entity["alias"]))].append(normalized)

    for (kind, alias), rows in sorted(grouped.items()):
        path = output_root / "by-entity" / plural_entity_kind(kind) / f"{alias}.jsonl"
        write_normalized_jsonl(rows, path)

    write_manifest(grouped, output_root / "manifest.json", reference_version)
    write_entity_summary(grouped, output_root / "entity-summary.json")


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
) -> None:
    """写出实体汇总。

    Args:
        grouped: 按实体分组后的 normalized 记录。
        output_path: 汇总输出路径。
    """

    summary = [
        {"kind": kind, "alias": alias, "record_count": len(rows), "path": entity_shard_path(kind, alias)}
        for (kind, alias), rows in sorted(grouped.items())
    ]
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
