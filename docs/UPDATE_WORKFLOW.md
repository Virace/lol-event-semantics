# 更新流程开发者介绍：live 采集、版本化累计更新与新版本接入

本文说明事件数据怎样经过采集、语义处理和 Review。live 采集入口可以独立准备当前版本的 raw round；
当前累计输入采用冻结 V2 的 `16.20` 合同，V1 `16.16` 历史档案保持不变。入口不会把任意新 live 版本直接写入长期语料。新版本必须先建立并
Review 新的版本化 frozen profile 与来源合同，再进入正式更新。GitHub Actions 当前全部停用，
采集与完整验证均在本地或云电脑直接执行；正式发布并完成本地完整流程验证后，才另行决定是否恢复。

如果你想直接动手扩充词库，请读 [CONTRIBUTING.md](../CONTRIBUTING.md)；本文是它的背景知识。

## 当前 live 采集：可独立放到云端执行

干净云环境使用 Python 3.13 和 uv，设置 `UV_PYTHON=3.13` 后运行：

```powershell
uv sync --dev --extra download --locked
uv run python scripts/collect_raw_events.py --output-root dist/collection `
  --champions all --maps all
```

两类默认 `all`，可用 `none` 关闭一类；英雄支持逗号分隔的别名或数字 ID，地图支持数字 ID。
采集器将当前 GAME/LCU 主版本配对，并要求固定提交的大厅快照与 LCU manifest ID 精确一致。
快照 JSON 每次按 `metadata.files` 的长度和 SHA-256 核验。请求来自同轮目录声明的所有
皮肤/炫彩及当前地图根 BIN，并与实际 WAD 索引交叉核对；它不宣称穷举任意 WAD 内的全部 BIN。
TFT/TFTM 被排除，事件原名保持不变。

输出布局为：

```text
dist/collection/
  latest.json                    # run 字段是最后成功的轮次目录名
  rounds/<digest>/
    raw/champions/<id>.txt
    raw/maps/<id>.txt
    source-manifest.json
    collection-plan.json
    collection-report.json
  cache/entities/                # 按 WAD 内容、BIN 清单、解析器身份复用
```

相同输出根互斥执行；每个实体完成后保存进度，重新运行原命令会校验已有文本摘要再复用，
损坏缓存会重取。来源、请求或解析器变化会形成不同身份；跨轮缓存仅在实体身份仍匹配时复用。
中途失败时来源中的 `raw-bins` 保持 `pending`，不会推进 `latest.json`；全部完成后才写为 `ok`。
来源解析或目录发现阶段就失败时可能尚无轮次报告，应查看命令错误。

同步 GAME/LCU manifest 与 WAD Range 下载会遵循现有 HTTP(S) 环境代理和 `NO_PROXY`。
适配只补充第三方同步连接池的代理路由，保留 TLS 验证、Range、超时、响应检查与异常；
不修改系统网络配置，不改变异步整包下载器，也不在代理失败后切换直连。

带 `catalog` 的新采集来源在 online 导入前还执行两层绑定：

1. 本轮 raw 文件集合必须与 `collection.raw_files` 完全相同，并逐文件核验 SHA-256。
2. reference 只能从 `catalog.revision` 的固定提交下载；metadata 摘要、GAME/LCU
   主版本、精确 LCU manifest ID 及每个请求文件的长度/SHA-256 必须匹配采集证据。

新 profile 的 `require_catalog: true` 强制上述证据；缺少证据时在 reference 下载前停止。
严格在线缓存保留原始 JSON 字节与 metadata；local 导入也会离线复核这些摘要与本轮 raw，
不能通过切换 local 模式跳过证据校验。中文版缓存仍使用既有 `zh_cn` 目录，远端为 `zh_CN`。
online 的严格缓存位于指定 cache root 下的 `snapshots/<catalog revision>/`，避免不同快照
留下的旧英雄文件混入本轮验证。英雄详情最多六路并发；下载失败只留下可诊断缓存，不写累计源。
历史 V1 不凭空新增 catalog 字段，因此仍保留旧输入来源身份。新证据存在时会进入数据集
来源摘要，防止同补丁不同目录或采集范围被当成同一输入。这些校验不代表语义批准或 promotion。

采集在本地或云电脑直接执行 CLI；需要长期续跑时保留同一个输出根。
16.20 已通过独立 V2 profile 合并累计 raw/reference；其他版本仍会被不匹配的 profile 拒绝。
采集完成只提供可审查的输入，不能替代质量门禁、精确模式审阅与最终数据集批准。
旧 `download_sources.py` 继续服务字符串表、大厅 JSON、RST 和本地兼容路径，其 raw-bins 的
`range(100)` 皮肤探测不用于完整 raw 采集。

## 总览：三层结构与一条流水线

```text
raw/events/            原始事件名（不可变源数据，累积式）
  ↓  scripts/run_full_refresh.py
