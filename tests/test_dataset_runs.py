"""隔离 dataset run 与 approved-local 原子 promotion 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from lol_event_semantics.dataset_identity import content_digest
from lol_event_semantics.review_decisions import canonical_digest, decision_set_digest
from lol_event_semantics.dataset_runs import (
    APPROVED_POINTER_NAME,
    PROMOTION_LOCK_NAME,
    DatasetRun,
    DatasetRunError,
    PromotionConflictError,
    PromotionLockedError,
    complete_dataset_run,
    create_dataset_run,
    fail_dataset_run,
    load_completed_run,
    promote_completed_run,
    resolve_approved_dataset,
)
from lol_event_semantics.full_refresh import RefreshResult, RefreshStepResult
from scripts.promote_dataset_run import main as promote_main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_invalid_generator_root_does_not_create_staging_run(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"

    with pytest.raises(DatasetRunError, match="Git 状态"):
        create_dataset_run(
            runs_root,
            repo_root=tmp_path / "missing-repository",
            command=["fixture"],
        )

    assert not runs_root.exists()


def test_successful_run_moves_from_staging_to_immutable_completed(tmp_path: Path) -> None:
    run = create_fixture_run(tmp_path, seed="first")
    staging_root = run.staging_root

    completed = complete_dataset_run(
        run,
        passed_result(),
        elapsed_seconds=1.25,
        promotion_eligibility=eligible_fixture(),
    )

    assert not staging_root.exists()
    assert completed.root.is_dir()
    assert (completed.root / "run.json").is_file()
    assert (completed.root / "completion.json").is_file()
    assert not (completed.root / "failure.json").exists()
    with pytest.raises(FileExistsError):
        (completed.root / "completion.json").open("x").close()
    assert load_completed_run(tmp_path, run.run_id) == completed


def test_failed_run_is_preserved_without_approved_pointer(tmp_path: Path) -> None:
    run = create_dataset_run(tmp_path, repo_root=REPO_ROOT, command=["fixture"])
    result = failed_result("build", 7)

    failed_root = fail_dataset_run(
        run,
        result=result,
        error_code="step_failed",
        message="build exited with 7",
        elapsed_seconds=0.5,
    )

    assert failed_root == tmp_path / "failed" / run.run_id
    assert (failed_root / "failure.json").is_file()
    assert not (tmp_path / APPROVED_POINTER_NAME).exists()
    with pytest.raises(DatasetRunError, match="尚无 approved"):
        resolve_approved_dataset(tmp_path)


def test_failed_destination_conflict_leaves_staging_without_terminal_marker(tmp_path: Path) -> None:
    run = create_dataset_run(tmp_path, repo_root=REPO_ROOT, command=["fixture"])
    (tmp_path / "failed" / run.run_id).mkdir()

    with pytest.raises(DatasetRunError, match="拒绝覆盖"):
        fail_dataset_run(
            run,
            result=failed_result("build", 7),
            error_code="step_failed",
            message="build exited with 7",
            elapsed_seconds=0.5,
        )

    assert run.staging_root.is_dir()
    assert not (run.staging_root / "failure.json").exists()


def test_clean_completed_run_promotes_and_resolves_atomically(tmp_path: Path) -> None:
    first = complete_dataset_run(
        create_fixture_run(tmp_path, seed="first"),
        passed_result(),
        elapsed_seconds=1.0,
        promotion_eligibility=eligible_fixture(),
    )

    pointer = promote_completed_run(
        tmp_path,
        first.run_id,
        expected_previous_digest=None,
    )

    pointer_path = tmp_path / APPROVED_POINTER_NAME
    assert json.loads(pointer_path.read_text(encoding="utf-8")) == pointer
    assert resolve_approved_dataset(tmp_path) == first.normalized_root.resolve()
    assert not (tmp_path / PROMOTION_LOCK_NAME).exists()


def test_candidate_without_absolute_gate_attestation_cannot_promote(tmp_path: Path) -> None:
    candidate = complete_dataset_run(
        create_fixture_run(tmp_path, seed="not-evaluated"),
        passed_result(),
        elapsed_seconds=1,
    )

    with pytest.raises(DatasetRunError, match="promotion eligibility"):
        promote_completed_run(tmp_path, candidate.run_id, expected_previous_digest=None)

    assert not (tmp_path / APPROVED_POINTER_NAME).exists()


def test_manual_review_candidate_requires_valid_dataset_approval(tmp_path: Path) -> None:
    candidate = complete_manual_review_candidate(tmp_path, seed="manual-pending")

    with pytest.raises(DatasetRunError, match="manual review pending"):
        promote_completed_run(tmp_path, candidate.run_id, expected_previous_digest=None)

    assert not (tmp_path / APPROVED_POINTER_NAME).exists()


def test_manual_review_rejects_stale_identity_or_target_digest(tmp_path: Path) -> None:
    candidate = complete_manual_review_candidate(tmp_path, seed="stale")
    approval = dataset_approval(candidate)
    stale_identity = {**approval, "dataset_id": "other-dataset"}

    with pytest.raises(DatasetRunError, match="dataset approval 无效"):
        promote_completed_run(tmp_path, candidate.run_id, expected_previous_digest=None, approval=stale_identity)

    stale_target = {
        **approval,
        "decisions": [{**approval["decisions"][0], "target_content_hash": "0" * 64}],
    }
    stale_target["decision_set_digest"] = decision_set_digest(stale_target["decisions"])
    with pytest.raises(DatasetRunError, match="dataset approval 无效"):
        promote_completed_run(tmp_path, candidate.run_id, expected_previous_digest=None, approval=stale_target)


def test_valid_manual_dataset_approval_promotes_with_review_attestation(tmp_path: Path) -> None:
    candidate = complete_manual_review_candidate(tmp_path, seed="approved")

    pointer = promote_completed_run(
        tmp_path,
        candidate.run_id,
        expected_previous_digest=None,
        approval=dataset_approval(candidate),
    )

    assert pointer["review"] == {
        "decision_set_digest": pointer["review"]["decision_set_digest"],
        "decision_id": "dataset-approval",
        "reviewer": "reviewer@example.test",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
        "source_decision_set_digest": "a" * 64,
    }
    assert resolve_approved_dataset(tmp_path) == candidate.normalized_root.resolve()


def test_promotion_cli_loads_candidate_bound_dataset_approval(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI 的 --decisions 把最终人工批准传入同一 promotion 状态机。"""

    candidate = complete_manual_review_candidate(tmp_path, seed="cli-approved")
    decisions_path = tmp_path / "dataset-approval.json"
    decisions_path.write_text(json.dumps(dataset_approval(candidate)) + "\n", encoding="utf-8")

    code = promote_main(
        [
            "--runs-root",
            str(tmp_path),
            "--run-id",
            candidate.run_id,
            "--expected-previous-digest",
            "none",
            "--decisions",
            str(decisions_path),
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert resolve_approved_dataset(tmp_path) == candidate.normalized_root.resolve()


def test_automatic_gate_failure_rejects_even_with_dataset_approval(tmp_path: Path) -> None:
    candidate = complete_manual_review_candidate(
        tmp_path,
        seed="automatic-failed",
        automatic_gates_passed=False,
    )

    with pytest.raises(DatasetRunError, match="automatic gates failed"):
        promote_completed_run(
            tmp_path,
            candidate.run_id,
            expected_previous_digest=None,
            approval=dataset_approval(candidate),
        )


def test_promotion_cli_reports_invalid_observed_pointer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / APPROVED_POINTER_NAME).write_text('{"status": "broken"}\n', encoding="utf-8")

    code = promote_main(["--runs-root", str(tmp_path), "--run-id", "missing"])

    assert code == 2
    assert "promotion rejected" in capsys.readouterr().err


def test_second_promotion_uses_cas_and_preserves_old_pointer_on_conflict(tmp_path: Path) -> None:
    first = complete_dataset_run(
        create_fixture_run(tmp_path, seed="first"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    second = complete_dataset_run(
        create_fixture_run(tmp_path, seed="second"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    promote_completed_run(tmp_path, first.run_id, expected_previous_digest=None)
    before = (tmp_path / APPROVED_POINTER_NAME).read_bytes()

    with pytest.raises(PromotionConflictError):
        promote_completed_run(tmp_path, second.run_id, expected_previous_digest=None)

    assert (tmp_path / APPROVED_POINTER_NAME).read_bytes() == before
    replaced = promote_completed_run(tmp_path, second.run_id, expected_previous_digest=first.content_digest)
    assert replaced["previous_content_digest"] == first.content_digest
    assert resolve_approved_dataset(tmp_path) == second.normalized_root.resolve()


def test_dirty_or_non_blocking_failure_candidate_cannot_replace_pointer(tmp_path: Path) -> None:
    approved = complete_dataset_run(
        create_fixture_run(tmp_path, seed="approved"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    promote_completed_run(tmp_path, approved.run_id, expected_previous_digest=None)
    before = (tmp_path / APPROVED_POINTER_NAME).read_bytes()

    dirty = complete_dataset_run(
        create_fixture_run(tmp_path, seed="dirty", dirty=True),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    with pytest.raises(DatasetRunError, match="dirty"):
        promote_completed_run(tmp_path, dirty.run_id, expected_previous_digest=approved.content_digest)

    warned = complete_dataset_run(
        create_fixture_run(tmp_path, seed="warned"),
        non_blocking_failure_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    with pytest.raises(DatasetRunError, match="quality gate"):
        promote_completed_run(tmp_path, warned.run_id, expected_previous_digest=approved.content_digest)

    assert (tmp_path / APPROVED_POINTER_NAME).read_bytes() == before


def test_stale_report_payload_or_lock_preserves_pointer(tmp_path: Path) -> None:
    approved = complete_dataset_run(
        create_fixture_run(tmp_path, seed="approved"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    promote_completed_run(tmp_path, approved.run_id, expected_previous_digest=None)
    before = (tmp_path / APPROVED_POINTER_NAME).read_bytes()

    stale = complete_dataset_run(
        create_fixture_run(tmp_path, seed="stale"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    (stale.root / "reports" / "quality-metrics.json").write_text('{"stale": true}\n', encoding="utf-8")
    with pytest.raises(DatasetRunError, match="过期"):
        promote_completed_run(tmp_path, stale.run_id, expected_previous_digest=approved.content_digest)

    extra_report = complete_dataset_run(
        create_fixture_run(tmp_path, seed="extra-report"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    (extra_report.root / "reports" / "late-audit.json").write_text("{}\n", encoding="utf-8")
    with pytest.raises(DatasetRunError, match="过期"):
        promote_completed_run(
            tmp_path,
            extra_report.run_id,
            expected_previous_digest=approved.content_digest,
        )

    stale_payload = complete_dataset_run(
        create_fixture_run(tmp_path, seed="stale-payload"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    (stale_payload.normalized_root / "debug.tmp").write_text("changed\n", encoding="utf-8")
    with pytest.raises(DatasetRunError, match="未管理文件"):
        promote_completed_run(
            tmp_path,
            stale_payload.run_id,
            expected_previous_digest=approved.content_digest,
        )

    locked = complete_dataset_run(
        create_fixture_run(tmp_path, seed="locked"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    (tmp_path / PROMOTION_LOCK_NAME).write_text("held\n", encoding="utf-8")
    with pytest.raises(PromotionLockedError):
        promote_completed_run(tmp_path, locked.run_id, expected_previous_digest=approved.content_digest)

    assert (tmp_path / APPROVED_POINTER_NAME).read_bytes() == before


def test_completion_generator_state_must_match_manifest(tmp_path: Path) -> None:
    completed = complete_dataset_run(
        create_fixture_run(tmp_path, seed="generator-state"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    completion_path = completed.root / "completion.json"
    completion = json.loads(completion_path.read_text(encoding="utf-8"))
    completion["generator_state"]["commit"] = "b" * 40
    completion_path.write_text(json.dumps(completion) + "\n", encoding="utf-8")

    with pytest.raises(DatasetRunError, match="generator state"):
        load_completed_run(tmp_path, completed.run_id)


def test_completion_failures_never_touch_existing_pointer(tmp_path: Path) -> None:
    approved = complete_dataset_run(
        create_fixture_run(tmp_path, seed="approved"),
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility=eligible_fixture(),
    )
    promote_completed_run(tmp_path, approved.run_id, expected_previous_digest=None)
    before = (tmp_path / APPROVED_POINTER_NAME).read_bytes()

    missing_report = create_fixture_run(tmp_path, seed="missing-report")
    (missing_report.report_root / "quality-metrics.json").unlink()
    with pytest.raises(DatasetRunError, match="缺少必需报告"):
        complete_dataset_run(missing_report, passed_result(), elapsed_seconds=1)

    failed_step = create_fixture_run(tmp_path, seed="failed-step")
    with pytest.raises(DatasetRunError, match="阻断步骤失败"):
        complete_dataset_run(failed_step, failed_result("validate", 1), elapsed_seconds=1)

    assert (tmp_path / APPROVED_POINTER_NAME).read_bytes() == before


def create_fixture_run(
    runs_root: Path,
    *,
    seed: str,
    dirty: bool = False,
) -> DatasetRun:
    """创建带最小 normalized manifest 与报告的 staging fixture。"""

    run = create_dataset_run(runs_root, repo_root=REPO_ROOT, command=["fixture"])
    run.normalized_root.mkdir(parents=True)
    identity = {
        "fixture_seed": seed,
        "input_identity": {
            "generator_state": {
                "commit": "a" * 40,
                "dirty": dirty,
            }
        },
    }
    files: list[dict[str, object]] = []
    digest = content_digest(identity, files)
    manifest = {
        "dataset_id": f"local-v1-{digest[:16]}",
        "content_digest": digest,
        "content_identity": identity,
        "files": files,
    }
    (run.normalized_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    run.report_root.mkdir(parents=True)
    (run.report_root / "normalized-validation.json").write_text('{"errors": 0}\n', encoding="utf-8")
    (run.report_root / "quality-metrics.json").write_text('{"warnings": 0}\n', encoding="utf-8")
    return run


def passed_result() -> RefreshResult:
    """返回五步全通过的结果。"""

    return RefreshResult(
        results=[
            RefreshStepResult(name=name, argv=[name], exit_code=0, blocking=True)
            for name in ("decompose", "build", "validate", "metrics", "ratchet")
        ]
    )


def failed_result(name: str, exit_code: int) -> RefreshResult:
    """返回单个阻断步骤失败结果。"""

    return RefreshResult(results=[RefreshStepResult(name=name, argv=[name], exit_code=exit_code, blocking=True)])


def non_blocking_failure_result() -> RefreshResult:
    """返回 ratchet 仅告警失败的可诊断 completed candidate。"""

    result = passed_result()
    result.results[-1] = RefreshStepResult(name="ratchet", argv=["ratchet"], exit_code=1, blocking=False)
    return result


def eligible_fixture() -> dict[str, object]:
    """返回仅供状态机测试的显式 promotion attestation。"""

    return {"eligible": True, "gate_version": "fixture.v1", "reason": "test fixture"}


def complete_manual_review_candidate(
    runs_root: Path,
    *,
    seed: str,
    automatic_gates_passed: bool = True,
) -> DatasetRun:
    """固化需要最终 dataset approval 的 completed candidate。"""

    run = create_fixture_run(runs_root, seed=seed)
    manifest = json.loads((run.normalized_root / "manifest.json").read_text(encoding="utf-8"))
    dataset_id = str(manifest["dataset_id"])
    payload = {"dataset_id": dataset_id, "content_digest": manifest["content_digest"]}
    target = {
        "target_type": "dataset",
        "target_id": dataset_id,
        "target_content_hash": canonical_digest(payload),
        "payload": payload,
        "allowed_actions": ["accept"],
        "required": True,
    }
    bundle = {
        "schema_version": 1,
        "review_bundle_id": f"bundle-{seed}",
        "lifecycle_state": "awaiting_manual_review",
        "dataset_id": dataset_id,
        "content_digest": manifest["content_digest"],
        "source_decision_set_digest": "a" * 64,
        "phase": "dataset_approval",
        "source_targets": [],
        "approval_targets": [target],
        "targets": [target],
        "required_target_count": 1,
    }
    review_root = run.report_root / "review-site"
    review_root.mkdir()
    (review_root / "review-bundle.json").write_text(json.dumps(bundle) + "\n", encoding="utf-8")
    return complete_dataset_run(
        run,
        passed_result(),
        elapsed_seconds=1,
        promotion_eligibility={
            "eligible": False,
            "automatic_gates_passed": automatic_gates_passed,
            "gate_version": "stable-product-gates.v1",
            "reason": "manual review pending" if automatic_gates_passed else "automatic gates failed",
            "lifecycle_state": "awaiting_manual_review",
        },
    )


def dataset_approval(candidate: DatasetRun) -> dict[str, object]:
    """构造与 fixture candidate 绑定的最终批准决定。"""

    manifest = json.loads((candidate.normalized_root / "manifest.json").read_text(encoding="utf-8"))
    payload = {"dataset_id": candidate.dataset_id, "content_digest": candidate.content_digest}
    decision = {
        "decision_schema_version": 1,
        "decision_id": "dataset-approval",
        "target_type": "dataset",
        "target_id": candidate.dataset_id,
        "target_content_hash": canonical_digest(payload),
        "action": "accept",
        "rationale": "fixture approval",
        "reviewer": "reviewer@example.test",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
        "payload": payload,
    }
    decisions = [decision]
    return {
        "schema_version": 1,
        "review_bundle_id": f"bundle-{manifest['content_identity']['fixture_seed']}",
        "dataset_id": candidate.dataset_id,
        "content_digest": candidate.content_digest,
        "source_decision_set_digest": "a" * 64,
        "decision_set_digest": decision_set_digest(decisions),
        "decisions": decisions,
    }
