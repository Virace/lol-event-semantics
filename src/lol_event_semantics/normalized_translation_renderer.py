"""normalized translation 纯渲染器。"""

from __future__ import annotations

import re
from typing import Any

from .normalized_translation_model import (
    FRAME_REGISTRY,
    RenderOptions,
    RenderResult,
    is_phase_suppressed_by_playback,
)

CONTEXT_ROLES = {"champion", "skin", "map", "mode", "context"}
TECHNICAL_USER_TOKENS = ("Play_", "OnBuffDeactivate", "buffactivate", "3D", "2D")
# counterpart frame 短语的亚词后缀（builder ASSET_KIND_WORDS 的 zh 侧），
# 定制 join 用它把 counterpart 中缀进「击杀 {cp} 语音」类措辞。
ASSET_WORD_SUFFIXES = ("语音", "音效", "音乐")


def render_translation(
    translation: dict[str, object],
    options: RenderOptions | None = None,
) -> RenderResult:
    """按语言、模式和上下文渲染 translation frame。

    Args:
        translation: translation frame 字典。
        options: 渲染参数。

    Returns:
        渲染结果。
    """

    active_options = options or RenderOptions()
    frame_type = string_or_none(translation.get("frame_type"))
    confidence = string_or_none(translation.get("frame_confidence")) or "unknown"

    if frame_type is None:
        return RenderResult("", None, confidence, diagnostics=("missing_frame_type",))

    template_roles = roles_for_template(frame_type, active_options.locale, active_options.mode)
    if not template_roles:
        return RenderResult("", frame_type, confidence, diagnostics=("missing_render_template",))

    arguments = translation.get("arguments")
    if not isinstance(arguments, dict):
        return RenderResult("", frame_type, confidence, diagnostics=("missing_arguments",))

    # playback（停止/静音）是全 frame 通用的壳层语义，模板未声明时统一追加在末尾，任何模式都不得丢弃。
    if "playback" in arguments and "playback" not in template_roles:
        template_roles = (*template_roles, "playback")
    # 已声明的特殊模式/机制上下文是全 frame 语义；旧 frame 模板未列出时
    # 统一置于句首，避免 Ruby/OdysseyAugments 等前缀在渲染阶段再次丢失。
    if "context" in arguments and "context" not in template_roles:
        template_roles = ("context", *template_roles)
    if "spell_context" in arguments and "spell_context" not in template_roles:
        template_roles = (*template_roles, "spell_context")

    role_texts: list[tuple[str, str]] = []
    used_roles: list[str] = []
    diagnostics: list[str] = []
    for role in template_roles:
        if role == "phase" and is_phase_suppressed_by_playback(arguments):
            continue

        argument = arguments.get(role)
        if not isinstance(argument, dict):
            diagnostics.append(f"missing_role:{role}")
            continue

        if argument_rendered_via_other_role(argument, arguments):
            continue

        # 上下文省略只看 argument 自带标记（T4.1 机制统一），对所有模式生效。
        if argument_omitted_for_context(argument, active_options.known_context):
            continue

        text, argument_diagnostics = argument_text(argument, active_options.locale, active_options.fallback)
        diagnostics.extend(argument_diagnostics)
        if text:
            role_texts.append((role, text))
            used_roles.append(role)

    text = render_role_texts(frame_type, active_options.locale, role_texts, arguments, active_options.mode)
    if active_options.mode not in {"debug", "audit"}:
        text = strip_technical_user_tokens(text)

    return RenderResult(
        text=text,
        frame_type=frame_type,
        confidence=confidence,
        used_roles=tuple(used_roles),
        omitted_roles=omitted_roles_for_rendering(
            frame_type, active_options.locale, active_options.known_context, arguments
        ),
        diagnostics=tuple(dict.fromkeys(diagnostics)),
    )


