"""目录来源边界测试：同轮证据、摘要与可恢复缓存。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lol_event_semantics.collection_sources import RAW_URL, prepare_catalog
from lol_event_semantics.wad_source import ManifestPair

COMMIT = "a" * 40
SUMMARY = "global/default/v1/champion-summary.json"
DETAIL = "global/default/v1/champions/1.json"


def _snapshot() -> tuple[ManifestPair, dict[str, bytes]]:
    """按真实 metadata.files 格式缩减出单英雄目录，不模拟游戏二进制。"""

    pair = ManifestPair(
        region="OC1",
        game_version="16.20.1",
        lcu_version="16.20.2",
        game_url="https://example.test/game.manifest",
        lcu_url="https://example.test/888A240B868B4030.manifest",
        game_id="1234567890ABCDEF",
        lcu_id="888A240B868B4030",
    )
    files = {
        SUMMARY: b'[{"id":1,"alias":"Annie"}]',
        "global/default/v1/skins.json": b'{"1000":{"id":1000}}',
        "global/default/v1/maps.json": b'[{"id":11,"mapStringId":"SR"}]',
        DETAIL: b'{"id":1,"skins":[{"id":1000}]}',
    }
    metadata = {
        "schemaVersion": 1,
        "gameVersion": "16.20",
        "lcuManifestId": "888A240B868B4030",
        "files": [
            {"path": path, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for path, data in files.items()
        ],
    }
    return pair, {"metadata.json": json.dumps(metadata).encode(), **files}


def test_catalog_repairs_corrupt_cache_and_preserves_source_bytes(tmp_path: Path) -> None:
    """损坏缓存重取，其余缓存按摘要复用，落盘保持上游原字节。"""

    pair, files = _snapshot()
    requested: list[str] = []

    def fetch(url: str) -> bytes:
        """只提供固定提交中的目录字节。"""

        path = url.removeprefix(f"{RAW_URL}/{COMMIT}/")
        requested.append(path)
        return files[path]

    root = prepare_catalog(tmp_path, pair, fetcher=fetch, revision=COMMIT)
    assert {path: (root / path).read_bytes() for path in files} == files
    (root / SUMMARY).write_bytes(b"broken")
    (root / "keep.txt").write_text("user file", encoding="utf-8")
    requested.clear()

    assert prepare_catalog(tmp_path, pair, fetcher=fetch, revision=COMMIT) == root
    assert sorted(requested) == [SUMMARY, "metadata.json"]
    assert (root / SUMMARY).read_bytes() == files[SUMMARY]
    assert (root / "keep.txt").read_text(encoding="utf-8") == "user file"


@pytest.mark.parametrize(
    "changes",
    [
        {"game_version": "16.21.1"},
        {"lcu_version": "16.19.2"},
        {"lcu_id": "1111111111111111"},
        {"lcu_id": ""},
    ],
)
def test_catalog_rejects_mismatched_manifest(tmp_path: Path, changes: dict[str, str]) -> None:
    """GAME/LCU 主版本及精确 LCU ID 任一不符，都不得落盘来源。"""

    pair, files = _snapshot()
    with pytest.raises(ValueError):
        prepare_catalog(
            tmp_path,
            replace(pair, **changes),
            fetcher=lambda url: files["metadata.json"],
            revision=COMMIT,
        )
    assert list(tmp_path.iterdir()) == []


def test_catalog_rejects_download_digest_mismatch(tmp_path: Path) -> None:
    """上游响应与已公布摘要不符时，保留已有文件并拒绝宣告完整目录。"""

    pair, files = _snapshot()
    target = tmp_path / "catalogs" / COMMIT / SUMMARY
    target.parent.mkdir(parents=True)
    target.write_bytes(b"previous invalid cache")
    files[SUMMARY] = b'[{"id":2,"alias":"Olaf"}]'
    with pytest.raises(ValueError, match="SHA-256"):
        prepare_catalog(
            tmp_path,
            pair,
            fetcher=lambda url: files[url.removeprefix(f"{RAW_URL}/{COMMIT}/")],
            revision=COMMIT,
        )
    assert target.read_bytes() == b"previous invalid cache"
    assert not (tmp_path / "catalogs" / COMMIT / "metadata.json").exists()


def test_catalog_requires_manifest_digest_for_every_file(tmp_path: Path) -> None:
    """缺少必要目录摘要时不能把未声明的 HTTP 内容当成可信证据。"""

    pair, files = _snapshot()
    metadata = json.loads(files["metadata.json"])
    metadata["files"] = [record for record in metadata["files"] if record["path"] != SUMMARY]
    with pytest.raises(ValueError, match="metadata.files"):
        prepare_catalog(
            tmp_path,
            pair,
            fetcher=lambda url: json.dumps(metadata).encode(),
            revision=COMMIT,
        )
    assert list(tmp_path.iterdir()) == []
