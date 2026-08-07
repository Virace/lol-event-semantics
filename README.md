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
  -> dist/normalized/              # 唯一对外主数据源（V2 单流 + 兼容实体分片）
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

# 3) 全量刷新（decompose→build→validate→metrics→ratchet，任一步失败即停）
uv run python scripts/run_full_refresh.py

# 4) 查询单个事件的解析结果
uv run lookup-normalized-event --event Play_vo_Annie_QAttack --mode main

# 辅助：客户端字符串表中英同键检索（词条核证工具）
uv run python scripts/search_stringtable.py 厄塔汗 "first blood"
```

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
manifest，数据源之间天然同版本。`16.14` 是本轮 frozen acceptance profile，
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
  - `dist/normalized/`：对外主数据源；`dist/reports/runs/`：质量与审计报告；`dist/work/`：中间产物。

## 事件拆解说明

- `raw_event_name` 在拆解记录中保持原样；unknown / ambiguous 片段必须进报告，不被静默翻译。
- 上下文别名主要维护在 `rules/contextual/**/*.yaml`；`rules/contextual_aliases.yaml` 保留为迁移和兼容来源。规则运行时全量聚合为一个 RuleBook，不按 owner 局部加载。
- 展示词表维护在 `rules/display_labels.yaml`（phase/action/counterpart/shop/announcer 等域），扩词条改词表、不改代码。
- residual modifier 在 `rules/residual_modifiers.yaml`；手工 fallback 实体在 `rules/external_entities.yaml`；已审阅整句 fallback 在 `rules/low_trust_events.yaml`（保留 `trust: low` 标记）。

## 展示数据与结构化翻译 contract

面向用户的短展示文案回答"这个音频什么时候触发"，不直译技术 token：

```text
安妮 Q 技能释放
终极魔典 阿狸大招释放
```

`Play_vo`、皮肤编号、`cast3D`、`2D/3D`、`voProcessing` 等技术 token 进
`debug_label` 或审计字段，不进短文案。

`dist/normalized/` 记录内的 `translation` 对象是结构化渲染 contract
（`frame_type`、版本化 `arguments`、多语言 label、`provenance`、fallback
标记）；`display.zh_cn_short/detail` 是兼容预览字段。需要按页面上下文
裁剪文案的下游程序应读取 `translation` 并按 `locale`、`mode`、
`known_context` 渲染。生成后运行：

每条兼容记录还会增量写入版本化 `record_id`、`source_scope`、`provenance`、
`asset_kind`、`pattern_id`、`support_level`、`semantic_frame`、`rendered_labels`、
`render_status`、`review_status` 和 `warnings`。`record_id` 由 raw、来源域/资产与
最终 owner 的 canonical tuple 生成，不依赖本机路径、输入顺序或生成时间；
`raw_event_name` 仍保持不可变，但不再被当作全数据集天然唯一键。

```bash
uv run python scripts/validate_normalized_outputs.py --normalized-root dist/normalized --output-json dist/reports/runs/latest/normalized-validation.json
```

V1 以 `zh_CN` 作为正式渲染 locale，并以 VO 为优先支持对象；SFX 保留，目标支持
状态为 Stable、Experimental 或 Unsupported。当前生成器已写入 additive support/review
字段，但在绝对门禁与 promotion 完成前仍全部保持 Experimental。报告用 approved、
pending、deferred scope 记录产品决定，不会把已批准范围提前冒充 Stable。首批批准冻结为
86 个 canonical pattern ID；未来同 family 新 ID 仍需重新 Review。

## Pattern Catalog（只读维护报告）

Pattern Catalog 从 semantic frame 派生，不以 `BaseName,Label` 为主模型。生成器会把
公共消费者目录写入 `dist/normalized/pattern-catalog.json`；下方入口则生成字段更丰富的
本地维护 inventory，默认读取 `rules/translation_patterns.yaml`，并要求显式指定被忽略
报告目录中的 JSON/Markdown 输出：

```powershell
uv run python scripts/report_translation_patterns.py `
  --normalized-root dist\normalized `
  --output-json dist\reports\runs\latest\translation-patterns.json `
  --output-md dist\reports\runs\latest\translation-patterns.md
```

公共 catalog 和维护 inventory 中的候选当前都保持 Experimental；`scope_status=approved`
仅表示产品范围已获批准，pending 与 deferred 分别表示待精确审阅和暂缓。公共 catalog
Schema、record Schema 与最小样例位于 `data/schema/`、`data/examples/`，生成时复制进
`dist/normalized/`。绝对门禁、promotion、review importer 与远端发布尚未实现。

## 自动更新与 CI

`.github/workflows/update-raw-events.yml` 为手动触发的更新 workflow
（在线最小子集 + 全量重建 + 质量棘轮门禁 + normalized artifact 发布）。
本地等价物为 `scripts/run_full_refresh.py`。更新流程详解见
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
