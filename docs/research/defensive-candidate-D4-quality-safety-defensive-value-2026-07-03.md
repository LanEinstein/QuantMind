# 防御候选 D4 —— 质量安全防御价值(F-score 门)

> **状态**:provenance-gated committed **设计**,评测前存档。防御选股线候选 4/4,测试顺序 D1→D2→D3→**D4**(owner 2026-07-03)。上游 = `defensive-selection-research-synthesis-2026-07-03.md`。
> **判据(owner)**:熊市/股灾不亏 + 回撤可控。置信:**MED**(A 股价值弱独立,靠 F-score 门支撑)。

---

## 1. 假设(可证伪)

**H1(D4)**:**便宜(低 PE/PB/高股息)∧ 高质量/高 F-score** 的名(防御价值,绝不 value alone),能避开「便宜但基本面恶化」的价值陷阱(经典回撤源),在熊市/股灾里因估值地板+质量安全而少亏。**H0**:防御价值扣债后不控回撤(A 股价值弱,F-score 门救不了)。

## 2. Committed 先验(评测前 hash 定死)

**便宜腿(committed 符号)**:

| 因子 | 先验符号 | 状态 |
|---|---|---|
| `pe_ttm` | − 低better(便宜) | ✅ `daily_basic` |
| `pb` | − 低better | ✅ `daily_basic` |
| `dv_ratio`(股息率) | + 高better | port `valuation.py` |
| `ep_ttm`(E/P) | + 高better | ✅ factor_lib |

**质量安全门(committed,gate,非 tilt)= Piotroski F-score 精神**:
- 盈利:ROE>0(`roe`)、经营现金流>0、GPM 改善(`gpm`);
- 安全:低杠杆(资产负债率不升)、低应计(`accr`)、无增发稀释;
- **门 = F-score ≥ 阈(如 ≥6/9);低 F-score 的便宜名(价值陷阱)剔除**。F-score 精确 9 点需**新代码**(`*_vip` 财报派生);一期可用 {roe>0 ∧ accr 非顶 ∧ gpm 非底 ∧ 资产负债率非顶} 4 点近似,披露。

**权重**:便宜腿 z-blend(pe/pb/dv/ep 等权)× F-score 门(二值 gate)。

**排除**:价值陷阱(低 F-score)+ 高 MAX 彩票 + 排除四件套 + 涨跌停不可成交。

## 3. Horizon 与引擎配置

- **horizon = 20d(月级)**,低换手(价值/质量是慢因子)。
- 引擎 = `run_gate_backtest(horizon=20)`;≤5 槽 + buffer 容器。

## 4. 数据 + 复用(今天可建,财报门需新代码)

`daily_basic`(pe/pb/dv)+ `ep_ttm`(已有)+ `*_vip` 财报(roe/gpm/accr/杠杆 via `fundamentals_pit.py`/`statements_pit.py`)。**新代码 = F-score 派生**(9 点或 4 点近似)。中性化删30%。

## 5. 机制(为何控回撤)

F-score 门滤掉基本面恶化的「便宜」陷阱名(那些越跌越便宜、持续下跌的价值陷阱=经典回撤源),只留改善的资产负债表 + 估值地板(低 PE/PB/高息)+ 质量安全 → 熊市/股灾里估值地板+质量支撑少亏。

## 6. Dev 测试协议(train_val only,不碰近期)

按 synthesis §4。regime 熊市累计 + 6 股灾切片 + MDD;**关键对照 = 纯便宜(value alone,无 F-score 门)vs D4(F-score 门)** → 隔离 F-score 门对价值陷阱回撤的作用。size+行业中性化删30%;`deflated_sharpe_hac` 对非清零账本(D4 append kind=ablation);对 size-matched random placebo 消融。

## 7. 诚实预期 + caveat

- 预期:F-score 门改善价值的回撤;但 A 股价值弱独立(Hanauer 2021)→ 净盈可能不高、5 日/20 日 IC 弱;D4 更可能是 D1 红利腿的补充而非独立赢家。
- caveat:一期用 4 点近似 F-score(非完整 9 点)→ 披露;价值/质量 A 股弱 → 仅作门/子分不作主权重(synthesis §5)。
- **FAIL 报 FAIL**。

## 8. 判据落点(owner 判)

dev 表出后:D4 熊市/股灾不崩 且 MDD 可控 且 净盈>0 且 F-score 门 vs 纯便宜显著改善回撤 且胜 placebo → 晋级;否则 FAIL 报 FAIL。**D1-D4 全 FAIL** → 上报 owner(绑定约束在选股 alpha 本身,重审方向)。
