# P1-7 二次修订 — 2026-05-26 Line-1 多候选辩论 + 日 LLM hard ¥20 → ¥100

> **修订基准**: [P1-7 成本预算 LLM only 日 ¥20 / 月 ¥440 / Kimi ¥4 + soft degrade](./P1-7-cost-budget-llm-only-monthly-440-daily-20-kimi-cap-4-soft-degrade-feishu-alert.md)
> **一次修订**: [P1-7-amendment-2026-05-24 事后 trailing-stop → 真·预留 + fan-out cap](./P1-7-amendment-2026-05-24-precall-reservation-fanout-cap.md)(本 amendment **只推翻其 §2.2 / §4 第 2 条的"一次辩论/每日 shortlist 非 per candidate"**,真·预留 / 统一计数器 / retry cap / import 隔离全留)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 6 项 + §8 预算真相
> **修订日期**: 2026-05-26
> **触发(owner 有意推翻 2 条,仅这 2 条,安全地基全留)**:
> 1. #43/#44 真 dry-run(真 Tushare + 真 qwen)暴露:Line-1 每日**只辩论 top-1 lead**(`line1_runner.py:246 lead_code = selection.shortlist[0]`),lead 被 RiskEngine 正确拒单(2026-05-25 `600584` 当日涨停 → `limit_up_down_block`)即**全天 0 BUY** → dry-run 必 FAIL,系统选不出任何优质股。owner 定位:"被拒后应辩论 shortlist 下一只",而非绕过拒单。
> 2. 日 ¥20 hard 对**多候选**辩论(每候选 1 次 4-agent ≈ ¥0.08~¥0.4)偏紧;owner 上调至 **¥100/日**(仍是 hard 上限,不是无 ceiling)。

---

## 0. 安全红线**不动**(本 amendment 绝不触碰)

永禁真实下单 / 飞书人工执行 / 全层 127.0.0.1 / LLM 不写决策字段(positive list 4 类不变)/ RiskEngine 纯函数 14-check 权威 / **InstructionPlan 单一构造点(M-004,仅 `instruction_plan_builder` 可构造)** / 人工 gate。
**RiskEngine 红线全留** —— 禁涨停 BUY / 禁跌停 SELL / long-only / 仓位三连(单股≤15% / 总仓≤70% / 单次≤5万)/ 熔断(≤5单/日 + 日亏-5% + 连亏3笔 + 60min冷却)。
> 重申:重跑时 lead 涨停被拒 = RiskEngine **正确履职**。本 amendment 要的是"被拒后辩论 shortlist 下一只",**绝不**是绕过拒单 / 改 harness / 改 prompt 凑 PASS。

---

## 1. 推翻条款(精确范围)

**仅推翻** P1-7-amendment-2026-05-24 §2.2 + §4 第 2 条里的这句:

> ~~「**一次辩论 / 每日 shortlist,不是 per candidate**」——辩论在收敛后的 shortlist 上**跑一次**,不是每个候选跑一次。~~

理由:该红线最初是为防"全市场 fan-out ¥0.8 × 20 候选 = ¥16 瞬间吃满 ¥20"。但它把 Line-1 钉死成"只辩 top-1",导致 top-1 被 RiskEngine 拒就全天空转。**真正的 fan-out 防护改由两层硬 bound 承担**(见 §2.3),不再需要"只辩一只"这条过严约束。

**保留不动**(同 amendment 其余条款):真·预留(preflight 估算 + 拒超 + 对账,跨 hard cap 的调用不发生)/ 所有 LLM 花费写同一 `llm:usage:{utc_date}` 计数器 / per-stage 0 重试 + budget-aware fallback / `cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}` / 常量 runtime 不可改 + hot-reload 禁用 / 告警仅飞书。

---

## 2. 新锁定条款

### 2.1 Line-1 按 shortlist 顺序多候选辩论(推翻"只辩一只")

- Line-1 每日 09:35 cron 按 `CandidateSelector` 产出的 `selection.shortlist` **顺序**遍历:对每只候选
  `build_lead_context → run_shortlist(单元素辩论)→ to_fund_manager_output → assemble_plan(14-check 单一构造点)→ RouteCoordinator`。
