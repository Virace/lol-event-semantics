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
                    "gameplay_event": arg("gameplay_event", "第一滴血"),
                    "phase": arg("phase", "阶段选择"),
                },
                "Map12 第一滴血阶段选择",
                "第一滴血阶段选择",
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


def counterpart_arg(label: str, *, fallback: bool = False) -> dict[str, object]:
    """构造 counterpart argument。"""

    argument: dict[str, object] = {
        "role": "counterpart",
        "labels": {"zh_CN": label, "en_US": label},
        "confidence": "low" if fallback else "high",
        "provenance": {"kind": "test"},
        "fallback": fallback,
    }
    if fallback:
        argument["fallback_reason"] = "missing_counterpart_zh_label"
    return argument


class CounterpartFrameRendererTests(unittest.TestCase):
    """验证两个 counterpart frame 的中文定制 join。"""

    def render_pair(self, translation: dict[str, object]) -> tuple[str, str]:
        """返回 (default, main) 中文文案。"""

        default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )
        return default_result.text, main_result.text

    def test_kill_with_counterpart_uses_infix_wording(self) -> None:
        """kill + cp 按「击杀 {cp} 语音」中缀。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "亚托克斯"),
                "skin": arg("skin", "仲裁圣骑 亚托克斯"),
                "trigger": arg("trigger", "击杀语音"),
                "counterpart": counterpart_arg("奥恩"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "亚托克斯 仲裁圣骑 击杀 奥恩 语音")
        self.assertEqual(main_text, "击杀 奥恩 语音")

    def test_milestone_with_counterpart_uses_postfix_bracket(self) -> None:
        """milestone 存在时 cp 后置：「五杀语音（{cp}）」。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "阿狸"),
                "trigger": arg("trigger", "五杀语音"),
                "milestone": arg("milestone", "五杀语音"),
                "counterpart": counterpart_arg("奥恩"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "阿狸 五杀语音（奥恩）")
        self.assertEqual(main_text, "五杀语音（奥恩）")

    def test_move_bracket_wording_with_relation(self) -> None:
        """move 家族按全角括号措辞拼 relation 与 cp。"""

        translation = frame_translation(
            "champion_interaction_cue",
            {
                "champion": arg("champion", "莫甘娜"),
                "skin": arg("skin", "灵魂莲华 莫甘娜"),
                "trigger": {**arg("trigger", "首次移动语音"), "key": "move"},
                "relation": arg("relation", "队友"),
                "counterpart": counterpart_arg("灵魂莲华 凯尔"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "莫甘娜 灵魂莲华 首次移动语音（队友 灵魂莲华 凯尔 在场）")
        self.assertEqual(main_text, "首次移动语音（队友 灵魂莲华 凯尔 在场）")

    def test_move_bracket_without_relation_keeps_counterpart_only(self) -> None:
        """move 无 relation 时括号内只有 cp（保留在场语义）。"""

        translation = frame_translation(
            "champion_interaction_cue",
            {
                "champion": arg("champion", "亚托克斯"),
                "trigger": {**arg("trigger", "首次移动语音"), "key": "move"},
                "counterpart": counterpart_arg("潘森"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "亚托克斯 首次移动语音（潘森 在场）")
        self.assertEqual(main_text, "首次移动语音（潘森 在场）")

    def test_fallback_english_counterpart_stays_out_of_main_text(self) -> None:
        """英文 fallback cp 进 default 详细文案，不进 main 短文案（棘轮口径）。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "卡莎"),
                "trigger": arg("trigger", "击杀语音"),
                "counterpart": counterpart_arg("Fifteen", fallback=True),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "卡莎 击杀 Fifteen 语音")
        self.assertEqual(main_text, "击杀语音")

    def test_english_kill_method_prefix_stays_out_of_main_text(self) -> None:
        """归一失败的 kill_method raw 前缀只进 default 详细文案。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "易"),
                "trigger": arg("trigger", "击杀语音"),
                "kill_method": arg("kill_method", "RActive"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "易 RActive 击杀语音")
        self.assertEqual(main_text, "击杀语音")

    def test_spell_key_kill_method_prefix_stays_out_of_main_text(self) -> None:
        """QWER 归一键紧贴前缀「R技能击杀语音」，同样只进 default 详细文案。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "提莫"),
                "trigger": arg("trigger", "击杀语音"),
                "kill_method": arg("kill_method", "R技能"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "提莫 R技能击杀语音")
        self.assertEqual(main_text, "击杀语音")

    def test_latin_mixed_official_counterpart_stays_out_of_main_text(self) -> None:
        """含拉丁字母的官方混排 cp 名（DJ 娑娜）只进 default 详细文案。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "阿狸"),
                "trigger": arg("trigger", "击杀语音"),
                "counterpart": counterpart_arg("DJ 娑娜"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "阿狸 击杀 DJ 娑娜 语音")
        self.assertEqual(main_text, "击杀语音")

    def test_playback_appends_to_counterpart_frames(self) -> None:
        """定制 join 不得丢 playback 壳语义。"""

        translation = frame_translation(
            "kill_or_destroy_event",
            {
                "champion": arg("champion", "厄运小姐"),
                "trigger": arg("trigger", "击杀音效"),
                "playback": arg("playback", "停止"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "厄运小姐 击杀音效停止")
        self.assertEqual(main_text, "击杀音效停止")

    def test_first_encounter_without_counterpart_keeps_base_phrase(self) -> None:
        """无 cp 的初遇按「初遇语音」直出。"""

        translation = frame_translation(
            "champion_interaction_cue",
            {
                "champion": arg("champion", "亚托克斯"),
                "trigger": {**arg("trigger", "初遇语音"), "key": "first_encounter"},
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "亚托克斯 初遇语音")
        self.assertEqual(main_text, "初遇语音")


def partner_arg(label: str, *, fallback: bool = False) -> dict[str, object]:
    """构造 dialogue partner argument。"""

    argument: dict[str, object] = {
        "role": "partner",
        "labels": {"zh_CN": label, "en_US": label},
        "confidence": "low" if fallback else "high",
        "provenance": {"kind": "test"},
        "fallback": fallback,
    }
    if fallback:
        argument["fallback_reason"] = "missing_partner_zh_label"
    return argument


class ChampionDialogueEventRendererTests(unittest.TestCase):
    """验证批次4 champion_dialogue_event 的中文定制 join。"""

    def render_pair(self, translation: dict[str, object]) -> tuple[str, str]:
        """返回 (default, main) 中文文案。"""

        default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )
        return default_result.text, main_result.text

    def test_partner_with_family_and_turn_uses_full_bracket(self) -> None:
        """核心「与 {partner} 对话语音」+ 全角括号「（{family词} 第{turn}句）」。"""

        translation = frame_translation(
            "champion_dialogue_event",
            {
                "champion": arg("champion", "凯尔"),
                "skin": arg("skin", "食日天使 凯尔"),
                "partner": partner_arg("莫甘娜"),
                "base_trigger": arg("base_trigger", "移动"),
                "turn": arg("turn", "第2句"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "凯尔 食日天使 与 莫甘娜 对话语音（移动 第2句）")
        self.assertEqual(main_text, "与 莫甘娜 对话语音（移动 第2句）")

    def test_partner_without_turn_keeps_family_only_bracket(self) -> None:
        """无 turn 时括号只保留 base family 词。"""

        translation = frame_translation(
            "champion_dialogue_event",
            {
                "champion": arg("champion", "凯尔"),
                "skin": arg("skin", "食日天使 凯尔"),
                "partner": partner_arg("莫甘娜"),
                "base_trigger": arg("base_trigger", "移动"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "凯尔 食日天使 与 莫甘娜 对话语音（移动）")
        self.assertEqual(main_text, "与 莫甘娜 对话语音（移动）")

    def test_partner_without_family_and_turn_has_no_bracket(self) -> None:
        """family 无词条且无 turn 时无括号；playback 壳语义不得丢。"""

        translation = frame_translation(
            "champion_dialogue_event",
            {
                "champion": arg("champion", "凯尔"),
                "partner": partner_arg("莫甘娜"),
                "playback": arg("playback", "停止"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "凯尔 与 莫甘娜 对话语音停止")
        self.assertEqual(main_text, "与 莫甘娜 对话语音停止")

    def test_fallback_english_partner_stays_out_of_main_text(self) -> None:
        """英文 fallback partner 进 default 详细文案，main 退化「对话语音」（棘轮口径）。"""

        translation = frame_translation(
            "champion_dialogue_event",
            {
                "champion": arg("champion", "霞"),
                "skin": arg("skin", "星之守护者 霞"),
                "partner": partner_arg("RakanNear", fallback=True),
                "base_trigger": arg("base_trigger", "攻击"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "霞 星之守护者 与 RakanNear 对话语音（攻击）")
        self.assertEqual(main_text, "对话语音（攻击）")


class AnnouncerEventRendererTests(unittest.TestCase):
    """验证批次5a announcer_event 的中文定制 join。"""

    def render_pair(self, translation: dict[str, object]) -> tuple[str, str]:
        """返回 (default, main) 中文文案。"""

        default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )
        return default_result.text, main_result.text

    def test_team_and_event_join_without_space(self) -> None:
        """核心「{team}{event}播报」直连无空格，default/main 一致。"""

        translation = frame_translation(
            "announcer_event",
            {
                "team": arg("team", "敌方"),
                "event": arg("event", "团灭"),
            },
        )

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "敌方团灭播报")
        self.assertEqual(main_text, "敌方团灭播报")

    def test_event_without_team_keeps_bare_core(self) -> None:
        """team 缺省为空串直连（「胜利播报」）。"""

        translation = frame_translation("announcer_event", {"event": arg("event", "胜利")})

        default_text, main_text = self.render_pair(translation)
        self.assertEqual(default_text, "胜利播报")
        self.assertEqual(main_text, "胜利播报")

    def test_playback_appends_to_announcer_frame(self) -> None:
        """playback 壳语义不得丢，追加在核心之后。"""

        translation = frame_translation(
            "announcer_event",
            {
                "team": arg("team", "紫方"),
                "event": arg("event", "五杀"),
                "playback": arg("playback", "停止"),
            },
        )

        default_text, _main_text = self.render_pair(translation)
        self.assertEqual(default_text, "紫方五杀播报停止")

    def test_english_template_joins_event_then_team(self) -> None:
        """en_US 用既有空格 join（event 前 team 后）。"""

        translation = frame_translation(
            "announcer_event",
            {
                "team": {
                    "role": "team",
                    "labels": {"zh_CN": "敌方", "en_US": "EnemyTeam"},
                    "confidence": "high",
                    "provenance": {"kind": "test"},
                },
                "event": {
                    "role": "event",
                    "labels": {"zh_CN": "团灭", "en_US": "Ace"},
                    "confidence": "high",
                    "provenance": {"kind": "test"},
                },
            },
        )

        result = render_translation(translation, RenderOptions(locale="en_US", mode="default"))
        self.assertEqual(result.text, "Ace EnemyTeam")


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

    def test_keeps_champion_name_in_middle_segment(self) -> None:
        """英雄名出现在皮肤名中间段时不剥离，只处理末尾重复。"""

        self.assertEqual(
            strip_repeated_champion_from_skin("暗焰 布兰德 典狱长", "布兰德"),
            "暗焰 布兰德 典狱长",
        )

    def test_keeps_multi_segment_label_equal_to_champion_name(self) -> None:
        """多段皮肤名整体等于英雄名时不剥成空串（间隔号整名写法）。"""

        self.assertEqual(
            strip_repeated_champion_from_skin("奥瑞利安 · 索尔", "奥瑞利安·索尔"),
            "奥瑞利安 · 索尔",
        )


class SkinlessActionStateRendererTests(unittest.TestCase):
    """验证批次5b 基础皮肤形态（无 skin 角色）的 skin_action_state 渲染。"""

    def test_skin_action_state_without_skin_renders_both_modes(self) -> None:
        """缺 skin argument 时 default 仍出「{champion} {action}{phase}」，main 不受影响。"""

        translation = frame_translation(
            "skin_action_state",
            {
                "champion": arg("champion", "加里奥"),
                "action": arg("action", "死亡"),
                "phase": arg("phase", "状态启动"),
            },
        )

        default_result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        main_result = render_translation(
            translation,
            RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})),
        )

        self.assertEqual(default_result.text, "加里奥 死亡状态启动")
        self.assertEqual(main_result.text, "死亡状态启动")


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
