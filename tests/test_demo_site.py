from __future__ import annotations

import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path

import pytest

from lol_event_semantics.dataset_identity import (
    content_digest,
    describe_payload_files,
    paths_digest,
    semantic_document_identity,
    sha256_file,
)
from lol_event_semantics.reference_collection import read_yaml
from lol_event_semantics.demo_site import DemoSiteError, build_demo_site
from lol_event_semantics.dataset_runs import DatasetRunError
from scripts.build_demo_site import main as build_demo_main


def test_demo_keeps_entity_assignment_and_feedback_orthogonal(tmp_path: Path) -> None:
    """已知装备不会因缺少英雄而进入反馈；未知候选保留同一浏览实体。"""

    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"
    summary = build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )
    index = read_json(output_root / "data/index.json")
    assert index["schema_version"] == 3
    assert summary.event_count == 5
    assert summary.map_event_count == 1
    assert summary.unassigned_event_count == 3
    assert index["counts"]["source_records"] == 11
    assert {entry["key"] for entry in index["categories"]} == {"champion", "item", "map", "mode_system", "unknown"}
    entries = {entry["key"]: entry for entry in index["entities"]}
    item = read_json(output_root / entries["item:1039"]["data_path"])
    event = item["events"][0]
    assert item["entity"]["label"] == "猎人的护身符"
    assert event["semantic_status"] == "determined"
    assert event["feedback_reasons"] == []
    assert event["support_level"] == "experimental"
    assert event["skin_number"] is None
    assert "semantic" not in event
    assert "translation" not in event
    assert "events" not in entries["item:1039"]
    unknown = read_json(output_root / entries["unknown:6688"]["data_path"])
    assert len(unknown["events"]) == 2
    assert all(event["semantic_status"] == "feedback" for event in unknown["events"])
    assert all(event["entity"]["candidate"] == {"kind": "item", "id": 6688} for event in unknown["events"])
    veigar = read_json(output_root / entries["champion:Veigar"]["data_path"])
    assert veigar["events"][0]["entity"]["alias"] == "Ruby_Veigar"
    assert veigar["events"][0]["semantic_status"] == "feedback"
    assert veigar["events"][0]["issues"][0]["code"] == "translation_missing_for_supported_frame"
    assert veigar["events"][0]["mode"]["key"] == "ruby"
    annie = read_json(output_root / entries["champion:Annie"]["data_path"])
    assert annie["events"][0]["skin_number"] == 1
    assert annie["events"][0]["map"]["label"] == "召唤师峡谷"
    assert annie["events"][0]["semantic_status"] == "determined"
    assert annie["events"][0]["support_level"] == "stable"
    assert [skin["number"] for skin in annie["skins"]] == [0, 1, 2]
    assert annie["skins"][2]["splash_skin_number"] == 1
    assert sum(entry["stats"]["events"] for entry in entries.values()) == 11
    assert all(
        read_json(output_root / entry["data_path"])["dataset"]["demo_id"] == index["dataset"]["demo_id"]
        for entry in entries.values()
    )


@pytest.mark.parametrize(
    ("alias", "map_id", "reference_alias", "zh_name"),
    [
        ("11", 11, "SR", "召唤师峡谷"),
        ("12", 12, "HA", "随机地图"),
        ("21", 21, "NB", "极限闪击"),
        ("30", 30, "TGR", "斗魂竞技场"),
        ("33", 33, "Strawberry", "无尽狂潮"),
        ("35", 35, "BDW", "班德尔之森"),
        ("Map11", 11, "SR", "召唤师峡谷"),
        ("SR", 11, "SR", "召唤师峡谷"),
    ],
)
def test_demo_localizes_confirmed_map_entities(
    tmp_path: Path, alias: str, map_id: int, reference_alias: str, zh_name: str
) -> None:
    """已确认地图的数字编号、技术别名与参考别名都使用本地中文名称。"""

    normalized, validation, reference = write_demo_inputs(tmp_path)
    path = normalized / "normalized-events.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    record = next(row for row in records if row["entity"]["kind"] == "map")
    raw = record["raw_event_name"]
    record["entity"]["alias"] = alias
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    write_map(reference, alias=reference_alias, map_id=map_id, en_name="Map", zh_name=zh_name)
    seal_demo_inputs(normalized, reference)
    output = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized, validation_json=validation, reference_root=reference, output_root=output
    )
    index = read_json(output / "data/index.json")
    entry = next(item for item in index["entities"] if item["kind"] == "map")
    document = read_json(output / entry["data_path"])
    assert entry["label"] == zh_name
    assert document["entity"]["label"] == zh_name
    assert document["entity"]["alias"] == alias
    assert document["events"][0]["raw_event_name"] == raw
    assert document["events"][0]["map"]["label"] == "通用 / 未指定"


