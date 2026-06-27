# 量化研究 · alpha-pivot 接手 prompt(新 session:从『调风险容器』转向『做强选股 alpha』)

> **用法**:新 session 把下面「PROMPT」整段交给模型。这是 owner 2026-06-27 基于 **C1a 避顶部 FAIL + 槽数×sizing frontier** 数据**重定框定**后的方向:**容器问题已答(≤5 集中 + 现金 buffer = MDD 控制最优),真正的绑定约束 = 选股 alpha 质量(没有配置过 DSR≥0.95)→ 停止工程风险容器,集中把选股 alpha 做强**。**push / 数据摄取 / look-once 前向 / live 激活 = owner-gated。**

---

## PROMPT(整段复制到新 session 后)

你是 Claude(Opus 4.8 / Fable 5),接手 QuantMind「量化第一闸门」研究的 **alpha-pivot**。**clean context —— 先读文档,文档是权威,你没有上个 session 记忆。** owner 已基于真数据重定框定。**本次按下面框定推进:不再调风险容器(已答),集中做强选股 alpha。每完成一个有意义节点向 owner 报告等确认;不自动 push、不自动摄取数据、不烧 test 集。**

### 第一步:读文档(按序,权威;别假设,grep 核实)

1. **重定框定的数据依据(必读,先读)**:
   - `docs/research/slot-frontier-results-2026-06-27.md`(**槽数×sizing frontier**:① 分散**伤害**〔槽 5→50 MDD 升 56%→66%、净盈降、DSR 降;A 股股灾全市场相关→1,分散无保护只稀释集中反转 alpha〕② 现金 buffer **控回撤**〔N=5 降暴露 0.72→0.30,MDD 56%→31%,buf40_5 双杀 CSI300〕③ **无配置过 DSR≥0.95(eq_5 集中最高仅 0.0059)→ 绑定约束=alpha 太弱非容器**)。
   - `docs/research/c1-avoid-top-exit-results-2026-06-27.md`(避顶部 EXIT-on-held FAIL on 每门:与短线反转 alpha 正面冲突,EXIT 砍刚买入的超卖名=反弹前砍仓;P-B 机械 −12% 止损本身亦净有害)。
   - `docs/decisions/qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md` + `qgr-confirmation-stop-swing-sizing-amendment-2026-06-27.md`(判据 + owner P-A..P-E 原则;**P-E 仓位口径=取消单股15%cap+置信集中+≥40%现金buffer 被 frontier 印证**)。
2. **量化第一闸门总纲 + 两层评测**:`docs/research/quant-first-gate-rearch-plan-2026-06-21.md` §4(评测学:A 可复用真 CPCV 竞技场 + SPA/RW 公平比 + 累计 trial 账本含 legacy 块 / B 稀缺前向确认 + go-live shadow replay)+ memory `project-quant-first-gate-rearch-2026-06-21`。
3. **现有 alpha 积木 + 已验**:`scripts/factor_research/factor_lib.py`(全因子:round-1 反转/价值/vol/turnover/amihud + R2 趋势/质量/成长 + R3 SUE/应计/资产增长 + **R4 分析师修正 report_rc** + QGR/QGR2 快腿 + crowding)+ `docs/research/factor-strategy-round{1..4}-result*.md`(round-4 = **分析师修正动量首个正交正超额 +2.68% provisional,已冻结 `ffc1db3` 等前向**)+ QGR-3 已验快腿 {rev_1d,max_5d,turn_spike}(`docs/research/qgr-3-*`)。
4. **数据资产清单(🚫 禁重下)**:`docs/research/data-inventory-marketdata-pit-2026-06-21.md`(`data/marketdata_pit/` ~29GB/23 端点:daily/adj/basic + 财报 + **report_rc 分析师** + cyq 筹码 + stk_factor_pro + 短线/主旋律集;Tushare 8000 档权限 memory `reference-tushare-entitlements-8000-2026-06-20`,⚠️ `tp`=利润总额非目标价〔目标价=`min_price`〕)。
5. **协作 + 全局原则**:`CLAUDE.md` §2/§3 + 研究专项原则 0-7(反过拟合门=真预言信它 / 不闭门造车 provenance-gated / 数据划分铁律 / test 已评 4 次第 5 次极慎 / `*_vip` 必分页 / 全程 PIT+幸存无偏)+ memory `MEMORY.md` 顶部活跃线 + `project-main-force-intent-research-program-2026-06-26.md`(全细节+坑)+ `feedback-owner-trading-principles-2026-06-27`。

