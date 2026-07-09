"""normalized translation renderer 测试。"""

from __future__ import annotations

import copy
import unittest

from lol_event_semantics.normalized_translation_model import RenderOptions
from lol_event_semantics.normalized_translation_renderer import (
    build_compat_display,
    render_translation,
    strip_repeated_champion_from_skin,
)


def galio_translation() -> dict[str, object]:
    """返回 Galio W 被动防御 translation fixture。"""

    return {
        "schema_version": 1,
        "frame_type": "ability_state_change",
        "frame_version": 1,
        "frame_confidence": "medium",
        "raw_event_name": "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate",
        "arguments": {
            "champion": {
                "role": "champion",
                "alias": "Galio",
                "labels": {"zh_CN": "加里奥", "en_US": "Galio"},
                "confidence": "high",
                "provenance": {"kind": "reference", "path": "reference/champions/Galio.yaml"},
                "omit_when_context_known": ["champion"],
            },
            "skin": {
                "role": "skin",
                "token": "Skin13",
                "labels": {"zh_CN": "Skin13", "en_US": "Skin13"},
                "confidence": "low",
                "provenance": {"kind": "fallback", "path": "raw_event_name"},
                "fallback": True,
                "fallback_reason": "missing_skin_label",
                "omit_when_context_known": ["skin"],
            },
            "ability_slot": {
                "role": "ability_slot",
                "value": "w",
                "labels": {"zh_CN": "W技能", "en_US": "W"},
                "confidence": "high",
                "provenance": {"kind": "event_token", "path": "raw_event_name"},
            },
            "ability_component": {
                "role": "ability_component",
                "key": "passive_defense",
                "labels": {"zh_CN": "被动防御", "en_US": "Passive Defense"},
                "confidence": "medium",
                "provenance": {"kind": "rule", "path": "rules/contextual/champions/Galio.yaml"},
            },
            "phase": {
                "role": "phase",
                "key": "state_deactivate",
                "labels": {"zh_CN": "状态结束", "en_US": "state ended"},
                "confidence": "high",
                "provenance": {"kind": "phase", "path": "rules/phases.yaml"},
            },
        },
        "diagnostics": [],
    }


def frame_translation(frame_type: str, arguments: dict[str, dict[str, object]]) -> dict[str, object]:
    """构造 renderer 用 translation frame。"""

    return {
        "schema_version": 1,
        "frame_type": frame_type,
        "frame_version": 1,
        "frame_confidence": "medium",
        "arguments": arguments,
    }


def arg(role: str, label: str) -> dict[str, object]:
    """构造 renderer argument。"""

    return {
        "role": role,
        "labels": {"zh_CN": label},
        "confidence": "high",
        "provenance": {"kind": "test"},
    }


class NormalizedTranslationRendererTests(unittest.TestCase):
    """验证 translation renderer。"""

    def test_renders_default_chinese_text(self) -> None:
        """默认中文包含上下文和核心动作。"""

        result = render_translation(galio_translation(), RenderOptions(locale="zh_CN", mode="default"))

        self.assertEqual(result.text, "加里奥 Skin13 W技能被动防御状态结束")
        self.assertEqual(result.used_roles, ("champion", "skin", "ability_slot", "ability_component", "phase"))

    def test_renders_main_text_without_known_context(self) -> None:
        """main 模式只保留核心动作。"""

        result = render_translation(
            galio_translation(),
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )

        self.assertEqual(result.text, "W技能被动防御状态结束")
        self.assertEqual(result.omitted_roles, ("champion", "skin"))

    def test_renderer_does_not_mutate_input(self) -> None:
        """渲染不能修改输入 dict。"""

        payload = galio_translation()
        before = copy.deepcopy(payload)

        render_translation(payload, RenderOptions(locale="zh_CN", mode="default"))

        self.assertEqual(payload, before)

    def test_fallback_argument_reports_diagnostic(self) -> None:
        """fallback label 被使用时输出诊断。"""

        result = render_translation(galio_translation(), RenderOptions(locale="zh_CN", mode="default"))

        self.assertIn("translation_label_fallback:skin", result.diagnostics)

    def test_user_text_does_not_include_technical_tokens(self) -> None:
        """普通用户文案不暴露 raw 技术 token。"""

        result = render_translation(galio_translation(), RenderOptions(locale="zh_CN", mode="default"))

        for token in ["Play_", "OnBuffDeactivate", "buffactivate", "3D", "2D"]:
            self.assertNotIn(token, result.text)

    def test_build_compat_display_uses_renderer_modes(self) -> None:
        """兼容 display 由 renderer 派生。"""

        display = build_compat_display(
            galio_translation(),
            raw_event_name="Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate",
        )

        self.assertEqual(display["zh_cn_short"], "W技能被动防御状态结束")
        self.assertEqual(display["zh_cn_detail"], "加里奥 Skin13 W技能被动防御状态结束")
        self.assertIn("debug_label", display)

    def test_renders_supported_non_ability_frames(self) -> None:
        """已支持 frame 都有中文 default/main 模板。"""

        cases = [
            (
                "skin_recall_state",
                {
                    "champion": arg("champion", "拉克丝"),
                    "skin": arg("skin", "星之守护者 拉克丝"),
                    "action": arg("action", "回城"),
                    "phase": arg("phase", "状态启动"),
                },
                "拉克丝 星之守护者 回城状态启动",
                "回城状态启动",
            ),
            (
                "skin_idle_state",
                {
                    "champion": arg("champion", "金克丝"),
                    "skin": arg("skin", "双城之战2 破碎者 金克丝"),
                    "ability_slot": arg("ability_slot", "R技能"),
                    "phase": arg("phase", "进入"),
                },
                "金克丝 双城之战2 破碎者 R技能待机进入",
                "R技能待机进入",
            ),
            (
                "skin_action_state",
                {
                    "champion": arg("champion", "瑟提"),
                    "skin": arg("skin", "机神苍狼 瑟提"),
                    "action": arg("action", "嘲讽"),
                    "phase": arg("phase", "状态启动"),
                },
                "瑟提 机神苍狼 嘲讽状态启动",
                "嘲讽状态启动",
            ),
            (
                "gameplay_phase_control",
                {
                    "map": arg("map", "Map12"),
                    "gameplay_event": arg("gameplay_event", "一血"),
                    "phase": arg("phase", "阶段选择"),
                },
                "Map12 一血阶段选择",
                "一血阶段选择",
            ),
            (
                "voice_event",
                {
                    "champion": arg("champion", "Ashe"),
                    "skin": arg("skin", "Skin06"),
                    "action": arg("action", "攻击语音"),
                },
                "Ashe Skin06 攻击语音",
                "攻击语音",
            ),
            (
                "ability_cast_event",
                {
                    "champion": arg("champion", "阿卡丽"),
                    "skin": arg("skin", "Skin02"),
                    "ability_slot": arg("ability_slot", "R技能"),
                    "phase": arg("phase", "释放"),
                },
                "阿卡丽 Skin02 R技能释放",
                "R技能释放",
            ),
        ]

        for frame_type, arguments, default_text, main_text in cases:
            with self.subTest(frame_type=frame_type):
                translation = frame_translation(frame_type, arguments)

                default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
                main_result = render_translation(
                    translation,
                    RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
                )

                self.assertEqual(default_result.text, default_text)
                self.assertEqual(main_result.text, main_text)
                self.assertNotIn("missing_render_template", default_result.diagnostics)


