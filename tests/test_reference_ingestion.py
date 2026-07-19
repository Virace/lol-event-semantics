"""本地数据源导入 reference 合集测试。"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lol_event_semantics.reference_collection import (
    ReferenceCollection,
    ReferenceEntityInput,
    load_reference_collection,
)
from lol_event_semantics.reference_ingestion import (
    import_hero_list,
    import_local_reference,
    import_nickname_overrides,
)


class ReferenceIngestionTests(unittest.TestCase):
    """验证 GAME data 与 Data Dragon 本地源导入。"""

    def test_import_local_sources_writes_internal_reference(self) -> None:
        """本地双来源导入后只写内部精简合集。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            reference_root = root / "reference"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)

            collection = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )
            collection.write()

            champion = (reference_root / "champions" / "Annie.yaml").read_text(encoding="utf-8")
            map_data = (reference_root / "maps" / "SummonersRift.yaml").read_text(encoding="utf-8")
            skinline = (reference_root / "skinlines" / "Skinline19.yaml").read_text(encoding="utf-8")
            skin = (reference_root / "skins" / "Galio" / "Skin13.yaml").read_text(encoding="utf-8")
            manifest = (reference_root / "manifest.yaml").read_text(encoding="utf-8")

        self.assertIn("alias: Annie", champion)
        self.assertIn("value: 安妮", champion)
        self.assertIn("value: AnnieQ", champion)
        self.assertIn("value: Disintegrate", champion)
        self.assertIn("value: 碎裂之火", champion)
        self.assertIn("summoner", map_data.lower())
        self.assertIn("Star Guardian Season 1", skinline)
        self.assertIn("星之守护者", skinline)
        self.assertIn("alias: GalioSkin13", skin)
        self.assertIn("value: Infernal Galio", skin)
        self.assertIn("value: 地狱火 加里奥", skin)
        self.assertIn("source: data_dragon", skin)
        self.assertIn("game_data:", manifest)
        self.assertIn("data_dragon:", manifest)
        self.assertIn("skinlines: 1", manifest)
        self.assertIn("skins: 1", manifest)
        self.assertFalse((reference_root / "16.12").exists())

    def test_repeated_import_keeps_reference_clean(self) -> None:
        """重复导入同一批本地 source 不应因为描述版本顺序产生 dirty。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            reference_root = root / "reference"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)

            first = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )
            first.write()
            second = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )

        self.assertFalse(second._dirty)

    def test_data_dragon_main_version_must_match_game_version(self) -> None:
        """Data Dragon 必须和 GAME 主版本对齐。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root, version="16.13.1")

            with self.assertRaisesRegex(ValueError, "main version"):
                import_local_reference(
                    reference_root=root / "reference",
                    game_data_root=game_data_root,
                    data_dragon_root=data_dragon_root,
                    game_version="16.12",
                    data_dragon_version="16.13.1",
                )

    def test_import_allows_missing_non_default_game_data_locale(self) -> None:
        """缺失非默认 GAME data 语言时仍可用 Data Dragon 补齐。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            reference_root = root / "reference"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)
            shutil.rmtree(game_data_root / "global" / "zh_cn")

            collection = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )
            collection.write()

            champion = (reference_root / "champions" / "Annie.yaml").read_text(encoding="utf-8")

        self.assertIn("value: 安妮", champion)
        self.assertIn("value: 碎裂之火", champion)

    def test_import_allows_missing_game_data_detail(self) -> None:
        """缺失 GAME data 英雄详情时仍可用 Data Dragon 补齐。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            reference_root = root / "reference"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)
            (game_data_root / "global" / "default" / "v1" / "champions" / "1.json").unlink()

            collection = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )
            collection.write()

            champion = (reference_root / "champions" / "Annie.yaml").read_text(encoding="utf-8")

        self.assertIn("value: AnnieQ", champion)
        self.assertIn("value: Disintegrate", champion)

    def test_zh_champion_name_routes_to_proper_name_and_title(self) -> None:
        """中文 name 进称号、title 进俗名，英文本名不变。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            game_data_root = root / "game-data"
            data_dragon_root = root / "dragontail"
            reference_root = root / "reference"
            write_game_data_fixture(game_data_root)
            write_data_dragon_fixture(data_dragon_root)

            collection = import_local_reference(
                reference_root=reference_root,
                game_data_root=game_data_root,
                data_dragon_root=data_dragon_root,
                game_version="16.12",
                data_dragon_version="16.12.1",
                languages=("en_US", "zh_CN"),
            )
            collection.write()
            entity = load_reference_collection(reference_root).champions["Annie"]["entity"]

        self.assertEqual([item["value"] for item in entity["names"]["zh_CN"]], ["安妮"])
        self.assertEqual([item["value"] for item in entity["names"]["en_US"]], ["Annie"])
        self.assertIn("黑暗之女", [item["value"] for item in entity["title"]["zh_CN"]])
        title_sources = {
            source["source"]
            for item in entity["title"]["zh_CN"]
            if item["value"] == "黑暗之女"
            for source in item["sources"]
        }
        self.assertEqual(title_sources, {"data_dragon", "game_data"})

    def test_import_hero_list_joins_by_id_despite_alias_casing(self) -> None:
        """hero_list 按 heroId join 已存在英雄，alias 大小写漂移不创建重复实体。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_root = root / "reference"
            hero_list_root = root / "hero-list"
            collection = ReferenceCollection(reference_root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=9,
                    alias="Fiddlesticks",
                    names={"en_US": "Fiddlesticks"},
                    source="data_dragon",
                    version="16.5.1",
                )
            )
            write_hero_list_fixture(hero_list_root)

            import_hero_list(collection, hero_list_root)
            collection.write()
            loaded = load_reference_collection(reference_root)
            entity = loaded.champions["Fiddlesticks"]["entity"]

        self.assertEqual(list(loaded.champions), ["Fiddlesticks"])
        self.assertEqual(entity["alias"], "Fiddlesticks")
        self.assertEqual([item["value"] for item in entity["names"]["zh_CN"]], ["费德提克"])
        self.assertIn("远古恐惧", [item["value"] for item in entity["title"]["zh_CN"]])
        self.assertEqual([item["value"] for item in entity["nicknames"]["zh_CN"]], ["稻草人"])
        self.assertIn("daocaoren", [item["value"] for item in entity["search_alias"]["zh_CN"]])

    def test_nickname_overrides_merge_with_official_and_skip_unknown(self) -> None:
        """人工俗称兜底并入已存在英雄并与官方俗称去重合并，未知英雄跳过。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference_root = root / "reference"
            overrides_root = root / "overrides"
            collection = ReferenceCollection(reference_root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=39,
                    alias="Irelia",
                    names={"en_US": "Irelia", "zh_CN": "艾瑞莉娅"},
                    nicknames={"zh_CN": ["女刀"]},
                    source="tencent_101",
                    version="16.13",
                )
            )
            overrides_root.mkdir(parents=True)
            (overrides_root / "champion_nicknames.yaml").write_text(
                "version: curated\n"
                "nicknames:\n"
                "  Irelia:\n"
                "    zh_CN: [刀妹, 女刀, null]\n"
                "  Nonexistent:\n"
                "    zh_CN: [无关]\n",
                encoding="utf-8",
            )

            import_nickname_overrides(collection, overrides_root)
            collection.write()
            self.assertIn("override", collection.source_versions)
            entity = load_reference_collection(reference_root).champions["Irelia"]["entity"]

        nicknames = entity["nicknames"]["zh_CN"]
        self.assertEqual([item["value"] for item in nicknames], ["刀妹", "女刀"])
        self.assertNotIn("None", [item["value"] for item in nicknames])
        女刀 = next(item for item in nicknames if item["value"] == "女刀")
        self.assertEqual(
            [source["source"] for source in 女刀["sources"]],
            ["override", "tencent_101"],
        )
        刀妹 = next(item for item in nicknames if item["value"] == "刀妹")
        self.assertEqual([source["source"] for source in 刀妹["sources"]], ["override"])

    def test_import_reference_data_help(self) -> None:
        """导入脚本提供本地模式入口。"""

        result = subprocess.run(
            [sys.executable, "scripts/import_reference_data.py", "--help"],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--data-dragon-root", result.stdout)
        self.assertIn("--hero-list-root", result.stdout)
        self.assertIn("--overrides-root", result.stdout)
        self.assertIn("--online", result.stdout)
        self.assertIn("--cache-root", result.stdout)
        self.assertNotIn("--dragontail-root", result.stdout)

    def test_import_reference_data_local_mode_requires_local_roots(self) -> None:
        """本地模式仍要求显式提供两个本地 source root。"""

        result = subprocess.run(
            [
                sys.executable,
                "scripts/import_reference_data.py",
                "--reference-root",
                "reference",
                "--game-version",
                "16.12",
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--game-data-root", result.stderr)


def write_game_data_fixture(root: Path) -> None:
    """写入最小 GAME data fixture。"""

    for locale, name, spell_name, map_name in [
        ("default", "Annie", "Disintegrate", "Summoner's Rift"),
        ("zh_cn", "安妮", "碎裂之火", "召唤师峡谷"),
    ]:
        base = root / "global" / locale / "v1"
        champions = base / "champions"
        champions.mkdir(parents=True)
        summary_rows = {
            "default": [
                {"id": 1, "alias": "Annie", "name": "Annie"},
                {"id": 3, "alias": "Galio", "name": "Galio"},
            ],
            "zh_cn": [
                {"id": 1, "alias": "Annie", "name": "黑暗之女"},
                {"id": 3, "alias": "Galio", "name": "正义巨像"},
            ],
        }[locale]
        (base / "champion-summary.json").write_text(
            json.dumps(summary_rows, ensure_ascii=False),
            encoding="utf-8",
        )
        (base / "maps.json").write_text(
            json.dumps([{"id": 11, "name": map_name, "mapStringId": "SummonersRift"}], ensure_ascii=False),
            encoding="utf-8",
        )
        (base / "skinlines.json").write_text(
            json.dumps(
                [
                    {
                        "id": 19,
                        "name": "Star Guardian Season 1" if locale == "default" else "星之守护者 第1季",
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (champions / "1.json").write_text(
            json.dumps(
                {
                    "id": 1,
                    "alias": "Annie",
                    "name": name,
                    "passive": {
                        "name": "Pyromania" if locale == "default" else "嗜火",
                        "description": "Passive text" if locale == "default" else "被动文本",
                    },
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": spell_name,
                            "description": "Annie hurls a fireball." if locale == "default" else "安妮扔出火球。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (champions / "3.json").write_text(
            json.dumps(
                {
                    "id": 3,
                    "alias": "Galio",
                    "name": "Galio" if locale == "default" else "加里奥",
                    "passive": {
                        "name": "Colossal Smash" if locale == "default" else "巨像重击",
                        "description": "Passive text" if locale == "default" else "被动文本",
                    },
                    "spells": [],
                    "skins": [
                        {
                            "id": 3013,
                            "name": "Infernal Galio" if locale == "default" else "地狱火 加里奥",
                            "skinType": "kEpic",
                            "isBase": False,
                            "splashPath": "/lol-game-data/assets/ASSETS/Characters/Galio/Skins/Skin13/Galio_Splash_13.jpg",
                            "uncenteredSplashPath": "/lol-game-data/assets/ASSETS/Characters/Galio/Skins/Skin13/Galio_Splash_Centered_13.jpg",
                            "tilePath": "/lol-game-data/assets/ASSETS/Characters/Galio/Skins/Skin13/Galio_Square_13.jpg",
                            "loadScreenPath": "/lol-game-data/assets/ASSETS/Characters/Galio/Skins/Skin13/Galio_LoadScreen_13.jpg",
                            "skinLines": [{"id": 8}],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


def write_hero_list_fixture(root: Path) -> None:
    """写入最小 hero_list fixture（heroId 字符串、title=俗名、name=称号）。"""

    payload = {
        "version": "16.13",
        "fileName": "hero_list.js",
        "hero": [
            {
                "heroId": "9",
                "alias": "FiddleSticks",  # 大小写与仓库 Fiddlesticks 不同
                "title": "费德提克",
                "name": "远古恐惧",
                "keywords": "稻草人,费德提克,远古恐惧,daocaoren,FiddleSticks,dcr",
            },
            {
                "heroId": "266",
                "alias": "Aatrox",  # 不在 reference 中，应被跳过
                "title": "亚托克斯",
                "name": "暗裔剑魔",
                "keywords": "剑魔,亚托克斯,暗裔剑魔,jianmo,Aatrox",
            },
        ],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "hero_list.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def write_data_dragon_fixture(root: Path, version: str = "16.12.1") -> None:
    """写入最小 Data Dragon fixture。"""

    for language, name, spell_name in [
        ("en_US", "Annie", "Disintegrate"),
        ("zh_CN", "安妮", "碎裂之火"),
    ]:
        base = root / version / "data" / language
        champion_root = base / "champion"
        champion_root.mkdir(parents=True)
        summary_data = {
            "en_US": {
                "Annie": {"id": "Annie", "key": "1", "name": "Annie", "title": "the Dark Child"},
                "Galio": {"id": "Galio", "key": "3", "name": "Galio", "title": "the Colossus"},
            },
            "zh_CN": {
                # 中文 DDragon 的 name=称号、title=俗名，与英文相反。
                "Annie": {"id": "Annie", "key": "1", "name": "黑暗之女", "title": "安妮"},
                "Galio": {"id": "Galio", "key": "3", "name": "正义巨像", "title": "加里奥"},
            },
        }[language]
        (base / "champion.json").write_text(
            json.dumps({"data": summary_data}, ensure_ascii=False),
            encoding="utf-8",
        )
        (champion_root / "Annie.json").write_text(
            json.dumps(
                {
                    "data": {
                        "Annie": {
                            "id": "Annie",
                            "key": "1",
                            "name": name,
                            "passive": {
                                "name": "Pyromania" if language == "en_US" else "嗜火",
                                "description": "Passive text" if language == "en_US" else "被动文本",
                            },
                            "spells": [
                                {
                                    "id": "AnnieQ",
                                    "name": spell_name,
                                    "description": "Annie hurls a fireball." if language == "en_US" else "安妮扔出火球。",
                                    "image": {"full": "AnnieQ.png"},
                                }
                            ],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        (champion_root / "Galio.json").write_text(
            json.dumps(
                {
                    "data": {
                        "Galio": {
                            "id": "Galio",
                            "key": "3",
                            "name": "Galio" if language == "en_US" else "正义巨像",
                            "skins": [
                                {"id": "3000", "num": 0, "name": "default"},
                                {
                                    "id": "3013",
                                    "num": 13,
                                    "name": "Infernal Galio" if language == "en_US" else "地狱火 加里奥",
                                },
                            ],
                            "passive": {
                                "name": "Colossal Smash" if language == "en_US" else "巨像重击",
                                "description": "Passive text" if language == "en_US" else "被动文本",
                            },
                            "spells": [],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
