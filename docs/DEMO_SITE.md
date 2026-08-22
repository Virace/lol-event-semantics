# 事件语义静态演示站

`scripts/build_demo_site.py` 把同一个 dataset run 的 normalized 数据与 validator 报告投影为统一只读静态站。它既用于检查英雄、皮肤、模式前缀、显式地图槽位、原始事件名和中文语义，也用于审看 SFX 残差与未归属记录；它不是 normalized contract、Product Review Site 或数据批准入口。生成结果不依赖服务端 API，可由本地 HTTP、GitHub Pages 或 Cloudflare Pages 托管。

## 本地构建与访问

默认从 `dist/runs/approved-local.json` 解引用已批准数据集：

```powershell
uv run python scripts/build_demo_site.py
uv run python -m http.server 8765 --directory dist\demo-site
```

检查尚未批准的 completed candidate 时必须显式指定输入，避免把候选误称为 approved：

```powershell
uv run python scripts/build_demo_site.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --validation-json dist\runs\completed\<run-id>\reports\normalized-validation.json `
  --reference-root reference `
  --output-root dist\demo-site
uv run python -m http.server 8765 --directory dist\demo-site
```

随后访问 `http://127.0.0.1:8765/`。默认 approved-local 入口会从 resolved normalized 的同一 run 推导 validator 报告；显式 `--normalized-root` 必须显式配对 `--validation-json`。生成器会验证标准 run 布局、报告声明的 run identity 与 record count，拒绝混用候选。不要直接双击 `index.html`，浏览器会阻止页面读取分片 JSON。生成器只会替换带有自身 marker 的输出目录，拒绝覆盖普通目录。

## 公共托管

同一输出兼容 GitHub 项目子路径和 Cloudflare Pages 根路径：所有站内资源使用相对 URL，页面状态保存在 hash 中；入口索引每次重验证，其余 JSON 分片用 `dataset_id + Demo schema_version` 查询参数隔离数据与投影合同的缓存版本。生成目录还包含：

- `.nojekyll`：阻止 GitHub Pages 把静态文件交给 Jekyll 处理。
- `_headers`：Cloudflare Pages 的 CSP、安全头与缓存策略；GitHub Pages 不读取该文件，因此 HTML 同时带有可跨平台生效的 CSP 与 referrer policy。
- `site.webmanifest`：使用相对 `start_url` / `scope`，不绑定域名或仓库路径。

### GitHub Pages

`.github/workflows/deploy-demo-pages.yml` 只允许手动触发。它在 Python 3.13 的全新锁定环境中从仓库内冻结输入执行完整 refresh，构建同一 candidate 的静态 Demo，再通过官方 Pages artifact 部署；不会复用开发机上的 `dist/` 或 approved-local pointer，也不会修改数据源。首次使用前，在仓库 Settings → Pages 中把 Source 设为 GitHub Actions；随后手动运行 `Deploy Demo to GitHub Pages` workflow。

### Cloudflare Pages

先按上面的显式 candidate 命令把站点构建到一个被忽略的目录，例如 `.temp/public-demo`，再把该目录作为 Direct Upload 资产上传：

```powershell
npx wrangler pages deploy .temp\public-demo --project-name <project-name>
```

也可以在 Cloudflare Dashboard 使用 Direct Upload。实际部署会创建或更新远端项目，必须单独确认账号、项目名和发布时机；本仓库不保存 token，也不把 Wrangler 加入 Python 依赖。

## 两级信息架构

侧栏先选择数据状态，再选择实体分类，四个入口始终使用同一 candidate：

侧栏中的“数据概览”默认折叠，只保留版本及英雄、地图数量摘要；需要完整事件统计时可展开，英雄或地图选择列表会占用其余可用高度。

- `已确定数据 → 英雄`：按安全基础英雄归属浏览事件、皮肤、模式与中文展示。
- `已确定数据 → 地图`：按显式 `semantic.slots.map` 浏览地图事件；地图身份来自结构化槽位及 `reference/maps`。
- `需要 Review → 英雄`：审阅没有显式地图槽位的残差与未归属记录，主要处理英雄归属、动作与翻译问题。
- `需要 Review → 地图`：审阅带显式地图槽位的残差与未归属记录；地图身份已知并不代表其余语义已经批准。

“已确定数据”只表示记录能安全进入当前英雄或地图浏览轴，不等于 `review_status=accepted`，也不等于 candidate 已被 promotion 为 approved-local。同一条记录可以因地图身份确定而出现在地图浏览中，同时因其他语义问题出现在地图 Review 中。

## Review 队列与定位

两个 Review 实体入口共享以下能力：

