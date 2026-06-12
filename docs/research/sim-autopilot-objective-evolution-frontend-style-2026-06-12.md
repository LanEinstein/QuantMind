# 模拟盘自动驾驶 + 客观自进化 + 前端驾驶舱 + 风格分型 — 设计 dossier(2026-06-12,session #76)

> owner 三项需求的调研与可实操开发规划。经 1 轮 codex 红队对抗(本文 §5 全文收录)+ owner 4 项 AskUserQuestion 拍板。
> 配套 4 份 amendment(本文 §6)+ plan.html 新 Phase AA/AB/AC/AD。
> 安全地基红线全留:永禁真实下单 / 飞书人工执行(实盘模式)/ 127.0.0.1 / LLM 不写决策 / RiskEngine 纯函数 / InstructionPlan 单一构造点 / PIT 可复现 / fail-closed。

---

## 0. owner 原始需求(2026-06-12)

1. **模拟盘双职责**:① 用模拟初始资金全自动交易,测试系统实战能力;② 作为整个系统能力**自进化的试验场**。交易时间内完全系统自动(选股/买卖/仓位管理),非交易时间(盘后/周末/节假日)复盘与自进化。自进化必须基于真实模拟盘数据做针对性调整(策略优化/更新、LLM harness 优化、阈值调整),**这些调整不靠用户或开发者确定,尊重客观表现**。
2. **前端同步推进**:模拟盘全自动的各项操作、指标、数据要在前端富有逻辑性地展现。当收益率/年化/跑赢指数较高 = 系统具备实盘能力;切到实盘+飞书模式后,前端给用户留快捷填表空间记录用户实际操作(尤其用户自主止盈止损的卖出),按钮逻辑精细设计;用户填表后飞书同步。
3. **风格分型**:系统判断个股适合短线(核心是数学)还是价值投资。价值投资 = 三层筛选(底层:符合大势与主线;中层:资金认可+题材容量;表层:多逻辑共振 ≥2 核心逻辑+基本面支撑/弹性)+ 题材四级赋权(1级国家事件/社会大势 > 2级政策支持 > 3级技术利好 > 4级个股利好)。前端与飞书消息明确标注短线/价值。

## 1. 代码事实基线(2026-06-12 调查,3 并行 agent)

### 1.1 模拟盘自动执行(需求 1 的"交易时间全自动"已基本存在)
- 纯 simulation 模式(`FEISHU_INTERACTIVE_ENABLED=false`)下 VALIDATED InstructionPlan 经 `RouteCoordinator.route()` → `SimulationExecutor.route()` → `MockBroker.place_order()` **全自动成交,无任何人工环节**(`backend/services/simulation_executor.py:99-247`)。Line-2 保护性 SELL 同样自动执行。HOLD 永不路由。
- 撮合:ALL_OR_NONE、涨跌停 at-fill 复核、分板块滑点、过户费(`backend/broker/mock_broker.py:215-317`)。
- **真正的缺口**:
  - a) 周末/节假日**零 job**(全部 cron trading-day/hours gated;`backend/broker/scheduler.py:214-264` 共 9 cron)→ "非交易时间复盘与自进化"基础设施缺失;
  - b) `evolution_shadow_run` 22:00 cron 是未接线 no-op(callback 未 wire,scheduler.py:382-390);
  - c) 16:00 不发对账(`initiate_reconciliation` 从未接线,reconciliation_initiate.py 注释自证);纯 sim 下镜像即权威,对账应转为**自完整性校验**;
  - d) **自进化晋升全程人工 gate(P2-2 红线)**——与"尊重客观表现、不靠用户/开发者确定"直接冲突(本次 amendment 推翻,sim 范围)。

### 1.2 前端现状(需求 2 的底盘)
- 13 页 4 分组;Performance 页已有 annualized/sharpe/drawdown/win_rate + HS300/SZ50/CYB 对标(`backend/api/performance.py:138-209`);AccountBanner 有总资产/PnL/30日 sparkline。
- acceptance 8 门(5 稳定性+3 策略)45 交易日滚动 + `can_switch_to_feishu_on()` 已实现(`backend/services/acceptance_report.py`),前端有 AcceptanceReports 页但**无"实盘就绪度"总览 gauge**。
- 写端点恰 2 个;回报录入(ExecutionReportEntry.vue)必须挂已存在的 `QM-` instruction_id,**无用户自主交易录入**;前后端 9 正则镜像字节相等(executionRegex.ts ↔ regex_patterns.py)。
- WS 14 类锁定;无短线/价值标签概念。
- ⚠️ codex 指出:performance.py 现有曲线部分由成交净额派生,作"实盘就绪证据"太弱,必须改以 EquityPoint(30s/EOD MTM)为账户真相源。

