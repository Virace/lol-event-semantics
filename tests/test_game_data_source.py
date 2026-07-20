"""GAME data snapshot 在线 source adapter 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.game_data_source import (
    GameDataSourceError,
    champion_ids,
    download_game_data_snapshot_subset,
)


class GameDataSourceTests(unittest.TestCase):
    """验证 GAME data snapshot 按需下载。"""

    def test_champion_ids_dedupes_and_sorts(self) -> None:
        """英雄 id 从 summary 中稳定读取。"""

        self.assertEqual(champion_ids([{"id": "2"}, {"id": 1}, {"id": 1}, {"id": None}]), [1, 2])

    def test_champion_ids_requires_list(self) -> None:
        """summary 不是数组时失败。"""

        with self.assertRaisesRegex(GameDataSourceError, "list"):
            champion_ids({"data": []})

    def test_download_game_data_snapshot_subset_writes_ingestion_layout(self) -> None:
        """按需下载 GAME data 到现有 ingestion layout。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            if url.endswith("/global/default/v1/champion-summary.json"):
                return json.dumps([{"id": 1, "alias": "Annie", "name": "Annie"}]).encode("utf-8")

            if url.endswith("/global/zh_CN/v1/champion-summary.json"):
                return json.dumps([{"id": 1, "alias": "Annie", "name": "安妮"}], ensure_ascii=False).encode("utf-8")

            if url.endswith("/maps.json"):
                return json.dumps([{"id": 11, "mapStringId": "SummonersRift", "name": "Summoner's Rift"}]).encode(
                    "utf-8"
                )

            if url.endswith("/skinlines.json"):
                return json.dumps([{"id": 19, "name": "Star Guardian Season 1"}]).encode("utf-8")

            if url.endswith("/global/default/v1/items.json"):
                return json.dumps([{"id": 1035, "name": "Emberknife"}]).encode("utf-8")

            if url.endswith("/global/zh_CN/v1/items.json"):
                return json.dumps([{"id": 1035, "name": "灰烬小刀"}], ensure_ascii=False).encode("utf-8")

            if url.endswith("/champions/1.json"):
                return json.dumps({"id": 1, "alias": "Annie", "name": "Annie", "spells": []}).encode("utf-8")

            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "game-data"
            download_game_data_snapshot_subset(
                output_root=root,
                game_version="16.12",
                languages=("en_US", "zh_CN"),
                base_url="https://example.test/game-data",
                fetcher=fetch,
            )

            self.assertTrue((root / "global" / "default" / "v1" / "champion-summary.json").is_file())
            self.assertTrue((root / "global" / "default" / "v1" / "skinlines.json").is_file())
            self.assertTrue((root / "global" / "default" / "v1" / "items.json").is_file())
            self.assertTrue((root / "global" / "zh_cn" / "v1" / "items.json").is_file())
            self.assertTrue((root / "global" / "zh_cn" / "v1" / "champions" / "1.json").is_file())

        self.assertEqual(len(calls), 10)


if __name__ == "__main__":
    unittest.main()
