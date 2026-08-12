# 下一 session prompt — D1-c 止盈倍数 regime 条件化 + SELL 逻辑(何时卖/卖多少)夯实 + T+1

> **用法**:新 session 把本文 + `docs/SESSION-KICKOFF.md` + `CLAUDE.md`(§2.x 红线)+ `docs/plan.html` SESSION_LOG #67 一起读,按本文推进。
> **状态(2026-06-03 收尾)**:D2 + D1-a + D1-b 全 done(本地未 push)。本文 = owner 锁定的后续工作 prompt。
> **owner 锁定(2026-06-03)**:① 先做下面 **任务 A(D1-c:r_multiple 止盈 regime 条件化)**;② 所谓「复利再投」**不是单独的资金再投功能**,而是要**把「何时 SELL / SELL 多少」的逻辑与功能做扎实**(下面 **任务 B**);③ 必须正确处理 **T+1**:卖出股票当天资金的可用性(下面 §B.3,owner 明确提醒)。

---

## 0. 现状回顾(接手必读)

止盈/止损全在 **Line-2 盘中 30s 确定性 runner**(`backend/orchestration/line2_intraday_runner.py`,已挂 BrokerScheduler,**live、非 env 门控**)+ 日线 runner。已落地(均本地未 push,env 默认 OFF / shadow 待验):

| session | 内容 | env 门控 | commit |
|---|---|---|---|
| #65 D2 | 长期持股(intact thesis)豁免 TAKE_PROFIT + 软 WEIGHT_TRIM,保 15% 硬顶 | `QUANTMIND_LINE2_THESIS_TAKEPROFIT_EXEMPT_ENABLED` (OFF) | `d24385e` |
| #66 D1-a | drawdown 止损阈值 = 个股 90 分位 \|日收益\| ×1.5 clamp[3%,12%] | `QUANTMIND_LINE2_ADAPTIVE_DRAWDOWN_ENABLED` (OFF) | `fd5fe20` |
| #67 D1-b | 熊市 regime 收紧 drawdown(×`bear_multiplier`0.8)+ 接通基准指数(U-D3) | `QUANTMIND_LINE2_REGIME_DRAWDOWN_ENABLED` (OFF) | `7445f5d` |

**SELL 触发现状(`evaluate_intraday_sell_intents`,优先级,每码至多出 1 个):**
`ATR_TRAILING_STOP` > `DRAWDOWN_STOP` > `THESIS_QUANT_BREAK` > `TAKE_PROFIT` > `WEIGHT_TRIM`
- 保护性止损(ATR/drawdown):卖**全部已结算** `available_volume`。
- THESIS_QUANT_BREAK:全退(clamp ¥50k)。
- **TAKE_PROFIT**:现价 ≥ `成本 + r_multiple×R`(R=`atr_stop_mult×ATR`)→ 卖 `tranche_fraction`(0.5)分批锁盈,余仓骑 ATR 移动止损。
- WEIGHT_TRIM:超配减仓(软 16.5%→13% / 长持硬顶 15%,ceil+clamp¥50k)。
另有**日线** anomaly SELL(`backend/monitoring/sell_signal.py` + `line2_daily_runner.py`):DOWN 价/EWMA/布林。

