# lol-event-semantics 项目开发规范

## 1. 适用范围与权威来源

本规范适用于仓库根目录及全部项目代码、脚本、测试、规则、参考数据和文档。每轮项目变更必须先读取 `AGENTS.md` 与本文件；局部目录存在更具体规则时，局部规则优先。

规则优先级依次为：用户当前明确要求、适用的 `AGENTS.md`、本规范及其路由的产品/约束/设计文件、仓库中的真实代码与可执行命令。`PRODUCT.md` 定义产品目标，`PROJECT_CONSTRAINTS.md` 定义硬边界，`PROJECT_DESIGN.md` 定义当前模块与数据流。README、`REPOSITORY.md`、`CONTRIBUTING.md` 和各目录 README 负责面向使用者或维护者的操作说明，必须与真实布局保持一致。

## 2. 技术栈与真实命令

- 运行时：Python 3.12 及以上；`.python-version` 固定当前本地开发解释器为 Python 3.14。
- 云端全新环境及已停用的 Actions 模板固定使用 Python 3.13；当前 `league-tools` 传递依赖在 3.14
  的无缓存构建环境中不可安装，本地 3.14 环境与代码的 3.12 最低语法边界保持不变。
- 项目与环境管理：uv，锁文件为 `uv.lock`。
- 共享模块采用 `src/` 布局；Hatchling 仅用于让 uv 安装项目代码和 console script，不把 wheel/sdist 作为交付物。
- 主要依赖：league-tools 1.2.0 及以上、msgpack、ruamel.yaml；下载 GAME manifest 时通过 `download` 可选组启用 riotmanifest。
- 开发工具：pytest、Ruff、mypy。

可执行命令：

```powershell
# 按锁文件安装默认开发环境
uv sync --dev --locked

# 需要下载 GAME manifest 时安装可选依赖
uv sync --dev --extra download --locked

# 当前 live raw 采集，只写独立轮次，不合并 raw/reference 或批准数据
uv run python scripts/collect_raw_events.py --output-root dist/collection --champions all --maps all

# 对本次实际修改的 Python 文件执行格式化或只读格式检查；FILE... 为实际路径
uv run ruff format -- FILE...
uv run ruff format --check -- FILE...

# 全仓库致命语法与名称错误检查
uv run ruff check src scripts tests

# 对 pyproject.toml 中已纳入的稳定核心模块执行 strict 类型检查
uv run mypy

# 默认测试门禁
uv run python -m pytest tests/ -q

# 共享模块导入冒烟
uv run python -c "import lol_event_semantics; print(lol_event_semantics.__version__)"

# 发布数据前的本地完整生成、校验、指标与质量棘轮链
uv run python scripts/run_full_refresh.py
```

`ruff format` 必须在命令尾传入本次修改的 Python 文件路径；未经单独授权，不运行无路径的全仓库格式化。提交或交付前在本地执行锁定依赖同步、Ruff、mypy 和 pytest。GitHub Actions 当前全部停用，工作流仅以 `.yml.disabled` 模板保留；先完成本地完整流程验证并正式发布，再另行决定是否恢复。完整刷新会写入被忽略的 `dist/`，成本高于默认测试，只在规则、参考数据、normalized contract 或发布产物受影响时运行。

## 3. 代码组织

- `src/lol_event_semantics/`：可复用共享模块。数据源适配、更新编排、语义拆解、规则加载、normalized 生成、校验和查询逻辑都应在这里形成可测试边界。
- `scripts/`：下载、导入、拆解、构建、验证和维护入口。脚本负责解析参数和组合包能力，不复制共享业务逻辑。
- `tests/`：pytest 收集的单元与文件系统集成测试；固定代表样例放在 `tests/fixtures/`。
- `raw/events/`：按实体累积且不可删除旧项的原始事件名。
- `rules/`、`overrides/`：人工维护的语义规则、词典和精确审阅决策。
- `reference/`：累计精简参考合集，解释阶段只读取这里。
- `data/`：小型 schema、样例与质量基线。
- `dist/`：生成的下游数据、报告和中间产物，整体不入 Git。

