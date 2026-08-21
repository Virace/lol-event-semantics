"""规则分片加载测试。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.rules import RuleBook, contextual_alias_payload


class RuleFragmentTests(unittest.TestCase):
    """验证 contextual alias 分片加载。"""

    def test_fragment_contextual_alias_loads_as_same_key(self) -> None:
        """分片规则应能用原 contextual key 命中。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            (rules / "contextual" / "champions").mkdir(parents=True)
            overrides.mkdir()
            self.write_minimal_rule_files(rules, overrides)
            (rules / "contextual" / "champions" / "Gangplank.yaml").write_text(
                "\n".join(
                    [
                        "scope: champion",
                        "owner: Gangplank",
                        "aliases:",
                        "  GangplankRUpgradeOne:",
                        "    kind: champion_spell_modifier",
                        "    spell: r",
                        "    asset_kind: sfx",
                        "    infer_stage: false",
                        "    component: cannon_barrage_upgrade_1",
                        "    label: 大招升级一阶",
                        "    required_tokens: [Empowered]",
                        "    qualifiers:",
                        "      Empowered: 强化",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            book = RuleBook.load(rules, overrides)

            alias = book.contextual_alias_for("champion", "Gangplank", "GangplankRUpgradeOne")
            self.assertIsNotNone(alias)
            self.assertEqual(alias.label, "大招升级一阶")
            self.assertEqual(alias.spell, "r")
            self.assertEqual(alias.asset_kind, "sfx")
            self.assertFalse(alias.infer_stage)
            self.assertEqual(alias.required_tokens, ("empowered",))
            self.assertEqual(alias.qualifiers, {"empowered": "强化"})
            self.assertEqual(alias.source_path, "rules/contextual/champions/Gangplank.yaml")

    def test_attack_aliases_use_attack_kind_without_forced_passive_relation(self) -> None:
        """Basic/CritAttack 规则源不得用 spell=P 或默认 related_spell=P 冒充被动技能。"""

        book = RuleBook.load(Path("rules"), Path("overrides"))
        checked = 0
        for alias in book.contextual_aliases.values():
            if alias.scope != "champion":
                continue
            marker = f"{alias.token}{alias.component or ''}".lower()
            if not any(
                cue in marker
                for cue in ("basicattack", "basic_attack", "critattack", "crit_attack")
            ):
                continue
            checked += 1
            self.assertEqual(alias.kind, "champion_attack_component", alias.token)
            self.assertIsNone(alias.spell, alias.token)
            if alias.token in {"MissFortuneBasicAttack", "MissFortuneCritAttack"}:
                self.assertIsNone(alias.related_spell, alias.token)

        self.assertGreater(checked, 40)

    def test_obvious_non_passive_sfx_rules_are_not_assigned_to_p(self) -> None:
        """攻击建筑、回城、皮肤拟音等明确非被动事件必须离开 P 槽位。"""

        book = RuleBook.load(Path("rules"), Path("overrides"))
        expected = {
            ("Draven", "DravenAttackP"): ("champion_attack_component", None),
            ("Nunu", "StructureAttack"): ("champion_attack_component", None),
            ("Graves", "AutoAttackRecoilCastEDummy"): ("champion_attack_component", None),
            ("Illaoi", "TentacleAttack"): ("champion_attack_component", None),
            ("Malzahar", "Voidling"): ("champion_spell_component", "w"),
            ("MissFortune", "MissFortuneBulletEMPTY"): ("generic_sfx_effect", None),
            ("Samira", "SheathGun"): ("generic_sfx_effect", None),
            ("Sett", "Recallin"): ("champion_action_component", None),
        }
        for (owner, token), (kind, spell) in expected.items():
            with self.subTest(owner=owner, token=token):
                alias = book.contextual_alias_for("champion", owner, token)
                self.assertIsNotNone(alias)
                assert alias is not None
                self.assertEqual(alias.kind, kind)
                self.assertEqual(alias.spell, spell)

        passive_attack_rules = {
            alias.token
            for alias in book.contextual_aliases.values()
            if alias.kind == "champion_attack_component" and alias.related_spell == "p"
        }
        self.assertEqual(passive_attack_rules, {"PackmateBasicAttack"})

    def test_duplicate_fragment_key_raises(self) -> None:
        """不同分片定义同一 key 应报错。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            (rules / "contextual" / "champions").mkdir(parents=True)
            overrides.mkdir()
            self.write_minimal_rule_files(rules, overrides)
            payload = "\n".join(
                [
                    "scope: champion",
                    "owner: Gangplank",
                    "aliases:",
                    "  CannonBarrage:",
                    "    kind: champion_spell",
                    "    label: Cannon Barrage",
                    "",
                ]
            )
            (rules / "contextual" / "champions" / "Gangplank.yaml").write_text(payload, encoding="utf-8")
            (rules / "contextual" / "champions" / "GangplankDuplicate.yaml").write_text(payload, encoding="utf-8")

            with self.assertRaises(ValueError):
                RuleBook.load(rules, overrides)

    def test_equivalent_legacy_and_fragment_rule_prefers_fragment_source_path(self) -> None:
        """旧单文件与等价分片共存时语义不变，并记录分片路径。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            (rules / "contextual" / "champions").mkdir(parents=True)
            overrides.mkdir()
            self.write_minimal_rule_files(
                rules,
                overrides,
                contextual_aliases="\n".join(
                    [
                        "champions:",
                        "  Gangplank:",
                        "    CannonBarrage:",
                        "      kind: champion_spell",
                        "      spell: r",
                        "      label: 加农炮幕",
                        "modes: {}",
                        "sfx: {}",
                        "",
                    ]
                ),
            )
            (rules / "contextual" / "champions" / "Gangplank.yaml").write_text(
                "\n".join(
                    [
                        "scope: champion",
                        "owner: Gangplank",
                        "aliases:",
                        "  CannonBarrage:",
                        "    kind: champion_spell",
                        "    spell: r",
                        "    label: 加农炮幕",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            book = RuleBook.load(rules, overrides)

            alias = book.contextual_alias_for("champion", "Gangplank", "CannonBarrage")
            self.assertIsNotNone(alias)
            self.assertEqual(alias.to_dict()["label"], "加农炮幕")
            self.assertEqual(alias.source_path, "rules/contextual/champions/Gangplank.yaml")

    def test_non_equivalent_legacy_and_fragment_rule_raises(self) -> None:
        """旧单文件与分片同 key 不同内容时应报错。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            (rules / "contextual" / "champions").mkdir(parents=True)
            overrides.mkdir()
            self.write_minimal_rule_files(
                rules,
                overrides,
                contextual_aliases="\n".join(
                    [
                        "champions:",
                        "  Gangplank:",
                        "    CannonBarrage:",
                        "      kind: champion_spell",
                        "      spell: r",
                        "      label: 加农炮幕",
                        "modes: {}",
                        "sfx: {}",
                        "",
                    ]
                ),
            )
            (rules / "contextual" / "champions" / "Gangplank.yaml").write_text(
                "\n".join(
                    [
                        "scope: champion",
                        "owner: Gangplank",
                        "aliases:",
                        "  CannonBarrage:",
                        "    kind: champion_spell",
                        "    spell: r",
                        "    label: 不同标签",
                        "",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                RuleBook.load(rules, overrides)

    def test_repository_fragments_are_equivalent_to_legacy_file(self) -> None:
        """仓库当前分片应与旧单文件语义等价。"""

        repo_root = Path(__file__).resolve().parents[1]

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            shutil.copytree(repo_root / "rules", rules)
            shutil.copytree(repo_root / "overrides", overrides)
            shutil.rmtree(rules / "contextual")

            legacy_book = RuleBook.load(rules, overrides)

        combined_book = RuleBook.load(repo_root / "rules", repo_root / "overrides")

        self.assertEqual(sorted(legacy_book.contextual_aliases), sorted(combined_book.contextual_aliases))

        for key, legacy_alias in legacy_book.contextual_aliases.items():
            self.assertEqual(
                contextual_alias_payload(legacy_alias),
                contextual_alias_payload(combined_book.contextual_aliases[key]),
            )

    def write_minimal_rule_files(
        self,
        rules: Path,
        overrides: Path,
        *,
        contextual_aliases: str = "champions: {}\nmodes: {}\nsfx: {}\n",
    ) -> None:
        """写入 RuleBook.load 需要的最小文件。"""

        (rules / "normalization.yaml").write_text("canonical_tokens: {}\nword_labels: {}\n", encoding="utf-8")
        (rules / "phases.yaml").write_text("phases: {}\nmodifiers: {}\n", encoding="utf-8")
        (rules / "map_actions.yaml").write_text("actions: {}\ntoken_labels: {}\n", encoding="utf-8")
        (rules / "action_aliases.yaml").write_text("global: {}\nchampions: {}\n", encoding="utf-8")
        (rules / "contextual_aliases.yaml").write_text(contextual_aliases, encoding="utf-8")
        (rules / "residual_modifiers.yaml").write_text("modifiers: {}\n", encoding="utf-8")
        (rules / "external_entities.yaml").write_text("entities: {}\n", encoding="utf-8")
        (overrides / "legacy_aliases.yaml").write_text("aliases: {}\n", encoding="utf-8")


class SpellMatchConstraintRuleTests(unittest.TestCase):
    """验证可信技能 residual 约束加载。"""

    def test_trusted_spell_residuals_load_and_query(self) -> None:
        """可信 residual 必须按英雄、槽位和完整 residual 精确命中。"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rules = root / "rules"
            overrides = root / "overrides"
            rules.mkdir()
            overrides.mkdir()
            RuleFragmentTests().write_minimal_rule_files(rules, overrides)
            (rules / "spell_match_constraints.yaml").write_text(
                "trusted_single_key_residuals:\n  Rengar:\n    q:\n      emp:\n        note: 强化 Q。\n",
                encoding="utf-8",
            )

            book = RuleBook.load(rules, overrides)

            entry = book.trusted_spell_residual_for("Rengar", "q", "Emp")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.note, "强化 Q。")
            self.assertIsNone(book.trusted_spell_residual_for("Rengar", "q", "Execute"))
            self.assertIsNone(book.trusted_spell_residual_for("Renekton", "e", "xecute"))


if __name__ == "__main__":
    unittest.main()
