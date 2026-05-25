# P0-6 修订 — 2026-05-25 双线 MVP go-live tier-aware 门(PILOT/FULL)+ re-scope I-002

> **修订基准**: [P0-6 simulation_auto 验收标准(45 交易日滚动窗口 + 稳定性/策略硬门)](./P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §1(双线)+ §5(安全地基保留)
> **修订日期**: 2026-05-25
> **触发**: 双线 MVP(Phase K+L+M+N)已建成且 `test_mvp_e2e` 证明可组合,但只跑在 fixture 快照 + stub LLM 上,无生产编排。owner 要把双线产品化上线:飞书收 BUY/SELL/ADD → **同花顺模拟盘(¥10 万)手动执行** → 飞书回报 → MockBroker 镜像。旧 **I-002 = 13 标的 45 日 sim**(已被 R0 全市场重定位作废)。owner `AskUserQuestion` 2026-05-25 拍板「**写 amendment 设双线专用 go-live 门**」;经 Codex 2 轮红队,Claude 拍板把它做成 **tier-aware 双层门**(Codex push-back:不得让裸 `can_switch_to_feishu_on()` 在 1 天后冒充 45 日 FULL PASS)。

## 1. 修订前(P0-6 原锁定)

- `simulation_auto` 验收 = **45 交易日滚动窗口** + 5 稳定性硬门(指令完整率 ≥95% / 回报准确率 ≥99% / 数据缺失 ≤1% / LLM 超时 ≤5% / 信号生成 ≥95%)+ 3 策略硬门(最大回撤 ≤8% / 累计 PnL ≥0 / 沪深300 累计超额 ≥0)。
- 切换 `FEISHU_INTERACTIVE_ENABLED=true` **必经** `AcceptanceService.can_switch_to_feishu_on()`(布尔,语义 = 最近 45 日窗口 PASS 且无 reset 在后),**严禁 env/CLI 绕过**(P0-6 §2 红线 5)。
- 现 `can_switch_to_feishu_on()` 返单一布尔;`backend/main.py` 启动门 + `backend/services/mode_router.py` 切换门把它**当作 45 日 FULL PASS**。
- 验收对象 = 旧 **13 标的固定池** 的 simulation_auto 信号(I-002 长跑)。

## 2. 修订后(本 amendment 锁定)

### 2.1 验收对象 re-scope:I-002 = 双线 MVP FULL 窗口

- 旧「I-002 = 13 标的 45 日连续 simulation_auto 长跑」**作废**(R0 已把 universe 重定位为全市场双线)。
- **I-002 重定义** = 「双线(Line-1 全市场选股 + Line-2 持仓监控)在 simulation_auto 下连续 45 交易日滚动窗口达标」。5 稳定性 + 3 策略硬门(§1)**全部保留不变**,只是验收对象换成双线信号。
- I-003(任何未来更高授权)仍 **blocked-on I-002 双线 FULL PASS**;永不把 PILOT 当 FULL(见 §2.4)。

### 2.2 tier-aware 门:`can_switch_to_feishu_on(target_tier)` → `GateDecision`

把单一布尔门升级为**显式分层**(P0-6 §2 红线 5「acceptance gate 是唯一开关 + 严禁 env/CLI 绕过」**不变**,只是门变成 tier-aware):

```
GateDecision(tier: PILOT|FULL, allowed: bool, reasons: tuple[str, ...])
can_switch_to_feishu_on(target_tier: "pilot" | "full") -> GateDecision
```

- **调用方语义锁定**:`backend/main.py` 启动门 + `backend/services/mode_router.py` 切换门**必须显式传 `target_tier`**;FULL 切换只认 `tier=FULL & allowed`,PILOT 切换只认 `tier=PILOT & allowed`。env/CLI 仍**绝不**作为旁路。
- **向后兼容**:无参旧调用等价 `target_tier="full"`(保持「裸布尔 = 45 日 FULL」语义,防止历史调用方被 PILOT 蒙混)。

### 2.3 PILOT 层(双线试点,可在生产编排建成后较早开启)

**动机**:① 账户是**同花顺模拟盘(无真实资金)**;② **owner 对每一条信号人工把关**后才在同花顺手动下单(人是最终 gate)。故「45 日自动安全窗口」可放宽为**有界试点**,但**不删除任何指标**、不破唯一开关红线。

`can_switch_to_feishu_on("pilot")` 返 `allowed=True` **当且仅当下列全部满足(不可约简最小集)**:

1. **SIM 账户断言** —— `active broker == mock`,永禁真实券商。
2. **J-007 owner 授权** —— `QUANTMIND_PROD_RUN=1` + `QUANTMIND_OWNER_PROD_AUTHORIZATION=<id>:YYYYMMDD`(≤7 天,`owner_authorization` 校验通过)。
3. **1 dry-run 交易日 PASS** —— `scripts/dry_run_double_line.py` 在 1 真实交易日 + 样本回放上 render-only 跑通,owner 确认信号合理(产出 dry-run PASS artifact)。
4. **真飞书发/收冒烟通过** —— 1 条真发到决策群 + 1 条回报经 `ExecutionReportParser` 严格正则解析通过。
5. **派发器 outbox 重启幂等** —— 同一 `instruction_id` 跨重启不重复发送(durable outbox 测试通过)。
6. **no-double-execution 不变量** —— 每个 VALIDATED `instruction_id` 互斥单路由(simulation 自动撮合 **XOR** feishu 人工回报镜像),测试通过。
7. **全回报模板 parse/apply** —— 含 `AMBIGUOUS` 绝不 mutate MockBroker。
8. **16:00 对账绿** —— `RECON-{date}` 主动发起 + fail-closed ticket 路径可用。
9. **data-quality pass** —— `DataQualityState` 4 阻断 breach 全清(staleness/divergence/freshness/quote_unavailable)。
10. **LLM 超时/成本门** —— LLM 超时率 ≤5% + `cost_guard` 日 ¥20 hard 预留生效。
11. **回滚路径就绪** —— 可一键回滚到 simulation-only(账户生命周期重置,见 [P1-2.A-amendment-2026-05-25-initial-capital-100k](./P1-2.A-amendment-2026-05-25-initial-capital-100k.md))。

**PILOT 期间**:飞书消息**必须**带 `「模拟盘 · 人工执行 · 试点」` banner(renderer 单源,防误当真实/自动);PILOT pass **显式持久化**为 `tier=PILOT`,审计可辨。

### 2.4 FULL 层(完整毕业,不变)

- `can_switch_to_feishu_on("full")` = **原 P0-6 45 交易日滚动窗口 + 5 稳定性 + 3 策略硬门**(§1),验收对象换为双线(§2.1)。
- **PILOT 期间 FULL 指标后台持续累计**:每日 16:00 acceptance_report 照常生成、45 日窗口照常滚动;PILOT 不暂停也不重置 FULL 累计。
- **永不把 PILOT 当 FULL**:任何「full feishu_on」状态 / I-003 / 未来真实授权**只读 `tier=FULL & allowed`**;`tier=PILOT` 的 pass 对 FULL 判定恒为 False。

### 2.5 安全核心不变(配 R0 §5 + P0-6 原红线)

- acceptance gate 仍是**唯一**切换开关;**严禁** env/CLI 绕过(只是门 tier-aware)。
- LLM 完全不参与验收路径(`acceptance*.py` 禁 import `backend.llm.*`)。
- P0 系统级中断重置 + reconciliation 冻结暂停(P0-6 §7/§8)对 FULL 累计**不变**。
- 永禁真实下单 / 飞书人工执行 / MockBroker 单一镜像 / RiskEngine 纯函数 / 全层 127.0.0.1。

## 3. 实施期任务映射(plan.html Phase U)

- **U-D2** `backend/services/acceptance_report.py` + `mode_router.py`:tier-aware `can_switch_to_feishu_on(target_tier)` → `GateDecision`;PILOT 11 条最小集校验;FULL 复用既有 45 日逻辑;调用方显式传 tier;Feishu pilot banner;回滚 simulation-only。
- **U-D3** `scripts/dry_run_double_line.py`:PILOT 条件 3 的 dry-run PASS artifact。
- **U-D4** 真冒烟(PILOT 条件 4)+ `ExecutionReportApplier` durable 幂等(PILOT 条件 5/6/7)。
- **U-D5** SSoT 记 re-scope I-002。

## 4. 红线清单(本 amendment 之后)

1. acceptance gate = **唯一**切换开关,**严禁 env/CLI 绕过**(P0-6 §2 红线 5 不破);门升级为 tier-aware,调用方必须显式传 `target_tier`。
2. **PILOT ≠ FULL**:PILOT pass 永不满足 FULL 判定;I-003 / 真实授权只认 FULL。
3. FULL = 原 45 交易日 + 5 稳定性 + 3 策略硬门(验收对象 = 双线);PILOT 期间后台持续累计、不暂停不重置。
4. PILOT 11 条不可约简最小集全满足才 `allowed`;任一缺失 → False(fail-closed)。
5. PILOT 飞书消息必带「模拟盘·人工·试点」banner(renderer 单源);PILOT pass 持久化 `tier=PILOT` 可审计。
6. I-002 re-scope = 双线 FULL 窗口(旧 13 标的 sim 作废);LLM 仍不参与验收路径。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG 同步追加(新增 Phase U + 记 I-002 re-scope)。`CLAUDE.md §2.8` 补充「go-live 门 tier-aware(PILOT/FULL);PILOT = 11 条最小集 + J-007 + 飞书 banner;FULL = 45 日双线;acceptance gate 仍唯一开关严禁绕过;I-002 re-scope 双线」。
