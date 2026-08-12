# amendment(2026-06-27)—— 确认门 + 强制止损 + 做T profit-gate + 安全底线 + 仓位口径(P-A..P-E)

> **状态**:**owner 主动追加(2026-06-27,第三步 plan 审批中 + AskUserQuestion 仓位修正)** · 决策边界变更,本 amendment 先行(实施 C0b/C1/C3/C5 前生效)· **作者**:Claude(Opus 4.8)
> **上位**:`qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md` §10(指针)+ `docs/research/system-roadmap-outline-2026-06-27.md`(大纲 + codex 收敛)+ 第三步 plan(`misty-doodling-pnueli`,owner 已批)。
> **触发**:owner 在评价协议规划中追加 5 条交易原则,均为决策边界级 → 须 amendment-first 编码,再写研究模拟器/信号码。
> **scope**:**研究侧 = 端到端模拟器(C0)的确定性规则 + sizing 配置**(sim 暂停,纯离线模拟,零 live 改动);**live 侧 = 仅标注须 owner-gated**(本 amendment 不改任何 live 代码/governance enum)。

## 1. owner 原则(精确编码)

- **P-A 确认门(等市场反馈再行动)**:系统形成「某票某点后要涨/要跌」的观点 **≠ 立即下单**;须**等市场确认**预判方向已启动才执行。**入场**:不追下跌中的飞刀、不预判式抄底;候选 as-of close T 形成 → **确认上涨已启动**(首次收复区间 / 动能确认 / 放量确认 的确定性代理)→ T+1 执行。**EXIT 侧对称**:不在急涨中抢卖;避顶部须**确认派发/滚顶**(放量滞涨 + 量价/OBV 背离 = 动能转弱),非仅因高位/延展就卖(与 rebar amendment §9 Erratum「左尾风险假说非择顶」一致)。
- **P-B 强制止损(两条腿,硬安全底线)**:短线持仓 + 价值长线持仓,**只要出现反常下跌就必须止损**,防亏损越滚越大。**强制非可选**;**硬触发不等确认**(与 P-A 并存:入场/止盈等市场确认,**止损不等**)。只增卖压、永不放松现有止损(同 rebar §3)。
- **P-C 做T profit-gate**:**绝不在无利润垫(无正浮盈)时做T**;只在持仓**已有正浮盈**时反复低吸高抛摊薄成本。靠做T 摊低亏损位 = 高风险、易陷亏损无底洞,**禁止**。
- **P-D 安全底线投机**:聪明、有策略、**资本保全优先,绝不冒险**。融入评价「稳定可观」定义:**下行保护权重高于收益最大化**;「不被挂山顶」(逆境非永久套牢)= 硬门。
- **P-E 仓位口径 = 置信度加权集中 + 硬现金 buffer**(替代等权 5 槽 + 单股 15% cap):
  - **① 取消单股 15% cap**:高置信优质股单名可重仓(示例单只 **60%**)。
  - **② ≤5 名 = 上限,非必须占满**(可 1-5 名)。
  - **③ 强制保留 ≥40% 现金 buffer = 严禁梭哈/全仓**(应对不时之需 + 给做T 低吸/再入留弹药 + P-D 安全底线)。
  - **口径**:总投资留 ≥~40% 现金(默认 40%,owner 可调)、单股 cap 取消(单名可达 ~60%)、≤5 名。

## 2. 研究侧编码(C0 端到端模拟器,确定性、离线、零 LLM)

| 原则 | 研究侧确定性机制 | 评测(竞技场内,付账本债) |
|---|---|---|
| P-A 入场确认门 | 候选 → 确定性确认触发(首次收复/动能/放量代理)→ T+1 入 | 三臂:`ranker` vs `+确认门` vs `+同延迟随机入场 placebo`;须严格胜随机延迟 placebo + 净 trade-off(防「晚入=少吃下跌暴露」机械效应) |
| P-A EXIT 对称 | 避顶部须确认滚顶(动能转弱)非纯延展 | 见避顶部刀(§A4 EXIT-专属 placebo + P&L 四分解 + missed-rally/false-exit) |
| P-B 强制止损 | 每持仓含确定性硬止损,反常下跌硬触发(不等确认) | 作每臂 baseline 一部分;消融「无止损」证保护值 |
| P-C 做T profit-gate | 做T 仅当持仓正浮盈;worst-case 日序;底仓地板;守 T+1;≤1 round-trip/日 | 做T增益(扣 ¥5 min 佣金)严格胜同频随机择时 placebo;profit-gate 消融 |
| P-D 安全底线 | 下行保护硬门(逆境非永久套牢 + 止损生效)优先 | 「稳定可观」定义:永久套牢 = FAIL;MDD/缩量回调仅披露 |
| P-E 置信集中+buffer | 置信加权 sizing 层(单股无固定 cap、单名可达 ~60%、≥40% buffer、≤5 名);走同一忠实 fill/成本/账本 plumbing | sizing 配置付账本债 + 与等权 5 槽对照;byte-exact 不变量以等权为 plumbing 参照 |

## 3. live 侧(本 amendment 不改 live;仅标注 owner-gated)

- **P-E 取消单股 15% cap → live RiskEngine check#5(`position_limit` 单股≤15%)/ §2.4 仓位三连(单股≤15%/总仓≤70%/单笔≤5万)** 改动 = **live 部署须独立 owner-gated amendment + RiskEngine 改 + 对抗测试 + 重启**。本研究侧仅模拟器配置,**不碰 live RiskEngine/governance enum/单一构造点**。诚实 caveat:总仓 ≤60%(≥40% buffer)比现行 ≤70% 更严(不放松总仓);单股 ~60% 比现行 ≤15% 更松(集中度提高)→ live 须 RiskEngine amendment。
- **P-A/P-B/P-C** 的 live 对应(Line-2 监控确认门 / 强制止损 / value_swing profit-gate)= 后续 live 激活时 owner-gated;本研究先证其在模拟器内的价值。

## 4. 红线合规

全留:sim 暂停 · 永禁真实下单 · 研究/评测零 LLM(live evidence-only)· 做T 守 T+1 · 不碰 backend value-sleeve/引擎字节/RiskEngine/单一构造点(研究模拟器 byte-exact 核对)· 改判据不清零 mining 债 · 反过拟合四门不放宽 · FAIL 报 FAIL · push/摄取/live 激活 owner-gated · codex 前置门(代码刀)。
