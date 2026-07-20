"""GAME 清单/本地 WAD 指纹的文件级 diff 与受影响实体集映射。

「部分下载/增量解析」的判定核心：游戏每半月更新一次，但每次真实变化的
英雄极少，第二次执行不必全量处理 191+ 英雄。两条判定通道产出同构的
「变化文件路径列表」，再统一映射为受影响实体集（英雄别名 / 地图 id /
字符串表语言）：

- **远程通道**：riotmanifest ``diff_manifests`` 对比上次锚定的 GAME
  manifest 与当前 manifest（URL 直入，库内自动下载解析清单元数据，不
  下载任何 WAD 内容），取 added/removed/changed/moved 文件路径。
- **本地通道**：对比两份本地 WAD 指纹快照（size+mtime_ns，见
  :func:`wad_source.scan_wad_fingerprints`），键即 manifest 同构相对
  路径，diff 是纯字典比较。

本模块除 :func:`diff_paths` 的默认 differ 外全部为纯逻辑：接受注入的
合成清单 diff 报告 / 指纹字典即可完整测试，不依赖网络与 riotmanifest。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from .game_manifest_wads import normalize_wad_path

type Differ = Callable[[str, str], Any]

# 语言变体后缀（如 zh_CN / en_US）：WAD 文件名点分段的地区码形态。
_LOCALE_SEGMENT = re.compile(r"^[a-z]{2}_[a-z]{2}$", re.IGNORECASE)
_MAP_SEGMENT = re.compile(r"^map(\d+)$", re.IGNORECASE)
_WAD_SUFFIX = ".wad.client"


@dataclass(frozen=True, slots=True)
class Affected:
    """一次清单 diff 映射出的受影响实体集（各字段升序去重）。

    空实例（三字段皆空）表示「有锚且确认无变化」；与「无锚需全量」的
    ``None`` 语义相区分（见 :func:`remote_affected` / :func:`local_affected`）。
    """

    champions: tuple[str, ...] = ()
    map_ids: tuple[str, ...] = ()
    stringtable_locales: tuple[str, ...] = ()


def diff_paths(old_url: str, new_url: str, *, differ: Differ | None = None) -> tuple[str, ...]:
    """对比两份 GAME manifest，返回变化文件路径（升序去重）。

    变化含 added/removed/changed 与 moved（新旧路径都算变化——removed
    与 moved 旧路径保留是刻意的：条目消失同样意味着对应实体状态改变，
    宁可多算触发一次幂等重提取，也不静默漏报）。

    Args:
        old_url: 上次锚定的 GAME manifest 地址（或本地路径）。
        new_url: 当前 GAME manifest 地址（或本地路径）。
        differ: 测试注入的 diff 执行器 ``(old, new) -> 报告对象``；缺省
            用 riotmanifest ``diff_manifests``（需可选依赖组 download）。
    """

    run = differ or _riot_differ
    return report_paths(run(old_url, new_url))


def report_paths(report: Any) -> tuple[str, ...]:
    """从 manifest diff 报告对象提取变化文件路径（升序去重）。

    鸭子类型消费 ``added/removed/changed``（条目带 ``path``）与可选
    ``moved``（条目带 ``old_path``/``new_path``），兼容 riotmanifest
    ``ManifestDiffReport`` 与测试合成报告；``unchanged`` 不参与。
    """

    paths: set[str] = set()
    for group in ("added", "removed", "changed"):
        for entry in getattr(report, group, ()) or ():
            paths.add(str(entry.path))
    for entry in getattr(report, "moved", ()) or ():
        paths.add(str(entry.old_path))
        paths.add(str(entry.new_path))
    return tuple(sorted(paths))


def fingerprint_diff(
    old: Mapping[str, Any], new: Mapping[str, Any]
) -> tuple[str, ...]:
    """对比两份 WAD 指纹快照，返回变化文件路径（升序去重）。

    键为 manifest 同构相对路径，值为指纹（size/mtime_ns 字典）；新增、
    删除与指纹值不等的键都计为变化，与 :func:`diff_paths` 的保守口径
    一致。
    """

    return tuple(sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key)))


def affected_from_paths(paths: Iterable[str]) -> Affected:
    """把变化文件路径集合映射为受影响实体集。

    只识别三类 ``*.wad.client``（其余路径静默忽略，属于本管线不消费的
    内容变化）：Champions 下映射英雄别名、Maps 下映射地图数字 id、
    Localized 下映射字符串表语言。
    """

    champions: set[str] = set()
    map_ids: set[str] = set()
    locales: set[str] = set()
    for path in paths:
        kind, value = classify_wad_path(path)
        if kind == "champion":
            champions.add(value)
        elif kind == "map":
            map_ids.add(value)
        elif kind == "stringtable":
            locales.add(value)
    return Affected(
        champions=tuple(sorted(champions)),
        map_ids=tuple(sorted(map_ids)),
        stringtable_locales=tuple(sorted(locales)),
    )


def classify_wad_path(path: str) -> tuple[str, str] | tuple[None, None]:
    """识别单个变化文件路径的实体归属。

    返回 ``(类别, 值)``：``("champion", 别名)`` / ``("map", 数字id)`` /
    ``("stringtable", 语言)``；无法归属返回 ``(None, None)``。

    路径先经 :func:`game_manifest_wads.normalize_wad_path` 规范（剥
    ``Game/`` 前缀、统一 POSIX 分隔），按所在目录段分类，仅接受
    ``*.wad.client`` 文件名：

    - ``Champions/{名字}[.语言].wad.client`` → 英雄别名。**语言变体
      （如 ``Aatrox.zh_CN.wad.client``）同样映射到该英雄**：事件名
      虽只存于基础 WAD 的皮肤 bin，理论上语言 WAD 单独变化不影响
      事件名，但归入受影响集的代价只是对该英雄做一次幂等重提取
      （秒级、产物逐字节同），换来对 Riot 内部布局漂移（bin 迁入
      语言 WAD 等）的防御——漏报会长期污染语料，误报只费几秒。
    - ``Maps/**/Map{N}[.语言].wad.client`` → 地图数字 id（同上含语言
      变体）；``Common*.wad.client`` 为跨地图共享资源，无法归属单一
      地图且当前管线不从中提取，返回不归属。
    - ``Localized/Global.{语言}.wad.client`` → 字符串表语言（大小写
      保留原样）；无语言段的 ``Global.wad.client`` 不含字符串表，
      返回不归属。
    """

    normalized = normalize_wad_path(path)
    if normalized is None:
        return (None, None)
    pure = PurePosixPath(normalized)
    name = pure.name
    if not name.lower().endswith(_WAD_SUFFIX):
        return (None, None)
    segments = name[: -len(_WAD_SUFFIX)].split(".")
    if len(segments) == 2 and not _LOCALE_SEGMENT.match(segments[1]):
        return (None, None)
    if len(segments) > 2 or not segments[0]:
        return (None, None)

    directories = {part.lower() for part in pure.parts[:-1]}
    stem = segments[0]
    if "champions" in directories:
        return ("champion", stem)
    if "maps" in directories:
        matched = _MAP_SEGMENT.match(stem)
        if matched is None:
            return (None, None)
        return ("map", matched.group(1))
    if "localized" in directories:
        if stem.lower() != "global" or len(segments) != 2:
            return (None, None)
        return ("stringtable", segments[1])
    return (None, None)


def remote_affected(
    anchor: Mapping[str, Any] | None, new_url: str, *, differ: Differ | None = None
) -> Affected | None:
    """远程模式的增量判定：由游标锚与当前 manifest 得出受影响集。

    Args:
        anchor: 游标中的 ``game_manifest`` 锚（含 ``url``）；``None``
            或缺 url（旧版游标结构）表示无锚。
        new_url: 当前解析出的 GAME manifest 地址。
        differ: 透传 :func:`diff_paths` 的测试注入 diff 执行器。

    Returns:
        无锚返回 ``None``（调用方回退全量并落锚）；锚与当前 manifest
        地址相同直接返回空 :class:`Affected`（不发起 diff）；否则返回
        清单 diff 的受影响集。
    """

    old_url = anchor.get("url") if isinstance(anchor, Mapping) else None
    if not isinstance(old_url, str) or not old_url:
        return None
    if old_url == new_url:
        return Affected()
    return affected_from_paths(diff_paths(old_url, new_url, differ=differ))


def local_affected(
    anchor: Mapping[str, Any] | None, current: Mapping[str, Any]
) -> Affected | None:
    """本地模式的增量判定：由游标指纹锚与当前扫描得出受影响集。

    Args:
        anchor: 游标中的 ``local_wads`` 指纹快照；``None`` 或空（旧版
            游标结构）表示无锚。
        current: 当前 :func:`wad_source.scan_wad_fingerprints` 扫描结果。

    Returns:
        无锚返回 ``None``（调用方回退全量并落锚）；否则返回指纹 diff
        的受影响集。
    """

    if not isinstance(anchor, Mapping) or not anchor:
        return None
    return affected_from_paths(fingerprint_diff(anchor, current))


def anchor_eligible(sources: Iterable[str], champions_arg: str) -> bool:
    """判断本次运行结束后是否允许推进增量锚。

    锚的语义是「上次全覆盖处理到的清单/指纹状态」，只有 stringtable 与
    raw-bins 都在本次源集、且英雄集为 ``all``（全量请求或增量模式对完整
    受影响集的处理）时才成立。子集运行（显式 ``--sources``/``--champions``
    子集）不推进锚：保留旧锚使下轮 diff 从旧状态起算，是安全的超集
    （最多多算幂等重提取）；提前推进才会漏处理。
    """

    return {"stringtable", "raw-bins"} <= set(sources) and champions_arg == "all"


def narrow_champions(champions: Sequence[str], affected: Affected | None) -> list[str]:
    """把请求英雄集窄化为受影响子集（保持请求顺序）。

    ``affected`` 为 ``None``（全量模式）原样返回；增量模式只保留清单
    判变的英雄。受影响集里请求外的名字（如 reference 尚未收录的新英雄
    WAD）由调用方另行告警，此处不放大请求范围。
    """

    if affected is None:
        return list(champions)
    keep = set(affected.champions)
    return [champion for champion in champions if champion in keep]


def narrow_locales(
    locales: Sequence[str], affected: Affected | None, present: Iterable[str]
) -> list[str]:
    """选出需要重下字符串表的语言（保持请求顺序）。

    ``affected`` 为 ``None``（全量模式）原样返回；增量模式保留清单判变
    的语言，并补上本地缺档的语言（``present`` 为已落盘语言集）——锚只
    保证「上次处理过的」是新的，缺档说明该语言从未落盘，必须补齐。
    """

    if affected is None:
        return list(locales)
    keep = set(affected.stringtable_locales)
    on_disk = set(present)
    return [locale for locale in locales if locale in keep or locale not in on_disk]


def _riot_differ(old_url: str, new_url: str) -> Any:
    """默认 diff 执行器：riotmanifest ``diff_manifests``（惰性导入）。"""

    try:
        from riotmanifest import diff_manifests
    except ImportError as exc:  # pragma: no cover - 依赖缺失提示
        raise ImportError(
            "清单增量 diff 需要可选依赖组 download（riotmanifest）：uv sync --extra download"
        ) from exc
    return diff_manifests(old_url, new_url)
