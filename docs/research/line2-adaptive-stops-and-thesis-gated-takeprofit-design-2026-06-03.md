# Line-2 止盈/止损自适应化 + 长期持股豁免强制止盈 — 设计草案(2026-06-03)

> **状态**: DRAFT — 待 owner 审批后才落 amendment + 写代码(amendment-first,本文不含代码改动)
> **owner 指令(2026-06-03)**: ①「除了有必要长期持股的股票之外,要做到及时止盈落袋为安,利用复利赚钱」②「止盈点/止损点要根据历史情况以及综合场外信息对未来的推测综合制定,而不能使用写死的数字」
> **owner 选定方向(AskUserQuestion)**: **A — 红线内自适应 + 接长期持股豁免**(确定性可复现,LLM 不碰决策;场外信息只做 advisory + 人工 gate)
> **治理基准**: R0 §1 Line-2 + §8 + `P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction` + `P0-10-amendment-line2-2026-05-30`(take-profit/trim P-005)+ `P0-10-amendment-line2-2026-06-01`(thesis;W)+ §2.12 自进化(P2-2)

---

## 0. 一句话结论

止盈止损**不是写死价格**——ATR、近期高点、R 单位本就是**按每只股的日线历史实时算的**;真正"写死"的只是几个**系数**。本设计在**不破任何安全地基红线**前提下做两件事:**D2**(先做,小)给长期持股(有 intact thesis)**豁免强制止盈/超配减仓**,让利润奔跑;**D1**(后做,大)把那几个系数从常量改成**按历史波动 / 市场 regime 确定性派生**的 pinned 工件,离线校准 + 45 日 shadow + 人工 gate。**场外信息(新闻/LLM 对未来的推测)只做 advisory,永不自动写决策阈值**——这是 owner 选 A 的核心约束。

---

## 1. 现状:哪些已自适应,哪些是写死的系数

止盈/止损在 `backend/monitoring/intraday_triggers.py`(30s 盘中确定性 runner,**已 live、非 env 门控**,P-005/006/007 全 done)+ `backend/monitoring/add_position.py`(补仓)。

| 触发 | 计算 | 已"按历史自适应"的部分 | 写死的系数(本设计的目标) |
|------|------|----------------------|--------------------------|
| `ATR_TRAILING_STOP` | 现价 < `近20日高 − k×ATR(14)` | ✅ 近期高点 + ATR **按个股日线历史算**,逐 tick | `atr_stop_mult k=2.0` / `recent_high_window=20` / `atr_window=14` |
| `TAKE_PROFIT` (+1R 分批) | 现价 ≥ `成本 + r×R`,R=`k×ATR` → 卖 `tranche` | ✅ R = ATR 派生(波动越大、止盈目标越远),**已是相对量** | `r_multiple=1.0` / `tranche_fraction=0.5` |
| `DRAWDOWN_STOP` | 日内 `(现价−昨收)/昨收 ≤ −5%` | ❌ 纯固定 5% | `drawdown_threshold=0.05`(**最该自适应**:对高波动股太紧、低波动股太松) |
| `WEIGHT_TRIM` | 权重 > `15%×1.10` → 修回 13% | — | `trim_band=0.10` / `trim_target_pct=0.13` |
| 补仓 `MARTINGALE` 拒 | 相对成本回撤 > `10%` → 拒 | ❌ 固定 10% | `max_add_drawdown_pct=0.10` |
| 补仓 `STRUCTURAL_BREAKDOWN` | 价 < `长MA×(1−10%)` → 拒 | ✅ 长 MA 按历史算 | `breakdown_tolerance=0.10` |
| 补仓 `NOT_OVERSOLD` | Wilder RSI < 35(日线路径) | ✅ RSI 按历史算 | `rsi_oversold=35.0` |

**结论**:止损/止盈级别**已经是个股波动的函数**(ATR/MA/RSI/近期高点),只是**系数是常量**。D1 = 把系数也变成历史/regime 的确定性函数。

