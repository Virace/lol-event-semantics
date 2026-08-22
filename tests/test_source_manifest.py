"""可移植来源清单的冻结档案契约测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lol_event_semantics.source_manifest import (
    build_manifest_source,
    load_source_manifest,
    normalize_portable_source_manifest,
    validate_portable_source_manifest,
    write_source_manifest,
)
from scripts.download_sources import build_source_cursor
from scripts.prepare_source_manifest import main, parse_args


def frozen_profile() -> dict[str, object]:
    return {
        "schema_version": 1,
        "profile_id": "fixture-v1",
        "base_game_version": "16.14",
        "corpus_scope": "cumulative",
        "expected_raw_unique_count": 1,
        "lcu_version_exception": {"allow_null": True, "reason": "fixture"},
    }


def test_manifest_mode_builds_portable_frozen_source_identity(tmp_path: Path) -> None:
    manifest = build_manifest_source(
        game_version="16.14.7945912",
        game_manifest_id="GAME-ABC",
        lcu_version="16.14.7940000",
        lcu_manifest_id="ABC123",
    )

    evidence = validate_portable_source_manifest(manifest, frozen_profile())
    target = tmp_path / "nested" / "source-manifest.json"
    write_source_manifest(target, manifest)

    assert evidence == {
        "profile_id": "fixture-v1",
        "base_game_version": "16.14",
        "mode": "manifest",
        "game_version": "16.14.7945912",
        "game_manifest_id": "GAME-ABC",
        "lcu_manifest_id": "ABC123",
    }
    assert load_source_manifest(target) == manifest
    assert "game_dir" not in target.read_text(encoding="utf-8")


def test_remote_downloader_cursor_projects_to_a_portable_manifest() -> None:
    """仓库原生远端 producer 必须写出可由 artifact consumer 验证的版本化游标。"""

    producer = build_source_cursor(
        mode="remote",
        game_dir=None,
        region="NA",
        game_version="16.14.7945912",
        lcu_version="16.14.7940000",
        game_manifest={"id": "GAME-ABC", "version": "16.14.7945912", "url": "https://example.test/game"},
        lobby_snapshot={"game_version": "16.14", "lcu_manifest_id": "LCU-ABC"},
        sources={"raw-bins": "ok"},
        local_wads=None,
        downloaded_at="2026-08-23T00:00:00+00:00",
    )

    portable = normalize_portable_source_manifest(producer)

    assert producer["schema_version"] == 1
    assert validate_portable_source_manifest(portable, frozen_profile())["game_manifest_id"] == "GAME-ABC"
    assert "downloaded_at" not in portable
    assert "game_dir" not in portable


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda data: data.update(mode="local"), "不能复用 local"),
        (lambda data: data.update(game_dir="D:/League"), "机器或运行期字段"),
        (lambda data: data.update(game_version="16.15"), "版本系列不匹配"),
        (lambda data: data["game_manifest"].update(id=""), "game_manifest.id.*非空字符串"),
        (lambda data: data["game_manifest"].update(version="16.15"), "版本系列不匹配"),
        (lambda data: data.update(lcu_version=None), "lcu_version 必须是非空字符串"),
        (lambda data: data["sources"].update({"raw-bins": "pending"}), "未完成来源"),
        (lambda data: data["lobby_snapshot"].update(lcu_manifest_id=""), "非空字符串"),
    ],
)
def test_portable_source_manifest_rejects_false_or_incomplete_provenance(change: object, match: str) -> None:
    manifest = build_manifest_source(
        game_version="16.14",
        game_manifest_id="GAME-ABC",
        lcu_version="16.14.1",
        lcu_manifest_id="ABC",
    )
    change(manifest)  # type: ignore[operator]

    with pytest.raises(ValueError, match=match):
        validate_portable_source_manifest(manifest, frozen_profile())


def test_prepare_source_manifest_normalizes_a_producer_manifest(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(frozen_profile()), encoding="utf-8")
    source_path = tmp_path / "producer.json"
    manifest = build_manifest_source(
        game_version="16.14",
        game_manifest_id="GAME-ABC",
        lcu_version="16.14.7940000",
        lcu_manifest_id="ABC",
        region="NA",
    )
    manifest["downloaded_at"] = "2026-08-13T00:00:00Z"
    manifest["game_dir"] = None
    source_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = tmp_path / "runtime.json"

    assert (
        main(
            [
                "--input",
                str(source_path),
                "--frozen-profile",
                str(profile_path),
                "--output",
                str(output_path),
            ]
        )
        == 0
    )
    normalized = load_source_manifest(output_path)
    assert normalized == {key: value for key, value in manifest.items() if key not in {"downloaded_at", "game_dir"}}


def test_prepare_source_manifest_rejects_unknown_producer_schema_before_projection(tmp_path: Path) -> None:
    profile_path = tmp_path / "profile.json"
    profile_path.write_text(json.dumps(frozen_profile()), encoding="utf-8")
    source_path = tmp_path / "producer.json"
    manifest = build_manifest_source(
        game_version="16.14",
        game_manifest_id="GAME-ABC",
        lcu_version="16.14.7940000",
        lcu_manifest_id="ABC",
    )
    manifest["schema_version"] = 2
    source_path.write_text(json.dumps(manifest), encoding="utf-8")
    output_path = tmp_path / "runtime.json"

    with pytest.raises(ValueError, match="source manifest schema_version"):
        main(
            [
                "--input",
                str(source_path),
                "--frozen-profile",
                str(profile_path),
                "--output",
                str(output_path),
            ]
        )

    assert not output_path.exists()


def test_prepare_source_manifest_cli_requires_lcu_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        parse_args(
            [
                "--game-version",
                "16.14",
                "--game-manifest-id",
                "GAME-ABC",
                "--lcu-manifest-id",
                "LCU-ABC",
                "--frozen-profile",
                "profile.json",
                "--output",
                "source-manifest.json",
            ]
        )

    assert "--lcu-version required when --input is omitted" in capsys.readouterr().err
