"""P5 产品审核 bundle 的生命周期与身份稳定性测试。"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from lol_event_semantics.dataset_identity import review_decision_identity, rules_tree_digest
from lol_event_semantics.normalized_contract import apply_normalized_contract, build_public_pattern_catalog
from lol_event_semantics.pattern_catalog import (
    canonical_pattern_signature,
    load_pattern_policy,
    pattern_id_for_signature,
)
from lol_event_semantics.product_review import build_review_bundle, write_review_site
from lol_event_semantics.review_decisions import ReviewDecisionError, decision_set_digest

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "rules" / "translation_patterns.yaml"


def test_bundle_transitions_from_source_review_to_dataset_approval(tmp_path: Path) -> None:
    """长期 source 决定和 corpus 接受后，bundle 仅切换到最终批准 target。"""

    normalized_root, support_diff_path, source_path, corpus_path = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    assert first["phase"] == "source_review"
    assert {target["target_type"] for target in first["targets"]} == {"pattern", "support"}
    assert {target["payload"]["target_contract_version"] for target in first["source_targets"]} == {
        "stable-source-target.v1"
    }

    decisions = [decision_for(target) for target in first["source_targets"]]
    write_json(
        source_path,
        {
            "schema_version": 1,
            "decision_set_id": "v1-test",
            "status": "accepted",
            "decision_set_digest": decision_set_digest(decisions),
            "decisions": decisions,
        },
    )
    corpus = read_json(corpus_path)
    corpus["review_status"] = "accepted"
    corpus["review_decision_set_digest"] = decision_set_digest(decisions)
    corpus["entries"][0]["review_status"] = "accepted"
    write_json(corpus_path, corpus)
    refresh_candidate_identity(normalized_root, source_path)

    final = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    assert final["phase"] == "dataset_approval"
    assert [target["target_type"] for target in final["targets"]] == ["dataset"]
    assert final["source_verification"]["status"] == "passed"


def test_bundle_identity_is_stable_for_same_candidate(tmp_path: Path) -> None:
    """同一 candidate 与长期源不变时，target hash 和 bundle id 均稳定。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    second = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)

    assert first["review_bundle_id"] == second["review_bundle_id"]
    assert first["source_targets"] == second["source_targets"]
    assert first["approval_targets"] == second["approval_targets"]


def test_source_target_identity_ignores_baseline_relative_support_evidence(tmp_path: Path) -> None:
    """长期源目标不因 approved pointer 变化产生的临时 support diff 而过期。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    changed_diff = read_json(support_diff_path)
    changed_diff.update(
        {
            "pattern_changes": [
                {
                    "pattern_id": "pattern-v1-example",
                    "before": {"support_level": "stable"},
                    "after": {"support_level": "stable"},
                }
            ],
            "baseline": {"dataset_id": "local-v1-approved"},
            "current": {"pattern_count": 1, "record_count": 1},
            "transition_counts": {"patterns": {"stable->stable": 1}},
        }
    )
    write_json(support_diff_path, changed_diff)
    second = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)

    assert first["source_targets"] == second["source_targets"]
    assert first["approval_targets"] != second["approval_targets"]
    assert first["support_diff_evidence"] != second["support_diff_evidence"]
    assert second["pattern_support_changes"]["pattern-v1-example"] is not None


def test_source_target_identity_separates_semantics_from_diagnostics(tmp_path: Path) -> None:
    """计数、样例与质量统计只作证据；模板或中文标签集合变化才触发重审。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)

    catalog = read_json(normalized_root / "pattern-catalog.json")
    pattern = catalog["patterns"][0]
    pattern["record_count"] = 99
    pattern["example_event_names"] = ["Play_vo_Another_Move"]
    pattern["warning_counts"] = {"diagnostic_only": 99}
    variant = pattern["zh_cn_label_variants"]["action"]["variants"][0]
    variant["record_count"] = 99
    variant["sample_raw_event_names"] = ["Play_vo_Another_Move"]
    write_json(normalized_root / "pattern-catalog.json", catalog)
    quality = read_json(normalized_root / "quality-summary.json")
    quality["stable"].update(
        {
            "record_count": 99,
            "confidence_distribution": {"semantic": {"medium": 99}},
            "residual_english": {"record_counts_by_category": {"proper_noun": 99}},
            "qualifier_coverage": {"record_count": 99},
            "violation_counts": {"diagnostic_only": 99},
        }
    )
    write_json(normalized_root / "quality-summary.json", quality)
    diagnostics_changed = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)

    assert first["source_targets"] == diagnostics_changed["source_targets"]
    assert first["stable_patterns"] != diagnostics_changed["stable_patterns"]
    assert first["stable_summary"] != diagnostics_changed["stable_summary"]

    catalog["patterns"][0]["zh_cn_label_variants"]["action"]["variants"][0]["label"] = "位移"
    write_json(normalized_root / "pattern-catalog.json", catalog)
    label_changed = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    assert first["source_targets"] != label_changed["source_targets"]


