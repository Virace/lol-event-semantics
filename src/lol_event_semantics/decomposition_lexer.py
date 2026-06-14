"""事件名词法拆解。"""

from __future__ import annotations

import re

from .decomposition_model import (
    DecompositionConfidence,
    EventDecompositionRecord,
    EventOwnerRef,
    EventShell,
    LexicalAtom,
    LexicalPart,
    UnknownFragment,
    UnknownKind,
)

SCHEMA_VERSION = "event-decomposition.v1"
DIMENSION_PATTERN = re.compile(r"^(?P<prefix>.*?)(?P<dimension>[0-9]D)(?P<suffix>.*)$")
TRAILING_PHASE_DIMENSION_PATTERN = re.compile(
    r"^(cast|hit|buffactivate|buffdeactivate|buffcast|missilelaunch|missilecast)([0-9]D)(.*)$",
    re.IGNORECASE,
)
PHASE_SUFFIX_PATTERN = re.compile(r"(Cast|Hit|End|Ready|Root|Stun|Max|Revive|RankOne|RankTwo|RankThree|RankFour|RankFive)$")
SPELL_KEY_PREFIX_PATTERN = re.compile(r"^([QWERP])([0-9]*)(.*)$")
MAP_OR_SYSTEM_OWNER_TOKENS = {"HA", "SRU"}


def decompose_event_name(
    raw_event_name: str,
    game_version: str,
    source_path: str | None = None,
) -> EventDecompositionRecord:
    """把单个原始事件名拆成词法记录。"""

    shell = parse_shell(raw_event_name)
    scope = infer_scope(shell, source_path)
    record = EventDecompositionRecord(
        schema_version=SCHEMA_VERSION,
        game_version=game_version,
        raw_event_name=raw_event_name,
        scope=scope,
        source_paths=[source_path] if source_path else [],
        shell=shell,
    )

    if not shell.body:
        record.unknowns.append(
            UnknownFragment(
                kind=UnknownKind.UNPARSED_FRAGMENT,
                raw=raw_event_name,
                location="raw_event_name",
                blocking_reason="shell body is empty",
                severity="high",
            )
        )
        record.confidence = DecompositionConfidence.FAILED
        return record

    owner_token, body_chunks = split_owner_and_chunks(shell.body)
    record.owner = parse_owner(owner_token, scope)
    record.lexical_atoms = [
        lex_chunk(chunk, index, record.owner.alias)
        for index, chunk in enumerate(body_chunks)
    ]

    if not record.lexical_atoms:
        record.unknowns.append(
            UnknownFragment(
                kind=UnknownKind.UNPARSED_FRAGMENT,
                raw=shell.body,
                location="shell.body",
                blocking_reason="event body has owner but no tail chunks",
                severity="medium",
            )
        )

    return record


def parse_shell(raw_event_name: str) -> EventShell:
    """解析 Play_vo_* 这类外壳。"""

    match = re.match(r"^(Play|Stop|Set|Switch|SetState|Mute)_([^_]+)_(.+)$", raw_event_name)

    if match is not None:
        return EventShell(
            verb=match.group(1).lower(),
            asset_kind=match.group(2).lower(),
            body=match.group(3),
        )

    implicit_asset_match = re.match(r"^(vo|sfx|mus)_(.+)$", raw_event_name, re.IGNORECASE)

    if implicit_asset_match is not None:
        return EventShell(
            asset_kind=implicit_asset_match.group(1).lower(),
            body=implicit_asset_match.group(2),
        )

    return EventShell(body=raw_event_name)


def infer_scope(shell: EventShell, source_path: str | None) -> str:
    """根据路径和 body 粗略推断作用域。"""

    normalized_path = (source_path or "").replace("\\", "/").lower()

    if "/maps/" in normalized_path:
        return "map"

    if "/champions/" in normalized_path:
        return "champion"

    if shell.body.lower().startswith("env_map") or re.search(r"map[0-9]+", shell.body, re.IGNORECASE):
        return "map"

    if shell.asset_kind == "vo" and shell.body:
        return "champion"

    return "unknown"


def split_owner_and_chunks(body: str) -> tuple[str, list[str]]:
    """把 body 拆成 owner token 和后续 chunk。"""

    parts = [part for part in body.split("_") if part]

    if not parts:
        return "", []

    return merge_split_owner_skin(parts[0], parts[1:])


