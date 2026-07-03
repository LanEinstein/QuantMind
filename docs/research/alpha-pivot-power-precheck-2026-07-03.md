# Alpha-pivot AP-0.5 收益盲 power 前置结果 —— NO-GO(2026-07-03)

> **状态**:AP05-001 交付(施工图 §4 / spec outline §5.0)。**收益盲**(不读任何 A4/panel 收益,不 import backtest/bar_source/panel)。**判据全部预声明并 hash**(`spec=469f8547b62d2452`,AP-0 冻结)。
> **一句话结论**:过 DSR≥0.95(deflation N=2417)所需的年化 Sharpe ≈ **2.67**,而已披露最优边(纯反转 eq_5)年化 Sharpe ≈ **0.31**;`SR_req = 8.6× SR_ref`,**远超 K=2 容差 → NO-GO**。**不烧 promotion trial**;按 owner 决策 #3 预承诺:本刀降纯诊断 + 数据上报 owner。
> **作者**:Claude(Opus 4.8)· owner:dr.zhang

---

## 1. 判据(committed,评测前 hash)

| 输入 | 值 | 来源 |
|---|---|---|
| deflation N | **2417** | AP0-002 账本预登记(base 2387 + 本刀 effective 30) |
| T(有效样本) | **497** | 纯反转 eq_5 rebalance 期数(frontier 披露 rebalance_dates=497) |
| 矩 | normal(skew=0, kurt=3) | `POWER_INPUTS`(codex R2-1) |
| HAC 通胀 | **3.40**(lag=4 结构保守上界) | `hac_conservative_inflation(H=5)`,见 §3 |
| K(power 系数) | **2**(go iff `SR_req ≤ K·SR_ref`) | owner 决策 #2 |
| SR_ref 来源 | 纯反转 eq_5 已披露 DSR 反解 | `slot_frontier_result.json`(零新 peek) |

---

## 2. 结果

| 量 | per-period | 年化(×√(252/5)=7.10) |
|---|---|---|
| SR0 = E[max SR] deflation 基准(N=2417) | 0.2994 | 2.125 |
| **SR_req**(DSR=0.95 所需 period-Sharpe) | **0.3758** | **2.668** |
| **SR_ref**(纯反转 eq_5,反解披露 DSR=0.00585) | **0.0436** | **0.310** |
| K·SR_ref(go 门槛) | — | **0.620** |
| **gap = SR_req / SR_ref** | — | **8.6×** |
| **go** | — | **False → NO-GO** |

**机制**:deflation 基准本身(E[max SR] over 2417 zero-skill 试验的年化 ≈ 2.13)已高过 eq_5 的真实边(0.31)近 7 倍;要以 0.95 置信压过这个基准还需再高一截(SR_req 年化 2.67)。**这不是「门保守」而是「真信号量级差得远」**——与 frontier 结论一致(全容器配置 DSR 0.003–0.006 ≪ 0.95,绑定约束 = 选股 alpha 质量,非容器)。

---

## 3. HAC 保守上界规则(预声明,非样本估计)

对 H=5 天重叠持仓的 DSR 零假设,日频重叠移动窗诱导自相关 `ρ_l = (H−l)/H`;Newey–West(Bartlett)在 lag=H−1=4 的通胀因子(Bartlett 权 `1−l/(lag+1)` 在 lag=4 时恰等于 `(H−l)/H`):

```
inflation = 1 + 2·Σ_{l=1..4} (1−l/5)·(5−l)/5 = 1 + 2·Σ ((5−l)/5)² = 1 + 2·(0.64+0.36+0.16+0.04) = 3.40
```

这是**预声明的结构规则**(不从样本估 HAC,零 peek),对 SR_req 取保守(偏难)方向 —— power 前置的 fail-safe:宁可保守拒,不冒进烧 trial。

---

## 4. 稳健性(判据对假设不敏感)

| 情形 | SR_req 年化 | 判据 |
|---|---|---|
| 主口径(HAC=3.40 保守上界) | 2.668 | NO-GO(8.6×) |
| HAC=1.0(最不保守,IID,= frontier 实际 lag=0 机制) | 1.661 | **仍 NO-GO**(5.4×) |
| 即使 SR_ref 高估到年化 0.5(2·=1.0) | — | 2.668 仍 ≫ 1.0 → NO-GO |

**结论 robust**:即便把 HAC 拉到最松、把 SR_ref 乐观上抬,SR_req 仍数倍于 K·SR_ref。判据不因 HAC/SR_ref 口径而翻转。

---

## 5. 诚实 caveat

- **SR_ref 反解**:eq_5 真实 per-period Sharpe 应由其收益序列直接算;但 frontier artifact `period_returns=null`,且 precheck 须收益盲(不 import backtest)。故用**已披露 DSR=0.00585 在 normal 矩 + eq_5 自身披露结构(lag=0, N=2387, T=496)下反解**近似其边(§2)。frontier 实际 DSR 用真 skew/kurt,反解用 normal 近似 → SR_ref 为近似值。但 §4 证判据对此不敏感。
- **T 与 lag 张力**:T=497(rebalance 非重叠)却叠加 lag=4 保守 HAC = 有意「belt-and-suspenders」保守(施工图 §4 预声明),偏难方向,不利 go —— 与 fail-safe 一致。
- **DSR ≠ Sharpe 倍数**:0.00585→0.95 非线性翻 160 倍;power 前置给出的「所需 period-SR」是比拍脑袋更硬的可达性判断(原则 #1 校准)。

---

## 6. 决策树落点(预承诺,不移球门)

```
AP-0.5 power 前置(收益盲)
└─ SR_req(2.67) > K·SR_ref(0.62) → NO-GO
   → 不烧 promotion trial(不申报四门)
   → 【owner 决策 #3 预承诺】本刀降纯诊断:
       只跑 attribution IC 披露 + 相对纯反转 SPA(AP-1 panel 合并 + IC 披露),不申报四门
     + 整理「≤5 前提可能装不下可过门 alpha」数据证据 → 上报 owner
   → owner 定向:(a) 重审 ≤5 闸门前提 / (b) 认可降纯诊断只做 IC 披露 / (c) 其他
```

**这是 AP05-002 owner gate**:no-go 已触发,`越门推进(直接跑 AP-2 四门)= 违规`。等 owner 明示定向后再动。

**为什么这是有价值的负结果**:power 前置用**收益盲的硬数学**把「绑定约束到底在因子层还是 ≤5 前提本身」钉死给 owner —— 答案:在 ≤5 集中容器 + 2417 累计 mining 债下,任何把年化 Sharpe 从 ~0.3 抬到 ~2.7 的复合都不现实;**继续调容器/加因子救不了 DSR 门**。真仲裁仍是 owner-gated look-once 前向(deflation 可能过度惩罚,但那是 B 层,不在本刀)。

---

## 7. 复现

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
FEISHU_INTERACTIVE_ENABLED=false $PY -m scripts.factor_research.alpha_pivot_power_precheck \
  --out data/factor_research/alpha_pivot_power.json
# SR_req_ann=2.6679  K*SR_ref_ann=0.6196  gap=8.6x  -> NO-GO
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research/test_alpha_pivot_power_precheck.py -q   # 10 passed
```

结果 JSON:`data/factor_research/alpha_pivot_power.json`(全输入 echo + SR0/SR_req/SR_ref/K/gap/go)。
