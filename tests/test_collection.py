"""采集轮次的原名保留、来源隔离和可恢复状态合同。"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from lol_event_semantics import collection
from lol_event_semantics.collection_discovery import CollectionTarget
from lol_event_semantics.source_manifest import build_manifest_source


class Extractor:
    """只替代网络提取边界；载荷标识交给事件提取边界返回明确事件集。"""

    def __init__(self, target: CollectionTarget) -> None:
        """提供一个实体的稳定内容身份和两份独立载荷。"""
        self.manifest = SimpleNamespace(files={target.wad_path: self})
        self.digest = "wad-content-1"
        self.payloads = {target.wad_path: {target.bin_paths[0]: b"base", target.bin_paths[1]: b"skin"}}
        self.requests: list[dict[str, list[str]]] = []

    def hexdigest(self) -> str:
        """返回当前来源内容身份。"""
        return self.digest

    def extract_files(self, requests: dict[str, list[str]]) -> dict[str, dict[str, bytes]]:
        """保留请求记录，并让缺失载荷按真实边界表现为未提取成功。"""
        self.requests.append(requests)
        return {
            wad: {path: self.payloads.get(wad, {})[path] for path in paths if path in self.payloads.get(wad, {})}
            for wad, paths in requests.items()
        }


@pytest.fixture
def target() -> CollectionTarget:
    """使用与现有 raw 布局一致的数字英雄身份和两条皮肤 BIN 路径。"""
    return CollectionTarget(
        "champions",
        "1",
        "Annie",
        "DATA/FINAL/Champions/Annie.wad.client",
        ("data/characters/annie/skins/skin0.bin", "data/characters/annie/skins/skin1.bin"),
    )


@pytest.fixture
def source() -> dict[str, object]:
    """构建采集边界接受的同轮来源身份，不承担版本配对验证。"""
    return build_manifest_source(
        game_version="16.20.8248524",
        game_manifest_id="CB4BCB7E1F5C1831",
        lcu_version="16.20.827.198",
        lcu_manifest_id="888A240B868B4030",
        region="EUW",
    )


@pytest.fixture
def extractor(target: CollectionTarget, monkeypatch: pytest.MonkeyPatch) -> Extractor:
    """隔离二进制解析，明确去重、大小写保留与 TFT 排除的输入。"""
    events = {
        b"base": ["Play_vo_Annie_Move2DStandard", "Play_vo_Annie_Move2DStandard", "Play_TFT_Annie"],
        b"skin": ["Play_vo_Annie_Attack2DGeneral", "play_vo_Annie_Move2DStandard"],
        b"map": ["Play_sfx_Env_Map11_Ambience"],
        b"updated": ["Play_vo_Annie_NewVersion"],
    }

    def extract_events(payload: bytes, *, strict: bool = False) -> list[str]:
        """替代严格解析边界，保持当前测试只验证采集状态和逐行文本合同。"""
        return events[payload]

    monkeypatch.setattr(collection, "extract_event_names", extract_events)
    return Extractor(target)


def read_json(path: Path) -> dict[str, Any]:
    """读取落盘状态，验证消费者能观察的结果。"""
    return json.loads(path.read_text(encoding="utf-8"))


def test_preserves_names_and_excludes_tft(
    tmp_path: Path, target: CollectionTarget, source: dict[str, object], extractor: Extractor
) -> None:
    """同名去重而不同大小写保留，TFT 事件不进入完成轮次。"""
    run = collection.collect_targets(extractor, [target], source, tmp_path)

    assert (run / "raw/champions/1.txt").read_text(encoding="utf-8").splitlines() == [
        "Play_vo_Annie_Attack2DGeneral",
        "Play_vo_Annie_Move2DStandard",
        "play_vo_Annie_Move2DStandard",
    ]
    report = read_json(run / "collection-report.json")
    assert report["status"] == "completed"
    assert report["event_count"] == 3
    assert report["excluded_tft_events"] == 1
    assert read_json(run / "source-manifest.json")["sources"]["raw-bins"] == "ok"


def test_failed_entity_preserves_completed_work_for_resume(
    tmp_path: Path, target: CollectionTarget, source: dict[str, object], extractor: Extractor
) -> None:
    """地图缺失不能声称整轮成功；补齐后只重采失败实体。"""
    map_target = CollectionTarget(
        "maps", "11", "Map11", "DATA/FINAL/Maps/Shipping/Map11.wad.client", ("data/maps/shipping/map11/map11.bin",)
    )
    extractor.manifest.files[map_target.wad_path] = extractor
    with pytest.raises(ValueError, match="未成功提取"):
        collection.collect_targets(extractor, [target, map_target], source, tmp_path)

    run = next((tmp_path / "rounds").iterdir())
    report = read_json(run / "collection-report.json")
    assert report["status"] == "failed"
    assert report["failed_target"] == "maps/11"
    assert set(report["completed"]) == {"champions/1"}
    assert read_json(run / "source-manifest.json")["sources"]["raw-bins"] != "ok"
    champion_payload = (run / "raw/champions/1.txt").read_bytes()

    extractor.payloads[map_target.wad_path] = {map_target.bin_paths[0]: b"map"}
    extractor.requests.clear()
    resumed = collection.collect_targets(extractor, [target, map_target], source, tmp_path)

    assert resumed == run
    assert extractor.requests == [{map_target.wad_path: list(map_target.bin_paths)}]
    assert (run / "raw/champions/1.txt").read_bytes() == champion_payload
    assert (run / "raw/maps/11.txt").read_text(encoding="utf-8") == "Play_sfx_Env_Map11_Ambience\n"
    assert read_json(run / "collection-report.json")["event_count"] == 4
    assert read_json(run / "source-manifest.json")["sources"]["raw-bins"] == "ok"


@pytest.mark.parametrize("identity", ["game_manifest", "lcu_manifest", "request"])
def test_source_and_request_changes_create_separate_rounds(
    tmp_path: Path,
    target: CollectionTarget,
    source: dict[str, object],
    extractor: Extractor,
    identity: str,
) -> None:
    """来源 manifest 或采集 BIN 清单改变时，不覆盖已有轮次。"""
    first = collection.collect_targets(extractor, [target], source, tmp_path)
    first_source = (first / "source-manifest.json").read_bytes()
    first_payload = (first / "raw/champions/1.txt").read_bytes()
    updated = json.loads(json.dumps(source))
    selected = target
    if identity == "game_manifest":
        updated["game_manifest"]["id"] = "different-game-manifest"
    elif identity == "lcu_manifest":
        updated["lobby_snapshot"]["lcu_manifest_id"] = "different-lcu-manifest"
    else:
        selected = replace(target, bin_paths=(target.bin_paths[0],))
    second = collection.collect_targets(extractor, [selected], updated, tmp_path)

    assert first != second
    assert (first / "source-manifest.json").read_bytes() == first_source
    assert (first / "raw/champions/1.txt").read_bytes() == first_payload
    expected = ["Play_vo_Annie_Move2DStandard"] if identity == "request" else first_payload.decode().splitlines()
    assert (second / "raw/champions/1.txt").read_text(encoding="utf-8").splitlines() == expected


@pytest.mark.parametrize("damage", ["output", "cache", "counts"])
def test_damaged_outputs_or_cache_are_recovered(
    tmp_path: Path,
    target: CollectionTarget,
    source: dict[str, object],
    extractor: Extractor,
    damage: str,
) -> None:
    """损坏的语料或计数不得进入已完成来源，必须从有效缓存或来源恢复。"""
    run = collection.collect_targets(extractor, [target], source, tmp_path)
    raw_path = run / "raw/champions/1.txt"
    expected = raw_path.read_bytes()
    report_path = run / "collection-report.json"
    report = read_json(report_path)
    entry = report["completed"]["champions/1"]
    cache_root = tmp_path / "cache/entities"
    if damage == "counts":
        entry["events"] = 900
        report_path.write_text(json.dumps(report), encoding="utf-8")
        (cache_root / f"{entry['cache_key']}.json").write_text(json.dumps(entry), encoding="utf-8")
    else:
        raw_path.write_bytes(b"damaged raw")
        if damage == "cache":
            (cache_root / f"{entry['cache_key']}.txt").write_bytes(b"damaged cache")
    extractor.requests.clear()

    assert collection.collect_targets(extractor, [target], source, tmp_path) == run
    assert raw_path.read_bytes() == expected
    assert read_json(report_path)["event_count"] == 3
    if damage == "output":
        assert extractor.requests == []
    else:
        assert extractor.requests == [{target.wad_path: list(target.bin_paths)}]


def test_changed_wad_content_is_not_reused(
    tmp_path: Path, target: CollectionTarget, source: dict[str, object], extractor: Extractor
) -> None:
    """同一 WAD 路径的来源内容改变后，新的轮次必须提取新事件。"""
    first = collection.collect_targets(extractor, [target], source, tmp_path)
    first_payload = (first / "raw/champions/1.txt").read_bytes()
    extractor.digest = "wad-content-2"
    extractor.payloads[target.wad_path] = dict.fromkeys(target.bin_paths, b"updated")
    extractor.requests.clear()

    second = collection.collect_targets(extractor, [target], source, tmp_path)

    assert second != first
    assert extractor.requests == [{target.wad_path: list(target.bin_paths)}]
    assert (second / "raw/champions/1.txt").read_text(encoding="utf-8") == "Play_vo_Annie_NewVersion\n"
    assert (first / "raw/champions/1.txt").read_bytes() == first_payload


def test_completed_run_stays_complete_during_reuse(
    tmp_path: Path,
    target: CollectionTarget,
    source: dict[str, object],
    extractor: Extractor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重跑完成轮次无需写文件，日志回调中断也不能降级成功结果。"""
    run = collection.collect_targets(extractor, [target], source, tmp_path)
    before = {path: path.read_bytes() for path in run.rglob("*") if path.is_file()}
    extractor.requests.clear()

    def reject_write(path: Path, payload: bytes) -> None:
        """完成结果的正常复用不允许写入任何文件。"""
        pytest.fail(f"完成轮次不应被覆写：{path}")

    def interrupt_progress(message: str) -> None:
        """模拟已验证完成结果之后的用户中断。"""
        raise KeyboardInterrupt

    monkeypatch.setattr(collection, "write_bytes", reject_write)
    assert collection.collect_targets(extractor, [target], source, tmp_path) == run
    with pytest.raises(KeyboardInterrupt):
        collection.collect_targets(extractor, [target], source, tmp_path, progress=interrupt_progress)
    assert extractor.requests == []
    assert {path: path.read_bytes() for path in before} == before
