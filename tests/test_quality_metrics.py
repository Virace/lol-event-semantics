"""normalized 质量指标测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.quality_metrics import collect_quality_metrics


def build_record(
    raw: str,
    alias: str,
    kind: str,
    confidence: str,
    zh_cn_short: str,
    *,
    with_translation: bool = False,
) -> dict[str, object]:
    """构造一条最小 normalized 记录。

    Args:
        raw: 原始事件名。
        alias: 实体别名。
        kind: 实体类型。
        confidence: 语义置信度。
        zh_cn_short: 短展示文案。
        with_translation: 是否附带顶层 translation 对象。

    Returns:
        可写入 JSONL 的记录 dict。
    """

    record: dict[str, object] = {
        "raw_event_name": raw,
        "entity": {"alias": alias, "kind": kind},
        "display": {"zh_cn_short": zh_cn_short},
        "semantic": {"confidence": confidence},
    }
    if with_translation:
        record["translation"] = {"template_key": "demo"}
    return record


class QualityMetricsTests(unittest.TestCase):
    """验证质量指标统计的各项口径。"""

    def setUp(self) -> None:
        """构造合成 normalized 输出与 validation 报告。"""

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.normalized_root = root / "normalized"
        champions_dir = self.normalized_root / "by-entity" / "champions"
        systems_dir = self.normalized_root / "by-entity" / "map_or_systems"
        champions_dir.mkdir(parents=True)
        systems_dir.mkdir(parents=True)

        (self.normalized_root / "manifest.json").write_text(
            json.dumps({"record_count": 5, "entity_count": 2, "schema_version": 1}),
            encoding="utf-8",
        )

        champion_records = [
            # 有 translation、无残留英文（alias 移除后只剩中文）、非兜底。
            build_record("Play_sfx_Aatrox_Q_OnCast", "Aatrox", "champion", "high", "Aatrox普攻施放", with_translation=True),
            # 纯兜底命中：精确等于 "{alias}事件"。
            build_record("Play_sfx_Aatrox_misc", "Aatrox", "champion", "medium", "Aatrox事件"),
            # 残留英文命中：移除 alias 后仍含拉丁字母。
            build_record("Play_sfx_Aatrox_Q_hit", "Aatrox", "champion", "low", "Aatrox Q OnCast"),
            # alias 大小写不敏感移除后无残留；兜底匹配大小写敏感故不命中。
            build_record("Play_sfx_Aatrox_idle", "Aatrox", "champion", "high", "aatrox音效事件"),
        ]
        system_records = [
            # 非 champion 实体的 translation 覆盖样本。
            build_record("Play_amb_SRU_wind", "SRU", "map_or_system", "high", "召唤师峡谷环境音", with_translation=True),
        ]

        (champions_dir / "Aatrox.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in champion_records),
            encoding="utf-8",
        )
        (systems_dir / "SRU.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in system_records),
            encoding="utf-8",
        )

        self.validation_json = root / "normalized-validation.json"
        self.validation_json.write_text(
            json.dumps(
                {
                    "issues": [
                        {"code": "translation_missing_for_supported_frame", "severity": "warning"},
                        {"code": "translation_missing_for_supported_frame", "severity": "warning"},
                        {"code": "broken_record", "severity": "error"},
                    ],
                    "summary": {"entity_count": 2, "error_count": 1, "record_count": 5, "warning_count": 2},
                }
            ),
            encoding="utf-8",
        )

    def collect(self) -> dict[str, object]:
        """对合成数据执行指标统计。"""

        return collect_quality_metrics(self.normalized_root, self.validation_json)

    def test_record_count_cross_check(self) -> None:
        """实扫记录数与 manifest 计数并存。"""

        metrics = self.collect()

        self.assertEqual(metrics["record_count"], 5)
        self.assertEqual(metrics["manifest_record_count"], 5)

    def test_confidence_distribution(self) -> None:
        """confidence 按值计数。"""

        metrics = self.collect()

        self.assertEqual(metrics["confidence_counts"], {"high": 3, "low": 1, "medium": 1})

    def test_translation_coverage(self) -> None:
        """translation 覆盖总数与按实体类型分组。"""

        metrics = self.collect()

        translation = metrics["translation"]
        self.assertEqual(translation["record_count"], 2)
        self.assertEqual(translation["by_entity_kind"], {"champion": 1, "map_or_system": 1})

    def test_fallback_label_metrics(self) -> None:
        """纯兜底文案精确匹配三种模板之一。"""

        metrics = self.collect()

        fallback = metrics["fallback_label"]
        self.assertEqual(fallback["record_count"], 1)
        self.assertAlmostEqual(fallback["ratio"], 0.2)

    def test_residual_english_metrics(self) -> None:
        """移除 alias 后仍含拉丁字母的记录计数。"""

        metrics = self.collect()

        residual = metrics["residual_english"]
        self.assertEqual(residual["record_count"], 1)
        self.assertAlmostEqual(residual["ratio"], 0.2)

    def test_validation_metrics(self) -> None:
        """validation 报告的 error/warning 与按 code 分组计数。"""

        metrics = self.collect()

        validation = metrics["validation"]
        self.assertEqual(validation["error_count"], 1)
        self.assertEqual(validation["warning_count"], 2)
        self.assertEqual(
            validation["issue_counts_by_code"],
            {"broken_record": 1, "translation_missing_for_supported_frame": 2},
        )

    def test_result_is_json_serializable(self) -> None:
        """指标结果可直接 JSON 序列化。"""

        metrics = self.collect()

        json.dumps(metrics, ensure_ascii=False, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
