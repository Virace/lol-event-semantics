# data

这里存放可提交的小型数据集、schema、fixture 或精选样例。

不要提交大型 raw dump、临时导出、缓存或一次性分析产物。

当前 V2 消费者 contract 依赖此目录中的小型权威资产：

- `schema/manifest.schema.json`：normalized manifest V2 Schema。
- `schema/normalized-event.schema.json`：normalized record V2 Schema。
  新增可选 event_id/event_id_algorithm 和 entity attribution/base_champion/candidate；旧 V2 仍可读取。
- `schema/pattern-catalog.schema.json`：Pattern Catalog V1 Schema。
- `schema/quality-summary.schema.json`：分层质量摘要与冻结 decision/pattern-set/record-count 的 Stable 绝对门禁 Schema。
- `schema/review-decision.schema.json`：候选绑定、可哈希且带审核人/时间/理由的版本化人工决定 Schema。
- `schema/review-bundle.schema.json`：Review Site 输入、source review 和最终 dataset approval 目标的 Schema。
- `accepted-corpus.v1.json`：语义复核迁移后的 91 个 Stable pattern 精确 zh_CN default/main 代表样本；首轮 P5 source review 已接受，后续候选仍需 dataset approval。
- `frozen-sources-manifest.v1.json`：当前 V1 可提交、无机器路径和运行时间的本地复现来源身份；远端更新必须携带本轮来源清单，不得复用它冒充在线采集。
- `examples/`：与上述 Schema 对应的最小样例、accepted corpus 副本，以及只依赖 Python 标准库的 `consumer.py`；consumer 会先证明必需消费路径与实际 payload 全部处于 manifest hash/digest 闭包，再查询单流。
- `frozen-profile.v1.json`：当前 V1 16.16 验收输入的版本、raw 唯一计数与显式 LCU 例外；
  它是预检合同，不是历史版本下载能力或未来 live 默认值。

其余边界：

- 日常消费 profiles 与无源码客户端位于 `src/lol_event_semantics/consumption.py` 和 `consumer_assets/`，各包附带自身的消费 schema；它们不要求消费者携带本目录全部审计资产。详见根 `CONSUMPTION.md`。

- 原始事件名属于 `raw/events/`。
- 长期规则属于 `rules/` 和 `overrides/`。
- 对外候选数据属于 `dist/runs/<state>/<run-id>/normalized/`；默认消费者只解引用 approved-local pointer。
- `16.16` 只是当前 V1 的 frozen acceptance profile，不是长期 live 默认值，也不在这里保存历史版本快照。
- Pattern Catalog inventory 属于 `dist/reports/` 下的本地忽略维护报告；公共 `pattern-catalog.json` 由同一 policy/inventory 确定性生成，不在这里手工维护完整快照。
