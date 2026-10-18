"""稀疏事件的保守词典解释与原名保留回归。"""

from copy import deepcopy
from pathlib import Path

import pytest
from reference_cache import reference_index
from reference_cache import translation_context

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import EventDecompositionRecord
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import enrich_with_reference
from lol_event_semantics.entity_attribution import AttributionContext, resolve_entity
from lol_event_semantics.mode_namespace import MODE_NAMESPACES
from lol_event_semantics.normalized_translation_builder import build_translation
from lol_event_semantics.normalized_translation_model import RenderOptions
from lol_event_semantics.normalized_translation_renderer import build_compat_display, render_translation
from lol_event_semantics.pattern_catalog import canonical_pattern_signature, load_pattern_policy


def enrich(raw: str) -> EventDecompositionRecord:
    """使用真实参考词典拆解单条事件。"""
    return enrich_with_reference(normalize_semantics(decompose_event_name(raw, game_version="test")), reference_index())


@pytest.mark.parametrize("owner", ["Urgot", "UrgotSkin01", "UrgotSkin03"])
def test_mount_targon_full_spelling_preserves_region_and_raw(owner: str) -> None:
    """完整地名只补足地区类型，不提升人工可信级别。"""
    raw = f"Play_vo_{owner}_UrgotRRecastMissile_cast3DMountTargon"
    record = enrich(raw)
    assert record.raw_event_name == raw
    context = record.semantic_slots["spell_context"]
    assert context["kind"] == "region"
    assert context["label"] == "Mount Targon"
    assert not any(unknown.raw == "MountTargon" for unknown in record.unknowns)
    entity = reference_index().resolve_token("MountTargon")
    assert entity is not None
    assert entity.metadata["trust"] == "manual_untrusted"


def test_unknown_region_extension_remains_unresolved() -> None:
    """补足地名不得吞掉未知限定词。"""
    record = enrich("Play_vo_Urgot_UrgotRRecastMissile_cast3DMountTargonUnknown")
    assert any(unknown.raw == "MountTargonUnknown" for unknown in record.unknowns)


@pytest.mark.parametrize("owner", ["MissFortuneSkin69", "SeraphineSkin69", "TristanaSkin80"])
def test_exact_ally_near_move_keeps_condition_in_translation(owner: str) -> None:
    """已见友方附近移动词尾保留关系条件，不伪造具体英雄。"""
    raw = f"Play_vo_{owner}_Move2DAllyNear"
    record = enrich(raw)
    assert record.raw_event_name == raw
    context = record.semantic_slots["movement_context"]
    assert context["raw"] == "AllyNear"
    assert context["kind"] == "team_proximity"
    assert context["key"] == "ally_near"
    assert record.semantic_slots["trigger_family"] == "move"
    assert not record.unknowns
    translation = build_translation(record, translation_context())
    assert translation is not None
    qualifier = translation["arguments"]["qualifier"]
    assert qualifier["token"] == "AllyNear"
    assert not qualifier["fallback"]
    assert "友方在附近" in build_compat_display(translation)["zh_cn_short"]


@pytest.mark.parametrize("tail", ["AllyNearUnknown", "EnemyNear", "AllyFar"])
def test_unreviewed_nearby_move_variants_stay_unknown(tail: str) -> None:
    """精确新增词条不外推其他关系、距离或未知条件。"""
    record = enrich(f"Play_vo_TristanaSkin80_Move2D{tail}")
    assert any(unknown.raw == tail for unknown in record.unknowns)


@pytest.mark.parametrize("guest", ["Atakhan", "Lamb", "Wolf", "Jarvan", "Yone", "UnknownGuest"])
def test_guest_namespace_preserves_tokens_without_champion_inference(guest: str) -> None:
    """嘉宾机制保留角色词和未知场景，不将其推断成英雄演员。"""
    raw = f"Play_vo_GoH_{guest}_UnreviewedScenario"
    record = enrich(raw)
    assert record.raw_event_name == raw
    namespace = record.semantic_slots["context_namespace"]
    assert namespace["key"] == "guests_of_honor"
    assert namespace["kind"] == "mechanism"
    assert namespace["guest_token"] == guest
    assert namespace["cue_path"] == ["UnreviewedScenario"]
    assert "mode_actor" not in record.semantic_slots
    assert "subject" not in record.semantic_slots
    assert [atom.raw for atom in record.lexical_atoms] == [guest, "UnreviewedScenario"]
    assert any(unknown.raw == "UnreviewedScenario" for unknown in record.unknowns)
    entity = resolve_entity(record, AttributionContext(translation_context()))
    assert entity["kind"] == "system"
    assert entity["alias"] == "guests_of_honor"


