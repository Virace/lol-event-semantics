"""全量 VO 复核发现的分类碰撞回归。"""

import pytest

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics


@pytest.mark.parametrize("owner", ["KaisaSkin71", "TristanaSkin80", "AhriSkin86"])
@pytest.mark.parametrize("outcome", ["Victory", "Defeat"])
def test_champion_game_end_keeps_outcome(owner: str, outcome: str) -> None:
    """英雄的胜负结束事件不能被中立生物 Victory 后缀规则截获。"""
    raw = f"Play_vo_{owner}_GameEnd2D{outcome}"
    record = normalize_semantics(decompose_event_name(raw, game_version="16.20"))
    assert record.raw_event_name == raw
    assert record.profile.primary_family == "game_end"
    assert record.semantic_slots["qualifier"] == outcome
    assert record.semantic_slots["dimension_token"] == "2D"


@pytest.mark.parametrize(
    ("raw", "action"),
    [
        ("Play_vo_Bluegolem_Victory2D", "victory"),
        ("Play_vo_Atakhan_AggroReset", "aggro_reset"),
        ("Play_vo_Atakhan_AggroStart", "aggro_start"),
        ("Play_vo_Atakhan_Ambient", "ambient"),
        ("Play_vo_Atakhan_Spawn", "spawn"),
    ],
)
def test_neutral_creature_voice_keeps_existing_actions(raw: str, action: str) -> None:
    """修复英雄结束事件时保留实际中立生物动作。"""
    record = normalize_semantics(decompose_event_name(raw, game_version="16.20"))
    assert record.profile.primary_family == "creature_voice"
    assert record.semantic_slots["creature_action"] == action


@pytest.mark.parametrize("owner", ["Shyvana", "ShyvanaSkin08"])
@pytest.mark.parametrize(
    ("action", "family"),
    [
        ("Attack2DGeneral", "attack"),
        ("Joke3DGeneral", "joke"),
        ("Kill3DGeneral", "kill"),
        ("Kill3DPenta", "kill"),
        ("Taunt3DGeneral", "taunt"),
    ],
)
def test_shyvana_dragon_form_preserves_action(owner: str, action: str, family: str) -> None:
    """龙形态修饰保留真实动作，不能覆盖为中立野怪分类。"""
    from reference_cache import reference_index, translation_context

    from lol_event_semantics.decomposition_reference import enrich_with_reference
    from lol_event_semantics.normalized_translation_builder import build_translation

    raw = f"Play_vo_{owner}_Dragon_{action}"
    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.raw_event_name == raw
    assert record.profile.primary_family == family
    assert record.semantic_slots["action_form"]["component"] == "dragon_form"
    if action == "Kill3DPenta":
        assert record.semantic_slots["milestone"] == "penta"
    translation = build_translation(record, translation_context())
    assert translation is not None
    arguments = translation["arguments"]
    action_argument = arguments.get("action") or arguments.get("trigger")
    assert action_argument is not None
    assert action_argument["form"] == "dragon_form"
    assert action_argument["labels"]["zh_CN"].startswith("龙形态")


@pytest.mark.parametrize(
    "raw",
    [
        "Play_vo_IvernSkin11_Dragon",
        "Play_vo_NexusBlitz_Murkwolf_MurkwolfBasicAttack_Attack3D",
        "Play_vo_NexusBlitz_Gromp_GrompBasicAttack_Attack3D",
    ],
)
def test_neutral_monster_context_is_not_shyvana_form(raw: str) -> None:
    """其他英雄与模式中的真实野怪上下文不受龙形态规则影响。"""
    from reference_cache import reference_index

    from lol_event_semantics.decomposition_reference import enrich_with_reference

    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.profile.primary_family == "neutral_monster"
    assert "action_form" not in record.semantic_slots


@pytest.mark.parametrize("owner", ["Viktor", "ViktorSkin01", "ViktorSkin02", "ViktorSkin05"])
@pytest.mark.parametrize("slot", ["Q", "W", "E", "R"])
def test_viktor_augment_voice_is_permanent_ability_upgrade(owner: str, slot: str) -> None:
    """维克托升级语音按已知进化机制解释，同时保留原始 Switch 片段。"""
    from reference_cache import reference_index, translation_context

    from lol_event_semantics.decomposition_reference import enrich_with_reference
    from lol_event_semantics.normalized_translation_builder import build_translation
    from lol_event_semantics.normalized_translation_renderer import build_compat_display

    raw = f"Play_vo_{owner}_Switch_{slot}Augment"
    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.raw_event_name == raw
    assert record.profile.primary_family == "spell"
    assert record.semantic_slots["spell_key"] == slot.lower()
    assert record.semantic_slots["contextual_alias"]["component"] == "permanent_ability_augment"
    assert any(atom.raw == "Switch" for atom in record.lexical_atoms)
    translation = build_translation(record, translation_context())
    assert translation is not None
    label = build_compat_display(translation)["zh_cn_short"]
    assert slot in label
    assert "升级" in label
    assert "释放" not in label


def test_viktor_switch_shell_stays_technical() -> None:
    """真实 Switch 外壳不能因同名 Augment 词被改成升级语音。"""
    from reference_cache import reference_index

    from lol_event_semantics.decomposition_reference import enrich_with_reference

    raw = "Switch_vo_Viktor_Switch_QAugment"
    record = enrich_with_reference(
        normalize_semantics(decompose_event_name(raw, game_version="16.20")), reference_index()
    )
    assert record.profile.primary_family == "switch"
    assert "contextual_alias" not in record.semantic_slots
