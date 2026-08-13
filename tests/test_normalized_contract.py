"""normalized V2 identity 与 additive contract 测试。"""

from __future__ import annotations

import copy
import json
import re
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.normalized_contract import (
    RECORD_ID_ALGORITHM_VERSION,
    apply_normalized_contract,
    build_public_pattern_catalog,
    canonical_record_identity,
    merge_equivalent_decomposition_records,
    normalized_asset_kind,
    record_id_for_normalized_record,
    source_identity_from_paths,
)
from lol_event_semantics.normalized_output import read_decomposition_root
from lol_event_semantics.pattern_catalog import load_pattern_policy

ROOT = Path(__file__).parents[1]
POLICY_PATH = ROOT / "rules" / "translation_patterns.yaml"


class NormalizedContractTests(unittest.TestCase):
    """验证 record identity、重复合并与公开增量字段。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载仓库冻结的 pattern policy。"""

        cls.policy = load_pattern_policy(POLICY_PATH)

    def test_record_id_ignores_path_root_order_and_runtime_metadata(self) -> None:
        """record_id 不依赖本机根、source_paths 顺序或生成时间。"""

        first = minimal_normalized_row()
        first["source_paths"] = [
            r"C:\checkout\raw\events\maps\11.txt",
            r"C:\checkout\raw\events\champions\1.txt",
        ]
        first["generated_at"] = "2026-08-07T00:00:00Z"
        second = copy.deepcopy(first)
        second["source_paths"] = [
            r"H:\other\raw\events\champions\1.txt",
            r"H:\other\raw\events\maps\11.txt",
        ]
        second["generated_at"] = "2099-01-01T00:00:00Z"

        self.assertEqual(canonical_record_identity(first), canonical_record_identity(second))
        self.assertEqual(record_id_for_normalized_record(first), record_id_for_normalized_record(second))
        identity = canonical_record_identity(first)
        self.assertEqual(identity[0], RECORD_ID_ALGORITHM_VERSION)
        self.assertNotIn("C:", str(identity))
        self.assertNotIn("H:", str(identity))

    def test_record_id_changes_for_owner_or_canonical_source_asset(self) -> None:
        """owner 或 canonical asset 不同时 record_id 必须不同。"""

        base = minimal_normalized_row()
        different_owner = copy.deepcopy(base)
        different_owner["entity"]["alias"] = "Ahri"
        different_asset = copy.deepcopy(base)
        different_asset["source_paths"] = ["raw/events/champions/103.txt"]

        ids = {
            record_id_for_normalized_record(base),
            record_id_for_normalized_record(different_owner),
            record_id_for_normalized_record(different_asset),
        }
        self.assertEqual(len(ids), 3)

    def test_source_identity_exposes_stable_scope_without_absolute_paths(self) -> None:
        """provenance 保留来源语义但剥离本机绝对根。"""

        identity = source_identity_from_paths(
            [r"C:\repo\raw\events\maps\11.txt", r"C:\repo\raw\events\champions\1.txt"]
        )

        self.assertEqual(identity.scope, "champions+maps")
        self.assertEqual(identity.domains, ("champions", "maps"))
        self.assertEqual(identity.canonical_assets, ("champions:1", "maps:11"))
        self.assertEqual(
            identity.provenance_paths,
            ("raw/events/champions/1.txt", "raw/events/maps/11.txt"),
        )

    def test_equivalent_partition_records_merge_source_provenance(self) -> None:
        """champions/maps 重叠分片的等价记录合并为一条。"""

        champion = decompose_event_name(
            "Set_Switch_aggro_off",
            game_version="16.14",
            source_path="raw/events/champions/83.txt",
        )
        map_record = copy.deepcopy(champion)
        map_record.source_paths = ["raw/events/maps/11.txt"]

        merged = merge_equivalent_decomposition_records([champion, map_record])

        self.assertEqual(len(merged), 1)
        self.assertEqual(
            merged[0].source_paths,
            ["raw/events/champions/83.txt", "raw/events/maps/11.txt"],
        )

    def test_semantically_distinct_records_are_not_merged(self) -> None:
        """同 raw 但语义内容不同不能被分片去重掩盖。"""

        first = decompose_event_name("Play_vo_Annie_Move", game_version="16.14")
        second = decompose_event_name("Play_vo_Annie_Move", game_version="16.14")
        second.semantic_slots["test_distinction"] = "different"

        self.assertEqual(len(merge_equivalent_decomposition_records([first, second])), 2)

    def test_decomposition_root_deduplicates_overlapping_partition_rows(self) -> None:
        """实际 root reader 不把同一记录从 champions/maps 各读一遍。"""

        record = decompose_event_name(
            "Set_Switch_aggro_off",
            game_version="16.14",
            source_path="raw/events/champions/83.txt",
        )
        record.source_paths.append("raw/events/maps/11.txt")
        payload = json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "champions.jsonl").write_text(payload, encoding="utf-8")
            (root / "maps.jsonl").write_text(payload, encoding="utf-8")

            records = read_decomposition_root(root)

        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].source_paths), 2)

    def test_additive_contract_fields_are_complete_and_queryable(self) -> None:
        """兼容行获得 identity、provenance、pattern、support 与 render 视图。"""

        row = minimal_normalized_row()
        apply_normalized_contract([row], self.policy)

        self.assertRegex(str(row["record_id"]), re.compile(r"^record-v1-[0-9a-f]{64}$"))
        self.assertEqual(row["record_id_algorithm"], RECORD_ID_ALGORITHM_VERSION)
        self.assertEqual(row["source_scope"], "champions")
        self.assertEqual(row["provenance"]["canonical_assets"], ["champions:1"])
        self.assertEqual(row["asset_kind"], "vo")
        self.assertRegex(str(row["pattern_id"]), re.compile(r"^pattern-v1-[0-9a-f]{64}$"))
        self.assertEqual(row["support_level"], "experimental")
        self.assertEqual(row["review_status"], "pending")
        self.assertEqual(row["semantic_frame"]["frame_type"], "voice_event")
        self.assertTrue(row["rendered_labels"]["zh_CN"]["default"])
        self.assertTrue(row["rendered_labels"]["zh_CN"]["main"])
        self.assertEqual(row["render_status"], "rendered")
        self.assertEqual(row["warnings"], ["z_warning"])
        self.assertEqual(row["schema_version"], 1)

    def test_record_id_collision_fails_closed(self) -> None:
        """同一公开身份出现两次时不得用 occurrence index 自动消歧。"""

        row = minimal_normalized_row()
        with self.assertRaisesRegex(ValueError, "record_id collision"):
            apply_normalized_contract([row, copy.deepcopy(row)], self.policy)

    def test_public_pattern_catalog_contains_consumer_contract_fields(self) -> None:
        """公共 catalog 从同批 inventory 生成稳定模板、角色和审计字段。"""

        row = minimal_normalized_row()
        report = apply_normalized_contract([row], self.policy)
        catalog = build_public_pattern_catalog(report, self.policy)
        pattern = catalog["patterns"][0]

        self.assertEqual(catalog["schema_version"], 1)
        self.assertEqual(catalog["pattern_count"], 1)
        self.assertEqual(pattern["pattern_id"], row["pattern_id"])
        self.assertEqual(pattern["asset_kind"], "vo")
        self.assertEqual(pattern["required_roles"], ["action"])
        self.assertEqual(pattern["optional_roles"], ["champion", "companion", "skin"])
        self.assertEqual(pattern["support_level"], "experimental")
        self.assertIn("{action}", pattern["zh_CN_template"]["main"])
        self.assertEqual(pattern["record_count"], 1)
        self.assertEqual(pattern["example_event_names"], ["Play_vo_Annie_Move"])
        self.assertEqual(
            pattern["zh_cn_label_variants"],
            {
                "action": {
                    "distinct_label_count": 1,
                    "variants": [
                        {
                            "label": "移动",
                            "record_count": 1,
                            "sample_raw_event_names": ["Play_vo_Annie_Move"],
                        }
                    ],
                }
            },
        )

    def test_public_asset_kind_maps_control_and_unknown_namespaces(self) -> None:
        """公共 asset_kind 只暴露 VO、SFX、control、unknown 四值。"""

        self.assertEqual(normalized_asset_kind("Play_vo_Annie_Move"), "vo")
        self.assertEqual(normalized_asset_kind("Play_sfx_Annie_Move"), "sfx")
        self.assertEqual(normalized_asset_kind("Set_Switch_aggro_off"), "control")
        self.assertEqual(normalized_asset_kind("Play_mus_Map11_Theme"), "unknown")


