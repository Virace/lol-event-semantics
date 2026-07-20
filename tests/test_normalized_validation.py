"""normalized 产物二次校验测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.normalized_translation_model import FRAME_REGISTRY, FrameSpec, RenderOptions
from lol_event_semantics.normalized_translation_renderer import render_translation
from lol_event_semantics.normalized_validation import validate_normalized_root


class NormalizedValidationTests(unittest.TestCase):
    """验证 normalized 输出校验器。"""

    def test_valid_minimal_output_has_no_issues(self) -> None:
        """结构完整且文案干净的最小产物不产生问题。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [annie_cast_record()])

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)
        self.assertEqual(report.record_count, 1)
        self.assertEqual(report.entity_count, 1)

    def test_manifest_and_summary_count_mismatch_are_errors(self) -> None:
        """manifest 与 summary 计数必须和实际 JSONL 一致。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [minimal_record("Play_vo_Annie_AnnieQ_OnCast")])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["record_count"] = 2
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            summary = json.loads((root / "entity-summary.json").read_text(encoding="utf-8"))
            summary[0]["record_count"] = 2
            (root / "entity-summary.json").write_text(json.dumps(summary), encoding="utf-8")

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("manifest_record_count_mismatch", codes)
        self.assertIn("summary_record_count_mismatch", codes)
        self.assertGreaterEqual(report.error_count, 2)

    def test_case_variant_entities_are_errors(self) -> None:
        """最终实体不能只因大小写差异拆成多个分片。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [
                minimal_record("Play_vo_BelVeth_TestA", alias="BelVeth"),
                minimal_record("Play_vo_Belveth_TestB", alias="Belveth"),
            ]
            write_normalized_fixture(root, [rows[0]])
            second_shard = root / "by-entity" / "champions" / "Belveth__2.jsonl"
            second_shard.write_text(json.dumps(rows[1], ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["entity_count"] = 2
            manifest["record_count"] = 2
            (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
            (root / "entity-summary.json").write_text(
                json.dumps(
                    [
                        {
                            "kind": "champion",
                            "alias": "BelVeth",
                            "record_count": 1,
                            "path": "by-entity/champions/BelVeth.jsonl",
                        },
                        {
                            "kind": "champion",
                            "alias": "Belveth",
                            "record_count": 1,
                            "path": "by-entity/champions/Belveth__2.jsonl",
                        },
                    ],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("case_variant_entity_alias", codes)
        self.assertGreaterEqual(report.error_count, 1)

    def test_quality_risks_are_warnings(self) -> None:
        """可生成但不像最终产物的问题应作为质量警告输出。"""

        record = minimal_record("Play_sfx_Aatroxskin33_Joke3D_loop", alias="Aatroxskin33")
        record["display"]["zh_cn_short"] = "Play_sfx_Aatroxskin33_Joke3D_loop"
        record["semantic"]["slots"]["low_trust_rule"] = {"trust": "low"}

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("suspicious_champion_alias", codes)
        self.assertIn("technical_token_in_display", codes)
        self.assertIn("low_trust_rule_in_output", codes)
        self.assertEqual(report.error_count, 0)
        self.assertGreaterEqual(report.warning_count, 3)

    def test_translation_missing_required_role_is_error(self) -> None:
        """ability_state_change 缺 phase 是硬错误。"""

        record = galio_translation_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        del arguments["phase"]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_frame_required_role_missing", codes)
        self.assertGreater(report.error_count, 0)

    def test_translation_fallback_label_is_warning(self) -> None:
        """fallback skin label 是质量 warning。"""

        record = galio_translation_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        arguments["skin"] = {
            "role": "skin",
            "token": "Skin13",
            "labels": {"zh_CN": "Skin13"},
            "confidence": "low",
            "provenance": {"kind": "fallback", "path": "raw_event_name"},
            "fallback": True,
            "fallback_reason": "missing_skin_label",
            "omit_when_context_known": ["skin"],
        }
        record["display"]["zh_cn_detail"] = "加里奥 Skin13 W技能被动防御状态结束"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_label_fallback", codes)
        self.assertEqual(report.error_count, 0)

    def test_rendered_user_text_with_technical_token_is_error(self) -> None:
        """用户渲染文案不能包含 OnBuffDeactivate。"""

        record = galio_translation_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        phase = arguments["phase"]
        assert isinstance(phase, dict)
        labels = phase["labels"]
        assert isinstance(labels, dict)
        labels["zh_CN"] = "OnBuffDeactivate"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("technical_token_in_rendered_user_text", codes)

    def test_display_mismatch_with_renderer_is_error(self) -> None:
        """已覆盖 frame 的 display 必须和 renderer 输出一致。"""

        record = galio_translation_record()
        record["display"]["zh_cn_short"] = "Galio W 技能"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_display_mismatch", codes)


class AnnouncerEventValidationTests(unittest.TestCase):
    """验证批次5a announcer_event frame 注册与必需角色校验。"""

    def test_valid_announcer_record_has_no_issues(self) -> None:
        """完整 announcer_event 记录（event+team）不产生问题。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [announcer_translation_record()])

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)

    def test_announcer_missing_event_role_is_error(self) -> None:
        """announcer_event 缺必需 event 角色是硬错误。"""

        record = announcer_translation_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        del arguments["event"]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_frame_required_role_missing", codes)
        self.assertGreater(report.error_count, 0)


class MediaBaseActionStateValidationTests(unittest.TestCase):
    """验证批次5b：skin_action_state 缺 skin 合法化与 media_base 家族注册。"""

    def test_skinless_action_state_record_has_no_issues(self) -> None:
        """基础皮肤形态（无 skin 角色）的 skin_action_state 记录不产生问题。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [media_base_action_record()])

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)

    def test_action_state_missing_action_role_is_still_error(self) -> None:
        """skin_action_state 缺 action 仍是硬错误（放宽仅限 skin 角色）。"""

        record = media_base_action_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        del arguments["action"]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_frame_required_role_missing", codes)
        self.assertGreater(report.error_count, 0)

    def test_media_base_without_translation_is_supported_frame_warning(self) -> None:
        """media_base 已注册为承接家族：缺 translation 报 supported frame warning。"""

        record = media_base_action_record()
        del record["translation"]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_missing_for_supported_frame", codes)

    def test_tier3_record_without_translation_is_not_missing(self) -> None:
        """Tier 口径收口：Tier3 纯技术记录刻意无 translation，不计 missing。"""

        record = media_base_action_record()
        del record["translation"]
        display = record["display"]
        assert isinstance(display, dict)
        display["tier"] = 3
        display["zh_cn_short"] = None

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertNotIn("translation_missing_for_supported_frame", codes)


class FrameRegistryInjectionTests(unittest.TestCase):
    """验证 frame 注册表是校验与渲染的单一事实源（T4.2）。

    注入临时 frame 而非真加生产 frame：只在注册表加一条，required_roles
    校验（normalized_validation）与模板渲染（renderer）必须同时看到，
    证明两侧都直接读 FRAME_REGISTRY、不存在第二份散落配置。
    """

    PROBE_FRAME = "registry_probe_frame"

    def setUp(self) -> None:
        """注入临时 probe frame，测试结束后清理。"""

        self.assertNotIn(self.PROBE_FRAME, FRAME_REGISTRY)
        FRAME_REGISTRY[self.PROBE_FRAME] = FrameSpec(
            required_roles=("probe",),
            templates={"zh_CN": {"default": ("probe",), "main": ("probe",)}},
        )
        self.addCleanup(FRAME_REGISTRY.pop, self.PROBE_FRAME, None)

    def probe_record(self) -> dict[str, object]:
        """构造使用 probe frame 的最小 normalized 记录。"""

        record = minimal_record("Play_vo_Annie_RegistryProbe")
        record["translation"] = {
            "schema_version": 1,
            "frame_type": self.PROBE_FRAME,
            "frame_version": 1,
            "frame_confidence": "high",
            "arguments": {
                "probe": {
                    "role": "probe",
                    "labels": {"zh_CN": "探针动作"},
                    "confidence": "high",
                    "provenance": {"kind": "test"},
                },
            },
            "provenance": {"builder": "test"},
        }
        record["display"] = {
            "zh_cn_short": "探针动作",
            "zh_cn_detail": "探针动作",
            "debug_label": "Play_vo_Annie_RegistryProbe",
        }
        return record

    def test_registered_frame_is_rendered_and_validated(self) -> None:
        """注册表新增一条后，渲染有模板、校验认 frame_type，无任何问题。"""

        record = self.probe_record()
        translation = record["translation"]
        assert isinstance(translation, dict)

        result = render_translation(translation, RenderOptions(locale="zh_CN", mode="default"))
        self.assertEqual(result.text, "探针动作")
        self.assertNotIn("missing_render_template", result.diagnostics)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0)
        self.assertEqual(report.warning_count, 0)

    def test_registered_frame_required_role_is_enforced(self) -> None:
        """注册表声明的 required_roles 缺失时按硬错误报出。"""

        record = self.probe_record()
        translation = record["translation"]
        assert isinstance(translation, dict)
        arguments = translation["arguments"]
        assert isinstance(arguments, dict)
        del arguments["probe"]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("translation_frame_required_role_missing", codes)
        self.assertNotIn("translation_frame_type_unknown", codes)


class DisplayLosslessnessTests(unittest.TestCase):
    """验证 display 无损性校验（display_drops_parsed_slot）。"""

    def validate(self, record: dict[str, object]) -> object:
        """写出单记录 fixture 并执行校验。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])
            return validate_normalized_root(root)

    def drop_issues(self, record: dict[str, object]) -> list[object]:
        """返回记录触发的 display_drops_parsed_slot 问题列表。"""

        report = self.validate(record)
        return [issue for issue in report.issues if issue.code == "display_drops_parsed_slot"]

    def test_stop_verb_missing_in_detail_is_warning(self) -> None:
        """Stop 壳动词未落入 detail 时报警。"""

        record = minimal_record("Stop_sfx_Annie_AnnieQ_cast")

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertIn("shell_verb=stop", issues[0].message)

    def test_stop_verb_present_in_detail_is_clean(self) -> None:
        """detail 带「停止」的 Stop 记录不报警。"""

        record = minimal_record("Stop_sfx_Annie_AnnieQ_cast")
        record["display"]["zh_cn_short"] = "Annie Q 技能音效停止"
        record["display"]["zh_cn_detail"] = "Annie Q 技能音效停止"

        self.assertEqual(self.drop_issues(record), [])

    def test_mute_verb_missing_in_detail_is_warning(self) -> None:
        """Mute 壳动词未落入 detail 时报警；带「静音」则不报。"""

        record = minimal_record("Mute_vo_Annie_AnnieQ_cast")

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("shell_verb=mute", issues[0].message)

        record["display"]["zh_cn_short"] = "Annie Q 技能语音静音"
        record["display"]["zh_cn_detail"] = "Annie Q 技能语音静音"
        self.assertEqual(self.drop_issues(record), [])

    def test_unmute_verb_missing_in_detail_is_warning(self) -> None:
        """UnMute 壳动词未落入 detail 时报警；「取消静音」含「静音」则不报。"""

        record = minimal_record("UnMute_vo_Annie_AnnieQ_cast")

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("shell_verb=unmute", issues[0].message)

        record["display"]["zh_cn_short"] = "Annie Q 技能语音取消静音"
        record["display"]["zh_cn_detail"] = "Annie Q 技能语音取消静音"
        self.assertEqual(self.drop_issues(record), [])

    def test_shell_verb_match_is_case_insensitive(self) -> None:
        """壳动词判定大小写不敏感：STOP_ 前缀同样触发。"""

        record = minimal_record("STOP_sfx_Annie_AnnieQ_cast")

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("shell_verb=stop", issues[0].message)

    def test_phase_with_pure_fallback_detail_is_warning(self) -> None:
        """phase 非空且 detail 为纯兜底模板时报警。"""

        record = minimal_record("Play_sfx_Annie_Spawn3D_buffactivate")
        record["semantic"]["slots"] = {"trigger_family": "misc", "phase": "buff_activate"}
        record["display"]["zh_cn_short"] = "Annie事件"
        record["display"]["zh_cn_detail"] = "Annie事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("phase=buff_activate", issues[0].message)

    def test_phase_with_paren_fallback_detail_is_not_flagged(self) -> None:
        """带括注的兜底 detail 不在保守口径内（宁可漏报）。"""

        record = minimal_record("Play_sfx_AnnieSkin01_Spawn3D_buffactivate")
        record["semantic"]["slots"] = {"trigger_family": "misc", "phase": "buff_activate"}
        record["display"]["zh_cn_short"] = "Annie事件"
        record["display"]["zh_cn_detail"] = "Annie事件（皮肤 Skin01）"

        self.assertEqual(self.drop_issues(record), [])

    def test_counterpart_with_pure_fallback_detail_is_warning(self) -> None:
        """counterpart 非空且 detail 为纯兜底模板时报警。"""

        record = minimal_record("Play_vo_Annie_FirstEncounter3DAkali")
        record["semantic"]["primary_family"] = "interaction"
        record["semantic"]["slots"] = {
            "trigger_family": "interaction",
            "counterpart": {"alias": "Akali", "kind": "champion", "label": "Akali"},
        }
        record["display"]["zh_cn_short"] = "Annie语音事件"
        record["display"]["zh_cn_detail"] = "Annie语音事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("counterpart=Akali", issues[0].message)

    def test_counterpart_drop_in_supported_family_is_error(self) -> None:
        """已承接家族（first_encounter）的 counterpart 丢弃升为硬错误（T3.2 收紧门禁）。"""

        record = minimal_record("Play_vo_Annie_FirstEncounter3DAkali")
        record["semantic"]["primary_family"] = "first_encounter"
        record["semantic"]["slots"] = {
            "trigger_family": "first_encounter",
            "counterpart": {"alias": "Akali", "kind": "champion", "label": "Akali"},
        }
        record["display"]["zh_cn_short"] = "Annie语音事件"
        record["display"]["zh_cn_detail"] = "Annie语音事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertIn("counterpart=Akali", issues[0].message)

    def test_counterpart_drop_in_uncovered_family_stays_warning(self) -> None:
        """未承接家族（encounter）的 counterpart 丢弃维持 warning。"""

        record = minimal_record("Play_vo_Yunara_SecondEncounter3DKennen", alias="Yunara")
        record["semantic"]["primary_family"] = "encounter"
        record["semantic"]["slots"] = {
            "trigger_family": "encounter",
            "counterpart": {"kind": "unresolved_entity", "raw": "Kennen"},
        }
        record["display"]["zh_cn_short"] = "Yunara事件"
        record["display"]["zh_cn_detail"] = "Yunara事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertIn("counterpart=Kennen", issues[0].message)

    def test_chat_partner_with_pure_fallback_detail_is_warning(self) -> None:
        """非 dialogue 家族的 chat_partner 丢弃维持 warning。"""

        record = minimal_record("Play_vo_Annie_Chat3DAhri")
        record["semantic"]["primary_family"] = "interaction"
        record["semantic"]["slots"] = {
            "trigger_family": "interaction",
            "chat_partner": {"alias": "Ahri", "kind": "champion"},
        }
        record["display"]["zh_cn_short"] = "Annie事件"
        record["display"]["zh_cn_detail"] = "Annie事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "warning")
        self.assertIn("chat_partner=Ahri", issues[0].message)

    def test_chat_partner_drop_in_dialogue_family_is_error(self) -> None:
        """已承接 dialogue 家族的 chat_partner 丢弃升为硬错误（批次4 收紧门禁）。"""

        record = minimal_record("Play_vo_Annie_Chat3DAhri_Move3DAhri_Chat2")
        record["semantic"]["primary_family"] = "dialogue"
        record["semantic"]["slots"] = {
            "trigger_family": "dialogue",
            "chat_partner": {"raw": "Ahri", "kind": "unresolved_entity"},
        }
        record["display"]["zh_cn_short"] = "Annie事件"
        record["display"]["zh_cn_detail"] = "Annie事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, "error")
        self.assertIn("chat_partner=Ahri", issues[0].message)

    def test_counterpart_with_meaningful_detail_is_not_flagged(self) -> None:
        """非兜底 detail 即使未含对手名也不报警（保守口径）。"""

        record = minimal_record("Play_vo_Annie_FirstEncounter3DAkali")
        record["semantic"]["primary_family"] = "interaction"
        record["semantic"]["slots"] = {
            "trigger_family": "interaction",
            "counterpart": {"alias": "Akali", "kind": "champion", "label": "Akali"},
        }
        record["display"]["zh_cn_short"] = "安妮遭遇敌人语音"
        record["display"]["zh_cn_detail"] = "安妮首次遭遇敌人语音"

        self.assertEqual(self.drop_issues(record), [])

    def test_multiple_dropped_slots_emit_single_issue(self) -> None:
        """一条记录同时丢多个槽位时只发一条问题并列出全部槽位。"""

        record = minimal_record("Stop_vo_Annie_Chat3DAhri_buffactivate")
        record["semantic"]["primary_family"] = "interaction"
        record["semantic"]["slots"] = {
            "trigger_family": "interaction",
            "phase": "buff_activate",
            "counterpart": {"alias": "Ahri", "kind": "champion"},
        }
        record["display"]["zh_cn_short"] = "Annie事件"
        record["display"]["zh_cn_detail"] = "Annie事件"

        issues = self.drop_issues(record)

        self.assertEqual(len(issues), 1)
        self.assertIn("shell_verb=stop", issues[0].message)
        self.assertIn("phase=buff_activate", issues[0].message)
        self.assertIn("counterpart=Ahri", issues[0].message)


class DisplayTierValidationTests(unittest.TestCase):
    """验证 display_short_missing 按 tier 区分 error 门槛。"""

    def validate(self, record: dict[str, object]) -> object:
        """写出单记录 fixture 并执行校验。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])
            return validate_normalized_root(root)

    def test_tier3_null_short_is_not_error(self) -> None:
        """tier3 记录 zh_cn_short=null 合法，不报 display_short_missing。"""

        record = minimal_record("Play_sfx_AatroxSkin01_AatroxBasicAttack2_OnCast")
        record["display"]["tier"] = 3
        record["display"]["zh_cn_short"] = None

        report = self.validate(record)

        codes = {issue.code for issue in report.issues}
        self.assertNotIn("display_short_missing", codes)
        self.assertEqual(report.error_count, 0)

    def test_tier1_null_short_is_still_error(self) -> None:
        """tier1 记录缺 zh_cn_short 仍是硬错误。"""

        record = minimal_record("Play_vo_Annie_AnnieQ_OnCast")
        record["display"]["tier"] = 1
        record["display"]["zh_cn_short"] = None

        report = self.validate(record)

        codes = {issue.code for issue in report.issues}
        self.assertIn("display_short_missing", codes)
        self.assertGreaterEqual(report.error_count, 1)

    def test_tier2_null_short_is_still_error(self) -> None:
        """tier2 记录缺 zh_cn_short 仍是硬错误（只有 tier3 豁免）。"""

        record = minimal_record("Play_sfx_AkshanSkin01_AkshanWRevive_buffcast")
        record["display"]["tier"] = 2
        record["display"]["zh_cn_short"] = None

        report = self.validate(record)

        codes = {issue.code for issue in report.issues}
        self.assertIn("display_short_missing", codes)
        self.assertGreaterEqual(report.error_count, 1)

    def test_missing_tier_field_still_requires_short(self) -> None:
        """没有 display.tier 字段时按旧契约要求 zh_cn_short（向后兼容）。"""

        record = minimal_record("Play_vo_Annie_AnnieQ_OnCast")
        record["display"]["zh_cn_short"] = None

        report = self.validate(record)

        codes = {issue.code for issue in report.issues}
        self.assertIn("display_short_missing", codes)


