"""GAME manifest WAD 按需抽取测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lol_event_semantics.game_manifest_wads import (
    GameManifestWadError,
    ManifestBinRequest,
    create_manifest,
    extract_round_from_game_manifest,
    normalize_wad_path,
)

from tests.test_game_wad_events import FakeAudioGroup, FakeBankUnit, FakeBin, FakeStringHash


class GameManifestWadsTests(unittest.TestCase):
    """验证 GAME manifest 到 raw round txt 的最小抽取层。"""

    def test_normalize_wad_path_strips_game_prefix(self) -> None:
        """GAME manifest 中不保留 Game/ 前缀。"""

        self.assertEqual(
            normalize_wad_path("Game/DATA/FINAL/Champions/Annie.wad.client"),
            "DATA/FINAL/Champions/Annie.wad.client",
        )

    def test_extract_round_from_game_manifest_uses_injected_manifest_and_extractor(self) -> None:
        """可通过注入 manifest/extractor 完成无网络单元测试。"""

        fetched: list[str] = []

        def fetch(url: str) -> bytes:
            fetched.append(url)
            return b"manifest-data"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_root = root / "round"
            cache_root = root / ".temp" / "game-manifest"
            result = extract_round_from_game_manifest(
                manifest_url="https://example.test/releases/game.manifest",
                output_root=output_root,
                cache_root=cache_root,
                requests=(
                    ManifestBinRequest(
                        group="champions",
                        entity="Annie",
                        wad_path="Game/DATA/FINAL/Champions/Annie.wad.client",
                        bin_paths=("data/characters/annie/skins/base/annie.bin",),
                    ),
                ),
                fetcher=fetch,
                manifest_factory=lambda manifest_path, download_root: FakeManifest(manifest_path, download_root),
                extractor_factory=lambda manifest: FakeExtractor(
                    {
                        (
                            "DATA/FINAL/Champions/Annie.wad.client",
                            "data/characters/annie/skins/base/annie.bin",
                        ): b"base-bin"
                    }
                ),
                bin_factory=lambda data: FakeBin(
                    [FakeAudioGroup([FakeBankUnit([FakeStringHash(f"Play_vo_Annie_{data.decode('utf-8')}")])])]
                ),
            )

            self.assertEqual(fetched, ["https://example.test/releases/game.manifest"])
            self.assertEqual(result.events, 1)
            self.assertEqual(
                output_root.joinpath("champions", "Annie.txt").read_text(encoding="utf-8"),
                "Play_vo_Annie_base-bin\n",
            )
            self.assertTrue((cache_root / "manifests" / "game.manifest").is_file())

    def test_create_manifest_without_riotmanifest_has_clear_error(self) -> None:
        """没有可选 riotmanifest 依赖时给出明确错误。"""

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("sys.modules", {"riotmanifest": None}):
                with self.assertRaisesRegex(GameManifestWadError, "riotmanifest"):
                    create_manifest(Path(tmp) / "game.manifest", Path(tmp) / "downloads", manifest_factory=None)


class FakeManifest:
    """测试用 manifest。"""

    def __init__(self, manifest_path: Path, download_root: Path) -> None:
        self.manifest_path = manifest_path
        self.download_root = download_root


class FakeExtractor:
    """测试用 WADExtractor。"""

    def __init__(self, files: dict[tuple[str, str], bytes]) -> None:
        self.files = files

    def extract_files(self, wad_file_paths: dict[str, list[str]]) -> dict[str, dict[str, bytes | None]]:
        return {
            wad_path: {
                bin_path: self.files.get((wad_path, bin_path))
                for bin_path in bin_paths
            }
            for wad_path, bin_paths in wad_file_paths.items()
        }


if __name__ == "__main__":
    unittest.main()
