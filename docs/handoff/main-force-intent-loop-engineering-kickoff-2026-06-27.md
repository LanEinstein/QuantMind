# 主力意图研究 · loop-engineering 自驱动接手 prompt(MDD 控制专项)

> 用法:新 session 用 `/loop`(**不带间隔 = 自定步速 self-paced**)把下面「PROMPT」整段交给模型。它会自动循环:调查 → 分析 → 实验 → 测试 → 复盘 → 改代码 → 再实验……直到**达标**或**诚实证伪止步**。owner 睡觉期间全自动,留清晰 SESSION_LOG 轨迹供晨起复核。push / 数据摄取 / live 激活全 owner-gated,loop 绝不自作。

---

## PROMPT(整段复制到 `/loop` 后)

你是 Claude(Opus 4.8 / Fable 5),以**自驱动 loop**接续 QuantMind「主力意图大数据研究纲领」。**唯一北极星 = owner 判据:稳定绝对净盈 + 控回撤(MDD≤8%)**。clean context —— 先读文档,文档是权威,你没有上个 session 记忆。

### 0. 先读(按序,权威)
1. `docs/research/qgr-4-exit-veto-ablation-results-2026-06-26.md` —— **本研究当前最重发现(你的起点)**:≤5 槽 long-only 反转排序器(及其拥挤买入集 veto / placebo 变体)2015–2025 全窗口 **MDD 54–58% ≫ 8% cap**;买入集 veto 不控回撤(反升)。结论:**可交易边在『真去暴露/避险/减持』侧,不在『买入集过滤』侧**(印证纲领 §2.1 非对称在系统层)。
2. `docs/research/main-force-intent-lowbase-transition-system-design-2026-06-26.md` §6.5/§6.6(regime 分层 + 三臂消融/placebo 纪律)+ `docs/research/main-force-intent-research-program-macro-direction-2026-06-26.md`(纲领;批 B 避险轮动锚点)。
3. `docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md`(**评测口径已冻结,不准动**:主指标=事件循环扣成本净 P&L,硬约束=MDD≤8% + 跨 CPCV 稳定为正;DSR floor 0.95;非清零账本 legacy floor N≈2383)。
4. `docs/research/quant-first-gate-rearch-plan-2026-06-21.md` §2.1/§4/§7(系统真实角色 + 两层评测 A/B)。
5. `CLAUDE.md` §2 + 原则 0/1/4 + `docs/plan.html` SESSION_LOG 顶 2 条(2026-06-26)+ memory `project-main-force-intent-research-program-2026-06-26.md`(含本刀全细节 + 坑)。
6. 复用件 grep 核实(别假设):`scripts/factor_research/` 的 `exit_veto_panel.py` / `exit_veto_ablation.py`(本刀竞技场驱动模板,直接 fork)/ `gate_backtest.py` / `gate_bar_source.py` / `baselines.py` / `multi_strategy_compare.py` / `honest_gates.py` / `trial_ledger.py` / `cpcv.py` / `neutralize.py`。

### 1. 达标定义(冻结判据,绝不放宽 —— 这是 oracle 不是建议)
一个 overlay/策略「**达标 PASS**」须在 **train_val** 上**同时**过 5 门(pre-committed,§6.6 + qgr-2 freeze):
① 绝对净 P&L > 0(扣成本,复利);② **MDD ≤ 8%**;③ 跨 CPCV combination 稳定为正(frac_positive ≥ 0.80);④ **DSR ≥ 0.95**(对非清零累计 N 去通胀);⑤ **严格击败 placebo**(同平均暴露/同通过率的「无技艺」对照,配对 t ≥ 2.0)+ 击败可部署 beta baseline(SPA/Romano-Wolf vs CSI300 买入持有)。regime 分层逆境不毁灭(熊市非灾难)。
> **达标 ≠ 上线**:train_val PASS → git 冻结 + 写 B 层前向确认预注册(`PreRegistration`,content-addressed,只看一次);**真前向/lockbox/go-live/sim 重启全 owner-gated**。loop 到 train_val PASS 即止于此步,不烧 test、不上线。

