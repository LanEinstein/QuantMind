# 接手 Prompt：以「少亏 ＋ 吃住不会消失的收益来源」重构行动计划

> 日期：2026-08-23
> 目标模型：**Fable 5**（主执行），**Codex**（决策讨论与唯一一轮正式审计）
> 授权：owner 2026-08-23 明确认可方向转变，指定新 session 重新构思行动计划
> 交付物：**研究 ＋ 计划书**，不是代码实现
> `real_broker_orders = false`（永久，见 §1.1）

---

## 〇、一句话任务

QuantMind 过去两年证明了一件事：**用公开数据预测哪只 A 股会涨，我们做不到。**
owner 已经拍板换方向——目标仍是「稳定盈利的交易系统」，但达成路径改为
**「少亏 ＋ 吃住不会消失的收益来源」**。你的任务是：基于本文件给出的全部既有证据、
本地资产和联网可获取的信息，**重新构思并写出下一阶段的行动计划书**，
放进 `./KickoffPrompts/`。

**不要直接开始写策略代码。先想清楚做什么、为什么、怎么验证。**

---

## 一、不可动摇的前提

### 1.1 唯一底线

**永禁真实券商程序化下单。** 系统只维护模拟盘；真实操作永远由 owner 本人在券商 App 完成，
再通过飞书告知系统同步状态。任何往真实下单方向写的代码一律拒绝并上报。

### 1.2 系统形态（owner 明确要求不变）

- **前端界面**（Vue 3，`frontend/`，:9276，只监听 127.0.0.1，远程走 SSH tunnel）＝**查看通道**
- **飞书**（`backend/integrations/feishu/`，WS 长连接收 + OpenAPI 发）＝**行动通道**，
  只在需要 owner 动手时推送，其余静默
- owner 自由文本回复 → 理解 → 更新模拟盘 → 回确认摘要

### 1.3 数据只有 L1，没有 L2 —— 这是本次计划书必须正面回答的约束

我们只有**日线级别的公开行情**（开高低收、成交量额、涨跌停价、复权因子、停复牌、
财报、筹码分布等），**没有逐笔委托、没有盘口深度、没有分钟级数据**。

直接后果，必须写进计划：

1. **任何依赖盘中微观结构的判断都做不了**（真实买卖压力、大单方向、撤单行为、
   盘口博弈、"主力"实时意图）。
2. **信号最早在收盘后成立，最早在次日开盘执行。** 这比作者/人的实际动作晚半个到一个交易日。
   该偏差已被实测校准过一次（见 §3.4）。
3. **因此"何时以及怎样给 owner 提建议"本身就是核心设计问题，不是工程细节。**
   owner 特别点出了这一条。计划书必须给出明确方案：
   - 建议在什么时点产生（盘后？次日盘前？）
   - 建议的有效期与失效条件（次日开盘跳空多少就作废？）
   - 建议里必须包含什么（个股、方向、目标仓位、理由、失效条件、如果没成交怎么办）
   - 什么情况下**不推送**（静默是默认状态，推送是例外）
   - owner 实际成交价与系统假设不一致时怎么对账
   - 建议的**颗粒度**（日频？周频？只在调仓日？）——L1 数据下高频建议是自欺欺人

---

## 二、必须继承的记分牌（这是本文件最有价值的部分）

**不要重做这些验证。它们都已经跑过，结论可靠。**

### 2.1 找「会涨的票」：约 10 次尝试，0 次成功

| 尝试 | 结论 |
|---|---|
| QGR 量化第一闸门（⑦快腿 `max_5d`/`turn_spike`/`rev_1d` ＋ ⑧底部确认门） | 建成，但未产生可认证的排名 alpha |
| AP-0.5（alpha pivot 端到端） | **NO-GO**，判定绑定约束＝**alpha 质量本身**，不是容器/工程 |
| DS-D1 红利低波作排名器 | FAIL——**作过滤器有效，作排名器无效**（重要区分） |
| DS-D2 反转 | `candidate_edge=False`，输给同宇宙随机 placebo |
| DS-AM 分析师修正动量 | `candidate_edge=False`（唯一薄边活口，DSR 0.039，远不足认证） |
| QGR-4 / B1 / B2 / C1a 择时叠加 | **全 FAIL**，合证「结构墙」→ **owner 已永久关门择时 overlay** |
| M3-520（卡 8 套利战法） | 交易级双窗口通过，**组合级样本外年化 −3.01%，不过** |
| M3 右侧波段（卡 1，2026-08-23） | **样本内每笔 −2.069%、placebo p=1.000，不过**（样本外 +0.481%/p=0.005） |

