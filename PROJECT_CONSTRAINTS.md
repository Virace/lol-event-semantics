# lol-event-semantics 项目约束

## 技术栈与版本边界

- 项目运行于 Python 3.12 及以上；本地解释器版本由 `.python-version` 管理。
- GitHub Actions 的全新锁定安装固定使用 Python 3.13，避免当前传递依赖在 Python 3.14
  无缓存环境中的构建兼容问题；这不改变项目的 Python 3.12 最低运行边界。
- 依赖、虚拟环境和命令统一使用 uv；提交依赖变更时同步提交 `pyproject.toml` 与 `uv.lock`。
- 共享模块采用 `src/lol_event_semantics/` 布局，console script 由 `pyproject.toml` 注册；Hatchling 只服务项目安装，不建立 Python 包发布或构建验收要求。
- `zstd` 是 league-tools/riotmanifest 的传递依赖，固定为 `1.5.7.2`，避免解析到已被上游撤回的 `1.5.7.3`；league-tools 的最低版本相应为兼容该约束的 `1.2.0`。
- 默认测试必须可在 GitHub Actions 的 Ubuntu runner 和维护者本地环境离线运行。

## 数据与兼容约束

- `raw_event_name` 是不可变源数据；新版本只增量合并，不因当前版本缺失而删除历史事件。
- 每轮采集只读取当时的 live 最新版本；项目不提供指定历史版本的下载、重建或回放。累计语料保存已纳入事件名，但不构成历史版本快照服务。
- `16.16` 是本轮 frozen acceptance profile，仅用于当前验收基线；不得把它编码为长期“最新版本”默认值。
- GAME 与 LCU 来源必须在同轮内进行主版本一致性校验；不能把不同版本的来源静默混合为同一结果。
- 远端 raw artifact 必须和可移植来源清单绑定同一 workflow run；manifest 采集必须生成本轮来源清单并提供非空 LCU version，不能借用本地冻结源的 null 例外。online 更新入口在任何网络准备前必须验证该清单、frozen profile 及 GAME/Data Dragon 请求版本属于同一系列；更新器在替换累计 raw/reference/state/report 前还要对 staging 输入做 fail-closed 预检。
- 当前 16.16 冻结源未暴露 `lcu_version`，只允许版本化 frozen profile 中记录的显式例外：
  `lobby_snapshot.game_version=16.16` 且 `lcu_manifest_id` 非空；该例外不得扩展到未来 live 采集。
- `rules/`、`overrides/` 和累计 `reference/` 是解释层的权威输入。生成文件不得反向成为人工维护源。
- `dist/runs/approved-local.json` 原子指向的 completed `normalized/` 是唯一默认消费源；
  `.staging`、`failed`、未批准 `completed` 与旧 `dist/normalized/` 都只能显式用于维护诊断。
  translation schema、frame type、字段和路径变化属于消费者 contract 变更。
