"""确定性数据集身份与冻结输入预检测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lol_event_semantics.dataset_identity import (
    CONTENT_DIGEST_VERSION,
    canonical_json_bytes,
    contract_assets_digest,
    content_digest,
    semantic_document_identity,
    source_manifest_identity,
    tree_digest,
    unmanaged_payload_paths,
    validate_frozen_inputs,
)


def test_tree_digest_ignores_root_location_and_file_times(tmp_path: Path) -> None:
    left = tmp_path / "left"
    right = tmp_path / "right"
    for root in (left, right):
        (root / "nested").mkdir(parents=True)
        (root / "a.txt").write_bytes(b"alpha\r\n")
        (root / "nested" / "b.txt").write_bytes(b"beta\n")

    (right / "a.txt").touch()

    assert tree_digest(left) == tree_digest(right)
    (right / "nested" / "b.txt").write_bytes(b"changed\n")
    assert tree_digest(left).digest != tree_digest(right).digest


def test_contract_assets_digest_excludes_quality_baseline(tmp_path: Path) -> None:
    (tmp_path / "schema").mkdir()
    (tmp_path / "examples").mkdir()
    (tmp_path / "schema" / "record.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "examples" / "record.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "accepted-corpus.v1.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "quality-baseline.json").write_text('{"warnings": 1}\n', encoding="utf-8")
    before = contract_assets_digest(tmp_path)

    (tmp_path / "quality-baseline.json").write_text('{"warnings": 2}\n', encoding="utf-8")

    assert contract_assets_digest(tmp_path) == before


def test_unmanaged_payload_paths_fail_closed() -> None:
    files = [
        {"path": "normalized-events.jsonl"},
        {"path": "by-entity/champions/Annie.jsonl"},
        {"path": "schema/record.json"},
        {"path": "debug.tmp"},
    ]

    assert unmanaged_payload_paths(files) == ["debug.tmp"]


def test_content_digest_is_canonical_and_order_sensitive_for_files() -> None:
    identity = {"b": 2, "a": 1}
    files = [{"path": "a", "bytes": 1, "sha256": "0" * 64}]

    assert canonical_json_bytes(identity) == b'{"a":1,"b":2}'
    assert content_digest(identity, files) == content_digest({"a": 1, "b": 2}, files)
    assert CONTENT_DIGEST_VERSION == "lol-event-semantics.content-digest.v1"


def test_source_and_reference_identity_exclude_run_metadata() -> None:
    left = {
        "mode": "local",
        "game_dir": "D:/Games/League",
        "downloaded_at": "2026-01-01T00:00:00Z",
        "game_version": "16.14.1",
        "lcu_version": None,
        "lobby_snapshot": {
            "game_version": "16.14",
            "lcu_manifest_id": "ABC",
            "generated_at": "2026-01-01T00:00:00Z",
        },
        "sources": {"lobby": "ok"},
    }
    right = {
        **left,
        "game_dir": "E:/Another/Machine",
        "downloaded_at": "2099-01-01T00:00:00Z",
        "lobby_snapshot": {**left["lobby_snapshot"], "generated_at": "2099-01-01T00:00:00Z"},
    }

    assert source_manifest_identity(left) == source_manifest_identity(right)
    assert semantic_document_identity(
        {"schema_version": 2, "updated_at": "old"},
        excluded_keys={"updated_at"},
    ) == semantic_document_identity(
        {"schema_version": 2, "updated_at": "new"},
        excluded_keys={"updated_at"},
    )


def test_frozen_preflight_accepts_only_documented_lcu_exception(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / "events.txt").write_text("Play_vo_A\nPlay_sfx_B\nPlay_vo_A\n", encoding="utf-8")
    sources_manifest = tmp_path / "sources.json"
    sources_manifest.write_text(
        json.dumps(
            {
                "game_version": "16.14.999",
                "lcu_version": None,
                "lobby_snapshot": {
                    "game_version": "16.14",
                    "lcu_manifest_id": "ABC123",
                },
            }
        ),
        encoding="utf-8",
    )
    reference_manifest = tmp_path / "reference.yaml"
    reference_manifest.write_text(
        """source_versions:
  game_data:
    main_version: '16.14'
  data_dragon:
    version: '16.14.1'
  tencent_101:
    version: '16.14'
""",
        encoding="utf-8",
    )
    profile = {
        "base_game_version": "16.14",
        "corpus_scope": "cumulative",
        "expected_raw_unique_count": 2,
        "profile_id": "test",
        "lcu_version_exception": {"allow_null": True, "reason": "fixture"},
    }

    result = validate_frozen_inputs(
        profile,
        raw_root=raw_root,
        sources_manifest_path=sources_manifest,
        reference_manifest_path=reference_manifest,
    )

    assert result["raw_unique_count"] == 2
    assert result["accepted_exceptions"] == [
        {
            "code": "lcu_version_unavailable_with_lobby_manifest_evidence",
            "reason": "fixture",
            "evidence": "lobby_snapshot.lcu_manifest_id=ABC123",
        }
    ]

    sources = json.loads(sources_manifest.read_text(encoding="utf-8"))
    sources["lobby_snapshot"]["lcu_manifest_id"] = ""
    sources_manifest.write_text(json.dumps(sources), encoding="utf-8")
    with pytest.raises(ValueError, match="LCU version"):
        validate_frozen_inputs(
            profile,
            raw_root=raw_root,
            sources_manifest_path=sources_manifest,
            reference_manifest_path=reference_manifest,
        )


def test_frozen_preflight_rejects_series_or_raw_count_drift(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    raw_root.mkdir()
    (raw_root / "events.txt").write_text("Play_vo_A\n", encoding="utf-8")
    sources_manifest = tmp_path / "sources.json"
    sources_manifest.write_text(
        json.dumps(
            {
                "game_version": "16.15.1",
                "lcu_version": "16.14.2",
                "lobby_snapshot": {"game_version": "16.14", "lcu_manifest_id": "ABC"},
            }
        ),
        encoding="utf-8",
    )
    reference_manifest = tmp_path / "reference.yaml"
    reference_manifest.write_text(
        """source_versions:
  game_data: {main_version: '16.14'}
  data_dragon: {version: '16.14.1'}
  tencent_101: {version: '16.14'}
""",
        encoding="utf-8",
    )
    profile = {
        "base_game_version": "16.14",
        "corpus_scope": "cumulative",
        "expected_raw_unique_count": 2,
        "profile_id": "test",
    }

    with pytest.raises(ValueError, match="sources.game_version"):
        validate_frozen_inputs(
            profile,
            raw_root=raw_root,
            sources_manifest_path=sources_manifest,
            reference_manifest_path=reference_manifest,
        )