@pytest.mark.parametrize(("quality_status", "expected"), [("passed", "candidate"), ("failed", "checks_failed")])
def test_demo_reports_dataset_quality_state(tmp_path: Path, quality_status: str, expected: str) -> None:
    """检查失败的诊断产物不能因中文解释可用而显示为待审候选或已批准。"""

    normalized, validation, reference = write_demo_inputs(tmp_path)
    manifest = read_json(normalized / "manifest.json")
    manifest["content_identity"]["quality_status"] = quality_status
    write_json(normalized / "manifest.json", manifest)
    seal_demo_inputs(normalized, reference)
    output = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized, validation_json=validation, reference_root=reference, output_root=output
    )
    index = read_json(output / "data/index.json")
    assert index["dataset"]["status"] == expected


def test_demo_binds_verified_approval_state_to_cache_identity(tmp_path: Path) -> None:
    """仅身份闭包有效的批准指针改变展示状态，并使批准前后的分片缓存分离。"""

    runs_root = tmp_path / "runs"
    normalized, validation, reference = write_demo_inputs(runs_root / "completed")
    manifest_path = normalized / "manifest.json"
    manifest = read_json(manifest_path)
    generator = {"commit": "a" * 40, "dirty": False}
    manifest["content_identity"]["input_identity"]["generator_state"] = generator
    manifest["content_digest"] = content_digest(manifest["content_identity"], manifest["files"])
    write_json(manifest_path, manifest)
    report = read_json(validation)
    report["summary"]["content_digest"] = manifest["content_digest"]
    write_json(validation, report)
    write_json(validation.parent / "quality-metrics.json", {})
    write_json(
        normalized.parent / "completion.json",
        {
            "schema_version": 1,
            "status": "completed",
            "run_id": normalized.parent.name,
            "dataset_id": manifest["dataset_id"],
            "content_digest": manifest["content_digest"],
            "normalized_manifest_sha256": sha256_file(manifest_path),
            "generator_state": generator,
            "quality_gate": "passed",
            "reports": [
                {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                for path in sorted(validation.parent.glob("*.json"))
            ],
        },
    )
    output = tmp_path / "site"
    kwargs = dict(normalized_root=normalized, validation_json=validation, reference_root=reference, output_root=output)
    build_demo_site(**kwargs)
    candidate = read_json(output / "data/index.json")["dataset"]
    assert candidate["status"] == "candidate"
    write_json(
        runs_root / "approved-local.json",
        {
            "schema_version": 1,
            "status": "approved_local",
            "run_id": normalized.parent.name,
            "dataset_id": manifest["dataset_id"],
            "content_digest": manifest["content_digest"],
            "relative_dataset_path": f"completed/{normalized.parent.name}/normalized",
        },
    )
    build_demo_site(**kwargs)
    approved = read_json(output / "data/index.json")["dataset"]
    assert approved["status"] == "approved_local"
    assert approved["content_digest"] == candidate["content_digest"]
    assert approved["demo_id"] != candidate["demo_id"]
    pointer = read_json(runs_root / "approved-local.json")
    pointer["content_digest"] = "0" * 64
    write_json(runs_root / "approved-local.json", pointer)
    with pytest.raises(DatasetRunError, match="身份不一致"):
        build_demo_site(**kwargs)


@pytest.mark.parametrize("with_record_id", [True, False])
def test_demo_does_not_spread_issues_across_same_raw(tmp_path: Path, with_record_id: bool) -> None:
    """同名不同实体仅关联自身问题，旧报告无法消歧时拒绝构建。"""

    normalized, validation, reference = write_demo_inputs(tmp_path)
    path = normalized / "normalized-events.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    records[-1]["raw_event_name"] = records[-3]["raw_event_name"]
    path.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    report = read_json(validation)
    issue = {"raw_event_name": records[-1]["raw_event_name"], "code": "unknown_entity_output"}
    if with_record_id:
        issue["record_id"] = records[-1]["record_id"]
    report["issues"].append(issue)
    write_json(validation, report)
    seal_demo_inputs(normalized, reference)
    args = dict(
        normalized_root=normalized, validation_json=validation, reference_root=reference, output_root=tmp_path / "site"
    )
    if not with_record_id:
        with pytest.raises(DemoSiteError, match="ambiguous legacy"):
            build_demo_site(**args)
        return
    build_demo_site(**args)
    index = read_json(tmp_path / "site/data/index.json")
    entries = {entry["key"]: entry for entry in index["entities"]}
    item = read_json(tmp_path / "site" / entries["item:1039"]["data_path"])
    assert item["events"][0]["issues"] == []
    unknown = read_json(tmp_path / "site" / entries["unknown:6688"]["data_path"])
    assert sum(bool(event["issues"]) for event in unknown["events"]) == 1


@pytest.mark.parametrize("mutation", ["payload", "reference", "reference_manifest", "manifest", "validation"])
def test_demo_rejects_content_substitution_with_unchanged_counts(tmp_path: Path, mutation: str) -> None:
    """相同条数与路径不能掩盖不同输入内容。"""

    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    if mutation == "payload":
        path = normalized_root / "normalized-events.jsonl"
        path.write_text(path.read_text(encoding="utf-8").replace("Q技能施放", "Q技能结束"), encoding="utf-8")
    elif mutation == "reference":
        path = reference_root / "champions/Annie.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("安妮", "不同名字"), encoding="utf-8")
    elif mutation == "reference_manifest":
        path = reference_root / "manifest.yaml"
        path.write_text(path.read_text(encoding="utf-8").replace("16.16.1", "16.17.1"), encoding="utf-8")
    elif mutation == "manifest":
        document = read_json(normalized_root / "manifest.json")
        document["content_digest"] = "0" * 64
        write_json(normalized_root / "manifest.json", document)
    else:
        document = read_json(validation_json)
        document["summary"]["content_digest"] = "0" * 64
        write_json(validation_json, document)
    with pytest.raises(DemoSiteError, match="does not match"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_build_demo_site_is_deterministic_and_refuses_unowned_output(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"

    build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )
    first_digest = directory_digest(output_root)
    build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )
    assert directory_digest(output_root) == first_digest

    occupied = tmp_path / "occupied"
    occupied.mkdir()
    (occupied / "user-file.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(DemoSiteError, match="generated marker"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=occupied,
        )

    assert (occupied / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_build_demo_site_rejects_validation_from_another_candidate(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    other_report = tmp_path / "other-run" / "reports" / "normalized-validation.json"
    other_report.parent.mkdir(parents=True)
    other_report.write_text(validation_json.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(DemoSiteError, match="same dataset run"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=other_report,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_build_demo_site_rejects_validation_record_count_mismatch(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    validation = read_json(validation_json)
    validation["summary"]["record_count"] = 7
    write_json(validation_json, validation)

    with pytest.raises(DemoSiteError, match="record count mismatch"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_build_demo_site_rejects_validation_claiming_another_run(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    validation = read_json(validation_json)
    validation["summary"]["normalized_root"] = str(tmp_path / ".staging" / "other-run" / "normalized")
    write_json(validation_json, validation)

    with pytest.raises(DemoSiteError, match="same dataset run"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_demo_rejects_legacy_entity_and_failed_validation(tmp_path: Path) -> None:
    """旧归属和硬错误数据不能被新的浏览布局伪装为已确认。"""

    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    report = read_json(validation_json)
    report["summary"]["error_count"] = 1
    write_json(validation_json, report)
    with pytest.raises(DemoSiteError, match="hard errors"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )
    report["summary"]["error_count"] = 0
    write_json(validation_json, report)
    path = normalized_root / "normalized-events.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    del records[0]["entity"]["attribution"]
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    seal_demo_inputs(normalized_root, reference_root)
    with pytest.raises(DemoSiteError, match="entity.attribution is required"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_build_demo_site_rejects_conflicting_explicit_map_identity(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    events_path = normalized_root / "normalized-events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    records[0]["semantic"]["slots"]["map"]["key"] = "map12"
    events_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    seal_demo_inputs(normalized_root, reference_root)
    with pytest.raises(DemoSiteError, match="conflicting map key and reference"):
        build_demo_site(
            normalized_root=normalized_root,
            validation_json=validation_json,
            reference_root=reference_root,
            output_root=tmp_path / "site",
        )


def test_demo_cli_requires_validation_for_explicit_candidate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = build_demo_main(
        [
            "--normalized-root",
            str(tmp_path / "candidate" / "normalized"),
            "--output-root",
            str(tmp_path / "site"),
        ]
    )

    assert exit_code == 2
    assert "--validation-json is required with --normalized-root" in capsys.readouterr().err


def test_demo_assets_keep_accessible_controls_and_static_hosting(tmp_path: Path) -> None:
    """静态包保留键盘控件、唯一DOM标识、安全响应头与相对资源。"""

    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )
    html = (output_root / "index.html").read_text(encoding="utf-8")
    dom = DemoDom()
    dom.feed(html)
    assert len(dom.ids) == len(set(dom.ids))
    assert dom.tags_by_id["semanticStatus"] == "select"
    assert dom.tags_by_id["entityType"] == "select"
    assert dom.tags_by_id["eventFilters"] == "form"
    assert dom.tags_by_id["datasetDisclosure"] == "details"
    assert dom.tags_by_id["copyPageLink"] == "button"
    assert 'lang="zh-CN"' in html
    assert 'aria-live="polite"' in html
    assert 'http-equiv="Content-Security-Policy"' in html
    assert 'name="referrer" content="no-referrer"' in html
    assert "isn't endorsed by Riot Games" in html
    assert (output_root / ".nojekyll").is_file()
    assert read_json(output_root / "site.webmanifest")["start_url"] == "."
    assert "X-Content-Type-Options: nosniff" in (output_root / "_headers").read_text(encoding="utf-8")


def write_demo_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    """生成含显式归属、语义状态与完整内容身份的代表语料。"""

    run_root = tmp_path / "test-run"
    normalized_root = run_root / "normalized"
    normalized_root.mkdir(parents=True)
    write_json(
        normalized_root / "manifest.json",
        {
            "schema_version": 2,
            "dataset_id": "local-v1-test",
            "content_digest": "a" * 64,
            "record_count": 11,
            "content_identity": {"base_game_version": "16.16"},
        },
    )
    records = [
        {
            "record_id": "record-annie",
            "raw_event_name": "Play_vo_Annie_Spell3DQCast",
            "asset_kind": "vo",
            "entity": {"kind": "champion", "alias": "Annie"},
            "display": {"tier": 1, "zh_cn_short": "Q技能施放", "zh_cn_detail": "安妮 Q技能施放"},
            "support_level": "stable",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "spell",
                "slots": {
                    "map": {
                        "alias": "SR",
                        "id": 11,
                        "key": "map11",
                        "kind": "map",
                        "label": "Summoner's Rift",
                        "metadata": {"reference_alias": "SR"},
                        "source": "reference/maps",
                    },
                    "mode_context": {"key": "jade", "label": "经典模式"},
                },
            },
            "semantic_frame": {
                "frame_type": "ability_cast_event",
                "arguments": {"skin": {"token": "Skin01"}},
            },
            "warnings": [],
        },
        {
            "record_id": "record-ruby-veigar",
            "raw_event_name": "Play_sfx_Ruby_Boss_FNVeigar_MordeR_buffdeactivate",
            "asset_kind": "sfx",
            "entity": {"kind": "champion", "alias": "Ruby"},
            "display": {"tier": 2, "zh_cn_short": None, "zh_cn_detail": "Ruby状态结束（末日人机）"},
            "support_level": "experimental",
            "render_status": "missing",
            "semantic": {
                "primary_family": "mode_entity",
                "slots": {
                    "mode_context": {"key": "ruby", "label": "末日人机"},
                    "mode_actor": {"raw": "FNVeigar"},
                },
            },
            "semantic_frame": None,
            "warnings": ["translation_missing"],
        },
        {
            "record_id": "record-ruby-malzahar",
            "raw_event_name": "Play_sfx_Ruby_MalzaharBasicAttack2_OnHit",
            "asset_kind": "sfx",
            "entity": {"kind": "champion", "alias": "Ruby"},
            "display": {
                "tier": 2,
                "zh_cn_short": "普攻命中",
                "zh_cn_detail": "玛尔扎哈普攻命中（末日人机）",
            },
            "support_level": "experimental",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "sfx",
                "slots": {"mode_context": {"key": "ruby", "label": "末日人机"}},
            },
            "semantic_frame": None,
            "warnings": [],
        },
        {
            "record_id": "record-strawberry-aatrox",
            "raw_event_name": "Play_sfx_Strawberry_Boss_Aatrox_Charge",
            "asset_kind": "sfx",
            "entity": {"kind": "map_or_system", "alias": "Strawberry"},
            "display": {"tier": 2, "zh_cn_short": "蓄力", "zh_cn_detail": "亚托克斯蓄力（PVE 模式）"},
            "support_level": "experimental",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "mode_entity",
                "slots": {
                    "mode_context": {"key": "strawberry", "label": "PVE 模式"},
                    "mode_actor": {"alias": "Aatrox"},
                },
            },
            "semantic_frame": None,
            "warnings": [],
        },
        {
            "record_id": "record-strawberry-dasher",
            "raw_event_name": "Play_sfx_Strawberry_Miniboss_Dasher_Dash_OnCast",
            "asset_kind": "sfx",
            "entity": {"kind": "map_or_system", "alias": "Strawberry"},
            "display": {"tier": 3, "zh_cn_short": "突进", "zh_cn_detail": "小首领突进"},
            "support_level": "experimental",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "mode_entity",
                "slots": {"mode_context": {"key": "strawberry", "label": "PVE 模式"}},
            },
            "semantic_frame": None,
            "warnings": [],
        },
        {
            "record_id": "record-ruby-mission-ziggs",
            "raw_event_name": "Play_mus_map11_MODES_25s3_Ruby_mission_mega_ziggs",
            "asset_kind": "music",
            "entity": {"kind": "map_or_system", "alias": "map11"},
            "display": {"tier": 3, "zh_cn_short": "任务音乐", "zh_cn_detail": "大型任务音乐"},
            "support_level": "experimental",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "mode_entity",
                "slots": {"mode_context": {"key": "ruby", "label": "末日人机"}},
            },
            "semantic_frame": None,
            "warnings": [],
        },
        {
            "record_id": "record-ruby-wukong",
            "raw_event_name": "Play_sfx_Ruby_WukongNimbus_OnCast",
            "asset_kind": "sfx",
            "entity": {"kind": "champion", "alias": "Ruby"},
            "display": {"tier": 2, "zh_cn_short": "突进", "zh_cn_detail": "孙悟空突进（末日人机）"},
            "support_level": "experimental",
            "render_status": "rendered",
            "semantic": {
                "primary_family": "sfx",
                "slots": {"mode_context": {"key": "ruby", "label": "末日人机"}},
            },
            "semantic_frame": None,
            "warnings": [],
        },
        {
            "record_id": "record-unassigned",
            "raw_event_name": "Play_sfx_Ruby_Boss_UnknownBoss_Charge",
            "asset_kind": "sfx",
            "entity": {"kind": "map_or_system", "alias": "Ruby"},
            "display": {"tier": 3, "zh_cn_short": None, "zh_cn_detail": "Ruby蓄力（末日人机）"},
            "support_level": "experimental",
            "render_status": "missing",
            "semantic": {
                "primary_family": "mode_entity",
                "slots": {
                    "map": {
                        "alias": "HA",
                        "id": 12,
                        "key": "map12",
                        "kind": "map",
                        "label": "Random Map",
                        "metadata": {"reference_alias": "HA"},
                        "source": "reference/maps",
                    },
                    "mode_context": {"key": "ruby", "label": "末日人机"},
                    "mode_actor": {"raw": "UnknownBoss"},
                },
            },
            "semantic_frame": None,
            "warnings": [],
        },
    ]
    owners = [
        ("champion", "Annie", None),
        ("champion", "Ruby_Veigar", "Veigar"),
        ("champion", "Ruby_Malzahar", "Malzahar"),
        ("champion", "Strawberry_Aatrox", "Aatrox"),
        ("mode_unit", "Strawberry_Dasher", None),
        ("map", "map11", None),
        ("champion", "Ruby_MonkeyKing", "MonkeyKing"),
        ("unknown", "UnknownBoss", None),
    ]
    for record, (kind, alias, base) in zip(records, owners):
        record["entity"] = {
            "kind": kind,
            "alias": alias,
            "attribution": {
                "basis": "unresolved" if kind == "unknown" else "source_asset",
                "confidence": "low" if kind == "unknown" else "high",
                "evidence": [],
            },
        }
        if base:
            record["entity"]["base_champion"] = {"kind": "champion", "alias": base, "basis": "reference_alias_suffix"}
    records.append(
        {
            "record_id": "record-item",
            "raw_event_name": "Play_sfx_1039_Activate",
            "asset_kind": "sfx",
            "entity": {
                "kind": "item",
                "alias": "1039",
                "attribution": {"basis": "reference_item_id", "confidence": "high", "evidence": []},
            },
            "display": {"zh_cn_short": "激活", "zh_cn_detail": "猎人的护身符激活"},
            "support_level": "experimental",
            "render_status": "rendered",
            "warnings": [],
        }
    )
    for index, raw in enumerate(["Play_sfx_6688_Activate", "Play_sfx_UNKNOWN_6688_Activate"]):
        records.append(
            {
                "record_id": f"record-unknown-{index}",
                "raw_event_name": raw,
                "asset_kind": "sfx",
                "entity": {
                    "kind": "unknown",
                    "alias": "6688",
                    "candidate": {"kind": "item", "id": 6688},
                    "attribution": {"basis": "unresolved", "confidence": "low", "evidence": []},
                },
                "display": {"zh_cn_short": "激活"},
                "support_level": "experimental",
                "render_status": "rendered",
                "warnings": [],
            }
        )
    (normalized_root / "normalized-events.jsonl").write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    validation_json = run_root / "reports" / "normalized-validation.json"
    validation_json.parent.mkdir()
    write_json(
        validation_json,
        {
            "issues": [
                {
                    "code": "translation_missing_for_supported_frame",
                    "message": "supported frame 缺少 translation。",
                    "path": "by-entity/champions/Ruby.jsonl:1",
                    "raw_event_name": "Play_sfx_Ruby_Boss_FNVeigar_MordeR_buffdeactivate",
                    "severity": "warning",
                },
                {
                    "code": "display_drops_parsed_slot",
                    "message": "展示 detail 丢弃已解析语义槽位。",
                    "path": "by-entity/maps/Ruby.jsonl:1",
                    "raw_event_name": "Play_sfx_Strawberry_Miniboss_Dasher_Dash_OnCast",
                    "severity": "warning",
                },
                {
                    "code": "translation_argument_low_confidence",
                    "message": "argument confidence=low。",
                    "path": "by-entity/maps/Ruby.jsonl:2.translation.arguments.action",
                    "raw_event_name": "Play_sfx_Ruby_Boss_UnknownBoss_Charge",
                    "severity": "warning",
                },
                {
                    "code": "translation_label_fallback",
                    "message": "argument 使用 fallback label。",
                    "path": "by-entity/maps/Ruby.jsonl:2.translation.arguments.action",
                    "raw_event_name": "Play_sfx_Ruby_Boss_UnknownBoss_Charge",
                    "severity": "warning",
                },
            ],
            "summary": {
                "entity_count": 4,
                "error_count": 0,
                "normalized_root": str(tmp_path / ".staging" / "test-run" / "normalized"),
                "record_count": 11,
                "warning_count": 4,
            },
        },
    )

    reference_root = tmp_path / "reference"
    (reference_root / "champions").mkdir(parents=True)
    (reference_root / "maps").mkdir(parents=True)
    (reference_root / "skins" / "Annie").mkdir(parents=True)
    (reference_root / "manifest.yaml").write_text(
        "schema_version: 2\nsource_versions:\n  data_dragon:\n    version: 16.16.1\n",
        encoding="utf-8",
    )
    write_champion(reference_root, alias="Aatrox", champion_id=266, en_name="Aatrox", zh_name="亚托克斯")
    write_champion(reference_root, alias="Ashe", champion_id=22, en_name="Ashe", zh_name="艾希")
    write_champion(
        reference_root,
        alias="Annie",
        champion_id=1,
        en_name="Annie",
        zh_name="安妮",
        title="黑暗之女",
        nickname="火女",
    )
    write_champion(reference_root, alias="Veigar", champion_id=45, en_name="Veigar", zh_name="维迦")
    write_champion(
        reference_root,
        alias="MonkeyKing",
        champion_id=62,
        en_name="Wukong",
        zh_name="孙悟空",
    )
    write_champion(reference_root, alias="Ziggs", champion_id=115, en_name="Ziggs", zh_name="吉格斯")
    write_champion(
        reference_root,
        alias="Malzahar",
        champion_id=90,
        en_name="Malzahar",
        zh_name="玛尔扎哈",
    )
    write_champion(reference_root, alias="Jade_Annie", champion_id=100001, en_name="Jade Annie", zh_name="经典安妮")
    (reference_root / "champions" / "None.yaml").write_text(
        """schema_version: 2
entity:
  type: champion
  id: -1
  alias: None
  names:
    en_US:
      - value: None
""",
        encoding="utf-8",
    )
    write_map(
        reference_root,
        alias="SR",
        map_id=11,
        en_name="Summoner's Rift",
        zh_name="召唤师峡谷",
    )
    write_map(
        reference_root,
        alias="HA",
        map_id=12,
        en_name="Random Map",
        zh_name="随机地图",
    )
    (reference_root / "skins" / "Annie" / "Skin01.yaml").write_text(
        """schema_version: 2
entity:
  type: skin
  champion_alias: Annie
  champion_id: 1
  skin_number: 1
  skin_token: Skin01
  names:
    en_US:
      - value: Goth Annie
    zh_CN:
      - value: 哥特萝莉 安妮
""",
        encoding="utf-8",
    )
    (reference_root / "skins" / "Annie" / "Skin02.yaml").write_text(
        """schema_version: 2
entity:
  type: skin
  champion_alias: Annie
  champion_id: 1
  skin_number: 2
  skin_token: Skin02
  names:
    en_US:
      - value: Goth  Annie (Ruby)
    zh_CN:
      - value: 哥特萝莉 安妮 红宝石
""",
        encoding="utf-8",
    )
    (reference_root / "items").mkdir()
    (reference_root / "items/1039.yaml").write_text(
        "entity:\n  type: item\n  id: 1039\n  names:\n    zh_CN:\n      - value: 猎人的护身符\n", encoding="utf-8"
    )
    seal_demo_inputs(normalized_root, reference_root)
    return normalized_root, validation_json, reference_root


def write_champion(
    reference_root: Path,
    *,
    alias: str,
    champion_id: int,
    en_name: str,
    zh_name: str,
    title: str | None = None,
    nickname: str | None = None,
) -> None:
    title_block = f"  title:\n    zh_CN:\n      - value: {title}\n" if title else ""
    nickname_block = f"  nicknames:\n    zh_CN:\n      - value: {nickname}\n" if nickname else ""
    (reference_root / "champions" / f"{alias}.yaml").write_text(
        f"""schema_version: 2
entity:
  type: champion
  id: {champion_id}
  alias: {alias}
  names:
    en_US:
      - value: {en_name}
    zh_CN:
      - value: {zh_name}
{title_block}{nickname_block}""",
        encoding="utf-8",
    )


def write_map(
    reference_root: Path,
    *,
    alias: str,
    map_id: int,
    en_name: str,
    zh_name: str,
) -> None:
    (reference_root / "maps" / f"{alias}.yaml").write_text(
        f"""schema_version: 2
entity:
  type: map
  id: {map_id}
  alias: {alias}
  names:
    en_US:
      - value: {en_name}
    zh_CN:
      - value: {zh_name}
""",
        encoding="utf-8",
    )


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class DemoDom(HTMLParser):
    """收集 Demo 静态 DOM 的公开元素 ID 合同。"""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []
        self.tags_by_id: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """记录带 ID 的开始标签。"""

        element_id = dict(attrs).get("id")
        if element_id is not None:
            self.ids.append(element_id)
            self.tags_by_id[element_id] = tag


def seal_demo_inputs(normalized_root: Path, reference_root: Path) -> None:
    """给合成语料附上与生产算法一致的文件与参考身份。"""

    manifest = read_json(normalized_root / "manifest.json")
    paths = [path for path in reference_root.rglob("*") if path.is_file() and path != reference_root / "manifest.yaml"]
    manifest["content_identity"]["input_identity"] = {
        "inputs": {
            "reference": paths_digest(reference_root, paths).to_dict(),
            "reference_manifest": semantic_document_identity(
                read_yaml(reference_root / "manifest.yaml"), excluded_keys={"updated_at"}
            ),
        }
    }
    manifest["files"] = describe_payload_files(normalized_root)
    manifest["content_digest"] = content_digest(manifest["content_identity"], manifest["files"])
    write_json(normalized_root / "manifest.json", manifest)
    report_path = normalized_root.parent / "reports/normalized-validation.json"
    report = read_json(report_path)
    report["summary"]["dataset_id"] = manifest["dataset_id"]
    report["summary"]["content_digest"] = manifest["content_digest"]
    write_json(report_path, report)
