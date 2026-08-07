# data

这里存放可提交的小型数据集、schema、fixture 或精选样例。

不要提交大型 raw dump、临时导出、缓存或一次性分析产物。

当前主流程不依赖此目录：

- 原始事件名属于 `raw/events/`。
- 长期规则属于 `rules/` 和 `overrides/`。
- 对外生成数据属于 `dist/normalized/`。
- `16.14` 只是当前 V1 的 frozen acceptance profile，不是长期 live 默认值，也不在这里保存历史版本快照。
- Pattern Catalog inventory 属于 `dist/reports/` 下的本地忽略报告；经人工批准的 accepted corpus 和正式消费者 schema 才能作为后续小型、可提交数据进入本目录。
