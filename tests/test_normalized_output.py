"""normalized 输出测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from reference_cache import reference_index
from reference_cache import translation_context as shared_translation_context
from reference_cache import write_outputs

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
from lol_event_semantics.decomposition_reference import enrich_with_reference
from lol_event_semantics.normalized_output import (
    NAME_FORM_FIELDS,
    ChampionEntityResolver,
    build_normalized_record,
    build_short_display,
    champion_by_alias_affix,
    item_entity_from_alias,
    load_champion_entity_resolver,
    load_entity_attribution_overrides,
    playback_verb_suffix,
    resolve_champion_entity,
)
from lol_event_semantics.normalized_validation import validate_normalized_root


class ItemEntityResolutionTests(unittest.TestCase):
    """验证 map/系统域数字别名到装备实体的解析（注入 id 集合的纯逻辑）。"""

    KNOWN_ITEM_IDS = frozenset({1035, 2065, 3040})

    def test_pure_numeric_alias_resolves_to_item(self) -> None:
        """纯数字别名命中装备表时归入 item 实体，无需 raw_alias。"""

        entity = item_entity_from_alias("1035", self.KNOWN_ITEM_IDS)
        self.assertEqual(entity, {"kind": "item", "alias": "1035"})

    def test_state_suffix_folds_into_same_item_with_raw_alias(self) -> None:
        """数字+状态尾缀折叠进同一装备实体，原始别名无损保留在 raw_alias。"""

        for alias, item_alias in [
            ("3040Shield", "3040"),
            ("2065Active", "2065"),
            ("2065MoveSpeed", "2065"),
        ]:
            with self.subTest(alias=alias):
                entity = item_entity_from_alias(alias, self.KNOWN_ITEM_IDS)
                self.assertEqual(
                    entity,
                    {"kind": "item", "alias": item_alias, "raw_alias": alias},
                )

    def test_unclaimed_or_non_numeric_aliases_stay_out(self) -> None:
        """表外数字与非数字别名不归装备域（宁缺毋错，维持既有实体分类）。"""

        for alias in ("9999", "578", "Announcer", "Env", "missile1"):
            with self.subTest(alias=alias):
                self.assertIsNone(item_entity_from_alias(alias, self.KNOWN_ITEM_IDS))


class NormalizedOutputTests(unittest.TestCase):
    """验证对外 normalized 数据。"""

    def assert_readable_short(self, raw_event_name: str) -> None:
        """断言事件的 zh_cn_short 非空可读且不复读原始事件名技术 token。

        「可读」按性质定义：短文案存在、不含下划线拼接痕迹、不把原始
        事件名里的技术 token（长度≥4 的段，如 skin61/buffactivate）原样
        泄漏给用户；具体措辞由词表决定，不在此锁定。
        """

        record = normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5"))
        normalized = build_normalized_record(record)
        short = normalized["display"]["zh_cn_short"]

        self.assertTrue(short)
        self.assertNotIn("_", short)
        lowered = short.lower()
        for token in raw_event_name.lower().split("_"):
            if len(token) >= 4:
                self.assertNotIn(token, lowered)

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
        """皮肤回城状态走结构化渲染，短文案可读且不泄漏技术 token。"""

        self.assert_readable_short("Play_lux_skin61_recall3d_buffactivate")

    def test_skin_idle_state_has_readable_display(self) -> None:
        """皮肤待机状态（武器/技能片段）走结构化渲染，短文案可读。"""

        cases = [
            "Play_jinx_skin60_idle_Powpow_in",
            "Play_jinx_skin60_idle_R_in",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                self.assert_readable_short(raw)

    def test_skin_action_state_has_readable_display(self) -> None:
        """皮肤动作状态走结构化渲染，短文案可读且不泄漏阶段 token。"""

        cases = [
            "play_sfx_SettSkin01_Taunt3D_buffactivate",
            "play_sfx_SettSkin01_Death3D_cast",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                self.assert_readable_short(raw)

    def test_structured_state_events_have_readable_display(self) -> None:
        """结构化状态事件（片段/状态/阶段控制）短文案可读。"""

        cases = [
            "Play_ziggs_skin05_idle3_01",
            "Play_sfx_SionSkin05_SionSlowStun_buffactivate",
            "map12_FirstBlood_phase_select",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                self.assert_readable_short(raw)

    def test_write_outputs_v2_stream_catalog_and_compatibility_shards(self) -> None:
        """输出 V2 单流/catalog/schema，同时保留按实体兼容分片。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs([record], root, reference_version="16.5")

            self.assertTrue((root / "manifest.json").is_file())
            self.assertTrue((root / "normalized-events.jsonl").is_file())
            self.assertTrue((root / "pattern-catalog.json").is_file())
            self.assertTrue((root / "entity-summary.json").is_file())
            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())
            self.assertTrue((root / "schema" / "normalized-event.schema.json").is_file())
            self.assertTrue((root / "examples" / "normalized-event.v2.json").is_file())
            self.assertFalse((root / "raw-event-index.json").exists())

            first_line = (root / "by-entity" / "champions" / "Annie.jsonl").read_text(encoding="utf-8").splitlines()[0]
            payload = json.loads(first_line)
            manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["raw_event_name"], "Play_vo_Annie_AnnieQ_OnCast")
            self.assertEqual(payload["schema_version"], 2)
            self.assertTrue(payload["record_id"].startswith("record-v1-"))
            self.assertTrue(manifest["contains_full_raw_event_index"])
            self.assertEqual(manifest["normalized_record_schema_version"], 2)
            self.assertNotIn("generated_at", manifest)
            self.assertTrue(manifest["dataset_id"].startswith("local-v1-"))
            self.assertEqual(len(manifest["content_digest"]), 64)
            self.assertEqual(validate_normalized_root(root).error_count, 0)

            with tempfile.TemporaryDirectory() as second_temp:
                second_root = Path(second_temp)
                write_outputs([record], second_root, reference_version="16.5")
                self.assertEqual(
                    (root / "manifest.json").read_bytes(),
                    (second_root / "manifest.json").read_bytes(),
                )
                self.assertEqual(
                    (root / "normalized-events.jsonl").read_bytes(),
                    (second_root / "normalized-events.jsonl").read_bytes(),
                )

            catalog_path = root / "pattern-catalog.json"
            catalog_path.write_text(catalog_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            (root / "debug.tmp").write_text("not managed\n", encoding="utf-8")
            tampered = validate_normalized_root(root)
            tamper_codes = {issue.code for issue in tampered.issues}
            self.assertIn("manifest_files_mismatch", tamper_codes)
            self.assertIn("unmanaged_payload_file", tamper_codes)

    def test_entity_shard_prefers_resolved_subject_hint(self) -> None:
        """内部 owner 形如 AnnieQ 时，分片仍归到真实英雄 Annie。"""

        record = normalize_semantics(decompose_event_name("Play_vo_AnnieQ_cast3D", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs([record], root, reference_version="16.5")

            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())
            self.assertFalse((root / "by-entity" / "champions" / "AnnieQ.jsonl").exists())

            payload = json.loads(
                (root / "by-entity" / "champions" / "Annie.jsonl").read_text(encoding="utf-8").splitlines()[0]
            )
            self.assertEqual(payload["entity"]["alias"], "Annie")
            self.assertTrue(payload["display"]["zh_cn_short"])

    def test_writer_removes_stale_generated_shards(self) -> None:
        """重复生成时清理旧 JSONL 分片，避免实体 summary 与文件不一致。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="16.5"))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stale = root / "by-entity" / "champions" / "OldChampion.jsonl"
            stale.parent.mkdir(parents=True)
            stale.write_text("{}\n", encoding="utf-8")

            write_outputs([record], root, reference_version="16.5")

            self.assertFalse(stale.exists())
            self.assertTrue((root / "by-entity" / "champions" / "Annie.jsonl").is_file())

    def test_writer_merges_case_variants_into_canonical_entity(self) -> None:
        """实体最终输出按大小写无关身份合并，但保留原始事件名。"""

        records = [
            decomposition_record_with_subject("Play_vo_BelVeth_TestA", "BelVeth"),
            decomposition_record_with_subject("Play_vo_Belveth_TestB", "Belveth"),
        ]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs(records, root, reference_version="16.5")

            summary = json.loads((root / "entity-summary.json").read_text(encoding="utf-8"))
            shard = root / "by-entity" / "champions" / "Belveth.jsonl"
            rows = [json.loads(line) for line in shard.read_text(encoding="utf-8").splitlines()]

            self.assertEqual(
                summary,
                [
                    {
                        "alias": "Belveth",
                        "kind": "champion",
                        "path": "by-entity/champions/Belveth.jsonl",
                        "record_count": 2,
                    }
                ],
            )
            self.assertFalse((root / "by-entity" / "champions" / "Belveth__2.jsonl").exists())
            self.assertEqual([row["entity"]["alias"] for row in rows], ["Belveth", "Belveth"])
            self.assertEqual(
                {row["raw_event_name"] for row in rows},
                {"Play_vo_BelVeth_TestA", "Play_vo_Belveth_TestB"},
            )


class MapSystemEntityDisplayTests(unittest.TestCase):
    """验证 map/系统域头部实体词条与通用后缀渲染（T3.4）。"""

    def test_announcer_owner_renders_chinese_label(self) -> None:
        """Announcer 属于 T3.4 头部词条表，短文案渲染本地化词条，英文 alias 不泄漏。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_vo_Announcer_Brawl_Ace",
                game_version="16.5",
                source_path="raw/events/maps/12.txt",
            )
        )

        short = build_short_display(record)
        self.assertTrue(short)
        self.assertNotIn("announcer", short.lower())

    def test_untabled_owner_keeps_english_alias(self) -> None:
        """表外别名（如 Ruby）不硬造中文，短文案继续保留英文 alias 现状。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Ruby_Boss_Test_OnHit",
                game_version="16.5",
                source_path="raw/events/maps/12.txt",
            )
        )

        self.assertIn("Ruby", build_short_display(record))

    def test_reviewed_community_mode_codenames_render_as_modes(self) -> None:
        """Kiwi/SLIME 经外部解释与本地共现复核后，不再作为用户可见实体名。"""

        cases = [
            (
                "Play_sfx_Kiwi_Augment_CrackOpenThatEgg_buffactivate",
                "海克斯大乱斗",
                "Kiwi",
            ),
            (
                "Play_sfx_SLIME_Blue_aggro1",
                "极限闪击",
                "SLIME",
            ),
        ]
        for raw, expected, internal_name in cases:
            with self.subTest(raw=raw):
                record = normalize_semantics(
                    decompose_event_name(raw, game_version="16.5", source_path="raw/events/maps/12.txt")
                )

                short = build_short_display(record)
                self.assertIn(expected, short)
                self.assertNotIn(internal_name.lower(), short.lower())

    def test_loop_suffix_consumed_without_raw_token_leak(self) -> None:
        """已解析的 loop 通用后缀参与渲染，原始 token 不泄漏进短文案。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Env_Map11_SRX_Chemtech_Dragon_Zone_Subtle_loop",
                game_version="16.5",
            )
        )

        short = build_short_display(record)
        self.assertTrue(short)
        self.assertNotIn("loop", short.lower())

    def test_map_system_end_phase_is_not_dropped(self) -> None:
        """map/system 兜底仍必须展示已解析的 end 阶段。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Ruby_RyzeRChannel_end",
                game_version="16.5",
            )
        )

        self.assertEqual(build_short_display(record), "Ruby结束")

    def test_unresolved_spell_still_renders_known_phase(self) -> None:
        """技能归属 fail-closed 时不能同时丢弃已解析的状态阶段。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Zac_zacrebirthstart_OnBuffActivate",
                game_version="16.5",
            )
        )

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(build_short_display(record), "Zac状态启动")


