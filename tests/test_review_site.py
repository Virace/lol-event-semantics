"""本地审核站生成测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.review_site import build_review_groups, read_jsonl_records, write_review_site


class ReviewSiteTests(unittest.TestCase):
    """验证静态审核站数据聚类与 HTML 输出。"""

    def test_build_review_groups_splits_batches_and_clusters_examples(self) -> None:
        records = [
            self.record(
                raw="Play_vo_DariusSkin04_DariusNoxianTacticsONHAttack_cast3D",
                asset_kind="vo",
                confidence="low",
                owner="Darius",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "NoxianTacticsONHAttack"},
            ),
            self.record(
                raw="Play_vo_DariusSkin15_DariusNoxianTacticsONHAttack_cast3D",
                asset_kind="vo",
                confidence="low",
                owner="Darius",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "NoxianTacticsONHAttack"},
            ),
            self.record(
                raw="Play_sfx_RivenSkin05_RivenTriCleave_OnCast",
                asset_kind="sfx",
                confidence="low",
                owner="Riven",
                primary_family="sfx",
                semantic_profile="spell_event",
                slots={"trigger_family": "sfx_event", "effect_base": "TriCleave"},
            ),
        ]

        groups = build_review_groups(records, max_examples=1)

        self.assertEqual([group["batch"] for group in groups], ["vo_primary", "sfx"])
        vo_group = groups[0]
        self.assertEqual(vo_group["count"], 2)
        self.assertEqual(vo_group["issue_type"], "low_confidence")
        self.assertEqual(vo_group["signature"]["owner"], "Darius")
        self.assertEqual(vo_group["signature"]["event_key"], "NoxianTacticsONHAttack")
        self.assertEqual(len(vo_group["examples"]), 1)

    def test_build_review_groups_explains_review_focus(self) -> None:
        records = [
            self.record(
                raw="Play_vo_DariusSkin04_DariusNoxianTacticsONHAttack_cast3D",
                asset_kind="vo",
                confidence="low",
                owner="Darius",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "NoxianTacticsONHAttack"},
            ),
            self.record(
                raw="Play_vo_ZoeSkin01_ZoeESleepCountDownSlow_buffactivate",
                asset_kind="vo",
                confidence="failed",
                owner="Zoe",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "ZoeESleepCountDownSlow"},
                unknowns=[{"kind": "residual", "raw": "SleepCountDownSlow", "blocking_reason": "unresolved"}],
            ),
            self.record(
                raw="Play_vo_LuxSkin07_LuxLightstrikeToggle_cast3D",
                asset_kind="vo",
                confidence="medium",
                owner="Lux",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "LightstrikeToggle"},
                candidates=[
                    {
                        "kind": "spell_alias",
                        "raw": "LightstrikeToggle",
                        "meaning": "E 技能变体",
                        "confidence": "medium",
                        "score": "0.72",
                        "evidence": "owner spell context",
                    }
                ],
            ),
        ]

        groups = build_review_groups(records)
        focus_by_owner = {group["signature"]["owner"]: group["review_focus"] for group in groups}

        self.assertEqual(focus_by_owner["Darius"]["kind"], "low_confidence")
        self.assertIn("整组低置信", focus_by_owner["Darius"]["summary"])
        self.assertIn("NoxianTacticsONHAttack", focus_by_owner["Darius"]["terms"])
        self.assertEqual(focus_by_owner["Zoe"]["kind"], "unknown")
        self.assertIn("SleepCountDownSlow", focus_by_owner["Zoe"]["terms"])
        self.assertEqual(focus_by_owner["Lux"]["kind"], "candidate")
        self.assertIn("LightstrikeToggle", focus_by_owner["Lux"]["terms"])

    def test_write_review_site_embeds_groups_and_review_controls(self) -> None:
        records = [
            self.record(
                raw="Play_vo_LuxSkin07_LuxLightstrikeToggle_cast3D",
                asset_kind="vo",
                confidence="medium",
                owner="Lux",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={
                    "trigger_family": "spell",
                    "spell_key": "e",
                    "contextual_alias": {"component": "light_strike_toggle", "source": "manual_contextual_alias"},
                },
            )
        ]

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "index.html"
            write_review_site(records, output_path)

            html = output_path.read_text(encoding="utf-8")
            self.assertIn("事件语义审核站", html)
            self.assertIn("manual_contextual_alias", html)
            self.assertIn("markGroup", html)
            self.assertIn("导出审核结果", html)
            self.assertIn("分组键", html)
            self.assertIn("审核重点", html)
            self.assertIn("renderReviewFocus", html)

    def test_write_review_site_keeps_large_payload_out_of_html(self) -> None:
        records = [
            self.record(
                raw="Play_vo_LuxSkin07_LuxLightstrikeToggle_cast3D",
                asset_kind="vo",
                confidence="medium",
                owner="Lux",
                primary_family="spell",
                semantic_profile="spell_event",
                slots={"trigger_family": "spell", "alias_base": "LightstrikeToggle"},
            )
        ]

        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "index.html"
            data_path = Path(tmp) / "review-data.json"
            write_review_site(records, output_path)

            html = output_path.read_text(encoding="utf-8")
            data = json.loads(data_path.read_text(encoding="utf-8"))

            self.assertTrue(data_path.is_file())
            self.assertIn("review-data.json", html)
            self.assertIn("fetch(dataUrl)", html)
            self.assertNotIn("Play_vo_LuxSkin07_LuxLightstrikeToggle_cast3D", html)
            self.assertEqual(data["summary"]["event_total"], 1)
            self.assertEqual(len(data["groups"]), 1)

    def test_write_review_site_uses_paged_rendering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_path = Path(tmp) / "index.html"
            write_review_site([], output_path)

            html = output_path.read_text(encoding="utf-8")

            self.assertIn("const pageSize", html)
            self.assertIn("visible.slice(start, end)", html)
            self.assertIn("pageSummary", html)
            self.assertNotIn("updatePager(0, 0, 0, 1);", html)

    def test_read_jsonl_records_dedupes_by_raw_event_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "champions.jsonl"
            second = root / "maps.jsonl"
            record = self.record(
                raw="Play_vo_Annie_Move2DStandard",
                asset_kind="vo",
                confidence="high",
                owner="Annie",
                primary_family="move",
                semantic_profile="self_action",
                slots={"trigger_family": "move"},
            )
            first.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            second.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")

            records = read_jsonl_records([first, second])

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["raw_event_name"], "Play_vo_Annie_Move2DStandard")

    def test_cli_writes_static_review_site(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_path = root / "champions.jsonl"
            input_path.write_text(
                json.dumps(
                    self.record(
                        raw="Play_vo_LuxSkin07_LuxLightstrikeToggle_cast3D",
                        asset_kind="vo",
                        confidence="medium",
                        owner="Lux",
                        primary_family="spell",
                        semantic_profile="spell_event",
                        slots={"trigger_family": "spell", "alias_base": "LightstrikeToggle"},
                    ),
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = root / "site" / "index.html"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_review_site.py",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(output_path.is_file())
            self.assertIn("groups=1", result.stdout)

    def record(
        self,
        *,
        raw: str,
        asset_kind: str,
        confidence: str,
        owner: str,
        primary_family: str,
        semantic_profile: str,
        slots: dict[str, object],
        unknowns: list[dict[str, object]] | None = None,
        candidates: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        """构造审核站输入记录。"""

        return {
            "raw_event_name": raw,
            "shell": {"asset_kind": asset_kind, "verb": "Play", "body": raw},
            "owner": {"alias": owner, "raw_owner_token": owner, "kind": "champion"},
            "profile": {"primary_family": primary_family, "semantic_profile": semantic_profile},
            "semantic_slots": slots,
            "confidence": confidence,
            "unknowns": unknowns or [],
            "candidates": candidates or [],
            "source_paths": ["raw/events/champions/99.txt"],
        }


if __name__ == "__main__":
    unittest.main()