### 2.2 塑造风险：1 次尝试，1 次成功

**SLV-1 防御 sleeve** 是整个项目唯一通过科学门的东西：

- 构造：D1 防御宇宙过滤 → `dv_ratio` top5 等权 → buffer40 → 20 日调仓（`spec_hash c1d058c3…`，已冻结）
- 全窗 125 次调仓 / 宇宙 1818：净 **+1.5M**，**最大回撤 19.58%**（同期沪深300 约 **46%**），
  熊市 +0.196，换手 0.01
- **胜随机 placebo +2.54 t，三种市况全胜**（说明 `dv_ratio` 真有作用，不是运气）
- 已披露的 caveat：DSR 0.228 ≪ 0.95（样本内不可认证，见 §2.3）；2018 年 −15.3%；
  `dv_ratio` 本质是拥挤的红利因子——**承重归在"风险性质"而不是"预测能力"上**

### 2.3 一条决定性的方法论结论（Fable 5 自己在 2026-07-04 下的）

**DSR ≥ 0.95 的样本内认证在算术上永久不可达**（AP-0.5 下 SR_req = 2.67），
**与候选无关，适用于全部横截面排名候选**。

对策（owner 已全授权）：
- **sleeve 打地基**（承重＝风险性质，数月可验）
- 排名层改**选拔制判据**
- **认证全部移到前向**（存活式 kill-switch，而不是样本内显著性）

**这条不要推翻重来。它是本项目最贵的一条方法论结论。**

### 2.4 外部旁证（2026-07-04，Tushare 创始人分享的交叉验证）

数据拥挤度图谱与我们的账本逐条吻合：**量价、财报、分析师这三类数据都没有可靠的独立排名 alpha。**
由此确立的研究原则：

> **扩数据正交性，不扩挖掘次数。**

硬 NO 清单：量价换花样续挖 / 择时 overlay / 无账本的自动因子工厂。

### 2.5 owner 的 5 条交易原则（决策边界级，系统设计必须遵守）

- **P-A 确认门**：形成观点 ≠ 立即下单；入场须等市场确认上涨已启动（不追下跌飞刀、不预判抄底）；
  EXIT 对称（不在急涨中抢卖）
- **P-B 强制止损**：反常下跌必止损，**硬触发不等确认**（与 P-A 并存：入场等确认，止损不等）
- **P-C 做 T profit-gate**：绝不在无正浮盈时做 T
- **P-D 安全底线**：资本保全优先，下行保护权重 > 收益最大化（"被挂山顶"＝ FAIL）
- **P-E 仓位口径**：取消单股 15% cap（高置信可达 ~60%）＋ **≤5 名**＋
  **强制 ≥40% 现金 buffer，严禁梭哈**

⚠️ **P-B 在 M3 两次研究里都没有被实现**（因为"反常"缺可观察定义）。
新计划应当正面处理它，而不是再次绕过。

### 2.6 主力意图研究纲领的载重发现（2026-06-26）

在海量真数据里找规律（而非套用美股先验）后的结论：
**主力足迹的可交易边在 RISK / EXIT / 避险 ≫ ENTRY 择时。**
EXIT 因子 A1/A2 通过；ENTRY 侧全部失败。

---

## 三、M2/M3 语料线的终态（复刻博主"全能的野人"）

### 3.1 已完成且冻结

- 18 张战法卡**全部经 owner 逐张确认**（`docs/research/yeren-system/playbook-cards-confirmed-batch{1,2,3}-*.md`）
- Base v3 是现行复刻语义基线（`base-v3-spec-2026-08-20.md`）
- 32 个 hypothesis 家族 0 遗漏
- **作者复刻层已冻结，任何后续结果不得反向改写卡面、Base 或 observation**

### 3.2 M3 两次验证都失败

- **卡 8（520）**：交易级过、组合级样本外年化 −3.01% 不过 → 研究候选、不可执行、封存
- **卡 1（右侧波段）**：样本内 −2.069%/p=1.000 不过 → 研究候选、未通过、封存
- 结果报告：`docs/research/yeren-system/m3-right-side-wave-results-2026-08-23.md`
- **两次失败都是"研究者补的增强层定义不成立"，不是对作者语义的证伪**
  （卡 1 的整个退出端、底部/大阳线/回踩窗口都是研究者补的）