def render_role_texts(
    frame_type: str,
    locale: str,
    role_texts: list[tuple[str, str]],
    arguments: dict[str, Any] | None = None,
    mode: str = "default",
) -> str:
    """按 frame、语言和模式合并 role 文案。

    Args:
        frame_type: translation frame 类型。
        locale: 目标语言。
        role_texts: `(role, text)` 列表。
        arguments: translation arguments 字典，供定制 join 读取结构信息。
        mode: 渲染模式；counterpart frame 的英文回退片段只进 default/debug/audit。

    Returns:
        合并后的文案。
    """

    if locale != "zh_CN":
        return " ".join(text for _role, text in role_texts)

    if frame_type == "skin_idle_state":
        return join_skin_idle_zh_cn(role_texts)

    if frame_type == "kill_or_destroy_event":
        return join_kill_or_destroy_zh_cn(role_texts, arguments or {}, mode)

    if frame_type == "champion_interaction_cue":
        return join_interaction_cue_zh_cn(role_texts, arguments or {}, mode)

    if frame_type == "champion_dialogue_event":
        return join_dialogue_event_zh_cn(role_texts, mode)

    if frame_type == "shop_voice_event":
        return join_shop_voice_zh_cn(role_texts)

    if frame_type == "announcer_event":
        return join_announcer_event_zh_cn(role_texts)

    if frame_type == "mode_unit_attack_event":
        return join_mode_unit_attack_zh_cn(role_texts)

    context = (arguments or {}).get("context")
    provenance = context.get("provenance") if isinstance(context, dict) else None
    if frame_type == "system_sfx_event" and isinstance(provenance, dict) and provenance.get("item_id") is not None:
        # 精确装备事件的主体是名词前缀，中文直接连接效果；模式上下文仍使用原有空格。
        return join_zh_cn([("item" if role == "context" else role, text) for role, text in role_texts])

    return join_zh_cn(role_texts)


def argument_text(argument: dict[str, object], locale: str, fallback: str) -> tuple[str, tuple[str, ...]]:
    """返回 argument 文案和诊断。

    Args:
        argument: translation argument 字典。
        locale: 目标语言。
        fallback: label 缺失时的回退策略。

    Returns:
        `(text, diagnostics)`。
    """

    diagnostics: list[str] = []
    role = string_or_none(argument.get("role")) or "unknown"
    labels = argument.get("labels")
    text = ""

    if isinstance(labels, dict):
        label = labels.get(locale) or labels.get("zh_CN") or labels.get("en_US")
        if isinstance(label, str):
            text = label

    if not text:
        text = fallback_argument_text(argument, fallback)
        diagnostics.append(f"missing_label:{role}")

    if argument.get("fallback") is True:
        diagnostics.append(f"translation_label_fallback:{role}")

    return text, tuple(diagnostics)


def roles_for_template(frame_type: str, locale: str, mode: str) -> tuple[str, ...]:
    """选择模板 role 顺序。

    模板只声明 role 顺序，不承担上下文省略职责（T4.1 机制统一，省略见
    argument_omitted_for_context）。未显式定义的 mode 继承 default，缺失
    locale 回落 zh_CN，注册表因此只需为有差异的组合显式建模板。

    Args:
        frame_type: translation frame 类型。
        locale: 目标语言。
        mode: 渲染模式。

    Returns:
        role 顺序；没有模板时返回空 tuple。
    """

    spec = FRAME_REGISTRY.get(frame_type)
    if spec is None:
        return ()

    locale_templates = spec.templates.get(locale) or spec.templates.get("zh_CN")
    if locale_templates is None:
        return ()

    return locale_templates.get(mode) or locale_templates.get("default") or ()


def argument_omitted_for_context(argument: dict[str, Any], known_context: frozenset[str]) -> bool:
    """判断 argument 是否因上下文已知而省略。

    省略判定的唯一依据（T4.1 机制统一）：argument 自带的
    omit_when_context_known 标记与调用方 known_context 的交集，对所有
    渲染模式一致生效。counterpart 等不带标记的角色因此天然不可省略。

    Args:
        argument: translation argument 字典。
        known_context: 调用方已知上下文。

    Returns:
        应省略时返回 True。
    """

    omit_when_known = argument.get("omit_when_context_known")
    if not isinstance(omit_when_known, list):
        return False

    return bool(known_context.intersection(str(item) for item in omit_when_known))


def argument_rendered_via_other_role(
    argument: dict[str, Any],
    arguments: dict[str, Any],
) -> bool:
    """判断结构化 argument 是否已被另一个渲染 role 消费。

    Args:
        argument: 当前 translation argument。
        arguments: 同一 frame 的完整 arguments。

    Returns:
        provenance.rendered_via 指向现存 role 时返回 True。
    """

    provenance = argument.get("provenance")
    if not isinstance(provenance, dict):
        return False

    rendered_via = string_or_none(provenance.get("rendered_via"))
    return rendered_via is not None and isinstance(arguments.get(rendered_via), dict)


