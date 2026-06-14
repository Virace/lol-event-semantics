# overrides

这里存放人工 review 后的精确覆盖和策展决策。

## 当前文件

- `legacy_aliases.yaml`：已 review 的历史 owner 或皮肤别名，适用于不适合放入通用规则路径的兼容情况。

## 维护原则

- `raw_event_name` 不在这里改写；这里只记录派生层需要的人工决策。
- 能抽象成可复用规则的内容优先放入 `rules/`。
- 只有明确需要逐项覆盖、兼容旧命名或保留 review 结论的内容才放入 `overrides/`。