def merge_split_owner_skin(owner_token: str, chunks: list[str]) -> tuple[str, list[str]]:
    """合并 owner 后被下划线拆开的 SkinNN 皮肤编号。"""

    if not chunks:
        return owner_token, chunks

    owner_match = re.fullmatch(
        r"(?P<alias>[A-Za-z][A-Za-z0-9]*?)(?P<skin>Skin[0-9]+)?",
        owner_token,
        re.IGNORECASE,
    )

    if owner_match is None:
        return owner_token, chunks

    first_chunk = chunks[0]
    owner_alias = owner_match.group("alias")
    owner_skin = owner_match.group("skin")
    split_skin = re.fullmatch(r"Skin[0-9]+", first_chunk, re.IGNORECASE)

    if split_skin is not None:
        if owner_skin is None:
            return f"{owner_token}{first_chunk}", chunks[1:]

        if compact_skin_token(owner_skin) == compact_skin_token(first_chunk):
            return owner_token, chunks[1:]

    repeated_owner_skin = re.fullmatch(
        rf"{re.escape(owner_alias)}(?P<skin>Skin[0-9]+)",
        first_chunk,
        re.IGNORECASE,
    )

    if owner_skin is None and repeated_owner_skin is not None:
        return first_chunk, chunks[1:]

    return owner_token, chunks


def compact_skin_token(token: str) -> str:
    """归一化 SkinNN 比较键。"""

    return token.lower()


def parse_owner(owner_token: str, scope: str) -> EventOwnerRef:
    """从 owner token 中保守提取 alias 和皮肤 token。"""

    if scope == "map" or owner_token in MAP_OR_SYSTEM_OWNER_TOKENS:
        return EventOwnerRef(kind="map_or_system", alias=owner_token or None, raw_owner_token=owner_token or None)

    match = re.match(r"^(?P<alias>[A-Za-z][A-Za-z0-9]*?)(?P<skin>Skin[0-9]+)?$", owner_token)

    if match is None:
        return EventOwnerRef(kind="unknown", raw_owner_token=owner_token or None)

    return EventOwnerRef(
        kind="champion",
        alias=match.group("alias"),
        skin_token=match.group("skin"),
        raw_owner_token=owner_token,
    )


def lex_chunk(chunk: str, chunk_index: int, owner_alias: str | None) -> LexicalAtom:
    """把一个下划线 chunk 拆成词法 atom。"""

    work = strip_owner_prefix(chunk, owner_alias)
    parts = split_inline_parts(work)
    normalized = " / ".join(str(part.raw) for part in parts) if parts else work

    return LexicalAtom(
        raw=chunk,
        normalized=normalized,
        chunk_index=chunk_index,
        parts=parts,
        source="pattern:inline",
        confidence=DecompositionConfidence.MEDIUM if parts else DecompositionConfidence.LOW,
    )


def strip_owner_prefix(chunk: str, owner_alias: str | None) -> str:
    """移除动作 chunk 里重复出现的 owner alias。"""

    if owner_alias and chunk.lower().startswith(owner_alias.lower()) and len(chunk) > len(owner_alias):
        return chunk[len(owner_alias) :]

    return chunk


def split_inline_parts(chunk: str) -> list[LexicalPart]:
    """识别 trigger、dimension、技能键、数字和 phase。"""

    parts: list[LexicalPart] = []
    work = chunk
    trigger, work = peel_trigger(work)

    if trigger:
        parts.append(LexicalPart(kind="trigger_token", raw=trigger, value=trigger.lower()))

    action_variant_dimension = re.fullmatch(r"(?P<variant>[0-9]+)(?P<dimension>[0-9]D)", work)

    if trigger is not None and action_variant_dimension is not None:
        variant = action_variant_dimension.group("variant")
        dimension = action_variant_dimension.group("dimension")
        parts.append(LexicalPart(kind="number_token", raw=variant, value=int(variant)))
        parts.append(LexicalPart(kind="dimension_token", raw=dimension, value=dimension))
        return parts

    trailing_dimension = TRAILING_PHASE_DIMENSION_PATTERN.match(work)

    if trailing_dimension is not None:
        phase, dimension, suffix = trailing_dimension.groups()
        parts.append(LexicalPart(kind="dimension_token", raw=dimension, value=dimension))
        parts.append(LexicalPart(kind="phase_token", raw=phase, value=phase.lower()))

        if suffix:
            parts.append(LexicalPart(kind="residue_token", raw=suffix, value=suffix))

        return parts

    dimension_match = DIMENSION_PATTERN.match(work)

    if dimension_match is not None and dimension_match.group("prefix") == "":
        dimension = dimension_match.group("dimension")
        parts.append(LexicalPart(kind="dimension_token", raw=dimension, value=dimension))
        work = dimension_match.group("suffix")
    elif dimension_match is not None and dimension_match.group("suffix") == "":
        dimension = dimension_match.group("dimension")
        prefix = dimension_match.group("prefix")
        parts.append(LexicalPart(kind="dimension_token", raw=dimension, value=dimension))
        parts.extend(split_suffix_parts(prefix, allow_spell_key=False))
        return parts

    # 非技能 trigger 里的 Penta/Pentakill 不能被误切成 P 技能。
    allow_spell_key = trigger is None or trigger == "Spell"
    parts.extend(split_suffix_parts(work, allow_spell_key=allow_spell_key))

    return parts


