"""内部 reference 精简合集测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.reference_collection import (
    ReferenceCollection,
    ReferenceEntityInput,
    ReferenceItemInput,
    ReferenceMapInput,
    ReferenceSkinInput,
    ReferenceSpellInput,
    TextSnapshotInput,
    load_reference_collection,
)


class ReferenceCollectionTests(unittest.TestCase):
    """验证 reference 合集的稳定写入和增量合并。"""

    def test_roundtrip_writes_stable_champion_yaml(self) -> None:
        """英雄 reference 写出后可读取，重复写入不改变内容。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=266,
                    alias="Aatrox",
                    names={"en_US": "Aatrox", "zh_CN": "亚托克斯"},
                    source="data_dragon",
                    version="16.12.1",
                )
            )
            collection.merge_spell(
                "Aatrox",
                ReferenceSpellInput(
                    slot="Q",
                    id="AatroxQ",
                    names={"en_US": "The Darkin Blade", "zh_CN": "暗裔利刃"},
                    source="data_dragon",
                    version="16.12.1",
                ),
            )
            collection.write()

            first = (root / "champions" / "Aatrox.yaml").read_text(encoding="utf-8")
            loaded = load_reference_collection(root)
            loaded.write()
            second = (root / "champions" / "Aatrox.yaml").read_text(encoding="utf-8")

        self.assertEqual(first, second)
        self.assertIn("alias: Aatrox", first)
        self.assertIn("zh_CN", first)

    def test_merge_champion_records_title_nicknames_and_search_alias(self) -> None:
        """英雄实体可记录称号、俗称和拼音搜索别名，并保留来源。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=266,
                    alias="Aatrox",
                    names={"zh_CN": "亚托克斯"},
                    title={"zh_CN": "暗裔剑魔"},
                    nicknames={"zh_CN": ["剑魔"]},
                    search_alias={"zh_CN": ["jianmo", "ytks"]},
                    source="tencent_101",
                    version="16.13",
                )
            )
            collection.write()
            loaded = load_reference_collection(root)
            entity = loaded.champions["Aatrox"]["entity"]

        self.assertEqual([item["value"] for item in entity["names"]["zh_CN"]], ["亚托克斯"])
        self.assertEqual([item["value"] for item in entity["title"]["zh_CN"]], ["暗裔剑魔"])
        self.assertEqual([item["value"] for item in entity["nicknames"]["zh_CN"]], ["剑魔"])
        self.assertEqual(
            [item["value"] for item in entity["search_alias"]["zh_CN"]],
            ["jianmo", "ytks"],
        )
        self.assertEqual(
            entity["title"]["zh_CN"][0]["sources"],
            [{"source": "tencent_101", "first_seen": "16.13", "last_seen": "16.13"}],
        )

    def test_title_merges_sources_across_inputs_without_duplicating_value(self) -> None:
        """同一称号来自多个来源时只保留一个值并合并来源。"""

        collection = ReferenceCollection(Path("unused"))
        collection.merge_champion(
            ReferenceEntityInput(
                id=266,
                alias="Aatrox",
                names={"zh_CN": "亚托克斯"},
                title={"zh_CN": "暗裔剑魔"},
                source="data_dragon",
                version="16.5.1",
            )
        )
        collection.merge_champion(
            ReferenceEntityInput(
                id=266,
                alias="Aatrox",
                names={"zh_CN": "亚托克斯"},
                title={"zh_CN": "暗裔剑魔"},
                source="tencent_101",
                version="16.13",
            )
        )
        titles = collection.champions["Aatrox"]["entity"]["title"]["zh_CN"]

        self.assertEqual([item["value"] for item in titles], ["暗裔剑魔"])
        self.assertEqual(
            [source["source"] for source in titles[0]["sources"]],
            ["data_dragon", "tencent_101"],
        )

    def test_source_versions_fold_into_interval(self) -> None:
        """同一来源跨版本确认时只滚动区间端点，来源行数不随版本数增长。"""

        collection = ReferenceCollection(Path("unused"))
        for version in ("16.5.1", "16.14.1", "16.9.1"):
            collection.merge_champion(
                ReferenceEntityInput(
                    id=266,
                    alias="Aatrox",
                    names={"zh_CN": "亚托克斯"},
                    source="data_dragon",
                    version=version,
                )
            )
        sources = collection.champions["Aatrox"]["entity"]["names"]["zh_CN"][0]["sources"]

        self.assertEqual(
            sources,
            [{"source": "data_dragon", "first_seen": "16.5.1", "last_seen": "16.14.1"}],
        )

    def test_legacy_per_version_sources_fold_on_touch(self) -> None:
        """历史逐版本来源条目在再次合并时就地折叠为区间形式。"""

        collection = ReferenceCollection(Path("unused"))
        collection.merge_champion(
            ReferenceEntityInput(
                id=266,
                alias="Aatrox",
                names={"zh_CN": "亚托克斯"},
                source="data_dragon",
                version="16.5.1",
            )
        )
        rows = collection.champions["Aatrox"]["entity"]["names"]["zh_CN"][0]["sources"]
        rows[:] = [
            {"source": "data_dragon", "version": "16.5.1"},
            {"source": "data_dragon", "version": "16.14.1"},
            {"source": "game_data", "version": "16.5"},
        ]

        collection.merge_champion(
            ReferenceEntityInput(
                id=266,
                alias="Aatrox",
                names={"zh_CN": "亚托克斯"},
                source="data_dragon",
                version="16.15.1",
            )
        )

        self.assertEqual(
            rows,
            [
                {"source": "data_dragon", "first_seen": "16.5.1", "last_seen": "16.15.1"},
                {"source": "game_data", "first_seen": "16.5", "last_seen": "16.5"},
            ],
        )

    def test_merge_champion_without_extra_fields_omits_empty_buckets(self) -> None:
        """不提供称号/俗称/拼音时实体不写出空 map。"""

        collection = ReferenceCollection(Path("unused"))
        collection.merge_champion(
            ReferenceEntityInput(
                id=1,
                alias="Annie",
                names={"en_US": "Annie"},
                source="game_data",
                version="16.12",
            )
        )
        entity = collection.champions["Annie"]["entity"]

        self.assertNotIn("title", entity)
        self.assertNotIn("nicknames", entity)
        self.assertNotIn("search_alias", entity)

    def test_merge_keeps_multiple_spell_ids_and_names_as_stable_sets(self) -> None:
        """同一技能的多个 ID 和名称以稳定集合保留。"""

        with tempfile.TemporaryDirectory() as tmp:
            collection = ReferenceCollection(Path(tmp) / "reference")
            collection.merge_champion(
                ReferenceEntityInput(
                    id=1,
                    alias="Annie",
                    names={"en_US": "Annie"},
                    source="game_data",
                    version="16.12",
                )
            )
            collection.merge_spell(
                "Annie",
                ReferenceSpellInput(
                    slot="Q",
                    id="AnnieQ",
                    names={"en_US": "Disintegrate"},
                    source="game_data",
                    version="16.12",
                ),
            )
            collection.merge_spell(
                "Annie",
                ReferenceSpellInput(
                    slot="Q",
                    id="AnnieQNew",
                    names={"en_US": "Disintegrate!"},
                    source="game_data",
                    version="16.13",
                ),
            )
            champion = collection.champions["Annie"]
            spell = champion["spells"][0]

        self.assertEqual([item["value"] for item in spell["ids"]], ["AnnieQ", "AnnieQNew"])
        self.assertEqual(
            [item["value"] for item in spell["names"]["en_US"]],
            ["Disintegrate", "Disintegrate!"],
        )
        self.assertEqual(spell["ids"][0]["sources"][0]["first_seen"], "16.12")

    def test_merge_champion_alias_is_case_insensitive(self) -> None:
        """跨来源 alias 大小写漂移时合并到同一英雄。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=9,
                    alias="FiddleSticks",
                    names={"en_US": "FiddleSticks"},
                    source="game_data",
                    version="16.5",
                )
            )
            collection.merge_champion(
                ReferenceEntityInput(
                    id=9,
                    alias="Fiddlesticks",
                    names={"en_US": "Fiddlesticks"},
                    source="data_dragon",
                    version="16.5.1",
                )
            )
            collection.merge_spell(
                "Fiddlesticks",
                ReferenceSpellInput(
                    slot="Q",
                    id="FiddlesticksQ",
                    names={"en_US": "Terrify"},
                    source="data_dragon",
                    version="16.5.1",
                ),
            )
            collection.write()
            loaded = load_reference_collection(root)
            file_exists = (root / "champions" / "FiddleSticks.yaml").is_file()

        self.assertEqual(len(loaded.champions), 1)
        self.assertTrue(file_exists)
        self.assertEqual(len(loaded.champions["Fiddlesticks"]["spells"]), 1)

    def test_description_similarity_marks_review_windows(self) -> None:
        """技能描述保存 hash、版本区间、相似度和 review 状态。"""

        collection = ReferenceCollection(Path("unused"))
        collection.merge_champion(
            ReferenceEntityInput(
                id=1,
                alias="Annie",
                names={"en_US": "Annie"},
                source="game_data",
                version="16.12",
            )
        )
        collection.merge_description(
            "Annie",
            "Q",
            TextSnapshotInput(
                language="en_US",
                value="Annie hurls a Mana infused fireball, dealing magic damage.",
                source="data_dragon",
                version="16.12.1",
            ),
        )
        collection.merge_description(
            "Annie",
            "Q",
            TextSnapshotInput(
                language="en_US",
                value="Annie hurls a Mana infused fireball, dealing magic damage!",
                source="data_dragon",
                version="16.12.2",
            ),
        )
        collection.merge_description(
            "Annie",
            "Q",
            TextSnapshotInput(
                language="en_US",
                value="Annie unleashes a redesigned spell with a different effect.",
                source="data_dragon",
                version="16.13.1",
            ),
        )

        spell = collection.champions["Annie"]["spells"][0]
        snapshots = spell["descriptions"]["en_US"]["snapshots"]

        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0]["first_seen"], "16.12.1")
        self.assertEqual(snapshots[0]["last_seen"], "16.12.2")
        self.assertEqual(snapshots[0]["review_status"], "accepted")
        self.assertEqual(snapshots[1]["first_seen"], "16.13.1")
        self.assertEqual(snapshots[1]["review_status"], "needs_review")
        self.assertGreater(snapshots[1]["similarity_to_previous"], 0.0)
        self.assertLess(snapshots[1]["similarity_to_previous"], 0.97)

    def test_manifest_records_languages_and_source_versions(self) -> None:
        """manifest 保存语言和来源版本信息，不用路径区分版本。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root, languages=("en_US", "zh_CN"))
            collection.set_source_version("game_data", main_version="16.12", snapshot_ref="abc")
            collection.set_source_version("data_dragon", version="16.12.1")
            collection.write()
            manifest = (root / "manifest.yaml").read_text(encoding="utf-8")

        self.assertIn("schema_version: 2", manifest)
        self.assertIn("- en_US", manifest)
        self.assertIn("main_version: '16.12'", manifest)
        self.assertFalse((root / "16.12").exists())

    def test_merge_item_writes_items_group_keyed_by_id(self) -> None:
        """装备实体以数字 id 为键写入独立 items 分组，双语名带来源区间。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_item(
                ReferenceItemInput(
                    id=1035,
                    names={"en_US": "Emberknife", "zh_CN": "灰烬小刀"},
                    source="game_data",
                    version="16.14",
                )
            )
            collection.write()
            loaded = load_reference_collection(root)
            item_file_exists = (root / "items" / "1035.yaml").is_file()

        self.assertIn("1035", loaded.items)
        entity = loaded.items["1035"]["entity"]
        self.assertEqual(entity["type"], "item")
        self.assertEqual(entity["id"], 1035)
        self.assertEqual([row["value"] for row in entity["names"]["zh_CN"]], ["灰烬小刀"])
        self.assertTrue(item_file_exists)

    def test_merge_map_writes_maps_group(self) -> None:
        """地图实体写入独立 maps 分组。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_map(
                ReferenceMapInput(
                    id=11,
                    alias="SummonersRift",
                    names={"en_US": "Summoner's Rift", "zh_CN": "召唤师峡谷"},
                    source="game_data",
                    version="16.12",
                )
            )
            collection.write()
            loaded = load_reference_collection(root)

        self.assertIn("SummonersRift", loaded.maps)
        self.assertEqual(loaded.maps["SummonersRift"]["entity"]["id"], 11)

    def test_merge_skin_writes_readable_skin_reference(self) -> None:
        """具体皮肤写入独立 reference/skins 分组。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_skin(
                ReferenceSkinInput(
                    champion_alias="Galio",
                    champion_id=3,
                    skin_number=13,
                    skin_token="Skin13",
                    id=3013,
                    alias="GalioSkin13",
                    names={"en_US": "Infernal Galio", "zh_CN": "地狱火 加里奥"},
                    skinlines=[],
                    source="game_data",
                    version="16.5",
                )
            )
            collection.write()
            loaded = load_reference_collection(root)
            manifest = (root / "manifest.yaml").read_text(encoding="utf-8")

        self.assertIn("Galio/Skin13", loaded.skins)
        entity = loaded.skins["Galio/Skin13"]["entity"]
        self.assertEqual(entity["type"], "skin")
        self.assertEqual(entity["champion_alias"], "Galio")
        self.assertEqual(entity["skin_token"], "Skin13")
        self.assertIn("skins: 1", manifest)

    def test_repeated_write_keeps_manifest_timestamp_stable(self) -> None:
        """同一个 collection 重复写入不会因为 timestamp 产生 diff。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.write()
            first = (root / "manifest.yaml").read_text(encoding="utf-8")
            collection.write()
            second = (root / "manifest.yaml").read_text(encoding="utf-8")

        self.assertEqual(first, second)

    def test_loaded_collection_updates_timestamp_after_real_change(self) -> None:
        """加载旧合集后发生真实变更时刷新更新时间。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.updated_at = "2026-06-15T00:00:00Z"
            collection.write()
            loaded = load_reference_collection(root)
            loaded.merge_champion(
                ReferenceEntityInput(
                    id=1,
                    alias="Annie",
                    names={"en_US": "Annie"},
                    source="game_data",
                    version="16.12",
                )
            )
            loaded.write()
            manifest = (root / "manifest.yaml").read_text(encoding="utf-8")

        self.assertNotIn("2026-06-15T00:00:00Z", manifest)

    def test_write_removes_stale_entity_files(self) -> None:
        """写入分组时清理当前集合外的旧 YAML。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=1,
                    alias="Annie",
                    names={"en_US": "Annie"},
                    source="game_data",
                    version="16.12",
                )
            )
            collection.write()
            collection.champions.clear()
            collection.write()

        self.assertFalse((root / "champions" / "Annie.yaml").exists())


if __name__ == "__main__":
    unittest.main()
