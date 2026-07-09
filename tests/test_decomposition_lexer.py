"""事件名拆解词法层测试。"""

from __future__ import annotations

import unittest

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import (
    DecompositionConfidence,
    EventDecompositionRecord,
    LexicalPart,
    UnknownKind,
)


class DecompositionModelTests(unittest.TestCase):
    """验证拆解数据模型可导入和序列化。"""

    def test_record_to_dict_keeps_raw_name(self) -> None:
        record = EventDecompositionRecord(
            schema_version="event-decomposition.v1",
            game_version="16.5",
            raw_event_name="Play_vo_Annie_Move2DStandard",
        )

        data = record.to_dict()

        self.assertEqual(data["raw_event_name"], "Play_vo_Annie_Move2DStandard")
        self.assertEqual(data["confidence"], DecompositionConfidence.LOW.value)

    def test_lexical_part_to_dict(self) -> None:
        part = LexicalPart(kind="dimension_token", raw="2D", value="2D")

        self.assertEqual(part.to_dict(), {"kind": "dimension_token", "raw": "2D", "value": "2D"})

    def test_unknown_kind_values_are_stable(self) -> None:
        self.assertEqual(UnknownKind.UNPARSED_FRAGMENT.value, "unparsed_fragment")


class DecompositionLexerShellTests(unittest.TestCase):
    """验证 shell、owner 和 body chunk 拆解。"""

    def test_decompose_champion_owner_and_shell(self) -> None:
        record = decompose_event_name("Play_vo_Annie_Move2DStandard", game_version="16.5")

        self.assertEqual(record.shell.verb, "play")
        self.assertEqual(record.shell.asset_kind, "vo")
        self.assertEqual(record.owner.raw_owner_token, "Annie")
        self.assertEqual(record.scope, "champion")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["Move2DStandard"])

    def test_keeps_skin_owner_token(self) -> None:
        record = decompose_event_name("Play_vo_AatroxSkin01_AatroxQ2_cast3D", game_version="16.5")

        self.assertEqual(record.owner.raw_owner_token, "AatroxSkin01")
        self.assertEqual(record.owner.alias, "Aatrox")
        self.assertEqual(record.owner.skin_token, "Skin01")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["AatroxQ2", "cast3D"])

    def test_merges_split_owner_skin_token(self) -> None:
        record = decompose_event_name("Play_sfx_Evelynn_Skin06_dance", game_version="16.5")

        self.assertEqual(record.owner.raw_owner_token, "EvelynnSkin06")
        self.assertEqual(record.owner.alias, "Evelynn")
        self.assertEqual(record.owner.skin_token, "Skin06")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["dance"])

    def test_drops_duplicate_owner_skin_token(self) -> None:
        record = decompose_event_name("Play_sfx_GangplankSkin14_Skin14_Dance3D_buffactivate", game_version="16.5")

        self.assertEqual(record.owner.raw_owner_token, "GangplankSkin14")
        self.assertEqual(record.owner.skin_token, "Skin14")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["Dance3D", "buffactivate"])

    def test_merges_repeated_owner_skin_chunk(self) -> None:
        record = decompose_event_name(
            "Play_sfx_Orianna_OriannaSkin07_OriannaBallFlash_buffactivate",
            game_version="16.5",
        )

        self.assertEqual(record.owner.raw_owner_token, "OriannaSkin07")
        self.assertEqual(record.owner.alias, "Orianna")
        self.assertEqual(record.owner.skin_token, "Skin07")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["OriannaBallFlash", "buffactivate"])

    def test_mute_shell_parses_like_play_shell(self) -> None:
        record = decompose_event_name("Mute_sfx_AkshanSkin01_AkshanWRevive_buffcast", game_version="16.5")

        self.assertEqual(record.shell.verb, "mute")
        self.assertEqual(record.shell.asset_kind, "sfx")
        self.assertEqual(record.owner.alias, "Akshan")

    def test_implicit_asset_kind_shell_without_play_verb(self) -> None:
        record = decompose_event_name("mus_ViegoSkin43_BigPartyBonanza", game_version="16.5")

        self.assertIsNone(record.shell.verb)
        self.assertEqual(record.shell.asset_kind, "mus")
        self.assertEqual(record.shell.body, "ViegoSkin43_BigPartyBonanza")
        self.assertEqual(record.owner.raw_owner_token, "ViegoSkin43")
        self.assertEqual(record.owner.skin_token, "Skin43")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["BigPartyBonanza"])

    def test_implicit_sfx_shell_keeps_skin_owner(self) -> None:
        record = decompose_event_name("sfx_XinZhaoSkin06_QIdle1_buffactivate", game_version="16.5")

        self.assertIsNone(record.shell.verb)
        self.assertEqual(record.shell.asset_kind, "sfx")
        self.assertEqual(record.owner.raw_owner_token, "XinZhaoSkin06")
        self.assertEqual(record.owner.alias, "XinZhao")
        self.assertEqual(record.owner.skin_token, "Skin06")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["QIdle1", "buffactivate"])


