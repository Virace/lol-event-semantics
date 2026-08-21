# 本地英雄事件演示站

`scripts/build_demo_site.py` 把同一个 dataset run 的 normalized 数据与 validator 报告投影为统一静态工作台。它既用于检查英雄、皮肤、模式前缀、原始事件名和中文语义，也用于审看 SFX 残差与未归属记录；它不是 normalized contract、Product Review Site 或数据批准入口。

## 构建与访问

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

## Review 队列与定位

侧栏的“英雄浏览 / Review 队列”切换始终使用同一 candidate。Review 区包含：

- `SFX 残差`：按 validator 合同固定分为合同结构、支持覆盖、语义投影和低证据审阅；每条记录保留问题代码与原因。
- `未归属记录`：保留无法用结构化证据安全归入基础英雄的记录，不根据字符串碰撞猜测归属。
- 搜索及 layer、code、family、entity、status 过滤；筛选、队列和选中记录写入 hash URL，可直接复制链接恢复。
- 桌面端 Review 表格使用与英雄页一致的主内容宽度；选中记录的详情在表格下方展开，不为隐藏侧栏预留列宽。
- 详情中的稳定 locator 同时包含 `record_id` 与原始 `raw_event_name`，人工 finding 应优先回传这两个字段。

示例入口：`http://127.0.0.1:8765/#view=review&queue=residual&layer=coverage`。hash 只控制本地页面状态，不会修改 candidate 或写回 Review 决定。

## 模式与实体归属

已确认的模式命名空间如下：

| 原始前缀 | 展示模式 | 解释 |
| --- | --- | --- |
| `Jade_` | 经典模式 | 前缀后的实体仍归入对应基础英雄 |
| `Ruby_` | 末日人机 | Doom Bots 模式英雄或英雄衍生实体 |
| `Strawberry_` | PVE 模式 | PVE 模式英雄或英雄衍生实体 |

归属顺序是：normalized 中的结构化 champion/frame/actor/subject/owner 证据优先；只有记录已经带有上述 `mode_context` 时，才检查原始事件名中的同名完整前缀。前缀后只接受紧邻实体 token，或 `Boss`、`Curse`、`Enemy`、`HUD`、`NPC` 这一个受控包装 token 后的实体。复合 token 使用 canonical alias 或官方英文名中的唯一最长匹配，短别名只允许完整匹配；因此 `Wukong` 可回到 canonical `MonkeyKing`，`FNVeigarSkin60` 这类 `FN + 英雄` 形式也被显式支持。

无法唯一、安全归属的记录保持未分配。生成器不会因为任意子串碰撞，把 `Dasher` 归为 `Ashe`、`Mission` 归为 `Sion`，也不会把任务音乐中后置提到的英雄当成事件实体。`raw_event_name` 始终原样展示和复制。

## 输出与限制

- `data/index.json` 保存数据集身份、全局计数和轻量英雄索引；`data/champions/<alias>.json` 按需加载单个英雄，避免首屏读取全部事件。
- `data/review/index.json` 保存同 candidate 的队列、层级与 facet 摘要；`residuals.json` 和 `unassigned.json` 只复制人工审阅所需的紧凑字段，不复制完整 normalized payload。
- 英雄头像和皮肤原画只引用 Riot Data Dragon HTTPS 地址，不复制或提交二进制游戏资产。Data Dragon 会把色彩变体列为独立皮肤编号，但不提供独立 splash；当同英雄内的英文名可唯一匹配括号前主皮肤时，变体复用主皮肤原画，名称与编号仍保持原值。无法唯一匹配时不猜测，并保留基础原画失败回退。
- `dist/demo-site/` 是被忽略的生成物，不进入 Git。页面中的候选/批准状态以输入数据集为准，演示站本身不执行 promotion。
- 当前 16.16 显式 candidate 的演示投影包含 106,926 条来源记录，其中 100,442 条归入 173 位基础英雄，6,484 条保持未分配。SFX Review 为 326 条记录 / 326 个问题，全部属于支持覆盖；合同结构、语义投影与低证据层均为 0。

页面依赖网络加载 Data Dragon 图片；事件数据、筛选、分页和中文标签均来自本地生成文件。视觉与真实图片可用性仍需在本地 HTTP 服务中人工检查。
