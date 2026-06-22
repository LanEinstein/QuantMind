# QGR-3 ⑦ 短线因子库 — 交付 + 接手锚点(完整快腿:反转+彩票剔除+1日动量+涨停结构)

> **状态**:**✅ ⑦ 完整快腿 done(tranche-1 `b4dbcdf` + tranche-2 `d7c0d5c`,本地未 push)** · **⑧ 主旋律映射 = owner 已定调,待实施冻结** · **日期**:2026-06-22 · **作者**:Claude(Opus 4.8)
> **这是什么**:QGR-3 build-new ⑦(短线因子库)的交付锚点 —— 因子设计 + **IC 从零验符号结果** + 数据划分 + 门禁/review + ⑧ owner 定调。主文档 = `docs/research/quant-first-gate-rearch-plan-2026-06-21.md`(§3 信号设计 / §6.1 build-new ⑦⑧ / §7 QGR-3 行);评测口径 = `qgr-2-eval-arena-freeze-spec-2026-06-22.md`(FROZEN)。
> **诚实红线**:强 IC = **必要非充分**(round 1-4 都强 IC 却 test FAIL 三次);本阶段**只建因子库 + 验符号,不搜索、不晋升**;诚实门(DSR/SPA/Romano-Wolf)永不指导搜索,真判据在 QGR-4 竞技场 + QGR-6 前向。

---

## 1. 范围(分两 tranche 落完整快腿)

- **tranche-1**(`b4dbcdf`):短期反转 + 强制负向(彩票)剔除 overlay(§3.1.1 + §3.2)。
- **tranche-2**(`d7c0d5c`):1 日动量(§3.1.2)+ 涨停结构特征(§3.3),含快腿 `fwd_ret_1d` horizon。

至此**快腿完整**。**底部确认门(§3.8B)= 慢腿,与 ⑧ 主旋律一起做**(§7)。⑧ 主旋律维度 owner 已定调(§6),实施在 ⑦ 之后。

## 2. 因子设计(provenance-gated;符号 R 阶段从零验)

新 `QGR_FACTORS` registry(`scripts/factor_research/factor_lib.py`),**独立于 round-1..4 registry → 历史 panel 字节不变**;全 attractive-low(高=该避);机制全复用既有 `EconomicMechanism` 值(**未动 governance 枚举**)。

| 因子 | 定义 | 机制 | 出处 |
|---|---|---|---|
| `rev_1d` | 1 日收益(反转) | mean_reversion | Gao-Jiang-Xiong-Xiong NBER 2023(A 股日内反转) |
| `rev_3d` | 3 日收益(反转) | mean_reversion | Carpenter-Lu-Whitelaw RFS 2021 |
| `max_5d` | 5 日内最大单日收益(短窗 MAX) | low_volatility_anomaly | Bali-Cakici-Whitelaw;Leippold-Wang-Zhou JFE 2022 |
| `turn_spike` | mean(近 5d)/mean(前置 20d 基线)−1(异常换手) | liquidity_premium | Nartea-Wu PBFJ 2018 |
| `n_limit_up_5d` | 5 日内涨停收盘次数(涨停截断的 MAX) | low_volatility_anomaly | "MAX is not the max under daily price limits" PBFJ 2021 |

**tranche-2 `QGR2_FACTORS`**(独立 registry;机制复用既有枚举):

| 因子 | 定义 | 先验 | 出处 |
|---|---|---|---|
| `intraday_ret_1d` | close/open−1(日内动量) | +1(续涨) | Gao et al. NBER 2023 |
| `overnight_gap_1d` | open/pre_close−1(隔夜跳空) | −1(反转,§3.6 陷阱) | §3.6 |
| `limit_streak_prev` | `<d` 连板数(limit_list_d,2020+) | −1(不追涨高位) | §3.3 / §3.8 |
| `broke_board_prev` | `<d` 破板 flag(open_times>0) | −1(fade) | §3.3 |

