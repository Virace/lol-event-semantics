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
