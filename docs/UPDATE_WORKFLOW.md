# 更新流程开发者介绍：当新版本 / 新英雄到来时会发生什么

本文以开发者日志的视角讲清一件事：**游戏更新了（新英雄、新地图、新皮肤、新播报事件），这套流水线如何消化它，哪些环节全自动、哪些环节需要人**。

如果你想直接动手扩充词库，请读 [CONTRIBUTING.md](../CONTRIBUTING.md)；本文是它的背景知识。

## 总览：三层结构与一条流水线

```text
raw/events/            原始事件名（不可变源数据，累积式）
  ↓  scripts/decompose_events.py
dist/work/…/*.jsonl    拆解层：事件名 → 词法原子 + 语义槽位（无损）
  ↓  scripts/build_normalized_outputs.py
dist/normalized/       翻译层：语义槽位 → translation frame + 中文文案
  ↓  scripts/validate_normalized_outputs.py + check_quality_ratchet.py
质量门禁               0 error 硬门 + 质量棘轮 + 黄金样例 + 全量 pytest
```

一键跑完整链：

```powershell
uv run python scripts/run_full_refresh.py `
  --raw-root raw\events --reference-root reference `
  --output-root dist\normalized --work-root dist\work\event-decomposition `
  --report-root dist\reports\runs\latest --baseline data\quality-baseline.json
```

任何一步失败即停（`FAILED at step: <步骤名>`）。

## 第 1 层：数据摄入（全自动）

新版本的事件名与参考数据进入仓库的路径：

1. `scripts/extract_raw_events_from_game.py` 从本地 WAD/BIN 抽取本轮事件名；
2. `scripts/check_raw_update.py` 只读对比本轮与累积 `raw/events/` 的差集；
3. `scripts/update_raw_events.py` 把差集合并进累积语料，并可串起 reference 导入；
4. `scripts/import_reference_data.py` 把三个官方数据源写进 `reference/`：
   - game-data（英雄/皮肤/技能）
   - Data Dragon（名称/称号）
   - 腾讯 `hero_list.js`（中文俗名/拼音搜索别名）

**新英雄在这一层不需要任何人工**：名字、称号、俗名、皮肤名会自动进入
`reference/`，官方数据缺俗称时才需要在 `overrides/champion_nicknames.yaml`
里人工补一条。

## 第 2 层：拆解（自动为主，非标准命名才补规则）

拆解层把 `Play_vo_AsheSkin08_VolleyRank2_OnCast` 这类事件名切成词法原子，
并解析成语义槽位（owner=Ashe、skin=Skin08、contextual_alias=VolleyRank2、
phase=cast…）。它依赖三类知识：

- **通用规则**：`rules/phases.yaml`、`rules/action_aliases.yaml`、
  `rules/normalization.yaml` 等——相位词、动作词、规范化写法；
- **参考实体**：`reference/` 里的英雄/皮肤/地图名，用来识别名字段；
- **英雄专属规则**：`rules/contextual/champions/<英雄>.yaml` 分片——
  只有当英雄用了非标准 token（技能内部代号、缩写）才需要。

新英雄如果走标准命名（绝大多数如此），这一层零人工。怎么发现需要补分
片：看 `dist/reports/runs/latest/` 里的置信度分布、`--write-low-trust-rules`
导出的低信任命中表、validate 的 suspicious 警告。

**设计原则：无损。** 所有 token 都进语义槽位，即使当前没有词条给它做中
文展示——展示层丢词会被 `display_drops_parsed_slot` 校验拦下。

## 第 3 层：翻译/展示（词表门禁，新概念先安全落地再人工扩表）

翻译层把语义槽位组装成结构化 translation frame（`voice_event` /
`ability_cast_event` / `announcer_event` / `shop_voice_event` 等），再渲染中文
文案。所有中文词条集中在 **`rules/display_labels.yaml`**，核心机制是
**词表门禁**：

> 表外的新词**不建 frame，回落 legacy 展示**。宁缺毋错，严禁英文
> fallback 混进中文文案。

所以新概念出现时的时间线是：

1. 新词落到 legacy/fallback → fallback 率上升或 `translation_missing`
   警告出现 → 质量棘轮（`data/quality-baseline.json`）阻断或报告显现；
2. 人工核验词义——**一手来源优先级：国服客户端字符串表（中英同键对
   照）> Data Dragon 中英同键 > 官方公告**。社区惯称不可直接采信（本
   仓库已勘出 Godlike=接近神、Anima Squad=幻灵战队、Rakan=洛 等多处
   反惯称案例）；
3. 把词条加进 `rules/display_labels.yaml` 对应分节（纯数据改动），全量
   重建，fallback 回落，基线收紧到新水位。

只有出现**全新的事件结构**（某个新玩法带来前所未见的事件形态）才需要
写代码加 frame 类型——这在历史上大约每个功能批次发生一次，普通的游戏
版本更新用不到。

## 质量安全网

| 机制 | 作用 |
|---|---|
| `validate_normalized_outputs.py` | 0 error 硬门：schema/契约/渲染冒烟/无损性 |
| `check_quality_ratchet.py` | 棘轮：fallback 率、残留英文率、可疑实体数等只许改善不许恶化 |
| 黄金样例（`tests/fixtures/normalized_translation_cases.json`） | 端到端逐字文本回归，每条 notes 记录核验依据 |
| 全量 pytest | 单测 + 契约测试 + 黄金守卫 |

更新数据后只要这四道门全绿，就可以认为这次更新被安全消化了。
