"""VO 全量核对发现的武器普攻与带对象被动语法回归。"""

from __future__ import annotations

import pytest
from reference_cache import reference_index, translation_context

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import enrich_with_reference
from lol_event_semantics.normalized_translation_builder import build_translation


@pytest.mark.parametrize(
    ("weapon", "label"),
    [("Calibrum", "通碧"), ("Severum", "断魄"), ("Gravitum", "坠明"), ("Infernum", "荧焰"), ("Crescendum", "折镜")],
)
def test_aphelios_weapon_attacks_are_not_q_casts(weapon: str, label: str) -> None:
    """五把武器的 Attack 与同名 Q 保持独立，武器身份进入最终 frame。"""
    raw = f"Play_vo_Aphelios_Aphelios{weapon}Attack_cast3D"
    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.raw_event_name == raw
    assert record.profile.primary_family == "basic_attack"
    assert record.semantic_slots["attack_kind"] == "basic_attack"
    assert record.semantic_slots.get("spell_key") is None
    assert record.semantic_slots["dimension_token"] == "3D"
    assert record.semantic_slots["phase"] == "cast"
    assert record.semantic_slots["contextual_alias"]["component"] == f"{weapon.lower()}_basic_attack"
    frame = build_translation(record, translation_context())
    assert frame is not None
    assert label in frame["arguments"]["attack"]["labels"]["zh_CN"]
    q_raw = f"Play_vo_Aphelios_Aphelios{weapon}Q_cast3D"
    q_record = enrich_with_reference(
        normalize_semantics(decompose_event_name(q_raw, game_version="16.20")), reference_index()
    )
    assert q_record.profile.primary_family == "spell"
    assert (q_record.semantic_slots.get("spell_key") or q_record.semantic_slots.get("spell_key_hint")) == "q"


@pytest.mark.parametrize("owner", ["Swain", "SwainSkin11", "Neeko"])
@pytest.mark.parametrize("target", ["Ashe", "Void", "General", "UnknownCondition", "NidaleeSkin11"])
def test_targeted_passive_cast_preserves_key_phase_and_target(owner: str, target: str) -> None:
    """限定英雄的 PCast 尾部保留被动、触发阶段及完整对象。"""
    raw = f"Play_vo_{owner}_Spell2DPCast{target}"
    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.raw_event_name == raw
    assert record.profile.primary_family == "spell"
    assert record.semantic_slots["spell_key"] == "p"
    assert record.semantic_slots["phase"] == "cast"
    assert record.semantic_slots["dimension_token"] == "2D"
    assert record.semantic_slots["spell_context"]["raw"] == target
    frame = build_translation(record, translation_context())
    assert frame is not None
    assert frame["arguments"]["ability_slot"]["key"] == "p"
    assert "spell_context" in frame["arguments"]


@pytest.mark.parametrize(
    "raw",
    [
        "Play_vo_Ahri_Spell2DPCastAshe",
        "Play_vo_Neeko_Spell3DPCastAshe",
        "Play_sfx_Neeko_Spell2DPCastAshe",
        "Play_vo_Neeko_Spell2DQCastAshe",
        "Play_vo_Neeko_Spell2DPCast",
        "Play_vo_Neeko_Spell2DPCastAhri_01",
        "Play_vo_Swain_Spell2DPCastAshe_cast3DLowHealth",
    ],
)
def test_targeted_passive_grammar_does_not_expand_other_prefixes(raw: str) -> None:
    """不把其他英雄、维度、音频类型或未确认的前缀归入新语法。"""
    record = normalize_semantics(decompose_event_name(raw, game_version="16.20"))
    assert "spell_context" not in record.semantic_slots
