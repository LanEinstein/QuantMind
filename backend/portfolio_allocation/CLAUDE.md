# backend/portfolio_allocation/ — 子任务上下文(Phase P)

> 状态:**P-002 done(模块+配置)**;P-003 在 `backend/services/line1_context_provider.py` 接线。治理:[P0-7-amendment-2026-05-30](../../docs/decisions/P0-7-amendment-2026-05-30-portfolio-allocation.md)。任务:plan.html P-002 / P-003。

## 职责
**组合层资金分配**(纯 Python,LLM/RiskEngine **上游**,确定性 + PIT):给定候选 σ + 账户 + 部署封套 → 逆波动率权重(等权兜底)→ 每只**目标现金额**(稳健分批 + incremental 减已持)→ 整手股数。供 Line-1 provider 用 `min(max_compliant_buy_volume(...), 目标手数)` clamp。**配比只压不放**,RiskEngine 14-check 仍独立权威。

## 模块结构(已实现)
| 文件 | 内容 |
|------|------|
| `volatility.py` | `inverse_vol_weights(sigma_by_code)` = `w_i=(1/σ_i)/Σ(1/σ_j)`;σ=None/非有限/≤ε 视为缺失;全缺失=等权 `1/N`;部分缺失=缺失名赋有效 `1/σ` 均值后整体归一。纯 stdlib,σ 取自 frame `closes`(对齐 `screening.volatility_20d`),bit-exact replay。 |
| `allocator.py` | `deployable_cash(...)` = `max(0, min(cash×deploy_fraction, cash−buffer×total))`;`compute_target_cash(...)` = raw→clamp(`min(per_name×total, 15%×total−已持, ¥50k)`)→**一遍残差重分配**;`cash_to_lots(...)` = `floor(¥/(price×lot))×lot`,**返 0 = 今日不买**(调用方当跳过,**不可**强转 1 手破 `volume>0`)。 |
| `policy.py` | `AllocationPolicy` frozen(method/deploy_fraction/per_name_target_pct/cash_buffer_pct/vol_lookback + 从 risk.yaml 读的 single_stock_cap_pct/single_instruction_cap/lot_size)+ 便捷方法(委托上面纯函数,单点 wiring)+ `load_allocation_policy(allocation_yaml, risk_yaml)` 严格校验 + `AllocationPolicyError`。 |

## 本模块红线(P0-7-amendment-2026-05-30 §4)
1. 配比层 = 纯 Python 上游,**严禁** `import backend.{llm,agents,mirofish}`;**不被 `backend/risk/` import**(redline `[P-002]` + `tests/portfolio_allocation/test_module_contract.py` AST + ruff TID251)。
2. 配比**只压不放**:`final volume = min(max_compliant, 配比目标)`;**永不放宽** 15%/70%/¥50k/≤5单。
3. **不构造 `InstructionPlan`**(R0 单一构造点;grep 仍 ⊆ {model, builder, tests})—— 配比只产数值喂 builder。
4. RiskEngine 14-check 仍**独立权威**,配比 pre-respect caps 仅为少被拒,非替代。
5. σ 取自 PIT-pin frame,确定性可复现(同输入同输出);数值**永不来自 LLM**。
6. 掉票(REJECT/DEGRADE/HOLD)预算今日不部署(保守欠配);**不做 mid-walk 动态再分配**(破确定性)。
7. **单源**:单股 15% / 单笔 ¥50k / 整手 100 一律从 `config/risk.yaml` `position_limits` 读,**不在 `allocation_policy.yaml` 重复**。runtime 不可改 + hot-reload 禁;改走 amendment + 重启。
8. per-name ~10% < 15% 硬顶,单日 ~1/3 现金:本配比层在 P0-7 三连之上**更保守**。

## import 隔离
**严禁** `import backend.{llm,agents,mirofish}`(亦不 import backend.{api,broker,risk,data};TID251 全局守门,本模块不在 per-file-ignores 白名单内)。可用:标准库(`math`/`dataclasses`/`pathlib`)+ `yaml` + `structlog`。

## 测试
`tests/portfolio_allocation/`:volatility(逆波动率公式 / 等权兜底 / σ=0/None / 和≈1 / 确定性)+ allocator(clamp / 残差重分配 / incremental / 整手 floor / 买不起返 0 / 对抗任意 σ ≤ caps + Σ ≤ deployable / replay)+ policy(loader happy + 各校验失败 + 单股 cap 来自 risk.yaml + frozen)+ 模块契约(AST 隔离 + `__all__`)。覆盖率 ≥80%。
