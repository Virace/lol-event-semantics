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
- `low_trust_events.yaml`：已审阅的整句 exact fallback。命中后可把低置信噪声提升为 `medium`，但会在 `semantic_slots.low_trust_rule` 保留 `trust: low`。
- `spell_match_constraints.yaml`：普攻类通用关键词（BasicAttack/CritAttack）允许推断为英雄技能的白名单，以及经 reference/raw 证据确认的 `owner + 单字母槽位 + residual` 精确白名单；未列入的弱匹配或 residual 不提升为技能槽位。

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
rules/low_trust_events.yaml
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

`low_trust_events.yaml` 只用于已经人工审阅过、暂时不适合抽成通用模式的 exact 事件名，不应作为通用模式库使用。若某类低信任规则后续能被官方参考数据或更明确的上下文规则解释，应迁移到对应高信任规则源；已确认的技能别名、内部技能 stem 和皮肤/技能 owner 上下文应优先放入 `contextual/**/*.yaml`。皮肤回城资源状态已由 `skin_recall_state` profile 统一处理，皮肤待机编号和武器/技能片段由 `skin_idle_state` profile 处理，非标准 shell 下的皮肤动作词尾 `2D/3D` 由 `skin_action_state` profile 处理，DJ Sona 音乐风格过渡由 `skin_audio_transition` profile 处理，Jinx/Jhin 皮肤命名状态由 `skin_named_state` profile 处理，Sion/Corki/Kled 等英雄状态由 `champion_status_state` profile 处理，Map FirstBlood 阶段选择由 `gameplay_phase_control` profile 处理，不再维护为低信任 exact fallback。
