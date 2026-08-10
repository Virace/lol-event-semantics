"""P5 审核决定的 fail-closed 与长期源应用测试。"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

import lol_event_semantics.review_decisions as review_module
from lol_event_semantics.review_decisions import (
    ReviewApplyError,
    ReviewDecisionError,
    apply_source_review,
    build_target,
    decision_set_digest,
    load_decision_set,
    load_review_bundle,
    validate_dataset_approval,
    validate_decision_set,
    verify_source_review,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_committed_review_examples_match_runtime_contract() -> None:
    """入库的 bundle/decision examples 必须可由正式 runtime 读取。"""

    assert load_review_bundle(REPO_ROOT / "data/examples/review-bundle.v1.json")["schema_version"] == 1
    assert load_decision_set(REPO_ROOT / "data/examples/review-decisions.v1.json")["schema_version"] == 1


def test_decision_set_rejects_duplicate_conflict_and_stale_target() -> None:
    """重复、同目标冲突和内容漂移都必须在写入前被拒绝。"""

    bundle = source_bundle()
    decision = decision_for(bundle["targets"][0])
    document = decision_document(bundle, [decision])

    duplicate_id = copy.deepcopy(decision)
    duplicate_id["target_id"] = "pattern-v1-other"
    duplicate_id["target_content_hash"] = bundle["targets"][1]["target_content_hash"]
    duplicate_id["target_type"] = "support"
    with pytest.raises(ReviewDecisionError, match="decision_id 冲突"):
        validate_decision_set(bundle, decision_document(bundle, [decision, duplicate_id]))

    conflict = copy.deepcopy(decision)
    conflict["decision_id"] = "decision-v1-conflict"
    with pytest.raises(ReviewDecisionError, match="同一 target"):
        validate_decision_set(bundle, decision_document(bundle, [decision, conflict]))

    stale = copy.deepcopy(decision)
    stale["target_content_hash"] = "f" * 64
    with pytest.raises(ReviewDecisionError, match="已过期"):
        validate_decision_set(bundle, decision_document(bundle, [stale]), require_complete=False)


def test_decision_digest_excludes_audit_envelope() -> None:
    """审核人、理由和点击时间不改变决定语义摘要。"""

    decision = decision_for(source_bundle()["targets"][0])
    changed_audit = {
        **decision,
        "rationale": "另一份审计说明。",
        "reviewer": "another-reviewer",
        "reviewed_at": "2026-08-11T12:34:56+00:00",
    }

    assert decision_set_digest([decision]) == decision_set_digest([changed_audit])


def test_source_review_apply_is_atomic_and_verifiable(tmp_path: Path) -> None:
    """全接受 source review 同时写入 source/corpus，随后可复核。"""

    bundle = source_bundle()
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    write_json(corpus_path, pending_corpus())
    decisions = [decision_for(target) for target in bundle["targets"]]

    result = apply_source_review(
        bundle,
        decision_document(bundle, decisions),
        source_path=source_path,
        corpus_path=corpus_path,
    )

    source = read_json(source_path)
    corpus = read_json(corpus_path)
    assert result["status"] == "applied"
    assert source["status"] == "accepted"
    assert corpus["review_status"] == "accepted"
    assert all(entry["review_status"] == "accepted" for entry in corpus["entries"])
    assert verify_source_review(bundle, source, corpus)["status"] == "passed"


def test_source_review_rolls_back_when_second_replace_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """第二个文件替换失败时恢复两个原文件并清理 journal。"""

    bundle = source_bundle()
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    write_json(corpus_path, pending_corpus())
    before_source = source_path.read_bytes()
    before_corpus = corpus_path.read_bytes()
    real_replace = review_module.os.replace
    calls = 0

    def fail_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(review_module.os, "replace", fail_second_replace)

    with pytest.raises(ReviewApplyError, match="rolled back"):
        apply_source_review(
            bundle,
            decision_document(bundle, [decision_for(target) for target in bundle["targets"]]),
            source_path=source_path,
            corpus_path=corpus_path,
        )

    assert source_path.read_bytes() == before_source
    assert corpus_path.read_bytes() == before_corpus
    assert not (tmp_path / review_module.TRANSACTION_DIR_NAME).exists()


def test_source_review_recovers_prepared_journal_after_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两次替换间中断后，下一次 apply 先恢复再完整提交。"""

    bundle = source_bundle()
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    write_json(corpus_path, pending_corpus())
    document = decision_document(bundle, [decision_for(target) for target in bundle["targets"]])
    real_replace = review_module.os.replace
    calls = 0

    def interrupt_second_replace(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SystemExit("injected interruption")
        real_replace(source, destination)

    monkeypatch.setattr(review_module.os, "replace", interrupt_second_replace)

    with pytest.raises(SystemExit, match="injected interruption"):
        apply_source_review(bundle, document, source_path=source_path, corpus_path=corpus_path)

    assert (tmp_path / review_module.TRANSACTION_DIR_NAME).is_dir()
    result = apply_source_review(bundle, document, source_path=source_path, corpus_path=corpus_path)

    assert result["status"] == "applied"
    assert read_json(source_path)["status"] == "accepted"
    assert read_json(corpus_path)["review_status"] == "accepted"
    assert not (tmp_path / review_module.TRANSACTION_DIR_NAME).exists()


def test_source_review_recovers_after_hard_killed_process_leaves_lock(tmp_path: Path) -> None:
    """进程硬退出留下 lock/journal 时，下次 apply 必须先恢复再完整提交。"""

    bundle = source_bundle()
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    write_json(corpus_path, pending_corpus())
    before_source = source_path.read_bytes()
    before_corpus = corpus_path.read_bytes()
    document = decision_document(bundle, [decision_for(target) for target in bundle["targets"]])
    child_script = tmp_path / "hard_kill_apply.py"
    child_script.write_text(
        "\n".join(
            [
                "import json",
                "import os",
                "from pathlib import Path",
                "import lol_event_semantics.review_decisions as review_module",
                "bundle = json.loads(Path(" + repr(str(tmp_path / "bundle.json")) + ").read_text(encoding='utf-8'))",
                "document = json.loads(Path("
                + repr(str(tmp_path / "document.json"))
                + ").read_text(encoding='utf-8'))",
                "source_path = Path(" + repr(str(source_path)) + ")",
                "corpus_path = Path(" + repr(str(corpus_path)) + ")",
                "real_replace = review_module.os.replace",
                "calls = 0",
                "def hard_kill(source, destination):",
                "    global calls",
                "    calls += 1",
                "    if calls == 2:",
                "        os._exit(73)",
                "    real_replace(source, destination)",
                "review_module.os.replace = hard_kill",
                "review_module.apply_source_review(bundle, document, source_path=source_path, corpus_path=corpus_path)",
            ]
        ),
        encoding="utf-8",
    )
    write_json(tmp_path / "bundle.json", bundle)
    write_json(tmp_path / "document.json", document)

    result = subprocess.run([sys.executable, str(child_script)], check=False)

    assert result.returncode == 73
    assert (tmp_path / review_module.TRANSACTION_LOCK_NAME).is_file()
    assert (tmp_path / review_module.TRANSACTION_DIR_NAME).is_dir()
    assert source_path.read_bytes() != before_source
    assert corpus_path.read_bytes() == before_corpus

    recovered = apply_source_review(bundle, document, source_path=source_path, corpus_path=corpus_path)

    assert recovered["status"] == "applied"
    assert read_json(source_path)["status"] == "accepted"
    assert read_json(corpus_path)["review_status"] == "accepted"
    assert (tmp_path / review_module.TRANSACTION_LOCK_NAME).is_file()
    assert not (tmp_path / review_module.TRANSACTION_DIR_NAME).exists()


def test_source_review_rejects_corpus_content_that_drifted_after_bundle(tmp_path: Path) -> None:
    """bundle 后 corpus 内容漂移时，source/corpus 都不得写入。"""

    pattern = build_target(
        "pattern",
        "pattern-v1-example",
        {"accepted_corpus": {"pattern_id": "pattern-v1-example", "raw_event_name": "Play_vo_Expected"}},
        actions=("accept", "reject"),
        required=True,
    )
    support = build_target(
        "support",
        "stable-scope:v1",
        {"message": "support"},
        actions=("accept", "reject"),
        required=True,
    )
    bundle = bundle_for("source_review", [pattern, support], source_targets=[pattern, support], approval_targets=[])
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    corpus = pending_corpus()
    corpus["entries"][0]["raw_event_name"] = "Play_vo_Changed"
    write_json(corpus_path, corpus)
    before_source = source_path.read_bytes()
    before_corpus = corpus_path.read_bytes()

    with pytest.raises(ReviewDecisionError, match="accepted corpus 已漂移"):
        apply_source_review(
            bundle,
            decision_document(bundle, [decision_for(pattern), decision_for(support)]),
            source_path=source_path,
            corpus_path=corpus_path,
        )

    assert source_path.read_bytes() == before_source
    assert corpus_path.read_bytes() == before_corpus


@pytest.mark.parametrize("action", ["reject", "edit_template", "change_support_level"])
def test_non_accept_source_decisions_leave_long_term_inputs_unchanged(tmp_path: Path, action: str) -> None:
    """要求修改或变更支持范围时，集中修复前不得写入长期源。"""

    bundle = source_bundle(actions=("accept", "reject", "edit_template", "change_support_level"))
    source_path = tmp_path / "review-decisions.v1.json"
    corpus_path = tmp_path / "accepted-corpus.v1.json"
    write_json(source_path, pending_source())
    write_json(corpus_path, pending_corpus())
    before_source = source_path.read_bytes()
    before_corpus = corpus_path.read_bytes()
    decisions = [
        decision_for(target, action=action if index == 0 else "accept")
        for index, target in enumerate(bundle["targets"])
    ]

    with pytest.raises(ReviewApplyError, match="未写入任何文件"):
        apply_source_review(
            bundle,
            decision_document(bundle, decisions),
            source_path=source_path,
            corpus_path=corpus_path,
        )

    assert source_path.read_bytes() == before_source
    assert corpus_path.read_bytes() == before_corpus


def test_dataset_approval_requires_current_dataset_accept() -> None:
    """最终批准必须绑定 dataset target 且明确接受。"""

    bundle = approval_bundle()
    accepted = decision_document(bundle, [decision_for(bundle["targets"][0])])
    assert validate_dataset_approval(bundle, accepted)["decision_id"] == accepted["decisions"][0]["decision_id"]

    rejected = decision_document(bundle, [decision_for(bundle["targets"][0], action="reject")])
    with pytest.raises(ReviewDecisionError, match="accept dataset"):
        validate_dataset_approval(bundle, rejected)


def source_bundle(*, actions: tuple[str, ...] = ("accept", "reject")) -> dict[str, object]:
    """构造两个必审 source target 的最小合法 bundle。"""

    targets = [
        build_target("pattern", "pattern-v1-example", {"message": "pattern"}, actions=actions, required=True),
        build_target("support", "stable-scope:v1", {"message": "support"}, actions=actions, required=True),
    ]
    return bundle_for("source_review", targets, source_targets=targets, approval_targets=[])


def approval_bundle() -> dict[str, object]:
    """构造单一最终 dataset target 的最小合法 bundle。"""

    target = build_target(
        "dataset",
        "local-v1-example",
        {"dataset_id": "local-v1-example"},
        actions=("accept", "reject"),
        required=True,
    )
    return bundle_for("dataset_approval", [target], source_targets=[], approval_targets=[target])


def bundle_for(
    phase: str,
    targets: list[dict[str, object]],
    *,
    source_targets: list[dict[str, object]],
    approval_targets: list[dict[str, object]],
) -> dict[str, object]:
    """构造 review decision API 所需的公共身份字段。"""

    return {
        "schema_version": 1,
        "review_bundle_id": "review-bundle-v1-example",
        "lifecycle_state": "awaiting_manual_review",
        "dataset_id": "local-v1-example",
        "content_digest": "0" * 64,
        "source_decision_set_digest": decision_set_digest([]),
        "phase": phase,
        "source_targets": source_targets,
        "approval_targets": approval_targets,
        "targets": targets,
        "required_target_count": sum(target["required"] is True for target in targets),
    }


def decision_for(target: object, *, action: str = "accept") -> dict[str, object]:
    """从当前 target 构造可验证决定。"""

    assert isinstance(target, dict)
    return {
        "decision_schema_version": 1,
        "decision_id": f"decision-v1-{target['target_type']}-{target['target_id']}",
        "target_type": target["target_type"],
        "target_id": target["target_id"],
        "target_content_hash": target["target_content_hash"],
        "action": action,
        "payload": {},
        "rationale": "已人工确认。",
        "reviewer": "tester",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
    }


def decision_document(bundle: dict[str, object], decisions: list[dict[str, object]]) -> dict[str, object]:
    """按实现同一 canonical digest 封装 decision set。"""

    return {
        "schema_version": 1,
        "review_bundle_id": bundle["review_bundle_id"],
        "dataset_id": bundle["dataset_id"],
        "content_digest": bundle["content_digest"],
        "source_decision_set_digest": bundle["source_decision_set_digest"],
        "decisions": decisions,
        "decision_set_digest": decision_set_digest(decisions),
    }


def pending_source() -> dict[str, object]:
    """构造空的长期 decision source。"""

    return {
        "schema_version": 1,
        "decision_set_id": "v1-test",
        "status": "pending",
        "decision_set_digest": decision_set_digest([]),
        "decisions": [],
    }


def pending_corpus() -> dict[str, object]:
    """构造与 pattern target 一一对应的待审 corpus。"""

    return {
        "schema_version": 1,
        "review_status": "pending",
        "entries": [{"pattern_id": "pattern-v1-example", "review_status": "pending"}],
    }


def write_json(path: Path, document: dict[str, object]) -> None:
    """写出稳定 JSON fixture。"""

    path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    """读取 fixture JSON。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
