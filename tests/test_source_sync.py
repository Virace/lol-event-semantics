"""source_sync 模块测试：reference 读取、路径解析与 RST 字典瘦身。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lol_event_semantics.source_sync import (
    champion_aliases,
    champion_numeric_id,
    find_ci,
    trim_rst_hashes,
)
from lol_event_semantics.wad_source import WadSourceError, main_version, resolve_pair
from tests.test_rst_stringtable import build_rst


class ReferenceLookupTest(unittest.TestCase):
    """champion_aliases / champion_numeric_id 对 reference 分片的读取。"""

    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.reference_root = Path(root.name)
        champions = self.reference_root / "champions"
        champions.mkdir(parents=True)
        (champions / "Ahri.yaml").write_text("alias: Ahri\nid: '103'\n", encoding="utf-8")
        (champions / "Locke.yaml").write_text("alias: Locke\nid: 805\n", encoding="utf-8")
        (champions / "None.yaml").write_text("alias: None\nid: '-1'\n", encoding="utf-8")

    def test_aliases_exclude_placeholder(self) -> None:
        """枚举按文件名排序且排除 None 占位分片。"""

        self.assertEqual(champion_aliases(self.reference_root), ["Ahri", "Locke"])

    def test_numeric_id_reads_quoted_and_bare(self) -> None:
        """id 字段带引号与裸数字都能取到。"""

        self.assertEqual(champion_numeric_id(self.reference_root, "Ahri"), "103")
        self.assertEqual(champion_numeric_id(self.reference_root, "Locke"), "805")

    def test_numeric_id_missing_or_invalid(self) -> None:
        """缺文件或 id 非数字返回 None。"""

        self.assertIsNone(champion_numeric_id(self.reference_root, "Nobody"))
        self.assertIsNone(champion_numeric_id(self.reference_root, "None"))


class FindCiTest(unittest.TestCase):
    """find_ci 的路径解析行为。"""

    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.base = Path(root.name)
        target = self.base / "global" / "zh_CN" / "v1"
        target.mkdir(parents=True)
        (target / "maps.json").write_text("{}", encoding="utf-8")

    def test_resolves_existing_path(self) -> None:
        """逐段命中返回实际路径。"""

        found = find_ci(self.base, "global/zh_CN/v1/maps.json")
        self.assertIsNotNone(found)
        self.assertTrue(found.is_file())

    def test_missing_path_returns_none(self) -> None:
        """任一段找不到返回 None。"""

        self.assertIsNone(find_ci(self.base, "global/ja_JP/v1/maps.json"))
        self.assertIsNone(find_ci(self.base, "global/zh_CN/v1/queues.json"))


class TrimRstHashesTest(unittest.TestCase):
    """trim_rst_hashes 按本地表交集瘦身与截断掩码。"""

    def test_keeps_only_local_hashes(self) -> None:
        """完整 64 位 hash 按表位宽截断后交集命中才保留，坏行忽略。"""

        table_dir = tempfile.TemporaryDirectory()
        self.addCleanup(table_dir.cleanup)
        stringtable_dir = Path(table_dir.name)
        # (1<<38)-1 条目保证位宽探测锁定 38（同 rst_stringtable 测试手法）。
        payload = build_rst({5: "第一滴血!", (1 << 38) - 1: "x"}, hash_bits=38)
        (stringtable_dir / "Global.zh_CN-lol.stringtable").write_bytes(payload)

        lines = [
            f"{5:016x} game_ui_first_blood",
            f"{(1 << 40) | 5:016x} same_low38_bits_variant",
            f"{7:016x} not_in_local_table",
            "deadbeef",
        ]
        out_path = stringtable_dir / "out" / "trimmed.txt"
        total, kept = trim_rst_hashes("\n".join(lines).encode("utf-8"), stringtable_dir, out_path)
        self.assertEqual((total, kept), (3, 2))
        content = out_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(content, lines[:2])


class MainVersionTest(unittest.TestCase):
    """main_version 的主版本截取。"""

    def test_truncates_to_major_minor(self) -> None:
        """只保留 major.minor，两段以内原样返回。"""

        self.assertEqual(main_version("16.14.7945912"), "16.14")
        self.assertEqual(main_version("16.14"), "16.14")


class ManifestPairTest(unittest.TestCase):
    """真实 LCU 版本资源与 GAME 同补丁配对的离线回归。"""

    def setUp(self) -> None:
        """隔离网络边界，复用已核验的 Riot 16.20 版本证据。"""

        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.cache = Path(root.name)
        # 取自 888A240B868B4030.manifest 的 LeagueClient.exe，偏移 30769502。
        # 2026-10-18 Windows VersionInfo 独立读取的 ProductVersion 为 16.20.827.198。
        self.payload = bytes.fromhex(
            "500072006f006400750063007400560065007200730069006f006e000000"
            "310036002e00320030002e003800320037002e003100390038000000"
        )
        client = SimpleNamespace(
            name="LeagueClient.exe",
            size=len(self.payload),
            chunks=[],
            download_chunks=Mock(return_value=self.payload),
        )
        self.resolver = SimpleNamespace(
            get_lcu_manifest=Mock(
                return_value=SimpleNamespace(
                    url="https://lol.secure.dyn.riotcdn.net/channels/public/releases/888A240B868B4030.manifest",
                    manifest_id="888A240B868B4030",
                )
            ),
            list_game_candidates=Mock(
                return_value=[
                    SimpleNamespace(
                        version=SimpleNamespace(normalized_build="16.20.8248524"),
                        url="https://lol.secure.dyn.riotcdn.net/channels/public/releases/CB4BCB7E1F5C1831.manifest",
                        manifest_id="CB4BCB7E1F5C1831",
                    )
                ]
            ),
        )
        self.backend = SimpleNamespace(
            LeagueManifestResolver=Mock(return_value=self.resolver),
            PatcherManifest=Mock(return_value=SimpleNamespace(files={"LeagueClient.exe": client})),
        )

    def test_pairs_three_digit_lcu_revision(self) -> None:
        """接受真实三位修订号，并保留 manifest 身份与原始 LCU 版本。"""

        with patch("lol_event_semantics.wad_source._riotmanifest", return_value=self.backend):
            pair = resolve_pair(attempts=1, work_dir=self.cache)
        self.assertEqual(pair.game_version, "16.20.8248524")
        self.assertEqual(pair.lcu_version, "16.20.827.198")
        self.assertEqual(pair.game_id, "CB4BCB7E1F5C1831")
        self.assertEqual(pair.lcu_id, "888A240B868B4030")
        carriers = list(self.cache.rglob("LeagueClient.exe"))
        self.assertEqual(len(carriers), 1)
        self.assertEqual(carriers[0].read_bytes(), self.payload)

    def test_rejects_other_patch_series(self) -> None:
        """LCU 修订号兼容不能让其他补丁的 GAME 通过同轮来源校验。"""

        self.resolver.list_game_candidates.return_value[0].version.normalized_build = "16.14.7945912"
        with (
            patch("lol_event_semantics.wad_source._riotmanifest", return_value=self.backend),
            self.assertRaisesRegex(WadSourceError, "未成对"),
        ):
            resolve_pair(attempts=1, work_dir=self.cache)


if __name__ == "__main__":
    unittest.main()
