# scripts

这里存放仓库入口脚本。脚本应保持轻量、面向具体任务；共享逻辑应放在 `src/lol_event_semantics/`。

## 主流程

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
  --game-version 16.12 `
  --game-data-base-url <snapshot-root-containing-global> `
  --report-root .temp\update-report
```

构建拆解 JSONL 和报告：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --reference-root reference `
  --output-root dist\work\event-decomposition `
  --report-root dist\reports\runs\latest
```

如需单独审阅低信任整句规则命中，追加 `--write-low-trust-rules`，会输出 `low-trust-rules.md/csv/jsonl`。

构建本地 review site：

```powershell
uv run python scripts/build_review_site.py `
  --input dist\work\event-decomposition\champions.jsonl `
  --input dist\work\event-decomposition\maps.jsonl `
  --output dist\work\review-site\index.html
```

构建 normalized 下游数据：

```powershell
uv run python scripts/build_normalized_outputs.py `
  --decomposition-root dist\work\event-decomposition `
  --output-root dist\normalized `
  --reference-version cumulative `
  --pattern-policy rules\translation_patterns.yaml `
  --contract-assets-root data
```

生成只读 Pattern Catalog 报告（候选仍为 Experimental，scope 可为 approved、pending 或 deferred；不写入 normalized 或规则源）：

```powershell
uv run python scripts/report_translation_patterns.py `
  --normalized-root dist\normalized `
  --output-json dist\reports\runs\latest\translation-patterns.json `
  --output-md dist\reports\runs\latest\translation-patterns.md
```

该报告从 semantic frame 派生模式，不以 `BaseName,Label` 作为主模型。默认规则输入为 `rules/translation_patterns.yaml`；JSON 和 Markdown 输出必须显式给定并应位于被忽略的报告目录。当前 inventory 有独立版本化报告 schema；正式消费者 schema、promotion、review importer 与远端发布均不属于当前实现。

本地一键刷新全链（decompose→build→validate→metrics→ratchet）：

```powershell
uv run python scripts/run_full_refresh.py `
  --raw-root raw\events `
  --reference-root reference `
  --output-root dist\normalized `
  --work-root dist\work\event-decomposition `
  --report-root dist\reports\runs\latest `
  --baseline data\quality-baseline.json
```

任一步子进程非 0 退出即停并打印 `FAILED at step: <步骤名>`；`--allow-ratchet-regression` 可让 ratchet 恶化只告警不阻断，`--update-first`（需配 `--game-version` `--game-data-base-url`）在核心链前先跑在线 raw/reference 更新。

按事件名测试 normalized 查询和渲染：

```powershell
uv run lookup-normalized-event `
  --event Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate `
  --mode main `
  --known-context champion,skin
```

## 当前入口脚本

- `export_raw_events.py`：从外部 `manifest/<version>/events` 导入 raw 的兼容入口；目标 raw 布局是累积式 `raw/events/`，后续会在 raw-update 工作中调整。
- `extract_game_data_json.py`：从 WAD 提取已知 `rcp-be-lol-game-data` JSON 路径，并支持可选 CommunityDragon fallback。
- `import_reference_data.py`：把本地或在线 GAME data 和 Data Dragon 导入仓库内部 `reference/` 精简合集，包括英雄、地图和皮肤系列分组。
- `extract_raw_events_from_game.py`：从本地 GAME WAD/BIN 或已抽取事件 JSON 生成标准 raw round txt。
- `check_raw_update.py`：只读检查本轮 raw round txt 相对累计 `raw/events/` 的差集。
- `update_raw_events.py`：把本轮 raw round txt 差集合并进累计 `raw/events/`，并可串起 reference 导入。
- `.github/workflows/update-raw-events.yml`：手动触发的 raw/reference 更新 workflow；可消费同一 workflow run 中的前置 raw round artifact，也可在可选 `riotmanifest` 可用时用 GAME manifest + `.github/raw-event-requests.example.json` 形状的请求表生成 raw round，报告作为 artifact 保存。
- `decompose_events.py`：把 raw event name 拆成 lexical atoms、semantic slots、JSONL 产物和 review reports；解释阶段只读取内部 `reference/`，可用 `--write-low-trust-rules` 额外导出低信任规则命中表。
- `build_review_site.py`：从 decomposition JSONL 构建本地静态 review 页面；输出目录需要用本地 HTTP 服务打开，因为页面数据在 `review-data.json`。
- `build_normalized_outputs.py`：从 decomposition JSONL 生成 `dist/normalized/` 下游数据；先合并 champions/maps 重叠分片中的等价记录，再写出唯一 `record_id`、来源 provenance、pattern/support/render/review 字段。V2 首选文件是 `normalized-events.jsonl` 与 `pattern-catalog.json`，同时复制 `data/schema/`、`data/examples/` 并保留 `entity-summary.json`、`by-entity/`、`translation` 和 `display` 兼容视图。
- `report_translation_patterns.py`：只读 Pattern Catalog 报告入口；默认读取 `rules/translation_patterns.yaml`，以 semantic frame 汇总候选并将 JSON/Markdown 写入显式指定的报告路径。候选保持 Experimental，scope 决策独立记录，不创建 Stable 规则或消费者 contract。
- `run_full_refresh.py`：本地一键刷新薄壳，按序串联 decompose→build→validate→metrics→ratchet 五个子进程（可选 `--update-first` 在最前插入在线更新）；任一阻断步骤非 0 退出即短路并打印 `FAILED at step: <步骤名> (exit <code>)`，`--allow-ratchet-regression` 让 ratchet 恶化仅告警继续；步骤串联逻辑在 `src/lol_event_semantics/full_refresh.py`。
- `validate_normalized_outputs.py`：校验 `dist/normalized/` 最终产物；硬错误覆盖 manifest/summary/JSONL 一致性、实体规范化、记录 schema、translation 结构和 renderer smoke，质量警告覆盖可疑实体归属、重复 raw、fallback label、低信任规则残留等；`--fail-on-warning` 可在 warning 存在时返回 2。
- `lookup-normalized-event`：项目 console script，按 `raw_event_name` 精确匹配；优先读取 `normalized-events.jsonl`，缺失时回落 `by-entity/**/*.jsonl`，并输出 record/pattern/support/provenance、translation 和 renderer 结果。`scripts/lookup_normalized_event.py` 仅保留为兼容包装。
- `migrate_contextual_aliases.py`：把 `rules/contextual_aliases.yaml` 迁移为 `rules/contextual/**/*.yaml` 分片。
- `translate_events.py`：遗留/兼容翻译 CLI，依赖外部 `rcp-be-lol-game-data`。
- `build_translation_preview.py`：遗留/兼容翻译预览生成器。
- `build_entity_db.py`：遗留/兼容本地实体库生成器。