class NormalizedValidationCliTests(unittest.TestCase):
    """验证 normalized 校验 CLI。"""

    def test_cli_writes_report(self) -> None:
        """CLI 可写出 JSON 报告并在只有 warning 时退出 0。"""

        record = minimal_record("Play_sfx_Aatroxskin33_Joke3D_loop", alias="Aatroxskin33")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output_path = root / "validation.json"
            write_normalized_fixture(root, [record])

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/validate_normalized_outputs.py",
                    "--normalized-root",
                    str(root),
                    "--output-json",
                    str(output_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["summary"]["error_count"], 0)
        self.assertGreaterEqual(payload["summary"]["warning_count"], 1)

    def test_cli_can_fail_on_warning(self) -> None:
        """开启 fail-on-warning 时，只有 warning 也返回 2。"""

        record = minimal_record("Play_sfx_Aatroxskin33_Joke3D_loop", alias="Aatroxskin33")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_fixture(root, [record])

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/validate_normalized_outputs.py",
                    "--normalized-root",
                    str(root),
                    "--fail-on-warning",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2, result.stderr)


def minimal_record(raw_event_name: str, alias: str = "Annie") -> dict[str, object]:
    """构造最小 normalized 记录。"""

    return {
        "schema_version": 1,
        "raw_event_name": raw_event_name,
        "game_version": "cumulative",
        "entity": {"kind": "champion", "alias": alias},
        "semantic": {
            "profile": "spell_event",
            "primary_family": "spell",
            "slots": {"trigger_family": "spell", "spell_key": "q"},
            "confidence": "high",
            "requires_context": False,
        },
        "display": {
            "zh_cn_short": f"{alias} Q 技能释放",
            "zh_cn_detail": f"{alias} Q 技能释放",
            "debug_label": raw_event_name,
        },
        "quality": {"unknown_count": 0, "candidate_count": 1, "diagnostics": []},
        "source_paths": ["raw/events/champions/1.txt"],
    }


