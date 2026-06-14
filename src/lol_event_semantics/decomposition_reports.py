"""事件名拆解产物与报告输出。"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Iterable

from .decomposition_model import EventDecompositionRecord, UnknownKind


def write_jsonl(records: Iterable[EventDecompositionRecord], output_path: Path) -> None:
    """把拆解记录写成 JSONL。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for record in records:
            file.write(json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True))
            file.write("\n")


def write_reports(records: list[EventDecompositionRecord], report_root: Path) -> None:
    """输出 profile、未知项和疑难项报告。"""

    report_root.mkdir(parents=True, exist_ok=True)
    write_profile_summary(records, report_root / "profile-summary.md")
    unparsed_rows = aggregate_unknowns(
        records,
        {UnknownKind.UNPARSED_FRAGMENT, UnknownKind.UNKNOWN_HEAD_TOKEN, UnknownKind.UNKNOWN_TAIL_TOKEN},
    )
    ambiguity_rows = aggregate_unknowns(
        records,
        {
            UnknownKind.AMBIGUOUS_ENTITY,
            UnknownKind.AMBIGUOUS_NUMBER,
            UnknownKind.AMBIGUOUS_SPELL_ALIAS,
            UnknownKind.REQUIRES_CHAMPION_MECHANIC_CONTEXT,
            UnknownKind.NESTED_TRIGGER_UNRESOLVED,
        },
    )
    write_unknown_markdown("Unparsed Fragments", unparsed_rows, report_root / "unparsed-fragments.md")
    write_unknown_csv(unparsed_rows, report_root / "unparsed-fragments.csv")
    write_unknown_markdown("Semantic Ambiguities", ambiguity_rows, report_root / "semantic-ambiguities.md")
    write_unknown_csv(ambiguity_rows, report_root / "semantic-ambiguities.csv")
    batch_rows = aggregate_unknown_batches(records)
    write_unknown_batches_markdown(batch_rows, report_root / "unknown-batches.md")
    write_unknown_batches_csv(batch_rows, report_root / "unknown-batches.csv")
    write_model_hard_cases(records, report_root / "model-hard-cases.md")
    candidate_rows = collect_candidate_rows(records)
    write_candidate_markdown(candidate_rows, report_root / "candidate-review.md")
    write_candidate_csv(candidate_rows, report_root / "candidate-review.csv")
    write_candidate_jsonl(candidate_rows, report_root / "candidate-review.jsonl")


def write_profile_summary(records: list[EventDecompositionRecord], output_path: Path) -> None:
    """输出 profile 计数报告。"""

    counts = Counter(record.profile.semantic_profile.value for record in records)
    lines = ["# Profile Summary", "", "| profile | count |", "| --- | ---: |"]

    for profile, count in counts.most_common():
        lines.append(f"| `{profile}` | {count} |")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_unknown_markdown(title: str, rows: list[dict[str, str | int]], output_path: Path) -> None:
    """输出未知项 Markdown。"""

    lines = [f"# {title}", "", "| kind | raw | count | reason | examples |", "| --- | --- | ---: | --- | --- |"]

    for row in rows:
        lines.append(
            f"| `{row['kind']}` | `{row['raw']}` | {row['count']} | {row['blocking_reason']} | {row['examples']} |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_unknown_csv(rows: list[dict[str, str | int]], output_path: Path) -> None:
    """输出未知项 CSV。"""

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["kind", "raw", "count", "blocking_reason", "examples"])
        writer.writeheader()
        writer.writerows(rows)


def write_unknown_batches_markdown(rows: list[dict[str, str | int]], output_path: Path) -> None:
    """输出按处理批次拆分的未知项 Markdown。"""

    fields = ["batch", "category", "asset_kind", "kind", "raw", "count", "blocking_reason", "examples"]
    lines = [
        "# Unknown Batches",
        "",
        "| batch | category | asset_kind | kind | raw | count | reason | examples |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- |",
    ]

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(str(row[field]))
                if field not in {"batch", "raw"}
                else f"`{markdown_cell(str(row[field]))}`"
                for field in fields
            )
            + " |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_unknown_batches_csv(rows: list[dict[str, str | int]], output_path: Path) -> None:
    """输出按处理批次拆分的未知项 CSV。"""

    fields = ["batch", "category", "asset_kind", "kind", "raw", "count", "blocking_reason", "examples"]

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_model_hard_cases(records: list[EventDecompositionRecord], output_path: Path) -> None:
    """输出缺少外部上下文时模型也难判断的样本。"""

    hard_kinds = {
        UnknownKind.REQUIRES_CHAMPION_MECHANIC_CONTEXT.value,
        UnknownKind.AMBIGUOUS_SPELL_ALIAS.value,
        UnknownKind.AMBIGUOUS_ENTITY.value,
        UnknownKind.NESTED_TRIGGER_UNRESOLVED.value,
    }
    lines = ["# Model Hard Cases", ""]

    for record in records:
        if any(unknown.kind.value in hard_kinds for unknown in record.unknowns):
            lines.append(f"- `{record.raw_event_name}`")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_candidate_markdown(rows: list[dict[str, str]], output_path: Path) -> None:
    """输出候选解释 Markdown。"""

    lines = [
        "# Candidate Review",
        "",
        "| raw_event_name | kind | raw | meaning | confidence | score | evidence |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]

    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                markdown_cell(row[key])
                for key in ["raw_event_name", "kind", "raw", "meaning", "confidence", "score", "evidence"]
            )
            + " |"
        )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_candidate_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    """输出候选解释 CSV。"""

    fields = ["raw_event_name", "kind", "raw", "meaning", "confidence", "score", "evidence", "candidate_json"]

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_candidate_jsonl(rows: list[dict[str, str]], output_path: Path) -> None:
    """输出候选解释 JSONL，便于后续喂给模型或人工工具。"""

    with output_path.open("w", encoding="utf-8", newline="\n") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            file.write("\n")


