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

构建拆解 JSONL 和报告：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --version 16.5 `
  --output-root dist\work\16.5\event-decomposition `
  --report-root dist\reports\runs\16.5 `
  --game-data-root dist\reference\16.5\game-data
```

构建本地 review site：

```powershell
uv run python scripts/build_review_site.py `
  --input dist\work\16.5\event-decomposition\champions.jsonl `
  --input dist\work\16.5\event-decomposition\maps.jsonl `
  --output dist\work\16.5\review-site\index.html
```

构建 normalized 下游数据：

```powershell
uv run python scripts/build_normalized_outputs.py `
  --decomposition-root dist\work\16.5\event-decomposition `
  --report-root dist\reports\runs\16.5 `
  --reference-version 16.5 `
  --output-root dist\normalized
```

## 当前入口脚本

- `export_raw_events.py`：从外部 `manifest/<version>/events` 导入 raw 的兼容入口；目标 raw 布局是累积式 `raw/events/`，后续会在 raw-update 工作中调整。
- `extract_game_data_json.py`：从 WAD 提取已知 `rcp-be-lol-game-data` JSON 路径，并支持可选 CommunityDragon fallback。
- `decompose_events.py`：把 raw event name 拆成 lexical atoms、semantic slots、JSONL 产物和 review reports；可通过 `--dragontail-root` 补充官方英雄技能 id。
- `build_review_site.py`：从 decomposition JSONL 构建本地静态 review 页面；输出目录需要用本地 HTTP 服务打开，因为页面数据在 `review-data.json`。
- `build_normalized_outputs.py`：从 decomposition JSONL 生成 `dist/normalized/` 下游数据；会重写 `manifest.json`、`entity-summary.json` 和 `by-entity/` 下由它管理的生成文件。
- `migrate_contextual_aliases.py`：把 `rules/contextual_aliases.yaml` 迁移为 `rules/contextual/**/*.yaml` 分片。
- `translate_events.py`：遗留/兼容翻译 CLI，依赖外部 `rcp-be-lol-game-data`。
- `build_translation_preview.py`：遗留/兼容翻译预览生成器。
- `build_entity_db.py`：遗留/兼容本地实体库生成器。