dist/runs/.staging/<run>/work/*.jsonl
                       拆解层：事件名 → 词法原子 + 语义槽位（无损）
  ↓  scripts/build_normalized_outputs.py
dist/runs/.staging/<run>/normalized/
                       翻译层：语义槽位 → translation frame + 中文文案
  ↓  global validate + Stable absolute gates + trend ratchet
质量门禁               0 error + Stable/accepted corpus 全零硬门 + Experimental 趋势 + 全量 pytest
  ↓  completed candidate + Review Site（不自动 promotion）
```

一键跑完整链：

```powershell
uv run python scripts/run_full_refresh.py `
  --raw-root raw\events --reference-root reference `
  --runs-root dist\runs --baseline data\quality-baseline.json
```

任何一步失败即停（`FAILED at step: <步骤名>`）。

### 已停用更新模板的输入合同

`.github/workflows/update-raw-events.yml.disabled` 仅保留供未来恢复时参考，当前不能触发。
以下行为描述模板原定义，不表示已启用；本地完整流程验证通过且正式发布后才能另行决定恢复。
模板复用同一入口，不旁路调用 build/validate。选择
`commit_changes=true` 时，runner 会先在本地形成只包含 `raw/reference/state` 的提交，
用 clean generator 跑完整候选；只有全部阻断门禁通过后才 fast-forward push。未选择提交时
不会写远端，只上传明确用于 Review 的 candidate。成功与失败 artifact 均保留 run 身份、
normalized（若已生成）、Review Site 和诊断报告，但两者不会混用名称：成功结果是
`candidate-dataset`，失败结果只能是 `failed-run-diagnostics`。artifact 本身不代表 approved。
选择 `raw_source_mode=artifact` 时，必须提供实际产出 raw round 的 `raw_input_run_id`。同一 run
还要上传来源清单 artifact，内部文件名固定为 `source-manifest.json`。workflow 使用当前仓库的
Actions token、仓库身份和同一个 run ID 下载两项输入，防止 raw 与来源证据跨轮混用。
新 `collect_raw_events.py` 直接生成该清单并与 raw round 绑定；旧 `download_sources.py` producer
仍须在同一 run 内用 `prepare_source_manifest.py --input <游标>` 投影并校验，再以固定文件名分别上传。

选择 `raw_source_mode=manifest` 时，必须提供 GAME manifest URL、对应 manifest ID 与同轮
`lcu_version`、`lcu_manifest_id`。本地冻结清单的 null LCU 例外不会放宽到远端采集。workflow 会生成不含机器路径和运行时间的
本轮来源清单，并在 raw 抽取成功后才标记 `raw-bins=ok`。两种模式最终都把同一个 `.temp/source-identity/source-manifest.json` 显式传给
`update_raw_events.py` 和 `run_full_refresh.py`。

同一约束也由直接入口强制执行：`update_raw_events.py --mode online` 及共享 online pipeline API
必须同时收到来源清单与 frozen profile，并在任何在线 reference 下载前验证 portable remote contract；
缺失参数、仓库内 `local` 冻结清单，或 GAME/显式 Data Dragon 请求版本不属于同一 frozen 系列都会
立即失败。

更新器先在 staging 中生成 raw/reference/state/report，再用本轮来源清单和
调用方选择的 profile 检查 GAME/LCU/reference 版本系列与 raw 唯一计数（当前为
`data/frozen-profile.v2.json`，累计 109191 个唯一原名）。任一项漂移都不会
替换长期目标。仓库中的 `data/frozen-sources-manifest.v1.json` 只用于复现已冻结的本地 V1 输入，
不冒充远端 workflow 本轮采集证明。

保留的模板指定 Python 3.13 做全新锁定安装，以避开当前传递依赖在 Python 3.14 无缓存环境中的
构建兼容问题，并定义采集与写入前的 Ruff、mypy 与完整 pytest。所有 Actions 当前均停用，
这些门禁需要通过本地命令执行，不能把保留模板视为已经运行的检查。

## 第 1 层：数据摄入（版本化累计更新）

输入事件名与参考数据进入仓库的路径：

1. `scripts/collect_raw_events.py` 从当前 live 自动发现并采集本轮事件名；显式本地 WAD/BIN 请求仍可用 `scripts/extract_raw_events_from_game.py`；
2. `scripts/check_raw_update.py` 只读对比本轮与累积 `raw/events/` 的差集；
3. `scripts/update_raw_events.py` 把差集合并进累积语料，并可串起 reference 导入；
4. `scripts/import_reference_data.py` 把三个官方数据源写进 `reference/`：
   - game-data（英雄/皮肤/技能）
   - Data Dragon（名称/称号）
   - 腾讯 `hero_list.js`（中文俗名/拼音搜索别名）

在已经建立对应 frozen profile 的版本中，标准名字、称号、皮肤名会自动进入 `reference/`；
官方数据缺少产品需要的别名时，才在 `overrides/champion_nicknames.yaml` 补充。当前 profile 之外的
版本会在写入前停止，不能把“下载成功”当成“已通过来源与语义 Review”。

## 第 2 层：拆解（自动为主，非标准命名才补规则）

拆解层把 `Play_vo_AsheSkin08_VolleyRank2_OnCast` 这类事件名切成词法原子，
并解析成语义槽位（owner=Ashe、skin=Skin08、contextual_alias=VolleyRank2、
phase=cast…）。它依赖三类知识：

- **通用规则**：`rules/phases.yaml`、`rules/action_aliases.yaml`、
  `rules/normalization.yaml` 等——相位词、动作词、规范化写法；
- **参考实体**：`reference/` 里的英雄/皮肤/地图名，用来识别名字段；
- **英雄专属规则**：`rules/contextual/champions/<英雄>.yaml` 分片——
  只有当英雄用了非标准 token（技能内部代号、缩写）才需要。

新英雄如果走标准命名（绝大多数如此），这一层零人工。怎么发现需要补分
片：看 `dist/reports/runs/latest/` 里的置信度分布、`--write-low-trust-rules`
导出的低信任命中表、validate 的 suspicious 警告。

**设计原则：无损。** 所有 token 都进语义槽位，即使当前没有词条给它做中
文展示——展示层丢词会被 `display_drops_parsed_slot` 校验拦下。

## 第 3 层：翻译/展示（词表门禁，新概念先安全落地再人工扩表）

翻译层把语义槽位组装成结构化 translation frame（`voice_event` /
`ability_cast_event` / `announcer_event` / `shop_voice_event` 等），再渲染中文
文案。所有中文词条集中在 **`rules/display_labels.yaml`**，核心机制是
**词表门禁**：

> 表外的新词**不建 frame，回落 legacy 展示**。宁缺毋错，严禁英文
> fallback 混进中文文案。

所以新概念出现时的时间线是：

1. 新词落到 legacy/fallback → Stable 范围由绝对门禁立即阻断；Experimental
   范围的 fallback 率上升或 `translation_missing` 则由趋势棘轮阻断或报告显现；
2. 代理核验词义——**一手来源优先级：国服客户端字符串表（中英同键对
   照）> Data Dragon 中英同键 > 官方公告**。可靠社区资料可作为旁证；当官网/客户端
   与多个独立社区证据形成一致闭环时可直接接受。只有单一惯称、证据冲突或无法绑定
   当前 token 时继续 fail-closed，不凭流行度猜译；
3. 把词条加进 `rules/display_labels.yaml` 对应分节（纯数据改动），全量
   重建，fallback 回落，基线收紧到新水位。

只有出现**全新的事件结构**（某个新玩法带来前所未见的事件形态）才需要
写代码加 frame 类型——这在历史上大约每个功能批次发生一次，普通的游戏
版本更新用不到。

## 质量安全网

| 机制 | 作用 |
|---|---|
| `validate_normalized_outputs.py` | 0 error 硬门：schema/契约/渲染冒烟/无损性 |
| `check_quality_ratchet.py` | 棘轮：fallback 率、残留英文率、可疑实体数等只许改善不许恶化 |
| Review bundle + 决定校验 | 代理结合官网/客户端与可靠社区旁证审核；长期源决定绑定 Stable 语义合同，临时 support diff 另行绑定最终 candidate approval |
| 全量 pytest | 单测 + 契约测试（代码逻辑与产物结构，不锁定具体文案） |

更新数据后只要这三道门全绿，就可以认为这次更新被安全消化了。