### 3.3 剩下 16 张卡的性质（关键洞察，新计划应据此设计）

它们缺可观察的买点触发器，**但其中最强的一批恰好是防守类的**：

| 卡 | 内容 | 证据强度 |
|---|---|---|
| 卡 4 | 禁亏损补仓 ＋ 禁浮盈加仓 | **`stable_core`（18 张里唯一）** |
| 卡 6 | 混沌—退潮—恐慌盘防守链（组合层降暴露） | `stable_core` ＋ candidate |
| 卡 9 | 干净的交易：只有 B 和 S，默认不做 T | candidate |
| 卡 11 | 持仓下跌先诊断原因（错杀→不动 / 高位滞涨→清仓） | candidate |
| 卡 12 | 一致预期兑现窗口的分层退出 | candidate |

**这些卡不需要自己产生买点，可以作为"在已有组合上的消融"来验证**
（上一版 M3 计划书已写明这才是它们的正确验证方式；该文件已由 owner 于 `cf9815a` 清除，结论转录于此，不必去找原件）。
博主反复讲的本来就是"怎么不亏"，不是"怎么选中妖股"。

### 3.4 已实测校准的偏差（可直接引用，不用重测）

- **日线"收盘成立、次日开盘执行"比人实际动作晚半日至一日**，
  实测每笔均值约 **+0.2pp**、**中位约 +0.002pp**（即典型交易几乎无差，差异全在尾部）
- **复权口径已定案**：后复权 `close × adj_factor`（同一 `trade_date`）；
  78 只证券因子历史与 `pct_chg` 对不上，**后续研究必须沿用排除**
- **前后复权对"只读斜率符号"的规则逐位等价**，不需要两条结果线
- **交易成本**：owner 实际佣金万 1.5 ＋ 单笔 5 元地板、过户费 0.001%、
  卖出印花税 0.1%、双边滑点各 0.1%。
  ⚠️ **5 元地板只在小额单上生效**：按一手（约 1–2 千元）评估时费用负担约 1.5pp/笔，
  但按真实仓位（如 12 万/笔）评估时地板不触发，真实费用约 0.3pp。
  **评估短周期规则时必须说清用的是哪个口径，否则会系统性高估或低估。**

---

## 四、🔴 当前最紧急的一件事（先查、先修，再谈新计划）

**SLV-1 前向试运营已停摆约 6 周。**

事实（2026-08-23 核实）：

```
data/factor_research/defensive_sleeve_forward_status.json
  status: ACCRUING
  forward: start=20260615, end=20260710, trading_days=19,
           complete_periods=0, schedule_rebalances=["20260615"]
  kill_switch: breaches=[], realized_mdd=0.037, mdd_kill=0.25,
               min_forward_periods=8
data/factor_research/sleeve_advisory_sent.json
  最后一次推送: 20260710 (2026-07-12 发出)
crontab: 无 sleeve 相关条目          ← 根因：每日 pipeline 从未安装
data/marketdata_pit: 行情已摄取到 20260819   ← 数据在，只是没跑
```

**认证需要 8 个完整周期（每 20 个交易日一次调仓），现在完成 0 个。**
数据都在本地，可以直接补跑 `20260710 → 20260819`，**一天都不损失**；
但每多停一周，认证就晚一周。

**这是项目唯一有正面证据的资产，正在因为一个没装的 cron 慢慢烂掉。**

已有的运维协议（`docs/research/defensive-sleeve-forward-trial-ops-2026-07-12.md` §4）：

```bash
# 手动补跑一次（幂等；脚本会从最后一个已存 daily 日期自动续摄取）
bash /home/ps/papers/QuantMind/scripts/sleeve_trial_daily.sh

# 一次性装 cron —— Claude/Fable 无权限，必须 owner 亲自执行
(crontab -l 2>/dev/null; \
 echo '40 17 * * 1-5 /home/ps/papers/QuantMind/scripts/sleeve_trial_daily.sh >> /home/ps/papers/QuantMind/logs/sleeve_trial_daily.log 2>&1') | crontab -
```

**KILLED 处置**：推送/状态出现 `KILLED` ＝ 预注册 kill-switch 触发 →
停止执行建议、上报 owner、如实落账，**绝不调阈值续跑**。