- **RiskEngine REJECTED(或 HOLD / DEGRADED / 非-BUY discard / 早返)→ 继续下一只候选**;**VALIDATED BUY → 收集/返回**(单只 vs 篮子见 §2.2)。
- **单一构造点不破**:每只候选的 plan 仍只经 `instruction_plan_builder.assemble_plan` 确定性派生 side/volume/limit_price,LLM 永不写决策字段;`grep "InstructionPlan(" ⊆ {model, builder, tests}` 不变。
- **遍历自然终止于** §2.3 的双重 bound(`max_debates_per_day` 或日 ¥100 预留拒超)——任一耗尽即 fail-closed 停止遍历,**优雅收尾返回已收集结果(可能 0 BUY),绝不崩**。

### 2.2 终止/收集语义 = 单只 vs 篮子(**owner 拍板,§4 决策点**)

本 amendment 支持两种模式,经 runner 参数 / 模块默认常量选择(runtime 不可改,改默认进 amendment):

- **单只(single)**:遇到第 1 个 VALIDATED BUY 即**立即返回**,不再辩论后续候选(省预算,贴近"每日 1 个最优买点")。
- **篮子(basket)**:**继续遍历**收集 shortlist 中**所有** VALIDATED BUY,直到 shortlist 耗尽 / `max_debates_per_day` 耗尽 / 日 ¥100 预留拒超;每只 BUY 各走单一构造点、各自路由(贴近"选出一篮子优质股")。篮子受 **P0-7 熔断 ≤5 单/日 + RiskEngine check-10 `today_instruction_count` 上限**自然 bound(第 6 单起 RiskEngine 直接拒,篮子上限 ≈ 当日剩余 instruction 名额)。

> **owner 决策(2026-05-26 AskUserQuestion):basket**(见 §4)。**默认 = basket**;single 仍由 runner 参数 / 常量可选(测试覆盖两模式)。

### 2.3 fan-out 防护改由两层硬 bound 承担(替代"只辩一只")

1. **`max_debates_per_day`**(`cost_guard.py:78` `_DEFAULT_MAX_DEBATES_PER_DAY`,默认 8;env `QUANTMIND_MAX_DEBATES_PER_DAY` 可调,改默认进 amendment)——`run_shortlist` 每次调用 claim 一个 debate slot(`graph.py:224`),多候选消耗多个 slot。**遍历最多辩论 `max_debates_per_day` 只**;超出即 `DailyBudgetExceededError` → 遍历 fail-closed 停止。
   - **本 amendment 评估**:篮子模式下 shortlist 可能 > 8;`CandidateSelector` top-N 默认产出 shortlist 通常 ≤ 8,且 RiskEngine check-10(≤5 单/日)在 5 个 VALIDATED BUY 后就拒,篮子实际上限 ≈ 5,**默认 8 足够**(留 headroom)。若 owner 选篮子且实测 shortlist 频繁 > 8 被截断,经本 amendment 升 `_DEFAULT_MAX_DEBATES_PER_DAY` 或设 `QUANTMIND_MAX_DEBATES_PER_DAY`(env 受本 amendment 祝福为 boot 覆盖路径)。
2. **日 ¥100 真·预留**(§2.4)——每次 `run_shortlist` 先 `reserve_budget(estimated)`,`reserved + spent > ¥100` 即拒该调用、不发生 → 遍历 fail-closed 停止。单次 4-agent 辩论实测 ¥0.08~0.4,¥100 足够 8 次辩论 + 余量。

> 两层 bound 各自独立、都 fail-closed:遍历在任一 bound 耗尽时优雅停止并返回已收集结果。即使 shortlist 极长,最坏情形 = `min(max_debates_per_day, ⌊¥100预留余额 / 单次辩论估价⌋)` 次辩论,远低于失控 fan-out。

### 2.4 日 LLM hard ¥20 → ¥100(唯一全 LLM 熔断阈值上调)