def test_review_bundle_rejects_unmatched_stable_scope(tmp_path: Path) -> None:
    """单独 build review 也不能绕过冻结 Stable 范围门禁。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    quality = read_json(normalized_root / "quality-summary.json")
    quality["scope_contract"]["actual_record_count"] = 2
    quality["scope_contract"]["matched"] = False
    write_json(normalized_root / "quality-summary.json", quality)

    with pytest.raises(ReviewDecisionError, match="Stable scope contract"):
        build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)


def test_stale_accepted_source_returns_to_source_review(tmp_path: Path) -> None:
    """稳定语义目标变化时应重新进入 source review，而不是让整条 refresh 崩溃。"""

    normalized_root, support_diff_path, source_path, corpus_path = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    decisions = [decision_for(target) for target in first["source_targets"]]
    write_json(
        source_path,
        {
            "schema_version": 1,
            "decision_set_id": "v1-test",
            "status": "accepted",
            "decision_set_digest": decision_set_digest(decisions),
            "decisions": decisions,
        },
    )
    corpus = read_json(corpus_path)
    corpus["review_status"] = "accepted"
    corpus["review_decision_set_digest"] = decision_set_digest(decisions)
    corpus["entries"][0]["review_status"] = "accepted"
    write_json(corpus_path, corpus)
    refresh_candidate_identity(normalized_root, source_path)
    catalog = read_json(normalized_root / "pattern-catalog.json")
    catalog["patterns"][0]["zh_CN_template"] = {"main": "位移"}
    write_json(normalized_root / "pattern-catalog.json", catalog)

    stale = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)

    assert stale["phase"] == "source_review"
    assert stale["source_verification"]["status"] == "stale"
    assert "target_content_hash" in stale["source_verification"]["reason"]
    assert {target["target_type"] for target in stale["targets"]} == {"pattern", "support"}


def test_malformed_accepted_source_remains_blocking(tmp_path: Path) -> None:
    """决定 envelope 损坏不是正常 target 漂移，必须继续 fail closed。"""

    normalized_root, support_diff_path, source_path, corpus_path = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    decisions = [decision_for(target) for target in first["source_targets"]]
    decisions[0]["reviewed_at"] = "not-a-timestamp"
    write_json(
        source_path,
        {
            "schema_version": 1,
            "decision_set_id": "v1-test",
            "status": "accepted",
            "decision_set_digest": decision_set_digest(decisions),
            "decisions": decisions,
        },
    )
    corpus = read_json(corpus_path)
    corpus["review_status"] = "accepted"
    corpus["review_decision_set_digest"] = decision_set_digest(decisions)
    corpus["entries"][0]["review_status"] = "accepted"
    write_json(corpus_path, corpus)
    refresh_candidate_identity(normalized_root, source_path)

    with pytest.raises(ReviewDecisionError, match="reviewed_at"):
        build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)


def test_corrupt_accepted_corpus_digest_remains_blocking(tmp_path: Path) -> None:
    """source/corpus 事务身份不一致属于损坏，不得伪装成可重审漂移。"""

    normalized_root, support_diff_path, source_path, corpus_path = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    decisions = [decision_for(target) for target in first["source_targets"]]
    digest = decision_set_digest(decisions)
    write_json(
        source_path,
        {
            "schema_version": 1,
            "decision_set_id": "v1-test",
            "status": "accepted",
            "decision_set_digest": digest,
            "decisions": decisions,
        },
    )
    corpus = read_json(corpus_path)
    corpus["review_status"] = "accepted"
    corpus["review_decision_set_digest"] = "f" * 64
    corpus["entries"][0]["review_status"] = "accepted"
    write_json(corpus_path, corpus)
    refresh_candidate_identity(normalized_root, source_path)

    with pytest.raises(ReviewDecisionError, match="decision source digest"):
        build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)


def test_bundle_exposes_label_variants_and_quality_risk_coverage(tmp_path: Path) -> None:
    """Review target 与摘要必须带上未渲染结构化风险的审核证据。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    first = json.loads((ROOT / "data" / "examples" / "normalized-event.v2.json").read_text(encoding="utf-8"))
    first["raw_event_name"] = "Play_vo_Annie_Dance"
    first["translation"]["arguments"]["action"]["key"] = "dance"
    first["translation"]["arguments"]["action"]["labels"] = {"zh_CN": "跳舞", "en_US": "dance"}
    second = copy.deepcopy(first)
    second["raw_event_name"] = "Play_vo_Ahri_Dance"
    second["entity"]["alias"] = "Ahri"
    second["source_paths"] = ["raw/events/champions/103.txt"]
    second["translation"]["arguments"]["action"]["labels"]["zh_CN"] = "舞蹈"
    records = [first, second]
    policy = load_pattern_policy(POLICY_PATH)
    pattern_id = pattern_id_for_signature(canonical_pattern_signature(records[0], policy))
    decisions = replace(
        policy.promotion_decisions,
        approved_pattern_ids=policy.promotion_decisions.approved_pattern_ids | {pattern_id},
    )
    approved_policy = replace(policy, promotion_decisions=decisions)
    report = apply_normalized_contract(records, approved_policy)
    catalog = build_public_pattern_catalog(report, approved_policy)
    write_json(normalized_root / "pattern-catalog.json", catalog)
    corpus_path = normalized_root / "examples" / "accepted-corpus.v1.json"
    corpus = read_json(corpus_path)
    corpus["entries"][0]["pattern_id"] = pattern_id
    write_json(corpus_path, corpus)
    quality = read_json(normalized_root / "quality-summary.json")
    quality["stable"].update(
        {
            "record_count": 2,
            "confidence_distribution": {"semantic": {"high": 1}, "frame": {"high": 1}},
            "residual_english": {"record_counts_by_category": {"unresolved": 1}},
            "qualifier_coverage": {"record_count": 1, "unresolved_english_record_count": 1},
        }
    )
    quality["experimental"].update(
        {
            "confidence_distribution": {"semantic": {"medium": 2}, "frame": {"medium": 2}},
            "qualifier_coverage": {"record_count": 2, "unresolved_english_record_count": 2},
        }
    )
    write_json(normalized_root / "quality-summary.json", quality)

    bundle = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    target_pattern = bundle["source_targets"][0]["payload"]["pattern"]
    display_pattern = bundle["stable_patterns"][0]["pattern"]

    assert target_pattern["zh_cn_labels"]["action"] == ["舞蹈", "跳舞"]
    assert display_pattern["zh_cn_label_variants"]["action"]["distinct_label_count"] == 2
    assert bundle["stable_summary"]["qualifier_coverage"]["unresolved_english_record_count"] == 1
    assert bundle["experimental_summary"]["confidence_distribution"]["frame"] == {"medium": 2}


