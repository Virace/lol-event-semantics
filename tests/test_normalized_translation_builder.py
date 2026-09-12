"""normalized translation builder 测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from reference_cache import reference_index
from reference_cache import translation_context as reference_translation_context

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import enrich_with_reference
from lol_event_semantics.display_labels import DisplayLabels
from lol_event_semantics.normalized_translation_builder import (
    ChampionLabelEntry,
    build_translation,
    gameplay_event_argument,
    map_argument,
    phase_argument,
)
from lol_event_semantics.normalized_translation_model import TranslationBuildContext
from lol_event_semantics.normalized_translation_renderer import build_compat_display

FIXTURE = Path(__file__).parent / "fixtures" / "normalized_translation_cases.json"


class NormalizedTranslationBuilderGalioTests(unittest.TestCase):
    """验证 Galio W 被动防御 frame。"""

    def test_galio_w_passive_defense_builds_ability_state_change(self) -> None:
        """Galio legacy owner token 输出多角色 translation frame。"""

        record = enriched_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate")
        translation = build_translation(
            record,
            TranslationBuildContext(
                champion_labels={
                    "Galio": {
                        "en_US": "Galio",
                        "zh_CN": "加里奥",
                    }
                },
                skin_labels={
                    ("Galio", "Skin13"): {
                        "en_US": "Infernal Galio",
                        "zh_CN": "地狱火 加里奥",
                    }
                },
            ),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["schema_version"], 1)
        self.assertEqual(translation["frame_type"], "ability_state_change")
        self.assertEqual(translation["frame_version"], 1)
        self.assertEqual(translation["frame_confidence"], "medium")

        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Galio")
        self.assertEqual(args["champion"]["labels"]["zh_CN"], "加里奥")
        self.assertEqual(args["skin"]["token"], "Skin13")
        self.assertEqual(args["skin"]["labels"]["zh_CN"], "地狱火 加里奥")
        self.assertFalse(args["skin"]["fallback"])
        self.assertEqual(args["ability_slot"]["value"], "w")
        self.assertEqual(args["ability_component"]["key"], "passive_defense")
        self.assertEqual(args["phase"]["key"], "effect_deactivate")


class NormalizedTranslationBuilderHeimerTests(unittest.TestCase):
    """验证 Heimer R 强化 Q 炮台组合语义。"""

    def test_rq_engine_audio_builds_empowered_q_turret_component(self) -> None:
        """精确 RQ cue 同时输出 R 槽位与强化 Q 炮台组件。"""

        translation = build_translation(
            enriched_record("Play_sfx_HeimerTBlue_HeimerdingerRQEngineAudio_OnBuffActivate"),
            reference_translation_context(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_state_change")
        args = translation["arguments"]
        self.assertEqual(args["ability_slot"]["key"], "r")
        self.assertEqual(args["ability_component"]["key"], "empowered_q_turret")
        self.assertEqual(args["ability_component"]["labels"]["zh_CN"], "强化Q炮台")
        self.assertEqual(args["phase"]["key"], "effect_activate")


class NormalizedTranslationBuilderChampionSfxEffectTests(unittest.TestCase):
    """验证已审阅公共 SFX 效果不会在翻译层丢失。"""

    def test_curated_champion_sfx_alias_builds_effect_frame(self) -> None:
        """Spawn/Run/Channel 应输出具体音效，而不是退化成“状态启动”。"""

        cases = [
            ("Play_sfx_ApheliosSkin01_Spawn3D_buffactivate", "spawn", "出生/生成"),
            ("Play_sfx_YuumiSkin01_Run3D_buffactivate_slow", "run", "奔跑"),
            ("Play_sfx_ZedSkin13_Channel_buffactivate", "channel", "引导"),
        ]

        for raw, expected_key, expected_label in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], expected_key)
                self.assertEqual(
                    translation["arguments"]["effect"]["labels"]["zh_CN"],
                    f"{expected_label}音效",
                )
                self.assertEqual(translation["arguments"]["phase"]["key"], "audio_start")

    def test_curated_champion_sfx_alias_renders_meaningful_chinese(self) -> None:
        """专用效果 frame 的短文案必须包含已审阅中文效果词。"""

        from lol_event_semantics.normalized_output import build_normalized_record

        normalized = build_normalized_record(
            enriched_record("Play_sfx_ApheliosSkin01_Spawn3D_buffactivate"),
            translation_context=reference_translation_context(),
        )

        self.assertEqual(normalized["display"]["tier"], 1)
        self.assertEqual(normalized["display"]["zh_cn_short"], "出生/生成音效开始")
        self.assertIn("厄斐琉斯", normalized["display"]["zh_cn_detail"])
        self.assertIn("出生/生成音效开始", normalized["display"]["zh_cn_detail"])
        self.assertNotIn("状态启动", normalized["display"]["zh_cn_detail"])

    def test_mode_sfx_buff_lifecycle_is_audio_not_ability_state(self) -> None:
        """模式特效的 buff 生命周期不得把 effect 别名误装成技能组件。"""

        from lol_event_semantics.normalized_output import build_normalized_record

        cases = [
            (
                "Play_sfx_Strawberry_Boss_Aatrox_ChainPlayer_buffactivate",
                "audio_start",
                "PVE 模式 锁链牵引音效开始",
            ),
            (
                "Play_sfx_Strawberry_Boss_Aatrox_ChainPlayer_buffdeactivate",
                "audio_end",
                "PVE 模式 锁链牵引音效结束",
            ),
        ]
        for raw, expected_phase, expected_short in cases:
            with self.subTest(raw=raw):
                normalized = build_normalized_record(
                    enriched_record(raw),
                    translation_context=reference_translation_context(),
                )

                translation = normalized["translation"]
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], "chain_player")
                self.assertEqual(translation["arguments"]["phase"]["key"], expected_phase)
                self.assertNotIn("ability_component", translation["arguments"])
                self.assertEqual(normalized["display"]["zh_cn_short"], expected_short)
                self.assertNotRegex(
                    normalized["display"]["zh_cn_detail"],
                    r"状态启动|状态结束|尚无中文短标签",
                )

    def test_map_sfx_alias_does_not_build_champion_effect_frame(self) -> None:
        """地图单位的 SFX 别名不得套用英雄专用效果 frame。"""

        translation = build_translation(
            enriched_record("Play_sfx_SRU_ChaosMinionRanged_OnBuffActivate"),
            reference_translation_context(),
        )

        self.assertTrue(translation is None or translation["frame_type"] != "champion_sfx_effect_event")

    def test_champion_spell_component_takes_priority_over_generic_sfx_alias(self) -> None:
        """英雄专属技能名应进入 ability component，不降级成公共 SFX 效果。"""

        cases = [
            (
                "Play_sfx_BlitzcrankSkin07_Overdrive_OnBuffActivate",
                "w",
                "overdrive",
                "过载运转",
                "manual_sfx_alias",
            ),
            (
                "Play_sfx_Gnar_GnarTransform_OnBuffActivate",
                "p",
                "rage_gene_transform",
                "狂怒基因变身",
                "manual_contextual_alias",
            ),
        ]

        for raw, spell_key, component_key, expected_label, expected_source in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "ability_state_change")
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], spell_key)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component_key)
                self.assertEqual(
                    translation["arguments"]["ability_component"]["labels"]["zh_CN"],
                    expected_label,
                )
                self.assertEqual(
                    translation["arguments"]["ability_component"]["provenance"]["source"],
                    expected_source,
                )

    def test_sfx_specific_spell_component_without_phase_keeps_component_frame(self) -> None:
        """SFX 专属技能组件即使没有标准相位，也不能退化成空翻译。"""

        translation = build_translation(
            enriched_record("Play_sfx_BlitzcrankSkin07_Overdrive"),
            reference_translation_context(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_ability_component_event")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "w")
        self.assertEqual(translation["arguments"]["ability_component"]["key"], "overdrive")
        self.assertEqual(
            translation["arguments"]["ability_component"]["labels"]["zh_CN"],
            "过载运转",
        )

    def test_spell_component_revival_phase_is_preserved(self) -> None:
        """RRevive 已解析出的复活阶段必须进入 component frame 与展示。"""

        translation = build_translation(
            enriched_record("Play_sfx_AatroxSkin07_AatroxRRevive_player"),
            reference_translation_context(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_ability_component_event")
        self.assertEqual(translation["arguments"]["ability_component"]["key"], "world_ender_revival")
        self.assertEqual(translation["arguments"]["phase"]["key"], "revive")
        display = build_compat_display(translation)
        self.assertIn(translation["arguments"]["phase"]["labels"]["zh_CN"], display["zh_cn_short"])

    def test_spell_component_cast_is_not_misrouted_as_state_change(self) -> None:
        """技能组件的 cast 相位仍是施放事件，不能仅因组件存在变成状态变化。"""

        cases = [
            (
                "Play_sfx_FizzSkin04_FizzPiercingStrike_OnCast",
                "q",
                "urchin_strike",
                "淘气打击",
            ),
        ]

        for raw, spell_key, component_key, expected_label in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())

                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "ability_cast_event")
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], spell_key)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component_key)
                self.assertEqual(
                    translation["arguments"]["ability_component"]["labels"]["zh_CN"],
                    expected_label,
                )
                self.assertEqual(translation["arguments"]["phase"]["key"], "cast")

    def test_sfx_specific_alias_does_not_change_vo_stable_pattern(self) -> None:
        """SFX 专名规则不得给同名 VO 记录新增组件角色。"""

        translation = build_translation(
            enriched_record("Play_vo_BlitzcrankSkin29_Overdrive_cast3D"),
            reference_translation_context(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "w")
        self.assertNotIn("ability_component", translation["arguments"])

    def test_legacy_iblitz_sfx_uses_base_champion_and_official_component(self) -> None:
        """iBlitz SFX 应恢复布里茨实体、W 槽位与过载运转专名。"""

        from lol_event_semantics.normalized_output import build_normalized_record

        normalized = build_normalized_record(
            enriched_record("Play_sfx_iBlitz_Overdrive_OnBuffActivate"),
            translation_context=reference_translation_context(),
        )

        self.assertEqual(normalized["entity"]["alias"], "Blitzcrank")
        self.assertEqual(normalized["entity"]["attribution"]["basis"], "reference_exact")
        self.assertEqual(normalized["display"]["zh_cn_short"], "W技能过载运转生效")
        self.assertIn("布里茨", normalized["display"]["zh_cn_detail"])
        self.assertNotIn("iBlitz", normalized["display"]["zh_cn_detail"])


class NormalizedTranslationBuilderFixtureTests(unittest.TestCase):
    """验证代表样例能构建 translation frame。"""

    def test_representative_cases_build_required_roles(self) -> None:
        """样例中的 supported frame 至少包含 required roles。"""

        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        supported = {
            "ability_state_change",
            "skin_recall_state",
            "skin_idle_state",
            "skin_action_state",
            "gameplay_phase_control",
            "voice_event",
            "ability_cast_event",
            "kill_or_destroy_event",
            "champion_interaction_cue",
            "champion_dialogue_event",
            "announcer_event",
        }
        for case in cases:
            if case["expected_frame_type"] not in supported:
                continue

            with self.subTest(raw=case["raw_event_name"]):
                translation = build_translation(enriched_record(case["raw_event_name"]), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], case["expected_frame_type"])
                args = translation["arguments"]
                for role in case["required_roles"]:
                    if role in {"champion", "skin"}:
                        continue

                    self.assertIn(role, args)


def enriched_record(raw_event_name: str):
    """构建带 reference enrichment 的测试记录。"""

    return enrich_with_reference(
        normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
        reference_index(),
    )


class NormalizedTranslationBuilderAnnieTibbersTests(unittest.TestCase):
    """验证 Annie 提伯斯普攻 OnHit(phase=hit) frame 构建。"""

    def test_tibbers_basic_attack_on_hit_builds_attack_frame(self) -> None:
        """提伯斯普攻可关联 R 召唤物，但用户 frame 仍必须是普通攻击。"""
        record = enriched_record("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack_OnHit")
        frame = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(frame)
        self.assertEqual(frame["frame_type"], "champion_attack_event")
        args = frame["arguments"]
        self.assertIn("phase", args)
        self.assertEqual(args["attack"]["key"], "tibbers_basic_attack")
        self.assertEqual(args["attack"]["labels"]["zh_CN"], "提伯斯普攻")
        self.assertNotIn("ability_slot", args)


class NormalizedTranslationBuilderShellVerbTests(unittest.TestCase):
    """验证 Stop/Mute 壳动词进入 translation frame（不得反转为播放语义）。"""

    def test_stop_event_attaches_playback_argument(self) -> None:
        """Stop_ 事件应附加 key=stop 的 playback argument。"""

        translation = build_translation(
            enriched_record("Stop_sfx_AuroraSkin11_Dance3D_leadin"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertIn("playback", args)
        self.assertEqual(args["playback"]["key"], "stop")

    def test_stop_spell_event_attaches_playback_argument(self) -> None:
        """Stop_ 技能事件同样带 playback，覆盖非 voice frame 路径。"""

        translation = build_translation(
            enriched_record("Stop_sfx_AurelionSolSkin21_AurelionSolQ_charge"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["playback"]["key"], "stop")

    def test_play_event_has_no_playback_argument(self) -> None:
        """普通 Play_ 事件不附加 playback argument。"""

        translation = build_translation(
            enriched_record("Play_vo_AsheSkin06_Attack2DGeneral"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("playback", translation["arguments"])

    def test_mute_verb_maps_to_mute_label(self) -> None:
        """mute 壳动词构建 key=mute 的 playback argument，label 非空。"""

        from lol_event_semantics.normalized_translation_builder import playback_argument

        argument = playback_argument("mute")
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertEqual(argument["key"], "mute")
        self.assertTrue(argument["labels"]["zh_CN"])

    def test_playback_control_suppresses_redundant_activation_phase(self) -> None:
        """Stop/Mute/UnMute 保留 phase 结构，但不得渲染成矛盾文案。"""

        translation = build_translation(
            enriched_record("Stop_sfx_IreliaSkin45_Emote_Joke_buffactivate"),
            reference_translation_context(),
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["arguments"]["phase"]["key"], "audio_start")
        self.assertEqual(translation["arguments"]["playback"]["key"], "stop")
        display = build_compat_display(translation)
        self.assertEqual(display["zh_cn_short"], "玩笑音效停止")
        self.assertNotRegex(display["zh_cn_detail"], r"开始停止|状态启动")


class NormalizedTranslationBuilderVoiceEventTests(unittest.TestCase):
    """验证 voice_event frame。"""

    def test_voice_families_build_voice_event(self) -> None:
        """各 VO 情绪/动作家族构建 voice_event frame，action key 对应家族。"""

        cases = [
            ("Play_vo_AsheSkin06_Attack2DGeneral", "attack"),
            ("Play_vo_AsheSkin06_Taunt2DGeneral", "taunt"),
            ("Play_vo_AsheSkin06_Joke2DGeneral", "joke"),
            ("Play_vo_AsheSkin06_Laugh2DGeneral", "laugh"),
            ("Play_vo_AsheSkin06_Dance2DGeneral", "dance"),
        ]
        for raw, action_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                args = translation["arguments"]
                self.assertEqual(args["champion"]["alias"], "Ashe")
                self.assertEqual(args["skin"]["token"], "Skin06")
                self.assertEqual(args["action"]["key"], action_key)


class NormalizedTranslationBuilderVoiceAssetKindTests(unittest.TestCase):
    """验证 VO/SFX 动作使用各自 frame，并按 asset_kind 选词。"""

    def test_sfx_action_families_use_action_frame_and_preserve_phase(self) -> None:
        """SFX 不是语音；情绪动作必须进入 action frame 并保留生命周期。"""

        cases = [
            ("Stop_sfx_AuroraSkin11_Dance3D_leadin", "leadin"),
            ("Play_sfx_JayceSkin05_Dance3D_buffactivate_loop", "loop"),
        ]
        for raw, phase_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "champion_action_event")
                self.assertEqual(translation["arguments"]["action"]["key"], "dance_sfx")
                self.assertEqual(translation["arguments"]["phase"]["key"], phase_key)

    def test_voice_action_lifecycle_is_a_structured_phase(self) -> None:
        """VO 动作仍使用 voice_event，但 Loop 不能从 frame 和中文层消失。"""

        translation = build_translation(
            enriched_record("Play_vo_Bard_Dance3D_Loop_buffactivate"),
            TranslationBuildContext(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "voice_event")
        self.assertEqual(translation["arguments"]["action"]["key"], "dance")
        self.assertEqual(translation["arguments"]["phase"]["key"], "loop")

    def test_recall_effort_is_a_take_variant_not_a_lifecycle_phase(self) -> None:
        """Effort01 只保留为可审计录音变体，不能伪造“努力声阶段”。"""

        raw = "Play_vo_MalzaharSkin06_Recall_Effort01"
        record = enriched_record(raw)
        translation = build_translation(record, reference_translation_context())

        self.assertEqual(record.semantic_slots["recall_variant"]["kind"], "efforts")
        self.assertEqual(record.semantic_slots["recall_variant"]["index"], "01")
        self.assertNotIn("phase", record.semantic_slots)
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "voice_event")
        self.assertNotIn("phase", translation["arguments"])
        display = build_compat_display(translation, raw_event_name=raw)
        self.assertEqual(display["zh_cn_short"], "回城语音")
        self.assertNotIn("努力声", display["zh_cn_detail"])

    def test_sfx_action_qualifier_remains_structured_and_visible(self) -> None:
        """DanceIn 的 In 不能因 SFX 改用 action frame 而丢失。"""

        translation = build_translation(
            enriched_record("Play_sfx_JaxSkin42_DanceIn3D_buffactivate"),
            TranslationBuildContext(),
        )

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_action_event")
        self.assertEqual(translation["arguments"]["action"]["key"], "dance_in_sfx")
        self.assertIn("进入跳舞", translation["arguments"]["action"]["labels"]["zh_CN"])
        self.assertEqual(translation["arguments"]["qualifier"]["key"], "in")
        display = build_compat_display(
            translation,
            raw_event_name="Play_sfx_JaxSkin42_DanceIn3D_buffactivate",
        )
        self.assertEqual(display["zh_cn_short"], "进入跳舞音效开始")

    def test_reviewed_sfx_action_qualifiers_do_not_fall_back_or_repeat(self) -> None:
        """已分桶的动作段名使用稳定措辞；纯技术后缀不进入用户文案。"""

        cases = [
            ("Play_sfx_Yorick_YorickDanceSfx_OnBuffActivate", None, "舞蹈音效开始"),
            ("Play_sfx_Zac_death3d_cast", None, "死亡音效开始"),
            ("Play_sfx_Nunu_DanceIntro3D_cast", "intro", "舞蹈引入音效开始"),
            ("Play_sfx_Sett_DanceSpam3D_buffactivate", "spam", "连续舞蹈音效开始"),
            ("Play_sfx_Urgot_RecallWindDown_cast", "winddown", "回城收尾音效开始"),
            ("Play_sfx_AurelionSolSkin38_JokeA3D_buffactivate", "a", "玩笑A段音效开始"),
            ("Play_sfx_ClericSoraka_RecallImproved_OnBuffActivate", "improved", "强化回城音效开始"),
        ]

        for raw, qualifier_key, expected in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                arguments = translation["arguments"]
                action = arguments["action"]
                self.assertEqual(action["confidence"], "high")
                self.assertFalse(action["fallback"])
                if qualifier_key is None:
                    self.assertNotIn("qualifier", arguments)
                else:
                    qualifier = arguments["qualifier"]
                    self.assertEqual(qualifier["key"], qualifier_key)
                    self.assertEqual(qualifier["confidence"], "high")
                    self.assertEqual(qualifier["provenance"]["rendered_via"], "action")
                display = build_compat_display(translation, raw_event_name=raw)
                self.assertEqual(display["zh_cn_short"], expected)
                self.assertNotIn("待审", display["zh_cn_short"])

    def test_asset_kind_words_cover_audio_types(self) -> None:
        """vo/sfx/mus 各有非空且互不相同的措辞，未识别类型保守回落 vo 措辞。"""

        from lol_event_semantics.normalized_translation_builder import asset_kind_words

        for kind in ("vo", "sfx", "mus"):
            with self.subTest(kind=kind):
                self.assertTrue(asset_kind_words(kind)[0])
                self.assertTrue(asset_kind_words(kind)[1])
        # 三类资产的选词必须彼此区分（sfx 不得误标语音），但不锁具体词条。
        distinct_zh = {asset_kind_words(kind)[0] for kind in ("vo", "sfx", "mus")}
        self.assertEqual(len(distinct_zh), 3)
        self.assertEqual(asset_kind_words(None), asset_kind_words("vo"))
        self.assertEqual(asset_kind_words("lux"), asset_kind_words("vo"))


class SelfActionVoiceEventTests(unittest.TestCase):
    """验证批次3 self_action 家族接入 voice_event。"""

    def test_families_build_voice_event_with_core_wording(self) -> None:
        """self_action 家族 vo 记录构建 voice_event，action key 对应家族。"""

        cases = [
            ("Play_vo_AatroxSkin33_Move2DStandard", "move"),
            ("Play_vo_AatroxSkin01_Death3D", "death"),
            ("Play_vo_Ezreal_BuyItem2DGeneral", "buy_item"),
            ("Play_vo_AatroxSkin33_Recall3DGeneral", "recall"),
            ("Play_vo_AatroxSkin33_Respawn2DGeneral", "respawn"),
            ("Play_vo_AatroxSkin33_TauntResponse3DGeneral", "taunt_response"),
            ("Play_vo_AatroxSkin33_Ping2DOnMyWay", "ping"),
            ("Play_vo_AatroxSkin33_JokeResponse3DGeneral", "joke_response"),
            ("Play_vo_AatroxSkin33_DanceResponse3DGeneral", "dance_response"),
            ("Play_vo_Aatrox_UseItem3DGuardianAngelR", "use_item"),
            ("Play_vo_AhriSkin86_LevelUp2DGeneral", "level_up"),
            ("Play_vo_Kalista_Assist3DGeneral", "assist"),
        ]
        for raw, action_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                self.assertEqual(translation["arguments"]["action"]["key"], action_key)

    def test_qualifier_specials_render_spec_wording(self) -> None:
        """qualifier 特例保留结构化角色；商店相反动作使用不同 action key。"""

        cases = [
            ("Play_vo_AurelionSolSkin31_Ping2DEnemyMissing", "ping"),
            ("Play_vo_AatroxSkin01_Move2DLong", "move"),
            ("Play_vo_Ornn_Recall3DCancel", "recall"),
            ("Play_vo_Azir_UseItem2DWard", "use_item"),
            ("Play_vo_LeonaSkin34_UseItemGroup2DWard", "use_item"),
            ("Play_vo_AhriSkin14_UseItem2DPotion", "use_item"),
            ("Play_vo_CaitlynSkin11_UseItem2DHealthPotion", "use_item"),
            ("Play_vo_AatroxSkin33_Shop2DOpen", "shop_open"),
            ("Play_vo_Taliyah_Shop2DClose", "shop_close"),
            ("Play_vo_ChogathSkin05_LevelUp2DSix", "level_up"),
            ("Play_vo_ChogathSkin05_LevelUp2DEleven", "level_up"),
            ("Play_vo_ChogathSkin05_LevelUp2DFifteen", "level_up"),
            ("Play_vo_Azir_LevelUp2DEighteen", "level_up"),
        ]
        for raw, action_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                self.assertEqual(translation["arguments"]["action"]["key"], action_key)
                qualifier = translation["arguments"]["qualifier"]
                self.assertEqual(qualifier["confidence"], "high")
                self.assertEqual(qualifier["provenance"]["rendered_via"], "action")

    def test_qualifier_is_visible_or_explicitly_pending_review(self) -> None:
        """已核验 qualifier 进动作中文；表外值可见且低置信，不能静默泛化。"""

        cases = [
            (
                "Play_vo_AurelionSolSkin31_Ping2DEnemyMissing",
                "敌人消失",
                "high",
                "enemymissing",
            ),
            ("Play_vo_AatroxSkin33_Ping2DOnMyWay", "正在路上", "high", "onmyway"),
            (
                "Play_vo_AsheSkin08_BuyItem2DInfinityEdge",
                "待审修饰（InfinityEdge）",
                "low",
                "unreviewed",
            ),
        ]
        for raw, expected_zh, confidence, expected_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                action = translation["arguments"]["action"]
                self.assertIn(expected_zh, action["labels"]["zh_CN"])
                self.assertEqual(action["confidence"], confidence)
                qualifier = translation["arguments"]["qualifier"]
                self.assertIn(expected_zh.removesuffix("购买装备语音"), qualifier["labels"]["zh_CN"])
                self.assertEqual(qualifier["confidence"], confidence)
                self.assertEqual(qualifier["fallback"], confidence != "high")
                self.assertEqual(qualifier["key"], expected_key)
                display = build_compat_display(translation, raw_event_name=raw)
                self.assertIn(expected_zh, display["zh_cn_detail"])
                self.assertIn(expected_zh, display["zh_cn_short"])

    def test_move_context_qualifier_is_visible_in_action(self) -> None:
        """move 位置 qualifier（River 等）使用已核验中文并入动作。"""

        translation = build_translation(enriched_record("Play_vo_AatroxSkin33_Move2DRiver"), TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["action"]["key"], "move")
        self.assertIn("河道", args["action"]["labels"]["zh_CN"])
        self.assertEqual(args["qualifier"]["labels"]["zh_CN"], "河道")

    def test_generic_qualifier_not_emitted(self) -> None:
        """General/Standard/Loop 等通用 qualifier 不产生 qualifier argument。"""

        for raw in [
            "Play_vo_AatroxSkin33_Recall3DGeneral",
            "Play_vo_AatroxSkin33_Move2DStandard",
            "Play_vo_ViSkin48_Dance3DLoop",
        ]:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertNotIn("qualifier", translation["arguments"])

    def test_technical_qualifier_is_safe_but_still_blocks_stable(self) -> None:
        """技术壳 token 只留在 token 字段，中文使用低置信安全占位。"""

        translation = build_translation(
            enriched_record("Play_vo_Yorick_LaughResponse3DGeneral"),
            TranslationBuildContext(),
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        qualifier = translation["arguments"]["qualifier"]
        self.assertEqual(qualifier["key"], "unreviewed")
        self.assertEqual(qualifier["token"], "Response3DGeneral")
        self.assertEqual(qualifier["labels"]["zh_CN"], "待审技术修饰")
        self.assertTrue(qualifier["fallback"])
        self.assertNotIn("3D", translation["arguments"]["action"]["labels"]["zh_CN"])

    def test_compound_move_and_spell_state_qualifiers_fail_closed(self) -> None:
        """复合移动上下文和技能状态不能被纯 Long/普通 Move 高置信吞并。"""

        cases = [
            ("Play_vo_ViegoSkin43_Move2DLongAllSwords", "待审移动修饰"),
            ("Play_vo_LuxSkin18_MoveOrder2DLong", "待审移动修饰"),
            ("Play_vo_AatroxSkin01_Move2DRReady", "待审技能状态"),
        ]
        for raw, expected_zh in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                qualifier = translation["arguments"]["qualifier"]
                self.assertEqual(qualifier["key"], "unreviewed")
                self.assertEqual(qualifier["confidence"], "low")
                self.assertTrue(qualifier["fallback"])
                self.assertEqual(qualifier["labels"]["zh_CN"], expected_zh)
                action = translation["arguments"]["action"]
                self.assertEqual(action["confidence"], "low")
                self.assertIn(expected_zh, action["labels"]["zh_CN"])

    def test_equivalent_item_qualifiers_share_canonical_key(self) -> None:
        """技术壳同义写法不得生成重复 pattern key。"""

        pairs = [
            ("Play_vo_Azir_UseItem2DWard", "Play_vo_LeonaSkin34_UseItemGroup2DWard", "ward"),
            ("Play_vo_AhriSkin14_UseItem2DPotion", "Play_vo_CaitlynSkin11_UseItem2DHealthPotion", "potion"),
        ]
        for first_raw, second_raw, expected_key in pairs:
            with self.subTest(first_raw=first_raw, second_raw=second_raw):
                first = build_translation(enriched_record(first_raw), TranslationBuildContext())
                second = build_translation(enriched_record(second_raw), TranslationBuildContext())
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                assert first is not None and second is not None
                self.assertEqual(first["arguments"]["qualifier"]["key"], expected_key)
                self.assertEqual(second["arguments"]["qualifier"]["key"], expected_key)

    def test_non_vo_champion_action_builds_meaningful_frame(self) -> None:
        """可确定动作的英雄 SFX 不再因非 VO 统一降为 Tier3。"""

        from lol_event_semantics.normalized_output import build_normalized_record

        record = enriched_record("Play_sfx_AatroxSkin07_Death3D_cast")
        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_action_event")

        normalized = build_normalized_record(record, translation_context=reference_translation_context())
        display = normalized["display"]
        assert isinstance(display, dict)
        self.assertEqual(display["tier"], 1)
        self.assertEqual(display["zh_cn_short"], "死亡音效开始")

    def test_spell_slot_records_keep_ability_frames(self) -> None:
        """（回归）带技能槽位的同族记录仍走 ability builder，不被 voice_event 抢占。"""

        cases = [
            ("Play_vo_Graves_GravesMove_cast3D", "ability_cast_event"),
            ("Play_sfx_Karthus_KarthusDeathDefiedBuff_OnBuffActivate", "ability_state_change"),
        ]
        for raw, frame_type in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], frame_type)


class NormalizedTranslationBuilderOwnerSkinTokenTests(unittest.TestCase):
    """验证 owner alias 内嵌皮肤号（GalioSKin04 大小写 typo）不进 champion 文案。"""

    def test_champion_argument_strips_embedded_skin_token(self) -> None:
        """champion alias 剥离尾部 SkinNN，skin token 单独成 argument。"""

        translation = build_translation(
            enriched_record("Play_vo_GalioSKin04_Taunt3DGeneral"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Galio")
        self.assertEqual(args["champion"]["labels"]["zh_CN"], "Galio")
        self.assertEqual(args["skin"]["token"], "Skin04")


class SpellSfxNoPhaseTests(unittest.TestCase):
    """验证无相位 spell sfx 的相位合成（复用 media_base 尾段词表与 overall 先例）。"""

    def test_no_phase_sfx_builds_overall_phase(self) -> None:
        """有技能键无相位的 sfx 记录合成 key=overall 整段相位进入 ability_cast_event。"""

        record = enriched_record("Play_sfx_PykeSkin01_PykeR_hitlocation")
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["ability_slot"]["value"], "r")
        self.assertEqual(args["phase"]["key"], "overall")
        self.assertIsNone(args["phase"].get("token"))

    def test_tail_phase_word_reuses_media_base_vocabulary(self) -> None:
        """cue 尾段命中 media_base 尾段相位词表（loop 等）时用该相位而非 overall。"""

        record = enriched_record("Play_sfx_LuxSkin01_LuxE_loop")
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["phase"]["key"], "loop")
        self.assertEqual(args["phase"]["token"], "loop")

    def test_vo_spell_without_phase_stays_legacy(self) -> None:
        """vo 无相位形态（voProcessing 开关等）不做合成，维持 legacy 不建 frame。"""

        record = enriched_record("Play_vo_AkaliSkin14_AkaliW_voProcessing_OFF")
        self.assertIsNone(build_translation(record, TranslationBuildContext()))

    def test_reviewed_missile_cast_phase_builds_translation(self) -> None:
        """已纳入词表的 missile_cast 应保留为“飞弹施放”，不退回 legacy。"""

        record = enriched_record("Play_sfx_AurelionSolSkin01_AurelionSolQ_missilecast")
        self.assertEqual(record.semantic_slots.get("phase"), "missile_cast")
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["arguments"]["phase"]["key"], "missile_cast")
        self.assertEqual(translation["arguments"]["phase"]["labels"]["zh_CN"], "飞弹施放")


class NormalizedTranslationBuilderAbilityCastTests(unittest.TestCase):
    """验证 ability_cast_event frame。"""

    def test_basic_spell_cast_builds_ability_cast_event(self) -> None:
        """普通技能施放构建 ability_cast_event，含槽位与相位角色。"""

        record = enriched_record("Play_sfx_Annie_AnnieQ_cast")
        translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Annie")
        self.assertEqual(args["ability_slot"]["value"], "q")
        self.assertIn("phase", args)

    def test_sfx_spell_cast_uses_hint_and_skin(self) -> None:
        """sfx 技能音效用 spell_key_hint 回退出槽位并带皮肤。"""

        record = enriched_record("Play_sfx_AkaliSkin02_AkaliRb_OnCast")
        translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Akali")
        self.assertEqual(args["skin"]["token"], "Skin02")
        self.assertEqual(args["ability_slot"]["value"], "r")
        self.assertIn("phase", args)

    def test_vo_non_spell_owner_prefix_builds_action_or_attack_frame(self) -> None:
        """VO 的 Execute/RangedAttack 保留动作语义且不得升级为技能槽位。"""

        cases = [
            (
                "Play_vo_Renekton_RenektonExecute_cast3D",
                "champion_action_event",
                "action",
                "execute_action",
            ),
            (
                "Play_vo_JayceSkin35_JayceRangedAttack_cast3D",
                "champion_attack_event",
                "attack",
                "ranged_basic_attack",
            ),
        ]
        for raw, frame_type, role, key in cases:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                translation = build_translation(record, TranslationBuildContext())

                self.assertNotIn("spell_key", record.semantic_slots)
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], frame_type)
                self.assertEqual(translation["arguments"][role]["key"], key)

    def test_vo_reference_spell_id_still_builds_high_confidence_slot(self) -> None:
        """精确 reference spell id 仍可建立 VO ability frame。"""

        cases = [
            ("Play_vo_Lux_LuxPrismaticWaveMissile_hit3D", "w"),
            ("Play_vo_Renekton_RenektonPreExecute_cast3D", "w"),
            ("Play_vo_RengarSkin08_RengarQemp_cast3D", "q"),
            ("Play_vo_Maokai_MaokaiRmis_OnHit", "r"),
            ("Play_vo_Kalista_KalistaExpunge_cast3D", "e"),
            ("Play_vo_KalistaSkin05_KalistaExpunge_cast3D", "e"),
        ]
        for raw, expected_slot in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], expected_slot)

    def test_vo_owner_prefix_hint_keeps_uppercase_internal_spell_root(self) -> None:
        """大写槽位后接大写内部词根的既有 VO 技能仍可建 frame。"""

        cases = [
            ("Play_vo_Caitlyn_CaitlynWSnare_Jade_hit3D", "w"),
            ("Play_vo_Smolder_SmolderRMomMissileSweetspot_miss3D", "r"),
            ("Play_vo_DianaSkin47_DianaQOuterMissile_miss3D", "q"),
        ]
        for raw, expected_slot in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], expected_slot)

    def test_ultimate_spellbook_builds_borrowed_ability_frame(self) -> None:
        """终极魔典借用大招由独立 frame 保留模式、英雄、槽位与阶段。"""

        record = enriched_record("Play_vo_UltBook_AhriR_OnCast")
        translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "borrowed_ability_event")
        self.assertEqual(translation["arguments"]["context"]["key"], "ultimate_spellbook")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Ahri")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
        self.assertEqual(translation["arguments"]["phase"]["key"], "cast")


class NormalizedTranslationBuilderSpellPhaseBackfillTests(unittest.TestCase):
    """验证 spell builder 回填 lexer 已识别的 phase（根因 B）。"""

    def test_inferred_spell_backfills_cast_phase(self) -> None:
        """set_inferred_spell_profile 应回填 cast3D 解析出的 phase。"""

        record = enriched_record("Play_vo_Alistar_Headbutt_cast3D")
        self.assertEqual(record.profile.primary_family, "spell")
        self.assertEqual(record.semantic_slots.get("phase"), "cast")

    def test_contextual_alias_spell_backfills_cast_phase(self) -> None:
        """set_champion_contextual_alias_profile 应回填 cast3D 解析出的 phase。"""

        record = enriched_record("Play_vo_ChogathSkin02_Feast_cast3D")
        self.assertEqual(record.profile.primary_family, "spell")
        self.assertEqual(record.semantic_slots.get("phase"), "cast")

    def test_inferred_spell_cast_builds_ability_cast_event(self) -> None:
        """回填后 cast3D 推断技能应能建 ability_cast_event frame 且带 phase 角色。"""

        record = enriched_record("Play_vo_Alistar_Headbutt_cast3D")
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertIn("phase", translation["arguments"])


class NormalizedTranslationBuilderMissileLaunchPhaseTests(unittest.TestCase):
    """验证 OnMissileLaunch 及其变体解析为 missile_launch（根因 A 最大头）。"""

    def test_on_missile_launch_resolves_phase(self) -> None:
        """OnMissileLaunch 应解析为 missile_launch 并回填到 spell slot。"""

        record = enriched_record("Play_sfx_AatroxSkin01_AatroxW_OnMissileLaunch")
        self.assertEqual(record.profile.primary_family, "spell")
        self.assertEqual(record.semantic_slots.get("phase"), "missile_launch")


class NormalizedTranslationBuilderLowercasePhaseTests(unittest.TestCase):
    """验证小写相位尾段经全链路回填到 spell slot（根因 A 大小写）。"""

    def test_lowercase_end_backfills_phase(self) -> None:
        """小写 _end 应回填 spell slot phase=end。"""

        record = enriched_record("Play_sfx_AatroxSkin01_AatroxR_end")
        self.assertEqual(record.profile.primary_family, "spell")
        self.assertEqual(record.semantic_slots.get("phase"), "end")


class NormalizedTranslationBuilderPhaseAllowlistRenderTests(unittest.TestCase):
    """验证扩充的 phase 经 translation 双层门进入 ability_cast_event frame（步骤 4）。"""

    def _phase_argument(self, raw: str) -> dict[str, object]:
        record = enriched_record(raw)
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        return translation["arguments"]["phase"]

    def test_missile_launch_renders(self) -> None:
        phase = self._phase_argument("Play_sfx_AatroxSkin01_AatroxW_OnMissileLaunch")
        self.assertEqual(phase["key"], "missile_launch")

    def test_end_renders(self) -> None:
        phase = self._phase_argument("Play_sfx_AatroxSkin01_AatroxR_end")
        self.assertEqual(phase["key"], "end")

    def test_detonate_renders(self) -> None:
        phase = self._phase_argument("Play_sfx_AatroxSkin01_AatroxR_detonate")
        self.assertEqual(phase["key"], "detonate")

    def test_phase_key_covers_allowlist(self) -> None:
        """phase_key 应认得全部高置信 allowlist phase。"""

        from lol_event_semantics.normalized_translation_builder import phase_key

        expected = {
            "missile_cast": "missile_cast",
            "missile_launch": "missile_launch",
            "launch": "launch",
            "end": "end",
            "start": "start",
            "land": "land",
            "detonate": "detonate",
            "charge": "charge",
            "fullcharge": "fullcharge",
            "stop": "stop",
            "miss": "miss",
            "catch": "catch",
            "return": "return",
            "ready": "ready",
            "swap": "swap",
            "reset": "reset",
        }
        for phase, key in expected.items():
            with self.subTest(phase=phase):
                self.assertEqual(phase_key(phase, "ability_cast_event"), key)


class ChampionArgumentNameSystemTests(unittest.TestCase):
    """验证 champion argument 的 label 来源标记与名称多形式接线。"""

    @staticmethod
    def build_champion_argument(entry: dict[str, str]) -> dict[str, object]:
        """用给定 label entry 构建 Annie Q 施放 frame 并返回 champion argument。

        Args:
            entry: Annie 的多语言 label 映射（可为 ChampionLabelEntry）。

        Returns:
            champion argument 字典。
        """

        record = enriched_record("Play_sfx_Annie_AnnieQ_cast")
        translation = build_translation(record, TranslationBuildContext(champion_labels={"Annie": entry}))
        assert translation is not None
        argument = translation["arguments"]["champion"]
        assert isinstance(argument, dict)
        return argument

    def test_reference_sourced_label_is_not_fallback(self) -> None:
        """reference 直取的 zh label 不标 fallback，来源写入 provenance。"""

        argument = self.build_champion_argument(
            ChampionLabelEntry(
                {"zh_CN": "安妮", "en_US": "Annie"},
                label_sources={"zh_CN": "reference_names", "en_US": "reference_names"},
            )
        )

        self.assertEqual(argument["labels"]["zh_CN"], "安妮")
        self.assertFalse(argument["fallback"])
        self.assertNotIn("fallback_reason", argument)
        self.assertEqual(
            argument["provenance"]["label_sources"],
            {"zh_CN": "reference_names", "en_US": "reference_names"},
        )

    def test_vote_sourced_zh_label_marks_fallback(self) -> None:
        """（用例 B）皮肤投票回填的 zh label 必须标 fallback 并写明原因。"""

        argument = self.build_champion_argument(
            ChampionLabelEntry(
                {"zh_CN": "安妮", "en_US": "Annie"},
                label_sources={"zh_CN": "skin_label_vote", "en_US": "reference_names"},
            )
        )

        self.assertTrue(argument["fallback"])
        self.assertEqual(argument["fallback_reason"], "champion_zh_label_from_skin_vote")
        self.assertEqual(argument["provenance"]["label_sources"]["zh_CN"], "skin_label_vote")

    def test_name_forms_carry_title_and_nicknames(self) -> None:
        """（用例 C）name_forms 无损带上称号与俗称列表。"""

        argument = self.build_champion_argument(
            ChampionLabelEntry(
                {"zh_CN": "亚托克斯", "en_US": "Aatrox"},
                name_forms={"title": {"zh_CN": ["暗裔剑魔"]}, "nicknames": {"zh_CN": ["剑魔"]}},
            )
        )

        self.assertEqual(
            argument["name_forms"],
            {"title": {"zh_CN": ["暗裔剑魔"]}, "nicknames": {"zh_CN": ["剑魔"]}},
        )

    def test_missing_nicknames_not_written_as_empty_field(self) -> None:
        """（用例 D）无俗称时 name_forms 不含 nicknames；全空时不写 name_forms。"""

        title_only = self.build_champion_argument(
            ChampionLabelEntry({"zh_CN": "阿卡丽"}, name_forms={"title": {"zh_CN": ["离群之刺"]}})
        )
        self.assertEqual(title_only["name_forms"], {"title": {"zh_CN": ["离群之刺"]}})
        self.assertNotIn("nicknames", title_only["name_forms"])

        empty = self.build_champion_argument(ChampionLabelEntry({"zh_CN": "安妮"}))
        self.assertNotIn("name_forms", empty)

    def test_plain_dict_entry_keeps_legacy_behavior(self) -> None:
        """普通 dict entry 保持既有行为：不标 fallback、不写 name_forms。"""

        argument = self.build_champion_argument({"zh_CN": "安妮", "en_US": "Annie"})

        self.assertEqual(argument["labels"]["zh_CN"], "安妮")
        self.assertFalse(argument["fallback"])
        self.assertNotIn("name_forms", argument)
        self.assertNotIn("label_sources", argument["provenance"])


class DisplayLabelsInjectionTests(unittest.TestCase):
    """验证 builder 展示词条可通过注入 DisplayLabels 覆盖（T3.3 词表外置）。"""

    def test_missing_phase_entry_falls_back_to_key_without_raising(self) -> None:
        """①: 注入词表缺一个 phase 词条时，label 回落为 key 本身且不抛错。"""

        empty_labels = DisplayLabels(phase={})
        argument = phase_argument("cast", "ability_cast_event", labels=empty_labels)
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertEqual(argument["labels"]["zh_CN"], "cast")
        self.assertEqual(argument["labels"]["en_US"], "cast")

    def test_added_gameplay_event_entry_renders(self) -> None:
        """②: 注入词表新增一个 gameplay_event 词条应正确渲染，无需改代码。"""

        labels = DisplayLabels(gameplay_event={"second_blood": ("二血", "second blood")})
        argument = gameplay_event_argument("second_blood", labels=labels)
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertEqual(argument["labels"]["zh_CN"], "二血")
        self.assertEqual(argument["labels"]["en_US"], "second blood")


class CounterpartFrameDispatchTests(unittest.TestCase):
    """验证 profile 分派：interaction/kill_or_score 走新 frame，self_action 不受影响。"""

    def test_interaction_attack_builds_champion_interaction_cue(self) -> None:
        """interaction profile 的 attack 家族不再被 voice_event 抢走。"""

        record = enriched_record("Play_vo_AatroxSkin01_Attack2DPantheon")
        self.assertEqual(record.profile.semantic_profile.value, "interaction")

        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_interaction_cue")

    def test_self_action_attack_and_taunt_keep_voice_event(self) -> None:
        """（回归）self_action 的 attack/taunt 仍构建 voice_event。"""

        for raw in ["Play_vo_AsheSkin06_Attack2DGeneral", "Play_vo_AsheSkin06_Taunt2DGeneral"]:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                self.assertEqual(record.profile.semantic_profile.value, "self_action")

                translation = build_translation(record, TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")

    def test_unsupported_interaction_families_build_no_frame(self) -> None:
        """encounter/recall/nearby_event/interactive 本批返回 None 不建 frame。"""

        for raw in [
            "Play_vo_Yunara_SecondEncounter3DKennen",
            "Play_vo_XayahSkin57_Recal3DRakan",
            "Play_vo_Urgot_Nearby3DDeathsAlly",
        ]:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                self.assertEqual(record.profile.semantic_profile.value, "interaction")
                self.assertIsNone(build_translation(record, TranslationBuildContext()))


class CounterpartArgumentResolutionTests(unittest.TestCase):
    """验证 counterpart 名称解析链：champion/skin/词表/fallback。"""

    def test_vocab_counterpart_resolves_chinese_label(self) -> None:
        """词表 kind（Baron）解析出中文词条，不标 fallback。"""

        record = enriched_record("Play_vo_Ezreal_Kill3DBaron")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        counterpart = translation["arguments"]["counterpart"]
        self.assertTrue(counterpart["labels"]["zh_CN"])
        self.assertFalse(counterpart["fallback"])

    def test_unmatched_counterpart_falls_back_to_english(self) -> None:
        """词表未命中（count_context Fifteen）直出英文 label，不硬造中文。"""

        record = enriched_record("Play_vo_KaisaSkin40_Kill3DFifteen")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        counterpart = translation["arguments"]["counterpart"]
        self.assertEqual(counterpart["labels"]["zh_CN"], "Fifteen")
        self.assertTrue(counterpart["fallback"])
        self.assertEqual(counterpart["fallback_reason"], "missing_counterpart_zh_label")
        self.assertEqual(counterpart["confidence"], "low")

    def test_general_counterpart_is_treated_as_no_counterpart(self) -> None:
        """FirstEncounter3DGeneral 的 kind=general 视为无特定对象。"""

        record = enriched_record("Play_vo_AatroxSkin01_FirstEncounter3DGeneral")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        self.assertNotIn("counterpart", translation["arguments"])
        self.assertIn("trigger", translation["arguments"])

    def test_objective_steal_bakes_spec_wording(self) -> None:
        """objective_steal 记录建 frame，抢夺目标进 objective_steal argument。"""

        record = enriched_record("Play_vo_MasterYiSkin89_Kill3DDragonSteal")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        args = translation["arguments"]
        self.assertIn("trigger", args)
        self.assertIn("objective_steal", args)

    def test_non_single_letter_kill_method_keeps_raw(self) -> None:
        """kill_method 归一失败（RActive）时 raw 原样保留在 argument。"""

        record = enriched_record("Play_vo_MasterYiSkin89_Kill3DRActive")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        args = translation["arguments"]
        self.assertIn("trigger", args)
        self.assertEqual(args["kill_method"]["labels"]["zh_CN"], "RActive")

    def test_single_letter_kill_method_renders_spec_wording_in_detail_only(self) -> None:
        """QWER 单字母击杀键归一成功，kill_method argument 保留原始按键 token。"""

        record = enriched_record("Play_vo_Teemo_Kill3DR")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        self.assertEqual(translation["arguments"]["kill_method"]["token"], "R")

    def test_ownerless_kill_with_technical_owner_label_builds_no_frame(self) -> None:
        """ownerless 事件（owner 段即事件体）的 champion label 残留 3D token，不建 frame。"""

        record = enriched_record("Play_vo_Kill3DPenta")
        self.assertIsNone(build_translation(record, reference_translation_context()))

    def test_sfx_kill_uses_sound_effect_word(self) -> None:
        """asset_kind=sfx 的 kill 记录同样构建 kill frame，trigger 角色齐备。"""

        record = enriched_record("Play_sfx_AhriSkin85_KillCounter_10_buffactivate")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        self.assertEqual(translation["frame_type"], "kill_or_destroy_event")
        self.assertTrue(translation["arguments"]["trigger"]["labels"]["zh_CN"])


class ChampionDialogueEventDispatchTests(unittest.TestCase):
    """验证批次4 dialogue_chain profile 分派到 champion_dialogue_event。"""

    def test_dialogue_chain_builds_champion_dialogue_event(self) -> None:
        """dialogue 链记录构建 champion_dialogue_event，槽位无损进 arguments。"""

        record = enriched_record("Play_vo_KayleSkin42_Chat3DMorgana_Move3DKayle01_Chat2")
        self.assertEqual(record.profile.semantic_profile.value, "dialogue_chain")

        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_dialogue_event")

        args = translation["arguments"]
        self.assertIn("champion", args)
        self.assertIn("skin", args)
        self.assertFalse(args["partner"]["fallback"])
        self.assertEqual(args["base_trigger"]["key"], "move")
        # base_trigger.counterpart_raw 与 line_variant 无损存 arguments，不进文案。
        self.assertEqual(args["base_trigger"]["provenance"]["counterpart_raw"], "Kayle")
        self.assertEqual(args["base_trigger"]["token"], "Move3DKayle01")
        self.assertEqual(args["turn"]["key"], "2")
        self.assertEqual(args["line_variant"]["value"], "1")

    def test_dialogue_without_partner_builds_no_frame(self) -> None:
        """chat_partner 与 precondition 双缺时缺必需 partner，不建 frame。

        批次6c 后 WithSennaNear 前置条件链已回落 precondition 建 frame（见
        Batch6cWithCompanionTests），本锁改用剥除两个槽位的记录守「不硬造
        对话对象」底线。
        """

        record = enriched_record("Play_vo_LucianSkin06_WithSennaNear_FirstMove2D01_Chat03")
        self.assertEqual(record.profile.semantic_profile.value, "dialogue_chain")
        record.semantic_slots["chat_partner"] = None
        record.semantic_slots["precondition"] = None
        self.assertIsNone(build_translation(record, reference_translation_context()))


class DialoguePartnerResolutionTests(unittest.TestCase):
    """验证 partner 解析链：champion_labels → skin_labels → 词表 → 英文 fallback。"""

    def test_champion_partner_resolves_reference_label(self) -> None:
        """raw=Morgana 直接当 champion alias 命中 reference，高置信不标 fallback。"""

        record = enriched_record("Play_vo_KayleSkin42_Chat3DMorgana_Move3DMorgana")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        partner = translation["arguments"]["partner"]
        self.assertTrue(partner["labels"]["zh_CN"])
        self.assertEqual(partner["confidence"], "high")
        self.assertFalse(partner["fallback"])

    def test_skin_partner_resolves_skin_label(self) -> None:
        """raw 带 SkinNN 时走 skin_labels 命中皮肤词条，不标 fallback。"""

        record = enriched_record("Play_vo_KayleSkin42_Chat3DMorgana_Move3DMorgana")
        record.semantic_slots["chat_partner"] = {"raw": "KayleSkin42", "kind": "unresolved_entity"}
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        partner = translation["arguments"]["partner"]
        self.assertTrue(partner["labels"]["zh_CN"])
        self.assertFalse(partner["fallback"])

    def test_vocab_partner_resolves_chinese_entry(self) -> None:
        """raw=Yordle 命中外置词表，medium 置信不标 fallback。"""

        record = enriched_record("Play_vo_KayleSkin42_Chat3DMorgana_Move3DMorgana")
        record.semantic_slots["chat_partner"] = {"raw": "Yordle", "kind": "unresolved_entity"}
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        partner = translation["arguments"]["partner"]
        self.assertTrue(partner["labels"]["zh_CN"])
        self.assertEqual(partner["confidence"], "medium")
        self.assertFalse(partner["fallback"])

    def test_proximity_partner_strips_near_far_and_resolves_champion(self) -> None:
        """RakanNear 剥离距离限定词后按 champion 解析，限定词与原 token 无损保留。"""

        record = enriched_record("Play_vo_XayahSkin04_Chat3DRakanNear_Attack2DGeneral")
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        partner = translation["arguments"]["partner"]
        self.assertEqual(partner["confidence"], "high")
        self.assertFalse(partner["fallback"])
        self.assertEqual(partner["provenance"]["proximity"], "near")
        self.assertEqual(partner["token"], "RakanNear")

    def test_unresolved_partner_falls_back_to_english(self) -> None:
        """解析链全未命中（含剥限定词重查）直出英文，不硬造中文。"""

        record = enriched_record("Play_vo_XayahSkin04_Chat3DRakanNear_Attack2DGeneral")
        record.semantic_slots["chat_partner"] = {"raw": "MysteryVoice", "kind": "unresolved_entity"}
        translation = build_translation(record, reference_translation_context())
        assert translation is not None
        partner = translation["arguments"]["partner"]
        self.assertEqual(partner["labels"]["zh_CN"], "MysteryVoice")
        self.assertTrue(partner["fallback"])
        self.assertEqual(partner["fallback_reason"], "missing_partner_zh_label")
        self.assertEqual(partner["confidence"], "low")
        self.assertNotIn("proximity", partner["provenance"])


class AnnouncerEventDispatchTests(unittest.TestCase):
    """验证播报员超族双条件分派：4 家族接入，tutorial 等同族异 kind 不分派。"""

    def test_announcer_families_build_announcer_event(self) -> None:
        """announcer/system_voice/announcer_persona 家族均分派到 announcer_event。"""

        cases = [
            ("Play_vo_Announcer_Female1_AceEnemyTeam", "announcer"),
            ("Play_vo_Announcer_Brawl_Victory", "system_voice"),
            ("Play_vo_Announcer_Maverick_DoubleKillEnemyTeam", "announcer_persona"),
        ]
        for raw, family in cases:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                self.assertEqual(record.profile.primary_family, family)
                self.assertEqual(record.semantic_slots.get("system_voice_kind"), "announcer")

                translation = build_translation(record, TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "announcer_event")

    def test_tutorial_kind_is_not_dispatched(self) -> None:
        """tutorial 记录（system_voice 家族但 kind=tutorial）被第二门排除，不建 frame。"""

        record = enriched_record("Play_vo_Tutorial_ChampSelect_Ahri")
        self.assertEqual(record.semantic_slots.get("system_voice_kind"), "tutorial")
        self.assertIsNone(build_translation(record, TranslationBuildContext()))


class AnnouncerEventTokenResolutionTests(unittest.TestCase):
    """验证事件 token 解析：On 剥离、队伍尾缀大小写、多段取尾、表外不建 frame。"""

    def test_team_suffix_split_keeps_stem_and_team(self) -> None:
        """AceEnemyTeam 剥出 EnemyTeam 尾缀，event/team 双 argument 无损。"""

        record = enriched_record("Play_vo_Announcer_Female1_AceEnemyTeam")
        translation = build_translation(record, TranslationBuildContext())
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["event"]["key"], "Ace")
        self.assertEqual(args["event"]["token"], "AceEnemyTeam")
        self.assertEqual(args["event"]["provenance"]["source"], "semantic_slots.cue_path")
        self.assertEqual(args["team"]["key"], "EnemyTeam")

    def test_on_prefix_strip_and_multi_segment_cue_tail(self) -> None:
        """多段 cue_path 取末段并剥 On 前缀（OnChampionPentaKillYourTeam）。"""

        record = enriched_record("Play_vo_Announcer_Female1_NexusBlitz_Core_OnChampionPentaKillYourTeam")
        translation = build_translation(record, TranslationBuildContext())
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["event"]["key"], "ChampionPentaKill")
        # token 保留 cue_path 末段原样（含 On 前缀与队伍尾缀）。
        self.assertEqual(args["event"]["token"], "OnChampionPentaKillYourTeam")
        self.assertEqual(args["team"]["key"], "YourTeam")

    def test_team_suffix_match_is_case_insensitive(self) -> None:
        """小写尾缀 purple 命中 Purple 词条，token 保留原始写法。"""

        record = enriched_record("Play_vo_Announcer_Female1_ChampionPentaKillpurple")
        translation = build_translation(record, TranslationBuildContext())
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["event"]["key"], "ChampionPentaKill")
        self.assertEqual(args["team"]["key"], "Purple")
        self.assertEqual(args["team"]["token"], "purple")

    def test_event_without_team_suffix_has_no_team_argument(self) -> None:
        """Victory 整词命中时无 team argument。"""

        record = enriched_record("Play_vo_Announcer_Brawl_Victory")
        translation = build_translation(record, TranslationBuildContext())
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["event"]["key"], "Victory")
        self.assertNotIn("team", args)

    def test_out_of_vocab_stem_builds_no_frame(self) -> None:
        """表外词干不建 frame，维持 legacy 展示不放英文 fallback。

        原样例 PentaKillHeroBlue 已被批次6e 主体三态承接，改用真表外词干守
        同一底线。
        """

        record = enriched_record("Play_vo_Announcer_Female1_MysteryEventYourTeam")
        self.assertEqual(record.semantic_slots.get("system_voice_kind"), "announcer")
        self.assertIsNone(build_translation(record, TranslationBuildContext()))


class Batch6eAnnouncerSubjectTests(unittest.TestCase):
    """验证批次6e 播报主体三态（You/Hero 中缀，语义经字符串表核验）。"""

    def test_subject_variants_build_frame(self) -> None:
        """{事件}{You|Hero}{队伍} 组合建 frame，主体词进 subject argument 且不再出 team。"""

        cases = [
            ("Play_vo_Announcer_Female1_RampageYouYourTeam", "You"),
            ("Play_vo_Announcer_Female1_LegendaryHeroEnemyTeam", "HeroEnemyTeam"),
            ("Play_vo_Announcer_Male1_KillingSpreeHeroYourTeam", "HeroYourTeam"),
            ("Play_vo_Announcer_Female1_PentaKillHeroBlue", "HeroBlue"),
        ]
        for raw, subject_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "announcer_event")
                args = translation["arguments"]
                self.assertEqual(args["subject"]["key"], subject_key)
                self.assertNotIn("team", args)

    def test_unverified_forms_stay_legacy(self) -> None:
        """Shutdown 三态（主体方向未证实）与 KillChampion 复合形态不建 frame。"""

        for raw in [
            "Play_vo_Announcer_Female1_ShutdownYouYourTeam",
            "Play_vo_Announcer_Female1_ShutdownHeroEnemyTeam",
            "Play_vo_Announcer_Female1_KillChampionYouHeroYourTeam",
            "Play_vo_Announcer_Female1_KillChampionTurretYouEnemyTeam",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), TranslationBuildContext()))

    def test_mode_package_prefix_builds_mode_argument(self) -> None:
        """包名段经词表出 mode argument（人声前缀剥离），事件本体照常。"""

        cases = [
            ("Play_vo_Announcer_Global_Female1Worlds23_OnAce", "Worlds23"),
            ("Play_vo_Announcer_Global_Pyke_OnAce", "Pyke"),
        ]
        for raw, mode_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                args = translation["arguments"]
                self.assertEqual(args["mode"]["key"], mode_key)
                self.assertEqual(args["event"]["key"], "Ace")

    def test_unverified_package_builds_no_mode_argument(self) -> None:
        """表外包名（RhaastKayn 等内部代号）不出 mode argument，事件本体照常。"""

        translation = build_translation(
            enriched_record("Play_vo_Announcer_Global_RhaastKayn_OnAce"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("mode", translation["arguments"])
        self.assertEqual(translation["arguments"]["event"]["key"], "Ace")

    def test_plain_team_suffix_path_is_unchanged(self) -> None:
        """既有纯队伍尾缀路径回归锁（无主体词时 team argument 照旧）。"""

        translation = build_translation(
            enriched_record("Play_vo_Announcer_Female1_ChampionPentaKillPurple"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["team"]["key"], "Purple")
        self.assertNotIn("subject", args)


class SpellRankVoiceEventTests(unittest.TestCase):
    """验证批次5b spell rank 语音：rankone/two/three 相位接入 ability_cast_event。"""

    def test_rank_phases_build_ability_cast_event(self) -> None:
        """RankOne/Two/Three 记录构建 ability_cast_event，rank 相位进 phase key。"""

        cases = [
            ("Play_vo_AatroxSkin33_Spell2DRRankOne", "r", "rankone"),
            ("Play_vo_UdyrSkin06_Spell2DQRankOne", "q", "rankone"),
            ("Play_vo_VayneSkin11_Spell2DRRankTwo", "r", "ranktwo"),
            ("Play_vo_VayneSkin11_Spell2DRRankThree", "r", "rankthree"),
        ]
        for raw, slot, phase_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "ability_cast_event")
                args = translation["arguments"]
                self.assertEqual(args["ability_slot"]["value"], slot)
                self.assertEqual(args["phase"]["key"], phase_key)
                self.assertIn("skin", args)

    def test_unparsed_rank_variant_builds_no_frame(self) -> None:
        """RankTwoJhin（拆解层未出 spell_key/phase）不建 frame，维持 legacy。"""

        record = enriched_record("Play_vo_Hwei_Spell2DRRankTwoJhin")
        self.assertIsNone(record.semantic_slots.get("spell_key"))
        self.assertIsNone(build_translation(record, TranslationBuildContext()))


class MediaBaseActionStateTests(unittest.TestCase):
    """验证批次5b media_base 动作状态路由到 skin_action_state frame。"""

    def test_vocab_actions_build_skin_action_state(self) -> None:
        """词表内动作词（含词尾 3D 剥离）构建 skin_action_state。"""

        cases = [
            ("Play_sfx_AzirSkin05_Base_Dance3D_buffactivate", "dance", "Dance3D"),
            ("Play_sfx_AzirSkin05_Base_Death3D_cast", "death", "Death3D"),
            ("Play_sfx_AzirSkin05_Base_Taunt3D_buffactivate", "taunt", "Taunt3D"),
            ("Play_sfx_GangplankSkin33_Base_Recall3D_buffactivate", "recall", "Recall3D"),
        ]
        for raw, action_key, token in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "skin_action_state")
                args = translation["arguments"]
                self.assertEqual(args["action"]["key"], action_key)
                # 原始动作 token 与维度标记无损保留在 argument（Base 分组词不进文案）。
                self.assertEqual(args["action"]["token"], token)
                self.assertEqual(args["action"]["provenance"]["dimension_token"], "3D")
                self.assertIn("phase", args)
                self.assertIn("skin", args)

    def test_lowercase_base_segment_is_matched(self) -> None:
        """cue_path 小写 base 段同样命中（大小写两态口径）。"""

        translation = build_translation(
            enriched_record("Play_sfx_AzirSkin05_base_Dance3D_buffactivate"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "skin_action_state")
        self.assertEqual(translation["arguments"]["action"]["key"], "dance")

    def test_skinless_base_champion_builds_frame_without_skin(self) -> None:
        """基础皮肤形态（无 SkinNN token）合法缺 skin 角色。"""

        translation = build_translation(
            enriched_record("Play_sfx_Galio_Base_Death3D_buffactivate"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "skin_action_state")
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Galio")
        self.assertNotIn("skin", args)
        self.assertEqual(args["action"]["key"], "death")

    def test_out_of_vocab_action_builds_no_frame(self) -> None:
        """表外动作词不建 frame（防错优先，严禁英文 fallback 进文案）。"""

        record = enriched_record("Play_sfx_AzirSkin05_Base_Dance3D_buffactivate")
        record.semantic_slots["cue_path"] = ["Base", "Backflip3D", "buffactivate"]
        self.assertIsNone(build_translation(record, TranslationBuildContext()))

    def test_reviewed_hud_skin_cue_recovers_champion_frame(self) -> None:
        """HUD 只是外壳；相邻 Champion/SkinNN 与已审规则可恢复皮肤界面语义。"""

        record = enriched_record("Play_sfx_hud_base_MissFortune_Skin16_GearUI_Available")
        self.assertEqual(record.semantic_slots.get("sfx_scope"), "champion_sfx")
        self.assertEqual(record.semantic_slots["subject"]["alias"], "MissFortune")
        self.assertEqual(record.semantic_slots.get("skin_token"), "Skin16")
        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
        self.assertEqual(translation["arguments"]["effect"]["key"], "gear_ui")
        self.assertEqual(translation["arguments"]["qualifier"]["key"], "available")

    def test_tail_phase_words_build_frame(self) -> None:
        """批次6a：cue 尾段相位词（leadin/loop/windup/winddown/leadout）经门禁进 phase。"""

        cases = [
            ("Play_sfx_Azir_Base_Dance3D_leadin", "dance", "leadin"),
            ("Play_sfx_GangplankSkin33_Base_Joke3D_loop", "joke", "loop"),
            ("Play_sfx_TristanaSkin33_Base_Dance3D_windup", "dance", "windup"),
            ("Play_sfx_Tristana_Base_Recall_winddown", "recall", "winddown"),
            ("Play_sfx_KarthusSkin26_Base_Recall_leadout", "recall", "leadout"),
        ]
        for raw, action_key, phase_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "skin_action_state")
                args = translation["arguments"]
                self.assertEqual(args["action"]["key"], action_key)
                self.assertEqual(args["phase"]["key"], phase_key)
                self.assertEqual(args["phase"]["provenance"]["source"], "semantic_slots.cue_path")

    def test_no_tail_action_builds_overall_sfx_phase(self) -> None:
        """批次6a：无尾段纯动作记录合成 key=overall 整段相位（无原始 token）。"""

        cases = [
            ("Play_sfx_EvelynnSkin08_Base_dance", "dance"),
            ("Play_sfx_EvelynnSkin08_Base_death", "death"),
            ("Play_sfx_ShenSkin22_Base_taunt", "taunt"),
        ]
        for raw, action_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "skin_action_state")
                args = translation["arguments"]
                self.assertEqual(args["action"]["key"], action_key)
                self.assertEqual(args["phase"]["key"], "overall")
                self.assertIsNone(args["phase"].get("token"))

    def test_reviewed_non_phase_tails_are_structured_qualifiers(self) -> None:
        """动作后的音层、姿势、段位与激活变体必须保留，不能因非相位尾段丢整条。"""

        cases = [
            ("Play_sfx_TristanaSkin33_Base_Death3D_body", "body", "overall"),
            ("Play_sfx_TristanaSkin33_Base_Death3D_gun", "gun", "overall"),
            ("Play_sfx_TaricSkin18_Base_Joke3D_pose1", "pose1", "overall"),
            ("Play_sfx_TaricSkin18_Base_Joke3D_open", "open", "overall"),
            ("Play_sfx_TristanaSkin51_Base_Recall_A", "a", "overall"),
            (
                "Play_sfx_TaricSkin18_Base_Dance3D_crystal_loop_buffactivate2",
                "crystal+buffactivate2",
                "loop",
            ),
        ]
        for raw, qualifier_key, phase_key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "skin_action_state")
                self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier_key)
                self.assertEqual(translation["arguments"]["phase"]["key"], phase_key)

    def test_idle_action_and_reviewed_owner_effect_do_not_fall_back_to_generic_event(self) -> None:
        """标准待机动作与悠米已审叫声都应有具体 frame。"""

        idle = build_translation(
            enriched_record("Play_sfx_YorickSkin12_Base_idle"),
            reference_translation_context(),
        )
        self.assertIsNotNone(idle)
        assert idle is not None
        self.assertEqual(idle["frame_type"], "skin_action_state")
        self.assertEqual(idle["arguments"]["action"]["key"], "idle")

        meow = build_translation(
            enriched_record("Play_sfx_YuumiSkin39_Base_meow_01"),
            reference_translation_context(),
        )
        self.assertIsNotNone(meow)
        assert meow is not None
        self.assertEqual(meow["frame_type"], "champion_sfx_effect_event")
        self.assertEqual(meow["arguments"]["effect"]["key"], "meow")
        self.assertEqual(meow["arguments"]["qualifier"]["key"], "01")


class Batch6dShopVoiceTests(unittest.TestCase):
    """验证批次6d 商店老板语音 frame（shop_voice_event）。"""

    def test_subject_resolution_chain(self) -> None:
        """对象按英雄→皮肤→地区阵营→装备/条件链解析，subject_kind 记入 provenance。"""

        cases = [
            ("Play_vo_Shopkeeper_Map12_SRU_StorekeeperSouth_Open2DZiggs", "champion"),
            ("Play_vo_Shopkeeper_Map12_HA_AP_Viking_Open2DOlafSkin03", "skin"),
            ("Play_vo_Shopkeeper_Map12_HA_AP_ShpSouth_Open2DZaun", "faction_or_region"),
            ("Play_vo_Shopkeeper_Map12_SRU_StorekeeperSouth_Purchase2DSpiritVisage", "shop_vocab"),
            ("Play_vo_Shopkeeper_Map12_HA_AP_Viking_Purchase2DFrozenMallet", "shop_vocab"),
            ("Play_vo_Shopkeeper_Map12_HA_AP_Hermit_Close3DNoPurchase", "shop_vocab"),
        ]
        for raw, subject_kind in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "shop_voice_event")
                args = translation["arguments"]
                self.assertEqual(args["shop_subject"]["provenance"]["subject_kind"], subject_kind)

    def test_general_and_bare_forms_build_frame_without_subject(self) -> None:
        """General 与裸动作形态合法缺 subject（浏览/环境/关店整体语音）。"""

        for raw in [
            "Play_vo_Shopkeeper_Map12_Bloom_TwilightGhost_Close3DGeneral",
            "Play_vo_Shopkeeper_Map12_HA_AP_Viking_Ambient3DGeneral",
            "Play_vo_Shopkeeper_Map12_BW_AP_Bubbs_Purchase",
        ]:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertNotIn("shop_subject", translation["arguments"])

    def test_champion_alias_drift_forms_resolve(self) -> None:
        """英雄 token 三类形态漂移经别名兜底按 champion 解析（仅 shop 链）。"""

        for raw in [
            "Play_vo_Shopkeeper_Map12_BW_AP_Finn_OpenReksai",
            "Play_vo_Shopkeeper_Map12_Bloom_TwilightGhost_Open2DWukong",
            "Play_vo_Shopkeeper_Map12_HA_AP_ShpNorth_Open2DRenataGlasc",
        ]:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                subject = translation["arguments"]["shop_subject"]
                self.assertEqual(subject["provenance"]["subject_kind"], "champion")
                self.assertTrue(subject["labels"]["zh_CN"])

    def test_out_of_vocab_subject_builds_no_frame(self) -> None:
        """表外对象（CountTen 语义未证实）不建 frame，维持 legacy。"""

        for raw in [
            "Play_vo_Shopkeeper_Map12_Bloom_TwilightGhost_Open2DCountTen",
            "Play_vo_Shopkeeper_Map12_HA_AP_Viking_Open2DCountSeven",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), reference_translation_context()))

    def test_non_shopkeeper_map_records_stay_legacy(self) -> None:
        """map 家族非商店记录维持无 frame（分派接管不抢占旧行为）。"""

        record = enriched_record("Play_sfx_Env_Map11_Emitter_northshopkeeper_exhaust")
        translation = build_translation(record, reference_translation_context())
        self.assertIsNone(translation)


class Batch6cWithCompanionTests(unittest.TestCase):
    """验证批次6c：With{Champion}[Near] 前置条件的对话链承接与同行英雄条件。"""

    def test_precondition_dialogue_chain_builds_frame_with_partner(self) -> None:
        """WithXNear 对话链回落 precondition 同行英雄为 partner。"""

        cases = [
            "Play_vo_Lucian_WithSennaNear_FirstMove2D01_Chat03",
            "Play_vo_SennaSkin16_WithLucianNear_KillChampion3D05_Chat02",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "champion_dialogue_event")
                args = translation["arguments"]
                self.assertEqual(args["partner"]["provenance"]["source"], "semantic_slots.precondition")
                self.assertEqual(args["partner"]["provenance"]["proximity"], "near")
                self.assertIn("base_trigger", args)

    def test_three_way_chain_appends_trigger_counterpart(self) -> None:
        """三方链（WithLucianNear+初遇Thresh）第三方英雄无损存 base_trigger provenance。"""

        raw = "Play_vo_SennaSkin16_WithLucianNear_FirstEncounter3D01Thresh_Chat02"
        translation = build_translation(enriched_record(raw), reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "champion_dialogue_event")
        args = translation["arguments"]
        self.assertEqual(args["base_trigger"]["provenance"]["counterpart_raw"], "Thresh")

    def test_companion_condition_on_framed_families(self) -> None:
        """kill/interaction/voice frame 追加同行英雄 companion argument。"""

        cases = [
            ("Play_vo_Lucian_KillChampion3DWithSennaNear", "kill_or_destroy_event"),
            ("Play_vo_Lucian_FirstEncounter3DThreshWithSennaNear", "champion_interaction_cue"),
            ("Play_vo_SennaSkin16_Move2DWithLucianNear", "voice_event"),
            ("Play_vo_Xayah_WithRakanDance3D", "voice_event"),
        ]
        for raw, frame_type in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), reference_translation_context())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], frame_type)
                args = translation["arguments"]
                self.assertIn("companion", args)
                self.assertEqual(args["companion"]["provenance"]["source"], "semantic_slots.precondition")

    def test_uncovered_families_with_condition_stay_legacy(self) -> None:
        """heal/first_move 基础家族未承接，With 变体同样不建 frame（范围锁）。"""

        for raw in ["Play_vo_Lucian_HealWithSennaNear", "Play_vo_Lucian_FirstMove2DWithSennaNear"]:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), reference_translation_context()))

    def test_unresolvable_companion_builds_no_companion_argument(self) -> None:
        """precondition 同行者中文名不可解析时不建 companion argument（宁缺毋错）。"""

        record = enriched_record("Play_vo_Lucian_KillChampion3DWithSennaNear")
        record.semantic_slots["precondition"]["counterpart"]["raw"] = "Bogus"
        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("companion", translation["arguments"])


class SpellRankArgumentTests(unittest.TestCase):
    """验证 rank argument 门禁修法：仅 contextual_alias.raw 以 Rank\\d+ 结尾时消费 spell_rank。"""

    def test_volley_rank_alias_builds_rank_argument(self) -> None:
        """VolleyRankN 记录消费 spell_rank 为 rank argument（等级数进 key）。"""

        cases = [
            ("Play_sfx_AsheSkin08_VolleyRank2_OnCast", "2"),
            ("Play_sfx_Ashe_VolleyRank5_OnCast", "5"),
        ]
        for raw, key in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "ability_cast_event")
                args = translation["arguments"]
                self.assertEqual(args["rank"]["key"], key)
                self.assertEqual(args["rank"]["provenance"]["source"], "semantic_slots.spell_rank")
                self.assertIn("ability_component", args)

    def test_rank_number_mismatch_builds_no_rank_argument(self) -> None:
        """alias 尾号与 spell_rank 不一致时不出 rank argument。"""

        record = enriched_record("Play_sfx_AsheSkin08_VolleyRank2_OnCast")
        record.semantic_slots["spell_rank"] = 3
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("rank", translation["arguments"])

    def test_variant_number_alias_builds_no_rank_argument(self) -> None:
        """Tibbers 普攻2 是攻击变体，不得写入 spell_rank 或 rank argument。"""

        record = enriched_record("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack2_OnCast")
        self.assertEqual(record.semantic_slots.get("attack_variant"), 2)
        self.assertIsNone(record.semantic_slots.get("spell_rank"))
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("rank", translation["arguments"])


class ChampionSfxSemanticRenderingTests(unittest.TestCase):
    """用户可见 SFX 文案必须使用权威名称和动作语义，不泄漏工程 token。"""

    def render(
        self,
        raw: str,
        *,
        source_path: str | None = None,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """按真实 reference 构建 frame 与兼容 display。"""

        record = enriched_record(raw)
        if source_path is not None:
            record.source_paths = [source_path]
        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        display = build_compat_display(translation, raw_event_name=raw)
        return translation, display

    def test_tryndamere_basic_attack_uses_chinese_skin_and_attack_phase(self) -> None:
        translation, display = self.render("Play_sfx_TryndamereSkin10_TryndamereBasicAttack2_OnCast")

        self.assertEqual(translation["frame_type"], "champion_attack_event")
        self.assertEqual(translation["arguments"]["attack"]["variant"], 2)
        self.assertEqual(translation["arguments"]["attack"]["labels"]["zh_CN"], "普通攻击2")
        self.assertEqual(translation["arguments"]["phase"]["key"], "attack_cast")
        self.assertEqual(display["zh_cn_short"], "普通攻击2出手")
        self.assertEqual(display["zh_cn_detail"], "泰达米尔 腥红之月 普通攻击2出手")
        self.assertNotRegex(display["zh_cn_detail"], r"Tryndamere|Skin10|状态启动")

    def test_reviewed_attack_variant_does_not_repeat_visible_number(self) -> None:
        """人工 attack alias 已带变体号时不得重复追加。"""

        translation, display = self.render("Play_vo_Skarner_SkarnerBasicAttack2_cast5D")

        attack = translation["arguments"]["attack"]
        self.assertEqual(attack["variant"], 2)
        self.assertEqual(attack["labels"]["zh_CN"], "普攻变体 2")
        self.assertEqual(display["zh_cn_short"], "普攻变体 2出手")
        self.assertEqual(display["zh_cn_short"].count("2"), 1)

    def test_tryndamere_recall_uses_audio_lifecycle_wording(self) -> None:
        _translation, display = self.render("Play_sfx_TryndamereSkin10_Recall3D_buffactivate")

        self.assertEqual(display["zh_cn_short"], "回城音效开始")
        self.assertEqual(display["zh_cn_detail"], "泰达米尔 腥红之月 回城音效开始")
        self.assertNotIn("状态启动", display["zh_cn_detail"])

    def test_tryndamere_bloodlust_hides_particle_token_and_uses_effect_phase(self) -> None:
        translation, display = self.render("Play_sfx_TryndamereSkin10_BloodlustParticle_OnBuffActivate")

        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "q")
        self.assertEqual(translation["arguments"]["phase"]["key"], "effect_activate")
        self.assertIn("Q技能嗜血杀戮效果生效", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"粒子|Particle|状态启动")

    def test_reviewed_sfx_components_keep_owner_slot_component_and_tail_semantics(self) -> None:
        """高频 SFX 组件应纠正词首误判，并保留命中、发射、引爆等尾段语义。"""

        cases = [
            (
                "Play_sfx_Brand_P_detonate2",
                "p",
                "被动技能灼烧标记引爆",
            ),
            (
                "Play_sfx_Fiddlesticks_FiddlesticksQmissilefear_OnHit",
                "q",
                "Q技能恐惧飞弹命中",
            ),
            (
                "Play_sfx_Shen_ShenRshield_OnBuffActivate",
                "r",
                "R技能慈悲度魂落护盾生效",
            ),
            (
                "Play_sfx_Kled_KledRiderQMissile_OnMissileLaunch",
                "q",
                "Q技能骑乘飞索捕熊器飞弹弹道发射",
            ),
        ]
        for raw, spell_key, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["ability_slot"]["key"], spell_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

    def test_reviewed_non_spell_sfx_clears_false_skill_hint_and_uses_audio_wording(self) -> None:
        """Evolve 等英文词首不是技能槽位，角色拟声音效也不得伪装成技能。"""

        cases = [
            (
                "Play_sfx_Kaisa_KaisaEvolveSelf_cast",
                "自身进化音效开始",
            ),
            (
                "Play_sfx_Yuumi_meow_01",
                "喵叫音效（1号变体）",
            ),
            (
                "Play_sfx_KledRiderSkin09_movement_saddle",
                "坐骑移动音效",
            ),
        ]
        for raw, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertNotIn("ability_slot", translation["arguments"])
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

    def test_high_frequency_generic_sfx_families_keep_meaning_and_lifecycle(self) -> None:
        """资源开关、双人舞和角色状态不得退回“音效事件/状态启动”。"""

        cases = [
            (
                "Play_sfx_EvelynnSkin01_RealTimeProcessing_ON",
                "real_time_processing",
                "audio_start",
                "实时音频处理音效开始",
            ),
            (
                "Play_sfx_EvelynnSkin42_RealTimeProcessing_OFF",
                "real_time_processing",
                "audio_end",
                "实时音频处理音效结束",
            ),
            (
                "Play_sfx_RakanSkin04_WithXayah_Dance_into",
                "duo_dance_with_xayah",
                "leadin",
                "与霞双人舞音效引入",
            ),
            (
                "Play_sfx_XayahSkin02_WithRakan_Dance_loop",
                "duo_dance_with_rakan",
                "loop",
                "与洛双人舞音效循环",
            ),
            (
                "Play_sfx_GarenSkin13_GarenRecouperate1_OnBuffActivate",
                "perseverance_recovery",
                "audio_start",
                "被动·坚韧恢复音效开始",
            ),
            (
                "Play_sfx_HeimerTYellowSkin06_HeimerdingerEngineAudio_turrettalk_buffactivate",
                "turret_engine_audio",
                "audio_start",
                "炮台引擎运转音效开始",
            ),
            (
                "Play_sfx_ZyraSkin02_ZyraPlantEnrage_OnBuffDeactivate",
                "plant_enrage",
                "audio_end",
                "植物狂暴音效结束",
            ),
            (
                "Play_sfx_RenektonSkin17_RenektonRageReady_OnBuffActivate",
                "rage_ready",
                "audio_start",
                "怒气就绪音效开始",
            ),
        ]
        for raw, effect_key, phase_key, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(translation["arguments"]["phase"]["key"], phase_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

    def test_reviewed_sfx_qualifiers_preserve_middle_cue_semantics(self) -> None:
        """主效果与阶段之间的模式、形态和资源修饰语不得在中文中消失。"""

        cases = [
            (
                "Play_sfx_KaynSkin20_Music_Mute",
                "skin_music",
                "mute",
                "皮肤音乐（静音）",
            ),
            (
                "Play_sfx_MorganaSkin80_MaskSwap3D_mask2",
                "mask_swap",
                "mask2",
                "面具切换音效（形态2）",
            ),
            (
                "Play_sfx_AhriSkin86_Evolve_Burst_Gear_buffactivate",
                "skin_evolve",
                "burst+gear",
                "皮肤进化音效（爆发、装备）开始",
            ),
        ]
        for raw, effect_key, qualifier_key, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

    def test_generic_sfx_aliases_restore_scope_and_keep_action_context(self) -> None:
        """动作 profile 后命中的公共 SFX 仍须产出英雄音效 frame，并保留动作上下文。"""

        cases = [
            (
                "Play_sfx_KaynSkin20_Dance3D_music_loop",
                "skin_music",
                "dance3d",
                "皮肤音乐（舞蹈）循环",
            ),
            (
                "Play_sfx_AsheSkin08_Recall3D_windup_hologram",
                "skin_action",
                "recall3d+hologram",
                "皮肤动作音效（回城、全息影像）起手",
            ),
        ]
        for raw, effect_key, qualifier_key, expected_short in cases:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                translation, display = self.render(raw)

                self.assertEqual(record.semantic_slots["sfx_scope"], "champion_sfx")
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

    def test_dimension_only_tail_does_not_block_engineering_owner_alias(self) -> None:
        """Loop_2D/3D 中的维度不能阻止工程 owner 回归真实英雄技能。"""

        for raw in (
            "Play_sfx_LilliaRSleep_Loop_2D",
            "Stop_sfx_LilliaRSleep_Loop_3D",
        ):
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["champion"]["alias"], "Lillia")
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
                self.assertEqual(
                    translation["arguments"]["ability_component"]["key"],
                    "lullaby_sleep",
                )
                self.assertIn("夜阑谣昏睡", display["zh_cn_short"])
                self.assertNotRegex(display["zh_cn_detail"], r"LilliaRSleep|音效事件|尚无中文短标签")

    def test_last_reviewed_pve_boss_and_form_sfx_families_are_specific(self) -> None:
        """PVE 首领机制与伊莉丝蜘蛛形态动作必须保留具体效果，不能只显示 Boss/状态。"""

        action_cases = [
            ("Play_sfx_Strawberry_Boss_Aatrox_Taunt3D_buffactivate_into", "boss_taunt"),
            ("Play_sfx_Strawberry_Boss_Belveth_Laugh3D_buffactivate", "boss_laugh"),
        ]
        for raw, action_key in action_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_action_event")
                self.assertEqual(translation["arguments"]["action"]["key"], action_key)
                self.assertEqual(translation["arguments"]["context"]["key"], "strawberry")
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

        boss_cases = [
            ("Play_sfx_Strawberry_Boss_Belveth_fallingdebris_explode", "falling_debris"),
            ("Play_sfx_Strawberry_Boss_Briar_player_targeted_buffactivate", "player_targeted"),
            ("Play_sfx_Strawberry_Boss_Briar_spawn_buffactivate", "boss_spawn"),
            ("Play_sfx_Strawberry_Boss_Reksai_fallingdebris_explode", "falling_debris"),
            ("Play_sfx_Strawberry_Boss_Reksai_mini_spawn", "mini_spawn"),
            ("Play_sfx_Strawberry_Boss_Reksai_spawn_buffactivate", "boss_spawn"),
        ]
        for raw, effect_key in boss_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(translation["arguments"]["context"]["key"], "strawberry")
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

        elise, elise_display = self.render("Play_sfx_Elise_Skin05_Spider_Emote_Dance_oba_loop")
        self.assertEqual(elise["frame_type"], "skin_action_state")
        self.assertEqual(elise["arguments"]["action"]["key"], "spider_form_dance_sfx")
        self.assertIn("蜘蛛形态", elise_display["zh_cn_detail"])
        self.assertNotRegex(elise_display["zh_cn_detail"], r"音效事件|尚无中文短标签")

    def test_system_hud_shell_recovers_reviewed_skin_champion(self) -> None:
        """HUD 只是外壳；cue 中已核验的 LuxSkin07 必须恢复拉克丝与皮肤界面点击语义。"""

        raw = "Play_sfx_hud_LuxSkin07_Generic_Click_UI"
        record = enriched_record(raw)
        translation, display = self.render(raw)

        self.assertEqual(record.semantic_slots["subject"]["alias"], "Lux")
        self.assertEqual(record.semantic_slots["skin_token"], "Skin07")
        self.assertEqual(record.semantic_slots["sfx_scope"], "champion_sfx")
        self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
        self.assertEqual(translation["arguments"]["effect"]["key"], "elementalist_ui_click")
        self.assertEqual(display["zh_cn_short"], "元素使界面点击音效")
        self.assertNotRegex(display["zh_cn_detail"], r"hud|LuxSkin07|Generic|音效事件")

    def test_emote_system_recovers_standard_champion_actions_and_lifecycle(self) -> None:
        """Emote/Base 是分组壳；其后的标准动作、维度和生命周期必须进入英雄动作 frame。"""

        cases = [
            (
                "Play_sfx_IreliaSkin45_Emote_Joke_buffactivate",
                "joke_sfx",
                "audio_start",
                "玩笑音效开始",
            ),
            (
                "Play_sfx_kayle_skin06_emote_joke_loop_buffactivate",
                "joke_sfx",
                "loop",
                "玩笑音效循环",
            ),
            (
                "Play_sfx_ChogathSkin07_emote_recallin",
                "recall_sfx",
                "in",
                "回城音效进入",
            ),
            (
                "Play_sfx_yasuo_skin87_emote_winddown_buffactivate_01",
                "winddown_sfx",
                "audio_start",
                "收招音效（1号变体）开始",
            ),
        ]
        for raw, action_key, phase_key, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_action_event")
                self.assertEqual(translation["arguments"]["action"]["key"], action_key)
                self.assertEqual(translation["arguments"]["phase"]["key"], phase_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"emote|Emote|音效事件|状态启动")

    def test_emote_specific_effects_keep_inner_qualifiers(self) -> None:
        """非标准表情效果也必须保留等级、按键、枪声、低通层等内层语义。"""

        cases = [
            (
                "Play_sfx_SamiraSkin30_emote_jumbotron_lv2",
                "soul_fighter_jumbotron",
                "lv2",
                "斗魂大屏幕音效（2级）",
            ),
            (
                "Play_sfx_SamiraSkin30_emote_dance_gunshot",
                "dance_emote",
                "gunshot",
                "舞蹈音效（枪声）",
            ),
            (
                "Stop_sfx_samira_skin30_emote_homeguard_lp",
                "homeguard_emote",
                "lp",
                "家园卫士音效（低通音频层）停止",
            ),
            (
                "Play_sfx_AhriSkin86_Emote_Faker_buffactivate",
                "faker_emote",
                None,
                "李相赫主题表情音效开始",
            ),
        ]
        for raw, effect_key, qualifier_key, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                if qualifier_key is None:
                    self.assertNotIn("qualifier", translation["arguments"])
                else:
                    self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier_key)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"emote|Emote|音效事件|状态启动")

    def test_plain_basic_attacks_do_not_carry_false_passive_relation(self) -> None:
        """普通攻击与暴击攻击不因通用规则被挂到 P；真正被动来源须另有明确证据。"""

        for raw in (
            "Play_sfx_MissFortune_MissFortuneBasicAttack_OnHit",
            "Play_sfx_Zyra_hellvine_ZyraBasicAttack_OnHit",
        ):
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_attack_event")
                self.assertIsNone(record.semantic_slots.get("related_spell_key"))
                self.assertNotIn("related_spell", translation["arguments"]["attack"])
                self.assertNotIn("P技能", display["zh_cn_detail"])

    def test_obvious_non_passive_p_rules_render_as_attack_effect_or_real_slot(self) -> None:
        """内部字母 P 或旧错误规则不得压过 Attack、Voidling 等真实语义。"""

        attack_cases = [
            ("Play_sfx_DravenSkin68_DravenAttackP_LQC_OnHit", "draven_axe_attack"),
            ("Play_sfx_NunuSkin04_NunuStructureAttack_OnCast", "structure_attack"),
            ("Play_sfx_IllaoiSkin10_IllaoiTentacleAttack_champ_hit", "tentacle_attack"),
        ]
        for raw, attack_key in attack_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)
                self.assertEqual(translation["frame_type"], "champion_attack_event")
                self.assertEqual(translation["arguments"]["attack"]["key"], attack_key)
                self.assertNotIn("P技能", display["zh_cn_detail"])

        voidling, voidling_display = self.render("Play_sfx_Malzahar_Voidling_buffactivate")
        self.assertEqual(voidling["arguments"]["ability_slot"]["key"], "w")
        self.assertNotIn("P技能", voidling_display["zh_cn_detail"])

        empty, empty_display = self.render("Play_sfx_MissFortune_MissFortuneBulletEMPTY_OnMissileLaunch")
        self.assertEqual(empty["frame_type"], "champion_sfx_effect_event")
        self.assertEqual(empty["arguments"]["effect"]["key"], "empty_bullet")
        self.assertNotIn("P技能", empty_display["zh_cn_detail"])

    def test_ruby_final_boss_veigar_keeps_actor_spell_and_mechanic_semantics(self) -> None:
        """Ruby/Boss/FN 只是模式与工程前缀；维迦、技能槽位和首领机制都必须保留。"""

        record = enriched_record("Play_sfx_Ruby_Boss_FNVeigar_DelayedDarkMatter_warning_small")
        self.assertEqual(record.semantic_slots["mode_actor"]["alias"], "Veigar")
        self.assertEqual(record.semantic_slots["mode_actor_role"], "boss")

        spell_cases = [
            (
                "Play_sfx_Ruby_Boss_FNVeigar_DelayedDarkMatter_warning_small",
                "w",
                "delayed_dark_matter",
                "warning+small",
            ),
            (
                "Play_sfx_Ruby_Boss_FNVeigarEventHorizon_loop",
                "e",
                "event_horizon",
                None,
            ),
            (
                "Play_sfx_Ruby_Boss_FNVeigarR_OnCast",
                "r",
                "primordial_burst",
                None,
            ),
        ]
        for raw, slot, component, qualifier in spell_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)
                self.assertEqual(translation["arguments"]["champion"]["alias"], "Veigar")
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], slot)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component)
                self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
                if qualifier is not None:
                    self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier)
                self.assertNotRegex(display["zh_cn_detail"], r"Ruby|FNVeigar|音效事件|维迦事件")

        mechanic_cases = [
            ("Play_sfx_Ruby_Boss_FNVeigar_Imprison_Player_shield_loop", "boss_imprison"),
            ("Play_sfx_Ruby_Boss_FNVeigar_MordeE_warning", "borrowed_deaths_grasp"),
            ("Play_sfx_Ruby_Boss_FNVeigar_SafeZone_shrink", "safe_zone"),
            ("Play_sfx_Ruby_Boss_FNVeigar_InstaKill_OnHit", "instant_kill"),
        ]
        for raw, effect in mechanic_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect)
                self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
                self.assertNotRegex(display["zh_cn_detail"], r"Ruby|FNVeigar|音效事件|维迦事件")

    def test_strawberry_boss_hud_keeps_actor_and_announcement_semantics(self) -> None:
        """Gameplay/HUD/Boss 是界面路径，不得把具体 Boss 压成“英雄事件”。"""

        cases = [
            (
                "Play_sfx_Strawberry_Gameplay_HUD_Boss_Aatrox_VictoryBanner_fakeout",
                "Aatrox",
                "boss_victory_banner",
                "fakeout",
            ),
            (
                "Play_sfx_Strawberry_Gameplay_HUD_Boss_Aatrox_announcement",
                "Aatrox",
                "boss_announcement",
                None,
            ),
            (
                "Play_sfx_Strawberry_Gameplay_HUD_Boss_Briar_announcement",
                "Briar",
                "boss_announcement",
                None,
            ),
            (
                "Play_sfx_Strawberry_Gameplay_HUD_Boss_Belveth_announcement",
                "Belveth",
                "boss_announcement",
                None,
            ),
            (
                "Play_sfx_Strawberry_Gameplay_HUD_Boss_RekSai_announcement",
                "RekSai",
                "boss_announcement",
                None,
            ),
        ]
        for raw, champion, effect, qualifier in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["champion"]["alias"], champion)
                self.assertEqual(translation["arguments"]["effect"]["key"], effect)
                self.assertEqual(translation["arguments"]["context"]["key"], "strawberry")
                if qualifier is None:
                    self.assertNotIn("qualifier", translation["arguments"])
                else:
                    self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier)
                self.assertNotRegex(
                    display["zh_cn_detail"],
                    r"Gameplay|HUD|Boss|英雄事件|音效事件|状态启动",
                )

    def test_named_skin_audio_states_have_specific_effects_and_clean_playback(self) -> None:
        """烬被动待机音轨与金克丝老虎机必须说明对象，停止/静音不能再拼“状态启动”。"""

        cases = [
            (
                "Play_sfx_JhinSkin04_Idle_Passive_buffactivate",
                "ability_state_change",
                "passive_idle_audio",
                None,
            ),
            (
                "Mute_sfx_JhinSkin23_Idle_Passive_buffactivate",
                "ability_state_change",
                "passive_idle_audio",
                None,
            ),
            (
                "Play_jinx_skin60_slotmachine_buffactivate_chemtech",
                "champion_sfx_effect_event",
                "slot_machine",
                "chemtech",
            ),
            (
                "Stop_jinx_skin60_slotmachine_buffactivate",
                "champion_sfx_effect_event",
                "slot_machine",
                None,
            ),
        ]
        for raw, frame_type, component, qualifier in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], frame_type)
                role = "ability_component" if component == "passive_idle_audio" else "effect"
                self.assertEqual(translation["arguments"][role]["key"], component)
                if qualifier is not None:
                    self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier)
                self.assertNotRegex(
                    display["zh_cn_detail"],
                    r"状态启动|开始停止|启动静音|启动取消静音|音效事件",
                )

    def test_summoned_bank_owner_and_true_passive_keep_skill_semantics(self) -> None:
        """魔盒是萨科 W 召唤物，派克 Passive 才是真实 P；两者都要保留具体组件。"""

        shaco, shaco_display = self.render("Play_sfx_ShacoBoxSkin64_ShacoBoxSpell_OnMissileLaunch")
        self.assertEqual(shaco["arguments"]["champion"]["alias"], "Shaco")
        self.assertEqual(shaco["arguments"]["ability_slot"]["key"], "w")
        self.assertEqual(
            shaco["arguments"]["ability_component"]["key"],
            "jack_in_the_box_attack",
        )
        self.assertEqual(shaco["arguments"]["phase"]["key"], "missile_launch")
        self.assertNotRegex(shaco_display["zh_cn_detail"], r"ShacoBox|未收录皮肤|音效事件")

        pyke_stop, pyke_stop_display = self.render("Stop_sfx_PykeSkin75_PykePassive_heal_start")
        self.assertEqual(pyke_stop["arguments"]["ability_slot"]["key"], "p")
        self.assertEqual(
            pyke_stop["arguments"]["ability_component"]["key"],
            "gift_of_the_drowned_ones_heal",
        )
        self.assertEqual(pyke_stop["arguments"]["playback"]["key"], "stop")
        self.assertEqual(pyke_stop["arguments"]["phase"]["key"], "start")
        self.assertNotIn(
            pyke_stop["arguments"]["phase"]["labels"]["zh_CN"],
            pyke_stop_display["zh_cn_detail"],
        )
        self.assertNotRegex(pyke_stop_display["zh_cn_detail"], r"开始停止|被动技能停止$")

        pyke_vfx, pyke_vfx_display = self.render("Play_sfx_PykeSkin75_PykePassiveVFX_buffactivate")
        self.assertEqual(pyke_vfx["arguments"]["ability_slot"]["key"], "p")
        self.assertEqual(
            pyke_vfx["arguments"]["ability_component"]["key"],
            "gift_of_the_drowned_ones_vfx",
        )
        self.assertEqual(pyke_vfx["arguments"]["phase"]["key"], "effect_activate")
        self.assertIn("溺水之幸", pyke_vfx_display["zh_cn_detail"])

    def test_ownerless_spell_banks_resolve_champion_slot_and_component(self) -> None:
        """技能全名被吞进 owner 的旧 bank 仍须恢复英雄、槽位和官方技能组件。"""

        cases = [
            ("Play_sfx_LilliaE_OnCast", "Lillia", "e", "swirlseed_cast", "cast"),
            ("Play_sfx_LilliaR_OnCast", "Lillia", "r", "lilting_lullaby_cast", "cast"),
            (
                "Play_sfx_NocturneParanoiaDash_OnBuffActivate",
                "Nocturne",
                "r",
                "paranoia_dash",
                "effect_activate",
            ),
            (
                "Play_sfx_OrianaIzunaCommand_OnCast",
                "Orianna",
                "q",
                "attack_command",
                "cast",
            ),
            (
                "Play_sfx_OrianaRedactShield_OnBuffActivate",
                "Orianna",
                "e",
                "protect_shield",
                "effect_activate",
            ),
            (
                "Play_sfx_SkarnerE_OnCast",
                "Skarner",
                "e",
                "ixtal_impact_cast",
                "cast",
            ),
            (
                "Play_sfx_SkarnerQ_OnCast",
                "Skarner",
                "q",
                "uproot_cast",
                "cast",
            ),
            (
                "Play_sfx_SkarnerQRockThrow_OnCast",
                "Skarner",
                "q",
                "q_rock_throw",
                "cast",
            ),
            (
                "Play_sfx_ChogathEAttack_OnCast",
                "Chogath",
                "e",
                "vorpal_spikes_attack",
                "cast",
            ),
        ]
        for raw, champion, slot, component, phase in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["champion"]["alias"], champion)
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], slot)
                self.assertEqual(
                    translation["arguments"]["ability_component"]["key"],
                    component,
                )
                self.assertEqual(translation["arguments"]["phase"]["key"], phase)
                self.assertNotRegex(
                    display["zh_cn_detail"],
                    r"Lillia|Nocturne|Oriana|Skarner|Chogath|状态启动|音效事件",
                )

    def test_residual_skin_actions_and_legacy_owner_typos_render_specific_chinese(self) -> None:
        """基础动作尾层、皮肤动画转场与英雄拼写 typo 不得再退化成泛化状态。"""

        taric, taric_display = self.render("Stop_sfx_TaricSkin18_Base_Joke3D_camera_flash_buffactivate")
        self.assertEqual(taric["frame_type"], "skin_action_state")
        self.assertEqual(taric["arguments"]["action"]["key"], "joke")
        self.assertEqual(taric["arguments"]["qualifier"]["key"], "camera+flash")
        self.assertEqual(taric["arguments"]["playback"]["key"], "stop")
        self.assertEqual(taric["arguments"]["phase"]["key"], "audio_start")
        self.assertNotIn(
            taric["arguments"]["phase"]["labels"]["zh_CN"],
            taric_display["zh_cn_detail"],
        )
        self.assertNotRegex(taric_display["zh_cn_detail"], r"状态启动|开始停止")

        transitions = [
            (
                "Play_sfx_KatarinaSkin29_Spell4ToIdle_buffactivate",
                "ultimate_to_idle",
                "大招动作转待机",
            ),
            (
                "Play_sfx_KatarinaSkin29_Spell4ToRun_buffactivate",
                "ultimate_to_run",
                "大招动作转奔跑",
            ),
            (
                "Play_sfx_KaynSkin20_SpellToIdle_mus_buffactivate",
                "spell_to_idle",
                "技能动作转待机",
            ),
        ]
        for raw, effect, label in transitions:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect)
                self.assertIn(label, display["zh_cn_detail"])
                self.assertNotIn("状态启动", display["zh_cn_detail"])

        reload_cases = [
            (
                "Play_sfx_Corki_RapidReload_helibuffactivate",
                "helibuffactivate",
                "直升机主题",
            ),
            ("Play_sfx_CorkiSkin08_RapidReload_Boom", "boom", "爆炸"),
        ]
        for raw, qualifier, label in reload_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)
                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], "rapid_reload")
                self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier)
                self.assertIn("快速装填", display["zh_cn_detail"])
                self.assertIn(label, display["zh_cn_detail"])

        ziggs, ziggs_display = self.render("Play_sfx_ZiggssSkin07_Emote_Joke_oba")
        self.assertEqual(ziggs["arguments"]["champion"]["alias"], "Ziggs")
        self.assertIn("吉格斯", ziggs_display["zh_cn_detail"])
        self.assertNotRegex(ziggs_display["zh_cn_detail"], r"Ziggss|未收录皮肤|音效事件")

    def test_padded_bard_stop_keeps_r_skill_and_stop_control(self) -> None:
        """__Stop_ 导出填充不改变巴德 R 技能或停止控制语义。"""

        translation, display = self.render("__Stop_sfx_Bard_BardR_mis")

        self.assertEqual(translation["arguments"]["champion"]["alias"], "Bard")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
        self.assertEqual(translation["arguments"]["playback"]["key"], "stop")
        self.assertIn("巴德", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"__Stop|音效事件|尚无中文短标签")

    def test_reviewed_system_sfx_paths_render_ui_and_gameplay_meaning(self) -> None:
        """HUD/Base 与 Gameplay 是路径壳；完整已审路径必须生成具体系统音效。"""

        cases = [
            (
                "Play_sfx_hud_base_Pings_AreaIsWarded",
                "pings_areaiswarded",
                "该区域已有视野标记音效",
            ),
            (
                "Play_sfx_hud_base_SpellFail_Cooldown",
                "spellfail_cooldown",
                "技能尚未冷却提示音",
            ),
            (
                "Play_sfx_hud_base_store_item_buy_legendary",
                "store_item_buy_legendary",
                "商店购买传说级装备音效",
            ),
            (
                "Play_sfx_hud_base_Map_Close",
                "map_close",
                "地图关闭音效",
            ),
            (
                "Play_sfx_Cherry_Gameplay_Revive_ring_ready_buffactivate",
                "revive_ring_ready",
                "斗魂竞技场 复活圈就绪提示音开始",
            ),
            (
                "Play_sfx_Cherry_Gameplay_DestructiblePillar_respawning_loop",
                "destructiblepillar_respawning",
                "斗魂竞技场 可破坏石柱重生音效循环",
            ),
            (
                "Stop_sfx_Cherry_Gameplay_Revive_progress_buffactivate",
                "revive_progress",
                "斗魂竞技场 复活进度音效停止",
            ),
            (
                "Play_sfx_Map35_Gameplay_champion_kill_order",
                "champion_kill_order",
                "秩序方英雄击杀音效",
            ),
            (
                "Play_sfx_DoomBots_Gameplay_HUD_timer_countdown",
                "hud_timer_countdown",
                "末日人机 倒计时音效",
            ),
        ]
        for raw, effect_key, expected_detail in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "system_sfx_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(display["zh_cn_detail"], expected_detail)
                self.assertNotRegex(
                    display["zh_cn_detail"],
                    r"\bhud\b|Gameplay|Cherry|DoomBots|Map35|状态启动|音效事件|事件$",
                )

        unknown = build_translation(
            enriched_record("Play_sfx_hud_base_UnreviewedWidget_Click"),
            reference_translation_context(),
        )
        self.assertIsNone(unknown)

    def test_plain_item_sfx_lifecycle_uses_reference_name(self) -> None:
        """纯装备 ID + 生命周期可复用系统音效 frame，不把状态写成技能。"""

        cases = [
            (
                "Play_sfx_1035_buffactivate",
                "item_1035_sfx",
                "灰烬小刀音效开始",
            ),
            (
                "Stop_sfx_3050_buffactivate",
                "item_3050_sfx",
                "基克的聚合音效停止",
            ),
            (
                "Play_sfx_6035_Spell_OnCast",
                "item_6035_sfx",
                "密银黎明音效开始",
            ),
        ]
        for raw, effect_key, expected_detail in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "system_sfx_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(translation["arguments"]["phase"]["key"], "audio_start")
                self.assertEqual(display["zh_cn_detail"], expected_detail)

    def test_item_upgrade_ready_keeps_inference_evidence_and_item_context(self) -> None:
        """3010 的 ready 使用精确审阅决策，升级推断与装备身份分别保留。"""

        translation, display = self.render("Play_sfx_3010_ready")
        effect = translation["arguments"]["effect"]
        self.assertEqual(effect["labels"]["zh_CN"], "升级就绪音效")
        self.assertEqual(effect["confidence"], "medium")
        self.assertEqual(effect["provenance"]["source"], "official_mechanism_and_event_phase_inference")
        self.assertEqual(translation["arguments"]["context"]["key"], "item_3010")
        self.assertEqual(display["zh_cn_short"], "升级就绪音效")
        self.assertEqual(display["zh_cn_detail"], "共生鞋鱼升级就绪音效")
        from lol_event_semantics.normalized_validation import validate_translation_render

        issues = []
        validate_translation_render(translation, "item:3010", {"display": display}, issues, "Play_sfx_3010_ready")
        self.assertNotIn("translation_display_mismatch", {issue.code for issue in issues})
        self.assertIsNone(build_translation(enriched_record("Play_sfx_3013_ready"), reference_translation_context()))

    def test_reviewed_item_sfx_actions_keep_item_action_and_playback_semantics(self) -> None:
        """装备 ID 与精确动作对命中时，effect 同时保留装备身份和动作。"""

        cases = [
            (
                "Play_sfx_3053Shield_OnBuffActivate",
                "item_3053_shield_sfx",
                "斯特拉克的挑战护手护盾音效开始",
            ),
            (
                "Stop_sfx_3748Projectile_OnBuffActivate",
                "item_3748_projectile_sfx",
                "巨型九头蛇投射物音效停止",
            ),
            (
                "Play_sfx_6697_buffactivate_bronze",
                "item_6697_bronze_sfx",
                "狂妄青铜段位音效开始",
            ),
        ]
        for raw, expected_key, expected_detail in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "system_sfx_event")
                effect = translation["arguments"]["effect"]
                self.assertEqual(effect["key"], expected_key)
                self.assertEqual(effect["provenance"]["source"], "reference/items")
                self.assertEqual(
                    effect["provenance"]["action_source"],
                    "display_labels.system_sfx.item_action",
                )
                self.assertEqual(display["zh_cn_detail"], expected_detail)

    def test_unknown_or_mismatched_item_sfx_actions_stay_unframed(self) -> None:
        """动作白名单按装备 ID 精确绑定，未知动作、错配动作和额外余段不得穿透。"""

        cases = [
            "Play_sfx_3053Mystery_OnBuffActivate",
            "Play_sfx_1035Shield_OnBuffActivate",
            "Play_sfx_999999Shield_OnBuffActivate",
            "Play_sfx_6697_buffactivate_bronze_extra",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), reference_translation_context()))

    def test_reviewed_direct_hud_spell_paths_use_exact_system_labels(self) -> None:
        """无 Base 壳的 HUD 技能提示仍只按完整已审路径建系统音效 frame。"""

        cases = [
            ("Play_sfx_hud_SpellFail_NoMana", "spellfail_nomana", "法力不足提示音"),
            ("Play_sfx_hud_SpellFail_Silence", "spellfail_silence", "沉默状态无法施法提示音"),
            ("Play_sfx_hud_Spell_Upgrade", "spell_upgrade", "技能加点音效"),
            ("Play_sfx_hud_Spell_Use", "spell_use", "技能使用音效"),
        ]
        for raw, effect_key, expected_detail in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "system_sfx_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertEqual(display["zh_cn_detail"], expected_detail)

        unknown = build_translation(
            enriched_record("Play_sfx_hud_Spell_Unreviewed"),
            reference_translation_context(),
        )
        self.assertIsNone(unknown)

    def test_compact_spell_sequences_use_effect_phases_and_keep_inner_semantics(self) -> None:
        """组合技的范围/分身/循环和 Q3 段数必须保留，不得统一写“状态启动”。"""

        cases = [
            (
                "Play_sfx_YasuoSkin55_YasuoQ3W_OnBuffActivate",
                "q+w",
                "3",
                None,
                "effect_activate",
                "Q+W组合技第3段生效",
            ),
            (
                "Play_sfx_LeblancSkin35_LeblancRW_clone_buffactivate",
                "r+w",
                None,
                "clone",
                "effect_activate",
                "R+W组合技（分身）生效",
            ),
            (
                "Play_sfx_HweiSkin21_HweiWQ_AoE_buffactivate_loop",
                "w+q",
                None,
                "aoe",
                "loop",
                "W+Q组合技（范围效果）循环",
            ),
        ]
        for raw, sequence, stage, qualifier, phase, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_ability_sequence_event")
                self.assertEqual(translation["arguments"]["ability_sequence"]["value"], sequence)
                if stage is None:
                    self.assertNotIn("ability_stage", translation["arguments"])
                else:
                    self.assertEqual(translation["arguments"]["ability_stage"]["value"], stage)
                if qualifier is None:
                    self.assertNotIn("qualifier", translation["arguments"])
                else:
                    self.assertEqual(translation["arguments"]["qualifier"]["key"], qualifier)
                self.assertEqual(translation["arguments"]["phase"]["key"], phase)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotIn("状态启动", display["zh_cn_detail"])

    def test_reviewed_residual_sfx_families_use_specific_action_or_skill_semantics(self) -> None:
        """高频残余族应归入具体技能、动作或角色音效，而不是通用事件。"""

        spell_cases = [
            (
                "Play_sfx_GalioSkin28_W_SelfShield",
                "w",
                "shield_of_durand_self_shield",
            ),
            (
                "Play_sfx_MordekaiserSkin06_W_foley_end",
                "w",
                "indestructible_foley",
            ),
        ]
        for raw, slot, component in spell_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["ability_slot"]["key"], slot)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

        generic_cases = [
            ("Play_sfx_LuluSkin05_LuluFaerieOverride_blink", "faerie_blink"),
            ("Play_sfx_KSanteSkin01_effect_wall_travel", "wall_travel"),
            ("Play_sfx_CorkiSkin08_DangerZoneLoaded_loop", "danger_zone_loaded"),
        ]
        for raw, effect_key in generic_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
                self.assertEqual(translation["arguments"]["effect"]["key"], effect_key)
                self.assertNotRegex(display["zh_cn_detail"], r"状态启动|音效事件|尚无中文短标签")

        elise, elise_display = self.render("Play_sfx_Elise_Skin05_Human_Emote_Joke_oba")
        self.assertEqual(elise["frame_type"], "champion_action_event")
        self.assertEqual(elise["arguments"]["action"]["key"], "human_form_joke")
        self.assertIn("人形态", elise_display["zh_cn_short"])

    def test_strawberry_npc_actor_role_and_ultimate_are_preserved(self) -> None:
        """Strawberry_NPC_英雄 是 PVE NPC 演员，不得误标为 Boss 或丢掉大招。"""

        record = enriched_record("Play_sfx_Strawberry_NPC_MissFortune_Ultimate_cast")
        translation, display = self.render("Play_sfx_Strawberry_NPC_MissFortune_Ultimate_cast")

        self.assertEqual(record.semantic_slots["mode_actor"]["alias"], "MissFortune")
        self.assertEqual(record.semantic_slots["mode_actor_role"], "npc")
        self.assertEqual(translation["arguments"]["context"]["key"], "strawberry")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
        self.assertIn("PVE 模式", display["zh_cn_detail"])
        self.assertNotIn("NPC", display["zh_cn_detail"])

        enemy_record = enriched_record("Play_sfx_Strawberry_Enemy_ShacoClone_Death")
        enemy_translation, enemy_display = self.render("Play_sfx_Strawberry_Enemy_ShacoClone_Death")
        self.assertEqual(enemy_record.semantic_slots["mode_actor"]["alias"], "Shaco")
        self.assertEqual(enemy_record.semantic_slots["mode_actor_role"], "enemy")
        self.assertEqual(enemy_translation["arguments"]["ability_slot"]["key"], "r")
        self.assertIn("分身", enemy_display["zh_cn_detail"])

    def test_engineering_spell_owners_and_reviewed_component_qualifiers_are_preserved(self) -> None:
        """技能 bank owner 必须回归真实英雄，已审阅光束段位必须进入 qualifier。"""

        spell_cases = [
            (
                "Play_sfx_LilliaERollingMissile_OnMissileLaunch",
                "Lillia",
                "e",
                "swirlseed_rolling_missile",
            ),
            (
                "Play_sfx_OrianaRedact_OnMissileLaunch",
                "Orianna",
                "e",
                "protect_command",
            ),
            (
                "Play_sfx_OrianaSkin06_OrianaRedactCommand_OnCast",
                "Orianna",
                "e",
                "protect_command",
            ),
        ]
        for raw, champion, slot, component in spell_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["champion"]["alias"], champion)
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], slot)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component)
                self.assertNotRegex(display["zh_cn_detail"], r"LilliaE|Oriana|音效事件|尚无中文短标签")

        lux, lux_display = self.render("Play_sfx_LuxSkin19_LuxMaliceCannonBeam_beammiddle")
        self.assertEqual(lux["arguments"]["ability_slot"]["key"], "r")
        self.assertEqual(lux["arguments"]["qualifier"]["key"], "beammiddle")
        self.assertIn("光束中段", lux_display["zh_cn_short"])

    def test_contextual_alias_required_tokens_prevent_neighbor_cue_hijacking(self) -> None:
        """局部主题/墙体 token 不得覆盖同 owner 下的其它技能 cue。"""

        cases = [
            (
                "Play_sfx_Darius_NorseKing_DariusCleave_OnCast",
                "q",
                "cleave",
            ),
            (
                "Play_sfx_KSanteSkin01_KSanteR_wall_exit_groundslam_windup_cast",
                "r",
                None,
            ),
            (
                "Play_sfx_Ruby_Curse_AniviaEgg_Rebirth_cast",
                "p",
                None,
            ),
        ]
        forbidden_components = {"norse_king_recall", "wall_travel", "curse_egg_timer"}
        for raw, expected_slot, expected_component in cases:
            with self.subTest(raw=raw):
                record = enriched_record(raw)
                aliases = [record.semantic_slots.get("contextual_alias"), record.semantic_slots.get("sfx_alias")]
                components = {alias.get("component") for alias in aliases if isinstance(alias, dict)}

                self.assertEqual(record.semantic_slots.get("spell_key"), expected_slot)
                self.assertTrue(components.isdisjoint(forbidden_components))
                if expected_component is not None:
                    self.assertIn(expected_component, components)

    def test_reviewed_action_and_spell_sfx_keep_tail_without_fake_stage(self) -> None:
        """动作/技能尾段应结构化；资源尾号不得一律解释成技能段数。"""

        shyvana, shyvana_display = self.render("Play_sfx_ShyvanaSkin04_ShyvanaDragonDance3D_leadin")
        self.assertEqual(shyvana["frame_type"], "champion_action_event")
        self.assertEqual(shyvana["arguments"]["action"]["key"], "dragon_form_dance")
        self.assertEqual(shyvana["arguments"]["phase"]["key"], "leadin")
        self.assertEqual(shyvana_display["zh_cn_short"], "龙形态舞蹈音效引入")

        spell_cases = [
            (
                "Play_sfx_TeemoSkin04_TeemoRBounce_missilecast",
                "r",
                "noxious_trap_bounce",
                "missile_cast",
            ),
            (
                "Stop_sfx_PantheonSkin06_PantheonRmissile3_OnMissileLaunch",
                "r",
                "grand_starfall_missile",
                "missile_launch",
            ),
            (
                "Play_sfx_AniviaSkin05_FlashFrostSpell_explode",
                "q",
                "flash_frost",
                "detonate",
            ),
            (
                "Play_sfx_TaliyahSkin01_TaliyahQMisBig_missilecast",
                "q",
                "threaded_volley_empowered_missile",
                "missile_cast",
            ),
            (
                "Play_sfx_AkshanSkin01_AkshanWRevive_buffcast",
                "w",
                "going_rogue_revive",
                "cast",
            ),
        ]
        for raw, slot, component, phase in spell_cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["arguments"]["ability_slot"]["key"], slot)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component)
                self.assertEqual(translation["arguments"]["phase"]["key"], phase)
                self.assertNotRegex(display["zh_cn_detail"], r"音效事件|尚无中文短标签")

        pantheon = enriched_record("Stop_sfx_PantheonSkin06_PantheonRmissile3_OnMissileLaunch")
        self.assertNotIn("spell_stage", pantheon.semantic_slots)

    def test_engineering_owners_and_ruby_actor_resolve_to_real_champions(self) -> None:
        """召唤物/技能 bank owner 与 Ruby 前缀不得泄漏成英雄名。"""

        azir, azir_display = self.render("Play_sfx_AzirSoldierSkin01_AzirSoldierMissile_OnMissileLaunch")
        self.assertEqual(azir["arguments"]["champion"]["alias"], "Azir")
        self.assertEqual(azir["arguments"]["ability_slot"]["key"], "w")
        self.assertNotIn("AzirSoldier", azir_display["zh_cn_detail"])

        zoe, zoe_display = self.render("Play_sfx_ZoeW_disablebypass")
        self.assertEqual(zoe["arguments"]["champion"]["alias"], "Zoe")
        self.assertEqual(zoe["arguments"]["effect"]["key"], "w_audio_bypass_disabled")
        self.assertNotIn("ZoeW", zoe_display["zh_cn_detail"])

        wukong, wukong_display = self.render("Play_sfx_Ruby_WukongSpinToWin_OnBuffActivate")
        self.assertEqual(wukong["arguments"]["context"]["key"], "ruby")
        self.assertEqual(wukong["arguments"]["champion"]["alias"], "MonkeyKing")
        self.assertEqual(wukong["arguments"]["ability_slot"]["key"], "r")
        self.assertIn("末日人机", wukong_display["zh_cn_detail"])
        self.assertNotIn("Ruby", wukong_display["zh_cn_detail"])

    def test_gnar_big_cues_render_specific_spell_attack_and_action_meaning(self) -> None:
        """纳尔巨型形态事件必须展示 Q/被动、普通攻击或具体动作，不得只剩阶段词。"""

        cases = [
            (
                "Play_sfx_GnarSkin15_GnarBigQMissile_OnMissileLaunch",
                "ability_cast_event",
                "Q技能投掷顽石飞弹弹道发射",
            ),
            (
                "Play_sfx_GnarSkin15_GnarTransformSoon_OnBuffActivate",
                "ability_state_change",
                "被动技能狂怒基因即将变身生效",
            ),
            (
                "Play_sfx_GnarSkin15_GnarBigAttackTower_OnCast",
                "champion_attack_event",
                "巨型形态对防御塔的普通攻击出手",
            ),
            (
                "Play_sfx_GnarSkin15_GnarBig_Dance3D_buffactivate",
                "skin_action_state",
                "巨型形态舞蹈音效开始",
            ),
            (
                "Play_sfx_GnarSkin22_GnarBig_Recall3D_buffactivate",
                "skin_recall_state",
                "巨型形态回城音效开始",
            ),
            (
                "Play_sfx_GnarSkin15_GnarBig_Dance3D_loop",
                "skin_action_state",
                "巨型形态舞蹈音效循环",
            ),
            (
                "Play_sfx_GnarSkin15_GnarBig_Recall_leadin",
                "skin_recall_state",
                "巨型形态回城音效引入",
            ),
            (
                "Play_sfx_GnarBig_Dance3D_buffactivate",
                "skin_action_state",
                "巨型形态舞蹈音效开始",
            ),
            (
                "Play_sfx_GnarBig_Recall_leadin",
                "champion_action_event",
                "巨型形态回城音效引入",
            ),
            (
                "Play_sfx_GnarSkin13_GnarLittle_Recall_leadin",
                "skin_recall_state",
                "小型形态回城音效引入",
            ),
            (
                "Play_vo_GnarBigSkin13_dance_buffactivate",
                "voice_event",
                "巨型形态跳舞语音开始",
            ),
        ]
        for raw, frame_type, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], frame_type)
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotRegex(display["zh_cn_detail"], r"Gnar|Skin\d+|尚无中文短标签")

        attack, attack_display = self.render("Play_sfx_GnarSkin15_GnarBigAttackTower_OnCast")
        self.assertNotIn("ability_slot", attack["arguments"])
        self.assertNotIn("被动技能", attack_display["zh_cn_detail"])

    def test_composite_action_tails_render_as_actions_with_lifecycle(self) -> None:
        """音效层缩写与 Ruby 主体解析都不能把动作误写成组合技或丢掉分段。"""

        cases = [
            (
                "Play_sfx_NamiSkin51_Dance3D_ee_loop_buffactivate",
                "champion_action_event",
                "舞蹈音效循环",
            ),
            (
                "Play_sfx_Ruby_Lux_Dance3D_loop",
                "champion_action_event",
                "末日人机 舞蹈音效循环",
            ),
            (
                "Play_sfx_Ruby_Boss_FNVeigar_Joke3D_loop",
                "champion_action_event",
                "末日人机 玩笑音效循环",
            ),
        ]
        for raw, frame_type, expected_short in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], frame_type)
                self.assertEqual(translation["arguments"]["phase"]["key"], "loop")
                self.assertEqual(display["zh_cn_short"], expected_short)
                self.assertNotIn("组合技", display["zh_cn_detail"])

    def test_aatrox_q3_renders_stage(self) -> None:
        translation, display = self.render("Play_sfx_Aatrox_AatroxQ3_OnCast_all")

        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "3")
        self.assertIn("Q技能第3段释放", display["zh_cn_detail"])

    def test_stage_only_voice_event_renders_stage_without_fabricated_phase(self) -> None:
        translation, display = self.render("Play_vo_WarwickSkin16_WarwickE2")

        self.assertEqual(translation["frame_type"], "champion_ability_stage_event")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "e")
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertNotIn("phase", translation["arguments"])
        self.assertEqual(display["zh_cn_short"], "E技能第2段")
        self.assertEqual(display["zh_cn_detail"], "沃里克 源计划：末日 E技能第2段")

    def test_numeric_spell_rank_voice_uses_rank_frame_not_stage_frame(self) -> None:
        translation, display = self.render("Play_vo_Kayle_Spell2DPRank2")

        self.assertEqual(translation["frame_type"], "champion_ability_rank_event")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "p")
        self.assertEqual(translation["arguments"]["ability_slot"]["labels"]["zh_CN"], "被动技能")
        self.assertEqual(translation["arguments"]["rank"]["value"], "2")
        self.assertNotIn("ability_stage", translation["arguments"])
        self.assertEqual(display["zh_cn_short"], "被动技能2级")
        self.assertEqual(display["zh_cn_detail"], "凯尔 被动技能2级")

    def test_owner_prefixed_long_basic_uses_long_range_attack_frame(self) -> None:
        translation, display = self.render("Play_vo_SennaSkin56_Senna3DLongBasic")

        self.assertEqual(translation["frame_type"], "champion_attack_event")
        self.assertEqual(translation["arguments"]["attack"]["key"], "ranged_basic_attack")
        self.assertEqual(display["zh_cn_short"], "远程普通攻击")
        self.assertEqual(display["zh_cn_detail"], "赛娜 北极星神 远程普通攻击")

    def test_contextual_word_stage_is_visible_with_component_name(self) -> None:
        translation, display = self.render("Play_vo_LeeSinSkin31_BlindMonkQOne_cast3D")

        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "1")
        self.assertIn("Q技能天音波第1段释放", display["zh_cn_detail"])

    def test_ruby_separated_spell_key_stage_is_visible(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_Ziggs_E2_OnBuffActivate")

        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertEqual(display["zh_cn_short"], "末日人机 E技能第2段生效")
        self.assertEqual(display["zh_cn_detail"], "末日人机 吉格斯 E技能第2段生效")

    def test_stage_only_spell_keeps_parsed_component_name(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_XerathArcanoPulse2_missilelaunchextended")

        self.assertEqual(translation["frame_type"], "champion_ability_stage_event")
        self.assertEqual(translation["arguments"]["ability_component"]["key"], "arcanopulse")
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertEqual(display["zh_cn_short"], "末日人机 Q技能奥能脉冲第2段")
        self.assertEqual(display["zh_cn_detail"], "末日人机 泽拉斯 Q技能奥能脉冲第2段")

    def test_whole_spell_audio_keeps_parsed_component_name(self) -> None:
        translation, display = self.render("Play_sfx_TwistedFateSkin25_Gate_marker")

        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["ability_component"]["key"], "portal")
        self.assertIn("R技能传送门整段音效", display["zh_cn_detail"])

    def test_contextual_skin_actions_are_not_rendered_as_passive_skills(self) -> None:
        """形态切换与皮肤状态动作应直译动作含义，不得显示为被动技能。"""

        cases = [
            ("Play_vo_LuxSkin07_Air_Unique2DTransform", "elementalist_transform", "元素形态切换"),
            ("Play_vo_KatarinaSkin29_Toggle3D", "toggle_state", "切换状态"),
            ("Play_sfx_ApheliosSkin09_Lunari_Thrust3D_buffactivate", "lunari_thrust", "皎月教突刺开始"),
        ]
        for raw, component, expected in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_action_event")
                self.assertEqual(translation["arguments"]["action"]["key"], component)
                self.assertIn(expected, display["zh_cn_detail"])
                self.assertNotIn("被动技能", display["zh_cn_detail"])

    def test_lunari_first_encounter_keeps_interaction_meaning(self) -> None:
        """同名动作别名不得吞掉首次遭遇皎月教这一交互语义。"""

        translation, display = self.render("Play_vo_Aphelios_FirstEncounter2DLunari")

        self.assertEqual(translation["frame_type"], "champion_interaction_cue")
        self.assertEqual(translation["arguments"]["counterpart"]["labels"]["zh_CN"], "皎月教")
        self.assertIn("初遇", display["zh_cn_detail"])
        self.assertIn("皎月教", display["zh_cn_detail"])
        self.assertNotIn("被动技能", display["zh_cn_detail"])

    def test_component_only_spell_events_keep_skill_and_component_meaning(self) -> None:
        """无标准相位的真技能组件仍须保留槽位与组件中文。"""

        cases = [
            (
                "Play_sfx_DariusSkin04_DariusHemoMax_loop",
                "p",
                "hemorrhage_max_stack",
                "被动技能出血最大层数循环",
            ),
            (
                "Play_vo_VayneSkin67_VayneInquisition_EmpoweredStart",
                "r",
                "ultimate_cast",
                "R技能终极审判强化开始",
            ),
        ]
        for raw, spell_key, component, expected in cases:
            with self.subTest(raw=raw):
                translation, display = self.render(raw)

                self.assertEqual(translation["frame_type"], "champion_ability_component_event")
                self.assertEqual(translation["arguments"]["ability_slot"]["key"], spell_key)
                self.assertEqual(translation["arguments"]["ability_component"]["key"], component)
                self.assertIn(expected, display["zh_cn_detail"])

        darius, _display = self.render("Play_sfx_DariusSkin04_DariusHemoMax_loop")
        self.assertEqual(darius["arguments"]["phase"]["key"], "loop")

        vayne, _display = self.render("Play_vo_VayneSkin67_VayneInquisition_EmpoweredStart")
        self.assertEqual(vayne["arguments"]["qualifier"]["key"], "empowered")
        self.assertEqual(vayne["arguments"]["phase"]["key"], "start")

    def test_draven_attack_qualifier_is_not_misread_as_spell_sequence(self) -> None:
        translation, display = self.render("Play_sfx_Draven_DravenAttackP_RQ_OnHit")

        self.assertEqual(translation["frame_type"], "champion_attack_event")
        self.assertEqual(translation["arguments"]["attack"]["key"], "draven_axe_attack")
        self.assertNotIn("ability_sequence", translation["arguments"])
        self.assertEqual(display["zh_cn_short"], "投斧普攻（右手旋转飞斧）命中")
        self.assertNotIn("P技能", display["zh_cn_detail"])

    def test_ruby_xerath_keeps_mode_actor_spell_and_stage(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_XerathArcanopulse2_OnCast")

        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Xerath")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "q")
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertEqual(display["zh_cn_short"], "末日人机 Q技能奥能脉冲第2段释放")
        self.assertIn("末日人机 泽拉斯 Q技能奥能脉冲第2段释放", display["zh_cn_detail"])

    def test_ruby_action_frame_keeps_mode_context_even_when_frame_did_not_declare_it(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_Blitzcrank_Dance3D_buffactivate")

        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertIn("末日人机", display["zh_cn_short"])
        self.assertIn("末日人机", display["zh_cn_detail"])
        self.assertNotIn("Ruby", display["zh_cn_detail"])

    def test_ruby_separated_actor_attack_uses_family_as_attack_fallback(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_Ziggs_BasicAttack2_OnCast")

        self.assertEqual(translation["frame_type"], "champion_attack_event")
        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Ziggs")
        self.assertEqual(translation["arguments"]["attack"]["key"], "basic_attack")
        self.assertEqual(translation["arguments"]["attack"]["variant"], 2)
        self.assertEqual(display["zh_cn_short"], "末日人机 普通攻击2出手")
        self.assertIn("末日人机 吉格斯 普通攻击2出手", display["zh_cn_detail"])

    def test_ruby_boss_engineering_prefix_resolves_veigar_as_actor(self) -> None:
        translation, display = self.render("Play_sfx_Ruby_Boss_FNVeigar_AttackInhib_missilelaunch")

        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Veigar")
        self.assertIn("末日人机 维迦", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"Ruby|FNVeigar")

    def test_champion_bank_identity_normalizes_companion_form_owner_and_skin(self) -> None:
        _translation, display = self.render(
            "Play_sfx_AnnieTibbersSkin10_AnnieTibbersBasicAttack2_OnCast",
            source_path="raw/events/champions/1.txt",
        )

        self.assertIn("安妮", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"AnnieTibbers|Skin10")

    def test_missing_counterpart_skin_uses_champion_name_and_explicit_placeholder(self) -> None:
        translation, display = self.render("Play_vo_AmbessaSkin01_FirstEncounter3DKatarinaSkin58")

        self.assertTrue(translation["arguments"]["counterpart"]["fallback"])
        self.assertIn("卡特琳娜（未收录皮肤）", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"Katarina|Skin58")

    def test_curated_archetype_context_does_not_leak_champion_alias(self) -> None:
        _translation, display = self.render("Play_vo_Ambessa_FirstEncounter3DAmbessasType")

        self.assertIn("安蓓萨偏好的类型", display["zh_cn_detail"])
        self.assertNotIn("Ambessa's Type", display["zh_cn_detail"])

    def test_buff_cast_spell_stage_is_rendered_as_cast_stage(self) -> None:
        translation, display = self.render("Play_sfx_IllaoiSkin01_IllaoiR2_OnBuffCast")

        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertEqual(translation["arguments"]["phase"]["key"], "cast")
        self.assertIn("R技能第2段释放", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"Illaoi|Skin01")

    def test_odyssey_augments_keeps_mechanism_and_canonical_yasuo_name(self) -> None:
        translation, display = self.render("Play_sfx_odysseyaugments_yasuorknockup_onbuffactivate")

        self.assertEqual(translation["arguments"]["context"]["key"], "odyssey_augments")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Yasuo")
        self.assertIn("奥德赛增幅芯片 亚索", display["zh_cn_detail"])
        self.assertNotRegex(display["zh_cn_detail"], r"\b[Yy]asuo\b|odysseyaugments")

    def test_odyssey_yasuo_combo_keeps_sequence_stage_context_and_hit_phase(self) -> None:
        """奥德赛增幅的 QE1/2/3 必须保留英雄、组合、段数与命中语义。"""

        for stage in (1, 2, 3):
            raw = f"Play_sfx_odysseyaugments_yasuoqe{stage}_onhit"
            with self.subTest(stage=stage):
                translation, display = self.render(raw)
                self.assertEqual(translation["frame_type"], "champion_ability_sequence_event")
                self.assertEqual(translation["arguments"]["context"]["key"], "odyssey_augments")
                self.assertEqual(translation["arguments"]["champion"]["alias"], "Yasuo")
                self.assertEqual(translation["arguments"]["ability_sequence"]["value"], "q+e")
                self.assertEqual(translation["arguments"]["ability_sequence"]["sequence"], ["q", "e"])
                self.assertEqual(translation["arguments"]["ability_stage"]["value"], str(stage))
                self.assertEqual(translation["arguments"]["phase"]["key"], "hit")
                self.assertEqual(
                    display["zh_cn_short"],
                    f"奥德赛增幅芯片 Q+E组合技第{stage}段命中",
                )
                self.assertEqual(
                    display["zh_cn_detail"],
                    f"奥德赛增幅芯片 亚索 Q+E组合技第{stage}段命中",
                )
                self.assertNotRegex(display["zh_cn_detail"], r"[Yy]asuo|odysseyaugments")

    def test_zyra_attack_component_is_visible_without_becoming_passive_skill(self) -> None:
        translation, display = self.render("Play_sfx_Zyra_hellvine_ZyraBasicAttack_OnHit")

        self.assertEqual(translation["frame_type"], "champion_attack_event")
        self.assertEqual(translation["arguments"]["attack"]["key"], "hellvine_basic_attack")
        self.assertEqual(display["zh_cn_short"], "地狱藤蔓普攻命中")
        self.assertNotIn("P技能", display["zh_cn_detail"])

    def test_ranged_attack_form_is_visible_without_false_r_skill(self) -> None:
        translation, display = self.render("Play_sfx_JayceSkin03_JayceRangedAttack2_OnCast")

        self.assertEqual(translation["arguments"]["attack"]["key"], "ranged_basic_attack")
        self.assertEqual(translation["arguments"]["attack"]["variant"], 2)
        self.assertEqual(display["zh_cn_short"], "远程普通攻击2出手")
        self.assertNotIn("R技能", display["zh_cn_detail"])


class MapArgumentDisplayLabelsTests(unittest.TestCase):
    """验证 map argument 真名解析与词表外 id 的占位 fallback（T3.3）。"""

    def test_curated_map_renders_real_chinese_and_english_name(self) -> None:
        """③: map11 的 zh/en 分别命中词表与 reference，来源写入 provenance 且不落 fallback。"""

        argument = map_argument(11)
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertTrue(argument["labels"]["zh_CN"])
        self.assertTrue(argument["labels"]["en_US"])
        self.assertFalse(argument["fallback"])
        self.assertEqual(argument["provenance"]["zh_source"], "display_labels.maps")
        self.assertEqual(argument["provenance"]["en_source"], "reference_maps")

    def test_unknown_map_id_keeps_placeholder(self) -> None:
        """④: 词表与 reference 都没有的 id（如 999）应保留 Map999 占位，不得瞎猜。"""

        argument = map_argument(999)
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertEqual(argument["labels"]["zh_CN"], "Map999")
        self.assertEqual(argument["labels"]["en_US"], "Map999")
        self.assertTrue(argument["fallback"])

    def test_curated_map_end_to_end_through_gameplay_phase_control_frame(self) -> None:
        """map11 经完整 gameplay_phase_control frame 构建后 map argument 同样命中词表不落 fallback。"""

        record = enriched_record("map11_FirstBlood_phase_select")
        translation = build_translation(record, reference_translation_context())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "gameplay_phase_control")
        args = translation["arguments"]
        self.assertFalse(args["map"]["fallback"])


if __name__ == "__main__":
    unittest.main()


class SkinLabelAliasCaseTest(unittest.TestCase):
    """skin/champion label 查表对 alias 大小写变体的兼容。

    事件名里的英雄 alias 与 reference alias 存在历史大小写分叉
    （BelVeth/Belveth、LeBlanc/Leblanc、RekSai/Reksai、Orianna/Oriana 等），
    两侧都是各自来源的既有事实，不改数据、由查表侧兼容。
    """

    def test_skin_label_matches_alias_case_variant(self) -> None:
        """reference 用 Belveth 时，事件 alias BelVeth 应命中同一皮肤 label。"""

        context = TranslationBuildContext(
            skin_labels={("Belveth", "Skin10"): {"zh_CN": "腥红之月 卑尔维斯", "en_US": "Blood Moon Bel'Veth"}}
        )
        self.assertEqual(
            context.skin_label("BelVeth", "Skin10"), {"zh_CN": "腥红之月 卑尔维斯", "en_US": "Blood Moon Bel'Veth"}
        )
        self.assertEqual(context.skin_label("Belveth", "Skin10"), context.skin_label("BELVETH", "Skin10"))

    def test_skin_label_returns_none_for_unknown_alias(self) -> None:
        """无对应条目时仍返回 None，不误命中其他英雄。"""

        context = TranslationBuildContext(skin_labels={("Belveth", "Skin10"): {"zh_CN": "x"}})
        self.assertIsNone(context.skin_label("Ahri", "Skin10"))
        self.assertIsNone(context.skin_label("Belveth", "Skin99"))

    def test_champion_label_matches_alias_case_variant(self) -> None:
        """英雄 label 查表同样兼容大小写变体。"""

        context = TranslationBuildContext(champion_labels={"Leblanc": {"zh_CN": "乐芙兰"}})
        self.assertEqual(context.champion_label("LeBlanc"), {"zh_CN": "乐芙兰"})
        self.assertIsNone(context.champion_label("Ahri"))

    def test_champion_identity_resolves_alias_case_and_numeric_id(self) -> None:
        context = TranslationBuildContext(
            champion_labels={"LeBlanc": {"zh_CN": "乐芙兰"}},
            champion_ids={7: "LeBlanc"},
        )

        self.assertEqual(context.canonical_champion_alias("leblanc"), "LeBlanc")
        self.assertEqual(context.champion_alias_for_id(7), "LeBlanc")
        self.assertIsNone(context.champion_alias_for_id(999))
