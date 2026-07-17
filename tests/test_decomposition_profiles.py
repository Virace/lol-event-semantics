"""事件名 profile 与语义 slot 测试。"""

from __future__ import annotations

import unittest

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import DecompositionConfidence, SemanticProfile, UnknownKind
from lol_event_semantics.decomposition_profiles import normalize_semantics


class ProfileClassifierSmokeTests(unittest.TestCase):
    """验证 profile 归一化入口。"""

    def test_normalize_semantics_returns_record(self) -> None:
        record = decompose_event_name("Play_vo_Annie_Move2DStandard", game_version="16.5")
        normalized = normalize_semantics(record)

        self.assertIs(normalized, record)
        self.assertEqual(normalized.profile.semantic_profile, SemanticProfile.SELF_ACTION)


class ProfileClassifierSlotTests(unittest.TestCase):
    """验证核心事件类型 slot。"""

    def normalize(self, raw: str):
        """拆解并归一化事件。"""

        return normalize_semantics(decompose_event_name(raw, game_version="16.5"))

    def test_first_encounter_interaction(self) -> None:
        record = self.normalize("Play_vo_Lux_FirstEncounter3DAshe")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "first_encounter")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Ashe")

    def test_kill_penta_is_milestone_not_target(self) -> None:
        record = self.normalize("Play_vo_LuxSkin17_KillChampion3DPentakill")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["trigger_family"], "kill")
        self.assertEqual(record.semantic_slots["milestone"], "penta")
        self.assertIsNone(record.semantic_slots["counterpart"])

    def test_kill_streak_is_counter_not_entity_target(self) -> None:
        record = self.normalize("Play_sfx_ViegoSkin43_KillStreak_Kill_Additive_Size_01_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["counter"]["kind"], "kill_streak")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_kill_count_is_counter_not_entity_target(self) -> None:
        record = self.normalize("Play_vo_KaisaSkin14_Kill3DCount_01")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["counter"]["kind"], "count")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_kill_triplekill_is_milestone_not_entity(self) -> None:
        record = self.normalize("Play_vo_DrMundoSkin03_Kill3DTriplekill")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["milestone"], "triple")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_kill_spell_active_tail_becomes_method(self) -> None:
        record = self.normalize("Play_vo_MasterYiSkin89_Kill3DRActive")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["kill_method"]["spell_key"], "r")
        self.assertEqual(record.semantic_slots["kill_method"]["state"], "active")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_kill_spell_sequence_tail_becomes_method(self) -> None:
        record = self.normalize("Play_vo_Hwei_Kill3DQW")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["kill_method"]["spell_sequence"], ["q", "w"])
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_kill_objective_steal_tail_becomes_score_context(self) -> None:
        record = self.normalize("Play_vo_MasterYiSkin89_Kill3DDragonSteal")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["objective_steal"]["objective"], "dragon")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_spell_q3_cast_requires_mechanic_context(self) -> None:
        record = self.normalize("Play_vo_Riven_Spell3DQ3Cast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        self.assertEqual(record.semantic_slots["spell_variant"], 3)
        self.assertEqual(record.semantic_slots["variant_kind"], "numbered_spell_variant")
        self.assertFalse(record.semantic_slots["requires_champion_mechanic_context"])
        self.assertFalse(record.unknowns)

    def test_spell_w1_cast_becomes_numbered_spell_variant(self) -> None:
        record = self.normalize("Play_vo_IreliaSkin37_Spell3DW1Cast_01")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_key"], "w")
        self.assertEqual(record.semantic_slots["spell_variant"], 1)
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["line_variant"], 1)
        self.assertFalse(record.unknowns)

    def test_move_first_ally_has_relation_target(self) -> None:
        record = self.normalize("Play_vo_Milio_Move2DFirstAllyQiyana")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["timing"], "first")
        self.assertEqual(record.semantic_slots["relation"], "ally")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Qiyana")

    def test_move_first_without_target_is_move_not_kill_milestone(self) -> None:
        record = self.normalize("Play_vo_Taliyah_Move2DFirst")

        self.assertEqual(record.profile.primary_family, "move")
        self.assertEqual(record.profile.semantic_profile, SemanticProfile.QUALIFIED_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["timing"], "first")
        self.assertNotIn("milestone", record.semantic_slots)

    def test_kill_first_still_maps_to_kill_milestone(self) -> None:
        record = self.normalize("Play_vo_Hwei_Kill3DFirst")

        self.assertEqual(record.profile.primary_family, "kill")
        self.assertEqual(record.semantic_slots["milestone"], "first")

    def test_move_location_residue_becomes_movement_context(self) -> None:
        record = self.normalize("Play_vo_AatroxSkin33_Move2DRiver")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["movement_context"]["kind"], "location")
        self.assertEqual(record.semantic_slots["movement_context"]["key"], "river")
        self.assertFalse(record.unknowns)

    def test_move_without_residue_is_generic_move(self) -> None:
        record = self.normalize("Play_vo_GnarBigSkin15_Move2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["qualifier"], "generic")
        self.assertFalse(record.unknowns)

    def test_move_order_long_becomes_long_distance_move_order(self) -> None:
        record = self.normalize("Play_vo_LucianSkin06_MoveOrder2DLong")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["movement_context"]["key"], "move_order")
        self.assertEqual(record.semantic_slots["distance"], "long")
        self.assertFalse(record.unknowns)

    def test_move_base_becomes_location_context(self) -> None:
        record = self.normalize("Play_vo_Ornn_Move2DBase")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["movement_context"]["key"], "base")
        self.assertFalse(record.unknowns)

    def test_move_match_state_becomes_context(self) -> None:
        record = self.normalize("Play_vo_KSante_Move2DWinning")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["movement_context"]["kind"], "match_state")
        self.assertEqual(record.semantic_slots["movement_context"]["key"], "winning")
        self.assertFalse(record.unknowns)

    def test_move_neutral_camp_becomes_context(self) -> None:
        record = self.normalize("Play_vo_IvernSkin01_Move2DGromp")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["movement_context"]["kind"], "neutral_camp")
        self.assertEqual(record.semantic_slots["movement_context"]["key"], "gromp")
        self.assertFalse(record.unknowns)

    def test_move_with_nearby_champion_tail_is_precondition(self) -> None:
        record = self.normalize("Play_vo_SennaSkin56_Move2DWithLucianNear")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Lucian")
        self.assertFalse(record.unknowns)

    def test_move_first_target_without_relation_becomes_interaction(self) -> None:
        record = self.normalize("Play_vo_AatroxSkin01_Move2DFirstPantheon")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["timing"], "first")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Pantheon")
        self.assertEqual(record.unknowns[0].kind, UnknownKind.AMBIGUOUS_ENTITY)

    def test_receive_buff_becomes_received_effect(self) -> None:
        record = self.normalize("Play_vo_Caitlyn_Receive3DBuff")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "receive")
        self.assertEqual(record.semantic_slots["received_effect"]["kind"], "buff")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_receive_heal_honeyfruit_keeps_effect_source(self) -> None:
        record = self.normalize("Play_vo_KaisaSkin14_Receive3DHealHoneyfruit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["received_effect"]["kind"], "heal")
        self.assertEqual(record.semantic_slots["received_effect"]["source"], "Honeyfruit")
        self.assertFalse(record.unknowns)

    def test_low_health_general_becomes_health_state(self) -> None:
        record = self.normalize("Play_vo_Aphelios_LowHealth2DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "low_health")
        self.assertEqual(record.semantic_slots["health_state"]["kind"], "low_health")
        self.assertEqual(record.semantic_slots["qualifier"], "general")
        self.assertFalse(record.unknowns)

    def test_attack_champion_2d_is_generic_target_with_dimension(self) -> None:
        record = self.normalize("Play_vo_CassiopeiaSkin18_AttackChampion2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "target_class")
        self.assertFalse(record.unknowns)

    def test_taunt_game_start_is_timing_not_entity_target(self) -> None:
        record = self.normalize("Play_sfx_ZoeSkin02_TauntGameStart3D_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "taunt")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["timing"], "game_start")
        self.assertFalse(record.unknowns)

    def test_taunt_nested_joke_response_is_not_entity_target(self) -> None:
        record = self.normalize("Play_vo_XinZhaoSkin05_TauntJokeResponse3DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "taunt")
        self.assertEqual(record.semantic_slots["nested_action"]["family"], "joke_response")
        self.assertFalse(record.unknowns)

    def test_dialogue_chain_profile(self) -> None:
        raw = "Play_vo_KayleSkin42_Chat3DMorgana_FirstEncounter3DKayle01_Chat2"
        record = self.normalize(raw)

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.DIALOGUE_CHAIN)
        self.assertEqual(record.semantic_slots["chat_partner"]["raw"], "Morgana")
        self.assertEqual(record.semantic_slots["base_trigger"]["family"], "first_encounter")
        self.assertEqual(record.semantic_slots["base_trigger"]["counterpart_raw"], "Kayle")
        self.assertEqual(record.semantic_slots["base_trigger"]["line_variant"], 1)
        self.assertEqual(record.semantic_slots["chat_turn"], 2)
        self.assertFalse(record.unknowns)

    def test_dialogue_chain_with_nearby_champion_context(self) -> None:
        record = self.normalize("Play_vo_SennaSkin16_WithLucianNear_FirstEncounter3D01Thresh_Chat02")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.DIALOGUE_CHAIN)
        self.assertEqual(record.semantic_slots["precondition"]["kind"], "nearby_champion")
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Lucian")
        self.assertEqual(record.semantic_slots["base_trigger"]["family"], "first_encounter")
        self.assertEqual(record.semantic_slots["base_trigger"]["counterpart_raw"], "Thresh")
        self.assertEqual(record.semantic_slots["base_trigger"]["line_variant"], 1)
        self.assertEqual(record.semantic_slots["chat_turn"], 2)
        self.assertFalse(record.unknowns)

    def test_dialogue_chain_with_nearby_champion_first_move(self) -> None:
        record = self.normalize("Play_vo_LucianSkin06_WithSennaNear_FirstMove2D01_Chat03")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.DIALOGUE_CHAIN)
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Senna")
        self.assertEqual(record.semantic_slots["base_trigger"]["family"], "first_move")
        self.assertEqual(record.semantic_slots["base_trigger"]["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["base_trigger"]["line_variant"], 1)
        self.assertEqual(record.semantic_slots["chat_turn"], 3)
        self.assertFalse(record.unknowns)

    def test_nearby_champion_can_qualify_simple_action(self) -> None:
        record = self.normalize("Play_vo_SennaSkin16_WithLucianNear_Heal2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "heal")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Lucian")
        self.assertFalse(record.unknowns)

    def test_nearby_champion_can_qualify_kill_tail(self) -> None:
        record = self.normalize("Play_vo_LucianSkin06_Kill3DWithSennaNear")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Senna")

    def test_first_encounter_splits_target_and_nearby_tail(self) -> None:
        record = self.normalize("Play_vo_LucianSkin06_FirstEncounter3DThreshWithSennaNear")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Thresh")
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Senna")

    def test_tutorial_voice_group_is_system_voice(self) -> None:
        record = self.normalize("Play_vo_Tutorial2_Tip_Act_Quickly")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["system_voice_kind"], "tutorial")
        self.assertEqual(record.semantic_slots["section"], "Tip")
        self.assertEqual(record.semantic_slots["cue_path"], ["Act", "Quickly"])
        self.assertFalse(record.unknowns)

    def test_announcer_mode_voice_group_is_system_voice(self) -> None:
        record = self.normalize("Play_vo_Announcer_Tournaments_Female1_OnWelcomeBandleCityClash")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["system_voice_kind"], "announcer")
        self.assertEqual(record.semantic_slots["mode"], "Tournaments")
        self.assertEqual(record.semantic_slots["announcer_persona"]["gender"], "female")
        self.assertEqual(record.semantic_slots["announcer_persona"]["variant"], 1)
        self.assertFalse(record.unknowns)

    def test_nexus_blitz_voice_group_is_game_mode_voice(self) -> None:
        record = self.normalize("Play_vo_NexusBlitz_Blue_BlueBasicAttack_Attack3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["system_voice_kind"], "game_mode")
        self.assertEqual(record.semantic_slots["mode"], "NexusBlitz")
        self.assertEqual(record.semantic_slots["actor"], "Blue")
        self.assertFalse(record.unknowns)

    def test_leading_voice_context_qualifies_known_action(self) -> None:
        record = self.normalize("Play_vo_Jayce_Sentinel_Attack2DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "attack")
        self.assertEqual(record.semantic_slots["pre_context"]["raw"], "Sentinel")
        self.assertEqual(record.semantic_slots["pre_context"]["kind"], "voice_context")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_basic_attack_is_attack_event(self) -> None:
        record = self.normalize("Play_vo_ShyvanaDragonSkin08_ShyvanaBasicAttack2_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "attack")
        self.assertEqual(record.semantic_slots["attack_kind"], "basic_attack")
        self.assertEqual(record.semantic_slots["attack_rank"], 2)
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_skin_basic_attack_keeps_skin_context(self) -> None:
        record = self.normalize("Play_vo_SingedSkin10_SingedSkin10BasicAttack2_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "attack")
        self.assertEqual(record.semantic_slots["attack_kind"], "basic_attack")
        self.assertEqual(record.semantic_slots["attack_context"], "Skin10")
        self.assertEqual(record.semantic_slots["attack_rank"], 2)
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_long_basic_is_attack_event(self) -> None:
        record = self.normalize("Play_vo_SennaSkin56_Senna3DLongBasic")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "attack")
        self.assertEqual(record.semantic_slots["attack_kind"], "basic_attack")
        self.assertEqual(record.semantic_slots["attack_range"], "long")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_contextual_basic_attack_before_attack_trigger_is_attack_event(self) -> None:
        record = self.normalize("Play_vo_SLIME_Blue_BlueBasicAttack_Attack3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "attack")
        self.assertEqual(record.semantic_slots["attack_kind"], "basic_attack")
        self.assertEqual(record.semantic_slots["actor"], "Blue")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_kalista_bind_ally_voice_condition_is_not_spell_alias(self) -> None:
        record = self.normalize("Play_vo_KalistaSkin05_KalistaVOBindAllyGlobal_OnBuffCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.profile.primary_family, "voice_condition")
        self.assertEqual(record.semantic_slots["trigger_family"], "voice_condition")
        self.assertEqual(record.semantic_slots["condition"]["key"], "bind_ally_global")
        self.assertEqual(record.semantic_slots["phase"], "on_buff_cast")
        self.assertFalse(record.unknowns)

    def test_interactive_champion_specific_voice_is_structured(self) -> None:
        record = self.normalize("Play_vo_NamiSkin41_Interactive_Interactive3DChampionSpecific21")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "interactive")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["specificity"], "champion_specific")
        self.assertEqual(record.semantic_slots["line_variant"], 21)
        self.assertFalse(record.unknowns)

    def test_move_spell_ready_keeps_spell_state(self) -> None:
        record = self.normalize("Play_vo_AatroxSkin01_Move2DRReady")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["spell_state"]["spell_key"], "r")
        self.assertEqual(record.semantic_slots["spell_state"]["state"], "ready")
        self.assertFalse(record.unknowns)

    def test_nearby_death_observation_tracks_relation(self) -> None:
        record = self.normalize("Play_vo_Vayne_Nearby3DDeathsAlly")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "nearby_event")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["observed_action"], "death")
        self.assertEqual(record.semantic_slots["relation"], "ally")
        self.assertFalse(record.unknowns)

    def test_nearby_use_item_observation_keeps_item(self) -> None:
        record = self.normalize("Play_vo_Vayne_Nearby3DUseItemHealthPotEnemy")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["observed_action"], "use_item")
        self.assertEqual(record.semantic_slots["item"], "health_pot")
        self.assertEqual(record.semantic_slots["relation"], "enemy")
        self.assertFalse(record.unknowns)

    def test_rally_team_state_is_structured_voice_context(self) -> None:
        record = self.normalize("Play_vo_DariusSkin15_RallyTeam3DAhead")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.QUALIFIED_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "rally_team")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["team_state"], "ahead")
        self.assertFalse(record.unknowns)

    def test_dimension_prefixed_pentakill_is_kill_milestone(self) -> None:
        record = self.normalize("Play_vo_LuxSkin01_Pentakill3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["trigger_family"], "kill")
        self.assertEqual(record.semantic_slots["milestone"], "penta")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_clear_camp_voice_context_is_structured(self) -> None:
        record = self.normalize("Play_vo_Nunu_ClearCamp3DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "clear_camp")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["qualifier"], "general")
        self.assertFalse(record.unknowns)

    def test_clear_camp_target_is_structured(self) -> None:
        record = self.normalize("Play_vo_XinZhaoSkin05_ClearCamp3DBrambleback")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "clear_camp")
        self.assertEqual(record.semantic_slots["camp_target"]["raw"], "Brambleback")
        self.assertFalse(record.unknowns)

    def test_open_voice_context_is_structured(self) -> None:
        record = self.normalize("Play_vo_Leblanc_Open2DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "open")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["qualifier"], "general")
        self.assertFalse(record.unknowns)

    def test_partner_dance_voice_has_nearby_precondition(self) -> None:
        record = self.normalize("Play_vo_Xayah_WithRakanDanceInto3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "dance")
        self.assertEqual(record.semantic_slots["precondition"]["counterpart"]["raw"], "Rakan")
        self.assertEqual(record.semantic_slots["transition"], "into")
        self.assertFalse(record.unknowns)

    def test_switch_voice_event_is_technical_control(self) -> None:
        record = self.normalize("Switch_VO_Kills_Regular")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "switch")
        self.assertEqual(record.semantic_slots["switch_group"], "Kills")
        self.assertEqual(record.semantic_slots["switch_target"], "Regular")
        self.assertFalse(record.unknowns)

    def test_neutral_creature_aggro_voice_is_structured(self) -> None:
        record = self.normalize("Play_vo_Atakhan_AggroReset")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "creature_voice")
        self.assertEqual(record.semantic_slots["creature_action"], "aggro_reset")
        self.assertFalse(record.unknowns)

    def test_animation_voice_event_is_structured(self) -> None:
        record = self.normalize("Play_vo_Aphelios_Anim3DDrinkPoisonEnd")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "animation")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["animation"]["raw"], "DrinkPoisonEnd")
        self.assertFalse(record.unknowns)

    def test_owner_spell_phase_only_event_is_structured(self) -> None:
        record = self.normalize("Play_vo_AnnieQ_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["trigger_family"], "spell")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_buff_state_voice_event_is_structured(self) -> None:
        record = self.normalize("Play_vo_Neeko_BlessingOfTheLizardElder_OnBuffActivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "buff_state")
        self.assertEqual(record.semantic_slots["buff_name"], "BlessingOfTheLizardElder")
        self.assertEqual(record.semantic_slots["phase"], "on_buff_activate")
        self.assertFalse(record.unknowns)

    def test_second_encounter_voice_is_interaction(self) -> None:
        record = self.normalize("Play_vo_Yunara_SecondEncounter3DKennen")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "encounter")
        self.assertEqual(record.semantic_slots["timing"], "second")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Kennen")
        self.assertFalse(record.unknowns)

    def test_recal_partner_voice_is_recall_interaction(self) -> None:
        record = self.normalize("Play_vo_XayahSkin57_Recal3DRakan")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.INTERACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "recall")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "Rakan")
        self.assertFalse(record.unknowns)

    def test_pyke_r_share_gold_is_medium_confidence_spell_outcome(self) -> None:
        record = self.normalize("Play_vo_PykeSkin01_RShareGold3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertTrue(record.profile.requires_context)
        self.assertEqual(record.confidence, DecompositionConfidence.MEDIUM)
        self.assertEqual(record.semantic_slots["trigger_family"], "spell")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        self.assertEqual(record.semantic_slots["outcome"], "share_gold")
        self.assertFalse(record.unknowns)

    def test_summoner_teleport_is_external_spell_event(self) -> None:
        record = self.normalize("Play_vo_EzrealSkin05_SummonerTeleport_cast2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["trigger_family"], "summoner_spell")
        self.assertEqual(record.semantic_slots["spell_name"], "teleport")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertFalse(record.unknowns)

    def test_response_trigger_is_simple_action(self) -> None:
        record = self.normalize("Play_vo_AatroxSkin33_TauntResponse3DGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "taunt_response")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertEqual(record.semantic_slots["qualifier"], "General")

    def test_use_item_trigger_is_simple_action(self) -> None:
        record = self.normalize("Play_vo_AhriSkin14_UseItem2DWard")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "use_item")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["qualifier"], "Ward")

    def test_homeguard_trigger_is_simple_action(self) -> None:
        record = self.normalize("Play_sfx_AhriSkin88_Homeguard3D_in")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "homeguard")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")

    def test_split_owner_skin_is_not_trigger_head(self) -> None:
        record = self.normalize("Play_sfx_Karthus_Skin10_Joke3D_buffactivate")

        self.assertEqual(record.owner.raw_owner_token, "KarthusSkin10")
        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "joke")
        self.assertFalse(record.unknowns)

    def test_skin_easter_egg_prefix_qualifies_next_action(self) -> None:
        record = self.normalize("Play_sfx_WarwickSkin35_skineasteregg_recall")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "recall")
        self.assertEqual(record.semantic_slots["pre_qualifier"]["key"], "skin_easter_egg")
        self.assertIsNone(record.semantic_slots["qualifier"])
        self.assertFalse(record.unknowns)


