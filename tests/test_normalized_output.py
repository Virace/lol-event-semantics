"""normalized 输出测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import (
    DecompositionConfidence,
    EventDecompositionRecord,
    EventOwnerRef,
    EventProfile,
    EventShell,
    SemanticProfile,
)
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
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

    def test_skin_recall_state_has_readable_display(self) -> None:
        """皮肤回城状态输出面向用户的回城文案。"""

        record = normalize_semantics(decompose_event_name("Play_lux_skin61_recall3d_buffactivate", game_version="16.5"))
        normalized = build_normalized_record(record)

        self.assertEqual(normalized["display"]["zh_cn_short"], "回城状态启动")

    def test_skin_idle_state_has_readable_display(self) -> None:
        """皮肤待机状态输出武器或技能片段文案。"""

        cases = [
            ("Play_jinx_skin60_idle_Powpow_in", "Q武器待机进入"),
            ("Play_jinx_skin60_idle_R_in", "R技能待机进入"),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                record = normalize_semantics(decompose_event_name(raw, game_version="16.5"))
                normalized = build_normalized_record(record)

                self.assertEqual(normalized["display"]["zh_cn_short"], expected)

    def test_skin_action_state_has_readable_display(self) -> None:
        """皮肤动作状态输出动作和阶段文案。"""

        cases = [
            ("play_sfx_SettSkin01_Taunt3D_buffactivate", "嘲讽状态启动"),
            ("play_sfx_SettSkin01_Death3D_cast", "死亡释放"),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                record = normalize_semantics(decompose_event_name(raw, game_version="16.5"))
                normalized = build_normalized_record(record)

                self.assertEqual(normalized["display"]["zh_cn_short"], expected)

    def test_structured_state_events_have_readable_display(self) -> None:
        """结构化状态事件输出可读文案。"""

        cases = [
            ("Play_ziggs_skin05_idle3_01", "待机3片段01"),
            ("Play_sfx_SionSkin05_SionSlowStun_buffactivate", "减速/眩晕状态启动"),
            ("map12_FirstBlood_phase_select", "一血阶段选择"),
        ]

        for raw, expected in cases:
            with self.subTest(raw=raw):
                record = normalize_semantics(decompose_event_name(raw, game_version="16.5"))
                normalized = build_normalized_record(record)

                self.assertEqual(normalized["display"]["zh_cn_short"], expected)

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
            self.assertEqual(payload["display"]["zh_cn_short"], "Q技能释放")

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

    def test_writer_merges_case_variants_into_canonical_entity(self) -> None:
        """实体最终输出按大小写无关身份合并，但保留原始事件名。"""

        records = [
            decomposition_record_with_subject("Play_vo_BelVeth_TestA", "BelVeth"),
            decomposition_record_with_subject("Play_vo_Belveth_TestB", "Belveth"),
        ]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_outputs(records, root, reference_version="16.5")

            summary = json.loads((root / "entity-summary.json").read_text(encoding="utf-8"))
            shard = root / "by-entity" / "champions" / "Belveth.jsonl"
            rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(summary, [{"alias": "Belveth", "kind": "champion", "path": "by-entity/champions/Belveth.jsonl", "record_count": 2}])
            self.assertFalse((root / "by-entity" / "champions" / "Belveth__2.jsonl").exists())
            self.assertEqual([row["entity"]["alias"] for row in rows], ["Belveth", "Belveth"])
            self.assertEqual(
                {row["raw_event_name"] for row in rows},
                {"Play_vo_BelVeth_TestA", "Play_vo_Belveth_TestB"},
            )


class NormalizedShellVerbDisplayTests(unittest.TestCase):
    """验证 Stop/Mute 壳动词进入 legacy 展示文案。"""

    def test_stop_event_legacy_short_display_marks_stop(self) -> None:
        """Stop_ 事件 legacy 短文案带停止语义，不得渲染成播放语义。"""

        from lol_event_semantics.normalized_output import build_short_display

        record = normalize_semantics(
            decompose_event_name("Stop_sfx_AuroraSkin11_Dance3D_leadin", game_version="16.5")
        )
        self.assertEqual(build_short_display(record), "Aurora跳舞停止")

    def test_emote_legacy_display_uses_asset_kind_word(self) -> None:
        """emote legacy 文案按 asset kind 选词，sfx 不得写成语音。"""

        from lol_event_semantics.normalized_output import build_short_display

        record = EventDecompositionRecord(
            schema_version="event-decomposition.v1",
            game_version="cumulative",
            raw_event_name="Play_sfx_Annie_Emote",
            scope="champion",
            shell=EventShell(verb="play", asset_kind="sfx", body="Annie_Emote"),
            owner=EventOwnerRef(kind="champion", alias="Annie", raw_owner_token="Annie"),
            profile=EventProfile(primary_family="emote", semantic_profile=SemanticProfile.SELF_ACTION),
        )
        self.assertEqual(build_short_display(record), "Annie表情音效")


class NormalizedTranslationOutputTests(unittest.TestCase):
    """验证 normalized 输出中的结构化 translation。"""

    def test_galio_w_passive_defense_uses_translation_renderer(self) -> None:
        """Galio W 被动防御事件不能退化为 Galio W 技能。"""

        record = enriched_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate")
        normalized = build_normalized_record(record)

        self.assertIn("translation", normalized)
        translation = normalized["translation"]
        self.assertIsInstance(translation, dict)
        self.assertEqual(translation["frame_type"], "ability_state_change")
        self.assertEqual(translation["arguments"]["ability_slot"]["value"], "w")
        self.assertEqual(translation["arguments"]["ability_component"]["key"], "passive_defense")
        self.assertEqual(translation["arguments"]["phase"]["key"], "state_deactivate")
        self.assertEqual(normalized["display"]["zh_cn_short"], "W技能被动防御状态结束")

    def test_writer_persists_translation_in_entity_shard(self) -> None:
        """JSONL 分片写出 translation 字段。"""

        record = enriched_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_normalized_outputs([record], root, reference_version="16.5")
            shard = root / "by-entity" / "champions" / "Galio.jsonl"
            payload = json.loads(shard.read_text(encoding="utf-8").splitlines()[0])

        self.assertIn("translation", payload)
        self.assertEqual(payload["translation"]["frame_type"], "ability_state_change")
        self.assertEqual(payload["display"]["zh_cn_short"], "W技能被动防御状态结束")
        self.assertEqual(payload["display"]["zh_cn_detail"], "加里奥 地狱火 W技能被动防御状态结束")


class ChineseNameSuffixTests(unittest.TestCase):
    """验证从皮肤全名提取英雄名后缀时尊重间隔号写法。"""

    def test_plain_suffix(self) -> None:
        """普通皮肤名取最后一段。"""

        from lol_event_semantics.normalized_output import chinese_name_suffix

        self.assertEqual(chinese_name_suffix("地狱火 加里奥"), "加里奥")

    def test_interpunct_suffix_merges_full_name(self) -> None:
        """空格间隔号写法（奥瑞利安 · 索尔）合并为整名，不得只取索尔。"""

        from lol_event_semantics.normalized_output import chinese_name_suffix

        self.assertEqual(chinese_name_suffix("墨影之灵 奥瑞利安 · 索尔"), "奥瑞利安·索尔")
        self.assertEqual(chinese_name_suffix("灰烬领主 奥瑞利安·索尔"), "奥瑞利安·索尔")


class NormalizedDisplayEndToEndTests(unittest.TestCase):
    """验证问题事件经完整链路后的 display 文案。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载共享 reference index 与 translation 上下文。"""

        from lol_event_semantics.normalized_output import build_translation_context

        cls.reference = ReferenceIndex.from_reference_collection("reference")
        cls.context = build_translation_context()

    def build_display(self, raw_event_name: str) -> dict[str, str]:
        """按生产链路渲染 display。"""

        record = enrich_with_reference(
            normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
            self.reference,
        )
        normalized = build_normalized_record(record, translation_context=self.context)
        display = normalized["display"]
        assert isinstance(display, dict)
        return display

    def test_aurelionsol_champion_label_keeps_full_name(self) -> None:
        """英雄中文名回填不得把奥瑞利安·索尔截成索尔。"""

        self.assertEqual(self.context.champion_labels["AurelionSol"]["zh_CN"], "奥瑞利安·索尔")

    def test_brand_skin08_detail_has_no_duplicated_champion(self) -> None:
        """champion 与皮肤内嵌变体英雄名不双写。"""

        display = self.build_display("Play_sfx_BrandSkin08_BrandR_OnCast")
        self.assertEqual(display["zh_cn_detail"], "布兰德 神龙尊者 R技能释放")
        self.assertEqual(display["zh_cn_short"], "R技能释放")

    def test_galio_skin_typo_detail_resolves_champion_and_skin(self) -> None:
        """owner alias 大小写 typo（GalioSKin04）不得输出 GalioSKin04 Skin04 冗余。"""

        display = self.build_display("Play_vo_GalioSKin04_Taunt3DGeneral")
        self.assertEqual(display["zh_cn_detail"], "加里奥 地狱之门卫士 嘲讽语音")

    def test_aurelionsol_stop_detail_keeps_interpunct_name(self) -> None:
        """间隔号英雄名整名展示，皮肤名整名去重，Stop 壳语义保留。"""

        display = self.build_display("Stop_sfx_AurelionSolSkin21_AurelionSolQ_charge")
        self.assertEqual(display["zh_cn_detail"], "奥瑞利安·索尔 墨影之灵 Q技能蓄力停止")
        self.assertEqual(display["zh_cn_short"], "Q技能蓄力停止")

    def test_stop_dance_sfx_display_marks_stop_and_sfx(self) -> None:
        """Stop_ sfx 跳舞事件短文案为跳舞音效停止。"""

        display = self.build_display("Stop_sfx_AuroraSkin11_Dance3D_leadin")
        self.assertEqual(display["zh_cn_short"], "跳舞音效停止")


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

    def test_cli_defaults_reference_version_to_cumulative(self) -> None:
        """CLI 不要求用户知道具体来源版本。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="cumulative"))

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
                    "--output-root",
                    str(output_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                check=False,
                capture_output=True,
                text=True,
            )
            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(manifest["reference_version"], "cumulative")

def decomposition_record_with_subject(raw_event_name: str, subject_hint: str) -> EventDecompositionRecord:
    """构造带指定主体的最小拆解记录。"""

    return EventDecompositionRecord(
        schema_version="event-decomposition.v1",
        game_version="cumulative",
        raw_event_name=raw_event_name,
        scope="champion",
        source_paths=["raw/events/champions/0.txt"],
        owner=EventOwnerRef(kind="champion", alias=subject_hint, raw_owner_token=subject_hint),
        profile=EventProfile(
            primary_family="spell",
            semantic_profile=SemanticProfile.SPELL_EVENT,
            requires_context=False,
            reason="test record",
        ),
        semantic_slots={"subject_hint": subject_hint, "trigger_family": "spell", "spell_key": "q"},
        confidence=DecompositionConfidence.HIGH,
    )


def enriched_record(raw_event_name: str) -> EventDecompositionRecord:
    """构造带 reference enrichment 的拆解记录。"""

    return enrich_with_reference(
        normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
        ReferenceIndex.from_reference_collection("reference"),
    )


if __name__ == "__main__":
    unittest.main()