class ChampionEntityAttributionTests(unittest.TestCase):
    """验证 champion 实体归一：碎片 alias 归属真实英雄且信息无损。"""

    @staticmethod
    def build_record(raw_event_name: str, source_path: str) -> EventDecompositionRecord:
        """按生产链路构造带来源路径的拆解记录。"""

        return normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5", source_path=source_path))

    def write_single(self, raw_event_name: str, source_path: str, root: Path) -> None:
        """写出单条记录的 normalized 输出。"""

        write_outputs([self.build_record(raw_event_name, source_path)], root, reference_version="16.5")

    @staticmethod
    def first_payload(path: Path) -> dict[str, object]:
        """读取分片首行记录。"""

        return json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    def test_numeric_switch_alias_downgrades_to_unknown(self) -> None:
        """Switch_CHAMP_578 的数字开关值降级为 unknown，alias 原样保留。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Switch_CHAMP_578_Off", "raw/events/champions/145.txt", root)

            self.assertFalse((root / "by-entity" / "champions" / "578.jsonl").exists())
            shard = root / "by-entity" / "unknown" / "578.jsonl"
            self.assertTrue(shard.is_file())
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity, {"kind": "unknown", "alias": "578"})

    def test_lowercase_skin_alias_resolves_champion_with_raw_alias(self) -> None:
        """jinx_skin60 归入 Jinx，raw_alias 保留 lexer 原始 alias。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_sfx_jinx_skin60_exalted_spawnin", "raw/events/champions/222.txt", root)

            shard = root / "by-entity" / "champions" / "Jinx.jsonl"
            self.assertTrue(shard.is_file())
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity["alias"], "Jinx")
            self.assertEqual(entity["raw_alias"], "jinx")

    def test_skin_typo_alias_resolves_champion(self) -> None:
        """GalioSKin04 typo 归入 Galio。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_vo_GalioSKin04_Taunt3DGeneral", "raw/events/champions/3.txt", root)

            self.assertTrue((root / "by-entity" / "champions" / "Galio.jsonl").is_file())
            self.assertFalse((root / "by-entity" / "champions" / "GalioSKin04.jsonl").exists())

    def test_pet_alias_attaches_to_champion_by_prefix(self) -> None:
        """AnnieTibbers 宠物实体挂靠 Annie，raw_alias 无损保留。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single(
                "Play_sfx_AnnieTibbersSkin10_AnnieTibbersBasicAttack2_OnCast",
                "raw/events/champions/1.txt",
                root,
            )

            shard = root / "by-entity" / "champions" / "Annie.jsonl"
            self.assertTrue(shard.is_file())
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity["alias"], "Annie")
            self.assertEqual(entity["raw_alias"], "AnnieTibbers")

    def test_named_skin_alias_attaches_to_champion_by_suffix(self) -> None:
        """ZombieBrand 皮肤别名按后缀挂靠 Brand。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_sfx_ZombieBrand_Dance3D_buffactivate", "raw/events/champions/63.txt", root)

            shard = root / "by-entity" / "champions" / "Brand.jsonl"
            self.assertTrue(shard.is_file())
            self.assertEqual(self.first_payload(shard)["entity"]["raw_alias"], "ZombieBrand")

    def test_switch_vo_spell_key_owner_falls_back_to_bank_champion(self) -> None:
        """Switch_VO_E_* 按 bank 文件 id 归属 Maokai，raw_alias 保留 E。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Switch_VO_E_ivernsbrush", "raw/events/champions/57.txt", root)

            self.assertFalse((root / "by-entity" / "champions" / "E.jsonl").exists())
            shard = root / "by-entity" / "champions" / "Maokai.jsonl"
            self.assertTrue(shard.is_file())
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity["alias"], "Maokai")
            self.assertEqual(entity["raw_alias"], "E")

    def test_setstate_skin_owner_resolves_champion_directly(self) -> None:
        """SetState_EkkoSkin19_E 壳修复后直接归属 Ekko。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("SetState_EkkoSkin19_E", "raw/events/champions/245.txt", root)

            self.assertTrue((root / "by-entity" / "champions" / "Ekko.jsonl").is_file())
            self.assertFalse((root / "by-entity" / "champions" / "E.jsonl").exists())

    def test_bare_skin_owner_falls_back_to_bank_champion(self) -> None:
        """Play_mus_Skin06_* 按 bank 文件 id 归属 Sona，raw_alias 保留 Skin06。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_mus_Skin06_Aggressive_select", "raw/events/champions/37.txt", root)

            shard = root / "by-entity" / "champions" / "Sona.jsonl"
            self.assertTrue(shard.is_file())
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity["alias"], "Sona")
            self.assertEqual(entity["raw_alias"], "Skin06")

    def test_ruby_mode_context_attaches_to_real_actor(self) -> None:
        """Ruby 是末日人机上下文；实体分片必须归到后续真实英雄。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            record = enrich_with_reference(
                self.build_record(
                    "Play_sfx_Ruby_AmumuBasicAttack_OnCast",
                    "raw/events/champions/66604.txt",
                ),
                reference_index(),
            )
            write_outputs([record], root, reference_version="16.5")

            shard = root / "by-entity" / "champions" / "Amumu.jsonl"
            self.assertTrue(shard.is_file())
            self.assertFalse((root / "by-entity" / "champions" / "Ruby.jsonl").exists())
            payload = self.first_payload(shard)
            self.assertEqual(payload["entity"], {"kind": "champion", "alias": "Amumu"})
            self.assertEqual(payload["semantic"]["slots"]["context_namespace"]["key"], "ruby")

    def test_regular_champion_alias_has_no_raw_alias(self) -> None:
        """未发生归一的真实英雄记录不写 raw_alias。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_vo_Annie_AnnieQ_OnCast", "raw/events/champions/1.txt", root)

            shard = root / "by-entity" / "champions" / "Annie.jsonl"
            entity = self.first_payload(shard)["entity"]
            self.assertEqual(entity, {"kind": "champion", "alias": "Annie"})

    def test_mixed_champion_and_map_sources_still_resolve_bank_champion(self) -> None:
        """英雄 bank 与地图 bank 混合来源不阻止文件 id 归属。"""

        record = self.build_record("Set_Switch_aggro_off", "raw/events/champions/83.txt")
        record.source_paths.extend(["raw/events/maps/11.txt", "raw/events/maps/21.txt"])

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs([record], root, reference_version="16.5")

            shard = root / "by-entity" / "champions" / "Yorick.jsonl"
            self.assertTrue(shard.is_file())
            self.assertEqual(self.first_payload(shard)["entity"]["raw_alias"], "aggro")

    def test_multi_bank_switch_alias_splits_by_source_file(self) -> None:
        """aggro 开关按各自 bank 文件 id 拆分归属不同英雄。"""

        records = [
            self.build_record("Set_Switch_aggro_off", "raw/events/champions/83.txt"),
            self.build_record("Set_Switch_aggro_on", "raw/events/champions/11.txt"),
        ]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs(records, root, reference_version="16.5")

            yorick = root / "by-entity" / "champions" / "Yorick.jsonl"
            master_yi = root / "by-entity" / "champions" / "MasterYi.jsonl"
            self.assertTrue(yorick.is_file())
            self.assertTrue(master_yi.is_file())
            self.assertEqual(self.first_payload(yorick)["entity"]["raw_alias"], "aggro")
            self.assertEqual(self.first_payload(master_yi)["entity"]["raw_alias"], "aggro")

    def test_affix_source_conflict_emits_quality_diagnostic(self) -> None:
        """挂靠结论与 bank 文件 id 矛盾时写入 quality diagnostic，且不改变挂靠优先级。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            # AnnieTibbers 前缀挂靠 Annie，但事件放在 Brand 的 bank（63.txt）里。
            self.write_single(
                "Play_sfx_AnnieTibbersSkin10_AnnieTibbersBasicAttack2_OnCast",
                "raw/events/champions/63.txt",
                root,
            )

            shard = root / "by-entity" / "champions" / "Annie.jsonl"
            self.assertTrue(shard.is_file())
            payload = self.first_payload(shard)
            self.assertEqual(payload["entity"]["alias"], "Annie")
            diagnostics = payload["quality"]["diagnostics"]
            conflicts = [item for item in diagnostics if item.startswith("entity_affix_source_conflict:")]
            self.assertEqual(len(conflicts), 1)
            self.assertIn("AnnieTibbers", conflicts[0])
            self.assertIn("affix=Annie", conflicts[0])
            self.assertIn("source_file=Brand", conflicts[0])

    def test_affix_matching_source_has_no_conflict_diagnostic(self) -> None:
        """挂靠结论与 bank 文件 id 一致时不产生矛盾 diagnostic。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single(
                "Play_sfx_AnnieTibbersSkin10_AnnieTibbersBasicAttack2_OnCast",
                "raw/events/champions/1.txt",
                root,
            )

            payload = self.first_payload(root / "by-entity" / "champions" / "Annie.jsonl")
            diagnostics = payload["quality"]["diagnostics"]
            self.assertFalse([item for item in diagnostics if item.startswith("entity_affix_source_conflict:")])


