下面整段作为新 session 的开场消息粘贴即可。它自包含,假设你对本仓库零上下文。

---

# 开工:Phase P — 组合配置 + 篮子汇总 + 仓位管理(从 P-002 起)

我们在 QuantMind(`/home/ps/papers/QuantMind`,分支 `main`)。先读 `docs/SESSION-KICKOFF.md` 和 CLAUDE.md,再按下面执行。**本 session 从 P-002 起做 Phase P 的代码任务。**

## 0. 背景(为什么有 Phase P)
2026-05-30 owner 调查"MVP 是否能一次给 ≤5 优质股 + Line-1 持仓配比 + Line-2 仓位管理 + 建仓后持续监控并飞书后续指示",深挖代码 + 外部调研后结论 = **部分具备**:
- ✅ **建仓后监控 + 飞书后续指示**已有(Line-2 daily 09:35 + 盘中 30s 两 cron;止损/移动止损/补仓 → 飞书)。
- ⚠️ **篮子 ≤5** 能力已接线(09:35 cron 逐只辩论收 VALIDATED BUY),但**逐条发、无汇总**。
- ❌ **Line-1 无组合配比**(`max_compliant_buy_volume` 每只独立顶格到 15% 单股上限,无加权/分散/资金封套)——最实缺口。
- ❌ **Line-2 缺止盈 + 超配减仓**(只在不利时卖,从不主动锁盈)。

Owner 经 AskUserQuestion 锁 **4 决策**(已写进 amendment,不要再问):
1. 配比方法 = **逆波动率加权**(`w_i=(1/σ_i)/Σ(1/σ_j)`)+ σ 缺/≤ε **等权兜底**。
2. 建仓力度 = **稳健分批**:单股目标 ~10%,单日部署 ~1/3 可用现金,分多日建满,留现金缓冲。
3. 飞书呈现 = **篮子汇总概览 + 逐只可执行指令**(两者都发)。
4. Line-2 = 补 **R 倍数分批止盈**(+1R 减半,余仓交现有 ATR 移动止损)+ **阈值带超配减仓**(>16.5% 减回 13%)。

计划全文(已 owner 批准):`/home/ps/.claude/plans/twinkly-wishing-glade.md`。

## 1. 当前进度
- **P-001 已 done + 本地提交 `1017f96`(未 push)**:3 份 amendment + plan.html Phase P 7 任务。
  - `docs/decisions/P0-7-amendment-2026-05-30-portfolio-allocation.md`(配比)
  - `docs/decisions/P0-3-amendment-2026-05-30-basket-digest.md`(篮子汇总 / 第 6 FeishuMessageKind)
  - `docs/decisions/P0-10-amendment-line2-2026-05-30-take-profit-trim.md`(止盈减仓)
- plan.html 中 `P-001` = done(commit `1017f96`),`P-002`..`P-007` = todo。

## 2. 开工协议(强制,违反=违规)
1. `grep -nE 'status="doing"|status="blocked"' docs/plan.html` 定位;确认活动 Phase = **P**,P-001 done,**P-002 当前可做**(depends P-001 已满足)。
2. 认领:把 `P-002` 改 `status:"doing"` + `session_date:"<今日>"`。
3. **一任务一 feature commit**;**每个有码任务 commit 前跑 codex-review**,修完所有 P0/P1/P2 再提交(codex 撞额度 → 回退 `claude /code-review` high 3-angle,别跳;见下 §6)。
4. 完成任务:状态改 `done` + 回填真实 7 位 `commit:`。
5. **push owner-gated** —— 别 push,提交完留给 owner。
6. Phase 全 done(或确实做不动)才写一次 docs-only SESSION_LOG。

