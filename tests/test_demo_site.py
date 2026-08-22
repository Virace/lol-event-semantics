from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from lol_event_semantics.demo_site import DemoSiteError, build_demo_site
from scripts.build_demo_site import main as build_demo_main


def test_build_demo_site_groups_mode_events_under_base_champions(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"

    summary = build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )

    assert summary.champion_count == 7
    assert summary.event_count == 5
    assert summary.map_count == 2
    assert summary.map_event_count == 2
    assert summary.unassigned_event_count == 3

    index = read_json(output_root / "data" / "index.json")
    assert index["schema_version"] == 2
    assert index["dataset"]["dataset_id"] == "local-v1-test"
    assert index["dataset"]["data_dragon_version"] == "16.16.1"
    assert index["counts"] == {
        "champion_events": 5,
        "champions": 7,
        "map_events": 2,
        "maps": 2,
        "source_records": 8,
        "unassigned_records": 3,
    }
    assert index["review"] == {
        "data_path": "data/review/index.json",
        "residual_records": 3,
        "unassigned_records": 3,
    }
    assert [champion["alias"] for champion in index["champions"]] == [
        "Aatrox",
        "Annie",
        "Ashe",
        "Malzahar",
        "Veigar",
        "MonkeyKing",
        "Ziggs",
    ]
    assert [(item["key"], item["event_count"]) for item in index["maps"]] == [
        ("map11", 1),
        ("map12", 1),
    ]
    annie_index = next(item for item in index["champions"] if item["alias"] == "Annie")
    assert "火女" in annie_index["search_terms"]
    assert annie_index["portrait_url"].endswith("/16.16.1/img/champion/Annie.png")

    annie = read_json(output_root / "data" / "champions" / "Annie.json")
    assert [skin["number"] for skin in annie["skins"]] == [0, 1, 2]
    assert annie["skins"][1]["splash_url"].endswith("/Annie_1.jpg")
    assert annie["skins"][2]["splash_url"].endswith("/Annie_1.jpg")
    assert annie["skins"][2]["splash_skin_number"] == 1
    assert annie["events"][0]["raw_event_name"] == "Play_vo_Annie_Spell3DQCast"
    assert annie["events"][0]["mode"] == {"key": "jade", "label": "经典模式", "prefix": "Jade"}
    assert annie["events"][0]["map"] == {
        "alias": "SR",
        "en_name": "Summoner's Rift",
        "id": 11,
        "key": "map11",
        "label": "召唤师峡谷",
    }
    assert annie["events"][0]["skin_number"] == 1

    veigar = read_json(output_root / "data" / "champions" / "Veigar.json")
    assert veigar["events"][0]["raw_event_name"] == "Play_sfx_Ruby_Boss_FNVeigar_MordeR_buffdeactivate"
    assert veigar["events"][0]["mode"]["key"] == "ruby"
    assert veigar["events"][0]["map"] == {
        "alias": None,
        "en_name": None,
        "id": None,
        "key": "base",
        "label": "通用 / 未指定",
    }
    assert veigar["events"][0]["zh_cn_short"] is None

    malzahar = read_json(output_root / "data" / "champions" / "Malzahar.json")
    assert malzahar["events"][0]["raw_event_name"] == "Play_sfx_Ruby_MalzaharBasicAttack2_OnHit"
    assert malzahar["events"][0]["mode"]["key"] == "ruby"

    ashe = read_json(output_root / "data" / "champions" / "Ashe.json")
    assert ashe["events"] == []

    ziggs = read_json(output_root / "data" / "champions" / "Ziggs.json")
    assert ziggs["events"] == []

    monkey_king = read_json(output_root / "data" / "champions" / "MonkeyKing.json")
    assert monkey_king["events"][0]["raw_event_name"] == "Play_sfx_Ruby_WukongNimbus_OnCast"

    aatrox = read_json(output_root / "data" / "champions" / "Aatrox.json")
    assert aatrox["events"][0]["mode"] == {
        "key": "strawberry",
        "label": "PVE 模式",
        "prefix": "Strawberry",
    }

    summoners_rift = read_json(output_root / "data" / "maps" / "map11.json")
    assert summoners_rift["schema_version"] == 2
    assert summoners_rift["map"] == {
        "alias": "SR",
        "en_name": "Summoner's Rift",
        "id": 11,
        "key": "map11",
        "label": "召唤师峡谷",
    }
    assert [event["record_id"] for event in summoners_rift["events"]] == ["record-annie"]
    assert summoners_rift["events"][0]["entity"] == {"alias": "Annie", "kind": "champion"}

    random_map = read_json(output_root / "data" / "maps" / "map12.json")
    assert [event["record_id"] for event in random_map["events"]] == ["record-unassigned"]
    assert random_map["events"][0]["entity"] == {"alias": "Ruby", "kind": "map_or_system"}

    review = read_json(output_root / "data" / "review" / "index.json")
    assert review["dataset"]["dataset_id"] == "local-v1-test"
    assert review["counts"] == {
        "residual_issues": 4,
        "residual_records": 3,
        "unassigned_records": 3,
    }
    assert review["scopes"] == [
        {
            "key": "champion",
            "label": "英雄",
            "residual_records": 2,
            "unassigned_records": 2,
        },
        {
            "key": "map",
            "label": "地图",
            "residual_records": 1,
            "unassigned_records": 1,
        },
    ]
    assert [(item["key"], item["record_count"]) for item in review["layers"]] == [
        ("contract_shape", 0),
        ("coverage", 1),
        ("semantic_projection", 1),
        ("review_evidence", 1),
    ]
    assert review["queues"] == [
        {
            "data_path": "data/review/residuals.json",
            "key": "residual",
            "label": "SFX 残差",
            "record_count": 3,
            "scope_counts": {"champion": 2, "map": 1},
        },
        {
            "data_path": "data/review/unassigned.json",
            "key": "unassigned",
            "label": "未归属记录",
            "record_count": 3,
            "scope_counts": {"champion": 2, "map": 1},
        },
    ]
    layer_by_key = {item["key"]: item for item in review["layers"]}
    assert layer_by_key["review_evidence"]["scope_record_counts"] == {"champion": 0, "map": 1}
    assert layer_by_key["review_evidence"]["scope_issue_counts"] == {"champion": 0, "map": 2}

    residuals = read_json(output_root / "data" / "review" / "residuals.json")["records"]
    residual = next(item for item in residuals if item["record_id"] == "record-unassigned")
    assert residual["layers"] == ["review_evidence"]
    assert [issue["code"] for issue in residual["issues"]] == [
        "translation_argument_low_confidence",
        "translation_label_fallback",
    ]
    assert residual["locator"] == {
        "raw_event_name": "Play_sfx_Ruby_Boss_UnknownBoss_Charge",
        "record_id": "record-unassigned",
    }
    assert residual["reason_code"] == "validator_issue"
    assert residual["review_scope"] == "map"

    unassigned = read_json(output_root / "data" / "review" / "unassigned.json")["records"]
    assert {item["record_id"] for item in unassigned} == {
        "record-strawberry-dasher",
        "record-ruby-mission-ziggs",
        "record-unassigned",
    }
    unassigned_by_id = {item["record_id"]: item for item in unassigned}
    assert unassigned_by_id["record-unassigned"]["map"] == {
        "alias": "HA",
        "en_name": "Random Map",
        "id": 12,
        "key": "map12",
        "label": "随机地图",
    }
    assert unassigned_by_id["record-ruby-mission-ziggs"]["map"]["key"] == "base"
    assert unassigned_by_id["record-ruby-mission-ziggs"]["review_scope"] == "champion"
    assert "semantic" not in unassigned[0]
    assert {item["reason_code"] for item in unassigned} == {"no_safe_champion_assignment"}


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


