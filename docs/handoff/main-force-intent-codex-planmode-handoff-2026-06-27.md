# 主力意图研究 · 新 session 接手 prompt(三步法:读文档 → Claude+codex 起草大纲 → plan mode 详细计划 + 科学评价协议 + 1 万本金真实模拟验证盈利)

> **用法**:新 session 把下面「PROMPT」整段交给模型。owner 把工作方式从「自驱动单刀实验」升级为**结构化三步法**:① 读文档熟悉已做/待做 ② Claude 联合 codex 起草后续行动规划**大纲**(逻辑严密,重视真实数据测试 + 大数据分析,双方无异议才进下一步)③ 用 plan mode 制定**详细工作计划**,核心是设计**科学公正的系统能力评价协议**,在**近期真实数据 + 股灾期间数据**上**真实模拟**(初始资金 **1 万元**),验证系统能否**稳定而可观盈利**。**push / 数据摄取 / live 激活 / EnterPlanMode 后的实施全 owner-gated。**

---

## PROMPT(整段复制到新 session 后)

你是 Claude(Opus 4.8 / Fable 5),接手 QuantMind「主力意图研究 / 量化第一闸门」工作。**clean context —— 先读文档,文档是权威,你没有上个 session 记忆。** owner 已把判据从「MDD≤8%」重定为「**避顶部派发 + 动态退出 + 做T + 短窗稳定可观净盈**」(详 amendment)。**本次严格按 owner 指定的三步法推进 —— 不自动跑单刀实验,而是先严密规划(Claude+codex 双重把关)再用 plan mode 出详细计划 + 科学评价协议,最后用 1 万本金真实模拟验证系统盈利能力。每一步完成向 owner 报告等确认。**

### 第一步:读文档,熟悉已做与待做工作(按序,权威)

1. **`docs/decisions/qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md`** —— **最新判据重定(你的判据基础,必先读)**:弃 MDD≤8% 硬门 → 新主判据 = 规则驱动持有期稳定可观净盈(智能动态退出非固定 horizon)+「不被挂山顶」硬门(避顶部派发)+ MDD 仅披露 + 做T(T+1 负成本);**反过拟合四门绝不放宽**;做 a 不做 L2;诚实分级(量/筹码有据,大资金/洗盘判别弱死,洗盘vs派发须账户级数据=拿不到)。
2. **B1/B2 两刀结果**:`docs/research/b1-regime-derisk-results-2026-06-26.md` + `b2-defensive-sleeve-results-2026-06-27.md`(均 FAIL on 旧 8%,触发判据重定;**结构墙** = rotation-only 竞技场任何 overlay 控不住 MDD;**B2 perm_cash2** 永久 2 槽现金净盈翻倍+MDD 减半但 DSR 0.044 不过门 = provisional)。
3. **结构墙前置**:`docs/research/qgr-4-exit-veto-ablation-results-2026-06-26.md`(买入集 veto 不控回撤)+ `mfi-batch-a-crowding-results-2026-06-26.md`(拥挤=RISK/EXIT 真信号 A1/A2 PASS)。
4. **评测竞技场**:`docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md`(主指标 + CPCV + 非清零账本 + baseline 面板 + Layer-B 前向;**注:§1.2 MDD≤8% 已被 amendment 重定,其余口径有效**)+ `quant-first-gate-rearch-plan-2026-06-21.md` §2.1/§4/§7(系统真实角色 + 两层评测 A/B + proxy 边界)。
5. **纲领 + 时序 MASK**:`docs/research/main-force-intent-research-program-macro-direction-2026-06-26.md`(§2.1 非对称 RISK/EXIT>>ENTRY、§2.3 避险目的地、**§2.10 吸筹派发本土实证 + 诚实分级**、§7 数据扩摄优先级)+ `...lowbase-transition-system-design-2026-06-26.md` **§6 时序 MASK「伏击」模拟器测试协议**(owner 点名要的真实模拟设计,直接据此设计评价协议)。
6. **协作 + 进度**:`CLAUDE.md` §2(红线)+ §3(工程原则)+ 研究专项原则 0-7 + `docs/plan.html` SESSION_LOG 顶 3 条(B1/B2/判据重定)+ memory `~/.claude/projects/-home-ps-papers-QuantMind/memory/project-main-force-intent-research-program-2026-06-26.md`(全细节 + 坑)+ `MEMORY.md` 顶部活跃线。
7. **复用件 grep 核实(别假设)**:`scripts/factor_research/` 的 **`arena_ablation.py`(共享:PIT 防火墙〔no-look-ahead 红线〕/ 非清零账本 / buy-hold→ArmResult / protected health,直接复用)** / `exit_veto_ablation.py` + `derisk_regime_ablation.py` + `defensive_sleeve_ablation.py`(三刀消融模板,fork)/ `gate_backtest.py` + `gate_bar_source.py`(真事件循环 + PIT bar source,初始资金参数化)/ `cpcv.py` / `honest_gates.py`(DSR-HAC/ONC)/ `trial_ledger.py`(非清零)/ `multi_strategy_compare.py`(SPA/RW)/ `neutralize.py`(size/行业中性)/ `build_qgr_panel.py`(快腿因子面板)/ `bottom_confirmation.py`(底部确认门)/ `crowding_factor_diagnostics.py`(崩盘概率条件)/ `cyq_perf_pit.py`(筹码)/ `limit_board_pit.py`(涨停结构)。数据资产清单 `docs/research/data-inventory-marketdata-pit-2026-06-21.md`(~29GB/23 端点,禁重下)。

