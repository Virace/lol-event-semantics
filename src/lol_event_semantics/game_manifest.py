"""GAME manifest 版本选择。"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


GAME_VERSION_SET_URL = "https://sieve.services.riotcdn.net/api/v1/products/lol/version-sets/{region}?q[platform]=windows"

type Fetcher = Callable[[str], bytes]


class GameManifestError(ValueError):
    """GAME manifest 解析或选择失败。"""


@dataclass(frozen=True, slots=True)
class GameManifest:
    """选中的 GAME manifest。"""

    id: str
    version: str
    url: str

    @property
    def main_version(self) -> str:
        """返回 major.minor 主版本。"""

        return main_version(self.version)

    def to_metadata(self) -> dict[str, str]:
        """转换为 raw update metadata 字段。"""

        return {
            "game_manifest_id": self.id,
            "game_manifest_url": self.url,
            "manifest_major_version": self.main_version,
        }


def select_game_manifest(payload: dict[str, Any], game_version: str) -> GameManifest:
    """从官方 version-set payload 中选择匹配 GAME 主版本的 manifest。"""

    target_main = main_version(game_version)
    manifests = parse_game_manifests(payload)

    if not manifests:
        raise GameManifestError("Payload does not contain a usable GAME manifest release")

    for manifest in manifests:
        if manifest.main_version == target_main:
            return manifest

    raise GameManifestError(f"No GAME manifest matched main version {target_main}")


def fetch_game_manifest(
    *,
    game_version: str,
    region: str = "NA",
    base_url_template: str = GAME_VERSION_SET_URL,
    fetcher: Fetcher | None = None,
) -> GameManifest:
    """从 Riot 官方 version-set 端点获取并选择 GAME manifest。"""

    fetch = fetcher or fetch_bytes
    url = build_version_set_url(base_url_template, region)
    payload = json.loads(fetch(url).decode("utf-8"))

    if not isinstance(payload, dict):
        raise GameManifestError("GAME version-set response must be a JSON object")

    return select_game_manifest(payload, game_version)


def build_version_set_url(template: str, region: str) -> str:
    """构造 Riot 官方 version-set URL。"""

    encoded_region = urllib.parse.quote(region, safe="")
    return template.format(region=encoded_region)


def fetch_bytes(url: str) -> bytes:
    """使用 urllib 下载 bytes。"""

    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def parse_game_manifests(payload: dict[str, Any]) -> list[GameManifest]:
    """解析 version-set payload 中的 GAME manifest 候选。"""

    releases = payload.get("releases")

    if not isinstance(releases, list):
        raise GameManifestError("GAME version-set payload must contain releases list")

    manifests: list[GameManifest] = []
    for release in releases:
        if not isinstance(release, dict):
            continue

        labels = release.get("labels")
        game_version = labels.get("game") if isinstance(labels, dict) else None

        if not isinstance(game_version, str):
            continue

        url = manifest_url_from_release(release)

        if url is None:
            continue

        release_id = release.get("id")
        manifests.append(
            GameManifest(
                id=str(release_id or game_version),
                version=game_version,
                url=url,
            )
        )

    return manifests


def manifest_url_from_release(release: dict[str, Any]) -> str | None:
    """从 release 行中读取 manifest URL。"""

    download = release.get("download")

    if isinstance(download, dict) and isinstance(download.get("url"), str):
        return str(download["url"])

    for key in ["url", "manifest_url", "manifestUrl"]:
        if isinstance(release.get(key), str):
            return str(release[key])

    return None


def main_version(version: str) -> str:
    """提取版本前两段。"""

    parts = str(version).split(".")

    if len(parts) < 2:
        raise GameManifestError(f"Version must contain at least major.minor: {version}")

    return ".".join(parts[:2])
