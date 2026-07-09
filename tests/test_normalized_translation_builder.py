"""normalized translation builder 测试。"""

from __future__ import annotations

import json
import unittest
from functools import lru_cache
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
from lol_event_semantics.normalized_translation_builder import ChampionLabelEntry, build_translation
from lol_event_semantics.normalized_translation_model import TranslationBuildContext

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
        self.assertEqual(args["ability_slot"]["labels"]["zh_CN"], "W技能")
        self.assertEqual(args["ability_component"]["key"], "passive_defense")
        self.assertEqual(args["ability_component"]["labels"]["zh_CN"], "被动防御")
        self.assertEqual(args["phase"]["key"], "state_deactivate")
        self.assertEqual(args["phase"]["labels"]["zh_CN"], "状态结束")


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


@lru_cache(maxsize=1)
def reference_index() -> ReferenceIndex:
    """返回测试共享的 reference index。"""

    return ReferenceIndex.from_reference_collection("reference")


class NormalizedTranslationBuilderAnnieTibbersTests(unittest.TestCase):
    """验证 Annie 提伯斯普攻 OnHit(phase=hit) frame 构建。"""

    def test_tibbers_basic_attack_on_hit_builds_ability_frame(self) -> None:
        """OnHit(phase=hit)的提伯斯普攻应能建 ability_state_change frame 且带皮肤与精确组件。"""
        record = enriched_record("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack_OnHit")
        frame = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(frame)
        self.assertEqual(frame["frame_type"], "ability_state_change")
        args = frame["arguments"]
        self.assertEqual(args["phase"]["labels"]["zh_CN"], "命中")
        self.assertEqual(args["ability_component"]["labels"]["zh_CN"], "提伯斯普攻")
        self.assertIn("skin", args)