**第一步产出**:向 owner 报一句话总结「**已做**:量化第一闸门 QGR-1..3 + round-1..4 alpha 搜 + 主力意图 RISK/EXIT 线(QGR-4/B1/B2/C1a 避顶部)全 FAIL + frontier 证容器已答(≤5+现金buffer 最优、分散伤害、无配置过 DSR);**重定框定**=绑定约束是选股 alpha 质量非容器;**待做**:**做强选股 alpha**(在固定 ≤5+buffer 容器里,provenance-gated 不挖矿,过四门 + look-once 前向)」。

### 第二步:框定(owner 2026-06-27 重定,务必内化)

**🧱 已答(别再调,别再 litigate):**
- **风险容器 = ≤5 集中 + 现金 buffer(P-E)**。frontier 证:分散**伤害**(A 股股灾全市场相关→分散无崩盘保护、只稀释集中反转 alpha + 削弱崩盘反弹);现金 buffer 是唯一控 MDD 杠杆(降暴露,组合层,buf40_5 把 56%→31%)。≤5 同时被双资本印证(¥1万 执行强制 ≤5;¥100万 alpha)。
- **EXIT/de-risk overlay = 净有害**(C1a:避顶部/止损与反转 alpha 冲突;B1/B2:regime/sleeve FAIL)。**不再建任何 held-EXIT / regime de-risk / 避顶部 overlay。**
- **风控不在选股闸层**:回撤靠组合层现金 buffer(降暴露)= 风险缩放器(MDD+return 正比缩,Sharpe 恒),不靠分散、不靠 EXIT。

**🎯 绑定约束 = 选股 alpha 质量(本 pivot 的全部焦点):**
- frontier 全配置 DSR ~0.003-0.006 ≪ 0.95;容器只在同一**弱 Sharpe 线**上换 MDD/return 比例 → **真问题是 ranker 的风险调整 alpha 太弱**。**做强 alpha = 把这条 Sharpe 线整体抬高。**
- 目标:在固定 ≤5+buffer 容器里,造一个**能扛 deflation(过 DSR≥0.95 四门)的选股 alpha**,再 look-once 前向。

