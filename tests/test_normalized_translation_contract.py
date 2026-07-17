"""normalized translation contract fixture 测试。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "normalized_translation_cases.json"
REQUIRED_CASE_KEYS = {
    "raw_event_name",
    "expected_frame_type",
    "required_roles",
    "zh_cn_default",
    "zh_cn_main",
    "notes",
}


class NormalizedTranslationContractTests(unittest.TestCase):
    """验证 translation 代表样例 contract。"""

    def test_fixture_cases_have_required_shape(self) -> None:
        """每条样例都提供后续 builder/renderer 所需字段。"""

        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

        self.assertGreaterEqual(len(cases), 20)
        for case in cases:
            with self.subTest(raw=case.get("raw_event_name")):
                self.assertTrue(REQUIRED_CASE_KEYS.issubset(case))
                self.assertIsInstance(case["required_roles"], list)
                self.assertTrue(case["raw_event_name"])
                self.assertTrue(case["expected_frame_type"])
                self.assertTrue(case["zh_cn_default"])
                self.assertTrue(case["zh_cn_main"])
                self.assertNotIn("Play_", case["zh_cn_default"])
                self.assertNotIn("OnBuff", case["zh_cn_default"])


if __name__ == "__main__":
    unittest.main()
