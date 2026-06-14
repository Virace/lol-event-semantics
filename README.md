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
  -> rules/ + overrides/ + versioned reference game-data
  -> decomposition JSONL + rule audit reports
  -> dist/normalized/
```

关键边界：

- `dist/normalized/` 是生成产物。最终数据有误时，应修改 raw 输入、参考数据、规则、覆盖或生成代码后重新生成。
- `dist/work/`、`dist/reports/runs/`、review site 和提取出的 `game-data` 都是内部维护产物，不是稳定消费者契约。
- 自动 raw 更新有单独规划。GAME WAD/BIN 提取不属于当前规则布局重整范围。
- 规则运行时仍然必须全量加载，除非未来有等价性测试证明按 owner 懒加载不会丢失跨 owner 命中。

## 目录

- `src/lol_event_semantics/`：共享 Python 包代码。
- `scripts/`：薄入口脚本。
- `raw/`：原始事件名源数据；目标形态是累积式 `raw/events/`。
- `rules/`：可复用归一化规则、词典和语义模板。
- `overrides/`：人工维护的精确覆盖和 review 决策。
- `data/`：可选的小型 schema、fixture 或样例数据；当前不是主流程必需依赖。
- `dist/reference/<version>/game-data/`：被忽略的版本敏感参考输入。
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

使用参考数据拆解事件名：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --version 16.5 `
  --output-root dist\work\16.5\event-decomposition `
  --report-root dist\reports\runs\16.5 `
  --game-data-root dist\reference\16.5\game-data
```

需要官方技能 id 时，可额外接入 DragonTail：

```powershell
uv run python scripts/decompose_events.py `
  --raw-root raw\events `
  --version 16.5 `
  --output-root dist\work\16.5\event-decomposition `
  --report-root dist\reports\runs\16.5 `
  --game-data-root dist\reference\16.5\game-data `
  --dragontail-root <dragontail-data-root>
```

构建本地 review site：

```powershell
uv run python scripts/build_review_site.py `
  --input dist\work\16.5\event-decomposition\champions.jsonl `
  --input dist\work\16.5\event-decomposition\maps.jsonl `
  --output dist\work\16.5\review-site\index.html

python -m http.server 8765 --bind 127.0.0.1 --directory dist\work\16.5\review-site
```

构建 normalized 下游数据：

```powershell
uv run python scripts/build_normalized_outputs.py `
  --decomposition-root dist\work\16.5\event-decomposition `
  --report-root dist\reports\runs\16.5 `
  --reference-version 16.5 `
  --output-root dist\normalized
```

## 兼容脚本

- `scripts/export_raw_events.py` 仍然从外部 `manifest/<version>/events` 导入到旧的版本化 raw 布局。目标 raw 布局是累积式 `raw/events/`，该脚本会在 raw-update 工作中调整。
- `scripts/translate_events.py`、`scripts/build_translation_preview.py`、`scripts/build_entity_db.py` 是遗留/兼容候选。删除或迁移前需要先检查引用和确认工作流。

## 事件拆解说明

- `raw_event_name` 在拆解记录中保持原样。
- 云顶之弈 / TFTM 事件名在 raw 导出和拆解边界过滤。
- 正式 unknown 报告应带匹配版本的 reference game-data 生成；无 reference 的运行只适合诊断。
- `scripts/extract_game_data_json.py` 可以从本地 WAD 准备 reference JSON，需要安装并配置 `league-tools`。
- `decompose_events.py --dragontail-root` 可以补充 DragonTail 中的官方英雄技能 id，例如 `NullLance`、`CardmasterStack`。
- 上下文别名主要维护在 `rules/contextual/**/*.yaml`；`rules/contextual_aliases.yaml` 暂时保留为迁移和兼容来源。
- 技能/实体 residual modifier 维护在 `rules/residual_modifiers.yaml`，只在可信 stem 命中后解析。
- 手工 fallback 实体维护在 `rules/external_entities.yaml`，它们是不可信来源，后续可以被官方结构化来源替换。
- unknown 或 ambiguous 片段必须进入报告，不应被静默翻译。

## 展示数据

面向用户的短展示文案应回答“这个音频什么时候触发”，而不是直译每个技术 token。例如：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
普朗克大招升级一阶
```

`Play_vo`、皮肤编号、`cast3D`、`2D`、`3D`、`voProcessing` 等技术 token 应进入 `debug_label` 或审计字段，不进入普通短展示文案。

normalized writer 会重写 `dist/normalized/` 下由它管理的生成文件：`manifest.json`、`entity-summary.json` 和 `by-entity/` 下的 JSONL 分片。
