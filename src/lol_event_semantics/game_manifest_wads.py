"""从 GAME manifest 按需抽取 WAD 内 BIN。"""

from __future__ import annotations

import urllib.request
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

from .game_wad_events import BinFactory, RoundExtractionResult, collect_bin_events, write_round_file


type Fetcher = Callable[[str], bytes]
type ManifestFactory = Callable[[Path, Path], Any]
type WadExtractorFactory = Callable[[Any], Any]


class GameManifestWadError(RuntimeError):
    """GAME manifest WAD 按需抽取失败。"""


@dataclass(frozen=True, slots=True)
class ManifestBinRequest:
    """单个 manifest WAD 内 BIN 抽取请求。"""

    group: str
    entity: str
    wad_path: str
    bin_paths: tuple[str, ...]


def extract_round_from_game_manifest(
    *,
    manifest_url: str,
    output_root: Path | str,
    cache_root: Path | str,
    requests: tuple[ManifestBinRequest, ...],
    fetcher: Fetcher | None = None,
    manifest_factory: ManifestFactory | None = None,
    extractor_factory: WadExtractorFactory | None = None,
    bin_factory: BinFactory | None = None,
) -> RoundExtractionResult:
    """从远端 GAME manifest 按需抽取 BIN 并写为 round txt。"""

    manifest_path = ensure_manifest_cached(manifest_url, Path(cache_root) / "manifests", fetcher=fetcher)
    manifest = create_manifest(manifest_path, Path(cache_root) / "downloads", manifest_factory)
    extractor = create_extractor(manifest, extractor_factory)
    create_bin = bin_factory or default_bin_factory
    events_by_entity: dict[tuple[str, str], set[str]] = {}
    warning_count = 0

    for request in requests:
        manifest_wad_path = normalize_wad_path(request.wad_path)
        if manifest_wad_path is None:
            warning_count += len(request.bin_paths)
            continue

        for bin_path in request.bin_paths:
            data = extract_bin_bytes(extractor, manifest_wad_path, bin_path)
            if data is None:
                warning_count += 1
                continue

            events_by_entity.setdefault((request.group, request.entity), set()).update(collect_bin_events(create_bin(data)))

    written_files = 0
    written_events = 0
    for (group, entity), events in sorted(events_by_entity.items()):
        if not events:
            continue

        write_round_file(Path(output_root) / group / f"{entity}.txt", sorted(events))
        written_files += 1
        written_events += len(events)

    warnings = tuple()
    if warning_count:
        from .game_wad_events import ExtractionWarning

        warnings = (ExtractionWarning(kind="missing_manifest_bin", path=str(warning_count)),)

    return RoundExtractionResult(files=written_files, events=written_events, warnings=warnings)


def ensure_manifest_cached(manifest_url: str, manifest_cache_dir: Path, *, fetcher: Fetcher | None = None) -> Path:
    """缓存远端 GAME manifest 文件。"""

    manifest_name = manifest_url.rstrip("/").rsplit("/", maxsplit=1)[-1] or "game.manifest"
    manifest_path = manifest_cache_dir / manifest_name
    if manifest_path.is_file():
        return manifest_path

    fetch = fetcher or fetch_bytes
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_bytes(fetch(manifest_url))
    return manifest_path


def normalize_wad_path(wad_path: str) -> str | None:
    """把 `Game/...` WAD 路径规范成 GAME manifest 中的相对路径。"""

    if not wad_path:
        return None

    raw_path = PurePosixPath(str(wad_path).replace("\\", "/"))
    parts = list(raw_path.parts)
    if parts and parts[0].lower() == "game":
        parts = parts[1:]
    if not parts:
        return None
    return PurePosixPath(*parts).as_posix()


def extract_bin_bytes(extractor: Any, wad_path: str, bin_path: str) -> bytes | None:
    """用 WADExtractor 从 GAME manifest 的 WAD 中抽取单个 BIN。"""

    extract_files = getattr(extractor, "extract_files", None)
    if extract_files is not None:
        try:
            result = extract_files({wad_path: [bin_path]})
        except (KeyError, FileNotFoundError, ValueError):
            result = None

        if isinstance(result, dict):
            wad_result = result.get(wad_path)
            if isinstance(wad_result, dict):
                data = wad_result.get(bin_path)
                if isinstance(data, bytes):
                    return data

    for method_name in ("extract_file", "extract"):
        method = getattr(extractor, method_name, None)
        if method is None:
            continue

        try:
            result = method(wad_path, bin_path)
        except (KeyError, FileNotFoundError, ValueError):
            continue

        if isinstance(result, bytes):
            return result
        if hasattr(result, "read"):
            content = result.read()
            if isinstance(content, bytes):
                return content

    return None


def create_manifest(manifest_path: Path, download_root: Path, manifest_factory: ManifestFactory | None) -> Any:
    """创建 PatcherManifest 或测试注入 manifest。"""

    if manifest_factory is not None:
        return manifest_factory(manifest_path, download_root)

    try:
        from riotmanifest import PatcherManifest
    except ImportError as exc:
        raise GameManifestWadError("riotmanifest is required for online GAME manifest WAD extraction") from exc

    return PatcherManifest(file=manifest_path, path=download_root)


def create_extractor(manifest: Any, extractor_factory: WadExtractorFactory | None) -> Any:
    """创建 WADExtractor 或测试注入 extractor。"""

    if extractor_factory is not None:
        return extractor_factory(manifest)

    try:
        from riotmanifest import WADExtractor
    except ImportError as exc:
        raise GameManifestWadError("riotmanifest is required for online GAME manifest WAD extraction") from exc

    return WADExtractor(manifest)


def default_bin_factory(data: bytes) -> Any:
    """创建默认 BIN 解析器。"""

    from league_tools.formats import BIN

    return BIN(data)


def fetch_bytes(url: str) -> bytes:
    """使用 urllib 下载 bytes。"""

    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()