def galio_translation_record() -> dict[str, object]:
    """构造带 ability_state_change translation 的 Galio 记录。"""

    record = minimal_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate", alias="Galio")
    record["semantic"]["slots"] = {
        "trigger_family": "spell",
        "spell_key": "w",
        "phase": "on_buff_deactivate",
        "skin_token": "Skin13",
        "contextual_alias": {"component": "passive_defense", "label": "W 被动防御"},
    }
    record["translation"] = minimal_translation()
    record["display"] = {
        "zh_cn_short": "W技能被动防御状态结束",
        "zh_cn_detail": "加里奥 地狱火 W技能被动防御状态结束",
        "debug_label": "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate",
    }
    return record


def annie_cast_record() -> dict[str, object]:
    """构造带 ability_cast_event translation 的 Annie Q 施放记录。"""

    record = minimal_record("Play_sfx_Annie_AnnieQ_cast")
    record["semantic"]["slots"] = {"trigger_family": "spell", "spell_key": "q", "phase": "cast"}
    record["translation"] = {
        "schema_version": 1,
        "frame_type": "ability_cast_event",
        "frame_version": 1,
        "frame_confidence": "high",
        "arguments": {
            "champion": {
                "role": "champion",
                "alias": "Annie",
                "labels": {"zh_CN": "Annie"},
                "confidence": "high",
                "provenance": {"kind": "reference", "path": "reference/champions/Annie.yaml"},
                "omit_when_context_known": ["champion"],
            },
            "ability_slot": {
                "role": "ability_slot",
                "value": "q",
                "labels": {"zh_CN": "Q技能"},
                "confidence": "high",
                "provenance": {"kind": "event_token", "path": "raw_event_name"},
            },
            "phase": {
                "role": "phase",
                "key": "cast",
                "labels": {"zh_CN": "释放"},
                "confidence": "high",
                "provenance": {"kind": "phase", "path": "rules/phases.yaml"},
            },
        },
        "provenance": {"builder": "test"},
    }
    record["display"] = {
        "zh_cn_short": "Q技能释放",
        "zh_cn_detail": "Annie Q技能释放",
        "debug_label": "Play_sfx_Annie_AnnieQ_cast",
    }
    return record


