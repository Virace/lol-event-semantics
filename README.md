# lol-event-semantics

《英雄联盟》音频事件名语义数据、规则与工具仓库。

本仓库只处理仍属于《英雄联盟》客户端的事件名。云顶之弈 / TFTM 相关事件名不进入 raw 快照、拆解产物、审计报告或对外数据。

## 项目目标

本仓库长期维护三类资产：

- `raw/events/`：累积的原始事件名源语料。`raw_event_name` 是不可变源数据，不能替换成可读标签。
- `rules/` 与 `overrides/`：人工维护的规则、词典、上下文别名、residual modifier 和精确 review 决策。
- `dist/normalized/`：生成的下游主数据源，按实体拆分，是消费者应读取的数据。

本项目不是音频提取器，也不发布完整 GAME WAD、BIN、BNK、WEM 或 WAV 包。

## 当前流程

目标流程：

```text
raw/events/
  -> rules/ + overrides/ + reference/
  -> decomposition JSONL + rule audit reports
  -> dist/normalized/
```

关键边界：

- `dist/normalized/` 是生成产物。最终数据有误时，应修改 raw 输入、参考数据、规则、覆盖或生成代码后重新生成。
- `dist/work/`、`dist/reports/runs/`、review site 和提取出的 `game-data` 都是内部维护产物，不是稳定消费者契约。
- 自动 raw 更新入口和手动 workflow 已纳入仓库；GAME manifest/WAD/BIN 抽取仍是可选更新来源，不是对外数据契约。
- 本地一键刷新入口 `scripts/run_full_refresh.py` 串起 decompose→build→validate→ratchet→metrics 全链，任一步失败即停并报出步骤名，是 CI 更新 workflow 的本地等价物。
- 规则运行时仍然必须全量加载，除非未来有等价性测试证明按 owner 懒加载不会丢失跨 owner 命中。

## 目录

- `src/lol_event_semantics/`：共享 Python 包代码。
- `scripts/`：薄入口脚本。
- `raw/`：原始事件名源数据；目标形态是累积式 `raw/events/`。
- `rules/`：可复用归一化规则、词典和语义模板。
- `overrides/`：人工维护的精确覆盖和 review 决策。
- `data/`：可选的小型 schema、fixture 或样例数据；当前不是主流程必需依赖。
- `reference/`：仓库维护的累计精简参考合集，包含 `champions/`、`maps/`、`skinlines/`，解释阶段只读取这里。
- `.temp/reference-sources/`：被忽略的在线参考输入缓存，仅用于导入 `reference/`。
- `dist/work/<run-id>/`：被忽略的单次执行中间产物。
- `dist/reports/runs/<run-id>/`：审计和维护报告。
- `dist/normalized/`：按实体拆分的对外生成数据。

## 开发

```powershell
uv sync
uv run python -c "import lol_event_semantics; print(lol_event_semantics.__version__)"
```

从本地 WAD 准备参考 game-data JSON：

```powershell
uv run python scripts/extract_game_data_json.py `
  --output-root dist\reference\16.5\game-data `
  --wad <default-assets.wad> `
  --wad <default-assets2.wad> `
  --locale default
```

把本地 GAME data 和 Data Dragon 导入内部 reference 合集：

```powershell
uv run python scripts/import_reference_data.py `
  --reference-root reference `
  --game-data-root dist\reference\16.5\game-data `
  --data-dragon-root <data-dragon-root> `
  --game-version 16.5 `
  --data-dragon-version 16.5.1
```

在线按需准备 GAME data snapshot 与 Data Dragon 后导入内部 reference 合集：

```powershell
uv run python scripts/import_reference_data.py `
  --online `
  --reference-root reference `
  --game-version 16.12 `
  --game-data-base-url <snapshot-root-containing-global>
```

把本地或前置步骤抽取出的本轮事件名合并进累计 `raw/events/`：

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

使用内部 reference 合集拆解事件名：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --reference-root reference `
  --output-root dist\work\event-decomposition `
  --report-root dist\reports\runs\latest
```

需要复核低信任整句规则时，可追加 `--write-low-trust-rules` 输出 `dist\reports\runs\latest\low-trust-rules.*`。

构建本地 review site：

```powershell
uv run python scripts/build_review_site.py `
  --input dist\work\event-decomposition\champions.jsonl `
  --input dist\work\event-decomposition\maps.jsonl `
  --output dist\work\review-site\index.html

python -m http.server 8765 --bind 127.0.0.1 --directory dist\work\review-site
```

构建 normalized 下游数据：

```powershell
uv run python scripts/build_normalized_outputs.py `
  --decomposition-root dist\work\event-decomposition `
  --output-root dist\normalized `
  --reference-version cumulative
```

GitHub Actions workflow:

