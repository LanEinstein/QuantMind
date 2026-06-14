# Codex 跨模型代码审查报告 — AE-003 golden replay 地基 + 决策定点化 + lint

**项目**: QuantMind
**任务**: AE-003(P1-0 golden replay 地基 + 决策阈值定点化 + decision_compare/Ref lint)
**审查时间**: 2026-06-14
**审查轮次**: 3 / 3(cycle1 `review --uncommitted` → 修 → cycle2 `exec` → 修 → cycle3 `exec` 复核)
**最终判定**: ✅ 通过(cycle3 verdict = PASS,全部历史问题 RESOLVED,无 P1 回归)

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件 | `backend/utils/decision_compare.py`(新)+ `backend/backtest/`(新 `__init__`/`golden_replay`)+ `backend/slot_portfolio/scoring.py`(迁移)+ `backend/candidate_selector/selector.py`(迁移)+ `scripts/redline-check.sh`(`[BACKTEST]` lint)+ `tests/backtest/`(新) |
| 发现问题总数 | 4(全 P2) |
| 已修复 | 4 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 第 1 轮发现 + 修复

### [P2] 同源比对只比聚合整数 — `golden_replay.py`
`compare_to_golden` 原只比 cash/market_value/total_equity 三个聚合整数:同 MV 的持仓**互换**或 `trade_date`
**错位**会假阳性通过(同源证伪不成立)。**修复**:逐行增比 `trade_date` 身份 + 权威 `code→volume` 持仓状态
(成本基价为派生量不比);新增互换/错位用例。→ **RESOLVED**

## 第 2 轮发现 + 修复(对第 1 轮修复的复核 + 新扫描)

### [P2] 重复持仓码仍可假阳性 — `golden_replay.py`
`{code: volume}` dict 会把重复码折叠(后者覆盖)→ 畸形 recorded 行被规范化掉。**修复**:`ReplayEquityPoint.__post_init__`
拒重复码(ValueError,镜像 `EquityPoint`)+ 比对改用保留重复的 sorted `(code, volume)` 列表。→ **RESOLVED**

### [P2] 畸形成交未拒 — `golden_replay.py`
`_apply_fill` 不校验 `volume>0`/`price_cents>0`/`cost_cents>=0` → 负 BUY 凭空造现金、负 SELL 增持仓。
**修复**:`ReplayFill.__post_init__` 构造期 fail-closed 校验(含 side ∈ {BUY,SELL});`_apply_fill` 去掉未知-side 分支。→ **RESOLVED**

### [P2] import 隔离闸可绕过 — `test_module_contract.py` + `redline-check.sh`
AST 测试只记 `ImportFrom.module` → `from backend import broker` 与相对 `from ..broker` 漏检。**修复**:AST 展开
`from X import y`→`X.y` + 相对导入按 leaf 名守门(全形式正控测试);shell grep 同步扩 from-backend-import + 相对形式。→ **RESOLVED**

## 第 3 轮复核(cycle3)

| 历史问题 | 状态 |
|---|---|
| 重复持仓假阳性 | RESOLVED |
| 畸形成交未拒 | RESOLVED |
| import 闸绕过 | RESOLVED |

**Verdict: PASS** — 无 P1 回归。

## 范围说明(AE-003 vs AE-004)

- AE-003 交付:① golden replay 同源地基(整数分会计,codex 头号"先于一切门")② `decision_compare` 定点工具(NEP 50
  跨版本恒等)③ 把**真正 NEP-50 敏感的 alpha 决策阈值**(`slot_portfolio/scoring.py` 挑战者 margin + 在位弱势 + `selector.py`
  value_gate,共 ~8 处)迁到 `decision_compare`,全量 slot/selector 测试零行为变化 ④ 三 redline lint(`[BACKTEST]` 隔离 +
  qlib `Ref` 前视 + decision_compare bare-float)+ AST 契约。
- **暂缓(AE-004 随双 lane oracle)**:① 双 lane oracle(订单流对账 + golden-vector)② 封闭式不变量(单股≤15%/总仓≤70%)
  ③ 主引擎决策阈值的**全量** decision_compare lint 覆盖(现 lint 范围 = `backend/backtest/`;production 仅迁移了 NEP-50 敏感
  的真决策阈值,bounds-validation 比较〔vs 0.0/1.0 哨兵〕不在范围,避免对正在停机无法验证的实盘行为做大面积语义改动)。

---

> 本报告由 Claude Code(修复)+ Codex CLI(审查)协同生成。