### 2. 本 loop 的任务 = 解 MDD 控制问题(本刀已证排序优化 + 买入集过滤走不通)
火力转向**真去暴露/避险/减仓**。优先级 backlog(各一完整实验,可证伪,literature/前沿 provenance-gated 取灵感再从零验):

- **B1(先做,最直接攻 MDD)= regime-gated 暴露 de-risk overlay**:确定性 crash/risk regime 检测(**无前视 PIT-clean**,从 CSI300 trailing 回撤 / 已实现波动 / 市场宽度〔%>MA〕/ 拥挤 cohort 负荷派生)→ 高危 regime 节流 gross 暴露(少填槽 / 持现金 / 一槽切防御 ETF)。**三臂 = baseline(不节流)/ de-risk overlay / placebo(同平均暴露缩减但 regime-盲:恒定 cash% 或随机择时节流)**。诚实预期:节流必降 MDD(少暴露),真问题 = **是否击败"恒定持现金"naive placebo**(regime 择时是否加值,而非仅降 beta)+ 净 P&L 是否存活 + **DSR/CPCV 是否扛得住 regime 阈值过拟合**(regime 参数是 DOF 雷区,**预承诺、别在结果上调**)。
- **B2 = 防御目的地(红利低波)**:先 grep PIT 库 `fund_daily` 有无红利/低波 ETF(如 510880/512890/515080 等,确认覆盖范围)→ de-risk regime 把书轮进它(非仅现金)。三臂 vs 纯现金 de-risk + 随机目的地。
- **B3 = 拥挤触发持仓 REDUCE/EXIT**(非买入集 veto):对**已持仓**拥挤名(`ideal_amplitude_20d_neut crowd_pct ≥ 阈`)标记保护性退出(经 health `hard_exit`/`protective_stop` 派生,只增卖压永不松止损)。三臂 vs placebo(剔同数量随机持仓)。
- **B4(owner-gated,默认 DEFER)= `stk_holdertrade` 减持硬排除**:须 owner 授权摄取(同 K-001 PIT 字节存档)→ owner 睡觉期间**不摄取**,留锚。

### 3. loop 单次迭代(闭环,每轮推进知识)
1. **Orient**:读 SESSION_LOG 顶条 + memory + 最新 results doc → 定位当前最优诚实结果 + 在途/未做 backlog。**确定本轮唯一实验**。
2. **Spec**:写/更新 spec 锚点(机理 + PIT 协议 + 三臂/placebo 设计 + 预承诺阈值 + 证伪台账「预承诺报 FAIL」)。research 侧离线 = spec 即可;**真改 live 决策边界才需 amendment**(本 loop 全离线研究,不改 live 一行)。
3. **Implement**:新建 `scripts/factor_research/<name>.py`(fork `exit_veto_ablation.py` 模板,**reuse 竞技场**)。**绝不动**:`gate_backtest`/`gate_bar_source`/round-1..4/QGR 既冻结面板字节 / `backend/` 引擎字节 / backend value-sleeve(AF-*)。
4. **codex 前置门(强制)**:`/code-review high`(codex CLI 撞限流就用它)→ 修完所有 P0/P1/P2(CONFIRMED + 关键 PLAUSIBLE)。
5. **本地门禁**:ruff + mypy strict + `pytest tests/factor_research`(写新单测覆盖确定性变换)+ redline 扫描(无 `import backend.{llm,agents,mirofish,risk}`)全绿。
6. **Smoke**:`--smoke-periods 8`(或 fork 等价)先验端到端非退化 + 不写真账本(加 guard)。
7. **Full run(后台,自定步速)**:**~40 分钟**(PitBarSource iterrows 全窗口慢)→ **必后台 + `python -u` + 自调度**;别前台(2min timeout)。用 `ScheduleWakeup`(~1200–1800s)轮询 JSON 就绪;别 busy-poll。
8. **Analyze**:对 5 门 + regime + veto/overlay bite + 与 placebo/beta 对比逐项算;**FAIL 报 FAIL**,不洗白。
9. **Report**:写 `docs/research/<name>-results-*.md`(真数字 + 诚实裁决 + caveat)+ 账本 append(family 唯一,**改判据不清零**)+ plan.html SESSION_LOG + 修订记录 + memory。
10. **一个 feature commit / 一个实验**(代码 + 结果分 commit 亦可,沿本刀 `b11fcef`/`65e067d` 粒度)。**push 待 owner 授权**(commit 落本地,绝不并进 commit 命令)。

