# AGENTS.md

本仓库维护《英雄联盟》音频事件名的语义数据、规则、生成工具与稳定下游产物。

## 适用范围

- 本文件适用于仓库根目录及全部子目录；更深层的局部 `AGENTS.md` 可增加更具体的约束。
- 每轮代码、配置、测试或数据规则变更前必须读取 `PROJECT_STANDARD.md`。
- 产品边界、技术约束和模块设计分别以 `PRODUCT.md`、`PROJECT_CONSTRAINTS.md`、`PROJECT_DESIGN.md` 为准。

## 不可越过的边界

- 使用 `uv` 管理环境、依赖和命令执行。
- `raw_event_name` 是不可变源数据；可读标签、翻译和审计信息只能作为派生数据保存。
- `rules/`、`overrides/` 和累计 `reference/` 是长期源输入；同一变化能在源输入表达时，不手改生成文件。
- 面向下游的稳定输出应生成到 `dist/`，不把大型临时数据、缓存、游戏资产或一次性导出提交进仓库。
- `scripts/` 只保留薄入口，共享逻辑放在 `src/lol_event_semantics/`。
- 目录、命令或公开数据契约变化时，同步更新 README、目录说明与项目规范。

## 常用门禁

- 安装：`uv sync --dev --locked`
- Lint：`uv run ruff check src scripts tests`
- 类型检查：`uv run mypy`
- 测试：`uv run python -m pytest tests/ -q`

完整测试策略、格式化边界和发布前数据刷新门禁见 `PROJECT_STANDARD.md`。
