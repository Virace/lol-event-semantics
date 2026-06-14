"""事件名拆解报告测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import UnknownFragment, UnknownKind
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
from lol_event_semantics.decomposition_reports import write_jsonl, write_reports


class DecompositionReportTests(unittest.TestCase):
    """验证 JSONL 与报告输出。"""

    def records(self):
        """构造报告测试记录。"""

        raw_names = [
            "Play_vo_Annie_Move2DStandard",
            "Play_vo_Riven_Spell3DQ3Cast",
            "Play_vo_AsheSkin08_VolleyRank2_cast3D",
        ]
        return [
            normalize_semantics(decompose_event_name(raw_name, game_version="16.5"))
            for raw_name in raw_names
        ]

    def test_write_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "champions.jsonl"
            write_jsonl(self.records(), path)

            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            self.assertEqual(json.loads(lines[0])["raw_event_name"], "Play_vo_Annie_Move2DStandard")

    def test_write_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp)
            write_reports(self.records(), report_root)

            self.assertTrue((report_root / "profile-summary.md").is_file())
            self.assertTrue((report_root / "unparsed-fragments.md").is_file())
            self.assertTrue((report_root / "unparsed-fragments.csv").is_file())
            self.assertTrue((report_root / "semantic-ambiguities.md").is_file())
            self.assertTrue((report_root / "semantic-ambiguities.csv").is_file())
            self.assertTrue((report_root / "model-hard-cases.md").is_file())
            self.assertTrue((report_root / "candidate-review.md").is_file())
            self.assertTrue((report_root / "candidate-review.csv").is_file())
            self.assertTrue((report_root / "candidate-review.jsonl").is_file())
            self.assertTrue((report_root / "unknown-batches.md").is_file())
            self.assertTrue((report_root / "unknown-batches.csv").is_file())

    def test_unknown_batches_split_voice_sfx_and_other(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp)
            records = [
                normalize_semantics(decompose_event_name(raw_name, game_version="16.5"))
                for raw_name in [
                    "Play_vo_Ahri_UnhandledVoiceThing",
                    "Play_sfx_RivenSkin05_RivenTriCleave_OnCast",
                    "Play_mus_map11_MODES_25s3_Ruby_death",
                ]
            ]
            unknowns = [
                ("UnhandledVoiceThing", "Play_vo_Ahri_UnhandledVoiceThing"),
                ("RivenTriCleave", "Play_sfx_RivenSkin05_RivenTriCleave_OnCast"),
                ("MODES", "Play_mus_map11_MODES_25s3_Ruby_death"),
            ]

            for record, (raw, location) in zip(records, unknowns, strict=True):
                record.unknowns.append(
                    UnknownFragment(
                        kind=UnknownKind.UNKNOWN_HEAD_TOKEN,
                        raw=raw,
                        location=location,
                        blocking_reason="synthetic report batching fixture",
                    )
                )

            write_reports(records, report_root)

            text = (report_root / "unknown-batches.md").read_text(encoding="utf-8")
            self.assertIn("`vo_primary`", text)
            self.assertIn("`sfx`", text)
            self.assertIn("`other`", text)
            self.assertIn("UnhandledVoiceThing", text)
            self.assertIn("RivenTriCleave", text)
            self.assertIn("MODES", text)

    def test_write_candidate_review_includes_full_event_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            v1 = game_data_root / "global" / "default" / "v1"
            v1.mkdir(parents=True)
            (v1 / "maps.json").write_text('[{"id": 12, "name": "Howling Abyss"}]', encoding="utf-8")
            (v1 / "champion-summary.json").write_text("[]", encoding="utf-8")
            index = ReferenceIndex.from_game_data_root(game_data_root)
            record = enrich_with_reference(
                normalize_semantics(decompose_event_name("Play_sfx_Env_Map12_Ambience", game_version="16.5")),
                index,
            )
            record.candidates[0]["score"] = 95
            report_root = root / "reports"

            write_reports([record], report_root)

            review_text = (report_root / "candidate-review.md").read_text(encoding="utf-8")
            self.assertIn("Play_sfx_Env_Map12_Ambience", review_text)
            self.assertIn("Map12 resolves to map: Howling Abyss", review_text)
            self.assertIn("| raw_event_name | kind | raw | meaning | confidence | score | evidence |", review_text)
            self.assertIn("| 95 |", review_text)
            review_json = json.loads((report_root / "candidate-review.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(review_json["score"], "95")


class DecompositionCliTests(unittest.TestCase):
    """验证 CLI 能扫描 raw tree 并输出产物。"""

    def test_cli_writes_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "16.5" / "events"
            champions = raw_root / "champions"
            champions.mkdir(parents=True)
            (champions / "1.txt").write_text(
                "\n".join(
                    [
                        "Play_vo_Annie_Move2DStandard",
                        "Play_vo_Riven_Spell3DQ3Cast",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "out"
            report_root = root / "reports"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/decompose_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--version",
                    "16.5",
                    "--output-root",
                    str(output_root),
                    "--report-root",
                    str(report_root),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output_root / "champions.jsonl").is_file())
            self.assertTrue((report_root / "profile-summary.md").is_file())
            audit_root = report_root / "rule-audit"
            self.assertTrue((audit_root / "match-hit-summary.csv").is_file())
            self.assertTrue((audit_root / "match-hit-summary.json").is_file())
            self.assertTrue((audit_root / "zero-hit-rules.csv").is_file())
            self.assertTrue((audit_root / "manifest.json").is_file())
            manifest = json.loads((audit_root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["raw_count"], 2)
            summary_header = (audit_root / "match-hit-summary.csv").read_text(encoding="utf-8-sig").splitlines()[0]
            self.assertEqual(summary_header, "matcher_kind,rule_id,source_path,hit_count,examples")

    def test_cli_excludes_tft_raw_events(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "16.5" / "events"
            maps = raw_root / "maps"
            maps.mkdir(parents=True)
            (maps / "22.txt").write_text(
                "\n".join(
                    [
                        "Play_sfx_tft_action_shop_buy",
                        "Play_vo_TFTTutorial_Yasuo_Purchase2D",
                        "Play_TFT_Mus_VillainLab_win",
                        "Play_sfx_Env_Map12_Ambience",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            output_root = root / "out"
            report_root = root / "reports"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/decompose_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--version",
                    "16.5",
                    "--output-root",
                    str(output_root),
                    "--report-root",
                    str(report_root),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            output_text = (output_root / "maps.jsonl").read_text(encoding="utf-8")
            self.assertNotIn("tft", output_text.lower())
            self.assertIn("Play_sfx_Env_Map12_Ambience", output_text)

    def test_cli_uses_game_data_root_for_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "16.5" / "events"
            maps = raw_root / "maps"
            maps.mkdir(parents=True)
            (maps / "12.txt").write_text("Play_sfx_Env_Map12_Ambience\n", encoding="utf-8")
            game_data_root = root / "game-data"
            v1 = game_data_root / "global" / "default" / "v1"
            v1.mkdir(parents=True)
            (v1 / "maps.json").write_text('[{"id": 12, "name": "Howling Abyss"}]', encoding="utf-8")
            (v1 / "champion-summary.json").write_text("[]", encoding="utf-8")
            output_root = root / "out"
            report_root = root / "reports"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/decompose_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--version",
                    "16.5",
                    "--output-root",
                    str(output_root),
                    "--report-root",
                    str(report_root),
                    "--game-data-root",
                    str(game_data_root),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((output_root / "maps.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["semantic_slots"]["map"]["id"], 12)
            self.assertTrue(record["candidates"])
            manifest = json.loads((report_root / "rule-audit" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["game_data_root"], game_data_root.as_posix())
            self.assertEqual(manifest["reference_version"], "16.5")

    def test_cli_uses_dragontail_spell_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "16.5" / "events"
            champions = raw_root / "champions"
            champions.mkdir(parents=True)
            champions.joinpath("38.txt").write_text("Play_sfx_KassadinSkin05_NullLance_OnCast\n", encoding="utf-8")
            game_data_root = root / "game-data"
            v1 = game_data_root / "global" / "default" / "v1"
            champion_root = v1 / "champions"
            champion_root.mkdir(parents=True)
            (v1 / "champion-summary.json").write_text(
                json.dumps([{"id": 38, "alias": "Kassadin", "name": "Kassadin"}]),
                encoding="utf-8",
            )
            (champion_root / "38.json").write_text(
                json.dumps(
                    {
                        "id": 38,
                        "alias": "Kassadin",
                        "name": "Kassadin",
                        "spells": [{"spellKey": "q", "name": "Null Sphere"}],
                    }
                ),
                encoding="utf-8",
            )
            dragontail_root = root / "dragontail" / "16.12.1" / "data" / "en_US"
            dragontail_champion_root = dragontail_root / "champion"
            dragontail_champion_root.mkdir(parents=True)
            (dragontail_champion_root / "Kassadin.json").write_text(
                json.dumps(
                    {
                        "data": {
                            "Kassadin": {
                                "id": "Kassadin",
                                "key": "38",
                                "name": "Kassadin",
                                "spells": [{"id": "NullLance", "name": "Null Sphere"}],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            output_root = root / "out"
            report_root = root / "reports"

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/decompose_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--version",
                    "16.5",
                    "--output-root",
                    str(output_root),
                    "--report-root",
                    str(report_root),
                    "--game-data-root",
                    str(game_data_root),
                    "--dragontail-root",
                    str(dragontail_root),
                ],
                cwd=Path.cwd(),
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            record = json.loads((output_root / "champions.jsonl").read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(record["semantic_slots"]["spell_key"], "q")
            self.assertEqual(record["semantic_slots"]["spell_match"]["match_source"], "dragontail_spell_id")