> ⚠️ **600011 没补仓**(owner 第 2 问)的根因正在此表:补仓的 `MARTINGALE`(回撤>10%)/ `STRUCTURAL_BREAKDOWN`(跌破长MA>10%)/ `BEAR_REGIME` 任一命中即拒。"下探到很低"= 命中拒补,**按设计不接飞刀**,非 bug。

---

## 2. 必须守住的安全地基红线(本设计的硬约束)

owner 选 A = 在这些红线**内**做,一条不破:

1. **LLM 不写决策字段**(§2.2)—— 止盈止损阈值**绝不**来自 LLM/新闻;LLM 仅 4 类 advisory 文本。
2. **InstructionPlan 单一构造点**(R0 §4)—— side/volume/limit_price 确定性派生;本设计只改"派生用的系数来源",仍在 builder 单一构造点内。
3. **RiskEngine 纯函数 + monitoring 零 LLM 决策路径**(§2.2 / N-005)—— 校准层 import-clean(禁 `backend.{llm,agents,agents_team,mirofish}`)。
4. **config runtime 不可改 + PIT 可复现**(§2.4 / R0 §3)—— 校准出的系数仍是 **pinned 工件 + 重启生效**;`FEATURE_CODE_VERSION` bump 让陈旧 replay fail-closed。
5. **自进化 7 禁 + 人工 gate**(§2.12)—— 系数的"学习/优化"走 P2-2 离线(DSPy/GEPA 或纯统计校准)+ 45 日 shadow + 飞书人工 gate + git + restart,**永不**运行时自动 mutate config、**永不** LLM 调参。
6. **只增卖压不放松止损 的精神**(W-004)—— D2 的豁免**只豁免可裁量的止盈/超配减仓,绝不豁免保护性止损**(ATR/drawdown/thesis-break 照常触发)。

---

## 3. D2(先做,小而高价值):长期持股豁免强制止盈/超配减仓

### 3.1 目标
有**长期持有 thesis 且 thesis 未破**的持仓 → 豁免 `TAKE_PROFIT` + `WEIGHT_TRIM`(可裁量的获利了结/再平衡),**让利润奔跑**;但**保护性止损照常**(`ATR_TRAILING_STOP` / `DRAWDOWN_STOP`)+ thesis 破了照常 `THESIS_QUANT_BREAK` 全退。非长期持仓(无 thesis 或 thesis 已破)→ 止盈照常触发(落袋 + 复利)。

### 3.2 机制(确定性,零 LLM)
runner 已有 `theses_by_code`(有 PositionThesis 的码)+ `thesis_break_by_code`(已破的码)。派生:

```
long_term_hold_codes = frozenset(theses_by_code) − frozenset(thesis_break_by_code)
                       # 有 thesis 且 intact = 长期 conviction 持仓
```

在 `evaluate_intraday_sell_intents` 的 **else 分支**(无保护性止损、无 thesis-break 时才评估 TP/trim)加一道门:`code in long_term_hold_codes` → **跳过 TP + trim**。因 TP/trim 本就排在保护性止损**之后**,豁免天然碰不到止损(红线 6 自动满足)。

### 3.3 精确改动
- `intraday_triggers.py::evaluate_intraday_sell_intents` 新增参数 `long_term_hold_codes: frozenset[str] = frozenset()`;else 分支内 `account is not None and code not in long_term_hold_codes` 才评估 TP/trim。空集 = 与现状 bit-for-bit 相同(纯加性)。
- `line2_intraday_runner.py` 派生 `long_term_hold_codes`(intact thesis 集)并传入;**env 门控** `QUANTMIND_LINE2_THESIS_TAKEPROFIT_EXEMPT_ENABLED`(默认 **OFF**,仿 W-004 `THESIS_QUANT_BREAK`),OFF → 传空集 → 行为不变。
- `FEATURE_CODE_VERSION` → v4(maths 改了:豁免影响输出)。
- 新测试:intact-thesis 码不出 TP/trim;broken-thesis 码 TP 照常;无 thesis 码 TP 照常;**止损不受豁免影响**(intact-thesis 码仍出 ATR/drawdown SELL);空集 = v3 输出不变。