- `cost_guard.py:56` `_DEFAULT_DAILY_BUDGET_RMB = 20.0` → **`100.0`**(env `QUANTMIND_DAILY_BUDGET` 覆盖路径不变)。
- 这是**唯一全 LLM 熔断触发器**(CLAUDE.md §2.2 / §2.10),真·预留语义不变:跨越 ¥100 的调用**不发生**。
- soft ceiling `_DEFAULT_SOFT_CEIL_PCT = 0.7` **数值不变**(它是**比例**,自动随 ¥20→¥100 变成 ¥70 触发"关 Kimi escalation",无需改常量)。
- **`scripts/redline-check.sh` 的 [M-005] 子检**断言 4 常量值;`_DEFAULT_DAILY_BUDGET_RMB = 20.0` 这一项必须同步改成 `= 100.0`(其余 3 项不变),否则本地门禁红。本 amendment 是该断言变更的授权依据。

### 2.5 Kimi 子上限 ¥4 —— **本 session 评估:维持 ¥4 不变**(给 owner 建议)

- **建议:不上调,保持 `_DEFAULT_KIMI_DAILY_CAP_RMB = 4.0`**。理由:
  1. Kimi(2.1/8.4 RMB/M)是 **escalation-only 降级路径**,MVP 主辩论用 qwen3.7-max(fund_manager)+ qwen3.6-plus(其余 3 agent,免费额度),**不依赖 Kimi**;Kimi cap 是独立护栏,非按日总额比例绑定。
  2. ¥4 cap 只暂停 Kimi escalation,**从不**暂停 deepseek/qwen(护栏不影响主链可用性)。
  3. 日总额已升 ¥100 兜底总花费;再放宽 Kimi 子上限是**削弱护栏却无 MVP 收益**。
- 若 owner 日后启用以 Kimi 为主的深推理路径,再经 amendment 单独评估上调(届时按"escalation 实际占比"定,而非机械按 20%)。

### 2.6 月 ¥440 soft —— **不变(owner 明确保留)**

- `_DEFAULT_MONTHLY_BUDGET_RMB = 440.0` 不变;50/80/100% 三节点不变;**100% 仍不停 LLM**(soft,仅告警)。
- **观察(非红线变更)**:日 ¥100 下,单个重日即可越过月 ¥440 → 50/80/100% 里程碑可能更早/更频触发。**可接受**:月预算本就 soft、从不熔断,里程碑退化为"提示性"通知,符合 owner"月 ¥440 soft 不变"指令。若 owner 日后嫌告警噪音,再经 amendment 调月值或里程碑。

---

## 3. 实施期任务调整(U-D4c,新建 SSoT 任务)

新建 plan.html 任务 **U-D4c**(phase `U`,depends `U-D4b`,priority `P0`),TDD + codex-review:

- `backend/orchestration/line1_runner.py`:`run()` 第 4~6 步从"只辩 `shortlist[0]`"改为**按 `selection.shortlist` 顺序遍历**:每只 `build_lead_context → run_shortlist([brief]) → to_fund_manager_output → assemble_plan → _route`;REJECTED/HOLD/DEGRADED/非-BUY/早返 → fallthrough 下一只;VALIDATED BUY → 按 §2.2 模式收集(single=首个即返 / basket=收齐);`DailyBudgetExceededError`(预留或 debate-slot 拒超)→ 捕获 + fail-closed 停止遍历 + 返回已收集结果(不崩)。
  - `Line1RunResult` 扩展为可承载**多个**路由结果(篮子模式;single 模式退化为 ≤1)——保持 frozen + 向后兼容(新增 `routed_plans: tuple[...] = ()` 或并行 `results` 列表,不破既有字段)。
  - 选择模式常量(默认值见 §4)+ runner 参数注入;runtime 不可改。
- `backend/services/cost_guard.py:56`:`_DEFAULT_DAILY_BUDGET_RMB = 20.0` → `100.0`。
- `scripts/redline-check.sh` [M-005]:`_DEFAULT_DAILY_BUDGET_RMB = 20.0` 断言 → `= 100.0`(其余 3 常量不变)。
- **TDD 回归覆盖**(测试先行):
  - (a) lead 被 RiskEngine 拒 → fallthrough 到下一只产出 VALIDATED BUY;
  - (b) 全 shortlist 被拒 → 0 BUY 优雅收尾不崩(返回空结果 + 末态 outcome);
  - (c) 日 ¥100 预留上限正确 bound(预留累计逼近 ¥100 → 下一只 `DailyBudgetExceededError` → 停止遍历,已收集结果保留);
  - (d) `max_debates_per_day` 耗尽 → 停止遍历不崩;
  - (e) 篮子模式下多只 VALIDATED BUY **各走单一构造点**(断言每只 plan 经 `assemble_plan`,LLM 文本未流入数值字段);
  - (f) 单只模式遇首个 BUY 即停(不再 claim 后续 debate slot)。
