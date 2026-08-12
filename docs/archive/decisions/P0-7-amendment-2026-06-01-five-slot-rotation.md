# P0-7 修订 — 2026-06-01 ≤5 持仓硬约束 + 卖一买一组合轮动(append-only RotationIntent)(方向③)

> **修订基准**: [P0-7 风险红线](./P0-7-risk-redlines-position-circuit-universe-llm-immutability.md) + [P0-7-amendment-2026-05-24-budget-adaptive-position](./P0-7-amendment-2026-05-24-budget-adaptive-position.md) + [P0-7-amendment-2026-05-30-portfolio-allocation](./P0-7-amendment-2026-05-30-portfolio-allocation.md)
> **关联**: P0-9(long-only + ≤5 单/日 cap + check#6 持仓数)/ R0 §4(单一构造点)/ R0 §3(PIT)/ P0-10-amendment-line2(Line-2 确定性 SELL)/ P0-10-amendment-line2-2026-06-01(thesis-health 作可选在位分)
> **修订日期**: 2026-06-01(规划 session #60)
> **决策人**: owner(AskUserQuestion 2026-06-01:方向③ 答「append-only RotationIntent」;≤5 持仓为 owner 锁定硬约束)
> **性质**: 决策边界锁定;本 session 不写代码,代码在 plan.html Phase V 实施。**codex 判方向③为 ship-first(无 LLM 权限 / 无 KG/thesis 依赖 / PIT 可 replay / 安全包络窄)。**
> **方法**: 3 轮 codex 对抗(round-1 推翻无状态、改 append-only;round-2 §Q2 给出确定性 incumbent-weak 锁定 + churn 控制 + 到期 fallback)。

## 0. 触发与意图

Owner:**每个交易日双线并行**(Line-1 发掘 + Line-2 管持仓),整体收益最大化,不一次买完不管。**硬约束:同时持仓 ≤5 只(ETF 也算)**;满 5 遇更好标的 → **先卖一腾位**(考虑 T+0/T+1)。

现状:**无 ≤5 持仓约束**(仅 check#6 `max_total_positions=10` + check#10 `≤5 单/日`);Line-1 选股 **holdings-blind**(每日全市场新选、不知当前持仓);**T+1**(股票当日买不可当日卖;配比政策已锁「不假设当日回笼」)。

## 1. 决策

### 1.1 ≤5 持仓硬约束(check#6,config + 重启)
`config/risk.yaml` `position_limits.max_total_positions: 10 → 5`。check#6(`_check_total_position_limit`)**已含 ETF 计数 + SELL 跳过 + 加仓已持不增计数**,语义天然契合 ≤5;**无需 15th check**(`risk_summary` min=max=14 schema 常量不破)。这是**硬 guard**(RiskEngine 拒第 6 笔新持仓 BUY),**非轮动**——轮动决策在上游。runtime 不可改 + hot-reload 禁(P0-7 §2 红线 14 沿用)。

### 1.2 轮动决策 = 确定性 `backend/slot_portfolio/` 上游模块 + append-only RotationIntent
- 新纯量化模块 `backend/slot_portfolio/`(import 隔离:禁 `backend.{llm,agents,mirofish}`;**不构造 InstructionPlan** —— R0 §4 单一构造点不破,grep ⊆ {model, builder, tests});上游于 builder;确定性 over PIT-pin frame,可 replay)。
- **append-only `RotationIntent` 记录**(codex round-1 推翻「无状态」:无状态会「丢失卖出理由」—— 隔夜 owner 已卖而挑战者次日失格 = 卖了好持仓没买回、系统不知情)。RotationIntent 显式记:卖出 instruction、incumbent code、challenger code、双方分数、`expires_at`、replay 输入。**注意**:P0-10-amendment-line2-2026-05-31 否决的是 broker_events **脆弱反查**,**不是**小 append-only 显式 intent —— 显式 intent 合规。

### 1.3 双条件轮动(防卖健康持仓 + 防追失格挑战者)
轮动 SELL **须同时满足**(codex round-2 §Q2 锁定;实施期 config 化阈值经 amendment + 重启):
- **在位者「独立够弱」`incumbent_independently_weak`**(全真):① 无保护性止损 active;② 无硬退出 pending;③ 持仓龄 ≥ 最短持有期(如 5 交易日);④ 当前 Line-1 universe 分位 ≤ P40;⑤ 自买入/上次再平衡 rank 恶化 ≥ 20 分位;⑥ ≥1 确认(自身分数低于其 20 日中位 ≥0.75 MAD / Line-2 异动 flag active / 局部高点回撤 ≥ 软阈);⑦ 无确定性否决(停牌 / 跌停不可卖 / 公司行动不安全)。
- **挑战者「以 margin 胜出」**:Line-1 合格且过全部买方硬门;分位 ≥ P75;`挑战者 rank − 在位者 rank ≥ 25 分位`;**预期组合分须以绝对 margin 胜出**(非仅 rank);多挑战者取最高确定性组合分。
- **「独立够弱」是防护核心**:在位者只要不够弱,**绝不**为追挑战者卖掉——根除「卖好持仓追幻影」。

### 1.4 T+1 跨日时序(无同日回笼)
- Line-1 选股从 **holdings-blind 改 holdings-aware**(知当前 5 持仓 + 已结算空槽)。
- T 日:满 5 且挑战者过 margin + 在位者够弱 → 发**轮动 SELL 最弱持仓**建议 + 落 RotationIntent;**当日不买**(配比政策「不假设当日回笼」沿用)。
- T+1 日:槽位**实际**腾出(owner 已卖,从 settled 持仓观察)+ 候选仍合格 → 用 settled cash 买入空槽。**腾出的槽从真实持仓观察,不靠 in-flight 承诺**。

### 1.5 反 churn + 到期 fallback(codex round-2)
- churn 控制:每日 ≤1 次轮动 SELL;同时 ≤1 个 open RotationIntent;轮动 **subcap ≤ 1**(占 ≤5 单/日的 1 个);**轮动让位保护性止损 / 强制退出**(止损/安全退出需 cap 时跳过轮动);同一 incumbent 20 交易日内不再被轮动卖;同 challenger/incumbent 对 30 交易日冷却。
- `RotationIntent.expires_at = min(3 交易日, 下次再平衡收盘)`。
- **到期 fallback**(防「卖了没买回」静默欠配):卖单已成而买未在到期内成 → 确定性兜底:① 试原挑战者(若仍合格);② 否则取当前最佳 ≥P75 合格挑战者;③ 否则留现金 + 标 `UNDERINVESTED_ROTATION_EXPIRED`,**阻断后续轮动**直到下一配比周期 / 人工 gate 解除。

### 1.6 解耦 ship(集成枢纽 = 替换分,但非前置依赖)
轮动**先行 ship**,只用**现有 Line-1 量化分 + 确定性 Line-2 在位健康**(§1.3 全确定性、今日可得),**不依赖**方向①(theme conviction)/②(thesis-health)。后两者**作可选的在位分/挑战分组件**,各自有独立 PIT replay + shadow 验证 + amendment 后再并入(codex:替换分是「带 provenance 标签 + 确定性归一的接口」,不是「①②未完成就不能 ship③」的 mega-dependency)。

## 2. 落地(plan.html Phase V;实施前本 amendment 是门)
`config/risk.yaml` ≤5 + `backend/slot_portfolio/`(确定性轮动决策 + RotationIntent 模型 + 双条件 + churn/到期/fallback)+ Line-1 holdings-aware 改造 + 对抗测试先写(断言不卖健康在位 / 不追失格挑战者 / 轮动让位保护性止损 / RotationIntent 可 replay / 到期 fallback 不静默欠配)。

## 3. 不变量(本 amendment 不触碰)
- P0-7 仓位三连(单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万)+ 熔断五连 + budget-adaptive 分层 + `concentration_exception` **全不变**;逆波动率配比「只压不放」之上更保守不变。
- long-only 永锁 + 6 forbidden_sides + `≤5 单/日 cap`(check#10)不变;轮动耗 cap(1 卖 + 后 1 买),subcap ≤1。
- RiskEngine 14-check 纯函数 IO-free + 独立权威 + `risk_summary` min=max=14 不变;轮动决策上游、不替代 14-check。
- 单一构造点 M-004 + PIT 可复现不变;`slot_portfolio` 不构造 InstructionPlan、确定性 over PIT-pin frame。
- T+1 settled `available_volume` 三层守门 + 「不假设当日回笼」不变。
