# 真实可交易性 MVP — 5 缺口 + Phase U 收尾(finalized plan, 2026-05-27)

> 本文件 = session #45 末提出的 5 个真实可交易性缺口的**已敲定执行计划 + 进度看板**。
> 经 plan mode 详细只读核查 + 3 轮 codex 评审 + 2 个 owner 决策(见下)敲定,owner 批准。
> **每完成一项就在本文件勾掉(`[ ]`→`[x]`)**;一任务一 feature commit,plan.html 回填真实 hash。
> 原始 SSoT = `docs/plan.html`(新增 U-E1..U-E5);红线 = `CLAUDE.md §2/§2.0`。

---

## 0. Owner 决策(已锁,2026-05-26/27)
- **缺口4 买入价呈现**:`参考价 + 建议区间 + 限价上限(价格笼子内)`;限价取实时盘口派生。**(U-E2 done 11b9d32)**
- **缺口5 回报字段**(**2026-05-27 反转**):owner **只报成交价(每股,不含费)+ 股数**;**系统替 owner 算含费成本价**。
  owner 佣金 **万分之1.5(0.00015)、不足 5 元按 5 元、双向** + 过户费/印花税(交易所标准,系统已建模)。
  ~~旧:owner 报含费成本价、系统永不重算~~ 已推翻。amendment:`docs/decisions/P0-4-amendment-2026-05-27-owner-reports-fillprice-system-computes-cost.md`。

## 1. 安全红线(全程不破)
永禁真实下单 / 飞书人工执行 / 127.0.0.1 / LLM 不写决策字段 / RiskEngine 纯函数 14-check 权威
(禁涨停 BUY·禁跌停 SELL·long-only·仓位三连·熔断)/ InstructionPlan + volume 单一构造点 M-004 / 人工 gate。
**凡改已锁决策(P0-3/P0-4/P0-5/P0-7/P0-8/P1-2.C)→ 先写 `docs/decisions/*-amendment-2026-05-27-*.md` 再改代码。**

## 2. 关键事实(已核查,非臆测)
- `trading_hours.is_trading_hours` 仅连续竞价;无集合竞价判定;stdlib-only(被 `risk/engine.py` import)。
- `line1_context_provider.py:308` `limit_price=round(lead.last_price,2)`=**T-1 EOD 收盘价**;实时层(`MarketMetaProvider.get_current_price` / `MarketDataService.get_stock_realtime`)存在但**未接入** Line-1。
- `mock_broker.apply_external_fill(BUY)`:`net=amount+fee; cash-=net; cost_price=fill_price`(不重算滑点/过户费);`_apply_buy` 已做加权平均成本;`attach_market_meta` 已有(simulation_auto at-fill recheck,P1-2.C)。
- `risk/engine` check#02 价比 prev_close 板内涨跌停带;check#12 禁涨停 BUY。**价格笼子(比卖一)全栈未建模** → 缺口4 新增。
- `renderer._dispatch_body_lines` **无判据**;`render_monitoring_sell(anomaly_reason)` 是 sanitized-param 范式(`_single_line`)。
- `InstructionPlan`(frozen/strict)**无 reasoning 字段** → 判据经 runner→renderer 参数传,不进 model。量化=`CandidateRow.factors`+score;推理=辩论 state。
- `regex_patterns` FILLED=volume+fill_price+fee;`compute_idempotency_key` 已含 instruction_id+kind+prefix+code+volumes+price+fee+reason;前端镜像 `frontend/src/utils/executionRegex.ts`(vitest 断言一致)。
- `route_coordinator` FEISHU_INTERACTIVE→`InstructionDispatcher` 发到 `FEISHU_DECISION_CHAT_ID`(durable at-most-once outbox=cond5 证据);入站=lark-oapi WS(已鉴权,无公网入站)。
- `dry_run_realdata.py:169 build_data_layer` 调 `load_data_sources_config()` **缺 yaml_path 参** → TypeError 被吞 → `market_meta` 恒 None。

## 3. 执行计划(顺序:E1→E2→E3(gap5)→E4(gap3)→E5(gap2)→U-D5)
> 每个代码任务:**amendment → TDD(非 risk>70%/risk>95%)→ 本地门禁全绿(pytest+ruff+redline-check+动前端再 type-check&vitest)→ codex review --uncommitted 修完 P0/P1/P2 → 1 feature commit → plan.html done+真 hash**。push origin main **owner-gated**。

