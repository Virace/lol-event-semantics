"""rst_stringtable 模块测试：合成 RST 文件的解析与位宽探测。"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.rst_stringtable import RST_MAGIC, load


def build_rst(strings: dict[int, str], hash_bits: int, version: int = 5) -> bytes:
    """按给定 hash 位宽拼装一份合成 RST 文件字节。

    Args:
        strings: key hash 到字符串原文的映射。
        hash_bits: 条目 uint64 中 hash 所占低位位数。
        version: 写入文件头的版本号。

    Returns:
        完整 RST 文件字节。
    """

    blob = b""
    values = []
    for key_hash, text in strings.items():
        values.append((len(blob) << hash_bits) | key_hash)
        blob += text.encode("utf-8") + b"\x00"
    header = RST_MAGIC + bytes([version]) + struct.pack("<I", len(values))
    return header + struct.pack(f"<{len(values)}Q", *values) + blob


class LoadTest(unittest.TestCase):
    """load 的解析行为。"""

    def write_temp(self, payload: bytes) -> Path:
        """把字节写入临时文件并返回路径。"""

        handle = tempfile.NamedTemporaryFile(suffix=".stringtable", delete=False)
        self.addCleanup(Path(handle.name).unlink)
        handle.write(payload)
        handle.close()
        return Path(handle.name)

    def test_parses_entries_with_cjk(self) -> None:
        """38 位表可解析，含中文与同 hash 跨语言对齐前提（hash 保序）。"""

        strings = {1: "第一滴血!", 2: "FIRST BLOOD!", (1 << 38) - 1: "厄塔汗"}
        table = load(self.write_temp(build_rst(strings, hash_bits=38)))
        self.assertEqual(table.version, 5)
        self.assertEqual(table.hash_bits, 38)
        self.assertEqual(table.entries, strings)

    def test_detects_wider_hash_bits(self) -> None:
        """40 位表：38 位解读会使偏移越界，应回退探测为 40。

        构造总长超过字符串区 1/4 的内容，保证 38 位（偏移×4）必然越界。
        """

        strings = {h: f"entry-{h}-{'x' * 40}" for h in range(1, 30)}
        table = load(self.write_temp(build_rst(strings, hash_bits=40)))
        self.assertEqual(table.hash_bits, 40)
        self.assertEqual(table.entries, strings)

    def test_rejects_non_rst_file(self) -> None:
        """魔数不符时报 ValueError。"""

        with self.assertRaises(ValueError):
            load(self.write_temp(b"PNG\x05junkjunk"))


if __name__ == "__main__":
    unittest.main()
