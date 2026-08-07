# data

这里存放可提交的小型数据集、schema、fixture 或精选样例。

不要提交大型 raw dump、临时导出、缓存或一次性分析产物。

当前 V2 消费者 contract 依赖此目录中的小型权威资产：

- `schema/manifest.schema.json`：normalized manifest V2 Schema。
- `schema/normalized-event.schema.json`：normalized record V2 Schema。
- `schema/pattern-catalog.schema.json`：Pattern Catalog V1 Schema。
- `examples/`：与上述 Schema 对应、身份引用一致的最小消费者样例。
- `frozen-profile.v1.json`：当前 V1 16.14 验收输入的版本、raw 唯一计数与显式 LCU 例外；
  它是预检合同，不是历史版本下载能力或未来 live 默认值。

其余边界：

- 原始事件名属于 `raw/events/`。
- 长期规则属于 `rules/` 和 `overrides/`。
- 对外生成数据属于 `dist/normalized/`。
- `16.14` 只是当前 V1 的 frozen acceptance profile，不是长期 live 默认值，也不在这里保存历史版本快照。
- Pattern Catalog inventory 属于 `dist/reports/` 下的本地忽略维护报告；公共 `pattern-catalog.json` 由同一 policy/inventory 确定性生成，不在这里手工维护完整快照。
