"""事件名拆解 profile 与语义 slot 归一化。"""

from __future__ import annotations

import re
from typing import Any

from .decomposition_lexer import SHELL_VERBS
from .decomposition_model import (
    DecompositionConfidence,
    EventDecompositionRecord,
    EventProfile,
    LexicalAtom,
    SemanticProfile,
    UnknownFragment,
    UnknownKind,
)

INTERACTION_TRIGGERS = {"attack", "taunt", "assist"}
SIMPLE_ACTION_TRIGGERS = {
    "basicattack",
    "buyitem",
    "critattack",
    "dance",
    "danceresponse",
    "death",
    "firstmove",
    "gameend",
    "heal",
    "homeguard",
    "idle",
    "joke",
    "joketauntresponse",
    "jokeresponse",
    "laugh",
    "levelup",
    "masteryresponse",
    "ping",
    "recall",
    "respawn",
    "shop",
    "tauntresponse",
    "unique",
    "useitem",
    "winddown",
}
ACTION_SEGMENT_PHASES = {
    "end": "end",
    "leadin": "leadin",
    "leadout": "leadout",
    "loop": "loop",
    "start": "start",
    "winddown": "winddown",
    "windup": "windup",
}
SKIN_ACTION_STATE_TRIGGERS = {
    "death",
    "joke",
    "laugh",
    "respawn",
    "taunt",
}
LEADING_ACTION_QUALIFIERS = {
    "skineasteregg": {
        "key": "skin_easter_egg",
        "label": "Skin Easter Egg",
    },
}
OTHER_CONTROL_HEADS = {
    "channel",
    "event",
    "firstblood",
    "hacked",
    "health",
    "missile",
    "opponent",
    "packsize",
    "setparam",
    "slotmachine",
    "state",
    "switch",
    "tip",
    "transition",
}
NAMED_CONTROL_STATE_HEADS = {
    "fx",
    "health",
    "level",
    "packsize",
    "vo",
}
VOICE_CONDITION_ALIASES = {
    "VOAllyDeath": {"key": "ally_death", "label": "Ally Death"},
    "VOBindAlly": {"key": "bind_ally", "label": "Bind Ally"},
    "VOBindAllyGlobal": {"key": "bind_ally_global", "label": "Bind Ally Global"},
    "VOUnBound": {"key": "unbound", "label": "Unbound"},
}
MOVEMENT_CONTEXT_RESIDUES = {
    "Base": {"kind": "location", "key": "base", "label": "Base"},
    "River": {"kind": "location", "key": "river", "label": "River"},
    "Jungle": {"kind": "location", "key": "jungle", "label": "Jungle"},
    "Sentinel": {"kind": "neutral_objective", "key": "sentinel", "label": "Sentinel"},
    "Brambleback": {"kind": "neutral_camp", "key": "brambleback", "label": "Red Brambleback"},
    "Gromp": {"kind": "neutral_camp", "key": "gromp", "label": "Gromp"},
    "Krugs": {"kind": "neutral_camp", "key": "krugs", "label": "Krugs"},
    "Raptors": {"kind": "neutral_camp", "key": "raptors", "label": "Raptors"},
    "Wolves": {"kind": "neutral_camp", "key": "wolves", "label": "Wolves"},
    "EpicMonster": {"kind": "target_class", "key": "epic_monster", "label": "Epic Monster"},
    "Homeguard": {"kind": "movement_state", "key": "homeguard", "label": "Homeguard"},
    "Rally": {"kind": "team_state", "key": "rally", "label": "Rally"},
    "Winning": {"kind": "match_state", "key": "winning", "label": "Winning"},
    "Losing": {"kind": "match_state", "key": "losing", "label": "Losing"},
    "Disguised": {"kind": "character_state", "key": "disguised", "label": "Disguised"},
    "Order": {"kind": "command", "key": "move_order", "label": "Move Order"},
}
TRIGGER_FAMILY_NAMES = {
    "basicattack": "basic_attack",
    "buyitem": "buy_item",
    "critattack": "crit_attack",
    "firstencounter": "first_encounter",
    "firstmove": "first_move",
    "gameend": "game_end",
    "joketauntresponse": "joke_taunt_response",
    "danceresponse": "dance_response",
    "jokeresponse": "joke_response",
    "killchampion": "kill",
    "levelup": "level_up",
    "masteryresponse": "mastery_response",
    "tauntresponse": "taunt_response",
    "useitem": "use_item",
}

ATTACK_CUE_PATTERN = re.compile(
    r"(?P<attack>BasicAttack|CritAttack|RangedAttack|MeleeAttack)(?P<variant>[0-9]*)$",
    re.IGNORECASE,
)
NUMERIC_SPELL_RANK_PATTERN = re.compile(
    r"(?:Spell)?(?:2D|3D)(?P<spell_key>[QWERP])Rank(?P<rank>[0-9]+)$",
    re.IGNORECASE,
)
NON_SPELL_PREFIX_WORDS = frozenset({"execute", "quadruped", "rangedattack"})
OWNER_PREFIXED_NON_SPELL_ACTIONS = {
    ("renekton", "execute"): "execute_action",
}


def normalize_semantics(record: EventDecompositionRecord) -> EventDecompositionRecord:
    """根据词法 atom 填充 profile、slot 和未知项。"""

    first_atom = record.lexical_atoms[0] if record.lexical_atoms else None

    if first_atom is None:
        if fill_ownerless_sfx_event(record):
            return record

        if fill_other_audio_or_control_event(record, first_atom):
            return record

        if fill_ownerless_voice_event(record):
            return record

        set_unknown_profile(record, "record has no lexical atoms")
        return record

    trigger = first_part_value(first_atom, "trigger_token")

    if fill_shell_switch_event(record, first_atom):
        return record
    elif fill_system_voice_group(record, first_atom):
        return record
    elif fill_structured_state_event(record):
        return record
    elif fill_champion_execute_action_event(record):
        return record
    elif fill_champion_attack_event(record):
        return record
    elif (
        trigger is None
        and should_prefer_generic_sfx(record, first_atom, trigger)
        and fill_sfx_event(record, first_atom)
    ):
        return record
    elif fill_skin_recall_state_event(record):
        return record
    elif fill_skin_idle_state_event(record):
        return record
    elif fill_skin_action_state_event(record):
        return record
    elif fill_other_audio_or_control_event(record, first_atom):
        return record
    elif fill_nearby_prefixed_event(record, first_atom):
        return record
    elif fill_leading_qualified_action(record, first_atom):
        return record
    elif fill_leading_contextual_action(record, first_atom):
        return record
    elif fill_special_champion_voice_event(record, first_atom):
        return record
    elif trigger == "move":
        fill_move(record, first_atom)
    elif trigger == "firstencounter":
        fill_first_encounter(record, first_atom)
    elif trigger in {"kill", "killchampion"}:
        fill_kill(record, first_atom)
    elif trigger == "spell" or first_part_value(first_atom, "spell_key_token") is not None:
        fill_spell(record, first_atom)
    elif should_prefer_generic_sfx(record, first_atom, trigger) and fill_sfx_event(record, first_atom):
        return record
    elif trigger == "chat":
        fill_dialogue_chain(record)
    elif trigger == "receive":
        fill_receive(record, first_atom)
    elif trigger == "lowhealth":
        fill_low_health(record, first_atom)
    elif trigger in INTERACTION_TRIGGERS:
        fill_interaction_or_action(record, first_atom, trigger)
    elif trigger in SIMPLE_ACTION_TRIGGERS:
        fill_simple_action(record, first_atom, trigger)
    else:
        set_unknown_profile(record, f"unsupported trigger: {trigger or first_atom.raw}")

    return record