def collect_candidate_rows(records: list[EventDecompositionRecord]) -> list[dict[str, str]]:
    """展开所有整句级候选解释。"""

    rows: list[dict[str, str]] = []

    for record in records:
        for candidate in record.candidates:
            rows.append(
                {
                    "raw_event_name": record.raw_event_name,
                    "kind": str(candidate.get("kind") or ""),
                    "raw": str(candidate.get("raw") or ""),
                    "meaning": str(candidate.get("meaning") or ""),
                    "confidence": str(candidate.get("confidence") or ""),
                    "score": str(candidate.get("score") or ""),
                    "evidence": str(candidate.get("evidence") or ""),
                    "candidate_json": json.dumps(candidate, ensure_ascii=False, sort_keys=True),
                }
            )

    return rows


def markdown_cell(value: str) -> str:
    """转义 Markdown 表格单元。"""

    return value.replace("|", "\\|").replace("\n", " ")


def aggregate_unknowns(
    records: list[EventDecompositionRecord],
    kinds: set[UnknownKind],
) -> list[dict[str, str | int]]:
    """按 unknown kind 和 raw 聚合。"""

    grouped: dict[tuple[str, str, str], dict[str, object]] = {}

    for record in records:
        for unknown in record.unknowns:
            if unknown.kind not in kinds:
                continue

            key = (unknown.kind.value, unknown.raw, unknown.blocking_reason)
            entry = grouped.setdefault(key, {"count": 0, "examples": []})
            entry["count"] = int(entry["count"]) + 1
            examples = entry["examples"]

            if isinstance(examples, list) and len(examples) < 3:
                examples.append(record.raw_event_name)

    rows = [
        {
            "kind": kind,
            "raw": raw,
            "count": int(entry["count"]),
            "blocking_reason": reason,
            "examples": "; ".join(str(item) for item in entry["examples"]),
        }
        for (kind, raw, reason), entry in grouped.items()
    ]
    rows.sort(key=lambda row: (-int(row["count"]), str(row["kind"]), str(row["raw"])))
    return rows


def aggregate_unknown_batches(records: list[EventDecompositionRecord]) -> list[dict[str, str | int]]:
    """按用户处理批次聚合所有未知项。"""

    grouped: dict[tuple[str, str, str, str, str, str], dict[str, object]] = {}

    for record in records:
        batch = record_unknown_batch(record)
        category = record_unknown_category(record, batch)
        asset_kind = record.shell.asset_kind or ""

        for unknown in record.unknowns:
            key = (
                batch,
                category,
                asset_kind,
                unknown.kind.value,
                unknown.raw,
                unknown.blocking_reason,
            )
            entry = grouped.setdefault(key, {"count": 0, "examples": []})
            entry["count"] = int(entry["count"]) + 1
            examples = entry["examples"]

            if isinstance(examples, list) and len(examples) < 3:
                examples.append(record.raw_event_name)

    rows = [
        {
            "batch": batch,
            "category": category,
            "asset_kind": asset_kind,
            "kind": kind,
            "raw": raw,
            "count": int(entry["count"]),
            "blocking_reason": reason,
            "examples": "; ".join(str(item) for item in entry["examples"]),
        }
        for (batch, category, asset_kind, kind, raw, reason), entry in grouped.items()
    ]
    rows.sort(
        key=lambda row: (
            batch_sort_key(str(row["batch"])),
            -int(row["count"]),
            str(row["category"]),
            str(row["kind"]),
            str(row["raw"]),
        )
    )
    return rows


def record_unknown_batch(record: EventDecompositionRecord) -> str:
    """返回未知项处理批次。"""

    asset_kind = record.shell.asset_kind

    if asset_kind == "vo":
        return "vo_primary"

    if asset_kind == "sfx":
        return "sfx"

    return "other"


def record_unknown_category(record: EventDecompositionRecord, batch: str) -> str:
    """返回批次内粗分类，便于人工分派。"""

    raw_lower = record.raw_event_name.lower()

    if batch == "sfx":
        if "nexusblitz" in raw_lower:
            return "mode_sfx"

        return "champion_or_map_sfx"

    if batch == "other":
        return "music_or_control"

    if "chat3d" in raw_lower or "_chat" in raw_lower:
        return "dialogue_chain"

    if "announcer" in raw_lower:
        return "announcer"

    if any(marker in raw_lower for marker in ["gamemode", "nexusblitz", "cherry", "strawberry", "ruby"]):
        return "special_mode_voice"

    return "champion_voice"


def batch_sort_key(batch: str) -> int:
    """返回批次排序键。"""

    order = {
        "vo_primary": 0,
        "sfx": 1,
        "other": 2,
    }
    return order.get(batch, 99)
