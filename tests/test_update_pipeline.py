"""本地端到端 update pipeline 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lol_event_semantics import update_pipeline
from lol_event_semantics.update_pipeline import run_local_update_pipeline, run_online_update_pipeline
from tests.test_reference_ingestion import write_data_dragon_fixture, write_game_data_fixture


class UpdatePipelineTests(unittest.TestCase):
    """验证本地 reference + raw 端到端更新。"""

    def test_local_pipeline_writes_reference_raw_state_and_report(self) -> None:
        """第一次运行写出 reference、raw、state 和报告，第二次 no-op。"""

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
            raw_input_root.joinpath("champions", "Annie.txt").write_text(
                "Play_vo_Annie_Move2DStandard\n",
                encoding="utf-8",
            )

            first = run_local_update_pipeline(
                reference_root=reference_root,
                raw_root=raw_root,
                state_file=state_file,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                raw_input_root=raw_input_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                report_root=report_root,
            )
            second = run_local_update_pipeline(
                reference_root=reference_root,
                raw_root=raw_root,
                state_file=state_file,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                raw_input_root=raw_input_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                report_root=report_root,
            )
            report = json.loads((report_root / "update-report.json").read_text(encoding="utf-8"))

            self.assertTrue(first.changed)
            self.assertFalse(second.changed)
            self.assertTrue((reference_root / "champions" / "Annie.yaml").is_file())
            self.assertTrue((raw_root / "champions" / "Annie.txt").is_file())
            self.assertTrue(state_file.is_file())
            self.assertTrue((report_root / "update-report.json").is_file())
            self.assertEqual(report["raw_update"]["added_events"], 0)
            self.assertEqual(report["source_versions"]["game_version"], "16.12")

    def test_local_pipeline_rolls_back_when_reference_import_fails(self) -> None:
        """reference 导入失败时不写半成品 raw/reference。"""

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
            write_data_dragon_fixture(data_dragon_root, version="16.13.1")
            raw_input_root.joinpath("champions").mkdir(parents=True)
            raw_input_root.joinpath("champions", "Annie.txt").write_text(
                "Play_vo_Annie_Move2DStandard\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "main version"):
                run_local_update_pipeline(
                    reference_root=reference_root,
                    raw_root=raw_root,
                    state_file=state_file,
                    game_data_root=game_data_root,
                    data_dragon_root=data_dragon_root,
                    raw_input_root=raw_input_root,
                    game_version="16.12",
                    data_dragon_version="16.13.1",
                    report_root=report_root,
                )

            self.assertFalse(reference_root.exists())
            self.assertFalse(raw_root.exists())
            self.assertFalse(state_file.exists())

    def test_local_pipeline_restores_existing_targets_when_commit_fails(self) -> None:
        """提交阶段失败时恢复已有 reference/raw/state/report。"""

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
            reference_root.joinpath("champions").mkdir(parents=True)
            reference_root.joinpath("manifest.yaml").write_text(
                "schema_version: 1\n"
                "updated_at: '2026-06-15T00:00:00Z'\n"
                "languages:\n"
                "  - en_US\n"
                "source_versions: {}\n"
                "entities:\n"
                "  champions: 1\n"
                "  maps: 0\n",
                encoding="utf-8",
            )
            reference_root.joinpath("champions", "Existing.yaml").write_text(
                "schema_version: 1\n"
                "entity:\n"
                "  type: champion\n"
                "  id: 999\n"
                "  alias: Existing\n"
                "  names:\n"
                "    en_US:\n"
                "      - value: Existing\n"
                "        sources:\n"
                "          - source: fixture\n"
                "            version: '1'\n"
                "spells: []\n",
                encoding="utf-8",
            )
            raw_root.joinpath("champions").mkdir(parents=True)
            raw_root.joinpath("champions", "Existing.txt").write_text("existing-raw\n", encoding="utf-8")
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text('{"existing": true}\n', encoding="utf-8")
            report_root.mkdir(parents=True)
            report_root.joinpath("update-report.json").write_text('{"existing": true}\n', encoding="utf-8")

            real_replace_output = update_pipeline.replace_output

            def fail_on_report(source: Path, target: Path, is_dir: bool) -> None:
                if target == report_root:
                    raise OSError("simulated report commit failure")

                real_replace_output(source, target, is_dir)

            with patch("lol_event_semantics.update_pipeline.replace_output", side_effect=fail_on_report):
                with self.assertRaisesRegex(OSError, "simulated report"):
                    run_local_update_pipeline(
                        reference_root=reference_root,
                        raw_root=raw_root,
                        state_file=state_file,
                        game_data_root=game_data_root,
                        data_dragon_root=data_dragon_root,
                        raw_input_root=raw_input_root,
                        game_version="16.12",
                        data_dragon_version="16.12.1",
                        report_root=report_root,
                    )

            self.assertIn("alias: Existing", reference_root.joinpath("champions", "Existing.yaml").read_text(encoding="utf-8"))
            self.assertEqual(raw_root.joinpath("champions", "Existing.txt").read_text(encoding="utf-8"), "existing-raw\n")
            self.assertEqual(state_file.read_text(encoding="utf-8"), '{"existing": true}\n')
            self.assertEqual(report_root.joinpath("update-report.json").read_text(encoding="utf-8"), '{"existing": true}\n')

    def test_online_pipeline_prepares_sources_and_writes_outputs(self) -> None:
        """在线 source cache 可以驱动同一条 reference/raw pipeline。"""

        def fetch(url: str) -> bytes:
            if url.endswith("/api/versions.json"):
                return json.dumps(["16.12.1"]).encode("utf-8")

            if "/global/default/v1/champion-summary.json" in url:
                return json.dumps([{"id": 1, "alias": "Annie", "name": "Annie"}]).encode("utf-8")

            if "/global/zh_CN/v1/champion-summary.json" in url:
                return json.dumps([{"id": 1, "alias": "Annie", "name": "安妮"}], ensure_ascii=False).encode("utf-8")

            if url.endswith("/maps.json"):
                return json.dumps([{"id": 11, "mapStringId": "SummonersRift", "name": "Summoner's Rift"}]).encode(
                    "utf-8"
                )

            if url.endswith("/skinlines.json"):
                return json.dumps([{"id": 19, "name": "Star Guardian Season 1"}]).encode("utf-8")

            if "/global/" in url and url.endswith("/champions/1.json"):
                return json.dumps(
                    {
                        "id": 1,
                        "alias": "Annie",
                        "name": "Annie",
                        "passive": {"name": "Pyromania", "description": "Passive text"},
                        "spells": [{"spellKey": "q", "name": "Disintegrate", "description": "Annie hurls a fireball."}],
                    }
                ).encode("utf-8")

            if url.endswith("/data/en_US/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "Annie"}}}).encode("utf-8")

            if url.endswith("/data/zh_CN/champion.json"):
                return json.dumps({"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮"}}}, ensure_ascii=False).encode("utf-8")

            if url.endswith("/data/en_US/championFull.json"):
                return json.dumps(
                    {
                        "data": {
                            "Annie": {
                                "id": "Annie",
                                "key": "1",
                                "name": "Annie",
                                "passive": {"name": "Pyromania", "description": "Passive text"},
                                "spells": [{"id": "AnnieQ", "name": "Disintegrate", "description": "Annie hurls a fireball."}],
                            }
                        }
                    }
                ).encode("utf-8")

            if url.endswith("/data/zh_CN/championFull.json"):
                return json.dumps(
                    {
                        "data": {
                            "Annie": {
                                "id": "Annie",
                                "key": "1",
                                "name": "安妮",
                                "passive": {"name": "嗜火", "description": "被动文本"},
                                "spells": [{"id": "AnnieQ", "name": "碎裂之火", "description": "安妮扔出火球。"}],
                            }
                        }
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

            if url.endswith("/champion/Annie.json"):
                return json.dumps(
                    {
                        "data": {
                            "Annie": {
                                "id": "Annie",
                                "key": "1",
                                "name": "Annie",
                                "passive": {"name": "Pyromania", "description": "Passive text"},
                                "spells": [{"id": "AnnieQ", "name": "Disintegrate", "description": "Annie hurls a fireball."}],
                            }
                        }
                    }
                ).encode("utf-8")

            if url.endswith("/hero_list.js"):
                return json.dumps(
                    {
                        "version": "16.13",
                        "hero": [
                            {
                                "heroId": "1",
                                "alias": "Annie",
                                "title": "安妮",
                                "name": "黑暗之女",
                                "keywords": "安妮,黑暗之女,小法师,annie,am",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ).encode("utf-8")

            raise AssertionError(url)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_input_root = root / "round"
            raw_input_root.joinpath("champions").mkdir(parents=True)
            raw_input_root.joinpath("champions", "Annie.txt").write_text(
                "Play_vo_Annie_Move2DStandard\n",
                encoding="utf-8",
            )

            result = run_online_update_pipeline(
                reference_root=root / "reference",
                raw_root=root / "raw" / "events",
                state_file=root / "raw" / "update-state.json",
                raw_input_root=raw_input_root,
                game_version="16.12",
                report_root=root / "report",
                cache_root=root / ".temp" / "reference-sources",
                game_data_base_url="https://example.test/game-data",
                data_dragon_base_url="https://example.test/ddragon",
                hero_list_url="https://example.test/hero_list.js",
                fetcher=fetch,
            )
            annie = (root / "reference" / "champions" / "Annie.yaml").read_text(encoding="utf-8")

            self.assertTrue(result.changed)
            self.assertTrue((root / "reference" / "champions" / "Annie.yaml").is_file())
            self.assertTrue((root / "reference" / "skinlines" / "Skinline19.yaml").is_file())
            self.assertTrue((root / "raw" / "events" / "champions" / "Annie.txt").is_file())
            self.assertTrue((root / "report" / "update-report.json").is_file())
            self.assertTrue((root / ".temp" / "reference-sources" / "game-data" / "global" / "default" / "v1").is_dir())
            self.assertIn("小法师", annie)
            self.assertTrue((root / ".temp" / "reference-sources" / "hero-list" / "hero_list.json").is_file())


if __name__ == "__main__":
    unittest.main()
