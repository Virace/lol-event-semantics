"""把事件名翻译成中文可读文本。"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.model import EventContext, RenderStyle, ScopeKind
from lol_event_semantics.translate import Translator, context_from_path


@dataclass(slots=True)
class TranslateArgs:
    """翻译脚本参数。"""

    game_data_root: Path
    event_names: list[str]
    input_file: Path | None
    style: RenderStyle
    scope: ScopeKind
    owner_id: int | None
    locale: str
    limit: int | None


def main(argv: list[str] | None = None) -> int:
    """运行事件翻译。"""

    args = parse_args(argv)
    translator = Translator.from_repo(args.game_data_root, locale=args.locale)

    if args.input_file is not None:
        return run_file(translator, args)

    return run_events(translator, args)


def parse_args(argv: list[str] | None = None) -> TranslateArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Translate raw event names into human-readable Chinese text using external game-data files."
    )
    parser.add_argument(
        "--game-data-root",
        type=Path,
        required=True,
        help="External rcp-be-lol-game-data root path. This path is never hardcoded by the project.",
    )
    parser.add_argument("--event-name", action="append", default=[], help="Single raw event name. Can be repeated.")
    parser.add_argument("--input-file", type=Path, help="Raw event text file, for example raw/events/champions/1.txt")
    parser.add_argument(
        "--style",
        choices=[style.value for style in RenderStyle],
        default=RenderStyle.DEFAULT.value,
        help="Output style.",
    )
    parser.add_argument(
        "--scope",
        choices=[ScopeKind.AUTO.value, ScopeKind.CHAMPION.value, ScopeKind.MAP.value],
        default=ScopeKind.AUTO.value,
        help="Optional scope hint when translating a single event name.",
    )
    parser.add_argument("--owner-id", type=int, help="Optional champion id or map id hint.")
    parser.add_argument("--locale", default="zh_cn", help="Display locale under the external game-data root.")
    parser.add_argument("--limit", type=int, help="Optional line limit for --input-file mode.")
    namespace = parser.parse_args(argv)

    if not namespace.event_name and namespace.input_file is None:
        parser.error("At least one --event-name or one --input-file is required.")

    return TranslateArgs(
        game_data_root=namespace.game_data_root,
        event_names=namespace.event_name,
        input_file=namespace.input_file,
        style=RenderStyle(namespace.style),
        scope=ScopeKind(namespace.scope),
        owner_id=namespace.owner_id,
        locale=namespace.locale,
        limit=namespace.limit,
    )


def run_events(translator: Translator, args: TranslateArgs) -> int:
    """翻译单个或多个事件名。"""

    ctx = EventContext(scope=args.scope, owner_id=args.owner_id)

    for raw_name in args.event_names:
        meaning = translator.translate(raw_name, ctx)
        print(translator.render(meaning, args.style))

    return 0


def run_file(translator: Translator, args: TranslateArgs) -> int:
    """翻译事件文件。"""

    path = args.input_file

    if path is None or not path.is_file():
        raise FileNotFoundError(f"Input file does not exist: {path}")

    ctx = context_from_path(path)

    if args.scope is not ScopeKind.AUTO:
        ctx.scope = args.scope

    if args.owner_id is not None:
        ctx.owner_id = args.owner_id

    rows = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    if args.limit is not None:
        rows = rows[: args.limit]

    for raw_name in rows:
        meaning = translator.translate(raw_name, ctx)
        text = translator.render(meaning, args.style)
        print(f"{text}\t# {raw_name}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
