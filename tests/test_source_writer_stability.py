"""数据摄入写入器的字节稳定性回归。"""

from lol_event_semantics.raw_update import update_raw_events
from lol_event_semantics.reference_collection import read_yaml, write_yaml


def test_raw_update_preserves_unchanged_bytes_and_existing_newlines(tmp_path):
    """新增一实体事件不能重写其他文件，也不能把已有 CRLF 改成 LF。"""

    root = tmp_path / "raw/champions"
    incoming = tmp_path / "incoming/champions"
    root.mkdir(parents=True)
    incoming.mkdir(parents=True)
    untouched = b"Play_vo_Z\r\nPlay_vo_A\r\n"
    (root / "1.txt").write_bytes(untouched)
    (root / "2.txt").write_bytes(b"Play_vo_B\r\n")
    (incoming / "2.txt").write_bytes(b"Play_vo_C\n")
    update_raw_events(root.parent, incoming.parent)
    assert (root / "1.txt").read_bytes() == untouched
    assert (root / "2.txt").read_bytes() == b"Play_vo_B\r\nPlay_vo_C\r\n"


def test_reference_writer_removes_only_semantically_empty_trailing_spaces(tmp_path):
    """长行折叠产生的空格可清理，真正字符串内的空格与换行不可丢。"""

    target = tmp_path / "reference.yaml"
    data = {
        "hash": "sha256:" + "a" * 64,
        "description": "Long description " * 40,
        "literal": "line one  \nline two \n",
        "quoted": " keep trailing space ",
    }
    write_yaml(target, data)
    assert read_yaml(target) == data
    first = target.read_bytes()
    write_yaml(target, data)
    assert target.read_bytes() == first