**⚠️ 反 deflation 债铁律(本 pivot 成败关键):**
- 非清零账本 **N=2387**(round-1..4 mining 债)。**每多搜一次就加债 → DSR 更难过**。round-1..4 铁证:**越挖越难过门**;唯一正交正超额(分析师修正 +2.68%)来自**文献先验**非挖矿。
- **∴ 严禁 data-mine 网格搜。用 provenance-gated 文献/牛人先验 → 单一 committed spec(1 trial)+ 从零验符号**(round-4 范式:Lv 2025 / 分析师修正先验 → 从零验)。
- **测试集已评 4 次**,第 5 次极慎;优先冻结新 spec + 等真前向窗口处子 OOS(原则 #4)。

### 第三步:做法(三步法,同 C1;不重做已答的容器)

按 owner 偏好的结构化三步法:① 读文档报已做/待做 → ② 起草行动大纲(`docs/research/*-outline-*.md`:选股 alpha 候选 + provenance + 从零验 + 诚实分级 + 单一 committed spec 防债 + 在 ≤5+buffer 容器评测)+ **codex 2 轮对抗到收敛** → ③ EnterPlanMode 详细计划 + 科学评价协议 → **owner ExitPlanMode 批准** → 实施。

**候选 alpha 源(供大纲,非穷举;provenance-gated,优先已验/已有先验,最小化新债):**
1. **分析师修正动量(round-4 最强 lead,优先)**:`report_rc`(np_rev/rev_diff/tp_impl/rating_chg)= 信息流正交 alpha,round-4 兑现 +2.68% provisional,已冻结 `ffc1db3`。**最干净一步可能 = look-once 前向验这个已冻结候选**(owner-gated;但 DSR 当时低,慎);或并入更强复合。
2. **复合多源**:QGR-3 已验快腿 {rev_1d,max_5d,turn_spike}(反转)+ 分析师修正(信息流)+ AF-003 基本面质量(ROE/GPM/EP/应计)+ QGR-3 ⑧ 底部确认门(宇宙质量过滤)→ 单一 committed 复合 spec,在 ≤5+buffer 容器过四门。
3. **主旋律择场(QGR-3 ⑧ 维度,AF-001 theme map 已冻 `4e97db2`)**:主题 OVER baseline IC 增量(政策发布日 PIT)—— 但 owner 此前『等 theme-map 稳定』;视 owner。
4. **新文献先验**(若 1-3 不够):从前沿文献/牛人分享取**预声明** alpha(provenance 记来源)→ 从零验。
- **诚实分级 + FAIL 报 FAIL**:每候选标 ✅有据/🟡谨慎/🔴弱死;不过四门不得晋级「候选」。

### 红线(全留,违反即停)

sim 暂停贯穿 · 永禁真实下单 · 离线 · 仅 Tushare 官方 SDK · PIT 字节存档+checksum · **train_val only(test 封存,真 OOS=owner-gated look-once,第 5 次极慎)** · 防火墙 bar-read⊆train_val · **研究/评测零 LLM** · size/行业中性化删最小 30% · **反过拟合四门不放宽** · **非清零账本不清零,provenance-gated 单一 committed spec 防 mining 债** · 不接 moneyflow 主路径 · 北向仅历史 · **不做 L2 / 不判别洗盘vs派发**(账户级数据壁垒,owner 拍板) · **不再建 held-EXIT/regime-de-risk/避顶部 overlay**(已证净有害) · 不碰 backend value-sleeve(AF-*)/冻结引擎字节/RiskEngine/单一构造点 · governance enum 不动 · codex 前置门 · FAIL 报 FAIL · **push/摄取/live 激活/look-once = owner-gated** · 报告中文/代码 commit 英文。

---

## 复用件速查(grep 核实)

**竞技场 + 容器(固定 ≤5+buffer)**:`scripts/factor_research/gate_backtest.py`(`run_gate_backtest` / `default_strategy_config(max_total_positions, single_stock_cap_percent)` / `default_friction` 已含 ¥5min佣金/印花/过户/分板块滑点 / `default_selector(final_shortlist_size=N)`)+ `gate_bar_source.PitBarSource` + `slot_frontier.py`(槽数×sizing,buf40_5 容器参照)+ `e2e_simulator.py`(C0b,若需 overlay——但本 pivot 不建 EXIT overlay)。
**因子 + 面板 + 评测**:`factor_lib.py`(全因子)/ `build_qgr_panel.py`/`build_crowding_panel.py`(panel builder,PIT)/ `neutralize.py`(size/行业中性删30%)/ `leak_probe.py`(泄漏门)/ `honest_gates.py`(DSR-HAC/ONC)/ `multi_strategy_compare.py`(SPA/RW)/ `cpcv.py` / `trial_ledger.py`(非清零,family 预声明 hash 先于结果)/ `crowding_factor_diagnostics.py`(IC 从零+崩盘概率条件 模板)/ `arena_ablation.py`(防火墙/账本/baseline 共享件)。
**数据**:`data/marketdata_pit/`(gitignored,~29GB,**禁重下**;清单 `data-inventory-marketdata-pit-2026-06-21.md`)。

## 速查命令

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
$PY -m ruff check scripts/factor_research/<f>.py && $PY -m mypy scripts/factor_research/<f>.py   # 逐文件 mypy
FEISHU_INTERACTIVE_ENABLED=false $PY -m pytest tests/factor_research -q                            # 基线 688 passed
grep -rnE "import backend\.(llm|agents|mirofish|risk)" scripts/factor_research/<f>.py              # 必空
cd <repo> && codex exec --sandbox read-only "<review prompt>" </dev/null                          # codex 前置门(撞限流→/code-review high)
# 全窗真实跑(PitBarSource ~40-50min):⚠️ harness-tracked 后台 bash 在 turn 边界被杀
#   → 须 setsid 全脱离 + 自配 structlog WARNING 静音 backend INFO 洪流 + Monitor 工具轮询 completion
nohup setsid $PY -u <runner with structlog WARNING> > <log> 2>&1 < /dev/null &   # 详见 C1a 经验(memory 坑)
```

## owner 决策点(本 pivot 需拍板)

1. **alpha 方向**:先 look-once 前向验 round-4 已冻结候选(`ffc1db3`,DSR 当时低慎花),还是先建更强复合 spec 再前向?
2. **主旋律维度(QGR-3 ⑧)** 现在并入还是后置?(owner 此前『等 theme-map 稳定』。)
3. **若所有 honest alpha 都不过 DSR≥0.95**:接受 provisional 弱边等前向?还是重审「≤5 量化闸门」前提本身?(frontier 已暴露这是真实可能。)
4. **push** 全部本地 commit(C0a/C0b/B1/B2/QGR-4/batch-A/C1a Node1-3/frontier 一摞)。

---

## 给 owner 的一句话(怎么用)

把上面「PROMPT」整段复制到新 session。模型会:① 读文档报「已做(容器已答)/待做(做强 alpha)」总结 → ② 内化重定框定(不再调容器,集中 alpha,provenance-gated 防 deflation 债)→ ③ 三步法(大纲+codex 2 轮 → plan mode → 你批准)设计选股 alpha 实验,在固定 ≤5+buffer 容器过四门 + look-once 前向。**每节点停下等你拍板,不自动 push/摄取/烧 test。** 当前一摞本地 commit、门禁全绿、codex 过,push 待你授权。
