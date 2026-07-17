"""normalized 质量指标测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.quality_metrics import collect_quality_metrics, compare_quality_metrics


def build_record(
    raw: str,
    alias: str,
    kind: str,
    confidence: str,
    zh_cn_short: str,
    *,
    with_translation: bool = False,
    tier: int | None = None,
) -> dict[str, object]:
    """构造一条最小 normalized 记录。

    Args:
        raw: 原始事件名。
        alias: 实体别名。
        kind: 实体类型。
        confidence: 语义置信度。
        zh_cn_short: 短展示文案。
        with_translation: 是否附带顶层 translation 对象。
        tier: display.tier 分层值；None 表示不写入该字段（模拟旧产物）。

    Returns:
        可写入 JSONL 的记录 dict。
    """

    display: dict[str, object] = {"zh_cn_short": zh_cn_short}
    if tier is not None:
        display["tier"] = tier

    record: dict[str, object] = {
        "raw_event_name": raw,
        "entity": {"alias": alias, "kind": kind},
        "display": display,
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

    def test_display_tier_distribution_defaults_to_unknown_without_tier_field(self) -> None:
        """本 fixture 记录均未写 display.tier，全部归入 unknown 桶。"""

        metrics = self.collect()

        self.assertEqual(metrics["display_tier_counts"], {"unknown": 5})


class QualityMetricsDisplayTierTests(unittest.TestCase):
    """验证 display.tier 分布统计。"""

    def setUp(self) -> None:
        """构造覆盖 tier1/tier2/tier3 与缺失 tier 的合成 normalized 输出。"""

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)

        self.normalized_root = root / "normalized"
        champions_dir = self.normalized_root / "by-entity" / "champions"
        champions_dir.mkdir(parents=True)

        records = [
            build_record("Play_vo_Aatrox_Kill3DAatrox", "Aatrox", "champion", "high", "击杀语音", tier=1),
            build_record("Play_vo_Aatrox_FirstEncounter3DAkali", "Aatrox", "champion", "high", "遭遇语音", tier=1),
            build_record("Mute_sfx_Aatrox_AatroxWRevive_buffcast", "Aatrox", "champion", "medium", "W技能音效", tier=2),
            build_record("Play_sfx_Aatrox_AatroxBasicAttack2_OnCast", "Aatrox", "champion", "medium", "", tier=3),
            build_record("Play_sfx_Aatrox_legacy_no_tier", "Aatrox", "champion", "low", "Aatrox事件"),
        ]
        (champions_dir / "Aatrox.jsonl").write_text(
            "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
            encoding="utf-8",
        )

        self.validation_json = root / "normalized-validation.json"
        self.validation_json.write_text(
            json.dumps({"issues": [], "summary": {"error_count": 0, "warning_count": 0}}),
            encoding="utf-8",
        )

    def test_tier_counts_split_by_value_and_unknown_bucket(self) -> None:
        """tier1/2/3 分别计数，缺失字段归入 unknown，不静默吞掉。"""

        metrics = collect_quality_metrics(self.normalized_root, self.validation_json)

        self.assertEqual(metrics["display_tier_counts"], {"1": 2, "2": 1, "3": 1, "unknown": 1})


def build_metrics(
    *,
    record_count: int = 100,
    error_count: int = 0,
    fallback_ratio: float = 0.2,
    residual_ratio: float = 0.5,
    issue_counts: dict[str, int] | None = None,
) -> dict[str, object]:
    """构造棘轮比较用的最小指标 dict。

    Args:
        record_count: 记录总数。
        error_count: validation error 计数。
        fallback_ratio: 纯兜底比例。
        residual_ratio: 残留英文比例。
        issue_counts: 按 code 分组的 issue 计数。

    Returns:
        与 collect_quality_metrics 输出同构的指标 dict。
    """

    counts = dict(issue_counts or {})
    return {
        "record_count": record_count,
        "fallback_label": {"record_count": round(record_count * fallback_ratio), "ratio": fallback_ratio},
        "residual_english": {"record_count": round(record_count * residual_ratio), "ratio": residual_ratio},
        "validation": {
            "error_count": error_count,
            "warning_count": sum(counts.values()),
            "issue_counts_by_code": counts,
        },
    }


class CompareQualityMetricsTests(unittest.TestCase):
    """验证棘轮比较的判定口径。"""

    @staticmethod
    def verdicts(result: dict[str, object]) -> dict[str, str]:
        """把逐项 checks 折叠成 metric -> verdict 映射。"""

        return {check["metric"]: check["verdict"] for check in result["checks"]}

    def test_equal_metrics_pass(self) -> None:
        """指标持平时整体通过，受门禁项全部 ok。"""

        metrics = build_metrics(issue_counts={"suspicious_champion_alias": 3})

        result = compare_quality_metrics(metrics, build_metrics(issue_counts={"suspicious_champion_alias": 3}))

        self.assertTrue(result["passed"])
        self.assertEqual(result["failed_metrics"], [])
        verdicts = self.verdicts(result)
        self.assertEqual(verdicts["validation.error_count"], "ok")
        self.assertEqual(verdicts["fallback_label.ratio"], "ok")
        self.assertEqual(verdicts["residual_english.ratio"], "ok")
        self.assertEqual(verdicts["validation.issue_counts_by_code.suspicious_champion_alias"], "ok")
        self.assertEqual(verdicts["record_count"], "info")

    def test_worse_metrics_fail(self) -> None:
        """error 上升、比例上升、受门禁 code 上升均判恶化。"""

        baseline = build_metrics(issue_counts={"low_confidence_output": 5})

        result = compare_quality_metrics(
            baseline,
            build_metrics(
                error_count=1,
                fallback_ratio=0.25,
                residual_ratio=0.55,
                issue_counts={"low_confidence_output": 6},
            ),
        )

        self.assertFalse(result["passed"])
        # failed_metrics 顺序跟随 checks 的固定声明顺序（error_count → 比例 →
        # 受门禁 code），属输出契约，故用精确顺序断言。
        self.assertEqual(
            result["failed_metrics"],
            [
                "validation.error_count",
                "fallback_label.ratio",
                "residual_english.ratio",
                "validation.issue_counts_by_code.low_confidence_output",
            ],
        )

    def test_improved_metrics_and_record_growth_pass(self) -> None:
        """比例下降、受门禁 code 下降通过；record_count 变化只报告。"""

        baseline = build_metrics(record_count=100, issue_counts={"unknown_entity_output": 4})

        result = compare_quality_metrics(
            baseline,
            build_metrics(
                record_count=200,
                fallback_ratio=0.1,
                residual_ratio=0.4,
                issue_counts={"unknown_entity_output": 2},
            ),
        )

        self.assertTrue(result["passed"])
        verdicts = self.verdicts(result)
        self.assertEqual(verdicts["record_count"], "info")
        record_check = next(check for check in result["checks"] if check["metric"] == "record_count")
        self.assertEqual(record_check["baseline"], 100)
        self.assertEqual(record_check["current"], 200)

    def test_error_count_must_be_zero(self) -> None:
        """即使低于基线，error_count 非 0 仍判恶化。"""

        result = compare_quality_metrics(build_metrics(error_count=2), build_metrics(error_count=1))

        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_metrics"], ["validation.error_count"])

    def test_missing_gated_metric_fails(self) -> None:
        """基线存在但当前缺失的受门禁指标判 missing 并整体失败。"""

        current = build_metrics()
        del current["residual_english"]

        result = compare_quality_metrics(build_metrics(), current)

        self.assertFalse(result["passed"])
        self.assertIn("residual_english.ratio", result["failed_metrics"])
        self.assertEqual(self.verdicts(result)["residual_english.ratio"], "missing")

    def test_missing_issue_counts_map_fails(self) -> None:
        """当前指标缺 issue_counts_by_code 时受门禁 code 全部判 missing。"""

        current = build_metrics()
        del current["validation"]["issue_counts_by_code"]

        result = compare_quality_metrics(build_metrics(), current)

        self.assertFalse(result["passed"])
        self.assertEqual(
            self.verdicts(result)["validation.issue_counts_by_code.suspicious_champion_alias"],
            "missing",
        )

    def test_empty_baseline_fails_all_gated_metrics(self) -> None:
        """空基线 vs 正常当前指标：受门禁项全部判 missing 并整体失败。"""

        result = compare_quality_metrics({}, build_metrics())

        self.assertFalse(result["passed"])
        self.assertEqual(
            result["failed_metrics"],
            [
                "validation.error_count",
                "fallback_label.ratio",
                "residual_english.ratio",
                "validation.issue_counts_by_code.display_drops_parsed_slot",
                "validation.issue_counts_by_code.low_confidence_output",
                "validation.issue_counts_by_code.suspicious_champion_alias",
                "validation.issue_counts_by_code.unknown_entity_output",
            ],
        )
        self.assertEqual(self.verdicts(result)["validation.error_count"], "missing")

    def test_missing_baseline_gated_key_fails(self) -> None:
        """基线侧缺单个受门禁键同样判 missing，畸形基线不得静默通过。"""

        baseline = build_metrics()
        del baseline["fallback_label"]

        result = compare_quality_metrics(baseline, build_metrics())

        self.assertFalse(result["passed"])
        self.assertEqual(result["failed_metrics"], ["fallback_label.ratio"])
        self.assertEqual(self.verdicts(result)["fallback_label.ratio"], "missing")

    def test_display_drop_code_is_ratcheted(self) -> None:
        """display_drops_parsed_slot 进入棘轮：基线缺 key 按 0，当前上升即失败。"""

        baseline = build_metrics(issue_counts={})

        result = compare_quality_metrics(
            baseline,
            build_metrics(issue_counts={"display_drops_parsed_slot": 1}),
        )

        self.assertFalse(result["passed"])
        self.assertIn(
            "validation.issue_counts_by_code.display_drops_parsed_slot",
            result["failed_metrics"],
        )
        check = next(
            item
            for item in result["checks"]
            if item["metric"] == "validation.issue_counts_by_code.display_drops_parsed_slot"
        )
        self.assertEqual(check["baseline"], 0)
        self.assertEqual(check["current"], 1)
        self.assertEqual(check["verdict"], "worse")

    def test_absent_code_counts_as_zero(self) -> None:
        """受门禁 code 在两侧 issue_counts_by_code 中缺失时按 0 比较。"""

        result = compare_quality_metrics(build_metrics(issue_counts={}), build_metrics(issue_counts={}))

        self.assertTrue(result["passed"])
        check = next(
            item
            for item in result["checks"]
            if item["metric"] == "validation.issue_counts_by_code.unknown_entity_output"
        )
        self.assertEqual(check["baseline"], 0)
        self.assertEqual(check["current"], 0)
        self.assertEqual(check["verdict"], "ok")


class QualityMetricsEdgeTests(unittest.TestCase):
    """验证指标采集的边界行为。"""

    def setUp(self) -> None:
        """准备空目录与最小 validation 报告。"""

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

        self.validation_json = self.root / "normalized-validation.json"
        self.write_validation({"issues": [], "summary": {"error_count": 0, "warning_count": 0}})

    def write_validation(self, report: dict[str, object]) -> None:
        """写出 validation 报告 JSON。"""

        self.validation_json.write_text(json.dumps(report), encoding="utf-8")

    def write_shard(self, normalized_root: Path, text: str) -> None:
        """在 by-entity 下写出单个 JSONL 分片。"""

        shard_dir = normalized_root / "by-entity" / "champions"
        shard_dir.mkdir(parents=True)
        (shard_dir / "Aatrox.jsonl").write_text(text, encoding="utf-8")

    def test_missing_manifest_yields_none(self) -> None:
        """无 manifest.json 时 manifest_record_count 为 None，实扫计数不受影响。"""

        normalized_root = self.root / "normalized"
        record = build_record("Play_sfx_Aatrox_Q", "Aatrox", "champion", "high", "Aatrox普攻")
        self.write_shard(normalized_root, json.dumps(record, ensure_ascii=False) + "\n")

        metrics = collect_quality_metrics(normalized_root, self.validation_json)

        self.assertIsNone(metrics["manifest_record_count"])
        self.assertEqual(metrics["record_count"], 1)

    def test_empty_root_yields_zero_counts_and_ratios(self) -> None:
        """空 normalized root 计数为 0，比例回落 0.0 而非除零。"""

        normalized_root = self.root / "normalized"
        normalized_root.mkdir()

        metrics = collect_quality_metrics(normalized_root, self.validation_json)

        self.assertEqual(metrics["record_count"], 0)
        self.assertEqual(metrics["fallback_label"], {"record_count": 0, "ratio": 0.0})
        self.assertEqual(metrics["residual_english"], {"record_count": 0, "ratio": 0.0})

    def test_bad_json_line_raises(self) -> None:
        """坏 JSON 行的解析异常向上传播，不被吞掉。"""

        normalized_root = self.root / "normalized"
        self.write_shard(normalized_root, "{not valid json}\n")

        with self.assertRaises(ValueError):
            collect_quality_metrics(normalized_root, self.validation_json)

    def test_validation_summary_missing_error_count_raises(self) -> None:
        """summary 缺 error_count 时立即抛 ValueError。"""

        normalized_root = self.root / "normalized"
        normalized_root.mkdir()
        self.write_validation({"issues": [], "summary": {"warning_count": 0}})

        with self.assertRaises(ValueError):
            collect_quality_metrics(normalized_root, self.validation_json)

    def test_validation_summary_non_int_warning_count_raises(self) -> None:
        """summary 的 warning_count 非整数时立即抛 ValueError。"""

        normalized_root = self.root / "normalized"
        normalized_root.mkdir()
        self.write_validation({"issues": [], "summary": {"error_count": 0, "warning_count": "2"}})

        with self.assertRaises(ValueError):
            collect_quality_metrics(normalized_root, self.validation_json)


if __name__ == "__main__":
    unittest.main()