class DecompositionLexerShellVerbCaseTests(unittest.TestCase):
    """锚定壳动词大小写不敏感后的行为。"""

    def test_lowercase_play_shell_parses_verb_and_owner(self) -> None:
        """raw 中现存的小写 play_ 壳必须解析出壳动词和真实 owner。"""

        record = decompose_event_name("play_sfx_SettSkin01_Taunt3D_buffactivate", game_version="16.5")

        self.assertEqual(record.shell.verb, "play")
        self.assertEqual(record.shell.asset_kind, "sfx")
        self.assertEqual(record.owner.alias, "Sett")
        self.assertEqual(record.owner.skin_token, "Skin01")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["Taunt3D", "buffactivate"])

    def test_lowercase_play_mus_shell_keeps_map_body(self) -> None:
        """小写 play_mus_ 地图音乐事件不再把 play 当 owner。"""

        record = decompose_event_name("play_mus_map12_bloom_gamestart", game_version="16.5")

        self.assertEqual(record.shell.verb, "play")
        self.assertEqual(record.shell.asset_kind, "mus")
        self.assertEqual(record.shell.body, "map12_bloom_gamestart")

    def test_lowercase_stop_shell_keeps_stop_semantics(self) -> None:
        """小写 stop_ 壳不得静默丢失停止语义。"""

        record = decompose_event_name("stop_sfx_SettSkin01_Taunt3D_buffactivate", game_version="16.5")

        self.assertEqual(record.shell.verb, "stop")
        self.assertEqual(record.shell.asset_kind, "sfx")
        self.assertEqual(record.owner.alias, "Sett")

    def test_lowercase_mute_shell_keeps_mute_semantics(self) -> None:
        """小写 mute_ 壳不得静默丢失静音语义。"""

        record = decompose_event_name("mute_sfx_Akshan_AkshanWRevive_buffcast", game_version="16.5")

        self.assertEqual(record.shell.verb, "mute")
        self.assertEqual(record.shell.asset_kind, "sfx")
        self.assertEqual(record.owner.alias, "Akshan")

    def test_unmute_shell_is_recognized(self) -> None:
        """UnMute_ 壳解析出 unmute 动词和真实 owner。"""

        record = decompose_event_name("UnMute_mus_EkkoSkin19_InteractiveMusic", game_version="16.5")

        self.assertEqual(record.shell.verb, "unmute")
        self.assertEqual(record.shell.asset_kind, "mus")
        self.assertEqual(record.owner.alias, "Ekko")
        self.assertEqual(record.owner.skin_token, "Skin19")

    def test_post_event_shell_is_recognized(self) -> None:
        """Post_Event_ 壳解析出 post 动词，owner 是真实英雄。"""

        record = decompose_event_name("Post_Event_Gwen_GwenW_ambient_ducking", game_version="16.5")

        self.assertEqual(record.shell.verb, "post")
        self.assertEqual(record.shell.asset_kind, "event")
        self.assertEqual(record.owner.alias, "Gwen")


