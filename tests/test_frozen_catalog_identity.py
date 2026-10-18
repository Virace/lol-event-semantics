"""新冻结档案的采集证据与历史空值兼容边界。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lol_event_semantics.dataset_identity import validate_frozen_inputs


def fixture_inputs(tmp_path: Path):
    """准备累计 raw 与完整同轮来源，故意保留历史例外以证明新档案拒绝回退。"""

    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "events.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")
    reference = tmp_path / "reference.yaml"
    reference.write_text(
        "source_versions:\n"
        "  game_data: {main_version: '16.20'}\n"
        "  data_dragon: {version: '16.20.1'}\n"
        "  tencent_101: {version: '16.20'}\n",
        encoding="utf-8",
    )
    source = {
        "game_version": "16.20.1",
        "lcu_version": "16.20.2",
        "game_manifest": {"id": "game-id", "version": "16.20.1"},
        "lobby_snapshot": {"game_version": "16.20", "lcu_manifest_id": "lcu-id"},
        "catalog": {"revision": "a" * 40, "metadata_sha256": "b" * 64},
        "sources": {"raw-bins": "ok"},
        "collection": {"run_id": "c" * 24, "raw_files": {"champions/1.txt": "d" * 64}},
    }
    profile = {
        "profile_id": "live-16.20",
        "base_game_version": "16.20",
        "corpus_scope": "cumulative",
        "expected_raw_unique_count": 1,
        "require_catalog": True,
        "lcu_version_exception": {"allow_null": True},
    }

    def validate():
        """通过公开预检入口验证当前 fixture。"""

        sources_path = tmp_path / "sources.json"
        sources_path.write_text(json.dumps(source), encoding="utf-8")
        return validate_frozen_inputs(
            profile,
            raw_root=raw,
            sources_manifest_path=sources_path,
            reference_manifest_path=reference,
        )

    return source, profile, validate


def test_required_catalog_accepts_complete_source(tmp_path: Path) -> None:
    """有效的新档案返回完整来源且无需历史例外。"""

    _, _, validate = fixture_inputs(tmp_path)
    result = validate()
    assert result["base_game_version"] == "16.20"
    assert result["accepted_exceptions"] == []


@pytest.mark.parametrize(
    "path,value",
    [
        (("catalog",), None),
        (("catalog", "revision"), "main"),
        (("catalog", "metadata_sha256"), "b" * 63),
        (("lcu_version",), None),
        (("lcu_version",), "16.16.1"),
        (("lobby_snapshot", "lcu_manifest_id"), " "),
        (("game_manifest", "id"), ""),
        (("game_manifest", "version"), "16.16.1"),
        (("sources", "raw-bins"), "pending"),
        (("collection",), None),
        (("collection", "run_id"), "not-a-run"),
        (("collection", "raw_files"), {}),
        (("collection", "raw_files"), {"../1.txt": "d" * 64}),
        (("collection", "raw_files"), {"champions/1.txt": "bad-digest"}),
    ],
)
def test_required_catalog_rejects_incomplete_source(tmp_path: Path, path: tuple[str, ...], value: object) -> None:
    """新版本不得通过缺身份、未完成来源、路径逃逸或历史空值例外预检。"""

    source, _, validate = fixture_inputs(tmp_path)
    target = source
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError):
        validate()


def test_legacy_profile_keeps_explicit_null_lcu_exception(tmp_path: Path) -> None:
    """未要求 catalog 的旧档案继续按原合同接受显式 LCU 例外。"""

    source, profile, validate = fixture_inputs(tmp_path)
    profile.pop("require_catalog")
    source.pop("catalog")
    source.pop("collection")
    source["lcu_version"] = None
    result = validate()
    assert len(result["accepted_exceptions"]) == 1
