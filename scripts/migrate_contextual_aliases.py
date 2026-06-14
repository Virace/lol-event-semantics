"""把 contextual aliases 单文件迁移为按 owner 分片。"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from lol_event_semantics.rules import rule_key


SECTION_DIRS = {
    "champions": "champions",
    "modes": "modes",
    "sfx": "sfx",
}

SECTION_SCOPES = {
    "champions": "champion",
    "modes": "mode",
    "sfx": "sfx",
}


def main(argv: list[str] | None = None) -> int:
    """运行 contextual alias 分片迁移。"""

    args = parse_args(argv)
    data = read_yaml(args.input)
    fragments = build_fragments(data, args.output_root)

    if args.dry_run:
        for target, fragment in fragments:
            aliases = fragment.get("aliases") or {}
            print(f"{target.as_posix()} aliases={len(aliases)}")
        print(f"files={len(fragments)} dry_run=true")
        return 0

    for target, fragment in fragments:
        write_yaml(target, fragment)

    print(f"files={len(fragments)} dry_run=false")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(description="Migrate rules/contextual_aliases.yaml into contextual fragments.")
    parser.add_argument("--input", type=Path, default=Path("rules/contextual_aliases.yaml"))
    parser.add_argument("--output-root", type=Path, default=Path("rules/contextual"))
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def read_yaml(path: Path) -> dict[str, Any]:
    """读取 YAML 文件。"""

    yaml = YAML(typ="safe")
    data = yaml.load(path.read_text(encoding="utf-8"))

    if isinstance(data, dict):
        return data

    return {}


def build_fragments(data: dict[str, Any], output_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    """构造分片目标路径和内容。"""

    fragments: list[tuple[Path, dict[str, Any]]] = []

    for section, dirname in SECTION_DIRS.items():
        owners = data.get(section) or {}

        if not isinstance(owners, dict):
            continue

        for owner in sorted(owners):
            rows = owners[owner]

            if not isinstance(rows, dict) or not rows:
                continue

            effective_rows = effective_alias_rows(rows)
            fragment = {
                "scope": SECTION_SCOPES[section],
                "owner": str(owner),
                "aliases": {
                    token: value
                    for token, value in sorted(effective_rows.items())
                },
            }
            fragments.append((output_root / dirname / f"{owner}.yaml", fragment))

    return fragments


def effective_alias_rows(rows: dict[Any, Any]) -> dict[str, Any]:
    """按旧单文件加载语义保留最终生效的 alias 行。"""

    effective_by_key: dict[str, tuple[str, Any]] = {}

    for token, value in rows.items():
        effective_by_key[rule_key(token)] = (str(token), value)

    return {
        token: value
        for token, value in (item for item in effective_by_key.values())
    }


def write_yaml(path: Path, data: dict[str, Any]) -> None:
    """写出一个分片 YAML。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.allow_unicode = True
    yaml.indent(mapping=2, sequence=4, offset=2)

    with path.open("w", encoding="utf-8", newline="") as file:
        yaml.dump(data, file)


if __name__ == "__main__":
    raise SystemExit(main())
