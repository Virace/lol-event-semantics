"""raw update CLI 测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_reference_ingestion import write_data_dragon_fixture, write_game_data_fixture


class RawUpdateCliTests(unittest.TestCase):
    """验证 raw update 命令行入口。"""

    def test_check_raw_update_reports_changed_without_writing(self) -> None:
        """check 命令输出差集 JSON，不写 raw_root。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_raw_update.py",
                    "--raw-root",
                    str(raw_root),
                    "--input-root",
                    str(input_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["changed"])
        self.assertTrue(payload["should_update"])
        self.assertEqual(payload["added_events"], 1)
        self.assertFalse(raw_root.exists())

    def test_check_raw_update_reports_manifest_metadata_change(self) -> None:
        """GAME manifest metadata 变化时提示 workflow 应更新。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            state_file = root / "raw" / "update-state.json"
            raw_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions").mkdir(parents=True)
            raw_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")
            input_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "last_checked": {
                            "game_version": None,
                            "manifest_major_version": None,
                            "game_manifest_id": "old",
                            "game_manifest_url": None,
                            "data_dragon_version": None,
                            "game_data_snapshot_metadata_sha256": None,
                            "data_dragon_champion_summary_sha256": None,
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/check_raw_update.py",
                    "--raw-root",
                    str(raw_root),
                    "--input-root",
                    str(input_root),
                    "--state-file",
                    str(state_file),
                    "--game-manifest-id",
                    "new",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(payload["changed"])
        self.assertTrue(payload["should_update"])
        self.assertTrue(payload["metadata_changed"])

    def test_update_raw_events_writes_state_and_noop_second_run(self) -> None:
        """update 命令写累计 raw 和 state，第二次 no-op。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            state_file = root / "raw" / "update-state.json"
            input_root = root / "round"
            input_root.joinpath("maps").mkdir(parents=True)
            input_root.joinpath("maps", "SummonersRift.txt").write_text("Play_sfx_Env_Map11_Ambience\n", encoding="utf-8")

            first = subprocess.run(
                [
                    sys.executable,
                    "scripts/update_raw_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--input-root",
                    str(input_root),
                    "--state-file",
                    str(state_file),
                    "--game-version",
                    "16.12",
                    "--data-dragon-version",
                    "16.12.1",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            second = subprocess.run(
                [
                    sys.executable,
                    "scripts/update_raw_events.py",
                    "--raw-root",
                    str(raw_root),
                    "--input-root",
                    str(input_root),
                    "--state-file",
                    str(state_file),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            first_payload = json.loads(first.stdout)
            second_payload = json.loads(second.stdout)
            state = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(first_payload["changed"])
        self.assertFalse(second_payload["changed"])
        self.assertEqual(state["last_checked"]["data_dragon_version"], None)

    def test_update_raw_events_local_mode_runs_pipeline(self) -> None:
        """update 命令 local mode 串起 reference 和 raw 更新。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            raw_input_root = root / "round"
            reference_root = root / "reference"
            raw_root = root / "raw" / "events"
            state_file = root / "raw" / "update-state.json"
            report_root = root / "report"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)
            raw_input_root.joinpath("champions").mkdir(parents=True)
            raw_input_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/update_raw_events.py",
                    "--mode",
                    "local",
                    "--reference-root",
                    str(reference_root),
                    "--raw-root",
                    str(raw_root),
                    "--state-file",
                    str(state_file),
                    "--game-data-root",
                    str(game_data_root),
                    "--data-dragon-root",
                    str(data_dragon_root),
                    "--raw-input-root",
                    str(raw_input_root),
                    "--game-version",
                    "16.12",
                    "--data-dragon-version",
                    "16.12.1",
                    "--report-root",
                    str(report_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(payload["changed"])
            self.assertTrue((reference_root / "champions" / "Annie.yaml").is_file())
            self.assertTrue((raw_root / "champions" / "Annie.txt").is_file())
            self.assertTrue((report_root / "update-report.json").is_file())

    def test_update_raw_events_local_mode_requires_reference_inputs(self) -> None:
        """local mode 缺少 reference 输入参数时失败且不写目标。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            raw_input_root = root / "round"
            raw_input_root.joinpath("champions").mkdir(parents=True)
            raw_input_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/update_raw_events.py",
                    "--mode",
                    "local",
                    "--raw-root",
                    str(raw_root),
                    "--raw-input-root",
                    str(raw_input_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--reference-root", result.stderr)
            self.assertFalse(raw_root.exists())


if __name__ == "__main__":
    unittest.main()