def test_build_demo_site_rejects_conflicting_explicit_map_identity(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    events_path = normalized_root / "normalized-events.jsonl"
    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    records[0]["semantic"]["slots"]["map"]["key"] = "map12"
    events_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

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


def test_demo_assets_keep_accessibility_security_and_responsive_contract(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )

    html = (output_root / "index.html").read_text(encoding="utf-8")
    javascript = (output_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (output_root / "styles.css").read_text(encoding="utf-8")
    dom = DemoDom()
    dom.feed(html)
    element_block = re.search(
        r"const elements = Object\.fromEntries\(\s*\[(.*?)\]\.map",
        javascript,
        re.DOTALL,
    )
    assert element_block is not None
    referenced_element_ids = set(re.findall(r'"([A-Za-z][A-Za-z0-9]+)"', element_block.group(1)))
    assert referenced_element_ids.issubset(dom.tags_by_id)

    assert 'lang="zh-CN"' in html
    assert 'name="theme-color" content="#f7f5f0"' in html
    assert 'rel="preconnect" href="https://ddragon.leagueoflegends.com"' in html
    assert 'aria-live="polite"' in html
    assert "isn't endorsed by Riot Games" in html
    assert "innerHTML" not in javascript
    assert "textContent" in javascript
    assert 'setAttribute("translate", "no")' in javascript
    assert 'eventFilters.addEventListener("submit"' in javascript
    assert 'reviewFilters.addEventListener("submit"' in javascript
    assert 'fetchJson("data/review/index.json")' in javascript
    assert 'params.get("status")' in javascript
    assert 'params.get("type")' in javascript
    assert 'params.get("view")' in javascript
    assert 'params.get("queue")' in javascript
    assert 'params.get("layer")' in javascript
    assert 'params.get("code")' in javascript
    assert 'params.get("family")' in javascript
    assert 'params.get("entity")' in javascript
    assert 'params.get("status")' in javascript
    assert 'params.get("record")' in javascript
    assert "window.history.replaceState" in javascript
    assert {
        "confirmedStatusButton",
        "reviewStatusButton",
        "championEntityButton",
        "mapEntityButton",
        "datasetDisclosure",
        "datasetSummaryInline",
        "browseNavigation",
        "browseSearch",
        "browseList",
        "championView",
        "mapView",
        "mapEventFilters",
        "mapEventTableBody",
        "reviewView",
        "reviewLayerCards",
        "reviewFilters",
        "reviewQueue",
        "reviewSearch",
        "reviewLayer",
        "reviewCode",
        "reviewFamily",
        "reviewEntity",
        "reviewMap",
        "reviewStatus",
        "reviewTableBody",
        "reviewDetail",
        "reviewDetailLocator",
    }.issubset(dom.tags_by_id)
    assert dom.tags_by_id["reviewFilters"] == "form"
    assert dom.tags_by_id["mapEventFilters"] == "form"
    assert dom.tags_by_id["datasetDisclosure"] == "details"
    assert dom.tags_by_id["reviewQueue"] == "select"
    assert dom.tags_by_id["mapFilter"] == "select"
    assert dom.tags_by_id["reviewMap"] == "select"
    assert dom.tags_by_id["reviewDetailLocator"] == "code"
    assert len(dom.ids) == len(set(dom.ids))
    assert "@media (max-height: 900px) and (min-width: 901px)" in stylesheet
    assert "@media (max-width: 600px)" in stylesheet
    assert ":focus-visible" in stylesheet
    assert "min-height: 44px" in stylesheet
    assert "linear-gradient" not in stylesheet


def test_demo_assets_are_ready_for_public_static_hosting(tmp_path: Path) -> None:
    normalized_root, validation_json, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized_root,
        validation_json=validation_json,
        reference_root=reference_root,
        output_root=output_root,
    )

    html = (output_root / "index.html").read_text(encoding="utf-8")
    javascript = (output_root / "app.js").read_text(encoding="utf-8")
    cloudflare_headers = (output_root / "_headers").read_text(encoding="utf-8")
    manifest = read_json(output_root / "site.webmanifest")
    champion = read_json(output_root / "data" / "champions" / "Annie.json")
    dom = DemoDom()
    dom.feed(html)

    assert (output_root / ".nojekyll").read_text(encoding="utf-8") == ""
    assert manifest["start_url"] == "."
    assert manifest["scope"] == "."
    assert manifest["lang"] == "zh-CN"
    assert champion["dataset"] == {
        "content_digest": "a" * 64,
        "dataset_id": "local-v1-test",
    }

    assert 'http-equiv="Content-Security-Policy"' in html
    assert 'name="referrer" content="no-referrer"' in html
    assert 'rel="manifest" href="site.webmanifest"' in html
    assert 'href="/"' not in html
    assert 'src="/"' not in html
    assert "PUBLIC DATA EXPLORER" in html
    assert "本地静态浏览器" not in html
    assert "本地数据投影" not in html
    assert dom.tags_by_id["copyPageLink"] == "button"

    assert 'url.searchParams.set("v", cacheVersion)' in javascript
    assert 'cache: isEntryPoint ? "no-cache" : "default"' in javascript
    assert "function assertDatasetIdentity" in javascript
    assert 'cache: "no-store"' not in javascript
    assert "请使用本地 HTTP 服务" not in javascript
    assert "请在站点目录运行本地 HTTP 服务" not in javascript

    assert "X-Content-Type-Options: nosniff" in cloudflare_headers
    assert "Content-Security-Policy:" in cloudflare_headers
    assert "/data/*" in cloudflare_headers


def write_demo_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_root = tmp_path / "test-run"
    normalized_root = run_root / "normalized"
    normalized_root.mkdir(parents=True)
    write_json(
        normalized_root / "manifest.json",
        {
            "schema_version": 2,
            "dataset_id": "local-v1-test",
            "content_digest": "a" * 64,
            "record_count": 8,
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
                "record_count": 8,
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
