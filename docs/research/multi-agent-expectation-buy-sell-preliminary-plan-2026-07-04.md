# 多 Agent 预期驱动买卖点 — 初步规划(交 Fable 5 出具体计划)

- **性质**:初步规划 / 问题框定 doc。owner 2026-07-04 洞察:**"买卖点选择的核心在于'预期',而这个预期完全可以由多 agent 基于真实数据与信息共同制定"**。owner 将本 doc 粘贴到新 session,由 **Fable 5** 出具体计划。
- **定位**:本 doc **不下结论、不建代码**;它给 Fable 5 一个自洽的起点——背景、核心假说、**必须解决的硬问题**(尤其一个容易被漏掉的致命污染)、现有可复用资产、几个候选投研形态、红线清单、以及 Fable 5 的具体计划必须回答的问题。
- **上位/关联(接手必读)**:`CLAUDE.md` §2(红线,尤其 §2.2 LLM 权限 / §2.3 多 Agent 守门 / §2.0 单一构造点)· `external-crosscheck-tushare-data-talk-2026-07-04.md`(为什么机械因子走到尽头)· `qgr-certification-rearch-amendment-2026-07-04-*.md`(dev=选拔/cert=前向,AP-0.5 算术墙)· `defensive-sleeve-spec-and-forward-validation-plan-2026-07-04.md`(并行主线:sleeve 地基)· `docs/decisions/P0-10-amendment-line2-2026-06-01-position-thesis-advisory.md`(PositionThesis = 已有的"预期+确定性失效"原型)· `main-force-intent-research-program-macro-direction-2026-06-26.md`(主力意图纲领,信息流方向)。

---

## 0. 一句话

机械横截面因子(价量/财报/分析师)作**独立可认证排名 alpha** 已跨机制证否(DS-D2/DS-AM)+ AP-0.5 算术墙。owner 提出换**单元**:不再是"因子打分",而是**"预期"**——对一只票的前瞻判断(盈利/价格/事件轨迹 + 置信 + 时限),由**多 agent 基于真实数据与信息共同制定**,买卖点从预期派生(非机械择时 overlay)。**这可能更正交、更抗拥挤**(memo:另类/信息流拥挤度最低),**但有一个致命的可测性陷阱(§3.1),Fable 5 的计划必须先解决它,否则整条线会像 LLM 回测一样自欺。**

## 1. 核心假说(owner 洞察的结构化)

- **单元 = 预期(expectation)**:一个结构化前瞻对象,至少含 {标的, 方向/幅度前瞻, 时限, 置信, 依据(evidence 血缘)}。
- **形成 = 多 agent 共同制定**:多个视角 agent(基本面/技术/资金/事件/风险…)各基于**真实数据与信息**(PIT)产出各自预期分量 + 依据,经编排综合成一个共识预期(带分歧度)。
- **买卖点 = 预期的派生**:买点 = 预期成立且入场条件满足;卖点 = **预期失效**(确定性失效阈值触发)或 thesis-break——**不是**机械 regime/择时信号(那条路 C1a/B1/B2/QGR-4 已四刀证否、owner 永久关门)。这与已有 `PositionThesis`(LLM 支柱文本 + 确定性量化失效阈值)同构,只是把它从"持仓监控"扩到"入场买点"。

## 2. 为什么这可能是出路(与已证否的区别)

- 机械因子失败的根因(memo):**同质数据 + 同质公开逻辑 → 策略扎堆 → 信号失效**。
- 一个由**多信息流经推理综合**的预期,潜在更正交/更少拥挤——**前提是能做到 PIT 保真 + 可测 + 红线兼容**。若做不到这三条,它只是"讲故事",比机械因子更难证伪、更容易过拟合。
- **AP-0.5 墙仍在**:即便预期有边,样本内 DSR≥0.95 不可达(候选无关)→ 认证只能走前向。这不改变,但预期形态的正交性可能让**前向存活**更可信。

