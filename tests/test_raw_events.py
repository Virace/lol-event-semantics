"""原始事件导出测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.raw_events import collect_events, list_source_files


class RawEventsTests(unittest.TestCase):
    """验证 raw 快照导出边界。"""

    def test_collect_events_excludes_tft_event_names(self) -> None:
        """导出 raw 时不接收 TFT / TFTM 命名空间事件。"""

        data = {
            "events": [
                "Play_sfx_tft_action_shop_buy",
                "Play_vo_TFTTutorial_Yasuo_Purchase2D",
                "Play_TFT_Mus_VillainLab_win",
                "Play_sfx_tftm_shop_open",
                "Play_vo_Irelia_Unique2DSurviveTeamFight",
                "Play_vo_Annie_Move2DStandard",
            ]
        }

        self.assertEqual(
            collect_events(data),
            [
                "Play_vo_Irelia_Unique2DSurviveTeamFight",
                "Play_vo_Annie_Move2DStandard",
            ],
        )

    def test_list_source_files_excludes_tft_map22_snapshot(self) -> None:
        """TFT map 22 源文件不进入 raw 导出流程。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            maps = root / "maps"
            maps.mkdir()
            champions = root / "champions"
            champions.mkdir()
            map11 = maps / "11.json"
            map22 = maps / "22.json"
            champion = champions / "39.json"
            map11.write_text('{"events": []}', encoding="utf-8")
            map22.write_text('{"events": []}', encoding="utf-8")
            champion.write_text('{"events": []}', encoding="utf-8")

            self.assertEqual(list_source_files(root), [champion, map11])
