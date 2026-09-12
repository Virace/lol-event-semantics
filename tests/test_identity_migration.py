"""跨数据集身份保持来源连续性，并对歧义和真实降级保守处理。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from lol_event_semantics.identity_migration import build_identity_report, load_identity_rows
from lol_event_semantics.normalized_contract import event_id_for_normalized_record, record_id_for_normalized_record
from lol_event_semantics.tiered_quality import build_support_diff


def row(owner: str = "Annie", sources: tuple[str, ...] = ("champions/1",), level: str = "stable") -> dict[str, Any]:
    """以真实来源格式构造同名事件，来源和 owner 可独立变化。"""

    record: dict[str, Any] = {
        "raw_event_name": "Play_vo_Annie_Move",
        "entity": {"kind": "champion", "alias": owner},
        "source_paths": [f"raw/events/{source}.txt" for source in sources],
        "support_level": level,
        "pattern_id": "pattern-v1-" + "1" * 64,
    }
    record["record_id"] = record_id_for_normalized_record(record)
    return record


def by_id(*records: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """保留同 raw 不同 owner 的独立记录。"""

    return {record["record_id"]: record for record in records}


def write_dataset(root: Path, *records: dict[str, Any]) -> None:
    """写入比较器读取的真实 JSONL 和 catalog 边界。"""

    root.mkdir()
    (root / "normalized-events.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8"
    )
    (root / "manifest.json").write_text(json.dumps({"dataset_id": root.name}), encoding="utf-8")
    (root / "pattern-catalog.json").write_text(
        json.dumps({"patterns": [{"pattern_id": "pattern-v1-" + "1" * 64, "support_level": "stable"}]}),
        encoding="utf-8",
    )


def test_provenance_changes_snapshot_but_preserves_event_identity() -> None:
    """新增 maps 来源不改变逻辑事件的连续身份。"""

    previous, current = row(), row(sources=("champions/1", "maps/0"))
    assert previous["record_id"] != current["record_id"]
    assert event_id_for_normalized_record(previous) == event_id_for_normalized_record(current)
    report = build_identity_report(by_id(previous), by_id(current))
    assert report["matched_count"] == 1
    assert report["removed_record_ids"] == report["added_record_ids"] == []
    assert report["changes"][0]["basis"] == "event_id"
    assert report["changes"][0]["provenance_changed"] is True
    assert report["changes"][0]["owner_changed"] is False


def test_same_raw_different_owner_remains_distinct() -> None:
    """raw 不能成为跨 owner 的全局主键。"""

    annie, jinx = row(), row("Jinx", ("champions/222",))
    assert event_id_for_normalized_record(annie) != event_id_for_normalized_record(jinx)
    report = build_identity_report(by_id(annie, jinx), by_id(row(sources=("champions/1", "maps/0")), jinx))
    assert report["matched_count"] == 2
    assert not report["ambiguous"]


def test_owner_correction_has_explicit_evidenced_migration() -> None:
    """同 raw 和来源交集唯一时，报告 owner 纠正而不隐藏身份变化。"""

    previous, current = row("Dance3D"), row()
    report = build_identity_report(by_id(previous), by_id(current))
    change = report["changes"][0]
    assert change["basis"] == "raw_and_source_overlap"
    assert change["owner_changed"] is True
    assert change["previous_record_id"] == previous["record_id"]
    assert change["current_record_id"] == current["record_id"]
    assert change["previous_event_id"] != change["current_event_id"]


def test_owner_change_without_source_evidence_stays_unmatched() -> None:
    """相同 raw 不能替代跨 owner 迁移证据。"""

    previous, current = row(), row("Jinx", ("champions/222",))
    report = build_identity_report(by_id(previous), by_id(current))
    assert report["matched_count"] == 0
    assert report["removed_record_ids"] == [previous["record_id"]]


def test_competing_source_migrations_are_ambiguous() -> None:
    """一个旧 owner 对应多个来源重叠的新 owner 时不能任取第一条。"""

    report = build_identity_report(by_id(row("Dance3D")), by_id(row(), row("Ruby_Annie")))
    assert report["matched_count"] == 0
    assert len(report["ambiguous"]) == 1
    assert len(report["ambiguous"][0]["current_ids"]) == 2


def test_duplicate_continuity_keys_do_not_merge_records() -> None:
    """同一连续键对应多个审计记录时，要求显式处理而不合并。"""

    first, second = row(), row(sources=("maps/0",))
    report = build_identity_report(by_id(first, second), by_id(row(sources=("champions/1", "maps/0"))))
    assert report["matched_count"] == 0
    assert report["ambiguous"][0]["basis"] == "event_id"


def test_exact_match_does_not_make_duplicate_continuity_key_unique() -> None:
    """移除一个精确匹配后，不能把重复连续身份剩下的记录当成唯一。"""

    first, second = row(), row(sources=("maps/0",))
    report = build_identity_report(by_id(first, second), by_id(first, row(sources=("champions/1", "maps/0"))))
    assert report["matched_count"] == 1
    assert report["ambiguous"][0]["basis"] == "event_id"


def test_declared_event_id_cannot_override_owner_evidence() -> None:
    """输入自报的连续键不能把同 raw 不同 owner 强行合并。"""

    previous, current = row(), row("Jinx", ("champions/222",))
    current["event_id"] = event_id_for_normalized_record(previous)
    report = build_identity_report(by_id(previous), by_id(current))
    assert report["matched_count"] == 0


@pytest.mark.parametrize("level,passes", [("stable", True), ("experimental", False)])
def test_support_diff_preserves_continuity_but_blocks_real_downgrade(tmp_path: Path, level: str, passes: bool) -> None:
    """旧产物无需新字段；仅 provenance 变化通过，真实 Stable 降级仍阻断。"""

    previous, current = row(), row(sources=("champions/1", "maps/0"), level=level)
    write_dataset(tmp_path / "previous", previous)
    write_dataset(tmp_path / "current", current)
    diff = build_support_diff(tmp_path / "current", tmp_path / "previous")
    assert diff["passed"] is passes
    assert diff["review_required"] is True
    assert diff["stable_record_downgrades"] == ([] if passes else [previous["record_id"]])
    if not passes:
        assert diff["record_changes"][0]["previous_record_id"] == previous["record_id"]
        assert diff["record_changes"][0]["current_record_id"] == current["record_id"]


def test_owner_migration_does_not_bypass_pattern_gate(tmp_path: Path) -> None:
    """身份匹配只恢复连续性，不能将已批准 pattern 变更视为安全。"""

    previous, current = row("Dance3D"), row()
    current["pattern_id"] = "pattern-v1-" + "3" * 64
    write_dataset(tmp_path / "previous", previous)
    write_dataset(tmp_path / "current", current)
    diff = build_support_diff(tmp_path / "current", tmp_path / "previous")
    assert diff["passed"] is False
    assert diff["stable_record_downgrades"] == [previous["record_id"]]


def test_ambiguous_stable_migration_blocks(tmp_path: Path) -> None:
    """歧义候选都为 Stable 也不能掩盖旧 Stable 没有唯一后继。"""

    previous = row("Dance3D")
    write_dataset(tmp_path / "previous", previous)
    write_dataset(tmp_path / "current", row(), row("Ruby_Annie"))
    diff = build_support_diff(tmp_path / "current", tmp_path / "previous")
    assert diff["passed"] is False
    assert diff["identity_migration"]["ambiguous"]


def test_duplicate_snapshot_identity_is_rejected(tmp_path: Path) -> None:
    """重复 JSONL 行不能被 dict 覆盖后从门禁中消失。"""

    write_dataset(tmp_path / "dataset", row(), row())
    with pytest.raises(ValueError, match="duplicate record_id"):
        load_identity_rows(tmp_path / "dataset")


def test_missing_dataset_is_rejected(tmp_path: Path) -> None:
    """错误目录不能伪装成空数据集并生成成功报告。"""

    with pytest.raises(ValueError, match="not found"):
        load_identity_rows(tmp_path / "missing")
