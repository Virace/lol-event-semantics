"""黄金样例 raw 事件名到渲染文案的端到端回归测试。

fixture 的 required_roles 字段为文档性质，本测试不作断言（role 覆盖由
tests/test_normalized_translation_builder.py 检查）。pending 守卫在全部
pending 样例晋升为活跃样例后空转通过，届时可整体删除该守卫测试。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
from lol_event_semantics.normalized_output import build_normalized_record, build_translation_context
from lol_event_semantics.normalized_translation_model import RenderOptions, TranslationBuildContext
from lol_event_semantics.normalized_translation_renderer import render_translation

FIXTURE = Path(__file__).parent / "fixtures" / "normalized_translation_cases.json"
REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "reference"
DEFAULT_OPTIONS = RenderOptions(locale="zh_CN", mode="default")
MAIN_OPTIONS = RenderOptions(locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"}))


class GoldenTranslationCaseTests(unittest.TestCase):
    """验证黄金样例经完整链路渲染出期望中文文案。"""

    reference: ReferenceIndex
    translation_context: TranslationBuildContext

    @classmethod
    def setUpClass(cls) -> None:
        """加载测试共享的 reference index 与 translation 上下文。"""

        cls.reference = ReferenceIndex.from_reference_collection(REFERENCE_ROOT)
        cls.translation_context = build_translation_context(REFERENCE_ROOT)

    def test_golden_cases_render_expected_zh_cn_text(self) -> None:
        """每条样例的 frame_type 与 default/main 两档渲染文案与 fixture 期望精确一致。"""

        cases = self.load_cases()
        self.assertGreaterEqual(len(cases), 20)

        for case in cases:
            raw = case["raw_event_name"]
            with self.subTest(raw=raw):
                if case.get("pending_frame") is True:
                    self.skipTest(f"frame_type={case['expected_frame_type']} 尚未实现，样例处于 pending 状态")

                normalized = self.build_normalized(raw)
                translation = normalized.get("translation")
                self.assertIsNotNone(
                    translation,
                    msg=(
                        f"未构建 translation frame（期望 frame_type={case['expected_frame_type']}，"
                        f"display={normalized.get('display')}）"
                    ),
                )
                assert isinstance(translation, dict)

                self.assertEqual(
                    translation.get("frame_type"),
                    case["expected_frame_type"],
                    msg="frame_type 与 fixture 期望不匹配",
                )
                default_text = render_translation(translation, DEFAULT_OPTIONS).text
                main_text = render_translation(translation, MAIN_OPTIONS).text
                self.assertEqual(
                    default_text,
                    case["zh_cn_default"],
                    msg=f"default 档文案不匹配（frame_type={translation.get('frame_type')}）",
                )
                self.assertEqual(
                    main_text,
                    case["zh_cn_main"],
                    msg=f"main 档文案不匹配（frame_type={translation.get('frame_type')}）",
                )

    def test_pending_cases_still_unsupported(self) -> None:
        """pending 样例的期望 frame 仍未实现；变红即 frame 已实现，应晋升样例。"""

        for case in self.load_cases():
            if case.get("pending_frame") is not True:
                continue

            raw = case["raw_event_name"]
            with self.subTest(raw=raw):
                translation = self.build_normalized(raw).get("translation")
                frame_type: object = None
                default_text: str | None = None
                main_text: str | None = None
                if isinstance(translation, dict):
                    frame_type = translation.get("frame_type")
                    default_text = render_translation(translation, DEFAULT_OPTIONS).text
                    main_text = render_translation(translation, MAIN_OPTIONS).text

                frame_matched = frame_type == case["expected_frame_type"]
                fully_matched = default_text == case["zh_cn_default"] and main_text == case["zh_cn_main"]
                self.assertFalse(
                    frame_matched or fully_matched,
                    msg=(
                        f"pending 样例已支持（实际 frame_type={frame_type}，"
                        f"期望 frame_type={case['expected_frame_type']}，双档文本全匹配={fully_matched}）："
                        "对应 frame 已实现，请移除该样例的 pending_frame 标记，晋升为活跃黄金样例"
                    ),
                )

    @staticmethod
    def load_cases() -> list[dict[str, object]]:
        """读取黄金样例 fixture。

        Returns:
            fixture 中的样例列表。
        """

        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def build_normalized(self, raw_event_name: str) -> dict[str, object]:
        """按生产链路把 raw 事件名转换为 normalized 记录。

        Args:
            raw_event_name: 原始事件名。

        Returns:
            含 translation（若可构建）的 normalized 记录。
        """

        record = enrich_with_reference(
            normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
            self.reference,
        )
        return build_normalized_record(record, translation_context=self.translation_context)


if __name__ == "__main__":
    unittest.main()