class NormalizedTranslationBuilderShellVerbTests(unittest.TestCase):
    """验证 Stop/Mute 壳动词进入 translation frame（不得反转为播放语义）。"""

    def test_stop_event_attaches_playback_argument(self) -> None:
        """Stop_ 事件应附加 playback=停止 argument。"""

        translation = build_translation(
            enriched_record("Stop_sfx_AuroraSkin11_Dance3D_leadin"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        args = translation["arguments"]
        self.assertIn("playback", args)
        self.assertEqual(args["playback"]["key"], "stop")
        self.assertEqual(args["playback"]["labels"]["zh_CN"], "停止")

    def test_stop_spell_event_attaches_playback_argument(self) -> None:
        """Stop_ 技能事件同样带 playback，覆盖非 voice frame 路径。"""

        translation = build_translation(
            enriched_record("Stop_sfx_AurelionSolSkin21_AurelionSolQ_charge"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["playback"]["labels"]["zh_CN"], "停止")

    def test_play_event_has_no_playback_argument(self) -> None:
        """普通 Play_ 事件不附加 playback argument。"""

        translation = build_translation(
            enriched_record("Play_vo_AsheSkin06_Attack2DGeneral"), TranslationBuildContext()
        )
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertNotIn("playback", translation["arguments"])

    def test_mute_verb_maps_to_mute_label(self) -> None:
        """mute 壳动词映射为静音。"""

        from lol_event_semantics.normalized_translation_builder import playback_argument

        argument = playback_argument("mute")
        self.assertIsNotNone(argument)
        assert argument is not None
        self.assertEqual(argument["labels"]["zh_CN"], "静音")


class NormalizedTranslationBuilderVoiceEventTests(unittest.TestCase):
    """验证 voice_event frame。"""

    def test_voice_families_build_voice_event(self) -> None:
        """各 VO 情绪/动作家族构建 voice_event frame，action label 带语音后缀。"""

        cases = [
            ("Play_vo_AsheSkin06_Attack2DGeneral", "攻击语音"),
            ("Play_vo_AsheSkin06_Taunt2DGeneral", "嘲讽语音"),
            ("Play_vo_AsheSkin06_Joke2DGeneral", "玩笑语音"),
            ("Play_vo_AsheSkin06_Laugh2DGeneral", "大笑语音"),
            ("Play_vo_AsheSkin06_Dance2DGeneral", "跳舞语音"),
        ]
        for raw, action_zh in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                args = translation["arguments"]
                self.assertEqual(args["champion"]["alias"], "Ashe")
                self.assertEqual(args["skin"]["token"], "Skin06")
                self.assertEqual(args["action"]["labels"]["zh_CN"], action_zh)


class NormalizedTranslationBuilderVoiceAssetKindTests(unittest.TestCase):
    """验证 voice_event action 按 shell.asset_kind 选词（sfx≠语音）。"""

    def test_sfx_voice_families_use_sound_effect_word(self) -> None:
        """sfx 事件按音效措辞，不得误标语音。"""

        cases = [
            ("Stop_sfx_AuroraSkin11_Dance3D_leadin", "跳舞音效"),
            ("Play_sfx_JayceSkin05_Dance3D_buffactivate_loop", "跳舞音效"),
        ]
        for raw, action_zh in cases:
            with self.subTest(raw=raw):
                translation = build_translation(enriched_record(raw), TranslationBuildContext())
                self.assertIsNotNone(translation)
                assert translation is not None
                self.assertEqual(translation["frame_type"], "voice_event")
                self.assertEqual(translation["arguments"]["action"]["labels"]["zh_CN"], action_zh)

    def test_asset_kind_words_cover_audio_types(self) -> None:
        """vo/sfx/mus 各有措辞，未识别类型保守按语音处理。"""

        from lol_event_semantics.normalized_translation_builder import asset_kind_words

        self.assertEqual(asset_kind_words("vo")[0], "语音")
        self.assertEqual(asset_kind_words("sfx")[0], "音效")
        self.assertEqual(asset_kind_words("mus")[0], "音乐")
        self.assertEqual(asset_kind_words(None)[0], "语音")
        self.assertEqual(asset_kind_words("lux")[0], "语音")


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
        """普通技能施放构建 ability_cast_event，含槽位与释放阶段。"""

        record = enriched_record("Play_sfx_Annie_AnnieQ_cast")
        translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Annie")
        self.assertEqual(args["ability_slot"]["value"], "q")
        self.assertEqual(args["ability_slot"]["labels"]["zh_CN"], "Q技能")
        self.assertEqual(args["phase"]["labels"]["zh_CN"], "释放")

    def test_sfx_spell_cast_uses_hint_and_skin(self) -> None:
        """sfx 技能音效用 spell_key_hint 回退并带皮肤。"""

        record = enriched_record("Play_sfx_AkaliSkin02_AkaliRb_OnCast")
        translation = build_translation(record, TranslationBuildContext())

        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        args = translation["arguments"]
        self.assertEqual(args["champion"]["alias"], "Akali")
        self.assertEqual(args["skin"]["token"], "Skin02")
        self.assertEqual(args["ability_slot"]["labels"]["zh_CN"], "R技能")
        self.assertEqual(args["phase"]["labels"]["zh_CN"], "释放")

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
        """回填后 cast3D 推断技能应能建 ability_cast_event frame。"""

        record = enriched_record("Play_vo_Alistar_Headbutt_cast3D")
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["phase"]["labels"]["zh_CN"], "释放")


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
    """验证扩充的 phase 经 translation 双层门渲染为 ability_cast_event（步骤 4）。"""

    def _phase_labels(self, raw: str) -> dict[str, str]:
        record = enriched_record(raw)
        translation = build_translation(record, TranslationBuildContext())
        self.assertIsNotNone(translation)
        assert translation is not None
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        return translation["arguments"]["phase"]["labels"]

    def test_missile_launch_renders(self) -> None:
        labels = self._phase_labels("Play_sfx_AatroxSkin01_AatroxW_OnMissileLaunch")
        self.assertEqual(labels["zh_CN"], "弹道发射")
        self.assertEqual(labels["en_US"], "missile launch")

    def test_end_renders(self) -> None:
        labels = self._phase_labels("Play_sfx_AatroxSkin01_AatroxR_end")
        self.assertEqual(labels["zh_CN"], "结束")
        self.assertEqual(labels["en_US"], "end")

    def test_detonate_renders(self) -> None:
        labels = self._phase_labels("Play_sfx_AatroxSkin01_AatroxR_detonate")
        self.assertEqual(labels["zh_CN"], "引爆")
        self.assertEqual(labels["en_US"], "detonate")

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


if __name__ == "__main__":
    unittest.main()
