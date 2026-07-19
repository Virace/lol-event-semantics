# 仓库说明

## 仓库定位

`lol-event-semantics` 用来维护《英雄联盟》音频事件名的语义规则、原始事件语料、审计报告和生成产物。

本仓库只处理仍属于《英雄联盟》客户端的事件名。云顶之弈 / TFTM 相关事件名不进入 raw 快照、拆解产物、审核报告或对外数据。

这个仓库关注的不是音频提取本身，而是事件名之上的语义资产：

- 原始事件名如何被理解
- 事件名如何被拆解和归一化
- 哪些结果来自官方 game-data / DragonTail 参考
- 哪些结果来自手写规则、人工覆盖或推断逻辑
- 如何把这些知识生成稳定、可审计、可复用的对外数据

`raw_event_name` 是不可变源数据。中文说明、展示文案、语义模板、分类标签和审计结果都是派生数据。

## 当前主流程

当前主流程以累积 raw、规则、参考数据、审计和 normalized 输出为中心：

```text
raw/events/
  -> rules/ + overrides/ + reference/
  -> dist/work/<run-id>/event-decomposition/
  -> dist/reports/runs/<run-id>/
  -> dist/normalized/
```

各目录职责：

- `raw/events/`：按实体累积的 raw event name 源语料。不按版本替换，不因为新版本重做而删除旧事件。
- `rules/`：长期维护的规则、词典、上下文别名和 residual modifier。
- `overrides/`：人工 review 后的精确覆盖和兼容决策。
- `reference/`：仓库维护的累计精简参考合集，包含 `champions/`、`maps/`、`skinlines/`，解释阶段只读取这里。
- `.temp/reference-sources/`：被忽略的版本敏感参考输入缓存，不对下游承诺稳定。
- `dist/work/<run-id>/`：单次执行中间产物，例如 event-decomposition 和 review-site。
- `dist/reports/runs/<run-id>/`：规则命中、未知项、候选项和维护审计报告。
- `dist/normalized/`：唯一对外主数据源，只按实体拆分，不包含完整大包索引。

旧的 `manifest/<version>/events` 和 `hashes/<version>/integrated` 路线只作为外部兼容来源存在，不再是本仓库主目录模型。

## 规则维护原则

规则源文件必须保持文本 diff 友好，不迁移到数据库或二进制格式。

`rules/contextual/**/*.yaml` 是当前主要维护布局；`rules/contextual_aliases.yaml` 暂时保留为迁移源和兼容输入：

```text
rules/contextual/champions/*.yaml
rules/contextual/modes/*.yaml
rules/contextual/sfx/*.yaml
rules/low_trust_events.yaml
```

拆分只改变维护形态，不改变匹配语义。运行时仍应把所有分片聚合为一个 `RuleBook`，不能按当前英雄只加载局部规则，否则会丢失跨 owner 命中。

`rules/low_trust_events.yaml` 是已审阅的整句 exact fallback。它用于把默认低置信噪声移出主报告，但仍会在 `semantic_slots.low_trust_rule` 标记 `trust: low`，并可通过 CLI 参数单独导出。

## 审计与报告

规则命中审计的目标是辅助维护，而不是替代规则。

需要回答的问题包括：

- 哪些手写规则命中过
- 哪些规则长期 0 命中
- 哪些事件依赖官方参考匹配
- 哪些事件依赖推断逻辑
- 小批量输入的审计范围是什么

因此报告必须记录输入范围、raw 数量、参考版本、reference 路径和命令摘要。小批量审计不能被误读为全库覆盖率。

## 对外数据

下游程序应读取 `dist/normalized/`，不要依赖中间 JSONL、报告、review site 或本地 game-data。

当前数据 contract 分层如下：

- `raw/events/` 是源语料层，`raw_event_name` 在所有派生产物中保持原样。
- `reference/` 是实体、英雄、皮肤、地图和 skinline 的 label/reference 层。
- `rules/` 与 `overrides/` 是语义解释层，负责把事件名归一为技能、状态、动作、阶段和上下文别名。
- `dist/normalized/` 是生成的消费者层，按实体拆分并保留兼容 `display` 预览。
- `translation` 是 normalized 记录内的本地化渲染层；需要页面上下文省略、不同语言语序或调试审计时，应优先使用它，而不是只依赖固定 `display.zh_cn_short/detail`。

普通用户展示文案应回答“这个音频什么时候触发”，例如：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
普朗克大招升级一阶
```

不要把 `Play_vo`、皮肤编号、`cast3D`、`2D`、`3D`、`voProcessing` 等技术 token 暴露到短展示文案。调试信息可以进入 `debug_label` 或审计字段。

## 自动更新边界

自动 raw 更新入口和手动 GitHub Actions workflow 已纳入仓库。GAME manifest/WAD/BIN 抽取是可选更新来源，不是对外数据契约。

自动更新的关键边界：

- `lol-game-data-snapshot` 可提供英雄列表、皮肤列表和 LCU snapshot 状态。
- 事件名来源必须是 GAME manifest 中的 WAD/BIN，例如 `DATA/FINAL/Champions/Aatrox.wad.client`。
- `raw/update-state.json` 只作为最后成功处理游标。
- GAME/LCU 无变化时 workflow 不应生成空报告。

## 当前非目标

当前仓库不默认承担以下职责：

- 音频提取逻辑本体
- UI 展示逻辑
- 在线服务部署
- 大体积原始游戏数据归档
- 手工编辑最终 normalized 产物