def media_base_action_record() -> dict[str, object]:
    """构造带 skin_action_state translation 的 media_base 基础皮肤动作记录（批次5b）。"""

    record = minimal_record("Play_sfx_Galio_Base_Death3D_buffactivate", alias="Galio")
    record["semantic"] = {
        "profile": "map_or_system",
        "primary_family": "media_base",
        "slots": {
            "trigger_family": "media_base",
            "sfx_scope": "champion_sfx",
            "phase": "buff_activate",
            "cue_path": ["Base", "Death3D", "buffactivate"],
        },
        "confidence": "medium",
        "requires_context": False,
    }
    record["translation"] = {
        "schema_version": 1,
        "frame_type": "skin_action_state",
        "frame_version": 1,
        "frame_confidence": "medium",
        "arguments": {
            "champion": {
                "role": "champion",
                "alias": "Galio",
                "labels": {"zh_CN": "加里奥"},
                "confidence": "high",
                "provenance": {"kind": "reference", "path": "reference/champions/Galio.yaml"},
                "omit_when_context_known": ["champion"],
            },
            "action": {
                "role": "action",
                "key": "death",
                "token": "Death3D",
                "labels": {"zh_CN": "死亡", "en_US": "death"},
                "confidence": "high",
                "provenance": {"source": "semantic_slots.cue_path"},
            },
            "phase": {
                "role": "phase",
                "key": "state_activate",
                "labels": {"zh_CN": "状态启动", "en_US": "state started"},
                "confidence": "high",
                "provenance": {"source": "semantic_slots.phase"},
            },
        },
        "provenance": {"builder": "test"},
    }
    record["display"] = {
        "zh_cn_short": "死亡状态启动",
        "zh_cn_detail": "加里奥 死亡状态启动",
        "debug_label": "Play_sfx_Galio_Base_Death3D_buffactivate",
    }
    return record


