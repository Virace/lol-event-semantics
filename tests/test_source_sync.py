"""source_sync 模块测试：reference 读取、路径解析与 RST 字典瘦身。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.source_sync import (
    champion_aliases,
    champion_numeric_id,
    find_ci,
    trim_rst_hashes,
)
from lol_event_semantics.wad_source import main_version
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
        total, kept = trim_rst_hashes(
            "\n".join(lines).encode("utf-8"), stringtable_dir, out_path
        )
        self.assertEqual((total, kept), (3, 2))
        content = out_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(content, lines[:2])


class MainVersionTest(unittest.TestCase):
    """main_version 的主版本截取。"""

    def test_truncates_to_major_minor(self) -> None:
        """只保留 major.minor，两段以内原样返回。"""

        self.assertEqual(main_version("16.14.7945912"), "16.14")
        self.assertEqual(main_version("16.14"), "16.14")


if __name__ == "__main__":
    unittest.main()