class ChampionEntityResolverChainTests(unittest.TestCase):
    """用合成 resolver 毫秒级覆盖五步归一链优先级与边界。"""

    @staticmethod
    def resolver(
        aliases: dict[str, str] | None = None,
        ids: dict[int, str] | None = None,
        manual: dict[str, dict[str, str]] | None = None,
    ) -> ChampionEntityResolver:
        """构造合成 champion 实体归一解析器。"""

        return ChampionEntityResolver(aliases=aliases or {}, ids=ids or {}, manual=manual or {})

    def test_exact_match_wins_and_never_diagnoses(self) -> None:
        """精确匹配优先于一切，且不做 bank 矛盾检查。"""

        resolver = self.resolver(aliases={"annie": "Annie"}, ids={63: "Brand"})
        resolution = resolve_champion_entity("annie", ["raw/events/champions/63.txt"], resolver)

        self.assertEqual((resolution.kind, resolution.alias), ("champion", "Annie"))
        self.assertIsNone(resolution.diagnostic)

    def test_manual_keep_beats_skin_strip_and_file_id(self) -> None:
        """人工 keep 优先于 SkinNN 剥离与文件 id 回退。"""

        resolver = self.resolver(
            aliases={"foo": "Foo"},
            ids={1: "Foo"},
            manual={"fooskin12": {"alias": "FooSkin12", "decision": "keep", "note": "x"}},
        )
        resolution = resolve_champion_entity("FooSkin12", ["raw/events/champions/1.txt"], resolver)

        self.assertEqual((resolution.kind, resolution.alias), ("champion", "FooSkin12"))

    def test_manual_champion_maps_to_reference_casing(self) -> None:
        """人工 champion 决策映射到 reference 规范写法。"""

        resolver = self.resolver(
            aliases={"jarvaniv": "JarvanIV"},
            manual={"jarviniv": {"alias": "JarvinIV", "decision": "champion", "champion": "jarvanIV", "note": "x"}},
        )
        resolution = resolve_champion_entity("JarvinIV", [], resolver)

        self.assertEqual((resolution.kind, resolution.alias), ("champion", "JarvanIV"))

    def test_manual_downgrade_changes_kind_keeps_alias(self) -> None:
        """人工降级改 kind 不改 alias。"""

        resolver = self.resolver(
            manual={"578": {"alias": "578", "decision": "downgrade", "kind": "unknown", "note": "x"}},
        )
        resolution = resolve_champion_entity("578", ["raw/events/champions/145.txt"], resolver)

        self.assertEqual((resolution.kind, resolution.alias), ("unknown", "578"))

    def test_skin_strip_beats_affix(self) -> None:
        """SkinNN 剥离优先于前后缀挂靠（Foos 前缀更长也不抢）。"""

        resolver = self.resolver(aliases={"foo": "Foo", "foos": "Foos"})
        resolution = resolve_champion_entity("FooSkin12", [], resolver)

        self.assertEqual(resolution.alias, "Foo")

    def test_affix_beats_file_id_with_conflict_diagnostic(self) -> None:
        """挂靠优先于文件 id；两者矛盾时只附 diagnostic 不换结论。"""

        resolver = self.resolver(aliases={"foo": "Foo", "bar": "Bar"}, ids={63: "Bar"})
        resolution = resolve_champion_entity("FooPet", ["raw/events/champions/63.txt"], resolver)

        self.assertEqual((resolution.kind, resolution.alias), ("champion", "Foo"))
        self.assertEqual(
            resolution.diagnostic,
            "entity_affix_source_conflict:alias=FooPet affix=Foo source_file=Bar",
        )

    def test_affix_agreeing_with_file_id_has_no_diagnostic(self) -> None:
        """挂靠与文件 id 一致或 bank 无结论时不产生 diagnostic。"""

        resolver = self.resolver(aliases={"foo": "Foo"}, ids={1: "Foo"})

        agree = resolve_champion_entity("FooPet", ["raw/events/champions/1.txt"], resolver)
        no_bank = resolve_champion_entity("FooPet", [], resolver)

        self.assertIsNone(agree.diagnostic)
        self.assertIsNone(no_bank.diagnostic)

    def test_file_id_fallback_then_unresolved(self) -> None:
        """文件 id 兜底；全部失败时保留原 alias。"""

        resolver = self.resolver(aliases={"yorick": "Yorick"}, ids={83: "Yorick"})

        by_file = resolve_champion_entity("aggro", ["raw/events/champions/83.txt"], resolver)
        unresolved = resolve_champion_entity("aggro", ["raw/events/maps/11.txt"], resolver)

        self.assertEqual(by_file.alias, "Yorick")
        self.assertEqual((unresolved.kind, unresolved.alias), ("champion", "aggro"))

    def test_equal_length_affix_tie_break_prefers_prefix(self) -> None:
        """等长前后缀撞车偏前缀：AshenKnightPyke 归 Ashe。

        这是对现行 tie-break 的文档化锚定而非理想行为——现实中
        Ashen Knight Pyke 是 Pyke 的皮肤，理想归属应为 Pyke。当前
        raw 语料没有此类 alias；若未来出现且与 bank 文件 id 矛盾，
        entity_affix_source_conflict diagnostic 会以 warning 暴露。
        """

        resolver = self.resolver(aliases={"ashe": "Ashe", "pyke": "Pyke"})

        self.assertEqual(champion_by_alias_affix("ashenknightpyke", resolver), "Ashe")

    def test_prefix_gate_excludes_two_letter_champion(self) -> None:
        """长度门排除 Vi 这类两字母英雄的前缀挂靠。"""

        resolver = self.resolver(aliases={"vi": "Vi"})

        self.assertIsNone(champion_by_alias_affix("vistorm", resolver))

    def test_suffix_match_attaches_plain_word_ending(self) -> None:
        """后缀挂靠会把 Explosion 挂到 Sion——锚定现状并标注风险。

        当前 raw 语料的 champion 碎片中不存在 *sion 类普通单词 alias
        （全量审计后缀命中 12 例均为皮肤别名）；若未来出现，bank 文件
        id 矛盾 diagnostic 是安全网。此用例防止行为被静默改变。
        """

        resolver = self.resolver(aliases={"sion": "Sion"})

        self.assertEqual(champion_by_alias_affix("explosion", resolver), "Sion")

    def test_affix_requires_nonempty_remainder_and_min_lengths(self) -> None:
        """挂靠要求余段非空、前缀≥3、后缀≥4，单字母不参与。"""

        resolver = self.resolver(aliases={"foo": "Foo", "zed": "Zed"})

        self.assertIsNone(champion_by_alias_affix("foo", resolver))
        self.assertIsNone(champion_by_alias_affix("e", resolver))
        self.assertIsNone(champion_by_alias_affix("bigzed", resolver))
        self.assertEqual(champion_by_alias_affix("zedshadow", resolver), "Zed")


