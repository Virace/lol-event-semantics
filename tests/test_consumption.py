"""验证不同消费 profile 保留同一触发解释、来源定位与状态。"""

from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from lol_event_semantics.consumption import (
    FLAGS,
    build_consumption,
    canonical_bytes,
    describe_files,
    pack_rows,
    project_record,
    read_package_records,
    validate_consumption,
    verify_package,
)
from lol_event_semantics.dataset_identity import content_digest, describe_payload_files


def argument(role: str, label: str, **values: object) -> dict[str, object]:
    """创建有独立预期中文的最小正式参数。"""

    return {"role": role, "labels": {"zh_CN": label}, "confidence": "high", "provenance": {}, **values}


def example(raw: str = "Play_vo_AhriSkin01_Q_cast") -> dict[str, object]:
    """构造主体、皮肤、模式和阶段均存在的事件。"""

    return {
        "raw_event_name": raw,
        "record_id": "record-v1-" + "1" * 64,
        "entity": {"kind": "champion", "alias": "Ahri", "attribution": {"basis": "source_asset"}},
        "asset_kind": "vo",
        "support_level": "experimental",
        "render_status": "rendered",
        "review_status": "pending",
        "translation": {
            "schema_version": 1,
            "frame_type": "ability_state_change",
            "frame_version": 1,
            "frame_confidence": "high",
            "arguments": {
                "context": argument("context", "特殊模式", key="test_mode"),
                "champion": argument("champion", "阿狸", alias="Ahri", omit_when_context_known=["champion"]),
                "skin": argument("skin", "高丽风情 阿狸", token="Skin01", omit_when_context_known=["skin"]),
                "ability_slot": argument("ability_slot", "Q技能", key="q"),
                "phase": argument("phase", "释放", key="cast"),
            },
        },
    }


def write_source(root: Path, rows: list[dict[str, object]]) -> None:
    """写出带真实字节摘要的小型本地源，避免依赖全量语料。"""

    root.mkdir()
    (root / "normalized-events.jsonl").write_bytes(b"".join(canonical_bytes(row) + b"\n" for row in rows))
    identity = {"profile": "consumption-test"}
    files = describe_payload_files(root)
    digest = content_digest(identity, files)
    manifest = {
        "schema_version": 2,
        "content_identity": identity,
        "files": files,
        "content_digest": digest,
        "dataset_id": "local-v1-" + digest[:16],
        "record_count": len(rows),
    }
    (root / "manifest.json").write_bytes(canonical_bytes(manifest))


def test_subject_context_is_removed_but_mode_and_trigger_remain() -> None:
    """删除冗余主体不会删除模式、技能和触发阶段。"""

    row = project_record(example(), 7)
    assert row["label"] == "特殊模式 Q技能释放"
    assert row["full_label"] == "特殊模式 阿狸 高丽风情 Q技能释放"
    assert row["skin"] == 1
    assert (
        row["flags"] == FLAGS["raw_contains_skin"] | FLAGS["champion_context_omitted"] | FLAGS["skin_context_omitted"]
    )
    assert row["state"] == ["vo", "experimental", "rendered", "pending", "translation"]


def test_borrowed_champion_is_not_removed_as_subject() -> None:
    """模式或装备借用的英雄是事件含义，不能当主体上下文省略。"""

    row = example()
    row["entity"] = {"kind": "system", "alias": "augment"}
    projected = project_record(row, 1)
    assert "阿狸" in projected["label"]
    assert not projected["flags"] & FLAGS["champion_context_omitted"]


def test_missing_translation_keeps_preview_status_without_invention() -> None:
    """未解释事件既不删除，也不伪造正式中文解释。"""

    row = example("Play_sfx_6688Cast_OnCast")
    row.pop("translation")
    row["entity"] = {"kind": "unknown", "alias": "6688"}
    row["asset_kind"] = "sfx"
    row["render_status"] = "missing"
    result = project_record(row, 2)
    assert result["label"] is None
    assert result["flags"] & FLAGS["needs_review"]
    assert result["flags"] & FLAGS["unresolved_entity"]
    row["display"] = {"zh_cn_detail": "已有候选解释"}
    result = project_record(row, 2)
    assert result["label"] == "已有候选解释"
    assert result["state"][-1] == "preview"


