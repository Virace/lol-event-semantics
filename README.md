# lol-event-semantics

《英雄联盟》音频事件名语义数据、规则与工具仓库：把客户端音频事件名
（如 `Play_vo_XayahSkin09_Spell3Cast2D`）解析为结构化语义，并给出以游戏内
官方用词为准的中文/英文展示文案。

- 本仓库只处理仍属于《英雄联盟》客户端的事件名。云顶之弈 / TFTM 相关事件名不进入 raw 快照、拆解产物、审计报告或对外数据。
- 本项目不是音频提取器，不重新分发 GAME WAD、BIN、BNK、WEM、WAV 或任何游戏资产大包。
- `raw_event_name` 是不可变源数据；一切可读标签与翻译都是派生数据。
- 产品视图是一部累计语义词典：每轮只采集当前 live 输入，已纳入事件保留在累计语料中；不提供指定历史版本下载、重建或回放。

## 数据流总览

```text
scripts/download_sources.py        # 初始化/更新入口：一键下载全部数据源
  -> dist/sources/                 # 字符串表 / 大厅 JSON / 皮肤 bin / RST key 字典
raw/events/ + rules/ + overrides/ + reference/
  -> decomposition JSONL + 审计报告
  -> dist/runs/completed/<run>/normalized/
  -> dist/runs/approved-local.json # 唯一默认消费入口（原子指向 approved run）
```

## 快速开始

```powershell
uv sync --extra download          # download 可选组含 riotmanifest（仅下载数据源时需要）

# 1) 一键获取数据源（同版本重跑自动跳过）
#    本地模式（推荐，秒级）：提供游戏安装目录，直接解包本地 WAD，无需联网
uv run python scripts/download_sources.py --game-dir "<游戏安装根目录>" `
  --sources stringtable,raw-bins --champions all --emit-raw-round .temp\raw-events-round
uv run python scripts/download_sources.py --sources lobby,rst-hashes
#    远程模式（无本地客户端时）：按 live manifest 局部下载（区服等价，默认 OC；
#    注：远程全量英雄拉取偏慢，待优化）
uv run python scripts/download_sources.py --sources stringtable,raw-bins --champions all
#    增量模式（第二次执行起）：按上次锚定清单/本地 WAD 指纹做文件级 diff，
#    只处理受影响英雄与判变语言；无锚自动回退全量并落锚（--force 强制全量）
uv run python scripts/download_sources.py --changed-only
uv run python scripts/download_sources.py --game-dir "<游戏安装根目录>" --changed-only `
  --emit-raw-round .temp\raw-events-round

# 2) 把本轮事件名并入累计语料（增量合并，旧事件永不删除）
uv run python scripts/update_raw_events.py --mode local `
  --reference-root reference --raw-root raw\events `
  --state-file raw\update-state.json --raw-input-root .temp\raw-events-round `
  --report-root .temp\update-report

# 3) 全量刷新到隔离 run（含 validate、Stable gate、Review Site、趋势 ratchet）
uv run python scripts/run_full_refresh.py

# 4) 查询单个事件的已批准解析结果（P5 完成首个 approved-local 后可用）
uv run lookup-normalized-event --event Play_vo_AatroxSkin01_AatroxE_cast3D --mode main

# 辅助：客户端字符串表中英同键检索（词条核证工具）
uv run python scripts/search_stringtable.py 厄塔汗 "first blood"
```

### 本地英雄事件演示站

演示站按英雄汇总皮肤、模式前缀、原始事件名与中文语义。默认读取 approved-local；检查 16.16 completed candidate 时应显式传入其 normalized 目录：

```powershell
uv run python scripts/build_demo_site.py `
  --normalized-root dist\runs\completed\<run-id>\normalized
uv run python -m http.server 8765 --directory dist\demo-site
```

