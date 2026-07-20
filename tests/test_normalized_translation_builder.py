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
                }
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
        self.assertEqual(args["phase"]["key"], "state_deactivate")


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

    def test_tibbers_basic_attack_on_hit_builds_ability_frame(self) -> None:
        """OnHit(phase=hit)的提伯斯普攻应能建 ability_state_change frame，皮肤/相位/组件角色齐备。"""
        record = enriched_record("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack_OnHit")
        frame = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(frame)
        self.assertEqual(frame["frame_type"], "ability_state_change")
        args = frame["arguments"]
        self.assertIn("phase", args)
        self.assertIn("ability_component", args)
        self.assertIn("skin", args)


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
    """验证 voice_event 对 sfx 资产的承接与 asset_kind 选词回落逻辑。"""

    def test_sfx_voice_families_use_sound_effect_word(self) -> None:
        """sfx 记录同样进入 voice_event frame，action key 保持家族键。"""

        cases = [
            "Stop_sfx_AuroraSkin11_Dance3D_leadin",
            "Play_sfx_JayceSkin05_Dance3D_buffactivate_loop",
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                self.assertEqual(translation["arguments"]["action"]["key"], "dance")

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
        """qualifier 特例（long/Cancel/Ward/Potion/Open/Close/级别词）仍建 voice_event，action key 保持家族键。"""

        cases = [
            ("Play_vo_AatroxSkin01_Move2DLong", "move"),
            ("Play_vo_Ornn_Recall3DCancel", "recall"),
            ("Play_vo_Azir_UseItem2DWard", "use_item"),
            ("Play_vo_AhriSkin14_UseItem2DPotion", "use_item"),
            ("Play_vo_CaitlynSkin11_UseItem2DHealthPotion", "use_item"),
            ("Play_vo_AatroxSkin33_Shop2DOpen", "shop"),
            ("Play_vo_Taliyah_Shop2DClose", "shop"),
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

    def test_uncovered_qualifier_kept_lossless_out_of_display(self) -> None:
        """未译 qualifier（ping 细分词/装备名）无损进 qualifier argument，不进双档文案。"""

        cases = [
            ("Play_vo_AatroxSkin33_Ping2DOnMyWay", "OnMyWay"),
            ("Play_vo_AsheSkin08_BuyItem2DInfinityEdge", "InfinityEdge"),
        ]
        for raw, token in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                qualifier = translation["arguments"]["qualifier"]
                self.assertEqual(qualifier["labels"]["en_US"], token)
                self.assertEqual(qualifier["token"], token)

                display = build_compat_display(translation, raw_event_name=raw)
                self.assertNotIn(token, display["zh_cn_detail"])
                self.assertNotIn(token, display["zh_cn_short"])

    def test_move_context_qualifier_kept_lossless(self) -> None:
        """move 位置/状态 qualifier（River 等）不吞核心家族，context 无损进 argument。"""

        translation = build_translation(
            enriched_record("Play_vo_AatroxSkin33_Move2DRiver"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertEqual(args["action"]["key"], "move")
        self.assertEqual(args["qualifier"]["labels"]["en_US"], "River")
        self.assertEqual(args["qualifier"]["key"], "river")

    def test_generic_qualifier_not_emitted(self) -> None:
        """General/Standard 等通用 qualifier 不产生 qualifier argument。"""

        for raw in ["Play_vo_AatroxSkin33_Recall3DGeneral", "Play_vo_AatroxSkin33_Move2DStandard"]:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertNotIn("qualifier", translation["arguments"])

    def test_non_vo_records_build_no_frame_and_keep_legacy_display(self) -> None:
        """非 vo 记录不建 frame（维持 Tier3 兜底），legacy display 保持 Tier3 结构。"""

        from lol_event_semantics.normalized_output import build_normalized_record

        record = enriched_record("Play_sfx_AatroxSkin07_Death3D_cast")
        self.assertIsNone(build_translation(record, TranslationBuildContext()))

        normalized = build_normalized_record(record)
        display = normalized["display"]
        assert isinstance(display, dict)
        self.assertEqual(display["tier"], 3)
        self.assertIsNone(display["zh_cn_short"])
        self.assertTrue(display["zh_cn_detail"])

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

    def test_ultimate_spellbook_not_claimed_by_ability_cast(self) -> None:
        """终极魔典借用大招不应被 ability_cast_event 抢占（留给 borrowed frame）。"""

        record = enriched_record("Play_vo_UltBook_AhriR_OnCast")
        translation = build_translation(record, TranslationBuildContext())

        if translation is not None:
            self.assertNotEqual(translation["frame_type"], "ability_cast_event")


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

    def test_system_scope_hud_base_builds_no_frame(self) -> None:
        """hud_base 系统 UI 音（system_sfx，owner 带伪 champion alias）被门禁排除。"""

        record = enriched_record("Play_sfx_hud_base_MissFortune_Skin16_GearUI_Available")
        self.assertEqual(record.semantic_slots.get("sfx_scope"), "system_sfx")
        self.assertIsNone(build_translation(record, TranslationBuildContext()))

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

    def test_non_phase_tails_build_no_frame(self) -> None:
        """批次6a legacy 锁：非相位尾段（音层/姿势/变体号/多段链）不建 frame。"""

        for raw in [
            "Play_sfx_TristanaSkin33_Base_Death3D_body",
            "Play_sfx_TristanaSkin33_Base_Death3D_gun",
            "Play_sfx_TaricSkin18_Base_Joke3D_pose1",
            "Play_sfx_TaricSkin18_Base_Joke3D_open",
            "Play_sfx_TristanaSkin51_Base_Recall_A",
            "Play_sfx_TaricSkin18_Base_Dance3D_crystal_loop_buffactivate2",
        ]:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), TranslationBuildContext()))

    def test_out_of_vocab_action_with_no_tail_builds_no_frame(self) -> None:
        """批次6a legacy 锁：表外动作词（idle/meow 等）即便无尾段也不建 frame。"""

        for raw in ["Play_sfx_YorickSkin12_Base_idle", "Play_sfx_YuumiSkin39_Base_meow_01"]:
            with self.subTest(raw=raw):
                self.assertIsNone(build_translation(enriched_record(raw), TranslationBuildContext()))


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
                self.assertEqual(translation["frame_type"], "ability_state_change")
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
        """spell_rank 承载变体号（Tibbers 普攻2 等）且 alias 非 Rank 结尾时不得误标技能等级。"""

        record = enriched_record("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack2_OnCast")
        self.assertEqual(record.semantic_slots.get("spell_rank"), 2)
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("rank", translation["arguments"])


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
        translation = build_translation(record, TranslationBuildContext())
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
        self.assertEqual(context.skin_label("BelVeth", "Skin10"), {"zh_CN": "腥红之月 卑尔维斯", "en_US": "Blood Moon Bel'Veth"})
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
