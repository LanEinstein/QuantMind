# QuantMind 跨 session 协作上下文

> **🚀 新 session 开工**:先读 [`docs/SESSION-KICKOFF.md`](docs/SESSION-KICKOFF.md)(通用开工协议:检查上次节点 → 开工改状态 → 完成改 done+记录 → 末尾一句话指下一步),按它无缝衔接。

QuantMind = 多 Agent 投研信号 + 模拟实盘 + 飞书人工执行。**永禁**真实券商程序化下单。
**当前状态(2026-06-21)**:决策对齐 ✅ + 旧 S/A-H/J/I/X 基础设施 ✅ + 双线重构 v2 **MVP(Phase K-N)2026-06-01 真启上线 ✅** + 双线投研逻辑 v3(V/W/Y/Z)+ 存量(O/Q/R/T)+ 模拟盘自动驾驶/自进化(AA-AE)**全 done(#60-#93)**。**当前 = owner 暂停运行,做『量化选股策略研究专项』**(用真实 A 股大历史推 OOS 可证盈利策略)。**round-1..4/R5 四轮框定已废(2026-06-21 owner 识破根本错误 = 框定错)**:把量化当『要跑赢 CSI300 的组合策略』优化 → 越做越增强指数 → round-4 那个 provisional PASS(锁定 test 超额 +2.68%)是 ≤5 持仓 top-N 系统**装不下、且仅 provisional(DSR 0.007/哨兵不过/前向未确认)的增强指数,不能上线**(历史记录 `docs/research/factor-strategy-round{1..4}-result*.md` + R5;R5 已证 +2.68% 一大半来自构造/size tilt 而非因子)。**纠正框架 = 量化第一闸门重做(QGR;`docs/research/quant-first-gate-rearch-plan-2026-06-21.md`,codex 评审 REVISE→定稿)**:量化 = 面对 ~5000 票的**第一道选股闸门**(输出预算内『优质+确定上涨』≤5 名+ETF 交 agent 二筛),非组合/指数跟踪器。**owner 4 决策**:判据=**绝对净盈+控回撤**(去 CSI300 超额硬门,仅披露)/ horizon=**两条腿并进**(先 5-10td 选股闸门,研究侧公平比 vs 真超短)/ **sim 暂停**直到 B 层前向确认(不赶 interim)/ 清理旧错内容。评测学=两层(A 可复用真 CPCV 竞技场+SPA/Romano-Wolf 公平比+累计 trial 账本含 legacy 块〔改判据**不清零** mining 债〕/ B 稀缺前向确认+go-live **真管线 shadow replay**)。代码全在 `scripts/factor_research/`,离线/确定性/LLM 零参与/import 隔离。**⏭️ NEXT = QGR-0 owner go → 落 amendment(判据改绝对净盈)→ QGR-1 摄取。**owner 重定位为**双线架构(全市场量化选股 + 持仓监控)+ 本地知识图谱 + 自进化多 agent**,在已建 backend 上演进。

> **研究专项已验证原则(owner 2026-06-20 锁定 + 2026-06-21 QGR 纠正,跨 session 强制;新验证的原则及时回写此处)**
> 0. **【最高优先,2026-06-21 QGR】量化 = 第一道选股闸门过滤器,不是组合/指数跟踪器**:前四轮把它当『要跑赢 CSI300 的组合』优化 = **框定错** → 越做越增强指数 → round-4 那个 provisional PASS 增强指数 **≤5 持仓 top-N 系统装不下、不能上线**。纠正:判据 = **绝对净盈 + 控回撤**(去 CSI300 超额硬门,仅披露);评测 = 两层(可复用真 CPCV 竞技场 + 累计 trial 账本含 legacy 块〔改判据**不清零** mining 债〕+ SPA/Romano-Wolf 多策略公平比 + 稀缺前向确认 + go-live 真管线 shadow replay);量化 = 面对 ~5000 票的第一道闸门(精度优先于覆盖)。详 `docs/research/quant-first-gate-rearch-plan-2026-06-21.md` + [[project-quant-first-gate-rearch-2026-06-21]]。**下面原则 1-6 仍有效但须套用本新框架;#4『test 第 N 次』/#5『benchmark 超额』口径已被本条 + 两层评测 + 绝对净盈判据取代。**
> 1. **反过拟合门 = 真预言,信它**:DSR≥0.95 主门 / PBO≤0.5 / SPA-vs-passive 三轮三次精准预测 OOS 失败;低 DSR 不是"门保守",是真脆弱 —— 开发证据 ≠ 判定。**round-4 精化(非推翻)**:DSR 0.007(四轮最低)+ 哨兵不过却 test PASS(超额 +2.68%)→ 低 DSR = **低置信**(此 PASS dev 证据四轮最弱 + 第 4 次评测),**不是门错**;脆弱策略可在单 OOS 窗口靠运气过线。真稳健须 **dev(高 DSR)+ test 双过**;**低 DSR 的 PASS = provisional,须前向窗口确认,不直接上线**。
> 2. **不闭门造车**:因子/策略从**前沿文献 + 牛人分享**汲取灵感(provenance-gated 记来源)→ 再严格验证;严禁拍脑袋造因子。**round-4 印证**:Lv 2025(分析师价值集中大盘段)先验 → 从零验符号 → test 兑现首个正超额。
> 3. **数据划分铁律**:**从零数学提取**新因子/权重 → 用既有 locked split(test 封存);**测试现成(已发表)策略** → 可用更广数据(全历史,不限 12 个月 test 窗口)做复现,**不烧 test 集**。
> 4. **测试集已评测 4 次**(round-1/2/3/4)→ 每多评一次 OOS 价值递减;**第 5 次极慎**。下一步判定**优先冻结新策略 + 等真前向窗口(test_end 2026-06-12 之后新增数据)做处子 OOS**(round-4 策略已 git 冻结 `ffc1db3`,正等前向确认),次选既有 test 第 5 次(须显式披露)。
> 5. **benchmark-gate 现实**:强势大盘年里长多增强指数要跑赢 cap 加权 CSI300,**必须有真·正交 alpha**;价值/质量/反转/SUE/应计(零成本财报衍生因子)证不够 → **分析师修正动量(信息流,`report_rc`:rev_diff/np_rev/tp_impl/cover_chg)在 round-4 兑现首个正超额(+2.68%,provisional)** = 前三轮缺的料。卖方覆盖偏大盘 → 正好补 cap 加权对齐缺口。
> 6. **Tushare `*_vip` 及多行/票端点单调用静默截断** → **必 limit+offset 分页**;coverage manifest fail-closed 兜底(详见 memory `reference-tushare-statement-vip-row-cap`)。
> 7. 研究全程:PIT/幸存无偏/无前视 + 字节存档+checksum + 仅 Tushare 官方 SDK + 离线 + LLM 只用于文献 + 永禁真实下单 + 不动 governance enum + codex 前置门 + 四门不放宽 + FAIL 报 FAIL。
SSoT = `docs/plan.html`(任务清单 + Session Log + 维护协议)。**总纲 = `docs/decisions/R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md` + v2 6 amendment(§2.0)+ v3 4 amendment(§2.0b)**;红线条文 → 对应 `docs/decisions/*.md` §2。

## 1. 项目进度管理协议(**强制,违反 = 违规实现**)

详细表格见 `docs/plan.html#protocol`,本节冲突以 plan.html 为准。

1. **开始前调查**:`grep` `docs/plan.html` 的 `#session-log` + `status: "doing"`/`status: "blocked"`,定位上次停在哪、在途任务。**确定本次活动 Phase**(最早一个含 `status="todo"` 的字母 Phase)以及该 Phase 内所有 `depends` 已满足的任务集 — **本次 session = 该 Phase 全套未阻塞任务,不是单子任务**(2026-05-12 用户锁定)。
2. **认领任务**:把该 Phase 当前可做的所有任务 `TASKS` 状态 `todo`→`doing` + 填 `session_date: "YYYY-MM-DD"`;依赖未完成改 `blocked` + `notes` 写 `blocked_by:`。
3. **完成任务**:每个任务自己一次 feature commit 后,状态改 `done` + 回填 `commit:`(真实 hash 7 位) + `notes:`(What+Why 1-2 句)。一个 commit / 一个任务,git 历史按任务粒度可读。
4. **结束 session**(Phase 全部 done 或确实无法继续后才结束):一次性 docs-only commit 把 `SESSION_LOG` 顶部追加一条覆盖整个 Phase 的 `{date, session: "#N phase-X full", owner, state_in, actions(每任务 + commit hash 枚举), commit, next}`;"修订记录"加一行。**不要**每个任务都跟一次 docs-only commit。
5. **改决策边界**:先新增 `docs/decisions/*-amendment-YYYY-MM-DD-{原因}.md`,再改代码;无 amendment 的行为差异 = 违规。

> 报告"完成"前:SSoT 必须已改 ✅ + 真实 commit hash + 报告里写改了什么。只改 localStorage 不算项目进度。

## 2. 核心红线(违反即停;细则见 `docs/decisions/`)

**2.0 双线重构 v2 amendment 总览(2026-05-24,总纲 R0)** Owner 有意推翻 4 条 + 新锁 2 条;安全地基(永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / RiskEngine 纯函数 / 人工 gate)**全留**。下面 §2.3/2.4/2.5/2.10/2.12 已内联 amendment 指针。
- **推翻 4**:P0-9 13 标的→全市场筛(`P0-9-amendment-2026-05-24`)/ P0-7 +budget-adaptive+concentration_exception(`P0-7-amendment-2026-05-24`)/ P0-8 MiroFish 加分项→Line1 建议核心仍 evidence-only(`P0-8-amendment-2026-05-24`)/ P0-10 4 固定→可进化团队+≥2 交易员(`P0-10-amendment-2026-05-24`)/ P2-2 deferred→主动发现+知识图谱(`P2-2-amendment-2026-05-24`)/ P1-7 事后→真·预留+fan-out cap(`P1-7-amendment-2026-05-24`)。
- **新红线 2**:① **PIT 数据可复现** — 全市场 `MarketDataSnapshot` 存**原始字节**+checksum(仿 BrokerSnapshot,严禁 hash-only)+ coverage manifest + SignalInputManifest(消费行血缘)+ 复权因子 artifact pin + 离线 bit-exact `replay <signal_id>`;回测/shadow/实时三路径同源。② **InstructionPlan 单一构造点** — 仅 `instruction_plan_builder` 可构造(`grep "InstructionPlan(" ⊆ {model, builder, tests}`);`side/volume/limit_price` 确定性派生,**永不**来自 LLM JSON;4 可写文本字段永不解析进数值订单字段(import 隔离证明不了字段值来源 → 升 provenance-clean + 对抗测试先写)。
- **MVP = Phase K+L+M+N** 双线端到端 on 快照(无自进化/无 MiroFish 核心/无全异动栈)。经 Codex 2 轮红队(Conditional GO)。**✅ 2026-06-01 真启上线。**

**2.0b 双线投研逻辑 v3 amendment 总览(2026-06-01,#60 规划)** MVP 上线后新增 4 方向,各一 amendment(实施前置门;**安全地基红线全留**:永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / RiskEngine 纯函数 / 单一构造点 / PIT 可复现 / fail-closed)。设计依据 `docs/research/line-strategy-rearch-design-draft-2026-06-01.md` + 3 轮 codex 对抗。**实施 ship 序 V→W→Y→Z**(amendment-first):
- **③ ≤5 槽轮动(Phase V,ship-first;`P0-7-amendment-2026-06-01-five-slot-rotation`)**:check#6 `max_total_positions` 10→5(含 ETF;V-001)+ 确定性 `backend/slot_portfolio/`(纯量化、import 隔离、不构造 InstructionPlan)+ append-only `RotationIntent` + 在位『独立够弱』AND 挑战『margin 胜出』双条件 + subcap≤1 让位保护性止损 + T+1 跨日(今卖次日买真实空槽,不当日回笼)+ 到期 fallback 防『卖了没买回』。
- **② 持仓 thesis(Phase W;`P0-10-amendment-line2-2026-06-01-position-thesis-advisory`)**:`PositionThesis` 持久化(LLM 支柱文本 + **确定性**量化失效阈值;显式落库非反查,根除 P-006 脆弱性)+ 阶段1 LLM advisory 盘后复盘(新 EOD cron,evidence-only + display-only 飞书,owner 人工执行,**monitoring 仍零 LLM**)→ 阶段2 确定性 `THESIS_QUANT_BREAK`(仅白名单量化模板派生,**只增卖压、永不放松现有止损**)。
- **① 产业链倒推(Phase Y,最复杂放最后;`P0-8-amendment-2026-06-01-llm-theme-research-peer-sourcing`)**:LLM 联网调查 = **定时·留痕(provenance 捕获)·人工 pin 的 peer-sourcing 层**,**量化仍资格权威**(过排除四件套+可负担+14-check+人工 gate),纯量化路径始终可跑;**结构化 5 步倒推 prompt SOP**(明确如何调查/分析;骨架 frozen,措辞/exemplars 经 P2-2 可进化)+ 文件式版本化 registry;新 `THEME-` 第 6 evidence 前缀;主题配额 ≤cap−3、保 ≥3 量化名额;**严禁 LLM 进运行时数据路径 / 剪 universe / 否决板块**。
- **④ 前端(Phase Z,贯穿;`P1-5-amendment-2026-06-01-dual-line-panels-and-page-reconcile`)**:新 viz 全做现有页内 panel/tab;如实修订页锁漂移(doc 11→实建 13 页 / WS 12→14 类,Z-001 reconcile);仅 2 写端点不变、全只读、127.0.0.1。

**2.1 模式(P0-1)** simulation_auto(always-on)+ feishu_interactive(可叠加);唯一开关 `FEISHU_INTERACTIVE_ENABLED`。旧 `AUTHORIZATION_MODE`×`QUANTMIND_PHASE` 矩阵 Phase A 一次性破坏式删除;grep 必空。切换 = 账户生命周期事件(归档 + MockBroker reset + 飞书初始化对账 + 期间冻结)。

**2.2 LLM 权限(P0-10)** 可写仅 4 类:`InstructionPlan.reasoning` / `evidence_collection.content` / `agent_debate_records.{reasoning_text,conclusion}` / `risk_parameter_proposals.proposal_text`。严禁写决策字段 / `RiskCheckSummary` / `evidence_id` 命名 / MockBroker / `WatchlistPolicy` / `DataQualityState` / `ReconciliationTicket` / `AcceptanceReport` / `RiskConfig` / 飞书消息文本。单调用 30s + 0 重试;LLM 全停 1h 系统中断;¥100/日 hard 全 LLM 暂停(2026-05-26 P1-7-amendment ¥20→¥100,见 §2.10)。严禁参与回报解析 / 对账 / 验收 / 数据质量 / RiskEngine。Pydantic strict + `extra="forbid"` + lint 三层守门。

**2.3 多 Agent 守门(P0-10)** 4 必经 Agent `fundamental_analyst` + `technical_analyst` + `risk_officer` + `fund_manager`,任一缺失即降级 HOLD。`fund_manager` 唯一 BUY/SELL/HOLD 倡议者;`debate_round_count ≥ 1` 必经;`fund_manager_shadow_baseline` 永不入决策路径。双层守门:Builder 五道早返 + RiskEngine 14-check 独立并行。 **(2026-05-24 P0-10-amendment:4 必经保留 + 新增 ≥2 交易员 agent 人格卡 frozen git;fund_manager 仍唯一倡议方向;LangGraph 编排,RiskEngine/Builder 作纯节点 LLM 无边可写;InstructionPlan 单一构造点见 §2.0。)** **(2026-05-25 P0-10-amendment-line2:§2.3 = Line-1 LLM 选股路径;Line-2 持仓监控为独立**确定性零 LLM** 路径,SELL/ADD 方向由确定性 AnomalyDetector/AddPositionEvaluator 派生不经 fund_manager/辩论,经 builder 新增 `assemble_monitoring_plan` 确定性构造(单一构造点不破)+ 5 早返 + RiskEngine 14-check + 飞书人工;`debate_round_count=1`=确定性监控评估轮,`signal_id` 用 `LINE2-MON-` 前缀供 audit 区分。)** **(2026-05-26 P1-7-amendment:Line-1 每日 09:35 cron 按 `CandidateSelector` shortlist **顺序多候选辩论** —— 每只 `build_lead_context→run_shortlist(单元素辩论)→assemble_plan(单一构造点)→RouteCoordinator`,RiskEngine REJECTED/HOLD/DEGRADED/非-BUY 即 fallthrough 下一只,VALIDATED BUY 收集(basket,收齐所有);推翻旧"只辩 top-1 lead"/"一次辩论每日";fan-out 受 `max_debates_per_day`(默认8)+ 日 ¥100 真·预留双重 fail-closed bound;篮子额外受熔断 ≤5单/日 + check-10 bound。lead 涨停被拒=RiskEngine 正确履职→辩下一只,绝不绕过拒单。)**

**2.4 风险 / 配置 / 路由(P0-7 / P0-9 / P1-5)** `RiskConfig` / `agent_models.yaml` / `watchlist_policy.yaml` / `broker.yaml` runtime 不可改 + hot-reload 全禁;修改 = git diff + amendment + 重启。`backend/api/{risk,watchlist,llm,agents,cost}*.py` 仅 GET;全后端仅 2 写端点(`POST /api/execution-reports` + `POST /api/reconciliation-tickets/{id}/decide`)。`backend/risk/` 严禁 `import backend.{llm,agents,mirofish,data}`,纯函数无 IO。仓位三连 单股 ≤15% / 总仓 ≤70% / 单次 ≤5 万;熔断 ≤5 单/日 + 日亏 -5% + 连亏 3 笔 + 60min 冷却(SELL 不熔断);universe 沪深主板+创业板+ETF,禁 ST/科创/北交/可转债,禁涨停 BUY / 跌停 SELL,long-only。universe = 全市场主板+创业板+ETF 规则集(13 标的已废→全市场);排除四件套前移 `screening` 硬排除 + Builder 第五道早返兜底;**≤5 并发持仓**(check#6,`P0-7-amendment-2026-06-01`;V-001 实施 `max_total_positions` 10→5,§2.0b ③)。 **(2026-05-24 P0-9-amendment:13 标的 → 全市场主板+创业板+ETF universe 规则;排除四件套前移 `screening` 硬排除,Builder 早返保留为最后防御;科创/北交/ST/可转债仍永禁。P0-7-amendment:+budget-adaptive 分层 Micro<¥2k 仅 ETF / `NO_COMPLIANT_TRADE` 一等 outcome / ETF `concentration_exception` RiskEngine 独立再校验;Normal≥¥10k 三连不变。)**

**2.5 数据情报(P0-8)** 主备 staleness ≤5s / divergence ≤0.3% / freshness ≥60s。全 watchlist 30s 个股快照;多域 5 源(财经 2 + 时政 1 + 全球 2),跨域不去重。MiroFish 加分非核心:事件 cap=1 + 17:00 复盘双路径;输出仅入 `evidence_collection`(MIROFISH- 前缀)不入 `RiskCheckSummary`。`evidence_id` 前缀 `NEWS-`/`MIROFISH-`/`MARKET-`/`RISK-`/`DEBATE-`(2026-06-01 `P0-8-amendment` 新增 `THEME-` 第 6,主题研究 peer-sourcing,§2.0b ①)。 **(2026-05-24 P0-8-amendment:MiroFish 升 Line1 建议核心,仍 evidence-only by-construction;确定性 `CandidateSelector` 出候选,MiroFish 仅合格集内有界重排 ≤1 分位、永不否决板块、top-N 后保 ≥3 量化名额、缺席量化兜底。PIT 数据可复现见 §2.0 新红线①。**数据源(P0-8-amendment-2026-05-24-tushare-data-source,K-001 落地)**:新增 Tushare Pro 全市场扫描层 = 官方 Python SDK only(`ts.pro_api`,严禁 MCP/skill 进运行时数据路径);adata/akshare 实时主备 + 新闻 5 源不变 + akshare/baostock/adata 兜底;`TUSHARE_TOKEN` 异质凭证不入 LLM 3+飞书 5 池(EXPECTED_POOL_SIZE 仍 8),数据成本不设 ceiling。模块 0 `backend/marketdata_snapshot/`(K-001..K-006 done)= 存原始字节+checksum / coverage / 消费行血缘 / 复权 pin / 离线 replay,纯模块零 backend.* 子包 import。)**

> **Tushare 可获取数据类型清单(2026-06-19 实测确认;现有 `TUSHARE_TOKEN` ≈5000+ 积分档)** —— 数据源仍**仅官方 SDK** `ts.pro_api`(严禁 MCP/skill/adata/akshare 进研究 PIT 路径);IPv4-only 出站;`TUSHARE_TOKEN` 不入 LLM 3+飞书 5 池;真摄取须 PIT 字节存档+checksum+coverage(同 K-001)+ owner-gated。完整探测记录 = memory `reference-tushare-entitlements-2026-06-19`。
> - **✅ 行情/微结构**:`daily`/`weekly`/`monthly`/`adj_factor`/`daily_basic`(已摄取)+ `stk_limit`(涨跌停价)/`limit_list_d`/`suspend_d`(停复牌)/`moneyflow`(个股资金流)/`moneyflow_hsgt`/`hsgt_top10`/`hk_hold`(北向持股)/`top_list`(龙虎榜)/`margin`+`margin_detail`(融资融券)/`block_trade`(大宗交易)。
> - **✅ 财务报表(PIT ann_date,vip 全市场)**:`fina_indicator_vip`(已摄取)+ `income_vip`/`balancesheet_vip`/`cashflow_vip`(利润/资产负债/现金流量)/`forecast_vip`(业绩预告)/`express_vip`(业绩快报)/`fina_mainbz_vip`(主营构成)/`fina_audit`(审计意见)/`dividend`(分红送股)/`disclosure_date`(披露计划)。
> - **✅ 参考/治理(PIT)**:`stock_basic`(已摄取)+ `namechange`(曾用名/ST 史 → 可做 PIT ST 排除)/`share_float`(限售解禁)/`stk_holdernumber`(股东人数)/`stk_holdertrade`(增减持)/`repurchase`(回购)/`stk_managers`(管理层)。
> - **✅ 指数/行业/基金**:`index_daily`/`index_weight`(成分权重,已摄取)/`index_member_all`+`index_member`(成分,已摄取)/`index_classify`(申万分类)/`fund_basic`/`fund_nav`/`fund_portfolio`。
> - **✅ 概念**:`ths_index`(同花顺概念)。
> - **✅ 分析师/筹码(2026-06-20 owner 充值到 8000 积分后实探解锁)**:`report_rc`(券商分析师盈利预测/评级,2016+,按 `report_date` PIT;`eps`/`np`/`tp` 目标价/`rating` → **分析师修正动量,round-4 头号正交 alpha 源**)/ `cyq_chips`(筹码分布)/ `stk_factor_pro`(技术因子 pro)/ `ccass_hold`(中央结算持股)。完整 8000 档权限图 = round-4 R4-1 真探针任务(docs.qq.com 积分权限表 JS 渲染取不到→真探针权威)。
> - **❌ 仍不可用 / 已变更**:`concept` 端点名已变更→用 `ths_index`;>8000 档(如部分另需单独申请的端点)按需 R4-1 探。

**2.6 飞书(P0-2 / P0-4)** 永禁 HTTPS 回调入站;事件订阅仅 `lark-oapi` WebSocket(3s ack);`tenant_access_token` 仅内存。备用 webhook 仅发系统告警,**绝不**发买卖 / 对账 / 澄清。纯文本 + 严格正则;不通过 = AMBIGUOUS 绝不更新 MockBroker;严禁猜 `instruction_id`。所有飞书消息必经 `renderer.py`(防 prompt injection)。

**2.7 InstructionPlan / 账本(P0-3 / P0-4 / P0-5 / P1-2.A-C)** `instruction_id` 严格 `^QM-\d{8}-\d{6}-\d{6}-(BUY|SELL|HOLD)-\d{3}$`。HOLD 永不路由不发飞书;`parse_ok=False` 强制 HOLD;状态机 `DRAFT→VALIDATED→DISPATCHED→FILLED/EXPIRED/REJECTED/AMBIGUOUS`,`model_copy(update={"status":...})` 违规。核心结构全 frozen Pydantic v2 strict + `extra="forbid"`。16:00 系统主动发对账(`RECON-{YYYYMMDD}-{seq}`);阈值 cash 1 元 / volume 0% / cost 0.01 元;超阈值 ticket fail-closed 三选一。**5 种买卖冻结独立并行**:切换 / OPEN ticket / 熔断冷却 / DataQualityState / EOD pipeline;永禁聚合 `frozen=true`。MockBroker 单一镜像,覆盖必经 `ReconciliationApplier::reset_to_snapshot`,直接 mutation `_cash`/`_positions`/`_trades` 违规。撮合 ALL_OR_NONE + 涨跌停 at-fill recheck(`price_limit_violation_at_fill` ≠ engine `limit_up_block`)+ 分板块滑点 1.5/1.5/3.5/1.5 bp + 深市过户费 0.00341% 双边。持久化 hybrid delta + EOD snapshot + checksum 失败拒自动恢复 + append-only 8 红线。30s intraday MTM + per-position `EquityPoint`;三级回退 Redis≤60s → Mongo≤300s → degraded;禁 cost_price fallback。

**2.8 验收(P0-6)** 45 交易日滚动 + 静态 `config/holidays.yaml`(不引入 akshare 节假日 API)。5 稳定性 指令完整率 ≥95% / 回报准确率 ≥99% / 数据缺失 ≤1% / LLM 超时 ≤5% / 信号生成 ≥95%;3 策略 最大回撤 ≤8% / PnL ≥0 / 沪深 300 累计超额 ≥0。5 类 P0 系统级中断重置;reconciliation freeze 暂停而非重置。切换 `feishu_on` 必经 `acceptance.can_switch_to_feishu_on()`,严禁 env/CLI 绕过。

**2.9 安全 / 可观测(P1-6 + P0-2-amendment-2026-05-16)** LLM 3 key + 飞书 5 凭证(`FEISHU_APP_ID`/`FEISHU_APP_SECRET`/`FEISHU_VERIFY_TOKEN`/`FEISHU_ENCRYPT_KEY`/`FEISHU_ALERT_CHAT_ID`)全 `~/.bashrc`;**P0-2 amendment 2026-05-16 锁定:owner 飞书租户禁用 custom-bot,凭证池 6→5,告警通道走自建应用同款 OpenAPI 发到 FEISHU_ALERT_CHAT_ID;`FEISHU_CUSTOM_BOT_*` 永禁存在,出现即 warning + audit**。`.env` 严禁 `LLM_KEY/FEISHU_*/FEISHU_ALERT_*` 前缀;启动期 `secrets_validator` fail-fast。fingerprint = SHA256[:8],严禁 plaintext / 末四位;5 类强制轮换 + 12 月 warning。全层 127.0.0.1 only(Backend + Vite + Mongo + Redis + Nginx),远程仅 SSH tunnel;不加本机 auth middleware;httpx 出站 `local_address="0.0.0.0"`(IPv4 only egress)。Mongo `audit_events` TTL 180 天 + JSONL 30 天双写,34 类锁定(类 5 evolution 7 类 actor=SYSTEM/SCHEDULER);LLM 严禁写 audit;调试事件走 `logs/quantmind.jsonl`。gitleaks pre-commit 强制,严禁 `--no-verify`。

**2.10 成本预算(P1-7)** LLM only:日 ¥100 hard(唯一全 LLM 熔断;2026-05-26 amendment ¥20→¥100)+ 月 ¥440 soft(50/80/100% 三节点,100% 不停;不变)+ Kimi 日 ¥4(不变)。数据 / 运维不设 ceiling。`cost_guard` + `SoftDegradeManager` 严禁 `import backend.{llm,agents,mirofish,data}`。软触发 ¥70=70% 优先关 Kimi escalation;严禁削减 4 必经 Agent / 降 fast 频次 / 全 deepseek-only。告警仅飞书 + audit + Phase B 成本拆解面板;严禁 SMTP / Slack / Discord;Alerter dedup_15min。 **(2026-05-24 P1-7-amendment:¥/日 hard 从事后 trailing-stop 升**真·预留**(preflight 预留+拒超+对账)+ `max_debates_per_day` + per-stage retry cap + 所有 LLM 含 Line-2 异动写同一 `llm:usage:{utc_date}` 计数器。**2026-05-26 P1-7-amendment**:日 hard ¥20→¥100(唯一熔断阈值上调;soft 0.7 比例不变=¥70;月 ¥440 + Kimi ¥4 不变);**推翻"一次辩论/每日 shortlist 非 per candidate"** → Line-1 按 shortlist 顺序**多候选辩论**(每候选 1 次 4-agent,REJECTED→fallthrough 下一只),fan-out 改由 `max_debates_per_day`(默认 8)+ 日 ¥100 真·预留双重 fail-closed bound 承担;收集语义=basket(收齐所有 VALIDATED BUY,额外受熔断 ≤5单/日 + check-10 bound)。)**

**2.11 前端(P1-5 + P1-5-amendment-2026-06-01)** 页锁/WS 数 **如实修订为实建枚举集**(doc 11→13 页 / WS 12→14 类,Z-001 reconcile;再加顶级页/WS 类仍需 amendment);决策闭环 4 分组;Simulation.vue 留代码不进菜单;删的 2 类 `auth_mode_change`/`approval_update` **永禁**;SSE 仅 LLM 流式;双线新 viz 全做现有页内 panel/tab(§2.0b ④)。用户回报双路径同 `ExecutionReportApplier`,前端 JS 正则镜像与 `regex_patterns.py` 单一真相源,不一致 fail-closed。三层 reason 抽屉 3 tab(Builder / Engine / Broker)命名空间区分。前端不存任何**凭证**到 `localStorage`/`sessionStorage`/`cookie`(UI 偏好如 focusMode 不违规)。

**2.12 自进化(P2-2;deferred 已解除 → Phase R/Q/Y)** 启用 3 路径:DSPy GEPA 离线 prompt(≤¥5/次)+ RAG provenance-gated 白名单(arxiv/semanticscholar/openreview/白名单 GitHub releases/akshare changelog)+ FinMem exemplars(≤3/prompt)。严禁 7 路径:fine-tune / online learning / RLHF / DPO / continual SFT / 自动 mutate config / 新 LLM provider / LLM 自动决策权。全人工 gate + 飞书主动通知;shadow 45 日完全沿用 P0-6;文件式 prompt registry + git + restart;严禁 MLflow/LangSmith。BrokerScheduler 第五 cron `evolution_shadow_run` 22:00 mon-fri;Phase A/B 实施期严禁写任何自进化代码。 **(2026-05-24 P2-2-amendment:deferred 解除 → Phase R 主动策略发现 + 知识图谱(SQLite+NetworkX,双时态+SUPERSEDES)+ `LiveArtifactRegistry`(startup 只认批准哈希,对抗测试先写);人工 gate + 45 日 shadow + 飞书 + git+restart 不变;7 禁不变。MVP(K-N)已上线 → 自进化代码在 Phase R/Q;① 主题 prompt 的进化接口(文件式 registry + GEPA/exemplars)见 §2.0b。)**

## 3. 工程原则

- 注释 / commit 英文;UI 文档中文;public function 必须 type hints + docstring(WHY 而非 WHAT)。
- 不可变结构优先(frozen dataclass / NamedTuple / Pydantic frozen);文件 200-400 行典型 / 800 上限;函数 <50 行;嵌套 <4 层。
- 测试金字塔 + ruff 全绿才允许 commit;非 risk >70%,risk >95%。**测试通过 ≠ 闭环可用**(audit 反面教材 1139 绿但 RiskEngine 不接订单;断言要覆盖被谁调用、贯穿到哪)。
- Codex review = **代码任务强制前置门禁**(2026-05-24 用户锁定,推翻 2026-05-12 手动-only):**但凡有代码编写的任务,commit 之前先跑 codex-review,修复完所有 P0/P1/P2(CRITICAL/HIGH/MEDIUM)bug 后再 commit + push**。本地门禁(pytest + ruff + redline-check + 前端 type-check + vitest 全绿)是前置必要条件但不充分;codex-review 是 commit 前的最后一道门。按 codex-review skill 跑(1 cycle 起步,major 用户可指定 5 轮 R1-R5,输出 `docs/reviews/{task_id}-codex-review-summary.md`),修完 P0/P1/P2 后再提交。**例外**:docs-only / 配置文档 / SSoT 记账 commit 不需 codex("有代码编写的任务"才触发)。codex CLI 不可用时须上报 owner 拿决策,**严禁**静默跳过推未审代码。绿测试 ≠ commit-safe(印证 [[feedback_codex_findings_real]])。
- fail-closed for data corruption / fail-open for infra glitches;完整升级路径优先,不为省工作量妥协可用性。

## 4. 重要文档

| 路径 | 用途 |
|------|------|
| **`docs/plan.html`** | **实施 SSoT — 任务清单(S/A-X + K-N MVP + v3 V/W/Y/Z + 存量 O/Q/R/T)+ Session Log(权威下一步指针)+ 维护协议** |
| **`docs/decisions/R0-two-line-rearch-...-2026-05-24.md`** | **双线重构 v2 总纲 + 2 新红线(PIT 可复现 / 单一构造点);先读** |
| `docs/decisions/` | 决策 + amendments(R0 + v2 6 amendment + v3 4 amendment 2026-06-01);红线细则 §2 + §2.0 + §2.0b |
| `docs/research/` | 调研 dossier(v2:冷启动+回测 / agent 架构 / 知识图谱+异动;v3 2026-06-01:产业链倒推 / thesis+T+1 轮动 / line-strategy 设计草案) |
| `docs/quantmind_project_audit_2026-05-07.md` | 早期审计(双线重构前;历史参考) |
| `docs/reviews/` | codex review + 阶段 summary |
| `~/.claude/projects/-home-ps-papers-QuantMind/memory/MEMORY.md` | 跨 session 自记忆索引 |
| `~/.claude/rules/` | 全局规范 |

## 5. 操作速查

```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000
cd frontend && npm run dev   # :9276(Phase A 后必锁 127.0.0.1)
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70
pytest -q backend/risk --cov=backend/risk --cov-fail-under=95
cd frontend && npm run type-check && npm run test -- --run && npm run build

# 红线扫描精选(完整版见 plan.html#gates)
grep -rn "AUTHORIZATION_MODE\|QUANTMIND_PHASE\|live_confirm" backend/                          # Phase A 后必空
grep -rnE "@router\.(post|put|patch|delete)" backend/api/                                       # 仅留 2 写端点
grep -rn "from backend\.\(llm\|agents\|mirofish\|data\)" backend/risk/ backend/services/cost_guard.py
grep -rnE "host\s*[=:]\s*['\"]?0\.0\.0\.0" frontend/vite.config.ts deploy/                       # 必空
grep -rnE "DEEPSEEK_API_KEY|DASHSCOPE_API_KEY|MOONSHOT_API_KEY|FEISHU_" .env .env.example       # 必空
grep -rnE "FEISHU_CUSTOM_BOT_(WEBHOOK_URL|SIGN_SECRET)" backend/ tests/ .env* 2>/dev/null      # 必空 (P0-2-amendment-2026-05-16)
# 双线重构 v2(2026-05-24)新红线扫描
grep -rn "InstructionPlan(" backend/ | grep -vE "models/instruction\.py|instruction_plan_builder\.py"   # 必空 (R0 单一构造点;tests 除外)
grep -rnE "total_codes.*13|watchlist_size_must_equal" config/                                   # Phase L 后必空 (P0-9-amendment 全市场)
grep -rn "import backend\.\(llm\|agents\|mirofish\)" backend/screening/ backend/budget_policy/ backend/candidate_selector/ backend/marketdata_snapshot/  # 必空 (纯量化隔离)
```

LLM key:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。
飞书:`FEISHU_APP_ID` / `FEISHU_APP_SECRET` / `FEISHU_VERIFY_TOKEN` / `FEISHU_ENCRYPT_KEY` / `FEISHU_ALERT_CHAT_ID`(告警群 open_chat_id;P0-2-amendment-2026-05-16 锁定:owner 飞书租户禁用 custom-bot,告警通道走自建应用同款 OpenAPI 不走 webhook;`FEISHU_CUSTOM_BOT_*` 永禁存在)。