**建议你把"救活 SLV-1"作为计划书的第 0 项，并在写计划之前就先把补跑做掉**
（补跑是幂等的、不改任何冻结口径、不需要新授权；装 cron 需要 owner 亲自做，
请把命令给他）。

---

## 五、owner 已经认可的战略判断（本次方向转变的依据）

请把这段当作**已达成的共识**，不要重新论证，但可以在计划书里深化或修正：

### 5.1 三类收益来源必须分开

| 类型 | 性质 | 我们的战绩 |
|---|---|---|
| **Alpha（超额收益）** | 零和，**会被套利掉** | 两年、两条独立研究线、约 10 次尝试，**全败**。结论可靠，不需再验 |
| **风险溢价** | **非零和，拥挤也不会消失**（来源是"有人不愿承担这个风险"，不是"有人不知道"） | **只碰过一次（SLV-1）就成功了** |
| **制度红利** | 靠规则赚钱，**根本不需要预测** | **一次都没碰过** |

### 5.2 核心判断

**"稳定盈利"能达成，但路径不是"预测得更准"，而是"少亏 ＋ 吃住不会消失的收益来源"。**

### 5.3 "稳定盈利"拆成四件事，现状是

| 要件 | 现状 |
|---|---|
| 1. 一个正的期望收益来源 | ⚠️ 有半个（SLV-1 的风格溢价，前向未认证） |
| 2. 能活着熬过去的回撤 | ✅ 唯一真正证明过的能力 |
| 3. 成本与容量吃不掉收益 | ✅ 在 owner 资金量级上不是瓶颈 |
| 4. 组件之间足够独立 | ❌ 目前只有一个组件 |

**计划书的核心任务，就是把第 1 和第 4 件事补上，同时不破坏第 2 件。**

### 5.4 现实的预期天花板（坦白写进计划书）

以 owner 的资金量级、A 股、只有 L1 公开数据、不加杠杆——
现实天花板大约是**"接近市场的长期收益，但回撤只有市场的一半左右"**，
不是"年化 20% 回撤 5%"。SLV-1 的样本内形状（19.6% vs 46%）就是这个量级。

**计划书如果给出比这更乐观的目标，必须给出为什么这次不一样的具体理由。**

---

## 六、你需要探索并在计划书里回答的问题

以下是方向，不是任务清单；你可以增删，但**每一条的取舍都要写理由**。

### 6.1 风险溢价层（最有把握，优先级最高）

- SLV-1 目前只吃 `dv_ratio`（红利）。还有哪些**风险溢价**在 A 股是可持续的？
  低波、质量、小市值、价值——**联网查最新的学术与业界证据**，
  特别注意哪些在 A 股被证伪、哪些只是拥挤但仍有补偿。
- 关键区分：**过滤器 vs 排名器**。DS-D1 已证明红利低波**作过滤器有效、作排名器无效**。
  新的因子应该按哪种角色使用？
- 多个 sleeve 如何组合才能让第 4 件事（组件独立性）成立？

### 6.2 制度红利层（完全未探索，可能是最大的空白）

- **打新**：现在的中签率、破发率、年化贡献还剩多少？需要什么数据？
- **可转债**：低价债 / 双低策略、下修博弈、强赎条款。数据从哪来？
- **ETF 折溢价、LOF、分级残余**
- **指数成分调整**（`index_weight` 端点本地有 2016-01 至 2026-07 的 127 个快照）
- 这些的共同优点：**不需要预测方向**，和我们失败的所有尝试不同源。
- ⚠️ 也要诚实评估：很多制度红利已经衰减，**先查清楚现在还赚不赚钱，再决定要不要开工**。

### 6.3 防守层（把语料用在它擅长的地方）

- 把卡 4 / 6 / 9 / 11 / 12 作为**已有组合上的消融实验**来验证，而不是让它们各自造买点。
- 每次消融只回答一个具体问题，例如「加上禁亏损补仓，最大回撤降不降？」
- **必须预注册**，不得一口气排列组合所有卡再挑最好看的。
- 这里也是正面解决 **owner 原则 P-B（反常下跌必止损）** 的地方——
  "反常"需要一个 L1 可观察的定义（例如相对全市场/同行业的异常弱势），
  注意本地 PIT **没有指数历史**（`index_daily_major` 只有 2026-08 的 4 个快照），
  市场基准需要**从面板自己构造**（如全市场横截面中位涨跌幅）。

