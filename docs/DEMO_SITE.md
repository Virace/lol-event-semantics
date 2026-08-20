# 本地英雄事件演示站

`scripts/build_demo_site.py` 把一份 normalized 数据集投影为按英雄浏览的静态站点。它用于检查英雄、皮肤、模式前缀、原始事件名和中文语义之间的关系，不是 normalized contract、Product Review Site 或数据批准入口。

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
  --reference-root reference `
  --output-root dist\demo-site
uv run python -m http.server 8765 --directory dist\demo-site
```

随后访问 `http://127.0.0.1:8765/`。不要直接双击 `index.html`，浏览器会阻止页面读取分片 JSON。生成器只会替换带有自身 marker 的输出目录，拒绝覆盖普通目录。

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
- 英雄头像和皮肤原画只引用 Riot Data Dragon HTTPS 地址，不复制或提交二进制游戏资产。某些炫彩没有独立 splash 时，界面会回退到基础原画。
- `dist/demo-site/` 是被忽略的生成物，不进入 Git。页面中的候选/批准状态以输入数据集为准，演示站本身不执行 promotion。
- 当前 16.16 clean candidate 的演示投影包含 106,926 条来源记录，其中 100,430 条归入 173 位基础英雄，6,496 条保持未分配。模式事件包括经典模式 61 条、末日人机 1,030 条、PVE 模式 105 条；未分配项以 HUD、环境、任务、通用敌兵和 Boss 系统事件为主。

页面依赖网络加载 Data Dragon 图片；事件数据、筛选、分页和中文标签均来自本地生成文件。视觉与真实图片可用性仍需在本地 HTTP 服务中人工检查。
