# scripts

这里存放仓库入口脚本。脚本应保持轻量、面向具体任务；共享逻辑应放在 `src/lol_event_semantics/`。

## 主流程

采集当前 live 的英雄、皮肤与地图事件（云端全新环境使用 Python 3.13）：

```powershell
uv sync --dev --extra download --locked
uv run python scripts/collect_raw_events.py --output-root dist/collection `
  --champions all --maps all
```

`all` 是两类的默认范围；`none` 关闭一类，或用逗号分隔的英雄别名/数字 ID、地图 ID 指定子集。
采集器把同轮目录声明的皮肤/炫彩及当前地图根 BIN 与真实 WAD 索引核对后提取，排除 TFT/TFTM。
它不穷举任意 WAD 内所有 BIN，也不直接修改累计 `raw/reference`。
成功输出 `dist/collection/rounds/<digest>/raw/{champions,maps}/*.txt`，同目录包含来源清单、
采集计划与报告；`latest.json.run` 是最后成功目录名。保留整个输出根即可在重跑时复核并复用
已完成实体；失败不更新 latest，缺失或损坏的缓存重新采集。新 live 结果导入前仍须建立并审阅对应
版本化 profile，不能直接交给只接受 16.16 的冻结更新流程。

准备 reference game-data JSON：

```powershell
uv run python scripts/extract_game_data_json.py `
  --output-root dist\reference\16.5\game-data `
  --wad <default-assets.wad> `
  --wad <default-assets2.wad> `
  --locale default
```

导入内部 reference 合集：

```powershell
uv run python scripts/import_reference_data.py `
  --reference-root reference `
  --game-data-root dist\reference\16.5\game-data `
  --data-dragon-root <data-dragon-root> `
  --game-version 16.5 `
  --data-dragon-version 16.5.1
```

在线导入内部 reference 合集：

```powershell
uv run python scripts/import_reference_data.py `
  --online `
  --reference-root reference `
  --game-version 16.12 `
  --game-data-base-url <snapshot-root-containing-global>
```

从本地事件 JSON 或显式 WAD/BIN 请求表生成本轮 raw round txt：

```powershell
uv run python scripts/extract_raw_events_from_game.py `
  --mode event-files `
  --input-root .temp\raw-event-payloads `
  --output-root .temp\raw-events-round
```

更新累计 raw/reference：

```powershell
uv run python scripts/update_raw_events.py `
  --mode online `
  --reference-root reference `
  --raw-root raw\events `
  --state-file raw\update-state.json `
  --raw-input-root .temp\raw-events-round `
  --game-version 16.16 `
  --game-data-base-url <snapshot-root-containing-global> `
  --sources-manifest .temp\source-identity\source-manifest.json `
  --frozen-profile data\frozen-profile.v1.json `
  --report-root .temp\update-report
```

`--mode online` 必须同时提供本轮可移植远端来源清单和 frozen profile；入口会在任何在线 reference
下载前拒绝缺失、`local` 模式、不符合 profile 的来源证据，以及跨 frozen 系列的 GAME/显式 Data Dragon
请求版本。`--mode local` 仍可省略两者，或成对提供以对
staging 输出执行 frozen preflight。

构建拆解 JSONL 和报告：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --reference-root reference `
  --output-root .temp\manual-candidate\work `
  --report-root .temp\manual-candidate\reports
```

如需单独审阅低信任整句规则命中，追加 `--write-low-trust-rules`，会输出 `low-trust-rules.md/csv/jsonl`。

构建本地 review site：

```powershell
uv run python scripts/build_review_site.py `
  --input .temp\manual-candidate\work\champions.jsonl `
  --input .temp\manual-candidate\work\maps.jsonl `
  --output .temp\manual-candidate\review-site\index.html
```

构建 normalized 下游数据：

```powershell
uv run python scripts/build_normalized_outputs.py `
  --decomposition-root .temp\manual-candidate\work `
  --output-root .temp\manual-candidate\normalized `
  --repo-root . --raw-root raw\events `
  --reference-root reference --rules-root rules --overrides-root overrides `
  --reference-version 16.16 `
  --pattern-policy rules\translation_patterns.yaml `
  --contract-assets-root data `
  --sources-manifest data\frozen-sources-manifest.v1.json `
  --frozen-profile data\frozen-profile.v1.json
```

生成只读 Pattern Catalog 报告（候选仍为 Experimental，scope 可为 approved、pending 或 deferred；不写入 normalized 或规则源）：

```powershell
uv run python scripts/report_translation_patterns.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --output-json .temp\manual-reports\translation-patterns.json `
  --output-md .temp\manual-reports\translation-patterns.md
```

该报告从 semantic frame 派生模式，不以 `BaseName,Label` 作为主模型。默认规则输入为 `rules/translation_patterns.yaml`；JSON 和 Markdown 输出必须显式给定并应位于被忽略的报告目录。当前 inventory 有独立版本化报告 schema；首个 approved-local promotion 已完成，远端稳定发布仍不属于当前实现。

本地一键刷新全链（decompose→build→validate→quality→review→metrics→ratchet）：

```powershell
uv run python scripts/run_full_refresh.py `
  --raw-root raw\events `
  --reference-root reference `
  --runs-root dist\runs `
  --baseline data\quality-baseline.json
```

每次调用先独占创建 `.staging/<run-id>`，所有 work/normalized/reports 都绑定该 run；任一步阻断失败即写 `failure.json` 并固化到 `failed/`，成功写 `completion.json` 后同卷 rename 到 `completed/`。`--allow-ratchet-regression` 可让 ratchet 恶化只告警并保留 completed candidate，但该 candidate 不可 promotion；`--update-first`（需配 `--game-version` `--game-data-base-url`）在核心链前先跑在线 raw/reference 更新。

`reports/review-site/` 中的产品审核页导出候选绑定的决定。页面展示 Stable/Experimental
置信度、Stable 标签英文残留、qualifier 覆盖和每个 pattern 的全部非实体中文标签变体，
用于识别语义漂移；它们不构成自动接受或晋级。全接受 source review 后，可用统一入口原子
应用并验证；任何需要修改的 action 都会在写前停止，留给 P5-C2 集中修规则：

```powershell
uv run python scripts/manage_review.py apply `
  --bundle dist\runs\completed\<run-id>\reports\review-site\review-bundle.json `
  --decisions <review-decisions.json>
```

promotion CLI 在自动门禁之外还验证最终 dataset approval；没有 `--decisions` 时，等待人工审核的真实候选会 fail-closed：

```powershell
uv run python scripts/promote_dataset_run.py `
  --runs-root dist\runs `
  --run-id <run-id> `
  --decisions <dataset-approval.json> `
  --expected-previous-digest none
```

按事件名测试 normalized 查询和渲染：

```powershell
uv run lookup-normalized-event `
  --event Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate `
  --mode main `
  --known-context champion,skin
```

## 当前入口脚本

- `collect_raw_events.py`：当前 live raw 采集入口；自动核验成对 GAME/LCU、固定提交快照和 WAD 索引，发现目录声明范围并按实体续跑，输出 raw round、同轮来源和报告。共享逻辑在 `collection.py`、`collection_sources.py`、`collection_discovery.py`。
- `export_raw_events.py`：从外部 `manifest/<version>/events` 导入 raw 的兼容入口；目标 raw 布局是累积式 `raw/events/`，后续会在 raw-update 工作中调整。
- `extract_game_data_json.py`：从 WAD 提取已知 `rcp-be-lol-game-data` JSON 路径，并支持可选 CommunityDragon fallback。
- `import_reference_data.py`：把本地或在线 GAME data 和 Data Dragon 导入仓库内部 `reference/` 精简合集，包括英雄、地图和皮肤系列分组。
- `extract_raw_events_from_game.py`：从本地 GAME WAD/BIN 或已抽取事件 JSON 生成标准 raw round txt。
- `check_raw_update.py`：只读检查本轮 raw round txt 相对累计 `raw/events/` 的差集。
- `download_sources.py`：保留字符串表、大厅 JSON、RST 与本地下载兼容能力；旧 raw-bins 仍使用 `range(100)` 探测，不承担完整 raw 采集。其 producer 游标带版本化 `schema_version`；`prepare_source_manifest.py` 可把旧游标规范化为可移植来源清单，或在显式 manifest 抽取成功后构建清单。该适配入口按 frozen profile 校验，拒绝 `local` 来源冒充 workflow 采集；新 collector 直接生成同轮清单，无需旧游标转换。
- `update_raw_events.py`：把本轮 raw round txt 差集合并进累计 `raw/events/`，并可串起 reference 导入；online 模式强制提供同轮可移植远端来源清单与 frozen profile，并在下载前验证来源合同；local 模式可成对提供二者。随后对 staging raw/reference 做 frozen preflight，再原子替换长期目标。
- `.github/workflows/update-raw-events.yml.disabled`：已停用的 frozen V1 16.16 更新模板，保留 Python 3.13 环境、同轮 raw/来源、隔离 full refresh、成功候选与失败诊断分离、完整门禁后才 push 的原定义。当前直接运行本地或云电脑 CLI；本地完整流程验证通过且正式发布后，才另行决定是否启用 Actions。未来 live 版本仍须先建立新的版本化 profile/来源合同，模板不自动 promotion。
- `decompose_events.py`：把 raw event name 拆成 lexical atoms、semantic slots、JSONL 产物和 review reports；解释阶段只读取内部 `reference/`，可用 `--write-low-trust-rules` 额外导出低信任规则命中表。
- `build_review_site.py`：从 decomposition JSONL 构建通用维护 review 页面；它不是 P5 产品批准入口。
- `build_demo_site.py`：从 approved-local 或显式 normalized candidate 生成 `dist/demo-site/` 平台中立静态 Demo V3；显式 candidate 必须传同 run `--validation-json`，输入须带实体归属证据。六类实体与“全部 / 已确定 / 待反馈”独立筛选，轻量索引之后只加载当前实体分片；中文复用消费投影，详情保留身份、来源归属与问题。它不写回 Review 决定、不执行 promotion，完整说明见 `docs/DEMO_SITE.md`。
- `build_consumption.py`：从显式本地 `--normalized-root` 或默认 approved-local 导出 web/software/lookup，`--output-root` 必须尚不存在。默认全部 profile、支持级别与资产类型；可用 `--profile`、`--support-level`、`--asset-kind`、`--entity kind:alias` 筛选。完整 normalized 审计数据保留不变，格式与客户端用法见 `CONSUMPTION.md`。
- `validate_consumption.py`：用 `--root` 指定单个 profile，校验 manifest、文件闭包、摘要及记录/路由一致性；可配 `--normalized-root` 重新生成并核对源中文投影。验证不授予数据集批准。
- `compare_event_identity.py`：用 `--previous-root`、`--current-root` 指定两份 normalized，用 `--output-json` 保存身份迁移报告。先比较 `record_id` 与由事实派生的 `event_id`，再以原名和来源重叠确定一对一迁移；歧义不自动合并。
- `build_normalized_outputs.py`：从 decomposition JSONL 生成指定 candidate root；先对显式 raw/reference/rules/overrides/source/profile/contract 输入做冻结预检与树摘要，再合并 champions/maps 重叠分片中的等价记录，写出唯一 `record_id`、来源 provenance、pattern/support/render/review 字段、`quality-summary.json` 和确定性 manifest content identity。已核验 qualifier 按结构化 key 渲染，未知 key 保留待审 fallback；皮肤短展示移除重复 owner。V2 首选文件是 `normalized-events.jsonl`、`pattern-catalog.json` 与 `quality-summary.json`，同时复制 schema/examples/accepted corpus/无源码 consumer，并保留兼容视图。
- `report_translation_patterns.py`：只读 Pattern Catalog 维护报告入口；默认读取 `rules/translation_patterns.yaml`，以 semantic frame 汇总 pattern 并将 JSON/Markdown 写入显式指定的报告路径。只有 exact approved 且结构合格的 pattern 为 Stable；未来同 family 新 ID 仍回到 Experimental/pending。
- `check_tiered_quality.py`：读取 candidate 内的确定性质量摘要和全局 validation 报告，独立执行冻结 decision、91-pattern 集合摘要与 4,127-record 总数的 Stable absolute gates，并相对当前 approved-local 输出 `quality-diff.json`；带未解析且无显式证据 residual 的单字母技能键启发式会阻断 Stable。所有 support-level 新增、移除、晋级和降级都进入 Review 明细，Stable pattern/record 降级会阻断，Experimental warning 只报告。
- `manage_review.py`：构建 candidate-bound 产品 Review Site/bundle，导入并验证版本化决定；长期 source target 只哈希 Stable pattern/corpus/scope 的稳定语义，包含冻结的 Stable pattern 数、ID digest 与总记录数；baseline-relative support diff、逐 pattern/label 样例计数和诊断分布作为页面证据并继续绑定最终 dataset approval。全接受 source review 使用 journal 与 OS advisory lock 更新长期 decision source/accepted corpus，硬退出后锁所有权自动释放、下一次 apply 先恢复 journal；目标漂移回到 source review，决定/corpus 损坏与其他 action fail-closed。
- `run_full_refresh.py`：本地一键刷新薄壳，创建隔离 run 后按序串联 decompose→build→validate→quality→review→metrics→ratchet 七个子进程（可选 `--update-first`）；阻断失败固化 failed，成功固化 completed candidate，不自动 promotion。自动门禁只证明技术状态，用户 Review 前 `eligible=false`。步骤串联逻辑在 `src/lol_event_semantics/full_refresh.py`，run/pointer 状态机在 `dataset_runs.py`。
- `promote_dataset_run.py`：对 completed run 做 clean generator、正式 eligibility、报告/payload 摘要、独占锁与 expected previous digest CAS 门禁；等待人工 Review 的候选还必须验证 `--decisions` 中的 dataset approval，再原子替换 `approved-local.json`。冲突/锁/门禁分别非 0 退出。
- `validate_normalized_outputs.py`：校验指定 candidate/approved normalized root；硬错误覆盖 manifest/summary/JSONL 一致性、manifest 文件摘要与 content digest 复算、实体规范化、记录 schema、translation 结构和 renderer smoke，质量警告覆盖可疑实体归属、重复 raw、fallback label、低信任规则残留等；`--fail-on-warning` 可在 warning 存在时返回 2。
- `lookup-normalized-event`：项目 console script，默认只解引用 `dist/runs/approved-local.json`；维护者可显式传 `--normalized-root` 查询 candidate。按 `raw_event_name` 精确匹配，优先 V2 单流、缺失时回落兼容分片。`scripts/lookup_normalized_event.py` 仅保留为兼容包装。
- `migrate_contextual_aliases.py`：把 `rules/contextual_aliases.yaml` 迁移为 `rules/contextual/**/*.yaml` 分片。
- `translate_events.py`：遗留/兼容翻译 CLI，依赖外部 `rcp-be-lol-game-data`。
- `build_translation_preview.py`：遗留/兼容翻译预览生成器。
- `build_entity_db.py`：遗留/兼容本地实体库生成器。