### 3.4 红线分析(D2)
- 确定性(intact thesis = 数据事实,thesis 阈值确定性派生);零 LLM 进决策。✅ 红线 1/3
- 只移除**可裁量卖压**,绝不放松保护性止损 / thesis-break。✅ 红线 6
- env 默认 OFF + 45 日 shadow + owner gate 才启。✅ 红线 5
- 不构造 InstructionPlan、不改 builder 单一构造点。✅ 红线 2
- **amendment**: `P0-10-amendment-line2-2026-06-03-thesis-gated-takeprofit-exemption`(扩 P0-10-amendment-line2-2026-05-30 take-profit 语义:长期持股豁免)。

---

## 4. D1(后做,大):止盈/止损系数 = 历史 / regime 确定性派生

### 4.1 目标
把 §1 表里"写死的系数"(尤其 `drawdown_threshold` 5%、`atr_stop_mult` 2.0、`r_multiple` 1.0、`max_add_drawdown_pct` 10%)改成**按历史波动 / 市场 regime 确定性派生**的 pinned 工件——满足 owner「不写死,据历史定」。

### 4.2 设计:确定性校准层(仿 L-005 `calibration.py`)
新 `backend/monitoring/intraday_calibration.py`(纯量化、零 LLM、import-clean):
- **(i) 个股波动缩放**:`drawdown_threshold` 由固定 5% → `k × 个股近 N 日|日收益|的分位`(高波动股更宽的日内止损,低波动股更紧);`atr_stop_mult`/`r_multiple` 已是 ATR 相对量(已自适应),可进一步按波动分位微调。
- **(ii) regime 条件化**:复用已有 `classify_regime`(BEAR/…,benchmark 指数历史的确定性函数)→ 每 regime 一套校准 config(如熊/高波动 regime:更紧止盈 + 更宽止损;平稳上行:更松止盈让利润奔跑)。
- 输出 = 每 regime / 每波动桶 的 `IntradayTriggerConfig`,存 **pinned 工件**(`config/intraday_trigger_calibration.yaml` + loader,runtime-immutable,仿 `risk.yaml`/L-005);trigger maths 仍是 pinned config + PIT 快照的纯函数。

### 4.3 "场外信息对未来的推测" 如何进来(owner A 的核心约束)
**不直接写阈值**。三条合规通道:
1. **regime 分类**(确定性,纯市场数据)——"对未来的推测"经 regime 进入(熊/牛/震荡 → 不同系数)。
2. **owner 人工判断**——看 advisory(thesis 文本 + Phase Y 主题研究 + 新闻 evidence)后,在飞书**人工 gate** 决定是否执行;场外信息经"人"进场,不经自动阈值。
3. **PositionThesis 文本**(W)——LLM 支柱文本塑造"该不该长期持有"(→ D2 豁免),但**失效阈值确定性派生**(W 已对抗测试钉死:改文本→阈值 bit-exact 不变)。
> ⛔ **明确不做**:让新闻/LLM 的数值预测直接改 `drawdown_threshold`/`atr_stop_mult` 等 —— 那是 LLM 写决策字段,破红线 1/2/3。如果 owner 将来想要这条,必须单独评估"破地基红线的代价 + 重大 amendment"(对应 AskUserQuestion 的选项 B,本设计不含)。

### 4.4 系数怎么"学/更新"(红线内)
P2-2 离线路径:纯统计校准(分位/regime,无 LLM)或 DSPy/GEPA(若涉及 prompt)→ **45 日 shadow 沿用 P0-6** → 飞书人工通知 + gate → git commit 新 pinned 工件 → 重启生效。**永不**运行时自动改、**永不** LLM 运行时调参(§2.12 7 禁)。

### 4.5 红线分析(D1)
- 校准出的系数仍 **pinned + 重启生效**(config 不可改不破)✅ 红线 4;离线派生 + PIT 可复现 ✅ 红线 4;校准层 import-clean 零 LLM ✅ 红线 3;recalibration 走 P2-2 shadow + 人工 gate ✅ 红线 5;`FEATURE_CODE_VERSION` bump fail-closed ✅。
- **amendment**: `P0-7-amendment-2026-06-XX-adaptive-intraday-thresholds`(把"locked runtime-immutable 常量阈值"→"历史/regime 确定性派生的 pinned 工件,离线校准 + shadow + 人工 gate";阈值上下界 clamp 防校准越界;14-check 不变)。