### [x] U-E1 — 缺口1:A股规则研究 + trading_hours 集合竞价判定(地基)✅ feature 1270e7a
- [x] 决策:维持 09:35 连续竞价 cron(不改 09:25 开盘集合竞价撮合)
- [x] 研究 dossier `docs/research/a-share-trading-rules-2026-05-27.md`:集合竞价(09:15-09:25 撤单窗口/09:25 开盘价、14:57-15:00 收盘)、价格笼子(连续竞价买入≤max(卖一×1.02,卖一+10×tick;股票+0.10/ETF+0.01);创业板/科创×1.02)、T+1、涨跌停分板(主板±10%/创业·科创±20%/主板 ST 2025改±10%/北交±30%/新股首日不限)、最小变动价位 0.01·ETF 0.001/1手=100、五档盘口、高端玩法(打板/低吸/竞价抢筹,仅作信号输入参考)、**已建模 vs 未建模诚实边界**(MockBroker 不建模集合竞价撮合/五档盘口)
- [x] `trading_hours.py` 新增非破坏式 `is_opening_call_auction`/`is_closing_call_auction`/`is_call_auction`/`market_phase`(stdlib-only;不改 `is_trading_hours`)
- [x] amendment `P0-7-amendment-2026-05-27-call-auction-predicates-and-matching-boundary.md`(集合竞价判定 additive + MockBroker 不建模撮合声明)
- [x] TDD(3868 passed/cov 90.38%)+ 门禁(ruff+redline 全绿)+ codex(无问题)+ commit 1270e7a + plan.html done
- ⚠️ U-E2 预研:`StockQuote` 无 best_ask(仅 price/last);adata/akshare spot 无五档盘口 → U-E2 须新增 bid/ask 取数或 degrade

