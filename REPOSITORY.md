# 仓库说明

## 仓库定位

`lol-event-semantics` 用来维护《英雄联盟》音频事件名的累计语义词典：包括语义规则、原始事件语料、审计报告和生成产物。

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
  -> dist/runs/.staging/<run>/{work,normalized,reports}/
  -> dist/runs/completed/<run>/ or dist/runs/failed/<run>/
  -> dist/runs/approved-local.json
```

各目录职责：

- `raw/events/`：按实体累积的 raw event name 源语料。不按版本替换，不因为新版本重做而删除旧事件。
- `rules/`：长期维护的规则、词典、上下文别名和 residual modifier。
- `overrides/`：人工 review 后的精确覆盖和兼容决策。
- `reference/`：仓库维护的累计精简参考合集，包含 `champions/`、`maps/`、`skinlines/`、`skins/`、`items/`，解释阶段只读取这里。
- `.temp/reference-sources/`：被忽略的版本敏感参考输入缓存，不对下游承诺稳定。
- `dist/collection/`：本地或云电脑直接运行 `scripts/collect_raw_events.py` 的独立采集结果、来源证据和可复用缓存；成功轮次由 `latest.json.run` 指向，不自动合并累计源。
- `dist/sources/`：`scripts/download_sources.py` 的数据源落点（字符串表 / 大厅 JSON / 皮肤 bin / RST key 字典 + 版本游标 `manifest.json`）；layout 与手工放置规范见根 README「目录契约」。
- `dist/runs/`：事务性数据集运行根；`.staging` 隔离写入，`failed` 保留诊断，`completed` 保存不可变候选，`approved-local.json` 原子指向唯一默认消费数据集。每个 completed run 的 `normalized/` 以单流/catalog/quality-summary 为 V2 首选并保留兼容分片；manifest 固化输入树摘要、payload SHA-256 与 `content_digest`。
- `dist/consumption/<export-id>/`：从明确 normalized 派生的 web/software/lookup 独立消费包，格式和校验方式见 `CONSUMPTION.md`。
- `dist/demo-site/`：只读 Demo V3，使用 `data/index.json` 与惰性加载的 `data/entities/*.json` 统一浏览英雄、装备、地图、模式实体、公共系统、未知实体。
- `dist/work/`、`dist/reports/runs/`：仅保留给显式维护命令的旧式独立输出；正式 full refresh 不再写入。

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

下游程序应通过 `dist/runs/approved-local.json` 解析 completed `normalized/`，不要默认读取 staging、failed、未批准 candidate、中间 JSONL、报告、review site 或本地 game-data。

当前数据 contract 分层如下：

- `raw/events/` 是源语料层，`raw_event_name` 在所有派生产物中保持原样。
- `reference/` 是实体、英雄、皮肤、地图和 skinline 的 label/reference 层。
- `rules/` 与 `overrides/` 是语义解释层，负责把事件名归一为技能、状态、动作、阶段和上下文别名。
- approved completed run 的 `normalized/` 是完整审计层，保留源证据、语义 frame、兼容字段和多语言渲染信息。V2 审计消费者优先读取按 `record_id` 排序的 `normalized-events.jsonl`；旧消费者可继续读取按实体拆分的兼容路径和 `display` 预览。
- `web`、`software`、`lookup` 是审计层下的消费投影，分别面向按需加载网页、逐行导入软件和单文件原名查询；默认保留全部支持级别、资产类型及同名多候选。字典编码与主体上下文省略不改变原名、状态和源定位，也不缩减 normalized 审计内容。
- `translation` 是 normalized 记录内的本地化渲染层；需要页面上下文省略、不同语言语序或调试审计时，应优先使用它，而不是只依赖固定 `display.zh_cn_short/detail`。
- normalized 行以 `record_id` 作为包含来源集合的审计快照身份，另以原名与逻辑实体生成 `event_id`；补充来源不改变事件身份，纠正实体则可能改变。`source_scope` / `provenance` 区分 champions、maps 等来源。不能把 raw 名称误当作全局唯一键；身份迁移工具保留多候选歧义，只确认有来源证据的一对一关系。
- 逻辑实体归属由来源资产与 reference 精确证据优先确认；同层冲突保留未知，人工 `keep` / `downgrade` 不能覆写强来源。模式上下文不直接证明独立实体，数字 owner 只有命中装备 reference 才确认为装备。

V1 以 `zh_CN` 作为正式渲染 locale，并以 VO 为优先支持对象；SFX 保留在语义词典中，支持状态为 Stable、Experimental 或 Unsupported。只有 exact approved 且结构合格的 pattern 可进入 Stable，范围以长期 decision source 与生成 catalog 为准；公共 catalog 与维护 inventory 都从 semantic frame 派生。Stable absolute gates 与 accepted corpus 已进入本地 full refresh，用户 Review/promotion 仍是 approved-local 的独立后续门禁。Demo 的全部/已确定/待反馈筛选与实体分类、支持级别和人工批准分别表达，不构成审核工作流。

普通用户展示文案应回答“这个音频什么时候触发”，例如：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
普朗克大招升级一阶
```

不要把 `Play_vo`、皮肤编号、`cast3D`、`2D`、`3D`、`voProcessing` 等技术 token 暴露到短展示文案。调试信息可以进入 `debug_label` 或审计字段。

## 自动更新边界

raw 采集与更新入口可在本地或云电脑直接执行。GitHub Actions 当前全部停用，更新和部署定义保留为 `.github/workflows/*.yml.disabled` 模板；本地完整流程验证通过且正式发布后，再另行决定是否恢复。提交或交付门禁由本地命令负责。GAME manifest/WAD/BIN 抽取是可选更新来源，不是对外数据契约。每轮只采集当时的 live 输入；累计语料保留已合并事件，但不支持指定历史版本下载、重建或回放。当前累计输入为 `16.20` V2 frozen profile，历史 `16.16` V1 保留不变；两者都不是固定的最新版本默认值。

自动更新的关键边界：

- `lol-game-data-snapshot` 可提供英雄列表、皮肤列表和 LCU snapshot 状态。
- 事件名来源必须是 GAME manifest 中的 WAD/BIN，例如 `DATA/FINAL/Champions/Aatrox.wad.client`。`scripts/collect_raw_events.py` 按同轮目录声明的皮肤/炫彩及地图根 BIN 采集轮次 txt，直接保存来源清单并支持按实体续跑；它不穷举任意 WAD 内的所有 BIN。旧 `download_sources.py` 的 `range(100)` 皮肤探测只保留为兼容能力，不承担完整 raw 采集。
- 整轮下载出自同一份 manifest，各数据源天然同版本；GAME/LCU manifest 成对校验（major.minor 一致）。
- `raw/update-state.json` 只作为最后成功处理游标。
- 重复采集应复核并复用身份未变的实体结果；成功报告与来源清单仍须反映本轮实际范围。

## 当前非目标

当前仓库不默认承担以下职责：

- 音频提取逻辑本体
- 下游应用的完整 UI 与业务逻辑（仓库仅提供消费客户端和只读静态 Demo）
- 在线服务部署
- 大体积原始游戏数据归档
- 手工编辑最终 normalized 产物
- 将 Pattern Catalog 候选直接提升为 Stable，或把只读报告作为远端发布产物