## 3. 全程红线(违反即停)
- **单一构造点**:配比层/止盈/减仓**只产数值喂 builder,绝不构造 `InstructionPlan`**。`grep -rn "InstructionPlan(" backend/ | grep -vE "models/instruction\.py|instruction_plan_builder\.py"` 必空(tests 除外)。
- **配比只压不放**:`final volume = min(max_compliant_buy_volume(...), 配比目标手数×100)`;**永不放宽** 单股15%/总仓70%/单笔¥50k/≤5单。RiskEngine 14-check 仍独立权威。
- **模块隔离**:`backend/portfolio_allocation/` 严禁 `import backend.{llm,agents,mirofish}`,**且不被 `backend/risk/` import**。Line-2 止盈/减仓在 `backend/monitoring/`(同样禁 `backend.{llm,agents,agents_team,mirofish}`;ledger 读在 provider 层)。
- **确定性 + PIT**:σ 取 frame 已有 `closes`(纯 stdlib,bit-exact replay);数值永不来自 LLM。
- **单源 cap**:单股 15% / 单笔 ¥50k / 整手 100 一律从 `config/risk.yaml` `position_limits` 读,**不在新 yaml 重复**。
- **Line-2**:止盈/减仓 = 部分 SELL,经现有 `assemble_monitoring_plan` → RiskEngine → 飞书;`signal_id` 保 `LINE2-MON-`;SELL 不熔断。
- **飞书篮子汇总**:display-only,**无任何可被入站正则解析的订单字段**;发决策群;经 `renderer.py` 防注入;LLM 不拼接。
- **配置 runtime 不可改 + hot-reload 禁**;改走 amendment + 重启。

---

## 4. P-002(本 session 首要)— `backend/portfolio_allocation/` 纯模块 + 配置

**目标**:确定性纯模块,给定(候选 σ + 账户 + 部署封套)算出每只**目标现金额**与**整手数**,供 P-003 的 provider 用 `min(max_compliant, 目标)` clamp。**本任务不接线**(P-003 才接)。

### 4.1 新建文件
| 文件 | 内容 |
|---|---|
| `config/allocation_policy.yaml` | 配比策略参数(见 4.2)|
| `backend/portfolio_allocation/__init__.py` | 公共 export(mirror `backend/budget_policy/__init__.py`)|
| `backend/portfolio_allocation/policy.py` | `AllocationPolicy` frozen + `load_allocation_policy` + `AllocationPolicyError` |
| `backend/portfolio_allocation/volatility.py` | `inverse_vol_weights` |
| `backend/portfolio_allocation/allocator.py` | `compute_target_cash` + `cash_to_lots` |
| `backend/portfolio_allocation/CLAUDE.md` | 子模块上下文(mirror `backend/budget_policy/CLAUDE.md`)|
| `tests/portfolio_allocation/test_volatility.py` | |
| `tests/portfolio_allocation/test_allocator.py` | |
| `tests/portfolio_allocation/test_policy.py` | |
| `tests/portfolio_allocation/test_module_contract.py` | clone `tests/budget_policy/test_module_contract.py`(AST 隔离 + 公共 API)|

### 4.2 `config/allocation_policy.yaml`(owner 已批准的数值)
```yaml
# Phase P P-002 — 组合层配比策略(runtime 不可改;改需 amendment + 重启)
# 治理: docs/decisions/P0-7-amendment-2026-05-30-portfolio-allocation.md
allocation:
  method: inverse_volatility   # 逆波动率加权;σ 缺/≤ε 自动等权兜底
  deploy_fraction: 0.33        # 单日最多部署「可用现金」的比例(稳健分批)
  per_name_target_pct: 0.10    # 单只目标权重上限(< 15% 硬顶)
  cash_buffer_pct: 0.05        # 部署后保留 ≥ 该比例×总资产 的现金缓冲
  vol_lookback: 20             # 波动率窗口(对齐 screening volatility_20d)
# 单股硬顶 15% / 单笔 ¥50k / 整手 100 从 config/risk.yaml position_limits 读(单源,不在此重复)
```

### 4.3 算法(确定性纯函数)
**`inverse_vol_weights(sigma_by_code: dict[str, float | None], *, eps: float = 1e-9) -> dict[str, float]`**
- `inv[c] = 1/σ_c` 当 `σ_c is not None and σ_c > eps`,否则记为缺失。
- 全部缺失 → 等权 `1/N`。
- 部分缺失 → 缺失名赋「有效名 `inv` 的均值」(中性、不favored不dropped),再整体归一化和为 1。
- 返回 `{code: weight}`,和≈1,确定性。

