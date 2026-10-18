# 事件语义静态演示站

`scripts/build_demo_site.py` 把同一运行批次的完整标准化审计数据与校验报告投影为只读静态站。第三版演示站统一浏览英雄、装备、地图、模式实体、公共系统和未知实体，中文触发解释复用消费层投影，支持按语义状态筛选和查看问题证据。它不替代标准化产物、产品审阅站或数据批准入口。生成结果不依赖服务端 API，可由本地 HTTP、GitHub Pages 或 Cloudflare Pages 托管。

## 本地构建与访问

默认从 `dist/runs/approved-local.json` 解引用已批准数据集：

```powershell
uv run python scripts/build_demo_site.py
uv run python -m http.server 8765 --directory dist\demo-site
```

检查尚未批准的已完成候选时必须显式指定输入，避免把待审候选误称为本地已批准：

```powershell
uv run python scripts/build_demo_site.py `
  --normalized-root dist\runs\completed\<run-id>\normalized `
  --validation-json dist\runs\completed\<run-id>\reports\normalized-validation.json `
  --reference-root reference `
  --output-root dist\demo-site
uv run python -m http.server 8765 --directory dist\demo-site
```

随后访问 `http://127.0.0.1:8765/`。默认本地批准入口会从解析后的标准化目录推导同批次校验报告；显式 `--normalized-root` 必须显式配对 `--validation-json`。生成器核对标准运行目录、报告身份与记录数、标准化产物内容身份以及参考输入摘要，拒绝混用候选或参考数据。每条记录必须带 `entity.attribution`；旧批准产物保持原样，构建新演示站须重新生成带归属证据与新版校验报告的候选。不要直接双击 `index.html`，浏览器会阻止页面读取分片 JSON。生成器只会替换带有自身生成标记的输出目录，拒绝覆盖普通目录。

## 公共托管

同一输出兼容 GitHub 项目子路径和 Cloudflare Pages 根路径：所有站内资源使用相对 URL，页面状态保存在地址片段中；入口索引每次重验证，实体分片请求使用 `demo_id` 隔离缓存。`demo_id` 绑定数据内容摘要、校验报告摘要、展示数据格式版本、生成器摘要及数据集展示状态；同一数据在批准前后的缓存标识不同。浏览器加载分片时核对 `dataset_id`、`content_digest`、`demo_id` 及格式版本，拒绝混入其他构建。生成目录还包含：

- `.nojekyll`：阻止 GitHub Pages 把静态文件交给 Jekyll 处理。
- `_headers`：Cloudflare Pages 的 CSP、安全头与缓存策略；GitHub Pages 不读取该文件，因此 HTML 同时带有可跨平台生效的 CSP 与 referrer policy。
- `site.webmanifest`：使用相对 `start_url` / `scope`，不绑定域名或仓库路径。

### GitHub Pages

GitHub Actions 当前全部停用。`.github/workflows/deploy-demo-pages.yml.disabled` 仅保留原部署模板：Python 3.13 锁定安装、冻结输入完整刷新、同候选静态站构建和 Pages artifact 部署；它不会被 GitHub 识别为可执行工作流。当前先按上文在本地完成构建与验收；本地完整流程验证通过且正式发布后，再另行决定是否恢复 Actions 和部署，不直接启用模板。

### Cloudflare Pages

先按上面的显式候选命令把站点构建到一个被忽略的目录，例如 `.temp/public-demo`，再把该目录作为 Direct Upload 资产上传：

```powershell
npx wrangler pages deploy .temp\public-demo --project-name <project-name>
```

也可以在 Cloudflare Dashboard 使用 Direct Upload。实际部署会创建或更新远端项目，必须单独确认账号、项目名和发布时机；本仓库不保存 token，也不把 Wrangler 加入 Python 依赖。

## 实体与语义状态

六类实体共用一个列表与详情界面；当前没有记录的类别不出现在索引中：

| 分类 | 标准化数据依据 |
| --- | --- |
| 英雄 | `champion`；显式 `base_champion` 可将模式英雄归入基础英雄浏览 |
| 装备 | `item`，编号经装备参考数据核验 |
| 地图 | `map`，统一归属层确认的逻辑地图实体 |
| 模式实体 | `mode_unit` / `mode_boss`，界面归组键为 `mode_system` |
| 公共系统 | `system`，含已确认的公共或模式系统上下文 |
| 未知实体 | `unknown`，保留原别名与归属缺口 |

语义状态独立于分类：“全部记录”显示全部；“已确定”要求中文已生成，且没有记录警告、校验问题或未解决归属；其余为“待反馈”。有中文不代表稳定支持，已确定也不代表模式审阅已接受或数据集已批准。状态只用于浏览，没有独立审阅工作台或决定写回。