访问 `http://127.0.0.1:8765/`。`Jade_`、`Ruby_`、`Strawberry_` 分别展示为经典模式、末日人机和 PVE 模式；模式实体仍归入基础英雄。详细规则、候选状态边界和图片来源见 [`docs/DEMO_SITE.md`](docs/DEMO_SITE.md)。

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
manifest，数据源之间天然同版本。`16.16` 是本轮 frozen acceptance profile，
不是长期固定的最新版本默认值。

## 目录契约

- `src/lol_event_semantics/`：共享 Python 包代码。
- `scripts/`：薄入口脚本（下载、导入、拆解、构建、校验、检索）。
- `raw/events/`：累积 raw 事件名语料（`champions/{数字id}.txt`、`maps/{id}.txt`）。
  **事件名获取已脚本化**（`download_sources.py --sources raw-bins --emit-raw-round`），
  不再需要手动解包提供；`raw/update-state.json` 是最后成功处理游标。
- `rules/` 与 `overrides/`：人工维护的规则、词典、上下文别名与精确覆盖。
- `reference/`：累计精简参考合集（`champions/`、`maps/`、`skinlines/`、`skins/`、`items/`），解释阶段只读取这里。
- `data/`：小型 schema、质量基线与样例数据。
- `dist/`（整体不入库）：
  - `dist/sources/`：数据源下载落点。`manifest.json` 版本游标；
    `stringtable/Global.{locale}-lol.stringtable`（GAME WAD，原 in-WAD 路径
    `data/menu/en_us/lol.stringtable`）；`lobby/{locale}/*.json`
    （en_US 即 `default`，来自 lol-game-data-snapshot 快照仓库的
    `global/{locale}/v1/` 原路径）；`raw-bins/champions/{Champion}/skin{N}.bin`
    （GAME WAD，原路径 `data/characters/{champion}/skins/skin{N}.bin`）；
    `rst-hashes/hashes.rst.xxh3.trimmed.txt`。
    **手工放置规范**：手动获取的文件放入上述同名路径即被识别（游标缺失仅告警，不阻断）；不支持其他手工路径。
  - `dist/runs/.staging/<run>/`：单次刷新隔离写入区；失败不会触碰 approved pointer。
  - `dist/runs/completed/<run>/`：含 `normalized/`、`reports/`、`run.json`、`completion.json` 的不可变候选。
  - `dist/runs/failed/<run>/`：含 `failure.json` 与部分诊断的失败运行。
  - `dist/runs/approved-local.json`：默认消费者唯一入口；同目录临时文件 fsync 后原子替换。
  - 旧 `dist/normalized/` 只作为显式维护候选路径兼容，不再被默认 lookup 消费。

## 事件拆解说明

- `raw_event_name` 在拆解记录中保持原样；unknown / ambiguous 片段必须进报告，不被静默翻译。
- 上下文别名主要维护在 `rules/contextual/**/*.yaml`；`rules/contextual_aliases.yaml` 保留为迁移和兼容来源。规则运行时全量聚合为一个 RuleBook，不按 owner 局部加载。
- 展示词表维护在 `rules/display_labels.yaml`（phase/action/counterpart/shop/announcer 等域），扩词条改词表、不改代码。
- residual modifier 在 `rules/residual_modifiers.yaml`；手工 fallback 实体在 `rules/external_entities.yaml`；已审阅整句 fallback 在 `rules/low_trust_events.yaml`（保留 `trust: low` 标记）。
- 单字母 `P/Q/W/E/R` 技能键若前缀猜测后仍有未解析 residual，不得进入 Stable；只有受控 modifier 解析或参考技能 ID 的明确匹配才能提高置信度。
- VO qualifier 使用结构化 key 分组：已核验 key 渲染为对应中文，未知 key 明示为待审并保留 fallback，不能混入 Stable。`ShopOpen/ShopClose` 分别渲染为“进店/离店”，`DanceResponse` 是独立于普通舞蹈的回应语义。
- 皮肤短展示会移除与上下文英雄重复的 owner 名称，保留皮肤本体及其他有效描述。

