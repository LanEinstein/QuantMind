# 研究 Dossier:持仓"投资逻辑(thesis)"追踪与衰减监控 + ≤5 槽位 T+1 轮动

> 外部调研 · 2026-06-01 · 面向 QuantMind 持仓监控层(Line-2)与组合轮动规划 session
> 仅供规划参考,**不改任何红线**。约束前提全程坚持:决策层确定性 / LLM advisory-only(永不写决策字段)/ A股 T+1 + 项目自定"不假设当日回笼"/ ≤5 槽位。
> 关联既有文档:`docs/research/a-share-trading-rules-2026-05-27.md`(T+1 broker 层已建模)、`docs/decisions/R0-two-line-rearch-...-2026-05-24.md`(单一构造点 + PIT 可复现)、Phase P 配比/篮子记忆。

---

## TOPIC A — 持仓 thesis 追踪与衰减监控

### A.1 业界如何把"投资逻辑"表示为结构化、机器可读对象

核心结论:成熟做法把每个买入决策固化成一份**结构化 thesis 记录**,其灵魂不是"看好的理由",而是**预先写死的"逻辑失效判据(invalidation points / kill criteria)"**——一组可观测、可量化的条件,一旦触发即认定逻辑破坏。这正好对应 owner 的 "逻辑还在 → 扛波动;逻辑破坏 → 减/清"。

**被反复引用的具体框架要素**(综合多份从业框架 datatobrief / investogy / eqvista):

1. **Thesis = 3-5 条支柱(pillars / key drivers)**:每条是"必须为真才能让这笔投资成立"的命题。买入即枚举这些支柱(对 QuantMind:行业链逻辑 + 量化因子 + agent 辩论结论)。
2. **Invalidation checklist(失效清单)/ kill switch**:为每条支柱配 3-5 个可量化指标 + 阈值,"一旦突破即认定 thesis 失效"——形成**客观卖出纪律**而非情绪反应。例:churn 升破 10%、某因子 z-score 跌出区间、行业景气指标转向。
3. **Catalysts + Timeline**:预期催化剂 + 兑现时间窗。逻辑没破但**催化迟迟不来 / 时间窗过期**本身就是一种软失效(time-stop)。
4. **观测事实 vs 推断假设 分离**:框架强调把"已观测事实"和"推断/假设"分栏,后续 re-check 时只需验证假设是否被新事实证伪。
5. **Pre-mortem(事前验尸)**:买入前写"假设这笔投资半年后亏了 30%,最可能的原因是什么"——把失败路径前置成监控项。
6. **Decision journal(决策日志)**:记录买入时的预测 + 推理,持续追踪 KPI(用户增长/毛利/FCF 等)对照实际,形成 2-3 年反馈回路。每个 top 基金经理对每个仓位维护成文 thesis。

> 关键洞察:**结构化的不是"理由文本",而是"失效条件"**。理由文本是 LLM 可写的自由字段;失效条件必须是确定性可判定的(数值阈值 / 布尔条件)。这与 QuantMind §2.2 "LLM 只写 reasoning 文本,决策字段确定性派生"完美对齐。

