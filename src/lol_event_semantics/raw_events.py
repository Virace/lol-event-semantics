"""导出原始事件名文本快照。"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgpack

SUPPORTED_SUFFIXES = {".json", ".msgpack", ".yaml", ".yml"}
EXCLUDED_EVENT_PATHS = {("maps", "22")}


@dataclass(slots=True)
class ExportArgs:
    """原始事件导出参数。"""

    source_root: Path
    version: str
    output_root: Path


@dataclass(slots=True)
class ExportResult:
    """单文件导出结果。"""

    source_path: Path
    output_path: Path
    count: int


def main(argv: list[str] | None = None) -> int:
    """运行原始事件导出。

    Args:
        argv: 可选命令行参数列表。省略时直接读取进程参数。

    Returns:
        int: 退出码。成功时返回 `0`。

    Raises:
        FileNotFoundError: 当输入目录不存在或没有可处理文件时抛出。
        ModuleNotFoundError: 当命中 YAML 文件但环境缺少 `PyYAML` 时抛出。
        ValueError: 当事件文件解码后不是预期的 `dict` 结构时抛出。
    """

    args = parse_args(argv)
    source_dir = build_source_dir(args)
    paths = list_source_files(source_dir)

    if not paths:
        raise FileNotFoundError(f"No supported event files were found under: {source_dir}")

    exports = [export_file(path, source_dir, args.output_root, args.version) for path in paths]
    total_events = sum(item.count for item in exports)
    output_dir = args.output_root / args.version / "events"

    print(f"source: {source_dir}")
    print(f"output: {output_dir}")
    print(f"files: {len(exports)}")
    print(f"events: {total_events}")

    return 0


def parse_args(argv: list[str] | None = None) -> ExportArgs:
    """解析命令行参数。

    Args:
        argv: 可选命令行参数列表。省略时直接读取进程参数。

    Returns:
        ExportArgs: 解析后的导出参数对象。
    """

    parser = argparse.ArgumentParser(
        description="Read manifest/<version>/events from an external output root and export raw text files."
    )
    parser.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="External tool output root, for example <external-output-root>",
    )
    parser.add_argument("--version", required=True, help="Game version, for example 16.5")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=default_output_root(),
        help="Raw text output root, defaults to the repository raw directory",
    )
    namespace = parser.parse_args(argv)

    return ExportArgs(
        source_root=namespace.source_root,
        version=namespace.version,
        output_root=namespace.output_root,
    )


def default_output_root() -> Path:
    """返回默认输出目录。

    Returns:
        Path: 仓库内的 `raw` 根目录。
    """

    return Path(__file__).resolve().parents[2] / "raw"


def build_source_dir(args: ExportArgs) -> Path:
    """拼出输入事件目录。

    Args:
        args: 导出参数对象。

    Returns:
        Path: `manifest/<version>/events` 对应的目录路径。

    Raises:
        FileNotFoundError: 当事件目录不存在时抛出。
    """

    source_dir = args.source_root / "manifest" / args.version / "events"

    if not source_dir.is_dir():
        raise FileNotFoundError(f"Events directory does not exist: {source_dir}")

    return source_dir


def list_source_files(source_dir: Path) -> list[Path]:
    """列出所有支持的输入文件。

    Args:
        source_dir: `events` 根目录。

    Returns:
        list[Path]: 已按路径排序的输入文件列表。
    """

    return sorted(
        path
        for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES and not is_excluded_source_path(path, source_dir)
    )


def export_file(source_path: Path, source_dir: Path, output_root: Path, version: str) -> ExportResult:
    """导出单个事件文件。

    Args:
        source_path: 当前正在处理的源文件路径。
        source_dir: `events` 根目录。
        output_root: 输出根目录。
        version: 当前导出的版本号。

    Returns:
        ExportResult: 单文件导出结果。
    """

    data = load_data(source_path)

    # 只在命中 `events` 节点时收集字符串，避免 alias、metadata 等字段误入输出。
    events = collect_events(data)
    output_path = build_output_path(source_path, source_dir, output_root, version)

    write_events(output_path, events)

    return ExportResult(source_path=source_path, output_path=output_path, count=len(events))


def is_excluded_source_path(path: Path, source_dir: Path) -> bool:
    """判断源文件是否属于仓库不再处理的模式范围。"""

    relative = path.relative_to(source_dir)
    path_key = tuple(part.lower() for part in relative.with_suffix("").parts)
    return path_key in EXCLUDED_EVENT_PATHS


def load_data(path: Path) -> dict[str, Any]:
    """按扩展名加载统一结构的事件数据。

    Args:
        path: 源文件路径。

    Returns:
        dict[str, Any]: 解码后的事件数据。

    Raises:
        ModuleNotFoundError: 当命中 YAML 文件但环境缺少 `PyYAML` 时抛出。
        ValueError: 当文件内容解码后不是 `dict` 时抛出。
    """

    suffix = path.suffix.lower()

    # 三种输入最终都要回到同一 `dict` 结构，后续逻辑才可以保持一致。
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
    elif suffix == ".msgpack":
        data = msgpack.unpackb(path.read_bytes(), raw=False)
    else:
        data = load_yaml_data(path)

    if not isinstance(data, dict):
        raise ValueError(f"Event file did not decode to a dict: {path}")

    return data


def load_yaml_data(path: Path) -> dict[str, Any]:
    """加载 YAML 事件数据。

    Args:
        path: YAML 文件路径。

    Returns:
        dict[str, Any]: 解码后的事件数据。

    Raises:
        ModuleNotFoundError: 当当前环境缺少 `PyYAML` 时抛出。
        ValueError: 当文件内容解码后不是 `dict` 时抛出。
    """

    try:
        import yaml
    except ModuleNotFoundError as exc:
        raise ModuleNotFoundError(
            "YAML input was detected, but PyYAML is not installed in the current environment. "
            "Install PyYAML before processing .yaml or .yml files."
        ) from exc

    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    if not isinstance(data, dict):
        raise ValueError(f"YAML event file did not decode to a dict: {path}")

    return data


def collect_events(node: Any) -> list[str]:
    """递归收集所有 `events` 字段中的事件名。

    Args:
        node: 当前遍历到的任意节点。

    Returns:
        list[str]: 当前节点下收集到的事件名列表。
    """

    if isinstance(node, dict):
        # 当前节点自己持有 `events` 时先展开，再继续向下递归寻找更深层的同名节点。
        events = collect_strings(node.get("events"))

        for key, value in node.items():
            if key == "events":
                continue

            events.extend(collect_events(value))

        return events

    if isinstance(node, list):
        events: list[str] = []

        # 列表节点只负责下探，不直接产出事件名。
        for value in node:
            events.extend(collect_events(value))

        return events

    return []


def collect_strings(node: Any) -> list[str]:
    """递归展开字符串叶子节点。

    Args:
        node: 当前遍历到的任意节点。

    Returns:
        list[str]: 从当前节点中展开得到的字符串列表。
    """

    if isinstance(node, str):
        return [] if is_excluded_event_name(node) else [node]

    if isinstance(node, list):
        items: list[str] = []

        for value in node:
            items.extend(collect_strings(value))

        return items

    if isinstance(node, dict):
        items: list[str] = []

        for value in node.values():
            items.extend(collect_strings(value))

        return items

    return []


def is_excluded_event_name(raw_event_name: str) -> bool:
    """判断事件名是否属于 TFT / TFTM 命名空间。"""

    return "tft" in raw_event_name.lower()


def build_output_path(source_path: Path, source_dir: Path, output_root: Path, version: str) -> Path:
    """构造目标文本路径。

    Args:
        source_path: 当前源文件路径。
        source_dir: `events` 根目录。
        output_root: 输出根目录。
        version: 当前导出的版本号。

    Returns:
        Path: 对应的目标文本文件路径。
    """

    # 保留 `events` 以下的相对层级，只把扩展名统一改成 `.txt`。
    relative_path = source_path.relative_to(source_dir).with_suffix(".txt")

    return output_root / version / "events" / relative_path


def write_events(path: Path, events: list[str]) -> None:
    """写出事件文本文件。

    Args:
        path: 目标文本文件路径。
        events: 待写出的事件名列表。
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(events)

    # 统一补尾部换行，便于后续 diff 和命令行工具按文本文件处理。
    if text:
        text = f"{text}\n"

    path.write_text(text, encoding="utf-8")
