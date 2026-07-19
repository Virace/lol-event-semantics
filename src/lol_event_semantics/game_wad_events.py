"""GAME WAD/BIN 事件抽取的 round 输入接口。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .raw_events import collect_events


ROUND_GROUPS = ("champions", "maps")


@dataclass(frozen=True, slots=True)
class ExtractionWarning:
    """事件抽取 warning。"""

    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class RoundExtractionResult:
    """round 输入抽取结果。"""

    files: int
    events: int
    warnings: tuple[ExtractionWarning, ...]


type WadFactory = Callable[[Path], Any]
type BinFactory = Callable[[bytes], Any]


def extract_round_from_event_files(
    input_root: Path | str,
    output_root: Path | str,
    *,
    expected_chroma_sources: tuple[str, ...] = (),
) -> RoundExtractionResult:
    """把本地已抽取事件 payload 标准化为 raw round txt。"""

    source_root = Path(input_root)
    target_root = Path(output_root)
    warnings = missing_chroma_warnings(source_root, expected_chroma_sources)
    written_files = 0
    written_events = 0

    for group in ROUND_GROUPS:
        group_root = source_root / group

        if not group_root.is_dir():
            continue

        for path in sorted(group_root.glob("*.json")):
            events = sorted(set(collect_events(load_json(path))))

            if not events:
                continue

            write_round_file(target_root / group / f"{path.stem}.txt", events)
            written_files += 1
            written_events += len(events)

    return RoundExtractionResult(
        files=written_files,
        events=written_events,
        warnings=tuple(warnings),
    )


def extract_round_from_wads(
    *,
    output_root: Path | str,
    requests: dict[tuple[str, str], list[str]],
    wad_paths: list[Path],
    wad_factory: WadFactory | None = None,
    bin_factory: BinFactory | None = None,
) -> RoundExtractionResult:
    """从本地 WAD 中抽取 BIN 事件并写为 round txt。"""

    target_root = Path(output_root)
    create_wad = wad_factory or default_wad_factory
    create_bin = bin_factory or default_bin_factory
    events_by_entity: dict[tuple[str, str], set[str]] = {}
    warnings: list[ExtractionWarning] = []

    for key, bin_paths in sorted(requests.items()):
        group, entity = key

        if group not in ROUND_GROUPS:
            continue

        for bin_path in bin_paths:
            data = extract_first_wad_bytes(wad_paths, bin_path, create_wad)

            if data is None:
                warning_kind = "missing_chroma" if "chroma" in bin_path.lower() else "missing_bin"
                warnings.append(ExtractionWarning(kind=warning_kind, path=bin_path))
                continue

            events_by_entity.setdefault((group, entity), set()).update(collect_bin_events(create_bin(data)))

    written_files = 0
    written_events = 0
    for (group, entity), events in sorted(events_by_entity.items()):
        if not events:
            continue

        write_round_file(target_root / group / f"{entity}.txt", sorted(events))
        written_files += 1
        written_events += len(events)

    return RoundExtractionResult(
        files=written_files,
        events=written_events,
        warnings=tuple(warnings),
    )


def extract_first_wad_bytes(wad_paths: list[Path], relative_path: str, wad_factory: WadFactory) -> bytes | None:
    """从多个 WAD 中抽取第一个命中的相对路径。"""

    for wad_path in wad_paths:
        if not Path(wad_path).is_file():
            continue

        try:
            results = wad_factory(Path(wad_path)).extract([relative_path], raw=True)
        except (KeyError, FileNotFoundError, ValueError):
            continue

        for result in results:
            if isinstance(result, bytes):
                return result

            if hasattr(result, "read"):
                content = result.read()

                if isinstance(content, bytes):
                    return content

    return None


def collect_bin_events(bin_object: Any) -> list[str]:
    """从 league_tools BIN 解析结果收集事件名。"""

    events: list[str] = []
    for audio_group in getattr(bin_object, "data", []) or []:
        for bank_unit in get_member(audio_group, "bank_units") or []:
            for event_data in get_member(bank_unit, "events") or []:
                raw = get_member(event_data, "string")

                if isinstance(raw, str) and raw.strip():
                    events.append(raw.strip())

            raw = get_member(bank_unit, "string")

            if isinstance(raw, str) and raw.strip():
                events.append(raw.strip())

    return sorted(set(events))


def get_member(value: Any, key: str) -> Any:
    """兼容对象属性和 dict 字段读取。"""

    if isinstance(value, dict):
        return value.get(key)

    return getattr(value, key, None)


def default_wad_factory(path: Path) -> Any:
    """创建默认 WAD 解析器。"""

    from league_tools import WAD

    return WAD(path)


def default_bin_factory(data: bytes) -> Any:
    """创建默认 BIN 解析器。"""

    from league_tools.formats import BIN

    return BIN(data)


def missing_chroma_warnings(root: Path, expected_paths: tuple[str, ...]) -> list[ExtractionWarning]:
    """生成缺失 chroma 输入 warning。"""

    warnings: list[ExtractionWarning] = []
    for relative_path in expected_paths:
        if not (root / relative_path).is_file():
            warnings.append(ExtractionWarning(kind="missing_chroma", path=relative_path))

    return warnings


def load_json(path: Path) -> Any:
    """读取本地事件 JSON payload。"""

    return json.loads(path.read_text(encoding="utf-8"))


def write_round_file(path: Path, events: list[str]) -> None:
    """写出标准 round txt 文件。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(events) + "\n", encoding="utf-8")
