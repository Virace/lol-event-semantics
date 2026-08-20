from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from lol_event_semantics.demo_site import DemoSiteError, build_demo_site


def test_build_demo_site_groups_mode_events_under_base_champions(tmp_path: Path) -> None:
    normalized_root, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"

    summary = build_demo_site(
        normalized_root=normalized_root,
        reference_root=reference_root,
        output_root=output_root,
    )

    assert summary.champion_count == 7
    assert summary.event_count == 5
    assert summary.unassigned_event_count == 3

    index = read_json(output_root / "data" / "index.json")
    assert index["dataset"]["dataset_id"] == "local-v1-test"
    assert index["dataset"]["data_dragon_version"] == "16.16.1"
    assert index["counts"] == {
        "champion_events": 5,
        "champions": 7,
        "source_records": 8,
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
    annie_index = next(item for item in index["champions"] if item["alias"] == "Annie")
    assert "火女" in annie_index["search_terms"]
    assert annie_index["portrait_url"].endswith("/16.16.1/img/champion/Annie.png")

    annie = read_json(output_root / "data" / "champions" / "Annie.json")
    assert [skin["number"] for skin in annie["skins"]] == [0, 1]
    assert annie["skins"][1]["splash_url"].endswith("/Annie_1.jpg")
    assert annie["events"][0]["raw_event_name"] == "Play_vo_Annie_Spell3DQCast"
    assert annie["events"][0]["mode"] == {"key": "jade", "label": "经典模式", "prefix": "Jade"}
    assert annie["events"][0]["skin_number"] == 1

    veigar = read_json(output_root / "data" / "champions" / "Veigar.json")
    assert veigar["events"][0]["raw_event_name"] == "Play_sfx_Ruby_Boss_FNVeigar_MordeR_buffdeactivate"
    assert veigar["events"][0]["mode"]["key"] == "ruby"
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


def test_build_demo_site_is_deterministic_and_refuses_unowned_output(tmp_path: Path) -> None:
    normalized_root, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"

    build_demo_site(
        normalized_root=normalized_root,
        reference_root=reference_root,
        output_root=output_root,
    )
    first_digest = directory_digest(output_root)
    build_demo_site(
        normalized_root=normalized_root,
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
            reference_root=reference_root,
            output_root=occupied,
        )

    assert (occupied / "user-file.txt").read_text(encoding="utf-8") == "keep"


def test_demo_assets_keep_accessibility_security_and_responsive_contract(tmp_path: Path) -> None:
    normalized_root, reference_root = write_demo_inputs(tmp_path)
    output_root = tmp_path / "site"
    build_demo_site(
        normalized_root=normalized_root,
        reference_root=reference_root,
        output_root=output_root,
    )

    html = (output_root / "index.html").read_text(encoding="utf-8")
    javascript = (output_root / "app.js").read_text(encoding="utf-8")
    stylesheet = (output_root / "styles.css").read_text(encoding="utf-8")

    assert 'lang="zh-CN"' in html
    assert 'name="theme-color" content="#f7f5f0"' in html
    assert 'rel="preconnect" href="https://ddragon.leagueoflegends.com"' in html
    assert 'aria-live="polite"' in html
    assert "isn't endorsed by Riot Games" in html
    assert "innerHTML" not in javascript
    assert "textContent" in javascript
    assert 'setAttribute("translate", "no")' in javascript
    assert 'eventFilters.addEventListener("submit"' in javascript
    assert "@media (max-width: 600px)" in stylesheet
    assert ":focus-visible" in stylesheet
    assert "min-height: 44px" in stylesheet
    assert "linear-gradient" not in stylesheet


def write_demo_inputs(tmp_path: Path) -> tuple[Path, Path]:
    normalized_root = tmp_path / "normalized"
    normalized_root.mkdir()
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
                "slots": {"mode_context": {"key": "jade", "label": "经典模式"}},
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

    reference_root = tmp_path / "reference"
    (reference_root / "champions").mkdir(parents=True)
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
    return normalized_root, reference_root


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