def peel_trigger(chunk: str) -> tuple[str | None, str]:
    """按最长优先剥离已知 trigger。"""

    triggers = [
        "FirstEncounter",
        "KillChampion",
        "JokeTauntResponse",
        "TauntResponse",
        "JokeResponse",
        "MasteryResponse",
        "BasicAttack",
        "CritAttack",
        "LowHealth",
        "FirstMove",
        "Homeguard",
        "Winddown",
        "BuyItem",
        "UseItem",
        "LevelUp",
        "GameEnd",
        "Move",
        "Attack",
        "Assist",
        "Heal",
        "Kill",
        "Death",
        "Recall",
        "Respawn",
        "Receive",
        "Joke",
        "Laugh",
        "Taunt",
        "Dance",
        "Spell",
        "Shop",
        "Ping",
        "Chat",
        "Idle",
        "Unique",
    ]

    for trigger in triggers:
        if chunk.lower().startswith(trigger.lower()):
            remainder = chunk[len(trigger) :]

            if not trigger_boundary_ok(remainder):
                continue

            return trigger, chunk[len(trigger) :]

    return None, chunk


def trigger_boundary_ok(remainder: str) -> bool:
    """避免把 KillerCut 这类普通词里的 Kill 当作 trigger。"""

    if not remainder:
        return True

    return not remainder[0].islower()


def split_suffix_parts(suffix: str, allow_spell_key: bool = True) -> list[LexicalPart]:
    """拆出常见技能键、数字、阶段和剩余片段。"""

    if not suffix:
        return []

    parts: list[LexicalPart] = []
    work = suffix
    key_match = SPELL_KEY_PREFIX_PATTERN.match(work) if allow_spell_key else None

    if key_match is not None and is_confident_spell_key_suffix(key_match.group(2), key_match.group(3)):
        key, number, rest = key_match.groups()
        parts.append(LexicalPart(kind="spell_key_token", raw=key, value=key.lower()))

        if number and rest.upper().startswith("D"):
            parts.append(LexicalPart(kind="dimension_token", raw=f"{number}D", value=f"{number}D"))
            work = rest[1:]
        else:
            if number:
                parts.append(LexicalPart(kind="number_token", raw=number, value=int(number)))

            work = rest

    phase_match = PHASE_SUFFIX_PATTERN.search(work)

    if phase_match is not None:
        prefix = work[: phase_match.start()]
        phase = phase_match.group(1)

        append_residue_or_number(parts, prefix)
        parts.append(LexicalPart(kind="phase_or_state_token", raw=phase, value=phase.lower()))
    elif work:
        append_residue_or_number(parts, work)

    return parts


def is_confident_spell_key_suffix(number: str, rest: str) -> bool:
    """判断首字母是否足够像技能键，而不是普通单词首字母。"""

    if not rest:
        return True

    if number:
        return True

    return PHASE_SUFFIX_PATTERN.fullmatch(rest) is not None


def append_residue_or_number(parts: list[LexicalPart], raw: str) -> None:
    """把剩余文本拆成残留 token 和可选尾部数字。"""

    if not raw:
        return

    if raw.isdigit():
        parts.append(LexicalPart(kind="number_token", raw=raw, value=int(raw)))
        return

    trailing_number = re.match(r"^(.+?)([0-9]+)$", raw)

    if trailing_number is not None and not re.search(r"Skin[0-9]+$", raw):
        residue, number = trailing_number.groups()
        parts.append(LexicalPart(kind="residue_token", raw=residue, value=residue))
        parts.append(LexicalPart(kind="number_token", raw=number, value=int(number)))
        return

    parts.append(LexicalPart(kind="residue_token", raw=raw, value=raw))