def join_zh_cn(role_texts: list[tuple[str, str]]) -> str:
    """按中文显示规则合并 role 文案。

    Args:
        role_texts: `(role, text)` 列表。

    Returns:
        中文文案。
    """

    output = ""
    previous_role = ""
    previous_text = ""
    for role, text in role_texts:
        current_text = strip_repeated_champion_from_skin(text, previous_text) if role == "skin" else text
        if role in {"companion", "spell_context"}:
            # 同行英雄和技能对象／条件统一用括号后缀，保留触发语境。
            current_text = f"（{current_text}）"
        if not output:
            output = current_text
        elif previous_role in CONTEXT_ROLES or role in CONTEXT_ROLES:
            output = f"{output} {current_text}"
        else:
            output = f"{output}{current_text}"
        previous_role = role
        previous_text = current_text

    return output


def join_mode_unit_attack_zh_cn(role_texts: list[tuple[str, str]]) -> str:
    """合并模式单位攻击文案，显式分隔上下文、单位与攻击动作。"""

    role_values = {role: text for role, text in role_texts}
    context = role_values.get("context")
    entity = role_values.get("entity")
    attack = role_values.get("attack", "")
    phase = role_values.get("phase", "")
    core = f"{attack}{phase}".strip()
    return " ".join(part for part in (context, entity, core) if part)


def join_skin_idle_zh_cn(role_texts: list[tuple[str, str]]) -> str:
    """合并皮肤待机 frame 的中文文案。"""

    context_text = join_zh_cn([(role, text) for role, text in role_texts if role in CONTEXT_ROLES])
    role_values = {role: text for role, text in role_texts}

    variant = role_values.get("variant")
    if variant:
        core_text = variant
    else:
        subject_text = join_zh_cn(
            [(role, text) for role, text in role_texts if role in {"ability_slot", "ability_component"}]
        )
        phase_text = role_values.get("phase", "")
        if subject_text:
            core_text = f"{subject_text}待机{phase_text}"
        else:
            core_text = f"待机{phase_text}" if phase_text else "待机"

    # 专用合并逻辑同样不能丢 playback 壳语义。
    playback_text = role_values.get("playback", "")
    if playback_text:
        core_text = f"{core_text}{playback_text}"

    if context_text and core_text:
        return f"{context_text} {core_text}"

    return core_text or context_text


def join_kill_or_destroy_zh_cn(
    role_texts: list[tuple[str, str]],
    arguments: dict[str, Any],
    mode: str,
) -> str:
    """合并击杀/摧毁 frame 的中文文案（T3.2 措辞表）。

    kill + cp 中缀「击杀 {cp} 语音」；milestone/kill_method/objective_steal
    存在时 cp 后置全角括号；kill_method 只在 default/debug/audit 前置
    （QWER 归一键紧贴「R技能击杀语音」，归一失败 raw 空格直出
    「REmpowered 击杀语音」；main 短文案保持零拉丁字母，棘轮口径）；
    counter 提得出数字时渲染尾缀「（变体N）」，否则忽略进 debug。

    Args:
        role_texts: `(role, text)` 列表。
        arguments: translation arguments 字典。
        mode: 渲染模式。

    Returns:
        中文文案。
    """

    values = {role: text for role, text in role_texts}
    context_text = join_zh_cn([(role, text) for role, text in role_texts if role in CONTEXT_ROLES])
    core = values.get("trigger", "")

    kill_method_text = values.get("kill_method", "")
    if kill_method_text and mode != "main":
        separator = "" if has_cjk_text(kill_method_text) else " "
        core = f"{kill_method_text}{separator}{core}".strip()

    counterpart_text = values.get("counterpart", "")
    if counterpart_visible(counterpart_text, mode):
        postfix_roles = ("milestone", "kill_method", "objective_steal")
        if any(isinstance(arguments.get(role), dict) for role in postfix_roles):
            core = f"{core}（{counterpart_text}）"
        else:
            core = insert_before_asset_word(core, counterpart_text)

    counter = arguments.get("counter")
    counter_number = string_or_none(counter.get("value")) if isinstance(counter, dict) else None
    if counter_number is not None:
        core = f"{core}（变体{counter_number}）"
    elif mode in {"debug", "audit"} and values.get("counter"):
        core = f"{core}（{values['counter']}）"

    companion_text = values.get("companion", "")
    if companion_text:
        core = f"{core}（{companion_text}）"

    core = f"{core}{values.get('playback', '')}"
    if context_text and core:
        return f"{context_text} {core}"

    return core or context_text