- `.github/workflows/update-raw-events.yml` 是手动触发的 raw/reference 更新 workflow。
- workflow 读取 `lol-game-data-snapshot` 与 Data Dragon 的在线最小子集。
- `raw_source_mode=artifact` 时消费同一 workflow run 中前置步骤上传的本轮 raw round txt。
- `raw_source_mode=manifest` 时可用 GAME manifest URL 和 JSON 请求表生成本轮 raw round txt；请求表形状见 `.github/raw-event-requests.example.json`，该模式需要运行环境提供可用的 `riotmanifest` 包。
- 更新后会运行小批量 decomposition smoke；`commit_changes=false` 时只跑验证和报告，只有显式设为 `true` 才提交 `raw/events/`、`raw/update-state.json` 和 `reference/`。

## 兼容脚本

- `scripts/export_raw_events.py` 仍然从外部 `manifest/<version>/events` 导入到旧的版本化 raw 布局。目标 raw 布局是累积式 `raw/events/`，该脚本会在 raw-update 工作中调整。
- `scripts/translate_events.py`、`scripts/build_translation_preview.py`、`scripts/build_entity_db.py` 是遗留/兼容候选。删除或迁移前需要先检查引用和确认工作流。

## 事件拆解说明

- `raw_event_name` 在拆解记录中保持原样。
- 云顶之弈 / TFTM 事件名在 raw 导出和拆解边界过滤。
- 正式 unknown 报告应带内部 `reference/` 合集生成；无 reference 的运行只适合诊断。
- `scripts/extract_game_data_json.py` 可以从本地 WAD 准备 reference JSON，需要安装并配置 `league-tools`。
- `scripts/import_reference_data.py --data-dragon-root` 可以把 Data Dragon 中的官方英雄技能 id，以及 GAME data 中的地图和皮肤系列导入内部 `reference/`。
- `scripts/update_raw_events.py --mode online` 会把在线 source 先缓存成现有本地 layout，再导入内部 `reference/`；解释阶段仍只读取 `reference/`。
- 上下文别名主要维护在 `rules/contextual/**/*.yaml`；`rules/contextual_aliases.yaml` 暂时保留为迁移和兼容来源。
- 技能/实体 residual modifier 维护在 `rules/residual_modifiers.yaml`，只在可信 stem 命中后解析。
- 手工 fallback 实体维护在 `rules/external_entities.yaml`，它们是不可信来源，后续可以被官方结构化来源替换。
- 已审阅整句 fallback 维护在 `rules/low_trust_events.yaml`；它们会从默认低置信噪声中移出，但在 `semantic_slots.low_trust_rule` 保留 `trust: low` 供筛选。
- unknown 或 ambiguous 片段必须进入报告，不应被静默翻译。

## 展示数据

面向用户的短展示文案应回答“这个音频什么时候触发”，而不是直译每个技术 token。例如：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
普朗克大招升级一阶
```

`Play_vo`、皮肤编号、`cast3D`、`2D`、`3D`、`voProcessing` 等技术 token 应进入 `debug_label` 或审计字段，不进入普通短展示文案。

### 结构化翻译 contract

`dist/normalized/` 记录可能包含 `translation` 对象。它是面向本地化和上下文渲染的结构化 contract，包含 `frame_type`、版本化 `arguments`、多语言 label、来源 `provenance`、fallback 标记和 renderer 可审计诊断。

`display.zh_cn_short` 和 `display.zh_cn_detail` 是兼容预览字段。对 renderer 已覆盖的 frame，它们从 `translation` 派生；需要根据页面上下文裁剪文案的下游程序，应优先读取 `translation` 并按 `locale`、`mode`、`known_context` 渲染，而不是把 `display` 当成唯一 API contract。

例如 Galio W 被动防御状态事件可以在默认场景渲染为带英雄和皮肤上下文的完整文案；当调用方已经知道 champion 和 skin tab 时，可渲染为：

```text
W技能被动防御状态结束
```

未来服务层可以由下游应用读取 normalized JSONL 后调用 renderer 生成类似响应；本仓库当前不提供 HTTP API：

```json
{
  "raw_event_name": "Play_sfx_GalioSkin13WPassiveDefense_OnBuffDeactivate",
  "locale": "zh_CN",
  "mode": "main",
  "known_context": ["champion", "skin"],
  "text": "W技能被动防御状态结束",
  "frame_type": "ability_state_change",
  "confidence": "medium",
  "used_roles": ["ability_slot", "ability_component", "phase"],
  "omitted_roles": ["champion", "skin"]
}
```

normalized writer 会重写 `dist/normalized/` 下由它管理的生成文件：`manifest.json`、`entity-summary.json` 和 `by-entity/` 下的 JSONL 分片。

生成后应运行 normalized 二次校验，确认 manifest、summary、JSONL 分片和记录结构一致，并输出质量警告报告：

```bash
uv run python scripts/validate_normalized_outputs.py --normalized-root dist/normalized --output-json dist/reports/runs/latest/normalized-validation.json
```
