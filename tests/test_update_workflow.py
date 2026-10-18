"""手动数据更新 workflow 的发布候选契约测试。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "update-raw-events.yml.disabled"
DEMO_WORKFLOW_PATH = ROOT / ".github" / "workflows" / "deploy-demo-pages.yml.disabled"
FROZEN_PROFILE_PATH = ROOT / "data" / "frozen-profile.v1.json"


def load_workflow_steps() -> list[dict[str, Any]]:
    """读取 update job 的声明式步骤。"""

    workflow = YAML(typ="safe").load(WORKFLOW_PATH)
    return workflow["jobs"]["update"]["steps"]


def load_workflow() -> dict[str, Any]:
    """读取完整 workflow 声明。"""

    return YAML(typ="safe").load(WORKFLOW_PATH)


def load_demo_workflow_steps() -> list[dict[str, Any]]:
    """读取静态 Demo build job 的声明式步骤。"""

    workflow = YAML(typ="safe").load(DEMO_WORKFLOW_PATH)
    return workflow["jobs"]["build"]["steps"]


def step_by_name(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """按稳定步骤名查找 workflow step。"""

    return next(step for step in steps if step.get("name") == name)


def test_update_workflow_builds_one_isolated_candidate_chain() -> None:
    """更新后只调用正式 full-refresh 链，不再旁路生成 normalized。"""

    steps = load_workflow_steps()
    refresh = step_by_name(steps, "Run isolated full refresh candidate")
    command = str(refresh["run"])

    assert "scripts/run_full_refresh.py" in command
    assert "--sources-manifest .temp/source-identity/source-manifest.json" in command
    assert "--frozen-profile data/frozen-profile.v1.json" in command
    assert "--allow-ratchet-regression" in command
    assert all(
        script not in command
        for script in (
            "decompose_events.py",
            "build_normalized_outputs.py",
            "validate_normalized_outputs.py",
            "report_quality_metrics.py",
            "check_quality_ratchet.py",
        )
    )
    workflow_commands = "\n".join(str(step.get("run", "")) for step in steps)
    assert workflow_commands.count("scripts/run_full_refresh.py") == 1


def test_update_commit_is_pushed_only_after_candidate_passes() -> None:
    """本地更新提交必须先形成 clean generator，full refresh 通过后才能 push。"""

    steps = load_workflow_steps()
    names = [str(step.get("name", "")) for step in steps]
    prepare = step_by_name(steps, "Prepare local update commit")
    push = step_by_name(steps, "Push validated data commit")

    assert names.index("Prepare local update commit") < names.index("Run isolated full refresh candidate")
    assert names.index("Run isolated full refresh candidate") < names.index("Upload dataset run candidate")
    assert names.index("Upload dataset run candidate") < names.index("Push validated data commit")
    assert prepare["if"] == "${{ inputs.commit_changes }}"
    assert push["if"] == "${{ inputs.commit_changes }}"
    assert "git commit" in str(prepare["run"])
    assert "git push" not in str(prepare["run"])
    assert "git push" in str(push["run"])


def test_update_workflow_uploads_candidate_identity_and_review_reports() -> None:
    """成功 candidate 与失败诊断使用不混淆的 artifact 合同。"""

    steps = load_workflow_steps()
    candidate = step_by_name(steps, "Upload dataset run candidate")
    diagnostics = step_by_name(steps, "Upload failed run diagnostics")
    candidate_paths = str(candidate["with"]["path"])
    diagnostic_paths = str(diagnostics["with"]["path"])

    assert candidate["if"] == "${{ success() }}"
    assert candidate["with"]["name"].startswith("candidate-dataset-")
    assert candidate["with"]["if-no-files-found"] == "error"
    assert "dist/runs/completed/*/completion.json" in candidate_paths
    assert "dist/runs/completed/*/normalized" in candidate_paths
    assert "dist/runs/completed/*/reports" in candidate_paths
    assert "dist/runs/failed" not in candidate_paths
    assert ".temp/source-identity/source-manifest.json" in candidate_paths
    assert diagnostics["if"] == "${{ failure() }}"
    assert diagnostics["with"]["name"].startswith("failed-run-diagnostics-")
    assert diagnostics["with"]["if-no-files-found"] == "warn"
    assert "dist/runs/failed/*/failure.json" in diagnostic_paths
    assert "dist/runs/failed/*/normalized" in diagnostic_paths
    assert "dist/runs/.staging/*/run.json" in diagnostic_paths
    assert "dist/runs/completed" not in diagnostic_paths
    assert ".temp/source-identity/source-manifest.json" in diagnostic_paths
    assert "/work" not in candidate_paths
    assert "/work" not in diagnostic_paths


def test_update_workflow_uses_locked_dependency_sets() -> None:
    """Actions 使用兼容的 Python 3.13，并由 uv.lock 解析依赖。"""

    steps = load_workflow_steps()

    assert step_by_name(steps, "Set up Python")["run"] == "uv python install 3.13"
    assert step_by_name(steps, "Sync dependencies")["run"] == "uv sync --python 3.13 --dev --locked"
    assert (
        step_by_name(steps, "Install optional GAME manifest extractor")["run"]
        == "uv sync --python 3.13 --dev --extra download --locked"
    )

    demo_steps = load_demo_workflow_steps()
    assert step_by_name(demo_steps, "Set up Python")["run"] == "uv python install 3.13"
    assert step_by_name(demo_steps, "Sync dependencies")["run"] == "uv sync --python 3.13 --dev --locked"


def test_artifact_mode_requires_an_explicit_source_run() -> None:
    """跨 workflow run 的 raw 与来源清单必须绑定同一来源 run。"""

    workflow = load_workflow()
    inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    steps = workflow["jobs"]["update"]["steps"]
    validate = step_by_name(steps, "Validate artifact source")
    download = step_by_name(steps, "Download extracted raw round")
    source_download = step_by_name(steps, "Download producer source manifest")
    normalize = step_by_name(steps, "Normalize artifact source identity")

    assert "raw_input_run_id" in inputs
    assert validate["if"] == "${{ inputs.raw_source_mode == 'artifact' }}"
    assert "^[1-9][0-9]*$" in str(validate["run"])
    assert download["with"]["github-token"] == "${{ github.token }}"
    assert download["with"]["repository"] == "${{ github.repository }}"
    assert download["with"]["run-id"] == "${{ inputs.raw_input_run_id }}"
    assert source_download["with"]["github-token"] == "${{ github.token }}"
    assert source_download["with"]["repository"] == "${{ github.repository }}"
    assert source_download["with"]["run-id"] == "${{ inputs.raw_input_run_id }}"
    assert source_download["with"]["name"] == "${{ inputs.source_manifest_artifact }}"
    assert "source-manifest.json" in str(normalize["run"])
    assert "scripts/prepare_source_manifest.py" in str(normalize["run"])


def test_manifest_mode_builds_and_threads_one_runtime_source_identity() -> None:
    """manifest 采集必须生成同轮来源清单，并在写入与候选构建中复用。"""

    steps = load_workflow_steps()
    validate = step_by_name(steps, "Validate manifest source inputs")
    prepare = step_by_name(steps, "Prepare manifest source identity")
    update = step_by_name(steps, "Update cumulative data")
    refresh = step_by_name(steps, "Run isolated full refresh candidate")

    names = [str(step.get("name", "")) for step in steps]
    assert names.index("Validate frozen workflow version") < names.index("Extract raw round from GAME manifest")
    assert names.index("Validate manifest source inputs") < names.index("Extract raw round from GAME manifest")
    assert names.index("Extract raw round from GAME manifest") < names.index("Prepare manifest source identity")
    assert "GAME_MANIFEST_ID" in str(validate["run"])
    assert "LCU_VERSION" in str(validate["run"])
    assert "LCU_MANIFEST_ID" in str(validate["run"])
    assert prepare["if"] == "${{ inputs.raw_source_mode == 'manifest' }}"
    assert "--game-manifest-id" in str(prepare["run"])
    assert "--lcu-manifest-id" in str(prepare["run"])
    assert "data/frozen-profile.v1.json" in str(prepare["run"])
    for step in (update, refresh):
        command = str(step["run"])
        assert "--sources-manifest .temp/source-identity/source-manifest.json" in command
        assert "--frozen-profile data/frozen-profile.v1.json" in command


def test_workflow_frozen_version_matches_committed_profile() -> None:
    """workflow 默认版本与唯一 frozen profile 保持同步，并在下载前校验。"""

    workflow = load_workflow()
    profile = json.loads(FROZEN_PROFILE_PATH.read_text(encoding="utf-8"))
    expected = profile["base_game_version"]
    game_version = workflow["on"]["workflow_dispatch"]["inputs"]["game_version"]
    steps = workflow["jobs"]["update"]["steps"]
    validate = step_by_name(steps, "Validate frozen workflow version")
    names = [str(step.get("name", "")) for step in steps]

    assert game_version["default"] == expected
    assert str(expected) in game_version["description"]
    assert "data/frozen-profile.v1.json" in str(validate["run"])
    assert "version_series(requested)" in str(validate["run"])
    assert names.index("Validate frozen workflow version") < names.index("Download extracted raw round")
    assert names.index("Validate frozen workflow version") < names.index("Extract raw round from GAME manifest")


def test_source_identity_is_retained_with_candidate_diagnostics() -> None:
    """candidate 与更新诊断 artifact 都要保留实际使用的来源身份。"""

    steps = load_workflow_steps()
    candidate = step_by_name(steps, "Upload dataset run candidate")
    update = step_by_name(steps, "Upload update report")

    assert ".temp/source-identity/source-manifest.json" in str(candidate["with"]["path"])
    assert ".temp/source-identity/source-manifest.json" in str(update["with"]["path"])
