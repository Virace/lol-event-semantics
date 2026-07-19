"""累计 raw/events 差集更新测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.raw_update import RawUpdateMetadata, check_raw_update, update_raw_events


class RawUpdateTests(unittest.TestCase):
    """验证累计 raw 事件集合更新。"""

    def test_update_adds_only_missing_events_and_is_stable(self) -> None:
        """只追加差集，重复运行无文件变化。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            raw_root.joinpath("champions").mkdir(parents=True)
            raw_root.joinpath("champions", "Annie.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Annie.txt").write_text(
                "Play_vo_Annie_Move2DStandard\nPlay_vo_Annie_Attack2DGeneral\n",
                encoding="utf-8",
            )

            first = update_raw_events(raw_root, input_root)
            before = raw_root.joinpath("champions", "Annie.txt").read_text(encoding="utf-8")
            second = update_raw_events(raw_root, input_root)
            after = raw_root.joinpath("champions", "Annie.txt").read_text(encoding="utf-8")

        self.assertTrue(first.changed)
        self.assertEqual(first.added_events, 1)
        self.assertFalse(second.changed)
        self.assertEqual(before, after)
        self.assertEqual(
            before.splitlines(),
            ["Play_vo_Annie_Attack2DGeneral", "Play_vo_Annie_Move2DStandard"],
        )

    def test_check_reports_noop_without_writing(self) -> None:
        """check 模式只报告差集，不写目标文件。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            input_root.joinpath("maps").mkdir(parents=True)
            input_root.joinpath("maps", "SummonersRift.txt").write_text(
                "Play_sfx_Env_Map11_Ambience\n",
                encoding="utf-8",
            )

            result = check_raw_update(raw_root, input_root)

        self.assertTrue(result.changed)
        self.assertEqual(result.added_events, 1)
        self.assertFalse(raw_root.exists())

    def test_update_state_records_metadata_and_totals(self) -> None:
        """更新状态文件记录来源 metadata 和累计 totals。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            state_file = root / "raw" / "update-state.json"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Aatrox.txt").write_text("Play_vo_Aatrox_Move2DStandard\n", encoding="utf-8")
            metadata = RawUpdateMetadata(
                game_version="16.12",
                manifest_major_version="16.12",
                game_manifest_id="manifest-1",
                game_manifest_url="https://example.test/manifest",
                data_dragon_version="16.12.1",
                game_data_snapshot_metadata_sha256="sha256:game",
                data_dragon_champion_summary_sha256="sha256:dd",
            )

            update_raw_events(raw_root, input_root, state_file=state_file, metadata=metadata)
            payload = json.loads(state_file.read_text(encoding="utf-8"))

        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["last_checked"]["game_version"], "16.12")
        self.assertEqual(payload["totals"]["champion_files"], 1)
        self.assertEqual(payload["totals"]["raw_event_names"], 1)

    def test_check_reports_metadata_change_without_raw_diff(self) -> None:
        """来源 metadata 变化时 should_update 为 true，但 raw changed 仍只表示差集。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            state_file = root / "raw" / "update-state.json"
            raw_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions").mkdir(parents=True)
            raw_root.joinpath("champions", "Aatrox.txt").write_text("Play_vo_Aatrox_Move2DStandard\n", encoding="utf-8")
            input_root.joinpath("champions", "Aatrox.txt").write_text("Play_vo_Aatrox_Move2DStandard\n", encoding="utf-8")
            update_raw_events(
                raw_root,
                input_root,
                state_file=state_file,
                metadata=RawUpdateMetadata(game_manifest_id="old"),
            )

            result = check_raw_update(
                raw_root,
                input_root,
                state_file=state_file,
                metadata=RawUpdateMetadata(game_manifest_id="new"),
            )

        self.assertFalse(result.changed)
        self.assertTrue(result.should_update)
        self.assertTrue(result.metadata_changed)

    def test_input_normalizes_duplicate_blank_and_whitespace_lines(self) -> None:
        """输入中的重复行、空行和首尾空白会被归一化。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Lux.txt").write_text(
                "  Play_vo_Lux_Move2DStandard  \n\nPlay_vo_Lux_Move2DStandard\n",
                encoding="utf-8",
            )

            result = update_raw_events(raw_root, input_root)
            output = raw_root.joinpath("champions", "Lux.txt").read_text(encoding="utf-8")

        self.assertEqual(result.added_events, 1)
        self.assertEqual(output, "Play_vo_Lux_Move2DStandard\n")

    def test_unknown_input_groups_are_ignored(self) -> None:
        """非 champions/maps 分组不进入累计 raw。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_root = root / "raw" / "events"
            input_root = root / "round"
            input_root.joinpath("skins").mkdir(parents=True)
            input_root.joinpath("skins", "Foo.txt").write_text("Play_vo_Foo\n", encoding="utf-8")

            result = update_raw_events(raw_root, input_root)

        self.assertFalse(result.changed)
        self.assertFalse((raw_root / "skins").exists())

    def test_legacy_versioned_input_can_be_migrated_into_cumulative_layout(self) -> None:
        """旧 raw/<version>/events 输入可迁移到累计 raw/events 输出。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_events = root / "raw" / "16.5" / "events"
            old_events.joinpath("champions").mkdir(parents=True)
            old_events.joinpath("champions", "1.txt").write_text("Play_vo_Annie_Move2DStandard\n", encoding="utf-8")
            new_raw = root / "new-raw" / "events"

            result = update_raw_events(new_raw, old_events)

            self.assertTrue(result.changed)
            self.assertTrue((new_raw / "champions" / "1.txt").is_file())
            self.assertFalse((new_raw / "16.5").exists())


if __name__ == "__main__":
    unittest.main()