def fill_leading_qualified_action(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 skineasteregg_recall 这类前置限定词动作。"""

    qualifier = LEADING_ACTION_QUALIFIERS.get(atom.raw.lower())

    if qualifier is None or len(record.lexical_atoms) < 2:
        return False

    action_atom = record.lexical_atoms[1]
    trigger = first_part_value(action_atom, "trigger_token") or action_atom.raw.lower()

    if trigger not in SIMPLE_ACTION_TRIGGERS:
        return False

    fill_simple_action(record, action_atom, trigger)
    record.profile.reason = f"matched {trigger} trigger with leading qualifier"
    record.semantic_slots["pre_qualifier"] = qualifier
    return True


def fill_nearby_prefixed_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 WithLucianNear_Heal2D / WithLucianNear_..._Chat02 这类前置条件事件。"""

    precondition = parse_nearby_context(atom.raw)

    if precondition is None or len(record.lexical_atoms) < 2:
        return False

    turn_atom = record.lexical_atoms[-1]

    if len(record.lexical_atoms) >= 3 and first_part_value(turn_atom, "trigger_token") == "chat":
        fill_dialogue_chain_slots(
            record=record,
            chat_partner=None,
            base_atom=record.lexical_atoms[1],
            turn_atom=turn_atom,
            precondition=precondition,
            reason="matched nearby champion dialogue precondition",
        )
        return True

    action_atom = record.lexical_atoms[1]

    if not fill_action_from_atom(record, action_atom, allow_spell_key=True):
        return False

    record.semantic_slots["precondition"] = precondition
    record.profile.reason = f"{record.profile.reason} with nearby champion precondition"
    return True


def fill_leading_contextual_action(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Sentinel_Attack2DGeneral 这类前置上下文加已知动作。"""

    if first_part_value(atom, "trigger_token") is not None or len(record.lexical_atoms) < 2:
        return False

    action_atom = record.lexical_atoms[1]

    if first_part_value(action_atom, "trigger_token") is None:
        return False

    if not fill_action_from_atom(record, action_atom, allow_spell_key=False):
        return False

    record.semantic_slots["pre_context"] = {
        "raw": atom.raw,
        "kind": "voice_context",
        "source": "leading_chunk",
    }
    record.profile.requires_context = True
    record.profile.reason = f"{record.profile.reason} with leading voice context"
    record.confidence = min_confidence(record.confidence, DecompositionConfidence.MEDIUM)
    return True


def fill_action_from_atom(record: EventDecompositionRecord, atom: LexicalAtom, allow_spell_key: bool) -> bool:
    """按单个 action atom 填充已知动作结构。"""

    trigger = first_part_value(atom, "trigger_token")

    if trigger == "move":
        fill_move(record, atom)
    elif trigger == "firstencounter":
        fill_first_encounter(record, atom)
    elif trigger in {"kill", "killchampion"}:
        fill_kill(record, atom)
    elif trigger == "spell" or (allow_spell_key and first_part_value(atom, "spell_key_token") is not None):
        fill_spell(record, atom)
    elif trigger == "receive":
        fill_receive(record, atom)
    elif trigger == "lowhealth":
        fill_low_health(record, atom)
    elif trigger in INTERACTION_TRIGGERS:
        fill_interaction_or_action(record, atom, trigger)
    elif trigger in SIMPLE_ACTION_TRIGGERS:
        fill_simple_action(record, atom, trigger)
    else:
        return False

    return True


def fill_system_voice_group(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别教程、通告员和特殊模式 VO 分组。"""

    if record.shell.asset_kind != "vo":
        return False

    owner = record.owner.alias or ""
    owner_key = owner.lower()
    slots: dict[str, object]

    if re.fullmatch(r"tutorial[0-9]*", owner_key, re.IGNORECASE):
        slots = {
            "trigger_family": "system_voice",
            "system_voice_kind": "tutorial",
            "section": atom.raw,
            "cue_path": [tail.raw for tail in record.lexical_atoms[1:]],
        }
    elif owner_key == "announcer":
        persona_atom = record.lexical_atoms[1] if len(record.lexical_atoms) > 1 else None
        persona = parse_announcer_persona(persona_atom)
        cue_start = 2 if persona is not None else 1
        slots = {
            "trigger_family": "system_voice",
            "system_voice_kind": "announcer",
            "mode": atom.raw,
            "announcer_persona": persona,
            "cue_path": [tail.raw for tail in record.lexical_atoms[cue_start:]],
        }
    elif owner_key == "gamemode":
        slots = {
            "trigger_family": "system_voice",
            "system_voice_kind": "game_mode",
            "mode": atom.raw,
            "cue_path": [tail.raw for tail in record.lexical_atoms[1:]],
        }
    elif owner_key == "nexusblitz":
        slots = {
            "trigger_family": "system_voice",
            "system_voice_kind": "game_mode",
            "mode": "NexusBlitz",
            "actor": atom.raw,
            "cue_path": [tail.raw for tail in record.lexical_atoms[1:]],
        }
    elif owner_key == "emotes":
        slots = {
            "trigger_family": "system_voice",
            "system_voice_kind": "emote_voice",
            "emotion": atom.raw,
            "cue_path": [tail.raw for tail in record.lexical_atoms[1:]],
        }
    else:
        return False

    record.profile = EventProfile(
        primary_family="system_voice",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=False,
        reason=f"matched {slots['system_voice_kind']} voice group",
    )
    record.semantic_slots = slots
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_shell_switch_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Switch_VO_* 这类 Wwise switch 控制事件。"""

    if record.shell.verb != "switch":
        return False

    record.profile = EventProfile(
        primary_family="switch",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched switch shell event",
    )
    record.semantic_slots = {
        "trigger_family": "switch",
        "switch_group": record.owner.raw_owner_token or record.owner.alias,
        "switch_target": atom.raw,
        "switch_path": [tail.raw for tail in record.lexical_atoms],
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_special_champion_voice_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别第一批英雄 VO 中没有显式 trigger 的常见事件结构。"""

    if record.shell.asset_kind != "vo":
        return False

    return (
        fill_interactive_voice(record, atom)
        or fill_phase_only_owner_spell_event(record, atom)
        or fill_neutral_creature_voice_event(record, atom)
        or fill_animation_event(record, atom)
        or fill_clone_event(record, atom)
        or fill_enter_camp_event(record, atom)
        or fill_forge_item_event(record, atom)
        or fill_purchase_event(record, atom)
        or fill_mastery_event(record, atom)
        or fill_transform_event(record, atom)
        or fill_weapon_upgrade_event(record, atom)
        or fill_second_encounter_event(record, atom)
        or fill_recal_partner_event(record, atom)
        or fill_nearby_observed_event(record, atom)
        or fill_rally_team_event(record, atom)
        or fill_dimension_prefixed_kill_milestone(record, atom)
        or fill_clear_camp_event(record, atom)
        or fill_open_event(record, atom)
        or fill_partner_dance_event(record, atom)
        or fill_spell_outcome_event(record, atom)
        or fill_summoner_spell_event(record, atom)
        or fill_reversed_move_event(record, atom)
        or fill_owner_prefixed_attack_event(record, atom)
        or fill_weapon_select_event(record, atom)
        or fill_objective_destroy_event(record, atom)
        or fill_resource_spell_event(record, atom)
        or fill_threshold_state_event(record, atom)
        or fill_voice_condition_event(record, atom)
        or fill_leading_contextual_attack_event(record, atom)
        or fill_leading_contextual_spell_alias(record, atom)
        or fill_untrusted_spell_alias_event(record, atom)
        or fill_buff_state_event(record, atom)
    )


def fill_owner_prefixed_attack_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 ShyvanaBasicAttack2 / Senna3DLongBasic 这类带 owner 前缀的普攻语音。"""

    if record.shell.asset_kind != "vo":
        return False

    for raw in owner_prefixed_attack_lookup_tokens(record, atom):
        match = re.fullmatch(r"(?P<context>[A-Za-z0-9]*?)(?P<attack>BasicAttack|CritAttack|LongBasic)", raw)

        if match is None:
            continue

        attack = match.group("attack")
        context = match.group("context") or None
        attack_kind = "crit_attack" if attack == "CritAttack" else "basic_attack"
        slots: dict[str, object] = {
            "trigger_family": "attack",
            "attack_kind": attack_kind,
            "dimension_token": first_dimension(record),
            "phase": first_phase(record),
            "source": "owner_prefixed_attack_event_name",
        }

        if attack == "LongBasic":
            slots["attack_range"] = "long"
            slots["attack_form"] = "ranged"

        if context is not None:
            slots["attack_context"] = context

        rank = first_number(atom)

        if rank is not None:
            slots["attack_rank"] = rank

        record.profile = EventProfile(
            primary_family="attack",
            semantic_profile=SemanticProfile.SELF_ACTION,
            requires_context=True,
            reason="matched owner-prefixed attack voice event",
        )
        record.semantic_slots = slots
        record.confidence = DecompositionConfidence.MEDIUM
        return True

    return False


def owner_prefixed_attack_lookup_tokens(record: EventDecompositionRecord, atom: LexicalAtom) -> list[str]:
    """返回用于识别 owner-prefixed attack 的候选 token。"""

    raw_tokens = [first_part_raw(atom, "residue_token"), strip_owner_alias(atom.raw, record.owner.alias), atom.raw]
    tokens: list[str] = []

    for raw in raw_tokens:
        if raw is None:
            continue

        tokens.append(raw)
        tokens.append(re.sub(r"[0-9]+$", "", raw))

    deduped: list[str] = []

    for token in tokens:
        if not token or token in deduped:
            continue

        deduped.append(token)

    return deduped


def fill_ownerless_voice_event(record: EventDecompositionRecord) -> bool:
    """识别没有 owner/tail 切分的 VO 事件体。"""

    if record.shell.asset_kind != "vo":
        return False

    raw = record.owner.raw_owner_token or record.owner.alias or ""
    kill_match = re.fullmatch(r"Kill(?P<dimension>[0-9]D)?(?P<residue>[A-Za-z]+)", raw)

    if kill_match is not None:
        milestone = normalize_kill_milestone(kill_match.group("residue"))

        if milestone is not None:
            record.profile = EventProfile(
                primary_family="kill",
                semantic_profile=SemanticProfile.KILL_OR_SCORE,
                requires_context=False,
                reason="matched ownerless kill milestone voice body",
            )
            record.semantic_slots = {
                "trigger_family": "kill",
                "dimension_token": kill_match.group("dimension"),
                "milestone": milestone,
                "counter": None,
                "kill_method": None,
                "objective_steal": None,
                "counterpart": None,
            }
            record.confidence = DecompositionConfidence.MEDIUM
            record.unknowns.clear()
            return True

    phantom_match = re.fullmatch(r"PhantomVoice(?P<variant>[0-9]+)", raw)

    if phantom_match is None:
        return False

    record.profile = EventProfile(
        primary_family="phantom_voice",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=True,
        reason="matched ownerless phantom voice body",
    )
    record.semantic_slots = {
        "trigger_family": "phantom_voice",
        "line_variant": int(phantom_match.group("variant")),
        "raw_voice_group": raw,
        "source": "event_name_heuristic",
    }
    record.confidence = DecompositionConfidence.LOW
    record.unknowns.clear()
    return True


def fill_interactive_voice(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Interactive_Interactive3DChampionSpecific21 这类交互语音。"""

    if atom.raw.lower() != "interactive" or len(record.lexical_atoms) < 2:
        return False

    cue_atom = record.lexical_atoms[1]
    match = re.fullmatch(
        r"Interactive(?P<dimension>[0-9]D)(?P<specificity>ChampionSpecific|SkinSpecific|General)?(?P<variant>[0-9]+)?",
        cue_atom.raw,
    )

    if match is None:
        return False

    specificity_raw = match.group("specificity")
    specificity_values = {
        "ChampionSpecific": "champion_specific",
        "SkinSpecific": "skin_specific",
        "General": "general",
    }
    record.profile = EventProfile(
        primary_family="interactive",
        semantic_profile=SemanticProfile.INTERACTION,
        requires_context=specificity_raw not in {None, "General"},
        reason="matched interactive champion voice pattern",
    )
    record.semantic_slots = {
        "trigger_family": "interactive",
        "dimension_token": match.group("dimension"),
        "specificity": specificity_values.get(specificity_raw or "General"),
        "line_variant": first_number(cue_atom),
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_phase_only_owner_spell_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 AnnieQ_cast3D / SwainW_2D 这类 owner 已带技能键的事件。"""

    owner_spell = parse_owner_spell_token(record.owner.alias or "")

    if owner_spell is None:
        return False

    phase = phase_alias_from_atom(atom) or first_part_value(atom, "phase_token")
    dimension = first_part_raw(atom, "dimension_token")

    if phase is None and dimension is None:
        return False

    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason="matched owner spell token with phase-only body",
    )
    record.semantic_slots = {
        "trigger_family": "spell",
        "subject_hint": owner_spell["subject_hint"],
        "spell_key": owner_spell["spell_key"],
        "owner_spell_token": record.owner.alias,
        "dimension_token": dimension,
        "phase": phase,
        "context_tail": [tail.raw for tail in record.lexical_atoms[atom.chunk_index + 1 :]],
    }

    if owner_spell.get("owner_spell_state") is not None:
        record.semantic_slots["owner_spell_state"] = owner_spell["owner_spell_state"]

    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_neutral_creature_voice_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Atakhan_AggroReset / Bluegolem_Victory2D 这类非英雄单位 VO。"""

    raw = atom.raw
    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    key = raw.lower()
    actions = {
        "aggroreset": "aggro_reset",
        "aggrostart": "aggro_start",
        "ambient": "ambient",
        "spawn": "spawn",
    }
    action = actions.get(key)

    if action is None and residue == "Victory":
        action = "victory"

    if action is None:
        return False

    owner = record.owner.alias or ""
    record.profile = EventProfile(
        primary_family="creature_voice",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=True,
        reason="matched neutral creature voice event",
    )
    record.semantic_slots = {
        "trigger_family": "creature_voice",
        "owner_raw": record.owner.raw_owner_token or owner,
        "creature_action": action,
        "dimension_token": dimension,
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_animation_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Anim3DDrinkPoisonEnd 这类动画状态 VO。"""

    match = re.fullmatch(r"Anim(?P<dimension>[0-9]D)(?P<animation>[A-Z][A-Za-z0-9]*)", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="animation",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched animation voice event",
    )
    record.semantic_slots = {
        "trigger_family": "animation",
        "dimension_token": match.group("dimension"),
        "animation": {
            "raw": match.group("animation"),
            "state": "end" if match.group("animation").endswith("End") else None,
        },
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_clone_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 CloneDestroyed2D 这类召唤物/分身状态事件。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")

    if residue != "CloneDestroyed":
        return False

    record.profile = EventProfile(
        primary_family="clone",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched clone state voice event",
    )
    record.semantic_slots = {
        "trigger_family": "clone_state",
        "dimension_token": dimension,
        "clone_state": "destroyed",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_enter_camp_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 EnterCamp2DCamp 这类进入野怪营地事件。"""

    match = re.fullmatch(r"EnterCamp(?P<dimension>[0-9]D)?(?P<target>Camp)?", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="enter_camp",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched enter camp voice event",
    )
    record.semantic_slots = {
        "trigger_family": "enter_camp",
        "dimension_token": match.group("dimension"),
        "target_class": "jungle_camp",
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_forge_item_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 ForgeItem 这类奥恩打造装备事件。"""

    if atom.raw != "ForgeItem":
        return False

    record.profile = EventProfile(
        primary_family="forge_item",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched forge item voice event",
    )
    record.semantic_slots = {
        "trigger_family": "forge_item",
        "cue_path": [tail.raw for tail in record.lexical_atoms[atom.chunk_index + 1 :]],
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_purchase_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Purchase2DGeneral 这类购买事件。"""

    match = re.fullmatch(r"Purchase(?P<dimension>[0-9]D)?(?P<qualifier>General)?", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="purchase",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched purchase voice event",
    )
    record.semantic_slots = {
        "trigger_family": "purchase",
        "dimension_token": match.group("dimension"),
        "qualifier": "general" if match.group("qualifier") else None,
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_mastery_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Mastery3DGeneral / FlashMastery3D 这类熟练度动作。"""

    match = re.fullmatch(r"(?P<flash>Flash)?Mastery(?P<dimension>[0-9]D)(?P<qualifier>General)?", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="mastery",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched mastery voice event",
    )
    record.semantic_slots = {
        "trigger_family": "mastery",
        "dimension_token": match.group("dimension"),
        "qualifier": "general" if match.group("qualifier") else None,
        "flash": match.group("flash") is not None,
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_transform_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Transform3D_Assassin 这类形态转换语音。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")

    if residue != "Transform":
        return False

    record.profile = EventProfile(
        primary_family="transform",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched transform voice event",
    )
    record.semantic_slots = {
        "trigger_family": "transform",
        "dimension_token": dimension,
        "target_form": raw_atom_after(record, atom.chunk_index),
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_weapon_upgrade_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Special2DWeaponUpgradeFirst/Second 这类武器升级语音。"""

    match = re.fullmatch(r"Special(?P<dimension>[0-9]D)WeaponUpgrade(?P<order>First|Second)", atom.raw)

    if match is None:
        return False

    order_values = {"First": 1, "Second": 2}
    record.profile = EventProfile(
        primary_family="weapon_upgrade",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched weapon upgrade voice event",
    )
    record.semantic_slots = {
        "trigger_family": "weapon_upgrade",
        "dimension_token": match.group("dimension"),
        "upgrade_order": order_values[match.group("order")],
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_second_encounter_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 SecondEncounter3DKennen 这类再次遭遇事件。"""

    match = re.fullmatch(r"SecondEncounter(?P<dimension>[0-9]D)(?P<target>[A-Z][A-Za-z0-9]*)", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="encounter",
        semantic_profile=SemanticProfile.INTERACTION,
        requires_context=True,
        reason="matched second encounter voice event",
    )
    record.semantic_slots = {
        "trigger_family": "encounter",
        "timing": "second",
        "dimension_token": match.group("dimension"),
        "counterpart": unresolved_counterpart(match.group("target")),
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_recal_partner_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Recal3DRakan 这类 Recall 拼写缺一位的伴侣事件。"""

    match = re.fullmatch(r"Recal(?P<dimension>[0-9]D)(?P<target>[A-Z][A-Za-z0-9]*)", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="recall",
        semantic_profile=SemanticProfile.INTERACTION,
        requires_context=True,
        reason="matched recall partner typo voice event",
    )
    record.semantic_slots = {
        "trigger_family": "recall",
        "dimension_token": match.group("dimension"),
        "counterpart": unresolved_counterpart(match.group("target")),
        "source": "event_name_typo_recal",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_nearby_observed_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Nearby3DDeathsAlly / Nearby3DUseItemHealthPotEnemy 这类附近观察事件。"""

    parsed = parse_nearby_observed_event(atom.raw)

    if parsed is None:
        return False

    record.profile = EventProfile(
        primary_family="nearby_event",
        semantic_profile=SemanticProfile.INTERACTION,
        requires_context=False,
        reason="matched nearby observed voice event",
    )
    record.semantic_slots = {
        "trigger_family": "nearby_event",
        "dimension_token": parsed["dimension_token"],
        "proximity": "nearby",
        "observed_action": parsed["observed_action"],
        "relation": parsed["relation"],
    }

    if parsed.get("item") is not None:
        record.semantic_slots["item"] = parsed["item"]

    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_rally_team_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 RallyTeam3DAhead / RallyTeam2DBehind 这类队伍局势语音。"""

    match = re.fullmatch(r"RallyTeam(?P<dimension>[0-9]D)(?P<state>Ahead|Behind|Even)", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="rally_team",
        semantic_profile=SemanticProfile.QUALIFIED_ACTION,
        requires_context=False,
        reason="matched rally team state voice event",
    )
    record.semantic_slots = {
        "trigger_family": "rally_team",
        "dimension_token": match.group("dimension"),
        "team_state": match.group("state").lower(),
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_dimension_prefixed_kill_milestone(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Pentakill3D 这类没有 Kill 前缀的击杀里程碑。"""

    trigger = first_part_value(atom, "trigger_token")
    if trigger not in (None, "kill", "killchampion"):
        # Move2DFirst 这类带非击杀动作词头的 atom,First 是该动作的时序
        # 修饰,不是击杀里程碑,须让位给对应动作的 fill_*。
        return False

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    milestone = normalize_kill_milestone(residue)

    if dimension is None or milestone is None:
        return False

    record.profile = EventProfile(
        primary_family="kill",
        semantic_profile=SemanticProfile.KILL_OR_SCORE,
        requires_context=False,
        reason="matched dimension-prefixed kill milestone",
    )
    record.semantic_slots = {
        "trigger_family": "kill",
        "dimension_token": dimension,
        "milestone": milestone,
        "counter": None,
        "counterpart": None,
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_clear_camp_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 ClearCamp3DGeneral 这类清理野区营地语音。"""

    match = re.fullmatch(r"ClearCamp(?P<dimension>[0-9]D)?(?P<target>[A-Za-z]+)?", atom.raw)

    if match is None:
        return False

    target = match.group("target")
    record.profile = EventProfile(
        primary_family="clear_camp",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched clear camp voice event",
    )
    record.semantic_slots = {
        "trigger_family": "clear_camp",
        "dimension_token": match.group("dimension"),
        "target_class": "jungle_camp",
        "qualifier": "general" if target == "General" else None,
    }

    if target and target != "General":
        record.semantic_slots["camp_target"] = {"raw": target, "kind": "neutral_camp"}

    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_open_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Open2DGeneral 这类打开/展开语音事件。"""

    match = re.fullmatch(r"Open(?P<dimension>[0-9]D)?(?P<qualifier>General)?", atom.raw)

    if match is None:
        return False

    qualifier = match.group("qualifier")
    record.profile = EventProfile(
        primary_family="open",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched open voice event",
    )
    record.semantic_slots = {
        "trigger_family": "open",
        "dimension_token": match.group("dimension"),
        "qualifier": qualifier.lower() if qualifier else None,
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_partner_dance_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 WithRakanDanceInto3D 这类伴侣在场舞蹈语音。"""

    match = re.fullmatch(
        r"With(?P<target>[A-Z][A-Za-z0-9]*)(?P<action>Dance)(?P<transition>Into)?(?P<dimension>[0-9]D)",
        atom.raw,
    )

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="dance",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched partner dance voice event",
    )
    record.semantic_slots = {
        "trigger_family": "dance",
        "dimension_token": match.group("dimension"),
        "precondition": {
            "raw": f"With{match.group('target')}Near",
            "kind": "nearby_champion",
            "proximity": "near",
            "counterpart": unresolved_counterpart(match.group("target")),
        },
    }

    if match.group("transition") is not None:
        record.semantic_slots["transition"] = "into"

    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_spell_outcome_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 RShareGold3D / RExecuteEnemy2D 这类技能结果语音。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")

    if dimension is None or residue is None:
        return False

    match = re.fullmatch(r"(?P<spell_key>[QWERP])(?P<outcome>ShareGold|ExecuteEnemy)", residue)

    if match is None:
        return False

    outcome_values = {
        "ShareGold": "share_gold",
        "ExecuteEnemy": "execute_enemy",
    }
    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason="matched inferred spell outcome voice event",
    )
    record.semantic_slots = {
        "trigger_family": "spell",
        "dimension_token": dimension,
        "spell_key": match.group("spell_key").lower(),
        "outcome": outcome_values[match.group("outcome")],
        "source": "inferred_from_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_summoner_spell_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 SummonerTeleport_cast2D 这类召唤师技能语音。"""

    match = re.fullmatch(r"Summoner(?P<spell>[A-Z][A-Za-z0-9]*)", atom.raw)

    if match is None:
        return False

    spell_name = snake_case(match.group("spell"))
    record.profile = EventProfile(
        primary_family="summoner_spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=False,
        reason="matched summoner spell voice event",
    )
    record.semantic_slots = {
        "trigger_family": "summoner_spell",
        "spell_name": spell_name,
        "dimension_token": first_dimension(record),
        "phase": first_phase(record),
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_reversed_move_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 LongMove2D 这类修饰语在 trigger 前面的移动语音。"""

    match = re.fullmatch(r"LongMove(?P<dimension>[0-9]D)", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="move",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched reversed long move voice event",
    )
    record.semantic_slots = {
        "trigger_family": "move",
        "dimension_token": match.group("dimension"),
        "distance": "long",
        "tail_modifiers": [
            {
                "raw": "Long",
                "key": "long",
                "kind": "movement_distance",
                "label": "Long Distance",
                "source": "event_name_heuristic",
            }
        ],
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_weapon_select_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 GunSelect2D 这类武器选择语音。"""

    match = re.fullmatch(r"(?P<weapon>Gun)Select(?P<dimension>[0-9]D)", atom.raw)

    if match is None:
        return False

    weapon = match.group("weapon").lower()
    record.profile = EventProfile(
        primary_family="weapon_select",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched weapon select voice event",
    )
    record.semantic_slots = {
        "trigger_family": "weapon_select",
        "dimension_token": match.group("dimension"),
        "weapon_kind": weapon,
        "source": "event_name_heuristic",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_objective_destroy_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 TowerDestroy3D 这类目标摧毁语音。"""

    match = re.fullmatch(r"(?P<objective>Tower)Destroy(?P<dimension>[0-9]D)", atom.raw)

    if match is None:
        return False

    objective_values = {
        "Tower": {"kind": "structure", "key": "tower", "label": "Tower"},
    }
    objective = objective_values[match.group("objective")]
    record.profile = EventProfile(
        primary_family="objective_destroy",
        semantic_profile=SemanticProfile.KILL_OR_SCORE,
        requires_context=False,
        reason="matched objective destroy voice event",
    )
    record.semantic_slots = {
        "trigger_family": "objective_destroy",
        "dimension_token": match.group("dimension"),
        "objective": objective,
        "result": "destroy",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_resource_spell_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 MaxResource3DQ 这类资源状态加技能键语音。"""

    match = re.fullmatch(r"MaxResource(?P<dimension>[0-9]D)?(?P<spell_key>[QWERP])?", atom.raw)

    if match is None:
        return False

    spell_key = match.group("spell_key")
    record.profile = EventProfile(
        primary_family="resource_state",
        semantic_profile=SemanticProfile.SPELL_EVENT if spell_key else SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched resource state voice event",
    )
    record.semantic_slots = {
        "trigger_family": "resource_state",
        "dimension_token": match.group("dimension"),
        "resource_state": "max",
        "spell_key": spell_key.lower() if spell_key else None,
        "source": "event_name_heuristic",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_threshold_state_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 AnimalThreshold 这类阈值状态语音。"""

    match = re.fullmatch(r"(?P<context>[A-Za-z]+)Threshold", atom.raw)

    if match is None:
        return False

    record.profile = EventProfile(
        primary_family="threshold_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched threshold state voice event",
    )
    record.semantic_slots = {
        "trigger_family": "threshold_state",
        "threshold": {
            "raw": atom.raw,
            "context": snake_case(match.group("context")),
            "kind": "state_threshold",
        },
        "source": "event_name_heuristic",
    }
    record.confidence = DecompositionConfidence.LOW
    return True


def fill_voice_condition_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 KalistaVOBindAllyGlobal 这类非技能语音条件。"""

    if record.shell.asset_kind != "vo":
        return False

    for raw in voice_condition_lookup_tokens(record, atom):
        condition = VOICE_CONDITION_ALIASES.get(raw)

        if condition is None:
            continue

        record.profile = EventProfile(
            primary_family="voice_condition",
            semantic_profile=SemanticProfile.SELF_ACTION,
            requires_context=True,
            reason="matched champion voice condition event",
        )
        record.semantic_slots = {
            "trigger_family": "voice_condition",
            "condition": {
                "raw": raw,
                "key": condition["key"],
                "label": condition["label"],
                "source": "event_name_heuristic",
            },
            "phase": first_full_phase(record),
        }
        record.confidence = DecompositionConfidence.MEDIUM
        return True

    return False


def voice_condition_lookup_tokens(record: EventDecompositionRecord, atom: LexicalAtom) -> list[str]:
    """返回语音条件识别候选 token。"""

    tokens = [first_part_raw(atom, "residue_token"), strip_owner_alias(atom.raw, record.owner.alias), atom.raw]
    deduped: list[str] = []

    for token in tokens:
        if token is None or token in deduped:
            continue

        deduped.append(token)

    return deduped


def first_full_phase(record: EventDecompositionRecord) -> str | None:
    """优先读取事件尾部 OnBuffCast 这类完整 phase alias。

    技能/效果词干内部也可能以 Root、Stun 等状态词结尾；它们描述组件，
    不能抢走后续显式 OnBuffActivate/OnCast 的事件生命周期。
    """

    for atom in reversed(record.lexical_atoms):
        phase = phase_alias_from_atom(atom)

        if phase is not None:
            return phase

    for atom in reversed(record.lexical_atoms):
        phase = first_part_value(atom, "phase_token")

        if phase is not None:
            return phase

    for atom in reversed(record.lexical_atoms):
        phase = first_part_value(atom, "phase_or_state_token")

        if phase is not None:
            return phase

    return None


def first_action_lifecycle_phase(
    record: EventDecompositionRecord,
    action_atom: LexicalAtom,
) -> str | None:
    """返回动作尾段中最具体的生命周期。

    ``Dance3D_Loop_buffactivate`` 同时含音频分段与 Wwise 承载层状态；对
    用户和下游来说 ``Loop`` 信息更具体，因此先识别动作后的分段词，再
    回落到 ``buffactivate`` 等完整 phase alias。
    """

    for atom in record.lexical_atoms[action_atom.chunk_index + 1 :]:
        compact = re.sub(r"[^a-z0-9]", "", atom.raw.lower())
        for token, phase in ACTION_SEGMENT_PHASES.items():
            if re.fullmatch(rf"{re.escape(token)}[0-9]*", compact):
                return phase

    return first_full_phase(record)


def add_self_action_context(
    record: EventDecompositionRecord,
    action_atom: LexicalAtom,
    slots: dict[str, object],
) -> None:
    """为 VO/SFX 自身动作补齐生命周期，并为 SFX 保留主体上下文。"""

    phase = first_action_lifecycle_phase(record, action_atom)
    if phase is not None:
        slots["phase"] = phase
        slots["cue_path"] = [tail.raw for tail in record.lexical_atoms]

    if record.shell.asset_kind != "sfx":
        return

    slots.update(
        {
            "owner_context": record.owner.to_dict(),
            "subject_hint": record.owner.alias,
            "skin_token": record.owner.skin_token,
            "cue_path": [tail.raw for tail in record.lexical_atoms],
            "asset_kind": "sfx",
        }
    )


def fill_leading_contextual_attack_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Blue_BlueBasicAttack_Attack3D 这类实体普攻语音。"""

    if first_part_value(atom, "trigger_token") is not None or len(record.lexical_atoms) < 3:
        return False

    alias_atom = record.lexical_atoms[1]
    trigger_atom = record.lexical_atoms[2]

    if first_part_value(trigger_atom, "trigger_token") != "attack":
        return False

    raw_alias = first_part_raw(alias_atom, "residue_token") or alias_atom.raw
    stripped_alias = strip_owner_alias(raw_alias, atom.raw)
    match = re.fullmatch(r"(?P<context>[A-Za-z0-9]*?)(?P<attack>BasicAttack|CritAttack)", stripped_alias)

    if match is None:
        return False

    attack = match.group("attack")
    context = match.group("context") or None
    slots: dict[str, object] = {
        "trigger_family": "attack",
        "attack_kind": "crit_attack" if attack == "CritAttack" else "basic_attack",
        "actor": atom.raw,
        "dimension_token": first_part_raw(trigger_atom, "dimension_token"),
        "source": "leading_contextual_attack_event_name",
    }

    if context is not None:
        slots["attack_context"] = context

    rank = first_number(alias_atom)

    if rank is not None:
        slots["attack_rank"] = rank

    record.profile = EventProfile(
        primary_family="attack",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched leading contextual attack voice event",
    )
    record.semantic_slots = slots
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_leading_contextual_spell_alias(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 Sentinel_JayceShockBlast... 这类前置上下文加技能别名。"""

    if first_part_value(atom, "trigger_token") is not None or len(record.lexical_atoms) < 2:
        return False

    alias_atom = record.lexical_atoms[1]

    if not is_untrusted_spell_alias_atom(record, alias_atom):
        return False

    fill_untrusted_spell_alias_slots(record, alias_atom, reason="matched leading contextual spell alias")
    record.semantic_slots["pre_context"] = {
        "raw": atom.raw,
        "kind": "voice_context",
        "source": "leading_chunk",
    }
    return True


def fill_untrusted_spell_alias_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 DariusCleave_cast3D / VolleyRank2_cast3D 这类低置信技能别名。"""

    if not is_untrusted_spell_alias_atom(record, atom):
        return False

    fill_untrusted_spell_alias_slots(record, atom, reason="matched untrusted spell alias voice event")
    return True


def fill_buff_state_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """识别 BlessingOfTheLizardElder_OnBuffActivate 这类增益状态语音。"""

    trigger = first_part_value(atom, "trigger_token")
    if trigger in SIMPLE_ACTION_TRIGGERS:
        # dance_buffactivate 一类结构的主体仍是动作；buffactivate 只是动作
        # 资源生命周期，不能把 Dance 反写成匿名 buff 名。
        return False

    phase_atom = next_phase_atom(record, atom.chunk_index + 1)

    if phase_atom is None:
        return False

    phase = phase_alias_from_atom(phase_atom)

    if phase is None or "buff" not in phase and phase not in {"activate", "deactivate"}:
        return False

    record.profile = EventProfile(
        primary_family="buff_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched buff state voice event",
    )
    record.semantic_slots = {
        "trigger_family": "buff_state",
        "buff_name": atom.raw,
        "phase": phase,
        "cue_path": [tail.raw for tail in record.lexical_atoms[atom.chunk_index + 1 :]],
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_untrusted_spell_alias_slots(
    record: EventDecompositionRecord,
    atom: LexicalAtom,
    reason: str,
) -> None:
    """填充低置信技能别名 slot，不直接解释成具体技能含义。"""

    alias_base = first_part_raw(atom, "residue_token") or strip_owner_alias(atom.raw, record.owner.alias)
    owner_spell_hint = owner_prefixed_spell_hint(record, atom.raw)
    spell_key_hint = owner_spell_hint["spell_key"] if owner_spell_hint is not None else infer_spell_key_hint(atom.raw)
    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=True,
        reason=reason,
    )
    record.semantic_slots = {
        "trigger_family": "spell",
        "alias_raw": atom.raw,
        "alias_base": alias_base,
        "alias_kind": "untrusted_internal_or_visual_spell_alias",
        "dimension_token": first_part_raw(atom, "dimension_token") or first_dimension(record),
        "phase": first_part_value(atom, "phase_or_state_token") or first_phase(record),
        "spell_rank": first_number(atom),
        "source": "event_name_heuristic",
    }

    if spell_key_hint is not None:
        record.semantic_slots["spell_key_hint"] = spell_key_hint

    if owner_spell_hint is not None:
        record.semantic_slots["alias_residual"] = owner_spell_hint["residual"]

    record.confidence = DecompositionConfidence.MEDIUM if owner_spell_hint is not None else DecompositionConfidence.LOW


def fill_skin_recall_state_event(record: EventDecompositionRecord) -> bool:
    """识别皮肤回城资源状态，避免落入技术控制或 buff fallback。"""

    skin_recall = parse_skin_recall_state(record.raw_event_name)

    if skin_recall is None:
        return False

    record.profile = EventProfile(
        primary_family="skin_recall_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched skin recall resource state",
    )
    record.semantic_slots = {
        "trigger_family": "skin_recall_state",
        "subject_hint": skin_recall["subject_hint"],
        "skin_token": skin_recall["skin_token"],
        "recall_action": "recall",
        "recall_dimension": skin_recall.get("dimension"),
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": skin_recall["cue_path"],
        "source": "skin_recall_event_name",
    }

    if skin_recall.get("phase") is not None:
        record.semantic_slots["phase"] = skin_recall["phase"]

    if skin_recall.get("variant") is not None:
        record.semantic_slots["recall_variant"] = skin_recall["variant"]

    record.confidence = DecompositionConfidence.MEDIUM
    return True


def parse_skin_recall_state(raw_event_name: str) -> dict[str, Any] | None:
    """从整条事件名中提取皮肤回城状态信息。"""

    tokens = [token for token in raw_event_name.split("_") if token]
    skin = parse_skin_subject(tokens)

    if skin is None:
        return None

    recall_index = first_recall_token_index(tokens)

    if recall_index is None:
        return None

    phase, variant = parse_recall_state_tail(tokens[recall_index + 1 :])

    if phase is None and variant is None:
        return None

    recall_token = tokens[recall_index]
    return {
        "subject_hint": skin["subject_hint"],
        "skin_token": skin["skin_token"],
        "dimension": "3D" if "3d" in recall_token.lower() else None,
        "phase": phase,
        "variant": variant,
        "cue_path": tokens[recall_index:],
    }


def parse_skin_subject(tokens: list[str]) -> dict[str, str] | None:
    """从 token 序列中解析英雄和皮肤编号。"""

    for token in tokens:
        match = re.fullmatch(r"(?P<subject>[A-Za-z][A-Za-z0-9]*?)Skin(?P<skin>\d+)", token, flags=re.IGNORECASE)

        if match is not None:
            return {
                "subject_hint": canonical_subject_hint(match.group("subject")),
                "skin_token": f"Skin{match.group('skin')}",
            }

    for index, token in enumerate(tokens):
        match = re.fullmatch(r"skin_?(?P<skin>\d+)", token, flags=re.IGNORECASE)

        if match is None or index == 0:
            continue

        subject = previous_subject_token(tokens, index)

        if subject is None:
            continue

        return {
            "subject_hint": canonical_subject_hint(subject),
            "skin_token": f"Skin{match.group('skin')}",
        }

    for index, token in enumerate(tokens):
        if token.lower() != "skin" or index == 0 or index + 1 >= len(tokens) or not tokens[index + 1].isdigit():
            continue

        subject = previous_subject_token(tokens, index)

        if subject is None:
            continue

        return {
            "subject_hint": canonical_subject_hint(subject),
            "skin_token": f"Skin{tokens[index + 1]}",
        }

    return None


def previous_subject_token(tokens: list[str], skin_index: int) -> str | None:
    """返回 skin token 前最像英雄名的 token。"""

    ignored = {"play", "stop", "pause", "unpause", "mute", "unmute", "sfx", "vo"}

    for token in reversed(tokens[:skin_index]):
        if token.lower() not in ignored and re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", token):
            return token

    return None


def canonical_subject_hint(raw: str) -> str:
    """保留驼峰英雄别名；全小写 token 转为首字母大写。"""

    if raw.islower():
        return raw[:1].upper() + raw[1:]

    return raw


def first_recall_token_index(tokens: list[str]) -> int | None:
    """返回第一个独立 Recall/Recall3D token 的位置。"""

    for index, token in enumerate(tokens):
        if re.fullmatch(r"recall(?:3d)?", token, flags=re.IGNORECASE):
            return index

    return None


def parse_recall_state_tail(tokens: list[str]) -> tuple[str | None, dict[str, str] | None]:
    """解析 Recall 后的状态段和资源变体。"""

    phase_aliases = {
        "leadin": "leadin",
        "leadout": "leadout",
        "winddown": "winddown",
        "buffactivate": "buff_activate",
        "buffdeactivate": "buff_deactivate",
    }
    phase: str | None = None
    variant: dict[str, str] | None = None

    for token in tokens:
        compact = compact_text(token)

        if phase is None and compact in phase_aliases:
            phase = phase_aliases[compact]
            continue

        effort = re.fullmatch(r"efforts?(?P<index>\d*)", compact)
        if variant is None and effort is not None:
            variant = {"raw": token, "kind": "efforts"}

            if effort.group("index"):
                variant["index"] = effort.group("index")

            continue

        if phase is not None and variant is None and token.isdigit():
            variant = {"raw": token, "kind": "stage_index", "index": token}

    return phase, variant


def fill_skin_idle_state_event(record: EventDecompositionRecord) -> bool:
    """识别皮肤待机状态中的武器或技能片段。"""

    idle_state = parse_skin_idle_state(record.raw_event_name)

    if idle_state is None:
        return False

    record.profile = EventProfile(
        primary_family="skin_idle_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched skin idle resource state",
    )
    record.semantic_slots = {
        "trigger_family": "skin_idle_state",
        "subject_hint": idle_state["subject_hint"],
        "skin_token": idle_state["skin_token"],
        "idle_action": "idle",
        "idle_component": idle_state["component"],
        "phase": idle_state["phase"],
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": idle_state["cue_path"],
        "source": "skin_idle_event_name",
    }

    if idle_state.get("spell_key") is not None:
        record.semantic_slots["spell_key"] = idle_state["spell_key"]

    if idle_state.get("idle_variant") is not None:
        record.semantic_slots["idle_variant"] = idle_state["idle_variant"]

    if idle_state.get("segment_index") is not None:
        record.semantic_slots["segment_index"] = idle_state["segment_index"]

    record.confidence = DecompositionConfidence.MEDIUM
    return True


def parse_skin_idle_state(raw_event_name: str) -> dict[str, Any] | None:
    """从整条事件名中提取皮肤待机状态信息。"""

    tokens = [token for token in raw_event_name.split("_") if token]
    skin = parse_skin_subject(tokens)

    if skin is None:
        return None

    variant_index = first_idle_variant_token_index(tokens)

    if variant_index is not None and variant_index + 1 < len(tokens) and tokens[variant_index + 1].isdigit():
        variant = parse_idle_variant_token(tokens[variant_index])
        return {
            "subject_hint": skin["subject_hint"],
            "skin_token": skin["skin_token"],
            "component": {
                "kind": "idle_variant",
                "key": "idle",
                "index": variant["index"],
            },
            "phase": "segment",
            "idle_variant": variant,
            "segment_index": tokens[variant_index + 1],
            "cue_path": tokens[variant_index:],
        }

    idle_index = first_compact_token_index(tokens, "idle")

    if idle_index is None or idle_index + 2 >= len(tokens):
        return None

    component = parse_idle_component(tokens[idle_index + 1])
    phase = parse_idle_state_phase(tokens[idle_index + 2])

    if component is None or phase is None:
        return None

    return {
        "subject_hint": skin["subject_hint"],
        "skin_token": skin["skin_token"],
        "component": component["component"],
        "spell_key": component.get("spell_key"),
        "phase": phase,
        "cue_path": tokens[idle_index:],
    }


def first_compact_token_index(tokens: list[str], value: str) -> int | None:
    """返回第一个 compact 后等于 value 的 token 下标。"""

    for index, token in enumerate(tokens):
        if compact_text(token) == value:
            return index

    return None


def first_idle_variant_token_index(tokens: list[str]) -> int | None:
    """返回第一个 idleN 待机变体 token 下标。"""

    for index, token in enumerate(tokens):
        if parse_idle_variant_token(token) is not None:
            return index

    return None


def parse_idle_variant_token(raw: str) -> dict[str, int] | None:
    """解析 idle2 / idle3 这类待机编号。"""

    match = re.fullmatch(r"idle(?P<index>\d+)", raw, flags=re.IGNORECASE)

    if match is None:
        return None

    return {"index": int(match.group("index"))}


def parse_idle_component(raw: str) -> dict[str, Any] | None:
    """解析待机状态中的武器名或技能键。"""

    compact = compact_text(raw)

    if compact == "powpow":
        return {
            "spell_key": "q",
            "component": {
                "kind": "weapon",
                "key": "powpow",
                "label": "Pow-Pow",
                "label_zh": "砰砰",
            },
        }

    if compact in {"q", "w", "e", "r", "p"}:
        return {
            "spell_key": compact,
            "component": {
                "kind": "spell",
                "key": compact,
                "label": compact.upper(),
            },
        }

    return None


def parse_idle_state_phase(raw: str) -> str | None:
    """解析待机状态片段阶段。"""

    values = {
        "in": "in",
        "out": "out",
    }
    return values.get(compact_text(raw))


# 皮肤动作状态兜底的历史行为锚点：规范大小写壳（Play_sfx_*）交给 trigger
# 路径，小写壳（play_sfx_*）继续走皮肤动作状态解析。
# 动词表从 lexer 的 SHELL_VERBS 派生，消除双处手工同步。
CANONICAL_SHELL_VERB_HEADS = frozenset(SHELL_VERBS)


def has_canonical_cased_shell_verb(record: EventDecompositionRecord) -> bool:
    """判断 raw 事件名是否以规范大小写壳动词开头。

    Args:
        record: 拆解记录。

    Returns:
        首段是规范大小写壳动词时返回 True。
    """

    return record.raw_event_name.split("_", 1)[0] in CANONICAL_SHELL_VERB_HEADS


def fill_skin_action_state_event(record: EventDecompositionRecord) -> bool:
    """识别皮肤动作状态中的词尾方向标记和阶段。"""

    if has_canonical_cased_shell_verb(record) and record.shell.asset_kind in {"sfx", "vo"}:
        return False

    action_state = parse_skin_action_state(record.raw_event_name)

    if action_state is None:
        return False

    record.profile = EventProfile(
        primary_family="skin_action_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched skin action state with terminal dimension",
    )
    record.semantic_slots = {
        "trigger_family": "skin_action_state",
        "subject_hint": action_state["subject_hint"],
        "skin_token": action_state["skin_token"],
        "action": action_state["action"],
        "dimension_token": action_state["dimension"],
        "phase": action_state["phase"],
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": action_state["cue_path"],
        "source": "skin_action_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def parse_skin_action_state(raw_event_name: str) -> dict[str, Any] | None:
    """从整条事件名中提取皮肤动作状态。

    只把动作 token 末尾的 2D/3D 视为发声方向，避免误伤词中或词首的 3D。
    """

    tokens = [token for token in raw_event_name.split("_") if token]
    skin = parse_skin_subject(tokens)
    skin_index = first_skin_subject_end_index(tokens)

    if skin is None or skin_index is None:
        return None

    for index, token in enumerate(tokens[skin_index + 1 :], start=skin_index + 1):
        action = parse_terminal_dimension_action(token)

        if action is None:
            continue

        phase = parse_skin_action_phase(tokens[index + 1 :])

        if phase is None:
            return None

        return {
            "subject_hint": skin["subject_hint"],
            "skin_token": skin["skin_token"],
            "action": action["action"],
            "dimension": action["dimension"],
            "phase": phase,
            "cue_path": tokens[index:],
        }

    return None


def first_skin_subject_end_index(tokens: list[str]) -> int | None:
    """返回皮肤主体 token 结束位置，用于限制后续动作解析范围。"""

    for index, token in enumerate(tokens):
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*?Skin\d+", token, flags=re.IGNORECASE):
            return index

    for index, token in enumerate(tokens):
        if re.fullmatch(r"skin_?\d+", token, flags=re.IGNORECASE):
            return index

    for index, token in enumerate(tokens):
        if token.lower() == "skin" and index + 1 < len(tokens) and tokens[index + 1].isdigit():
            return index + 1

    return None


def parse_terminal_dimension_action(raw: str) -> dict[str, str] | None:
    """解析动作词尾的 2D/3D 方向标记。"""

    match = re.fullmatch(r"(?P<action>[A-Za-z]+)(?P<dimension>[23]D)", raw, flags=re.IGNORECASE)

    if match is None:
        return None

    action = compact_text(match.group("action"))

    if action not in SKIN_ACTION_STATE_TRIGGERS:
        return None

    return {
        "action": normalize_trigger_family(action),
        "dimension": match.group("dimension").upper(),
    }


def parse_skin_action_phase(tokens: list[str]) -> str | None:
    """解析皮肤动作状态后续阶段。"""

    values = {
        "buffactivate": "buff_activate",
        "buffdeactivate": "buff_deactivate",
        "cast": "cast",
        "oncast": "cast",
        "activate": "activate",
        "deactivate": "deactivate",
    }

    for token in tokens:
        phase = values.get(compact_text(token))

        if phase is not None:
            return phase

    return None


def fill_structured_state_event(record: EventDecompositionRecord) -> bool:
    """识别已可结构化解释的皮肤、英雄状态和玩法阶段事件。"""

    if fill_gameplay_phase_control_event(record):
        return True

    if fill_skin_audio_transition_event(record):
        return True

    if fill_skin_named_state_event(record):
        return True

    if fill_champion_status_state_event(record):
        return True

    return False


def fill_gameplay_phase_control_event(record: EventDecompositionRecord) -> bool:
    """识别 Map12_FirstBlood_phase_select 这类玩法阶段控制。"""

    parsed = parse_gameplay_phase_control(record.raw_event_name)

    if parsed is None:
        return False

    record.profile = EventProfile(
        primary_family="gameplay_phase_control",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=False,
        reason="matched gameplay phase control event",
    )
    record.semantic_slots = {
        "trigger_family": "gameplay_phase_control",
        "map_id": parsed["map_id"],
        "gameplay_event": parsed["gameplay_event"],
        "phase_action": parsed["phase_action"],
        "cue_path": parsed["cue_path"],
        "source": "gameplay_phase_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()
    return True


def parse_gameplay_phase_control(raw_event_name: str) -> dict[str, Any] | None:
    """解析地图玩法阶段控制事件。"""

    match = re.fullmatch(r"map(?P<map_id>\d+)_FirstBlood_phase_select", raw_event_name, flags=re.IGNORECASE)

    if match is None:
        return None

    return {
        "map_id": int(match.group("map_id")),
        "gameplay_event": "first_blood",
        "phase_action": "select",
        "cue_path": raw_event_name.split("_"),
    }


def fill_skin_audio_transition_event(record: EventDecompositionRecord) -> bool:
    """识别 DJ Sona 这类皮肤音乐风格过渡片段。"""

    parsed = parse_skin_audio_transition(record.raw_event_name)

    if parsed is None:
        return False

    record.profile = EventProfile(
        primary_family="skin_audio_transition",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched skin audio transition state",
    )
    record.semantic_slots = {
        "trigger_family": "skin_audio_transition",
        "subject_hint": parsed["subject_hint"],
        "skin_token": parsed["skin_token"],
        "control_action": parsed["control_action"],
        "genre_index": parsed["genre_index"],
        "transition_phase": parsed["transition_phase"],
        "segment_index": parsed["segment_index"],
        "cue_path": parsed["cue_path"],
        "source": "skin_audio_transition_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()
    return True


def parse_skin_audio_transition(raw_event_name: str) -> dict[str, Any] | None:
    """解析 SonaSkin06_GenreN_Transition_leadin_01 结构。"""

    tokens = raw_tokens(raw_event_name)
    skin = parse_skin_subject(tokens)

    if skin is None:
        return None

    genre_index = first_token_match_index(tokens, r"Genre(?P<index>\d+)")

    if genre_index is None or genre_index + 3 >= len(tokens):
        return None

    genre_match = re.fullmatch(r"Genre(?P<index>\d+)", tokens[genre_index], flags=re.IGNORECASE)
    transition = compact_text(tokens[genre_index + 1])
    phase = parse_named_state_phase(tokens[genre_index + 2])
    segment = tokens[genre_index + 3]

    if genre_match is None or transition != "transition" or phase is None or not segment.isdigit():
        return None

    return {
        "subject_hint": skin["subject_hint"],
        "skin_token": skin["skin_token"],
        "control_action": parse_control_action(tokens),
        "genre_index": int(genre_match.group("index")),
        "transition_phase": phase,
        "segment_index": segment,
        "cue_path": tokens[genre_index:],
    }


def fill_skin_named_state_event(record: EventDecompositionRecord) -> bool:
    """识别 slotmachine、Idle Passive 这类皮肤命名状态。"""

    parsed = parse_skin_named_state(record.raw_event_name)

    if parsed is None:
        return False

    record.profile = EventProfile(
        primary_family="skin_named_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched named skin state event",
    )
    record.semantic_slots = {
        "trigger_family": "skin_named_state",
        "subject_hint": parsed["subject_hint"],
        "skin_token": parsed.get("skin_token"),
        "control_action": parsed["control_action"],
        "state_key": parsed["state_key"],
        "phase": parsed["phase"],
        "cue_path": parsed["cue_path"],
        "source": "skin_named_state_event_name",
    }

    if parsed.get("state_variant") is not None:
        record.semantic_slots["state_variant"] = parsed["state_variant"]

    if parsed.get("segment_index") is not None:
        record.semantic_slots["segment_index"] = parsed["segment_index"]

    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()
    return True


def parse_skin_named_state(raw_event_name: str) -> dict[str, Any] | None:
    """解析可读的皮肤命名状态。"""

    tokens = raw_tokens(raw_event_name)
    slotmachine = parse_slotmachine_state(tokens)

    if slotmachine is not None:
        return slotmachine

    return parse_idle_passive_state(tokens)


def parse_slotmachine_state(tokens: list[str]) -> dict[str, Any] | None:
    """解析 Jinx skin60 slotmachine 状态。"""

    skin = parse_skin_subject(tokens)
    state_index = first_compact_token_index(tokens, "slotmachine")

    if skin is None or state_index is None or state_index + 1 >= len(tokens):
        return None

    phase = parse_named_state_phase(tokens[state_index + 1])

    if phase is None:
        return None

    variant = compact_text(tokens[state_index + 2]) if state_index + 2 < len(tokens) else None

    return {
        "subject_hint": skin["subject_hint"],
        "skin_token": skin["skin_token"],
        "control_action": parse_control_action(tokens),
        "state_key": "slotmachine",
        "state_variant": variant,
        "phase": phase,
        "cue_path": tokens[state_index:],
    }


def parse_idle_passive_state(tokens: list[str]) -> dict[str, Any] | None:
    """解析 Jhin Idle_Passive_buffactivate 状态。"""

    idle_index = first_compact_token_index(tokens, "idle")

    if idle_index is None or idle_index + 2 >= len(tokens):
        return None

    if compact_text(tokens[idle_index + 1]) != "passive":
        return None

    phase = parse_named_state_phase(tokens[idle_index + 2])
    subject = parse_subject_context(tokens, idle_index)

    if phase is None or subject is None:
        return None

    result = {
        "subject_hint": subject["subject_hint"],
        "skin_token": subject.get("skin_token"),
        "control_action": parse_control_action(tokens),
        "state_key": "passive_idle",
        "phase": phase,
        "cue_path": tokens[idle_index:],
    }

    if idle_index + 3 < len(tokens) and tokens[idle_index + 3].isdigit():
        result["segment_index"] = tokens[idle_index + 3]

    return result


def fill_champion_status_state_event(record: EventDecompositionRecord) -> bool:
    """识别英雄状态资源名。"""

    parsed = parse_champion_status_state(record.raw_event_name, record)

    if parsed is None:
        return False

    record.profile = EventProfile(
        primary_family="champion_status_state",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=True,
        reason="matched champion status state event",
    )
    record.semantic_slots = {
        "trigger_family": "champion_status_state",
        "subject_hint": parsed["subject_hint"],
        "skin_token": parsed.get("skin_token"),
        "control_action": parsed["control_action"],
        "status_key": parsed["status_key"],
        "phase": parsed.get("phase"),
        "cue_path": parsed["cue_path"],
        "source": "champion_status_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()
    return True


def parse_champion_status_state(
    raw_event_name: str,
    record: EventDecompositionRecord,
) -> dict[str, Any] | None:
    """解析 SlowStun、RapidReload、AnimalThreshold 这类英雄状态。"""

    tokens = raw_tokens(raw_event_name)

    for index, token in enumerate(tokens):
        compact = compact_text(token)

        if compact.endswith("slowstun"):
            subject = parse_subject_context(tokens, index) or prefixed_status_subject(token, "slowstun")

            if subject is None:
                continue

            return {
                "subject_hint": subject["subject_hint"],
                "skin_token": subject.get("skin_token"),
                "control_action": parse_control_action(tokens),
                "status_key": "slow_stun",
                "phase": first_named_state_phase(tokens[index + 1 :]),
                "cue_path": tokens[index:],
            }

        if compact == "rapidreload":
            subject = parse_subject_context(tokens, index) or record_owner_subject(record)

            if subject is None:
                continue

            return {
                "subject_hint": subject["subject_hint"],
                "skin_token": subject.get("skin_token"),
                "control_action": parse_control_action(tokens),
                "status_key": "rapid_reload",
                "phase": first_named_state_phase(tokens[index + 1 :]),
                "cue_path": tokens[index:],
            }

        if compact == "animalthreshold":
            subject = parse_subject_context(tokens, index) or record_owner_subject(record)

            if subject is None:
                continue

            return {
                "subject_hint": subject["subject_hint"],
                "skin_token": subject.get("skin_token"),
                "control_action": parse_control_action(tokens),
                "status_key": "animal_threshold",
                "phase": None,
                "cue_path": tokens[index:],
            }

    return None


def raw_tokens(raw_event_name: str) -> list[str]:
    """返回原始事件名的非空下划线 token。"""

    return [token for token in raw_event_name.split("_") if token]


def first_token_match_index(tokens: list[str], pattern: str) -> int | None:
    """返回第一个匹配正则的 token 下标。"""

    for index, token in enumerate(tokens):
        if re.fullmatch(pattern, token, flags=re.IGNORECASE):
            return index

    return None


def parse_control_action(tokens: list[str]) -> str | None:
    """解析事件壳中的控制动作。"""

    if not tokens:
        return None

    values = {
        "play": "play",
        "stop": "stop",
        "unmute": "unmute",
        "mute": "mute",
        "pause": "pause",
        "unpause": "unpause",
    }
    return values.get(compact_text(tokens[0]))


def parse_named_state_phase(raw: str) -> str | None:
    """解析命名状态常见阶段。"""

    values = {
        "buffactivate": "buff_activate",
        "onbuffactivate": "buff_activate",
        "buffdeactivate": "buff_deactivate",
        "onbuffdeactivate": "buff_deactivate",
        "leadin": "leadin",
        "leadout": "leadout",
        "winddown": "winddown",
        "cast": "cast",
        "oncast": "cast",
        "activate": "activate",
        "deactivate": "deactivate",
    }
    return values.get(compact_text(raw))


def first_named_state_phase(tokens: list[str]) -> str | None:
    """返回 token 序列里的第一个命名状态阶段。"""

    for token in tokens:
        phase = parse_named_state_phase(token)

        if phase is not None:
            return phase

    return None


def parse_subject_context(tokens: list[str], before_index: int) -> dict[str, str | None] | None:
    """解析 before_index 前的英雄/皮肤主体。"""

    skin = parse_skin_subject(tokens[: before_index + 1])

    if skin is not None:
        return skin

    subject = previous_subject_token(tokens, before_index)

    if subject is None:
        return None

    return {
        "subject_hint": canonical_subject_hint(subject),
        "skin_token": None,
    }


def prefixed_status_subject(raw: str, suffix: str) -> dict[str, str | None] | None:
    """从 SionSlowStun 这类状态 token 前缀中恢复主体。"""

    match = re.fullmatch(rf"(?P<subject>[A-Za-z][A-Za-z0-9]*?){suffix}", raw, flags=re.IGNORECASE)

    if match is None:
        return None

    return {
        "subject_hint": canonical_subject_hint(match.group("subject")),
        "skin_token": None,
    }


def record_owner_subject(record: EventDecompositionRecord) -> dict[str, str | None] | None:
    """从标准 shell owner 中读取主体。"""

    if record.owner.alias is None:
        return None

    return {
        "subject_hint": record.owner.alias,
        "skin_token": record.owner.skin_token,
    }


def is_untrusted_spell_alias_atom(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """判断 atom 是否足够像英雄 VO 技能别名，而不是普通未知实体。"""

    if first_part_value(atom, "trigger_token") is not None:
        return False

    if (first_part_raw(atom, "dimension_token") or first_part_value(atom, "phase_or_state_token")) is None:
        if first_dimension(record) is None and first_phase(record) is None:
            return False

    alias_base = first_part_raw(atom, "residue_token") or atom.raw
    owner_prefix = strip_owner_alias(atom.raw, record.owner.alias) != atom.raw
    return (
        owner_prefix
        or looks_like_named_spell_alias(atom.raw)
        or infer_spell_key_hint(atom.raw) is not None
        or has_spell_alias_term(alias_base)
    )


def fill_champion_attack_event(record: EventDecompositionRecord) -> bool:
    """把英雄音频中的强攻击词尾归一为独立攻击语义。

    ``BasicAttack`` / ``CritAttack`` 以及明确的 ``RangedAttack`` /
    ``MeleeAttack`` 是攻击动作，不因英雄被动或技能会强化普攻就升级为技能。
    词尾前的召唤物、形态或效果名保留为工程上下文，交给 reference 层补充
    ``related_spell_key``，但不会覆盖 ``attack_kind``。
    """

    if record.owner.kind == "map_or_system":
        return False

    matched_atom: LexicalAtom | None = None
    matched_index: int | None = None
    match: re.Match[str] | None = None
    for index, atom in enumerate(record.lexical_atoms):
        candidate = ATTACK_CUE_PATTERN.search(atom.raw)
        if candidate is not None:
            matched_atom = atom
            matched_index = index
            match = candidate
            break

    if matched_atom is None or matched_index is None or match is None:
        return False

    attack_token = match.group("attack").lower()
    attack_kind = "crit_attack" if attack_token == "critattack" else "basic_attack"
    attack_form = {
        "rangedattack": "ranged",
        "meleeattack": "melee",
    }.get(attack_token)
    variant_text = match.group("variant")
    component_prefix = matched_atom.raw[: match.start()] or None
    related_spell_match = re.search(r"(?P<spell_key>[QWERP])$", component_prefix or "")
    attack_context = component_prefix
    owner_alias = record.owner.alias
    if attack_context and owner_alias and attack_context.casefold().startswith(owner_alias.casefold()):
        attack_context = attack_context[len(owner_alias) :] or None
    actor = next(
        (
            atom.raw
            for atom in record.lexical_atoms[:matched_index]
            if attack_context and atom.raw.casefold() == attack_context.casefold()
        ),
        None,
    )

    record.profile = EventProfile(
        primary_family=attack_kind,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=attack_context is not None,
        reason="matched strong champion attack cue",
    )
    record.semantic_slots = {
        "trigger_family": attack_kind,
        "attack_kind": attack_kind,
        "attack_form": attack_form,
        "attack_variant": int(variant_text) if variant_text else None,
        "attack_component_raw": component_prefix,
        "attack_context": attack_context,
        "actor": actor,
        "related_spell_key": related_spell_match.group("spell_key").lower() if related_spell_match else None,
        "attack_raw": matched_atom.raw,
        "owner_context": record.owner.to_dict(),
        "subject_hint": record.owner.alias,
        "skin_token": record.owner.skin_token,
        "dimension_token": first_dimension(record),
        "phase": first_full_phase(record),
        "cue_path": [atom.raw for atom in record.lexical_atoms],
        "asset_kind": record.shell.asset_kind,
    }
    record.confidence = DecompositionConfidence.HIGH
    return True


def fill_champion_execute_action_event(record: EventDecompositionRecord) -> bool:
    """把 owner 紧邻的 ``Execute`` 识别为处决动作而非 E 技能。

    ``RenektonExecute`` 这类内部动作名的首字母 E 只是英文单词边界；没有
    reference spell id 时不得据此生成技能槽位。精确的 ``PreExecute``、
    ``SuperExecute`` 或 ``UltExecute`` 保留给 reference 规则处理。
    """

    owner_alias = record.owner.alias
    if record.owner.kind == "map_or_system" or not owner_alias:
        return False

    action = OWNER_PREFIXED_NON_SPELL_ACTIONS.get((owner_alias.casefold(), "execute"))
    if action is None:
        return False

    expected = f"{owner_alias}Execute".casefold()
    matched_atom = next((atom for atom in record.lexical_atoms if atom.raw.casefold() == expected), None)
    if matched_atom is None:
        return False

    record.profile = EventProfile(
        primary_family=action,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched exact owner-prefixed execute action",
    )
    record.semantic_slots = {
        "trigger_family": action,
        "action": action,
        "action_raw": matched_atom.raw,
        "owner_context": record.owner.to_dict(),
        "subject_hint": owner_alias,
        "skin_token": record.owner.skin_token,
        "dimension_token": first_dimension(record),
        "phase": first_full_phase(record),
        "cue_path": [atom.raw for atom in record.lexical_atoms],
        "asset_kind": record.shell.asset_kind,
    }
    record.confidence = DecompositionConfidence.MEDIUM
    return True


def fill_sfx_event(record: EventDecompositionRecord, atom: LexicalAtom) -> bool:
    """把剩余 SFX 结构化为效果事件，保留 raw 工程路径。"""

    if record.shell.asset_kind != "sfx":
        return False

    scope = sfx_scope(record)
    effect_head, effect_base = sfx_effect_head(record, atom)
    phase = first_full_phase(record)
    spell_hint = owner_prefixed_spell_hint(record, effect_head)
    semantic_profile = (
        SemanticProfile.SPELL_EVENT
        if scope == "champion_sfx"
        and (phase is not None or infer_spell_key_hint(effect_head) is not None or spell_hint is not None)
        else SemanticProfile.MAP_OR_SYSTEM
    )
    record.profile = EventProfile(
        primary_family="sfx",
        semantic_profile=semantic_profile,
        requires_context=True,
        reason="matched generic SFX effect event",
    )
    record.semantic_slots = {
        "trigger_family": "sfx_event",
        "sfx_scope": scope,
        "owner_context": record.owner.to_dict(),
        "effect_head": effect_head,
        "effect_base": effect_base,
        "dimension_token": first_dimension(record),
        "phase": phase,
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_sfx_event_name",
    }

    if spell_hint is not None:
        record.semantic_slots["spell_key_hint"] = spell_hint["spell_key"]
        record.semantic_slots["effect_residual"] = spell_hint["residual"]

    record.confidence = (
        DecompositionConfidence.MEDIUM
        if spell_hint is not None
        else DecompositionConfidence.LOW
        if semantic_profile is SemanticProfile.SPELL_EVENT
        else DecompositionConfidence.MEDIUM
    )
    return True


def owner_prefixed_spell_hint(record: EventDecompositionRecord, raw: str) -> dict[str, str] | None:
    """从 owner 前缀的别名里提取技能键提示。"""

    owner_alias = record.owner.alias

    if owner_alias is None or not raw.lower().startswith(owner_alias.lower()):
        return None

    residue = raw[len(owner_alias) :]

    if not residue:
        return None

    if residue.lower() in NON_SPELL_PREFIX_WORDS or any(
        residue.lower().startswith(f"{word}") for word in NON_SPELL_PREFIX_WORDS
    ):
        return None

    passive_match = re.fullmatch(r"Passive(?P<residual>[A-Za-z0-9]*)", residue)

    if passive_match is not None:
        return {
            "spell_key": "p",
            "residual": passive_match.group("residual"),
        }

    leading_key = re.fullmatch(r"(?P<spell_key>[QWERP])(?P<residual>[A-Za-z0-9]*)", residue)

    if leading_key is not None:
        return {
            "spell_key": leading_key.group("spell_key").lower(),
            "residual": leading_key.group("residual"),
        }

    trailing_key = re.fullmatch(r"(?P<residual>[A-Za-z][A-Za-z0-9]*)(?P<spell_key>[QWER])", residue)

    if trailing_key is None:
        return None

    return {
        "spell_key": trailing_key.group("spell_key").lower(),
        "residual": trailing_key.group("residual"),
    }


def should_prefer_generic_sfx(record: EventDecompositionRecord, atom: LexicalAtom, trigger: str | None) -> bool:
    """判断 SFX 是否应优先按通用效果音兜底处理。"""

    if record.shell.asset_kind != "sfx":
        return False

    scope = sfx_scope(record)

    if scope != "champion_sfx":
        return True

    if trigger in INTERACTION_TRIGGERS or trigger in SIMPLE_ACTION_TRIGGERS:
        return False

    if trigger in {"chat", "receive", "lowhealth"}:
        return False

    if trigger is None and compact_text(atom.raw) in LEADING_ACTION_QUALIFIERS:
        return False

    return True


def fill_ownerless_sfx_event(record: EventDecompositionRecord) -> bool:
    """识别没有 tail atom 的 SFX 效果事件。"""

    if record.shell.asset_kind != "sfx":
        return False

    raw = record.owner.raw_owner_token or record.owner.alias or record.shell.body
    record.profile = EventProfile(
        primary_family="sfx",
        semantic_profile=SemanticProfile.MAP_OR_SYSTEM,
        requires_context=True,
        reason="matched ownerless SFX effect event",
    )
    record.semantic_slots = {
        "trigger_family": "sfx_event",
        "sfx_scope": sfx_scope(record),
        "owner_context": record.owner.to_dict(),
        "effect_head": raw,
        "effect_base": raw,
        "dimension_token": None,
        "phase": None,
        "cue_path": [],
        "source": "raw_sfx_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()
    return True


def fill_other_audio_or_control_event(record: EventDecompositionRecord, atom: LexicalAtom | None) -> bool:
    """把非 VO/SFX 的音乐、状态和控制事件结构化。"""

    if not is_other_audio_or_control_record(record):
        return False

    if is_parameter_control(record, atom):
        fill_audio_parameter_event(record)
        return True

    if is_switch_control(record, atom):
        fill_audio_switch_event(record)
        return True

    if is_state_control(record, atom):
        fill_audio_state_event(record)
        return True

    if is_named_control_state(record):
        fill_named_control_state_event(record)
        return True

    scope = other_control_scope(record)
    trigger_family = other_trigger_family(record, scope)
    semantic_profile = (
        SemanticProfile.TECHNICAL_CONTROL
        if scope in {"control", "voice_control", "sfx_control"}
        else SemanticProfile.MAP_OR_SYSTEM
    )
    record.profile = EventProfile(
        primary_family="audio_control",
        semantic_profile=semantic_profile,
        requires_context=True,
        reason="matched generic non-VO/SFX audio control event",
    )
    record.semantic_slots = {
        "trigger_family": trigger_family,
        "control_scope": scope,
        "owner_context": record.owner.to_dict(),
        "control_head": other_control_head(record),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "dimension_token": first_dimension(record),
        "phase": first_phase(record),
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_other_audio_event_name",
    }
    record.confidence = (
        DecompositionConfidence.MEDIUM if scope in {"music", "item_or_system"} else DecompositionConfidence.LOW
    )
    record.unknowns.clear()
    return True


def fill_named_control_state_event(record: EventDecompositionRecord) -> None:
    """填充 Level/PackSize/VO 这类显式命名状态控制。"""

    group = record.lexical_atoms[0].raw
    path = [tail.raw for tail in record.lexical_atoms[1:]]
    record.profile = EventProfile(
        primary_family="audio_control",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched named audio state control event",
    )
    record.semantic_slots = {
        "trigger_family": "audio_state",
        "control_scope": "state",
        "state_group": group,
        "state_path": path,
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_other_audio_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()


def fill_audio_switch_event(record: EventDecompositionRecord) -> None:
    """填充 Wwise Switch 类控制事件。"""

    group, value = switch_group_and_value(record)
    record.profile = EventProfile(
        primary_family="audio_control",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched audio switch control event",
    )
    record.semantic_slots = {
        "trigger_family": "audio_switch",
        "control_scope": "switch",
        "switch_group": group,
        "switch_value": value,
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_other_audio_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()


def fill_audio_state_event(record: EventDecompositionRecord) -> None:
    """填充 Wwise State 类控制事件。"""

    group, path = state_group_and_path(record)
    record.profile = EventProfile(
        primary_family="audio_control",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched audio state control event",
    )
    record.semantic_slots = {
        "trigger_family": "audio_state",
        "control_scope": "state",
        "state_group": group,
        "state_path": path,
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_other_audio_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()


def fill_audio_parameter_event(record: EventDecompositionRecord) -> None:
    """填充 RTPC、VoicePitch 和 SetParam 参数控制事件。"""

    group, path = parameter_group_and_path(record)
    record.profile = EventProfile(
        primary_family="audio_control",
        semantic_profile=SemanticProfile.TECHNICAL_CONTROL,
        requires_context=True,
        reason="matched audio parameter control event",
    )
    record.semantic_slots = {
        "trigger_family": "audio_parameter",
        "control_scope": "parameter",
        "parameter_group": group,
        "parameter_path": path,
        "owner_context": record.owner.to_dict(),
        "asset_kind": record.shell.asset_kind,
        "verb": record.shell.verb,
        "cue_path": [tail.raw for tail in record.lexical_atoms],
        "source": "raw_other_audio_event_name",
    }
    record.confidence = DecompositionConfidence.MEDIUM
    record.unknowns.clear()


def is_other_audio_or_control_record(record: EventDecompositionRecord) -> bool:
    """判断记录是否属于非 VO/SFX 的音频控制兜底范围。"""

    asset_kind = (record.shell.asset_kind or "").lower()

    if asset_kind in {"vo", "sfx"}:
        return False

    body_key = record.shell.body.lower()
    owner_key = compact_text(record.owner.raw_owner_token or record.owner.alias or "")
    atom_keys = [compact_text(atom.raw) for atom in record.lexical_atoms]

    if asset_kind in {"item", "mus", "music", "rtpc", "state", "switch", "voicepitch"}:
        return True

    if record.shell.verb in {"switch", "setstate"}:
        return True

    if record.shell.verb == "set" and asset_kind in {"state", "switch"}:
        return True

    if body_key.startswith("pause_mus") or body_key.startswith("duck_sfx"):
        return True

    if record.shell.verb in {"play", "post", "set"} and asset_kind:
        return True

    if "_mus_" in body_key or body_key.startswith("play_mus_"):
        return True

    if "_vo_" in body_key or "_sfx_" in body_key or "_fx" in body_key:
        return True

    if owner_key in {"duck", "flange", "pause", "reset", "setstate"}:
        return True

    if any(atom in OTHER_CONTROL_HEADS for atom in atom_keys):
        return True

    return any(atom in {"fx", "level", "sfx", "vo"} for atom in atom_keys)


def is_parameter_control(record: EventDecompositionRecord, atom: LexicalAtom | None) -> bool:
    """判断记录是否是参数控制。"""

    asset_kind = (record.shell.asset_kind or "").lower()

    if asset_kind in {"rtpc", "voicepitch"}:
        return True

    return atom is not None and compact_text(atom.raw) == "setparam"


def is_named_control_state(record: EventDecompositionRecord) -> bool:
    """判断记录是否是 Level/PackSize 等明确命名状态。"""

    if len(record.lexical_atoms) < 2:
        return False

    return compact_text(record.lexical_atoms[0].raw) in NAMED_CONTROL_STATE_HEADS


def is_switch_control(record: EventDecompositionRecord, atom: LexicalAtom | None) -> bool:
    """判断记录是否是 Switch 控制。"""

    asset_kind = (record.shell.asset_kind or "").lower()

    if asset_kind == "switch" or record.shell.verb == "switch":
        return True

    return atom is not None and compact_text(atom.raw) == "switch"


def is_state_control(record: EventDecompositionRecord, atom: LexicalAtom | None) -> bool:
    """判断记录是否是 State 控制。"""

    asset_kind = (record.shell.asset_kind or "").lower()
    owner_key = compact_text(record.owner.raw_owner_token or record.owner.alias or "")

    if asset_kind == "state" or record.shell.verb == "setstate" or owner_key == "setstate":
        return True

    return atom is not None and compact_text(atom.raw) == "state"


def switch_group_and_value(record: EventDecompositionRecord) -> tuple[str | None, str | None]:
    """返回 Switch 控制组和值。"""

    asset_kind = (record.shell.asset_kind or "").lower()

    if asset_kind == "switch":
        group = record.owner.raw_owner_token or record.owner.alias
        value = record.lexical_atoms[0].raw if record.lexical_atoms else record.shell.body
        return group, value

    if record.shell.verb == "switch":
        group = record.shell.asset_kind
        value = record.shell.body or (record.lexical_atoms[0].raw if record.lexical_atoms else None)
        return group, value

    atom_index = 0
    if record.lexical_atoms and compact_text(record.lexical_atoms[0].raw) == "switch":
        atom_index = 1

    group = raw_atom_at(record, atom_index)
    value = raw_atom_at(record, atom_index + 1)
    return group, value


def state_group_and_path(record: EventDecompositionRecord) -> tuple[str | None, list[str]]:
    """返回 State 控制组和值路径。"""

    asset_kind = (record.shell.asset_kind or "").lower()
    owner_key = compact_text(record.owner.raw_owner_token or record.owner.alias or "")

    if asset_kind == "state":
        return record.owner.raw_owner_token or record.owner.alias, [tail.raw for tail in record.lexical_atoms]

    if record.shell.verb == "setstate":
        group = record.shell.asset_kind or record.owner.raw_owner_token or record.owner.alias
        path = [tail.raw for tail in record.lexical_atoms] or ([record.shell.body] if record.shell.body else [])
        return group, path

    if owner_key == "setstate":
        return record.owner.raw_owner_token or record.owner.alias, [tail.raw for tail in record.lexical_atoms]

    atom_index = 0
    if record.lexical_atoms and compact_text(record.lexical_atoms[0].raw) == "state":
        atom_index = 1

    group = raw_atom_at(record, atom_index)
    path = [tail.raw for tail in record.lexical_atoms[atom_index + 1 :]]
    return group, path


def other_control_scope(record: EventDecompositionRecord) -> str:
    """返回非 VO/SFX 控制事件的粗粒度范围。"""

    asset_kind = (record.shell.asset_kind or "").lower()
    body_key = record.shell.body.lower()

    if asset_kind in {"mus", "music"} or "_mus_" in body_key or record.owner.raw_owner_token == "Mus":
        return "music"

    if asset_kind == "item":
        return "item_or_system"

    if asset_kind in {"rtpc", "voicepitch"}:
        return "parameter"

    if body_key.startswith("duck_sfx") or "_sfx_" in body_key:
        return "sfx_control"

    if "_vo_" in body_key:
        return "voice_control"

    return "control"


def other_trigger_family(record: EventDecompositionRecord, scope: str) -> str:
    """返回非 VO/SFX 控制事件的触发族。"""

    if scope == "music":
        return "music_event"

    if record.shell.asset_kind == "item":
        return "item_audio_event"

    return "audio_control_or_music"


def parameter_group_and_path(record: EventDecompositionRecord) -> tuple[str | None, list[str]]:
    """返回参数控制组和值路径。"""

    asset_kind = (record.shell.asset_kind or "").lower()

    if asset_kind in {"rtpc", "voicepitch"}:
        return asset_kind, [tail.raw for tail in record.lexical_atoms]

    if record.lexical_atoms and compact_text(record.lexical_atoms[0].raw) == "setparam":
        return record.lexical_atoms[0].raw, [tail.raw for tail in record.lexical_atoms[1:]]

    return record.owner.raw_owner_token or record.owner.alias, [tail.raw for tail in record.lexical_atoms]


def other_control_head(record: EventDecompositionRecord) -> str | None:
    """返回控制事件的粗粒度主名。"""

    if record.lexical_atoms:
        return record.lexical_atoms[0].raw

    return record.owner.raw_owner_token or record.owner.alias or record.shell.body or None


def raw_atom_at(record: EventDecompositionRecord, index: int) -> str | None:
    """返回指定位置的 atom raw。"""

    if index >= len(record.lexical_atoms):
        return None

    return record.lexical_atoms[index].raw


def sfx_scope(record: EventDecompositionRecord) -> str:
    """按 owner/body 粗分 SFX 来源。"""

    owner = record.owner.raw_owner_token or record.owner.alias or ""
    owner_key = compact_text(owner)
    body_key = record.shell.body.lower()
    mode_owners = {
        "augment",
        "cherry",
        "env",
        "gamemode",
        "nexusblitz",
        "perks",
        "reciprocity",
        "ruby",
        "slime",
        "strawberry",
    }
    system_owners = {
        "hud",
        "ui",
    }

    if owner_key in system_owners:
        return "system_sfx"

    if owner_key in mode_owners:
        return "mode_sfx"

    if owner and owner[0].isdigit():
        return "item_or_system_sfx"

    if record.owner.kind == "champion" and (record.owner.skin_token is not None or record.owner.alias is not None):
        return "champion_sfx"

    return "system_sfx"


def sfx_effect_head(record: EventDecompositionRecord, atom: LexicalAtom) -> tuple[str, str]:
    """返回 SFX 的主效果名和去 owner 后的基础名。"""

    phase = first_part_value(atom, "phase_or_state_token") or phase_alias_from_atom(atom)
    owner = record.owner.raw_owner_token or record.owner.alias or ""

    if phase is not None and len(record.lexical_atoms) == 1 and owner:
        return owner, owner

    effect_head = atom.raw
    effect_base = first_part_raw(atom, "residue_token") or strip_owner_alias(effect_head, record.owner.alias)
    return effect_head, effect_base


def first_phase_from_atoms(atoms: list[LexicalAtom]) -> str | None:
    """返回 atom 列表中的第一个阶段。"""

    for atom in atoms:
        phase = (
            first_part_value(atom, "phase_or_state_token")
            or first_part_value(atom, "phase_token")
            or phase_alias_from_atom(atom)
        )

        if phase is not None:
            return phase

    return None


def compact_text(raw: str) -> str:
    """返回仅用于局部匹配的紧凑小写文本。"""

    return re.sub(r"[^a-z0-9]+", "", raw.lower())


def set_unknown_profile(record: EventDecompositionRecord, reason: str) -> None:
    """标记未知 profile。"""

    record.profile = EventProfile(
        primary_family="unknown",
        semantic_profile=SemanticProfile.UNKNOWN_PROFILE,
        requires_context=True,
        reason=reason,
    )
    record.confidence = DecompositionConfidence.LOW

    if not record.lexical_atoms:
        return

    raw = record.lexical_atoms[0].raw
    unknown_kind = (
        UnknownKind.AMBIGUOUS_SPELL_ALIAS if looks_like_named_spell_alias(raw) else UnknownKind.UNKNOWN_HEAD_TOKEN
    )
    record.unknowns.append(
        UnknownFragment(
            kind=unknown_kind,
            raw=raw,
            location="lexical_atoms[0]",
            blocking_reason=reason,
            candidate_meanings=["spell_alias", "visual_action_name", "internal_action_name"]
            if unknown_kind is UnknownKind.AMBIGUOUS_SPELL_ALIAS
            else [],
            severity="medium",
        )
    )


def fill_move(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充移动事件 slot。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    slots: dict[str, object] = {
        "trigger_family": "move",
        "dimension_token": dimension,
    }

    movement_context = parse_movement_context_residue(residue)

    if residue is None:
        slots["qualifier"] = "generic"
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.HIGH
    elif residue in {"Standard", "General"}:
        slots["qualifier"] = residue.lower()
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.HIGH
    elif residue == "First":
        slots["timing"] = "first"
        profile = SemanticProfile.QUALIFIED_ACTION
        confidence = DecompositionConfidence.HIGH
    elif movement_context is not None:
        if movement_context.get("dimension_token") is not None:
            slots["dimension_token"] = movement_context["dimension_token"]

        if movement_context.get("distance") is not None:
            slots["distance"] = movement_context["distance"]
            slots["tail_modifiers"] = [
                {
                    "raw": "Long",
                    "key": "long",
                    "kind": "movement_distance",
                    "label": "Long Distance",
                }
            ]

        if movement_context.get("context") is not None:
            slots["movement_context"] = movement_context["context"]
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.HIGH
    elif (spell_state := parse_move_spell_state(atom)) is not None:
        slots["spell_state"] = spell_state
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.MEDIUM
    elif residue and (precondition := parse_nearby_context(residue)) is not None:
        slots["qualifier"] = "generic"
        slots["precondition"] = precondition
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.MEDIUM
    elif residue and (relation := parse_relation_residue(residue)) is not None:
        timing, relation_name, target_raw = relation
        slots["timing"] = timing
        if relation_name:
            slots["relation"] = relation_name
        slots["counterpart"] = unresolved_counterpart(target_raw)
        profile = SemanticProfile.INTERACTION
        confidence = DecompositionConfidence.MEDIUM
        add_ambiguous_entity(record, target_raw, "semantic_slots.counterpart")
    else:
        slots["raw_residue"] = residue
        profile = SemanticProfile.UNKNOWN_PROFILE
        confidence = DecompositionConfidence.LOW
        record.unknowns.append(
            UnknownFragment(
                kind=UnknownKind.UNKNOWN_TAIL_TOKEN,
                raw=residue or atom.raw,
                location="semantic_slots.raw_residue",
                blocking_reason="move residue is not covered by current rules",
                severity="medium",
            )
        )

    record.profile = EventProfile(
        primary_family="move",
        semantic_profile=profile,
        requires_context=profile in {SemanticProfile.UNKNOWN_PROFILE, SemanticProfile.INTERACTION},
        reason="matched move trigger",
    )
    record.semantic_slots = slots
    record.confidence = confidence


def fill_first_encounter(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充首次遭遇事件 slot。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    residue, precondition = split_nearby_suffix(residue)
    counterpart = unresolved_counterpart(residue)
    record.profile = EventProfile(
        primary_family="first_encounter",
        semantic_profile=SemanticProfile.INTERACTION,
        requires_context=residue not in {None, "General"},
        reason="matched first encounter trigger",
    )
    record.semantic_slots = {
        "trigger_family": "first_encounter",
        "dimension_token": dimension,
        "counterpart": counterpart,
    }

    if precondition is not None:
        record.semantic_slots["precondition"] = precondition

    if counterpart and counterpart.get("kind") == "unresolved_entity":
        add_ambiguous_entity(record, str(counterpart["raw"]), "semantic_slots.counterpart")

    record.confidence = DecompositionConfidence.MEDIUM


def fill_kill(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充击杀、连杀和击杀目标 slot。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    residue, precondition = split_nearby_suffix(residue)
    milestone = normalize_kill_milestone(residue)
    counter = normalize_kill_counter(residue)
    kill_method = parse_kill_method_residue(residue)
    objective_steal = parse_objective_steal_residue(residue)
    qualifier = normalize_kill_qualifier(residue)
    counterpart = (
        None if milestone or counter or kill_method or objective_steal or qualifier else unresolved_counterpart(residue)
    )

    record.profile = EventProfile(
        primary_family="kill",
        semantic_profile=SemanticProfile.KILL_OR_SCORE,
        requires_context=counterpart is not None and counterpart.get("kind") == "unresolved_entity",
        reason="matched kill trigger",
    )
    record.semantic_slots = {
        "trigger_family": "kill",
        "dimension_token": dimension,
        "milestone": milestone,
        "counter": counter,
        "kill_method": kill_method,
        "objective_steal": objective_steal,
        "qualifier": qualifier,
        "counterpart": counterpart,
    }

    if precondition is not None:
        record.semantic_slots["precondition"] = precondition

    if counterpart and counterpart.get("kind") == "unresolved_entity":
        add_ambiguous_entity(record, str(counterpart["raw"]), "semantic_slots.counterpart")

    record.confidence = (
        DecompositionConfidence.HIGH
        if milestone or counter or kill_method or objective_steal or qualifier
        else DecompositionConfidence.MEDIUM
    )


def fill_spell(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充技能事件 slot。"""

    dimension = first_part_raw(atom, "dimension_token") or first_dimension(record)
    spell_key = first_part_value(atom, "spell_key_token")
    number = first_number(atom)
    phase = first_part_value(atom, "phase_or_state_token") or first_full_phase(record)
    numeric_rank = NUMERIC_SPELL_RANK_PATTERN.fullmatch(atom.raw)
    spell_rank = None
    if numeric_rank is not None:
        spell_key = numeric_rank.group("spell_key").lower()
        spell_rank = int(numeric_rank.group("rank"))
        number = None
    variant_kind = "spell_rank" if spell_rank is not None else "numbered_spell_variant" if number is not None else None

    record.profile = EventProfile(
        primary_family="spell",
        semantic_profile=SemanticProfile.SPELL_EVENT,
        requires_context=False,
        reason="matched spell trigger or spell key",
    )
    record.semantic_slots = {
        "trigger_family": "spell",
        "dimension_token": dimension,
        "spell_key": spell_key,
        "spell_stage": number,
        "spell_variant": number,
        "variant_kind": variant_kind,
        "spell_rank": spell_rank,
        "phase": phase,
        "line_variant": trailing_line_variant(record, atom),
        "requires_champion_mechanic_context": False,
    }

    record.confidence = DecompositionConfidence.MEDIUM


def fill_receive(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充收到增益、护盾、治疗等状态触发。"""

    dimension = first_part_raw(atom, "dimension_token")
    residue = first_part_raw(atom, "residue_token")
    received_effect = parse_received_effect(residue)

    record.profile = EventProfile(
        primary_family="receive",
        semantic_profile=SemanticProfile.SELF_ACTION
        if received_effect is not None
        else SemanticProfile.UNKNOWN_PROFILE,
        requires_context=received_effect is None,
        reason="matched receive trigger",
    )
    record.semantic_slots = {
        "trigger_family": "receive",
        "dimension_token": dimension,
        "received_effect": received_effect,
    }

    if received_effect is None:
        record.unknowns.append(
            UnknownFragment(
                kind=UnknownKind.UNKNOWN_TAIL_TOKEN,
                raw=residue or atom.raw,
                location="semantic_slots.received_effect",
                blocking_reason="receive residue is not covered by current rules",
                severity="medium",
            )
        )
        record.confidence = DecompositionConfidence.LOW
    else:
        record.confidence = DecompositionConfidence.HIGH


def fill_low_health(record: EventDecompositionRecord, atom: LexicalAtom) -> None:
    """填充低生命值状态语音。"""

    residue = first_part_raw(atom, "residue_token")
    qualifier = residue.lower() if residue == "General" else residue
    record.profile = EventProfile(
        primary_family="low_health",
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason="matched low health trigger",
    )
    record.semantic_slots = {
        "trigger_family": "low_health",
        "dimension_token": first_part_raw(atom, "dimension_token"),
        "health_state": {"kind": "low_health", "label": "Low Health"},
        "qualifier": qualifier,
    }
    record.confidence = DecompositionConfidence.HIGH


def fill_dialogue_chain(record: EventDecompositionRecord) -> None:
    """填充对话链 slot。"""

    first_atom = record.lexical_atoms[0]
    partner = first_part_raw(first_atom, "residue_token")
    base_atom = record.lexical_atoms[1] if len(record.lexical_atoms) > 1 else None
    turn_atom = record.lexical_atoms[-1] if record.lexical_atoms else None
    chat_take = None
    # Chat2_01 形态：尾 atom 是纯数字（无 chat 词头）时，它是该轮次的变体
    # take 号而非轮次本身，真正轮次在前一个 Chat{N} atom；take 号无损存
    # chat_take，不进文案（否则「第2句」的变体会被错标成「第1句」）。
    if (
        turn_atom is not None
        and len(record.lexical_atoms) >= 2
        and first_part_value(turn_atom, "trigger_token") is None
        and first_number(turn_atom) is not None
        and first_part_value(record.lexical_atoms[-2], "trigger_token") == "chat"
        and first_number(record.lexical_atoms[-2]) is not None
    ):
        chat_take = first_number(turn_atom)
        turn_atom = record.lexical_atoms[-2]
    fill_dialogue_chain_slots(
        record=record,
        chat_partner=partner,
        base_atom=base_atom,
        turn_atom=turn_atom,
        precondition=None,
        reason="matched chat trigger",
        chat_take=chat_take,
    )


def fill_dialogue_chain_slots(
    record: EventDecompositionRecord,
    chat_partner: str | None,
    base_atom: LexicalAtom | None,
    turn_atom: LexicalAtom | None,
    precondition: dict[str, object] | None,
    reason: str,
    chat_take: int | None = None,
) -> None:
    """统一填充普通对话链与带前置条件的对话链。

    Args:
        chat_take: 轮次的变体 take 号（Chat2_01 尾数字），无损入 slot、不进文案。
    """

    base_trigger = summarize_nested_trigger(base_atom)
    line_variant = base_trigger.get("line_variant") if base_trigger is not None else first_number(base_atom)
    record.profile = EventProfile(
        primary_family="dialogue",
        semantic_profile=SemanticProfile.DIALOGUE_CHAIN,
        requires_context=base_trigger is None,
        reason=reason,
    )
    record.semantic_slots = {
        "trigger_family": "dialogue",
        "chat_partner": unresolved_counterpart(chat_partner),
        "base_trigger": base_trigger,
        "chat_turn": first_number(turn_atom) if turn_atom else None,
        "chat_take": chat_take,
        "line_variant": line_variant,
    }

    if precondition is not None:
        record.semantic_slots["precondition"] = precondition

    if base_trigger is None:
        record.unknowns.append(
            UnknownFragment(
                kind=UnknownKind.NESTED_TRIGGER_UNRESOLVED,
                raw=record.raw_event_name,
                location="semantic_slots.base_trigger",
                blocking_reason="dialogue chain requires nested trigger review",
                severity="medium",
            )
        )
    record.confidence = DecompositionConfidence.MEDIUM


def fill_interaction_or_action(record: EventDecompositionRecord, atom: LexicalAtom, trigger: str) -> None:
    """填充攻击、协助和嘲讽等可能带目标的事件。"""

    residue = first_part_raw(atom, "residue_token")
    dimension = first_part_raw(atom, "dimension_token")
    trigger_family = normalize_trigger_family(trigger)
    slots: dict[str, object] = {
        "trigger_family": trigger_family,
        "dimension_token": dimension,
    }

    if residue and residue != "General":
        timing = normalize_timing_residue(residue)
        nested_action = parse_nested_action_residue(residue)

        if timing is not None:
            slots["timing"] = timing
            profile = SemanticProfile.SELF_ACTION
            confidence = DecompositionConfidence.HIGH
        elif nested_action is not None:
            slots["nested_action"] = nested_action
            profile = SemanticProfile.SELF_ACTION
            confidence = DecompositionConfidence.HIGH
        elif record.shell.asset_kind == "sfx":
            slots["sfx_variant"] = residue
            profile = SemanticProfile.SELF_ACTION
            confidence = DecompositionConfidence.MEDIUM
        else:
            counterpart = unresolved_counterpart(residue)
            slots["counterpart"] = counterpart

            if counterpart and counterpart.get("kind") == "unresolved_entity":
                add_ambiguous_entity(record, residue, "semantic_slots.counterpart")

            profile = SemanticProfile.INTERACTION
            confidence = DecompositionConfidence.MEDIUM
    else:
        slots["qualifier"] = (residue or "general").lower()
        profile = SemanticProfile.SELF_ACTION
        confidence = DecompositionConfidence.HIGH

    if profile is SemanticProfile.SELF_ACTION:
        add_self_action_context(record, atom, slots)

    record.profile = EventProfile(
        primary_family=trigger_family,
        semantic_profile=profile,
        requires_context=profile is SemanticProfile.INTERACTION,
        reason=f"matched {trigger} trigger",
    )
    record.semantic_slots = slots
    record.confidence = confidence


def fill_simple_action(record: EventDecompositionRecord, atom: LexicalAtom, trigger: str) -> None:
    """填充普通自身动作。"""

    trigger_family = normalize_trigger_family(trigger)
    qualifier = first_part_raw(atom, "residue_token")

    if qualifier is not None and qualifier.lower() == trigger.lower():
        qualifier = None

    record.profile = EventProfile(
        primary_family=trigger_family,
        semantic_profile=SemanticProfile.SELF_ACTION,
        requires_context=False,
        reason=f"matched {trigger} trigger",
    )
    slots: dict[str, object] = {
        "trigger_family": trigger_family,
        "dimension_token": first_part_raw(atom, "dimension_token"),
        "qualifier": qualifier,
    }
    add_self_action_context(record, atom, slots)
    if record.shell.asset_kind == "sfx":
        if trigger_family in {"basic_attack", "crit_attack"}:
            slots["attack_kind"] = trigger_family
            slots["attack_variant"] = first_number(atom)
    record.semantic_slots = slots
    record.confidence = DecompositionConfidence.HIGH


def normalize_trigger_family(trigger: str) -> str:
    """把粘连 trigger 名称规范化成稳定 family 名。"""

    return TRIGGER_FAMILY_NAMES.get(trigger, trigger)


def min_confidence(
    left: DecompositionConfidence,
    right: DecompositionConfidence,
) -> DecompositionConfidence:
    """取更低置信度，用于保守降级带推断上下文的结果。"""

    order = {
        DecompositionConfidence.FAILED: 0,
        DecompositionConfidence.LOW: 1,
        DecompositionConfidence.MEDIUM: 2,
        DecompositionConfidence.HIGH: 3,
    }
    return left if order[left] <= order[right] else right


def unresolved_counterpart(raw: str | None) -> dict[str, object] | None:
    """构造未解析实体候选。"""

    if raw is None:
        return None

    if raw == "General":
        return {"raw": raw, "kind": "general"}

    if raw == "Champion":
        return {"raw": raw, "kind": "target_class", "label": "Champion"}

    return {
        "raw": raw,
        "kind": "unresolved_entity",
        "candidate_kind": ["champion", "skin", "region", "faction", "skinline", "object"],
    }


def add_ambiguous_entity(record: EventDecompositionRecord, raw: str, location: str) -> None:
    """记录需要外部词典确认的实体。"""

    record.unknowns.append(
        UnknownFragment(
            kind=UnknownKind.AMBIGUOUS_ENTITY,
            raw=raw,
            location=location,
            blocking_reason="entity kind requires external dictionary or manual taxonomy",
            candidate_meanings=["champion", "skin", "region", "faction", "skinline", "object"],
            severity="medium",
        )
    )


def parse_relation_residue(raw: str) -> tuple[str, str, str] | None:
    """解析 FirstAllyQiyana / FirstEnemyStarGuardian 这类关系尾巴。"""

    for prefix, relation in [("FirstAlly", "ally"), ("FirstEnemy", "enemy")]:
        if raw.startswith(prefix) and len(raw) > len(prefix):
            return "first", relation, raw[len(prefix) :]

    if raw.startswith("First") and len(raw) > len("First"):
        return "first", "", raw[len("First") :]

    return None


def parse_movement_context_residue(raw: str | None) -> dict[str, object] | None:
    """解析移动事件的地点、命令和距离尾部。"""

    if raw is None:
        return None

    if raw in MOVEMENT_CONTEXT_RESIDUES:
        return {
            "context": {
                "raw": raw,
                **MOVEMENT_CONTEXT_RESIDUES[raw],
            },
            "dimension_token": None,
            "distance": None,
        }

    if raw == "Long":
        return {
            "context": None,
            "dimension_token": None,
            "distance": "long",
        }

    if raw == "LongAllSwords":
        return {
            "context": {
                "raw": raw,
                "kind": "equipment_state",
                "key": "all_swords",
                "label": "All Swords",
            },
            "dimension_token": None,
            "distance": "long",
        }

    order_long = re.fullmatch(r"Order(?P<dimension>[0-9]D)?(?P<distance>Long)?", raw)

    if order_long is None:
        return None

    return {
        "context": {
            "raw": raw,
            **MOVEMENT_CONTEXT_RESIDUES["Order"],
        },
        "dimension_token": order_long.group("dimension"),
        "distance": "long" if order_long.group("distance") else None,
    }


def parse_move_spell_state(atom: LexicalAtom) -> dict[str, object] | None:
    """解析 Move2DRReady 这类移动触发下的技能状态。"""

    residue = first_part_raw(atom, "residue_token")
    state = first_part_value(atom, "phase_or_state_token")

    if residue in {"Q", "W", "E", "R", "P"} and state is not None:
        return {
            "raw": f"{residue}{state.title()}",
            "spell_key": residue.lower(),
            "state": state,
        }

    residue_state = re.fullmatch(r"(?P<spell_key>[QWERP])(?P<state>Active|Ready)", residue or "")

    if residue_state is None:
        return None

    return {
        "raw": residue or "",
        "spell_key": residue_state.group("spell_key").lower(),
        "state": residue_state.group("state").lower(),
    }


def parse_nearby_observed_event(raw: str) -> dict[str, object] | None:
    """解析 Nearby3DDeathsAlly 这类附近其他单位事件。"""

    match = re.fullmatch(r"Nearby(?P<dimension>[0-9]D)(?P<body>.+?)(?P<relation>Ally|Enemy)", raw)

    if match is None:
        return None

    body = match.group("body")
    actions: dict[str, dict[str, object]] = {
        "Deaths": {"observed_action": "death"},
        "Death": {"observed_action": "death"},
        "Recall": {"observed_action": "recall"},
        "UseItemHealthPot": {"observed_action": "use_item", "item": "health_pot"},
    }
    action = actions.get(body)

    if action is None:
        return None

    return {
        "dimension_token": match.group("dimension"),
        "relation": match.group("relation").lower(),
        **action,
    }


def parse_received_effect(raw: str | None) -> dict[str, str] | None:
    """解析 Receive3D 后面的效果类型。"""

    if raw is None:
        return None

    values = {
        "Buff": ("buff", "Buff"),
        "Shield": ("shield", "Shield"),
        "Heal": ("heal", "Heal"),
    }

    for prefix, (kind, label) in values.items():
        if raw == prefix:
            return {"raw": raw, "kind": kind, "label": label}

        if raw.startswith(prefix) and len(raw) > len(prefix):
            return {
                "raw": raw,
                "kind": kind,
                "label": label,
                "source": raw[len(prefix) :],
            }

    return None


def normalize_kill_milestone(raw: str | None) -> str | None:
    """归一化击杀里程碑。"""

    values = {
        "Penta": "penta",
        "Pentakill": "penta",
        "Double": "double",
        "Triple": "triple",
        "Triplekill": "triple",
        "Quadra": "quadra",
        "First": "first",
        "FirstBlood": "first_blood",
        "Ace": "ace",
        "Assist": "assist",
    }
    return values.get(raw or "")


def normalize_kill_counter(raw: str | None) -> dict[str, str] | None:
    """归一化击杀、层数和计数类尾巴。"""

    values = {
        "Count": ("count", "计数"),
        "Counter": ("counter", "计数器"),
        "Streak": ("kill_streak", "连续击杀"),
    }
    value = values.get(raw or "")

    if value is None:
        return None

    kind, label = value
    return {"raw": raw or "", "kind": kind, "label": label}


def normalize_kill_qualifier(raw: str | None) -> str | None:
    """归一化击杀尾部的通用限定词。"""

    values = {
        "General": "general",
        "DGeneral": "general",
    }
    return values.get(raw or "")


def normalize_timing_residue(raw: str | None) -> str | None:
    """归一化动作尾部的时机类 residue。"""

    values = {
        "GameStart": "game_start",
    }
    return values.get(raw or "")


def parse_kill_method_residue(raw: str | None) -> dict[str, object] | None:
    """解析 Kill3DRActive / Kill3DQW 这类击杀方式尾巴。"""

    if raw is None:
        return None

    active_match = re.fullmatch(r"(?P<spell_key>[QWERP])(?P<state>Active|Ready)", raw)

    if active_match is not None:
        return {
            "kind": "spell",
            "raw": raw,
            "spell_key": active_match.group("spell_key").lower(),
            "state": active_match.group("state").lower(),
        }

    if raw in {"Ult", "Ultimate"}:
        return {"kind": "spell", "raw": raw, "spell_key": "r"}

    range_match = re.fullmatch(r"(?P<spell_key>[QWERP])(?P<range>Far|Long)", raw)

    if range_match is not None:
        return {
            "kind": "spell",
            "raw": raw,
            "spell_key": range_match.group("spell_key").lower(),
            "range": range_match.group("range").lower(),
        }

    sequence = parse_spell_sequence_token(raw)

    if len(sequence) >= 2:
        return {
            "kind": "spell_sequence",
            "raw": raw,
            "spell_sequence": sequence,
        }

    return None


def parse_objective_steal_residue(raw: str | None) -> dict[str, object] | None:
    """解析 DragonSteal / StealBaron 这类抢目标上下文。"""

    if raw is None:
        return None

    values = {
        "DragonSteal": "dragon",
        "StealDragon": "dragon",
        "StealBaron": "baron",
        "BaronSteal": "baron",
        "EpicSteal": "epic_objective",
    }
    objective = values.get(raw)

    if objective is None:
        return None

    return {
        "raw": raw,
        "objective": objective,
        "result": "steal",
    }


def parse_nested_action_residue(raw: str | None) -> dict[str, object] | None:
    """解析 TauntJokeResponse3DGeneral 里的嵌套动作尾巴。"""

    if raw is None:
        return None

    match = re.fullmatch(
        r"(?P<trigger>DanceResponse|JokeResponse|TauntResponse)(?P<dimension>[0-9]D)?(?P<qualifier>General)?",
        raw,
    )

    if match is None:
        return None

    family = normalize_trigger_family(match.group("trigger").lower())
    return {
        "family": family,
        "dimension_token": match.group("dimension"),
        "qualifier": "general" if match.group("qualifier") else None,
        "raw": raw,
    }


def parse_spell_sequence_token(raw: str | None) -> list[str]:
    """解析 QW/RQ 这类紧凑技能键序列。"""

    if raw is None or re.fullmatch(r"[QWERP]{2,4}", raw) is None:
        return []

    return [char.lower() for char in raw]


def parse_nearby_context(raw: str) -> dict[str, object] | None:
    """解析 WithXNear 这类英雄在附近的触发条件。"""

    match = re.fullmatch(r"With(?P<target>[A-Z][A-Za-z0-9]*?)Near", raw)

    if match is None:
        return None

    target = match.group("target")
    return {
        "raw": raw,
        "kind": "nearby_champion",
        "proximity": "near",
        "counterpart": unresolved_counterpart(target),
    }


def split_nearby_suffix(raw: str | None) -> tuple[str | None, dict[str, object] | None]:
    """从 ThreshWithSennaNear / WithSennaNear 中拆出主体和附近条件。"""

    if raw is None:
        return None, None

    match = re.fullmatch(r"(?P<target>.*?)(?P<context>With[A-Z][A-Za-z0-9]*Near)", raw)

    if match is None:
        return raw, None

    target = match.group("target") or None
    return target, parse_nearby_context(match.group("context"))


def parse_announcer_persona(atom: LexicalAtom | None) -> dict[str, object] | None:
    """解析 Female1 / Male1 这类通告员人声编号。"""

    if atom is None:
        return None

    raw = first_part_raw(atom, "residue_token")

    if raw not in {"Female", "Male"}:
        return None

    return {
        "raw": atom.raw,
        "gender": raw.lower(),
        "variant": first_number(atom),
    }


def summarize_nested_trigger(atom: LexicalAtom | None) -> dict[str, object] | None:
    """把嵌套触发 atom 摘要成 slot。"""

    if atom is None:
        return None

    trigger = first_part_value(atom, "trigger_token")
    family = normalize_trigger_family(trigger) if trigger is not None else None
    counterpart_raw = first_part_raw(atom, "residue_token")
    line_variant = first_number(atom)

    if counterpart_raw is not None:
        prefixed_variant = re.fullmatch(r"(?P<variant>[0-9]+)(?P<target>[A-Za-z][A-Za-z0-9]*)", counterpart_raw)

        if prefixed_variant is not None:
            line_variant = line_variant or int(prefixed_variant.group("variant"))
            counterpart_raw = prefixed_variant.group("target")

    if family is None:
        return None

    return {
        "family": family,
        "dimension_token": first_part_raw(atom, "dimension_token"),
        "counterpart_raw": counterpart_raw,
        "line_variant": line_variant,
        "raw": atom.raw,
    }


def first_part_raw(atom: LexicalAtom, kind: str) -> str | None:
    """返回指定 kind 的第一个 raw 值。"""

    for part in atom.parts:
        if part.kind == kind:
            return part.raw

    return None


def first_part_value(atom: LexicalAtom, kind: str) -> str | None:
    """返回指定 kind 的第一个 value 字符串。"""

    for part in atom.parts:
        if part.kind == kind and part.value is not None:
            return str(part.value)

    return None


def first_number(atom: LexicalAtom | None) -> int | None:
    """返回 atom 中第一个数字。"""

    if atom is None:
        return None

    for part in atom.parts:
        if part.kind == "number_token" and isinstance(part.value, int):
            return part.value

    return None


def trailing_line_variant(record: EventDecompositionRecord, atom: LexicalAtom) -> int | None:
    """返回技能 atom 后紧邻的纯数字行号。"""

    next_index = atom.chunk_index + 1

    if next_index >= len(record.lexical_atoms):
        return None

    next_atom = record.lexical_atoms[next_index]

    if next_atom.raw.isdigit():
        return first_number(next_atom)

    return None


def first_dimension(record: EventDecompositionRecord) -> str | None:
    """返回记录中第一个维度 token。"""

    for atom in record.lexical_atoms:
        dimension = first_part_raw(atom, "dimension_token")

        if dimension is not None:
            return dimension

    return None


def first_phase(record: EventDecompositionRecord) -> str | None:
    """返回记录中第一个阶段 token。"""

    for atom in record.lexical_atoms:
        phase = (
            first_part_value(atom, "phase_or_state_token")
            or first_part_value(atom, "phase_token")
            or phase_alias_from_atom(atom)
        )

        if phase is not None:
            return phase

    return None


def looks_like_named_spell_alias(raw: str) -> bool:
    """判断未知 head 是否像命名技能、视觉技能或技能等级别名。"""

    return bool(
        re.search(
            r"(Rank[0-9A-Za-z]+|Strike|Shot|Blade|Arrow|Flare|Wave|Toss|Attack|Toggle|Dice|Volley)",
            raw,
        )
    )


def has_spell_alias_term(raw: str) -> bool:
    """判断内部别名里是否含常见技能或视觉动作词。"""

    terms = {
        "Automatic",
        "Barrage",
        "Bind",
        "Bomb",
        "Cleave",
        "Execute",
        "Feint",
        "Fireball",
        "Grenade",
        "Grab",
        "Hallucinate",
        "Infest",
        "Kick",
        "Landslide",
        "Missile",
        "Nimbus",
        "Orbs",
        "Passive",
        "Sound",
        "Shadows",
        "Shield",
        "Spin",
        "Spear",
        "Stance",
        "Teleport",
        "Ult",
        "Whirlwind",
        "Wind",
        "Command",
    }
    return any(term in raw for term in terms)


def infer_spell_key_hint(raw: str) -> str | None:
    """从 Ult 或紧凑技能键片段中提取低置信键位提示。"""

    if "Ult" in raw or "Ultimate" in raw:
        return "r"

    match = re.search(r"(?:^|VO)([QWERP])(?=[A-Z0-9])", raw)

    if match is not None:
        return match.group(1).lower()

    embedded = re.search(r"(?<=[a-z0-9])([QWERP])(?=[A-Z][a-z])", raw)

    if embedded is None:
        return None

    return embedded.group(1).lower()


def strip_owner_alias(raw: str, owner_alias: str | None) -> str:
    """移除技能别名里重复出现的 owner alias。"""

    if owner_alias and raw.lower().startswith(owner_alias.lower()) and len(raw) > len(owner_alias):
        return raw[len(owner_alias) :]

    return raw


def parse_owner_spell_token(raw: str) -> dict[str, object] | None:
    """从 AnnieQ / IvernWPassive 这类 owner token 里提取技能键。"""

    match = re.fullmatch(r"(?P<subject>[A-Za-z][A-Za-z0-9]*?)(?P<spell_key>[QWERP])(?P<state>Passive)?", raw)

    if match is None:
        return None

    return {
        "subject_hint": match.group("subject"),
        "spell_key": match.group("spell_key").lower(),
        "owner_spell_state": "passive" if match.group("state") else None,
    }


def phase_alias_from_atom(atom: LexicalAtom) -> str | None:
    """把 OnCast 等完整 atom 映射为阶段值。"""

    values = {
        "onbuffactivate": "on_buff_activate",
        "onbuffdeactivate": "on_buff_deactivate",
        "onbuffcast": "on_buff_cast",
        "buffactivate": "buff_activate",
        "buffdeactivate": "buff_deactivate",
        "buffcast": "buff_cast",
        "cast": "cast",
        "hit": "hit",
        "onhit": "hit",
        "missilelaunch": "missile_launch",
        "onmissilelaunch": "missile_launch",
        "oml": "missile_launch",
        "misslelaunch": "missile_launch",
        "launch": "launch",
        "missilecast": "missile_cast",
        "oncast": "cast",
        "activate": "activate",
        "deactivate": "deactivate",
    }
    return values.get(atom.raw.lower())


def next_phase_atom(record: EventDecompositionRecord, start_index: int) -> LexicalAtom | None:
    """返回 start_index 之后第一个阶段 atom。"""

    for atom in record.lexical_atoms[start_index:]:
        if phase_alias_from_atom(atom) is not None:
            return atom

    return None


def raw_atom_after(record: EventDecompositionRecord, index: int) -> str | None:
    """返回 index 后一个 atom 的 raw。"""

    next_index = index + 1

    if next_index >= len(record.lexical_atoms):
        return None

    return record.lexical_atoms[next_index].raw


def snake_case(raw: str) -> str:
    """把简单 PascalCase 名称转成 snake_case。"""

    return re.sub(r"(?<!^)([A-Z])", r"_\1", raw).lower()