### 1.3 选股/风格/题材现状(需求 3 的底盘)
- Line-1 排序 = 纯 5 因子(momentum_20d 0.40 / ma_ratio_5_20 0.25 / volatility_20d 0.20 倒置 / avg_amount_20d 0.15 / rsi_14 仅展示)——**天然短线数学栈**;资格 = 排除四件套+流动性+IPO 龄等 9 项硬门 fail-closed(`backend/screening/`)。
- 题材层(Phase Y 已建):5 步倒推 SOP + provenance 全捕获 + 人工 pin + 配额 ≤2/5 + ≥3 量化保留(`backend/theme_research/`,`backend/candidate_selector/selector.py`)。SOP step① 已区分 macro/policy/tech 拐点——**四级赋权的 tier 分类有现成挂点**。
- PositionThesis(Phase W)= "长持"雏形:pillars + 确定性失效阈值(ANCHOR_DRAWDOWN/TIME_STOP/SCORE_DECAY)+ time_stop_trade_days + intact thesis 止盈豁免(硬止损永不豁免)——**style 字段的天然容器**。
- KG 12 节点/17 边含 TREND/CHAIN_LINK/DRIVES/UPSTREAM_OF(Y-001)——共振计数与主线匹配的图基础已备。
- 4 必经 agent + fund_manager 唯一倡议;fund_manager 只见 3 分析报告+辩论记录+确定性 volume/price 提案。

## 2. owner 拍板(AskUserQuestion,2026-06-12)

| # | 决策点 | owner 选择 |
|---|--------|-----------|
| 1 | 模拟盘自进化晋升机制(P2-2 人工 gate) | **客观判据全自动晋升(sim 范围)**:确定性 ObjectivePromotionEngine,飞书只通知不等批准,失败自动回滚;安全地基参数永久冻结不可进化;实盘/feishu 模式晋升仍人工 gate |
| 2 | shadow 验证窗口(统一 45 日) | **分层窗口+样本量双门槛**:阈值参数 ≥15td 且 ≥30 触发样本;prompt/harness ≥20td 且 ≥15 笔独立成交;新策略代码 45td 不变;晋升必须过 bootstrap CI(现有 ShadowChain 已算 CI,接上判定) |
| 3 | 前端用户手动操作填表(仅 2 写端点) | **新增第 3 写端点** `POST /api/manual-trades`,独立 ExternalExecutionEvent 域模型;红线改"仅 3 写端点枚举锁定" |
| 4 | 价值线排序权威(量化资格权威/题材配额) | **统一硬门+受约束槽位分配**:单一量化资格硬门 → 确定性 StyleClassifier → 价值槽 ≤2/纯量化 ≥3(维持现配额),价值槽内三层得分排序;配额本身列可进化参数交客观晋升调整 |

## 3. 目标架构

### 3.1 需求 1:模拟盘自动驾驶 + 客观自进化(Phase AA + AB)

**日历(Asia/Shanghai)**:
```
交易日  09:35 Line-1 选股(既有) / 30s Line-2 触发(既有) / 16:00 EOD+自动对账(AA-001)
        17:00 MiroFish 复盘(既有) / 17:30 thesis 复盘(既有) / 18:00 当日归因复盘(AA-002 新)
        22:00 进化实验 run(R-004 接线 + AB)
周六    10:00 周度深复盘 + 实验规划(AA-003 新)
周日/节假日  补跑窗口(若周六失败)+ 离线回测/参数搜索长任务(AA-003)
每日(若有晋升)08:30 受控重启激活窗口(AB-003;距 09:35 有 65min 缓冲 + 健康断言)
```
**周末/节假日 lane 的 ops 门(全部满足才跑,否则 skip+audit)**:无 OPEN 对账票 / 最近 EOD snapshot 有效 checksum / artifact store 干净 / 磁盘余量 / LLM 预算余量 / 数据新鲜度门 / 距下一开盘 ≥2h 不做激活。

**归因复盘存储(ReviewRecord,AA-002)**:append-only,facts-first(逐笔:entry/exit 价 vs 当日 VWAP、触发器命中/吞没、滑点、持有期收益、所属 policy_hash、style);"错过信号"反事实默认 **non-promotable**,仅预注册信号(实际产出过的 HOLD/被拒单)可入晋升证据——防后见之明偏差(codex P2-6)。

