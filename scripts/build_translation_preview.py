"""批量生成事件翻译预览并提取英文残留。"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from lol_event_semantics.translate import Translator, context_from_path

WORD_RX = re.compile(r"[A-Za-z][A-Za-z0-9]*")
IGNORE_WORDS = {"Q", "W", "E", "R", "P"}


@dataclass(slots=True)
class BuildArgs:
    """批量预览参数。"""

    game_data_root: Path
    raw_root: Path
    output_root: Path
    residue_path: Path
    summary_path: Path


def main(argv: list[str] | None = None) -> int:
    """运行批量预览。"""

    args = parse_args(argv)
    translator = Translator.from_repo(args.game_data_root)
    stats = Counter()
    residue_words = Counter()
    short_words = Counter()
    residue_lines = defaultdict(list)

    for path in sorted(args.raw_root.rglob("*.txt")):
        ctx = context_from_path(path)
        rel = path.relative_to(args.raw_root)
        out_path = args.output_root / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        rendered: list[str] = []

        for raw_name in iter_rows(path):
            meaning = translator.translate(raw_name, ctx)
            text = translator.render(meaning)
            rendered.append(f"{text}\t# {raw_name}")
            stats[meaning.confidence.value] += 1

            words = [word for word in WORD_RX.findall(text) if word not in IGNORE_WORDS]

            if not words:
                continue

            for word in words:
                if len(word) >= 4:
                    residue_words[word] += 1
                else:
                    short_words[word] += 1

            key = " ".join(words[:5])

            if len(residue_lines[key]) < 3:
                residue_lines[key].append(
                    {
                        "file": rel.as_posix(),
                        "text": text,
                        "raw": raw_name,
                        "words": words,
                    }
                )

        out_path.write_text("\n".join(rendered) + ("\n" if rendered else ""), encoding="utf-8")

    args.summary_path.write_text(
        json.dumps({"confidence": dict(stats)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    args.residue_path.write_text(
        json.dumps(
            {
                "top_words": residue_words.most_common(200),
                "short_words": short_words.most_common(80),
                "sample_groups": [
                    {"key": key, "samples": samples}
                    for key, samples in residue_lines.items()
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(f"output_root: {args.output_root}")
    print(f"summary_path: {args.summary_path}")
    print(f"residue_path: {args.residue_path}")
    print(f"confidence: {dict(stats)}")

    return 0


def parse_args(argv: list[str] | None = None) -> BuildArgs:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        description="Build translated preview files from raw event text files and extract English residue lines."
    )
    parser.add_argument("--game-data-root", type=Path, required=True, help="External rcp-be-lol-game-data root.")
    parser.add_argument("--raw-root", type=Path, required=True, help="Raw event text root.")
    parser.add_argument("--output-root", type=Path, required=True, help="Preview output root.")
    parser.add_argument("--residue-path", type=Path, required=True, help="English residue report path.")
    parser.add_argument("--summary-path", type=Path, required=True, help="Summary report path.")
    ns = parser.parse_args(argv)

    return BuildArgs(
        game_data_root=ns.game_data_root,
        raw_root=ns.raw_root,
        output_root=ns.output_root,
        residue_path=ns.residue_path,
        summary_path=ns.summary_path,
    )


def iter_rows(path: Path) -> list[str]:
    """返回非空事件行。"""

    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


if __name__ == "__main__":
    raise SystemExit(main())
