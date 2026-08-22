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
| `data/` | 小型 schema、代表样例和质量基线；`16.16` 仅作为本轮冻结验收 profile |
| `tests/` | 确定性行为、contract 与文件系统集成测试 |
| `dist/` | 被忽略的隔离 runs、生成数据、报告、review site 和中间工作目录 |

## 关键模块边界

- 来源边界：`http_fetch.py`、`online_sources.py`、`game_manifest*.py`、`game_wad_events.py`、`data_dragon_source.py`、`tencent_hero_list_source.py` 负责获取并校验外部数据，不解释事件语义。
- 累积更新边界：`reference_ingestion.py`、`reference_collection.py`、`raw_update.py`、`source_sync.py`、`update_pipeline.py` 把外部输入转换为仓库内 reference/raw，并负责差集、游标和失败恢复。
- 语义边界：`decomposition_*.py`、`rules.py`、`model.py`、`match_audit.py` 读取 raw/reference/rules/overrides，输出保留来源、置信度和未知项的结构化拆解。
- 消费者输出边界：`normalized_contract.py` 负责版本化 record identity、来源身份与 additive support/render/review 视图；`normalized_translation_*.py`、`normalized_output.py`、`normalized_validation.py`、`tiered_quality.py`、`quality_metrics.py`、`display_labels.py` 把拆解记录转换为稳定 normalized contract。`tiered_quality.py` 拥有 Stable absolute gates、accepted corpus 和 support diff，并对带未解析 residual 的单字母技能键启发式 fail-closed；旧 `quality_metrics.py` 只保留全局趋势棘轮。champions/maps 分片中的等价拆解记录先合并 provenance；若最终 canonical identity 仍冲突则 fail-closed。
- 数据集身份边界：`dataset_identity.py` 对冻结 profile 做版本/计数预检，对来源模式、GAME/LCU 版本、同轮 LCU 证据以及可用的 manifest 身份连同 raw、reference、rules、overrides、契约资产和生成器源码计算规范化摘要，并从有序 payload 文件摘要派生 `content_digest`/`dataset_id`；运行时间与路径只属于后续 run metadata。
- 运行与提升边界：`dataset_runs.py` 独占创建 `.staging` run，固化 success/failure 终态，
  以 completed run + 小型 `approved-local.json` pointer 代替目录覆盖；promotion 在锁内执行
  clean/attestation/report/payload/CAS 门禁与原子替换。`product_review.py` 生成候选绑定的
  Review Site/bundle，`review_decisions.py` 校验版本化决定，以 OS advisory lock + 可恢复 journal 原子应用长期源并为最终数据集
  approval 提供 fail-closed attestation。长期 source target 只哈希 Stable pattern/corpus/scope 的稳定语义合同；
  baseline-relative support diff、逐 pattern/label 样例计数和诊断分布保留为页面证据并继续绑定最终 dataset approval；
  冻结的 Stable pattern 数、ID digest 与总记录数仍在 source hash 内。目标集合或哈希变化
  回到 source review，决定/corpus 结构或事务摘要损坏仍硬失败。默认 lookup 只经 pointer 解析。
- 模式报告边界：`pattern_catalog.py` 从 semantic frame 汇总 Pattern Catalog inventory，并仅把 exact approved 且结构合格的 pattern 投影为 Stable；它保留 pattern 的全部非实体中文标签变体，供 Product Review 检查语义漂移。`scripts/report_translation_patterns.py` 只是把 JSON/Markdown 写入调用方指定的被忽略报告路径，不反向修改规则源。
- 演示投影边界：`demo_site.py` 只读同一标准 dataset run 的 normalized、validator 报告与 reference；它先验证 run identity 和 record count，再把结构化实体证据及已确认模式前缀保守归入基础英雄，同时生成显式地图分片和紧凑 SFX 残差/未归属 Review 分片。页面先按“已确定数据 / 需要 Review”分流，再在各自内部按英雄 / 地图浏览；这里的“已确定”只表示实体浏览投影有结构化归属，不改变记录的 `review_status` 或 dataset approval。显式 `semantic.slots.map` 会按 `reference/maps` 本地化；没有结构化地图槽位时不根据 raw 名、来源路径或文件名推断地图。输出使用相对 URL、版本化分片请求和平台中立托管元文件，可作为本地或公共只读静态站；它不修改 raw/reference、不写回 Review 决定、不参与 promotion，也不把未分配系统事件猜成英雄事件。英雄和皮肤图片只引用 Data Dragon，不进入仓库产物。
- 入口边界：`full_refresh.py` 提供可注入 runner 的编排逻辑；`scripts/run_full_refresh.py`、下载/导入/构建/查询/模式报告脚本和 `normalized_lookup_cli.py` 负责 CLI 适配。

`scripts/` 可以依赖共享模块，共享模块不得依赖脚本。来源适配器不得直接写最终 normalized；拆解层不得依赖 `dist/` 中的上次生成结果；展示渲染不得改写 raw 或 reference。

## 核心数据流