**`compute_target_cash(weights, deployable_cash, total_assets, existing_value_by_code, *, per_name_target_pct, single_stock_cap_pct, single_instruction_cap, eps=1e-9) -> dict[str, float]`**
1. `raw[c] = weights[c] * deployable_cash`
2. `cap[c] = max(0, min(per_name_target_pct*total_assets, single_stock_cap_pct*total_assets - existing_value_by_code.get(c,0), single_instruction_cap))`
3. `alloc[c] = min(raw[c], cap[c])`
4. **一遍残差重分配**:`residual = deployable_cash - Σalloc`;若 `residual>eps`,按 `weights` 在未触顶名(`alloc[c] < cap[c]-eps`)间分摊,再 `min(cap[c], …)`。
5. 返回 `{code: 目标¥}`(今日增量目标,≥0)。

**`cash_to_lots(target_cash, price, lot=100) -> int`**
- `price<=0` 或非有限 → `0`。
- `lots = floor(target_cash / (price*lot))`;返回 `max(0, lots)*lot`。
- **返回 0 = 今日不买这只**(P-003 必须把"目标 0 手"当作跳过,**不要** `min(max_compliant, 0)` 触 Pydantic `volume>0`;见 §5 P-003 注意)。

**`deployable_cash` 推导**(放 allocator 或 policy helper):
`deployable = max(0, min(available_cash*deploy_fraction, available_cash - cash_buffer_pct*total_assets))`。

### 4.4 `policy.py`(mirror `backend/budget_policy/policy.py:230-294`)
- `AllocationPolicy` frozen:`method:str` / `deploy_fraction:float` / `per_name_target_pct:float` / `cash_buffer_pct:float` / `vol_lookback:int` / 以及从 risk.yaml 读来的 `single_stock_cap_pct:float` / `single_instruction_cap:float` / `lot_size:int`。
- `load_allocation_policy(allocation_yaml_path, risk_yaml_path)`:读 `allocation_policy.yaml` 的 `allocation` 块 + `risk.yaml` 的 `position_limits`(取 `max_single_stock_pct` / `max_single_instruction_amount` / `volume_lot_size`,**单源**)。严格校验(deploy_fraction∈(0,1]、per_name_target_pct∈(0, single_stock_cap]、vol_lookback≥2 等),失败 raise `AllocationPolicyError`。load-once,无 hot-reload。

### 4.5 复用接口(file:line —— 直接照抄范式)
- σ 来源:`backend/screening/factors.py:81` `volatility(closes, 20)` = `statistics.pstdev(returns[-20:])`,挂在 `FactorVector.volatility_20d: float | None`(`factors.py:131`);经 `CandidateRow.factors.volatility_20d`(P-003 用)。**P-002 只吃 `dict[str, float|None]`,不依赖 screening 类型**(保持纯)。
- loader 范式:`backend/budget_policy/policy.py:230` `load_budget_tier_config`(读 risk.yaml + 单源 position_limits + frozen + 严格校验)。
- `position_limits` 键(`config/risk.yaml`):`max_single_stock_pct`(0.15)/ `max_single_instruction_amount`(50000)/ `max_total_position_pct`(0.70)/ `volume_lot_size`(100)。
- 隔离测试模板:`tests/budget_policy/test_module_contract.py`(AST 扫 `backend.{llm,agents,mirofish}` + `__all__` 一致)。
- CLAUDE.md 模板:`backend/budget_policy/CLAUDE.md`。

### 4.6 测试(TDD,≥80% 覆盖)
- `test_volatility`:逆波动率公式正确;σ=None / σ≤ε 等权兜底;全缺失=等权;和≈1;确定性(同输入同输出)。
- `test_allocator`:clamp 到 `min(10%目标,15%硬顶−已持,¥50k)`;残差重分配;incremental(减已持有);`cash_to_lots` 整 100 手 floor + 买不起返 0;**对抗:任意 σ(含极端/0/None)下每只目标¥ ≤ 各 cap 且 Σ ≤ deployable**;同输入 bit-exact 同结果(replay)。
- `test_policy`:loader happy + 各校验失败 raise + 单股 cap 确实来自 risk.yaml(改 allocation_policy.yaml 不影响 cap)+ frozen 不可变。
- `test_module_contract`:clone budget_policy 版,改 `_ROOT`/包名 + planted-violation 自检 + `__all__`。

---

## 5. P-003..P-007(后续;Plan agent 已验证的集成缝)

