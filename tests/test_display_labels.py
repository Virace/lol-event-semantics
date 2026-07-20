"""normalized translation 展示词表加载器（display_labels）测试。

覆盖词表外置的关键契约：从 dict/路径解析各分节、畸形词条不炸库、
默认 rules/display_labels.yaml 与 reference 地图真名索引可加载且结构良构
（具体词条内容属数据修订范畴，不在测试中锁定）、以及两类结果都进程内
缓存（不重复文件 IO）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.display_labels import (
    DEFAULT_DISPLAY_LABELS_PATH,
    get_display_labels,
    get_reference_item_names,
    get_reference_map_names,
    load_display_labels,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


class LoadDisplayLabelsFromDictTests(unittest.TestCase):
    """验证从注入 dict 解析展示词表各分节（供调用方测试注入替代词表）。"""

    def test_parses_all_sections(self) -> None:
        """五个分节都应按预期结构解析。"""

        labels = load_display_labels(
            {
                "phase": {"cast": {"zh_CN": "释放", "en_US": "cast"}},
                "action": {"recall": {"zh_CN": "回城", "en_US": "recall"}},
                "ability_component": {"passive_defense": "被动防御"},
                "gameplay_event": {"first_blood": {"zh_CN": "第一滴血", "en_US": "first blood"}},
                "maps": {11: "召唤师峡谷"},
            }
        )
        self.assertEqual(labels.phase["cast"], ("释放", "cast"))
        self.assertEqual(labels.action["recall"], ("回城", "recall"))
        self.assertEqual(labels.ability_component["passive_defense"], "被动防御")
        self.assertEqual(labels.gameplay_event["first_blood"], ("第一滴血", "first blood"))
        self.assertEqual(labels.maps[11], "召唤师峡谷")

    def test_string_map_keys_are_coerced_to_int(self) -> None:
        """maps 分节的字符串形式 key 应规范为 int，与 semantic_slots.map_id 对齐。"""

        labels = load_display_labels({"maps": {"11": "召唤师峡谷"}})
        self.assertEqual(labels.maps[11], "召唤师峡谷")

    def test_malformed_rows_are_skipped_not_raised(self) -> None:
        """畸形词条（缺字段/非法 key）应被跳过而不是抛错，避免一条坏词条拖垮整个词表。"""

        labels = load_display_labels(
            {
                "phase": {"cast": {"zh_CN": "释放"}},  # 缺 en_US
                "maps": {"not_a_number": "无效"},
            }
        )
        self.assertNotIn("cast", labels.phase)
        self.assertEqual(labels.maps, {})

    def test_empty_dict_yields_empty_sections(self) -> None:
        """空 dict 应解析为全空分节，不抛错。"""

        labels = load_display_labels({})
        self.assertEqual(labels.phase, {})
        self.assertEqual(labels.action, {})
        self.assertEqual(labels.ability_component, {})
        self.assertEqual(labels.gameplay_event, {})
        self.assertEqual(labels.maps, {})
        self.assertEqual(labels.announcer_event, {})
        self.assertEqual(labels.announcer_team, {})

    def test_parses_nested_announcer_section(self) -> None:
        """announcer 嵌套段（event/team 子表）应解析到独立字段。"""

        labels = load_display_labels(
            {
                "announcer": {
                    "event": {"Ace": "团灭"},
                    "team": {"EnemyTeam": "敌方"},
                }
            }
        )
        self.assertEqual(labels.announcer_event["Ace"], "团灭")
        self.assertEqual(labels.announcer_team["EnemyTeam"], "敌方")

    def test_malformed_announcer_section_yields_empty_maps(self) -> None:
        """announcer 段非 mapping（或子表畸形）时应解析为空表，不抛错。"""

        labels = load_display_labels({"announcer": "broken"})
        self.assertEqual(labels.announcer_event, {})
        self.assertEqual(labels.announcer_team, {})


class LoadDisplayLabelsFromPathTests(unittest.TestCase):
    """验证从文件路径加载展示词表（供测试注入替代文件）。"""

    def test_load_from_explicit_path(self) -> None:
        """显式路径应绕过默认词表，读取指定文件内容。"""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "display_labels.yaml"
            path.write_text(
                "phase:\n  cast:\n    zh_CN: 释放\n    en_US: cast\n",
                encoding="utf-8",
            )
            labels = load_display_labels(path)
            self.assertEqual(labels.phase["cast"], ("释放", "cast"))


class DefaultDisplayLabelsTests(unittest.TestCase):
    """验证默认 rules/display_labels.yaml 可加载且各分节结构良构（不锁定具体词条内容）。"""

    def test_default_display_labels_sections_are_well_formed(self) -> None:
        """核心分节非空、词条形状正确；具体词条中文/英文属数据修订范畴，不在此断言。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)

        # 双语分节：值为 (zh, en) 二元组且两侧非空。
        for section in (labels.phase, labels.action, labels.gameplay_event, labels.shop_action):
            self.assertTrue(section)
            for zh, en in section.values():
                self.assertTrue(zh)
                self.assertTrue(en)

        # 单语分节：值为非空字符串。
        for section in (
            labels.ability_component,
            labels.shop_subject,
            labels.announcer_event,
            labels.announcer_team,
            labels.announcer_subject,
            labels.announcer_mode,
        ):
            self.assertTrue(section)
            for value in section.values():
                self.assertTrue(value)

        # maps 分节：key 已规范为 int、值为非空地图名。
        self.assertTrue(labels.maps)
        for map_id, name in labels.maps.items():
            self.assertIsInstance(map_id, int)
            self.assertTrue(name)

    def test_get_display_labels_is_cached_singleton(self) -> None:
        """默认词表应进程内缓存，两次调用返回同一对象。"""

        first = get_display_labels()
        second = get_display_labels()
        self.assertIs(first, second)


class ReferenceMapNamesTests(unittest.TestCase):
    """验证 reference/maps 英文真名索引（按 entity.id 建索引）。"""

    def test_reference_map_names_build_id_index(self) -> None:
        """真实 reference/maps 应产出非空的 int id → 非空英文真名索引（不锁定具体地图名）。"""

        names = get_reference_map_names()
        self.assertTrue(names)
        for map_id, name in names.items():
            self.assertIsInstance(map_id, int)
            self.assertIsInstance(name, str)
            self.assertTrue(name)

    def test_get_reference_map_names_is_cached_singleton(self) -> None:
        """reference 地图英文真名索引应进程内缓存，避免逐条记录重复文件 IO。"""

        first = get_reference_map_names()
        second = get_reference_map_names()
        self.assertIs(first, second)


class ReferenceItemNamesTests(unittest.TestCase):
    """验证 reference/items 装备双语名索引（按 entity.id 建索引）。"""

    def test_reference_item_names_build_id_index(self) -> None:
        """真实 reference/items 应产出非空的 int id → 双语非空名索引（不锁定具体装备名）。"""

        names = get_reference_item_names()
        self.assertTrue(names)
        for item_id, labels in names.items():
            self.assertIsInstance(item_id, int)
            self.assertTrue(labels)
            self.assertLessEqual(set(labels), {"zh_CN", "en_US"})
            for value in labels.values():
                self.assertTrue(value)

    def test_get_reference_item_names_is_cached_singleton(self) -> None:
        """装备名索引应进程内缓存，并与地图索引共享同一次合集加载。"""

        first = get_reference_item_names()
        second = get_reference_item_names()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