## 3. Fable 5 必须解决的硬问题(本规划的核心)

### 3.1 ⚠️ 致命污染:LLM 训练泄漏 = 历史回测根本无效

**这是最容易被漏掉、但会毁掉整条线的问题。** 若 agent 含 LLM,而 LLM 在"2018 年的数据"上形成预期——**LLM 的训练语料已包含 2019-2025 的结果**,它"预测"2018→2019 时其实知道答案。任何对 LLM 形成之预期的**历史回测都被前瞻知识污染**(比 look-ahead 更隐蔽,四门也测不出来,因为泄漏在模型权重里不在数据里)。

Fable 5 的计划**必须**围绕这条设计,候选出路:
- **(i) 回测用确定性预期**:回测阶段 agent 只用**确定性/量化 PIT** 形成预期(无 LLM 前瞻知识),LLM 只作 evidence-only 事后色彩(不入决策)。可回测 + 可过门,但损失 LLM 推理丰度。
- **(ii) 只前向认证**:LLM 参与的预期**不做历史回测**(承认污染),只在**真前向**(实时,LLM 真不知未来)跑 shadow,认证 = 前向存活(同 sleeve)。丰但慢,且对"近过去"训练截断仍需处理。
- **(iii) 混合**:确定性预期骨架(可回测)+ LLM evidence-only 增强(前向监控),决策确定性派生。红线兼容,量化预期是可测核心。

**owner 洞察本身与红线不冲突,但"用 LLM 回测证明它有边"会——Fable 5 须明确本线的证据从哪来(前向 vs 确定性回测),不能用被污染的历史回测自我背书。**

### 3.2 红线兼容(硬约束,非可选)

- **§2.2 LLM 权限**:LLM 可写仅 4 类文本字段(reasoning / evidence content / debate text / risk proposal text);**严禁写决策字段 / side/volume/limit_price**。→ "预期"须是**结构化、确定性可消费**对象;LLM 贡献依据+推理文本,**决策数值确定性派生**,不来自 LLM JSON。
- **§2.0 单一构造点**:`InstructionPlan` 仅 `instruction_plan_builder` 可构造;side/volume/limit_price 确定性派生。→ 预期→买卖点的转换必须落在既有确定性 builder 内。
- **§2.3 多 Agent 守门**:4 必经 agent + fund_manager 唯一方向倡议者 + debate ≥1 + RiskEngine 14-check 独立。→ 多 agent 预期形成须套进既有 LangGraph 编排 + 双层守门,不新开旁路。
- **研究期 LLM 只用于文献**:研究/回测路径**零运行时 LLM**(除文献)。→ 回测不能实时调 LLM 打分历史(既贵又 §3.1 污染)。
- 永禁真实下单 / 飞书人工 / PIT 字节存档 / 账本不清零 / 四门不放宽。

### 3.3 反过拟合可测性

- 预期 → 仓位 → 前向收益,须过**四门 + placebo**:**多 agent 共识预期 vs naive/共识预期基线**(placebo:随机预期?卖方一致预期?上期预期不变?)——多 agent 综合是否胜 naive?
- **"预期"的 placebo 怎么定** = Fable 5 要设计的关键(类比 DS 线的 random top-5)。
- AP-0.5:胜 placebo 也只前向认证。

### 3.4 PIT 保真 + "买卖点非择时"

- agent 摄取的"真实数据与信息"必须 PIT(news/analyst/filings 均有时间戳,firewall <d);§3.1 的 LLM 权重泄漏另算。
- 买卖点必须**预期/thesis 驱动**(入场=预期成立+条件;出场=预期失效确定性阈值),**非机械择时 overlay**(已证否)。Fable 5 须显式框成 thesis-driven position management。

## 4. 现有可复用资产(复用为纲,别重造)

