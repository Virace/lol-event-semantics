---
name: pipeline-run
description: 编排本仓库的事件语义数据流水线（raw -> reference -> decomposition -> normalized -> 校验）。用户想跑某一段或全链路数据处理时使用。
disable-model-invocation: true
---

# pipeline-run

固化 `scripts/` 下各脚本的先后顺序与参数，避免每次回忆 CLI 标志。所有命令用 `uv run` 在项目根执行。权威参数说明见 `scripts/README.md`，本表为常用调用骨架。

## 阶段与命令

执行某一段前，先与用户确认输入版本号、数据快照 URL 等可变参数。

### 1. 准备 reference game-data（按需）

```bash
# 在线导入内部 reference 合集
uv run python scripts/import_reference_data.py \
  --online \
  --reference-root reference \
  --game-version <ver> \
  --game-data-base-url <snapshot-root-containing-global>
```

### 2. 更新累计 raw / reference

```bash
uv run python scripts/update_raw_events.py \
  --mode online \
  --reference-root reference \
  --raw-root raw/events \
  --state-file raw/update-state.json \
  --raw-input-root .temp/raw-events-round \
  --game-version <ver> \
  --game-data-base-url <snapshot-root-containing-global> \
  --report-root .temp/update-report
```

只读差集预检（不写入）：`uv run python scripts/check_raw_update.py ...`（见 README）。

### 3. 构建拆解 JSONL 与报告

```bash
uv run python scripts/decompose_events.py \
  --raw-root raw/events \
  --reference-root reference \
  --output-root dist/work/event-decomposition \
  --report-root dist/reports/runs/latest
```

需要审阅低信任整句规则命中时追加 `--write-low-trust-rules`。

### 4. 构建 normalized 下游数据

```bash
uv run python scripts/build_normalized_outputs.py \
  --decomposition-root dist/work/event-decomposition \
  --output-root dist/normalized \
  --reference-version cumulative
```

### 5. 校验 normalized 产物（交付前必跑）

```bash
uv run python scripts/validate_normalized_outputs.py \
  --normalized-root dist/normalized
```

退出码：`0` 通过；`1` 有硬错误；加 `--fail-on-warning` 时 `2` 表示存在质量警告。

### 抽查单个事件

```bash
uv run lookup-normalized-event \
  --event <raw_event_name> \
  --mode main \
  --known-context champion,skin
```

## 约定

- 不手改 `raw/`、`dist/`、`reference/` 生成物；要改输出先改 `rules/` / `overrides/` 源输入再重跑对应阶段。
- `.temp/` 下为本轮临时产物，不提交。
- 每跑完一段，贴出脚本打印的统计/报告路径与退出码；校验阶段务必报告 errors/warnings 计数。
- 多版本数据更新流程的完整说明与 CI 形态见 `scripts/README.md` 与 `.github/workflows/update-raw-events.yml`。