class EntityAttributionOverridesValidationTests(unittest.TestCase):
    """固化 entity_attribution.yaml 加载的 fail-fast 校验。"""

    @staticmethod
    def write_overrides(root: Path, body: str) -> Path:
        """把 champion_aliases 条目写成临时 overrides 文件。"""

        (root / "entity_attribution.yaml").write_text(
            f"version: curated\nchampion_aliases:\n{body}",
            encoding="utf-8",
        )
        return root

    def test_missing_note_fails(self) -> None:
        """缺 note 的条目直接报错并指出 alias。"""

        with tempfile.TemporaryDirectory() as temp:
            root = self.write_overrides(Path(temp), "  - alias: FooPet\n    decision: keep\n")

            with self.assertRaises(ValueError) as ctx:
                load_entity_attribution_overrides(root)

        self.assertIn("FooPet", str(ctx.exception))

    def test_illegal_decision_fails(self) -> None:
        """decision 不在 keep/champion/downgrade 之内时报错。"""

        with tempfile.TemporaryDirectory() as temp:
            root = self.write_overrides(Path(temp), "  - alias: FooPet\n    decision: merge\n    note: x\n")

            with self.assertRaises(ValueError) as ctx:
                load_entity_attribution_overrides(root)

        self.assertIn("FooPet", str(ctx.exception))

    def test_downgrade_missing_or_illegal_kind_fails(self) -> None:
        """downgrade 条目缺 kind 或 kind 非 unknown/map_or_system 时报错。"""

        cases = [
            "  - alias: FooPet\n    decision: downgrade\n    note: x\n",
            "  - alias: FooPet\n    decision: downgrade\n    kind: champion\n    note: x\n",
        ]

        for body in cases:
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                root = self.write_overrides(Path(temp), body)

                with self.assertRaises(ValueError) as ctx:
                    load_entity_attribution_overrides(root)

                self.assertIn("FooPet", str(ctx.exception))

    def test_champion_decision_missing_champion_field_fails(self) -> None:
        """decision=champion 缺 champion 字段时报错。"""

        with tempfile.TemporaryDirectory() as temp:
            root = self.write_overrides(Path(temp), "  - alias: FooPet\n    decision: champion\n    note: x\n")

            with self.assertRaises(ValueError) as ctx:
                load_entity_attribution_overrides(root)

        self.assertIn("FooPet", str(ctx.exception))

    def test_duplicate_alias_fails(self) -> None:
        """alias 大小写不敏感重复时报错。"""

        body = (
            "  - alias: FooPet\n    decision: keep\n    note: x\n  - alias: foopet\n    decision: keep\n    note: y\n"
        )

        with tempfile.TemporaryDirectory() as temp:
            root = self.write_overrides(Path(temp), body)

            with self.assertRaises(ValueError) as ctx:
                load_entity_attribution_overrides(root)

        self.assertIn("foopet", str(ctx.exception))

    def test_champion_target_missing_in_reference_fails(self) -> None:
        """champion 决策指向 reference 不存在的英雄时构建 resolver 报错。"""

        body = "  - alias: FooPet\n    decision: champion\n    champion: NotARealChampion\n    note: x\n"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference_root = root / "reference"
            write_reference_champion(reference_root, "Annie", names={"en_US": "Annie"})
            overrides_root = root / "overrides"
            overrides_root.mkdir()
            self.write_overrides(overrides_root, body)

            with self.assertRaises(ValueError) as ctx:
                load_champion_entity_resolver(reference_root, overrides_root)

        self.assertIn("FooPet", str(ctx.exception))

    def test_alias_conflicting_with_reference_fails(self) -> None:
        """人工 alias 与 reference 英雄冲突时构建 resolver 报错。"""

        body = "  - alias: Annie\n    decision: keep\n    note: x\n"

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reference_root = root / "reference"
            write_reference_champion(reference_root, "Annie", names={"en_US": "Annie"})
            overrides_root = root / "overrides"
            overrides_root.mkdir()
            self.write_overrides(overrides_root, body)

            with self.assertRaises(ValueError) as ctx:
                load_champion_entity_resolver(reference_root, overrides_root)

        self.assertIn("Annie", str(ctx.exception))


