"""采集 raw 与更新入口来源证明的绑定测试。"""

import hashlib
import json

import pytest

from lol_event_semantics.source_manifest import validate_collection_raw
from lol_event_semantics import update_pipeline
from tests.test_update_pipeline import write_frozen_fixture


def fixture_raw(tmp_path):
    """写入可独立校验的单文件轮次。"""

    payload = b"Play_vo_Annie_Move2DStandard\n"
    target = tmp_path / "champions/1.txt"
    target.parent.mkdir()
    target.write_bytes(payload)
    source = {"collection": {"run_id": "a" * 24, "raw_files": {"champions/1.txt": hashlib.sha256(payload).hexdigest()}}}
    return source, target


def test_collection_raw_accepts_exact_payload(tmp_path):
    """完整且摘要一致的轮次可以进入后续 staging。"""

    source, _ = fixture_raw(tmp_path)
    validate_collection_raw(source, tmp_path)


@pytest.mark.parametrize("change", ["missing", "extra", "corrupt", "escape"])
def test_collection_raw_rejects_tampering(tmp_path, change):
    """缺文件、增加文件、改原名与路径逃逸都不能保持来源有效。"""

    source, target = fixture_raw(tmp_path)
    if change == "missing":
        target.unlink()
    elif change == "extra":
        target.with_name("2.txt").write_bytes(target.read_bytes())
    elif change == "corrupt":
        target.write_bytes(b"Play_vo_Annie_Attack2DGeneral\n")
    else:
        source["collection"]["raw_files"] = {"../1.txt": "a" * 64}
    with pytest.raises(ValueError):
        validate_collection_raw(source, tmp_path)


def test_online_catalog_binding_rejects_raw_before_fetch(tmp_path, monkeypatch):
    """线上入口在任何 reference 下载前验证实际 raw 来源。"""

    source_path, profile = write_frozen_fixture(tmp_path)
    source = json.loads(source_path.read_text())
    source["catalog"] = {"revision": "a" * 40, "metadata_sha256": "b" * 64}
    source_path.write_text(json.dumps(source))

    def unexpected(**kwargs):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(update_pipeline, "prepare_online_reference_sources", unexpected)
    with pytest.raises(ValueError, match="collection"):
        update_pipeline.run_online_update_pipeline(
            reference_root=tmp_path / "reference",
            raw_root=tmp_path / "raw",
            state_file=tmp_path / "state.json",
            raw_input_root=tmp_path / "round",
            game_version="16.12",
            report_root=tmp_path / "reports",
            cache_root=tmp_path / "cache",
            sources_manifest_path=source_path,
            frozen_profile_path=profile,
        )


@pytest.mark.parametrize("fault", ["raw", "pending", "missing_catalog", "cache"])
def test_local_catalog_binding_fails_before_staging(tmp_path, monkeypatch, fault):
    """local 也必须在触碰累计目标前拒绝损坏的采集证据。"""

    source_path, profile_path = write_frozen_fixture(tmp_path)
    source = json.loads(source_path.read_text())
    profile = json.loads(profile_path.read_text())
    profile["require_catalog"] = True
    profile_path.write_text(json.dumps(profile))
    round_root = tmp_path / "round"
    round_root.mkdir()
    evidence, raw_file = fixture_raw(round_root)
    source.update(evidence)
    metadata = json.dumps(
        {
            "schemaVersion": 1,
            "gameVersion": "16.12",
            "lcuManifestId": "fixture-lcu-manifest",
            "files": [
                {
                    "path": "global/default/v1/champion-summary.json",
                    "bytes": 2,
                    "sha256": hashlib.sha256(b"[]").hexdigest(),
                }
            ],
        }
    ).encode()
    source["catalog"] = {"revision": "a" * 40, "metadata_sha256": hashlib.sha256(metadata).hexdigest()}
    cache = tmp_path / "game-data"
    target = cache / "global/default/v1/champion-summary.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"[]")
    (cache / "metadata.json").write_bytes(metadata)
    if fault == "raw":
        raw_file.write_bytes(b"Play_vo_Annie_Attack2DGeneral\n")
    elif fault == "pending":
        source["sources"]["raw-bins"] = "pending"
    elif fault == "missing_catalog":
        del source["catalog"]
    else:
        target.write_bytes(b"{}")
    source_path.write_text(json.dumps(source))

    def unexpected(**kwargs):
        raise AssertionError("must not import")

    monkeypatch.setattr(update_pipeline, "import_local_reference", unexpected)
    reference = tmp_path / "reference"
    expected = {
        "raw": "raw SHA-256",
        "pending": "未完成来源",
        "missing_catalog": "缺少采集 catalog",
        "cache": "reference 文件长度或 SHA-256",
    }
    with pytest.raises(ValueError, match=expected[fault]):
        update_pipeline.run_local_update_pipeline(
            reference_root=reference,
            raw_root=tmp_path / "raw",
            state_file=tmp_path / "state.json",
            game_data_root=cache,
            data_dragon_root=tmp_path / "dragon",
            raw_input_root=round_root,
            game_version="16.12",
            data_dragon_version="16.12.1",
            report_root=tmp_path / "reports",
            sources_manifest_path=source_path,
            frozen_profile_path=profile_path,
        )
    assert not reference.exists()
    assert not (tmp_path / "state.json").exists()