**第一步产出**:向 owner 报一句话总结 ——「**已做**:QGR-1 摄取 / QGR-2 竞技场+口径冻结 / QGR-3 快腿因子库+底部确认门 / batch-A 拥挤 EXIT(A1/A2 PASS)/ QGR-4 买入集 veto(FAIL)/ B1 regime de-risk(FAIL)/ B2 永久防御 sleeve(FAIL on 旧8%)→ owner 判据重定」+「**待做**:避顶部派发 EXIT、减持硬排除、做 T overlay、动态退出整合、**系统级端到端盈利验证(1 万本金)**」。

### 第二步:Claude 联合 codex 起草后续行动规划大纲(双重把关,均无异议才进第三步)

- **Claude 起草** `docs/research/system-roadmap-outline-2026-06-27.md` = 后续行动规划**大纲(非详细计划)**,逻辑严密,**重视真实数据测试 + 大数据分析**。须覆盖:
  - (a) **系统组件端到端组装**:量化第一选股闸(快腿幸存因子 + 底部确认 + 主旋律资格)→ 避顶部派发 EXIT(持仓退出叠加)→ 做 T overlay(T+1 摊低成本)→ 动态退出(规则驱动持有期)→ 减持硬排除;每组件的输入/输出/red-line 合规;
  - (b) **每组件信号假说 + 从零验路径 + 诚实分级**(§2.10:量/筹码有据;趋势持续/大资金弱;洗盘判别 NULL);
  - (c) **大数据分析方法**:海量真 A 股 PIT 数据找规律(不靠固有认知/网络三言两语),size/行业中性化 + 删最小 30% 必做(防 round-1..4 死法);
  - (d) **真实测试方法**:真竞技场(`gate_backtest`)+ CPCV(按日期分组)+ 反过拟合四门(DSR≥0.95/PBO/SPA/RW,**不放宽**)+ 非清零账本 + 制度分层(含**股灾期切片**)+ fills-aware + look-once 前向;
  - (e) **系统级盈利验证总体设计**(对接第三步评价协议)。
- **codex 对抗审查大纲**:`cd <repo> && codex exec --sandbox read-only "<把大纲交给它做方法学对抗>" </dev/null`(**注意 `</dev/null` 防 stdin deadlock**;撞限流 → `/code-review high` 兜底 或 `codex-oracle` agent)。让 codex 专找:逻辑漏洞 / 过拟合陷阱 / 数据泄漏(future leak)/ 不可交易标签(一字板/T+1/封板幻想成交)/ size 污染 / in-sample 营销 / 把暴露缩减伪影当 alpha。
- **迭代到 Claude 与 codex 均无异议** → 把 codex 意见 + 收敛记录写进大纲文档末尾 → **向 owner 报告大纲 + codex 收敛,等 owner 确认进第三步**。

### 第三步:plan mode 详细工作计划 + 科学公正系统能力评价协议(owner 最看重)

用 **EnterPlanMode** 制定详细工作计划(任务分解 + 依赖 + 每任务 gate + codex 前置门),**核心是设计科学公正的系统能力评价协议**:

- **数据两类**:
  - **股灾期间数据**(逆境不毁灭检验):train_val 内股灾切片 = 2015-06 股灾 / 2016-01 熔断 / 2018 熊市 / 2020-02 疫情 / 2022 调整 / 2024-02 微盘崩盘 —— 系统在这些窗口须**不被挂山顶**(避顶部 EXIT 生效 + 缩量回调可接受、非永久下跌)。
  - **近期真实数据**(处子 OOS):test 封存窗 / lockbox / test_end 2026-06-12 之后新数据 —— **owner 授权才烧**(test 集封存红线;look-once);近期 = 检验系统在当下市场的真实盈利。
