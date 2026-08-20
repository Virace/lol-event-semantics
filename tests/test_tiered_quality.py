"""P4 分层质量、accepted corpus 与 support diff 测试。"""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lol_event_semantics.normalized_contract import apply_normalized_contract, build_public_pattern_catalog
from lol_event_semantics.pattern_catalog import (
    PatternPolicy,
    canonical_pattern_signature,
    load_pattern_policy,
    pattern_id_for_signature,
)
from lol_event_semantics.tiered_quality import (
    V1_STABLE_SCOPE,
    StableScope,
    build_gate_report,
    build_quality_summary,
    build_support_diff,
    load_accepted_corpus,
)

ROOT = Path(__file__).parents[1]
POLICY = load_pattern_policy(ROOT / "rules" / "translation_patterns.yaml")


class TieredQualityTests(unittest.TestCase):
    """验证 Stable 绝对门禁不会被全局基线或 Experimental 噪声稀释。"""

    def test_exact_approved_ability_passes_and_classifies_ability_key(self) -> None:
        rows, report, catalog, corpus = build_contract([ability_record()])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(rows[0]["support_level"], "stable")
        self.assertEqual(rows[0]["review_status"], "accepted")
        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["stable"]["violation_counts"], {})
        self.assertEqual(summary["stable"]["residual_english"]["record_counts_by_category"], {"ability_key": 1})
        self.assertTrue(summary["accepted_corpus"]["passed"])

    def test_each_stable_violation_is_absolute(self) -> None:
        mutations = {
            "missing_translation": lambda row: row.pop("translation"),
            "wrong_frame": lambda row: row["translation"].__setitem__("frame_type", "voice_event"),
            "missing_required_role": lambda row: row["translation"]["arguments"].pop("ability_slot"),
            "renderer_diagnostic": lambda row: row["translation"]["arguments"].pop("ability_slot"),
            "fallback_argument": lambda row: row["translation"]["arguments"]["phase"].__setitem__("fallback", True),
            "required_low_confidence": lambda row: row["translation"]["arguments"]["ability_slot"].__setitem__(
                "confidence", "low"
            ),
            "missing_zh_cn": lambda row: row["translation"]["arguments"]["phase"]["labels"].pop("zh_CN"),
            "render_failure": lambda row: row.__setitem__("render_status", "missing"),
            "rendered_label_mismatch": lambda row: row["rendered_labels"]["zh_CN"].__setitem__("main", "漂移文案"),
            "pending_review": lambda row: row.__setitem__("review_status", "pending"),
            "unresolved_english": lambda row: row["translation"]["arguments"]["phase"]["labels"].__setitem__(
                "zh_CN", "Cast"
            ),
        }
        for expected, mutate in mutations.items():
            with self.subTest(expected=expected):
                rows, report, catalog, corpus = build_contract([ability_record()])
                mutate(rows[0])
                summary = build_test_quality_summary(rows, report, catalog, corpus)
                self.assertEqual(summary["status"], "failed")
                self.assertIn(expected, summary["stable"]["violation_counts"])
                gate = build_gate_report(
                    summary,
                    {"summary": {"error_count": 0}},
                    {"passed": True},
                )
                self.assertFalse(gate["passed"])

    def test_experimental_problem_is_observed_but_does_not_block_stable(self) -> None:
        approved = ability_record()
        experimental = voice_record("Play_vo_Annie_Move")
        experimental["translation"]["arguments"]["action"]["fallback"] = True
        rows, report, catalog, corpus = build_contract([approved, experimental], approved_records=[approved])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(summary["status"], "passed")
        self.assertEqual(summary["experimental"]["record_count"], 1)
        self.assertFalse(summary["experimental"]["blocking"])

    def test_allowed_proper_noun_abbreviation_and_technical_tokens_do_not_block(self) -> None:
        """五分类中四类允许英文与 unresolved 的阻断语义分离。"""

        proper = voice_record("Play_vo_Annie_Dance")
        proper["translation"]["arguments"]["action"] = argument("action", "dance", "跳舞")
        proper["translation"]["arguments"]["champion"]["labels"]["zh_CN"] = "Annie"
        abbreviation = voice_record("Play_vo_Annie_LevelUp")
        abbreviation["translation"]["arguments"]["action"] = argument("action", "level_up", "HP")
        technical = voice_record("Play_vo_Annie_Respawn")
        technical["translation"]["arguments"]["action"] = argument("action", "respawn", "VO")
        rows, report, catalog, corpus = build_contract([proper, abbreviation, technical])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        counts = summary["stable"]["residual_english"]["record_counts_by_category"]
        self.assertEqual(counts, {"abbreviation": 1, "proper_noun": 1, "technical": 1})
        self.assertEqual(summary["status"], "passed")

    def test_structured_qualifier_english_blocks_even_when_renderer_hides_it(self) -> None:
        """不进入 main/default 模板的 qualifier 仍必须接受英文残留门禁。"""

        record = ability_record()
        record["translation"]["arguments"]["qualifier"] = argument("qualifier", "open", "Open")
        rows, report, catalog, corpus = build_contract([record])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(rows[0]["rendered_labels"]["zh_CN"]["main"], "Q技能释放")
        self.assertEqual(summary["status"], "failed")
        self.assertIn("unresolved_english", summary["stable"]["violation_counts"])
        self.assertEqual(summary["stable"]["qualifier_coverage"]["unresolved_english_record_count"], 1)

    def test_unresolved_single_key_spell_residual_blocks_without_rejecting_other_medium_records(self) -> None:
        """只阻断带未解释后缀的单字母技能启发式，不一刀切全部 medium。"""

        ability = ability_record()
        ability["translation"]["frame_confidence"] = "medium"
        ability["semantic"]["slots"].update(
            {
                "source": "event_name_heuristic",
                "subject": {"key": "annie"},
                "spell_key": "q",
                "spell_match": {"match_source": "spell_key", "matched_term": "annieq"},
                "unresolved_residual": "angedattack",
            }
        )
        voice = voice_record("Play_vo_Annie_Move")
        voice["translation"]["frame_confidence"] = "medium"
        rows, report, catalog, corpus = build_contract([ability, voice])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["stable"]["violation_counts"], {"unresolved_single_key_spell_residual": 1})
        self.assertEqual(summary["global"]["confidence_distribution"]["frame"], {"medium": 2})

    def test_full_passive_match_with_residual_is_not_a_single_key_false_positive(self) -> None:
        """完整 Passive 词条不是 owner + P 的单字母误拆。"""

        ability = ability_record()
        ability["translation"]["frame_confidence"] = "medium"
        ability["semantic"]["slots"].update(
            {
                "source": "event_name_heuristic",
                "subject": {"key": "sion"},
                "spell_key": "p",
                "spell_match": {"match_source": "passive_key", "matched_term": "sionpassive"},
                "unresolved_residual": "delay",
            }
        )
        rows, report, catalog, corpus = build_contract([ability])

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(summary["status"], "passed")
        self.assertNotIn(
            "unresolved_single_key_spell_residual",
            summary["stable"]["violation_counts"],
        )

    def test_corpus_text_drift_blocks_stable(self) -> None:
        rows, report, catalog, corpus = build_contract([ability_record()])
        corpus["entries"][0]["zh_cn_main"] = "错误文案"

        summary = build_test_quality_summary(rows, report, catalog, corpus)

        self.assertEqual(summary["status"], "failed")
        self.assertEqual(summary["accepted_corpus"]["failed_entry_count"], 1)
        self.assertIn("accepted_corpus_failure", summary["stable"]["violation_counts"])

    def test_v1_scope_rejects_coordinated_policy_and_corpus_shrink(self) -> None:
        """candidate 必须命中冻结的 91 patterns / 4,389 records。"""

        rows, report, catalog, corpus = build_contract([ability_record()])

        summary = build_quality_summary(rows, report, catalog, corpus)

        violations = summary["stable"]["violation_counts"]
        self.assertEqual(summary["status"], "failed")
        self.assertIn("approved_pattern_count_mismatch", violations)
        self.assertIn("approved_pattern_ids_digest_mismatch", violations)
        self.assertIn("stable_record_count_mismatch", violations)
        self.assertFalse(summary["scope_contract"]["matched"])

    def test_gate_attestation_never_claims_manual_eligibility(self) -> None:
        rows, report, catalog, corpus = build_contract([ability_record()])
        summary = build_test_quality_summary(rows, report, catalog, corpus)

        gate = build_gate_report(
            summary,
            {"summary": {"error_count": 0}},
            {"passed": True},
            stable_scope=build_test_scope(rows, report, corpus),
        )

        self.assertTrue(gate["passed"])
        self.assertTrue(gate["promotion_eligibility"]["automatic_gates_passed"])
        self.assertFalse(gate["promotion_eligibility"]["eligible"])
        self.assertEqual(gate["promotion_eligibility"]["reason"], "manual review pending")

        summary["scope_contract"]["matched"] = False
        tampered = build_gate_report(
            summary,
            {"summary": {"error_count": 0}},
            {"passed": True},
            stable_scope=build_test_scope(rows, report, corpus),
        )
        self.assertFalse(tampered["passed"])

    def test_support_diff_detects_pattern_and_record_downgrade(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            previous = root / "previous"
            current = root / "current"
            write_support_fixture(previous, "stable")
            write_support_fixture(current, "experimental")

            diff = build_support_diff(current, previous)

        self.assertFalse(diff["passed"])
        self.assertTrue(diff["review_required"])
        self.assertEqual(diff["stable_pattern_downgrades"], ["pattern-v1-" + "1" * 64])
        self.assertEqual(diff["stable_record_downgrades"], ["record-v1-" + "2" * 64])
        self.assertEqual(diff["pattern_changes"][0]["previous"], "stable")
        self.assertEqual(diff["pattern_changes"][0]["current"], "experimental")
        self.assertEqual(diff["record_changes"][0]["previous"], "stable")

    def test_support_diff_requires_review_for_upgrade_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            previous = root / "previous"
            current = root / "current"
            write_support_fixture(previous, "experimental")
            write_support_fixture(current, "stable")

            diff = build_support_diff(current, previous)

        self.assertTrue(diff["passed"])
        self.assertTrue(diff["review_required"])
        self.assertEqual(diff["transition_counts"]["patterns"], {"experimental->stable": 1})
        self.assertEqual(diff["transition_counts"]["records"], {"experimental->stable": 1})

    def test_initial_support_diff_lists_stable_scope_for_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp) / "current"
            write_support_fixture(current, "stable")

            diff = build_support_diff(current, None)

        self.assertTrue(diff["passed"])
        self.assertTrue(diff["review_required"])
        self.assertIsNone(diff["previous"])
        self.assertEqual(diff["transition_counts"]["patterns"], {"none->stable": 1})
        self.assertEqual(diff["transition_counts"]["records"], {"none->stable": 1})

    def test_readme_consumer_queries_use_real_stable_and_experimental_events(self) -> None:
        """README quick start 不再引用不存在或错误分层的事件名。"""

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        corpus = json.loads((ROOT / "data" / "accepted-corpus.v1.json").read_text(encoding="utf-8"))
        stable_names = {entry["raw_event_name"] for entry in corpus["entries"]}

        self.assertIn("Play_vo_AatroxSkin01_AatroxE_cast3D --expect stable", readme)
        self.assertIn("Play_vo_AatroxSkin01_AatroxE_cast3D", stable_names)
        self.assertIn("Play_vo_Annie_Move2DStandard --expect experimental", readme)
        self.assertNotIn("Play_vo_Annie_Move2DStandard", stable_names)
        self.assertIn(
            "Play_vo_Annie_Move2DStandard",
            (ROOT / "raw" / "events" / "champions" / "1.txt").read_text(encoding="utf-8"),
        )

    def test_committed_corpus_matches_exact_approved_policy(self) -> None:
        """提交的 91 条 corpus 与已完成的 exact source approval 一一对应。"""

        corpus = load_accepted_corpus(ROOT / "data" / "accepted-corpus.v1.json")
        pattern_ids = {entry["pattern_id"] for entry in corpus["entries"]}

        self.assertEqual(corpus["review_status"], "accepted")
        self.assertTrue(all(entry["review_status"] == "accepted" for entry in corpus["entries"]))
        self.assertEqual(len(pattern_ids), 91)
        self.assertEqual(pattern_ids, POLICY.promotion_decisions.approved_pattern_ids)
        digest = hashlib.sha256(json.dumps(sorted(pattern_ids), separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(corpus["decision_id"], V1_STABLE_SCOPE.decision_id)
        self.assertEqual(len(pattern_ids), V1_STABLE_SCOPE.pattern_count)
        self.assertEqual(digest, V1_STABLE_SCOPE.pattern_ids_digest)


def build_test_quality_summary(
    rows: list[dict[str, object]],
    report: dict[str, object],
    catalog: dict[str, object],
    corpus: dict[str, object],
) -> dict[str, object]:
    """以 fixture 自身冻结范围运行门禁，避免小样本冒充生产 V1 范围。"""

    scope = build_test_scope(rows, report, corpus)
    return build_quality_summary(rows, report, catalog, corpus, stable_scope=scope)


def build_test_scope(
    rows: list[dict[str, object]],
    report: dict[str, object],
    corpus: dict[str, object],
) -> StableScope:
    """从 fixture 期望值构造仅用于单元测试的冻结范围。"""

    approved = report["approved_scope_patterns"]
    assert isinstance(approved, list)
    pattern_ids = sorted(str(pattern["pattern_id"]) for pattern in approved if isinstance(pattern, dict))
    digest = hashlib.sha256(json.dumps(pattern_ids, separators=(",", ":")).encode()).hexdigest()
    return StableScope(
        decision_id=str(corpus["decision_id"]),
        pattern_count=len(pattern_ids),
        record_count=sum(row.get("support_level") == "stable" for row in rows),
        pattern_ids_digest=digest,
    )


def build_contract(
    records: list[dict[str, object]],
    *,
    approved_records: list[dict[str, object]] | None = None,
) -> tuple[list[dict[str, object]], dict[str, object], dict[str, object], dict[str, object]]:
    """给测试 records 应用与 writer 相同的 support/catalog/corpus 契约。"""

    approved_records = records if approved_records is None else approved_records
    approved_ids = frozenset(
        pattern_id_for_signature(canonical_pattern_signature(record, POLICY)) for record in approved_records
    )
    decisions = replace(POLICY.promotion_decisions, approved_pattern_ids=approved_ids)
    policy: PatternPolicy = replace(POLICY, promotion_decisions=decisions)
    rows = copy.deepcopy(records)
    report = apply_normalized_contract(rows, policy)
    catalog = build_public_pattern_catalog(report, policy)
    corpus = {
        "schema_version": 1,
        "corpus_id": decisions.decision_id,
        "decision_id": decisions.decision_id,
        "selection_policy": "lexicographically-first-record.v1",
        "review_status": "pending",
        "entries": copy.deepcopy(report["accepted_corpus_candidates"]),
    }
    return rows, report, catalog, corpus


def ability_record() -> dict[str, object]:
    """构造最小 ability Stable 记录。"""

    record = voice_record("Play_vo_Annie_AbilityQCast")
    record["semantic"] = {
        "profile": "spell_event",
        "primary_family": "spell",
        "slots": {"trigger_family": "spell"},
    }
    record["translation"]["frame_type"] = "ability_cast_event"
    record["translation"]["arguments"] = {
        "champion": argument("champion", "annie", "安妮"),
        "ability_slot": argument("ability_slot", "q", "Q技能"),
        "phase": argument("phase", "cast", "释放"),
    }
    return record


def voice_record(raw: str) -> dict[str, object]:
    """构造最小 voice 记录。"""

    return {
        "schema_version": 2,
        "raw_event_name": raw,
        "source_paths": ["raw/events/champions/Annie.txt"],
        "entity": {"kind": "champion", "alias": "Annie"},
        "semantic": {
            "profile": "self_action",
            "primary_family": "voice",
            "slots": {"trigger_family": "movement"},
        },
        "quality": {"diagnostics": [], "unknown_count": 0},
        "translation": {
            "schema_version": 1,
            "frame_version": 1,
            "frame_type": "voice_event",
            "frame_confidence": "high",
            "arguments": {
                "champion": argument("champion", "annie", "安妮"),
                "action": argument("action", "move", "移动"),
            },
        },
    }


def argument(role: str, key: str, zh_cn: str) -> dict[str, object]:
    """构造可信且无 fallback 的 translation argument。"""

    return {
        "role": role,
        "key": key,
        "value": key,
        "labels": {"zh_CN": zh_cn, "en_US": key},
        "confidence": "high",
        "fallback": False,
        "provenance": ["test"],
    }


def write_support_fixture(root: Path, level: str) -> None:
    """写出 support diff 所需的最小 source-root fixture。"""

    root.mkdir(parents=True)
    record_id = "record-v1-" + "2" * 64
    pattern_id = "pattern-v1-" + "1" * 64
    row = {"record_id": record_id, "pattern_id": pattern_id, "support_level": level}
    (root / "normalized-events.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (root / "pattern-catalog.json").write_text(
        json.dumps({"patterns": [{"pattern_id": pattern_id, "support_level": level}]}),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(json.dumps({"dataset_id": f"local-v1-{level}"}), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
