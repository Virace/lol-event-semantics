"""WAD 数据源局部提取：字符串表 / 英雄皮肤 bin（远程/本地双模式）。

按 SPEC（2026-07-20 数据源体系）实现 S1/S6 两个 WAD 通道，两种获取模式：

- **远程**：解析 live manifest 对 → riotmanifest 按 WAD 内固定路径局部
  提取，不下载整 WAD（需可选依赖组 ``download``：``uv sync --extra
  download``；riotmanifest 自带并发与重试，本模块不重复实现）。逐英雄
  网络提取，吞吐显著低于本地解包。
- **本地**：给定本地游戏安装目录（如 WeGame 的 ``英雄联盟``），直接用
  league-tools 解包 ``Game/DATA/FINAL`` 下的 WAD——游戏内部目录结构
  标准固定，本地解包远快于下载，且不需要 riotmanifest；两种模式产出
  逐字节一致。

客户端 WAD 内部路径多年稳定，全部集中为本模块常量，漂移时只改这里。
大厅 JSON（S2）不走 WAD——主渠道为 lol-game-data-snapshot 快照仓库
（约 6 小时更新一次、带 metadata.json 版本对照），见
:mod:`source_sync`。

与既有 :mod:`game_wad_events` / :mod:`game_manifest_wads` 的分工：那两个
模块面向自动更新 workflow 的轮次 txt 直出（bin 事件收集含
bank_unit.string 且排序）；本模块面向数据源落盘归档，事件名只取
events 字段并保持 bin 内原始顺序。

区服等价（各区文件内容相同，仅更新时间有别，OC 最快）；GAME 与 LCU
manifest 必须「成对」：主次版本号（major.minor）一致，修订号允许不同。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, get_ident
from types import ModuleType
from typing import Any, Iterator

DEFAULT_REGION = "OC1"
_BIN_PARSE_LOCK = RLock()


def _riotmanifest():
    """惰性导入 riotmanifest（仅远程模式需要）。"""

    try:
        import riotmanifest
    except ImportError as exc:  # pragma: no cover - 依赖缺失提示
        raise ImportError("远程下载需要可选依赖组 download（riotmanifest）：uv sync --extra download") from exc
    return riotmanifest


# ---- WAD 内固定路径常量（漂移时集中改这里） ----
GAME_LOCALIZED_WAD = "DATA/FINAL/Localized/Global.{locale}.wad.client"
# 内层 en_us 为固定目录名，各语言 WAD 内相同（经 CommunityDragon 镜像核实）。
STRINGTABLE_INNER = "data/menu/en_us/lol.stringtable"
CHAMPION_WAD = "DATA/FINAL/Champions/{champion}.wad.client"
SKIN_BIN_INNER = "data/characters/{champion}/skins/skin{index}.bin"
# 单 WAD 单次批量探测的皮肤 bin 上限（WADExtractor 单 WAD 建议目标数 120）。
MAX_SKIN_PROBE = 100


class WadSourceError(RuntimeError):
    """WAD 数据源获取失败（宁缺毋错：目标不完整即失败，不落盘半成品）。"""


@dataclass(frozen=True, slots=True)
class ManifestPair:
    """已解析并通过成对校验的 GAME/LCU manifest。

    Attributes:
        region: 解析用区服。
        game_version: GAME 侧版本显示串。
        lcu_version: LCU 侧版本显示串。
        game_url: GAME manifest 地址。
        lcu_url: LCU manifest 地址。
        game_id: GAME manifest 标识（增量游标锚用；解析源缺失时为空串）。
        lcu_id: LCU manifest 标识，用于绑定同轮大厅数据来源。
    """

    region: str
    game_version: str
    lcu_version: str
    game_url: str
    lcu_url: str
    game_id: str = ""
    lcu_id: str = ""


def main_version(version: str) -> str:
    """取版本串的 major.minor 主版本。"""

    return ".".join(str(version).split(".")[:2])


def _version_text(version: object) -> str:
    """把 riotmanifest 的 VersionInfo 规范为构建号文本。"""

    return str(getattr(version, "normalized_build", None) or version)


def _read_lcu_version(payload: bytes) -> str:
    """读取 Windows 客户端资源中的四段版本，保留修订号原始位数。

    Riot 16.20 客户端使用 ``16.20.827.198``；PE 版本资源的数值分段
    不要求补零，不能沿用 riotmanifest 2.4.3 对第四段四位宽度的限制。
    """

    pattern = rb"(?:[0-9]\x00)+(?:\.\x00(?:[0-9]\x00)+){3}\x00\x00"
    for label in ("ProductVersion", "FileVersion"):
        offset = payload.find(label.encode("utf-16le"))
        if offset < 0:
            continue
        match = re.search(pattern, payload[offset : offset + 512])
        if match is not None:
            return match.group().decode("utf-16le").rstrip("\x00")
    raise WadSourceError("LeagueClient.exe 缺少四段 ProductVersion/FileVersion")


def _build_key(version: str) -> tuple[int, ...]:
    """将 GAME 紧凑版本与 LCU 四段版本转换为相同的数值排序键。"""

    parts = version.split(".")
    if not all(part.isascii() and part.isdigit() for part in parts):
        raise WadSourceError(f"无法比较 manifest 版本：{version}")
    if len(parts) == 3 and len(parts[2]) > 4:
        # GAME metadata 的构建号按 riotmanifest 的 3/4 规则拆分。
        parts = [*parts[:2], parts[2][:-4], parts[2][-4:]]
    if len(parts) != 4:
        raise WadSourceError(f"无法比较 manifest 版本：{version}")
    return tuple(int(part) for part in parts)


def _resolve_pair(backend: ModuleType, region: str, work_dir: Path) -> ManifestPair:
    """通过公开 manifest API 读取真实 LCU 版本并选择兼容 GAME 候选。"""

    resolver = backend.LeagueManifestResolver()
    lcu = resolver.get_lcu_manifest(region)
    cache = work_dir / hashlib.sha256(lcu.url.encode("utf-8")).hexdigest()
    manifest = backend.PatcherManifest(lcu.url, path=str(cache))
    client = manifest.files.get("LeagueClient.exe")
    if client is None or client.name != "LeagueClient.exe":
        raise WadSourceError("LCU manifest 缺少 LeagueClient.exe 版本载体")
    # 直接取校验过的 chunks，避免第三方仅按尺寸复用中断后预分配的文件。
    payload = client.download_chunks(client.chunks)
    if len(payload) != client.size:
        raise WadSourceError("LeagueClient.exe 下载长度与 manifest 不一致")
    lcu_version = _read_lcu_version(payload)
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "LeagueClient.exe").write_bytes(payload)
    candidates = [
        item
        for item in resolver.list_game_candidates(region)
        if item.version
        and main_version(_version_text(item.version)) == main_version(lcu_version)
        and _build_key(_version_text(item.version)) <= _build_key(lcu_version)
    ]
    if not candidates:
        raise WadSourceError(f"GAME/LCU manifest 未成对：没有不高于 LCU {lcu_version} 的同补丁 GAME")
    game = max(candidates, key=lambda item: _build_key(_version_text(item.version)))
    return ManifestPair(
        region=region,
        game_version=_version_text(game.version),
        lcu_version=lcu_version,
        game_url=game.url,
        lcu_url=lcu.url,
        game_id=str(game.manifest_id or ""),
        lcu_id=str(lcu.manifest_id or ""),
    )


def resolve_pair(
    region: str = DEFAULT_REGION,
    attempts: int = 4,
    backoff: float = 3.0,
    *,
    work_dir: Path | None = None,
) -> ManifestPair:
    """解析 live manifest 对并做成对校验。

    sieve/riotcdn 存在偶发不可达（历史上 GH Actions 与本地均遇到过），
    解析步骤带退避重试；下载步骤的重试由 riotmanifest 内部承担。

    Args:
        region: 区服代号；各区文件内容等价，默认取更新最快的 OC。
        attempts: 解析尝试次数。
        backoff: 重试基础退避秒数（线性递增）。
        work_dir: 版本载体保存目录；默认位于 dist/sources/work/manifest-resolution。

    Returns:
        校验通过的 :class:`ManifestPair`。

    Raises:
        WadSourceError: 版本载体不可用，或没有同补丁且不高于 LCU 的 GAME。
    """

    if attempts < 1 or backoff < 0:
        raise ValueError("attempts 必须为正数，backoff 不得为负数")
    backend = _riotmanifest()
    cache = work_dir if work_dir is not None else Path("dist/sources/work/manifest-resolution")
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return _resolve_pair(backend, region, cache)
        except Exception as exc:  # noqa: BLE001 - 网络抖动统一重试
            last = exc
            if attempt < attempts - 1:
                time.sleep(backoff * (attempt + 1))
    raise WadSourceError(f"manifest 解析失败（重试 {attempts} 次）：{last}") from last


def open_extractor(manifest_url: str, work_dir: Path):
    """构造 manifest 的 WAD 局部提取器（远程模式）。"""

    rm = _riotmanifest()
    return rm.WADExtractor(rm.PatcherManifest(manifest_url, path=str(work_dir)))


def _require(extracted: dict[str, dict[str, bytes | None]], wad: str, inner: str) -> bytes:
    """取提取结果中的单个文件，缺失即报错。"""

    data = extracted.get(wad, {}).get(inner)
    if data is None:
        raise WadSourceError(f"WAD 提取缺失：{wad} :: {inner}")
    return data


def download_stringtables(extractor, locales: tuple[str, ...], out_dir: Path) -> dict[str, Path]:
    """提取各语言字符串表并落盘。

    Args:
        extractor: GAME manifest 的提取器。
        locales: 语言列表（如 ``("zh_CN", "en_US")``）。
        out_dir: 输出目录（``stringtable/``）。

    Returns:
        语言到落盘路径的映射；文件名沿 ``Global.{locale}-lol.stringtable`` 约定。
    """

    request = {GAME_LOCALIZED_WAD.format(locale=locale): [STRINGTABLE_INNER] for locale in locales}
    extracted = extractor.extract_files(request)
    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for locale in locales:
        wad = GAME_LOCALIZED_WAD.format(locale=locale)
        target = out_dir / f"Global.{locale}-lol.stringtable"
        target.write_bytes(_require(extracted, wad, STRINGTABLE_INNER))
        results[locale] = target
    return results


def download_champion_bins(extractor, champion: str, out_root: Path) -> list[Path]:
    """提取单个英雄全部皮肤 bin（skin0..N，批量探测存在项）。

    Args:
        extractor: GAME manifest 的提取器。
        champion: 英雄别名（WAD 文件名用原样、内层路径用小写）。
        out_root: 输出根（``raw-bins/champions/``）。

    Returns:
        落盘的 bin 路径列表（按 skin 号升序）；skin0 缺失视为整体失败。
    """

    wad = CHAMPION_WAD.format(champion=champion)
    inners = [SKIN_BIN_INNER.format(champion=champion.lower(), index=index) for index in range(MAX_SKIN_PROBE)]
    extracted = extractor.extract_files({wad: inners}).get(wad, {})
    results: list[Path] = []
    for index in range(MAX_SKIN_PROBE):
        data = extracted.get(SKIN_BIN_INNER.format(champion=champion.lower(), index=index))
        if data is None:
            continue
        target = out_root / champion / f"skin{index}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        results.append(target)
    if not results:
        raise WadSourceError(f"英雄 bin 无一命中（含 skin0）：{wad}")
    return results


# ---- 本地游戏目录通道（Game/DATA/FINAL 为标准固定布局） ----
GAME_FINAL_SUBDIR = Path("Game/DATA/FINAL")
LOCAL_VERSION_FILE = Path("Game/content-metadata.json")
# 增量判定指纹扫描覆盖的 DATA/FINAL 子目录（英雄 / 字符串表 / 地图 WAD）。
FINGERPRINT_SUBDIRS = ("Champions", "Localized", "Maps/Shipping")


def scan_wad_fingerprints(game_dir: Path) -> dict[str, dict[str, int]]:
    """扫描本地安装目录中增量判定相关 WAD 的文件指纹。

    覆盖 ``Game/DATA/FINAL`` 下 Champions / Localized / Maps/Shipping
    三个目录的全部 ``*.wad.client``（含语言变体，与
    :func:`manifest_diff.classify_wad_path` 的映射口径一致）。键为 GAME
    manifest 同构的相对路径（无 ``Game/`` 前缀、POSIX 分隔），值为
    ``{"size": 字节数, "mtime_ns": 纳秒 mtime}``——补丁器重写变化文件
    即改写这两项；同尺寸同 mtime 的内容改写理论上探测不到，但补丁流程
    不产生该形态。修复/重装可能整体刷新 mtime，代价只是退化为一次全量
    重提取（结果幂等）。目录缺失（如精简安装）跳过不报错。

    Returns:
        指纹快照（键升序），可直接 JSON 序列化进游标并与历史快照做
        :func:`manifest_diff.fingerprint_diff`。
    """

    fingerprints: dict[str, dict[str, int]] = {}
    final_root = game_dir / GAME_FINAL_SUBDIR
    for subdir in FINGERPRINT_SUBDIRS:
        directory = final_root / Path(subdir)
        if not directory.is_dir():
            continue
        for wad in sorted(directory.glob("*.wad.client")):
            stat = wad.stat()
            fingerprints[f"DATA/FINAL/{subdir}/{wad.name}"] = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    return fingerprints


def local_game_version(game_dir: Path) -> str | None:
    """读本地安装的构建版本（content-metadata.json 的 version 字段）。"""

    path = game_dir / LOCAL_VERSION_FILE
    if not path.is_file():
        return None
    version = json.loads(path.read_text(encoding="utf-8")).get("version", "")
    return str(version).split("+")[0] or None


def local_stringtables(game_dir: Path, locales: tuple[str, ...], out_dir: Path) -> dict[str, Path]:
    """从本地游戏目录的 Localized WAD 提取各语言字符串表。

    Args:
        game_dir: 游戏安装根目录（含 ``Game/DATA/FINAL``）。
        locales: 语言列表；对应 WAD 须已安装（国服客户端通常含
            zh_CN/en_US 等五语言）。
        out_dir: 输出目录，文件命名与远程模式一致。

    Returns:
        语言到落盘路径的映射。

    Raises:
        WadSourceError: WAD 缺失或提取失败。
    """

    from league_tools import WAD

    out_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for locale in locales:
        wad_path = game_dir / GAME_FINAL_SUBDIR / "Localized" / f"Global.{locale}.wad.client"
        if not wad_path.is_file():
            raise WadSourceError(f"本地 Localized WAD 缺失：{wad_path}")
        data = WAD(str(wad_path)).extract([STRINGTABLE_INNER], raw=True)[0]
        if not data:
            raise WadSourceError(f"本地 WAD 提取缺失：{wad_path} :: {STRINGTABLE_INNER}")
        target = out_dir / f"Global.{locale}-lol.stringtable"
        target.write_bytes(data)
        results[locale] = target
    return results


def local_champion_bins(game_dir: Path, champion: str, out_root: Path) -> list[Path]:
    """从本地游戏目录的英雄 WAD 提取全部皮肤 bin（skin0..N 批量探测）。

    Args:
        game_dir: 游戏安装根目录（含 ``Game/DATA/FINAL``）。
        champion: 英雄别名（WAD 文件名用原样、内层路径用小写）。
        out_root: 输出根（``raw-bins/champions/``）。

    Returns:
        落盘的 bin 路径列表（按 skin 号升序）。

    Raises:
        WadSourceError: WAD 缺失或 bin 无一命中。
    """

    from league_tools import WAD

    wad_path = game_dir / GAME_FINAL_SUBDIR / "Champions" / f"{champion}.wad.client"
    if not wad_path.is_file():
        raise WadSourceError(f"本地英雄 WAD 缺失：{wad_path}")
    inners = [SKIN_BIN_INNER.format(champion=champion.lower(), index=index) for index in range(MAX_SKIN_PROBE)]
    extracted = WAD(str(wad_path)).extract(inners, raw=True)
    results: list[Path] = []
    for index, data in enumerate(extracted):
        if not data:
            continue
        target = out_root / champion / f"skin{index}.bin"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        results.append(target)
    if not results:
        raise WadSourceError(f"本地英雄 bin 无一命中（含 skin0）：{wad_path}")
    return results


def extract_event_names(bin_data: bytes, *, strict: bool = False) -> list[str]:
    """从英雄或地图 BIN 结构化提取音频事件名（去重保序）。

    经 league-tools BIN 解析 bank unit 的 events 字段，与既有 raw 语料
    生产方式同源；与语料的比对入口见 scripts/download_sources.py
    ``--verify-corpus``。

    Args:
        bin_data: 从 WAD 提取的原始 BIN 字节。
        strict: 校验 PROP 文件头，并拒绝解析器报告诊断后的空或部分结果。
            默认关闭，保留已有维护入口的行为。合法的无事件 BIN 仍返回空列表。

    Raises:
        WadSourceError: 严格模式下文件头无效、解析异常或存在 WARNING 及以上诊断。
    """

    if not strict:
        return _parse_event_names(bin_data)
    if not bin_data.startswith(b"PROP"):
        raise WadSourceError("BIN 文件头无效：需要 PROP")
    with _bin_diagnostics() as diagnostics:
        try:
            events = _parse_event_names(bin_data)
        except Exception as exc:
            raise WadSourceError(f"BIN 解析失败：{type(exc).__name__}") from exc
        if diagnostics:
            summary = "; ".join(diagnostics[:3])[:500]
            raise WadSourceError(f"BIN 解析存在诊断，拒绝不完整结果：{summary}")
        return events


def _parse_event_names(bin_data: bytes) -> list[str]:
    """保留解析器给出的原名，以首次出现的顺序去重。"""

    from league_tools import BIN

    seen: dict[str, None] = {}
    for top in BIN(bin_data).data:
        for unit in top.bank_units:
            for event in unit.events:
                seen.setdefault(str(event.string), None)
    return list(seen)


@contextmanager
def _bin_diagnostics() -> Iterator[list[str]]:
    """临时捕获当前严格解析的库诊断，并恢复原日志开关。

    第三方解析器会捕获部分解析异常并仅记录日志，且库默认关闭日志。
    严格调用串行切换公开日志开关，只添加和移除自己的诊断 sink，保留调用方
    现有 sink；调用方已启用的日志在退出后仍保持启用。
    """

    from league_tools import disable_logging, enable_logging, is_logging_enabled
    from loguru import logger

    with _BIN_PARSE_LOCK:
        enabled = is_logging_enabled()
        thread = get_ident()
        diagnostics: list[str] = []

        def capture(message: Any) -> None:
            """仅保存短诊断文本，避免把日志对象留在采集结果中。"""

            if len(diagnostics) < 3:
                diagnostics.append(str(message.record["message"])[:500])

        def relevant(record: Any) -> bool:
            """隔离其他线程和调用方自身日志。"""

            name = record["name"] or ""
            return name.startswith("league_tools.") and record["thread"].id == thread

        sink = logger.add(capture, level="WARNING", filter=relevant)
        try:
            if not enabled:
                enable_logging()
            yield diagnostics
        finally:
            if not enabled:
                disable_logging()
            logger.remove(sink)