class NormalizedShellVerbDisplayTests(unittest.TestCase):
    """验证 Stop/Mute 壳动词进入 legacy 展示文案。"""

    def test_stop_event_legacy_short_display_marks_stop(self) -> None:
        """Stop_ 事件 legacy 短文案以壳动词词表的停止后缀收尾，不得渲染成播放语义。"""

        record = normalize_semantics(decompose_event_name("Stop_sfx_AuroraSkin11_Dance3D_leadin", game_version="16.5"))
        short = build_short_display(record)
        suffix = playback_verb_suffix("stop")

        self.assertTrue(suffix)
        self.assertTrue(short.endswith(suffix))
        # 后缀之外仍有事件主体文案，停止语义不是整条文案的全部。
        self.assertGreater(len(short), len(suffix))

    def test_emote_legacy_display_uses_asset_kind_word(self) -> None:
        """emote legacy 文案按 asset kind 选词，sfx 与 vo 不得渲染成同一文案。"""

        def emote_record(asset_kind: str) -> EventDecompositionRecord:
            return EventDecompositionRecord(
                schema_version="event-decomposition.v1",
                game_version="cumulative",
                raw_event_name=f"Play_{asset_kind}_Annie_Emote",
                scope="champion",
                shell=EventShell(verb="play", asset_kind=asset_kind, body="Annie_Emote"),
                owner=EventOwnerRef(kind="champion", alias="Annie", raw_owner_token="Annie"),
                profile=EventProfile(primary_family="emote", semantic_profile=SemanticProfile.SELF_ACTION),
            )

        sfx_short = build_short_display(emote_record("sfx"))
        vo_short = build_short_display(emote_record("vo"))

        self.assertTrue(sfx_short)
        self.assertTrue(vo_short)
        self.assertNotEqual(sfx_short, vo_short)


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
        self.assertEqual(translation["arguments"]["phase"]["key"], "effect_deactivate")
        self.assertTrue(normalized["display"]["zh_cn_short"])

    def test_writer_persists_translation_in_entity_shard(self) -> None:
        """JSONL 分片写出 translation 与 display 结构字段。"""

        record = enriched_record("Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate")

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_outputs([record], root, reference_version="16.5")
            shard = root / "by-entity" / "champions" / "Galio.jsonl"
            payload = json.loads(shard.read_text(encoding="utf-8").splitlines()[0])

        self.assertIn("translation", payload)
        self.assertEqual(payload["translation"]["frame_type"], "ability_state_change")
        self.assertTrue(payload["display"]["zh_cn_short"])
        self.assertTrue(payload["display"]["zh_cn_detail"])


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


class TranslationContextNameSystemTests(unittest.TestCase):
    """验证 translation 上下文的英雄名称体系接线。"""

    def test_champion_zh_label_follows_reference_names(self) -> None:
        """（用例 A）zh 主 label 直取 names.zh_CN，不被皮肤投票覆盖，并跟随 reference 改名。"""

        from lol_event_semantics.normalized_output import build_translation_context

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_reference_champion(root, "Testo", names={"en_US": "Testo", "zh_CN": "参考俗名"})
            write_reference_skins(root, "Testo", ["星域 投票旧名", "深海 投票旧名"])
            context = build_translation_context(root)

        entry = context.champion_labels["Testo"]
        self.assertEqual(entry["zh_CN"], "参考俗名")
        self.assertEqual(entry["en_US"], "Testo")
        self.assertEqual(getattr(entry, "label_sources")["zh_CN"], "reference_names")

    def test_missing_reference_zh_name_falls_back_to_skin_vote(self) -> None:
        """（用例 B）reference 缺 zh 名时回填皮肤投票结果并标记来源。"""

        from lol_event_semantics.normalized_output import build_translation_context

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_reference_champion(root, "Testo", names={"en_US": "Testo"})
            write_reference_skins(root, "Testo", ["星域 投票名", "深海 投票名"])
            context = build_translation_context(root)

        entry = context.champion_labels["Testo"]
        self.assertEqual(entry["zh_CN"], "投票名")
        self.assertEqual(getattr(entry, "label_sources")["zh_CN"], "skin_label_vote")
        self.assertEqual(getattr(entry, "label_sources")["en_US"], "reference_names")

    def test_name_forms_shape_from_real_reference(self) -> None:
        """（用例 C）真实 reference 的名称多形式接线非空且结构合法。

        不锁任何具体中文值：只要求存在带 name_forms 的英雄，且每个
        name_forms 的字段都在 NAME_FORM_FIELDS 白名单内、语言映射到
        非空字符串列表、列表内无重复。
        """

        context = shared_translation_context()
        entries_with_forms = [entry for entry in context.champion_labels.values() if getattr(entry, "name_forms")]

        self.assertTrue(entries_with_forms)
        for entry in entries_with_forms:
            name_forms = getattr(entry, "name_forms")
            self.assertLessEqual(set(name_forms), set(NAME_FORM_FIELDS))
            for locales in name_forms.values():
                self.assertTrue(locales)
                for values in locales.values():
                    self.assertTrue(values)
                    self.assertTrue(all(isinstance(value, str) and value for value in values))
                    self.assertEqual(len(values), len(set(values)))

    def test_name_forms_omit_empty_fields(self) -> None:
        """（用例 D）无数据的名称形式整体省略，不写空字段占位。"""

        context = shared_translation_context()
        all_forms = [getattr(entry, "name_forms") for entry in context.champion_labels.values()]

        # 省略路径需真实发生：总有英雄没有俗称。
        self.assertTrue(any("nicknames" not in forms for forms in all_forms))
        for forms in all_forms:
            for locales in forms.values():
                self.assertTrue(locales)
                self.assertTrue(all(values for values in locales.values()))


def write_reference_champion(root: Path, alias: str, *, names: dict[str, str]) -> None:
    """写出临时 reference 英雄 YAML。

    Args:
        root: 临时 reference 根目录。
        alias: 英雄 alias。
        names: 语言到英雄名的映射。
    """

    from lol_event_semantics.reference_collection import write_yaml

    entity = {
        "type": "champion",
        "id": 9999,
        "alias": alias,
        "names": {
            language: [{"value": value, "sources": [{"source": "test", "version": "1"}]}]
            for language, value in names.items()
        },
    }
    write_yaml(root / "champions" / f"{alias}.yaml", {"schema_version": 1, "entity": entity})


def write_reference_skins(root: Path, champion_alias: str, zh_full_names: list[str]) -> None:
    """写出临时 reference 皮肤 YAML 序列。

    Args:
        root: 临时 reference 根目录。
        champion_alias: 皮肤所属英雄 alias。
        zh_full_names: 各皮肤的中文全名（"皮肤名 英雄名" 格式）。
    """

    from lol_event_semantics.reference_collection import write_yaml

    for index, zh_name in enumerate(zh_full_names, start=1):
        token = f"Skin{index:02d}"
        entity = {
            "type": "skin",
            "champion_alias": champion_alias,
            "champion_id": 9999,
            "skin_number": index,
            "skin_token": token,
            "id": 9999000 + index,
            "alias": f"{champion_alias}{token}",
            "names": {"zh_CN": [{"value": zh_name, "sources": [{"source": "test", "version": "1"}]}]},
        }
        write_yaml(
            root / "skins" / champion_alias / f"{token}.yaml",
            {"schema_version": 1, "entity": entity},
        )