**客观晋升闭环(AB)**:
```
实验生成(22:00/周末;GEPA prompt 变体 / 白名单参数网格 / 策略变体)
  → ExperimentRegistry append-only 登记(含全部失败实验;晋升门槛随试验次数校正,防多重检验)
  → shadow 对照(challenger vs incumbent,同 PIT 输入,反事实 replay + rqalpha 差分 oracle〔R-002〕)
  → ObjectivePromotionEngine(确定性):分层窗口+样本量双门槛+bootstrap CI 显著
     + acceptance 8 门不劣化 + 回撤不恶化 + anti-gaming 门(最低敞口/最少信号数/换手边界)
  → PromotionIntent(append-only)+ activation manifest(内容寻址,含全套 artifact 哈希)
  → next_boot.lock 原子切换 → 08:30 受控重启(systemd 外部 supervisor)→ 开机哈希/健康断言
     (失败 → 自动回退上一 manifest;git 事后镜像激活记录,git 不作运行时控制面 — codex P0-4)
  → 晋升后 incumbent 继续 shadow 跑(live counterfactual 基线)→ K 日回归劣化 → 自动降级
     (降级只影响未来入场;持仓按 entry_policy_hash 沿用入场时卖出栈,退役 artifact 永久保留可回放 — codex P0-5)
  → 飞书通知(已晋升/已降级,display-only,不等批准)
```

**冻结不可进化集(frozen non-evolvable,AB-005;违者 = 红线违规)**:
- 安全地基全部:永禁真实下单 / 飞书人工(实盘) / 127.0.0.1 / LLM 权限 4 类 / 单一构造点 / PIT / RiskEngine 纯函数;
- 仓位三连(15%/70%/¥5万)/ ≤5 槽 cap / 熔断 4 件 / 预算 4 常量(¥100 日 hard 等)/ universe 排除四件套 / 14-check 存在性与语义 / 对账阈值 / 模式开关。

**可进化白名单(每参数带 immutable clamp 区间,clamp 本身不可进化)**:
- 触发器系数:atr_stop_mult、r_multiple、time-stop 天数、drawdown 分位参数、强势卖出族阈值(**safety-adjacent:能推迟 SELL 的参数更紧 clamp + 沿用"止损只紧不松"单调约束**);
- selector 因子权重(归一约束);风格槽位配额(价值槽 0..2 整数域);题材 tier 权重(序约束:tier1≥tier2≥tier3≥tier4);
- prompt 措辞/exemplars(≤3)/SOP 措辞(骨架 frozen 不变)——prompt artifact 按"政策 artifact"对待:禁类 lint + 请求/响应字节捕获 + replay record(codex P0-3)。

**绩效按 policy 分段(AA-004,codex P0-1)**:EquityPoint/acceptance/performance 全部挂 `policy_hash`(activation manifest 哈希);晋升即开新段;前端曲线可按段查看 + 全程拼接(标注切换点);"实盘就绪"判定以**当前段**为准——避免混血曲线不可复现。持仓铭牌:`entry_policy_hash` + style + 卖出栈版本。

### 3.2 需求 2:前端驾驶舱 + 实盘交互(Phase AD,Z 先行)
- **AD-001 就绪度与 KPI**:以 EquityPoint 为真相源(非成交净额派生);KPI 头部(总收益/年化/HS300 超额/回撤/夏普)+ acceptance 8 门 gauge + `can_switch_to_feishu_on` 状态;短窗(<45td)年化降权显示并标注样本天数/数据质量 flag;曲线按 policy_hash 分段。
- **AD-002 自动驾驶时间线**:今日链路(筛选→候选→辩论→指令→成交→Line-2 触发→盘后复盘)逐事件时间轴,全只读,复用现有 WS/轮询。
- **AD-003 进化面板**:ExperimentRegistry/shadow 对照(challenger vs incumbent 曲线)/PromotionIntent 历史/当前 activation manifest;全只读。
- **AD-004 风格标签**:InstructionPlans/Portfolio/飞书消息统一 badge(短线⚡/价值🏛);标签来自 AC 的确定性分类,display-only。
- **AD-005 手动操作填表(仅 feishu_interactive 模式可见)**:
  - 新域模型 `ExternalExecutionEvent`(**绝不伪造 InstructionPlan**,codex P0-6):external_trade_id、code、side、volume、price、executed_at、reason 枚举(用户止盈/用户止损/用户加仓/其他)、free-text note、`origin=USER_DISCRETIONARY`;
  - `POST /api/manual-trades`(第 3 写端点):严格 schema + 可卖量 clamp(100 整手、T+1 可用)+ 确认步;经独立 applier 进 MockBroker 镜像(同 `apply_external_fill` 语义)+ 对账可见;
  - 飞书同步:renderer 渲染"**已记录**"语义消息(绝无下单动词,对抗 parse 必 no_pattern_match),发决策群;
  - **绩效三分流(codex P0-7)**:`system_suggested` / `user_discretionary` / `reconciliation_reset`;实盘就绪度、自进化评分**只读 system_suggested 段**——防把用户 alpha 学成系统能力;
  - 按钮逻辑:持仓行内"记录卖出"预填 code/可卖量;建议卡上"已按建议执行/已自主调整"两路;偏离建议的数量自动落 deviation 日志(沿用 report-is-truth 先例)。