def minimal_normalized_row() -> dict[str, object]:
    """构造满足 voice_event 结构门禁的最小兼容 normalized 行。"""

    return {
        "schema_version": 1,
        "raw_event_name": "Play_vo_Annie_Move",
        "game_version": "16.14",
        "entity": {"kind": "champion", "alias": "Annie"},
        "semantic": {
            "profile": "self_action",
            "primary_family": "voice",
            "slots": {"trigger_family": "movement"},
            "confidence": "high",
            "requires_context": False,
        },
        "display": {"zh_cn_short": "移动语音", "zh_cn_detail": "安妮移动语音", "debug_label": "debug"},
        "quality": {"unknown_count": 0, "candidate_count": 0, "diagnostics": ["z_warning"]},
        "source_paths": ["raw/events/champions/1.txt"],
        "translation": {
            "schema_version": 1,
            "frame_type": "voice_event",
            "frame_version": 1,
            "frame_confidence": "high",
            "arguments": {
                "champion": argument("champion", "annie", "安妮"),
                "action": argument("action", "move", "移动"),
            },
        },
    }


def argument(role: str, key: str, zh_cn: str) -> dict[str, object]:
    """构造无 fallback 的高置信 translation argument。"""

    return {
        "role": role,
        "key": key,
        "labels": {"zh_CN": zh_cn, "en_US": key},
        "confidence": "high",
        "fallback": False,
    }


if __name__ == "__main__":
    unittest.main()