**已建、务必复用的 pattern(D1-a/b 的经验,codex 反复钉死):**
1. **env 门控**:`main.py` 读 `QUANTMIND_LINE2_*_ENABLED`,把 config/flag 传 `Line2IntradayRunner(__init__)`;默认 OFF = 前版 bit-for-bit。
2. **实际触发值入 record(PIT)**:`IntradaySellIntent.effective_drawdown_threshold` 贯穿**全部** sell 构造,`_sell_record` 写它(非静态 config)——否则自适应触发的信号 replay 误拒。D1-c 必须同样为 r_multiple 加 `effective_r_multiple`。
3. **config_hash(PIT)**:runner `_compute_config_hash` 纳入 `{version, config}` + env flag → 陈旧 manifest fail-closed。
4. **`FEATURE_CODE_VERSION` bump**:任何 maths 改动(triggers 现 v6 / calibration 现 v2)。
5. **外部数据 PIT 三重守门**:`main._observable_index_closes`(finite-positive + ≤T-1 可观测 + 陈旧失效)——任何引入外部序列照此。
6. **regime**:`classify_regime(index_closes)`(已接通 U-D3);以 **bool `is_bear`** 传纯计算层(保 `intraday_calibration` import-clean,零 backend.* 子包)。
7. **import 隔离 N-005**:`backend/monitoring/` 禁 `backend.{llm,agents,agents_team,mirofish}`。

---

## 任务 A(先做):D1-c —— r_multiple 止盈倍数 regime 条件化

### A.1 目标
止盈**时机**随 regime 自适应,服务 owner「及时止盈落袋」:**熊市/震荡更早止盈**(降 `r_multiple` → 更小盈利即落袋)、**牛市让利润奔跑**(升 `r_multiple`)。当前 `r_multiple=1.0` 写死(`IntradayTriggerConfig`)。

### A.2 r_multiple 在哪用(精确)
`backend/monitoring/intraday_triggers.py::_take_profit_intent`:
```python
r_unit = cfg.atr_stop_mult * atr
target = cost + cfg.r_multiple * r_unit
if price < target or price <= cost:
    return None
...
stop_level=round(target, 4),   # ← 已记录目标价
```

### A.3 设计(镜像 D1-b)
- **配置**:新增 frozen `TakeProfitCalibrationConfig`(或扩 `DrawdownCalibrationConfig` 加止盈字段;建议**独立 config 独立 env**,便于分开 shadow):`bull_r_multiple`/`neutral_r_multiple`/`bear_r_multiple`(如 1.3/1.0/0.6),runtime-immutable,只离线 P2-2 重校准。
- **派生**:纯函数(放 `intraday_calibration.py`)`effective_r_multiple(regime, config) -> float`,以 **bool 或一个轻量 regime 标记** 传入(保 import-clean;若需三态,传 `is_bull`/`is_bear` 两 bool 或一个本模块自定义 StrEnum——**勿 import add_position.MarketRegime** 破纯净)。
- **接线**:`evaluate_intraday_sell_intents` 已有 `regime` 参数;`_take_profit_intent` 用 `effective_r = effective_r_multiple(regime, tp_cfg)` 取代 `cfg.r_multiple`。
- **env**:`QUANTMIND_LINE2_REGIME_TAKEPROFIT_ENABLED`(默认 OFF);main.py 开时传 `TakeProfitCalibrationConfig()` 否则 None → 用静态 `cfg.r_multiple`(v6 bit-for-bit)。
- **PIT(codex 必查,照 D1-a/b 教训)**:
  - `IntradaySellIntent` 加 `effective_r_multiple: float | None`,`_take_profit_intent` 填;`_sell_record` 的 `threshold_params` 为 TAKE_PROFIT 写 `effective_r_multiple`(+ 现有 `stop_level` 已是 target 价)。**注意**:若担心 lower-priority 也需要(D1-a cycle-2 教训),评估是否贯穿全部 sell 构造——r_multiple 只影响 TAKE_PROFIT 触发,大概率只 TAKE_PROFIT 需要,但**请让 codex 确认**。
  - config_hash 纳 `takeprofit_calibration {version, config}` + env flag。
  - `FEATURE_CODE_VERSION` triggers v6→v7(+ 若动 calibration 模块,其版本 bump)。

### A.4 红线(保留)
零 LLM(r_multiple 仅 regime/历史派生,严禁 LLM/新闻);config runtime 不可改(frozen+pinned+重启);PIT 可复现(effective 值入 record + config_hash + 版本 fail-closed);RiskEngine 纯函数 / 单一构造点 / N-005 import 隔离;env 默认 OFF + shadow + 人工 gate;**场外信息只 advisory**(owner 选 A)。**clamp**:`r_multiple` 设下界(如 ≥0.5)防熊市止盈过早=噪声卖出 + 上界(如 ≤2.0)。