| 资产 | 位置 | 对本线的用途 |
|---|---|---|
| 4 必经 agent + ≥2 交易员 + fund_manager 编排 | `backend/agents*` + v2 rearch | 多 agent 预期形成的现成机器(LangGraph) |
| **PositionThesis(预期+确定性失效原型)** | `docs/decisions/P0-10-amendment-line2-2026-06-01` + backend | **已有的"预期+确定性失效阈值"**;本线 = 把它扩到入场买点 |
| evidence_collection(evidence-only,provenance-gated) | backend | LLM 依据落库,不入决策 |
| 单一构造点 builder + RiskEngine 14-check | backend | 预期→买卖点的确定性派生落点 |
| 回测 harness + 四门 + 账本 | `scripts/factor_research/gate_backtest` 等 | 可测性(确定性预期分量) |
| 主力意图纲领 + 事件信息流清单 | `main-force-intent-*` + memo §3 | 预期的"真实信息"来源(stk_holdertrade/top_list/block_trade/share_float,owner-gated 摄取) |

## 5. 初步投研形态(建议给 Fable 5 的选项,非定论)

- **形态 A(确定性预期集成,可回测)**:多个**确定性**预期模型(基本面预期/技术预期/资金预期/事件预期)各出前瞻分量 → 综合共识预期 → 确定性买卖点。可过四门 + placebo,可前向。**LLM 仅 evidence-only**。最稳,先做。
- **形态 B(LLM 预期,前向认证)**:LLM 参与预期,**不回测**(§3.1),只前向 shadow 存活认证。丰,慢,晚。
- **形态 C(混合)**:A 的确定性骨架 + B 的 LLM evidence-only 增强,决策确定性派生。红线兼容。
- **建议起点 = 形态 A**(可测、红线干净、能与 sleeve 并行验证),把 LLM 丰度留到形态 B/C 的前向阶段。Fable 5 定夺。

## 6. 成功定义 / 判据(供 Fable 5 精化)

- 多 agent 共识预期在**前向**胜 naive/共识预期基线(风险维度 + 净盈);PIT 干净;红线干净;**不靠被污染的历史 LLM 回测背书**。
- 绝对净盈>0 + 控回撤(与 owner 目标一致);认证走前向存活(kill-switch),非样本内显著性。
- 与 sleeve 关系:预期驱动买卖点可**叠在 sleeve 之上**(sleeve 定宇宙 + buffer;预期定宇宙内买卖点)或独立;Fable 5 判。

## 7. 红线 / 约束清单(Fable 5 计划须全遵守)

永禁真实下单 · LLM 只写 4 文本字段/不写决策/研究期只用于文献 · 单一构造点 · 4 必经 agent + fund_manager 唯一倡议 + RiskEngine 14-check · **§3.1 LLM 训练泄漏:不用被污染历史回测自证** · PIT 字节存档 + firewall <d + 禁重下 · 四门不放宽 · 账本不清零 · 前向摄取/live 激活/push 全 owner-gated · 择时 overlay 永禁(买卖点须预期/thesis 驱动)· FAIL 报 FAIL。

## 8. 交给 Fable 5 的具体问题(其计划须回答)

1. **"预期"的精确 schema** 是什么?(字段、确定性 vs LLM 分量的边界)
2. **§3.1 污染怎么解**:本线证据从确定性回测 / 前向 / 混合哪条来?怎么保证不被 LLM 前瞻知识自欺?
3. **多 agent 综合的确定性映射**:各 agent 预期分量 → 共识预期 → 买卖点,如何在红线内确定性派生(LLM 只依据)?
4. **预期的 placebo** 怎么定,四门怎么套?
5. **形态 A/B/C 选哪个先做**,第一刀的最小可测切片是什么?
6. 与 **sleeve / 事件信息流数据 / PositionThesis** 的关系与复用。
7. 与 owner 双资本(¥100万 研究证 / ¥1万 执行可行性)、模拟实盘、飞书人工的对接。

---

*本 doc 为初步规划,方向与设计定夺权在 owner + Fable 5;不构成任务立项、不动代码/账本。*