def test_guest_namespace_is_not_inferred_from_nonowner_cue() -> None:
    """嘉宾缩写只接受 owner 位置，不劫持普通英雄词尾。"""
    record = enrich("Play_vo_Annie_Move2DGoH")
    assert "context_namespace" not in record.semantic_slots


@pytest.mark.parametrize(
    "guest,cue",
    [
        ("Atakhan", "ReachMaxKills"),
        ("Yone", "YourTeamJoinsDemons"),
        ("Yone", "YourTeamJoinsHunters"),
        ("Lamb", "AugmentSelected"),
        ("Wolf", "AugmentSelected"),
        ("Jarvan", "SelfBecomesKing"),
    ],
)
def test_actual_guest_cues_remain_unreviewed(guest: str, cue: str) -> None:
    """真实新增嘉宾事件只补机制归类，场景原文继续等待精确核验。"""
    record = enrich(f"Play_vo_GoH_{guest}_{cue}")
    assert record.semantic_slots["context_namespace"]["cue_path"] == [cue]
    assert any(unknown.raw == cue for unknown in record.unknowns)
    assert "mode_actor" not in record.semantic_slots
    assert resolve_entity(record, AttributionContext(translation_context()))["kind"] != "champion"


def test_existing_namespace_actor_policy_is_unchanged() -> None:
    """已支持命名空间继续使用原有演员解析策略。"""
    for namespace in MODE_NAMESPACES:
        if namespace.prefix != "GoH":
            assert namespace.resolve_actor
            assert not namespace.owner_only
    record = enrich("Play_sfx_Ruby_Brand_P")
    assert record.semantic_slots["mode_actor"]["alias"] == "Brand"


@pytest.mark.parametrize("cue", ["AhriE_cast", "AhriR_cast", "AhriEMissile_hit", "AhriWDamageMissile_hit"])
def test_identical_speaker_skin_renders_once_without_changing_pattern(cue: str) -> None:
    """同主体皮肤仅去除重复显示，完整角色及模式签名继续保留。"""
    raw = f"Play_vo_AhriSkin04_{cue}3DSkin04"
    record = enrich(raw)
    translation = build_translation(record, translation_context())
    assert translation is not None
    argument = translation["arguments"]["spell_context"]
    assert argument["token"] == "Skin04"
    assert argument["alias"] == "AhriSkin04"
    assert argument["provenance"]["context_scope"] == "speaker_skin"
    assert argument["provenance"]["rendered_via"] == "skin"
    assert record.semantic_slots["spell_context"]["context_scope"] == "speaker_skin"
    for mode in ("default", "main"):
        result = render_translation(translation, RenderOptions(locale="zh_CN", mode=mode))
        assert "（" not in result.text
        assert "spell_context" in result.omitted_roles
        assert "spell_context" not in result.used_roles
        assert result.text.count("偶像歌手") == (1 if mode == "default" else 0)
    old = deepcopy(translation)
    del old["arguments"]["spell_context"]["provenance"]["rendered_via"]
    del old["arguments"]["spell_context"]["provenance"]["context_scope"]
    policy = load_pattern_policy(Path(__file__).parents[1] / "rules/translation_patterns.yaml")
    row = {
        "raw_event_name": raw,
        "semantic": {"profile": "spell_event", "primary_family": "spell", "slots": record.semantic_slots},
    }
    assert canonical_pattern_signature({**row, "translation": translation}, policy) == canonical_pattern_signature(
        {**row, "translation": old}, policy
    )


@pytest.mark.parametrize("tail", ["Skin05", "AhriSkin04", "NidaleeSkin11", "UnknownCondition"])
def test_other_or_unconfirmed_spell_context_is_not_suppressed(tail: str) -> None:
    """其他目标、显式皮肤目标及未知限定均不得按说话者皮肤省略。"""
    record = enrich(f"Play_vo_AhriSkin04_AhriE_cast3D{tail}")
    unknowns = deepcopy(record.unknowns)
    translation = build_translation(record, translation_context())
    assert translation is not None
    assert "rendered_via" not in translation["arguments"]["spell_context"]["provenance"]
    result = render_translation(translation, RenderOptions(locale="zh_CN", mode="main"))
    assert "spell_context" in result.used_roles
    assert "spell_context" not in result.omitted_roles
    assert record.unknowns == unknowns