```text
外部 GAME/LCU/Data Dragon/官网来源
  -> 当轮 live 下载与来源校验
  -> raw/events/ + reference/

raw/events/ + reference/ + rules/ + overrides/
  -> decomposition records + match audit
  -> normalized translation builder
  -> dist/runs/.staging/<run>/{normalized,reports,work}
  -> dist/runs/completed/<run>/ or dist/runs/failed/<run>/
  -> reports/review-site/{review-bundle.json,index.html}
     (Stable/Experimental 置信度、英文残留、qualifier 覆盖、非实体标签变体)
  -> dist/runs/approved-local.json (promotion only)
  -> global validator + Stable quality gates/support diff + trend metrics/ratchet

semantic frame + rules/translation_patterns.yaml
  -> read-only Pattern Catalog report (Stable exact scope + Experimental pending/deferred)

canonical normalized rows + committed data/schema + data/examples
  -> normalized-events.jsonl + pattern-catalog.json + compatibility by-entity/
  -> deterministic manifest content identity + payload file digests

explicit candidate or approved-local normalized + same-run validation + reference
  -> dist/demo-site/{index.html,hosting metadata,data/index.json,data/champions/*.json,data/maps/*.json,data/review/*.json}
```

更新链先在临时位置准备变更，确认差集和输入有效后再替换累计数据与游标。解释链把所有 contextual 分片聚合为一个 RuleBook，避免按当前 owner 局部加载而丢失跨实体命中。消费者链从统一 frame 注册表派生必需角色和模板，减少 builder、validator 与 renderer 之间的重复定义。

## 入口与组合方式

- `uv run python scripts/download_sources.py`：准备字符串表、大厅 JSON、皮肤 BIN、RST hash 与 raw round。
- `uv run python scripts/update_raw_events.py`：合并 raw/reference 并写更新报告。
- `uv run python scripts/run_full_refresh.py`：在单个隔离 run 内依次执行 decompose、build、validate、quality、review、metrics、ratchet；quality 阶段以冻结的 decision/pattern-set/record-count 检查 Stable 绝对范围，并将所有 support-level 新增、移除、晋级和降级写入结构化 diff（Stable 降级阻断）；review 阶段生成与 candidate 身份绑定的静态页面和 bundle。Stable/global/support-diff 任一自动门禁失败即固化 failed，成功固化 completed candidate，但人工 Review 前不自动 promotion。
- `uv run python scripts/manage_review.py`：构建、校验并应用版本化人工决定；source review 仅允许全接受时同时替换 decision source 与 accepted corpus，其他 action fail-closed。
- `uv run python scripts/promote_dataset_run.py`：对带正式门禁 attestation 的 clean completed run 做 lock/CAS/payload 复核；当 candidate 等待人工审核时还必须验证 `--decisions` 指向的 dataset approval，再原子替换 approved pointer。
- `uv run lookup-normalized-event`：按事件名查询生成的 normalized 记录并渲染指定上下文。
- `uv run python scripts/build_demo_site.py`：从 approved-local 或显式 normalized candidate 生成被忽略的平台中立静态 Demo；显式 candidate 必须配对同 run `--validation-json`，页面用“已确定 / Review × 英雄 / 地图”两级导航组织实体浏览和残差/未归属队列。
- `uv run python scripts/report_translation_patterns.py`：只读生成 Pattern Catalog JSON/Markdown 报告；默认规则输入为 `rules/translation_patterns.yaml`，输出路径必须由 `--output-json` 与 `--output-md` 显式指定。
- `.github/workflows/ci.yml`：锁定环境中的静态检查、类型检查与离线测试。
- `.github/workflows/deploy-demo-pages.yml`：仅手动触发；从冻结输入完成完整 refresh 和静态 Demo 构建后，通过官方 Pages artifact 部署，不复用或提升本地 candidate。
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
- approved pointer 指向的 `normalized/manifest.json`、`normalized-events.jsonl`、`pattern-catalog.json`、`quality-summary.json`、schemas/examples、兼容分片及记录内 `translation` 是下游数据 contract。V2 manifest 的文件摘要与内容身份由 validator 复算，任一 payload 漂移均为硬错误。
- `TRANSLATION_SCHEMA_VERSION`、`FRAME_VERSION`、frame registry 和 required roles 共同约束 renderer/validator；变更时必须同步 builder、validator、测试和文档。`Stop/Mute/UnMute` 等 playback control 是附加结构角色，已解析 phase 仍保留在 frame；renderer 只用共享谓词抑制与 playback 冲突的冗余 phase 展示，validator 仅对同一谓词证明的有意省略放行。`system_sfx_event` 只接收完整已审路径，或 reference 能证明装备身份的生命周期；装备余段必须命中 `display_labels.system_sfx.item_action` 的“装备 ID/动作词干”精确组合，并合入既有 effect，不新增 frame role。其他地图、模式与装备工程 cue 保持 Experimental/Review。
- 英雄攻击事件的正整数 `attack_variant` 同时保留在 semantic slot 与 `translation.arguments.attack.variant`，并必须出现在中文 attack label 和最终 display；展示使用原始变体后缀（如“普通攻击2”），不得在没有额外证据时解释为攻击次序。人工别名已含同号时不得重复追加。
- `zh_CN` 是当前正式渲染 locale；VO 优先支持，SFX 不删除但按 Stable、Experimental、Unsupported 区分能力边界。
- `16.16` 是本轮 frozen acceptance profile，不参与未来 live 版本的默认选择。正式消费者 Pattern Catalog schema、本地 promotion 机制、分层绝对门禁 attestation 与全接受 source review importer 已实现；非 accept 决定的产品修复、首个 approved 数据集与远端发布仍是后续工作。当前 inventory 仍使用独立的报告 schema。

当前没有在线服务、数据库或跨进程消息协议。若未来引入这些边界，必须先明确数据所有权、版本兼容、失败恢复和部署环境，再更新本设计与项目约束。