class DecompositionLexerShellAssetRestrictionTests(unittest.TestCase):
    """锚定壳第二段只剥离已知资产/控制组段的行为。"""

    def test_play_shell_keeps_legacy_wildcard_asset(self) -> None:
        """Play_ 壳保留历史通配剥离；owner 落到裸 SkinNN token。"""

        record = decompose_event_name("Play_Rammus_Skin08_Recall_Goal", game_version="16.5")

        self.assertEqual(record.shell.verb, "play")
        self.assertEqual(record.shell.asset_kind, "rammus")
        self.assertIsNone(record.owner.alias)
        self.assertEqual(record.owner.skin_token, "Skin08")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["Recall", "Goal"])

    def test_switch_owner_token_is_not_swallowed(self) -> None:
        """Switch_Rakan_IsAlone 的 Rakan 属于 owner。"""

        record = decompose_event_name("Switch_Rakan_IsAlone", game_version="16.5")

        self.assertEqual(record.shell.verb, "switch")
        self.assertIsNone(record.shell.asset_kind)
        self.assertEqual(record.owner.alias, "Rakan")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["IsAlone"])

    def test_setstate_owner_token_is_not_swallowed(self) -> None:
        """SetState_EkkoSkin19_E 的 owner 是 EkkoSkin19，E 是状态值。"""

        record = decompose_event_name("SetState_EkkoSkin19_E", game_version="16.5")

        self.assertEqual(record.shell.verb, "setstate")
        self.assertIsNone(record.shell.asset_kind)
        self.assertEqual(record.owner.alias, "Ekko")
        self.assertEqual(record.owner.skin_token, "Skin19")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["E"])

    def test_set_switch_control_shell_is_preserved(self) -> None:
        """Set_Switch_aggro_off 仍按控制组壳解析。"""

        record = decompose_event_name("Set_Switch_aggro_off", game_version="16.5")

        self.assertEqual(record.shell.verb, "set")
        self.assertEqual(record.shell.asset_kind, "switch")
        self.assertEqual(record.owner.alias, "aggro")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["off"])

    def test_champ_numeric_switch_owner_stays_unknown(self) -> None:
        """Switch_CHAMP_578_Off 的数字开关值不是英雄 alias。"""

        record = decompose_event_name(
            "Switch_CHAMP_578_Off",
            game_version="16.5",
            source_path="raw/events/champions/145.txt",
        )

        self.assertEqual(record.shell.verb, "switch")
        self.assertEqual(record.shell.asset_kind, "champ")
        self.assertEqual(record.owner.kind, "unknown")
        self.assertEqual(record.owner.raw_owner_token, "578")

    def test_two_segment_setstate_event_keeps_legacy_shape(self) -> None:
        """SetState_Ally1 这类两段事件保持旧解析形态（无壳）。"""

        record = decompose_event_name("SetState_Ally1", game_version="16.5")

        self.assertIsNone(record.shell.verb)
        self.assertEqual(record.shell.body, "SetState_Ally1")
        self.assertEqual(record.owner.raw_owner_token, "SetState")


