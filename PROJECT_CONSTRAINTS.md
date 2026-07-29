# lol-event-semantics 项目约束

## 技术栈与版本边界

- 项目运行于 Python 3.12 及以上；本地解释器版本由 `.python-version` 管理。
- 依赖、虚拟环境和命令统一使用 uv；提交依赖变更时同步提交 `pyproject.toml` 与 `uv.lock`。
- 共享模块采用 `src/lol_event_semantics/` 布局，console script 由 `pyproject.toml` 注册；Hatchling 只服务项目安装，不建立 Python 包发布或构建验收要求。
- `zstd` 是 league-tools/riotmanifest 的传递依赖，固定为 `1.5.7.2`，避免解析到已被上游撤回的 `1.5.7.3`；league-tools 的最低版本相应为兼容该约束的 `1.2.0`。
- 默认测试必须可在 GitHub Actions 的 Ubuntu runner 和维护者本地环境离线运行。

## 数据与兼容约束

- `raw_event_name` 是不可变源数据；新版本只增量合并，不因当前版本缺失而删除历史事件。
- 项目只跟踪 live 最新版本，不承诺历史版本回放。
- GAME 与 LCU 来源必须在同轮内进行主版本一致性校验；不能把不同版本的来源静默混合为同一结果。
- `rules/`、`overrides/` 和累计 `reference/` 是解释层的权威输入。生成文件不得反向成为人工维护源。
- `dist/normalized/` 是唯一对外主数据源；translation schema、frame type、字段和路径变化属于消费者 contract 变更。
- YAML/JSON/JSONL 等文本资产必须保持可审阅、确定性和 UTF-8 编码。

## 安全与隐私

- 仓库不需要用户身份、支付信息或其他个人数据，不得新增此类收集。
- 不提交访问令牌、Cookie、私有下载 URL、`.env`、本地游戏安装绝对路径或网络响应中的敏感查询参数。
- 下载和导入代码必须校验版本、状态、长度、格式或 schema，不能把成功退出码等同于目标文件已正确生成。
- 外部文本、YAML、JSON、manifest 和文件路径视为不可信输入；不拼接执行 shell，不让来源路径逃逸指定输出根。
- 不提交或重新分发受版权保护的大体积游戏资产；只保留项目需要的原始事件名与精简参考。

## 运行与部署环境

- 项目是脚本、CLI 和离线数据流水线，不部署常驻服务或数据库。
- GitHub Actions 提供默认 CI 和手动 raw/reference 更新；生成数据通过 workflow artifact 交付，`dist/` 不入 Git。
- 真实 GAME manifest/CDN、本地游戏安装和在线数据源属于可选集成环境，不得成为默认离线测试前提。

## 禁止事项

- 不手工编辑由脚本管理的 normalized、报告或中间产物来掩盖源规则问题。
- 不把共享逻辑复制到多个 `scripts/` 入口。
- 不在未核验官方来源时新增中文展示词。
- 不用全仓库机械格式化、目录重排或抽象层清理夹带功能变更。
- 不把临时缓存、一次性实验、完整外部数据快照或本地 review 输出提交到仓库。

## 项目级例外

累计 `raw/`、`reference/`、`rules/` 和 `overrides/` 是项目知识资产，允许以文本形式入库；这不构成提交完整游戏数据或任意生成缓存的许可。Windows PowerShell 是主要维护命令示例环境，但共享模块和默认 CI 不应依赖 Windows 专属路径语义。
