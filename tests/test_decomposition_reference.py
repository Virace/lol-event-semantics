"""事件名拆解参考数据源测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from reference_cache import reference_index as shared_reference_index

from lol_event_semantics.decomposition_lexer import decompose_event_name
from lol_event_semantics.decomposition_model import SemanticProfile
from lol_event_semantics.decomposition_profiles import normalize_semantics
from lol_event_semantics.decomposition_reference import ReferenceIndex, enrich_with_reference
from lol_event_semantics.match_audit import MatchAuditRecorder, audit_context
from lol_event_semantics.reference_collection import (
    ReferenceCollection,
    ReferenceEntityInput,
    ReferenceMapInput,
    ReferenceSkinInput,
    ReferenceSpellInput,
    TextSnapshotInput,
)


class ReferenceIndexTests(unittest.TestCase):
    """验证外部参考数据索引。"""

    def make_game_data_root(self) -> Path:
        """构造最小 game-data fixture。"""

        root = Path(tempfile.mkdtemp())
        data_root = root / "global" / "default" / "v1"
        data_root.mkdir(parents=True)
        (data_root / "maps.json").write_text(
            json.dumps(
                [
                    {"id": 11, "name": "Summoner's Rift"},
                    {"id": 12, "name": "Howling Abyss"},
                ]
            ),
            encoding="utf-8",
        )
        (data_root / "skinlines.json").write_text(
            json.dumps(
                [
                    {"id": 19, "name": "Star Guardian Season 1"},
                    {"id": 44, "name": "Dark Star"},
                    {"id": 18, "name": "PROJECT"},
                ]
            ),
            encoding="utf-8",
        )
        (data_root / "champion-summary.json").write_text(
            json.dumps(
                [
                    {"id": 1, "alias": "Annie", "name": "Annie"},
                    {"id": 103, "alias": "Ahri", "name": "Ahri"},
                    {"id": 266, "alias": "Aatrox", "name": "Aatrox"},
                    {"id": 7, "alias": "Leblanc", "name": "LeBlanc"},
                    {"id": 63, "alias": "Brand", "name": "Brand"},
                    {"id": 166, "alias": "Akshan", "name": "Akshan"},
                    {"id": 86, "alias": "Garen", "name": "Garen"},
                    {"id": 55, "alias": "Katarina", "name": "Katarina"},
                    {"id": 99, "alias": "Lux", "name": "Lux"},
                    {"id": 22, "alias": "Ashe", "name": "Ashe"},
                    {"id": 119, "alias": "Draven", "name": "Draven"},
                    {"id": 122, "alias": "Darius", "name": "Darius"},
                    {"id": 131, "alias": "Diana", "name": "Diana"},
                    {"id": 60, "alias": "Elise", "name": "Elise"},
                    {"id": 105, "alias": "Fizz", "name": "Fizz"},
                    {"id": 120, "alias": "Hecarim", "name": "Hecarim"},
                    {"id": 420, "alias": "Illaoi", "name": "Illaoi"},
                    {"id": 43, "alias": "Karma", "name": "Karma"},
                    {"id": 145, "alias": "Kaisa", "name": "Kai'Sa"},
                    {"id": 42, "alias": "Corki", "name": "Corki"},
                    {"id": 29, "alias": "Twitch", "name": "Twitch"},
                    {"id": 104, "alias": "Graves", "name": "Graves"},
                    {"id": 54, "alias": "Malphite", "name": "Malphite"},
                    {"id": 432, "alias": "Bard", "name": "Bard"},
                    {"id": 68, "alias": "Rumble", "name": "Rumble"},
                    {"id": 37, "alias": "Sona", "name": "Sona"},
                    {"id": 67, "alias": "Vayne", "name": "Vayne"},
                    {"id": 101, "alias": "Xerath", "name": "Xerath"},
                    {"id": 2, "alias": "Olaf", "name": "Olaf"},
                    {"id": 58, "alias": "Renekton", "name": "Renekton"},
                    {"id": 268, "alias": "Azir", "name": "Azir"},
                    {"id": 203, "alias": "Kindred", "name": "Kindred"},
                    {"id": 111, "alias": "Nautilus", "name": "Nautilus"},
                    {"id": 85, "alias": "Kennen", "name": "Kennen"},
                    {"id": 92, "alias": "Riven", "name": "Riven"},
                    {"id": 89, "alias": "Leona", "name": "Leona"},
                    {"id": 48, "alias": "Trundle", "name": "Trundle"},
                    {"id": 35, "alias": "Shaco", "name": "Shaco"},
                    {"id": 141, "alias": "Kayn", "name": "Kayn"},
                    {"id": 28, "alias": "Evelynn", "name": "Evelynn"},
                    {"id": 429, "alias": "Kalista", "name": "Kalista"},
                    {"id": 893, "alias": "Aurora", "name": "Aurora"},
                    {"id": 20, "alias": "Nunu", "name": "Nunu & Willump"},
                    {"id": 4, "alias": "TwistedFate", "name": "Twisted Fate"},
                    {"id": 5, "alias": "XinZhao", "name": "Xin Zhao"},
                    {"id": 126, "alias": "Jayce", "name": "Jayce"},
                    {"id": 427, "alias": "Ivern", "name": "Ivern"},
                    {"id": 121, "alias": "Khazix", "name": "Kha'Zix"},
                    {"id": 90, "alias": "Malzahar", "name": "Malzahar"},
                    {"id": 74, "alias": "Heimerdinger", "name": "Heimerdinger"},
                    {"id": 114, "alias": "Fiora", "name": "Fiora"},
                    {"id": 6, "alias": "Urgot", "name": "Urgot"},
                    {"id": 64, "alias": "LeeSin", "name": "Lee Sin"},
                    {"id": 412, "alias": "Thresh", "name": "Thresh"},
                    {"id": 254, "alias": "Vi", "name": "Vi"},
                    {"id": 76, "alias": "Nidalee", "name": "Nidalee"},
                    {"id": 62, "alias": "MonkeyKing", "name": "Wukong"},
                    {"id": 41, "alias": "Gangplank", "name": "Gangplank"},
                    {"id": 555, "alias": "Pyke", "name": "Pyke"},
                    {"id": 21, "alias": "MissFortune", "name": "Miss Fortune"},
                    {"id": 75, "alias": "Nasus", "name": "Nasus"},
                    {"id": 202, "alias": "Jhin", "name": "Jhin"},
                    {"id": 27, "alias": "Singed", "name": "Singed"},
                    {"id": 11, "alias": "MasterYi", "name": "Master Yi"},
                    {"id": 117, "alias": "Lulu", "name": "Lulu"},
                    {"id": 30, "alias": "Karthus", "name": "Karthus"},
                    {"id": 102, "alias": "Shyvana", "name": "Shyvana"},
                    {"id": 61, "alias": "Orianna", "name": "Orianna"},
                    {"id": 516, "alias": "Ornn", "name": "Ornn"},
                    {"id": 142, "alias": "Zoe", "name": "Zoe"},
                    {"id": 157, "alias": "Yasuo", "name": "Yasuo"},
                    {"id": 238, "alias": "Zed", "name": "Zed"},
                    {"id": 136, "alias": "AurelionSol", "name": "Aurelion Sol"},
                    {"id": 523, "alias": "Aphelios", "name": "Aphelios"},
                    {"id": 800, "alias": "Mel", "name": "Mel"},
                    {"id": 38, "alias": "Kassadin", "name": "Kassadin"},
                    {"id": 235, "alias": "Senna", "name": "Senna"},
                    {"id": 497, "alias": "Rakan", "name": "Rakan"},
                    {"id": 360, "alias": "Samira", "name": "Samira"},
                    {"id": 234, "alias": "Viego", "name": "Viego"},
                    {"id": 950, "alias": "Naafiri", "name": "Naafiri"},
                    {"id": 26, "alias": "Zilean", "name": "Zilean"},
                    {"id": 24, "alias": "Jax", "name": "Jax"},
                    {"id": 498, "alias": "Xayah", "name": "Xayah"},
                    {"id": 34, "alias": "Anivia", "name": "Anivia"},
                    {"id": 16, "alias": "Soraka", "name": "Soraka"},
                    {"id": 31, "alias": "Chogath", "name": "Cho'Gath"},
                    {"id": 83, "alias": "Yorick", "name": "Yorick"},
                    {"id": 240, "alias": "Kled", "name": "Kled"},
                    {"id": 106, "alias": "Volibear", "name": "Volibear"},
                    {"id": 799, "alias": "Ambessa", "name": "Ambessa"},
                    {"id": 1050, "alias": "Skarner", "name": "Skarner"},
                    {"id": 134, "alias": "Syndra", "name": "Syndra"},
                    {"id": 154, "alias": "Zyra", "name": "Zyra"},
                    {"id": 161, "alias": "Velkoz", "name": "Vel'Koz"},
                    {"id": 110, "alias": "Varus", "name": "Varus"},
                    {"id": 57, "alias": "Maokai", "name": "Maokai"},
                    {"id": 526, "alias": "Rell", "name": "Rell"},
                    {"id": 115, "alias": "Ziggs", "name": "Ziggs"},
                    {"id": 40, "alias": "Janna", "name": "Janna"},
                    {"id": 876, "alias": "Lillia", "name": "Lillia"},
                    {"id": 127, "alias": "Lissandra", "name": "Lissandra"},
                    {"id": 236, "alias": "Lucian", "name": "Lucian"},
                    {"id": 98, "alias": "Shen", "name": "Shen"},
                ]
            ),
            encoding="utf-8",
        )
        champion_root = data_root / "champions"
        champion_root.mkdir()
        (champion_root / "99.json").write_text(
            json.dumps(
                {
                    "id": 99,
                    "alias": "Lux",
                    "name": "Lux",
                    "passive": {
                        "name": "Illumination",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Lux/HUD/Icons2D/LuxIlluminatingFraulein.png",
                    },
                    "skins": [
                        {"id": 99000, "name": "Lux", "isBase": True},
                        {"id": 99006, "name": "Star Guardian Lux", "isBase": False},
                    ],
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": "Light Binding",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Lux/HUD/Icons2D/LuxLightBinding.png",
                        },
                        {
                            "spellKey": "e",
                            "name": "Lucent Singularity",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Lux/HUD/Icons2D/LuxLightStrikeKugel.png",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "76.json").write_text(
            json.dumps(
                {
                    "id": 76,
                    "alias": "Nidalee",
                    "name": "Nidalee",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Aspect Of The Cougar",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Nidalee/HUD/Icons2D/Nidalee_R1.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "103.json").write_text(
            json.dumps(
                {
                    "id": 103,
                    "alias": "Ahri",
                    "name": "Ahri",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Spirit Rush",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Ahri/HUD/Icons2D/Ahri_R.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "41.json").write_text(
            json.dumps(
                {
                    "id": 41,
                    "alias": "Gangplank",
                    "name": "Gangplank",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Cannon Barrage",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Gangplank/HUD/Icons2D/Gangplank_R.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "142.json").write_text(
            json.dumps(
                {
                    "id": 142,
                    "alias": "Zoe",
                    "name": "Zoe",
                    "spells": [
                        {
                            "spellKey": "e",
                            "name": "Sleepy Trouble Bubble",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Zoe/HUD/Icons2D/Zoe_E.png",
                            "description": "Zoe causes the target to become drowsy, then sleep after a countdown.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "157.json").write_text(
            json.dumps(
                {
                    "id": 157,
                    "alias": "Yasuo",
                    "name": "Yasuo",
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": "Steel Tempest",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Yasuo/HUD/Icons2D/Yasuo_Q1.png",
                        },
                        {
                            "spellKey": "w",
                            "name": "Wind Wall",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Yasuo/HUD/Icons2D/Yasuo_W.png",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "136.json").write_text(
            json.dumps(
                {
                    "id": 136,
                    "alias": "AurelionSol",
                    "name": "Aurelion Sol",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Falling Star / The Skies Descend",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/AurelionSol/HUD/Icons2D/AurelionSol_R.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "38.json").write_text(
            json.dumps(
                {
                    "id": 38,
                    "alias": "Kassadin",
                    "name": "Kassadin",
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": "Null Sphere",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Kassadin/HUD/Icons2D/Kassadin_Q.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        dragontail_root = root / "dragontail" / "16.12.1" / "data" / "en_US"
        dragontail_champion_root = dragontail_root / "champion"
        dragontail_champion_root.mkdir(parents=True)
        (dragontail_champion_root / "Kassadin.json").write_text(
            json.dumps(
                {
                    "type": "champion",
                    "format": "standAloneComplex",
                    "data": {
                        "Kassadin": {
                            "id": "Kassadin",
                            "key": "38",
                            "name": "Kassadin",
                            "spells": [
                                {
                                    "id": "NullLance",
                                    "name": "Null Sphere",
                                }
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (dragontail_champion_root / "TwistedFate.json").write_text(
            json.dumps(
                {
                    "type": "champion",
                    "format": "standAloneComplex",
                    "data": {
                        "TwistedFate": {
                            "id": "TwistedFate",
                            "key": "4",
                            "name": "Twisted Fate",
                            "spells": [
                                {"id": "WildCards", "name": "Wild Cards"},
                                {"id": "PickACard", "name": "Pick a Card"},
                                {"id": "CardmasterStack", "name": "Stacked Deck"},
                                {"id": "Destiny", "name": "Destiny"},
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (dragontail_champion_root / "Amumu.json").write_text(
            json.dumps(
                {
                    "type": "champion",
                    "format": "standAloneComplex",
                    "data": {
                        "Amumu": {
                            "id": "Amumu",
                            "key": "32",
                            "name": "Amumu",
                            "spells": [
                                {"id": "BandageToss", "name": "Bandage Toss"},
                                {"id": "AuraofDespair", "name": "Despair"},
                                {"id": "Tantrum", "name": "Tantrum"},
                                {"id": "CurseoftheSadMummy", "name": "Curse of the Sad Mummy"},
                            ],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "254.json").write_text(
            json.dumps(
                {
                    "id": 254,
                    "alias": "Vi",
                    "name": "Vi",
                    "spells": [
                        {
                            "spellKey": "e",
                            "name": "Relentless Force",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Vi/HUD/Icons2D/ViE.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "22.json").write_text(
            json.dumps(
                {
                    "id": 22,
                    "alias": "Ashe",
                    "name": "Ashe",
                    "spells": [
                        {
                            "spellKey": "w",
                            "name": "Volley",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Ashe/HUD/Icons2D/Volley.png",
                        },
                        {
                            "spellKey": "e",
                            "name": "Hawkshot",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Ashe/HUD/Icons2D/Ashe_E.png",
                            "description": "Ashe sends her Hawk Spirit on a scouting mission anywhere on the map.",
                        },
                        {
                            "spellKey": "r",
                            "name": "Enchanted Crystal Arrow",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Ashe/HUD/Icons2D/EnchantedCrystalArrow.png",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "20.json").write_text(
            json.dumps(
                {
                    "id": 20,
                    "alias": "Nunu",
                    "name": "Nunu & Willump",
                    "spells": [
                        {
                            "spellKey": "w",
                            "name": "Biggest Snowball Ever!",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Nunu/HUD/Icons2D/NunuW.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "122.json").write_text(
            json.dumps(
                {
                    "id": 122,
                    "alias": "Darius",
                    "name": "Darius",
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": "Decimate",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Darius/HUD/Icons2D/DariusQ.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "6.json").write_text(
            json.dumps(
                {
                    "id": 6,
                    "alias": "Urgot",
                    "name": "Urgot",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Fear Beyond Death",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Urgot/HUD/Icons2D/Urgot_R.png",
                            "dynamicDescription": "Urgot can <recast>Recast</recast> this Ability, suppressing the victim and dragging them to himself.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "4.json").write_text(
            json.dumps(
                {
                    "id": 4,
                    "alias": "TwistedFate",
                    "name": "Twisted Fate",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Destiny",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/TwistedFate/HUD/Icons2D/TwistedFate_R.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "126.json").write_text(
            json.dumps(
                {
                    "id": 126,
                    "alias": "Jayce",
                    "name": "Jayce",
                    "spells": [
                        {
                            "spellKey": "e",
                            "name": "Thundering Blow / Acceleration Gate",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Jayce/HUD/Icons2D/Jayce_E.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "64.json").write_text(
            json.dumps(
                {
                    "id": 64,
                    "alias": "LeeSin",
                    "name": "Lee Sin",
                    "spells": [
                        {
                            "spellKey": "q",
                            "name": "Sonic Wave / Resonating Strike",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/LeeSin/HUD/Icons2D/LeeSinQ1.png",
                            "dynamicDescription": "Lee Sin may recast Resonating Strike after Sonic Wave hits.",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "412.json").write_text(
            json.dumps(
                {
                    "id": 412,
                    "alias": "Thresh",
                    "name": "Thresh",
                    "passive": {
                        "name": "Damnation",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Thresh/HUD/Icons2D/Thresh_Passive.png",
                        "description": "Thresh can harvest the souls of enemies that die near him.",
                    },
                    "spells": [],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "114.json").write_text(
            json.dumps(
                {
                    "id": 114,
                    "alias": "Fiora",
                    "name": "Fiora",
                    "spells": [
                        {
                            "spellKey": "r",
                            "name": "Grand Challenge",
                            "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Fiora/HUD/Icons2D/FioraR.png",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        # 末句 "The extra basic attack can also crit." 为测试构造，真实描述无 crit 一词；
        # 仅用于让 CritAttack 走 description_keywords 门路径（test_masteryi_crit_attack_*），勿与 reference 同步。
        (champion_root / "11.json").write_text(
            json.dumps(
                {
                    "id": 11,
                    "alias": "MasterYi",
                    "name": "Master Yi",
                    "passive": {
                        "name": "Double Strike",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/MasterYi/HUD/Icons2D/MasterYi_Passive1.png",
                        "description": "Every few basic attacks, Master Yi strikes twice. The extra basic attack can also crit.",
                    },
                    "spells": [],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "266.json").write_text(
            json.dumps(
                {
                    "id": 266,
                    "alias": "Aatrox",
                    "name": "Aatrox",
                    "passive": {
                        "name": "Deathbringer Stance",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Aatrox/HUD/Icons2D/Aatrox_Passive.png",
                        # 逐字摘自 reference/champions/Aatrox.yaml 的 en_US 描述快照
                        "description": "Periodically, Aatrox's next basic attack deals bonus <magicDamage>magic damage</magicDamage> and heals him, based on the target's max health.",
                    },
                    "spells": [],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "202.json").write_text(
            json.dumps(
                {
                    "id": 202,
                    "alias": "Jhin",
                    "name": "Jhin",
                    "passive": {
                        "name": "Whisper",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Jhin/HUD/Icons2D/Jhin_P.png",
                        "description": "Jhin's hand cannon is a precise instrument. It fires at a fixed rate and holds 4 shots.",
                    },
                    "spells": [],
                }
            ),
            encoding="utf-8",
        )
        (champion_root / "27.json").write_text(
            json.dumps(
                {
                    "id": 27,
                    "alias": "Singed",
                    "name": "Singed",
                    "passive": {
                        "name": "Noxious Slipstream",
                        "abilityIconPath": "/lol-game-data/assets/ASSETS/Characters/Singed/HUD/Icons2D/Singed_Passive.png",
                        "description": "Singed drafts off nearby champions, gaining a burst of movement speed.",
                    },
                    "spells": [],
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_resolves_map_id_token(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        entity = index.resolve_token("Map12", raw_event_name="Play_sfx_Env_Map12_Ambience")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "map")
        self.assertEqual(entity.id, 12)
        self.assertEqual(entity.label, "Howling Abyss")

    def test_from_reference_collection_resolves_map_champion_and_spell(self) -> None:
        """内部 reference 合集可构建解释索引。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=38,
                    alias="Kassadin",
                    names={"en_US": "Kassadin"},
                    source="game_data",
                    version="16.5",
                )
            )
            collection.merge_spell(
                "Kassadin",
                ReferenceSpellInput(
                    slot="Q",
                    id="NullLance",
                    names={"en_US": "Null Sphere"},
                    source="data_dragon",
                    version="16.5.1",
                ),
            )
            collection.merge_description(
                "Kassadin",
                "Q",
                TextSnapshotInput(
                    language="en_US",
                    value="Kassadin fires an orb of void energy.",
                    source="data_dragon",
                    version="16.5.1",
                ),
            )
            collection.merge_map(
                ReferenceMapInput(
                    id=12,
                    alias="HowlingAbyss",
                    names={"en_US": "Howling Abyss"},
                    source="game_data",
                    version="16.5",
                )
            )
            collection.merge_skinline(
                ReferenceEntityInput(
                    id=19,
                    alias="Skinline19",
                    names={"en_US": "Star Guardian Season 1"},
                    source="game_data",
                    version="16.5",
                )
            )
            collection.write()

            index = ReferenceIndex.from_reference_collection(root)

        self.assertEqual(index.maps_by_id[12].label, "Howling Abyss")
        self.assertEqual(index.champions_by_id[38].alias, "Kassadin")
        skinline = index.resolve_token("StarGuardian")
        self.assertIsNotNone(skinline)
        self.assertEqual(skinline.kind, "skinline")
        self.assertEqual(skinline.source, "reference/skinlines")
        spell = index.champion_spells_by_alias["kassadin"][0]
        self.assertEqual(spell.spell_key, "q")
        self.assertEqual(spell.terms["nulllance"], "reference_spell_id")

    def test_base_champion_precedes_mode_variant_name(self) -> None:
        """模式变体的同名展示标签不得覆盖基础英雄。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_champion(
                ReferenceEntityInput(
                    id=90022,
                    alias="Ashe",
                    names={"en_US": "Ashe"},
                    source="game_data",
                    version="16.16",
                )
            )
            collection.merge_champion(
                ReferenceEntityInput(
                    id=1,
                    alias="Jade_Ashe",
                    names={"en_US": "Ashe"},
                    source="game_data",
                    version="16.16",
                )
            )
            collection.merge_champion(
                ReferenceEntityInput(
                    id=90062,
                    alias="MonkeyKing",
                    names={"en_US": "Wukong"},
                    source="game_data",
                    version="16.16",
                )
            )
            collection.merge_champion(
                ReferenceEntityInput(
                    id=2,
                    alias="Jade_Wukong",
                    names={"en_US": "Wukong"},
                    source="game_data",
                    version="16.16",
                )
            )
            collection.write()

            index = ReferenceIndex.from_reference_collection(root)
            ashe = index.resolve_token("Ashe")
            jade_ashe = index.resolve_token("Jade_Ashe")
            wukong = index.resolve_token("Wukong")
            jade_wukong = index.resolve_token("Jade_Wukong")

        self.assertIsNotNone(ashe)
        self.assertEqual(ashe.alias, "Ashe")
        self.assertIsNotNone(jade_ashe)
        self.assertEqual(jade_ashe.alias, "Jade_Ashe")
        self.assertIsNotNone(wukong)
        self.assertEqual(wukong.alias, "MonkeyKing")
        self.assertIsNotNone(jade_wukong)
        self.assertEqual(jade_wukong.alias, "Jade_Wukong")

    def test_reference_collection_resolves_champion_skin_token(self) -> None:
        """reference collection 中的具体皮肤可按 GalioSkin13 解析。"""

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "reference"
            collection = ReferenceCollection(root)
            collection.merge_skin(
                ReferenceSkinInput(
                    champion_alias="Galio",
                    champion_id=3,
                    skin_number=13,
                    skin_token="Skin13",
                    id=3013,
                    alias="GalioSkin13",
                    names={"en_US": "Infernal Galio", "zh_CN": "地狱火 加里奥"},
                    skinlines=[{"id": 8}],
                    source="game_data",
                    version="16.5",
                )
            )
            collection.write()

            index = ReferenceIndex.from_reference_collection(root)
            entity = index.resolve_token("GalioSkin13")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "skin")
        self.assertEqual(entity.alias, "GalioSkin13")
        self.assertEqual(entity.label, "Infernal Galio")
        self.assertEqual(entity.source, "reference/skins")
        self.assertEqual(entity.metadata["champion_alias"], "Galio")
        self.assertEqual(entity.metadata["skin_token"], "Skin13")

    def test_resolves_missing_map_id_as_fallback_map(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        entity = index.resolve_token("Map14", raw_event_name="Play_sfx_Env_Map14_Ambience")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "map")
        self.assertEqual(entity.id, 14)
        self.assertEqual(entity.source, "map_id_fallback")
        self.assertEqual(entity.metadata["trust"], "missing_current_reference")

    def test_resolves_champion_alias(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        entity = index.resolve_token("Garen", raw_event_name="Play_vo_AatroxSkin01_FirstEncounter3DGaren")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "champion")
        self.assertEqual(entity.label, "Garen")

    def test_resolves_champion_display_name_alias(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        entity = index.resolve_token("Wukong", raw_event_name="Play_sfx_Ruby_Wukong_Dance3D_buffactivate")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "champion")
        self.assertEqual(entity.alias, "MonkeyKing")
        self.assertEqual(entity.label, "Wukong")

    def test_resolves_missing_skin_number_as_untrusted_fallback(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        entity = index.resolve_token("ThreshSkin19", raw_event_name="Play_vo_AsheSkin76_FirstEncounter3DThreshSkin19")

        self.assertIsNotNone(entity)
        self.assertEqual(entity.kind, "skin")
        self.assertEqual(entity.source, "skin_token_fallback")
        self.assertEqual(entity.metadata["champion_alias"], "Thresh")
        self.assertEqual(entity.metadata["skin_number"], 19)
        self.assertEqual(entity.metadata["skin_token"], "Skin19")
        self.assertEqual(entity.metadata["trust"], "missing_current_reference")

    def test_resolves_skin_number_with_short_or_misspelled_champion_alias(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        yi = index.resolve_token("YiSkin09", raw_event_name="Play_vo_CamilleSkin01_Kill3DYiSkin09")
        shyvanna = index.resolve_token("ShyvannaSkin08", raw_event_name="Play_vo_VayneSkin25_Taunt3DShyvannaSkin08")

        self.assertIsNotNone(yi)
        self.assertEqual(yi.metadata["champion_alias"], "MasterYi")
        self.assertEqual(yi.metadata["skin_token"], "Skin09")
        self.assertIsNotNone(shyvanna)
        self.assertEqual(shyvanna.metadata["champion_alias"], "Shyvana")
        self.assertEqual(shyvanna.metadata["skin_token"], "Skin08")

    def test_resolves_legacy_and_skin_variants_to_base_champion(self) -> None:
        root = self.make_game_data_root()
        index = ReferenceIndex.from_game_data_root(root)

        cases = [
            ("Oriana", "Orianna"),
            ("HeimerTYellow", "Heimerdinger"),
            ("HeimerTBlue", "Heimerdinger"),
            ("BattlecastChogath", "Chogath"),
            ("FireworksCorki", "Corki"),
            ("PunkTwitch", "Twitch"),
        ]

        for token, expected in cases:
            with self.subTest(token=token):
                entity = index.resolve_token(token, raw_event_name=f"Play_sfx_Test_{token}")

                self.assertIsNotNone(entity)
                self.assertEqual(entity.kind, "champion")
                self.assertEqual(entity.alias, expected)


class ReferenceEnrichmentTests(unittest.TestCase):
    """验证参考数据能补充整句级候选解释。"""

    def setUp(self) -> None:
        """准备最小参考索引。"""

        root = ReferenceIndexTests().make_game_data_root()
        self.index = ReferenceIndex.from_game_data_root(
            root,
            dragontail_root=root / "dragontail" / "16.12.1" / "data" / "en_US",
        )

    def enrich(self, raw: str):
        """拆解、归一化并接入参考数据。"""

        record = normalize_semantics(decompose_event_name(raw, game_version="16.5"))
        return enrich_with_reference(record, self.index)

    def test_map12_becomes_map_or_system_profile(self) -> None:
        record = self.enrich("Play_sfx_Env_Map12_Ambience")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["map"]["id"], 12)
        self.assertEqual(record.semantic_slots["map"]["label"], "Howling Abyss")
        self.assertTrue(record.candidates)

    def test_announcer_gender_token_gets_candidate(self) -> None:
        record = self.enrich("Play_vo_Announcer_Female1_AceBlue")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["announcer"]["gender"], "female")
        self.assertEqual(record.semantic_slots["announcer"]["variant"], 1)

    def test_announcer_persona_uses_manual_external_entity(self) -> None:
        record = self.enrich("Play_vo_Announcer_Ringcaller_BuyAugmentYou")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["reference_entity"]["kind"], "announcer_persona")
        self.assertEqual(record.semantic_slots["reference_entity"]["label"], "Ringcaller")
        self.assertEqual(record.semantic_slots["reference_entity"]["metadata"]["trust"], "manual_untrusted")

    def test_skinline_from_reference_data_resolves_ambiguous_entity(self) -> None:
        record = self.enrich("Play_vo_AhriSkin14_Move2DFirstAllyStarGuardian")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "skinline")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Star Guardian Season 1")
        self.assertEqual(record.semantic_slots["counterpart"]["source"], "skinlines.json")
        self.assertFalse(any(unknown.raw == "StarGuardian" for unknown in record.unknowns))

    def test_external_manual_entity_resolves_region(self) -> None:
        record = self.enrich("Play_vo_AkaliSkin14_Attack2DNoxus")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "region")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Noxus")
        self.assertEqual(record.semantic_slots["counterpart"]["source"], "rules/external_entities.yaml")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["trust"], "manual_untrusted")

    def test_manual_map_context_resolves_howling_abyss_target(self) -> None:
        record = self.enrich("Play_vo_CamilleSkin01_Move2DFirstHowlingAbyss")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "map_or_location")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Howling Abyss")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["trust"], "manual_untrusted")
        self.assertFalse(any(unknown.raw == "HowlingAbyss" for unknown in record.unknowns))

    def test_manual_target_class_resolves_epic_monster(self) -> None:
        record = self.enrich("Play_vo_Kayn_Attack2DEpicMonster")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "target_class")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Epic Monster")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["trust"], "manual_untrusted")
        self.assertFalse(any(unknown.raw == "EpicMonster" for unknown in record.unknowns))

    def test_manual_skinline_context_resolves_pajama_guardian(self) -> None:
        record = self.enrich("Play_vo_CaitlynSkin22_FirstEncounter3DPajamaGuardian")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "skinline")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Pajama Guardian")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["trust"], "manual_untrusted")
        self.assertFalse(any(unknown.raw == "PajamaGuardian" for unknown in record.unknowns))

    def test_champion_group_counterpart_is_split_from_full_token(self) -> None:
        record = self.enrich("Play_vo_Pantheon_FirstEncounter3DGarenOrKatarina")

        counterpart = record.semantic_slots["counterpart"]
        self.assertEqual(counterpart["kind"], "champion_group")
        self.assertEqual(counterpart["connector"], "or")
        self.assertEqual([member["alias"] for member in counterpart["members"]], ["Garen", "Katarina"])
        self.assertFalse(any(unknown.raw == "GarenOrKatarina" for unknown in record.unknowns))

    def test_relation_prefixed_counterpart_splits_relation_and_champion(self) -> None:
        record = self.enrich("Play_vo_Kayn_Taunt3DAllyAatrox")

        counterpart = record.semantic_slots["counterpart"]
        self.assertEqual(counterpart["kind"], "related_entity")
        self.assertEqual(counterpart["relation"], "ally")
        self.assertEqual(counterpart["entity"]["alias"], "Aatrox")
        self.assertFalse(any(unknown.raw == "AllyAatrox" for unknown in record.unknowns))

    def test_relation_suffixed_counterpart_splits_relation_and_champion(self) -> None:
        record = self.enrich("Play_vo_Ekko_Taunt3DViAlly")

        counterpart = record.semantic_slots["counterpart"]
        self.assertEqual(counterpart["kind"], "related_entity")
        self.assertEqual(counterpart["relation"], "ally")
        self.assertEqual(counterpart["entity"]["alias"], "Vi")
        self.assertFalse(any(unknown.raw == "ViAlly" for unknown in record.unknowns))

    def test_near_prefixed_counterpart_splits_proximity_and_champion(self) -> None:
        record = self.enrich("Play_vo_Ambessa_Kill3DNearMel")

        counterpart = record.semantic_slots["counterpart"]
        self.assertEqual(counterpart["kind"], "proximity_entity")
        self.assertEqual(counterpart["proximity"], "near")
        self.assertEqual(counterpart["entity"]["alias"], "Mel")
        self.assertFalse(any(unknown.raw == "NearMel" for unknown in record.unknowns))

    def test_skin_target_resolves_after_ambiguous_counterpart(self) -> None:
        record = self.enrich("Play_vo_AhriSkin14_Attack2DLuxSkin06")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "skin")
        self.assertEqual(record.semantic_slots["counterpart"]["label"], "Star Guardian Lux")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["skin_token"], "Skin06")
        self.assertFalse(any(unknown.raw == "LuxSkin06" for unknown in record.unknowns))

    def test_missing_skin_target_resolves_after_ambiguous_counterpart(self) -> None:
        record = self.enrich("Play_vo_AsheSkin76_FirstEncounter3DThreshSkin19")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "skin")
        self.assertEqual(record.semantic_slots["counterpart"]["source"], "skin_token_fallback")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["champion_alias"], "Thresh")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["skin_number"], 19)
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["trust"], "missing_current_reference")
        self.assertFalse(any(unknown.raw == "ThreshSkin19" for unknown in record.unknowns))

    def test_prefixed_skin_target_keeps_relation_context(self) -> None:
        record = self.enrich("Play_vo_KaynSkin02_TauntAllyThreshSkin09_3D")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "skin")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["champion_alias"], "Thresh")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["skin_token"], "Skin09")
        self.assertEqual(record.semantic_slots["counterpart"]["metadata"]["context_prefix"], "ally")
        self.assertFalse(any(unknown.raw == "AllyThreshSkin09" for unknown in record.unknowns))

    def test_switch_event_gets_technical_control_profile(self) -> None:
        record = self.enrich("AkshanSkin01_Switch_AkshanTerrain_Brush")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "switch")
        self.assertEqual(record.semantic_slots["switch_target"], "Brush")

    def test_switch_spell_key_fragment_does_not_become_spell_inference(self) -> None:
        record = self.enrich("FioraSkin04_Switch_R_01")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.TECHNICAL_CONTROL)
        self.assertEqual(record.semantic_slots["trigger_family"], "switch")
        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))

    def test_leblanc_rw_gets_spell_combo_candidate(self) -> None:
        record = self.enrich("Play_sfx_LeblancSkin04_LeblancRW_clone_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_sequence"], ["r", "w"])
        combo = next(candidate for candidate in record.candidates if candidate["kind"] == "champion_spell_combo")
        self.assertEqual(combo["subject"]["alias"], "Leblanc")
        self.assertEqual(combo["spell_sequence"], ["r", "w"])

    def test_lux_light_binding_scores_from_champion_spell_name(self) -> None:
        record = self.enrich("Play_sfx_LuxSkin05_LuxLightBinding_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Lux")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["subject"]["alias"], "Lux")
        self.assertEqual(candidate["spell_key"], "q")
        self.assertGreaterEqual(candidate["score"], 90)
        self.assertIn("spell_name", candidate["components"])
        self.assertFalse(
            any(unknown.raw == "LuxLightBinding" for unknown in record.unknowns),
            "已被技能名确认的 raw 不应继续作为未知头 token",
        )

    def test_lux_light_strike_kugel_scores_from_asset_path(self) -> None:
        record = self.enrich("Play_sfx_LuxSkin05_LuxLightStrikeKugel_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_key"], "e")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "asset_path")
        self.assertGreaterEqual(candidate["score"], 85)
        self.assertIn("asset_path", candidate["components"])

    def test_dragontail_spell_ids_reduce_sfx_spell_noise(self) -> None:
        cases = [
            ("Play_sfx_KassadinSkin05_NullLance_OnCast", "Kassadin", "q", "nulllance"),
            ("Play_sfx_TwistedFateSkin10_CardmasterStack_hit", "TwistedFate", "e", "cardmasterstack"),
            ("Play_sfx_AmumuSkin07_AuraofDespair_OnBuffActivate", "Amumu", "w", "auraofdespair"),
        ]

        for raw, alias, spell_key, matched_term in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
                self.assertEqual(record.semantic_slots["subject"]["alias"], alias)
                self.assertEqual(record.semantic_slots["spell_key"], spell_key)
                candidate = next(
                    candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell"
                )
                self.assertEqual(candidate["matched_term"], matched_term)
                self.assertEqual(candidate["match_source"], "dragontail_spell_id")
                self.assertGreaterEqual(candidate["score"], 90)

    def test_form_owner_uses_base_champion_spell_index(self) -> None:
        record = self.enrich("Play_vo_NidaleeCougarSkin06_AspectOfTheCougar_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Nidalee")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_name")
        self.assertIn("owner_context", candidate["components"])

    def test_urgot_recast_missile_matches_spell_key_with_residual(self) -> None:
        record = self.enrich("Play_sfx_Urgot_UrgotRRecastMissile_OnHit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Urgot")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_key")
        self.assertEqual(candidate["residual"], "recastmissile")
        self.assertGreaterEqual(candidate["score"], 75)
        self.assertLess(candidate["score"], 90)
        self.assertIn("residual_fragment", candidate["components"])
        self.assertIn("recast_context", candidate["components"])

    def test_urgot_long_distance_residual_is_structured(self) -> None:
        record = self.enrich("Play_sfx_Urgot_UrgotRRecastMissileLongDistance_OnMissileLaunch")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        self.assertEqual(record.semantic_slots["residual_modifiers"][0]["key"], "recast")
        self.assertEqual(record.semantic_slots["residual_modifiers"][1]["key"], "missile")
        self.assertEqual(record.semantic_slots["residual_modifiers"][2]["key"], "long_distance")
        self.assertEqual(record.semantic_slots["unresolved_residual"], "")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["residual"], "recastmissilelongdistance")
        self.assertIn("residual_modifier", candidate["components"])
        self.assertEqual(candidate["unresolved_residual"], "")

    def test_ruby_urgot_recast_missile_uses_embedded_champion_subject(self) -> None:
        record = self.enrich("Play_sfx_Ruby_UrgotRRecastMissile_OnHit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Urgot")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_key")
        self.assertEqual(candidate["residual"], "recastmissile")
        self.assertGreaterEqual(candidate["score"], 70)
        self.assertIn("embedded_subject", candidate["components"])

    def test_ruby_urgot_recast_missile_launch_keeps_phase_context(self) -> None:
        record = self.enrich("Play_sfx_Ruby_UrgotRRecastMissile_OnMissileLaunch")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertGreaterEqual(candidate["score"], 70)
        self.assertIn("phase_context", candidate["components"])

    def test_leesin_qone_matches_spell_key_stage_variant(self) -> None:
        record = self.enrich("Play_sfx_LeeSin_LeeSinQOne_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "LeeSin")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_key_stage")
        self.assertEqual(candidate["stage"], 1)
        self.assertGreaterEqual(candidate["score"], 90)

    def test_ruby_leesin_qone_uses_embedded_champion_subject(self) -> None:
        record = self.enrich("Play_sfx_Ruby_LeeSinQOne_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "LeeSin")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_key_stage")
        self.assertEqual(candidate["stage"], 1)
        self.assertIn("embedded_subject", candidate["components"])

    def test_champion_title_alias_matches_spell_key_stage_variant(self) -> None:
        record = self.enrich("Play_vo_LeeSinSkin31_BlindMonkQOne_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "LeeSin")
        self.assertEqual(record.semantic_slots["spell_key"], "q")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_key_stage")
        self.assertEqual(candidate["stage"], 1)
        self.assertIn("owner_context", candidate["components"])

    def test_ashe_spirit_of_the_hawk_matches_description_keywords(self) -> None:
        record = self.enrich("Play_sfx_Ashe_AsheSpiritOfTheHawk_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Ashe")
        self.assertEqual(record.semantic_slots["spell_key"], "e")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "description_keywords")
        self.assertGreaterEqual(candidate["score"], 75)
        self.assertIn("description_keywords", candidate["components"])

    def test_masteryi_basic_attack_is_not_inferred_as_passive_spell(self) -> None:
        """普攻类通用词命中未入白名单英雄时不得产出技能候选。"""

        record = self.enrich("Play_sfx_MasterYiSkin02_MasterYiBasicAttack_OnCast")

        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))
        self.assertIsNone(record.semantic_slots.get("spell_key"))

    def test_masteryi_crit_attack_is_not_inferred_as_passive_spell(self) -> None:
        """CritAttack 类通用词命中同样受白名单门限约束。"""

        record = self.enrich("Play_sfx_MasterYiSkin02_MasterYiCritAttack_OnHit")

        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))
        self.assertIsNone(record.semantic_slots.get("spell_key"))

    def test_aatrox_basic_attack_keeps_whitelisted_passive_inference(self) -> None:
        """白名单内英雄的普攻类关键词命中保持原有技能推断。"""

        record = self.enrich("Play_sfx_AatroxSkin01_AatroxBasicAttack_OnCast")

        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["subject"]["alias"], "Aatrox")
        self.assertEqual(candidate["spell_key"], "p")
        self.assertEqual(candidate["match_source"], "description_keywords")
        self.assertEqual(record.semantic_slots["spell_key"], "p")

    def test_aatrox_whitelisted_basic_attack_records_spell_constraint_audit(self) -> None:
        """白名单放行普攻类关键词推断时应记录 manual_spell_constraint 审计事件。"""

        recorder = MatchAuditRecorder()

        with audit_context(recorder):
            self.enrich("Play_sfx_AatroxSkin01_AatroxBasicAttack_OnCast")

        hits = [event for event in recorder.events if event.matcher_kind == "manual_spell_constraint"]
        self.assertTrue(hits)
        self.assertEqual(hits[0].rule_id, "spell_constraint:attack_modifier:Aatrox:p")
        self.assertEqual(hits[0].source_path, "rules/spell_match_constraints.yaml")
        self.assertEqual(hits[0].raw_event_name, "Play_sfx_AatroxSkin01_AatroxBasicAttack_OnCast")

    def test_ashe_volley_rank_matches_spell_name_with_rank_residual(self) -> None:
        record = self.enrich("Play_vo_AsheSkin08_VolleyRank2_cast3D")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Ashe")
        self.assertEqual(record.semantic_slots["spell_key"], "w")
        self.assertEqual(record.semantic_slots["spell_rank"], 2)
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_name")
        self.assertEqual(candidate["residual"], "rank2")
        self.assertGreaterEqual(candidate["score"], 70)

    def test_thresh_passive_souls_matches_passive_with_residual_keyword(self) -> None:
        record = self.enrich("Play_sfx_Thresh_ThreshPassiveSouls_buffactivate_collect")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Thresh")
        self.assertEqual(record.semantic_slots["spell_key"], "p")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "passive_key")
        self.assertEqual(candidate["residual"], "souls")
        self.assertGreaterEqual(candidate["score"], 75)
        self.assertIn("description_keywords", candidate["components"])

    def test_jhin_idle_passive_state_is_not_inferred_as_passive_spell(self) -> None:
        """皮肤命名状态事件里的裸 Passive 是动画状态命名，不得推断为 P 技能。"""

        record = self.enrich("Play_sfx_JhinSkin05_Idle_Passive_buffactivate")

        self.assertEqual(record.profile.primary_family, "skin_named_state")
        self.assertEqual(record.semantic_slots["state_key"], "passive_idle")
        self.assertIsNone(record.semantic_slots.get("spell_key"))
        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))

    def test_jhin_idle_passive_gate_drop_records_audit_reason(self) -> None:
        """裸 Passive 门丢弃候选时应留下可审计的丢弃原因。"""

        recorder = MatchAuditRecorder()

        with audit_context(recorder):
            self.enrich("Play_sfx_JhinSkin05_Idle_Passive_buffactivate")

        drops = [event for event in recorder.events if event.matcher_kind == "inference_gate"]
        self.assertTrue(drops)
        self.assertEqual(drops[0].rule_id, "spell_gate:bare_passive_state_token")
        self.assertEqual(drops[0].owner, "Jhin")

    def test_singed_bare_passive_buff_keeps_passive_key_inference(self) -> None:
        """非动画状态事件的裸 Passive 命中仍保持 passive_key 推断。"""

        record = self.enrich("Play_sfx_SingedSkin09_Passive_buffactivate")

        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["subject"]["alias"], "Singed")
        self.assertEqual(candidate["spell_key"], "p")
        self.assertEqual(candidate["match_source"], "passive_key")
        self.assertEqual(candidate["matched_term"], "passive")
        self.assertEqual(record.semantic_slots["spell_key"], "p")

    def test_owner_context_scores_spell_name_without_champion_prefix(self) -> None:
        record = self.enrich("Play_sfx_AsheSkin06_EnchantedCrystalArrow_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Ashe")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["subject"]["alias"], "Ashe")
        self.assertEqual(candidate["spell_key"], "r")
        self.assertGreaterEqual(candidate["score"], 90)
        self.assertIn("owner_context", candidate["components"])

    def test_twisted_fate_gate_uses_contextual_alias(self) -> None:
        """contextual 别名规则应解析出主体/技能键/component 槽位并消化别名 token（不锁定规则内容值）。"""

        record = self.enrich("Play_sfx_TwistedFate_Gate_OnBuffActivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "TwistedFate")
        self.assertTrue(record.semantic_slots["spell_key"])
        self.assertTrue(record.semantic_slots["contextual_alias"]["component"])
        self.assertNotIn("low_trust_rule", record.semantic_slots)
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "contextual_alias")
        self.assertEqual(candidate["source"], "manual_contextual_alias")
        self.assertIn("owner_context", candidate["components"])
        self.assertFalse(any(unknown.raw == "Gate" for unknown in record.unknowns))

    def test_public_sfx_effect_aliases_are_not_low_confidence(self) -> None:
        """公共 sfx 效果别名应命中 sfx_alias 槽位且不落低置信，也不误挂英雄主体（不锁定规则内容值）。"""

        cases = [
            "Play_sfx_ApheliosSkin01_Spawn3D_buffactivate",
            "Play_sfx_YuumiSkin01_Run3D_buffactivate_slow",
            "Play_sfx_ZedSkin13_Channel_buffactivate",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertTrue(record.semantic_slots["sfx_alias"]["component"])
                self.assertNotEqual(record.confidence.value, "low")
                self.assertNotIn("subject", record.semantic_slots)

    def test_low_trust_event_rule_promotes_reviewed_control_state(self) -> None:
        """已人工审阅的内部控制事件仍可走低信任 exact fallback。"""

        record = self.enrich("_RESET_ALL_FX")

        self.assertEqual(record.confidence.value, "medium")
        rule = record.semantic_slots["low_trust_rule"]
        self.assertEqual(rule["rule_id"], "low_trust_event_0001")
        self.assertEqual(rule["category"], "technical_audio_control")
        self.assertEqual(rule["trust"], "low")

    def test_skin_recall_state_uses_general_profile(self) -> None:
        """皮肤回城资源状态应使用通用 profile，而不是 exact low_trust fallback。"""

        cases = [
            ("Play_fizz_skin_04_recall_leadin_01", "Fizz", "Skin04", "leadin", "01"),
            ("Play_fizz_skin_04_recall_leadout_01", "Fizz", "Skin04", "leadout", "01"),
            ("Play_lux_skin61_recall3d_buffactivate", "Lux", "Skin61", "buff_activate", None),
            ("play_sfx_SettSkin01_Recall3D_buffactivate", "Sett", "Skin01", "buff_activate", None),
            ("Play_sxf_YuumiSkin11_Recall3D_winddown", "Yuumi", "Skin11", "winddown", None),
            ("Play_UrgotSkin09_Recall_efforts01", "Urgot", "Skin09", "efforts", "01"),
        ]

        for raw, subject, skin_token, phase, variant_index in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.confidence.value, "medium")
                self.assertEqual(record.profile.primary_family, "skin_recall_state")
                self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
                self.assertEqual(record.semantic_slots["trigger_family"], "skin_recall_state")
                self.assertEqual(record.semantic_slots["subject_hint"], subject)
                self.assertEqual(record.semantic_slots["skin_token"], skin_token)
                self.assertEqual(record.semantic_slots["recall_action"], "recall")
                self.assertEqual(record.semantic_slots["phase"], phase)
                self.assertNotIn("low_trust_rule", record.semantic_slots)

                if variant_index is not None:
                    self.assertEqual(record.semantic_slots["recall_variant"]["index"], variant_index)

    def test_skin_idle_weapon_and_spell_state_uses_general_profile(self) -> None:
        """皮肤待机状态中的武器名和技能键应作为可信语义解析。"""

        cases = [
            ("Play_jinx_skin60_idle_Powpow_in", "q", "weapon", "powpow"),
            ("Play_jinx_skin60_idle_R_in", "r", "spell", "r"),
        ]

        for raw, spell_key, component_kind, component in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.confidence.value, "medium")
                self.assertEqual(record.profile.primary_family, "skin_idle_state")
                self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
                self.assertEqual(record.semantic_slots["trigger_family"], "skin_idle_state")
                self.assertEqual(record.semantic_slots["subject_hint"], "Jinx")
                self.assertEqual(record.semantic_slots["skin_token"], "Skin60")
                self.assertEqual(record.semantic_slots["spell_key"], spell_key)
                self.assertEqual(record.semantic_slots["idle_component"]["kind"], component_kind)
                self.assertEqual(record.semantic_slots["idle_component"]["key"], component)
                self.assertEqual(record.semantic_slots["phase"], "in")
                self.assertNotIn("low_trust_rule", record.semantic_slots)

    def test_skin_action_state_strips_terminal_dimension_only(self) -> None:
        """皮肤动作状态只把动作 token 词尾 2D/3D 作为发声方向剥离。"""

        cases = [
            ("play_sfx_SettSkin01_Death3D_cast", "death", "3D", "cast"),
            ("play_sfx_SettSkin01_Taunt3D_buffactivate", "taunt", "3D", "buff_activate"),
        ]

        for raw, action, dimension, phase in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.confidence.value, "medium")
                self.assertEqual(record.profile.primary_family, "skin_action_state")
                self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
                self.assertEqual(record.semantic_slots["trigger_family"], "skin_action_state")
                self.assertEqual(record.semantic_slots["subject_hint"], "Sett")
                self.assertEqual(record.semantic_slots["skin_token"], "Skin01")
                self.assertEqual(record.semantic_slots["action"], action)
                self.assertEqual(record.semantic_slots["dimension_token"], dimension)
                self.assertEqual(record.semantic_slots["phase"], phase)
                self.assertNotIn("low_trust_rule", record.semantic_slots)

        negative_cases = [
            "play_sfx_SettSkin01_3DTaunt_buffactivate",
            "play_sfx_SettSkin01_Taunt3DExtra_buffactivate",
        ]

        for raw in negative_cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertNotEqual(record.profile.primary_family, "skin_action_state")

    def test_structured_state_events_migrate_from_low_trust_exact_rules(self) -> None:
        """可读的皮肤/英雄状态和玩法阶段应使用结构化 profile。"""

        cases = [
            (
                "Play_ziggs_skin05_idle3_01",
                "skin_idle_state",
                {"subject_hint": "Ziggs", "skin_token": "Skin05", "idle_variant.index": 3, "segment_index": "01"},
            ),
            (
                "Play_SonaSkin06_Genre2_Transition_leadin_01",
                "skin_audio_transition",
                {"subject_hint": "Sona", "skin_token": "Skin06", "genre_index": 2, "transition_phase": "leadin"},
            ),
            (
                "Play_jinx_skin60_slotmachine_buffactivate_hextech",
                "skin_named_state",
                {
                    "subject_hint": "Jinx",
                    "skin_token": "Skin60",
                    "state_key": "slotmachine",
                    "state_variant": "hextech",
                },
            ),
            (
                "UnMute_sfx_JhinSkin25_Idle_Passive_buffactivate",
                "skin_named_state",
                {"subject_hint": "Jhin", "skin_token": "Skin25", "state_key": "passive_idle", "phase": "buff_activate"},
            ),
            (
                "Play_sfx_SionSkin05_SionSlowStun_buffactivate",
                "champion_status_state",
                {"subject_hint": "Sion", "skin_token": "Skin05", "status_key": "slow_stun", "phase": "buff_activate"},
            ),
            (
                "Play_sfx_Corki_RapidReload_OnBuffDeactivate",
                "champion_status_state",
                {"subject_hint": "Corki", "status_key": "rapid_reload", "phase": "buff_deactivate"},
            ),
            (
                "Play_vo_Kled_AnimalThreshold",
                "champion_status_state",
                {"subject_hint": "Kled", "status_key": "animal_threshold"},
            ),
            (
                "map12_FirstBlood_phase_select",
                "gameplay_phase_control",
                {"map_id": 12, "gameplay_event": "first_blood", "phase_action": "select"},
            ),
        ]

        for raw, profile, expected_slots in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.confidence.value, "medium")
                self.assertEqual(record.profile.primary_family, profile)
                self.assertNotIn("low_trust_rule", record.semantic_slots)

                for path, expected in expected_slots.items():
                    value = record.semantic_slots
                    for part in path.split("."):
                        value = value[part]

                    self.assertEqual(value, expected)

    def test_map_unit_sfx_aliases_are_not_champion_spell_events(self) -> None:
        """地图单位 sfx 别名应归 MAP_OR_SYSTEM 的 system_sfx，不得误判为英雄技能事件（不锁定规则内容值）。"""

        cases = [
            "Play_sfx_SRU_Red_SRU_RedBasicAttack_OnCast",
            "Play_sfx_SRU_ChaosMinionRanged_OnBuffActivate",
            "Play_sfx_HA_ChaosMinionRangedBasicAttack_OnCast",
        ]

        for raw in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
                self.assertEqual(record.semantic_slots["sfx_scope"], "system_sfx")
                self.assertTrue(record.semantic_slots["sfx_alias"]["component"])
                self.assertNotEqual(record.confidence.value, "low")
                self.assertNotIn("subject", record.semantic_slots)

    def test_jayce_acceleration_gate_uses_reference_spell_name(self) -> None:
        record = self.enrich("Play_sfx_Jayce_JayceAccelerationGate_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Jayce")
        self.assertEqual(record.semantic_slots["spell_key"], "e")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "inferred_champion_spell")
        self.assertEqual(candidate["match_source"], "spell_name")
        self.assertGreaterEqual(candidate["score"], 90)

    def test_draven_attack_p_uses_contextual_passive_alias(self) -> None:
        """contextual 别名携带残留上下文时应解析主体并把未识别 token 无损进 unresolved_context。"""

        record = self.enrich("Play_sfx_Draven_DravenAttackP_LQC_OnHit")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Draven")
        self.assertTrue(record.semantic_slots["spell_key"])
        self.assertTrue(record.semantic_slots["contextual_alias"]["component"])
        self.assertEqual(record.semantic_slots["unresolved_context"], ["LQC"])
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "contextual_alias")
        self.assertEqual(candidate["raw"], "DravenAttackP")
        self.assertFalse(any(unknown.raw == "P" for unknown in record.unknowns))

    def test_ruby_boss_uses_mode_contextual_alias(self) -> None:
        record = self.enrich("Play_sfx_Ruby_Boss_FNVeigar_Death")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "death")
        self.assertEqual(record.semantic_slots["mode_context"]["key"], "ruby")
        self.assertEqual(record.semantic_slots["mode_context"]["source"], "manual_contextual_alias")
        self.assertEqual(record.semantic_slots["mode_entity"]["kind"], "mode_boss")
        self.assertEqual(record.semantic_slots["mode_actor"]["raw"], "FNVeigar")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "contextual_alias")
        self.assertEqual(candidate["raw"], "Boss")
        self.assertEqual(candidate["source"], "manual_contextual_alias")
        self.assertFalse(any(unknown.raw == "Boss" for unknown in record.unknowns))

    def test_jade_qualifier_gets_classic_mode_context(self) -> None:
        record = self.enrich("Play_vo_Caitlyn_CaitlynWSnare_Jade_hit3D")

        self.assertEqual(record.semantic_slots["mode_context"]["key"], "jade")
        self.assertEqual(record.semantic_slots["mode_context"]["label"], "经典模式")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "mode_namespace")
        self.assertEqual(candidate["raw"], "Jade")
        self.assertEqual(candidate["position"], "qualifier")
        self.assertEqual(record.raw_event_name, "Play_vo_Caitlyn_CaitlynWSnare_Jade_hit3D")

    def test_strawberry_boss_gets_pve_mode_context_and_actor(self) -> None:
        record = self.enrich("Play_sfx_Strawberry_Boss_Aatrox_AatroxQ1_cast")

        self.assertEqual(record.semantic_slots["mode_context"]["key"], "strawberry")
        self.assertEqual(record.semantic_slots["mode_context"]["label"], "PVE 模式")
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Aatrox")
        self.assertEqual(record.semantic_slots["mode_actor"]["alias"], "Aatrox")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "mode_namespace")
        self.assertEqual(candidate["raw"], "Strawberry")
        self.assertEqual(candidate["position"], "owner")
        self.assertEqual(candidate["actor_role"], "boss")

    def test_mode_prefix_does_not_match_ruby_crystal_item_name(self) -> None:
        record = self.enrich("Play_vo_Galio_BuyItem2DRubyCrystal")

        self.assertNotIn("mode_context", record.semantic_slots)
        self.assertFalse(any(candidate["kind"] == "mode_namespace" for candidate in record.candidates))

    def test_game_mode_ruby_boss_uses_nested_mode_context(self) -> None:
        record = self.enrich("Play_sfx_GameMode_Ruby_Boss_FNVeigar_Death")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["mode_context"]["key"], "ruby")
        self.assertEqual(record.semantic_slots["mode_entity"]["kind"], "mode_boss")
        self.assertEqual(record.semantic_slots["mode_actor"]["raw"], "FNVeigar")
        self.assertFalse(any(unknown.raw in {"Ruby", "Boss"} for unknown in record.unknowns))

    def test_short_alias_does_not_match_unrelated_prefix_for_spell_inference(self) -> None:
        record = self.enrich("Play_mus_Map11_VillainLab_win")

        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))
        self.assertNotEqual(record.semantic_slots.get("subject", {}).get("alias"), "Vi")

    def test_ruby_brand_gets_composite_owner_candidate(self) -> None:
        record = self.enrich("Play_sfx_Ruby_Brand_Dance3D_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "dance")
        self.assertEqual(record.semantic_slots["owner_composition"]["entity"]["alias"], "Brand")
        candidate = next(candidate for candidate in record.candidates if candidate["kind"] == "composite_owner")
        self.assertEqual(candidate["prefix"], "Ruby")
        self.assertEqual(candidate["entity"]["alias"], "Brand")

    def test_ruby_wukong_display_name_gets_composite_owner_candidate(self) -> None:
        record = self.enrich("Play_sfx_Ruby_Wukong_Dance3D_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "dance")
        self.assertEqual(record.semantic_slots["mode_context"]["key"], "ruby")
        self.assertEqual(record.semantic_slots["mode_actor"]["alias"], "MonkeyKing")
        self.assertEqual(record.semantic_slots["owner_composition"]["entity"]["alias"], "MonkeyKing")
        self.assertFalse(any(unknown.raw == "Wukong" for unknown in record.unknowns))

    def test_ruby_champion_actor_without_underscore_gets_mode_actor_context(self) -> None:
        record = self.enrich("Play_sfx_Ruby_Brand_P_detonate1")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["mode_context"]["key"], "ruby")
        self.assertEqual(record.semantic_slots["mode_context"]["source"], "mode_actor_convention")
        self.assertEqual(record.semantic_slots["mode_actor"]["alias"], "Brand")
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Brand")
        self.assertEqual(record.semantic_slots["spell_key"], "p")
        self.assertFalse(any(unknown.raw == "Brand" for unknown in record.unknowns))

    def test_move_long_becomes_tail_modifier(self) -> None:
        record = self.enrich("Play_vo_AatroxSkin01_Move2DLong")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["tail_modifiers"][0]["key"], "long")
        self.assertEqual(record.semantic_slots["tail_modifiers"][0]["kind"], "movement_distance")
        self.assertFalse(record.unknowns)

    def test_zoe_sleep_countdown_residual_keeps_countdown_as_compound(self) -> None:
        record = self.enrich("Play_sfx_ZoeSkin01_ZoeESleepCountDownSlow_buffactivate")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["spell_key"], "e")
        keys = [modifier["key"] for modifier in record.semantic_slots["residual_modifiers"]]
        self.assertEqual(keys, ["sleep", "countdown", "slow"])
        self.assertEqual(record.semantic_slots["unresolved_residual"], "")

    def test_yasuo_q3w_gets_spell_sequence_context(self) -> None:
        record = self.enrich("Play_sfx_YasuoSkin02_YasuoQ3W_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Yasuo")
        self.assertEqual(record.semantic_slots["spell_sequence"], ["q", "w"])
        self.assertEqual(record.semantic_slots["spell_sequence_modifiers"][0]["cast_count"], 3)
        self.assertFalse(any(unknown.raw == "YasuoQ3W" for unknown in record.unknowns))

    def test_vi_e_attack_matches_short_spell_key_with_residual(self) -> None:
        record = self.enrich("Play_sfx_ViSkin01_ViEAttack_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Vi")
        self.assertEqual(record.semantic_slots["spell_key"], "e")
        self.assertEqual(record.semantic_slots["residual_modifiers"][0]["key"], "attack")
        self.assertEqual(record.semantic_slots["unresolved_residual"], "")

    def test_kill_with_empowered_r_gets_spell_method(self) -> None:
        record = self.enrich("Play_sfx_AurelionSolSkin02_Kill3DREmpowered")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.KILL_OR_SCORE)
        self.assertEqual(record.semantic_slots["kill_method"]["spell_key"], "r")
        self.assertEqual(record.semantic_slots["kill_method"]["residual_modifiers"][0]["key"], "empowered")
        self.assertIsNone(record.semantic_slots["counterpart"])
        self.assertFalse(any(unknown.raw == "REmpowered" for unknown in record.unknowns))

    def test_gangplank_r_upgrades_are_structured_modifiers(self) -> None:
        cases = [
            ("Play_vo_Gangplank_GangplankRUpgradeOne", "upgrade_1"),
            ("Play_vo_Gangplank_GangplankRUpgradeTwo", "upgrade_2"),
            ("Play_vo_Gangplank_GangplankRUpgradeThree", "upgrade_3"),
        ]

        for raw, key in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)

                self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
                self.assertEqual(record.semantic_slots["subject"]["alias"], "Gangplank")
                self.assertEqual(record.semantic_slots["spell_key"], "r")
                self.assertEqual(record.semantic_slots["residual_modifiers"][0]["key"], key)
                self.assertEqual(record.semantic_slots["unresolved_residual"], "")
                self.assertFalse(record.unknowns)

    def test_ultbook_ahri_r_on_cast_is_ultimate_spellbook_spell(self) -> None:
        record = self.enrich("Play_vo_UltBook_AhriR_OnCast")

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SPELL_EVENT)
        self.assertEqual(record.semantic_slots["mode_context"]["key"], "ultimate_spellbook")
        self.assertTrue(record.semantic_slots["mode_context"]["label"])
        self.assertEqual(record.semantic_slots["subject"]["alias"], "Ahri")
        self.assertEqual(record.semantic_slots["spell_key"], "r")
        self.assertEqual(record.semantic_slots["spell_kind"], "ultimate")
        self.assertEqual(record.semantic_slots["phase"], "cast")
        self.assertFalse(record.unknowns)

    def test_composite_owner_does_not_match_short_alias_prefix(self) -> None:
        record = self.enrich("Play_mus_Map11_VillainLab_win")

        self.assertFalse(any(candidate["kind"] == "composite_owner" for candidate in record.candidates))

    def test_lowercase_recall_becomes_self_action(self) -> None:
        record = enrich_with_reference(
            normalize_semantics(decompose_event_name("Play_jhin_skin02_recall_leadin", game_version="16.5")),
            ReferenceIndex.builtins(),
        )

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.SELF_ACTION)
        self.assertEqual(record.semantic_slots["trigger_family"], "recall")

    def test_emote_wheel_abbreviation_becomes_system_profile(self) -> None:
        record = enrich_with_reference(
            normalize_semantics(decompose_event_name("Play_sfx_Emotes_EW_Affectionate_01", game_version="16.5")),
            ReferenceIndex.builtins(),
        )

        self.assertEqual(record.profile.semantic_profile, SemanticProfile.MAP_OR_SYSTEM)
        self.assertEqual(record.semantic_slots["trigger_family"], "emote_wheel")

    def test_wuko_alias_resolves_to_wukong_counterpart(self) -> None:
        record = self.enrich("Play_vo_CamilleSkin01_Kill3DWuko")

        self.assertEqual(record.semantic_slots["counterpart"]["alias"], "MonkeyKing")
        self.assertFalse(record.unknowns)

    def test_base_skin_counterpart_is_structured_from_champion_name(self) -> None:
        record = self.enrich("Play_vo_RenektonSkin26_FirstEncounter3DNasusBase")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "champion_base_skin")
        self.assertEqual(record.semantic_slots["counterpart"]["champion"]["alias"], "Nasus")
        self.assertFalse(record.unknowns)

    def test_or_group_with_concatenated_champions_is_structured(self) -> None:
        record = self.enrich("Play_vo_LuxSkin07_Kill3DGangplankPykeOrMissFortune")

        counterpart = record.semantic_slots["counterpart"]
        self.assertEqual(counterpart["kind"], "champion_group")
        self.assertEqual(counterpart["connector"], "or")
        self.assertEqual(
            [member["alias"] for member in counterpart["members"]],
            ["Gangplank", "Pyke", "MissFortune"],
        )
        self.assertFalse(record.unknowns)

    def test_themed_champion_counterpart_is_structured(self) -> None:
        record = self.enrich("Play_vo_Hwei_Move2DFirstDarkJhin")

        self.assertEqual(record.semantic_slots["counterpart"]["kind"], "themed_entity")
        self.assertEqual(record.semantic_slots["counterpart"]["theme"], "dark")
        self.assertEqual(record.semantic_slots["counterpart"]["entity"]["alias"], "Jhin")
        self.assertFalse(record.unknowns)

    def test_composite_owner_backfills_skin_token(self) -> None:
        """英雄+皮肤+形态复合 owner 应把皮肤段回填到 semantic_slots。"""
        record = self.enrich("Play_sfx_AnnieSkin08Tibbers_AnnieTibbersBasicAttack_OnHit")
        self.assertEqual(record.semantic_slots.get("skin_token"), "Skin08")

    def test_manual_untrusted_voice_targets_resolve_from_external_entities(self) -> None:
        """external_entities 词表中的语音对象应经 builtins 索引解析出 kind 与 label（不锁定词表内容值）。"""

        index = ReferenceIndex.builtins()

        for token in ("MtTargon", "Nexus", "Sentinels", "Gnetic"):
            with self.subTest(token=token):
                resolved = index.resolve_token(token)
                self.assertIsNotNone(resolved)
                self.assertTrue(resolved.kind)
                self.assertTrue(resolved.label)


class ReferenceSpellResolutionRegressionTests(unittest.TestCase):
    """验证 reference spell id 与单字母槽位前缀的归属边界。"""

    @classmethod
    def setUpClass(cls) -> None:
        """加载仓库累计 reference，覆盖真实英雄内部技能 id。"""

        cls.index = ReferenceIndex.from_reference_collection(Path(__file__).parents[1] / "reference")

    def enrich(self, raw_event_name: str):
        """按生产拆解链路解析一条回归事件。"""

        return enrich_with_reference(
            normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
            self.index,
        )

    def test_lux_prismatic_wave_missile_prefers_reference_spell_id(self) -> None:
        """LuxPrismaticWaveMissile 应由 W 的精确 reference id 归属。"""

        record = self.enrich("Play_vo_Lux_LuxPrismaticWaveMissile_hit3D")

        self.assertEqual(record.semantic_slots["subject"]["alias"], "Lux")
        self.assertEqual(record.semantic_slots["spell_key"], "w")
        self.assertEqual(record.semantic_slots["spell_match"]["match_source"], "reference_spell_id")
        self.assertEqual(record.confidence.value, "high")

    def test_renekton_execute_does_not_match_e_slot_prefix(self) -> None:
        """Execute 不能仅因 RenektonE 前缀被误判为 E。"""

        record = self.enrich("Play_sfx_Renekton_RenektonExecute_OnCast")

        self.assertIsNone(record.semantic_slots.get("spell_key"))
        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))

    def test_renekton_pre_execute_resolves_w_from_reference_spell_id(self) -> None:
        """PreExecute 有精确 reference id 时应稳定解析为 W。"""

        record = self.enrich("Play_sfx_Renekton_RenektonPreExecute_OnCast")

        self.assertEqual(record.semantic_slots["spell_key"], "w")
        self.assertEqual(record.semantic_slots["spell_match"]["match_source"], "reference_spell_id")
        self.assertEqual(record.confidence.value, "high")

    def test_jayce_ranged_attack_does_not_match_r_slot_prefix(self) -> None:
        """RangedAttack 不能仅因 JayceR 前缀被误判为 R。"""

        record = self.enrich("Play_sfx_Jayce_JayceRangedAttack_OnCast")

        self.assertIsNone(record.semantic_slots.get("spell_key"))
        self.assertFalse(any(candidate["kind"] == "inferred_champion_spell" for candidate in record.candidates))

    def test_owner_prefixed_hint_is_only_promoted_with_reference_evidence(self) -> None:
        """hint 保留匹配线索；普通词首不能仅凭 hint 升级为 spell_key。"""

        cases = [
            ("Play_vo_Renekton_RenektonExecute_cast3D", "e", None),
            ("Play_vo_JayceSkin35_JayceRangedAttack_cast3D", "r", None),
            ("Play_vo_RengarSkin08_RengarQemp_cast3D", "q", "q"),
            ("Play_vo_Maokai_MaokaiRmis_OnHit", "r", "r"),
            ("Play_vo_Kalista_KalistaExpunge_cast3D", "e", "e"),
            ("Play_vo_Caitlyn_CaitlynWSnare_Jade_hit3D", "w", None),
            ("Play_sfx_AkaliSkin02_AkaliRb_OnCast", "r", None),
        ]
        for raw, expected_hint, expected_key in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)
                self.assertEqual(record.semantic_slots.get("spell_key_hint"), expected_hint)
                self.assertEqual(record.semantic_slots.get("spell_key"), expected_key)

    def test_manual_trusted_residuals_restore_obvious_unknown_vo_spell_cues(self) -> None:
        """跨皮肤 raw 族与 reference 均确认的 VO 技能 cue 不得退成纯 fallback。"""

        cases = [
            ("Play_vo_Smolder_SmolderQUpgrade", "q"),
            ("Play_vo_WarwickSkin16_WarwickRsound_UltimateEfforts", "r"),
            ("Play_vo_WarwickSkin16_WarwickWActiveMiss", "w"),
            ("Play_sfx_Darius_NorseKing_DariusQCast_OnBuffDeactivate", "q"),
            ("Play_sfx_HeimerTBlue_HeimerdingerQSpawnDestroyAudio_OnBuffActivate", "q"),
            ("Play_sfx_LockeTotem_LockeRPickUp_buffactivate_foley_loop", "r"),
            ("Play_sfx_Maokai_Maokairsoundmis_OnMissileLaunch", "r"),
            ("Play_sfx_PaxSona_SonaQProcAttacker_buffactivate", "q"),
            ("Play_sfx_PaxSona_SonaWShield_buffactivate", "w"),
            ("Play_sfx_PaxSona_SonaEPassiveAttack_buffactivate", "e"),
            ("Play_sfx_SyndraJusticar_SyndraRSpell_OnCast", "r"),
            ("Play_sfx_SyndraJusticar_SyndraWCast_OnCast", "w"),
            ("Play_sfx_TeemoMushroom_TeemoRTrap_OnBuffActivate", "r"),
            ("Play_sfx_VarusAscended_VarusQEmpoweredWChargeSFX_OnBuffActivate", "q"),
            ("Play_sfx_VarusAscended_VarusWBasicAttack_OnBuffActivate", "w"),
            ("Play_sfx_XmasKatarina_KatarinaQMis_OnMissileLaunch", "q"),
            ("Play_sfx_XmasKatarina_KatarinaRSound_OnBuffActivate", "r"),
            ("Stop_sfx_XmasKatarina_KatarinaRTriggerSound_OnMissileCast", "r"),
            ("Play_sfx_ZiggsPoolParty_ZiggsQSpell_OnCast", "q"),
            ("Play_sfx_ZiggsPoolParty_ZiggsRImpactSound_OnHit", "r"),
            ("Play_sfx_ZyraThornPlant_ZyraQPlantMissile_OnMissileLaunch", "q"),
            ("Play_sfx_ZyraGraspingPlant_ZyraEPlantAttack_OnCast", "e"),
        ]
        for raw, expected_key in cases:
            with self.subTest(raw=raw):
                record = self.enrich(raw)
                self.assertEqual(record.semantic_slots.get("spell_key"), expected_key)
                self.assertEqual(record.confidence.value, "high")
                self.assertIn(
                    "trusted_single_key_residual",
                    record.semantic_slots["spell_match"]["components"],
                )

    def test_heimer_rq_engine_audio_preserves_empowered_q_turret_context(self) -> None:
        """RQEngineAudio 不只标为 R，还保留 R 强化 Q 炮台组件。"""

        record = self.enrich("Play_sfx_HeimerTBlue_HeimerdingerRQEngineAudio_OnBuffActivate")

        self.assertEqual(record.semantic_slots.get("spell_key"), "r")
        self.assertEqual(record.semantic_slots["contextual_alias"]["component"], "empowered_q_turret")
        self.assertEqual(record.confidence.value, "high")


class ShopkeeperVoiceSlotsTests(unittest.TestCase):
    """验证批次6d 商店老板语音槽位解析（fill_shopkeeper_voice_slots）。"""

    index: ReferenceIndex

    @classmethod
    def setUpClass(cls) -> None:
        """复用进程内共享的真实 reference index。"""

        cls.index = shared_reference_index()

    def enrich(self, raw_event_name: str):
        """按生产链路拆解并 enrich 单条事件名。"""

        return enrich_with_reference(
            normalize_semantics(decompose_event_name(raw_event_name, game_version="16.5")),
            self.index,
        )

    def test_action_subject_and_persona_slots(self) -> None:
        """标准形态拆出动作/维度/对象/店主槽位，map profile 不受影响。"""

        record = self.enrich("Play_vo_Shopkeeper_Map12_SRU_StorekeeperSouth_Open2DZiggs")
        slots = record.semantic_slots
        self.assertEqual(record.profile.primary_family, "map")
        self.assertEqual(slots["map"]["id"], 12)
        self.assertEqual(slots["shop_action"], "open")
        self.assertEqual(slots["dimension_token"], "2D")
        self.assertEqual(slots["shop_subject"]["raw"], "Ziggs")
        self.assertEqual(slots["shop_persona"]["raw"], "SRU_StorekeeperSouth")

    def test_bare_action_forms_without_dimension(self) -> None:
        """屠夫之桥店主的裸动作形态（无 2D/3D、可无对象）同样命中。"""

        record = self.enrich("Play_vo_Shopkeeper_Map12_BW_AP_Bubbs_OpenCorki")
        slots = record.semantic_slots
        self.assertEqual(slots["shop_action"], "open")
        self.assertIsNone(slots.get("dimension_token"))
        self.assertEqual(slots["shop_subject"]["raw"], "Corki")
        self.assertEqual(slots["shop_persona"]["raw"], "BW_AP_Bubbs")

        record = self.enrich("Play_vo_Shopkeeper_Map12_BW_AP_Bubbs_Purchase")
        self.assertEqual(record.semantic_slots["shop_action"], "purchase")
        self.assertNotIn("shop_subject", record.semantic_slots)

    def test_general_subject_uses_general_kind(self) -> None:
        """General 对象按 unresolved_counterpart 惯例标 kind=general。"""

        record = self.enrich("Play_vo_Shopkeeper_Map12_Bloom_TwilightGhost_Close3DGeneral")
        slots = record.semantic_slots
        self.assertEqual(slots["shop_action"], "close")
        self.assertEqual(slots["shop_subject"]["kind"], "general")

    def test_non_action_tail_leaves_slots_untouched(self) -> None:
        """表外尾形态不写商店槽位，维持既有 map profile。"""

        record = self.enrich("Play_vo_Shopkeeper_Map12_SRU_StorekeeperSouth_Foo")
        self.assertNotIn("shop_action", record.semantic_slots)
        self.assertEqual(record.profile.primary_family, "map")