class DecompositionLexerOwnerSkinCaseTests(unittest.TestCase):
    """锚定 owner 中 SkinNN 大小写/typo 变体的拆分。"""

    def test_skin_typo_owner_splits_alias_and_skin(self) -> None:
        """GalioSKin04 typo 拆出 Galio 与规整化 Skin04。"""

        record = decompose_event_name("Play_vo_GalioSKin04_Taunt3DGeneral", game_version="16.5")

        self.assertEqual(record.owner.alias, "Galio")
        self.assertEqual(record.owner.skin_token, "Skin04")
        self.assertEqual(record.owner.raw_owner_token, "GalioSKin04")

    def test_lowercase_skin_owner_splits_alias_and_skin(self) -> None:
        """jinx_skin60 合并后拆出 jinx 与 Skin60，原始写法保留。"""

        record = decompose_event_name("Play_sfx_jinx_skin60_exalted_spawnin", game_version="16.5")

        self.assertEqual(record.owner.alias, "jinx")
        self.assertEqual(record.owner.skin_token, "Skin60")
        self.assertEqual(record.owner.raw_owner_token, "jinxskin60")

    def test_compact_lowercase_skin_owner_splits(self) -> None:
        """Aatroxskin33 拆出 Aatrox 与 Skin33。"""

        record = decompose_event_name("Play_sfx_Aatroxskin33_Joke3D_loop", game_version="16.5")

        self.assertEqual(record.owner.alias, "Aatrox")
        self.assertEqual(record.owner.skin_token, "Skin33")

    def test_bare_skin_owner_has_no_alias(self) -> None:
        """Play_mus_Skin06_* 的 owner 只有皮肤信息，没有英雄 alias。"""

        record = decompose_event_name("Play_mus_Skin06_Aggressive_select", game_version="16.5")

        self.assertEqual(record.owner.kind, "champion")
        self.assertIsNone(record.owner.alias)
        self.assertEqual(record.owner.skin_token, "Skin06")
        self.assertEqual(record.owner.raw_owner_token, "Skin06")

    def test_spell_key_owner_is_not_champion_alias(self) -> None:
        """Switch_VO_E_* 的 E 是技能键开关组，不是英雄 alias。"""

        record = decompose_event_name(
            "Switch_VO_E_ivernsbrush",
            game_version="16.5",
            source_path="raw/events/champions/57.txt",
        )

        self.assertEqual(record.owner.kind, "unknown")
        self.assertIsNone(record.owner.alias)
        self.assertEqual(record.owner.raw_owner_token, "E")
        self.assertEqual([atom.raw for atom in record.lexical_atoms], ["ivernsbrush"])


class DecompositionLexerInlineTests(unittest.TestCase):
    """验证粘连 token 的拆解。"""

    def test_move_2d_standard(self) -> None:
        record = decompose_event_name("Play_vo_Annie_Move2DStandard", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw) for part in parts],
            [
                ("trigger_token", "Move"),
                ("dimension_token", "2D"),
                ("residue_token", "Standard"),
            ],
        )

    def test_spell_q3_cast(self) -> None:
        record = decompose_event_name("Play_vo_Riven_Spell3DQ3Cast", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw) for part in parts],
            [
                ("trigger_token", "Spell"),
                ("dimension_token", "3D"),
                ("spell_key_token", "Q"),
                ("number_token", "3"),
                ("phase_or_state_token", "Cast"),
            ],
        )

    def test_kill_pentakill_does_not_split_spell_key(self) -> None:
        record = decompose_event_name("Play_vo_LuxSkin17_KillChampion3DPentakill", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw) for part in parts],
            [
                ("trigger_token", "KillChampion"),
                ("dimension_token", "3D"),
                ("residue_token", "Pentakill"),
            ],
        )

    def test_joke_variant_keeps_trailing_dimension(self) -> None:
        record = decompose_event_name("Play_sfx_PoppySkin33_Joke23D_buffactivate", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw, part.value) for part in parts],
            [
                ("trigger_token", "Joke", "joke"),
                ("number_token", "2", 2),
                ("dimension_token", "3D", "3D"),
            ],
        )

    def test_lowercase_trigger_keeps_canonical_trigger(self) -> None:
        record = decompose_event_name("Play_vo_GnarBigSkin13_dance_buffactivate", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(parts[0].kind, "trigger_token")
        self.assertEqual(parts[0].raw, "Dance")
        self.assertEqual(parts[0].value, "dance")

    def test_lowercase_trigger_with_dimension(self) -> None:
        record = decompose_event_name("Play_vo_JinxSkin01_laugh3D_in", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw, part.value) for part in parts],
            [
                ("trigger_token", "Laugh", "laugh"),
                ("dimension_token", "3D", "3D"),
            ],
        )

    def test_interaction_target_can_end_with_dimension(self) -> None:
        record = decompose_event_name("Play_vo_CassiopeiaSkin18_AttackChampion2D", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw, part.value) for part in parts],
            [
                ("trigger_token", "Attack", "attack"),
                ("dimension_token", "2D", "2D"),
                ("residue_token", "Champion", "Champion"),
            ],
        )

    def test_trigger_does_not_split_inside_killer_word(self) -> None:
        record = decompose_event_name("Play_sfx_ZedSkin03_ZedKillerCut_buffactivate", game_version="16.5")
        parts = record.lexical_atoms[0].parts

        self.assertEqual(
            [(part.kind, part.raw, part.value) for part in parts],
            [("residue_token", "KillerCut", "KillerCut")],
        )

    def test_owner_prefixed_q2_cast3d(self) -> None:
        record = decompose_event_name("Play_vo_AatroxSkin01_AatroxQ2_cast3D", game_version="16.5")
        first_atom = record.lexical_atoms[0]
        second_atom = record.lexical_atoms[1]

        self.assertEqual(first_atom.normalized, "Q / 2")
        self.assertEqual(second_atom.normalized, "3D / cast")

    def test_preserves_5d_dimension(self) -> None:
        record = decompose_event_name("Play_vo_AurelionSolSkin31_AurelionSolR_cast5D", game_version="16.5")
        second_atom = record.lexical_atoms[1]

        self.assertIn(("dimension_token", "5D"), [(part.kind, part.raw) for part in second_atom.parts])

    def test_lowercase_end_suffix_is_phase(self) -> None:
        """小写 _end 尾段应识别为 phase（大小写不敏感）。"""

        record = decompose_event_name("Play_sfx_Aatrox_AatroxR_end", game_version="16.5")
        end_atom = record.lexical_atoms[1]

        self.assertEqual(end_atom.raw, "end")
        self.assertEqual(
            [(part.kind, part.value) for part in end_atom.parts],
            [("phase_or_state_token", "end")],
        )

    def test_expanded_phase_suffix_words_are_phases(self) -> None:
        """扩充的相位整词（小写）应识别为 phase。"""

        cases = {
            "start": "start",
            "land": "land",
            "detonate": "detonate",
            "stop": "stop",
            "swap": "swap",
            "reset": "reset",
            "fullcharge": "fullcharge",
        }
        for chunk, expected in cases.items():
            with self.subTest(chunk=chunk):
                record = decompose_event_name(f"Play_sfx_Aatrox_AatroxR_{chunk}", game_version="16.5")
                atom = record.lexical_atoms[1]
                self.assertEqual(
                    [(part.kind, part.value) for part in atom.parts],
                    [("phase_or_state_token", expected)],
                )


