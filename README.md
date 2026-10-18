# lol-event-semantics

《英雄联盟》音频事件名语义数据、规则与工具仓库：把客户端音频事件名
（如 `Play_vo_XayahSkin09_Spell3Cast2D`）解析为结构化语义，并给出以游戏内
官方用词为准的中文/英文展示文案。

- 本仓库只处理仍属于《英雄联盟》客户端的事件名。云顶之弈 / TFTM 相关事件名不进入 raw 快照、拆解产物、审计报告或对外数据。
- 本项目不是音频提取器，不重新分发 GAME WAD、BIN、BNK、WEM、WAV 或任何游戏资产大包。
- `raw_event_name` 是不可变源数据；一切可读标签与翻译都是派生数据。
- 界面和日常说明统一使用中文状态术语，区分支持级别、中文生成、模式审阅和数据集批准；见 [`状态术语`](docs/TERMINOLOGY.md)。英文状态码仅用于程序接口与原始审计数据。
- 产品视图是一部累计语义词典：每轮只采集当前 live 输入，已纳入事件保留在累计语料中；不提供指定历史版本下载、重建或回放。

## 数据流总览

```text
scripts/collect_raw_events.py      # 当前 live 英雄/皮肤/地图事件采集
  -> dist/collection/rounds/<run>/  # raw round / 同轮来源清单 / 计划与报告
  -> 版本化 profile 与来源审查后，显式合并累计语料
scripts/download_sources.py        # 字符串表 / 大厅 JSON / RST 等兼容下载入口
  -> dist/sources/
raw/events/ + rules/ + overrides/ + reference/
  -> decomposition JSONL + 审计报告
  -> dist/runs/completed/<run>/normalized/
  -> dist/runs/approved-local.json # 唯一默认消费入口（原子指向已批准运行批次）
  -> dist/consumption/<export-id>/{web,software,lookup}/ # 按用途派生消费包
```

## 快速开始

```powershell
uv sync --dev --extra download --locked

# 1) 采集当前 live：默认全部英雄/皮肤与地图，可直接重复同一命令续跑
uv run python scripts/collect_raw_events.py --output-root dist/collection `
  --champions all --maps all

# 2) 只读检查新事件；采集不会自动合并 raw/reference
$collection = Get-Content dist/collection/latest.json -Raw | ConvertFrom-Json
uv run python scripts/check_raw_update.py --raw-root raw/events `
  --input-root "dist/collection/rounds/$($collection.run)/raw"

# 3) 对仓库当前累计输入生成候选（不自动使用上面的新采集结果）
uv run python scripts/run_full_refresh.py

# 4) 查询单个事件的已批准解析结果（P5 完成首个 approved-local 后可用）
uv run lookup-normalized-event --event Play_vo_AatroxSkin01_AatroxE_cast3D --mode main

# 辅助：客户端字符串表中英同键检索（词条核证工具）
uv run python scripts/search_stringtable.py 厄塔汗 "first blood"
```

云端全新环境使用 Python 3.13（`uv python install 3.13`，并设置 `UV_PYTHON=3.13`），
再执行上述锁定安装。采集范围支持 `--champions Annie,Ahri` 或数字 ID、`--maps 11`；
`all` 为默认，`none` 关闭对应类别，不能同时关闭两类。范围是同轮目录声明的全部皮肤/炫彩
与当前地图根 BIN，不宣称穷举任意 WAD 内的全部 BIN；TFT/TFTM 不进入事件语料。
中断后保留输出目录再重跑，已完成实体会按身份和摘要复核后复用。
受限网络环境中，同步 manifest/BIN 读取遵循已有 `HTTP_PROXY`、`HTTPS_PROXY` 与
`NO_PROXY`，不关闭 TLS 校验，也不修改系统代理设置。
新 live 版本要先建立并审阅对应 profile，才能进入累计更新和正式生成，见
[`更新流程`](docs/UPDATE_WORKFLOW.md)。

带 `catalog` 证据的采集轮次进入 online 更新时，会先核验本轮 raw 的文件集合与
SHA-256，再把 reference 固定到同一快照提交，逐文件检查长度与摘要；同补丁但不同
LCU manifest 的快照同样拒绝。新 profile 可通过 `require_catalog: true` 强制此合同。
这不会修改历史 V1 profile、已接受样本或语义批准决定。

