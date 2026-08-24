"""本地端到端 update pipeline 测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lol_event_semantics import update_pipeline
from lol_event_semantics.update_pipeline import run_local_update_pipeline, run_online_update_pipeline
from scripts import update_raw_events as update_raw_events_cli
from tests.test_reference_ingestion import write_data_dragon_fixture, write_game_data_fixture


def write_frozen_fixture(
    root: Path,
    *,
    base_version: str = "16.12",
    expected_raw_count: int = 1,
) -> tuple[Path, Path]:
    """写入最小 frozen profile 与可移植远端来源 manifest。"""

    sources_manifest = root / "sources.json"
    sources_manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "manifest",
                "region": None,
                "game_version": f"{base_version}.999",
                "lcu_version": f"{base_version}.1",
                "game_manifest": {
                    "id": "fixture-game-manifest",
                    "version": f"{base_version}.999",
                },
                "lobby_snapshot": {
                    "game_version": base_version,
                    "lcu_manifest_id": "fixture-lcu-manifest",
                },
                "sources": {"raw-bins": "ok"},
            }
        ),
        encoding="utf-8",
    )
    frozen_profile = root / "frozen-profile.json"
    frozen_profile.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "profile_id": "test-frozen-profile",
                "corpus_scope": "cumulative",
                "base_game_version": base_version,
                "expected_raw_unique_count": expected_raw_count,
            }
        ),
        encoding="utf-8",
    )
    return sources_manifest, frozen_profile


def write_hero_fixture(root: Path, *, version: str = "16.12") -> None:
    """写入带可控版本的最小腾讯英雄列表。"""

    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("hero_list.json").write_text(
        json.dumps(
            {
                "version": version,
                "hero": [
                    {
                        "heroId": "1",
                        "alias": "Annie",
                        "title": "安妮",
                        "name": "黑暗之女",
                        "keywords": "安妮,黑暗之女,annie",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def prepare_preflight_fixture(root: Path) -> dict[str, Path]:
    """准备 frozen preflight 的本地更新输入与输出路径。"""

    paths = {
        "game_data_root": root / "game-data",
        "data_dragon_root": root / "dragontail",
        "hero_list_root": root / "hero-list",
        "raw_input_root": root / "round",
        "reference_root": root / "reference",
        "raw_root": root / "raw" / "events",
        "state_file": root / "raw" / "update-state.json",
        "report_root": root / "report",
    }
    write_game_data_fixture(paths["game_data_root"])
    write_data_dragon_fixture(paths["data_dragon_root"])
    write_hero_fixture(paths["hero_list_root"])
    paths["raw_input_root"].joinpath("champions").mkdir(parents=True)
    paths["raw_input_root"].joinpath("champions", "Annie.txt").write_text(
        "Play_vo_Annie_Move2DStandard\n",
        encoding="utf-8",
    )
    sources_manifest, frozen_profile = write_frozen_fixture(root)
    paths["sources_manifest"] = sources_manifest
    paths["frozen_profile"] = frozen_profile
    return paths


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

    def test_local_pipeline_commits_when_frozen_preflight_matches(self) -> None:
        """staging raw/reference 与冻结来源身份匹配时才提交全部目标。"""

        with tempfile.TemporaryDirectory() as tmp:
            paths = prepare_preflight_fixture(Path(tmp))

            result = run_local_update_pipeline(
                reference_root=paths["reference_root"],
                raw_root=paths["raw_root"],
                state_file=paths["state_file"],
                game_data_root=paths["game_data_root"],
                data_dragon_root=paths["data_dragon_root"],
                raw_input_root=paths["raw_input_root"],
                game_version="16.12",
                data_dragon_version="16.12.1",
                report_root=paths["report_root"],
                hero_list_root=paths["hero_list_root"],
                sources_manifest_path=paths["sources_manifest"],
                frozen_profile_path=paths["frozen_profile"],
            )

            self.assertTrue(result.changed)
            self.assertTrue(paths["reference_root"].joinpath("manifest.yaml").is_file())
            self.assertTrue(paths["raw_root"].joinpath("champions", "Annie.txt").is_file())
            self.assertTrue(paths["state_file"].is_file())
            self.assertTrue(paths["report_root"].joinpath("update-report.json").is_file())

    def test_frozen_preflight_drift_leaves_all_targets_untouched(self) -> None:
        """profile/source/raw/reference 任一漂移都不得提交 staging 输出。"""

        cases = (
            ("profile", "frozen profile schema"),
            ("source", "sources.game_version"),
            ("raw", "raw 唯一事件数"),
            ("reference", "reference.source_versions.tencent_101.version"),
        )
        for drift, error_pattern in cases:
            with self.subTest(drift=drift), tempfile.TemporaryDirectory() as tmp:
                paths = prepare_preflight_fixture(Path(tmp))
                paths["state_file"].parent.mkdir(parents=True, exist_ok=True)
                paths["state_file"].write_text('{"existing": true}\n', encoding="utf-8")
                paths["report_root"].mkdir(parents=True)
                paths["report_root"].joinpath("update-report.json").write_text(
                    '{"existing": true}\n',
                    encoding="utf-8",
                )

                if drift == "profile":
                    profile = json.loads(paths["frozen_profile"].read_text(encoding="utf-8"))
                    profile["schema_version"] = 2
                    paths["frozen_profile"].write_text(json.dumps(profile), encoding="utf-8")
                elif drift == "source":
                    sources = json.loads(paths["sources_manifest"].read_text(encoding="utf-8"))
                    sources["game_version"] = "16.13.1"
                    paths["sources_manifest"].write_text(json.dumps(sources), encoding="utf-8")
                elif drift == "raw":
                    paths["raw_input_root"].joinpath("champions", "Annie.txt").write_text(
                        "Play_vo_Annie_Move2DStandard\nPlay_vo_Annie_Attack2DGeneral\n",
                        encoding="utf-8",
                    )
                else:
                    hero_list = json.loads(
                        paths["hero_list_root"].joinpath("hero_list.json").read_text(encoding="utf-8")
                    )
                    hero_list["version"] = "16.13"
                    paths["hero_list_root"].joinpath("hero_list.json").write_text(
                        json.dumps(hero_list, ensure_ascii=False),
                        encoding="utf-8",
                    )

                with patch.object(update_pipeline, "commit_staged_outputs") as commit_outputs:
                    with self.assertRaisesRegex(ValueError, error_pattern):
                        run_local_update_pipeline(
                            reference_root=paths["reference_root"],
                            raw_root=paths["raw_root"],
                            state_file=paths["state_file"],
                            game_data_root=paths["game_data_root"],
                            data_dragon_root=paths["data_dragon_root"],
                            raw_input_root=paths["raw_input_root"],
                            game_version="16.12",
                            data_dragon_version="16.12.1",
                            report_root=paths["report_root"],
                            hero_list_root=paths["hero_list_root"],
                            sources_manifest_path=paths["sources_manifest"],
                            frozen_profile_path=paths["frozen_profile"],
                        )

                commit_outputs.assert_not_called()
                self.assertFalse(paths["reference_root"].exists())
                self.assertFalse(paths["raw_root"].exists())
                self.assertEqual(paths["state_file"].read_text(encoding="utf-8"), '{"existing": true}\n')
                self.assertEqual(
                    paths["report_root"].joinpath("update-report.json").read_text(encoding="utf-8"),
                    '{"existing": true}\n',
                )

    def test_pipelines_require_paired_frozen_paths(self) -> None:
        """共享 local/online pipeline API 都不接受单个 frozen 输入。"""

        with self.assertRaisesRegex(TypeError, "必须同时提供"):
            run_local_update_pipeline(
                reference_root=Path("reference"),
                raw_root=Path("raw/events"),
                state_file=Path("raw/update-state.json"),
                game_data_root=Path("game-data"),
                data_dragon_root=Path("dragontail"),
                raw_input_root=Path("round"),
                game_version="16.12",
                data_dragon_version="16.12.1",
                report_root=Path("report"),
                sources_manifest_path=Path("sources.json"),
            )
        with self.assertRaisesRegex(TypeError, "必须同时提供"):
            run_online_update_pipeline(
                reference_root=Path("reference"),
                raw_root=Path("raw/events"),
                state_file=Path("raw/update-state.json"),
                raw_input_root=Path("round"),
                game_version="16.12",
                report_root=Path("report"),
                cache_root=Path("cache"),
                frozen_profile_path=Path("profile.json"),
            )

    def test_online_pipeline_requires_frozen_provenance_before_fetch(self) -> None:
        """在线 API 缺少来源清单与 profile 时必须在任何下载前失败。"""

        with patch.object(
            update_pipeline,
            "prepare_online_reference_sources",
            side_effect=AssertionError("online fetch must not start"),
        ) as prepare_sources:
            with self.assertRaisesRegex(TypeError, "在线更新必须提供"):
                run_online_update_pipeline(
                    reference_root=Path("reference"),
                    raw_root=Path("raw/events"),
                    state_file=Path("raw/update-state.json"),
                    raw_input_root=Path("round"),
                    game_version="16.12",
                    report_root=Path("report"),
                    cache_root=Path("cache"),
                )

        prepare_sources.assert_not_called()

    def test_online_pipeline_rejects_local_manifest_before_fetch(self) -> None:
        """在线 API 不得把仓库内 local manifest 冒充当轮远端来源。"""

        with tempfile.TemporaryDirectory() as tmp:
            sources_manifest, frozen_profile = write_frozen_fixture(Path(tmp))
            manifest = json.loads(sources_manifest.read_text(encoding="utf-8"))
            manifest["mode"] = "local"
            sources_manifest.write_text(json.dumps(manifest), encoding="utf-8")

            with patch.object(
                update_pipeline,
                "prepare_online_reference_sources",
                side_effect=AssertionError("online fetch must not start"),
            ) as prepare_sources:
                with self.assertRaisesRegex(ValueError, "不能复用 local"):
                    run_online_update_pipeline(
                        reference_root=Path("reference"),
                        raw_root=Path("raw/events"),
                        state_file=Path("raw/update-state.json"),
                        raw_input_root=Path("round"),
                        game_version="16.12",
                        report_root=Path("report"),
                        cache_root=Path("cache"),
                        sources_manifest_path=sources_manifest,
                        frozen_profile_path=frozen_profile,
                    )

            prepare_sources.assert_not_called()

    def test_online_pipeline_rejects_mismatched_request_versions_before_fetch(self) -> None:
        """联网请求参数也必须属于来源清单与 frozen profile 的版本系列。"""

        cases = (
            ({"game_version": "16.13"}, "online game_version"),
            ({"game_version": "16.12", "data_dragon_version": "16.13.1"}, "online data_dragon_version"),
        )
        for overrides, error_pattern in cases:
            with self.subTest(overrides=overrides), tempfile.TemporaryDirectory() as tmp:
                sources_manifest, frozen_profile = write_frozen_fixture(Path(tmp))
                kwargs = {
                    "reference_root": Path("reference"),
                    "raw_root": Path("raw/events"),
                    "state_file": Path("raw/update-state.json"),
                    "raw_input_root": Path("round"),
                    "game_version": "16.12",
                    "report_root": Path("report"),
                    "cache_root": Path("cache"),
                    "sources_manifest_path": sources_manifest,
                    "frozen_profile_path": frozen_profile,
                    **overrides,
                }
                with patch.object(
                    update_pipeline,
                    "prepare_online_reference_sources",
                    side_effect=AssertionError("online fetch must not start"),
                ) as prepare_sources:
                    with self.assertRaisesRegex(ValueError, error_pattern):
                        run_online_update_pipeline(**kwargs)

                prepare_sources.assert_not_called()

    def test_cli_passes_paired_frozen_paths_to_local_and_online(self) -> None:
        """CLI local/online 均把成对 frozen argv 传给共享 pipeline。"""

        common_argv = [
            "--reference-root",
            "reference",
            "--raw-root",
            "raw/events",
            "--state-file",
            "raw/update-state.json",
            "--raw-input-root",
            "round",
            "--game-version",
            "16.12",
            "--report-root",
            "report",
            "--sources-manifest",
            "sources.json",
            "--frozen-profile",
            "profile.json",
        ]
        cases = (
            (
                "local",
                "run_local_update_pipeline",
                [
                    "--game-data-root",
                    "game-data",
                    "--data-dragon-root",
                    "dragontail",
                    "--data-dragon-version",
                    "16.12.1",
                ],
            ),
            ("online", "run_online_update_pipeline", []),
        )

        for mode, runner_name, mode_argv in cases:
            with self.subTest(mode=mode), patch.object(update_raw_events_cli, runner_name) as runner:
                runner.return_value.to_dict.return_value = {}

                exit_code = update_raw_events_cli.main(["--mode", mode, *common_argv, *mode_argv])

                self.assertEqual(exit_code, 0)
                runner.assert_called_once()
                self.assertEqual(runner.call_args.kwargs["sources_manifest_path"], Path("sources.json"))
                self.assertEqual(runner.call_args.kwargs["frozen_profile_path"], Path("profile.json"))

    def test_cli_requires_paired_frozen_paths(self) -> None:
        """local/online CLI 只提供一个 frozen 参数时解析失败。"""

        argv = [
            "--mode",
            "online",
            "--reference-root",
            "reference",
            "--raw-root",
            "raw/events",
            "--state-file",
            "raw/update-state.json",
            "--raw-input-root",
            "round",
            "--game-version",
            "16.12",
            "--report-root",
            "report",
            "--sources-manifest",
            "sources.json",
        ]

        with self.assertRaises(SystemExit):
            update_raw_events_cli.parse_args(argv)

    def test_online_cli_requires_frozen_provenance(self) -> None:
        """online CLI 即使两个 frozen 参数都省略也必须解析失败。"""

        argv = [
            "--mode",
            "online",
            "--reference-root",
            "reference",
            "--raw-root",
            "raw/events",
            "--state-file",
            "raw/update-state.json",
            "--raw-input-root",
            "round",
            "--game-version",
            "16.12",
            "--report-root",
            "report",
        ]

        with self.assertRaises(SystemExit):
            update_raw_events_cli.parse_args(argv)

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
            raw_input_root.joinpath("champions", "Annie.txt").write_text(
                "Play_vo_Annie_Move2DStandard\n", encoding="utf-8"
            )
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

            self.assertIn(
                "alias: Existing", reference_root.joinpath("champions", "Existing.yaml").read_text(encoding="utf-8")
            )
            self.assertEqual(
                raw_root.joinpath("champions", "Existing.txt").read_text(encoding="utf-8"), "existing-raw\n"
            )
            self.assertEqual(state_file.read_text(encoding="utf-8"), '{"existing": true}\n')
            self.assertEqual(
                report_root.joinpath("update-report.json").read_text(encoding="utf-8"), '{"existing": true}\n'
            )

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

            if url.endswith("/items.json"):
                return json.dumps([{"id": 1035, "name": "Emberknife"}]).encode("utf-8")

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
                return json.dumps(
                    {"data": {"Annie": {"id": "Annie", "key": "1", "name": "安妮"}}}, ensure_ascii=False
                ).encode("utf-8")

            if url.endswith("/data/en_US/championFull.json"):
                return json.dumps(
                    {
                        "data": {
                            "Annie": {
                                "id": "Annie",
                                "key": "1",
                                "name": "Annie",
                                "passive": {"name": "Pyromania", "description": "Passive text"},
                                "spells": [
                                    {"id": "AnnieQ", "name": "Disintegrate", "description": "Annie hurls a fireball."}
                                ],
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
                                "spells": [
                                    {"id": "AnnieQ", "name": "Disintegrate", "description": "Annie hurls a fireball."}
                                ],
                            }
                        }
                    }
                ).encode("utf-8")

            if url.endswith("/hero_list.js"):
                return json.dumps(
                    {
                        "version": "16.12",
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
            sources_manifest, frozen_profile = write_frozen_fixture(root)

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
                sources_manifest_path=sources_manifest,
                frozen_profile_path=frozen_profile,
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