### 4. 停机条件(关键 —— 有界的诚实,别空转别造假)
- ✅ **达标 STOP**:某 overlay 在 train_val 过全 5 门 → git 冻结 + 写 B 层前向确认预注册 → **STOP**,写终版总结(交 owner 决前向/go-live)。
- 🛑 **诚实证伪 STOP**:把 B1→B3 principled backlog 跑完后若无一过门 → **STOP**,写诚实负面结论 + 最优诚实 partial(如「MDD≤8% 可达但净 P&L 转负 / 不胜恒定持现金 placebo / DSR 不过」)+ 它对纲领的含义(可能 = 『≤5 槽 long-only 在 MDD≤8% 下无可部署边,须换问题框定』,交 owner 重定方向)。**绝不**为达标:放宽门 / 调 regime 阈值拟合结果 / 烧 test 集 / 把暴露缩减伪影当 alpha / 无限循环重跑同一实验。
- 每轮须新增一个**可证伪的新假说**(provenance-gated),不盲目重跑。预算意识:重活分批,别 fan-out 过多子 agent(易撞限流,reset 2pm Asia/Shanghai)。
- **反过拟合门是真预言,信它**(原则 1):低 DSR/勉强显著 = provisional,不当达标。

### 5. 红线(全留,违反即停)
sim 暂停贯穿 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only(test 封存,真 OOS=B 层)** · 防火墙断言实际 bar-read 窗口 ⊆ train_val · LLM 永不进 PIT/评测路径 · governance enum 不动 · 不接 `moneyflow` · 北向仅历史 · **不碰 backend value-sleeve(AF-*)/ 既冻结面板字节 / 引擎字节** · 改 live 决策边界须 amendment(本 loop 不改 live)· codex 前置门 · 四门不放宽 · FAIL 报 FAIL · **push / 数据摄取 / live 激活 / 阈值定标到 live = owner-gated** · 报告中文 / 代码 commit 英文。

### 6. 速查
```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 门禁
$PY -m ruff check scripts/factor_research/<f>.py tests/factor_research/<t>.py
$PY -m mypy scripts/factor_research/<f>.py
$PY -m pytest tests/factor_research -q          # 基线 627 passed
grep -rnE "import backend\.(llm|agents|mirofish|risk)" scripts/factor_research/<f>.py   # 必空
# smoke + full(后台 -u)
$PY -u -m scripts.factor_research.<name> --smoke-periods 8 --out /tmp/.../smoke.json
nohup $PY -u -m scripts.factor_research.<name> --out data/factor_research/<name>_result.json > <log> 2>&1 &
```
数据资产清单 `docs/research/data-inventory-marketdata-pit-2026-06-21.md`;Tushare 权限 memory `reference-tushare-entitlements-8000-2026-06-20`;PIT 库 `data/marketdata_pit/`(gitignored,~29GB,禁重下)。

**先按项目协议梳理本轮子任务清单,再动手。从 B1 起手。**

---

## 给 owner 的一句话(晨起复核指南)
loop 会在 SESSION_LOG 顶部逐实验留条(B1/B2/B3…),每条含 commit hash + 真数字 + PASS/FAIL 裁决。**若看到「达标 STOP」**→ 有 train_val PASS 候选,等你拍前向确认;**若看到「诚实证伪 STOP」**→ 已穷尽 principled backlog 无过门,附最优 partial + 重定方向建议。push 全待你授权(本地 commit 链已备好)。
