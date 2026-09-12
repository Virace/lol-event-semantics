"""translation pattern inventory 测试。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from lol_event_semantics.pattern_catalog import (
    PatternPolicy,
    SIGNATURE_SCHEMA_VERSION,
    build_pattern_report,
    canonical_pattern_signature,
    load_normalized_records,
    load_pattern_policy,
    pattern_id_for_signature,
    render_pattern_markdown,
    write_pattern_report,
)

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "rules" / "translation_patterns.yaml"


class PatternCatalogTests(unittest.TestCase):
    """验证 canonical signature、Stable 门禁与确定性输出。"""

    def setUp(self) -> None:
        """加载仓库中的 experimental pattern policy。"""

        self.policy = load_pattern_policy(POLICY_PATH)

    def test_policy_contains_pending_experimental_vo_candidates(self) -> None:
        """七个 SPEC VO frame 只声明 pending proposal，不冒充 Stable。"""

        self.assertEqual(
            set(self.policy.stable_frames),
            {
                "voice_event",
                "ability_cast_event",
                "kill_or_destroy_event",
                "champion_interaction_cue",
                "champion_dialogue_event",
                "announcer_event",
                "shop_voice_event",
            },
        )
        for frame in self.policy.stable_frames.values():
            self.assertEqual(frame.support_level, "experimental")
            self.assertEqual(frame.proposed_support_level, "stable")
            self.assertEqual(frame.review_status, "pending")
            self.assertTrue(frame.semantic_profiles)
        decisions = self.policy.promotion_decisions
        self.assertEqual(decisions.decision_id, "v1-semantic-fix-2026-08-11")
        self.assertEqual(
            (decisions.eligible_default, decisions.ineligible_default, decisions.non_vo_default),
            ("pending", "deferred", "deferred"),
        )
        self.assertEqual(
            [selector.frame_type for selector in decisions.approved], ["ability_cast_event", "voice_event"]
        )
        self.assertEqual(len(decisions.approved_pattern_ids), 91)

    def test_policy_rejects_invalid_or_duplicate_approved_pattern_ids(self) -> None:
        """精确批准表只接受不重复的 canonical pattern id。"""

        source = POLICY_PATH.read_text(encoding="utf-8")
        pattern_ids = [
            line.strip().removeprefix("- ") for line in source.splitlines() if line.strip().startswith("- pattern-v1-")
        ]
        invalid_sources = (
            source.replace(pattern_ids[0], "pattern-v1-invalid", 1),
            source.replace(pattern_ids[1], pattern_ids[0], 1),
        )
        for invalid in invalid_sources:
            with self.subTest():
                with tempfile.TemporaryDirectory() as temp:
                    path = Path(temp) / "policy.yaml"
                    path.write_text(invalid, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, "approved_pattern_ids"):
                        load_pattern_policy(path)

    def test_policy_defaults_must_remain_experimental_pending(self) -> None:
        """defaults 不能绕过候选策略冒充 Stable/approved。"""

        source = POLICY_PATH.read_text(encoding="utf-8")
        invalid = source.replace(
            "defaults:\n  support_level: experimental\n  review_status: pending",
            "defaults:\n  support_level: stable\n  review_status: approved",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.yaml"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "defaults 必须保持 experimental/pending"):
                load_pattern_policy(path)

    def test_policy_rejects_unregistered_frame(self) -> None:
        """policy frame 必须直接引用 FRAME_REGISTRY，不能建立第二份幽灵 frame。"""

        source = POLICY_PATH.read_text(encoding="utf-8")
        invalid = source.replace(
            "  frames:\n",
            "  frames:\n"
            "    unknown_frame:\n"
            "      support_level: experimental\n"
            "      proposed_support_level: stable\n"
            "      review_status: pending\n"
            "      semantic_profiles: [self_action]\n"
            "      signature_roles: []\n",
        )
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "policy.yaml"
            path.write_text(invalid, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "未在 FRAME_REGISTRY 注册: unknown_frame"):
                load_pattern_policy(path)

    def test_signature_and_full_sha256_id_ignore_examples_and_entity_values(self) -> None:
        """signature/id 只依赖 canonical 形态，实体值、路径和展示文本保持参数化。"""

        first = voice_record("Play_vo_Annie_Move", champion="Annie")
        second = voice_record("Play_vo_Ahri_Move", champion="Ahri")
        second["source_paths"] = ["different/local/path"]
        second["generated_at"] = "2099-01-01T00:00:00Z"
        first_signature = canonical_pattern_signature(first, self.policy)
        second_signature = canonical_pattern_signature(second, self.policy)

        self.assertEqual(first_signature, second_signature)
        self.assertEqual(first_signature["schema_version"], SIGNATURE_SCHEMA_VERSION)
        self.assertEqual(first_signature["asset_kind"], "vo")
        self.assertEqual(first_signature["frame_version"], 1)
        self.assertEqual(first_signature["argument_keys"], {"action": "move", "qualifier": None})
        self.assertEqual(first_signature["roles"], ["action", "champion"])
        pattern_id = pattern_id_for_signature(first_signature)
        self.assertRegex(pattern_id, re.compile(r"^pattern-v1-[0-9a-f]{64}$"))
        self.assertEqual(pattern_id, pattern_id_for_signature(second_signature))

    def test_frame_version_is_part_of_signature_and_pattern_id(self) -> None:
        """translation frame schema 升级或缺失不得错误复用旧 pattern id。"""

        first = voice_record("Play_vo_Annie_Move")
        second = voice_record("Play_vo_Annie_Move")
        second["translation"]["frame_version"] = 2
        missing = voice_record("Play_vo_Annie_Move")
        del missing["translation"]["frame_version"]

        first_signature = canonical_pattern_signature(first, self.policy)
        second_signature = canonical_pattern_signature(second, self.policy)
        missing_signature = canonical_pattern_signature(missing, self.policy)

        self.assertEqual(second_signature["frame_version"], 2)
        self.assertEqual(missing_signature["frame_version"], "missing")
        self.assertNotEqual(pattern_id_for_signature(first_signature), pattern_id_for_signature(second_signature))
        self.assertNotEqual(pattern_id_for_signature(first_signature), pattern_id_for_signature(missing_signature))

    def test_signature_role_key_changes_pattern_id(self) -> None:
        """policy 指定的非实体 argument key 会进入 canonical signature。"""

        move = canonical_pattern_signature(voice_record("Play_vo_Annie_Move"), self.policy)
        attack_record = voice_record("Play_vo_Annie_Attack")
        attack_record["translation"]["arguments"]["action"]["key"] = "attack"
        attack = canonical_pattern_signature(attack_record, self.policy)

        self.assertNotEqual(pattern_id_for_signature(move), pattern_id_for_signature(attack))

    def test_spell_target_cannot_disappear_into_an_approved_pattern(self) -> None:
        """原名中的技能对象未进入解释时，既有模式批准也不能放行。"""

        record = ability_record()
        record["raw_event_name"] = "Play_vo_Annie_AnnieQ_cast3DEnemy"
        pattern_id = pattern_id_for_signature(canonical_pattern_signature(record, self.policy))
        policy = replace(
            self.policy,
            promotion_decisions=replace(self.policy.promotion_decisions, approved_pattern_ids=frozenset({pattern_id})),
        )

        pattern = build_pattern_report([record], policy)["patterns"][0]

        self.assertEqual(pattern["support_level"], "experimental")
        self.assertEqual(pattern["blocking_reasons"]["missing_spell_context"], 1)

    def test_spell_context_creates_a_new_review_target(self) -> None:
        """补齐条件改变模式身份，不能继承无条件技能的旧批准。"""

        original = ability_record()
        original_id = pattern_id_for_signature(canonical_pattern_signature(original, self.policy))
        policy = replace(
            self.policy,
            promotion_decisions=replace(self.policy.promotion_decisions, approved_pattern_ids=frozenset({original_id})),
        )
        record = ability_record()
        record["raw_event_name"] = "Play_vo_Annie_AnnieQ_cast3DEnemy"
        record["translation"]["arguments"]["spell_context"] = argument("spell_context", "enemy", "敌方")

        pattern = build_pattern_report([record], policy)["patterns"][0]

        self.assertNotEqual(pattern["pattern_id"], original_id)
        self.assertEqual(pattern["support_level"], "experimental")
        self.assertNotIn("missing_spell_context", pattern["blocking_reasons"])

    def test_input_order_does_not_change_report(self) -> None:
        """记录输入顺序不影响 pattern 顺序、计数或代表样例。"""

        records = [
            voice_record("Play_vo_Zed_Move", champion="Zed"),
            voice_record("Play_vo_Ahri_Move", champion="Ahri"),
            voice_record("Play_vo_Annie_Move", champion="Annie"),
        ]

        forward = build_pattern_report(records, self.policy)
        reverse = build_pattern_report(reversed(records), self.policy)

        self.assertEqual(forward, reverse)
        examples = forward["patterns"][0]["representative_examples"]
        self.assertEqual(
            [example["raw_event_name"] for example in examples],
            ["Play_vo_Ahri_Move", "Play_vo_Annie_Move", "Play_vo_Zed_Move"],
        )

    def test_pattern_retains_all_zh_cn_label_variants_with_bounded_event_samples(self) -> None:
        """同一 canonical pattern 的中文文案变体不能被首个样例遮蔽。"""

        first = voice_record("Play_vo_Annie_Move")
        second = voice_record("Play_vo_Ahri_Move")
        second["translation"]["arguments"]["action"]["labels"]["zh_CN"] = "移动中"
        third = voice_record("Play_vo_Zed_Move")
        third["translation"]["arguments"]["action"]["labels"]["zh_CN"] = "移动中"

        pattern = build_pattern_report([third, first, second], self.policy)["patterns"][0]
        variants = pattern["zh_cn_label_variants"]["action"]

        self.assertEqual(variants["distinct_label_count"], 2)
        self.assertEqual(
            variants["variants"],
            [
                {"label": "移动", "record_count": 1, "sample_raw_event_names": ["Play_vo_Annie_Move"]},
                {
                    "label": "移动中",
                    "record_count": 2,
                    "sample_raw_event_names": ["Play_vo_Ahri_Move", "Play_vo_Zed_Move"],
                },
            ],
        )
        self.assertNotIn("champion", pattern["zh_cn_label_variants"])

    def test_voice_qualifier_key_splits_stable_candidates_and_blocks_unreviewed_values(self) -> None:
        """缺失、已核验和待审 qualifier 不能混用同一个 Stable 候选。"""

        missing = voice_record("Play_vo_Annie_Ping2D")
        reviewed = voice_record("Play_vo_Annie_Ping2DOnMyWay")
        reviewed["translation"]["arguments"]["qualifier"] = argument("qualifier", "onmyway", "正在路上")
        unreviewed = voice_record("Play_vo_Annie_Ping2DTarget")
        unreviewed["translation"]["arguments"]["qualifier"] = argument("qualifier", "unreviewed", "待审修饰（Target）")
        unreviewed["translation"]["arguments"]["qualifier"]["confidence"] = "low"
        unreviewed["translation"]["arguments"]["qualifier"]["fallback"] = True

        self.assertEqual(
            canonical_pattern_signature(missing, self.policy)["argument_keys"],
            {"action": "move", "qualifier": None},
        )

        report = build_pattern_report([missing, reviewed, unreviewed], self.policy)
        proposal_keys = [pattern["signature"]["argument_keys"] for pattern in report["stable_proposals"]]
        blocked = next(
            pattern
            for pattern in report["patterns"]
            if pattern["signature"]["argument_keys"] == {"action": "move", "qualifier": "unreviewed"}
        )

        self.assertCountEqual(
            proposal_keys,
            [
                {"action": "move", "qualifier": None},
                {"action": "move", "qualifier": "onmyway"},
            ],
        )
        self.assertFalse(blocked["eligibility"]["stable"])
        self.assertIn("fallback_argument:qualifier", blocked["blocking_reasons"])

    def test_review_candidates_sort_by_frame_signature_then_id(self) -> None:
        """待审候选按语义家族组织，不按 SHA-256 随机打散。"""

        assist = voice_record("Play_vo_Annie_Assist")
        set_voice_action(assist, "assist", "助攻")
        dance = voice_record("Play_vo_Annie_Dance")
        set_voice_action(dance, "dance", "跳舞")

        approved_policy = self.policy_approving(assist, dance)
        report = build_pattern_report([dance, assist], approved_policy)
        keys = [candidate["signature"]["argument_keys"]["action"] for candidate in report["accepted_corpus_candidates"]]

        self.assertEqual(keys, ["assist", "dance"])
        self.assertEqual(
            report["accepted_corpus_candidates"],
            build_pattern_report([assist, dance], approved_policy)["accepted_corpus_candidates"],
        )

    def test_user_approved_scope_becomes_exact_stable_support(self) -> None:
        """P4 只把精确批准且结构合格的 pattern 晋级 Stable。"""

        dance = voice_record("Play_vo_Annie_Dance")
        set_voice_action(dance, "dance", "跳舞")
        move = voice_record("Play_vo_Annie_Move")
        blocked_dance = voice_record("Play_vo_AnnieSkin01_Dance")
        set_voice_action(blocked_dance, "dance", "跳舞")
        blocked_dance["translation"]["arguments"]["skin"] = argument("skin", "skin01", "默认皮肤")
        blocked_dance["translation"]["arguments"]["skin"]["fallback"] = True
        sfx = voice_record("Play_sfx_Annie_Dance")
        set_voice_action(sfx, "dance", "跳舞")

        report = build_pattern_report(
            [move, blocked_dance, sfx, dance],
            self.policy_approving(dance),
        )
        by_scope = {
            pattern["scope_status"]: pattern
            for pattern in report["patterns"]
            if pattern["signature"]["asset_kind"] == "vo"
        }
        approved = by_scope["approved"]
        pending = by_scope["pending"]
        deferred = by_scope["deferred"]

        self.assertEqual(approved["support_level"], "stable")
        self.assertIsNone(approved["proposed_support_level"])
        self.assertEqual(approved["review_status"], "accepted")
        self.assertEqual(pending["review_status"], "pending")
        self.assertEqual(pending["proposed_support_level"], "stable")
        self.assertFalse(deferred["eligibility"]["stable"])
        self.assertIsNone(deferred["proposed_support_level"])
        self.assertEqual(report["summary"]["approved_scope_pattern_count"], 1)
        self.assertEqual(report["summary"]["pending_proposal_pattern_count"], 1)
        self.assertEqual(report["promotion_decision"]["approved_pattern_id_count"], 1)
        self.assertEqual(report["promotion_decision"]["matched_approved_pattern_id_count"], 1)
        self.assertEqual(report["promotion_decision"]["unmatched_approved_pattern_ids"], [])
        self.assertEqual(len(report["accepted_corpus_candidates"]), 1)
        self.assertEqual(report["accepted_corpus_candidates"][0]["scope_status"], "approved")
        sfx_pattern = next(pattern for pattern in report["patterns"] if pattern["signature"]["asset_kind"] == "sfx")
        self.assertEqual(sfx_pattern["scope_status"], "deferred")

    def test_only_vo_can_become_stable_proposal(self) -> None:
        """同形 SFX 只进入 experimental candidate，不得晋级 Stable。"""

        vo = voice_record("Play_vo_Annie_Move")
        sfx = voice_record("Play_sfx_Annie_Move")
        report = build_pattern_report([vo, sfx], self.policy)

        self.assertEqual(len(report["stable_proposals"]), 1)
        self.assertEqual(report["stable_proposals"][0]["signature"]["asset_kind"], "vo")
        self.assertEqual(len(report["sfx_candidates"]), 1)
        candidate = report["sfx_candidates"][0]
        self.assertFalse(candidate["eligibility"]["stable"])
        self.assertEqual(candidate["support_level"], "experimental")
        self.assertIsNone(candidate["proposed_support_level"])
        self.assertEqual(candidate["review_status"], "pending")
        self.assertIn("asset_kind_not_vo", candidate["blocking_reasons"])

        matrix = report["support_matrix"]
        coverage = matrix["vo_eligible_coverage"]
        self.assertEqual(coverage["eligible_stable_pattern_count"], 1)
        self.assertEqual(coverage["total_pattern_count"], 1)
        self.assertEqual(coverage["pattern_ratio"], 1.0)
        voice_row = next(row for row in matrix["frames"] if row["frame_type"] == "voice_event")
        self.assertEqual(voice_row["total_pattern_count"], 2)
        self.assertEqual(voice_row["total_record_count"], 2)
        self.assertEqual(voice_row["eligible_stable_pattern_count"], 1)
        self.assertEqual(voice_row["eligible_stable_record_count"], 1)
        self.assertEqual(voice_row["blocked_pattern_count"], 1)
        self.assertEqual(voice_row["sfx_candidate_pattern_count"], 1)

    def test_frame_and_role_defer_selectors_override_structural_eligibility(self) -> None:
        """播报、空 turn 对话和 milestone 击杀即使结构合格也保持 defer。"""

        ability = ability_record()
        report = build_pattern_report(
            [ability, announcer_record(), dialogue_without_turn_record(), kill_milestone_record()],
            self.policy_approving(ability),
        )
        by_frame = {pattern["signature"]["frame_type"]: pattern for pattern in report["patterns"]}

        self.assertEqual(by_frame["ability_cast_event"]["scope_status"], "approved")
        for frame_type in ("announcer_event", "champion_dialogue_event", "kill_or_destroy_event"):
            with self.subTest(frame_type=frame_type):
                self.assertTrue(by_frame[frame_type]["eligibility"]["stable"])
                self.assertEqual(by_frame[frame_type]["scope_status"], "deferred")
                self.assertIsNone(by_frame[frame_type]["proposed_support_level"])

    def test_future_pattern_in_approved_family_returns_to_pending(self) -> None:
        """同 family 新 signature 未进入精确白名单时不得自动 approved。"""

        future = voice_record("Play_vo_Annie_DanceFuture")
        set_voice_action(future, "dance", "跳舞")
        future["translation"]["frame_version"] = 999
        pattern = build_pattern_report([future], self.policy)["patterns"][0]

        self.assertTrue(pattern["eligibility"]["stable"])
        self.assertEqual(pattern["scope_status"], "pending")
        self.assertEqual(pattern["review_status"], "pending")

    def test_legacy_record_without_shell_or_support_uses_raw_parser(self) -> None:
        """旧记录不需要 shell/support 字段，asset kind 从 raw 精确解析。"""

        record = voice_record("Stop_VO_Annie_Move")
        record.pop("support_level", None)
        signature = canonical_pattern_signature(record, self.policy)

        self.assertEqual(signature["asset_kind"], "vo")

    def test_missing_translation_blocks_stable(self) -> None:
        """缺 translation 的 VO 记录必须阻断。"""

        record = voice_record("Play_vo_Annie_Move")
        del record["translation"]
        self.assert_blocked(record, "missing_translation")

    def test_missing_or_wrong_frame_blocks_stable(self) -> None:
        """缺 frame 与未注册 frame 都必须阻断。"""

        missing = voice_record("Play_vo_Annie_Move")
        del missing["translation"]["frame_type"]
        self.assert_blocked(missing, "missing_frame_type")

        wrong = voice_record("Play_vo_Annie_Move")
        wrong["translation"]["frame_type"] = "not_a_frame"
        self.assert_blocked(wrong, "unsupported_frame_type")

    def test_registered_non_candidate_frame_blocks_stable(self) -> None:
        """已注册但不在七个 VO 候选内的 frame 不得晋级。"""

        record = voice_record("Play_vo_Annie_Move")
        record["translation"]["frame_type"] = "ability_state_change"
        record["translation"]["arguments"]["phase"] = argument("phase", "cast", "释放")
        self.assert_blocked(record, "frame_not_stable_candidate")

    def test_missing_required_role_blocks_stable(self) -> None:
        """required roles 直接来自 FRAME_REGISTRY，缺失即阻断。"""

        record = voice_record("Play_vo_Annie_Move")
        del record["translation"]["arguments"]["action"]
        self.assert_blocked(record, "missing_required_role:action")

    def test_any_fallback_argument_blocks_stable(self) -> None:
        """即使 optional argument 使用 fallback，整条记录也不得晋级。"""

        record = voice_record("Play_vo_Annie_Move")
        record["translation"]["arguments"]["champion"]["fallback"] = True
        self.assert_blocked(record, "fallback_argument:champion")

    def test_low_required_role_confidence_blocks_stable(self) -> None:
        """required role 的 low/failed/unknown 置信度必须阻断。"""

        for confidence in ("low", "failed", "unknown"):
            with self.subTest(confidence=confidence):
                record = voice_record("Play_vo_Annie_Move")
                record["translation"]["arguments"]["action"]["confidence"] = confidence
                self.assert_blocked(record, "untrusted_required_role:action")

    def test_unknown_count_or_entity_blocks_stable(self) -> None:
        """未消解 unknown 与 unknown entity 都必须 fail-closed。"""

        unknowns = voice_record("Play_vo_Annie_Move")
        unknowns["quality"]["unknown_count"] = 1
        self.assert_blocked(unknowns, "quality_unknowns_present")

        unknown_entity = voice_record("Play_vo_Annie_Move")
        unknown_entity["entity"]["kind"] = "unknown"
        self.assert_blocked(unknown_entity, "unknown_entity")

    def test_unknown_count_structure_must_be_explicit_non_boolean_integer(self) -> None:
        """缺失、bool 或非整数 unknown_count 都不得静默过关。"""

        cases: list[object] = [None, True, "0"]
        for value in cases:
            with self.subTest(value=value):
                record = voice_record("Play_vo_Annie_Move")
                if value is None:
                    del record["quality"]["unknown_count"]
                else:
                    record["quality"]["unknown_count"] = value
                self.assert_blocked(record, "quality_unknown_count_invalid")

    def test_unselected_semantic_profile_blocks_stable(self) -> None:
        """VO 与已批准 frame 都命中时仍须通过 semantic profile selector。"""

        record = voice_record("Play_vo_Annie_Move")
        record["semantic"]["profile"] = "interaction"
        self.assert_blocked(record, "semantic_profile_not_selected")

    def test_missing_zh_cn_output_blocks_stable(self) -> None:
        """zh_CN default/main 无可渲染文本时必须阻断。"""

        record = voice_record("Play_vo_Annie_Move")
        action_value = record["translation"]["arguments"]["action"]
        action_value["labels"] = {}
        action_value.pop("key")
        del record["translation"]["arguments"]["champion"]
        self.assert_blocked(record, "empty_zh_cn_default")
        self.assert_blocked(record, "empty_zh_cn_main")

    def test_renderer_diagnostic_blocks_stable(self) -> None:
        """renderer 发生 label 诊断时即使有 key fallback 也不得晋级。"""

        record = voice_record("Play_vo_Annie_Move")
        record["translation"]["arguments"]["action"]["labels"] = {}
        self.assert_blocked(record, "renderer_diagnostic:missing_label:action")

    def test_every_argument_requires_nonempty_zh_cn_label(self) -> None:
        """renderer 静默回落 en_US 时，显式 zh_CN label 门禁仍必须阻断。"""

        record = voice_record("Play_vo_Annie_Move")
        record["translation"]["arguments"]["action"]["labels"] = {"en_US": "move"}
        self.assert_blocked(record, "missing_zh_cn_label:action")

    def test_optional_missing_roles_are_warnings_not_blockers(self) -> None:
        """模板中的 optional role 缺失只计 warning，不推翻 required-role eligibility。"""

        record = voice_record("Play_vo_Annie_Move")
        del record["translation"]["arguments"]["champion"]
        report = build_pattern_report([record], self.policy)
        pattern = report["patterns"][0]

        self.assertTrue(pattern["eligibility"]["stable"])
        self.assertIn("renderer_optional_missing_role:champion", pattern["warning_counts"])

    def test_json_markdown_and_normalized_loading_are_deterministic(self) -> None:
        """JSON/Markdown 生成与 JSONL 加载均保持确定性且不包含本地路径。"""

        approved = voice_record("Play_vo_Annie_Dance")
        set_voice_action(approved, "dance", "跳舞")
        records = [approved, voice_record("Play_sfx_Annie_Move")]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shard = root / "normalized" / "by-entity" / "champions" / "Annie.jsonl"
            shard.parent.mkdir(parents=True)
            shard.write_text(
                "\n".join(json.dumps(record, ensure_ascii=False) for record in reversed(records)) + "\n",
                encoding="utf-8",
            )
            loaded = load_normalized_records(root / "normalized")
            report = build_pattern_report(loaded, self.policy_approving(approved))
            first_json = root / "first" / "patterns.json"
            first_md = root / "first" / "patterns.md"
            second_json = root / "second" / "patterns.json"
            second_md = root / "second" / "patterns.md"
            write_pattern_report(report, first_json, first_md)
            write_pattern_report(report, second_json, second_md)

            self.assertEqual(first_json.read_bytes(), second_json.read_bytes())
            self.assertEqual(first_md.read_bytes(), second_md.read_bytes())
            markdown = render_pattern_markdown(report)
            self.assertIn("## Support matrix", markdown)
            self.assertIn("## Policy-approved scope details", markdown)
            self.assertIn("self_action/voice/movement", markdown)
            self.assertIn("keys: action=dance", markdown)
            self.assertIn("## Blocking reasons", markdown)
            self.assertNotIn("## SFX candidates", markdown)
            self.assertNotIn("## All patterns", markdown)
            self.assertNotIn(str(root), first_json.read_text(encoding="utf-8"))

            candidates = report["accepted_corpus_candidates"]
            self.assertEqual(len(candidates), 1)
            candidate = candidates[0]
            self.assertEqual(
                set(candidate),
                {
                    "pattern_id",
                    "frame_type",
                    "signature",
                    "required_roles",
                    "expected_arguments",
                    "raw_event_name",
                    "zh_cn_default",
                    "zh_cn_main",
                    "scope_status",
                    "decision_id",
                    "review_status",
                },
            )
            self.assertEqual(candidate["review_status"], "pending")
            self.assertEqual(candidate["scope_status"], "approved")
            self.assertEqual(candidate["decision_id"], "v1-semantic-fix-2026-08-11")
            self.assertEqual(candidate["required_roles"], ["action"])
            self.assertEqual(candidate["expected_arguments"]["roles"], ["action", "champion"])
            self.assertEqual(
                candidate["expected_arguments"]["signature_keys"],
                {"action": "dance", "qualifier": None},
            )
            self.assertTrue(candidate["zh_cn_default"])
            self.assertTrue(candidate["zh_cn_main"])

    def test_markdown_top_sfx_is_limited_summary_sorted_by_record_count(self) -> None:
        """Markdown 只展示按记录量排序的有限 SFX 摘要，JSON 仍保留全量。"""

        attack_ahri = voice_record("Play_sfx_Ahri_Attack", champion="Ahri")
        attack_ahri["translation"]["arguments"]["action"] = argument("action", "attack", "攻击")
        attack_annie = voice_record("Play_sfx_Annie_Attack")
        attack_annie["translation"]["arguments"]["action"] = argument("action", "attack", "攻击")
        move = voice_record("Play_sfx_Annie_Move")
        report = build_pattern_report([move, attack_annie, attack_ahri], self.policy)
        markdown = render_pattern_markdown(report)

        self.assertEqual(len(report["sfx_candidates"]), 2)
        self.assertIn("## Top SFX candidates", markdown)
        self.assertIn("at most 20 candidates", markdown)
        self.assertLess(markdown.index("Play_sfx_Ahri_Attack"), markdown.index("Play_sfx_Annie_Move"))

    def test_cli_accepts_all_inventory_paths(self) -> None:
        """薄 CLI 接受四个固定路径参数并同时生成 JSON/Markdown。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shard = root / "normalized" / "by-entity" / "champions" / "Annie.jsonl"
            shard.parent.mkdir(parents=True)
            shard.write_text(json.dumps(voice_record("Play_vo_Annie_Move")) + "\n", encoding="utf-8")
            output_json = root / "report.json"
            output_md = root / "report.md"

            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "report_translation_patterns.py"),
                    "--normalized-root",
                    str(root / "normalized"),
                    "--policy",
                    str(POLICY_PATH),
                    "--output-json",
                    str(output_json),
                    "--output-md",
                    str(output_md),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output_json.is_file())
            self.assertTrue(output_md.is_file())
            self.assertIn("stable_proposals=1", result.stdout)

    def test_cli_reports_expected_failure_without_traceback(self) -> None:
        """预期文件/内容错误只输出短消息并返回 1。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "report_translation_patterns.py"),
                    "--normalized-root",
                    str(root / "missing"),
                    "--policy",
                    str(POLICY_PATH),
                    "--output-json",
                    str(root / "report.json"),
                    "--output-md",
                    str(root / "report.md"),
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("translation_patterns error:", result.stderr)
            self.assertIn("normalized root 不存在", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((root / "report.json").exists())

    def test_normalized_loader_fails_closed_for_missing_or_empty_by_entity(self) -> None:
        """缺 by-entity 或零记录时不生成伪成功空报告。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "缺少 by-entity"):
                load_normalized_records(root)

            (root / "by-entity").mkdir()
            with self.assertRaisesRegex(ValueError, "没有可盘点记录"):
                load_normalized_records(root)

    def assert_blocked(self, record: dict[str, object], reason: str) -> None:
        """断言单条记录未进入 Stable proposals 且包含指定原因。

        Args:
            record: 待评估 normalized 记录。
            reason: 预期 blocking reason。
        """

        report = build_pattern_report([record], self.policy)
        self.assertEqual(report["stable_proposals"], [])
        self.assertIn(reason, report["patterns"][0]["blocking_reasons"])

    def policy_approving(self, *records: dict[str, object]) -> PatternPolicy:
        """返回只精确批准给定测试记录 canonical pattern 的策略。"""

        pattern_ids = frozenset(
            pattern_id_for_signature(canonical_pattern_signature(record, self.policy)) for record in records
        )
        decisions = replace(self.policy.promotion_decisions, approved_pattern_ids=pattern_ids)
        return replace(self.policy, promotion_decisions=decisions)


