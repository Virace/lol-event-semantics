"""采集目录发现的覆盖范围与缺失保护，不模拟二进制格式兼容性。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from league_tools import WAD

from lol_event_semantics.collection_discovery import CollectionDiscoveryError, discover_targets


def write_catalog(root: Path, *, alias: str = "MasterYi", identifier: int = 11) -> None:
    """保存从真实目录缩减出的易大师皮肤和地图身份资料。"""
    (root / "champion-summary.json").write_text(json.dumps([{"id": identifier, "alias": alias}]), encoding="utf-8")
    skins = {
        str(identifier * 1000): {"id": identifier * 1000, "isBase": True},
        str(identifier * 1000 + 106): {
            "id": identifier * 1000 + 106,
            "chromas": [{"id": identifier * 1000 + 107}, {"id": identifier * 1000 + 108}],
        },
        str(identifier * 1000 + 116): {"id": identifier * 1000 + 116},
    }
    (root / "skins.json").write_text(json.dumps(skins), encoding="utf-8")
    (root / "maps.json").write_text(
        json.dumps(
            [
                {"id": 0, "mapStringId": ""},
                {"id": 11, "mapStringId": "SR"},
                {"id": 22, "mapStringId": "TFT"},
            ]
        ),
        encoding="utf-8",
    )


class HeaderExtractor:
    """隔离联网读取，只提供真实第三方接口的目录元数据形状。"""

    def __init__(self, contents: dict[str, tuple[str, ...]]) -> None:
        """按输入路径建立哈希目录；不伪造 WAD 二进制。"""
        self.manifest = SimpleNamespace(files={path: SimpleNamespace(name=path) for path in contents})
        self.contents = contents

    def get_wad_header(self, file: SimpleNamespace) -> SimpleNamespace:
        """通过第三方的公开路径哈希合同产生目录项。"""
        return SimpleNamespace(
            files=[SimpleNamespace(path_hash=WAD.get_hash(path)) for path in self.contents[file.name]]
        )


def test_catalog_skins_above_99_and_available_chromas_are_collected(tmp_path: Path) -> None:
    """真实 MasterYi 106/107/116 必须入选；未在目录命中的炫彩不产生空请求。"""
    write_catalog(tmp_path)
    paths = tuple(f"data/characters/masteryi/skins/skin{index}.bin" for index in (0, 106, 107, 116))
    extractor = HeaderExtractor({"DATA/FINAL/Champions/MasterYi.wad.client": paths})
    targets = discover_targets(extractor, tmp_path, maps=())
    assert len(targets) == 1
    assert targets[0].entity == "11"
    assert targets[0].bin_paths == paths


def test_new_champion_uses_current_catalog_without_reference(tmp_path: Path) -> None:
    """同轮目录中的新英雄无需累计 reference 分片即可按数字 ID 采集。"""
    write_catalog(tmp_path, alias="Yunara", identifier=804)
    (tmp_path / "skins.json").write_text(json.dumps({"804000": {"id": 804000, "isBase": True}}), encoding="utf-8")
    paths = ("data/characters/yunara/skins/skin0.bin",)
    extractor = HeaderExtractor({"DATA/FINAL/Champions/Yunara.wad.client": paths})
    targets = discover_targets(extractor, tmp_path, champions=("804",), maps=())
    assert [(target.entity, target.alias) for target in targets] == [("804", "Yunara")]


def test_mode_details_supply_skins_missing_from_global_catalog(tmp_path: Path) -> None:
    """真实快照的 Jade 模式皮肤来自英雄详情，不能依赖 global skins 的基础英雄子集。"""
    write_catalog(tmp_path, alias="Jade_Annie", identifier=60001)
    (tmp_path / "skins.json").write_text("{}", encoding="utf-8")
    details = tmp_path / "champions"
    details.mkdir()
    (details / "60001.json").write_text(
        json.dumps({"id": 60001, "alias": "Jade_Annie", "skins": [{"id": 60001000}, {"id": 60001305}]}),
        encoding="utf-8",
    )
    paths = ("data/characters/jade_annie/skins/skin0.bin", "data/characters/jade_annie/skins/skin305.bin")
    extractor = HeaderExtractor({"DATA/FINAL/Champions/Jade_Annie.wad.client": paths})
    assert discover_targets(extractor, tmp_path, maps=())[0].bin_paths == paths


def test_map_catalog_includes_common_and_excludes_tft(tmp_path: Path) -> None:
    """地图轮次保留公共事件并排除目录标明的 TFT 和 TFTChampion。"""
    write_catalog(tmp_path)
    extractor = HeaderExtractor(
        {
            "DATA/FINAL/Maps/Shipping/Common.wad.client": ("data/maps/shipping/common/common.bin",),
            "DATA/FINAL/Maps/Shipping/Map11.wad.client": ("data/maps/shipping/map11/map11.bin",),
            "DATA/FINAL/Maps/Shipping/Map22.wad.client": ("data/maps/shipping/map22/map22.bin",),
            "DATA/FINAL/Champions/TFTChampion.wad.client": (),
        }
    )
    targets = discover_targets(extractor, tmp_path, champions=())
    assert [(target.group, target.entity) for target in targets] == [("maps", "0"), ("maps", "11")]
    with pytest.raises(CollectionDiscoveryError, match="22"):
        discover_targets(extractor, tmp_path, champions=(), maps=("22",))


def test_unmapped_current_wad_and_unknown_selection_fail(tmp_path: Path) -> None:
    """新 WAD 未有目录身份或显式请求不存在时，禁止成功返回残缺轮次。"""
    write_catalog(tmp_path)
    extractor = HeaderExtractor({"DATA/FINAL/Champions/Yunara.wad.client": ()})
    with pytest.raises(CollectionDiscoveryError, match="未在同轮目录"):
        discover_targets(extractor, tmp_path, maps=())
    with pytest.raises(CollectionDiscoveryError, match="未发现请求"):
        discover_targets(extractor, tmp_path, champions=("MasterYi",), maps=())


def test_missing_primary_skin_or_map_bin_fails(tmp_path: Path) -> None:
    """目录声明的基础/正式皮肤和地图根 BIN 属于必要输入，缺失不可被当作可选炫彩。"""
    write_catalog(tmp_path)
    extractor = HeaderExtractor(
        {
            "DATA/FINAL/Champions/MasterYi.wad.client": ("data/characters/masteryi/skins/skin0.bin",),
            "DATA/FINAL/Maps/Shipping/Map11.wad.client": (),
        }
    )
    with pytest.raises(CollectionDiscoveryError, match="必要皮肤 BIN 缺失"):
        discover_targets(extractor, tmp_path, maps=())
    with pytest.raises(CollectionDiscoveryError, match="必要 BIN 缺失"):
        discover_targets(extractor, tmp_path, champions=())