---

## 5. amendment 草案(2 份,待审批后落正式文件)

### 5.1 `P0-10-amendment-line2-2026-06-03-thesis-gated-takeprofit-exemption`(D2)
- **修订基准**: P0-10-amendment-line2-2026-05-30(take-profit/trim)+ -2026-06-01(thesis)。
- **决策**: 有 intact PositionThesis 的持仓豁免 `TAKE_PROFIT`+`WEIGHT_TRIM`;保护性止损(ATR/drawdown)+ `THESIS_QUANT_BREAK` 不豁免。env 默认 OFF + shadow + 人工 gate。
- **红线保留**: 零 LLM 决策 / 不放松止损 / 单一构造点 / PIT / monitoring import 隔离 / 纯加性(空集=现状)。
- **变更**: `evaluate_intraday_sell_intents` 新增 `long_term_hold_codes` 门;`FEATURE_CODE_VERSION` v3→v4。

### 5.2 `P0-7-amendment-2026-06-03-adaptive-intraday-thresholds`(D1)
- **修订基准**: P0-7(风控仓位)+ P0-10-amendment-line2-2026-05-30。
- **决策**: §1 表中系数从常量 → 历史波动分位 / regime 确定性派生的 pinned 工件;离线校准 + 45 日 shadow + 飞书人工 gate + git + restart;场外信息只 advisory(regime + 人工 gate + thesis 文本),严禁 LLM/新闻写数值阈值;校准结果上下界 clamp。
- **红线保留**: config 不可改(pinned 工件+重启)/ PIT 可复现 / RiskEngine 14-check 不变 / 零 LLM / 自进化 7 禁 + 人工 gate / `FEATURE_CODE_VERSION` fail-closed。
- **变更**: 新 `intraday_calibration.py` + pinned 工件 + loader;`evaluate_intraday_*` 接 per-regime/per-bucket config。

---

## 6. 分期 rollout + 验证

| 期 | 内容 | 门控 | 验证 |
|----|------|------|------|
| **D2** | 长期持股豁免止盈(小,~1 文件改 + runner 接线 + 测试) | env 默认 OFF | TDD(豁免/止损不受影响/空集=现状)+ codex + 全量绿 + redline;启用前 45 日 shadow 看豁免对收益曲线影响 |
| **D1-a** | `drawdown_threshold` 个股波动分位自适应(单系数试点) | env 默认 OFF + pinned 工件 | 校准可复现测试 + shadow 对比固定 5% |
| **D1-b** | regime 条件化全系数 + 校准层 | env + pinned + 人工 gate | shadow 45 日 + 校准 artifact pin + replay bit-exact |

每期独立 amendment-first + 一任务一 feature commit + codex P0/P1/P2 修完 + 本地未 push 待 owner 授权。

---

## 7. 待 owner 拍板的参数决策点(落代码前需你定)

1. **D2 豁免范围**:只豁免 `TAKE_PROFIT`,还是连 `WEIGHT_TRIM`(超配减仓)一起豁免?(建议:两者都豁免,但 `WEIGHT_TRIM` 保留一个**硬上限**——长期持股权重也不该无限膨胀突破单股 15% 红线;即"豁免软性 16.5% 再平衡,但 15% 硬顶仍裁";需你确认)
2. **D2"长期持股"定义**:= 有 intact PositionThesis(本设计默认),还是要 owner 显式 pin 一个"长持白名单"?(thesis 是确定性且已有,推荐;白名单更重)
3. **D1 自适应优先系数**:先做哪个?(建议 `drawdown_threshold` 先试点——最该自适应、改动最小、最易 shadow 对比)
4. **D1 regime 粒度**:沿用现有 `classify_regime`(BEAR/非BEAR 二分)够不够,还是要细分(牛/震荡/熊 + 高/低波动)?
5. **复利再投**:止盈落袋后的现金,是回到 Phase V ≤5 槽轮动池等下次入场,还是需要专门的"止盈现金再投"语义?(关系到"利用复利赚钱"如何闭环)
