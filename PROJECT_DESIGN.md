# lol-event-semantics 项目设计

## 顶层目录职责

| 路径 | 职责 |
| --- | --- |
| `src/lol_event_semantics/` | 共享 Python 模块：来源适配、更新、拆解、翻译、生成、校验和查询 |
| `scripts/` | 参数解析与任务编排薄入口 |
| `raw/events/` | 不可变、累计的事件名源语料 |
| `rules/` | 可复用的归一化规则、词典、模板和上下文分片 |
| `overrides/` | 人工审阅后的精确覆盖、兼容别名和归属决策 |
| `reference/` | 从外部来源收敛出的累计精简实体参考 |
| `data/` | 小型 schema、代表样例和质量基线 |
| `tests/` | 确定性行为、contract 与文件系统集成测试 |
| `dist/` | 被忽略的生成数据、报告、review site 和中间工作目录 |

## 关键模块边界

- 来源边界：`http_fetch.py`、`online_sources.py`、`game_manifest*.py`、`game_wad_events.py`、`data_dragon_source.py`、`tencent_hero_list_source.py` 负责获取并校验外部数据，不解释事件语义。
- 累积更新边界：`reference_ingestion.py`、`reference_collection.py`、`raw_update.py`、`source_sync.py`、`update_pipeline.py` 把外部输入转换为仓库内 reference/raw，并负责差集、游标和失败恢复。
- 语义边界：`decomposition_*.py`、`rules.py`、`model.py`、`match_audit.py` 读取 raw/reference/rules/overrides，输出保留来源、置信度和未知项的结构化拆解。
- 消费者输出边界：`normalized_translation_*.py`、`normalized_output.py`、`normalized_validation.py`、`quality_metrics.py`、`display_labels.py` 把拆解记录转换为稳定 normalized contract，并校验 manifest、summary、JSONL 与 renderer 一致性。
- 入口边界：`full_refresh.py` 提供可注入 runner 的编排逻辑；`scripts/run_full_refresh.py`、下载/导入/构建/查询脚本和 `normalized_lookup_cli.py` 负责 CLI 适配。

`scripts/` 可以依赖共享模块，共享模块不得依赖脚本。来源适配器不得直接写最终 normalized；拆解层不得依赖 `dist/` 中的上次生成结果；展示渲染不得改写 raw 或 reference。

## 核心数据流

```text
外部 GAME/LCU/Data Dragon/官网来源
  -> 下载与来源校验
  -> raw/events/ + reference/

raw/events/ + reference/ + rules/ + overrides/
  -> decomposition records + match audit
  -> normalized translation builder
  -> dist/normalized/ + dist/reports/
  -> validator + quality metrics + ratchet
```

更新链先在临时位置准备变更，确认差集和输入有效后再替换累计数据与游标。解释链把所有 contextual 分片聚合为一个 RuleBook，避免按当前 owner 局部加载而丢失跨实体命中。消费者链从统一 frame 注册表派生必需角色和模板，减少 builder、validator 与 renderer 之间的重复定义。

## 入口与组合方式

- `uv run python scripts/download_sources.py`：准备字符串表、大厅 JSON、皮肤 BIN、RST hash 与 raw round。
- `uv run python scripts/update_raw_events.py`：合并 raw/reference 并写更新报告。
- `uv run python scripts/run_full_refresh.py`：依次执行 decompose、build、validate、metrics、ratchet；阻断步骤失败即停止。
- `uv run lookup-normalized-event`：按事件名查询生成的 normalized 记录并渲染指定上下文。
- `.github/workflows/ci.yml`：锁定环境中的静态检查、类型检查与离线测试。
- `.github/workflows/update-raw-events.yml`：手动更新数据、构建并上传 normalized artifact。

## 错误模型

- 外部输入在来源边界报告 HTTP、版本、长度、格式或缺失文件错误；不合格输入不得进入累计源数据。
- 领域模型和规则加载对无效类型或内容抛出明确异常；拆解无法确定的 token 进入 unknown/ambiguous 与审计报告，而不是伪造成成功翻译。
- normalized validator 区分硬错误和质量告警；硬错误阻断发布，告警进入报告并由质量棘轮决定是否允许。
- full refresh 记录每步 argv、退出码和阻断属性，返回第一个阻断失败；仅明确标记为 non-blocking 的步骤可以失败后继续。

## 重要外部与下游契约

- 外部来源包括 Riot GAME/LCU manifest 与 CDN、lol-game-data-snapshot、Data Dragon、腾讯英雄列表和 CommunityDragon RST hash；来源权威度和用途见 README。
- `raw/events/**/*.txt` 与 `raw/update-state.json` 是累计更新输入和成功游标。
- `rules/**/*.yaml`、`overrides/**/*.yaml`、`reference/**/*.yaml` 是长期可审阅源。
- `dist/normalized/manifest.json`、`entity-summary.json`、`by-entity/**/*.jsonl` 及记录内 `translation` 是下游数据 contract。
- `TRANSLATION_SCHEMA_VERSION`、`FRAME_VERSION`、frame registry 和 required roles 共同约束 renderer/validator；变更时必须同步 builder、validator、测试和文档。

当前没有在线服务、数据库或跨进程消息协议。若未来引入这些边界，必须先明确数据所有权、版本兼容、失败恢复和部署环境，再更新本设计与项目约束。