class ProfileClassifierAmbiguityTests(unittest.TestCase):
    """验证疑难项不会被吞掉。"""

    def normalize(self, raw: str):
        """拆解并归一化事件。"""

        return normalize_semantics(decompose_event_name(raw, game_version="16.5"))

    def test_star_guardian_target_remains_unresolved(self) -> None:
        record = self.normalize("Play_vo_AhriSkin14_Move2DFirstAllyStarGuardian")

        self.assertEqual(record.semantic_slots["relation"], "ally")
        self.assertEqual(record.semantic_slots["counterpart"]["raw"], "StarGuardian")
        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "unresolved_entity")

    def test_named_spell_alias_with_phase_is_structured_low_confidence(self) -> None:
        record = self.normalize("Play_vo_AsheSkin08_VolleyRank2_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertTrue(record.profile.requires_context)
        self.assertEqual(record.semantic_slots["trigger_family"], "spell")
        self.assertEqual(record.semantic_slots["alias_raw"], "VolleyRank2")
        self.assertEqual(record.semantic_slots["spell_rank"], 2)
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_internal_spell_alias_is_structured(self) -> None:
        record = self.normalize("Play_vo_DariusSkin04_DariusCleave_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "DariusCleave")
        self.assertEqual(record.semantic_slots["alias_base"], "Cleave")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_visual_spell_alias_without_owner_prefix_can_be_structured(self) -> None:
        record = self.normalize("Play_vo_MalphiteSkin06_Landslide_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "Landslide")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_title_prefixed_spell_key_alias_can_be_structured(self) -> None:
        record = self.normalize("Play_vo_LeeSinSkin31_BlindMonkRKick_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "BlindMonkRKick")
        self.assertEqual(record.semantic_slots["spell_key_hint"], "r")
        self.assertFalse(record.unknowns)

    def test_leading_contextual_spell_alias_keeps_pre_context(self) -> None:
        record = self.normalize("Play_vo_Jayce_Sentinel_JayceShockBlastWallSoundHit_OnBuffActivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["pre_context"]["raw"], "Sentinel")
        self.assertEqual(record.semantic_slots["alias_raw"], "JayceShockBlastWallSoundHit")
        self.assertEqual(record.semantic_slots["alias_base"], "ShockBlastWallSound")
        self.assertEqual(record.semantic_slots["phase"], "hit")
        self.assertFalse(record.unknowns)

    def test_unqualified_buff_name_with_phase_becomes_buff_state(self) -> None:
        record = self.normalize("Play_vo_Neeko_BlessingOfTheLizardElder_OnBuffActivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "buff_state")
        self.assertEqual(record.semantic_slots["buff_name"], "BlessingOfTheLizardElder")
        self.assertFalse(record.unknowns)

    def test_phase_only_internal_alias_uses_standalone_cast_phase(self) -> None:
        record = self.normalize("Play_vo_ShacoSkin23_HallucinateFull_cast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "HallucinateFull")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_loaded_alias_uses_standalone_cast_phase(self) -> None:
        record = self.normalize("Play_vo_Corki_CorkiLoaded_cast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "CorkiLoaded")
        self.assertEqual(record.semantic_slots["alias_base"], "Loaded")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_voice_spell_key_hint_keeps_residual(self) -> None:
        record = self.normalize("Play_vo_Akali_AkaliRb_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "AkaliRb")
        self.assertEqual(record.semantic_slots["spell_key_hint"], "r")
        self.assertEqual(record.semantic_slots["alias_residual"], "b")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_voice_spell_key_suffix_keeps_residual(self) -> None:
        record = self.normalize("Play_vo_Aphelios_ApheliosCalibrumQ_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["alias_raw"], "ApheliosCalibrumQ")
        self.assertEqual(record.semantic_slots["spell_key_hint"], "q")
        self.assertEqual(record.semantic_slots["alias_residual"], "Calibrum")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_reversed_long_move_is_structured_move(self) -> None:
        record = self.normalize("Play_vo_KaynSkin02_LongMove2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "move")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertEqual(record.semantic_slots["tail_modifiers"][0]["key"], "long")
        self.assertFalse(record.unknowns)

    def test_gun_select_voice_is_weapon_select(self) -> None:
        record = self.normalize("Play_vo_MissFortuneSkin16_GunSelect2D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "weapon_select")
        self.assertEqual(record.semantic_slots["weapon_kind"], "gun")
        self.assertEqual(record.semantic_slots["dimension_token"], "2D")
        self.assertFalse(record.unknowns)

    def test_tower_destroy_voice_is_objective_destroy(self) -> None:
        record = self.normalize("Play_vo_MordekaiserSkin06_TowerDestroy3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["trigger_family"], "objective_destroy")
        self.assertEqual(record.semantic_slots["objective"]["kind"], "structure")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_kill_r_far_is_spell_range_method(self) -> None:
        record = self.normalize("Play_vo_Teemo_Kill3DRFar")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["kill_method"]["spell_key"], "r")
        self.assertEqual(record.semantic_slots["kill_method"]["range"], "far")
        self.assertFalse(record.unknowns)

    def test_kill_d_general_is_generic_kill_qualifier(self) -> None:
        record = self.normalize("Play_vo_AkaliSkin82_KillDGeneral")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["qualifier"], "general")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(record.unknowns)

    def test_max_resource_spell_key_voice_is_resource_spell_event(self) -> None:
        record = self.normalize("Play_vo_Yunara_MaxResource3DQ")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["trigger_family"], "resource_state")
        self.assertEqual(record.semantic_slots["resource_state"], "max")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_animal_threshold_voice_is_threshold_state(self) -> None:
        record = self.normalize("Play_vo_Kled_AnimalThreshold")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.profile.primary_family, "champion_status_state")
        self.assertEqual(record.semantic_slots["trigger_family"], "champion_status_state")
        self.assertEqual(record.semantic_slots["status_key"], "animal_threshold")
        self.assertFalse(record.unknowns)

    def test_ownerless_kill_penta_voice_is_milestone(self) -> None:
        record = self.normalize("Play_vo_Kill3DPenta")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["trigger_family"], "kill")
        self.assertEqual(record.semantic_slots["milestone"], "penta")
        self.assertEqual(record.semantic_slots["dimension_token"], "3D")
        self.assertFalse(record.unknowns)

    def test_ownerless_phantom_voice_is_structured_system_voice(self) -> None:
        record = self.normalize("Play_vo_PhantomVoice1")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "phantom_voice")
        self.assertEqual(record.semantic_slots["line_variant"], 1)
        self.assertFalse(record.unknowns)

    def test_champion_sfx_with_phase_is_structured_spell_effect(self) -> None:
        record = self.normalize("Play_sfx_RivenSkin05_RivenTriCleave_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["sfx_scope"], "champion_sfx")
        self.assertEqual(record.semantic_slots["effect_head"], "RivenTriCleave")
        self.assertEqual(record.semantic_slots["effect_base"], "TriCleave")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_sfx_spell_key_hint_keeps_residual(self) -> None:
        record = self.normalize("Play_sfx_AkaliSkin02_AkaliEb_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["sfx_scope"], "champion_sfx")
        self.assertEqual(record.semantic_slots["effect_head"], "AkaliEb")
        self.assertEqual(record.semantic_slots["spell_key_hint"], "e")
        self.assertEqual(record.semantic_slots["effect_residual"], "b")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_owner_prefixed_sfx_spell_key_suffix_keeps_residual(self) -> None:
        record = self.normalize("Play_sfx_Aphelios_ApheliosCalibrumQ_OnHit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["sfx_scope"], "champion_sfx")
        self.assertEqual(record.semantic_slots["effect_head"], "ApheliosCalibrumQ")
        self.assertEqual(record.semantic_slots["spell_key_hint"], "q")
        self.assertEqual(record.semantic_slots["effect_residual"], "Calibrum")
        self.assertEqual(record.semantic_slots["phase"], "hit")
        self.assertFalse(record.unknowns)

    def test_mode_sfx_is_structured_system_effect(self) -> None:
        record = self.normalize("Play_sfx_GameMode_Slime2018_Arena_Crowd_Countdown_1")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["sfx_scope"], "mode_sfx")
        self.assertEqual(record.semantic_slots["effect_head"], "Slime2018")
        self.assertFalse(record.unknowns)

    def test_phase_only_item_sfx_uses_owner_as_effect_head(self) -> None:
        record = self.normalize("Play_sfx_3053Shield_OnBuffDeactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["effect_head"], "3053Shield")
        self.assertEqual(record.semantic_slots["phase"], "on_buff_deactivate")
        self.assertFalse(record.unknowns)

    def test_chat_sfx_stays_sfx_not_dialogue_chain(self) -> None:
        record = self.normalize("Play_sfx_hud_Chat_All")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "sfx_event")
        self.assertEqual(record.semantic_slots["effect_head"], "Chat")
        self.assertFalse(record.unknowns)

    def test_ownerless_sfx_uses_body_as_effect_head(self) -> None:
        record = self.normalize("Play_sfx_EggActivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "sfx_event")
        self.assertEqual(record.semantic_slots["effect_head"], "EggActivate")
        self.assertFalse(record.unknowns)

    def test_music_event_is_structured_without_translation(self) -> None:
        record = self.normalize("Play_mus_map11_MODES_25s3_Ruby_death")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "music_event")
        self.assertEqual(record.semantic_slots["control_scope"], "music")
        self.assertEqual(record.semantic_slots["cue_path"], ["MODES", "25s3", "Ruby", "death"])
        self.assertFalse(record.unknowns)

    def test_set_switch_event_is_technical_control(self) -> None:
        record = self.normalize("Set_Switch_aggro_off")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_switch")
        self.assertEqual(record.semantic_slots["switch_group"], "aggro")
        self.assertEqual(record.semantic_slots["switch_value"], "off")
        self.assertFalse(record.unknowns)

    def test_set_state_event_keeps_state_path(self) -> None:
        record = self.normalize("Set_State_Music_Set9Region_BandleCity")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_state")
        self.assertEqual(record.semantic_slots["state_group"], "Music")
        self.assertEqual(record.semantic_slots["state_path"], ["Set9Region", "BandleCity"])
        self.assertFalse(record.unknowns)

    def test_bare_switch_event_is_technical_control(self) -> None:
        """壳不再吞掉 owner 段后，开关组保留原始大小写并归属 owner。"""

        record = self.normalize("Switch_MaokaiSaplings_Brush")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "switch")
        self.assertEqual(record.semantic_slots["switch_group"], "MaokaiSaplings")
        self.assertEqual(record.semantic_slots["switch_target"], "Brush")
        self.assertFalse(record.unknowns)

    def test_item_audio_event_is_structured_system_event(self) -> None:
        record = self.normalize("Play_item_3877_debuff_ready_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "item_audio_event")
        self.assertEqual(record.semantic_slots["control_scope"], "item_or_system")
        self.assertEqual(record.semantic_slots["cue_path"], ["debuff", "ready", "buffactivate"])
        self.assertFalse(record.unknowns)

    def test_reset_fx_event_is_structured_control(self) -> None:
        record = self.normalize("_RESET_ALL_FX")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_control_or_music")
        self.assertEqual(record.semantic_slots["control_scope"], "control")
        self.assertEqual(record.semantic_slots["cue_path"], ["ALL", "FX"])
        self.assertFalse(record.unknowns)

    def test_named_level_control_is_structured_state(self) -> None:
        record = self.normalize("KayleSkin06_Level_01")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_state")
        self.assertEqual(record.semantic_slots["state_group"], "Level")
        self.assertEqual(record.semantic_slots["state_path"], ["01"])
        self.assertFalse(record.unknowns)

    def test_named_pack_size_control_is_structured_state(self) -> None:
        record = self.normalize("NaafiriSkin01_PackSize_Large")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_state")
        self.assertEqual(record.semantic_slots["state_group"], "PackSize")
        self.assertEqual(record.semantic_slots["state_path"], ["Large"])
        self.assertFalse(record.unknowns)

    def test_named_voice_form_control_is_structured_state(self) -> None:
        record = self.normalize("Elise_VO_Human")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_state")
        self.assertEqual(record.semantic_slots["state_group"], "VO")
        self.assertEqual(record.semantic_slots["state_path"], ["Human"])
        self.assertFalse(record.unknowns)

    def test_set_param_event_is_structured_parameter_control(self) -> None:
        record = self.normalize("Taliyah_SetParam_PassiveAnimOff")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_parameter")
        self.assertEqual(record.semantic_slots["parameter_group"], "SetParam")
        self.assertEqual(record.semantic_slots["parameter_path"], ["PassiveAnimOff"])
        self.assertFalse(record.unknowns)

    def test_rtpc_event_is_structured_parameter_control(self) -> None:
        record = self.normalize("Set_RTPC_SeraphineSkin43_RHits1")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_parameter")
        self.assertEqual(record.semantic_slots["parameter_group"], "rtpc")
        self.assertEqual(record.semantic_slots["parameter_path"], ["RHits1"])
        self.assertFalse(record.unknowns)

    def test_compact_set_state_event_is_structured_state_control(self) -> None:
        record = self.normalize("SetState_Ally1")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "audio_state")
        self.assertEqual(record.semantic_slots["state_group"], "SetState")
        self.assertEqual(record.semantic_slots["state_path"], ["Ally1"])
        self.assertFalse(record.unknowns)

    def test_unprefixed_effect_event_is_structured_other_control(self) -> None:
        record = self.normalize("Play_jinx_skin60_slotmachine_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.profile.primary_family, "skin_named_state")
        self.assertEqual(record.semantic_slots["trigger_family"], "skin_named_state")
        self.assertEqual(record.semantic_slots["state_key"], "slotmachine")
        self.assertEqual(record.semantic_slots["cue_path"], ["slotmachine", "buffactivate"])
        self.assertFalse(record.unknowns)

    def test_sfx_taunt_missile_tail_is_effect_variant_not_counterpart(self) -> None:
        record = self.normalize("Play_sfx_SamiraSkin01_SamiraTauntMissile_OnHit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "taunt")
        self.assertEqual(record.semantic_slots["sfx_variant"], "Missile")
        self.assertEqual(record.semantic_slots["phase"], "hit")
        self.assertFalse(record.unknowns)

    def test_sfx_taunt_letter_tail_is_effect_variant_not_counterpart(self) -> None:
        record = self.normalize("Play_sfx_MissFortuneSkin31_TauntA3D_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "taunt")
        self.assertEqual(record.semantic_slots["sfx_variant"], "A")
        self.assertFalse(record.unknowns)