class StripRepeatedChampionFromSkinTests(unittest.TestCase):
    """验证皮肤名去重英雄名的整名匹配边界。"""

    def test_strips_exact_champion_suffix(self) -> None:
        """官方皮肤名末尾的英雄名整名去重。"""

        self.assertEqual(strip_repeated_champion_from_skin("地狱火 加里奥", "加里奥"), "地狱火")
        self.assertEqual(
            strip_repeated_champion_from_skin("双城之战2 破碎者 金克丝", "金克丝"),
            "双城之战2 破碎者",
        )

    def test_strips_variant_champion_suffix(self) -> None:
        """皮肤名末段内嵌英雄名变体（魔龙布兰德≠布兰德）时去掉整个末段。"""

        self.assertEqual(strip_repeated_champion_from_skin("神龙尊者 魔龙布兰德", "布兰德"), "神龙尊者")

    def test_strips_interpunct_champion_suffix(self) -> None:
        """空格间隔号写法的英雄名（奥瑞利安 · 索尔）按整名去重。"""

        self.assertEqual(
            strip_repeated_champion_from_skin("墨影之灵 奥瑞利安 · 索尔", "奥瑞利安·索尔"),
            "墨影之灵",
        )

    def test_does_not_split_interpunct_champion_name(self) -> None:
        """英雄名子串（索尔）不得把间隔号整名切断。"""

        self.assertEqual(
            strip_repeated_champion_from_skin("墨影之灵 奥瑞利安 · 索尔", "索尔"),
            "墨影之灵 奥瑞利安 · 索尔",
        )

    def test_keeps_single_segment_skin_label(self) -> None:
        """皮肤名只有一段时不去重，避免剥空。"""

        self.assertEqual(strip_repeated_champion_from_skin("魔龙布兰德", "布兰德"), "魔龙布兰德")
        self.assertEqual(strip_repeated_champion_from_skin("加里奥", "加里奥"), "加里奥")


class NormalizedTranslationRendererPlaybackTests(unittest.TestCase):
    """验证 playback（停止/静音）壳语义参与渲染，不被静默丢弃。"""

    def test_playback_role_appends_to_default_and_main(self) -> None:
        """voice_event 带 playback 时两档文案都以停止收尾。"""

        translation = frame_translation(
            "voice_event",
            {
                "champion": arg("champion", "奥萝拉"),
                "skin": arg("skin", "巫灵 奥萝拉"),
                "action": arg("action", "跳舞音效"),
                "playback": arg("playback", "停止"),
            },
        )

        default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )

        self.assertEqual(default_result.text, "奥萝拉 巫灵 跳舞音效停止")
        self.assertEqual(main_result.text, "跳舞音效停止")

    def test_playback_role_appends_to_skin_idle_frame(self) -> None:
        """skin_idle_state 的专用合并逻辑也不能丢 playback。"""

        translation = frame_translation(
            "skin_idle_state",
            {
                "champion": arg("champion", "金克丝"),
                "skin": arg("skin", "双城之战2 破碎者 金克丝"),
                "ability_slot": arg("ability_slot", "R技能"),
                "phase": arg("phase", "进入"),
                "playback": arg("playback", "停止"),
            },
        )

        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )

        self.assertEqual(main_result.text, "R技能待机进入停止")


if __name__ == "__main__":
    unittest.main()