def test_review_site_preserves_json_newline_escape(tmp_path: Path) -> None:
    """生成页面不得把导出 JSON 的换行转义展开成非法 JavaScript 跨行字符串。"""

    normalized_root, support_diff_path, source_path, _ = write_review_inputs(tmp_path)
    bundle = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    _, site_path = write_review_site(bundle, tmp_path / "review-site")

    html = site_path.read_text(encoding="utf-8")
    assert "JSON.stringify(decisionDocument,null,2)+'\\n'" in html


def write_review_inputs(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    """写出构建产品审核 bundle 的最小 candidate 与长期源。"""

    normalized_root = tmp_path / "normalized"
    examples = normalized_root / "examples"
    examples.mkdir(parents=True)
    pattern_id = "pattern-v1-example"
    write_json(
        normalized_root / "manifest.json",
        {
            "dataset_id": "local-v1-example",
            "content_digest": "1" * 64,
            "content_identity": {
                "input_identity": {
                    "generator_state": {"commit": "a" * 40, "dirty": False},
                }
            },
        },
    )
    write_json(
        normalized_root / "pattern-catalog.json",
        {
            "patterns": [
                {
                    "pattern_id": pattern_id,
                    "support_level": "stable",
                    "frame_type": "voice_event",
                    "frame_version": 1,
                    "asset_kind": "vo",
                    "record_count": 1,
                    "signature": {"frame_type": "voice_event"},
                    "required_roles": ["action"],
                    "optional_roles": [],
                    "zh_CN_template": {"main": "移动"},
                    "en_US_template": None,
                    "example_event_names": ["Play_vo_Example_Move"],
                    "warning_counts": {},
                    "provenance_policy": {},
                    "zh_cn_label_variants": {
                        "action": {
                            "distinct_label_count": 1,
                            "variants": [
                                {
                                    "label": "移动",
                                    "record_count": 1,
                                    "sample_raw_event_names": ["Play_vo_Example_Move"],
                                }
                            ],
                        }
                    },
                }
            ]
        },
    )
    write_json(
        normalized_root / "quality-summary.json",
        {
            "decision_id": "v1-test",
            "status": "passed",
            "stable": {"record_count": 1, "violation_counts": {}},
            "experimental": {"top_warnings": [], "residual_english": {"top_tokens_by_category": {}}},
            "scope_contract": {
                "matched": True,
                "expected_pattern_count": 1,
                "expected_pattern_ids_digest": "2" * 64,
                "expected_record_count": 1,
                "actual_pattern_count": 1,
                "actual_pattern_ids_digest": "2" * 64,
                "actual_record_count": 1,
            },
            "accepted_corpus": {"results": []},
        },
    )
    corpus_path = examples / "accepted-corpus.v1.json"
    write_json(
        corpus_path,
        {
            "schema_version": 1,
            "corpus_id": "v1-test",
            "decision_id": "v1-test",
            "selection_policy": "lexicographically-first-record.v1",
            "review_status": "pending",
            "entries": [
                {
                    "pattern_id": pattern_id,
                    "frame_type": "voice_event",
                    "signature": {},
                    "required_roles": ["action"],
                    "expected_arguments": {},
                    "raw_event_name": "Play_vo_Example_Move",
                    "zh_cn_default": "示例移动",
                    "zh_cn_main": "移动",
                    "scope_status": "approved",
                    "decision_id": "v1-test",
                    "review_status": "pending",
                }
            ],
        },
    )
    support_diff_path = tmp_path / "support-diff.json"
    write_json(
        support_diff_path,
        {
            "pattern_changes": [],
            "baseline": None,
            "current": {"pattern_count": 1},
            "transition_counts": {"patterns": {"none->stable": 1}},
            "stable_pattern_downgrades": [],
            "stable_record_downgrades": [],
        },
    )
    rules_root = tmp_path / "rules"
    rules_root.mkdir()
    source_path = rules_root / "review-decisions.v1.json"
    write_json(
        source_path,
        {
            "schema_version": 1,
            "decision_set_id": "v1-test",
            "status": "pending",
            "decision_set_digest": decision_set_digest([]),
            "decisions": [],
        },
    )
    refresh_candidate_identity(normalized_root, source_path)
    return normalized_root, support_diff_path, source_path, corpus_path


def refresh_candidate_identity(normalized_root: Path, source_path: Path) -> None:
    """模拟 full refresh 把当前 rules/review source 绑定进 candidate identity。"""

    manifest_path = normalized_root / "manifest.json"
    manifest = read_json(manifest_path)
    input_identity = manifest.setdefault("content_identity", {}).setdefault("input_identity", {})
    input_identity["inputs"] = {
        "rules": rules_tree_digest(source_path.parent).to_dict(),
        "review_decisions": review_decision_identity(read_json(source_path)),
    }
    review_identity = input_identity["inputs"]["review_decisions"]
    manifest["content_identity"]["review"] = {
        "decision_set_id": review_identity["decision_set_id"],
        "decision_set_digest": review_identity["digest"],
        "decision_status": review_identity["status"],
    }
    write_json(manifest_path, manifest)


def decision_for(target: object) -> dict[str, object]:
    """构造 source verification 使用的 accept 决定。"""

    assert isinstance(target, dict)
    return {
        "decision_schema_version": 1,
        "decision_id": f"decision-v1-{target['target_type']}",
        "target_type": target["target_type"],
        "target_id": target["target_id"],
        "target_content_hash": target["target_content_hash"],
        "action": "accept",
        "payload": {},
        "rationale": "已确认。",
        "reviewer": "tester",
        "reviewed_at": "2026-08-10T00:00:00+00:00",
    }


def write_json(path: Path, document: dict[str, object]) -> None:
    """写出测试 JSON。"""

    path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8")


def read_json(path: Path) -> dict[str, object]:
    """读取测试 JSON。"""

    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value