**P-003 Line-1 接线(provider 内,两层)** — `backend/services/line1_context_provider.py` + `backend/orchestration/line1_runner.py`
- provider 加 `prime_allocation(shortlist_rows: Sequence[CandidateRow])`:walk 前算 `target_cash_by_code`(σ 取 `row.factors.volatility_20d`;`deployable` 用 `run_state.account`;`existing_value` 来自 `run_state.positions`;cap 从 `run_state.risk_config.position_limits`)。
- `build_lead_context` 内(`line1_context_provider.py:407` 现 `volume = max_compliant_buy_volume(...)`)改:`target_lots = cash_to_lots(target_cash[code], limit_price); volume = min(max_compliant, target_lots)`。**若 `target_lots==0` → 视为今日不买(返回一个 `Line1QuoteDegrade`-类跳过 或新 skip 分支,别强行 1 手)**。两处 `proposed_volume`(brief `:462` + AssemblyContext `:500`)同步。
- runner:`Line1ContextProvider` Protocol(`line1_runner.py:194`)加可选 `prime_allocation`;`run()` 在 `selection` 后(`~:359`)`hasattr` 守护调用一次,传 `[by_code[c] for c in selection.shortlist]`。**committed 线程 / 单一构造点 / RiskEngine 不动**。
- 测试:`tests/orchestration/test_line1_runner.py` 的 `FakeProvider` 加 no-op `prime_allocation`(保 `volume==200` 绿);`tests/services/test_line1_context_provider.py` 加配比-clamp + target-0-skip 用例。

**P-004 飞书篮子汇总** — `backend/integrations/feishu/renderer.py` + `line1_runner.py` + `main.py`
- `FeishuMessageKind` 5→6 加 `BASKET_DIGEST`;成员数测试 + redline 校验同步 5→6。
- `render_basket_digest(routed_buys, *, pilot)`:全部 ≤5 只 code/name + 目标手数·权重·金额 + 合计部署 + 现金占用;**display-only**(mirror `renderer.py:171` `render_smoke_ping` 的非可执行文风;**禁印** `QM-…-BUY-… 已执行` 形 token)。
- 发送:`run()` 末(`_aggregate` 后 `~:426`)`routed_buys` 非空 → **独立幂等发送**(`FeishuClient.send_message`,幂等键 `f"{sid}-basket-digest"`,走现有 `OutboxRepository.try_claim/mark_sent`,**不走 `InstructionDispatcher`**)。sender 注入保 runner import 隔离。
- 对抗测试:digest 文本喂 `parse_execution_report` 必 `ExecutionReportParseError(no_pattern_match)`;幂等(两 run 一发)。

**P-005 Line-2 止盈+减仓触发** — `backend/monitoring/intraday_triggers.py` + `backend/orchestration/line2_intraday_runner.py`
- `IntradayTriggerKind`(`intraday_triggers.py:99`)加 `TAKE_PROFIT` + `WEIGHT_TRIM`;把两者**并入** `evaluate_intraday_sell_intents`(`:252`,加 `account` 参数 + take-profit/trim config + 可选 `take_profit_already_taken: frozenset[str]` 默认空)。
  - 止盈:`R = atr_stop_mult × close_atr(closes, window)`(复用 `add_position.py:259`);`live ≥ cost + r_multiple×R` 且净盈利 → SELL `floor((available_volume×tranche_fraction)/100)×100`;sub-1-lot 跳过(不发 0)。
  - 减仓:`weight = vol×live/total_assets`;`> max_single_stock_pct×(1+trim_band)` → 减回 `trim_target_pct`;settled `available_volume` clamp + 整手。
- 复用 `IntradaySellIntent`(`:125`,不applicable 字段置 0)→ `make_intraday_sell_context`/provider/`render_monitoring_sell`/manifest **全不改**;`evidence_id = MARKET-{code}-{kind.value}`;`anomaly_reason` 带"止盈 +1R"/"超配回调"。
- **去重键 `(code,side)` → `(code,trigger_kind)`**(`line2_intraday_runner.py:418`);优先级 **ATR > 回撤 > 止盈 > 减仓**;每 code/tick ≤1 intent;SELL 仍压同 code ADD。
- take-profit/trim 参数扩进 `IntradayTriggerConfig`(`:108`,已 runtime 不可改 + 入 replay manifest):`r_multiple=1.0` / `tranche_fraction=0.5` / `trim_band=0.10` / `trim_target_pct=0.13`;单股 cap 从 RiskConfig 读。
- 测试:新 kinds 仅在新条件触发(原 15 intraday 测试绿)+ 优先级 + 去重键 + 止盈/ADD 互斥(止盈在 cost 上、ADD 在 cost 下)。

