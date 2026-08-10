"""normalized dataset V2 schema、单流与兼容校验测试。"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.dataset_identity import content_digest, describe_payload_files
from lol_event_semantics.normalized_contract import record_id_for_normalized_record
from lol_event_semantics.normalized_validation import validate_normalized_root
from lol_event_semantics.pattern_catalog import (
    canonical_pattern_signature,
    load_pattern_policy,
    pattern_id_for_signature,
)

ROOT = Path(__file__).parents[1]
DATA_ROOT = ROOT / "data"


class NormalizedV2ContractTests(unittest.TestCase):
    """验证 V2 valid/invalid fixture 与 V1 dual-read 边界。"""

    def test_valid_v2_fixture_has_no_hard_errors(self) -> None:
        """完整 V2 单流、catalog、schema/examples 与兼容分片可联合校验。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0, [issue.to_dict() for issue in report.issues])
        self.assertEqual(report.record_count, 1)

    def test_legacy_v2_not_evaluated_fixture_does_not_require_p4_assets(self) -> None:
        """P4 条件资产不得破坏旧 V2 not_evaluated 的只读兼容。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            for relative in (
                "quality-summary.json",
                "schema/quality-summary.schema.json",
                "examples/quality-summary.v1.json",
                "examples/consumer.py",
                "examples/accepted-corpus.v1.json",
            ):
                (root / relative).unlink()
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            identity = manifest["content_identity"]
            identity["quality_status"] = "not_evaluated"
            identity.pop("quality_gate_version", None)
            manifest["outputs"] = [output for output in manifest["outputs"] if output != "quality-summary.json"]
            rewrite_manifest_identity(root, manifest)

            report = validate_normalized_root(root)

        self.assertEqual(report.error_count, 0, [issue.to_dict() for issue in report.issues])

    def test_passed_quality_requires_frozen_stable_scope_contract(self) -> None:
        """status=passed 不能绕过 V1 Stable decision/pattern/record 绝对范围。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            quality_path = root / "quality-summary.json"
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality["status"] = "passed"
            quality_path.write_text(json.dumps(quality), encoding="utf-8")
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["content_identity"]["quality_status"] = "passed"
            rewrite_manifest_identity(root, manifest)

            report = validate_normalized_root(root)
            completed = run_source_free_consumer(root)

        self.assertIn("stable_scope_contract_mismatch", {issue.code for issue in report.issues})
        self.assertEqual(completed.returncode, 2)
        self.assertIn("frozen V1 Stable scope", completed.stdout)

    def test_v2_missing_required_record_field_fails_closed(self) -> None:
        """V2 行缺 record_id 时必须是硬错误。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            shard = next((root / "by-entity").rglob("*.jsonl"))
            row = json.loads(shard.read_text(encoding="utf-8"))
            del row["record_id"]
            shard.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("v2_record_required_field_missing", codes)
        self.assertIn("record_id_invalid", codes)

    def test_unknown_manifest_and_record_major_versions_are_rejected(self) -> None:
        """dual-read 只接受 V1/V2，不静默吞掉未知 major。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["schema_version"] = 99
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            shard = next((root / "by-entity").rglob("*.jsonl"))
            row = json.loads(shard.read_text(encoding="utf-8"))
            row["schema_version"] = 99
            shard.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

            report = validate_normalized_root(root)

        codes = {issue.code for issue in report.issues}
        self.assertIn("manifest_schema_version_invalid", codes)
        self.assertIn("record_schema_version_invalid", codes)

    def test_duplicate_record_id_is_a_hard_error(self) -> None:
        """V2 公开主键重复不得降级成 raw warning。"""

        row = v2_record()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [row, copy.deepcopy(row)])

            report = validate_normalized_root(root)

        self.assertIn("duplicate_record_id", {issue.code for issue in report.issues})

    def test_source_scope_must_match_canonical_provenance(self) -> None:
        """可查询 source_scope 不能与来源路径派生身份漂移。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            shard = next((root / "by-entity").rglob("*.jsonl"))
            row = json.loads(shard.read_text(encoding="utf-8"))
            row["source_scope"] = "maps"
            shard.write_text(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

            report = validate_normalized_root(root)

        self.assertIn("source_scope_identity_mismatch", {issue.code for issue in report.issues})

    def test_committed_schema_declares_v2_required_identity_fields(self) -> None:
        """提交入库的 Schema 直接声明 record identity/support/render 字段。"""

        schema = json.loads((DATA_ROOT / "schema" / "normalized-event.schema.json").read_text(encoding="utf-8"))
        required = set(schema["required"])

        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertTrue(
            {
                "record_id",
                "source_scope",
                "provenance",
                "asset_kind",
                "pattern_id",
                "support_level",
                "render_status",
                "review_status",
            }.issubset(required)
        )

    def test_committed_examples_are_identity_consistent(self) -> None:
        """示例 record_id 与 catalog pattern 引用可直接复算和关联。"""

        row = json.loads((DATA_ROOT / "examples" / "normalized-event.v2.json").read_text(encoding="utf-8"))
        catalog = json.loads((DATA_ROOT / "examples" / "pattern-catalog.v1.json").read_text(encoding="utf-8"))
        policy = load_pattern_policy(ROOT / "rules" / "translation_patterns.yaml")

        self.assertEqual(row["record_id"], record_id_for_normalized_record(row))
        self.assertEqual(row["pattern_id"], catalog["patterns"][0]["pattern_id"])
        self.assertEqual(row["pattern_id"], pattern_id_for_signature(canonical_pattern_signature(row, policy)))

    def test_source_free_consumer_verifies_and_queries_without_project_import(self) -> None:
        """复制进数据集的 stdlib consumer 可脱离项目源码校验并查询。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            row = v2_record()
            write_v2_fixture(root, [row])
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "examples" / "consumer.py"),
                    "--root",
                    str(root),
                    "--query",
                    str(row["raw_event_name"]),
                    "--expect",
                    "experimental",
                ],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": ""},
            )

        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertTrue(result["verified"])
        self.assertEqual(result["matches"][0]["raw_event_name"], row["raw_event_name"])

    def test_source_free_consumer_rejects_payload_tamper(self) -> None:
        """consumer 在读取 stream 前先验证 manifest 文件哈希。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            with (root / "normalized-events.jsonl").open("a", encoding="utf-8") as file:
                file.write("{}\n")
            completed = subprocess.run(
                [sys.executable, str(root / "examples" / "consumer.py"), "--root", str(root)],
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": ""},
            )

        self.assertEqual(completed.returncode, 2)
        self.assertIn("payload byte size mismatch", completed.stdout)

    def test_source_free_consumer_rejects_unhashed_required_stream(self) -> None:
        """必需消费路径即使仍存在，也不得从 manifest hash 闭包移除。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            manifest["files"] = [entry for entry in manifest["files"] if entry["path"] != "normalized-events.jsonl"]
            digest = content_digest(manifest["content_identity"], manifest["files"])
            manifest["content_digest"] = digest
            manifest["dataset_id"] = f"local-v1-{digest[:16]}"
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

            completed = run_source_free_consumer(root)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("missing required payload paths", completed.stdout)

    def test_source_free_consumer_rejects_unlisted_payload_file(self) -> None:
        """manifest.files 必须与 manifest 之外的实际 payload 文件集完全相等。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_v2_fixture(root, [v2_record()])
            (root / "unlisted.json").write_text("{}\n", encoding="utf-8")

            completed = run_source_free_consumer(root)

        self.assertEqual(completed.returncode, 2)
        self.assertIn("payload closure mismatch", completed.stdout)


def run_source_free_consumer(root: Path) -> subprocess.CompletedProcess[str]:
    """以无项目 import 环境运行 fixture 内 consumer。"""

    return subprocess.run(
        [sys.executable, str(root / "examples" / "consumer.py"), "--root", str(root)],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": ""},
    )


def rewrite_manifest_identity(root: Path, manifest: dict[str, object]) -> None:
    """在 fixture 变更后重算 payload table、content digest 与 dataset id。"""

    files = describe_payload_files(root)
    identity = manifest["content_identity"]
    assert isinstance(identity, dict)
    digest = content_digest(identity, files)
    manifest["files"] = files
    manifest["content_digest"] = digest
    manifest["dataset_id"] = f"local-v1-{digest[:16]}"
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def v2_record() -> dict[str, object]:
    """读取并修正为 canonical identity 一致的 V2 example record。"""

    row = json.loads((DATA_ROOT / "examples" / "normalized-event.v2.json").read_text(encoding="utf-8"))
    row["record_id"] = record_id_for_normalized_record(row)
    return row


def write_v2_fixture(root: Path, rows: list[dict[str, object]]) -> None:
    """写出含 V2 单流、catalog、schemas/examples 与 legacy shards 的 fixture。"""

    normalized_rows = [copy.deepcopy(row) for row in rows]
    for row in normalized_rows:
        row["record_id"] = record_id_for_normalized_record(row)

    alias = str(normalized_rows[0]["entity"]["alias"])
    shard = root / "by-entity" / "champions" / f"{alias}.jsonl"
    shard.parent.mkdir(parents=True)
    shard.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in normalized_rows),
        encoding="utf-8",
    )
    (root / "normalized-events.jsonl").write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in sorted(normalized_rows, key=lambda item: str(item["record_id"]))
        ),
        encoding="utf-8",
    )

    pattern_catalog = json.loads((DATA_ROOT / "examples" / "pattern-catalog.v1.json").read_text(encoding="utf-8"))
    pattern_catalog["patterns"][0]["pattern_id"] = normalized_rows[0]["pattern_id"]
    pattern_catalog["patterns"][0]["record_count"] = len(normalized_rows)
    (root / "pattern-catalog.json").write_text(
        json.dumps(pattern_catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    manifest = json.loads((DATA_ROOT / "examples" / "manifest.v2.json").read_text(encoding="utf-8"))
    manifest["record_count"] = len(normalized_rows)
    (root / "entity-summary.json").write_text(
        json.dumps(
            [
                {
                    "kind": "champion",
                    "alias": alias,
                    "record_count": len(normalized_rows),
                    "path": f"by-entity/champions/{alias}.jsonl",
                }
            ],
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copytree(DATA_ROOT / "schema", root / "schema")
    shutil.copytree(DATA_ROOT / "examples", root / "examples")
    shutil.copyfile(DATA_ROOT / "accepted-corpus.v1.json", root / "examples" / "accepted-corpus.v1.json")
    quality = json.loads((DATA_ROOT / "examples" / "quality-summary.v1.json").read_text(encoding="utf-8"))
    record_support_counts = value_counts(normalized_rows, "support_level")
    pattern_level = str(pattern_catalog["patterns"][0]["support_level"])
    stable_record_count = record_support_counts.get("stable", 0)
    stable_pattern_count = int(pattern_level == "stable")
    quality["status"] = "failed"
    quality["global"]["record_count"] = len(normalized_rows)
    quality["global"]["pattern_count"] = 1
    quality["global"]["support"] = {
        "record_counts": record_support_counts,
        "pattern_counts": {pattern_level: 1},
    }
    quality["stable"]["passed"] = False
    quality["stable"]["record_count"] = stable_record_count
    quality["stable"]["pattern_count"] = stable_pattern_count
    quality["stable"]["approved_pattern_count"] = 0
    quality["stable"]["violation_counts"] = {"fixture_not_v1_scope": 1}
    quality["experimental"]["record_count"] = record_support_counts.get("experimental", 0)
    quality["scope_contract"]["actual_pattern_count"] = stable_pattern_count
    quality["scope_contract"]["actual_record_count"] = stable_record_count
    quality["scope_contract"]["matched"] = False
    quality["accepted_corpus"]["passed"] = False
    quality["accepted_corpus"]["failed_entry_count"] = 1
    (root / "quality-summary.json").write_text(
        json.dumps(quality, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    identity = manifest["content_identity"]
    identity["support_level_counts"] = record_support_counts
    identity["review"]["record_counts"] = value_counts(normalized_rows, "review_status")
    identity["quality_status"] = quality["status"]
    identity["quality_gate_version"] = quality["gate_version"]
    if "quality-summary.json" not in manifest["outputs"]:
        manifest["outputs"].append("quality-summary.json")
    rewrite_manifest_identity(root, manifest)


def value_counts(rows: list[dict[str, object]], field: str) -> dict[str, int]:
    """按字符串字段统计 V2 fixture 计数。"""

    counts: dict[str, int] = {}
    for row in rows:
        value = str(row[field])
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


if __name__ == "__main__":
    unittest.main()