def announcer_translation_record() -> dict[str, object]:
    """构造带 announcer_event translation 的播报员记录。"""

    record = minimal_record("Play_vo_Announcer_Female1_AceEnemyTeam", alias="Announcer")
    record["semantic"] = {
        "profile": "map_or_system",
        "primary_family": "announcer",
        "slots": {
            "trigger_family": "system_voice",
            "system_voice_kind": "announcer",
            "mode": "Female1",
            "cue_path": ["AceEnemyTeam"],
        },
        "confidence": "high",
        "requires_context": False,
    }
    record["translation"] = {
        "schema_version": 1,
        "frame_type": "announcer_event",
        "frame_version": 1,
        "frame_confidence": "high",
        "arguments": {
            "event": {
                "role": "event",
                "key": "Ace",
                "token": "AceEnemyTeam",
                "labels": {"zh_CN": "团灭", "en_US": "Ace"},
                "confidence": "high",
                "provenance": {"source": "semantic_slots.cue_path"},
            },
            "team": {
                "role": "team",
                "key": "EnemyTeam",
                "token": "EnemyTeam",
                "labels": {"zh_CN": "敌方", "en_US": "EnemyTeam"},
                "confidence": "high",
                "provenance": {"source": "semantic_slots.cue_path"},
            },
        },
        "provenance": {"builder": "test"},
    }
    record["display"] = {
        "zh_cn_short": "敌方团灭播报",
        "zh_cn_detail": "敌方团灭播报",
        "debug_label": "Play_vo_Announcer_Female1_AceEnemyTeam",
    }
    return record