页面与日常讨论统一使用中文术语，完整约定见[术语说明](TERMINOLOGY.md)。支持级别、中文生成、模式审阅和数据集批准是不同维度：

| 维度 | 页面用语 |
| --- | --- |
| 支持级别 | 稳定支持、试验支持、暂不支持 |
| 中文生成 | 已生成、回退显示、缺少解释、不适用 |
| 模式审阅 | 已接受、待审阅、已拒绝、暂缓 |
| 语义状态 | 已确定、待反馈 |
| 数据集状态 | 检查未通过、待审候选、本地已批准 |

数据集状态显示在“数据概览”中。标准化清单中的质量检查失败时显示“检查未通过”；只有同批次的本地批准指针通过正式身份复核后才显示“本地已批准”，其余显示“待审候选”。此处是构建时状态快照，批准状态变化后须重建演示站。状态标签、筛选项和归属原因均使用中文；数据字段、枚举值、地址参数、原始事件名与身份标识保留原值。

实体归属由标准化产物统一提供，演示站不重新根据原名子串猜归属。归属层优先采用来源资产和参考数据精确证据；同层冲突保持未知，人工“保留”或“降级”决定无权覆写强来源。模式命名空间仅证明模式上下文，不能把音频线索自动提升为独立实体；`Jade_`、`Ruby_`、`Strawberry_` 分别保留经典模式、末日人机和 PVE 模式上下文。

逻辑地图实体与事件的地图上下文分别表达。事件详情中的地图上下文只来自显式 `semantic.slots.map`，经 `reference/maps` 本地化；没有槽位时显示“通用 / 未指定”，不会从事件原名补猜。逻辑地图实体则使用标准化数据已确认的归属结果；纯数字编号、地图技术别名和参考别名都可查找本地中文名称，原始实体别名保留用于定位。

## 中文、筛选与定位

首要展示是事件原名与中文触发解释。中文通过 `consumption.py` 复用正式生成器，只省略有结构化证据的主体英雄和皮肤上下文，交互对象、模式、动作和阶段继续保留。预览解释、缺少解释、试验支持和待反馈保持显式。完整审计数据不复制到每个页面分片，也不因演示站或消费包体积要求删减；消费合同见 [`CONSUMPTION.md`](../CONSUMPTION.md)。

实体内支持原名、中文、身份标识及问题搜索，以及事件类型、支持级别、模式、地图、皮肤和反馈原因筛选；问题可按中文名称或原始代码搜索。分页控制本次显示的行数。地址片段保存实体、状态、搜索与筛选，例如 `#status=feedback&type=item&entity=item%3A3152`。旧 `status=review` 会转为 `feedback`；不再使用旧的队列和层级工作台路由。通过复制页面链接可恢复筛选状态。

详情保留原名、归属依据与证据、未确认的候选归属、校验问题、中文生成与模式审阅状态及两个身份：`event_id` 绑定原名与逻辑实体，补充来源不改变；`record_id` 表示包含来源集合的本版本审计记录。已知诊断使用中文说明，未识别的问题或动态证据以“原始值”“原始诊断”等中文标签明确标识并保留原文。回报问题时应提供数据集身份、原名和这两个标识，不能只凭原名覆盖同名候选。实体纠正可能改变事件身份，跨候选迁移应使用 `compare_event_identity.py` 的有证据一对一关系。

## 输出与加载

- `data/index.json` 保存第三版展示格式、数据集与演示站身份、数据集状态、全局及分类计数、实体摘要和分片路径，不附带全量事件。
- `data/entities/<hash>.json` 保存当前实体的事件、皮肤和状态统计。页面先读索引，再按需加载所选实体；不预取全库，内存最多缓存六个实体片。
- 每个分片绑定同一数据集与演示站身份。切换实体时按当前路由显示结果，旧请求不会覆盖新选择。
- 英雄头像和皮肤原画只引用 Riot Data Dragon HTTPS 地址，不复制游戏资产。色彩变体若能在同英雄内按英文名唯一匹配主皮肤，就复用其原画；无法唯一匹配时不猜测，并保留基础原画失败回退。
- `dist/demo-site/` 和 `.temp/public-demo/` 是被忽略的生成物；生成计数以当前 `data/index.json` 为准，演示站不执行批准或发布。

页面依赖网络加载 Data Dragon 图片；事件数据、筛选、分页和中文标签均来自同一静态构建。视觉与真实图片可用性仍需通过 HTTP(S) 站点检查。
