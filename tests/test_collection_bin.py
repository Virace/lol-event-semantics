"""严格采集的诊断传播合同；解析兼容性由真实 WAD/BIN 验证。"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import league_tools
import pytest
from loguru import logger

from lol_event_semantics.wad_source import WadSourceError, extract_event_names


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    """测试结束后恢复调用方的库日志开关，不改动现有 sink。"""

    enabled = league_tools.is_logging_enabled()
    try:
        yield
    finally:
        (league_tools.enable_logging if enabled else league_tools.disable_logging)()


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize(
    ("level", "diagnostic"),
    [
        ("WARNING", "未知元素标记: 0x12345678"),
        ("ERROR", "解析第 1/2 个BANK_UNIT时出错: 无效的事件数据"),
    ],
)
def test_strict_parsing_rejects_reported_partial_results(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, level: str, diagnostic: str
) -> None:
    """复现第三方报告诊断后返回部分结果的边界，禁止将它标记为完整采集。"""

    (league_tools.enable_logging if enabled else league_tools.disable_logging)()

    def parse(payload: bytes) -> SimpleNamespace:
        """替代 BIN 解析边界，只模拟其已核实的日志及部分返回行为。"""

        assert league_tools.is_logging_enabled()
        logger.patch(lambda record: record.update(name="league_tools.formats.bin.parser")).log(level, diagnostic)
        event = SimpleNamespace(string="Play_vo_Annie_Move2DStandard")
        return SimpleNamespace(data=[SimpleNamespace(bank_units=[SimpleNamespace(events=[event])])])

    monkeypatch.setattr(league_tools, "BIN", parse)
    with pytest.raises(WadSourceError, match="拒绝不完整结果"):
        extract_event_names(b"PROP", strict=True)
    assert league_tools.is_logging_enabled() is enabled


def test_invalid_signature_is_rejected_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """第三方会对错误文件头静默返回空结果，严格入口应在解析前拒绝。"""

    def parse(payload: bytes) -> None:
        """若无效文件头到达解析器则表明保护失效。"""

        pytest.fail("无效 PROP 文件头不应到达底层解析器")

    monkeypatch.setattr(league_tools, "BIN", parse)
    with pytest.raises(WadSourceError, match="文件头无效"):
        extract_event_names(b"not a BIN", strict=True)


def test_valid_empty_result_is_allowed_and_logging_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """空事件本身不是解析失败，错误依据是文件头、异常和解析器诊断。"""

    league_tools.disable_logging()
    monkeypatch.setattr(league_tools, "BIN", lambda payload: SimpleNamespace(data=[]))
    assert extract_event_names(b"PROP", strict=True) == []
    assert not league_tools.is_logging_enabled()


def test_parser_exception_is_wrapped_and_logging_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """底层直接抛出的失败也必须保留异常链并恢复原日志状态。"""

    league_tools.disable_logging()

    def parse(payload: bytes) -> None:
        """给出解析边界允许抛出的明确失败。"""

        raise ValueError("invalid bank unit")

    monkeypatch.setattr(league_tools, "BIN", parse)
    with pytest.raises(WadSourceError, match="BIN 解析失败") as failure:
        extract_event_names(b"PROP", strict=True)
    assert isinstance(failure.value.__cause__, ValueError)
    assert not league_tools.is_logging_enabled()


def test_default_mode_retains_existing_parser_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    """旧调用不启用严格门禁，避免同步改变旧维护入口的合同。"""

    league_tools.disable_logging()
    monkeypatch.setattr(league_tools, "BIN", lambda payload: SimpleNamespace(data=[]))
    assert extract_event_names(b"legacy input") == []
    assert not league_tools.is_logging_enabled()