- `SFX 残差`：按 validator 合同固定分为合同结构、支持覆盖、语义投影和低证据审阅；每条记录保留问题代码与原因。
- `未归属记录`：保留无法用结构化证据安全归入基础英雄的记录，不根据字符串碰撞猜测归属。
- 搜索及 layer、code、family、entity、map、status 过滤；筛选、队列和选中记录写入 hash URL，可直接复制链接恢复。
- 桌面端 Review 表格使用与英雄页一致的主内容宽度；选中记录的详情在表格下方展开，不为隐藏侧栏预留列宽。
- 详情中的稳定 locator 同时包含 `record_id` 与原始 `raw_event_name`，人工 finding 应优先回传这两个字段。

示例入口：

- 英雄 Review：`http://127.0.0.1:8765/#status=review&type=champion&queue=residual&layer=coverage`
- 地图 Review：`http://127.0.0.1:8765/#status=review&type=map&queue=unassigned&map=map12`

旧 `#view=review` 与 `#champion=...` 深链仍会被识别并规范化为新 hash。hash 只控制页面状态，不会修改 candidate 或写回 Review 决定。

## 模式与实体归属

已确认的模式命名空间如下：

| 原始前缀 | 展示模式 | 解释 |
| --- | --- | --- |
| `Jade_` | 经典模式 | 前缀后的实体仍归入对应基础英雄 |
| `Ruby_` | 末日人机 | Doom Bots 模式英雄或英雄衍生实体 |
| `Strawberry_` | PVE 模式 | PVE 模式英雄或英雄衍生实体 |

归属顺序是：normalized 中的结构化 champion/frame/actor/subject/owner 证据优先；只有记录已经带有上述 `mode_context` 时，才检查原始事件名中的同名完整前缀。前缀后只接受紧邻实体 token，或 `Boss`、`Curse`、`Enemy`、`HUD`、`NPC` 这一个受控包装 token 后的实体。复合 token 使用 canonical alias 或官方英文名中的唯一最长匹配，短别名只允许完整匹配；因此 `Wukong` 可回到 canonical `MonkeyKing`，`FNVeigarSkin60` 这类 `FN + 英雄` 形式也被显式支持。

无法唯一、安全归属的记录保持未分配。生成器不会因为任意子串碰撞，把 `Dasher` 归为 `Ashe`、`Mission` 归为 `Sion`，也不会把任务音乐中后置提到的英雄当成事件实体。`raw_event_name` 始终原样展示和复制。

## 地图投影

- 地图只来自 normalized 的显式 `semantic.slots.map`。结构化 ID 或 alias 命中 `reference/maps` 时，页面优先展示同一参考中的 `zh_CN` 名称，并在详情保留 alias / Map ID。
- 未携带地图槽位的记录显示为“通用 / 未指定”；即使 raw event 或来源路径含 `map11` 等文本，也不会据此补猜地图。
- 当前冻结 candidate 有 1,009 条显式地图记录，分布于 7 张地图，都会生成 `data/maps/<key>.json` 浏览分片；这些记录当前也全部位于未归属队列，其中 76 条同时带 validator 残差。
- 英雄表不增加新列，地图标签与模式共享“模式 / 地图”列；地图浏览表沿用相同五列表格宽度；Review 表继续复用“实体 / 语义族”列，完整地图身份放在记录详情中。

## 输出与限制

- `data/index.json` 保存数据集身份、全局计数和轻量英雄/地图索引；`data/champions/<alias>.json` 与 `data/maps/<key>.json` 按需加载单个实体，避免首屏读取全部事件。
- 每个英雄、地图与 Review 分片都携带或引用同一 `dataset_id`；页面拒绝混入另一轮构建留下的缓存分片。
- `data/review/index.json` 保存同 candidate 的队列、英雄/地图 scope、层级与 facet 摘要；`residuals.json` 和 `unassigned.json` 只复制人工审阅所需的紧凑字段（包括显式地图投影），不复制完整 normalized payload。
- 英雄头像和皮肤原画只引用 Riot Data Dragon HTTPS 地址，不复制或提交二进制游戏资产。Data Dragon 会把色彩变体列为独立皮肤编号，但不提供独立 splash；当同英雄内的英文名可唯一匹配括号前主皮肤时，变体复用主皮肤原画，名称与编号仍保持原值。无法唯一匹配时不猜测，并保留基础原画失败回退。
- `dist/demo-site/` 和 `.temp/public-demo/` 都是被忽略的生成物，不进入 Git。页面中的候选/批准状态以输入数据集为准，演示站本身不执行 promotion。
- 当前 16.16 显式 candidate 的演示投影包含 106,926 条来源记录，其中 100,442 条归入 173 位基础英雄，1,009 条带显式地图槽位并分布于 7 张地图，6,484 条保持未分配。SFX Review 为 276 条记录 / 276 个问题，按入口分为英雄 200 条、地图 76 条，全部属于支持覆盖；合同结构、语义投影与低证据层均为 0。

页面依赖网络加载 Data Dragon 图片；事件数据、筛选、分页和中文标签均来自同一静态构建。视觉与真实图片可用性仍需通过 HTTP(S) 站点人工检查。