def voice_record(raw: str, *, champion: str = "Annie") -> dict[str, object]:
    """构造满足 voice_event Stable 候选条件的最小 normalized 记录。

    Args:
        raw: 原始事件名。
        champion: 参数化英雄名。

    Returns:
        测试用 normalized 记录。
    """

    return {
        "raw_event_name": raw,
        "entity": {"kind": "champion", "alias": champion},
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
                "champion": argument("champion", champion.lower(), champion),
                "action": argument("action", "move", "移动"),
            },
        },
    }


def set_voice_action(record: dict[str, object], key: str, zh_cn: str) -> None:
    """替换 voice_event 的 action argument。"""

    record["translation"]["arguments"]["action"] = argument("action", key, zh_cn)


def ability_record() -> dict[str, object]:
    """构造用户已批准 scope 内的 ability_cast_event。"""

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


def announcer_record() -> dict[str, object]:
    """构造结构合格但明确 defer 的 announcer_event。"""

    record = voice_record("Play_vo_Announcer_DoubleKill")
    record["entity"] = {"kind": "map_or_system", "alias": "Announcer"}
    record["semantic"] = {
        "profile": "map_or_system",
        "primary_family": "announcer",
        "slots": {"trigger_family": "announcer"},
    }
    record["translation"]["frame_type"] = "announcer_event"
    record["translation"]["arguments"] = {"event": argument("event", "double_kill", "双杀")}
    return record


