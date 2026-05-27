# 真实可交易性 MVP — 5 缺口 + Phase U 收尾(finalized plan, 2026-05-27)

> 本文件 = session #45 末提出的 5 个真实可交易性缺口的**已敲定执行计划 + 进度看板**。
> 经 plan mode 详细只读核查 + 3 轮 codex 评审 + 2 个 owner 决策(见下)敲定,owner 批准。
> **每完成一项就在本文件勾掉(`[ ]`→`[x]`)**;一任务一 feature commit,plan.html 回填真实 hash。
> 原始 SSoT = `docs/plan.html`(新增 U-E1..U-E5);红线 = `CLAUDE.md §2/§2.0`。

---

## 0. Owner 决策(已锁,2026-05-26/27)
- **缺口4 买入价呈现**:`参考价 + 建议区间 + 限价上限(价格笼子内)`;限价取实时盘口派生。
- **缺口5 BUY 回报字段**:`成本价(含费,每股) + 股数`(2 个数)——**按本次成交(per-fill)含费均价**解读,系统永不重算费率。

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

### [ ] U-E3 — 缺口5:回报模板 + set-from-report 记账 + schema 版本
- [ ] BUY 正则 v2「成本价(含费,每股) 股数」;`ExecutionReport` + EXECUTION_REPORT_APPLIED payload 加 `report_schema_version`(v1=fill_price+fee 旧/sim;v2=cost_price_incl_fee 人工 BUY);recovery loader 按版本分支;`compute_idempotency_key` 加 version+cost_price_incl_fee
- [ ] applier feishu_interactive 路径:`cash_delta=-(cost_price_incl_fee*filled_volume)`;持仓成本=加权平均 blend(复用 `_apply_buy`);**永不**重算滑点/费(P1-2.C 重算只留 simulation_auto)
- [ ] 成本口径隔离:interactive 镜像纯含费;simulation_auto 独立账户生命周期(模式切换 archive+reset,P0-1)→ 两口径永不共存(测试断言)
- [ ] SELL=成交价+股数(费在对账 true-up);per-fill 澄清文案「填写本次成交成本价(含费,每股),非持仓/摊薄成本价;券商仅显示持仓成本价时请选『待对账』」+ 同代码已有持仓告警(真实场景=Line-2 补仓)
- [ ] 前端 `executionRegex.ts` 镜像同步更新(vitest 一致)
- [ ] amendments `P0-4`(字段+版本)+`P0-5/P1-2.C`(interactive set-from-report 加权平均 vs auto 重算分流)
- [ ] TDD(per-fill cash+blend+v1/v2 replay+idempotency+成本口径隔离)+ 门禁(含前端)+ codex + commit + plan.html

### [ ] U-E4 — 缺口3:飞书消息含判据(显眼 + 可量化 + 推理)
- [ ] `render_buy_signal` 加判据段:① 量化(score+各因子+为何入选 shortlist)② 推理(fund_manager reasoning + 3 分析师结论),逐条 `_single_line()`+**长度截断**,纯文本无 markdown,**display-only**(永不进 parser/idempotency/risk)
- [ ] 经 `line1_runner._route_candidate` 把 `CandidateRow.factors` + 辩论 state 文本传进 `render_buy_signal` 参数(不进 InstructionPlan 字段)
- [ ] 买卖信号最显眼:header/股数/限价 顶部加粗式纯文本排版
- [ ] amendment `P0-3`(模板扩展;防注入 + plain-text 不变量保留)
- [ ] TDD(判据渲染快照 + 防注入 + 不入数值字段)+ 门禁 + codex + commit + plan.html

### [ ] U-E5 — 缺口2:端到端双线测(出站真发 + 入站真回填)【owner-gated】
- [ ] owner 前置:建决策群 + 设 `FEISHU_DECISION_CHAT_ID`/`FEISHU_INTERACTIVE_ENABLED=true`
- [ ] 真发 1 条 BUY 到决策群(缺口2/3/4 落地后)
- [ ] owner 按 v2 模板回填 → lark-oapi WS 接收(verify/encrypt token 鉴权)+ **owner open_id allowlist** → parser → ExecutionReportApplier(durable 幂等)→ MockBroker 镜像 → 对账(现金1元/量0%/成本0.01元)
- [ ] 给 owner 证据:镜像前后持仓 + 成本价 + 对账结果
- [ ] 翻 PILOT cond5(outbox 重启幂等)/cond6(无双重执行)/cond7(全回报模板 parse-apply)/cond11 证据
- [ ] TDD(e2e replay/idempotency)+ 门禁 + codex + commit + plan.html

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
