# QuantMind 决策文档目录

本目录存放已经定稿的项目决策文档。每份文档对应 `docs/quantmind_owner_decision_points_2026-05-07.md` 中的一个决策点。

## 命名约定

```text
{决策编号}-{决策结果简述}.md
```

**决策编号**取自 `docs/quantmind_owner_decision_points_2026-05-07.md`(`P0-1` / `P0-2` / `P1-3` 等),保留与决策清单的可追溯性。
**决策结果简述**用 hyphen-case 英文短语,直观说明定稿了什么。

例如:

```text
P0-1-positioning-live-confirm-with-sim-auto.md
P0-2-data-sources-akshare-baostock-tushare.md
P0-3-broker-minqmt-via-windows-bridge.md
P0-4-risk-budget-100k-cny.md
```

## 内容要求

每份决策文档至少包含:

1. 元数据表(决策日期、决策点编号、状态、决策人、关联 audit)
2. 决策摘要(一段话能复述清楚)
3. 决策具体内容(可分多节)
4. 红线/边界(立即生效的硬约束)
5. 影响范围(需要改动的代码或配置,留给 implementation 阶段)
6. 决策依据(audit 引用 + 代码事实抽检 + 用户选择记录)
7. 后续动作(checklist)

## 不放进本目录的内容

- 未定稿的调研清单、过程笔记、对比表 → 放在 `docs/` 根目录或单独工作目录
- 阶段总结(Phase X 的执行回顾)→ 放在 `docs/reviews/`
- 仍在讨论中、未拍板的决策候选

## 状态变更原则

决策一旦写入本目录,只允许新增 amendment 文档,不就地修改。如需推翻或调整既有决策,新建:

```text
{决策编号}-amendment-{日期}-{原因}.md
```

并在原决策文档顶部加 `> 已被 amendment-XXX 修订` 提示。