- 门禁:`pytest --cov-fail-under=70` 全绿 + `ruff` + `bash scripts/redline-check.sh`(M-004 单一构造点 / X-018 orchestration 隔离 / N-005 Line-2 隔离 / L-004 / K-006 + **更新后的 M-005**)全绿 + `codex review --uncommitted`(1 cycle,修完 P0/P1/P2;codex 撞额度回退 `claude /code-review` high)。
- 一任务一 feature commit + 回填真实 7 位 hash 到 SSoT;push origin main 待 owner 授权。

---

## 4. owner 决策点(§2.2 终止/收集语义 = 单只 vs 篮子)

**待 owner 拍板**:Line-1 每日应"只产出第 1 个可路由 BUY(single)"还是"产出多只可路由 BUY 组成篮子(basket)"?

- harness 倾向把决策交给 owner;owner 在 kickoff 中表示倾向 **basket**("更贴近'选出优质股'")。
- 本 amendment 默认值在该决策后填入本节 + §2.2 + CLAUDE.md §2.3;代码两模式都实现(只改默认常量)。

> **决策填入(2026-05-26 owner 经 AskUserQuestion 拍板)**:**basket(篮子)**。Line-1 每日继续遍历 shortlist 收集**所有** VALIDATED BUY,直到 shortlist 耗尽 / `max_debates_per_day` 耗尽 / 日 ¥100 预留拒超,并额外受 P0-7 熔断 ≤5单/日 + RiskEngine check-10 `today_instruction_count` 自然 bound(实际篮子上限 ≈ 当日剩余 instruction 名额,默认约 5)。理由:更贴近"选出一篮子优质股";飞书决策群一天可收到多条 BUY 指示。
> 模块默认常量 = basket;single 模式经 runner 参数 / 常量保留(两模式均测试覆盖,U-D4c (e)/(f))。

---

## 5. 红线清单(本 amendment 之后)

1. 日 LLM hard = **¥100**(真·预留,跨越不发生);soft 0.7 比例不变(=¥70 关 Kimi escalation);月 ¥440 soft 不变;Kimi 日 ¥4 不变。4 常量里仅日 hard 数值变。
2. Line-1 **按 shortlist 顺序多候选辩论**(推翻"只辩一只");REJECTED→fallthrough 下一只;受 `max_debates_per_day` + 日 ¥100 预留双重 fail-closed bound;遍历优雅收尾不崩。
3. 终止/收集 = single / basket(§4 owner 拍板);篮子额外受 P0-7 熔断 ≤5单/日 + RiskEngine check-10 自然 bound。
4. **InstructionPlan 单一构造点 M-004 不破**:每只候选 plan 经 `assemble_plan` 确定性派生;LLM 永不写决策字段。
5. 所有 LLM 花费写同一 `llm:usage:{utc_date}` 计数器(amendment 2026-05-24 §2.4 不变);per-stage 0 重试 + budget-aware fallback 不变。
6. `cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}`;常量 runtime 不可改 + hot-reload 禁用(P1-7 §1 不变)。
7. RiskEngine 红线全留(禁涨停 BUY / 禁跌停 SELL / long-only / 仓位三连 / 熔断);lead 涨停被拒 = 正确履职,fallthrough 下一只,**绝不绕过拒单**。
8. 告警仅飞书 + audit + Phase B 成本面板;dedup_15min;严禁 SMTP/Slack/Discord(不变)。

---

## 6. 修订记录追加

`docs/plan.html` 新建 U-D4c 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md 同步:§2.2(`¥20/日 hard` → `¥100/日 hard`)、§2.10(`日 ¥20 hard` → `日 ¥100 hard` + `软触发 ¥14=70%` → `软触发 ¥70=70%` + 删"一次辩论/每日 shortlist 非 per candidate"措辞改为"Line-1 按 shortlist 多候选辩论")、§2.3(补"Line-1 按 shortlist 顺序多候选辩论直到可路由 BUY")。记忆库加一条 feedback/project memory。