def join_interaction_cue_zh_cn(
    role_texts: list[tuple[str, str]],
    arguments: dict[str, Any],
    mode: str,
) -> str:
    """合并英雄交互 frame 的中文文案（T3.2 措辞表）。

    非 move 家族 cp 中缀「初遇 {cp} 语音」；move 家族用全角括号措辞
    「{timing}移动语音（{relation} {cp} 在场）」（timing 已由 builder 拼进
    trigger 短语）。英文回退 cp 只进 default/debug/audit。

    Args:
        role_texts: `(role, text)` 列表。
        arguments: translation arguments 字典。
        mode: 渲染模式。

    Returns:
        中文文案。
    """

    values = {role: text for role, text in role_texts}
    context_text = join_zh_cn([(role, text) for role, text in role_texts if role in CONTEXT_ROLES])
    core = values.get("trigger", "")

    counterpart_text = values.get("counterpart", "")
    if counterpart_visible(counterpart_text, mode):
        trigger = arguments.get("trigger")
        family = string_or_none(trigger.get("key")) if isinstance(trigger, dict) else None
        if family == "move":
            parts = [part for part in (values.get("relation", ""), counterpart_text) if part]
            core = f"{core}（{' '.join(parts)} 在场）"
        else:
            core = insert_before_asset_word(core, counterpart_text)

    companion_text = values.get("companion", "")
    if companion_text:
        core = f"{core}（{companion_text}）"

    core = f"{core}{values.get('playback', '')}"
    if context_text and core:
        return f"{context_text} {core}"

    return core or context_text


def join_dialogue_event_zh_cn(role_texts: list[tuple[str, str]], mode: str) -> str:
    """合并英雄对话链 frame 的中文文案（批次4 措辞表）。

    核心「与 {partner} 对话语音」；修饰用全角括号「（{family词} 第{turn}句）」，
    括号内词缺一半时只保留另一半，全缺则无括号。含拉丁字母的 partner
    fallback 只进 default/debug/audit（counterpart 同口径），main 短文案
    核心退化为「对话语音」保持零拉丁字母（residual_english 棘轮口径，
    argument 数据不受影响）。

    Args:
        role_texts: `(role, text)` 列表。
        mode: 渲染模式。

    Returns:
        中文文案。
    """

    values = {role: text for role, text in role_texts}
    context_text = join_zh_cn([(role, text) for role, text in role_texts if role in CONTEXT_ROLES])

    partner_text = values.get("partner", "")
    if counterpart_visible(partner_text, mode):
        core = f"与 {partner_text} 对话语音"
    else:
        core = "对话语音"

    bracket_parts = [part for part in (values.get("base_trigger", ""), values.get("turn", "")) if part]
    if bracket_parts:
        core = f"{core}（{' '.join(bracket_parts)}）"

    core = f"{core}{values.get('playback', '')}"
    if context_text and core:
        return f"{context_text} {core}"

    return core or context_text


def join_shop_voice_zh_cn(role_texts: list[tuple[str, str]]) -> str:
    """合并商店老板语音 frame 的中文文案（批次6d 措辞表）。

    核心「商店老板{action}语音」；subject 用全角括号后缀（「商店老板购买
    语音（振奋盔甲）」）；mode（极地大乱斗）作前缀上下文空格分隔，main
    模式模板已不含 mode。店主内部代号不进文案（无官方中文名）。

    Args:
        role_texts: ``(role, text)`` 列表。

    Returns:
        中文文案。
    """

    values = {role: text for role, text in role_texts}
    core = f"商店老板{values.get('shop_action', '')}语音"

    subject_text = values.get("shop_subject", "")
    if subject_text:
        core = f"{core}（{subject_text}）"

    core = f"{core}{values.get('playback', '')}"
    mode_text = values.get("mode", "")
    if mode_text:
        return f"{mode_text} {core}"

    return core


def join_announcer_event_zh_cn(role_texts: list[tuple[str, str]]) -> str:
    """合并播报员事件 frame 的中文文案（批次5a/6e 措辞表）。

    核心「{subject}{team}{event}播报」，subject/team 缺省为空串直连无空格
    （「敌方团灭播报」「你接近暴走播报」「友方英雄超神播报」）；subject 在
    场时 builder 不出 team（队伍已并入主体词），两者不会同现。各角色均经
    词表门禁产出纯中文，main 零拉丁字母自然满足；playback 壳语义不得丢。

    Args:
        role_texts: `(role, text)` 列表。

    Returns:
        中文文案。
    """

    values = {role: text for role, text in role_texts}
    core = f"{values.get('subject', '')}{values.get('team', '')}{values.get('event', '')}播报"
    core = f"{core}{values.get('playback', '')}"
    mode_text = values.get("mode", "")
    if mode_text:
        return f"{mode_text} {core}"

    return core


