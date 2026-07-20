"""数据源获取与落盘的共享逻辑（scripts/download_sources.py 的实现层）。

覆盖四类不走 WAD 提取器的领域操作：reference 分片的英雄枚举、
CommunityDragon RST key 字典的本地瘦身、lol-game-data-snapshot 快照仓库
的克隆与大厅 JSON 复制、bin 提取事件名与既有 raw 语料的比对。WAD 通道
本体在 :mod:`wad_source`，HTTP 重试在 :mod:`http_fetch`。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .rst_stringtable import load as load_stringtable
from .wad_source import extract_event_names

RST_HASHES_URL = (
    "https://raw.githubusercontent.com/CommunityDragon/Data/master/hashes/lol/hashes.rst.xxh3.txt"
)
# lol-game-data-snapshot：大厅 JSON 与 game-data 主渠道（避免 LCU WAD 解包；
# 快照约 6 小时更新一次，metadata.json 提供 gameVersion/lcuManifestId 对照）。
# 获取方式为整仓浅克隆到 dist/sources/ 内（已 gitignore），后续 pull 增量
# 更新——相比逐文件 HTTP（仅英雄详情就 100+ 请求）网络成本大幅降低，
# reference 导入可用 file:// 指向克隆实现零网络。
SNAPSHOT_GIT_URL = "https://github.com/Virace/lol-game-data-snapshot.git"
SNAPSHOT_CLONE_DIRNAME = "lol-game-data-snapshot"
LOBBY_JSON_NAMES = ("items", "maps", "queues", "skinlines", "skins")


def champion_aliases(reference_root: Path) -> list[str]:
    """从 reference 分片枚举英雄别名（排除占位条目）。"""

    return sorted(
        p.stem for p in (reference_root / "champions").glob("*.yaml") if p.stem != "None"
    )


def champion_numeric_id(reference_root: Path, alias: str) -> str | None:
    """读取英雄的数字 id（raw 语料文件名），解析失败返回 None。"""

    path = reference_root / "champions" / f"{alias}.yaml"
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("id:"):
            value = stripped.split(":", 1)[1].strip().strip("'\"")
            return value if value.isdigit() else None
    return None


def trim_rst_hashes(raw_dict: bytes, stringtable_dir: Path, out_path: Path) -> tuple[int, int]:
    """按本地字符串表 hash 交集瘦身字典。

    Returns:
        （字典总行数, 瘦身后行数）。
    """

    table = load_stringtable(next(stringtable_dir.glob("Global.*-lol.stringtable")))
    mask = (1 << table.hash_bits) - 1
    kept: list[str] = []
    total = 0
    for line in raw_dict.decode("utf-8").splitlines():
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        total += 1
        if int(parts[0], 16) & mask in table.entries:
            kept.append(line)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return total, len(kept)


def ensure_snapshot_clone(sources_root: Path) -> Path:
    """克隆或增量更新快照仓库的本地浅克隆。

    落点在 dist/sources/ 内（已 gitignore，不会进入远程仓库）；已存在时
    ``git pull --ff-only`` 增量更新，网络成本远低于逐文件 HTTP。

    Returns:
        克隆根目录路径。

    Raises:
        RuntimeError: git 命令失败。
    """

    clone = sources_root / SNAPSHOT_CLONE_DIRNAME
    if (clone / ".git").is_dir():
        command = ["git", "-C", str(clone), "pull", "--ff-only"]
    else:
        clone.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "clone", "--depth", "1", SNAPSHOT_GIT_URL, str(clone)]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"快照仓库克隆/更新失败：{result.stderr.strip()[:300]}")
    return clone


def find_ci(base: Path, relative: str) -> Path | None:
    """在 base 下按大小写不敏感方式解析相对路径。

    Windows 文件系统天然不敏感、直接命中；Linux 上目录实际大小写与请求
    不一致时（如 zh_CN vs zh_cn）逐段扫描兜底。找不到返回 None。
    """

    current = base
    for part in Path(relative).parts:
        direct = current / part
        if direct.exists():
            current = direct
            continue
        if not current.is_dir():
            return None
        matches = [e for e in current.iterdir() if e.name.lower() == part.lower()]
        if not matches:
            return None
        current = matches[0]
    return current


def copy_lobby_from_clone(
    clone: Path, meta: dict, locales: tuple[str, ...], out_dir: Path
) -> dict[str, Path]:
    """从本地克隆复制大厅 JSON（各语言 + default）到 lobby layout。

    快照内为原路径结构 ``global/{locale}/v1/{name}.json``；本地落
    ``lobby/{locale小写}/{name}.json``（en_US 即 default）。

    Returns:
        ``"{locale}/{name}"`` 到落盘路径的映射；缺文件即报错。
    """

    snapshot_languages = set(meta.get("languages", []))
    wanted = dict.fromkeys(
        ["default" if l.lower() == "en_us" else l for l in locales] + ["default"]
    )
    results: dict[str, Path] = {}
    for locale in wanted:
        if snapshot_languages and locale not in snapshot_languages:
            raise RuntimeError(f"快照仓库不含语言 {locale}（现有：{sorted(snapshot_languages)}）")
        for name in LOBBY_JSON_NAMES:
            source = find_ci(clone, f"global/{locale}/v1/{name}.json")
            if source is None:
                raise RuntimeError(f"快照克隆缺文件：global/{locale}/v1/{name}.json")
            target = out_dir / locale.lower() / f"{name}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            results[f"{locale}/{name}"] = target
    return results


def verify_corpus(bins: list[Path], corpus_file: Path) -> str:
    """对比 bin 提取事件名与既有 raw 语料，返回摘要行。"""

    extracted: set[str] = set()
    for path in bins:
        extracted.update(extract_event_names(path.read_bytes()))
    if not corpus_file.is_file():
        return f"语料文件缺失：{corpus_file}（提取 {len(extracted)} 条）"
    corpus = {line.strip() for line in corpus_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    hit = len(corpus & extracted)
    return (
        f"语料 {len(corpus)} 条 | 提取 {len(extracted)} 条 | 命中 {hit} "
        f"({hit / len(corpus):.1%}) | 语料独有 {len(corpus - extracted)} | 提取多出 {len(extracted - corpus)}"
    )
