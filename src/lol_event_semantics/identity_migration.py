"""保留审计快照身份的跨数据集连续性比较。"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .normalized_contract import event_id_for_normalized_record, source_identity_from_paths
from .normalized_lookup import iter_normalized_rows


@dataclass(frozen=True)
class IdentityMatch:
    """有明确依据的一对一记录关系。"""

    previous_id: str
    current_id: str
    basis: str


def load_identity_rows(root: Path) -> dict[str, dict[str, Any]]:
    """加载比较所需字段，拒绝重复身份并避免保留大型渲染负载。"""

    rows: dict[str, dict[str, Any]] = {}
    if not (root / "normalized-events.jsonl").is_file() and not (root / "by-entity").is_dir():
        raise ValueError(f"normalized stream or by-entity directory not found: {root}")
    fields = ("record_id", "raw_event_name", "entity", "source_paths", "support_level", "pattern_id")
    for _path, _line, row in iter_normalized_rows(root):
        record_id = row.get("record_id")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("identity comparison requires a non-empty record_id")
        if record_id in rows:
            raise ValueError(f"duplicate record_id in identity comparison: {record_id}")
        rows[record_id] = {field: row[field] for field in fields if field in row}
    return rows


def match_identities(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
) -> tuple[list[IdentityMatch], list[dict[str, Any]]]:
    """按快照、连续身份、来源交集依次匹配，冲突关系保持未匹配。

    来源迁移只接受同 raw、有 canonical asset 交集、双方均唯一的关系。
    未携带完整身份的旧行只允许原 record_id 精确匹配。
    """

    matches = [IdentityMatch(key, key, "record_id") for key in sorted(previous.keys() & current.keys())]
    remaining_old = set(previous).difference(match.previous_id for match in matches)
    remaining_new = set(current).difference(match.current_id for match in matches)
    old_events = _event_groups(previous, remaining_old)
    new_events = _event_groups(current, remaining_new)
    all_old_events = _event_groups(previous, previous)
    all_new_events = _event_groups(current, current)
    ambiguous: list[dict[str, Any]] = []
    blocked_old: set[str] = set()
    blocked_new: set[str] = set()
    for event_id in sorted(old_events.keys() & new_events.keys()):
        old_ids, new_ids = old_events[event_id], new_events[event_id]
        if len(all_old_events[event_id]) == len(all_new_events[event_id]) == 1:
            matches.append(IdentityMatch(old_ids[0], new_ids[0], "event_id"))
            remaining_old.remove(old_ids[0])
            remaining_new.remove(new_ids[0])
        else:
            blocked_old.update(old_ids)
            blocked_new.update(new_ids)
            ambiguous.append(
                {"basis": "event_id", "event_id": event_id, "previous_ids": old_ids, "current_ids": new_ids}
            )

    old_raw = _raw_groups(previous, remaining_old - blocked_old)
    new_raw = _raw_groups(current, remaining_new - blocked_new)
    for raw in sorted(old_raw.keys() & new_raw.keys()):
        edges: dict[str, list[str]] = {}
        reverse: dict[str, list[str]] = defaultdict(list)
        for old_id in old_raw[raw]:
            assets = _assets(previous[old_id])
            candidates = [new_id for new_id in new_raw[raw] if assets.intersection(_assets(current[new_id]))]
            edges[old_id] = candidates
            for new_id in candidates:
                reverse[new_id].append(old_id)
        for old_id, new_ids in edges.items():
            if len(new_ids) == 1 and len(reverse[new_ids[0]]) == 1:
                matches.append(IdentityMatch(old_id, new_ids[0], "raw_and_source_overlap"))
            elif new_ids:
                ambiguous.append(
                    {
                        "basis": "raw_and_source_overlap",
                        "raw_event_name": raw,
                        "previous_ids": [old_id],
                        "current_ids": new_ids,
                    }
                )
    return sorted(matches, key=lambda match: (match.previous_id, match.current_id)), ambiguous


def build_identity_report(
    previous: Mapping[str, Mapping[str, Any]],
    current: Mapping[str, Mapping[str, Any]],
    *,
    comparison: tuple[list[IdentityMatch], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """输出变化关系、未匹配身份和歧义；不授予任何质量或发布批准。"""

    matches, ambiguous = comparison if comparison is not None else match_identities(previous, current)
    changes: list[dict[str, Any]] = []
    for match in matches:
        old, new = previous[match.previous_id], current[match.current_id]
        owner_changed = _owner(old) != _owner(new)
        provenance_changed = _provenance(old) != _provenance(new)
        support_changed = old.get("support_level") != new.get("support_level")
        pattern_changed = old.get("pattern_id") != new.get("pattern_id")
        if not (
            match.previous_id != match.current_id
            or owner_changed
            or provenance_changed
            or support_changed
            or pattern_changed
        ):
            continue
        changes.append(
            {
                "previous_record_id": match.previous_id,
                "current_record_id": match.current_id,
                "previous_event_id": _event_id(old),
                "current_event_id": _event_id(new),
                "raw_event_name": new.get("raw_event_name"),
                "basis": match.basis,
                "owner_changed": owner_changed,
                "previous_owner": list(_owner(old)),
                "current_owner": list(_owner(new)),
                "provenance_changed": provenance_changed,
                "previous_source_assets": sorted(_assets(old)),
                "current_source_assets": sorted(_assets(new)),
                "previous_source_paths": list(_provenance(old)),
                "current_source_paths": list(_provenance(new)),
                "support_changed": support_changed,
                "previous_support_level": old.get("support_level"),
                "current_support_level": new.get("support_level"),
                "pattern_changed": pattern_changed,
                "previous_pattern_id": old.get("pattern_id"),
                "current_pattern_id": new.get("pattern_id"),
            }
        )
    return {
        "schema_version": 1,
        "identity_policy": "raw-owner-with-evidenced-migration.v1",
        "matched_count": len(matches),
        "unchanged_count": len(matches) - len(changes),
        "match_basis_counts": dict(sorted(Counter(match.basis for match in matches).items())),
        "changes": changes,
        "removed_record_ids": sorted(set(previous).difference(match.previous_id for match in matches)),
        "added_record_ids": sorted(set(current).difference(match.current_id for match in matches)),
        "ambiguous": ambiguous,
    }


def _event_id(row: Mapping[str, Any]) -> str | None:
    """从事实字段派生连续键，不能信任输入中声明的 event_id。"""

    try:
        return event_id_for_normalized_record(row)
    except ValueError:
        return None


def _event_groups(rows: Mapping[str, Mapping[str, Any]], ids: Iterable[str]) -> dict[str, list[str]]:
    """按完整连续身份分组。"""

    groups: dict[str, list[str]] = defaultdict(list)
    for record_id in sorted(ids):
        event_id = _event_id(rows[record_id])
        if event_id is not None:
            groups[event_id].append(record_id)
    return groups


def _raw_groups(rows: Mapping[str, Mapping[str, Any]], ids: Iterable[str]) -> dict[str, list[str]]:
    """只把有完整逻辑身份的记录用于来源迁移。"""

    groups: dict[str, list[str]] = defaultdict(list)
    for record_id in sorted(ids):
        if _event_id(rows[record_id]) is not None:
            groups[str(rows[record_id]["raw_event_name"])].append(record_id)
    return groups


def _owner(row: Mapping[str, Any]) -> tuple[str, str]:
    """提取参与连续身份计算的 owner。"""

    entity = row.get("entity")
    return (str(entity.get("kind") or ""), str(entity.get("alias") or "")) if isinstance(entity, dict) else ("", "")


def _provenance(row: Mapping[str, Any]) -> tuple[str, ...]:
    """规范化来源路径，排除宿主绝对路径差异。"""

    paths = row.get("source_paths")
    return source_identity_from_paths(paths if isinstance(paths, list) else []).provenance_paths


def _assets(row: Mapping[str, Any]) -> set[str]:
    """提取可用于迁移佐证的资产身份，未知路径不充当证据。"""

    paths = row.get("source_paths")
    return set(source_identity_from_paths(paths if isinstance(paths, list) else []).canonical_assets)