- **新 reader**:`limit_board_pit.read_limit_board`(`limit_list_d`,返回 `(available, records)` 区分 pre-2020 无快照〔None〕vs 不在榜〔0〕);涨停结构**严格用前一交易日 `<d`**(同日盘后才齐=前视)。
- **限价数据**:新 `limit_status_pit.read_limits`(`stk_limit` PIT 涨跌停价,RAW 同日,fail-closed)。`n_limit_up_5d` = RAW 收盘 ≥ RAW 涨停价的天数;`at_up_limit_d`/`at_down_limit_d` = 日 d 收盘是否封板(作列披露,不剪 cohort)。
- **panel**:`build_qgr_panel.py`(train_val,reuse `_cohort`/`_forward_returns`,`_QGRCodeSeries(_CodeSeries)`;cohort 与 round-1 同〔板块白名单+流动性+单价+底 30% size 剔除〕→ IC 可比);**sacred-split**:feature 窗 = train_val+embargo,`assert_all_not_test` 守门,test 物理不可达。
- **真 panel**(2026-06-22 真跑):**326,854 行 / 3003 码 / 498 rebalance 日 / 20150202..20250425**;QGR 因子覆盖 ~100%;`industry_l1` 66.3%(申万表缺退市码,同 R2-2);cohort 内 at_up_limit 2.16% / at_down_limit 1.21%。

## 3. IC 从零验符号结果(`qgr-3-short-factor-diagnostics-2026-06-22.md`)

R4-4 协议:中性化(行业+log市值)IC `|t|≥3` + 符号对 + 与 round-1 carry 簇 `|corr|≤0.7`。**t 是乐观 screen 非判据**(重叠前向窗自相关、3 horizon 取最优)。

**幸存 3 个(= QGR-4 快腿候选轴)**:

| 幸存 | 中性化 |t| | 与 round-1 最共线 | 读 |
|---|---|---|---|
| `max_5d` | **−11.3**(20d) | ret_5d 0.62 | 最强;短窗 MAX 彩票稳健 |
| `turn_spike` | **−4.6**(5d) | ret_20d 0.31 | 与 round-1 turn_20d(level)正交 → "换手加速度"是新轴 |
| `rev_1d` | **−3.0**(5d) | ret_5d 0.39 | 1 日反转;仅 5d horizon 有信号,10/20d 衰减到 0(文献"日反转快速衰减") |

**诚实丢弃 2 个**:
- `rev_3d` — **与 round-1 ret_5d 共线 0.72 > 0.7**(冗余,非无信号;本身 neut t=−6.8 强,但 ≈ 已有 5 日反转,不是新轴)。
- `n_limit_up_5d` — **中性化后符号翻转**(raw t=−13.1 负,neut t=**+2.7 正**)→ raw 彩票信号大半是 size/行业假象,涨停本身边际不干净 → 丢弃。**印证主文档 §3.6 陷阱清单"涨停结构小心当 size proxy"**。

**§3.1 限价 loser-leg 披露实证兑现**:排除跌停名后反转 IC **增强**(rev_1d −1.88→−2.84,rev_3d −3.87→−4.40)→ 证实"跌停 falling-knife 污染 loser leg",排除后信号更干净(主文档预言成真)。strategy(QGR-4)剪 un-buyable 封板名;诊断保留它们使 IC 无偏 + 披露效应。

**tranche-2(1日动量+涨停结构)= 4 个全诚实丢弃**,且揭示 2 个与文献先验相反的符号翻转(强 |t|,但**不事后翻先验"拯救"= 反 p-hacking 纪律**):

| tranche-2 因子 | 中性化 IC | 判定 | 读 |
|---|---|---|---|
| `intraday_ret_1d` | neut **t=−7.4**(1d,先验+) | misaligned 丢 | 日频"日内强势"票 T+1 **反转**非续涨 → **A 股日频快腿是反转不是动量**(日内动量需分钟数据);印证 §3.1.2 |
| `overnight_gap_1d` | neut **t=+8.1**(10d,先验−) | misaligned 丢 | 隔夜跳空在 close-to-close 多日是**正向/动量**,与 §3.6 陷阱预期反转相反(陷阱或特指日内逆选择) |
| `limit_streak_prev` | raw t=−7.85 → **neut t=+2.2 翻+弱** | 中性化崩塌丢 | raw 是 size/行业假象;support 仅 157 日(post-2020) |
| `broke_board_prev` | neut t=−0.83 无信号 | 丢 | 中性化吃掉;support 89 日 |

→ **诚实负面结果**:快腿动量/涨停结构在严格中性化+从零验符号下**都不是干净可加排名轴**(符号与机制先验冲突 / size 假象)→ 强化 tranche-1 的 `{rev_1d, max_5d, turn_spike}` 是快腿核心。涨停结构印证 §3.3"只作特征/过滤,不当排名轴";日频动量印证主文档"两条腿对比"的关键:**T+1 快腿若用日频信号是反转效应**(真超短动量须分钟数据,本研究 universe/数据不支持)。`limit_list_d` 2020+ → 涨停结构 post-2020 only(§1 定义率 61% = post-2020 占比,非数据丢失,已披露)。

## 4. 数据划分铁律(遵守)

