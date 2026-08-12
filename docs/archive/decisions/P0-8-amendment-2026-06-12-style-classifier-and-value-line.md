# P0-8 amendment(2026-06-12):风格分型(短线/价值)+ 三层筛选价值线 + 题材四级赋权

> 状态:**已锁定**(owner 2026-06-12 AskUserQuestion 拍板:"统一硬门+受约束槽位分配")。
> 变更对象:P0-8-amendment-2026-05-24 / P0-8-amendment-2026-06-01 的候选排序条款:在"量化资格权威 + 主题配额 ≤2 + ≥3 量化保留"框架内,新增**确定性风格分型与价值槽内三层得分排序**;THEME SOP schema 扩 `theme_tier` 字段(骨架 frozen 集合只增不改,镜像 Y-001 先例)。
> 不动:资格硬门全量化(排除四件套/流动性/可负担/14-check);LLM 不进运行时数据路径(三层得分全部由确定性 PIT 特征 + 人工 pin 的题材 artifact 算出);纯量化路径始终可跑;无 pin 则价值槽空;硬止损/熔断/仓位三连/14-check **风格不变式**。
> 设计依据:owner 需求 3 + dossier 2026-06-12 §3.3 + codex P0-8/P1-1/P1-2/P1-4/P1-5。

## 1. 决策(锁定)

### 1.1 StyleClassifier(确定性,买入时定型)
- 每个候选/持仓打 `style ∈ {SHORT_TERM, VALUE}`:输入 = 量化因子谱(动量/波动/弹性)+ 三层得分 + thesis 可派生性;确定性同输入同输出,可 replay;落 `PositionThesis.style` + 持仓铭牌(entry_policy_hash/卖出栈版本,与 P2-2-amendment-2026-06-12 §1.3 配套)。
- 标签贯穿 InstructionPlan reasoning 上下文、evidence、前端、飞书消息(display-only,**标签永不改变任何风控数值**)。

### 1.2 三层筛选得分(价值线;全确定性 PIT 特征)
- **底层·大势主线**:pinned THEME artifact 覆盖(候选 ∈ pin 链/板块)+ 板块动量分位 + 既有确定性 regime;
- **中层·资金认可+题材容量**:事件后异常收益(event-study;事件日=pinned EVENT/THEME 节点日期)+ 板块成交额/换手分位 + 自由流通市值容量 + Amihud 非流动性 + 北向/主力资金分位(**仅 PIT-safe 源**);
- **表层·多逻辑共振+高弹性**:KG 逻辑共振计数(**独立 evidence family 计数**,同一 LLM run 重复引用只算 1)+ 基本面支撑(tushare fundamentals **按公告日 PIT**,严禁报告期泄漏)+ 弹性(beta/振幅/自由流通占比)。

### 1.3 题材四级赋权
- THEME SOP 输出 schema 扩 `theme_tier ∈ {1 国家事件/社会大势, 2 政策支持, 3 技术利好, 4 个股利好}`:LLM 给 tier 建议,**人工 pin 时确认 tier**(tier 是 pin artifact 的一部分,不是 runtime LLM 输出);
- 三层得分中题材项 × tier 权重,初始 1.0/0.75/0.5/0.25,config 化 + 列入可进化白名单(**序约束 tier1≥tier2≥tier3≥tier4 为 immutable clamp**)。

### 1.4 受约束槽位分配(owner 拍板,codex P1-1/P1-2)
- 统一量化资格硬门(不变)→ 全体合格候选过 StyleClassifier → **价值槽 ≤2 / 纯量化 ≥3**(沿用现配额语义,不放开)→ 价值槽内按三层得分排序,短线槽按现 5 因子排序;
- 价值槽空缺/无 pin artifact → 纯量化照跑,与现状 **bit-identical**;
- 配额本身(价值槽 0..2)列入可进化白名单,拿实证后由客观晋升调整(P2-2-amendment-2026-06-12)。

### 1.5 风格软层与不变式(codex P0-8,对抗测试钉死)
- style 只影响**软层**:止盈带 / time-stop / 复盘节奏 / 展示横幅;VALUE 风格复用既有 thesis-gated 止盈豁免(其本身不豁免保护性止损);
- 硬保护止损(ATR/drawdown)、熔断、仓位三连、14-check、可卖量、单一构造点 **风格不变式**——对抗测试:任意 style 标签组合下保护性 SELL 输出 bit-identical。

## 2. 实施
Phase AC(AC-001..AC-007)。新因子集进 `backend/screening/` 同构纯模块(import 隔离沿用);event-study/资金流特征若需新数据源,沿用 Tushare SDK-only + PIT 快照纪律(P0-8-amendment-tushare 先例)。