- **初始资金 = 1 万元**(贴近实盘 ¥9k Small 档):**这是实盘现实验证**——小资金下整手(100 股)+ ≤10% ADV 撮合 + 单股 15% cap(¥1500)+ T+1 + 做T T+1 + budget-adaptive 分层(Micro<¥2k 仅 ETF / Small / Normal≥¥10k 三连)**咬合更紧**,比 100 万纯因子研究更能验证「实盘能否稳定盈利」。`gate_backtest` 的 `initial_capital_yuan` 参数化为 10000。
- **真实模拟**(端到端,非单因子):真事件循环 + fills-aware + 涨跌停不可成交 + 分板块滑点 + 减持排除 + 避顶部 EXIT + 做T overlay,**整套系统**跑。对接 `lowbase-transition-system-design` §6 时序 MASK「伏击」模拟器(as-of T−1 收盘 → T+1 可成交入场 → 规则驱动持有 → 避顶部/破位/thesis 失效退出 + 做T;future-NaN 投毒泄漏门;三重屏障可交易标签)。
- **新判据评测**(amendment):① 规则驱动持有期**绝对净盈**(扣成本,1 万本金)② **避顶部命中**(及时退派发结构、不挂山顶)③ **做 T 增益**(更低成本→负成本)④ 跨 CPCV/regime **frac_positive 稳定为正** ⑤ MDD/缩量回调**仅披露** ⑥ **反过拟合四门(DSR≥0.95/PBO/SPA/RW)不放宽** + 非清零账本 + 制度分层。
- **「稳定而可观盈利」量化定义**(plan mode 内与 owner 敲定具体阈值):**稳定** = 跨 CPCV combination + regime(含股灾期)frac_positive ≥ 阈 + 股灾期不毁灭(非永久套牢);**可观** = 1 万本金绝对净盈年化 ≥ 阈(owner 定);**look-once 前向 + provisional 标记**(低 DSR / 勉强显著 = provisional 不当达标,原则 #1)。
- **EnterPlanMode 产出计划 → ExitPlanMode 交 owner 批准才实施**;实施仍每任务 codex 前置门 + 本地门禁全绿。

### 红线(全留,违反即停)

sim 暂停贯穿 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only(test 封存,真 OOS=B 层 owner-gated)** · 防火墙断言 bar-read ⊆ train_val · LLM 永不进 PIT/评测路径(LLM 综合研判 = evidence-only/advisory,确定性规则派生卖/做T)· 做 T 守 T+1 · 不接 moneyflow 主路径 · 北向仅历史 · **不做 L2** · governance enum 不动 · **不碰 backend value-sleeve(AF-*)/ 既冻结面板字节 / 引擎字节** · 改判据/决策边界须 amendment · codex 前置门 · **反过拟合四门不放宽(只放宽了 MDD)** · FAIL 报 FAIL · **push / 数据摄取 / live 激活 / 实施 = owner-gated** · 报告中文 / 代码 commit 英文。

### 速查

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 门禁
$PY -m ruff check scripts/factor_research/<f>.py tests/factor_research/<t>.py
$PY -m mypy scripts/factor_research/<f>.py
$PY -m pytest tests/factor_research -q          # 基线 657 passed
grep -rnE "import backend\.(llm|agents|mirofish|risk)" scripts/factor_research/<f>.py   # 必空
# codex(第二步对抗审查;前台 + </dev/null 防 stdin deadlock;撞限流→/code-review high)
cd <repo> && codex exec --sandbox read-only "<prompt>" </dev/null
# 真实模拟(后台 -u;PitBarSource 全窗 ~40min,detached nohup 不自通知须 ScheduleWakeup 轮询)
nohup $PY -u -m scripts.factor_research.<name> --out data/factor_research/<name>_result.json > <log> 2>&1 &
```
数据资产 `docs/research/data-inventory-marketdata-pit-2026-06-21.md`;Tushare 权限 memory `reference-tushare-entitlements-8000-2026-06-20`;PIT 库 `data/marketdata_pit/`(gitignored,~29GB,禁重下)。

**严格按三步顺序推进,每步完成向 owner 报告等确认。第二步 Claude 与 codex 须均无异议才进第三步;第三步 EnterPlanMode 出计划后 ExitPlanMode 交 owner 批准才实施。**

---

## 给 owner 的一句话(怎么用)

把上面「PROMPT」整段复制到新 session。模型会:① 读文档报「已做/待做」总结 → ② 起草行动大纲 + 拉 codex 对抗到双方无异议,报你确认 → ③ EnterPlanMode 出详细计划 + 科学评价协议(近期+股灾期数据、1 万本金、稳定可观盈利定义),ExitPlanMode 交你批准。**每步都停下等你拍板,不自动烧钱/不自动实施。** 当前 session 的自驱动 loop 我已停(B1/B2 + 判据重定已 commit 落地,push 待你授权)。
