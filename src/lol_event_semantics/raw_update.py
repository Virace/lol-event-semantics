"""累计 raw/events 差集更新。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


SCHEMA_VERSION = 1
RAW_GROUPS = ("champions", "maps")


@dataclass(frozen=True, slots=True)
class RawUpdateMetadata:
    """本轮 raw 更新来源 metadata。"""

    game_version: str | None = None
    manifest_major_version: str | None = None
    game_manifest_id: str | None = None
    game_manifest_url: str | None = None
    data_dragon_version: str | None = None
    game_data_snapshot_metadata_sha256: str | None = None
    data_dragon_champion_summary_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class RawEntityDiff:
    """单个 raw 实体文件差集。"""

    group: str
    name: str
    existing_events: int
    incoming_events: int
    added_events: int


@dataclass(frozen=True, slots=True)
class RawUpdateResult:
    """raw 更新结果摘要。"""

    changed: bool
    should_update: bool
    metadata_changed: bool
    existing_events: int
    incoming_events: int
    added_events: int
    entities: tuple[RawEntityDiff, ...]

    def to_dict(self) -> dict[str, object]:
        """返回 JSON 友好的结果。"""

        return {
            "changed": self.changed,
            "should_update": self.should_update,
            "metadata_changed": self.metadata_changed,
            "existing_events": self.existing_events,
            "incoming_events": self.incoming_events,
            "added_events": self.added_events,
            "entities": [asdict(item) for item in self.entities],
        }


def check_raw_update(
    raw_root: Path | str,
    input_root: Path | str,
    *,
    state_file: Path | str | None = None,
    metadata: RawUpdateMetadata | None = None,
) -> RawUpdateResult:
    """只检查本轮输入相对累计 raw 的差集。"""

    current = read_raw_tree(Path(raw_root))
    incoming = read_raw_tree(Path(input_root))
    result = diff_raw_trees(current, incoming)
    metadata_changed = has_metadata_changed(Path(state_file), metadata) if state_file is not None and metadata is not None else False
    return with_update_flags(result, metadata_changed=metadata_changed)


def update_raw_events(
    raw_root: Path | str,
    input_root: Path | str,
    *,
    state_file: Path | str | None = None,
    metadata: RawUpdateMetadata | None = None,
) -> RawUpdateResult:
    """把本轮事件名差集合并进累计 raw/events。"""

    target = Path(raw_root)
    current = read_raw_tree(target)
    incoming = read_raw_tree(Path(input_root))
    result = diff_raw_trees(current, incoming)

    if result.changed:
        merged = merge_raw_trees(current, incoming)
        write_raw_tree(target, merged)

    if state_file is not None:
        write_update_state(Path(state_file), target, metadata or RawUpdateMetadata())

    return result


def diff_raw_trees(
    current: dict[tuple[str, str], set[str]],
    incoming: dict[tuple[str, str], set[str]],
) -> RawUpdateResult:
    """计算两个 raw tree 的差集。"""

    diffs: list[RawEntityDiff] = []
    existing_total = sum(len(values) for values in current.values())
    incoming_total = sum(len(values) for values in incoming.values())
    added_total = 0

    for key in sorted(incoming):
        existing = current.get(key, set())
        new_values = incoming[key] - existing

        if not new_values:
            continue

        group, name = key
        added = len(new_values)
        added_total += added
        diffs.append(
            RawEntityDiff(
                group=group,
                name=name,
                existing_events=len(existing),
                incoming_events=len(incoming[key]),
                added_events=added,
            )
        )

    return RawUpdateResult(
        changed=added_total > 0,
        should_update=added_total > 0,
        metadata_changed=False,
        existing_events=existing_total,
        incoming_events=incoming_total,
        added_events=added_total,
        entities=tuple(diffs),
    )


def with_update_flags(result: RawUpdateResult, *, metadata_changed: bool) -> RawUpdateResult:
    """返回带 metadata 变化判断的结果。"""

    return RawUpdateResult(
        changed=result.changed,
        should_update=result.changed or metadata_changed,
        metadata_changed=metadata_changed,
        existing_events=result.existing_events,
        incoming_events=result.incoming_events,
        added_events=result.added_events,
        entities=result.entities,
    )


def has_metadata_changed(state_file: Path, metadata: RawUpdateMetadata) -> bool:
    """判断本轮来源 metadata 是否不同于上次检查。"""

    if not state_file.is_file():
        return True

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    previous = payload.get("last_checked") if isinstance(payload, dict) else None
    return previous != asdict(metadata)


def merge_raw_trees(
    current: dict[tuple[str, str], set[str]],
    incoming: dict[tuple[str, str], set[str]],
) -> dict[tuple[str, str], set[str]]:
    """合并累计 raw tree 和本轮输入。"""

    merged = {key: set(values) for key, values in current.items()}

    for key, values in incoming.items():
        merged.setdefault(key, set()).update(values)

    return merged


def read_raw_tree(root: Path) -> dict[tuple[str, str], set[str]]:
    """读取 raw/events 树。"""

    if not root.is_dir():
        return {}

    tree: dict[tuple[str, str], set[str]] = {}
    for group in RAW_GROUPS:
        group_root = root / group

        if not group_root.is_dir():
            continue

        for path in sorted(group_root.glob("*.txt")):
            events = read_event_file(path)
            if events:
                tree[(group, path.stem)] = events

    return tree


def read_event_file(path: Path) -> set[str]:
    """读取单个事件名文本文件。"""

    events: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        raw_event_name = line.strip()

        if raw_event_name:
            events.add(raw_event_name)

    return events


def write_raw_tree(root: Path, tree: dict[tuple[str, str], set[str]]) -> None:
    """稳定写出累计 raw/events 树。"""

    for group in RAW_GROUPS:
        group_root = root / group
        group_root.mkdir(parents=True, exist_ok=True)
        expected = {f"{name}.txt" for current_group, name in tree if current_group == group}

        for path in sorted(group_root.glob("*.txt")):
            if path.name not in expected:
                path.unlink()

    for (group, name), events in sorted(tree.items()):
        write_event_file(root / group / f"{name}.txt", events)


def write_event_file(path: Path, events: set[str]) -> None:
    """稳定写出单个事件名文本文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(sorted(events))

    if text:
        text += "\n"

    path.write_text(text, encoding="utf-8")


def write_update_state(path: Path, raw_root: Path, metadata: RawUpdateMetadata) -> None:
    """写出 raw/update-state.json。"""

    tree = read_raw_tree(raw_root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "updated_at": current_timestamp(),
        "last_checked": asdict(metadata),
        "totals": raw_totals(tree),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def raw_totals(tree: dict[tuple[str, str], set[str]]) -> dict[str, int]:
    """统计累计 raw tree。"""

    return {
        "champion_files": sum(1 for group, _name in tree if group == "champions"),
        "map_files": sum(1 for group, _name in tree if group == "maps"),
        "raw_event_names": sum(len(values) for values in tree.values()),
    }


def current_timestamp() -> str:
    """返回 UTC 时间戳。"""

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