def has_cjk_text(text: str) -> bool:
    """判断文本是否含 CJK 字符。

    Args:
        text: 待判断文本。

    Returns:
        含中文字符时返回 True。
    """

    return any("一" <= char <= "鿿" for char in text)


def counterpart_visible(text: str, mode: str) -> bool:
    """判断 counterpart 文案是否进入当前模式。

    纯中文 cp 任何模式都不省略（北极星：联动是特殊情况）；含拉丁字母的
    cp（英文回退、DJ 娑娜/T1 杰斯这类官方混排名）保留在 default/debug/
    audit 详细文案，不进 main 短文案（residual_english 棘轮口径，
    argument 数据不受影响）。

    Args:
        text: counterpart 文案。
        mode: 渲染模式。

    Returns:
        应渲染时返回 True。
    """

    if not text:
        return False

    return mode != "main" or not has_latin_text(text)


def has_latin_text(text: str) -> bool:
    """判断文本是否含 ASCII 拉丁字母（residual_english 棘轮同口径）。

    Args:
        text: 待判断文本。

    Returns:
        含拉丁字母时返回 True。
    """

    return any("A" <= char <= "Z" or "a" <= char <= "z" for char in text)


def insert_before_asset_word(base: str, insert: str) -> str:
    """把 counterpart 中缀进核心短语的亚词（语音/音效/音乐）之前。

    Args:
        base: 核心短语（如「击杀语音」）。
        insert: counterpart 文案。

    Returns:
        中缀后的短语（如「击杀 奥恩 语音」）；无亚词后缀时空格追加兜底。
    """

    for word in ASSET_WORD_SUFFIXES:
        if base.endswith(word):
            return f"{base[: -len(word)]} {insert} {word}"

    return f"{base} {insert}".strip()


def strip_repeated_champion_from_skin(skin_text: str, champion_text: str) -> str:
    """去掉官方皮肤 label 中重复的英雄所有者名。

    Args:
        skin_text: 皮肤 label。
        champion_text: 前一个 champion label。

    Returns:
        去重后的皮肤 label。
    """

    if not champion_text:
        return skin_text

    segments = skin_text.split(" ")

    # 官方皮肤名可能把“·”写成独立空格段（奥瑞利安 · 索尔），比较时去空格按整名对齐。
    normalized_champion = champion_text.replace(" ", "")
    if skin_text.replace(" ", "") == normalized_champion:
        return skin_text
    for start in range(1, len(segments)):
        remainder = " ".join(segments[:start])
        if remainder.endswith("·"):
            # 剩余文本以“·”收尾说明匹配从整名中间切开，跳过避免留下悬挂间隔号。
            continue

        if "".join(segments[start:]) == normalized_champion:
            return remainder

    # 末段内嵌英雄名变体（如 魔龙布兰德 ≠ 布兰德）时去掉整个末段。
    last_segment = segments[-1]
    remainder = " ".join(segments[:-1])
    if last_segment != champion_text and last_segment.endswith(champion_text) and not remainder.endswith("·"):
        return remainder or skin_text

    # 有些官方 label 把英雄名放在前缀或中间（如「图奇 · 影足」和
    # 「K/DA ALL OUT 萨勒芬妮 独立音乐人」），也有使用正式全名首段/常用短名的情况
    # （「舰队司令 烈娜塔」「律政大亨 蒙多」）。这里只接受完整名称、间隔号全名，
    # 或 skin label 中与正式名称开头一致的独立词，避免任意子串剥离。
    candidates = [champion_text]
    compact_champion = re.sub(r"\s+", "", champion_text)
    champion_parts = [part.strip() for part in re.split(r"\s*·\s*", champion_text) if part.strip()]
    if len(champion_parts) > 1:
        candidates.append(champion_parts[0])
    for token in re.findall(r"[A-Za-z0-9\u3400-\u9fff]+", skin_text):
        if 2 <= len(token) < len(compact_champion) and compact_champion.startswith(token):
            candidates.append(token)

    full_name_pattern = None
    if len(champion_parts) > 1:
        full_name_pattern = r"\s*·\s*".join(re.escape(part) for part in champion_parts)

    patterns = [full_name_pattern] if full_name_pattern is not None else []
    patterns.extend(re.escape(candidate) for candidate in dict.fromkeys(candidates) if candidate)
    for candidate_pattern in patterns:
        match = re.search(
            rf"(?<![A-Za-z0-9\u3400-\u9fff])(?:{candidate_pattern})(?![A-Za-z0-9\u3400-\u9fff])",
            skin_text,
        )
        if match is None:
            continue

        compact_match = re.sub(r"\s+", "", match.group(0))
        prefix = skin_text[: match.start()].rstrip()
        # 「索尔」这类两字简称不能从「奥瑞利安 · 索尔」末尾单独剥离。
        if len(compact_match) <= 2 and prefix.endswith("·") and compact_match == compact_champion:
            continue

        stripped = f"{skin_text[: match.start()]} {skin_text[match.end() :]}"
        stripped = " ".join(stripped.split())
        stripped = re.sub(r"^[·:：]\s*", "", stripped)
        stripped = re.sub(r"\s*·$", "", stripped).strip()
        if stripped:
            return stripped

    return skin_text


