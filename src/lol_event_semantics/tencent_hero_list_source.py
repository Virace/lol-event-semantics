"""腾讯 hero_list 在线下载与名称体系解析。

hero_list.js 是中国服免鉴权 GET 端点，扩展名为 .js 但内容是纯 JSON。
它补全 DDragon/game_data 缺失的玩家俗称和拼音搜索别名，并提供与
DDragon zh_CN 一致的俗名/称号（命名反直觉：``title`` 是俗名、``name`` 是称号）。
"""

from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


TENCENT_HERO_LIST_URL = "https://game.gtimg.cn/images/lol/act/img/js/heroList/hero_list.js"
TENCENT_SOURCE = "tencent_101"

type Fetcher = Callable[[str], bytes]


class HeroListSourceError(ValueError):
    """hero_list source 解析或下载失败。"""


@dataclass(frozen=True, slots=True)
class HeroNameEntry:
    """单个英雄的名称体系条目。"""

    hero_id: int
    alias: str
    common_name: str  # 俗名 ← hero_list.title
    title: str  # 称号 ← hero_list.name
    nicknames: tuple[str, ...]  # 俗称 ← keywords 中 CJK，排除俗名/称号
    search_alias: tuple[str, ...]  # 拼音/缩写 ← keywords 中非 CJK，排除英文 alias
    version: str


def download_hero_list(
    *,
    output_root: Path | str,
    url: str = TENCENT_HERO_LIST_URL,
    fetcher: Fetcher | None = None,
) -> str:
    """下载 hero_list.js 并写出本地缓存，返回其 version。"""

    fetch = fetcher or fetch_bytes
    payload = read_json_bytes(fetch(url))
    version = hero_list_version(payload)
    write_json_bytes(Path(output_root) / "hero_list.json", payload)
    return version


def load_hero_list(path: Path | str) -> dict[str, Any]:
    """读取本地 hero_list 缓存。"""

    target = Path(path)
    if not target.is_file():
        raise HeroListSourceError(f"hero_list cache does not exist: {target}")

    payload = json.loads(target.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise HeroListSourceError(f"hero_list cache did not decode to a mapping: {target}")
    return payload


def parse_hero_entries(payload: Any) -> list[HeroNameEntry]:
    """把 hero_list payload 解析为名称体系条目。"""

    version = hero_list_version(payload)
    heroes = payload.get("hero") if isinstance(payload, dict) else None
    if not isinstance(heroes, list):
        raise HeroListSourceError("hero_list payload missing hero list")

    entries: list[HeroNameEntry] = []
    for row in heroes:
        entry = parse_hero_row(row, version)
        if entry is not None:
            entries.append(entry)
    return entries


def parse_hero_row(row: Any, version: str) -> HeroNameEntry | None:
    """解析单个 hero 行；缺少 heroId 时跳过。"""

    if not isinstance(row, dict):
        return None

    hero_id = parse_int(row.get("heroId"))
    if hero_id is None:
        return None

    alias = str(row.get("alias") or "")
    common_name = str(row.get("title") or "").strip()  # 俗名
    title = str(row.get("name") or "").strip()  # 称号
    keywords = split_keywords(row.get("keywords"))

    cjk = [token for token in keywords if is_cjk(token)]
    non_cjk = [token for token in keywords if not is_cjk(token)]
    nicknames = dedupe(token for token in cjk if token not in (common_name, title))
    search_alias = dedupe(token for token in non_cjk if token.casefold() != alias.casefold())

    return HeroNameEntry(
        hero_id=hero_id,
        alias=alias,
        common_name=common_name,
        title=title,
        nicknames=nicknames,
        search_alias=search_alias,
        version=version,
    )


def hero_list_version(payload: Any) -> str:
    """读取 hero_list version。"""

    if not isinstance(payload, dict) or not payload.get("version"):
        raise HeroListSourceError("hero_list payload missing version")
    return str(payload["version"])


def split_keywords(value: Any) -> list[str]:
    """把逗号分隔的 keywords 拆成去空白的 token 列表。"""

    if not isinstance(value, str):
        return []
    return [token.strip() for token in value.split(",") if token.strip()]


def is_cjk(token: str) -> bool:
    """判断 token 是否包含 CJK 汉字。

    区间 U+3400–U+9FFF（扩展 A + 基本区）覆盖全部英雄中文名与俗称；用于把 keywords
    里的中文（俗称）与拼音/缩写（非 CJK，进 search_alias）分流。罕见的扩展 B+/兼容汉字
    暂不覆盖，当前英雄无此类字符。
    """

    return any("㐀" <= char <= "鿿" for char in token)


def dedupe(tokens: Any) -> tuple[str, ...]:
    """保序去重。"""

    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            result.append(token)
    return tuple(result)


def read_json_bytes(payload: bytes) -> Any:
    """从 bytes 解码 JSON。"""

    return json.loads(payload.decode("utf-8"))


def write_json_bytes(path: Path, payload: Any) -> None:
    """稳定写出 JSON payload。"""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_bytes(url: str) -> bytes:
    """使用共享重试 fetcher 下载 bytes（见 http_fetch 模块）。"""

    from .http_fetch import fetch_bytes as fetch_with_retry

    return fetch_with_retry(url)


def parse_int(value: Any) -> int | None:
    """宽松解析整数。"""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None
