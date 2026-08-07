# lol-event-semantics 产品说明

## 目标用户

- 维护《英雄联盟》音频事件语义规则、参考数据和更新流水线的项目维护者。
- 需要把客户端音频事件名转换为稳定结构化语义、中文/英文展示信息的下游工具开发者。
- 审阅未知事件、规则命中、来源证据和质量变化的数据维护者。

## 要解决的问题

客户端事件名混合了英雄、皮肤、技能、阶段、触发方式和技术 token，不能直接作为自然语言展示，也不能仅靠字符串直译稳定解释。项目把累计语料、可验证的官方参考、长期规则和人工审阅决策组织为一部可生成、可审计、可复用的累计语义词典；在这一过程中不改写原始事件名。

## 核心能力

- 累积并保留《英雄联盟》客户端的原始音频事件名。
- 从官方和维护中的参考源导入英雄、皮肤、地图、装备及相关标签。
- 通过规则、词典和精确覆盖拆解事件，保留置信度、来源与未知片段。
- 生成由 `dist/runs/approved-local.json` 指向的稳定消费者数据和版本化 translation frame contract。
- 为每份候选生成可复算的输入身份、payload 文件摘要、`content_digest` 和 `dataset_id`，
  使“相同干净 commit + 相同输入”成为可验证事实，而不是依赖生成时间或目录名。
- 隔离每次生成运行；失败只保留诊断，成功候选只有通过正式门禁与 Review 后才以原子
  approved-local 指针进入默认消费路径。
- 以 VO 为优先解释和展示对象，同时保留 SFX；SFX 的支持状态明确区分 Stable、Experimental 与 Unsupported。
- 将 `zh_CN` 作为正式渲染 locale，并保留结构化多语言信息和回退审计。
- 从 semantic frame 派生只读 Pattern Catalog，供维护者发现可复用的翻译模式候选，而不把候选直接提升为稳定 contract。
- 校验生成物的一致性，输出质量指标、审计报告和本地 review site。
- 通过本地脚本与 GitHub Actions 更新 raw/reference 并执行质量棘轮。

## 非目标

- 不提取、转码或重新分发 BNK、WEM、WAV、GAME WAD 等音频或游戏资产大包。
- 不处理云顶之弈或 TFTM 事件。
- 不提供在线 API、托管查询服务或终端用户 UI。
- 不提供指定历史版本的下载、重建或回放；每轮采集只读取当时的 live 输入，累计语料负责保留已纳入的事件名。
- 不把中间拆解文件、review site 或本地下载缓存作为稳定下游 contract。
- 不猜测缺少一手来源的中文术语，也不把社区惯称冒充官方用词。

## 可验证的成功标准

- 每条对外记录保留原始 `raw_event_name`，所有可读标签和翻译均可识别为派生数据。
- 相同源输入、规则和 reference 能生成确定性的 normalized 记录、manifest 与 summary。
- 生成后 validator 无硬错误，质量指标不突破仓库基线允许的方向。
- 结构化 translation 输出通过 frame、必需角色、locale 回退和 renderer contract 测试。
- 更新链对差集、版本与失败具有可审计报告，不因中途失败留下部分写入状态。
- `16.14` 仅是本轮冻结验收 profile，不是长期固定的“最新版本”默认值。
- Pattern Catalog 候选在正式门禁完成前始终保持 Experimental；`scope_status` 与 `review_status` 分别记录已批准、待精确审阅或暂缓的产品决定，只有完成后续 promotion 才进入 Stable。批准范围同时冻结 canonical pattern ID；未来新增的同 family pattern 默认回到 pending，不能继承历史批准。
- 默认 CI 能在锁定依赖环境中完成静态检查、类型检查和离线测试。
