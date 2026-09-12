# 中文事件消费产物

完整 `normalized/` 保留源证据、语义 frame、兼容字段和审计能力；网页或软件无需附带这份完整审计数据。`build_consumption.py` 从一份已校验内容身份的本地 normalized 生成独立消费包。

## 选择用途

| Profile | 使用方式 | 内容 |
| --- | --- | --- |
| `web` | 浏览器按实体或事件名按需加载 | 实体分片、去重中文词典、紧凑记录、可选原名路由桶、ES module 客户端 |
| `software` | 软件一次导入或逐行读取 | `events.jsonl`，具名字段、触发解释、完整主体文案、record/event 身份和状态 |
| `lookup` | 简单工具读取单个 JSON | `lookup.json`，事件原名到词条 ID 和记录上下文的索引；同名始终保留多候选 |

默认生成全部三种 profile，保留全部支持级别和资产类型。可以显式选择用途、VO/SFX、Stable 或特定实体；筛选条件和结果计数写入 manifest。

```powershell
# 从明确的本地 candidate 构建；输出目录必须尚不存在
uv run python scripts/build_consumption.py `
  --normalized-root dist/runs/completed/<run-id>/normalized `
  --output-root dist/consumption/<export-id>

# 只导出某个英雄的网页数据
uv run python scripts/build_consumption.py `
  --normalized-root dist/runs/completed/<run-id>/normalized `
  --profile web --entity champion:Ahri `
  --output-root dist/consumption/<export-id>-ahri

# 仅选正式支持的语音；省略 normalized-root 时读取 approved-local
uv run python scripts/build_consumption.py `
  --profile lookup --support-level stable --asset-kind vo `
  --output-root dist/consumption/<export-id>-stable-vo

# 复核文件闭包，并逐条重新从源 frame 生成中文投影进行比较
uv run python scripts/validate_consumption.py `
  --root dist/consumption/<export-id>/web `
  --normalized-root dist/runs/completed/<run-id>/normalized
```

导出不会修改审计数据、规则、批准状态或旧输出。失败时保留不完整目录；只有具备可验证 manifest 的目录才是完成产物。

## 中文解释与上下文

首要合同是 `事件原名 → 用户看得懂的中文触发解释`。使用正式 renderer 的 default 模式，只省略可以证明是当前主体的英雄和皮肤上下文。不会盲目剪掉字符串前缀，也不会把交互对象、借用技能的英雄、模式、动作或阶段当作主体删除。

例如，阿狸的皮肤 Q 事件在已有英雄页面上可以显示 `Q技能释放`；具体皮肤中文名无需在每条 JSON 里重复。语义上需要的模式或交互对象继续存在于解释中。仅有预览的事件保留预览并明确标记，完全缺解释时 `label=null`，原名不删除。

软件包额外保存 `full_label`，需要完整主体文案时可以直接读取。网页包省略的是上下文显示名称，而不是事件原名、实体、结构化皮肤编号或语义状态；调用方可以根据实体和皮肤编号使用自己的名称表。

这里的无损是针对已声明的消费合同：选择范围内逐条保留原名、中文触发解释、上下文事实、状态和审计定位。完整规则命中、provenance、多语言 frame 与调试资料仍属于原审计数据，不声称小包能够还原所有审计字段。

## 网页紧凑格式

每个实体分片有一次性 `entity` 信息、`labels` 中文词典、`states` 状态词典和记录数组。列顺序由 `columns` 明确声明：

```json
{
  "columns": ["raw", "label", "flags", "skin", "state", "source_index"],
  "labels": ["Q技能释放"],
  "states": [["vo", "experimental", "rendered", "pending", "translation"]],
  "rows": [["Play_vo_AhriSkin01_AhriQ_cast3D", 0, 7, 1, 0, 123]]
}
```

`label`、`state` 是所在文件内的数组索引，不是跨导出永久 ID。`skin` 是有结构化主体证据的皮肤编号，缺失为 null；`source_index` 是原 `normalized-events.jsonl` 的一基行号，与源 content digest 共同定位完整审计记录。同一个原名可以对应多条记录，消费者不得直接覆盖为单值。

`state_columns` 顺序为 `asset_kind, support_level, render_status, review_status, label_basis`。`rendered` 只代表正式渲染器已生成文案，不代表 Stable 或人工批准；Experimental、fallback、missing 和 preview 都保持显式。

| 标志位 | 含义 |
| --- | --- |
| 1 | 原事件名含有 Skin 数字 token；这是词法事实，不等于已识别皮肤 |
| 2 | 主体英雄上下文已从解释中省略 |
| 4 | 主体皮肤上下文已从解释中省略 |
| 8 | 中文来自既有预览，尚无可用正式渲染 |
| 16 | 存在归属、渲染或解释缺口，需要审阅 |
| 32 | 逻辑实体尚未确认 |

标志可以按位组合，数值定义同时写入 manifest。支持级别单独读取，不能用某个标志推导 Stable。

`web/manifest.json` 只列实体分片与文件摘要，不附带全量事件。已知实体时直接加载对应片；只知道事件名时先按 UTF-8 SHA-256 前两个十六进制字符加载 `routes/<bucket>.json`，再加载命中的实体片。不要预取所有分片。

```javascript
import { createClient } from "./web/client.mjs";

const client = await createClient(new URL("./web/", import.meta.url));
const result = await client.lookup("Play_vo_AatroxSkin01_AatroxE_cast3D");
if (result.status === "FOUND") {
  for (const record of result.records) console.log(record.label, record.state);
}
```

使用普通 HTTP 静态托管；无需 API 或框架。客户端校验实际加载文件的摘要，MISS 与网络/完整性错误分别处理，缓存有界。`.gz` 是确定性压缩副本，供支持 gzip 的分发或部署配置选用；默认客户端请求 `.json`，不能把 gzip 体积误称为未配置压缩时的网络体积。

## 身份与验证

每个包 manifest 保留源 `dataset_id/content_digest`、筛选器、记录数、公共投影摘要、文件 SHA-256 和确定性 `package_id`。不同包可独立校验；修改或增加文件、错配路由、越界词条、重复源定位均应失败。

每个 profile 附带只依赖 Python 标准库的 `consumer.py`，其 `--help` 给出独立查询用法。`validate_consumption.py --normalized-root ...` 进一步证明导出符合当前源投影。两者不授予 dataset approval。

可以用 `python -I -B consumer.py PROFILE_DIR EVENT_NAME` 在未安装本项目的环境中查询。作为模块导入时也使用 `-B` 或设置 `PYTHONDONTWRITEBYTECODE=1`，避免在封闭消费目录内生成 `__pycache__`，导致完整性检查因额外文件失败。

`record_id` 继续表示包含来源集合的审计快照身份。新增 `event_id` 由原名和逻辑实体生成，不因补充来源改变；它也不能跨实体纠正保持不变。使用以下命令获取有来源证据的一对一迁移关系，歧义不会自动合并：

```powershell
uv run python scripts/compare_event_identity.py `
  --previous-root dist/runs/completed/<previous-run>/normalized `
  --current-root dist/runs/completed/<current-run>/normalized `
  --output-json dist/reports/identity-migration.json
```

新字段对旧 V2 审计产物是 additive 兼容；已批准旧产物保持原样。读取旧数据可派生连续身份，但新实体 Demo 要求重新生成包含归属证据和新 validator 身份报告的候选。
