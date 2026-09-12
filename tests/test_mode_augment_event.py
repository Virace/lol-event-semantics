"""mode_augment_event frame（Arena/ARAM 增益系事件）测试。

结构断言风格：分派、角色齐备、词表外不建 frame、mode 合法缺席与渲染冒烟
均用注入词表验证，不锁定仓库词表的具体中文文案。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from reference_cache import reference_index

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import enrich_with_reference
from lol_event_semantics.display_labels import DisplayLabels, load_display_labels
from lol_event_semantics.normalized_translation_builder import (
    build_mode_augment_event,
    build_translation,
)
from lol_event_semantics.normalized_translation_model import (
    FRAME_REGISTRY,
    RenderOptions,
    TranslationBuildContext,
)
from lol_event_semantics.normalized_translation_renderer import render_translation

# 注入词表：Code 与词条均为测试自造值，证明分派与渲染不依赖仓库词表内容。
INJECTED_LABELS = DisplayLabels(
    phase={
        "state_activate": ("状态启动", "state started"),
        "state_deactivate": ("状态结束", "state ended"),
        "cast": ("释放", "cast"),
        "loop": ("循环", "loop"),
        "overall": ("整段音效", "full sfx"),
    },
    augments={
        "Ceremony": ("测试仪式增益", "Test Ceremony Augment"),
        "Goredrink": ("测试增益", "Test Augment"),
        "MadeUpAug": ("虚构增益", "Made Up Aug"),
        "ServeBeyondDeath": ("超越死亡的效劳", "Serve Beyond Death"),
        "Oathsworn": ("誓约者", "Oathsworn"),
    },
)
GET_LABELS_TARGET = "lol_event_semantics.normalized_translation_builder.get_display_labels"


def enriched_record(raw_event_name: str):
    """构建带 reference enrichment 的测试记录。"""

    return enrich_with_reference(
        normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
        reference_index(),
    )


class ModeAugmentDispatchTests(unittest.TestCase):
    """验证增益系三家族记录的分派与角色齐备。"""

    def test_cherry_augment_builds_frame_with_mode(self) -> None:
        """Cherry owner 的增益记录建 frame，mode 经 T3.4 词条表出中文。"""

        record = enriched_record("Play_sfx_Cherry_Augment_Goredrink_buffactivate")
        with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
            translation = build_translation(
                record, TranslationBuildContext(map_system_entity_labels={"cherry": "测试竞技模式"})
            )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "mode_augment_event")

        args = translation["arguments"]
        for role in FRAME_REGISTRY["mode_augment_event"].required_roles:
            self.assertIn(role, args)

        augment = args["augment"]
        self.assertEqual(augment["labels"]["zh_CN"], "测试增益")
        self.assertEqual(augment["labels"]["en_US"], "Test Augment")
        self.assertEqual(augment["provenance"]["source"], "display_labels.augments")
        self.assertEqual(augment["provenance"]["reference_kind"], "augment")
        self.assertEqual(augment["provenance"]["code"], "Goredrink")

        self.assertEqual(args["phase"]["key"], "state_activate")

        mode = args["mode"]
        self.assertEqual(mode["token"], "Cherry")
        zh_mode = mode["labels"]["zh_CN"]
        self.assertNotEqual(zh_mode, "Cherry")
        self.assertTrue(any("一" <= char <= "鿿" for char in zh_mode))

    def test_cherry_item_records_item_kind(self) -> None:
        """Cherry_Item 记录的 augment provenance 归类为 item。"""

        record = enriched_record("Play_sfx_Cherry_Item_MadeUpAug_buffactivate")
        with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
            translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "mode_augment_event")
        self.assertEqual(translation["arguments"]["augment"]["provenance"]["reference_kind"], "item")

    def test_aram_augment_uses_reviewed_mode_label(self) -> None:
        """ARAM 复用仓库已核验的“极地大乱斗”模式词条。"""

        record = enriched_record("Play_sfx_Augment_ARAM_MadeUpAug_buffactivate")
        with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
            translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "mode_augment_event")
        args = translation["arguments"]
        self.assertEqual(args["mode"]["key"], "ARAM")
        self.assertEqual(args["mode"]["labels"]["zh_CN"], "极地大乱斗")
        for role in FRAME_REGISTRY["mode_augment_event"].required_roles:
            self.assertIn(role, args)

    def test_explicit_borrowed_champion_spell_records_keep_ability_frames(self) -> None:
        """显式 owner+槽位+组件段由 ability builder 承接，不被增益 frame 抢占。"""

        cases = [
            ("Play_sfx_Augment_ARAM_Oathsworn_KalistaRMis_OnCast", "ability_cast_event", "r"),
            (
                "Play_sfx_Augment_ARAM_MidnightExpress_ThreshWShield_OnBuffActivate",
                "ability_state_change",
                "w",
            ),
            (
                "Play_sfx_Augment_ARAM_HoldVeryStill_TeemoPStealthBuff_OnBuffActivate",
                "ability_state_change",
                "p",
            ),
        ]
        for raw_event_name, expected_frame, expected_slot in cases:
            with self.subTest(raw_event_name=raw_event_name):
                record = enriched_record(raw_event_name)
                with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
                    translation = build_translation(record, TranslationBuildContext())

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], expected_frame)
                self.assertEqual(translation["arguments"]["ability_slot"]["value"], expected_slot)

    def test_non_spell_ultimate_word_stays_mode_augment_event(self) -> None:
        """Ultimate 普通词段不能被 broad Ult hint 误判为 R 技能。"""

        for shell_verb in ("Play", "Stop"):
            with self.subTest(shell_verb=shell_verb):
                record = enriched_record(f"{shell_verb}_sfx_Cherry_Augment_Ceremony_UltimateBackup_buffactivate")
                with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
                    translation = build_translation(record, TranslationBuildContext())

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "mode_augment_event")


class ModeAugmentGateTests(unittest.TestCase):
    """验证宁缺毋错门禁：词表外 Code 与词表外相位不建 frame。"""

    def test_vocab_miss_builds_no_frame(self) -> None:
        """词表外 Code 不建 frame，记录维持 legacy 展示。"""

        record = enriched_record("Play_sfx_Cherry_Augment_Goredrink_buffactivate")
        self.assertIsNone(build_mode_augment_event(record, TranslationBuildContext(), labels=DisplayLabels()))
        with patch(GET_LABELS_TARGET, return_value=DisplayLabels()):
            self.assertIsNone(build_translation(record, TranslationBuildContext()))

    def test_reviewed_buff_cast_borrowed_spell_keeps_augment_and_target(self) -> None:
        """已审 ARAM 增益须同时保留增益、借用英雄技能、施放阶段和听者目标。"""

        cases = [
            (
                "Play_sfx_Augment_ARAM_ServeBeyondDeath_RenataW_buffcast_player",
                "player",
                "自身",
                False,
            ),
            (
                "Play_sfx_Augment_ARAM_ServeBeyondDeath_RenataW_buffcast_others",
                "others",
                "其他玩家",
                False,
            ),
            (
                "Stop_sfx_Augment_ARAM_ServeBeyondDeath_RenataW_buffcast_others",
                "others",
                "其他玩家",
                True,
            ),
        ]
        context = TranslationBuildContext(
            champion_labels={"Renata": {"zh_CN": "烈娜塔 · 戈拉斯克", "en_US": "Renata Glasc"}}
        )
        for raw, qualifier, qualifier_label, stopped in cases:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                self.assertEqual(record.semantic_slots.get("phase"), "buff_cast")
                with patch(GET_LABELS_TARGET, return_value=INJECTED_LABELS):
                    translation = build_translation(record, context)

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "mode_augment_event")
                args = translation["arguments"]
                self.assertEqual(args["mode"]["key"], "ARAM")
                self.assertEqual(args["augment"]["key"], "ServeBeyondDeath")
                self.assertEqual(args["champion"]["alias"], "Renata")
                self.assertEqual(args["ability_slot"]["key"], "w")
                self.assertEqual(args["qualifier"]["key"], qualifier)
                self.assertEqual(args["phase"]["key"], "cast")
                if stopped:
                    self.assertEqual(args["playback"]["key"], "stop")

                rendered = render_translation(
                    translation,
                    RenderOptions(locale="zh_CN", mode="default"),
                ).text
                self.assertIn("极地大乱斗", rendered)
                self.assertIn("超越死亡的效劳", rendered)
                self.assertIn("烈娜塔 · 戈拉斯克", rendered)
                self.assertIn("W技能", rendered)
                self.assertIn(qualifier_label, rendered)
                self.assertNotRegex(rendered, r"ARAM|ServeBeyondDeath|RenataW|状态启动")
                if stopped:
                    self.assertNotIn("释放", rendered)
                    self.assertIn("停止", rendered)
                else:
                    self.assertIn("释放", rendered)

    def test_non_target_family_is_ignored(self) -> None:
        """reference_entity 不属三家族的记录不进本 builder。"""

        record = enriched_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate")
        self.assertIsNone(build_mode_augment_event(record, TranslationBuildContext(), labels=INJECTED_LABELS))


class ModeAugmentPhaseSynthesisTests(unittest.TestCase):
    """验证无相位 sfx 的相位合成（cue 尾段相位词 / 整段音效）。"""

    def test_no_phase_without_tail_synthesizes_overall(self) -> None:
        """无相位且尾段非相位词：合成 key=overall 的整段音效相位。"""

        record = enriched_record("Play_sfx_Augment_ARAM_MadeUpAug_grab")
        self.assertIsNone(record.semantic_slots.get("phase"))
        translation = build_mode_augment_event(record, TranslationBuildContext(), labels=INJECTED_LABELS)

        self.assertIsNotNone(translation)
        assert translation is not None
        phase = translation["arguments"]["phase"]
        self.assertEqual(phase["key"], "overall")
        self.assertEqual(phase["provenance"]["synthesized"], "sfx_no_phase")
        self.assertEqual(phase["provenance"]["frame_type"], "mode_augment_event")

    def test_no_phase_with_tail_phase_word_uses_it(self) -> None:
        """无相位但尾段是相位词（loop 等）：直接用该相位词。"""

        record = enriched_record("Play_sfx_Augment_ARAM_MadeUpAug_loop")
        self.assertIsNone(record.semantic_slots.get("phase"))
        translation = build_mode_augment_event(record, TranslationBuildContext(), labels=INJECTED_LABELS)

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["arguments"]["phase"]["key"], "loop")


class ModeAugmentRegistryAndRenderTests(unittest.TestCase):
    """验证 FRAME_REGISTRY 契约与渲染冒烟（validation/renderer 走注册表）。"""

    def test_frame_registry_contract(self) -> None:
        """注册表条目：required_roles 与 zh 模板按设计声明。"""

        spec = FRAME_REGISTRY["mode_augment_event"]
        self.assertEqual(spec.required_roles, ("augment",))
        self.assertEqual(
            spec.templates["zh_CN"]["default"],
            ("mode", "augment", "champion", "ability_slot", "qualifier", "phase"),
        )
        self.assertEqual(
            spec.templates["zh_CN"]["main"],
            ("augment", "champion", "ability_slot", "qualifier", "phase"),
        )

    def test_render_smoke_zh_default_and_main(self) -> None:
        """zh default/main 渲染非空且不含原始 Code token（注入词表）。"""

        record = enriched_record("Play_sfx_Cherry_Augment_Goredrink_buffactivate")
        translation = build_mode_augment_event(record, TranslationBuildContext(), labels=INJECTED_LABELS)
        self.assertIsNotNone(translation)
        assert translation is not None

        default = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )
        for result in (default, main):
            self.assertTrue(result.text)
            self.assertNotIn("Goredrink", result.text)
            self.assertNotIn("_", result.text)
        self.assertIn("augment", default.used_roles)
        self.assertIn("augment", main.used_roles)


class DisplayLabelsAugmentsSectionTests(unittest.TestCase):
    """验证 display_labels 的 augments 分节解析。"""

    def test_injected_dict_parses_and_skips_malformed(self) -> None:
        """注入 dict：合法词条入表，畸形词条跳过不炸。"""

        labels = load_display_labels(
            {
                "augments": {
                    "Goldrend": {"zh_CN": "夺金", "en_US": "Goldrend"},
                    "BadScalar": "不是映射",
                    "MissingEnglish": {"zh_CN": "只有中文"},
                    "WrongType": {"zh_CN": 1, "en_US": 2},
                }
            }
        )
        self.assertEqual(labels.augments, {"Goldrend": ("夺金", "Goldrend")})

    def test_section_absent_defaults_to_empty(self) -> None:
        """无 augments 分节时解析为空映射，不影响其他分节。"""

        labels = load_display_labels({})
        self.assertEqual(labels.augments, {})

    def test_repository_vocab_has_wellformed_entries(self) -> None:
        """仓库词表 augments 分节非空且值为双语字符串对（不锁具体词条）。"""

        labels = load_display_labels()
        self.assertTrue(labels.augments)
        for code, pair in labels.augments.items():
            self.assertIsInstance(code, str)
            self.assertEqual(len(pair), 2)
            self.assertTrue(all(isinstance(value, str) and value for value in pair))


if __name__ == "__main__":
    unittest.main()
