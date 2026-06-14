"""normalized 输出测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.normalized_output import build_normalized_record, write_normalized_outputs


class NormalizedOutputTests(unittest.TestCase):
    """验证对外 normalized 数据。"""

    def test_short_display_hides_debug_tokens(self) -> None:
        """短文案不暴露事件名内部技术 token。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))
        normalized = build_normalized_record(record)

        self.assertEqual(normalized["raw_event_name"], "Play_vo_Annie_AnnieQ_OnCast")
        self.assertIn("display", normalized)
        display = normalized["display"]
        self.assertIsInstance(display, dict)
        short = display["zh_cn_short"]
        self.assertNotIn("Play_vo", short)
        self.assertNotIn("OnCast", short)
        self.assertNotIn("3D", short)

    def test_write_outputs_entity_shards_without_full_index(self) -> None:
        """输出只保留按实体拆分的数据，不生成完整 raw 索引。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_outputs([record], root, reference_version="16.5")

            self.assertTrue((root / "manifest.json").is_file())
            self.assertTrue((root / "entity-summary.json").is_file())
            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())
            self.assertFalse((root / "raw-event-index.json").exists())

            first_line = (root / "by-entity" / "champions" / "Annie.jsonl").read_text(
                encoding="utf-8"
            ).splitlines()[0]
            payload = json.loads(first_line)
            self.assertEqual(payload["raw_event_name"], "Play_vo_Annie_AnnieQ_OnCast")

    def test_entity_shard_prefers_resolved_subject_hint(self) -> None:
        """内部 owner 形如 AnnieQ 时，分片仍归到真实英雄 Annie。"""

        record = normalize_semantics(decompose_event_name("Play_vo_AnnieQ_cast3D", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_outputs([record], root, reference_version="16.5")

            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())
            self.assertFalse((root / "by-entity" / "champions" / "AnnieQ.jsonl").exists())

            payload = json.loads(
                (root / "by-entity" / "champions" / "Annie.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(payload["display"]["zh_cn_short"], "Annie Q 技能释放")

    def test_writer_removes_stale_generated_shards(self) -> None:
        """重复生成时清理旧 JSONL 分片，避免实体 summary 与文件不一致。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stale = root / "by-entity" / "champions" / "OldChampion.jsonl"
            note = root / "by-entity" / "README.md"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}\n", encoding="utf-8")
            note.write_text("manual note\n", encoding="utf-8")

            write_normalized_outputs([record], root, reference_version="16.5")

            self.assertFalse(stale.exists())
            self.assertTrue(note.exists())
            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())


class NormalizedOutputCliTests(unittest.TestCase):
    """验证 normalized 输出 CLI。"""

    def test_cli_writes_outputs_from_decomposition_jsonl(self) -> None:
        """CLI 能读取拆解 JSONL 并写出 normalized 分片。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            decomposition_root = root / "event-decomposition"
            output_root = root / "normalized"
            decomposition_root.mkdir()
            (decomposition_root / "champions.jsonl").write_text(
                json.dumps(record.to_dict(), ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/build_normalized_outputs.py",
                    "--decomposition-root",
                    str(decomposition_root),
                    "--reference-version",
                    "16.5",
                    "--output-root",
                    str(output_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output_root / "manifest.json").is_file())
            self.assertTrue((output_root / "by-entity" / "champions" / "Annie.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