def dialogue_without_turn_record() -> dict[str, object]:
    """构造 turn 缺省且明确 defer 的对话事件。"""

    record = voice_record("Play_vo_Annie_DialogueMorgana")
    record["semantic"] = {
        "profile": "dialogue_chain",
        "primary_family": "dialogue",
        "slots": {"trigger_family": "dialogue"},
    }
    record["translation"]["frame_type"] = "champion_dialogue_event"
    record["translation"]["arguments"] = {
        "champion": argument("champion", "annie", "安妮"),
        "partner": argument("partner", "morgana", "莫甘娜"),
        "base_trigger": argument("base_trigger", "move", "移动"),
    }
    return record


def kill_milestone_record() -> dict[str, object]:
    """构造带 milestone role 且明确 defer 的击杀事件。"""

    record = voice_record("Play_vo_Annie_PentaKill")
    record["semantic"] = {
        "profile": "kill_or_score",
        "primary_family": "kill",
        "slots": {"trigger_family": "kill"},
    }
    record["translation"]["frame_type"] = "kill_or_destroy_event"
    record["translation"]["arguments"] = {
        "champion": argument("champion", "annie", "安妮"),
        "trigger": argument("trigger", "kill", "击杀"),
        "milestone": argument("milestone", "penta_kill", "五杀"),
    }
    return record


def argument(role: str, key: str, zh_cn: str) -> dict[str, object]:
    """构造无 fallback 的 translation argument。

    Args:
        role: argument role。
        key: 稳定参数键。
        zh_cn: 中文 label。

    Returns:
        测试用 argument。
    """

    return {
        "role": role,
        "key": key,
        "labels": {"zh_CN": zh_cn, "en_US": key},
        "confidence": "high",
        "fallback": False,
    }


if __name__ == "__main__":
    unittest.main()