完整 normalized 是审计合同；`consumption.py` 在其下生成 web/software/lookup 消费合同，不能为减小网页体积删除审计证据。消费中文由正式 renderer 派生，主体上下文省略必须有结构化证据，字典编码与分片不得丢失原名、多候选、状态和源定位。消费合同与命令见 `CONSUMPTION.md`；修改此层须运行 `tests/test_consumption.py`、`tests/test_consumer_clients.py` 并对真实候选执行 `validate_consumption.py --normalized-root ...`。

依赖方向为 `scripts -> src/lol_event_semantics`；共享模块不得反向导入脚本。源数据与规则经拆解层生成结构化记录，再由 normalized 层输出消费者 contract；校验和质量指标只读取生成结果，不成为源数据。只有稳定能力已由多个相关模块组成、需要公开边界或依赖隔离时才建立子模块目录，不为单个小文件或形式化分层创建目录。

## 4. 命名与文档

- Python 标识符遵循 PEP 8：模块、函数与变量使用 `snake_case`，类使用 `CapWords`，常量使用 `UPPER_CASE`。
- 保留项目既有领域词：raw、reference、decomposition、normalized、frame、locale、WAD、RST；对外字段名和已发布 contract 不因纯风格偏好改名。
- 新增或修改的函数必须有以中文为主的 Google-style docstring，并符合 PEP 257 的结构；短小函数可以只有一句摘要，有参数、返回值、异常或重要副作用时写相应章节。
- 注释解释不可从代码直接看出的意图、不变量、来源优先级、兼容约束或 workaround；不逐行复述实现、不记录修改过程、不保留失效注释。
- `raw_event_name`、translation schema 字段和 normalized 路径属于消费者可见契约，命名变化必须先明确兼容方案并同步文档与测试。
- 面向使用者的状态标签和日常说明使用 `docs/TERMINOLOGY.md` 中的中文术语；支持级别、中文生成、模式审阅和整个数据集批准分别表达。程序枚举、事件原名与审计标识保持兼容，不直接作为界面状态标签。

## 5. 错误与日志

- 共享模块内对无效调用参数使用 `TypeError`，对内容、状态或外部数据不满足约束使用 `ValueError`；文件和网络边界保留可识别的原始异常链，不用空捕获掩盖失败。
- CLI 成功返回 0；可预期失败返回非 0，并输出包含失败步骤或可执行修复方向的短消息。`run_full_refresh.py` 遇到阻断步骤失败必须短路，质量棘轮只有在显式允许时才可降级为告警。
- 机器可读问题写入 JSON/JSONL 报告或稳定字段；终端消息服务人工定位，不把整段堆栈、内部对象或下载内容当作对外 contract。
- 报告和日志不得记录凭证、令牌、私有 URL 查询参数、用户本地游戏绝对路径或大体积响应体。关联一次刷新使用运行目录和报告清单中的稳定 run 信息，不另造隐式全局状态。

## 6. 变更门禁

修改过程中实时纠正本轮新增或直接涉及的命名、模块路径、脚本/共享逻辑边界和失效文档。除纯文档变化外，至少执行 Ruff、mypy 和受影响测试；依赖或环境配置变化时验证锁文件同步与实际安装；规则、reference、normalized contract 或产物生成变化时追加完整刷新与代表事件人工抽查。项目不发布 Python 包，因此不把 wheel/sdist 构建作为开发或交付门禁。最终交付必须报告已运行命令、未运行门禁、遗留风险和工作树状态。

### 测试策略

测试优先防止三类高成本回归：原始事件名或来源数据被错误改写，语义拆解/翻译 contract 漂移，以及多文件更新或生成流程产生部分成功状态。

