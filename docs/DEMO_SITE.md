# 事件语义静态演示站

`scripts/build_demo_site.py` 把同一个 dataset run 的完整 normalized 审计数据与 validator 报告投影为只读静态站。Demo V3 统一浏览英雄、装备、地图、模式实体、公共系统和未知实体，中文触发解释复用消费层投影，支持按语义状态筛选和查看问题证据。它不替代 normalized、Product Review Site 或数据批准入口。生成结果不依赖服务端 API，可由本地 HTTP、GitHub Pages 或 Cloudflare Pages 托管。

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

随后访问 `http://127.0.0.1:8765/`。默认 approved-local 入口会从 resolved normalized 的同一 run 推导 validator 报告；显式 `--normalized-root` 必须显式配对 `--validation-json`。生成器核对标准 run 布局、报告身份与记录数、normalized payload/content identity 以及 reference 输入摘要，拒绝混用候选或参考数据。每条记录必须带 `entity.attribution`；旧批准产物保持原样，构建新 Demo 须重新生成带归属证据与新版 validator 报告的候选。不要直接双击 `index.html`，浏览器会阻止页面读取分片 JSON。生成器只会替换带有自身 marker 的输出目录，拒绝覆盖普通目录。

## 公共托管

同一输出兼容 GitHub 项目子路径和 Cloudflare Pages 根路径：所有站内资源使用相对 URL，页面状态保存在 hash 中；入口索引每次重验证，实体分片请求使用 `demo_id` 隔离缓存。`demo_id` 绑定 dataset content digest、validation 摘要、Demo schema 与生成器摘要；浏览器加载分片时核对 `dataset_id`、`content_digest`、`demo_id` 及 schema，拒绝混入其他构建。生成目录还包含：

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

## 实体与语义状态

六类实体共用一个列表与详情界面；当前没有记录的类别不出现在索引中：

| 分类 | normalized 依据 |
| --- | --- |
| 英雄 | `champion`；显式 `base_champion` 可将模式英雄归入基础英雄浏览 |
| 装备 | `item`，编号经装备 reference 核验 |
| 地图 | `map`，统一归属层确认的逻辑地图实体 |
| 模式实体 | `mode_unit` / `mode_boss`，界面归组键为 `mode_system` |
| 公共系统 | `system`，含已确认的公共或模式系统上下文 |
| 未知实体 | `unknown`，保留原别名与归属缺口 |

语义状态独立于分类：`all` 显示全部；`determined`（已确定）要求 `render_status=rendered` 且没有记录警告、validator 问题或未解决归属；其余为 `feedback`（待反馈）。有中文不代表 Stable，已确定也不代表 `review_status=accepted` 或数据集已批准。状态只用于浏览，没有独立 Review 工作台或决定写回。

实体归属由 normalized 统一提供，Demo 不重新根据 raw 子串猜 owner。归属层优先采用来源资产和 reference 精确证据；同层冲突保持未知，人工 `keep` / `downgrade` 无权覆写强来源。模式命名空间仅证明模式上下文，不能把 cue 自动提升为独立实体；`Jade_`、`Ruby_`、`Strawberry_` 分别保留经典模式、末日人机和 PVE 模式上下文。

逻辑地图实体与事件的地图上下文分别表达。事件详情中的地图上下文只来自显式 `semantic.slots.map`，经 `reference/maps` 本地化；没有槽位时显示“通用 / 未指定”，不会从事件原名补猜。逻辑地图实体则使用 normalized 已确认的归属结果。

## 中文、筛选与定位

首要展示是事件原名与中文触发解释。中文通过 `consumption.py` 复用正式 renderer，只省略有结构化证据的主体英雄和皮肤上下文，交互对象、模式、动作和阶段继续保留。预览、缺失、Experimental 和待反馈保持显式。完整审计数据不复制到每个页面分片，也不因 Demo 或消费包体积要求删减；消费合同见 [`CONSUMPTION.md`](../CONSUMPTION.md)。

实体内支持原名/中文/身份/问题搜索，以及资产类型、支持度、模式、地图、皮肤和反馈原因筛选；分页控制本次显示的行数。hash 保存实体、状态、搜索与筛选，例如 `#status=feedback&type=item&entity=item%3A3152`。旧 `status=review` 会转为 `feedback`；不再使用旧 queue/layer 工作台路由。通过复制页面链接可恢复筛选状态。

详情保留原名、归属依据与证据、校验问题、渲染/审核状态及两个身份：`event_id` 绑定原名与逻辑实体，补充来源不改变；`record_id` 表示包含来源集合的本版本审计记录。回报问题时应提供 dataset 身份、原名和这两个 ID，不能只凭原名覆盖同名候选。实体纠正可能改变事件身份，跨候选迁移应使用 `compare_event_identity.py` 的有证据一对一关系。

## 输出与加载

- `data/index.json` 保存 V3 schema、数据集与 Demo 身份、全局/分类计数、实体摘要和分片路径，不附带全量事件。
- `data/entities/<hash>.json` 保存当前实体的事件、皮肤和状态统计。页面先读索引，再按需加载所选实体；不预取全库，内存最多缓存六个实体片。
- 每个分片绑定同一数据集与 Demo 身份。切换实体时按当前路由显示结果，旧请求不会覆盖新选择。
- 英雄头像和皮肤原画只引用 Riot Data Dragon HTTPS 地址，不复制游戏资产。色彩变体若能在同英雄内按英文名唯一匹配主皮肤，就复用其原画；无法唯一匹配时不猜测，并保留基础原画失败回退。
- `dist/demo-site/` 和 `.temp/public-demo/` 是被忽略的生成物；生成计数以当前 `data/index.json` 为准，演示站不执行 promotion。

页面依赖网络加载 Data Dragon 图片；事件数据、筛选、分页和中文标签均来自同一静态构建。视觉与真实图片可用性仍需通过 HTTP(S) 站点检查。