def test_all_profiles_round_trip_without_overwriting_same_raw(tmp_path: Path) -> None:
    """三种格式均保留同名不同实体、状态、空文案和原始定位。"""

    first = example()
    other = copy.deepcopy(first)
    other["entity"] = {"kind": "champion", "alias": "Lux"}
    missing = example("NoExplanation")
    missing.pop("translation")
    missing["render_status"] = "missing"
    source = tmp_path / "source"
    write_source(source, [first, other, missing])
    result = build_consumption(source, tmp_path / "out")
    assert result["record_count"] == 3
    for product in result["products"]:
        root = tmp_path / "out" / product["profile"]
        assert validate_consumption(root, source)["record_count"] == 3
        rows = read_package_records(root, verify_package(root))
        assert sum(row["raw_event_name"] == first["raw_event_name"] for row in rows) == 2
        assert next(row for row in rows if row["raw_event_name"] == "NoExplanation")["label"] is None


def test_filters_are_explicit_and_empty_selection_is_valid(tmp_path: Path) -> None:
    """未命中筛选时输出合法空包，manifest仍声明来源和筛选范围。"""

    source = tmp_path / "source"
    write_source(source, [example()])
    result = build_consumption(source, tmp_path / "out", support_levels=["stable"])
    assert result["record_count"] == 0
    assert result["filters"]["support_levels"] == ["stable"]
    for profile in ("web", "software", "lookup"):
        assert validate_consumption(tmp_path / "out" / profile, source)["record_count"] == 0


def test_output_is_deterministic_and_existing_output_is_preserved(tmp_path: Path) -> None:
    """相同输入在不同目录生成相同文件，已有输出不被覆盖。"""

    source = tmp_path / "source"
    write_source(source, [example()])
    first = build_consumption(source, tmp_path / "a", profiles=["web"])
    second = build_consumption(source, tmp_path / "b", profiles=["web"])
    assert first == second
    assert verify_package(tmp_path / "a" / "web") == verify_package(tmp_path / "b" / "web")
    with pytest.raises(ValueError, match="already exists"):
        build_consumption(source, tmp_path / "a")


def test_source_and_consumer_tampering_are_rejected(tmp_path: Path) -> None:
    """摘要验证守住实际字节，而不是只信manifest的成功状态。"""

    source = tmp_path / "source"
    write_source(source, [example()])
    build_consumption(source, tmp_path / "out", profiles=["lookup"])
    path = tmp_path / "out" / "lookup" / "lookup.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hashes"):
        validate_consumption(path.parent)
    (source / "normalized-events.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hashes"):
        build_consumption(source, tmp_path / "other")


def test_dictionary_ids_do_not_replace_raw_record_identity() -> None:
    """文案可共用词条ID，事件原名及源定位仍逐条保留。"""

    rows = [project_record(example(raw), index) for index, raw in enumerate(("EventA", "EventB"), 1)]
    packed = pack_rows(rows)
    assert len(packed["labels"]) == 1
    assert [row[0] for row in packed["rows"]] == ["EventA", "EventB"]
    assert [row[-1] for row in packed["rows"]] == [1, 2]


@pytest.mark.parametrize("field", ["full_label", "record_id", "event_id"])
def test_software_source_validation_checks_extra_fields(tmp_path: Path, field: str) -> None:
    """即使重签消费包摘要，软件专属字段也必须与源投影一致。"""

    source = tmp_path / "source"
    write_source(source, [example()])
    build_consumption(source, tmp_path / "out", profiles=["software"])
    root = tmp_path / "out/software"
    path = root / "events.jsonl"
    row = json.loads(path.read_text(encoding="utf-8"))
    row[field] = "changed"
    path.write_bytes(canonical_bytes(row) + b"\n")
    path.with_name(path.name + ".gz").write_bytes(gzip.compress(path.read_bytes(), mtime=0))
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest.pop("package_id")
    manifest["files"] = describe_files(root)
    manifest["package_id"] = "consumption-v1-" + hashlib.sha256(canonical_bytes(manifest)).hexdigest()
    (root / "manifest.json").write_bytes(canonical_bytes(manifest))
    with pytest.raises(ValueError, match="software fields"):
        validate_consumption(root, source)
