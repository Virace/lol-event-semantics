"""GAME manifest 选择逻辑测试。"""

from __future__ import annotations

import unittest

from lol_event_semantics.game_manifest import (
    GameManifest,
    GameManifestError,
    build_version_set_url,
    fetch_game_manifest,
    main_version,
    select_game_manifest,
)


class GameManifestTests(unittest.TestCase):
    """验证 GAME manifest 数据结构和版本匹配。"""

    def test_selects_manifest_by_main_version(self) -> None:
        """只按 major.minor 匹配 GAME manifest。"""

        payload = {
            "releases": [
                {
                    "id": "16.12.999",
                    "labels": {"game": "16.12.999"},
                    "download": {"url": "https://example.test/16.12.manifest"},
                },
                {
                    "id": "16.13.1",
                    "labels": {"game": "16.13.1"},
                    "download": {"url": "https://example.test/16.13.manifest"},
                },
            ]
        }

        manifest = select_game_manifest(payload, "16.12.1")

        self.assertEqual(manifest.id, "16.12.999")
        self.assertEqual(manifest.main_version, "16.12")
        self.assertEqual(manifest.url, "https://example.test/16.12.manifest")

    def test_rejects_lcu_manifest_shape(self) -> None:
        """误传 LCU manifest 时给出明确错误。"""

        payload = {
            "releases": [
                {
                    "id": "lcu-16.12",
                    "labels": {"lcu": "16.12"},
                    "download": {"url": "https://example.test/lcu.manifest"},
                }
            ]
        }

        with self.assertRaisesRegex(GameManifestError, "GAME manifest"):
            select_game_manifest(payload, "16.12.1")

    def test_fails_when_main_version_is_missing(self) -> None:
        """没有主版本匹配时失败，不回退到其他版本。"""

        payload = {
            "releases": [
                {
                    "id": "16.13.1",
                    "labels": {"game": "16.13.1"},
                    "download": {"url": "https://example.test/16.13.manifest"},
                }
            ]
        }

        with self.assertRaisesRegex(GameManifestError, "16.12"):
            select_game_manifest(payload, "16.12.1")

    def test_main_version_extracts_first_two_segments(self) -> None:
        """主版本只取前两段。"""

        self.assertEqual(main_version("16.12.999"), "16.12")

    def test_game_manifest_to_metadata(self) -> None:
        """manifest 可转为 raw update metadata 字段。"""

        manifest = GameManifest(id="abc", version="16.12.3", url="https://example.test/manifest")

        self.assertEqual(
            manifest.to_metadata(),
            {
                "game_manifest_id": "abc",
                "game_manifest_url": "https://example.test/manifest",
                "manifest_major_version": "16.12",
            },
        )

    def test_fetch_game_manifest_uses_official_version_set_payload(self) -> None:
        """在线 manifest 获取只消费官方 version-set payload。"""

        calls: list[str] = []

        def fetch(url: str) -> bytes:
            calls.append(url)
            return (
                b'{"releases":['
                b'{"id":"16.12.999","labels":{"game":"16.12.999"},"download":{"url":"https://example.test/game.manifest"}},'
                b'{"id":"lcu","labels":{"lcu":"16.12"},"download":{"url":"https://example.test/lcu.manifest"}}'
                b"]}"
            )

        manifest = fetch_game_manifest(
            game_version="16.12.1",
            region="NA",
            base_url_template="https://sieve.example.test/api/{region}?q[platform]=windows",
            fetcher=fetch,
        )

        self.assertEqual(calls, ["https://sieve.example.test/api/NA?q[platform]=windows"])
        self.assertEqual(manifest.id, "16.12.999")
        self.assertEqual(manifest.url, "https://example.test/game.manifest")

    def test_build_version_set_url_escapes_region(self) -> None:
        """region 作为 URL path 参数时会转义。"""

        self.assertEqual(
            build_version_set_url("https://example.test/{region}", "NA 1"),
            "https://example.test/NA%201",
        )


if __name__ == "__main__":
    unittest.main()
