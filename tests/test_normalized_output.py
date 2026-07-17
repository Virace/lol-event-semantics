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
from lol_event_semantics.normalized_output import (
    DEFAULT_REFERENCE_ROOT,
    ChampionEntityResolver,
    build_normalized_record,
    build_short_display,
    champion_by_alias_affix,
    load_champion_entity_resolver,
    load_entity_attribution_overrides,
    resolve_champion_entity,
    write_normalized_outputs,
)


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


class MapSystemEntityDisplayTests(unittest.TestCase):
    """验证 map/系统域头部实体词条与通用后缀渲染（T3.4）。"""

    def test_announcer_owner_renders_chinese_label(self) -> None:
        """Announcer 属于 T3.4 头部词条表，短文案渲染中文名而非英文 alias。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_vo_Announcer_Brawl_Ace",
                game_version="16.5",
                source_path="raw/events/maps/12.txt",
            )
        )

        self.assertIn("播报员", build_short_display(record))

    def test_untabled_owner_keeps_english_alias(self) -> None:
        """表外别名（如 Ruby）不硬造中文，短文案继续保留英文 alias 现状。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Ruby_Boss_Test_OnHit",
                game_version="16.5",
                source_path="raw/events/maps/12.txt",
            )
        )

        short = build_short_display(record)
        self.assertTrue(short.startswith("Ruby"))
        self.assertEqual(short, "Ruby命中")

    def test_loop_suffix_renders_chinese_loop_word(self) -> None:
        """已解析的 loop 通用后缀渲染成"循环"，复用既有阶段词表措辞。"""

        record = normalize_semantics(
            decompose_event_name(
                "Play_sfx_Env_Map11_SRX_Chemtech_Dragon_Zone_Subtle_loop",
                game_version="16.5",
            )
        )

        self.assertIn("循环", build_short_display(record))


class ChampionEntityAttributionTests(unittest.TestCase):
    """验证 champion 实体归一：碎片 alias 归属真实英雄且信息无损。"""

    @staticmethod
    def build_record(raw_event_name: str, source_path: str) -> EventDecompositionRecord:
        """按生产链路构造带来源路径的拆解记录。"""

        return normalize_semantics(
            decompose_event_name(raw_event_name, game_version="16.5", source_path=source_path)
        )

    def write_single(self, raw_event_name: str, source_path: str, root: Path) -> None:
        """写出单条记录的 normalized 输出。"""

        write_normalized_outputs([self.build_record(raw_event_name, source_path)], root, reference_version="16.5")

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

    def test_ruby_stays_standalone_champion(self) -> None:
        """Ruby 教程 bot 保留独立实体，不被文件 id 回退归并。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_single("Play_sfx_Ruby_AmumuBasicAttack_OnCast", "raw/events/champions/66604.txt", root)

            shard = root / "by-entity" / "champions" / "Ruby.jsonl"
            self.assertTrue(shard.is_file())
            self.assertFalse((root / "by-entity" / "champions" / "Ruby_Amumu.jsonl").exists())
            self.assertNotIn("raw_alias", self.first_payload(shard)["entity"])

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
            write_normalized_outputs([record], root, reference_version="16.5")

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
            write_normalized_outputs(records, root, reference_version="16.5")

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
            "  - alias: FooPet\n    decision: keep\n    note: x\n"
            "  - alias: foopet\n    decision: keep\n    note: y\n"
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
            root = self.write_overrides(Path(temp), body)

            with self.assertRaises(ValueError) as ctx:
                load_champion_entity_resolver(DEFAULT_REFERENCE_ROOT, root)

        self.assertIn("FooPet", str(ctx.exception))

    def test_alias_conflicting_with_reference_fails(self) -> None:
        """人工 alias 与 reference 英雄冲突时构建 resolver 报错。"""

        body = "  - alias: Annie\n    decision: keep\n    note: x\n"

        with tempfile.TemporaryDirectory() as temp:
            root = self.write_overrides(Path(temp), body)

            with self.assertRaises(ValueError) as ctx:
                load_champion_entity_resolver(DEFAULT_REFERENCE_ROOT, root)

        self.assertIn("Annie", str(ctx.exception))


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

    def test_aatrox_name_forms_from_real_reference(self) -> None:
        """（用例 C）真实 reference 的 Aatrox 携带俗名、称号与俗称。"""

        from lol_event_semantics.normalized_output import build_translation_context

        context = build_translation_context()
        entry = context.champion_labels["Aatrox"]

        # 断言硬编码真实 reference（亚托克斯/暗裔剑魔/剑魔），reference 刷新时会影响。
        self.assertEqual(entry["zh_CN"], "亚托克斯")
        self.assertEqual(
            getattr(entry, "name_forms"),
            {"title": {"zh_CN": ["暗裔剑魔"]}, "nicknames": {"zh_CN": ["剑魔"]}},
        )

    def test_champion_without_nicknames_omits_field(self) -> None:
        """（用例 D）真实 reference 中无俗称的 Akali 不写空 nicknames 字段。"""

        from lol_event_semantics.normalized_output import build_translation_context

        context = build_translation_context()
        entry = context.champion_labels["Akali"]

        # 断言硬编码真实 reference（阿卡丽有称号"离群之刺"、无俗称），reference 刷新时会影响。
        name_forms = getattr(entry, "name_forms")
        self.assertEqual(name_forms.get("title"), {"zh_CN": ["离群之刺"]})
        self.assertNotIn("nicknames", name_forms)


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


class DisplayTierTests(unittest.TestCase):
    """验证 display.tier 分层与 Tier3 短文案退出。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载共享 reference index 与 translation 上下文（同生产链路）。"""

        cls.reference = ReferenceIndex.from_reference_collection("reference")
        from lol_event_semantics.normalized_output import build_translation_context

        cls.context = build_translation_context()

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
        """存疑但保留短文案：spell sfx 与其它命名 sfx 判 Tier2。"""

        cases = [
            "Mute_sfx_AkshanSkin01_AkshanWRevive_buffcast",  # spell sfx
            "Play_sfx_Cherry_Augment_BannerOfCommand_buffactivate",  # augment_system sfx
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                normalized = self.build(raw)
                display = normalized["display"]
                self.assertIsInstance(display, dict)
                self.assertEqual(display["tier"], 2)
                self.assertIsInstance(display["zh_cn_short"], str)
                self.assertTrue(display["zh_cn_short"])

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
            "Play_sfx_AatroxSkin01_AatroxBasicAttack2_OnCast",  # basic_attack
            "Play_sfx_AatroxSkin01_AatroxCritAttack_OnCast",  # crit_attack
            "Play_sfx_AhriSkin04_Idle3D_buffactivate",  # idle
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

    def test_tier3_does_not_drop_lexical_atoms_or_slots(self) -> None:
        """Tier3 退出短文案不得删除已解析 token/slot（无损原则）。"""

        record = enrich_with_reference(
            normalize_semantics(
                decompose_event_name("Play_sfx_AatroxSkin01_AatroxBasicAttack2_OnCast", game_version="16.5")
            ),
            self.reference,
        )
        normalized = build_normalized_record(record, translation_context=self.context)

        self.assertEqual(normalized["semantic"]["slots"], record.semantic_slots)
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
        ReferenceIndex.from_reference_collection("reference"),
    )


if __name__ == "__main__":
    unittest.main()