### 3.3 需求 3:风格分型 + 三层筛选价值线(Phase AC)
- **AC-001 StyleClassifier(确定性,买入时定型)**:输入 = 量化因子谱(动量/波动/弹性)+ 三层得分 + thesis 可派生性;输出 style ∈ {SHORT_TERM, VALUE};落 PositionThesis.style + 持仓铭牌;同输入同输出可 replay。
- **三层筛选得分(全确定性 PIT 特征 + 人工 pin 的题材 artifact,LLM 不进运行时路径)**:
  - **底层·大势主线**:pinned THEME artifact 覆盖(候选在 pin 链/板块内)+ 板块动量分位 + regime(既有确定性 regime 通道);
  - **中层·资金认可+容量**:事件后异常收益(event-study,事件=pinned EVENT/THEME 节点日期)+ 板块成交额/换手分位 + 自由流通市值 + Amihud 非流动性 + 北向/主力资金分位(仅 PIT-safe 源);
  - **表层·共振+弹性**:KG 逻辑共振计数(**独立 evidence family 计数**,同一 LLM run 的重复引用只算 1,codex P1-4)+ 基本面支撑(tushare fundamentals,**按公告日 PIT**,codex P1-5)+ 弹性(beta/振幅/自由流通占比);
  - **四级赋权**:THEME SOP schema 扩 `theme_tier` 字段(1 国家事件/2 政策/3 技术/4 个股;LLM 给 tier 建议,**人工 pin 时确认 tier**)→ 三层得分中题材项乘 tier 权重(1.0/0.75/0.5/0.25,config 化白名单可进化,序约束)。
- **AC-005 受约束槽位分配**:统一资格硬门(不变)→ 全体候选过 StyleClassifier → 价值槽 ≤2、纯量化 ≥3(沿用现配额语义)→ 价值槽内按三层得分排序、短线槽按现 5 因子排序;`peer_sourced=None` 及价值槽空时与现状 bit-identical。
- **风格不变式(codex P0-8,对抗测试钉死)**:style 只影响**软层**(止盈带/time-stop/复盘节奏/展示);硬保护止损(ATR/drawdown)、熔断、仓位三连、14-check **风格不变式**;VALUE 风格沿用既有 thesis-gated 止盈豁免机制(其本身就不豁免保护性止损)。

## 4. 与现有 Phase 的关系
- **Phase Z(双线前端 reconcile)不变,仍先行**;AD 在 Z 之后做增量。
- **Phase R**:R-002(rqalpha 差分 oracle)成为 AB 的硬前置;R-003 的"人工 amendment + 飞书批准"语义**在 sim 范围被 AB 客观晋升替代**(实盘模式保留);R-004(22:00 cron + sub-budget)由 AB 接线吸收。R-003/R-004 任务 notes 已加 amendment 指针。
- **Phase O(MiroFish)**:板块涨概率可作底层"大势主线"的 evidence 增强,非 AC 硬前置。
- **Phase T**:交易员人格卡/exemplars 进化产物走 AB 同一客观晋升门。
- **推荐 ship 序**:Z(轻量)→ AA → R-002 → AB → AC → AD(AD-001/002 可与 AB 并行先做只读部分)。

## 5. codex 红队记录(2026-06-12,1 轮,全文要点)

> 结论:"Proposal A(自进化)是真正危险的一个——它把'产出候选 artifact 等人工激活'改成'系统自主改变生产模拟盘策略',必须按红线 amendment 对待而非小扩展。Proposal B 可行当且仅当手动交易建模为外部真相事件而非伪 instruction。Proposal C 健全当且仅当价值风格仍受量化资格门控且不放松硬卖出保护。"

