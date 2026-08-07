"""本地一键刷新脚本的步骤串联测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.full_refresh import run_refresh_steps
from scripts.run_full_refresh import RefreshPaths, build_steps, main, parse_args


def step_names(steps) -> list[str]:
    """提取步骤名列表。"""

    return [name for name, _ in steps]


def as_path(value: str) -> str:
    """按平台归一化路径字符串，便于跨平台断言。"""

    return str(Path(value))


def find_argv(steps, name: str) -> list[str]:
    """按步骤名取出对应 argv。"""

    for step_name, argv in steps:
        if step_name == name:
            return argv

    raise AssertionError(f"step not found: {name}")


class BuildStepsTests(unittest.TestCase):
    """验证默认参数下的步骤组装。"""

    def test_core_chain_order_and_argv(self) -> None:
        """默认五步顺序正确且 argv 含正确参数。"""

        args = parse_args(
            [
                "--raw-root",
                "raw/events",
                "--reference-root",
                "reference",
                "--baseline",
                "data/quality-baseline.json",
            ]
        )
        paths = RefreshPaths(
            output_root=Path("out/normalized"),
            work_root=Path("out/work"),
            report_root=Path("out/reports"),
        )
        steps = build_steps(args, paths)

        self.assertEqual(
            step_names(steps),
            ["decompose", "build", "validate", "metrics", "ratchet"],
        )

        decompose = find_argv(steps, "decompose")
        self.assertIn("decompose_events.py", decompose[1])
        self.assertIn("--raw-root", decompose)
        self.assertIn(as_path("raw/events"), decompose)
        self.assertIn("--output-root", decompose)
        self.assertIn(as_path("out/work"), decompose)

        build = find_argv(steps, "build")
        self.assertIn("--decomposition-root", build)
        self.assertIn(as_path("out/work"), build)
        self.assertIn("--output-root", build)
        self.assertIn(as_path("out/normalized"), build)

        # validate 与 metrics 复用 report_root 下同一 validation 报告路径。
        validate = find_argv(steps, "validate")
        self.assertIn("--normalized-root", validate)
        self.assertIn(as_path("out/normalized"), validate)
        validation_json = validate[validate.index("--output-json") + 1]
        self.assertTrue(validation_json.endswith("normalized-validation.json"))

        ratchet = find_argv(steps, "ratchet")
        self.assertIn("--baseline", ratchet)
        self.assertIn(as_path("data/quality-baseline.json"), ratchet)
        self.assertEqual(ratchet[ratchet.index("--validation-json") + 1], validation_json)

        metrics = find_argv(steps, "metrics")
        self.assertEqual(metrics[metrics.index("--validation-json") + 1], validation_json)
        self.assertTrue(metrics[metrics.index("--output-json") + 1].endswith("quality-metrics.json"))

    def test_update_first_prepends_update_step(self) -> None:
        """--update-first 时在最前插入 update 步。"""

        args = parse_args(
            [
                "--update-first",
                "--game-version",
                "16.12",
                "--game-data-base-url",
                "https://example.test/game-data",
            ]
        )
        steps = build_steps(
            args,
            RefreshPaths(Path("out/normalized"), Path("out/work"), Path("out/reports")),
        )

        self.assertEqual(
            step_names(steps),
            ["update", "decompose", "build", "validate", "metrics", "ratchet"],
        )
        update = find_argv(steps, "update")
        self.assertIn("update_raw_events.py", update[1])
        self.assertIn("--mode", update)
        self.assertIn("online", update)
        self.assertEqual(update[update.index("--game-version") + 1], "16.12")
        self.assertEqual(
            update[update.index("--game-data-base-url") + 1],
            "https://example.test/game-data",
        )

    def test_update_first_requires_online_params(self) -> None:
        """--update-first 缺在线参数时报错退出。"""

        with self.assertRaises(SystemExit):
            parse_args(["--update-first"])


class RunRefreshStepsTests(unittest.TestCase):
    """验证失败短路语义。"""

    def test_empty_steps_is_ok(self) -> None:
        """无步骤即成功：ok 为真、results 为空、runner 不被调用。"""

        calls: list[str] = []

        def runner(name: str, argv: list[str]) -> int:
            calls.append(name)
            return 0

        result = run_refresh_steps([], runner)

        self.assertTrue(result.ok)
        self.assertEqual(result.results, [])
        self.assertIsNone(result.failed_step)
        self.assertEqual(calls, [])

    def test_first_step_failure_short_circuits(self) -> None:
        """首步（index 0）阻断失败即短路，后续零执行。"""

        calls: list[str] = []

        def runner(name: str, argv: list[str]) -> int:
            calls.append(name)
            return 5 if name == "a" else 0

        steps = [("a", []), ("b", []), ("c", [])]
        result = run_refresh_steps(steps, runner)

        self.assertFalse(result.ok)
        self.assertEqual(calls, ["a"])
        self.assertIsNotNone(result.failed_step)
        self.assertEqual(result.failed_step.name, "a")
        self.assertEqual(result.failed_step.exit_code, 5)

    def test_all_pass_runs_every_step(self) -> None:
        """全部成功时按序执行所有步骤且 ok。"""

        calls: list[str] = []

        def runner(name: str, argv: list[str]) -> int:
            calls.append(name)
            return 0

        steps = [("a", ["x"]), ("b", ["y"]), ("c", ["z"])]
        result = run_refresh_steps(steps, runner)

        self.assertTrue(result.ok)
        self.assertIsNone(result.failed_step)
        self.assertEqual(calls, ["a", "b", "c"])

    def test_blocking_failure_short_circuits(self) -> None:
        """中途阻断步骤失败即短路，后续步骤不执行。"""

        calls: list[str] = []

        def runner(name: str, argv: list[str]) -> int:
            calls.append(name)
            return 7 if name == "b" else 0

        steps = [("a", []), ("b", []), ("c", [])]
        result = run_refresh_steps(steps, runner)

        self.assertFalse(result.ok)
        self.assertEqual(calls, ["a", "b"])
        self.assertIsNotNone(result.failed_step)
        self.assertEqual(result.failed_step.name, "b")
        self.assertEqual(result.failed_step.exit_code, 7)

    def test_non_blocking_failure_continues(self) -> None:
        """非阻断步骤失败不短路，后续步骤继续执行且整体 ok。"""

        calls: list[str] = []

        def runner(name: str, argv: list[str]) -> int:
            calls.append(name)
            return 1 if name == "ratchet" else 0

        steps = [("validate", []), ("ratchet", []), ("metrics", [])]
        result = run_refresh_steps(steps, runner, non_blocking=frozenset({"ratchet"}))

        self.assertTrue(result.ok)
        self.assertIsNone(result.failed_step)
        self.assertEqual(calls, ["validate", "ratchet", "metrics"])


class MainTests(unittest.TestCase):
    """验证脚本入口的注入执行与退出码。"""

    def test_main_returns_failed_step_exit_code(self) -> None:
        """阻断步骤失败时 main 返回该步 exit code 并报名。"""

        with tempfile.TemporaryDirectory() as temp:
            calls: list[str] = []

            def tracking_runner(name: str, argv: list[str]) -> int:
                calls.append(name)
                return 3 if name == "build" else 0

            code = main(["--raw-root", "raw/events", "--runs-root", temp], runner=tracking_runner)

        self.assertEqual(code, 3)
        # build 失败后 validate/ratchet/metrics 不再执行。
        self.assertEqual(calls, ["decompose", "build"])

    def test_main_allow_ratchet_regression_continues(self) -> None:
        """--allow-ratchet-regression 时 ratchet 失败非阻断，全链走完且整体成功。"""

        with tempfile.TemporaryDirectory() as temp:
            calls: list[str] = []

            def runner(name: str, argv: list[str]) -> int:
                calls.append(name)
                materialize_run_outputs(name, argv)
                return 1 if name == "ratchet" else 0

            code = main(["--allow-ratchet-regression", "--runs-root", temp], runner=runner)

        self.assertEqual(code, 0)
        # ratchet 现为最后一步，非阻断失败不影响整体成功。
        self.assertIn("metrics", calls)
        self.assertEqual(calls[-1], "ratchet")

    def test_main_ratchet_failure_blocks_by_default(self) -> None:
        """默认 ratchet 失败（exit 1）阻断，但 metrics 已在其前产出。"""

        with tempfile.TemporaryDirectory() as temp:
            calls: list[str] = []

            def runner(name: str, argv: list[str]) -> int:
                calls.append(name)
                materialize_run_outputs(name, argv)
                return 1 if name == "ratchet" else 0

            code = main(["--runs-root", temp], runner=runner)

        self.assertEqual(code, 1)
        # metrics 在 ratchet 之前执行，故 ratchet 阻断失败时 metrics 已产出。
        self.assertIn("metrics", calls)
        self.assertEqual(calls, ["decompose", "build", "validate", "metrics", "ratchet"])


def materialize_run_outputs(name: str, argv: list[str]) -> None:
    """为 main 编排测试写出 completion 所需的最小候选/报告。"""

    if name == "build":
        output_root = Path(argv[argv.index("--output-root") + 1])
        output_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "dataset_id": "local-v1-" + "1" * 16,
            "content_digest": "1" * 64,
            "content_identity": {
                "input_identity": {
                    "generator_state": {"commit": "a" * 40, "dirty": False},
                }
            },
        }
        (output_root / "manifest.json").write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    elif name in {"validate", "metrics"}:
        output_path = Path(argv[argv.index("--output-json") + 1])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text("{}\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
