# Normalized Dataset V2 Migration

V2 是 additive migration。现有 `by-entity/**/*.jsonl`、`entity-summary.json`、
`translation` 和 `display` 在兼容期继续生成；新消费者应优先读取
`normalized-events.jsonl` 和 `pattern-catalog.json`。

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

当前 `support_level` 仍全部为 `experimental`；`review_status=accepted` 只表示
P1 产品范围已经批准，不代表已经通过 P4 Stable 绝对门禁。

## Schema 与兼容策略

- manifest `schema_version=2`，record `schema_version=2`，Pattern Catalog
  `schema_version=1`。
- Schema 与最小样例同时位于数据集 `schema/`、`examples/`，源文件在仓库
  `data/`。
- validator 同时读取 manifest/record V1 和 V2，未知 major fail-closed。
- lookup 优先 V2 单流；没有单流时回落 legacy by-entity。

P3 会继续补全确定性 manifest identity、content digest 和隔离 promotion；因此
本阶段的 manifest 仍含运行时间，不应被当作最终 approved-local 身份。