| 风险或稳定契约 | 可观察行为 | 验证方式与层级 | 环境或依赖 | 位置 | 执行时机 |
| --- | --- | --- | --- | --- | --- |
| 规则、词典和拆解逻辑回归 | 相同输入得到稳定 semantic slots、frame roles 与 provenance | 确定性行为测试 | 仓库内小型规则与 fixture | `tests/test_decomposition_*.py`、`tests/test_normalized_translation_*.py` | 修改解析、规则或渲染时 |
| raw/reference 更新破坏累计数据或留下半成品 | 旧事件保留，差集正确，失败时原文件与游标不被部分替换 | 临时目录文件系统集成测试 | 本地文件系统与 mocked 下载边界 | `tests/test_raw_update.py`、`tests/test_update_pipeline.py`、`tests/test_source_sync.py` | 修改更新链时 |
| normalized 消费者 contract 漂移 | manifest、summary、JSONL、translation schema 与 renderer 一致 | contract/行为测试与生成后 validator | 仓库 reference、规则及生成目录 | `tests/test_normalized_*.py`、`scripts/validate_normalized_outputs.py` | 修改输出模型、schema 或发布数据时 |
| 完整流水线顺序、短路、隔离 run 或 promotion 失效 | 步骤顺序正确，失败固化且旧 pointer 不变，completed/pointer 身份可复核 | 编排与文件系统状态机测试；发布前完整刷新 | pytest 注入 runner；Windows/Linux 临时目录；完整刷新使用仓库数据 | `tests/test_run_full_refresh.py`、`tests/test_dataset_runs.py`、`scripts/run_full_refresh.py` | 修改编排/promotion 时；发布前 |
| 外部 CDN、manifest 或第三方数据语义变化 | 真实响应仍满足版本、长度、格式和来源约束 | 默认测试 mock 边界；必要时定向在线验收 | 网络、第三方服务或本地游戏安装 | 对应 source 测试与下载脚本 | 适配器修改时；发布前按需 |

明确 Bug、解析规则、状态转换和输出 contract 优先先写失败测试。第三方接口探索或未知游戏数据先记录可复现输入与验收场景，边界稳定后再固化自动化测试。纯文案、注释、格式、私有重命名和不改变语义的文件移动不新增源码字面量测试，使用目标 diff、静态检查和已有回归证明。

人工验收覆盖自动化无法真实证明的本地游戏安装路径、真实 GAME/LCU manifest 与 CDN 可用性、生成 review site 的视觉和交互、以及中文文案是否符合游戏内语境。默认测试不得依赖在线服务或大型游戏资产。

命令分层：

- 定向检查：受影响测试文件，加上 Ruff 与当前 mypy 门禁。
- 默认交付门禁：`uv run ruff check src scripts tests`、`uv run mypy`、`uv run python -m pytest tests/ -q`。
- 发布数据门禁：默认交付门禁、`uv run python scripts/run_full_refresh.py`、代表事件查询与必要的人工审阅。

当前 mypy 只覆盖已完整注解的稳定核心模块；新增稳定模块应逐步加入，未纳入部分仍由运行时测试和 Ruff 守护。Ruff 暂不对历史代码执行全规则或全量格式门禁；扩大规则集必须先形成可审阅的独立清债变更。在线源和真实游戏安装链不在本地默认门禁中，涉及相应适配器时必须报告是否完成在线验收。

live 采集入口以 `tests/test_collection.py`、`tests/test_collection_sources.py` 和
`tests/test_collection_discovery.py` 维护来源、目录发现、原名与失败恢复边界；适配器变化时另做真实
GAME/LCU 的定向在线验收，不能用离线 mock 宣称未知版本兼容。新云环境直接执行采集 CLI，
使用 Python 3.13、`UV_PYTHON=3.13` 及 `uv sync --dev --extra download --locked`。
采集 CLI 只生产 raw round 与证据，不复跑语义发布链；完整交付门禁仍在本地执行。

## 7. 项目例外

- 项目采用累计 raw/reference 与大量文本规则作为长期知识资产；它们虽然是数据文件，但不是可随时再下载替代的构建缓存。`dist/`、`.temp/` 和大型游戏资产才是禁止提交的生成/临时内容。
- 本地维护解释器为 Python 3.14，但项目声明运行于 Python 3.12 及以上；代码与 mypy 以 3.12 语法和类型能力作为最低边界。
- 由于仓库已有大规模未统一格式化的历史代码，初始化阶段只要求修改过的 Python 文件使用 Ruff formatter，不做全仓库机械重排。复审条件是独立格式化任务获得授权且能保持行为测试全绿。
- 类型检查采用严格但增量的模块白名单，而不是用宽松全仓库配置制造虚假覆盖。每个新纳入模块必须在 strict 模式下通过。