`download_sources.py` 继续提供字符串表、大厅 JSON、RST 字典与本地下载兼容能力。
其旧 raw-bins 路径仍按 `range(100)` 探测皮肤编号，不用于完整 raw 采集；完整采集使用
`collect_raw_events.py`。

### 事件语义静态演示站

演示站 V3 用统一实体浏览组织同一候选：英雄、装备、地图、模式实体、公共系统、未知实体六类，与“全部 / 已确定 / 待反馈”语义状态独立筛选。默认读取 approved-local 及其同 run validation 报告；检查尚未批准的已完成候选时必须同时显式传入二者：

```powershell
uv run python scripts/build_demo_site.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --validation-json dist\runs\completed\<run-id>\reports\normalized-validation.json
uv run python -m http.server 8765 --directory dist\demo-site
```

访问 `http://127.0.0.1:8765/`。生成结果是平台中立的纯静态站，可托管到 GitHub Pages 或 Cloudflare Pages。页面先读取轻量索引，再按需读取当前实体分片；中文解释复用消费层投影，支持度、语义状态与人工审核分别展示。实体归属直接读取 normalized 的证据；模式英雄可通过显式基础英雄关系归组，模式与地图上下文仍保留。详情提供 `event_id`、`record_id`、原名、归属依据和校验问题，筛选可通过 hash URL 恢复。“已确定”要求中文已生成且无记录警告、校验或归属问题，不代表稳定支持或人工批准。旧审计产物缺少归属证据时须重建候选；详细构建、身份检查、托管与图片来源见 [`docs/DEMO_SITE.md`](docs/DEMO_SITE.md)。

### 按用途导出中文事件数据

完整 normalized 保留审计证据。在其下可独立生成 `web`（实体分片、中文词典、原名路由和浏览器客户端）、`software`（具名字段 JSONL）与 `lookup`（原名到多候选的单文件索引）。默认保留全部支持级别和资产类型；只导出稳定支持或 VO 须显式筛选。

```powershell
uv run python scripts/build_consumption.py `
  --normalized-root dist/runs/completed/<run-id>/normalized `
  --output-root dist/consumption/<export-id>
uv run python scripts/validate_consumption.py `
  --root dist/consumption/<export-id>/web `
  --normalized-root dist/runs/completed/<run-id>/normalized
```

输出目录必须尚不存在。省略 `--normalized-root` 时读取 approved-local；导出不改变批准状态。网页仅省略有结构化证据的主体英雄/皮肤上下文，保留原名、多候选、语义状态与源定位。完整格式、筛选与独立客户端用法见 [`CONSUMPTION.md`](CONSUMPTION.md)。

## 数据源体系

词条择词按来源权威度分层，游戏内用词优先（我们翻译的是游戏内事件名，
调用方是游戏本体）；多来源歧义都保留并标注来源，标准输出以官方为准：

| 层 | 来源 | 说明 |
|---|---|---|
| L1 | 游戏内字符串表（RST，中英） | 游戏内用词的最权威来源 |
| L2 | 大厅 JSON（items/maps/queues/skinlines/skins） | 客户端数据层官方名（地图名/模式名词性区分依据） |
| L3 | 官网 / 联盟宇宙 / 官方公告 | 世界观语域官方词，与 L1 冲突时保留为别名 |
| L4 | Data Dragon / game-data / hero_list | 结构化官方数据接口 |
| L5 | 社区（wiki / 惯称） | 官方渠道无此概念时降级使用 |
| L6 | 自译 | 兜底，须标注直译性质 |

版本策略：每轮只跟 live 最新版本，不提供指定历史版本下载、重建或回放；
GAME 与 LCU manifest 成对校验（major.minor 一致），整轮下载出自同一份
manifest，数据源之间天然同版本。当前累计输入使用独立 `16.20` V2 profile；`16.16` V1 保留为历史验收合同，
不是长期固定的最新版本默认值。

## 目录契约

