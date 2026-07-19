"""normalized translation 展示词表加载器（display_labels）测试。

覆盖 T3.3 词表外置的关键契约：从 dict/路径解析各分节、畸形词条不炸库、
默认 rules/display_labels.yaml 词条数与原内联 dict 一致、reference 地图英文
真名索引可正确按 id 命中、以及两类结果都进程内缓存（不重复文件 IO）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.display_labels import (
    DEFAULT_DISPLAY_LABELS_PATH,
    DEFAULT_REFERENCE_ROOT,
    get_display_labels,
    get_reference_map_names,
    load_display_labels,
    load_reference_map_names,
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
                "gameplay_event": {"first_blood": {"zh_CN": "一血", "en_US": "first blood"}},
                "maps": {11: "召唤师峡谷"},
            }
        )
        self.assertEqual(labels.phase["cast"], ("释放", "cast"))
        self.assertEqual(labels.action["recall"], ("回城", "recall"))
        self.assertEqual(labels.ability_component["passive_defense"], "被动防御")
        self.assertEqual(labels.gameplay_event["first_blood"], ("一血", "first blood"))
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
    """验证默认 rules/display_labels.yaml 词条数与原内联 dict 逐项一致。"""

    def test_default_display_labels_counts_match_original_inline_dicts(self) -> None:
        """迁移前后词条数不能变化（maps 为 T3.3 新增；批次5b 增 rank 相位与 dance；批次6a 增尾段相位）。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)
        self.assertEqual(len(labels.phase), 32)
        self.assertEqual(len(labels.action), 7)
        self.assertEqual(len(labels.ability_component), 4)
        self.assertEqual(len(labels.gameplay_event), 1)
        self.assertGreaterEqual(len(labels.maps), 6)

    def test_batch5b_entries_present(self) -> None:
        """批次5b 词条：spell rank 相位 1-3 级与 media_base dance 动作词。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)
        self.assertEqual(labels.phase["rankone"], ("1级", "rank 1"))
        self.assertEqual(labels.phase["ranktwo"], ("2级", "rank 2"))
        self.assertEqual(labels.phase["rankthree"], ("3级", "rank 3"))
        self.assertEqual(labels.action["dance"], ("舞蹈", "dance"))

    def test_batch6a_entries_present(self) -> None:
        """批次6a 词条：media_base 尾段相位（起手/循环）与无尾段整段音效（overall=音效）。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)
        self.assertEqual(labels.phase["windup"], ("起手", "wind up"))
        self.assertEqual(labels.phase["loop"], ("循环", "loop"))
        self.assertEqual(labels.phase["overall"], ("音效", "sfx"))

    def test_batch6d_shop_section_present(self) -> None:
        """批次6d 商店词表：动作词 5 条；subject 含条件转写与 Data Dragon 核验装备名。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)
        self.assertEqual(len(labels.shop_action), 5)
        self.assertEqual(labels.shop_action["open"], ("开店", "shop open"))
        self.assertEqual(labels.shop_action["purchase"], ("购买", "purchase"))
        self.assertEqual(labels.shop_subject["SpiritVisage"], "振奋盔甲")
        self.assertEqual(labels.shop_subject["FrostQueensClaim"], "冰霜女皇的旨令")
        self.assertEqual(labels.shop_subject["YommusGhostblade"], "幽梦之灵")
        self.assertEqual(labels.shop_subject["NoPurchase"], "未购买")
        # 语义未证实的计数词不入表（宁缺毋错）
        self.assertNotIn("CountTen", labels.shop_subject)
        self.assertNotIn("CountSeven", labels.shop_subject)

    def test_default_announcer_section_matches_batch5a_vocab(self) -> None:
        """announcer 段词条数与批次5a 词表一致（第一批 20 + 第二批 19）。"""

        labels = load_display_labels(REPO_ROOT / DEFAULT_DISPLAY_LABELS_PATH)
        self.assertEqual(len(labels.announcer_event), 39)
        self.assertEqual(len(labels.announcer_team), 6)
        self.assertEqual(labels.announcer_event["ChampionPentaKill"], "五杀")
        self.assertEqual(labels.announcer_team["YourTeam"], "己方")
        # 第二批反社区惯称词条：Godlike=接近神、Legendary=超神（勿混淆）。
        self.assertEqual(labels.announcer_event["Godlike"], "接近神")
        self.assertEqual(labels.announcer_event["Legendary"], "超神")
        self.assertEqual(labels.announcer_event["KillingSpreeSet6"], "超神")
        self.assertEqual(labels.announcer_event["DampenerDie"], "召唤水晶被摧毁")

    def test_get_display_labels_is_cached_singleton(self) -> None:
        """默认词表应进程内缓存，两次调用返回同一对象。"""

        first = get_display_labels()
        second = get_display_labels()
        self.assertIs(first, second)


class ReferenceMapNamesTests(unittest.TestCase):
    """验证 reference/maps 英文真名索引（按 entity.id 建索引）。"""

    def test_load_reference_map_names_reads_known_ids(self) -> None:
        """已知地图 id 应命中 reference 记录的 entity.names.en_US[0].value。"""

        names = load_reference_map_names(REPO_ROOT / DEFAULT_REFERENCE_ROOT)
        self.assertEqual(names[11], "Summoner's Rift")
        self.assertEqual(names[12], "Random Map")
        self.assertEqual(names[30], "Arena")
        self.assertEqual(names[22], "Teamfight Tactics")
        self.assertEqual(names[0], "Common")

    def test_get_reference_map_names_is_cached_singleton(self) -> None:
        """reference 地图英文真名索引应进程内缓存，避免逐条记录重复文件 IO。"""

        first = get_reference_map_names()
        second = get_reference_map_names()
        self.assertIs(first, second)


if __name__ == "__main__":
    unittest.main()
