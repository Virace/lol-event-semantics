"""腾讯 hero_list 数据源下载与名称体系解析测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.tencent_hero_list_source import (
    download_hero_list,
    load_hero_list,
    parse_hero_entries,
)


SAMPLE_PAYLOAD = {
    "hero": [
        {
            "heroId": "266",
            "alias": "Aatrox",
            "title": "亚托克斯",  # 俗名（与 DDragon zh_CN.title 一致）
            "name": "暗裔剑魔",  # 称号
            "keywords": "剑魔,亚托克斯,暗裔剑魔,jianmo,yatuokesi,Aatrox,jm,ayjm,ytks",
        },
        {
            "heroId": "9",
            "alias": "FiddleSticks",  # 大小写与仓库 Fiddlesticks 不同
            "title": "费德提克",
            "name": "远古恐惧",
            "keywords": "稻草人,费德提克,远古恐惧,daocaoren,FiddleSticks,dcr",
        },
        {
            "heroId": "145",
            "alias": "Kaisa",
            "title": "卡莎",
            "name": "虚空之女",
            "keywords": "卡莎,虚空之女,kasha,Kaisa,xkzn",  # 无额外俗称
        },
    ],
    "version": "16.13",
    "fileName": "hero_list.js",
}


class TencentHeroListSourceTests(unittest.TestCase):
    """验证 hero_list 下载与名称体系解析。"""

    def test_download_writes_local_hero_list_and_returns_version(self) -> None:
        """注入 fetcher 下载后写出本地缓存并返回 version。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return json.dumps(SAMPLE_PAYLOAD, ensure_ascii=False).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "hero-list"
            version = download_hero_list(
                output_root=root,
                url="https://example.test/hero_list.js",
                fetcher=fetch,
            )
            cached = load_hero_list(root / "hero_list.json")

        self.assertEqual(version, "16.13")
        self.assertEqual(calls, ["https://example.test/hero_list.js"])
        self.assertEqual(len(cached["hero"]), 3)

    def test_parse_routes_title_to_common_name_and_name_to_title(self) -> None:
        """title=俗名、name=称号，并按 heroId 转 int。"""

        entries = {entry.hero_id: entry for entry in parse_hero_entries(SAMPLE_PAYLOAD)}
        aatrox = entries[266]

        self.assertEqual(aatrox.hero_id, 266)
        self.assertEqual(aatrox.common_name, "亚托克斯")
        self.assertEqual(aatrox.title, "暗裔剑魔")
        self.assertEqual(aatrox.version, "16.13")

    def test_parse_extracts_cjk_nicknames_excluding_common_name_and_title(self) -> None:
        """俗称取 keywords 中 CJK，并排除俗名与称号本身。"""

        entries = {entry.hero_id: entry for entry in parse_hero_entries(SAMPLE_PAYLOAD)}

        self.assertEqual(entries[266].nicknames, ("剑魔",))
        self.assertEqual(entries[9].nicknames, ("稻草人",))
        self.assertEqual(entries[145].nicknames, ())

    def test_parse_extracts_non_cjk_search_alias_excluding_english_alias(self) -> None:
        """search_alias 取 keywords 中非 CJK，并去掉与英文 alias 重复者。"""

        entries = {entry.hero_id: entry for entry in parse_hero_entries(SAMPLE_PAYLOAD)}
        aatrox = entries[266]

        self.assertNotIn("Aatrox", aatrox.search_alias)
        self.assertIn("jianmo", aatrox.search_alias)
        self.assertIn("ytks", aatrox.search_alias)

    def test_parse_keeps_hero_id_as_int_join_key(self) -> None:
        """heroId 字符串解析为 int，作为与 entity.id 的 join key。"""

        entries = parse_hero_entries(SAMPLE_PAYLOAD)
        ids = sorted(entry.hero_id for entry in entries)

        self.assertEqual(ids, [9, 145, 266])
        self.assertTrue(all(isinstance(entry.hero_id, int) for entry in entries))


if __name__ == "__main__":
    unittest.main()