- `src/lol_event_semantics/`：共享 Python 包代码。
- `scripts/`：薄入口脚本（下载、导入、拆解、构建、校验、检索）。
- `raw/events/`：累积 raw 事件名语料（`champions/{数字id}.txt`、`maps/{id}.txt`）。
  **事件名获取已脚本化**（`collect_raw_events.py`），
  不再需要手动解包提供；`raw/update-state.json` 是最后成功处理游标。
- `rules/` 与 `overrides/`：人工维护的规则、词典、上下文别名与精确覆盖。
- `reference/`：累计精简参考合集（`champions/`、`maps/`、`skinlines/`、`skins/`、`items/`），解释阶段只读取这里。
- `data/`：小型 schema、质量基线与样例数据。
- `dist/`（整体不入库）：
  - `dist/collection/rounds/<digest>/`：本轮 `raw/{champions,maps}/*.txt`、`source-manifest.json`、
    `collection-plan.json` 与 `collection-report.json`；成功后 `dist/collection/latest.json` 的 `run`
    指向该目录名。失败不推进指针，来源中的 `raw-bins` 保持 `pending`。
    `dist/collection/cache/entities/` 按 WAD 内容、BIN 计划及解析器身份复用实体结果，复用前校验摘要；
    其他目录快照、manifest 与提取缓存也只保留在采集输出根内。
  - `dist/sources/`：数据源下载落点。`manifest.json` 版本游标；
    `stringtable/Global.{locale}-lol.stringtable`（GAME WAD，原 in-WAD 路径
    `data/menu/en_us/lol.stringtable`）；`lobby/{locale}/*.json`
    （en_US 即 `default`，来自 lol-game-data-snapshot 快照仓库的
    `global/{locale}/v1/` 原路径）；`raw-bins/champions/{Champion}/skin{N}.bin`
    （GAME WAD，原路径 `data/characters/{champion}/skins/skin{N}.bin`）；
    `rst-hashes/hashes.rst.xxh3.trimmed.txt`。
    **手工放置规范**：手动获取的文件放入上述同名路径即被识别（游标缺失仅告警，不阻断）；不支持其他手工路径。
  - `dist/runs/.staging/<run>/`：单次刷新隔离写入区；失败不会触碰批准指针。
  - `dist/runs/completed/<run>/`：含 `normalized/`、`reports/`、`run.json`、`completion.json` 的不可变候选。
  - `dist/runs/failed/<run>/`：含 `failure.json` 与部分诊断的失败运行。
  - `dist/runs/approved-local.json`：默认消费者唯一入口；同目录临时文件 fsync 后原子替换。
  - `dist/consumption/<export-id>/`：从明确 normalized 派生的 `web/`、`software/`、`lookup/` 独立消费包，各自带 manifest 与校验客户端。
  - `dist/demo-site/`：同候选的只读静态 Demo，索引与实体分片按需加载。
  - 旧 `dist/normalized/` 只作为显式维护候选路径兼容，不再被默认 lookup 消费。

## 事件拆解说明

- `raw_event_name` 在拆解记录中保持原样；unknown / ambiguous 片段必须进报告，不被静默翻译。
- 上下文别名主要维护在 `rules/contextual/**/*.yaml`；`rules/contextual_aliases.yaml` 保留为迁移和兼容来源。规则运行时全量聚合为一个 RuleBook，不按 owner 局部加载。
- 展示词表维护在 `rules/display_labels.yaml`（phase/action/counterpart/shop/announcer/system SFX 等域），扩词条改词表、不改代码。系统音效只承接完整已审路径；装备 SFX 以 reference 装备 ID 定身份，纯生命周期直接承接，带效果后缀或额外段的记录只有命中“装备 ID/动作词干”精确白名单时才建 frame，未知或错配组合继续待审，不用装备总名掩盖语义缺口。
- residual modifier 在 `rules/residual_modifiers.yaml`；手工 fallback 实体在 `rules/external_entities.yaml`；已审阅整句 fallback 在 `rules/low_trust_events.yaml`（保留 `trust: low` 标记）。
- 单字母 `P/Q/W/E/R` 技能键若前缀猜测后仍有未解析 residual，不得进入稳定支持；只有受控 modifier 解析或参考技能 ID 的明确匹配才能提高置信度。
- VO qualifier 使用结构化 key 分组：已核验 key 渲染为对应中文，未知 key 明示为待审并保留 fallback，不能混入稳定支持。`ShopOpen/ShopClose` 分别渲染为“进店/离店”，`DanceResponse` 是独立于普通舞蹈的回应语义。
- 皮肤短展示会移除与上下文英雄重复的 owner 名称，保留皮肤本体及其他有效描述。

