"""RST（Riot String Table）客户端字符串表读取。

客户端 ``Localized/`` 目录的 ``*.stringtable`` 文件为 RST 二进制格式：
``RST`` 魔数 + 1 字节版本 + uint32 条目数，随后每条目一个小端 uint64
（低位为 key 的截断 hash，高位为字符串区内偏移），字符串区紧随条目表、
为 NUL 结尾 UTF-8。key 原文不落盘，但同一 key 在各语言文件中 hash 相同，
故跨语言文件按同 hash 对齐即可得中英对照。

hash 位宽随客户端版本漂移（历史上有 40/39/38 位），本模块按「偏移全部
落在文件内且抽样字符串可解码」自动探测，不硬编码单一位宽。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

RST_MAGIC = b"RST"
# 位宽候选按偏移从大到小（位宽小→偏移大）依序试探：真实位宽是能铺满
# 字符串区的最小位宽，位宽猜大时偏移减半、文件尾部字符串无法引用，
# 位宽猜小时偏移翻倍、必然越界，故取首个通过校验的候选。
HASH_BITS_CANDIDATES = (38, 39, 40)
# 抽样解码条数：足够暴露错位（错位常落进多字节字符中间），又不必全量解码。
DECODE_SAMPLE_SIZE = 256


@dataclass(slots=True)
class RstTable:
    """一份已解析的 RST 字符串表。

    Attributes:
        version: 文件头声明的 RST 版本号。
        hash_bits: 探测得到的 key hash 位宽。
        entries: key hash 到字符串原文的映射。
    """

    version: int
    hash_bits: int
    entries: dict[int, str]


def _string_at(data: bytes, offset: int) -> str | None:
    """解码 ``offset`` 处的 NUL 结尾 UTF-8 字符串，非法时返回 None。"""

    end = data.find(b"\x00", offset)
    if end == -1:
        return None
    try:
        return data[offset:end].decode("utf-8")
    except UnicodeDecodeError:
        return None


def _detect_hash_bits(values: tuple[int, ...], data: bytes, data_start: int) -> int:
    """探测条目 uint64 的 hash 位宽。

    Args:
        values: 条目表的原始 uint64 序列。
        data: 完整文件字节。
        data_start: 字符串区起始偏移。

    Returns:
        首个使全部偏移落在文件内、且抽样字符串均可解码的候选位宽。

    Raises:
        ValueError: 所有候选位宽均校验失败（文件损坏或格式未知）。
    """

    for bits in HASH_BITS_CANDIDATES:
        offsets = [value >> bits for value in values]
        if offsets and data_start + max(offsets) >= len(data):
            continue
        sample = offsets[:DECODE_SAMPLE_SIZE]
        if all(_string_at(data, data_start + offset) is not None for offset in sample):
            return bits
    raise ValueError("无法探测 RST hash 位宽：所有候选均校验失败")


def load(path: Path) -> RstTable:
    """读取一份 ``*.stringtable`` 文件。

    Args:
        path: RST 文件路径。

    Returns:
        解析后的 :class:`RstTable`；条目字符串解码失败的个别条目会被跳过。

    Raises:
        ValueError: 文件头魔数不是 ``RST``。
    """

    data = path.read_bytes()
    if data[:3] != RST_MAGIC:
        raise ValueError(f"非 RST 文件：{path}")
    version = data[3]
    count = struct.unpack_from("<I", data, 4)[0]
    values = struct.unpack_from(f"<{count}Q", data, 8)
    data_start = 8 + count * 8

    hash_bits = _detect_hash_bits(values, data, data_start)
    mask = (1 << hash_bits) - 1
    entries: dict[int, str] = {}
    for value in values:
        text = _string_at(data, data_start + (value >> hash_bits))
        if text is not None:
            entries[value & mask] = text
    return RstTable(version=version, hash_bits=hash_bits, entries=entries)
