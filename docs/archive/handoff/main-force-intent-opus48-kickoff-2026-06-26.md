# 接手 kickoff prompt — 主力意图研究纲领(新 Opus 4.8 上下文)

> owner 2026-06-26 指示:commit 后写此 prompt,用新的 Opus 4.8 上下文继续推进。**下面整段可直接粘贴到新 session。**

---

你是 Claude(Opus 4.8),接续 QuantMind 的**「主力意图大数据研究纲领」**。这是**全新上下文 —— 先读下列文档,再动手**(你没有上一个 session 的记忆,文档是权威)。

## 先读(按序)
1. `docs/research/main-force-intent-research-program-macro-direction-2026-06-26.md` —— **宏观纲领,权威方向**(重点:§2 关键发现 + §2.10 吸筹/洗盘实证 + §4 五层基建蓝图 + §6 分阶段路线 + §10 接手清单)。
2. `docs/research/main-force-intent-lowbase-transition-system-design-2026-06-26.md` —— 窄战术(低位结构转折)+ **时序 MASK 测试协议** + codex 两轮对抗。
3. `docs/research/quant-first-gate-rearch-plan-2026-06-21.md`(QGR 总框架)+ `docs/research/data-inventory-marketdata-pit-2026-06-21.md`(已摄 PIT 数据清单)。
4. `CLAUDE.md` §2 核心红线 + `docs/plan.html` 的 `#session-log`。
5. memory:`~/.claude/projects/-home-ps-papers-QuantMind/memory/project-main-force-intent-research-program-2026-06-26.md`(+ MEMORY.md 索引)。

## 已确立的方向(不要重新论证,直接执行)
- **owner 结构命题已采纳**:A 股大波段 = 主力/机构驱动(国情决定的客观现实,非玄学)。**让真 A 股数据决定规律,别用美股文献预判**;但 **PIT + 反过拟合严格性不可旁路**(owner 同此立场)。
- **载重发现 = 非对称**:主力足迹的可交易边 **RISK/EXIT/避险闸 ≫ ENTRY 择时 alpha**。吸筹 vs 派发在日频 ex-ante 几乎不可分(连民生 Wyckoff 无泄漏版只 IC~0.07);筹码类因子多是 CGO/反转/size 换皮。→ **先做风控/退出/避险闸;进场用低位结构 + 逆向 + 质量;吸筹择时进证伪台账等真数据。**
- **判据** = 绝对净盈 + MDD≤8%(去 CSI300 超额硬门);**≤5 持仓第一闸门**;命中放量大涨 = **诊断**(fills-aware 分母),**非晋级门**(晋级 = QGR-2 事件循环净 P&L)。

## 第一刀(本 session 目标)
1. **P0 基础设施**:L0 扩 PIT 端点(优先 `stk_holdertrade` 减持)+ **future-NaN 投毒泄漏门**(纲领 §4/§7;同 K-001 字节存档纪律)。
2. **批 A = RISK/EXIT 拥挤闸**(先验最强、最契合控回撤):拥挤度(**float-cap 归一**,90–95 分位极值)+ 反转 blow-off → 作 **REDUCE/EXIT/veto**,跑现有 CPCV 竞技场 + DSR/PBO/SPA + 非清零账本。
3. 对**纲领 + 批 A spec 跑 codex 前置门**(codex CLI 超时则 `/code-review high` 兜底)。

## 强制约束(违反即停)
- 严格性不可旁路;**改决策边界先落 amendment**;**有代码编写的任务 commit 前过 codex 前置门**;**FAIL 报 FAIL**。
- **size/行业中性化 + 删最小 30%**(否则重蹈 round-1..4 的 size-tilt 死法;Feb-2024 微盘崩盘就是它爆炸)。
- **不接入 `moneyflow` 信号路径**(符号翻转 + 可对敲欺骗);**北向仅历史研究**(日度数据 2024-08 已死);**不把「主力意图」当产品宣称**(= 主力意图假说,可交易内核诚实命名为「低位结构转折」)。
- **绝不碰** `backend/` value-sleeve 域(AF-*)+ `scripts/factor_research/` 既冻结字节/locked split(除非显式 amendment)。
- **sim 暂停**贯穿至真前向确认。
- **重活分批跑、别一次 fan-out 太多 agent**(上 session 撞限流的教训;子 agent 明令"自己搜、不再派子 agent")。
- **push origin main 须 owner 授权**;commit 落本地。**面向 owner 报告用中文**,thinking 用英文,代码/commit 英文。

## 两条非阻塞深挖(纲领 §8,有余力再做)
- filtered-state(在线 HMM)相位检测的诚实实现;
- 理想振幅因子的独立 size-中性化复测(券商 size-neutral 声明未经复现)。

先按项目协议梳理本 session 的 P0 + 批 A 子任务清单(plan.html 无对应任务则按纲领 §6 建 Phase/任务再认领),再动手。