## 展示数据与结构化翻译 contract

面向用户的短展示文案回答"这个音频什么时候触发"，不直译技术 token：

```text
安妮 Q 技能释放
终极魔典阿狸大招释放
```

`Play_vo`、皮肤编号、`cast3D`、`2D/3D`、`voProcessing` 等技术 token 进
`debug_label` 或审计字段，不进短文案。

已批准运行批次的 normalized 记录内 `translation` 对象是结构化渲染 contract
（`frame_type`、版本化 `arguments`、多语言 label、`provenance`、fallback
标记）；`display.zh_cn_short/detail` 是兼容预览字段。需要按页面上下文
裁剪文案的下游程序应读取 `translation` 并按 `locale`、`mode`、
`known_context` 渲染。生成后运行：

每条兼容记录还会增量写入版本化 `record_id`、`event_id`、`source_scope`、`provenance`、
`asset_kind`、`pattern_id`、`support_level`、`semantic_frame`、`rendered_labels`、
`render_status`、`review_status` 和 `warnings`。`record_id` 由 raw、来源域/资产与
最终 owner 的 canonical tuple 生成，不依赖本机路径、输入顺序或生成时间；
`raw_event_name` 仍保持不可变，但不再被当作全数据集天然唯一键。

`record_id` 表示包含来源集合的审计快照身份；`event_id` 由原名和逻辑实体生成，补充来源不会改变它，实体纠正则可能改变。`compare_event_identity.py` 可比较两份 normalized：优先匹配记录身份与事件身份，再用相同原名和来源重叠证明一对一迁移；歧义单独报告，不自动合并。实体归属优先使用来源资产与 reference 精确证据；冲突保持未知，人工 `keep` / `downgrade` 不能覆写强来源。详见 [`CONSUMPTION.md`](CONSUMPTION.md)。

`manifest.json` 的 V2 内容身份不含生成时间、本机绝对路径或运行耗时。它记录
冻结 profile、Git commit/dirty 状态、raw/reference/rules/overrides/契约资产与
生成器源码的规范化摘要，并列出 manifest 之外每个 payload 文件的 SHA-256；
`content_digest` 和 `dataset_id` 由这些稳定字段确定。历史 `16.16` profile 对
缺失的 `lcu_version` 只接受一条例外：lobby snapshot 同为 16.16 且保留非空
LCU manifest id。dirty 工作树可以生成候选，但后续 promotion 不得批准它。

`run_full_refresh.py` 的每个步骤都绑定同一个 staging run，并把候选绑定的
`review-bundle.json` 与本地静态产品审阅站写入 `reports/review-site/`；成功写
`completion.json` 后同卷 rename 到 `completed/`，失败则写 `failure.json` 后移入
`failed/`。`promote_dataset_run.py` 只接受 clean、报告/payload 摘要未漂移且带
正式绝对门禁 attestation 的 completed run，并在独占锁内以 expected previous
digest 做 CAS。P4 自动门禁 attestation 会明确记录 `automatic_gates_passed`，但在
P5 用户审阅前始终保持 `eligible=false`，因此不会把技术通过冒充人工批准。人工
决定以版本化 JSON 导出；稳定支持源审阅只在所有目标均接受时原子更新
`rules/review-decisions.v1.json` 与已接受样本集，拒绝或需要修改的结果不会部分写入。
长期 source target 的哈希只覆盖稳定支持模式/corpus/scope 的稳定语义；相对 approved-local
的 support diff、逐 pattern/label 样例计数和诊断分布仍完整显示，并进入最终数据集批准，
但不会因基线指针或非范围统计变化让相同语义决定失效；冻结的稳定支持模式 数、ID digest
与总记录数仍在 source hash 内。真实目标漂移会回到源审阅；决定源、
corpus 合同或事务摘要损坏则继续阻断。
最终数据集批准必须由产品审阅站针对重建后的同一 `dataset_id`/`content_digest` 导出，
promotion 时通过 `--decisions` 显式传入。

