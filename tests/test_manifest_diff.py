"""manifest_diff 模块测试：路径归类、受影响集映射、diff 判定与本地指纹。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from lol_event_semantics.manifest_diff import (
    Affected,
    affected_from_paths,
    anchor_eligible,
    classify_wad_path,
    diff_paths,
    fingerprint_diff,
    local_affected,
    narrow_champions,
    narrow_locales,
    remote_affected,
    report_paths,
)
from lol_event_semantics.wad_source import scan_wad_fingerprints


def build_report(
    added: tuple[str, ...] = (),
    removed: tuple[str, ...] = (),
    changed: tuple[str, ...] = (),
    unchanged: tuple[str, ...] = (),
    moved: tuple[tuple[str, str], ...] = (),
) -> SimpleNamespace:
    """构造与 riotmanifest ManifestDiffReport 同构的合成报告。"""

    def entry(path: str) -> SimpleNamespace:
        return SimpleNamespace(path=path)

    return SimpleNamespace(
        added=tuple(entry(p) for p in added),
        removed=tuple(entry(p) for p in removed),
        changed=tuple(entry(p) for p in changed),
        unchanged=tuple(entry(p) for p in unchanged),
        moved=tuple(SimpleNamespace(old_path=o, new_path=n) for o, n in moved),
    )


class ClassifyWadPathTest(unittest.TestCase):
    """classify_wad_path 对三类 WAD 的归类与语言变体取舍。"""

    def test_champion_base_and_locale_variant(self) -> None:
        """英雄基础 WAD 与语言变体都归到同一英雄别名。"""

        self.assertEqual(
            classify_wad_path("DATA/FINAL/Champions/Aatrox.wad.client"),
            ("champion", "Aatrox"),
        )
        self.assertEqual(
            classify_wad_path("DATA/FINAL/Champions/Aatrox.zh_CN.wad.client"),
            ("champion", "Aatrox"),
        )
        self.assertEqual(
            classify_wad_path("DATA/FINAL/Champions/Ruby_Wukong.en_US.wad.client"),
            ("champion", "Ruby_Wukong"),
        )

    def test_game_prefix_and_backslashes_normalized(self) -> None:
        """Game/ 前缀与反斜杠路径归类结果一致。"""

        self.assertEqual(
            classify_wad_path("Game\\DATA\\FINAL\\Champions\\Ahri.wad.client"),
            ("champion", "Ahri"),
        )

    def test_map_wad_maps_to_numeric_id(self) -> None:
        """地图 WAD（含语言变体）映射为数字 id，Common 不归属。"""

        self.assertEqual(
            classify_wad_path("DATA/FINAL/Maps/Shipping/Map11.wad.client"),
            ("map", "11"),
        )
        self.assertEqual(
            classify_wad_path("DATA/FINAL/Maps/Shipping/Map12.ja_JP.wad.client"),
            ("map", "12"),
        )
        self.assertEqual(
            classify_wad_path("DATA/FINAL/Maps/Shipping/Common.wad.client"),
            (None, None),
        )

    def test_localized_global_maps_to_locale(self) -> None:
        """Localized 下 Global.{语言} 映射到字符串表语言，无语言段不归属。"""

        self.assertEqual(
            classify_wad_path("DATA/FINAL/Localized/Global.zh_CN.wad.client"),
            ("stringtable", "zh_CN"),
        )
        self.assertEqual(
            classify_wad_path("DATA/FINAL/Localized/Global.wad.client"),
            (None, None),
        )

    def test_unrelated_paths_ignored(self) -> None:
        """管线不消费的路径（根 WAD、非 wad.client、多段文件名）不归属。"""

        for path in (
            "DATA/FINAL/TFTSet10.wad.client",
            "DATA/FINAL/Champions/Aatrox.wad.subchunktoc",
            "DATA/FINAL/Champions/Aatrox.zh_CN.extra.wad.client",
            "League of Legends.exe",
            "",
        ):
            self.assertEqual(classify_wad_path(path), (None, None))


class AffectedFromPathsTest(unittest.TestCase):
    """affected_from_paths 的聚合、去重与排序。"""

    def test_aggregates_three_kinds_sorted(self) -> None:
        """三类路径聚合到各自字段，语言变体与基础 WAD 去重为一。"""

        affected = affected_from_paths(
            [
                "DATA/FINAL/Champions/Zed.wad.client",
                "DATA/FINAL/Champions/Ahri.zh_CN.wad.client",
                "DATA/FINAL/Champions/Ahri.wad.client",
                "DATA/FINAL/Maps/Shipping/Map21.wad.client",
                "DATA/FINAL/Localized/Global.en_US.wad.client",
                "DATA/FINAL/UI.wad.client",
            ]
        )
        self.assertEqual(affected.champions, ("Ahri", "Zed"))
        self.assertEqual(affected.map_ids, ("21",))
        self.assertEqual(affected.stringtable_locales, ("en_US",))

    def test_empty_paths_yield_empty_affected(self) -> None:
        """无变化路径产出全空受影响集。"""

        self.assertEqual(affected_from_paths([]), Affected())


class ReportPathsTest(unittest.TestCase):
    """report_paths 对 diff 报告的路径提取口径。"""

    def test_collects_added_removed_changed_moved(self) -> None:
        """added/removed/changed 与 moved 双端路径都计入，unchanged 排除。"""

        report = build_report(
            added=("a.wad.client",),
            removed=("b.wad.client",),
            changed=("c.wad.client", "a.wad.client"),
            unchanged=("keep.wad.client",),
            moved=(("old/d.wad.client", "new/d.wad.client"),),
        )
        self.assertEqual(
            report_paths(report),
            (
                "a.wad.client",
                "b.wad.client",
                "c.wad.client",
                "new/d.wad.client",
                "old/d.wad.client",
            ),
        )

    def test_tolerates_report_without_moved(self) -> None:
        """缺 moved 字段的报告对象也能消费。"""

        report = SimpleNamespace(
            added=(SimpleNamespace(path="x.wad.client"),),
            removed=(),
            changed=(),
        )
        self.assertEqual(report_paths(report), ("x.wad.client",))


class DiffPathsTest(unittest.TestCase):
    """diff_paths 的注入 differ 委托。"""

    def test_uses_injected_differ(self) -> None:
        """注入 differ 收到新旧地址且结果经 report_paths 提取。"""

        calls: list[tuple[str, str]] = []

        def differ(old: str, new: str) -> SimpleNamespace:
            calls.append((old, new))
            return build_report(changed=("DATA/FINAL/Champions/Jinx.wad.client",))

        result = diff_paths("https://old.test/a.manifest", "https://new.test/b.manifest", differ=differ)
        self.assertEqual(calls, [("https://old.test/a.manifest", "https://new.test/b.manifest")])
        self.assertEqual(result, ("DATA/FINAL/Champions/Jinx.wad.client",))


class FingerprintDiffTest(unittest.TestCase):
    """fingerprint_diff 的纯字典比较口径。"""

    def test_added_removed_changed_keys(self) -> None:
        """新增、删除与指纹值变化的键都计为变化，相同键不计。"""

        old = {
            "DATA/FINAL/Champions/Ahri.wad.client": {"size": 10, "mtime_ns": 1},
            "DATA/FINAL/Champions/Zed.wad.client": {"size": 20, "mtime_ns": 2},
            "DATA/FINAL/Champions/Gone.wad.client": {"size": 30, "mtime_ns": 3},
        }
        new = {
            "DATA/FINAL/Champions/Ahri.wad.client": {"size": 10, "mtime_ns": 1},
            "DATA/FINAL/Champions/Zed.wad.client": {"size": 20, "mtime_ns": 9},
            "DATA/FINAL/Champions/New.wad.client": {"size": 40, "mtime_ns": 4},
        }
        self.assertEqual(
            fingerprint_diff(old, new),
            (
                "DATA/FINAL/Champions/Gone.wad.client",
                "DATA/FINAL/Champions/New.wad.client",
                "DATA/FINAL/Champions/Zed.wad.client",
            ),
        )

    def test_identical_snapshots_have_no_diff(self) -> None:
        """指纹完全一致时无变化。"""

        snapshot = {"DATA/FINAL/Localized/Global.zh_CN.wad.client": {"size": 1, "mtime_ns": 2}}
        self.assertEqual(fingerprint_diff(snapshot, dict(snapshot)), ())


class RemoteAffectedTest(unittest.TestCase):
    """remote_affected 的锚判定分支。"""

    def test_missing_or_legacy_anchor_returns_none(self) -> None:
        """无锚或旧版游标结构（缺 url）回退全量。"""

        self.assertIsNone(remote_affected(None, "https://new.test/m"))
        self.assertIsNone(remote_affected({}, "https://new.test/m"))
        self.assertIsNone(remote_affected({"version": "16.13.1"}, "https://new.test/m"))

    def test_same_url_short_circuits_without_diff(self) -> None:
        """锚与当前 manifest 相同则空受影响集，不发起 diff。"""

        def differ(old: str, new: str) -> SimpleNamespace:
            raise AssertionError("同 URL 不应发起 diff")

        affected = remote_affected(
            {"url": "https://cdn.test/same.manifest"},
            "https://cdn.test/same.manifest",
            differ=differ,
        )
        self.assertEqual(affected, Affected())

    def test_different_url_maps_diff_to_affected(self) -> None:
        """锚不同则经 diff 得出受影响集。"""

        def differ(old: str, new: str) -> SimpleNamespace:
            return build_report(
                changed=(
                    "DATA/FINAL/Champions/Ahri.wad.client",
                    "DATA/FINAL/Localized/Global.zh_CN.wad.client",
                )
            )

        affected = remote_affected(
            {"url": "https://cdn.test/old.manifest"},
            "https://cdn.test/new.manifest",
            differ=differ,
        )
        self.assertEqual(affected.champions, ("Ahri",))
        self.assertEqual(affected.stringtable_locales, ("zh_CN",))


class LocalAffectedTest(unittest.TestCase):
    """local_affected 的锚判定分支。"""

    def test_missing_anchor_returns_none(self) -> None:
        """无指纹锚回退全量。"""

        current = {"DATA/FINAL/Champions/Ahri.wad.client": {"size": 1, "mtime_ns": 2}}
        self.assertIsNone(local_affected(None, current))
        self.assertIsNone(local_affected({}, current))

    def test_anchor_diff_maps_to_affected(self) -> None:
        """指纹变化映射为受影响英雄集。"""

        anchor = {
            "DATA/FINAL/Champions/Ahri.wad.client": {"size": 1, "mtime_ns": 2},
            "DATA/FINAL/Champions/Zed.wad.client": {"size": 3, "mtime_ns": 4},
        }
        current = {
            "DATA/FINAL/Champions/Ahri.wad.client": {"size": 1, "mtime_ns": 2},
            "DATA/FINAL/Champions/Zed.wad.client": {"size": 3, "mtime_ns": 9},
        }
        affected = local_affected(anchor, current)
        self.assertEqual(affected, Affected(champions=("Zed",)))


class AnchorEligibleTest(unittest.TestCase):
    """anchor_eligible 的锚推进资格规则。"""

    def test_full_coverage_run_is_eligible(self) -> None:
        """stringtable+raw-bins 均在源集且英雄集为 all 才允许推进锚。"""

        self.assertTrue(anchor_eligible(("stringtable", "lobby", "raw-bins", "rst-hashes"), "all"))
        self.assertTrue(anchor_eligible(("raw-bins", "stringtable"), "all"))

    def test_subset_runs_are_not_eligible(self) -> None:
        """源子集或英雄子集运行保留旧锚。"""

        self.assertFalse(anchor_eligible(("raw-bins",), "all"))
        self.assertFalse(anchor_eligible(("stringtable", "lobby"), "all"))
        self.assertFalse(anchor_eligible(("stringtable", "raw-bins"), "Ahri,Zed"))


class NarrowTest(unittest.TestCase):
    """narrow_champions / narrow_locales 的窄化规则。"""

    def test_narrow_champions_keeps_order_and_none_passthrough(self) -> None:
        """全量（None）原样返回；增量按受影响集过滤且保持请求顺序。"""

        requested = ["Zed", "Ahri", "Jinx"]
        self.assertEqual(narrow_champions(requested, None), requested)
        affected = Affected(champions=("Ahri", "Zed"))
        self.assertEqual(narrow_champions(requested, affected), ["Zed", "Ahri"])

    def test_narrow_locales_includes_missing_on_disk(self) -> None:
        """判变语言与本地缺档语言都保留，未变且在档的跳过。"""

        requested = ["zh_CN", "en_US", "ja_JP"]
        self.assertEqual(narrow_locales(requested, None, present=()), requested)
        affected = Affected(stringtable_locales=("zh_CN",))
        self.assertEqual(
            narrow_locales(requested, affected, present={"zh_CN", "en_US"}),
            ["zh_CN", "ja_JP"],
        )


class ScanWadFingerprintsTest(unittest.TestCase):
    """scan_wad_fingerprints 对本地安装目录的指纹扫描。"""

    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.game_dir = Path(root.name)
        final = self.game_dir / "Game" / "DATA" / "FINAL"
        (final / "Champions").mkdir(parents=True)
        (final / "Localized").mkdir(parents=True)
        (final / "Maps" / "Shipping").mkdir(parents=True)
        (final / "Champions" / "Ahri.wad.client").write_bytes(b"ahri")
        (final / "Champions" / "Ahri.zh_CN.wad.client").write_bytes(b"ahri-zh")
        (final / "Champions" / "Ahri.wad.subchunktoc").write_bytes(b"toc")
        (final / "Localized" / "Global.zh_CN.wad.client").write_bytes(b"table")
        (final / "Maps" / "Shipping" / "Map11.wad.client").write_bytes(b"map")
        (final / "TFTSet10.wad.client").write_bytes(b"tft")

    def test_scan_covers_three_dirs_with_manifest_style_keys(self) -> None:
        """键为无 Game/ 前缀的 POSIX 相对路径，只收三目录的 wad.client。"""

        snapshot = scan_wad_fingerprints(self.game_dir)
        self.assertEqual(
            sorted(snapshot),
            [
                "DATA/FINAL/Champions/Ahri.wad.client",
                "DATA/FINAL/Champions/Ahri.zh_CN.wad.client",
                "DATA/FINAL/Localized/Global.zh_CN.wad.client",
                "DATA/FINAL/Maps/Shipping/Map11.wad.client",
            ],
        )
        entry = snapshot["DATA/FINAL/Champions/Ahri.wad.client"]
        self.assertEqual(entry["size"], 4)
        self.assertIsInstance(entry["mtime_ns"], int)

    def test_snapshot_diffs_against_itself_and_after_touch(self) -> None:
        """快照自比无变化；改写文件后 diff 命中该文件。"""

        before = scan_wad_fingerprints(self.game_dir)
        self.assertEqual(fingerprint_diff(before, scan_wad_fingerprints(self.game_dir)), ())
        target = self.game_dir / "Game" / "DATA" / "FINAL" / "Champions" / "Ahri.wad.client"
        target.write_bytes(b"ahri-patched")
        after = scan_wad_fingerprints(self.game_dir)
        self.assertEqual(
            fingerprint_diff(before, after),
            ("DATA/FINAL/Champions/Ahri.wad.client",),
        )

    def test_missing_dirs_are_skipped(self) -> None:
        """目录缺失（精简安装）不报错，返回可得部分。"""

        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(scan_wad_fingerprints(Path(tmp)), {})


if __name__ == "__main__":
    unittest.main()