### 6.4 建议产生与推送的设计（owner 特别点名，L1 约束的正面回答）

见 §1.3 的六个子问题。这一节应当是计划书里**最具体**的一节。

参考实现：`scripts/push_sleeve_advisory.py` ＋ `backend/integrations/feishu/renderer.py` 的
`render_sleeve_advisory` 已经跑通过一条 display-only 建议推送链路（2026-07-12 实发过一次），
可作为形态与措辞的起点；但它只覆盖「每月调仓日推目标持仓」这一种情形，
其余情形（临时减仓、止损、不推送）都还没有设计。

### 6.5 明确不建议做的（除非你能给出推翻既有证据的理由）

1. 再找新的选股信号 / 再换一个退出定义重做卡 1
2. 把剩下 16 张卡逐张硬做成独立策略
3. 择时 overlay（owner 已永久关门）
4. 加杠杆或提高频率去"放大"收益——我们没有可放大的边
5. 无账本的自动因子工厂 / 参数网格 / 评分表
6. 重启 520、补 S8

---

## 七、工作纪律（强制）

### 7.1 反过度防御

参考 [HERO-Anti-OverDefense](https://github.com/wanshuiyin/HERO-Anti-OverDefense)，四禁：
① 无实际用途的校验和/指纹/摘要；② 防御本项目不会出现的输入；
③ 用评分表/机械清单/复验循环替代人的判断；④ 为想象中的未来需求预建开关/框架/兼容层。

判断句：**"这能检测到什么具体故障，我会因此做出什么不同的决定？"** 答不上来就不写。

### 7.2 跨模型 review：一个任务至多一轮

**Codex（或任何跨模型）review 与随后的修改，一个任务至多进行一次。**
一轮 review ＋ 一轮修复，然后停止。

**禁止**：复验到"无剩余缺陷"、"连续两轮干净才收工"、R1-R5 轮转、为确认收敛再跑一轮。

owner 原话：「有足够的证据表明，多轮 review 会大幅提升 AI 的误判率以及过度纠错。」

P0/P1 必修；P2/P3 按判断取舍，不必清零；未处置的写进报告即可。**docs-only 任务豁免 review。**

**本次任务是计划书撰写（docs-only），因此默认豁免正式 review；
但重大研究判断建议与 Codex 做决策讨论**（讨论 ≠ 审计，见 §7.3）。

### 7.3 Codex 的两个角色必须分开

1. **决策讨论者**：在出现实质选择时回答清晰的决策题。给它的问题必须包含：
   现有事实 ＋ 禁止改动项 ＋ **你的单一推荐** ＋ 最多一个可行备选 ＋ 两者的具体差异 ＋
   要求它明确回答"采纳主建议 / 采纳备选 / 指出遗漏的实质约束"。
   **不得问"帮我全面 review""还有没有问题"。**
2. **唯一正式审计者**：只在有代码改动时、全部结果出来后进行一次。

调用方式（实测有效）：

```bash
# 决策讨论 / 自定义 prompt（--base 和 --uncommitted 都不能带自定义 PROMPT）
codex exec --sandbox read-only "$(cat /path/to/question.md)" </dev/null > out.log 2>&1

# 正式审计（内置 review 指令，不能带自定义 prompt）
codex review --uncommitted
```

**坑**：`codex exec` 后台跑会 stdin deadlock，必须 `</dev/null`；
一次调用常跑 10–30 分钟且中途只输出工具轨迹，**不要反复读输出文件**，
用一个阻塞式 waiter 等它退出。

### 7.4 预注册纪律（凡是要跑出数字的都适用）

- **写完预注册并提交，再写实现，再跑一次。** 顺序不能反。
- 一个主口径，不列参数网格；OOS 不参与参数选择。
- 完整跨期加载面板，窗口用参数限定——
  **坑：把面板截到样本切分日会改变边界成交语义**（卡 8 上出现过 667 笔幻影差）。
- 失败即封存，**不做补救性重跑、不换定义再试**。
- 判据、seed、placebo reps、p 值公式（含比较算子与并列处理）全部运行前写死。
- 所有无法识别项明写，不能藏在实现默认值里。

### 7.5 git 与秘密

- conventional commits，message 用英文；**commit 只落本地，`git push` 必须 owner 明示授权**
- 秘密只在 `~/.bashrc`（LLM key ＋ `FEISHU_*` ＋ `TUSHARE_TOKEN`），
  严禁入 `.env` 和代码；gitleaks pre-commit 已装，**严禁 `--no-verify`**
- `data/marketdata_pit/`（~29 GB，append-only）**严禁删改、严禁从零重下**
- `data/yeren_corpus/` 同样 append-only

### 7.6 语言

回复 owner 用中文；代码、注释、commit message 用英文。

---

## 八、环境与开工检查

```bash
cd /home/ps/papers/QuantMind
git status -sb
git log --oneline -6

PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q            # 期望 ~7366 passed / 14 skipped
$PY/ruff check backend/ scripts/ tests/                   # 期望 All checks passed
tail -1 data/yeren_research/worklog.jsonl | $PY/python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"
# 期望：M3-right-side-wave-autonomous-execution | M3-right-side-wave-sealed-owner-direction
```

- 当前分支 `agent/m2-evidence-reconstruction`，**本地领先 origin 7 个 commit，全部未 push**
- 跑测试**必须**带 `FEISHU_INTERACTIVE_ENABLED=false`，否则会连飞书
- 研究全窗跑长任务用 `setsid` 完全脱离 ＋ 轮询等待（turn 边界会杀普通后台进程）

---

## 九、本地资产清单（不要重造）

### 9.1 数据

`data/marketdata_pit/`（~29 GB，append-only，字节档）：

| 端点 | 覆盖 | 备注 |
|---|---|---|
| `daily` / `adj_factor` / `daily_basic` / `stk_limit` / `suspend_d` / `stk_factor_pro` | 20150105–20260819，2826 个交易日 | 全 A 股日线主力数据 |
| `cyq_perf`（筹码分布） | 20180102–20260819，2094 天 | **有历史但基本没用过** |
| `limit_list_d`（涨跌停板） | 20200102–20260819，1607 天 | **有历史但基本没用过** |
| `index_weight` | 20160129–20260731，127 个 | 指数成分权重 |
| `report_rc`（分析师） | 2014+，156 个 | ⚠️ `tp` 是利润总额不是目标价，目标价＝`min_price` |
| `income/cashflow/balancesheet/fina_indicator_vip` | 2015Q1–2026Q2 | ⚠️ `*_vip` 单调用有行上限且**静默截断**，必须 limit+offset 分页 |
| `namechange` | 1990–20260813 | ST 状态的 PIT 来源 |
| `moneyflow` / `top_list` / `margin` / `index_daily_major` 等 | **只有 2026-08 的 4 个快照** | **没有历史，不能直接用于回测** |

清单与增量更新协议：`docs/research/data-inventory-marketdata-pit-2026-06-21.md`

⚠️ **本地没有指数历史**。需要市场基准时**从面板自己构造**（如全市场横截面中位涨跌幅）。

⚠️ **北向 `hk_hold`/`hsgt` 2024-08 后断更。**

### 9.2 代码（可直接复用）

| 路径 | 用途 |
|---|---|
| `scripts/factor_research/defensive_sleeve_{spec,science_gate,forward,ablation}.py` | SLV-1 全套（spec 已冻结 hash `c1d058c3…`） |
| `scripts/sleeve_trial_daily.sh` / `scripts/push_sleeve_advisory.py` | 每日试运营 pipeline ＋ 飞书 display-only 推送 |
| `scripts/yeren_research/pit_priced_panel.py` | 日线 × 复权因子同日连接、完整跨期加载 |
| `scripts/yeren_research/pit_limit_panel.py` | **（2026-08-23 新增）** 整张 `stk_limit` 面板一次对齐到价格面板（约 900 MB / 15 秒），消除了旧方案"要知道碰哪些 bar 就得先知道涨跌停"的循环依赖 |
| `scripts/yeren_research/m3_520.py::matched_horizon_placebo` | 同证券、同窗口、同持有期、随机入场时点的 placebo |
| `scripts/yeren_research/m3_520_portfolio_validation.py::run_portfolio` | 确定性资金重放账本（现金、跨公司行为盯市、槽位/敞口上限、费用） |
| `scripts/yeren_research/m3_right_side_wave{,_rule}.py` | 规则层与研究驱动层分离的模板 |
| `backend/integrations/feishu/` | 飞书收发（`renderer.py` 已有 `render_sleeve_advisory`） |
| `backend/broker/` | MockBroker 模拟盘账本 |
| `frontend/` | Vue 3 前端骨架，15 个 view 已存在 |
| `scripts/ingest_historical_pit.py` | 幂等增量摄取 |

⚠️ **出站必须 IPv4-only**（httpx 传 `local_address="0.0.0.0"`）；服务只监听 127.0.0.1。
⚠️ Tushare 只用官方 SDK `ts.pro_api()`。

### 9.3 必读文档（按重要性）

1. `CLAUDE.md`、`AGENTS.md`（工作守则）
2. `docs/research/defensive-sleeve-science-gate-results-2026-07-04.md`（**唯一通过的科学门**）
3. `docs/research/defensive-sleeve-spec-and-forward-validation-plan-2026-07-04.md`
4. `docs/research/defensive-sleeve-forward-trial-ops-2026-07-12.md`（**当前停摆的那件事**）
5. `docs/archive/decisions/qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md`（DSR 不可达裁决）
6. `docs/research/external-crosscheck-tushare-data-talk-2026-07-04.md`（外部旁证）
7. `docs/research/yeren-system/m3-right-side-wave-results-2026-08-23.md`（最新失败的完整解剖）
8. `docs/research/midterm-rearch-action-plan-2026-08-12.md`（**上一版行动纲领——你要取代它**）
9. `docs/research/data-inventory-marketdata-pit-2026-06-21.md`（PIT 清单与增量更新协议）
10. `docs/archive/decisions/qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md`（owner 5 条交易原则的落地 amendment）

⚠️ **`KickoffPrompts/` 下的旧接手 prompt 已由 owner 于 commit `cf9815a` 主动清空**，
本文件是该目录当前唯一的文件。你需要的历史结论**已全部转录在本文件 §二/§三**，
不要去找那些已删文件；确需原文时用 `git show cf9815a^:KickoffPrompts/<name>` 取历史版本。
`docs/plan.html`（旧线 SSoT）在当前工作树中**不存在**，不要引用。

---

## 十、交付物

在 `./KickoffPrompts/` 下产出一份行动计划书，**必须包含**：

1. **三十秒摘要**：新方向一句话，以及它与旧纲领的区别
2. **继承的终态**：哪些已经定案、不再重做（可直接引用本文件 §二/§三）
3. **系统目标的重新定义**：把"稳定盈利"翻译成可检验的具体判据
   （收益、回撤、容量、认证方式各是什么）
4. **收益来源地图**：风险溢价 / 制度红利 / 防守层各自的候选、证据、开工条件、
   **以及每一条为什么值得做或不值得做**
5. **L1 数据约束下的建议产生与推送设计**（§1.3 的六个子问题逐条回答）
6. **里程碑与依赖顺序**，每个里程碑写清：产出物、判据、失败分支、owner 门在哪
7. **明确不做清单** ＋ 每条理由
8. **第一个工作单元的完整开工剧本**：精确命令、预期输出、可能踩的坑
9. **风险与坦白**：这套计划最可能在哪里失败

**风格要求（owner 既有反馈）**：
handoff / 计划文档要 **exhaustive**——含完整命令、预期输出、具体代码片段；
**质量优先于最小改动**；报告"完成"前先改 SSoT。

---

## 十一、你不该做的

1. 不要在计划书获 owner 认可前实现新策略代码
2. 不要重做 §二 已有结论的验证
3. 不要修改已冻结的：SLV-1 spec、18 张卡卡面、Base v3、observation、hypothesis
4. 不要 push（需 owner 明示授权）
5. 不要动 `data/marketdata_pit/`、`data/yeren_corpus/` 既有档案
6. 不要创建任何真实券商程序化下单路径
7. 不要跑第二轮跨模型 review

---

## 十二、开工口令

> 按 `KickoffPrompts/NEXT-strategy-rebuild-loss-avoidance-and-durable-return-2026-08-23.md` 开工。
> 先按 §八 做开工检查，再按 §四 把 SLV-1 补跑救活（幂等，不需新授权；装 cron 的命令给 owner）。
> 然后联网调研 ＋ 与 Codex 做决策讨论，产出 §十 定义的行动计划书。
> 目标是"少亏 ＋ 吃住不会消失的收益来源"，不是"预测得更准"。
> 数据只有 L1，**建议的时点与形式本身就是核心设计问题**。
> 系统形态不变：前端查看 ＋ 飞书行动，owner 人工执行。
> 计划书写完即停，不实现、不 push，`real_broker_orders=false`。