def minimal_translation() -> dict[str, object]:
    """构造最小 ability_state_change translation。"""

    return {
        "schema_version": 1,
        "frame_type": "ability_state_change",
        "frame_version": 1,
        "frame_confidence": "medium",
        "arguments": {
            "champion": {
                "role": "champion",
                "alias": "Galio",
                "labels": {"zh_CN": "加里奥"},
                "confidence": "high",
                "provenance": {"kind": "reference", "path": "reference/champions/Galio.yaml"},
                "omit_when_context_known": ["champion"],
            },
            "skin": {
                "role": "skin",
                "token": "Skin13",
                "labels": {"zh_CN": "地狱火 加里奥"},
                "confidence": "high",
                "provenance": {"kind": "reference", "path": "reference/skins/Galio/Skin13.yaml"},
                "omit_when_context_known": ["skin"],
            },
            "ability_slot": {
                "role": "ability_slot",
                "value": "w",
                "labels": {"zh_CN": "W技能"},
                "confidence": "high",
                "provenance": {"kind": "event_token", "path": "raw_event_name"},
            },
            "ability_component": {
                "role": "ability_component",
                "key": "passive_defense",
                "labels": {"zh_CN": "被动防御"},
                "confidence": "medium",
                "provenance": {"kind": "rule", "path": "rules/contextual/champions/Galio.yaml"},
            },
            "phase": {
                "role": "phase",
                "key": "state_deactivate",
                "labels": {"zh_CN": "状态结束"},
                "confidence": "high",
                "provenance": {"kind": "phase", "path": "rules/phases.yaml"},
            },
        },
        "provenance": {"builder": "test"},
    }


def write_normalized_fixture(root: Path, rows: list[dict[str, object]]) -> None:
    """写出测试 normalized 目录。"""

    shard = root / "by-entity" / "champions" / f"{rows[0]['entity']['alias']}.jsonl"
    shard.parent.mkdir(parents=True)
    shard.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "reference_version": "cumulative",
                "entity_count": 1,
                "record_count": len(rows),
                "contains_full_raw_event_index": False,
                "outputs": ["manifest.json", "entity-summary.json", "by-entity/"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "entity-summary.json").write_text(
        json.dumps(
            [
                {
                    "kind": "champion",
                    "alias": rows[0]["entity"]["alias"],
                    "record_count": len(rows),
                    "path": f"by-entity/champions/{rows[0]['entity']['alias']}.jsonl",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
