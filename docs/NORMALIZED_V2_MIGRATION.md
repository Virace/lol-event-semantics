# Normalized Dataset V2 Migration

V2 是 additive migration。现有 `by-entity/**/*.jsonl`、`entity-summary.json`、
`translation` 和 `display` 在兼容期继续生成；新消费者应优先读取
`normalized-events.jsonl`、`pattern-catalog.json` 和 `quality-summary.json`。

## 主键与查询

- 使用 `record_id` 作为记录唯一键，不把 `raw_event_name` 当作全局唯一键。
- 仍可按 `raw_event_name` 查询，但调用方必须允许多结果，并用 `source_scope`、
  `provenance` 或 `record_id` 消歧。
- `normalized-events.jsonl` 按 `record_id` 排序；`pattern-catalog.json` 按
  `pattern_id` 排序。

## 新增记录字段

V2 正式增加 `record_id`、`record_id_algorithm`、`source_scope`、`provenance`、
`asset_kind`、`pattern_id`、`support_level`、`semantic_frame`、`rendered_labels`、
`render_status`、`review_status` 和 `warnings`。旧字段不删除。

P4 仅将 exact approved 且通过绝对门禁的 86 个 pattern 标记 Stable；其余
pattern 仍为 Experimental。`review_status=accepted` 表示 P1 产品范围已批准，
但 Stable candidate 仍不等于 approved-local。

## Schema 与兼容策略

- manifest `schema_version=2`，record `schema_version=2`，Pattern Catalog
  `schema_version=1`。
- manifest V2 additive 接受旧 `quality_status=not_evaluated`；P4 writer 使用
  `passed|failed`、`quality_gate_version` 和必需的 `quality-summary.json`。
- Schema 与最小样例同时位于数据集 `schema/`、`examples/`，源文件在仓库
  `data/`。
- validator 同时读取 manifest/record V1 和 V2，未知 major fail-closed。
- lookup 优先 V2 单流；没有单流时回落 legacy by-entity。

P3-C1 已补全确定性 manifest identity 与 content digest：manifest 不再含运行时间，
并记录冻结输入 profile、Git dirty 状态、各输入树摘要及每个 payload 文件 SHA-256。
validator 会复算文件表、content digest 和 dataset id。P3-C2 已把每轮写入隔离到
`.staging/<run-id>`，成功/失败分别固化到 `completed/`、`failed/`，并提供带
lock/CAS/clean/report/payload/eligibility 门禁的 `approved-local.json` 原子替换。
默认 lookup 不再扫描 candidate。P4 attestation 在 P5 用户 Review 前保持 `eligible=false`，
因此当前真实 completed candidate 仍不能 promotion 或被当作已批准发布状态。
