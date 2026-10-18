"""reference 与采集轮次的快照绑定回归。"""

import hashlib
import json

import pytest

from lol_event_semantics.reference_snapshot import bind_reference_snapshot, validate_local_reference_snapshot
from lol_event_semantics.dataset_identity import source_manifest_identity
from lol_event_semantics.game_data_source import download_game_data_snapshot_subset


def fixture_source(*, version="16.20", lcu="lcu", data=b"[]"):
    """提供独立的采集身份和快照字节。"""

    path = "global/default/v1/champion-summary.json"
    metadata = json.dumps(
        {
            "schemaVersion": 1,
            "gameVersion": version,
            "lcuManifestId": lcu,
            "files": [{"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}],
        }
    ).encode()
    source = {
        "game_version": "16.20.1",
        "lcu_version": "16.20.2",
        "lobby_snapshot": {"lcu_manifest_id": "lcu"},
        "catalog": {"revision": "a" * 40, "metadata_sha256": hashlib.sha256(metadata).hexdigest()},
    }
    return source, metadata, path


def test_snapshot_fetch_is_pinned_and_verified():
    """正确响应原样保留，地址必须绑定固定提交。"""

    source, metadata, path = fixture_source()
    calls = []

    def fetch(url):
        calls.append(url)
        return metadata if url.endswith("/metadata.json") else b"[]"

    base, fetch = bind_reference_snapshot(source, fetcher=fetch)
    assert base.endswith("/" + "a" * 40)
    assert fetch(f"{base}/{path}") == b"[]"
    assert len(calls) == 2
    for invalid in (f"{base}/missing.json", f"{base}/../{path}", f"https://other.test/{path}"):
        with pytest.raises(ValueError, match="清单"):
            fetch(invalid)
    assert len(calls) == 2


@pytest.mark.parametrize("version,lcu", [("16.21", "lcu"), ("16.20", "other")])
def test_snapshot_rejects_version_and_lcu_drift(version, lcu):
    """同补丁之外或同补丁不同 LCU 都不能冒充当轮来源。"""

    source, metadata, _ = fixture_source(version=version, lcu=lcu)
    with pytest.raises(ValueError, match="不一致"):
        bind_reference_snapshot(source, fetcher=lambda _: metadata)


def test_snapshot_rejects_metadata_drift():
    """不可变提交的 metadata 摘要也必须与采集证明一致。"""

    source, metadata, _ = fixture_source()
    with pytest.raises(ValueError, match="SHA-256"):
        bind_reference_snapshot(source, fetcher=lambda _: metadata + b" ")


@pytest.mark.parametrize("data", [b"{}", b"[] "])
def test_snapshot_rejects_payload_digest_and_size_drift(data):
    """文件的大小相同仍须核验 SHA-256。"""

    source, metadata, path = fixture_source()
    base, fetch = bind_reference_snapshot(
        source, fetcher=lambda url: metadata if url.endswith("/metadata.json") else data
    )
    with pytest.raises(ValueError, match="SHA-256"):
        fetch(f"{base}/{path}")


@pytest.mark.parametrize("catalog", [None, {}, {"revision": "main"}, {"revision": "a" * 40}])
def test_snapshot_requires_complete_catalog_before_fetch(catalog):
    """缺少身份时不得开始联网。"""

    def unexpected(_):
        raise AssertionError("must not fetch")

    with pytest.raises(ValueError):
        bind_reference_snapshot({"catalog": catalog}, fetcher=unexpected)


def test_catalog_and_collection_are_part_of_dataset_source_identity():
    """同补丁的新快照或采集范围不得共用旧数据集来源身份。"""

    source, _, _ = fixture_source()
    before = source_manifest_identity(source)
    source["catalog"]["revision"] = "b" * 40
    assert source_manifest_identity(source) != before
    before = source_manifest_identity(source)
    source["collection_scope"] = {"champions": "all", "maps": "all"}
    assert source_manifest_identity(source) != before


def test_legacy_source_identity_does_not_gain_empty_catalog_fields():
    """历史 V1 来源缺少新字段时保留原有身份合同。"""

    identity = source_manifest_identity({"game_version": "16.16"})["identity"]
    assert "catalog" not in identity
    assert "collection" not in identity
    assert "collection_scope" not in identity


@pytest.mark.parametrize("tampered", [False, True])
def test_local_reference_snapshot_checks_raw_bytes_and_chinese_layout(tmp_path, tampered):
    """本地 zh_cn 对应远端 zh_CN，缓存 JSON 不能静默改写。"""

    source, metadata, path = fixture_source()
    document = json.loads(metadata)
    document["files"][0]["path"] = path.replace("/default/", "/zh_CN/")
    entry = document["files"][0]
    document["files"].extend(
        {**entry, "path": entry["path"].replace("champion-summary.json", name)}
        for name in ("maps.json", "items.json", "skinlines.json")
    )
    metadata = json.dumps(document).encode()
    source["catalog"]["metadata_sha256"] = hashlib.sha256(metadata).hexdigest()
    (tmp_path / "metadata.json").write_bytes(metadata)
    local = tmp_path / path.replace("/default/", "/zh_cn/")
    local.parent.mkdir(parents=True)
    local.write_bytes(b"{}" if tampered else b"[]")
    for name in ("maps.json", "items.json", "skinlines.json"):
        local.with_name(name).write_bytes(b"[]")
    if tampered:
        with pytest.raises(ValueError, match="SHA-256"):
            validate_local_reference_snapshot(source, tmp_path, languages=("zh_CN",))
    else:
        validate_local_reference_snapshot(source, tmp_path, languages=("zh_CN",))


def test_local_snapshot_requires_declared_locale_files(tmp_path):
    """删除请求语言的缓存不能退回缺失语言容忍逻辑。"""

    source, metadata, _ = fixture_source()
    (tmp_path / "metadata.json").write_bytes(metadata)
    with pytest.raises(FileNotFoundError):
        validate_local_reference_snapshot(source, tmp_path, languages=("en_US",))


def test_local_snapshot_requires_each_summary_champion(tmp_path):
    """英雄详情缺失必须失败，不能被 ingestion 的兼容跳过行为掩盖。"""

    source, metadata, _ = fixture_source()
    document = json.loads(metadata)
    payloads = {
        "champion-summary.json": b'[{"id":1}]',
        "maps.json": b"[]",
        "items.json": b"[]",
        "skinlines.json": b"[]",
        "champions/1.json": b'{"id":1}',
    }
    document["files"] = [
        {"path": f"global/default/v1/{name}", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        for name, data in payloads.items()
    ]
    metadata = json.dumps(document).encode()
    source["catalog"]["metadata_sha256"] = hashlib.sha256(metadata).hexdigest()
    (tmp_path / "metadata.json").write_bytes(metadata)
    for name, data in payloads.items():
        if name.startswith("champions/"):
            continue
        target = tmp_path / "global/default/v1" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with pytest.raises(FileNotFoundError):
        validate_local_reference_snapshot(source, tmp_path, languages=("en_US",))


def test_strict_download_preserves_original_json_bytes(tmp_path):
    """严格缓存不格式化来源 JSON，以便 local 入口重新核验原始摘要。"""

    summary = b'[ { "id": 1 } ]\n'
    detail = b'{ "id" : 1 }\n'

    def fetch(url):
        if url.endswith("champion-summary.json"):
            return summary
        if "/champions/" in url:
            return detail
        return b"[ ]\n"

    download_game_data_snapshot_subset(
        output_root=tmp_path,
        game_version="16.20",
        languages=("zh_CN",),
        fetcher=fetch,
        preserve_source_bytes=True,
    )
    base = tmp_path / "global/zh_cn/v1"
    assert (base / "champion-summary.json").read_bytes() == summary
    assert (base / "champions/1.json").read_bytes() == detail