来源:
- [How to Build an Investment Thesis: A Professional Framework — DataToBrief](https://www.datatobrief.com/blog/how-to-build-investment-thesis-framework)
- [8 Investment Thesis Example Frameworks — Investogy](https://blog.investogy.com/investment-thesis-example/)
- [Investment Thesis — Eqvista](https://eqvista.com/investment-thesis/)
- [thesis-validation skill — LobeHub(把 thesis 拆成 observed facts / inference / assumption + invalidation checklist 的 LLM skill 范式)](https://lobehub.com/skills/marian2js-trading-skills-thesis-validation)

### A.2 Thesis decay / alpha decay / 信号陈旧——检测方法(定性 + 定量双路)

QuantMind 想要的是 (a) 定性逻辑 re-check(LLM/新闻驱动)+ (b) 定量漂移检测 的混合。业界方法分两族:

**(b) 定量漂移 / alpha decay 检测**(确定性,可入决策路径):

- **Minimum Regime Performance (MRP)**(Alexander & Fabozzi 2026):衡量策略在"结构不同的市场 regime"间的稳健度,惩罚"高衰减倾向"的策略——类比尾部风险度量。可作 thesis 健康分的一个维度。
- **Information Coefficient (IC) 衰减监控**:对产生 thesis 的量化因子,滚动跟踪 IC / rank-IC;IC 由正转负或显著衰减 = 因子边缘消失的早信号。
- **滚动表现 vs 基线**:持有期内个股相对行业 / 相对沪深300 的累计超额由正转负、跌破买入时设定的 catalyst 兑现轨道。
- **信号陈旧度(staleness)**:动量类信号典型寿命约 10 个月后转负;均值回归 1-5 天(日内)/ 3-10 天(波段)。陈旧度 = 距信号产生的时间 / 该信号类型的半衰期。
- **统计漂移 / 异常**:PRR、ROR、Bayesian 校验、GBM/RF 模式识别用于量化信号恶化(来自药物警戒迁移到量化的统计族)。对 QuantMind 更轻量的等价物:因子分布漂移检测(z-score 越界、PSI/KL 散度)、价格行为突变(已有 Line-2 确定性 AnomalyDetector)。

**(a) 定性逻辑 re-check**(LLM/新闻驱动,**advisory-only**):

- LLM 拿"原始 thesis 支柱 + 失效清单 + 最新证据(新闻/财报/MiroFish 因果链)"做**逐条 re-check**,输出"每条支柱仍成立 / 受威胁 / 已破坏"+ 推理文本。
- 严格约束(QuantMind 必守):LLM 输出**只进 evidence/advisory 字段**(`evidence_collection.content` + `agent_debate_records.reasoning/conclusion`),**绝不**直接置 SELL。是否触发减/清由确定性规则层判定。

来源:
- [Strategy Decay Detection — VertoxQuant(MRP 框架,正文付费墙;摘要可用)](https://www.vertoxquant.com/p/strategy-decay-detection)
- [Signal Decay Analysis: Understanding Alpha Lifecycles — microalphas](https://microalphas.com/signal-decay-patterns/)
- [Alpha Decay: what does it look like — Maven Securities](https://www.mavensecurities.com/alpha-decay-what-does-it-look-like-and-what-does-it-mean-for-systematic-traders/)

### A.3 开源 / GitHub:决策日志、thesis 追踪、持仓 rationale

| 仓库 | 关注点 | stars | license | 可移植性 |
|---|---|---|---|---|
| [`Shweddy/the-investor360`](https://github.com/Shweddy/the-investor360) | Obsidian 投资日志 + Claude 当分析师:earnings ingestion、**thesis trackers、risk registers、decision journals** | 1 | none(无许可证→默认保留所有权利,不可直接复用代码,**仅借鉴 schema/工作流**) | 思路对口,代码不可拿;借鉴其 thesis tracker / risk register 字段结构 |
| [`marian2js trading-skills / thesis-validation`](https://lobehub.com/skills/marian2js-trading-skills-thesis-validation)(LobeHub skill) | 把 thesis 拆 observed facts / inference / assumption + 产出 **invalidation checklist**,标记缺失输入/弱证据 | n/a(skill) | 见原 repo | **最贴 A.1 范式**——可直接借鉴其 prompt 结构做 LLM thesis re-check skill(advisory-only) |
| `thakursaksham147s/Notion-Template`(Stock Market Beginner OS) | Notion 投资纪律模板:watchlist、研究页、mistake journal、investment rules | low | n/a(模板) | 仅纪律模板参考,无代码 |
| 通用交易日志(`trading journal` 一类,如各种 web app) | 记录每笔交易理由/复盘 | 杂 | 杂 | 多为 UI app,与 QuantMind 后端解耦需求不符;不推荐移植 |

> 现状判断:**没有一个高星、可直接移植的"持仓 thesis 追踪"后端库**。该领域成熟的是"框架/模板/prompt 范式"而非"现成代码"。结论:QuantMind 应**自建轻量结构化 thesis 模型**(借鉴 A.1 字段 + investor360/thesis-validation 的 schema),而非引入依赖。

### A.4 架构:advisory(LLM 感知/证据)vs deterministic(规则做卖出决策)

这是 2026 年 agentic-trading 文献的明确共识,且**正是 QuantMind 已采用的架构**——外部研究给了它学名与背书:

- **两层工作记忆模型**(arXiv 2605.19337 *Agentic Trading: When LLM Agents Meet Financial Markets*):
  - **Layer A — Deterministic State Store**:持仓、挂单、余额、风险限额的 ground truth。"**应对 LLM 只读,仅由环境(EMS/OMS 回报)更新**。把这个 state 当作可遗忘/可幻觉的 memory 是关键漏洞。"→ 对应 QuantMind 的 MockBroker 镜像 + RiskConfig + DailyTradingState。
  - **Layer B — Generative Context**:LLM 的观测与推理轨迹,transient、可被淘汰。
  - **Taxonomy**:确定性 = 账户/持仓/风险限额/成交;LLM 驱动 = 感知综合 / 候选动作生成 / 推理论证;混合 = 对 LLM 输出做确定性约束校验(`holds < max_leverage` 这类 validity check 在动作进入执行前确定性拦截)。
- **Determinism-Faithfulness Assurance Harness (DFAH)**(arXiv 2601.15322 *Replayable Financial Agents*):度量 trajectory determinism + evidence-conditioned faithfulness;发现"决策确定性与任务准确率不显著相关"——即**确定性是为了可审计/可复现,而不是靠它提升收益**。背书 QuantMind §2.0 PIT 可复现 + 离线 replay。
- **TrustTrade**(arXiv 2603.22567):human-inspired selective consensus 降低 LLM 交易决策不确定性——高共识信号优先、弱接地/时间不一致输入被折扣。对应"thesis re-check 多 agent 取共识"。

> "LLM as perception/evidence, rules as actuator" 不仅有先例,已是 audit-oriented agentic-trading 的**推荐范式**。QuantMind 的 Line-2 确定性路径(AnomalyDetector / AddPositionEvaluator 派生方向,LLM 仅写 evidence/reasoning)= 这套范式的标准实现。

来源:
- [Agentic Trading: When LLM Agents Meet Financial Markets — arXiv 2605.19337](https://arxiv.org/abs/2605.19337)
- [Replayable Financial Agents (DFAH) — arXiv 2601.15322](https://arxiv.org/pdf/2601.15322)
- [TrustTrade: Selective Consensus — arXiv 2603.22567](https://arxiv.org/pdf/2603.22567)
- [LLM Agent in Financial Trading: A Survey — arXiv 2408.06361](https://arxiv.org/pdf/2408.06361)

### A.5 QuantMind 推荐方案(TOPIC A)

1. **新增 frozen `PositionThesis` 结构(机器可读)**,在 Line-1 BUY 落地时由 builder 写入(单一构造点不破),字段建议:
   - `pillars: list[ThesisPillar]`——每条含 `kind`(industry_chain / quant_factor / agent_rationale)、`claim_text`(LLM 可写 advisory)、**`invalidation: list[InvalidationRule]`(确定性:metric + comparator + threshold)**。
   - `catalysts + time_window`(time-stop:窗口过期标记 thesis 软失效)。
   - `entry_snapshot_ref`——pin 买入时的 PIT 快照/因子值(对齐 §2.0 复现红线),供后续 drift 对比。
   - `entry_rationale_text`——LLM advisory 自由文本(只读追溯,不入决策)。
2. **确定性 ThesisHealthEvaluator(Line-2 节点,零 LLM)**:逐条评估 invalidation rule(因子漂移 / IC 衰减 / 相对超额转负 / time-stop)→ 输出 `intact / weakening / broken` + 命中的具体规则。**这是减/清方向的唯一确定性来源**,经现有 AnomalyDetector / AddPositionEvaluator 同款路径派生,过 5 早返 + RiskEngine 14-check + 飞书人工。
3. **LLM thesis re-check = advisory 叠加**:借鉴 thesis-validation skill 的 prompt 范式,逐支柱判"仍成立/受威胁/破坏"+ 写 `evidence_collection`(新前缀如 `THESIS-`,需 §2.5 evidence_id 前缀 amendment)+ `agent_debate_records.reasoning`。**永不**置 SELL;只在确定性层已判 weakening/broken 时,作为飞书消息里给 owner 的人读判据。
4. **失效优先级**:定量硬失效(因子/价格越界)= 强信号直接进减/清评估;定性软失效(逻辑受威胁但量价未破)= 仅升级监控频率 / 飞书提示,不自动减仓——避免 LLM 噪声驱动换手。
5. 落地前置:决策边界变化(新 thesis 结构 + Line-2 新评估器 + evidence 前缀)须先写 `docs/decisions/*-amendment-YYYY-MM-DD-thesis-tracking.md`(§1.5 红线)。

---

## TOPIC B — ≤5 槽位 + T+1 的组合轮动("卖一买一")

### B.1 Turnover-aware / 替换式组合构造:新候选何时"够格"顶掉最弱持仓

**最直接、最贴 QuantMind 的确定性先例 = qlib `TopkDropoutStrategy`**(microsoft/qlib,MIT,>16k stars——基础设施级背书)。它就是固定 N 槽位 + 排名替换 + 内建反 churn 缓冲的标准实现:

- 参数:`topk`(组合持有数量,对 QuantMind = 5)、`n_drop`(每次再平衡最多替换几只)、`method_sell`(`bottom`=卖最低分)、`method_buy`(`top`=买最高分)、`hold_thresh`(**最短持有天数**,卖前校验 `get_stock_count(stock) >= hold_thresh`)、`only_tradable`(过滤不可交易标的)、`risk_degree`(资金使用率)。
- **排名缓冲(hysteresis)= 顶掉旧仓的"margin"机制**——核心是它的候选选取:
  ```python
  today = get_first_n(
      pred_score[~pred_score.index.isin(last)].sort_values(ascending=False).index,
      self.n_drop + self.topk - len(last),
  )
  sell = last[last.isin(get_last_n(comb, self.n_drop))]  # comb = last+today 重排
  buy  = today[: len(sell) + self.topk - len(last)]
  ```
  含义:把"当前持仓 + 候选"合并重排,只有当某新候选**排名高到能把某持仓挤进 last-n_drop**才发生替换。等价于"**挑战者必须以排名 margin 击败在位者**",而不是分数略高就换——这正是 owner 要的"卖一买一"且防抖。
- **换手率确定性可控**:`turnover ≈ 2 × n_drop / topk`。QuantMind 取 topk=5、n_drop=1 → 每次最多换 1 只,换手 ≈ 40%/再平衡周期,天然限制 churn,且贴合"卖一买一"语义。

**替换打分(replacement score)的一般做法**(综合 alphaarchitect / Quantpedia 动量轮动族):
- 候选与在位者**用同一打分尺**(CandidateSelector 的量化分 + Line-1 辩论结论质量),挑战者分数须 ≥ 在位者分数 + **buffer δ** 才触发替换(no-trade band 的排名版)。
- rank-based rotation:持有 top-K,只有当某持仓**掉出 top-(K+buffer)**才卖(而非掉出 top-K 即卖)——延迟卖出,显著降换手且收益几乎不损。

来源:
- [qlib TopkDropoutStrategy 源码 signal_strategy.py — microsoft/qlib(MIT)](https://github.com/microsoft/qlib/blob/main/qlib/contrib/strategy/signal_strategy.py)
- [qlib 文档 Portfolio Strategy / TopkDropout(turnover=2·Drop/K)](https://qlib.readthedocs.io/en/stable/component/strategy.html)
- [TopkDropoutStrategy 详解 — Vadim's blog](https://vadim.blog/qlib-portfolio-strategy)
- [Performance v. Turnover: A Story by 4,000 Alphas — arXiv 1509.08110](https://arxiv.org/pdf/1509.08110)

### B.2 T+1 结算下的轮动时序("卖今天,settled cash 明天买")

**A股规则真相(须分清股票腿 vs 现金腿)**:
- **股票 T+1**:当日买入的股份当日不可卖(`available_volume` 次日才释放;已在 broker 层建模,见 `a-share-trading-rules-2026-05-27.md`)。
- **现金 T+0(市场层面)**:当日卖出所得资金**当日即可用于买入其他股票**(只是不可提现到银行)。即市场上"卖一"的钱当天能用来"买一"。

**但 QuantMind 的配比政策已主动收紧为"不假设当日回笼(不依赖当日卖出资金)"**——这是比市场规则更保守的自定约束(Phase P 配比红线)。因此正确的轮动时序对 QuantMind 是:

1. **Day T**:确定性层判定"候选 C 够格顶掉最弱持仓 W"(满足 B.1 排名 margin + B.3 反 churn 闸门)→ **只发 SELL W**(经 RiskEngine + 飞书人工)。**不在同日用 W 的卖出款买 C**(遵守"不假设当日回笼")。
2. **Day T+1**:W 的卖出资金已 settled、稳定可用 → 再发 **BUY C**(用 settled cash,经预算真·预留 + RiskEngine)。
3. 该"先卖、隔日买"的两步序列**天然规避 T+1 现金时滞带来的状态歧义**(避免在未结算资金上预下单导致 fail-open 风险),也避免同日"卖一买一"被对账/冻结逻辑误判。

> 设计含义:"卖一买一"在 QuantMind 不是一个原子动作,而是**跨两个交易日的有状态序列**:T 日 SELL → 标记一个 pending rotation 意图 → T+1 日确认 settled cash → BUY。pending 意图须 fail-closed:若 T+1 候选已不再够格(分数掉了/涨停了/被风控拒)则放弃买入,**绝不**强行补仓(对应 §2.3 "lead 涨停被拒=正确履职,辩下一只")。

来源:
- [A股 T+1 制度 — 百度百科(股票 T+1 / 资金 T+0:当日卖出资金当日可再买股不可提现)](https://baike.baidu.com/item/T+1/4691442)
- [富途 A股通交易规则](https://www.futuhk.com/hans/support/topic2_108)
- [T+1 Settlement: sell-today-buy-tomorrow with settled cash — IBKR Campus](https://www.interactivebrokers.com/campus/traders-insight/securities/stocks/t1-settlement-what-it-means-for-traders-and-investors/)

### B.3 Hysteresis / 反 churn:防止 5 个槽位被反复抖动

四类可叠加的确定性闸门(全部 LLM-free,可直接进决策路径):

1. **排名缓冲带 / no-trade band(rank 版)**:候选须比在位者好出 margin δ(B.1)。等价于 tolerance band——配置落地只在偏离超阈才动,否则不动。
2. **Hysteresis 双阈值(进/出不对称)**:进场阈高于出场阈。例:只在候选进 top-3 才考虑替换,但持仓掉出 top-(K+2=7) 才卖——制造延迟,削弱 whipsaw。
3. **最短持有期(minimum holding period)= qlib `hold_thresh`**:刚买入 N 天内不卖(异常/止损/SELL 例外另算)。直接杜绝"今天买明天卖"的抖动,也与 T+1 不冲突。
4. **每日换手 cap**:复用既有熔断 ≤5 单/日 + `max_debates_per_day` + check-10 bound 作为轮动 fan-out 上界;Line-2 轮动每日至多 1 次"卖一买一"建议,避免 5 槽位日内频繁洗牌。

> 学术与从业证据一致:tolerance band + hysteresis 显著降换手与交易成本,收益几乎不损甚至更优(alphaarchitect 再平衡研究 / Quantpedia 动量轮动族 / arXiv 1509.08110 turnover-performance)。最优"no-trade zone"宽度可建模(arXiv 2411.07949 双参数最优带、arXiv 2003.04646 最优带形状)——但对 QuantMind 建议**先用简单固定 margin + hold_thresh**,可调参数入 `watchlist_policy.yaml`/配比配置(runtime 不可改 + amendment + 重启,守 §2.4)。

来源:
- [Portfolio Rebalancing: Momentum and Tolerance Bands — Alpha Architect](https://alphaarchitect.com/2017/05/destabilizing-rebalancing/)
- [Hysteresis and the Defensive Rotation Strategy Part 2 — VolatilityTradingStrategies](https://www.volatilitytradingstrategies.com/blog/hysteresis-and-the-defensive-rotation-strategy-part-2)
- [Sector Momentum Rotational System — Quantpedia](https://quantpedia.com/strategies/sector-momentum-rotational-system)
- [Optimal two-parameter portfolio management with transaction costs — arXiv 2411.07949](https://arxiv.org/pdf/2411.07949)

### B.4 开源:固定 N 槽位轮动 / 动量轮动 / 替换逻辑

| 仓库 | 关注点 | stars | license | 评价 |
|---|---|---|---|---|
| [microsoft/qlib `TopkDropoutStrategy`](https://github.com/microsoft/qlib/blob/main/qlib/contrib/strategy/signal_strategy.py) | **固定 topk 槽位 + n_drop 排名替换 + hold_thresh 反 churn + only_tradable** | >16k(全库) | **MIT** | **首选参考实现**。算法直接对口"卖一买一 + 排名 margin + 最短持有期"。可移植算法逻辑(MIT 许可),不必引整库 |
| [`garroshub/Quant_Sector_Rotation_Strategy`](https://github.com/garroshub/Quant_Sector_Rotation_Strategy) | 动量+波动 ETF 轮动,**LLM 增强分析**(LLM 作分析非决策,印证 advisory 范式) | 11 | none | 思路参考(尤其 LLM advisory 分层),代码无许可证不可直接用 |
| [`LangxiaoXie/rotation-momentum-strategy`](https://github.com/LangxiaoXie/rotation-momentum-strategy) | **A股**行业轮动动量,13.9% CAGR vs 8.2% CSI300 | 0 | none | A股本土对口;低星无许可证,仅作 A股轮动参数/基准参考 |
| [`renhe0707/sector-rotation`](https://github.com/renhe0707/sector-rotation) | 申万行业轮动 + 多因子打分 + 回测(中文) | 1 | none | A股多因子打分参考 |
| [`Francesco-Baldassarre/etf-momentum-rotation`](https://github.com/Francesco-Baldassarre/etf-momentum-rotation) | ETF 动量轮动 + **交易成本建模** + 月度再平衡 | 0 | none | 交易成本建模思路参考 |
| [`MalharMardikar/SECTOR-ROTATION-BACKTEST-STRATEGY`](https://github.com/MalharMardikar/SECTOR-ROTATION-BACKTEST-STRATEGY) | 4 种配置对比 + Fama-French + MACD regime filter | 0 | none | regime filter 思路参考 |

> 现状判断:固定 N 槽位轮动的**唯一高星、合规(MIT)、可移植**资产是 **qlib TopkDropoutStrategy**。其余多为低星、无许可证的学习项目——仅作思路/参数参考,**严禁拷贝无许可证代码**。

### B.5 QuantMind 推荐方案(TOPIC B)

1. **轮动核心 = 移植 qlib TopkDropout 算法语义**(MIT 可移植算法),参数化为:`topk=5`(含 ETF)、`n_drop=1`(卖一买一)、`method_sell=bottom`、`method_buy=top`、`hold_thresh=N 日`(最短持有期)、排名 margin δ(挑战者须以 δ 名次/分数击败在位者)。打分尺统一用 CandidateSelector 量化分 + Line-1 辩论质量。**纯确定性,零 LLM**(对齐 Line-2 路径)。
2. **"卖一买一"= 跨日两步有状态序列(守"不假设当日回笼")**:
   - T 日:确定性层判替换成立 → 仅 SELL 最弱持仓 W(经 RiskEngine + 飞书人工)→ 记 pending rotation 意图。
   - T+1 日:确认 W 的 settled cash → 重新校验候选 C 仍够格(分数/涨停/风控)→ BUY C;不够格则放弃,绝不强买。
3. **反 churn 四闸门叠加**:排名 margin(B.3-1)+ 进/出不对称双阈(B.3-2)+ `hold_thresh` 最短持有(B.3-3)+ 复用熔断 ≤5 单/日 与每日至多 1 次轮动建议(B.3-4)。
4. **决策层权威不变**:替换打分只产出"候选优先序";最终 SELL/BUY 仍经现有 builder 单一构造点 + 5 早返 + RiskEngine 14-check + 飞书人工。轮动模块**严禁** `import backend.{llm,agents,mirofish}`(纯量化隔离,守 §2.4/§2.0)。
5. **配置入 `watchlist_policy.yaml` / 配比配置**(topk / n_drop / hold_thresh / margin δ)——runtime 不可改,改即 git diff + amendment + 重启(守 §2.4)。
6. 落地前置:跨日 pending rotation 状态 + Line-2 轮动节点是决策边界变化,须先写 `docs/decisions/*-amendment-YYYY-MM-DD-slot-rotation.md`。

---

## 一句话收口

- **TOPIC A**:业界把 thesis 固化为"支柱 + 确定性失效清单 + 催化时间窗",LLM 只写理由文本与 re-check 证据(advisory),减/清由确定性 ThesisHealthEvaluator 判——这正是 2026 agentic-trading 文献力推的 "LLM 感知/证据 + 规则 actuator"(arXiv 2605.19337 两层记忆),QuantMind 已在此范式上,只需补一个结构化 `PositionThesis` + 确定性健康评估器。无高星可移植库,自建为宜。
- **TOPIC B**:固定 ≤5 槽位"卖一买一"的标准确定性实现 = qlib `TopkDropoutStrategy`(MIT,可移植算法:topk/n_drop/hold_thresh/排名 margin = 排名缓冲反 churn)。在 QuantMind 落地为**跨日两步序列**(T 日 SELL → T+1 日用 settled cash BUY),严守"不假设当日回笼",四类确定性反 churn 闸门叠加,决策权威仍归 builder + RiskEngine + 飞书人工。
