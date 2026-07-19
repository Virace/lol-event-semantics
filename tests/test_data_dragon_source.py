"""Data Dragon 在线 source adapter 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.data_dragon_source import (
    DataDragonSourceError,
    download_data_dragon_subset,
    select_data_dragon_version,
)


class DataDragonSourceTests(unittest.TestCase):
    """验证 Data Dragon 按需下载。"""

    def test_select_data_dragon_version_matches_main_version(self) -> None:
        """Data Dragon 版本选择只按主版本匹配。"""

        self.assertEqual(select_data_dragon_version(["16.13.1", "16.12.2", "16.12.1"], "16.12"), "16.12.2")

    def test_select_data_dragon_version_fails_without_main_match(self) -> None:
        """没有主版本匹配时失败。"""

        with self.assertRaisesRegex(DataDragonSourceError, "16.12"):
            select_data_dragon_version(["16.13.1"], "16.12")

    def test_download_data_dragon_subset_writes_dragontail_layout(self) -> None:
        """按需下载 summary 和 champion detail 到本地 layout。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            if url.endswith("/api/versions.json"):
                return json.dumps(["16.13.1", "16.12.1"]).encode("utf-8")

            if url.endswith("/data/en_US/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie"}}}).encode("utf-8")

            if url.endswith("/data/zh_CN/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮"}}}, ensure_ascii=False).encode("utf-8")

            if url.endswith("/data/en_US/championFull.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie", "spells": []}}}).encode("utf-8")

            if url.endswith("/data/zh_CN/championFull.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮", "spells": []}}}, ensure_ascii=False).encode("utf-8")

            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dragontail"
            version = download_data_dragon_subset(
                output_root=root,
                game_version="16.12",
                languages=("en_US", "zh_CN"),
                fetcher=fetch,
            )

            self.assertEqual(version, "16.12.1")
            self.assertTrue((root / "16.12.1" / "data" / "en_US" / "champion.json").is_file())
            self.assertTrue((root / "16.12.1" / "data" / "zh_CN" / "champion" / "Annie.json").is_file())

        self.assertEqual(len(calls), 5)

    def test_download_data_dragon_subset_reuses_cached_detail(self) -> None:
        """已有 champion detail 缓存时不重复请求。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            if url.endswith("/api/versions.json"):
                return json.dumps(["16.12.1"]).encode("utf-8")

            if url.endswith("/data/en_US/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie"}}}).encode("utf-8")

            if url.endswith("/data/en_US/championFull.json"):
                raise OSError("fixture championFull unavailable")

            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dragontail"
            detail_path = root / "16.12.1" / "data" / "en_US" / "champion" / "Annie.json"
            detail_path.parent.mkdir(parents=True)
            detail_path.write_text(
                json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie", "spells": []}}}),
                encoding="utf-8",
            )

            version = download_data_dragon_subset(
                output_root=root,
                game_version="16.12",
                languages=("en_US",),
                fetcher=fetch,
            )

        self.assertEqual(version, "16.12.1")
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