class DecompositionLexerSpellKeyConfidenceTests(unittest.TestCase):
    """锁定 is_confident_spell_key_suffix 在 IGNORECASE 改动后的关键行为。"""

    def test_confident_for_empty_number_and_phase_suffix(self) -> None:
        """空尾、数字尾、相位尾仍判为技能键。"""

        from lol_event_semantics.decomposition_lexer import is_confident_spell_key_suffix

        self.assertTrue(is_confident_spell_key_suffix("", ""))
        self.assertTrue(is_confident_spell_key_suffix("3", ""))
        self.assertTrue(is_confident_spell_key_suffix("", "Cast"))

    def test_not_confident_for_plain_words(self) -> None:
        """普通单词尾不应被误判为技能键。"""

        from lol_event_semantics.decomposition_lexer import is_confident_spell_key_suffix

        self.assertFalse(is_confident_spell_key_suffix("", "Champion"))
        self.assertFalse(is_confident_spell_key_suffix("", "Standard"))
        self.assertFalse(is_confident_spell_key_suffix("", "assive"))


class DecompositionLexerDialogueTests(unittest.TestCase):
    """验证对话链事件不会丢失嵌套触发信息。"""

    def test_chat_chain_keeps_all_chunks(self) -> None:
        raw = "Play_vo_KayleSkin42_Chat3DMorgana_FirstEncounter3DKayle01_Chat2"
        record = decompose_event_name(raw, game_version="16.5")

        self.assertEqual(
            [atom.raw for atom in record.lexical_atoms],
            ["Chat3DMorgana", "FirstEncounter3DKayle01", "Chat2"],
        )
        self.assertEqual(record.lexical_atoms[0].parts[0].raw, "Chat")
        self.assertEqual(record.lexical_atoms[1].parts[0].raw, "FirstEncounter")
        self.assertEqual(record.lexical_atoms[2].parts[0].raw, "Chat")
