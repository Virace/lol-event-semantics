# 贡献指引

本仓库的核心设计目标是：**扩充词库/关键词数据，就能把新事件全部处理下来
——不需要改代码**。绝大多数有价值的贡献是纯 YAML 数据改动。

背景知识（流水线怎么运转、新版本怎么被消化）见
[docs/UPDATE_WORKFLOW.md](docs/UPDATE_WORKFLOW.md)。

## 你可以扩充什么（按常见程度排序）

### 1. 展示词表 `rules/display_labels.yaml`

所有中文展示词条的单一来源，按分节组织：

| 分节 | 内容 | 例子 |
|---|---|---|
| `phase` | 相位/阶段词 | `leadin: 引入`、`rankone: 1级` |
| `action` | 皮肤动作词 | `dance: 舞蹈`、`recall: 回城` |
| `counterpart` | 交互对象官方译名（地区/野怪/皮肤系列…） | `Zaun: 祖安`、`Elderwood: 永恒之森` |
| `announcer.event / team / subject / mode` | 播报事件词干/队伍/主体/语音包 | `PentaKill: 五杀`、`HeroEnemyTeam: 敌方英雄` |
| `shop.action / subject` | 商店老板动作词与对象（装备名/条件） | `SpiritVisage: 振奋盔甲` |
| `maps` | 地图 id → 中文名 | `12: 嚎哭深渊` |

**门禁设计：表外词条不会出错误文案，只会回落 legacy 展示。** 所以补词条
的收益是覆盖率，风险是译名错误——因此有硬性核验要求（见下）。

### 2. 英雄规则分片 `rules/contextual/champions/<英雄>.yaml`

当某英雄的事件名用了非标准 token（技能内部代号、缩写、彩蛋名），需要
一个分片告诉拆解层它的语义。参考现有 120+ 分片的写法（如 `Ashe.yaml`
的 `VolleyRank2`＝W 技能等级语音）。

### 3. 俗名兜底 `overrides/champion_nicknames.yaml`

官方三数据源都没有的社区俗称（如 Irelia=刀妹）放这里，纯数据可扩展。

### 4. 代表样例 `tests/fixtures/normalized_translation_cases.json`

每条样例锁定一个事件名应产出的 frame 结构（`expected_frame_type` 与
`required_roles`）；`zh_cn_*` 与 `notes` 字段记录当时核验过的文案与依据，
仅作文档参考，测试不逐字回归文案——修订词条不会挂测试。

## 核验要求（硬性）

**每个中文词条必须有一手来源，禁止凭记忆或社区惯称直填。** 来源优先级：

1. **国服客户端字符串表**（CommunityDragon 镜像，中英同键对照）——播报
   词、玩法术语的首选；
2. **Data Dragon** `zh_CN`/`en_US` 同版本同 id 成对数据——装备名、英雄/
   皮肤名；已删除的老装备可回溯历史版本（如 `8.24.1`、`7.24.2`）；
3. 官方公告/官网——上面两者覆盖不到时。

社区惯称与官方词不一致的真实案例（都已进词表注释，供警惕）：
Godlike=接近神（非「超神」）、Rampage=接近暴走（非「暴走」）、
Brawl=神木之门（非「乱斗」）、Rakan=洛（非「拉坎」）、
Anima Squad=幻灵战队。

**语义没证实的词不要猜。** 表外回落是安全的，错词条是有害的。仓库里
有一批刻意留白的例子（Shopkeeper 的 `CountSeven`、播报 `Shutdown` 三态
的主体方向），都在等更硬的证据。

## 提交 PR 前的验证

```powershell
# 1. 全量重建 + 校验 + 棘轮（任一步失败即停）
uv run python scripts/run_full_refresh.py `
  --raw-root raw\events --reference-root reference `
  --output-root dist\normalized --work-root dist\work\event-decomposition `
  --report-root dist\reports\runs\latest --baseline data\quality-baseline.json

# 2. 全量测试
uv run python -m pytest

# 3. 抽查你影响到的事件
uv run lookup-normalized-event --event <事件名> --mode main
```

验收标准：0 error、棘轮不恶化（改善更好）、pytest 全绿、抽查文案自然
无歧义。

## 什么不建议在 PR 里改

- **拆解/翻译层代码**（`src/lol_event_semantics/`）——除非你在实现新的
  frame 类型并且先开 issue 讨论过设计；
- **`raw/events/`**——原始语料是不可变源数据，由更新流水线维护；
- **`data/quality-baseline.json`**——基线由维护者在指标改善后收紧；
- 绕过词表门禁往文案里塞英文 fallback——这是设计上明确禁止的。
