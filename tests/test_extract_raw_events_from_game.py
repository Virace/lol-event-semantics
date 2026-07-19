"""extract_raw_events_from_game CLI 测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.extract_raw_events_from_game import load_local_requests, load_manifest_requests


class ExtractRawEventsFromGameCliTests(unittest.TestCase):
    """验证 raw event round 抽取入口。"""

    def test_load_requests_reads_explicit_json_shape(self) -> None:
        """请求表按 champions/maps 实体读取。"""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.json"
            path.write_text(
                json.dumps(
                    {
                        "champions": {"Annie": ["data/characters/annie/skins/base/annie.bin"]},
                        "maps": {"SummonersRift": ["data/maps/map11.bin"]},
                        "skins": {"Ignored": ["ignored.bin"]},
                    }
                ),
                encoding="utf-8",
            )

            requests = load_local_requests(path)

        self.assertEqual(
            requests,
            {
                ("champions", "Annie"): ["data/characters/annie/skins/base/annie.bin"],
                ("maps", "SummonersRift"): ["data/maps/map11.bin"],
            },
        )

    def test_load_manifest_requests_reads_wad_and_bins(self) -> None:
        """manifest 模式请求表包含 WAD 路径和 BIN 列表。"""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "requests.json"
            path.write_text(
                json.dumps(
                    {
                        "champions": {
                            "Annie": {
                                "wad": "Game/DATA/FINAL/Champions/Annie.wad.client",
                                "bins": ["data/characters/annie/skins/base/annie.bin"],
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            requests = load_manifest_requests(path)

        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].entity, "Annie")
        self.assertEqual(requests[0].wad_path, "Game/DATA/FINAL/Champions/Annie.wad.client")

    def test_event_files_mode_writes_round_txt(self) -> None:
        """CLI 可把已抽取事件 JSON 标准化为 round txt。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "input"
            output_root = root / "round"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Annie.json").write_text(
                json.dumps({"events": ["Play_vo_Annie_Move2DStandard"]}),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/extract_raw_events_from_game.py",
                    "--mode",
                    "event-files",
                    "--input-root",
                    str(input_root),
                    "--output-root",
                    str(output_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)
            output_exists = output_root.joinpath("champions", "Annie.txt").is_file()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["events"], 1)
        self.assertTrue(output_exists)

    def test_wads_mode_requires_request_file_and_wad(self) -> None:
        """WAD 模式必须显式提供请求表和本地 WAD。"""

        result = subprocess.run(
            [
                sys.executable,
                "scripts/extract_raw_events_from_game.py",
                "--output-root",
                "round",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--request-file", result.stderr)
        self.assertIn("--wad", result.stderr)

    def test_manifest_mode_requires_manifest_url(self) -> None:
        """manifest 模式必须显式提供 GAME manifest URL。"""

        result = subprocess.run(
            [
                sys.executable,
                "scripts/extract_raw_events_from_game.py",
                "--mode",
                "manifest",
                "--output-root",
                "round",
                "--request-file",
                "requests.json",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--game-manifest-url", result.stderr)


if __name__ == "__main__":
    unittest.main()