## 展示数据与结构化翻译 contract

面向用户的短展示文案回答"这个音频什么时候触发"，不直译技术 token：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
```

`Play_vo`、皮肤编号、`cast3D`、`2D/3D`、`voProcessing` 等技术 token 进
`debug_label` 或审计字段，不进短文案。

approved run 的 normalized 记录内 `translation` 对象是结构化渲染 contract
（`frame_type`、版本化 `arguments`、多语言 label、`provenance`、fallback
标记）；`display.zh_cn_short/detail` 是兼容预览字段。需要按页面上下文
裁剪文案的下游程序应读取 `translation` 并按 `locale`、`mode`、
`known_context` 渲染。生成后运行：

每条兼容记录还会增量写入版本化 `record_id`、`source_scope`、`provenance`、
`asset_kind`、`pattern_id`、`support_level`、`semantic_frame`、`rendered_labels`、
`render_status`、`review_status` 和 `warnings`。`record_id` 由 raw、来源域/资产与
最终 owner 的 canonical tuple 生成，不依赖本机路径、输入顺序或生成时间；
`raw_event_name` 仍保持不可变，但不再被当作全数据集天然唯一键。

`manifest.json` 的 V2 内容身份不含生成时间、本机绝对路径或运行耗时。它记录
冻结 profile、Git commit/dirty 状态、raw/reference/rules/overrides/契约资产与
生成器源码的规范化摘要，并列出 manifest 之外每个 payload 文件的 SHA-256；
`content_digest` 和 `dataset_id` 由这些稳定字段确定。当前 `16.16` profile 对
缺失的 `lcu_version` 只接受一条例外：lobby snapshot 同为 16.16 且保留非空
LCU manifest id。dirty 工作树可以生成候选，但后续 promotion 不得批准它。

`run_full_refresh.py` 的每个步骤都绑定同一个 staging run，并把候选绑定的
`review-bundle.json` 与本地静态 Review Site 写入 `reports/review-site/`；成功写
`completion.json` 后同卷 rename 到 `completed/`，失败则写 `failure.json` 后移入
`failed/`。`promote_dataset_run.py` 只接受 clean、报告/payload 摘要未漂移且带
正式绝对门禁 attestation 的 completed run，并在独占锁内以 expected previous
digest 做 CAS。P4 自动门禁 attestation 会明确记录 `automatic_gates_passed`，但在
P5 用户 Review 前始终保持 `eligible=false`，因此不会把技术通过冒充人工批准。人工
决定以版本化 JSON 导出；Stable 源审阅只在所有目标均接受时原子更新
`rules/review-decisions.v1.json` 与 accepted corpus，拒绝或需要修改的结果不会部分写入。
长期 source target 的哈希只覆盖 Stable pattern/corpus/scope 的稳定语义；相对 approved-local
的 support diff、逐 pattern/label 样例计数和诊断分布仍完整显示，并进入最终 dataset approval，
但不会因基线指针或非范围统计变化让相同语义决定失效；冻结的 Stable pattern 数、ID digest
与总记录数仍在 source hash 内。真实目标漂移会回到 source review；决定源、
corpus 合同或事务摘要损坏则继续阻断。
最终数据集批准必须由 Review Site 针对重建后的同一 `dataset_id`/`content_digest` 导出，
promotion 时通过 `--decisions` 显式传入。

Review Site 同时展示 Stable/Experimental 的置信度分布、Stable 中文标签中的英文残留、
qualifier 覆盖，以及每个 pattern 的全部非实体中文标签变体；这些是审核证据，不代表
候选已获接受或可晋级。

```bash
uv run python scripts/validate_normalized_outputs.py `
  --normalized-root dist/runs/completed/<run-id>/normalized `
  --output-json dist/reports/manual/<run-id>-validation.json