### [x] U-E2 — 缺口4:实时盘口 + 价格笼子限价(确定性派生)✅ feature fe1744d(cage 核心)+ 11b9d32(集成)
- [x] 新建纯模块 `backend/risk/price_cage.py` ✅ **feature fe1744d**(codex 5 轮 6 findings 全修 + 末轮 clean,100% cov,44 tests):`cage_ceiling`/`cage_bounded_buy_limit`/`is_within_cage`/`tick_size`;ceiling=max(best_ask*1.02, best_ask+10×tick)(主板/ETF)/×1.02(创业·科创);**tick 感知**(股 0.01/ETF 0.001);**exact cap 做 ≤ 比较**,display ceiling + 最终限价 floor 到 0.01 向下(永不 废单 + pipeline 兼容);缺/坏 best_ask/last/tol → fail-closed(ValueError/False);**Board 按值比较非身份**(跨类/reload 安全)
- [x] 数据层加**五档盘口取数**(best_ask=卖一):新增 `StockOrderbook` frozen(code+last+best_ask+best_bid+source+ts)+ `MarketDataService.get_stock_orderbook`(adata `get_market_five` s1/b1 primary / akshare `stock_bid_ask_em` sell_1/buy_1/最新 fallback;无正卖一/inf→落 fallback)+ `get_stock_realtime_dual`(双 spot 腿)
- [x] `Line1ContextProvider` 接 **双源实时**(last 双源 divergence≤0.3%/staleness≤5s,P0-8;best_ask 主源)经 `cage_bounded_buy_limit` 派生 limit_price;volume 经 `max_compliant_buy_volume()` 用 cage 限价**确定性重算**(单一构造点不破);build_lead_context 改 async
- [x] PIT:实时 spot+盘口 canonical-JSON 原始字节+checksum+signal_id 血缘存 `SnapshotStore`(best-effort)
- [x] `RiskEngine` check#02 加**命名 cage 子校验**(传入 frozen `CageQuote`=best_ask+source,无 IO;调 `is_within_cage`;折叠进 check#02 保 14-check 计数;先于 prev_close 带跑;缺 best_ask/board fail-closed;message 带 `price_cage_violation` 子原因)
- [x] **DEGRADED 非可执行**一等 outcome:无双源新鲜 quote/缺 best_ask/单源/divergent/stale/NaN backup → `Line1QuoteDegrade` → `Line1Outcome.QUOTE_DEGRADED` + `render_non_actionable_quote`(无 instruction_id/无回报模板/header「非交易参考·不可下单」),跳辩论篮子顺延,**绝不**在 last/T-1 收盘价上路由真 BUY
- [x] 修 `dry_run_realdata.build_data_layer`(`load_data_sources_config()` 缺 yaml_path → TypeError 被吞 → market_data 恒 None)+ 接 market_data/snapshot_store 进 dry-run/main Line-1 provider
- [x] amendments `P0-3`(limit 实时+cage)+`P0-7`(check#02 cage 子校验+cage_tolerance_pct 入 risk.yaml universe + Line-2 ADD/staleness 后续记录)+`P0-8`(Line-1 接实时主备+卖一+PIT)
- [x] TDD(cage 子校验/缺 best_ask→degrade + 5 降级路径 + PIT 持久化 + 双源 divergence/NaN backup/tz fail-closed)+ 门禁 **3947 passed/cov 90.51%(risk 99%)** + ruff + redline 全绿 + codex cycle1 1×P1 修(NaN backup)+ claude /code-review high(codex 撞额度回退)修 inf/列漂移/tz crash + commit 11b9d32 + plan.html done

### [x] U-E3 — 缺口5(**反转,2026-05-27 owner**):owner 报「成交价 + 股数」,**系统算含费成本价** ✅ feature 5feb27c
> **决策反转**:owner 只汇报**成交价(每股,不含费)+ 股数**;**系统替 owner 计算含费成本价**。
> owner 佣金 **万分之1.5(0.00015)、不足 5 元按 5 元、双向**;过户费/印花税为交易所标准(系统已建模)。
> amendment 已写:`docs/decisions/P0-4-amendment-2026-05-27-owner-reports-fillprice-system-computes-cost.md`。
- [x] FILLED 正则 **v2「成交价 + 股数」**(owner **不再填手续费**);`report_schema_version`(v1=legacy owner-fee FILLED;v2=人工 interactive 价+量,系统算费;v1 仅 FILLED 合法边界守门);recovery loader 按版本分支 + fail-closed 守门;`compute_idempotency_key` 含 version+成交价+股数(v2 fee=None 自然排除)
- [x] **系统算费**(复用 `cost_calculator.calculate_cost`,加 `apply_slippage_model=False` —— owner 上报价即真实 fill):`gross=价×量`;佣金=`max(gross×0.00015,5.0)`;过户费=`gross×0.0000341`(SZ_MAIN/CHUANGYE/159 ETF;沪市 0);印花税仅 SELL;BUY 现金=-(gross+佣金+过户费),成本价=该值/量;SELL 现金=+(gross−佣金−印花−过户费)。**v2 全经济量委托 `OrderCostBreakdown` 单一真相源**(codex/claude review 修 dual-gross 子分钱不一致)
- [x] applier feishu_interactive:BUY 持仓成本=加权平均 blend(复用 `_apply_buy`);SELL 已实现盈亏对加权均;两路径系统算费
- [x] **config**:`BrokerConfig.commission_rate` 0.0003→**0.00015** + `config/broker.yaml` 同步(`min_commission=5.0` 不变);runtime 不可改(P1-2.C/P0-7 §2 红线 1,amendment+重启)
- [x] 成本口径隔离:interactive(系统算费,无滑点)vs simulation_auto(滑点模型)by-construction 不同 + 账户生命周期隔离(模式切换 archive+reset,P0-1)→ 测试断言
- [x] 对账仍权威:系统算费与 owner 券商有差(优惠/返佣)→ 16:00 ticket fail-closed 三选一(P0-5)
- [x] 前端 `executionRegex.ts` 镜像同步(FILLED 价+量;vitest 一致)+ renderer 回报模板同步
- [x] amendments `P0-4`(已写,反转决策);commission_rate + 无滑点人工成本由 P0-4-amendment §2.2/§2.3 覆盖(未另起 P0-5/P1-2.C)
- [x] TDD(系统算费 BUY 买佣+过户 / SELL 卖佣+印花+过户 / 分板过户费 / min ¥5 floor + 加权均 blend + v1/v2 replay + 幂等含 version + 口径隔离 + 前端镜像 + v1-only-FILLED + recovery v2 守门)+ 门禁 3967 passed/cov 90.59%(risk 98.81%)+ ruff + redline + 前端 type-check/vitest(139)/build 全绿 + **claude /code-review high(codex 撞额度回退)**修 dual-gross+2 LOW → commit 5feb27c + plan.html done

### [x] U-E4 — 缺口3:飞书消息含判据(显眼 + 可量化 + 推理)✅ feature 5ca6ba8
- [x] `render_buy_signal` 加判据段:① 量化(score+各因子+为何入选 shortlist)② 推理(fund_manager reasoning + 3 分析师结论),逐条 `single_line()`+**长度截断**(≤160/≤120),纯文本无 markdown,**display-only**(永不进 parser/idempotency/risk);因子/评分 None 或非有限→`—(数据不足)` fail-closed
- [x] 经 `line1_runner._process_candidate→_build_buy_rationale→_route_candidate` 把 `CandidateRow.factors`(用 `dataclasses.fields` 派生)+ 辩论 `TeamState` 文本传进 `render_buy_signal` 参数(不进 InstructionPlan 字段)
- [x] 买卖信号最显眼:header/股数/限价 顶部 `▶` + `━` 分隔纯文本显眼块(`【` 不被误认头)
- [x] amendment `P0-3-amendment-2026-05-27-buy-signal-rationale-display-only.md`(模板扩展;防注入 + plain-text + display-only 不变量保留)
- [x] 抽 `text_safety.py`(`single_line`/`truncate` byte-identical)+ feishu 包隔离测试泛化扫全模块
- [x] TDD(判据渲染快照 + 防注入嵌入换行/控制符 + 截断 + display-only:AST 不可达 parser/幂等 + 不入 plan/risk_summary + volume 不变)+ 门禁 **3989 passed/cov 90.63%** + ruff + redline 全绿 + **claude /code-review high**(codex 撞额度回退)7 角度修 5 finding(docs/reviews/U-E4-codex-review-summary.md)+ commit 5ca6ba8 + plan.html done。**无前端改动**(判据出站,前端镜像入站正则未动)

### [ ] U-E5 — 缺口2:端到端双线测(出站真发 + 入站真回填)【owner-gated】 — (A) 无发送前置 done `0c2a8b6`,cond3 done `8739b11`+`7820338`,(B) cond4 真发待 owner
- [x] **(A) 入站 owner open_id allowlist**(feature `0c2a8b6`):新纯模块 `backend/integrations/feishu/inbound_gate.py`(`InboundGate` 三态 + `from_env` fail-closed)+ `main.py::_feishu_dispatch` 经 gate(DROP_NOT_OWNER 写 audit + return,不触 parser/applier)+ 启动期 fail-fast;amendment `P0-2-amendment-2026-05-27-owner-open-id-allowlist`。新 env = `FEISHU_OWNER_OPEN_ID`(owner open_id,逗号分隔多个)
- [x] **(A) 只读 `scripts/list_feishu_chats.py`** 实跑核对 OK:机器人在 2 群,`FEISHU_DECISION_CHAT_ID`→『QuantMind决策执行群』present、与告警群不同(`decision_is_alert=false`);零发送
- [x] **(A) 翻 PILOT cond5(outbox 重启幂等)/cond6(单路由无双执行)/cond7(全回报模板 parse-apply+AMBIGUOUS 不改镜像)/cond11(模拟态回滚演练)→ true**(test 签收;cond3/cond4 仍 false → PILOT gate 仍被拒)。新测试 inbound_gate(12)+list_feishu_chats(6)+pilot_cond_evidence(cond11 演练+ledger 锁)
- [x] **(A) 门禁 + 审查**:4009 passed/cov 90.58% + ruff + redline ALL PASS;claude /code-review high(codex 撞额度至 ~5-31)2 correctness 角度 clean;docs/reviews/U-E5-codex-review-summary.md
- [x] **(B 前置)cond3 dry-run unblocker(feature `8739b11`)**:开盘 5-28 dry-run 全 5 候选 quote_degraded → 诊断 akshare 备腿 `stock_zh_a_spot_em()` 全市场批量被 eastmoney 反爬持续重置(no_proxy 无效;3/3+5/5 实测);owner 5-28 决策**实时备腿换 Tushare sina** → amendment `P0-8-amendment-2026-05-28-tushare-sina-realtime-backup` + 改 `get_stock_realtime_dual` 备腿走 `ts.realtime_quote(src=sina)`(单标的、含 L1+5档、绕 eastmoney);claude /code-review high(codex 撞额度)7 维度 10 finding 全修(NaN→`_positive_or_none` fail-closed / `_to_tushare_ts_code` defer `classify_board` 收 universe-block / ValueError 分键 `dual_fallback_input_error` / lazy import / 4 处 operator-字串迁 "tushare-sina" / `now(UTC)` 同主腿语义 / `asyncio.gather` 并发 / `_ASIA_SHANGHAI`/`_BARE_CODE_RE` 重复删)。4048 passed(+10)/cov 90.58%/ruff+redline ALL PASS。
- [x] **(B 前置)cond3 真 BUY 渲出 owner 审通过 → 翻 cond3 `7820338`**:post-fix dry-run verdict PASS / line1_rendered=3 / real_sends=0 / cost_guard ¥0.19;3 BUY 605111(¥69.71/200)/600909(¥7.65/1900)/000725(¥5.71/2600)各 score 0.85+ + 5 因子 + fund_manager + 3 分析师 reasoning + 14-check 全过;RiskEngine 正当拒 002185(price_reasonability 21.69>板带 20.54);owner approved → 翻 `dry_run_double_line_pass: false→true`(evidence block 入 yaml header + 2 test ledger 同步)。**PILOT gate 仍因 cond4 false 拒**(预期)。
- [ ] **(B) owner 前置**:设 `FEISHU_INTERACTIVE_ENABLED=true` + `FEISHU_OWNER_OPEN_ID`(已 ✅ 5-27 设)+ go-live gate(剩 cond4 + 5 live-probe 含 owner auth)全过(注:设 INTERACTIVE 后 main.py 在 gate 未过时 SystemExit)
- [ ] **(B) 真发 1 条 BUY 到决策群**(发前必向 owner 明示内容+目标群拿确认)→ owner v2 模板回填 → WS(鉴权+allowlist)→ parser → ExecutionReportApplier(durable 幂等)→ MockBroker 镜像 → 对账(现金1元/量0%/成本0.01元)→ 给 owner 证据镜像前后+成本价+对账 → 翻 cond4

### [ ] U-D5 — 双线生产 e2e + redline 编排隔离 + SSoT 收官(回收)
- [ ] `test_mvp_e2e` 双线生产 e2e(0 真实网络 + 注入 fake)
- [ ] module-contract/redline-check 加 orchestration 隔离 + 单一构造点 + monitoring 隔离
- [ ] `backend/orchestration/CLAUDE.md`
- [ ] plan.html SESSION_LOG 收官 + 修订记录 + re-scope I-002
- [ ] PILOT 11 门齐后 owner 设 env(`QUANTMIND_PROD_RUN=1`/`OWNER_PROD_AUTHORIZATION=<id>:YYYYMMDD≤7天`/`QUANTMIND_FEISHU_TIER=pilot`/`FEISHU_INTERACTIVE_ENABLED=true`/`FEISHU_DECISION_CHAT_ID`)→ `can_switch_to_feishu_on("pilot")` 全过 → 次日 09:3x cron

## 4. 凭证现状
LLM 3(DEEPSEEK/DASHSCOPE/MOONSHOT)+ Tushare + 飞书 5(APP_ID/SECRET/VERIFY/ENCRYPT/ALERT_CHAT_ID)全 SET;
`FEISHU_DECISION_CHAT_ID`/`INTERACTIVE`/`PROD_RUN`/`OWNER_PROD_AUTH`/`FEISHU_TIER` 现全空(owner 设)。
**8 commit + 本批新 commit 累积未 push;push origin main 全部待 owner 授权。**

## 5. codex 3 轮评审要点(已落入设计)
- cage 必须进 RiskEngine(check#02 子校验,传 frozen quote,无 IO);缺 best_ask/单源/stale → degraded 非可执行,**绝不**用 last/T-1 收盘价路由真 BUY。
- BUY 现金用 per-fill 含费成本价 × 本次股数;持仓成本加权平均 blend;v1/v2 schema 版本化(不伪造 fee=0);interactive 含费口径与 auto 不含费口径**永不共存**(账户生命周期隔离 + 测试)。
- volume 仍确定性(cage 限价重算,单一构造点)。判据 display-only + 长度截断 + 不入 parser/risk。入站 lark-oapi 鉴权 + owner open_id allowlist + 幂等。cage 限价 floor 到 tick 后再校验。
