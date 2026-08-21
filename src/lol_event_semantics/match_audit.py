"""规则与参考匹配命中审计。"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from .rules import RuleBook

# 普攻改写白名单的规则来源文件；命中留痕与 zero-hit 枚举共用，保证 rule_id/source_path 对得上


@dataclass(slots=True)
class MatchAuditEvent:
    """单次 matcher 命中记录。"""

    matcher_kind: str
    rule_id: str
    source_path: str
    raw_event_name: str
    owner: str | None = None
    token: str | None = None


@dataclass(slots=True)
class MatchAuditRecorder:
    """收集分解过程中的 matcher 命中。"""

    events: list[MatchAuditEvent] = field(default_factory=list)

    def record(self, event: MatchAuditEvent) -> None:
        """记录一次命中。"""

        self.events.append(event)


_CURRENT_RECORDER: MatchAuditRecorder | None = None


@contextmanager
def audit_context(recorder: MatchAuditRecorder | None) -> Iterator[None]:
    """在当前调用栈中设置审计 recorder。"""

    global _CURRENT_RECORDER
    previous = _CURRENT_RECORDER
    _CURRENT_RECORDER = recorder

    try:
        yield
    finally:
        _CURRENT_RECORDER = previous


def current_recorder() -> MatchAuditRecorder | None:
    """返回当前审计 recorder。"""

    return _CURRENT_RECORDER


def record_match(event: MatchAuditEvent) -> None:
    """如果当前存在 recorder，则记录命中。"""

    recorder = current_recorder()

    if recorder is None:
        return

    recorder.record(event)


def summarize_rule_hits(events: list[MatchAuditEvent], max_examples: int = 5) -> list[dict[str, object]]:
    """按规则 ID 汇总命中次数。"""

    grouped: dict[tuple[str, str, str], list[MatchAuditEvent]] = defaultdict(list)

    for event in events:
        grouped[(event.matcher_kind, event.rule_id, event.source_path)].append(event)

    rows: list[dict[str, object]] = []

    for (matcher_kind, rule_id, source_path), hits in sorted(grouped.items()):
        examples = list(dict.fromkeys(hit.raw_event_name for hit in hits))[:max_examples]
        rows.append(
            {
                "matcher_kind": matcher_kind,
                "rule_id": rule_id,
                "source_path": source_path,
                "hit_count": len(hits),
                "examples": "; ".join(examples),
            }
        )

    return rows


def summarize_zero_hit_rules(
    rulebook: RuleBook | None,
    events: list[MatchAuditEvent],
) -> list[dict[str, str]]:
    """列出本次输入未命中的可枚举手写规则。"""

    if rulebook is None:
        return []

    hit_rule_ids = {event.rule_id for event in events}
    rows: list[dict[str, str]] = []

    for alias in rulebook.contextual_aliases.values():
        rule_id = f"contextual:{alias.scope}:{alias.owner}:{alias.token}"

        if rule_id in hit_rule_ids:
            continue

        rows.append(
            {
                "rule_id": rule_id,
                "source_path": alias.source_path,
                "scope": alias.scope,
                "owner": alias.owner,
                "token": alias.token,
                "reason_hint": "not matched by current input; not a deletion signal",
            }
        )

    for modifier in rulebook.residual_modifiers.values():
        rule_id = f"residual:{modifier.kind}:{modifier.key}"

        if rule_id in hit_rule_ids:
            continue

        rows.append(
            {
                "rule_id": rule_id,
                "source_path": modifier.source,
                "scope": "residual",
                "owner": "",
                "token": modifier.token,
                "reason_hint": "not matched by current input; not a deletion signal",
            }
        )

    return sorted(rows, key=lambda row: row["rule_id"])


def write_match_hit_summary(rows: list[dict[str, object]], output_path: Path) -> None:
    """写出规则命中汇总 CSV。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["matcher_kind", "rule_id", "source_path", "hit_count", "examples"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_match_hit_summary_json(rows: list[dict[str, object]], output_path: Path) -> None:
    """写出规则命中汇总 JSON。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_zero_hit_rules(rows: list[dict[str, str]], output_path: Path) -> None:
    """写出本次输入未命中的手写规则。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["rule_id", "source_path", "scope", "owner", "token", "reason_hint"],
        )
        writer.writeheader()
        writer.writerows(rows)


def write_match_audit_manifest(
    output_path: Path,
    *,
    input_scope: str,
    raw_count: int,
    reference_version: str,
    reference_root: Path | None,
    command: str,
) -> None:
    """写出规则审计 manifest。"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "input_scope": input_scope,
        "raw_count": raw_count,
        "reference_version": reference_version,
        "reference_root": reference_root.as_posix() if reference_root is not None else None,
        "command": command,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