class NormalizedDisplayEndToEndTests(unittest.TestCase):
    """验证问题事件经完整链路后的 display 性质（不锁具体文案）。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载共享 reference index 与 translation 上下文。"""

        cls.reference = reference_index()
        cls.context = shared_translation_context()

    def build_display(self, raw_event_name: str) -> dict[str, str]:
        """按生产链路渲染 display。"""

        normalized = self.build_normalized(raw_event_name)
        display = normalized["display"]
        assert isinstance(display, dict)
        return display

    def build_normalized(
        self,
        raw_event_name: str,
        *,
        source_path: str | None = None,
    ) -> dict[str, object]:
        """按生产链路构建 normalized 记录。"""

        record = enrich_with_reference(
            normalize_semantics(
                decompose_event_name(
                    raw_event_name,
                    game_version="16.5",
                    source_path=source_path,
                )
            ),
            self.reference,
        )
        return build_normalized_record(record, translation_context=self.context)

    def test_galio_skin_typo_detail_resolves_champion_and_skin(self) -> None:
        """owner alias 大小写 typo（GalioSKin04）不得把原始 alias 或皮肤 token 泄漏进 detail。"""

        display = self.build_display("Play_vo_GalioSKin04_Taunt3DGeneral")
        detail = display["zh_cn_detail"]

        self.assertTrue(detail)
        self.assertNotIn("galioskin04", detail.lower())
        self.assertNotIn("skin04", detail.lower())

    def test_aurelionsol_stop_detail_keeps_interpunct_name(self) -> None:
        """间隔号英雄名以整名恰好出现一次于 detail，不被截断也不双写。

        英雄 label 取运行时 translation 上下文而非硬编码，reference 改名
        时性质仍成立；若整名被截成后半段或与皮肤名双写，计数即偏离 1。
        """

        display = self.build_display("Stop_sfx_AurelionSolSkin21_AurelionSolQ_charge")
        champion_label = self.context.champion_labels["AurelionSol"]["zh_CN"]

        self.assertTrue(champion_label)
        self.assertEqual(display["zh_cn_detail"].count(champion_label), 1)
        self.assertTrue(display["zh_cn_short"])

    def test_stop_dance_sfx_display_marks_stop(self) -> None:
        """Stop_ sfx 事件经完整链路后短文案仍以壳动词词表的停止后缀收尾。"""

        normalized = self.build_normalized("Stop_sfx_AuroraSkin11_Dance3D_leadin")
        display = normalized["display"]
        translation = normalized["translation"]
        assert isinstance(display, dict)
        assert isinstance(translation, dict)
        suffix = playback_verb_suffix("stop")

        self.assertTrue(suffix)
        self.assertEqual(translation["frame_type"], "champion_action_event")
        self.assertEqual(display["zh_cn_short"], f"舞蹈音效引入{suffix}")
        self.assertTrue(display["zh_cn_short"].endswith(suffix))

    def test_action_lifecycle_has_meaningful_chinese_for_voice_and_sfx(self) -> None:
        """相同动作的 VO/SFX 中文都保留 Loop，且资产类型不能混写。"""

        cases = [
            ("Play_vo_Bard_Dance3D_Loop_buffactivate", "voice_event", "跳舞语音循环"),
            ("Play_sfx_Bard_Dance3D_Loop_buffactivate", "champion_action_event", "舞蹈音效循环"),
        ]
        for raw, frame_type, expected_short in cases:
            with self.subTest(raw=raw):
                normalized = self.build_normalized(raw)
                translation = normalized["translation"]
                display = normalized["display"]
                assert isinstance(translation, dict)
                assert isinstance(display, dict)

                self.assertEqual(translation["frame_type"], frame_type)
                self.assertEqual(translation["arguments"]["phase"]["key"], "loop")
                self.assertEqual(display["zh_cn_short"], expected_short)

    def test_legacy_fallback_uses_authoritative_champion_and_skin_labels(self) -> None:
        """没有 translation frame 的 legacy detail 也不得泄漏英雄 alias 或 SkinNN。"""

        display = self.build_display("Play_sfx_AatroxSkin01_AatroxRRevive_all")
        detail = display["zh_cn_detail"]
        champion = self.context.champion_labels["Aatrox"]["zh_CN"]
        full_skin = self.context.skin_labels[("Aatrox", "Skin01")]["zh_CN"]
        skin = full_skin.removesuffix(f" {champion}")

        self.assertIn(champion, detail)
        self.assertIn(skin, detail)
        self.assertNotRegex(detail, r"\bAatrox\b|Skin01")

    def test_glued_skin_skill_owner_uses_authoritative_champion_name(self) -> None:
        """ZoeSkin01W 这类粘连 owner 不得把 Zoe/Skin01 泄漏进中文展示。"""

        display = self.build_display("Play_sfx_ZoeSkin01W_enablebypass")
        detail = display["zh_cn_detail"]

        self.assertIn(self.context.champion_labels["Zoe"]["zh_CN"], detail)
        self.assertNotRegex(detail, r"(?i)Zoe|Skin01")

    def test_glued_skin_action_owner_builds_meaningful_action(self) -> None:
        """GangplankSkin23Taunt3D 要还原为普朗克的嘲讽动作。"""

        display = self.build_display("Play_sfx_GangplankSkin23Taunt3D_buffactivate")
        detail = display["zh_cn_detail"]

        self.assertIn(self.context.champion_labels["Gangplank"]["zh_CN"], detail)
        self.assertIn("嘲讽", detail)
        self.assertNotRegex(detail, r"(?i)Gangplank|Skin23|Taunt")

    def test_weak_pascal_prefix_does_not_render_as_skill_key(self) -> None:
        """EvolveSelf 的 E 只是英文词首，低置信 hint 不得进入用户中文。"""

        display = self.build_display("Play_sfx_KaisaSkin14_KaisaEvolveSelf_cast")
        detail = display["zh_cn_detail"]

        self.assertNotRegex(detail, r"E\s*技能")

    def test_fizz_piercing_strike_renders_q_not_passive(self) -> None:
        """有 owner 级证据的 PiercingStrike 应显示为淘气打击 Q，而不是 P。"""

        display = self.build_display("Play_sfx_FizzSkin04_FizzPiercingStrike_OnCast")
        detail = display["zh_cn_detail"]

        self.assertIn("Q技能", detail)
        self.assertIn("淘气打击", detail)
        self.assertNotRegex(detail, r"P\s*技能")

    def test_ruby_boss_normalized_entity_is_actor_not_mode_namespace(self) -> None:
        """Ruby 是模式上下文，FNVeigar 还原出的维迦才是稳定实体。"""

        raw = "Play_sfx_Ruby_Boss_FNVeigar_AttackInhib_missilelaunch"
        record = enrich_with_reference(
            normalize_semantics(decompose_event_name(raw, game_version="16.5")),
            self.reference,
        )
        normalized = build_normalized_record(record, translation_context=self.context)

        self.assertEqual(normalized["entity"], {"kind": "champion", "alias": "Veigar"})
        self.assertNotIn("Ruby", normalized["display"]["zh_cn_detail"])

    def test_mode_only_event_uses_post_prefix_system_entity(self) -> None:
        """没有英雄 actor 时，Ruby/Strawberry 后首段才是系统实体，模式前缀不能占用 entity。"""

        cases = [
            ("Play_sfx_Ruby_Curse_MirrorImage_clone_buffdeactivate", "Curse"),
            ("Play_sfx_Strawberry_Spire_Quest_idle_appear", "Spire"),
        ]

        for raw, alias in cases:
            with self.subTest(raw=raw):
                record = enrich_with_reference(
                    normalize_semantics(decompose_event_name(raw, game_version="16.5")),
                    self.reference,
                )
                normalized = build_normalized_record(record, translation_context=self.context)

                self.assertEqual(normalized["entity"], {"kind": "mode_system", "alias": alias})
                self.assertEqual(normalized["semantic"]["slots"]["mode_entity"]["alias"], alias)
                self.assertNotIn(normalized["entity"]["alias"].casefold(), {"ruby", "strawberry"})

    def test_reviewed_mode_system_sfx_has_meaningful_chinese_frame(self) -> None:
        """已审阅的模式系统实体应显示业务名与音效阶段，不再退化成“状态启动”。"""

        cases = [
            (
                "Play_sfx_Ruby_Curse_MirrorImage_clone_buffdeactivate",
                "Curse",
                "诅咒音效",
                "audio_end",
                "末日人机 诅咒音效结束",
            ),
            (
                "Play_sfx_Strawberry_Spire_Quest_idle_appear",
                "Spire",
                "尖塔音效",
                None,
                "PVE 模式 尖塔音效",
            ),
            (
                "Stop_sfx_Strawberry_Gameplay_HUD_Boon_appear",
                "Gameplay",
                "玩法音效",
                None,
                "PVE 模式 玩法音效停止",
            ),
            (
                "Play_sfx_GameMode_Ruby_Pumpkin_destroyed",
                "Pumpkin",
                "南瓜音效",
                None,
                "末日人机 南瓜音效",
            ),
        ]

        for raw, entity_alias, effect_label, phase_key, expected_short in cases:
            with self.subTest(raw=raw):
                normalized = self.build_normalized(raw)
                translation = normalized["translation"]

                self.assertEqual(normalized["entity"]["alias"], entity_alias)
                self.assertEqual(translation["frame_type"], "mode_system_sfx_event")
                self.assertEqual(translation["arguments"]["effect"]["labels"]["zh_CN"], effect_label)
                if phase_key is None:
                    self.assertNotIn("phase", translation["arguments"])
                else:
                    self.assertEqual(translation["arguments"]["phase"]["key"], phase_key)
                self.assertEqual(normalized["display"]["zh_cn_short"], expected_short)
                self.assertNotRegex(expected_short, r"状态启动|状态结束")

    def test_reviewed_ruby_ryze_channel_keeps_actor_spell_component_and_phase(self) -> None:
        """Ruby_RyzeRChannel 应保留末日人机演员、R 引导组件与结束相位。"""

        normalized = self.build_normalized("Play_sfx_Ruby_RyzeRChannel_end")

        translation = normalized["translation"]
        self.assertEqual(translation["frame_type"], "ability_cast_event")
        self.assertEqual(translation["arguments"]["context"]["key"], "ruby")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Ryze")
        self.assertEqual(translation["arguments"]["ability_slot"]["key"], "r")
        self.assertEqual(
            translation["arguments"]["ability_component"]["key"],
            "realm_warp_channel",
        )
        self.assertEqual(translation["arguments"]["phase"]["key"], "end")
        self.assertEqual(normalized["display"]["zh_cn_detail"], "末日人机 瑞兹 R技能曲境折跃引导结束")

    def test_mode_actor_generic_sfx_renders_reviewed_effect_in_chinese(self) -> None:
        """PVE 首领专属效果必须展示演员、效果与相位，不再退成“音效事件”。"""

        normalized = self.build_normalized("Play_sfx_Strawberry_Boss_Aatrox_FallingSword_missile_land")

        translation = normalized["translation"]
        self.assertEqual(translation["frame_type"], "champion_sfx_effect_event")
        self.assertEqual(translation["arguments"]["context"]["key"], "strawberry")
        self.assertEqual(translation["arguments"]["champion"]["alias"], "Aatrox")
        self.assertEqual(translation["arguments"]["effect"]["labels"]["zh_CN"], "坠落之剑音效")
        self.assertIn("坠落之剑音效", normalized["display"]["zh_cn_detail"])
        self.assertNotIn("音效事件", normalized["display"]["zh_cn_detail"])

    def test_reviewed_embedded_component_stage_is_rendered(self) -> None:
        """规则声明的嵌入段次必须进入 frame 和中文，不能只留在内部 token。"""

        normalized = self.build_normalized("Play_sfx_Zoe_ZoeQMis2Warning_missilecast_self")

        translation = normalized["translation"]
        self.assertEqual(translation["arguments"]["ability_stage"]["value"], "2")
        self.assertIn("第2段", normalized["display"]["zh_cn_detail"])

    def test_mode_actor_map_sfx_recovers_skill_and_attack_chinese(self) -> None:
        """模式资产即使挂在 map 下，也要以真实演员恢复技能/攻击语义。"""

        cases = [
            (
                "Play_sfx_Ruby_Karthus_RDamageSound_OnBuffActivate",
                "ability_state_change",
                "末日人机 卡尔萨斯 R技能生效",
            ),
            (
                "Play_sfx_Strawberry_Boss_Briar_BasicAttack_cast",
                "champion_attack_event",
                "PVE 模式 贝蕾亚 普通攻击出手",
            ),
            (
                "Play_sfx_strawberry_tibbersbasicattack_OnCast",
                "mode_unit_attack_event",
                "PVE 模式 提伯斯 普通攻击出手",
            ),
        ]

        for raw, frame_type, expected_detail in cases:
            with self.subTest(raw=raw):
                normalized = self.build_normalized(raw, source_path="raw/events/maps/11.txt")

                self.assertEqual(normalized["translation"]["frame_type"], frame_type)
                self.assertEqual(normalized["display"]["zh_cn_detail"], expected_detail)
                self.assertNotRegex(normalized["display"]["zh_cn_detail"], r"状态启动|状态结束")

        tibbers = self.build_normalized(
            "Play_sfx_strawberry_tibbersbasicattack_OnCast",
            source_path="raw/events/maps/11.txt",
        )
        self.assertEqual(tibbers["entity"], {"kind": "mode_unit", "alias": "Tibbers"})


