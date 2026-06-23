---
name: champion-rule
description: 为指定英雄新增或补全 rules/contextual/champions/<Name>.yaml 上下文别名分片。用户通过 /champion-rule <英雄名> 触发。
disable-model-invocation: true
---

# champion-rule

为某个英雄创建或补全上下文别名规则分片，并完成校验。参数为英雄的内部名（PascalCase，如 `Zed`、`LeeSin`、`TwistedFate`）。

## 背景（必须遵守）

- 分片落点：`rules/contextual/champions/<Name>.yaml`，文件名用英雄内部名。
- 加载机制：`rules/contextual/**/*.yaml` 由 `src/lol_event_semantics/rules.py` 的 `read_contextual_fragments` 自动 `rglob` 发现，**不需要**手动登记到 `rules/contextual_aliases.yaml` 或任何索引。
- 冲突规则：同 `(scope, owner, token)` key 若在分片与 `contextual_aliases.yaml`（或其它分片）出现且内容不同，加载时会 `raise ValueError`。新增 token 前先确认未与既有定义冲突。
- 这些是规范化真值源；不要去改 `raw/`、`dist/`、`reference/`，含义只落在规则源里。

## 流程

1. **确认英雄名与已有分片**：检查 `rules/contextual/champions/<Name>.yaml` 是否已存在。已存在则补全 `aliases`，不存在则新建。
2. **收集待映射 token**：从用户给的 token 列表，或用 `lookup-normalized-event` / `decompose_events` 报告里该英雄未解释的残留 token 入手。必要时先问用户该 token 对应哪个技能/组件。
3. **写分片**：顶层三字段 `scope: champion`、`owner: <Name>`、`aliases:` 必须齐备（缺任一会被静默忽略）。每条 alias 字段参照现有英雄写法：

   ```yaml
   scope: champion
   owner: <Name>
   aliases:
     <Token>:
       kind: champion_spell_component
       spell: q            # p/q/w/e/r
       component: <语义组件名，snake_case>
       label: <中文可读标签>
       confidence: high    # high / medium / low
       source: manual_contextual_alias
       note: <为什么该 token 在该英雄语境下成立、为何不泛化为全局词义>
   ```

   - `note` 用中文，说明语境与不泛化的理由（参照 `rules/contextual/champions/Zed.yaml`、`LeeSin.yaml`）。
   - 通用词（Ult、Marker、Gate 等）务必限定在该英雄 scope，不要提升为全局含义。
4. **校验**（必须执行并贴出结果）：

   ```bash
   # 规则可加载、无冲突抛错
   uv run python -c "from pathlib import Path; from lol_event_semantics.rules import RuleBook; RuleBook.load(Path('rules'), Path('overrides')); print('rules load ok')"
   # 规则分片相关测试
   uv run python -m unittest tests.test_rules_fragments
   ```
5. **可选：审查**。改动较多时用 `yaml-rules-reviewer` 子代理复核结构与冲突。
6. **报告**：列出新增/修改的 token、file:line、校验结果。

## 边界

- 只动 `rules/contextual/champions/<Name>.yaml`（必要时含同英雄的既有分片）。不改其它规则文件、不改生成物。
- 不确定 token 语义时先问，不要臆造映射或 confidence。
