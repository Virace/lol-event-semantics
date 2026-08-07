"""normalized dataset V2 schema、单流与兼容校验测试。"""

from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

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
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
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


if __name__ == "__main__":
    unittest.main()