class NormalizedOutputCliTests(unittest.TestCase):
    """验证 normalized 输出 CLI。"""

    def test_cli_writes_outputs_from_decomposition_jsonl(self) -> None:
        """CLI 能读取拆解 JSONL 写出 normalized 分片；缺省 --reference-version 时记 cumulative。"""

        record = normalize_semantics(decompose_event_name("Play_vo_Annie_AnnieQ_OnCast", game_version="cumulative"))

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo_root = Path(__file__).resolve().parents[1]
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
                    "--repo-root",
                    str(repo_root),
                    "--raw-root",
                    str(repo_root / "raw" / "events"),
                    "--reference-root",
                    str(repo_root / "reference"),
                    "--rules-root",
                    str(repo_root / "rules"),
                    "--overrides-root",
                    str(repo_root / "overrides"),
                    "--sources-manifest",
                    str(repo_root / "data" / "frozen-sources-manifest.v1.json"),
                    "--frozen-profile",
                    str(repo_root / "data" / "frozen-profile.v1.json"),
                ],
                cwd=repo_root,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((output_root / "by-entity" / "champions" / "Annie.jsonl").is_file())
            manifest = json.loads((output_root / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["reference_version"], "cumulative")


class DisplayTierTests(unittest.TestCase):
    """验证 display.tier 分层与 Tier3 短文案退出。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载共享 reference index 与 translation 上下文（同生产链路）。"""

        cls.reference = reference_index()
        cls.context = shared_translation_context()

    def build(self, raw_event_name: str) -> dict[str, object]:
        """按生产链路构造 normalized 记录。"""

        record = enrich_with_reference(
            normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
            self.reference,
        )
        return build_normalized_record(record, translation_context=self.context)

    def test_tier3_map_ambience_emitters_drop_short_display(self) -> None:
        """地图纯氛围发声器（Emitter/Bloom/Ambience）判 Tier3，退出短文案。

        这些是地图环境循环声（大龙坑锁链、泉水树木），无玩家事件语义
        （T3.5 处置决策，20_DECISIONS 2026-07-17）。
        """

        cases = [
            "Play_sfx_Env_Map10_Emitter_bigchains",
            "Play_sfx_Env_Map11_Bloom_TreeGroans_Fountain",
            "Play_sfx_Env_Map10_Ambience_base",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertEqual(display["tier"], 3)
                self.assertIsNone(display["zh_cn_short"])

    def test_tier2_map_gameplay_semantic_events_keep_short_display(self) -> None:
        """有玩家语义的地图事件（塔摧毁等）不随氛围规则降级，短文案保留。"""

        normalized = self.build("Play_sfx_ENV_map10_ChaosTurret_break01")
        display = normalized["display"]

        self.assertEqual(display["tier"], 2)
        self.assertIsInstance(display["zh_cn_short"], str)
        self.assertTrue(display["zh_cn_short"])

    def test_tier1_vo_dialogue_and_action_families(self) -> None:
        """vo 资产的可感知台词/动作家族判 Tier1，短文案保留。"""

        cases = [
            "Play_vo_AatroxSkin01_FirstEncounter3DAkali",  # first_encounter
            "Play_vo_AatroxSkin01_Kill3DAatrox",  # kill
            "Play_vo_AatroxSkin01_Move2DStandard",  # move
            "Play_vo_AhriSkin86_BuyItem2DGeneral",  # buy_item
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertIsInstance(display, dict)
                self.assertEqual(display["tier"], 1)
                self.assertIsInstance(display["zh_cn_short"], str)
                self.assertTrue(display["zh_cn_short"])

    def test_tier1_covers_translation_covered_family(self) -> None:
        """已被 translation frame 覆盖的家族（如皮肤回城）判 Tier1。"""

        normalized = self.build("Play_lux_skin61_recall3d_buffactivate")

        self.assertIn("translation", normalized)
        self.assertEqual(normalized["display"]["tier"], 1)

    def test_tier2_ambiguous_named_sfx(self) -> None:
        """尚未形成稳定 frame 的命名 SFX 仍判 Tier2。"""

        cases = [
            # augment_system sfx（Ceremony 组段不是增益 Code，词表外不建 frame）
            "Play_sfx_Cherry_Augment_Ceremony_MadScientist_buffactivate",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertIsInstance(display, dict)
                self.assertEqual(display["tier"], 2)
                self.assertIsInstance(display["zh_cn_short"], str)
                self.assertTrue(display["zh_cn_short"])

    def test_reviewed_spell_sfx_is_tier1(self) -> None:
        """已确认组件、槽位和阶段的英雄 SFX 应进入稳定 Tier1。"""

        normalized = self.build("Mute_sfx_AkshanSkin01_AkshanWRevive_buffcast")

        self.assertEqual(normalized["display"]["tier"], 1)
        self.assertEqual(normalized["display"]["zh_cn_short"], "W技能赴险夺人复活静音")
        arguments = normalized["translation"]["arguments"]
        self.assertEqual(arguments["ability_slot"]["key"], "w")
        self.assertEqual(arguments["phase"]["key"], "cast")
        self.assertEqual(arguments["playback"]["key"], "mute")

    def test_tier1_execute_action_rejects_false_spell_key_hint(self) -> None:
        """Execute 输出处决动作中文，不得暴露词首误拆出的 E 技能。"""

        normalized = self.build("Play_sfx_RenektonSkin17_RenektonExecute_OnCast")
        display = normalized["display"]
        self.assertIsInstance(display, dict)
        self.assertEqual(display["tier"], 1)
        self.assertEqual(display["zh_cn_short"], "处决动作开始")
        self.assertNotIn("E技能", display["zh_cn_detail"])
        self.assertEqual(normalized["translation"]["frame_type"], "champion_action_event")

    def test_tier1_attack_sfx_keeps_real_action_semantics(self) -> None:
        """普通、暴击与远程攻击都有玩家语义，不得再退出中文短文案。"""

        cases = [
            ("Play_sfx_AatroxSkin01_AatroxBasicAttack2_OnCast", "普通攻击2出手"),
            ("Play_sfx_AatroxSkin01_AatroxCritAttack_OnCast", "暴击攻击出手"),
            ("Play_sfx_JayceSkin03_JayceRangedAttack2_OnCast", "远程普通攻击2出手"),
            ("Play_vo_JayceSkin35_JayceRangedAttack_cast3D", "远程普通攻击出手"),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                self.assertEqual(normalized["display"]["tier"], 1)
                self.assertEqual(normalized["display"]["zh_cn_short"], expected)
                self.assertEqual(normalized["translation"]["frame_type"], "champion_attack_event")

    def test_tier1_vo_asset_within_technical_families(self) -> None:
        """普攻/暴击/待机/材质开关家族里混入的 vo 语音判 Tier1，不随家族误判 Tier3。

        这几个家族多数记录是纯技术 sfx（见 test_tier3_pure_technical_families_
        drop_short_display），但各自混有少量真实台词：Akali 被动攻击台词、
        Ambessa 连击暴击台词、Ahri 待机彩蛋台词、Ivern 换形态台词（verb=play
        的材质/状态切换，不是控制信号）。
        """

        cases = [
            "Play_vo_AkaliSkin14_AkaliBasicAttackPassiveMelee_cast3D",  # basic_attack
            "Play_vo_AmbessaSkin01_AmbessaCritAttackChained1_cast3D",  # crit_attack
            "Play_vo_AhriSkin14_Idle3DGeneral",  # idle
            "Play_vo_IvernSkin01_IvernP_Switch_Blue",  # switch，verb=play
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertIsInstance(display, dict)
                self.assertEqual(display["tier"], 1)
                self.assertIsInstance(display["zh_cn_short"], str)
                self.assertTrue(display["zh_cn_short"])

    def test_tier3_control_verb_overrides_vo_asset(self) -> None:
        """控制类壳动词优先于 vo 例外：即使资产段解析成 vo 也仍是 Tier3。

        Switch_vo_Yuumi_Base_Attached 的 verb=switch，是亚穆的附体/脱离状态
        切换信号，不是播放的台词，不应被“vo 资产判 Tier1”的例外放行。
        """

        normalized = self.build("Switch_vo_Yuumi_Base_Attached")
        display = normalized["display"]

        self.assertEqual(display["tier"], 3)
        self.assertIsNone(display["zh_cn_short"])

    def test_tier3_pure_technical_families_drop_short_display(self) -> None:
        """纯技术家族判 Tier3，zh_cn_short 置 null，detail/debug_label 不丢。"""

        cases = [
            "AkshanSkin01_Switch_AkshanTerrain_Brush",  # 材质开关
            "Set_RTPC_SeraphineSkin43_RHits1",  # 音频总线 RTPC 控制
            "Play_mus_map01_phase_select_base",  # mus 背景音乐控制
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertIsInstance(display, dict)
                self.assertEqual(display["tier"], 3)
                self.assertIsNone(display["zh_cn_short"])
                self.assertTrue(display["zh_cn_detail"])
                self.assertNotIn("translation", normalized)

    def test_tier1_attack_does_not_drop_lexical_atoms_or_slots(self) -> None:
        """攻击升为可展示语义后仍不得删除已解析 token/slot。"""

        record = enrich_with_reference(
            normalize_semantics(
                decompose_event_name("Play_sfx_AatroxSkin01_AatroxBasicAttack2_OnCast", game_version="16.5")
            ),
            self.reference,
        )
        normalized = build_normalized_record(record, translation_context=self.context)

        self.assertEqual(normalized["semantic"]["slots"], record.semantic_slots)
        self.assertEqual(normalized["display"]["tier"], 1)
        self.assertEqual(normalized["translation"]["frame_type"], "champion_attack_event")
        self.assertTrue(normalized["display"]["debug_label"])


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
        reference_index(),
    )


if __name__ == "__main__":
    unittest.main()