def build_compat_display(
    translation: dict[str, object] | None,
    *,
    legacy_display: dict[str, str] | None = None,
    raw_event_name: str = "",
) -> dict[str, str]:
    """从 translation 渲染兼容 display 字段，缺 translation 时保留 legacy fallback。

    Args:
        translation: translation frame 字典。
        legacy_display: 旧 display 字段。
        raw_event_name: 原始事件名。

    Returns:
        兼容 normalized display 字典。
    """

    if translation is None:
        return dict(legacy_display or {})

    short = render_translation(
        translation,
        RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin", "item"})),
    )
    detail = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
    debug_source = raw_event_name or string_or_none(translation.get("raw_event_name")) or ""
    legacy = legacy_display or {}
    return {
        "zh_cn_short": short.text or legacy.get("zh_cn_short", ""),
        "zh_cn_detail": detail.text or legacy.get("zh_cn_detail", ""),
        "debug_label": debug_source or legacy.get("debug_label", ""),
    }


def omitted_roles_for_rendering(
    frame_type: str,
    locale: str,
    known_context: frozenset[str],
    arguments: dict[object, object],
) -> tuple[str, ...]:
    """返回因上下文或 playback 控制语义省略的角色。

    始终按 default 模板的完整 role 集合报告（而非当前模式模板），调用方
    因此能在 main 等短模板下仍看到 champion/skin 被省略的事实。

    Args:
        frame_type: translation frame 类型。
        locale: 目标语言。
        known_context: 调用方已知上下文。
        arguments: translation arguments 字典。

    Returns:
        已省略角色。
    """

    default_roles = roles_for_template(frame_type, locale, "default")
    omitted = []
    for role in default_roles:
        argument = arguments.get(role)
        if not isinstance(argument, dict):
            continue
        if argument_omitted_for_context(argument, known_context) or argument_rendered_via_other_role(
            argument, arguments
        ):
            omitted.append(role)
    if "phase" in default_roles and is_phase_suppressed_by_playback(arguments):
        omitted.append("phase")
    return tuple(omitted)


def fallback_argument_text(argument: dict[str, object], fallback: str) -> str:
    """返回 argument fallback 文案。

    Args:
        argument: translation argument 字典。
        fallback: fallback 策略。

    Returns:
        fallback 文案。
    """

    for key in fallback_keys(fallback):
        value = argument.get(key)
        if isinstance(value, str) and value:
            return value

    return ""


def fallback_keys(fallback: str) -> tuple[str, ...]:
    """返回 fallback 策略对应字段顺序。

    Args:
        fallback: fallback 策略。

    Returns:
        字段名顺序。
    """

    if fallback == "token":
        return ("token", "key", "value", "alias")
    if fallback == "raw":
        return ("raw", "token", "key", "value", "alias")

    return ("token", "key", "value", "alias")


def strip_technical_user_tokens(text: str) -> str:
    """移除普通用户文案中的技术 token。

    Args:
        text: 原始文案。

    Returns:
        清理后的文案。
    """

    result = text
    for token in TECHNICAL_USER_TOKENS:
        result = result.replace(token, "")

    return " ".join(result.split())


def string_or_none(value: Any) -> str | None:
    """把非空字符串值安全转为 str。

    Args:
        value: 待读取值。

    Returns:
        非空字符串；否则返回 None。
    """

    if isinstance(value, str) and value:
        return value

    return None