**P-006 止盈 tranche gate(ledger 派生)** — `backend/services/line2_context_providers.py`
- provider 端从 `broker_events`(`stream_since`)按 correlation→instruction→`side=SELL` + `evidence_ids` 含 `MARKET-{code}-take_profit`、限连续持仓 episode(volume 未归零)算 `take_profit_already_taken: frozenset[str]`,在 build intents 前传入 P-005 评估器。`backend/monitoring/` 仍 import-clean(不直连 ledger)。
- 复用 `backend/services/daily_state_assembler.py:128` stream-and-filter 先例;**不新增 `LedgerEventKind`**(闭集;用 evidence 判别)。
- 测试:replay(重跑同 ledger 同结果)+ 已减半不再止盈 + 全平重买新 episode 可再止盈。
- ⚠️ episode 复原若过脆 → 退化"按交易日去重"(仍 ledger 派生非内存),**触此线先报 owner**。

**P-007 红线 + dry-run + 收尾** — `scripts/redline-check.sh` + `scripts/dry_run_double_line.py` + `docs/plan.html`
- redline-check 加 `[P-002]` 模块隔离子检 + `FeishuMessageKind` 成员数=6 + 确认 M-004 `InstructionPlan(` AST 仍绿。
- `dry_run_double_line.py` 扩:多只短名单 → 逆波动率 size(非每只 15%)+ 部署 ≤1/3 现金 + digest 渲染且不可解析;持仓 >+1R → 部分止盈;>16.5% → 减仓。
- 全套绿 + Phase P 全 done + 一次 docs-only SESSION_LOG。

---

## 6. 门禁命令
```bash
PYENV=/home/ps/anaconda3/envs/zhanglan/bin
# 单测(P-002)
$PYENV/pytest -q tests/portfolio_allocation --cov=backend/portfolio_allocation --cov-report=term-missing
# 全量 + 覆盖(非 risk ≥70%)
$PYENV/pytest -q --cov=backend --cov-fail-under=70
# ruff(触动文件)
$PYENV/ruff check backend/portfolio_allocation tests/portfolio_allocation
# 红线
bash scripts/redline-check.sh
# 配比层隔离 + 单一构造点 自查
grep -rn "import backend\.\(llm\|agents\|mirofish\)" backend/portfolio_allocation/        # 必空
grep -rn "InstructionPlan(" backend/ | grep -vE "models/instruction\.py|instruction_plan_builder\.py"  # 必空(tests 除外)
```
**codex-review(每码任务 commit 前,强制)**:`codex review --uncommitted`(cycle 1,无 prompt)→ 修 P0/P1/P2 → `codex exec --sandbox read-only </dev/null`(verify)。**codex 撞额度** → 回退 `claude /code-review`(high,3-angle),别等别跳。codex exec 务必 `</dev/null`(防 stdin 死锁)。

## 7. 参考
- 计划:`/home/ps/.claude/plans/twinkly-wishing-glade.md`
- amendment:`docs/decisions/P0-7-amendment-2026-05-30-portfolio-allocation.md` / `P0-3-amendment-2026-05-30-basket-digest.md` / `P0-10-amendment-line2-2026-05-30-take-profit-trim.md`
- 记忆:`~/.claude/projects/-home-ps-papers-QuantMind/memory/project_phase_p_allocation_kickoff_2026_05_30.md`(+ MEMORY.md 同名条目)
- 投研:`backend/budget_policy/`(loader+隔离范式)、`backend/screening/factors.py`(σ)、`backend/monitoring/intraday_triggers.py`(Line-2)、`backend/services/line1_context_provider.py`(P-003 接线点)。

**纪律**:绿测 ≠ commit-safe(codex 历史抓出真 bug);断言要覆盖"被谁调用、贯穿到哪",不止"函数返回对"。完整升级路径优先,不为省工作量妥协可用性。