产品审阅站同时展示稳定支持/试验支持的置信度分布、稳定支持中文标签中的英文残留、
qualifier 覆盖，以及每个 pattern 的全部非实体中文标签变体；这些是审核证据，不代表
候选已获接受或可晋级。

```bash
uv run python scripts/validate_normalized_outputs.py `
  --normalized-root dist/runs/completed/<run-id>/normalized `
  --output-json dist/reports/manual/<run-id>-validation.json
```

审核分两步完成，浏览器页面必须通过本地 HTTP 服务访问：

```powershell
# 先打开已完成候选自带的源审阅页面并导出决定
uv run python -m http.server 8765 `
  --directory dist\runs\completed\<run-id>\reports\review-site

# 全部接受后应用长期源决定；存在“需要修改”时此命令 fail-closed 且不写文件
uv run python scripts/manage_review.py apply `
  --bundle dist\runs\completed\<run-id>\reports\review-site\review-bundle.json `
  --decisions <导出的-review-decisions.json>

# 提交长期源后重新生成工作树干净的候选，完成最终数据集批准，再晋级
uv run python scripts/promote_dataset_run.py --run-id <new-run-id> `
  --decisions <导出的-dataset-approval.json>
```

V1 以 `zh_CN` 作为正式渲染 locale，并以 VO 为优先支持对象；SFX 保留，目标支持
状态为稳定支持、试验支持或暂不支持。已批准基线冻结了 91 个精确语义模式，覆盖 4,127 条记录；
修复对象或条件等语义后形成的新模式须重新审阅，不能继承相近模式的批准。当前修复结果须通过
重新审阅与范围复核后才可批准；同类新模式、全部 SFX 和暂缓范围保持试验支持。
稳定支持使用独立 `quality-summary.json` 绝对门禁，不读取也不受
`quality-baseline.json` 放宽；已接受样本集的 91 条代表样本逐条精确复算 `zh_CN`
default/main。门禁还独立冻结 decision ID、91 个 pattern ID 集合摘要和 4,127 条稳定支持
记录总数，不能通过同时缩减 policy 与 corpus 绕过。corpus 与
`rules/review-decisions.v1.json` 的状态必须一致；首轮 92 项源审阅已全部接受，后续
工作树干净的候选进入数据集批准，仍需候选绑定的最终批准才能晋级。

## Pattern Catalog（只读维护报告）

Pattern Catalog 从 semantic frame 派生，不以 `BaseName,Label` 为主模型。生成器会把
公共消费者目录写入已批准运行批次的 `normalized/pattern-catalog.json`；下方入口则生成字段更丰富的
本地维护 inventory，默认读取 `rules/translation_patterns.yaml`，并要求显式指定被忽略
报告目录中的 JSON/Markdown 输出：

```powershell
uv run python scripts/report_translation_patterns.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --output-json dist\reports\runs\latest\translation-patterns.json `
  --output-md dist\reports\runs\latest\translation-patterns.md