### A.5 TDD + 门禁 + codex(每步同 D1-a/b)
- 测试:`effective_r_multiple` 三态 / clamp;`_take_profit_intent` 熊市更早触发(同一价在 r=1.0 不触发、r=0.6 触发)/ None=v6 bit-for-bit;`_sell_record` 写 effective;config_hash 含 takeprofit flag。
- 门禁:`FEISHU_INTERACTIVE_ENABLED=false pytest -q --cov=backend --cov-fail-under=70` + `ruff check <files>` + `bash scripts/redline-check.sh`(N-005/M-004)全绿。
- **codex**:`codex review --uncommitted </dev/null`,修完所有 P0/P1/P2 再 commit(经验:止盈/PIT 类 codex 常 3-6 轮,认真改)。
- amendment-first:`docs/decisions/P0-7-amendment-2026-06-XX-regime-conditioned-takeprofit.md`(基准 P0-7-amendment-adaptive + P0-10-amendment-line2-2026-05-30 take-profit)。

---

## 任务 B(并行/紧接):把「何时 SELL / SELL 多少」逻辑夯实(owner 口中的「复利」真义)

> owner 明确:复利**不是**单独的「卖出资金再投」功能,而是**把 SELL 的触发(何时)与定量(多少)做对、做全、做可解释**,使止盈落袋的资金自然进入下一轮买入实现复利。

### B.1 何时 SELL —— 完整触发决策树(审 + 文档化 + 验证)
列全并核对:盘中 5 触发(ATR/drawdown/thesis-break/take-profit/weight-trim,优先级见 §0)+ 日线 anomaly SELL(DOWN 价/EWMA/布林)。
- 核对:覆盖是否完整(有无该卖没卖的情形)、优先级是否正确(保护性止损永不被掩盖)、每日去重 `(code, kind)` 是否合理、停牌干净降级。
- 产出:一份 SELL 决策树文档(可放 `docs/research/`),每触发的条件/阈值/数据源/PIT 来源一目了然。

