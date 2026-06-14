"""game-data JSON 准备脚本测试。"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


def load_script_module():
    """加载 scripts/extract_game_data_json.py。"""

    script_path = Path("scripts/extract_game_data_json.py")
    spec = importlib.util.spec_from_file_location("extract_game_data_json", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["extract_game_data_json"] = module
    spec.loader.exec_module(module)
    return module


class ExtractGameDataJsonTests(unittest.TestCase):
    """验证 WAD/CommunityDragon 路径生成逻辑。"""

    def test_catalog_paths_use_communitydragon_layout(self) -> None:
        module = load_script_module()

        paths = module.catalog_paths(["default"])

        self.assertIn("plugins/rcp-be-lol-game-data/global/default/v1/maps.json", paths)
        self.assertIn("plugins/rcp-be-lol-game-data/global/default/v1/champion-summary.json", paths)

    def test_champion_paths_are_derived_from_summary_ids(self) -> None:
        module = load_script_module()
        rows = [{"id": 1}, {"id": "7"}, {"id": -1}, {"alias": "Missing"}]

        paths = module.champion_detail_paths(rows, "default")

        self.assertEqual(
            paths,
            [
                "plugins/rcp-be-lol-game-data/global/default/v1/champions/1.json",
                "plugins/rcp-be-lol-game-data/global/default/v1/champions/7.json",
            ],
        )

    def test_communitydragon_url_keeps_relative_path(self) -> None:
        module = load_script_module()

        url = module.communitydragon_url("plugins/rcp-be-lol-game-data/global/default/v1/maps.json")

        self.assertEqual(
            url,
            "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/maps.json",
        )
