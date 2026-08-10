"""P5 产品审核 bundle 的生命周期与身份稳定性测试。"""

from __future__ import annotations

import json
from pathlib import Path

from lol_event_semantics.dataset_identity import review_decision_identity, rules_tree_digest
from lol_event_semantics.product_review import build_review_bundle, write_review_site
from lol_event_semantics.review_decisions import decision_set_digest


def test_bundle_transitions_from_source_review_to_dataset_approval(tmp_path: Path) -> None:
    """长期 source 决定和 corpus 接受后，bundle 仅切换到最终批准 target。"""

    normalized_root, support_diff_path, source_path, corpus_path = write_review_inputs(tmp_path)
    first = build_review_bundle(normalized_root, support_diff_path, source_path, source_path.parent)
    assert first["phase"] == "source_review"
    assert {target["target_type"] for target in first["targets"]} == {"pattern", "support"}

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
            "scope_contract": {"matched": True},
            "accepted_corpus": {"results": []},
        },
    )
    corpus_path = examples / "accepted-corpus.v1.json"
    write_json(
        corpus_path,
        {
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