### B.2 SELL 多少 —— 定量(审 + 决策是否精化)
- 止损/thesis-break:全 `available_volume`(T+1 已结算)。take-profit:`tranche_fraction`(0.5)。trim:回 target。全受**单次 ¥50k**(check#9)+ 熔断 ≤5 单/日 + 单股 15% 红线。
- **待 owner 决策点**:止盈是否**分级**(+1R 卖 50% → +2R 再卖一档 → 余仓骑 trailing)?当前是单档 +1R 后纯 trailing。分级更「落袋」但更复杂。若做,需 ledger 记「已止盈档位」(P-006 `take_profit_already_taken` 已有雏形)。

### B.3 ⚠️ T+1 资金可用性(owner 明确提醒,务必查实并对齐)
- **股票 T+1**:SELL 量已正确用 `available_volume`(T+1 已结算,不可卖未结算股)——这层已对。
- **资金**:A 股惯例 = 卖出资金**当日可用于继续买入证券(T+0 资金周转)**,但 **T+1 才能提现(转出银行)**。owner 提醒「卖了当天拿不到钱」对**提现**成立、对**再买入**通常不成立。**但本系统永禁真实下单 + MockBroker 镜像 + 飞书人工执行**,故**必须查实 `backend/broker/mock_broker.py` 的现金结算模型**:SELL 成交后 `available_cash` 是**当日即增**(可同日 BUY)还是 **T+1**?
  - 查 `apply_external_fill` / `_cash` / 结算逻辑 + 现有测试。
  - 若 MockBroker 当日即放可用现金 → 止盈资金可经 **Line-1 09:35 cron / Phase V ≤5 槽轮动**(轮动本就 **T+1 跨日**:今卖次日买)在**次日**买入实现复利,**无需新机制**——只需验证 SELL→现金→次日 BUY 闭环真的通。
  - 若与 A 股语义不符(错锁/错放)→ 这是 bug,修(改结算语义须 amendment + 对账影响评估)。
- **产出**:在 SELL 决策树文档里写清「SELL→现金可用时点→下一 BUY」的闭环 + T+1 约束,确认复利路径成立(Phase V 轮动 + Line-1 次日入场)。**严禁**为「同日复投」破 T+1 或破永禁真实下单/人工 gate。

---

## 红线总表(任务 A/B 全程不可破)
永禁真实下单 / 飞书人工 gate / 127.0.0.1 / **LLM 不写决策字段** / RiskEngine 纯函数 / InstructionPlan 单一构造点 / **PIT 可复现**(config_hash + effective 值入 record + FEATURE_CODE_VERSION fail-closed)/ config runtime 不可改(pinned + 重启)/ N-005 monitoring import 隔离 / 自进化 7 禁 + 人工 gate + 45 日 shadow / **场外信息只 advisory(owner 选 A,严禁 LLM/新闻进数值阈值)** / SELL 用 T+1 已结算 available_volume / 熔断 ≤5 单/日 + 单次 ¥50k + 单股 15%。

## 命令速查
```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70
/home/ps/anaconda3/envs/zhanglan/bin/ruff check <改动文件>
bash scripts/redline-check.sh
codex review --uncommitted </dev/null        # 修完 P0/P1/P2 再 commit
# 一任务一 feature commit(本地;push 待 owner 授权)→ SESSION_LOG 追加条目 → 更「修订记录」li
```

## 文件地图
- `backend/monitoring/intraday_triggers.py` — SELL 触发评估 + `_take_profit_intent`(r_multiple 在此)。
- `backend/monitoring/intraday_calibration.py` — `derive_drawdown_threshold`(止盈 derive 加这或同级纯模块)。
- `backend/orchestration/line2_intraday_runner.py` — 接线 + `_compute_config_hash` + `_sell_record`。
- `backend/main.py` — env 门控 + `index_closes` 接线 + `_observable_index_closes`。
- `backend/monitoring/sell_signal.py` + `line2_daily_runner.py` — 日线 anomaly SELL。
- `backend/broker/mock_broker.py` — **SELL 成交现金结算(B.3 T+1 查实点)**。
- amendment → `docs/decisions/`;codex 报告 → `docs/reviews/`;设计/决策树 → `docs/research/`。

## 5 个 dossier 决策点状态(`docs/research/line2-adaptive-stops-...-2026-06-03.md`)
- ① D2 豁免范围:✅ 定(豁免止盈+软超配,保 15% 硬顶)。
- ② 长持定义:✅ = intact PositionThesis。
- ③ D1 先做哪系数:✅ drawdown(D1-a)+ regime(D1-b);**下一 = r_multiple(任务 A)**。
- ④ regime 粒度:目前 BEAR/非-BEAR;任务 A 可引入 BULL/NEUTRAL/BEAR 三态(止盈倍数三档)。
- ⑤ 复利再投:✅ owner 重定义 = **任务 B(SELL 何时/多少 + T+1)**,非单独机制。

## 另:运行态待办(owner 亲自,与上面代码工作解耦)
- **#64 MTM 修复(`ff78877`)待 owner pull + 重启 + 交易时段验证**(`/api/portfolio/equity-points/latest` 的 `market_value>0` 且 `positions=5`)。
- 本对话 #64–#67 + 此前 #63 + Phase V/W 全部**本地未 push,待 owner 授权**。
- D2/D1-a/D1-b 激活均需各自 env on + 45 日 shadow + 重启;**#67 已让既有「ADD 熊市禁补」即时生效**(熊市不再建议补仓,无需 env,严格保守,owner 周知)。
