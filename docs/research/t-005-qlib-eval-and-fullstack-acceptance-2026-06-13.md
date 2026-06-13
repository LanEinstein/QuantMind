# T-005 — qlib 评估 + 全栈联调 + 程序级验收(决策记录)

> 任务: plan.html T-005(Phase T 收尾)。依据: P0-6(验收框架)+ R0 §7 + 回测调研 dossier(`docs/research/coldstart-knowledge-and-backtest.md`)。**无新 amendment**(完全沿用 P0-6;不引入新红线)。

## 1. qlib 引入评估 → 结论:**非核心,deferred**

T-005 原列「如需引入 qlib 数据/模型工作流」。逐项评估后**不引入 qlib 库进运行时**:

- **现状**:仓内唯一 qlib 触点 = `backend/knowledge_graph/seed/qlib_factors.py` —— 只借用 qlib Alpha158/Alpha360 的**因子名 taxonomy**(种子知识,NOASSERTION 不抄实现),**不依赖 qlib 库**;`pip` 中 qlib **未安装**。
- **不引入的理由**:① 选股因子计算已由 `backend/screening/factors.py` 确定性自实现(PIT 可复现,R0 §3),qlib 的数据/模型工作流与本系统「全市场 Tushare 快照 + 存原始字节 + 离线 replay」的 PIT 红线**重叠且更重**,引入只增依赖不增能力;② 回测差分 oracle 已由 R-002 `backtest_oracle.py`(rqalpha 可选 test-time oracle)覆盖,且 rqalpha 已是「永不入实时路径」的隔离依赖 —— 再叠 qlib 回测栈无新增价值;③ 模型类自进化(因子/策略发现)走 Phase R/AB 的 `ExperimentRegistry` + `ObjectivePromotionEngine` + `anti_overfit`(purged CV / deflated Sharpe),已是更贴合本系统人工 gate 红线的路径。
- **若未来需要**:qlib 作**可选 test-time 工具**(类比 rqalpha,`anomaly-stack` extra 同款 lazy-import + fail-closed),**永不入实时数据/决策路径**,且须先过 R0 §3 PIT 可复现校验 + 单独 amendment。本期不开此路径。

## 2. 双线全栈联调 → 已覆盖

T-001(交易员人格卡)+ T-002(交易员接辩论)+ T-003(全异动栈)已分别端到端测试。T-005 新增**全栈合流 e2e**(`tests/monitoring/test_mvp_e2e.py::test_full_stack_two_line_e2e`),在 N-005 双线 gate 上**同时启用**交易员人格 + 全异动栈,断言:

- Line-1:6 次 LLM 调用(3 分析师 + 2 交易员 + fund_manager),BUY 仍为**确定性 sizing 的 VALIDATED plan**(交易员文本不入 volume —— 单一构造点不破);
- Line-2:全异动栈仍把 SELL 经 `assemble_monitoring_plan` 单一构造点 + 14-check + renderer 输出;
- `test_full_stack_preserves_mvp_gate` 钉死默认路径(无交易员/核心 4 检测器)= MVP gate **bit-identical**(4 次调用)—— 新旋钮严格 additive。

## 3. 程序级验收 → 完全沿用 P0-6,无新增

- 验收引擎 = `backend/services/acceptance_report.py`(P0-6:45 交易日滚动 + 静态 `config/holidays.yaml` + 5 稳定性/3 策略硬门槛);切换 `feishu_interactive` 仍**唯一**经 `AcceptanceService.can_switch_to_feishu_on()`(`backend/api/acceptance.py`),**严禁 env/CLI 绕过**(env 只选评估哪个 tier 的 gate,从不绕过 `allowed` 裁决,P0-6-amendment-2026-05-25 §4)。该 gate 行为由 `tests/test_acceptance_report.py` + `tests/test_api_acceptance.py` + `tests/test_phase_i_001_orchestration.py` 既有覆盖。
- **本期 T-001..T-004 全栈未新增任何写端点 / 未触碰验收路径 / 未引入 env 绕过** —— 全栈跑通不削弱 P0-6 gate。

## 4. 推迟到整体测试期(owner『先完整开发后整体测试』)

- **45 交易日滚动验收 RUN**(真跑系统积累 45 日数据 → 5+3 硬门槛裁决)属**整体测试**阶段,与 I-002(45 交易日滚动验收窗口)/ I-003(feishu_interactive 启用 gate)合并,owner 在 O+T 开发完成后集中跑。
- T-001..T-004 各自的**运行期激活**(交易员人格 boot 加载已 fail-closed 生效;全异动栈 env 门控 OFF 待 owner shadow 后启用;exemplars 进化待离线 GEPA + 人工 pin)随 owner 重启 / 启用流程逐项落地。

## 5. 结论

Phase T 五任务(T-001..T-005)**开发完成**;qlib 评估后定为非核心 deferred;全栈联调 e2e 绿;程序级验收完全沿用 P0-6 无新增。**仅剩『整体测试』阶段**(R-003 / S-004 / I-002 / I-003 / U-E5)需跑系统,按 owner 序推迟。
