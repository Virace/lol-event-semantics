"""版本化人工审核决定、长期源应用与最终数据集批准。"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .advisory_lock import AdvisoryLockUnavailableError, advisory_file_lock
from .dataset_identity import canonical_json_bytes, review_decision_set_digest

__all__ = [
    "BUNDLE_SCHEMA_VERSION",
    "DECISION_SCHEMA_VERSION",
    "DECISION_SET_SCHEMA_VERSION",
    "DECISION_SOURCE_SCHEMA_VERSION",
    "ReviewApplyError",
    "ReviewDecisionError",
    "SourceReviewStaleError",
    "apply_source_review",
    "build_target",
    "canonical_digest",
    "decision_set_digest",
    "load_decision_set",
    "load_decision_source",
    "load_review_bundle",
    "validate_dataset_approval",
    "validate_decision_set",
    "verify_source_review",
]

BUNDLE_SCHEMA_VERSION = 1
DECISION_SCHEMA_VERSION = 1
DECISION_SET_SCHEMA_VERSION = 1
DECISION_SOURCE_SCHEMA_VERSION = 1

TARGET_TYPES = frozenset({"pattern", "record", "exception", "support", "regression", "dataset"})
ACTIONS = frozenset({"accept", "reject", "edit_template", "override_argument", "change_support_level", "supersede"})
SOURCE_PHASE = "source_review"
APPROVAL_PHASE = "dataset_approval"
TRANSACTION_DIR_NAME = ".review-source-transaction"
TRANSACTION_LOCK_NAME = ".review-source-transaction.lock"


class ReviewDecisionError(ValueError):
    """review bundle 或 decision contract 不合法。"""


class ReviewApplyError(ReviewDecisionError):
    """决定尚不能安全应用到长期源。"""


class SourceReviewStaleError(ReviewDecisionError):
    """已接受的长期源决定不再命中当前稳定审核目标。"""


class _DecisionTargetStaleError(ReviewDecisionError):
    """决定结构有效，但绑定的审核目标集合或内容已经变化。"""


def canonical_digest(value: object) -> str:
    """返回规范 JSON 值的 SHA-256。"""

    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def build_target(
    target_type: str,
    target_id: str,
    payload: Mapping[str, object],
    *,
    actions: Iterable[str],
    required: bool,
) -> dict[str, object]:
    """构造带内容哈希的审核目标。"""

    action_list = sorted(set(actions))
    if target_type not in TARGET_TYPES:
        raise ReviewDecisionError(f"不支持的 review target_type: {target_type}")
    if not target_id:
        raise ReviewDecisionError("review target_id 不能为空")
    if not action_list or any(action not in ACTIONS for action in action_list):
        raise ReviewDecisionError(f"review target actions 无效: {target_id}")
    document = deepcopy(dict(payload))
    return {
        "target_type": target_type,
        "target_id": target_id,
        "target_content_hash": canonical_digest(document),
        "allowed_actions": action_list,
        "required": required,
        "payload": document,
    }


def decision_set_digest(decisions: Iterable[Mapping[str, object]]) -> str:
    """返回排除审计 envelope 的 canonical decision set digest。"""

    return review_decision_set_digest(decisions)


def load_review_bundle(path: Path) -> dict[str, Any]:
    """读取并完整校验 review bundle。"""

    bundle = _read_object(path)
    _validate_bundle(bundle)
    return bundle


def load_decision_set(path: Path) -> dict[str, Any]:
    """读取 decision set；目标绑定在稍后的 bundle 校验中完成。"""

    document = _read_object(path)
    if document.get("schema_version") != DECISION_SET_SCHEMA_VERSION:
        raise ReviewDecisionError(f"不支持的 decision set schema: {path}")
    decisions = _object_list(document.get("decisions"), "decision set decisions")
    expected = decision_set_digest(decisions)
    if document.get("decision_set_digest") != expected:
        raise ReviewDecisionError("decision_set_digest 与 canonical decisions 不一致")
    return document


def load_decision_source(path: Path, *, allow_transaction_state: bool = False) -> dict[str, Any]:
    """读取并校验提交入库的长期 review decision source。"""

    if allow_transaction_state:
        return _load_decision_source_unlocked(path)
    try:
        with _review_transaction_lock(path):
            _reject_unfinished_transaction(path)
            return _load_decision_source_unlocked(path)
    except ReviewApplyError as exc:
        raise ReviewDecisionError(str(exc)) from exc


def _load_decision_source_unlocked(path: Path) -> dict[str, Any]:
    """在调用方持有 transaction lock 时读取长期 decision source。"""

    document = _read_object(path)
    if document.get("schema_version") != DECISION_SOURCE_SCHEMA_VERSION:
        raise ReviewDecisionError(f"不支持的 decision source schema: {path}")
    _required_text(document, "decision_set_id", "decision source")
    if document.get("status") not in {"pending", "accepted"}:
        raise ReviewDecisionError("decision source status 必须是 pending 或 accepted")
    decisions = _object_list(document.get("decisions"), "decision source decisions")
    expected = decision_set_digest(decisions)
    if document.get("decision_set_digest") != expected:
        raise ReviewDecisionError("decision source digest 与 canonical decisions 不一致")
    if document.get("status") == "pending" and decisions:
        raise ReviewDecisionError("pending decision source 不得包含已应用决定")
    return document


def validate_decision_set(
    bundle: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    require_complete: bool = True,
) -> dict[str, Any]:
    """校验 decision set 与当前 bundle 目标、hash 和允许 action 一致。"""

    _validate_bundle(bundle)
    _reject_unknown_fields(
        document,
        {
            "schema_version",
            "review_bundle_id",
            "dataset_id",
            "content_digest",
            "source_decision_set_digest",
            "decision_set_digest",
            "decisions",
            "exported_at",
        },
        "decision set",
    )
    if document.get("schema_version") != DECISION_SET_SCHEMA_VERSION:
        raise ReviewDecisionError("不支持的 decision set schema")
    identity_fields = {
        "review_bundle_id": bundle.get("review_bundle_id"),
        "dataset_id": bundle.get("dataset_id"),
        "content_digest": bundle.get("content_digest"),
        "source_decision_set_digest": bundle.get("source_decision_set_digest"),
    }
    for field, expected in identity_fields.items():
        if document.get(field) != expected:
            raise ReviewDecisionError(f"decision set {field} 与 review bundle 不一致")

    targets = _target_map(bundle.get("targets"), "review bundle targets")
    decisions = _object_list(document.get("decisions"), "decision set decisions")
    seen_ids: set[str] = set()
    seen_targets: set[tuple[str, str]] = set()
    normalized: list[dict[str, Any]] = []
    for index, decision in enumerate(decisions):
        location = f"decisions[{index}]"
        if decision.get("decision_schema_version") != DECISION_SCHEMA_VERSION:
            raise ReviewDecisionError(f"{location} schema version 无效")
        _reject_unknown_fields(
            decision,
            {
                "decision_schema_version",
                "decision_id",
                "target_type",
                "target_id",
                "target_content_hash",
                "action",
                "payload",
                "rationale",
                "reviewer",
                "reviewed_at",
            },
            location,
        )
        decision_id = _required_text(decision, "decision_id", location)
        target_type = _required_text(decision, "target_type", location)
        target_id = _required_text(decision, "target_id", location)
        target_hash = _required_digest(decision, "target_content_hash", location)
        action = _required_text(decision, "action", location)
        _required_text(decision, "rationale", location)
        _required_text(decision, "reviewer", location)
        _required_time(decision, "reviewed_at", location)
        if not isinstance(decision.get("payload"), dict):
            raise ReviewDecisionError(f"{location}.payload 必须是 object")
        if decision_id in seen_ids:
            raise ReviewDecisionError(f"decision_id 冲突: {decision_id}")
        key = (target_type, target_id)
        if key in seen_targets:
            raise ReviewDecisionError(f"同一 target 存在冲突决定: {target_type}:{target_id}")
        target = targets.get(key)
        if target is None:
            raise _DecisionTargetStaleError(f"decision 引用未知 target: {target_type}:{target_id}")
        if target_hash != target.get("target_content_hash"):
            raise _DecisionTargetStaleError(f"decision target_content_hash 已过期: {target_type}:{target_id}")
        allowed = target.get("allowed_actions")
        if not isinstance(allowed, list) or action not in allowed:
            raise ReviewDecisionError(f"target 不允许 action={action}: {target_type}:{target_id}")
        seen_ids.add(decision_id)
        seen_targets.add(key)
        normalized.append(deepcopy(decision))

    if require_complete:
        missing = sorted(
            f"{target_type}:{target_id}"
            for (target_type, target_id), target in targets.items()
            if target.get("required") is True and (target_type, target_id) not in seen_targets
        )
        if missing:
            raise _DecisionTargetStaleError(f"decision set 尚有未决定目标: {', '.join(missing)}")

    expected_digest = decision_set_digest(normalized)
    if document.get("decision_set_digest") != expected_digest:
        raise ReviewDecisionError("decision_set_digest 与 canonical decisions 不一致")
    result = deepcopy(dict(document))
    result["decisions"] = sorted(
        normalized,
        key=lambda item: (str(item["target_type"]), str(item["target_id"]), str(item["decision_id"])),
    )
    return result


def apply_source_review(
    bundle: Mapping[str, Any],
    document: Mapping[str, Any],
    *,
    source_path: Path,
    corpus_path: Path,
) -> dict[str, object]:
    """把全接受的 source review 原子应用到 decision source 与 corpus。"""

    if bundle.get("phase") != SOURCE_PHASE:
        raise ReviewApplyError("只有 source_review bundle 可以应用到长期源")
    validated = validate_decision_set(bundle, document)
    decisions = _object_list(validated.get("decisions"), "validated decisions")
    unresolved = [
        f"{decision['target_type']}:{decision['target_id']}:{decision['action']}"
        for decision in decisions
        if decision.get("action") != "accept"
    ]
    if unresolved:
        raise ReviewApplyError("含需要集中修改长期源的决定，未写入任何文件: " + ", ".join(sorted(unresolved)))

    with _review_transaction_lock(source_path):
        _recover_review_transaction(source_path, corpus_path)
        return _apply_accepted_source_review(
            bundle,
            validated,
            source_path=source_path,
            corpus_path=corpus_path,
        )


def _apply_accepted_source_review(
    bundle: Mapping[str, Any],
    validated: Mapping[str, Any],
    *,
    source_path: Path,
    corpus_path: Path,
) -> dict[str, object]:
    """在 transaction lock 内应用已验证的全接受决定。"""

    decisions = _object_list(validated.get("decisions"), "validated decisions")
    source = load_decision_source(source_path, allow_transaction_state=True)
    if source.get("decision_set_digest") != bundle.get("source_decision_set_digest"):
        raise ReviewApplyError("decision source 已变化，review bundle 过期")
    corpus = _read_object(corpus_path)
    entries = _object_list(corpus.get("entries"), "accepted corpus entries")
    _verify_corpus_targets(bundle, entries)
    _verify_corpus_contract(bundle, corpus)
    pattern_decisions = {
        str(decision["target_id"]): decision for decision in decisions if decision.get("target_type") == "pattern"
    }
    entry_ids = {str(entry.get("pattern_id")) for entry in entries}
    if entry_ids != set(pattern_decisions):
        raise ReviewApplyError("pattern decisions 与 accepted corpus 范围不一致")

    reviewed_entries: list[dict[str, Any]] = []
    for entry in entries:
        reviewed = deepcopy(entry)
        pattern_id = str(reviewed["pattern_id"])
        decision = pattern_decisions[pattern_id]
        reviewed["review_status"] = "accepted"
        reviewed["review_decision_id"] = decision["decision_id"]
        reviewed_entries.append(reviewed)

    reviewers = sorted({str(decision["reviewer"]) for decision in decisions})
    reviewed_at = max(str(decision["reviewed_at"]) for decision in decisions)
    decision_digest = str(validated["decision_set_digest"])
    updated_corpus = deepcopy(corpus)
    updated_corpus.update(
        {
            "review_status": "accepted",
            "review_decision_set_digest": decision_digest,
            "entries": reviewed_entries,
        }
    )
    updated_source = {
        "schema_version": DECISION_SOURCE_SCHEMA_VERSION,
        "decision_set_id": source["decision_set_id"],
        "status": "accepted",
        "decision_set_digest": decision_digest,
        "reviewers": reviewers,
        "reviewed_at": reviewed_at,
        "decisions": decisions,
    }
    _replace_documents({source_path: updated_source, corpus_path: updated_corpus})
    return {
        "status": "applied",
        "decision_set_digest": decision_digest,
        "decision_count": len(decisions),
        "pattern_count": len(pattern_decisions),
    }


def verify_source_review(
    bundle: Mapping[str, Any],
    source: Mapping[str, Any],
    corpus: Mapping[str, Any],
) -> dict[str, object]:
    """证明长期决定在当前 bundle 中仍命中，且 corpus 已无 pending。"""

    _validate_bundle(bundle)
    if source.get("status") != "accepted":
        raise ReviewDecisionError("decision source 尚未 accepted")
    decisions = _object_list(source.get("decisions"), "decision source decisions")
    verification_bundle = deepcopy(dict(bundle))
    verification_bundle["phase"] = SOURCE_PHASE
    verification_bundle["targets"] = deepcopy(bundle.get("source_targets"))
    verification_bundle["required_target_count"] = sum(
        target.get("required") is True for target in _object_list(verification_bundle["targets"], "source targets")
    )
    verification_document = {
        "schema_version": DECISION_SET_SCHEMA_VERSION,
        "review_bundle_id": bundle.get("review_bundle_id"),
        "dataset_id": bundle.get("dataset_id"),
        "content_digest": bundle.get("content_digest"),
        "source_decision_set_digest": bundle.get("source_decision_set_digest"),
        "decision_set_digest": source.get("decision_set_digest"),
        "decisions": decisions,
    }
    try:
        validated = validate_decision_set(verification_bundle, verification_document)
    except _DecisionTargetStaleError as exc:
        raise SourceReviewStaleError(str(exc)) from exc
    decisions = _object_list(validated.get("decisions"), "validated source decisions")
    targets = _target_map(bundle.get("source_targets"), "source targets")
    by_target = {(str(item.get("target_type")), str(item.get("target_id"))): item for item in decisions}
    for key, target in targets.items():
        if target.get("required") is not True:
            continue
        decision = by_target.get(key)
        if decision is None or decision.get("action") != "accept":
            raise ReviewDecisionError(f"accepted source 含未接受目标: {key[0]}:{key[1]}")
        if decision.get("target_content_hash") != target.get("target_content_hash"):
            raise SourceReviewStaleError(f"source target hash 已漂移: {key[0]}:{key[1]}")

    entries = _object_list(corpus.get("entries"), "accepted corpus entries")
    _verify_corpus_targets(bundle, entries)
    _verify_corpus_contract(bundle, corpus)
    if corpus.get("review_status") != "accepted" or any(entry.get("review_status") != "accepted" for entry in entries):
        raise ReviewDecisionError("accepted corpus 仍含 pending entry")
    if corpus.get("review_decision_set_digest") != source.get("decision_set_digest"):
        raise ReviewDecisionError("accepted corpus 与 decision source digest 不一致")
    return {
        "status": "passed",
        "decision_set_digest": source["decision_set_digest"],
        "decision_count": len(decisions),
        "accepted_corpus_entry_count": len(entries),
    }


def _verify_corpus_targets(bundle: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
    """证明当前 corpus 与 bundle 中逐 pattern 审核的内容仍一致。"""

    targets = _target_map(bundle.get("source_targets"), "source targets")
    pattern_targets = {
        target_id: target for (target_type, target_id), target in targets.items() if target_type == "pattern"
    }
    entries_by_id: dict[str, dict[str, Any]] = {}
    for entry in entries:
        pattern_id = _required_text(entry, "pattern_id", "accepted corpus entry")
        if pattern_id in entries_by_id:
            raise ReviewDecisionError(f"accepted corpus pattern_id 重复: {pattern_id}")
        entries_by_id[pattern_id] = entry
    if set(entries_by_id) != set(pattern_targets):
        raise ReviewDecisionError("accepted corpus 与 source pattern targets 范围不一致")
    for pattern_id, target in pattern_targets.items():
        payload = target.get("payload")
        expected = payload.get("accepted_corpus") if isinstance(payload, dict) else None
        if not isinstance(expected, dict):
            raise ReviewDecisionError(f"source pattern target 缺少 accepted_corpus: {pattern_id}")
        actual = {field: deepcopy(entries_by_id[pattern_id].get(field)) for field in expected}
        if actual != expected:
            raise ReviewDecisionError(f"accepted corpus 已漂移: {pattern_id}")


def _verify_corpus_contract(bundle: Mapping[str, Any], corpus: Mapping[str, Any]) -> None:
    """证明 corpus 根级选择合同仍与 support target 一致。"""

    targets = _target_map(bundle.get("source_targets"), "source targets")
    support_targets = [target for (target_type, _), target in targets.items() if target_type == "support"]
    if len(support_targets) != 1:
        raise ReviewDecisionError("source targets 必须包含一个 support target")
    payload = support_targets[0].get("payload")
    expected = payload.get("accepted_corpus") if isinstance(payload, dict) else None
    if not isinstance(expected, dict):
        raise ReviewDecisionError("support target 缺少 accepted_corpus 合同")
    actual = {
        "schema_version": corpus.get("schema_version"),
        "corpus_id": corpus.get("corpus_id"),
        "decision_id": corpus.get("decision_id"),
        "selection_policy": deepcopy(corpus.get("selection_policy")),
        "entry_count": len(_object_list(corpus.get("entries"), "accepted corpus entries")),
    }
    if actual != expected:
        raise ReviewDecisionError("accepted corpus 根合同已漂移")


def validate_dataset_approval(
    bundle: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, object]:
    """验证最终 dataset approval 与 candidate 身份和已应用源决定一致。"""

    if bundle.get("phase") != APPROVAL_PHASE:
        raise ReviewDecisionError("candidate 尚未进入 dataset_approval 阶段")
    validated = validate_decision_set(bundle, document)
    decisions = _object_list(validated.get("decisions"), "dataset approval decisions")
    dataset = [decision for decision in decisions if decision.get("target_type") == "dataset"]
    if len(dataset) != 1 or dataset[0].get("action") != "accept":
        raise ReviewDecisionError("最终批准必须包含一个 accept dataset decision")
    return {
        "decision_set_digest": validated["decision_set_digest"],
        "decision_id": dataset[0]["decision_id"],
        "reviewer": dataset[0]["reviewer"],
        "reviewed_at": dataset[0]["reviewed_at"],
        "source_decision_set_digest": bundle["source_decision_set_digest"],
    }


def _validate_bundle(bundle: Mapping[str, Any]) -> None:
    if bundle.get("schema_version") != BUNDLE_SCHEMA_VERSION:
        raise ReviewDecisionError("不支持的 review bundle schema")
    _required_text(bundle, "review_bundle_id", "review bundle")
    _required_text(bundle, "dataset_id", "review bundle")
    _required_digest(bundle, "content_digest", "review bundle")
    _required_digest(bundle, "source_decision_set_digest", "review bundle")
    if bundle.get("lifecycle_state") != "awaiting_manual_review":
        raise ReviewDecisionError("review bundle lifecycle_state 无效")
    if bundle.get("phase") not in {SOURCE_PHASE, APPROVAL_PHASE}:
        raise ReviewDecisionError("review bundle phase 无效")
    source_targets = _target_map(bundle.get("source_targets"), "review bundle source_targets")
    approval_targets = _target_map(bundle.get("approval_targets"), "review bundle approval_targets")
    targets = _target_map(bundle.get("targets"), "review bundle targets")
    expected = source_targets if bundle.get("phase") == SOURCE_PHASE else approval_targets
    if targets != expected:
        raise ReviewDecisionError("review bundle targets 与当前 phase 不一致")
    required_count = sum(target.get("required") is True for target in targets.values())
    if bundle.get("required_target_count") != required_count:
        raise ReviewDecisionError("review bundle required_target_count 无效")


def _target_map(value: object, location: str) -> dict[tuple[str, str], dict[str, Any]]:
    targets = _object_list(value, location)
    result: dict[tuple[str, str], dict[str, Any]] = {}
    for index, target in enumerate(targets):
        item_location = f"{location}[{index}]"
        _reject_unknown_fields(
            target,
            {"target_type", "target_id", "target_content_hash", "allowed_actions", "required", "payload"},
            item_location,
        )
        target_type = _required_text(target, "target_type", item_location)
        target_id = _required_text(target, "target_id", item_location)
        target_hash = _required_digest(target, "target_content_hash", item_location)
        if target_type not in TARGET_TYPES:
            raise ReviewDecisionError(f"{item_location}.target_type 无效")
        if not isinstance(target.get("payload"), dict):
            raise ReviewDecisionError(f"{item_location}.payload 必须是 object")
        if canonical_digest(target["payload"]) != target_hash:
            raise ReviewDecisionError(f"{item_location}.target_content_hash 无效")
        actions = target.get("allowed_actions")
        if (
            not isinstance(actions, list)
            or not actions
            or len(actions) != len(set(actions))
            or any(action not in ACTIONS for action in actions)
        ):
            raise ReviewDecisionError(f"{item_location}.allowed_actions 无效")
        if not isinstance(target.get("required"), bool):
            raise ReviewDecisionError(f"{item_location}.required 必须是 bool")
        key = (target_type, target_id)
        if key in result:
            raise ReviewDecisionError(f"review target 重复: {target_type}:{target_id}")
        result[key] = deepcopy(target)
    return result


def _reject_unknown_fields(document: Mapping[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(document) - allowed)
    if unknown:
        raise ReviewDecisionError(f"{location} 含未知字段: {', '.join(unknown)}")


def _replace_documents(documents: Mapping[Path, Mapping[str, object]]) -> None:
    paths = list(documents)
    if not paths:
        return
    journal = paths[0].parent / TRANSACTION_DIR_NAME
    if journal.exists():
        raise ReviewApplyError(f"存在未恢复的 review source transaction: {journal}")

    staged: dict[Path, Path] = {}
    backups: list[Path] = []
    prepared = False
    try:
        journal.mkdir()
        targets: list[dict[str, object]] = []
        for index, (path, document) in enumerate(documents.items()):
            path.parent.mkdir(parents=True, exist_ok=True)
            original = path.read_bytes() if path.exists() else None
            backup = journal / f"original-{index}.bin"
            if original is not None:
                _write_bytes(backup, original)
                backups.append(backup)
            stage = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
            _write_stage(stage, document)
            staged[path] = stage
            targets.append(
                {
                    "path": str(path.resolve()),
                    "stage_path": str(stage.resolve()),
                    "original_present": original is not None,
                    "original_sha256": _sha256_bytes(original) if original is not None else None,
                    "replacement_sha256": _sha256_bytes(stage.read_bytes()),
                }
            )
        _write_stage(
            journal / "prepared.json",
            {"schema_version": 1, "status": "prepared", "targets": targets},
        )
        prepared = True
        _fsync_dir(journal)
        _fsync_dir(journal.parent)
        for path, stage in staged.items():
            os.replace(stage, path)
        for parent in {path.parent for path in documents}:
            _fsync_dir(parent)
        _write_stage(
            journal / "committed.json",
            {"schema_version": 1, "status": "committed", "targets": targets},
        )
        _fsync_dir(journal)
        _cleanup_review_transaction(journal, paths)
    except OSError as exc:
        try:
            if prepared:
                committed = (journal / "committed.json").is_file()
                _recover_transaction(journal, paths)
                if committed:
                    return
            else:
                for backup in backups:
                    backup.unlink(missing_ok=True)
                journal.rmdir()
        except (OSError, ReviewDecisionError) as recovery_error:
            raise ReviewApplyError(
                f"review source transaction failed: {exc}; recovery pending: {recovery_error}"
            ) from exc
        raise ReviewApplyError(f"review source transaction failed and was rolled back: {exc}") from exc
    finally:
        for stage in staged.values():
            stage.unlink(missing_ok=True)


@contextmanager
def _review_transaction_lock(source_path: Path) -> Iterator[None]:
    lock = source_path.parent / TRANSACTION_LOCK_NAME
    try:
        with advisory_file_lock(lock):
            yield
    except AdvisoryLockUnavailableError as exc:
        raise ReviewApplyError(f"另一 review source transaction 正在运行: {lock}") from exc


def _reject_unfinished_transaction(source_path: Path) -> None:
    journal = source_path.parent / TRANSACTION_DIR_NAME
    if journal.exists():
        raise ReviewDecisionError("检测到未收口的 review source transaction；请重新执行 apply 触发恢复")


def _recover_review_transaction(source_path: Path, corpus_path: Path) -> None:
    journal = source_path.parent / TRANSACTION_DIR_NAME
    if journal.exists():
        if not (journal / "prepared.json").exists() and not (journal / "committed.json").exists():
            journal.rmdir()
            return
        _recover_transaction(journal, [source_path, corpus_path])


def _recover_transaction(journal: Path, paths: list[Path]) -> None:
    committed_path = journal / "committed.json"
    document = _read_object(committed_path if committed_path.is_file() else journal / "prepared.json")
    targets = _object_list(document.get("targets"), "review transaction targets")
    expected_paths = [str(path.resolve()) for path in paths]
    actual_paths = [str(target.get("path")) for target in targets]
    expected_status = "committed" if committed_path.is_file() else "prepared"
    if document.get("schema_version") != 1 or document.get("status") != expected_status:
        raise ReviewApplyError("review transaction journal schema 无效")
    if actual_paths != expected_paths:
        raise ReviewApplyError("review transaction targets 与调用范围不一致")

    if committed_path.is_file():
        for path, target in zip(paths, targets, strict=True):
            if not path.is_file() or _sha256_bytes(path.read_bytes()) != target.get("replacement_sha256"):
                raise ReviewApplyError(f"已提交 review transaction 的目标已漂移: {path}")
        _cleanup_review_transaction(journal, paths)
        return

    for index, (path, target) in reversed(list(enumerate(zip(paths, targets, strict=True)))):
        if target.get("original_present") is True:
            backup = journal / f"original-{index}.bin"
            original = backup.read_bytes()
            if _sha256_bytes(original) != target.get("original_sha256"):
                raise ReviewApplyError(f"review transaction backup 已损坏: {backup}")
            restore = path.with_name(f".{path.name}.{secrets.token_hex(6)}.restore")
            try:
                _write_bytes(restore, original)
                os.replace(restore, path)
            finally:
                restore.unlink(missing_ok=True)
        else:
            path.unlink(missing_ok=True)
    for parent in {path.parent for path in paths}:
        _fsync_dir(parent)
    _cleanup_review_transaction(journal, paths)


def _cleanup_review_transaction(journal: Path, paths: list[Path]) -> None:
    committed_path = journal / "committed.json"
    document = _read_object(committed_path if committed_path.is_file() else journal / "prepared.json")
    targets = _object_list(document.get("targets"), "review transaction targets")
    for index, (path, target) in enumerate(zip(paths, targets, strict=True)):
        stage = Path(str(target.get("stage_path")))
        if stage.parent.resolve() != path.parent.resolve() or not stage.name.startswith(f".{path.name}."):
            raise ReviewApplyError("review transaction stage path 无效")
        stage.unlink(missing_ok=True)
        (journal / f"original-{index}.bin").unlink(missing_ok=True)
    (journal / "prepared.json").unlink(missing_ok=True)
    committed_path.unlink(missing_ok=True)
    journal.rmdir()
    _fsync_dir(journal.parent)


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _write_stage(path: Path, document: Mapping[str, object]) -> None:
    payload = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write_bytes(path, payload)


def _write_bytes(path: Path, payload: bytes) -> None:
    with path.open("xb") as file:
        file.write(payload)
        file.flush()
        os.fsync(file.fileno())


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewDecisionError(f"无法读取 JSON object: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReviewDecisionError(f"JSON 根必须是 object: {path}")
    return value


def _object_list(value: object, location: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise ReviewDecisionError(f"{location} 必须是 object array")
    return [deepcopy(item) for item in value]


def _required_text(document: Mapping[str, Any], field: str, location: str) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ReviewDecisionError(f"{location}.{field} 必须是非空字符串")
    return value


def _required_digest(document: Mapping[str, Any], field: str, location: str) -> str:
    value = _required_text(document, field, location)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ReviewDecisionError(f"{location}.{field} 必须是小写 SHA-256")
    return value


def _required_time(document: Mapping[str, Any], field: str, location: str) -> str:
    value = _required_text(document, field, location)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReviewDecisionError(f"{location}.{field} 必须是 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise ReviewDecisionError(f"{location}.{field} 必须包含时区")
    return value
