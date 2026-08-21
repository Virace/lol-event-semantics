"""规则命中审计测试。"""

from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
from lol_event_semantics.match_audit import (
    MatchAuditEvent,
    MatchAuditRecorder,
    audit_context,
    summarize_rule_hits,
    summarize_zero_hit_rules,
    write_match_audit_manifest,
    write_match_hit_summary,
    write_zero_hit_rules,
)
from lol_event_semantics.rules import ContextualAlias, ResidualModifier, RuleBook


class MatchAuditTests(unittest.TestCase):
    """验证审计事件聚合和写出。"""

    def test_summarize_rule_hits_counts_by_rule_id(self) -> None:
        """同一规则的多次命中应聚合为一行。"""

        recorder = MatchAuditRecorder()
        recorder.record(
            MatchAuditEvent(
                matcher_kind="manual_rule",
                rule_id="contextual:champion:gangplank:GangplankRUpgradeOne",
                source_path="rules/contextual/champions/Gangplank.yaml",
                raw_event_name="Play_vo_Gangplank_GangplankRUpgradeOne",
                owner="Gangplank",
                token="GangplankRUpgradeOne",
            )
        )
        recorder.record(
            MatchAuditEvent(
                matcher_kind="manual_rule",
                rule_id="contextual:champion:gangplank:GangplankRUpgradeOne",
                source_path="rules/contextual/champions/Gangplank.yaml",
                raw_event_name="Play_vo_Gangplank_GangplankRUpgradeOne",
                owner="Gangplank",
                token="GangplankRUpgradeOne",
            )
        )

        rows = summarize_rule_hits(recorder.events)

        self.assertEqual(rows[0]["hit_count"], 2)
        self.assertEqual(rows[0]["rule_id"], "contextual:champion:gangplank:GangplankRUpgradeOne")
        self.assertEqual(rows[0]["examples"], "Play_vo_Gangplank_GangplankRUpgradeOne")

    def test_write_match_hit_summary_writes_csv(self) -> None:
        """命中汇总应写出可审计 CSV。"""

        rows = [
            {
                "matcher_kind": "reference",
                "rule_id": "reference:game-data:champion:Annie",
                "source_path": "game-data",
                "hit_count": 1,
                "examples": "Play_vo_Annie_Move2DStandard",
            }
        ]

        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "rule-audit" / "match-hit-summary.csv"
            write_match_hit_summary(rows, output_path)

            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                written_rows = list(csv.DictReader(file))

        self.assertEqual(written_rows[0]["matcher_kind"], "reference")
        self.assertEqual(written_rows[0]["hit_count"], "1")

    def test_zero_hit_rules_excludes_hit_manual_rules(self) -> None:
        """0 命中报告只列出本次输入未命中的手写规则。"""

        rulebook = RuleBook(
            contextual_aliases={
                ("champion", "gangplank", "gangplankrupgradeone"): ContextualAlias(
                    scope="champion",
                    owner="Gangplank",
                    token="GangplankRUpgradeOne",
                    kind="spell_upgrade",
                    label="Cannon Barrage upgrade 1",
                ),
                ("champion", "ahri", "ahrir"): ContextualAlias(
                    scope="champion",
                    owner="Ahri",
                    token="AhriR",
                    kind="spell",
                    label="Spirit Rush",
                ),
            },
            residual_modifiers={
                "empowered": ResidualModifier(
                    token="Empowered",
                    key="empowered",
                    kind="spell_modifier",
                    label="Empowered",
                )
            },
        )
        events = [
            MatchAuditEvent(
                matcher_kind="manual_contextual_alias",
                rule_id="contextual:champion:Gangplank:GangplankRUpgradeOne",
                source_path="manual_contextual_alias",
                raw_event_name="Play_vo_Gangplank_GangplankRUpgradeOne",
                owner="Gangplank",
                token="GangplankRUpgradeOne",
            )
        ]

        rows = summarize_zero_hit_rules(rulebook, events)
        rule_ids = {row["rule_id"] for row in rows}

        self.assertNotIn("contextual:champion:Gangplank:GangplankRUpgradeOne", rule_ids)
        self.assertIn("contextual:champion:Ahri:AhriR", rule_ids)
        self.assertIn("residual:spell_modifier:empowered", rule_ids)

        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "zero-hit-rules.csv"
            write_zero_hit_rules(rows, output_path)
            with output_path.open("r", encoding="utf-8-sig", newline="") as file:
                written_rows = list(csv.DictReader(file))

        self.assertEqual(written_rows[0]["reason_hint"], "not matched by current input; not a deletion signal")

    def test_write_manifest_records_run_scope(self) -> None:
        """manifest 应记录输入范围和参考路径。"""

        with tempfile.TemporaryDirectory() as temp:
            output_path = Path(temp) / "manifest.json"
            write_match_audit_manifest(
                output_path,
                input_scope="full_raw_root",
                raw_count=10,
                reference_version="16.5",
                reference_root=Path("reference"),
                command="scripts/decompose_events.py ...",
            )

            payload = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["input_scope"], "full_raw_root")
        self.assertEqual(payload["raw_count"], 10)
        self.assertEqual(payload["reference_root"], "reference")

    def test_reference_and_contextual_hits_are_recorded_from_enrichment(self) -> None:
        """参考实体和人工上下文规则命中应进入审计 recorder。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "game-data"
            v1 = root / "global" / "default" / "v1"
            champion_root = v1 / "champions"
            champion_root.mkdir(parents=True)
            (v1 / "maps.json").write_text('[{"id": 12, "name": "Howling Abyss"}]', encoding="utf-8")
            (v1 / "champion-summary.json").write_text(
                json.dumps([{"id": 4, "alias": "TwistedFate", "name": "Twisted Fate"}]),
                encoding="utf-8",
            )
            (champion_root / "4.json").write_text(
                json.dumps({"id": 4, "alias": "TwistedFate", "name": "Twisted Fate", "spells": []}),
                encoding="utf-8",
            )
            reference = ReferenceIndex.from_game_data_root(root)
            recorder = MatchAuditRecorder()

            with audit_context(recorder):
                for raw_name in [
                    "Play_sfx_Env_Map12_Ambience",
                    "Play_sfx_TwistedFate_Gate_OnBuffActivate",
                ]:
                    enrich_with_reference(
                        normalize_semantics(decompose_event_name(raw_name, game_version="16.5")),
                        reference,
                    )

        matcher_kinds = {event.matcher_kind for event in recorder.events}
        self.assertIn("reference", matcher_kinds)
        self.assertIn("manual_contextual_alias", matcher_kinds)
        self.assertTrue(any(event.rule_id == "contextual:champion:TwistedFate:Gate" for event in recorder.events))


if __name__ == "__main__":
    unittest.main()