```

审核分两步完成，浏览器页面必须通过本地 HTTP 服务访问：

```powershell
# 先打开 completed candidate 自带的 source review 页面并导出决定
uv run python -m http.server 8765 `
  --directory dist\runs\completed\<run-id>\reports\review-site

# 全部接受后应用长期源决定；存在“需要修改”时此命令 fail-closed 且不写文件
uv run python scripts/manage_review.py apply `
  --bundle dist\runs\completed\<run-id>\reports\review-site\review-bundle.json `
  --decisions <导出的-review-decisions.json>

# 提交长期源后重新生成 clean candidate，完成最终 dataset approval，再晋级
uv run python scripts/promote_dataset_run.py --run-id <new-run-id> `
  --decisions <导出的-dataset-approval.json>
```

V1 以 `zh_CN` 作为正式渲染 locale，并以 VO 为优先支持对象；SFX 保留，目标支持
状态为 Stable、Experimental 或 Unsupported。语义复核后的 91 个精确批准 canonical pattern
在 candidate 中晋级为 Stable，共覆盖 4,389 条记录；同 family 的新 ID、全部 SFX 和暂缓
范围仍保持 Experimental。Stable 使用独立 `quality-summary.json` 绝对门禁，不读取也不受
`quality-baseline.json` 放宽；accepted corpus 的 91 条代表样本逐条精确复算 `zh_CN`
default/main。门禁还独立冻结 decision ID、91 个 pattern ID 集合摘要和 4,389 条 Stable
记录总数，不能通过同时缩减 policy 与 corpus 绕过。corpus 与
`rules/review-decisions.v1.json` 的状态必须一致；首轮 92 项源审阅已全部接受，后续
clean candidate 进入 dataset approval，仍需候选绑定的最终批准才能晋级。

## Pattern Catalog（只读维护报告）

Pattern Catalog 从 semantic frame 派生，不以 `BaseName,Label` 为主模型。生成器会把
公共消费者目录写入 approved run 的 `normalized/pattern-catalog.json`；下方入口则生成字段更丰富的
本地维护 inventory，默认读取 `rules/translation_patterns.yaml`，并要求显式指定被忽略
报告目录中的 JSON/Markdown 输出：

```powershell
uv run python scripts/report_translation_patterns.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --output-json dist\reports\runs\latest\translation-patterns.json `
  --output-md dist\reports\runs\latest\translation-patterns.md
```

公共 catalog 仅把 exact approved 且结构合格的 pattern 标记为 Stable；pending 与 deferred
分别表示待精确审阅和暂缓。公共 manifest/record/catalog/quality Schema、accepted corpus、
无项目源码 consumer 和最小样例位于 `data/`，生成时复制进 candidate 的 `normalized/`。
确定性身份、事务 run、分层绝对门禁、全接受 source review 的版本化 decision importer 和原子 pointer
机制已实现；首个真实 approved-local promotion 与远端发布仍属于后续阶段。

### 无源码消费者快速验证

下面的入口只使用 Python 标准库，同时校验 JSON Schema、manifest 必需路径和实际 payload
文件集闭包、逐文件哈希、`content_digest`、quality/catalog 身份、JSONL 排序与 Pattern Catalog
引用。把 `<run-id>` 换成一次 P4 completed candidate；三个命令分别证明 Stable 命中、
Experimental 明示与稳定 MISS：

```powershell
$root = "dist\runs\completed\<run-id>\normalized"
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_AatroxSkin01_AatroxE_cast3D --expect stable
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_Annie_Move2DStandard --expect experimental
uv run python "$root\examples\consumer.py" --root $root --query Play_vo_NotARealEvent --expect miss
```

## 自动更新与 CI

`.github/workflows/update-raw-events.yml` 为手动触发的更新 workflow
（在线最小子集 + 全量重建 + 质量棘轮门禁 + normalized artifact 发布）。
远端 workflow 仍是旧式 artifact 路径，不等同于本地 approved-local 发布链；本地权威入口为
`scripts/run_full_refresh.py`。更新流程详解见
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
