"""在线 reference source 编排测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.online_sources import prepare_online_reference_sources


class OnlineSourcesTests(unittest.TestCase):
    """验证在线 source 会落成本地 ingestion layout。"""

    def test_prepare_online_reference_sources_downloads_both_sources(self) -> None:
        """下载 GAME data 与 Data Dragon 的最小子集。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            if url.endswith("/api/versions.json"):
                return json.dumps(["16.12.1"]).encode("utf-8")

            if "/global/default/v1/champion-summary.json" in url:
                return json.dumps([{"id": 1, "alias": "Annie", "name": "Annie"}]).encode("utf-8")

            if "/global/zh_cn/v1/champion-summary.json" in url:
                return json.dumps([{"id": 1, "alias": "Annie", "name": "安妮"}], ensure_ascii=False).encode("utf-8")

            if url.endswith("/maps.json"):
                return json.dumps([{"id": 11, "mapStringId": "SummonersRift", "name": "Summoner's Rift"}]).encode(
                    "utf-8"
                )

            if url.endswith("/skinlines.json"):
                return json.dumps([{"id": 19, "name": "Star Guardian Season 1"}]).encode("utf-8")

            if "/global/" in url and url.endswith("/champions/1.json"):
                return json.dumps({"id": 1, "alias": "Annie", "name": "Annie", "spells": []}).encode("utf-8")

            if url.endswith("/data/en_US/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie"}}}).encode("utf-8")

            if url.endswith("/data/zh_CN/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮"}}}, ensure_ascii=False).encode("utf-8")

            if url.endswith("/data/en_US/championFull.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie", "spells": []}}}).encode("utf-8")

            if url.endswith("/data/zh_CN/championFull.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮", "spells": []}}}, ensure_ascii=False).encode("utf-8")

            if url.endswith("/champion/Annie.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie", "spells": []}}}).encode("utf-8")

            if url.endswith("/hero_list.js"):
                return json.dumps(
                    {
                        "version": "16.13",
                        "hero": [
                            {
                                "heroId": "1",
                                "alias": "Annie",
                                "title": "安妮",
                                "name": "黑暗之女",
                                "keywords": "安妮,黑暗之女,annie,am",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            sources = prepare_online_reference_sources(
                cache_root=Path(tmp) / "cache",
                game_version="16.12",
                game_data_base_url="https://example.test/game-data",
                data_dragon_base_url="https://example.test/ddragon",
                hero_list_url="https://example.test/hero_list.js",
                fetcher=fetch,
            )

            self.assertEqual(sources.data_dragon_version, "16.12.1")
            self.assertEqual(sources.hero_list_version, "16.13")
            self.assertTrue((sources.game_data_root / "global" / "default" / "v1" / "champions" / "1.json").is_file())
            self.assertTrue((sources.game_data_root / "global" / "default" / "v1" / "skinlines.json").is_file())
            self.assertTrue((sources.data_dragon_root / "16.12.1" / "data" / "zh_CN" / "champion.json").is_file())
            self.assertTrue((sources.hero_list_root / "hero_list.json").is_file())

        self.assertEqual(len(calls), 14)


if __name__ == "__main__":
    unittest.main()
