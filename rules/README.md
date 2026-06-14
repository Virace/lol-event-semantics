# rules

这里存放长期维护的归一化规则、词典和语义模板。规则文件应保持文本 diff 友好，不迁移到数据库或二进制格式。

## 当前文件

- `normalization.yaml`：token 清理规则和共享词标签。
- `phases.yaml`：阶段 key 和展示标签。
- `map_actions.yaml`：地图动作标签和 token 级地图标签。
- `action_aliases.yaml`：已 review 的全局与英雄特定动作别名。
- `contextual/**/*.yaml`：按 owner、模式或 SFX/system 拆分的上下文别名。
- `contextual_aliases.yaml`：迁移期保留的兼容来源。
- `residual_modifiers.yaml`：可信技能/实体 stem 命中后解析的结构化修饰符。
- `external_entities.yaml`：区域、物种、阵营、结构物和中立生物等手工 fallback 实体。

## 上下文别名布局

上下文别名按匹配上下文 owner 拆分：

```text
rules/contextual_aliases.yaml        # 迁移期兼容来源
rules/contextual/champions/*.yaml    # 英雄 owner 上下文
rules/contextual/modes/*.yaml        # 模式 owner 上下文
rules/contextual/sfx/*.yaml          # SFX / 系统上下文
rules/residual_modifiers.yaml
rules/action_aliases.yaml
rules/external_entities.yaml
```

拆分只改变维护形态，不改变匹配语义。运行时仍必须把所有 contextual fragments 聚合为一个 `RuleBook`；除非有等价性测试证明安全，否则不要只加载当前英雄的规则文件。

新增 contextual fragment 时应显式声明 `scope` 和 `owner`：

```yaml
scope: champion
owner: Gangplank
aliases:
  GangplankR:
    kind: champion_spell
    spell: r
    component: cannon_barrage
    label: 火炮轰击
    confidence: high
```

规则不是展示翻译。展示文本由下游生成器根据语义槽生成，可以比规则 label 更自然。
