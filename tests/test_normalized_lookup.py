"""normalized 事件名查询测试。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.normalized_lookup import lookup_normalized_events
from lol_event_semantics.normalized_translation_model import RenderOptions


class NormalizedLookupTests(unittest.TestCase):
    """验证 normalized lookup 逻辑。"""

    def test_lookup_event_renders_translation(self) -> None:
        """按 raw_event_name 命中记录并输出 renderer 文案。"""

        raw = "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_lookup_fixture(root, [galio_record()])

            results = lookup_normalized_events(
                root,
                [raw],
                render_options=RenderOptions(
                    locale="zh_CN", mode="main", known_context=frozenset({"champion", "skin"})
                ),
            )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["matched"])
        match = results[0]["matches"][0]
        self.assertEqual(match["path"], "by-entity/champions/Galio.jsonl")
        self.assertEqual(match["render"]["text"], "W技能被动防御状态结束")
        self.assertEqual(match["translation"]["frame_type"], "ability_state_change")
        self.assertEqual(match["translation"]["arguments"]["ability_component"]["label"], "被动防御")

    def test_lookup_reports_missing_event(self) -> None:
        """没有命中的事件返回 matched=false。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_lookup_fixture(root, [galio_record()])

            results = lookup_normalized_events(root, ["Missing_Event"])

        self.assertEqual(results, [{"raw_event_name": "Missing_Event", "matched": False, "matches": []}])

    def test_lookup_prefers_v2_stream_over_legacy_shards(self) -> None:
        """存在 normalized-events 时不再重复扫描 by-entity。"""

        raw = "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate"
        stream_row = galio_record()
        stream_row.update(
            {
                "record_id": "record-v1-" + "1" * 64,
                "source_scope": "champions",
                "asset_kind": "sfx",
                "pattern_id": "pattern-v1-" + "2" * 64,
                "support_level": "experimental",
                "render_status": "rendered",
                "review_status": "pending",
            }
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_lookup_fixture(root, [galio_record()])
            (root / "normalized-events.jsonl").write_text(
                json.dumps(stream_row, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            results = lookup_normalized_events(root, [raw])

        match = results[0]["matches"][0]
        self.assertEqual(len(results[0]["matches"]), 1)
        self.assertEqual(match["path"], "normalized-events.jsonl")
        self.assertEqual(match["record_id"], stream_row["record_id"])
        self.assertEqual(match["support_level"], "experimental")


class NormalizedLookupCliTests(unittest.TestCase):
    """验证 normalized lookup CLI。"""

    def test_cli_json_output_contains_render_text(self) -> None:
        """CLI JSON 输出可被 Web 端消费。"""

        raw = "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_lookup_fixture(root, [galio_record()])

            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "lookup-normalized-event",
                    "--normalized-root",
                    str(root),
                    "--event",
                    raw,
                    "--mode",
                    "main",
                    "--known-context",
                    "champion,skin",
                    "--json",
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            payload = json.loads(result.stdout)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(payload["query_count"], 1)
        self.assertEqual(payload["results"][0]["matches"][0]["render"]["text"], "W技能被动防御状态结束")

    def test_cli_default_does_not_consume_unapproved_candidate(self) -> None:
        """未传显式 root 时只读 approved pointer，不扫描 candidate/legacy 目录。"""

        raw = "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_lookup_fixture(root / "normalized-candidate", [galio_record()])
            result = subprocess.run(
                [
                    "uv",
                    "run",
                    "lookup-normalized-event",
                    "--runs-root",
                    str(root / "runs"),
                    "--event",
                    raw,
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 2)
        self.assertIn("approved normalized dataset not available", result.stderr)


def galio_record() -> dict[str, object]:
    """构造 Galio translation normalized 记录。"""

    raw = "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate"
    return {
        "schema_version": 1,
        "raw_event_name": raw,
        "game_version": "cumulative",
        "entity": {"kind": "champion", "alias": "Galio"},
        "semantic": {
            "profile": "spell_event",
            "primary_family": "spell",
            "slots": {
                "spell_key": "w",
                "phase": "on_buff_deactivate",
                "contextual_alias": {"component": "passive_defense", "label": "W 被动防御"},
            },
            "confidence": "medium",
            "requires_context": True,
        },
        "display": {
            "zh_cn_short": "W技能被动防御状态结束",
            "zh_cn_detail": "加里奥 地狱火 W技能被动防御状态结束",
            "debug_label": raw,
        },
        "quality": {"unknown_count": 0, "candidate_count": 1, "diagnostics": []},
        "source_paths": ["raw/events/champions/3.txt"],
        "translation": galio_translation(),
    }


def galio_translation() -> dict[str, object]:
    """构造 Galio ability_state_change translation。"""

    return {
        "schema_version": 1,
        "frame_type": "ability_state_change",
        "frame_version": 1,
        "frame_confidence": "medium",
        "arguments": {
            "champion": {
                "role": "champion",
                "alias": "Galio",
                "labels": {"zh_CN": "加里奥", "en_US": "Galio"},
                "confidence": "high",
                "provenance": {"source": "test"},
                "omit_when_context_known": ["champion"],
            },
            "skin": {
                "role": "skin",
                "alias": "GalioSkin13",
                "token": "Skin13",
                "labels": {"zh_CN": "地狱火 加里奥", "en_US": "Infernal Galio"},
                "confidence": "high",
                "provenance": {"source": "test"},
                "omit_when_context_known": ["skin"],
            },
            "ability_slot": {
                "role": "ability_slot",
                "value": "w",
                "labels": {"zh_CN": "W技能", "en_US": "W ability"},
                "confidence": "high",
                "provenance": {"source": "test"},
            },
            "ability_component": {
                "role": "ability_component",
                "key": "passive_defense",
                "labels": {"zh_CN": "被动防御", "en_US": "Passive Defense"},
                "confidence": "medium",
                "provenance": {"source": "test"},
            },
            "phase": {
                "role": "phase",
                "key": "state_deactivate",
                "labels": {"zh_CN": "状态结束", "en_US": "state ended"},
                "confidence": "high",
                "provenance": {"source": "test"},
            },
        },
        "provenance": {"builder": "test"},
    }


def write_lookup_fixture(root: Path, rows: list[dict[str, object]]) -> None:
    """写出最小 normalized lookup fixture。"""

    shard = root / "by-entity" / "champions" / "Galio.jsonl"
    shard.parent.mkdir(parents=True)
    shard.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