从零数学提取新因子 → 用既有 locked split(train_val 20150202..20250430,**test 20250604..20260612 封存未读**);CPCV 池 = 2015..2026-06-12;消费 `data/marketdata_pit/` 既有 PIT(禁重下);复权 pin / `<d` 无前视 / 幸存无偏 cohort 全留。诚实门**永不**指导本阶段(只验符号,不搜)。

## 5. 门禁 / review

- **tranche-1**(`b4dbcdf`):30 新测试;`/code-review high` 4 路 finder → 修 1(`limit_up_count` 等长守卫)+ 1 文档。
- **tranche-2**(`d7c0d5c`):20 新测试;`tests/factor_research/` **537 passed**(round-1..4 + tranche-1 字节不变);ruff + mypy strict + `redline-check.sh` 全绿;零 backend 改动 / 永不碰 live;机制复用既有枚举(未动 governance)。**`/code-review high` 3 路 finder**:PIT `<d` 平移 / 字节稳定 / availability None-vs-0 / 参数顺序**全部独立确认 clean**;实修 `broke_board_prev` fail-closed 对称性('U' 日 open_times 缺失 → None)+ 空 limit 串 → None + 诊断 post-2020 覆盖 caveat。codex 仍因在超时内真跑构建不可用 → `/code-review high` 兜底(`[[feedback_codex_rate_limit_fallback]]`)。

## 6. ⑧ 主旋律映射 — owner 定调(2026-06-22,待实施冻结)

owner 否决"局限科技"的旧清单(理由:看十五五最新文件 + 不局限科技,科技股正被减持)。最终定调:

- **维持 value-sleeve 4 主题层 + 共享单一真相源**:① 战略性新兴产业 ② 卡脖子自主可控 ③ 人工智能+全链 ④ 传统产业升级·高股息;**排除未来产业**(量子/核聚变/脑机/6G/纯具身智能=无盈利可估值炒作)。与 `value-sleeve-amendment-2026-06-22`(Phase AF)**同一真相源,不重复造**。
- **路线 B(长期主线历史锚 + 十五五强化)**:长期国家战略主线用历史确立日做 `effective_from`(回测可验证"跟主旋律择场"),十五五作强化延续;纯 2026 新主题走前向。
- **PIT 实现**:成分只走申万 `index_member_all`(L1/L2/L3 都带 in/out date,312 个 L3)+ `index_classify`;**`ths_index` 仅目录非 PIT**(无成分无 in/out),只作人类可读主题名对照。`effective_from` 锚:十五五建议 **2025-10-28** / 纲要 **2026-03-16** + 各行业政策日;用公开发布日(防 hindsight)。
- **universe 红线维持**:科创688/北交8 仍永禁(P0-7 §1.3.4 / P0-9 §1.2);核心理由 = 未盈利无法被价值/E-P 因子估值(同 value-sleeve 排未来产业逻辑)+ 历史断点(科创 2019-07 才开板,破坏路线 B 历史验证)+ 适当性/执行。若要纳科创已盈利硬科技龙头 = 单独 universe amendment。

**⑧ 实施待办**(在 ⑦ 之后):4 层 → 申万 L3 代码集 + 各层 `effective_from` 冻结 registry → **专门 codex PIT-soundness 门** → owner 确认主题清单+政策日 → 冻结。与 Phase AF-001 共享。

## 7. 下一步

1. **⑧ 主旋律维度**(上述待办;关键人工 gate + codex PIT 门 + owner 冻结;与 AF-001 共享真相源)。
2. **底部确认门(§3.8B)= 慢腿**:`cyq_perf` 筹码成本带 + 缩量 + 资金流企稳 + 无破位 + 无困境,与 ⑧ 主旋律(择场)+ 主题内 value-at-low 一起构成慢持仓腿。
3. **QGR-4**:在 QGR-2 竞技场用快腿幸存轴 `{max_5d, turn_spike, rev_1d}`(+ ⑧ 主题 + 慢腿 + round-1)搜候选闸门 → SPA/Romano-Wolf 公平比 + 累计 N deflation;**5-10td 选股闸门 vs T+1 真超短两条腿公平比**(本轮已得关键证据:日频 T+1 信号是反转非动量,真超短动量须分钟数据→ universe/数据不支持,两条腿对比偏向 5-10td 选股腿)。

> **接手协议**:本文档 + `qgr-3-short-factor-diagnostics-2026-06-22.md`(IC 全表)+ 主文档 + `CLAUDE.md`。sim 暂停直到 QGR-6 前向过门。push 待 owner 授权。