**P0(全部吸收)**:
1. 自动晋升污染就绪度证据 → 绩效按 policy_hash 分段(→AA-004);
2. 纯天数 shadow 在 ~1 笔/日下统计无效 → 样本量门槛 + bootstrap CI(现有 ShadowChain CI 被判定忽略,接上)(→AB-002;owner 拍板);
3. prompt 进化是间接 LLM 自治泄漏 → prompt = 政策 artifact:禁类 lint + 字节捕获 + replay(→AB-006);
4. 自动 git commit + 自动重启是错误激活原语(脏树/绕审/竞态) → PromotionIntent + 内容寻址 manifest + 原子 next_boot.lock + 外部 supervisor 重启 + 开机断言;git 仅事后镜像(→AB-003);
5. 回滚对持仓欠规定 → 持仓记 entry_policy_hash/style/卖出栈版本;降级只影响未来入场;退役 artifact 永久保留(→AB-004);
6. 手动交易不得伪造 InstructionPlan(USER- 伪 id 会毒化决策账本与验收指标) → 独立 ExternalExecutionEvent 域(→AD-005);
7. 手动交易必须与系统绩效分流(否则进化把用户 alpha 学成系统的) → 三分流 segmentation(→AD-005/AA-004);
8. 价值风格不得放松硬退出 → 风格不变式 + 对抗测试(→AC-006)。

**P1(吸收)**:双池改"统一硬门+受约束分配"(owner 拍板);题材配额 ≤2 暂不放开(配额列可进化参数,拿实证后由客观晋升调整);可进化白名单要硬 clamp + safety-adjacent 分类(能推迟 SELL 的参数);资金认可/容量的确定性 PIT 特征菜单(event-study/换手分位/ADV/Amihud/北向);基本面按公告日 PIT;前端就绪度用 EquityPoint 真相源 + 短窗年化降权;飞书同步用"已记录"语义。

**P2(吸收)**:ExperimentRegistry 记全部失败实验(防多重检验机器);晋升后 incumbent 续跑 shadow(live counterfactual);anti-gaming 门(最低敞口/最少信号/换手边界——空仓策略回撤好看);周末 job ops 门;**MockBroker 过拟合是真实激励回路** → shadow 评估用更严苛成交假设(涨跌停不成交/ADV 容量上限/价格冲击/延迟成交/陈旧报价拒绝)(→AB-007);ReviewRecord facts-first,反事实默认 non-promotable。

## 6. amendment 清单(本 session 落定,实施前置门)

| amendment | 推翻/新增 | 对应 Phase |
|---|---|---|
| `P2-2-amendment-2026-06-12-sim-objective-promotion.md` | 推翻"全人工 gate + 统一 45 日 shadow + 7 禁之'自动 mutate config'"(**仅 sim 范围**;实盘人工 gate 不变);新增冻结不可进化集 + 可进化白名单 + 客观晋升判据 + 激活/回滚机制 | AB(+AA-004) |
| `P1-2.A-amendment-2026-06-12-sim-autopilot-review-crons.md` | 新增 18:00 归因复盘 cron + 周末/节假日复盘实验 lane + 16:00 sim 自动对账接线(BrokerScheduler 新 cron 族,ops 门) | AA |
| `P1-5-amendment-2026-06-12-manual-trade-third-write-endpoint.md` | 写端点 2→3(枚举锁定):`POST /api/manual-trades`;ExternalExecutionEvent 域;绩效三分流;飞书"已记录"同步 | AD-005 |
| `P0-8-amendment-2026-06-12-style-classifier-and-value-line.md` | 风格分型 + 三层筛选价值线:统一硬门不变;受约束槽位分配(价值 ≤2/量化 ≥3);THEME SOP 扩 theme_tier;四级赋权权重;风格软层不变式 | AC |

## 7. 风险与缓解(汇总)
| 风险 | 缓解 |
|---|---|
| 进化对 MockBroker 怪癖过拟合 | AB-007 严苛 shadow 成交假设 + rqalpha 差分 oracle(R-002)交叉校验 |
| 多重检验:海量实验总有"显著"赢家 | ExperimentRegistry 全记录 + 晋升门槛随试验次数校正(deflated Sharpe / Bonferroni 风格) |
| 晋升-回滚震荡 | 晋升冷却期 + auto-demote 用 incumbent live counterfactual 而非绝对收益 + K 日观察 |
| 自动重启把服务搞挂 | 外部 supervisor + 开机哈希/健康断言 + 失败自动回退上一 manifest + 08:30 窗口距开盘 65min |
| 用户 alpha 污染系统能力评估 | 绩效三分流;就绪度/进化评分只读 system_suggested |
| 价值线放松风控 | 风格不变式对抗测试;硬止损/熔断/三连/14-check 风格无关 |
| LLM 经 prompt 进化间接获得决策影响 | prompt artifact 禁类 lint + 字节捕获 + 同一客观晋升门 + 骨架 frozen |