- `raw_event_name` 必须保留原值但不是公开唯一键；`record_id` 由版本化 canonical tuple 确定，至少包含 raw、来源域/资产和最终 owner，不得包含本机绝对路径、输入顺序、随机数或生成时间。真实 identity 冲突必须 fail-closed 或用可解释来源身份消歧。
- `event_id` 是 additive 连续身份，由 raw 和逻辑 owner 生成；补充来源不改变它，实体纠正仍须显式迁移。同名多 owner 不合并，歧义不能绕过稳定支持降级门禁。
- `entity.attribution` 区分来源容器、精确 reference、装备数字 ID、显式地图、公共系统及未确认依据；cue 或动作 token 不能独立证明实体。模式实体与基础英雄的关联必须显式保留。
- web/software/lookup 是从审计 normalized 派生的独立消费包，manifest 必须绑定源身份、范围和逐文件摘要。网页可省略已有主体上下文显示名称，但不得删除触发条件、原名、支持状态或审计定位；不能把已生成文案或预览文案冒充稳定支持。
- `zh_CN` 是正式渲染 locale；其他 locale、回退与来源信息必须以结构化 translation 数据表达，不得以兼容 `display` 预览替代正式渲染 contract。
- VO 是优先支持对象；SFX 必须保留并按稳定支持、试验支持、暂不支持表达支持状态，不能因未稳定而静默丢弃。
- Pattern Catalog 只能从 semantic frame 派生，不能以 `BaseName,Label` 等字符串对作为主模型。只有同时命中已审 selector、精确 canonical pattern ID 且通过稳定支持绝对门禁的记录可标记稳定支持；任何新 ID 默认回到试验支持／待审阅。稳定支持质量门禁不读取趋势 baseline，并独立冻结已批准 decision、pattern ID 集合与记录总数，不能靠协调缩减 policy/corpus 洗绿。任何 support level 新增、移除、晋级或降级都必须进入结构化 diff；稳定支持降级额外阻断自动门禁。已接受样本集与最终 approved-local promotion 仍分别受精确断言和人工审阅约束。
- YAML/JSON/JSONL 等文本资产必须保持可审阅、确定性和 UTF-8 编码。
- normalized 内容身份不得包含时间、耗时、绝对路径、输入遍历顺序或随机值；dirty 工作树可生成
  本地候选，但不得进入已批准或已发布状态。
- completed run 不得原地覆盖或修改；promotion 必须校验 payload/report 摘要、clean generator、
  正式门禁 attestation；等待人工审核的候选还必须验证与 dataset/content digest 绑定的最终批准决定，
  并在锁内用 expected previous digest 做 CAS 后原子替换小型 pointer。源审阅决定与已接受样本集
  必须作为同一事务更新，任一拒绝、修改要求、stale target 或写入失败都不得留下部分状态。

## 安全与隐私

- 仓库不需要用户身份、支付信息或其他个人数据，不得新增此类收集。
- 不提交访问令牌、Cookie、私有下载 URL、`.env`、本地游戏安装绝对路径或网络响应中的敏感查询参数。
- 下载和导入代码必须校验版本、状态、长度、格式或 schema，不能把成功退出码等同于目标文件已正确生成。
- 外部文本、YAML、JSON、manifest 和文件路径视为不可信输入；不拼接执行 shell，不让来源路径逃逸指定输出根。
- 不提交或重新分发受版权保护的大体积游戏资产；只保留项目需要的原始事件名与精简参考。

## 运行与部署环境

- 项目是脚本、CLI 和离线数据流水线，不部署常驻应用服务或数据库；可部署由仓库生成、无后端且不写回数据的只读静态 Demo。
- GitHub Actions 只保留手动 raw/reference 更新与静态 Demo 部署，不为每次 push/PR 运行重复 test job；提交或交付门禁仍须在本地执行。当前更新 workflow 只接受 frozen V1 16.16，未来 live 版本须先增加版本化 profile/来源合同。成功结果以 `candidate-dataset` artifact 交付，失败结果只能以 `failed-run-diagnostics` 交付；二者都保留本轮来源清单、隔离 run 身份与 Review 报告，不得自动视为已批准或远端稳定发布。`dist/` 不入 Git。
- 真实 GAME manifest/CDN、本地游戏安装和在线数据源属于可选集成环境，不得成为默认离线测试前提。

## 禁止事项

- 不手工编辑由脚本管理的 normalized、报告或中间产物来掩盖源规则问题。
- 不把共享逻辑复制到多个 `scripts/` 入口。
- 不在未核验官方来源时新增中文展示词。
- 不把 Pattern Catalog 报告、候选或单次采集结果发布为远端稳定产物；远端发布属于后续阶段。
- 不用全仓库机械格式化、目录重排或抽象层清理夹带功能变更。
- 不把临时缓存、一次性实验、完整外部数据快照或本地 review 输出提交到仓库。

## 项目级例外

累计 `raw/`、`reference/`、`rules/` 和 `overrides/` 是项目知识资产，允许以文本形式入库；这不构成提交完整游戏数据或任意生成缓存的许可。Windows PowerShell 是主要维护命令示例环境，但共享模块和手动 Actions workflow 不应依赖 Windows 专属路径语义。
