"""GAME WAD 事件 round 输入接口测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.game_wad_events import (
    ExtractionWarning,
    collect_bin_events,
    extract_round_from_event_files,
    extract_round_from_wads,
)


class GameWadEventsTests(unittest.TestCase):
    """验证本地事件文件标准化为 raw round 输入。"""

    def test_extract_round_from_event_files_writes_standard_txt_layout(self) -> None:
        """本地已抽取事件 payload 可转为 champions/maps txt。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "input"
            output_root = root / "round"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("maps").mkdir(parents=True)
            input_root.joinpath("champions", "Annie.json").write_text(
                json.dumps(
                    {
                        "events": [
                            "Play_vo_Annie_Move2DStandard",
                            "Play_vo_Annie_Move2DStandard",
                        ]
                    }
                ),
                encoding="utf-8",
            )
            input_root.joinpath("maps", "SummonersRift.json").write_text(
                json.dumps({"events": ["Play_sfx_Env_Map11_Ambience"]}),
                encoding="utf-8",
            )

            result = extract_round_from_event_files(input_root, output_root)

            self.assertEqual(result.files, 2)
            self.assertEqual(result.events, 2)
            self.assertEqual(result.warnings, ())
            self.assertEqual(
                (output_root / "champions" / "Annie.txt").read_text(encoding="utf-8"),
                "Play_vo_Annie_Move2DStandard\n",
            )

    def test_missing_chroma_source_is_warning_not_failure(self) -> None:
        """缺少 chroma 输入只产生 warning，不中断整轮。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "input"
            output_root = root / "round"
            input_root.joinpath("champions").mkdir(parents=True)
            input_root.joinpath("champions", "Annie.json").write_text(
                json.dumps({"events": ["Play_vo_Annie_Move2DStandard"]}),
                encoding="utf-8",
            )

            result = extract_round_from_event_files(
                input_root,
                output_root,
                expected_chroma_sources=("champions/AnnieChroma.json",),
            )

            self.assertEqual(
                result.warnings,
                (ExtractionWarning(kind="missing_chroma", path="champions/AnnieChroma.json"),),
            )
            self.assertTrue((output_root / "champions" / "Annie.txt").is_file())

    def test_unknown_groups_are_ignored(self) -> None:
        """非 champions/maps 分组不会进入 round 输出。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_root = root / "input"
            output_root = root / "round"
            input_root.joinpath("skins").mkdir(parents=True)
            input_root.joinpath("skins", "Foo.json").write_text(
                json.dumps({"events": ["Play_vo_Foo"]}),
                encoding="utf-8",
            )

            result = extract_round_from_event_files(input_root, output_root)

            self.assertEqual(result.files, 0)
            self.assertFalse(output_root.joinpath("skins").exists())

    def test_collect_bin_events_reads_bank_unit_strings(self) -> None:
        """BIN 解析结果中的 bank unit string 会被收集。"""

        bin_object = FakeBin(
            [
                FakeAudioGroup(
                    [
                        FakeBankUnit([FakeStringHash("Play_vo_Annie_Move2DStandard")]),
                        {"string": "Play_vo_Annie_Attack2DGeneral"},
                        FakeBankUnit([FakeStringHash("")]),
                    ]
                )
            ]
        )

        self.assertEqual(
            collect_bin_events(bin_object),
            ["Play_vo_Annie_Attack2DGeneral", "Play_vo_Annie_Move2DStandard"],
        )

    def test_extract_round_from_wads_uses_injected_wad_and_bin_factories(self) -> None:
        """本地 WAD adapter 可抽取 BIN 并写 round txt。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wad_path = root / "Annie.wad.client"
            wad_path.write_bytes(b"fake-wad")
            output_root = root / "round"

            result = extract_round_from_wads(
                output_root=output_root,
                requests={
                    ("champions", "Annie"): [
                        "data/characters/annie/skins/base/annie.bin",
                        "data/characters/annie/skins/chroma/annie_chroma.bin",
                    ]
                },
                wad_paths=[wad_path],
                wad_factory=lambda path: FakeWad(
                    {
                        "data/characters/annie/skins/base/annie.bin": b"base-bin",
                    }
                ),
                bin_factory=lambda data: FakeBin(
                    [FakeAudioGroup([FakeBankUnit([FakeStringHash(f"Play_vo_Annie_{data.decode('utf-8')}")])])]
                ),
            )

            self.assertEqual(result.files, 1)
            self.assertEqual(result.events, 1)
            self.assertEqual(
                result.warnings,
                (
                    ExtractionWarning(
                        kind="missing_chroma",
                        path="data/characters/annie/skins/chroma/annie_chroma.bin",
                    ),
                ),
            )
            self.assertEqual(
                output_root.joinpath("champions", "Annie.txt").read_text(encoding="utf-8"),
                "Play_vo_Annie_base-bin\n",
            )

    def test_extract_round_from_wads_falls_back_to_later_wads(self) -> None:
        """前一个 WAD 未命中时继续尝试后续 WAD。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_wad = root / "first.wad.client"
            second_wad = root / "second.wad.client"
            first_wad.write_bytes(b"first")
            second_wad.write_bytes(b"second")
            output_root = root / "round"

            def wad_factory(path: Path):
                if path == first_wad:
                    return FakeWad({})

                return FakeWad({"data/characters/annie/skins/base/annie.bin": b"second-bin"})

            result = extract_round_from_wads(
                output_root=output_root,
                requests={("champions", "Annie"): ["data/characters/annie/skins/base/annie.bin"]},
                wad_paths=[first_wad, second_wad],
                wad_factory=wad_factory,
                bin_factory=lambda data: FakeBin(
                    [FakeAudioGroup([FakeBankUnit([FakeStringHash(f"Play_vo_Annie_{data.decode('utf-8')}")])])]
                ),
            )

            self.assertEqual(result.files, 1)
            self.assertEqual(result.warnings, ())
            self.assertEqual(
                output_root.joinpath("champions", "Annie.txt").read_text(encoding="utf-8"),
                "Play_vo_Annie_second-bin\n",
            )


class FakeAudioGroup:
    """测试用音频组。"""

    def __init__(self, bank_units):
        self.bank_units = bank_units


class FakeBankUnit:
    """测试用 bank unit。"""

    def __init__(self, events):
        self.events = events


class FakeStringHash:
    """测试用事件 string hash。"""

    def __init__(self, string: str):
        self.string = string


class FakeBin:
    """测试用 BIN 对象。"""

    def __init__(self, data):
        self.data = data


class FakeWad:
    """测试用 WAD 对象。"""

    def __init__(self, files: dict[str, bytes]):
        self.files = files

    def extract(self, paths, out_dir="", raw=False):
        return [self.files.get(str(path)) for path in paths]


if __name__ == "__main__":
    unittest.main()