```

公共 catalog 仅把精确审阅已接受且结构合格的模式标记为稳定支持；待审阅与暂缓
分别表示尚需精确审阅和当前暂缓纳入支持。公共 manifest/record/catalog/quality Schema、已接受样本集、
无项目源码 consumer 和最小样例位于 `data/`，生成时复制进 candidate 的 `normalized/`。
确定性身份、事务 run、分层绝对门禁、全接受源审阅的版本化 decision importer 和原子 pointer
机制已实现，首个真实 approved-local promotion 已完成；GitHub workflow 当前全部停用并保留为模板。
候选与审核证据不会被自动声明为已批准或稳定发布。

### 无源码消费者快速验证

下面的入口只使用 Python 标准库，同时校验 JSON Schema、manifest 必需路径和实际 payload
文件集闭包、逐文件哈希、`content_digest`、quality/catalog 身份、JSONL 排序与 Pattern Catalog
引用。把 `<run-id>` 换成一次已完成候选；三个命令分别证明稳定支持命中、
试验支持明示与稳定 MISS：

```powershell
$root = "dist\runs\completed\<run-id>\normalized"
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_AatroxSkin01_AatroxE_cast3D --expect stable
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_Annie_Move2DStandard --expect experimental
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_NotARealEvent --expect miss
```

## 本地更新与停用的 Actions 模板

当前采集、更新、生成和验证均在本地或云电脑直接运行 CLI。GitHub Actions 全部停用，原定义保留为
`.github/workflows/update-raw-events.yml.disabled` 和
`.github/workflows/deploy-demo-pages.yml.disabled`，不会被 GitHub 识别为可执行 workflow。
只有本地完整流程验证通过且正式发布后，才评估并明确决定是否恢复 Actions；不自动恢复模板。
提交或交付前仍按 `PROJECT_STANDARD.md` 在本地执行完整门禁。

累计输入现使用 frozen V2 16.20；默认 full refresh 选择 `data/frozen-profile.v2.json` 和
`data/frozen-sources-manifest.v2.json`。V1 文件保留不变；已停用的更新模板仍是历史 V1 16.16 定义。
直接 online 更新入口在网络准备前验证同轮 GAME/LCU 的 portable remote contract，再对累计更新做
staging preflight。清单参与候选内容身份，GAME manifest ID 也进入来源身份；仓库内的本地 frozen
source manifest 不冒充在线采集。16.20 已完成真实目录范围采集及累计来源导入，包含 178 个实体、
9207 个 BIN；新增 2265 个全局原名，累计唯一数为 109191。每实体新增来源条目为 10551，
其中含已有原名的新来源，不能算成 10551 个新事件。来源接入不代表质量门禁或语义批准已完成。
未来 live 版本须先建立并 Review 新的版本化 profile/来源合同。
保留的更新模板记录 artifact 同 producer run、成功候选与失败诊断分离、完整门禁后才 push 的边界，
模板本身不授予执行或 promotion 权限。更新流程详解见
`docs/UPDATE_WORKFLOW.md`，贡献指引见 `CONTRIBUTING.md`，仓库分层说明见
`REPOSITORY.md`。

## 兼容/遗留脚本

`scripts/export_raw_events.py`、`scripts/translate_events.py`、
`scripts/build_translation_preview.py`、`scripts/build_entity_db.py`
为遗留/兼容候选，删除或迁移前需检查引用。

## 数据来源

本仓库消费以下公开数据（事实说明，逐源列明出处）：

- **Riot Games 官方 manifest 与 CDN**：GAME/LCU 版本集
  （sieve.services.riotcdn.net）与 bundle CDN（riotcdn.net），用于按
  manifest 局部提取游戏内字符串表与英雄皮肤 bin。
- **lol-game-data-snapshot**（github.com/Virace/lol-game-data-snapshot）：
  自维护的 game-data 快照仓，**大厅 JSON（items/maps/queues/skinlines/skins）与
  reference 导入的主渠道**——约 6 小时更新一次，`metadata.json` 提供
  gameVersion 与 LCU manifest 对照信息；需要的新文件在快照仓库侧补充，
  避免本仓库重复实现 LCU WAD 解包。
- **Data Dragon**（ddragon.leagueoflegends.com）：英雄、装备、技能结构化
  数据的 API 子集（不使用 dragontail 全量包）。
- **腾讯《英雄联盟》官网接口**：英雄列表（hero_list），接口地址见
  `src/lol_event_semantics/tencent_hero_list_source.py`。
- **CommunityDragon Data**（github.com/CommunityDragon/Data）：RST key
  hash 字典（`hashes/lol/hashes.rst.xxh3.txt`），用于把字符串表 hash 反解
  为 key 名。注：本项目刻意直接解析游戏文件而非使用 CommunityDragon 的
  解包镜像（raw.communitydragon.org），以避免镜像更新滞后。

## 鸣谢

感谢以下开源/公开免费的社区项目（按依赖程度排序）：

- **[CommunityDragon](https://github.com/CommunityDragon)** —— RST key
  hash 字典与镜像服务。字符串表 key 反解直接构建在其社区维护的哈希表
  之上，没有它就只能靠模式反推。
- **[League of Legends Wiki](https://wiki.leagueoflegends.com/)** ——
  语音/音频页面与词条组织是本项目多轮联网核验的重要参照。

本项目为粉丝作品，与 Riot Games 无关联，未获其背书；League of Legends
及相关资产为 Riot Games, Inc. 商标或版权财产。
