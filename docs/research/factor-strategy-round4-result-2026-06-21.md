# 量化选股策略研究 第 4 轮 — 终判报告(2026-06-21)

> **判定 = PASS(四门全过)** —— **四轮首次**。但这是一个 **provisional(暂定)PASS**:
> 真兑现了正指数超额,但 dev 反过拟合门未背书 + 系测试集第 4 次评测 → **上线前须真前向窗口处子 OOS 确认,不直接 go-live**。诚实优先,PASS 不夸大、caveat 不掩盖。

---

## 0. 一句话

**以分析师修正动量(`report_rc`:rev_diff/np_rev/tp_impl/cover_chg)为正交 alpha 源的增强指数策略,在锁定测试集上净赚 +24.51%、扣成本超额 CSI300 +2.68%、Sharpe 1.81、回撤 6.40%,四门全过 = 四轮首个真·正指数超额。** 这是前三轮(价值/质量/反转/SUE/应计)缺的「信息流」原料终于补上 —— 印证 owner 铁律②「不闭门造车,文献驱动」(Lv 2025:分析师价值集中大盘段,正好对齐 cap 加权 CSI300)。**但 dev 阶段 DSR=0.007(四轮最低)+ 哨兵不过 = 该 PASS 的 dev 稳健证据是四轮最弱的;须前向窗口确认。**

---

## 1. 一次性锁定测试结果(R4-6,test 2025-06-04 → 2026-06-12,49 个调仓期)

| 指标 | 值 | 门 | 判定 |
|---|---|---|---|
| 净累计收益(扣成本) | **+24.51%** | >0 | ✅ PASS |
| 超额 vs CSI300 | **+2.68%**(CSI300 +21.84%) | ≥0 | ✅ **PASS** |
| 最大回撤 | **6.40%** | ≤15% | ✅ PASS |
| 期化夏普 | **+1.81** | ≥0.5 | ✅ PASS |
| **四门** | | | **✅ 全过 = PASS** |

补充披露(非门):年化 +25.30% / IR +0.59 / TE 3.95% / 换手 0.18 / net_active≈1.8e-17(beta≈1 ✓)/ size_active +0.087 / forced UW 12.6%。分年:**2025H2 +27.11%(29 期)/ 2026H1 −2.04%(20 期)**。

**无偷看证明**:策略 git 冻结 `ffc1db3`(2026-06-21 13:30:49)→ test 读取写盘 13:33:44;`load_frozen_strategy` 用 git 冻结常量打分(非 artifact),firewall 验证 artifact 一致(3dp 内 5e-4 容差),`test_real_frozen_constants_match_search_result` 绿。冻结在读 test 之前。

---

## 2. 跨四轮对比(同一锁定测试集,CSI300 同期 +21.84%)

| 轮 | 终选 alpha 增量 | 净 | 夏普 | 回撤 | **超额 vs CSI300** | dev DSR | dev 哨兵 | dev SPA-vs-passive | 终判 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 价值/防御/反转(long-only top-5) | +5.48% | 0.54 | 7.78% | **−16.36%** | 0.066 | — | — | FAIL |
| 2 | +质量/成长(增强指数构造修复) | +21.58% | 1.80 | 6.11% | **−0.26%** | 0.056 | 过 | 0.126 | FAIL |
| 3 | +应计(零成本财报衍生) | +17.83% | 1.51 | 7.00% | **−4.00%** | 0.032 | — | 0.110 | FAIL |
| **4** | **+分析师修正(信息流)** | **+24.51%** | **1.81** | **6.40%** | **+2.68%** | **0.007** | **不过** | **0.056** | **PASS** |

**叙事弧**:round-2 修好构造(beta≈1、size 漂移),round-3 应计太弱原地踏步,**round-4 分析师修正把超额从 −0.26/−4.00 推过零线到 +2.68%**。SPA-vs-passive 单调改善(0.126→0.110→0.056)预示了这一步 —— 分析师 alpha 一直在逼近显著,test 上兑现为正超额。

---

## 3. 冻结策略(R4-5 搜索选出,`ffc1db3` git 冻结)

- **构造** = `constituent_only`(真增强指数,只在 CSI300 成分内 tilt = R2-3 小盘漂移的修法),`k=0.20 / a_max=0.04`(**全网格最强 tilt**;round-2/3 选的是弱 tilt k=0.10/0.05)。
- **16 因子权重(3dp,Σ=1.000)**:`rev_diff=0.216`(分析师修正扩散,**主导**)、ret_5d=0.161、amihud=0.114、np_yoy=0.082、gpm=0.077、accr=0.074、cover_chg=0.060、ret_20d=0.053、rev_yoy=0.036、tp_impl=0.037、np_rev=0.035、roe=0.018、vol=0.013、ep_ttm=0.012、max=0.007、turn=0.005。
- **分析师块(rev_diff+cover_chg+tp_impl+np_rev)≈ 0.348 ≈ 35% 权重** —— 真被重用(对比 round-3 accr 仅 0.006)。

---

## 4. 诚实研读(PASS 不夸大,caveat 不掩盖)

### 4.1 真兑现:分析师修正 = 四轮缺的正交 alpha 源 ✅
信息流因子(分析师对未来盈利的修正)与财报衍生因子(价值/质量/反转/SUE/应计)**正交**(R4-4:与 12-carry 簇 max 共线 0.38)。卖方覆盖偏大盘 → 正好补「强势大盘年跑赢 cap 加权 CSI300」的缺口。前三轮的零成本财报因子证不够;`report_rc`(rev_diff 主导)够了。**这是 owner 铁律②(文献/牛人驱动→严验)的胜利**:Lv 2025(arXiv 2502.20489)先验「分析师价值集中大盘段」→ 我们从零验证符号 → test 兑现。

### 4.2 张力:PASS 却伴四轮最低 DSR(0.007)+ 哨兵不过 ⚠️(核心 caveat)
- dev 阶段反过拟合门 **未背书** 这个 PASS:DSR 0.007(累计 N=2348 deflate;即便按单轮 612 也过不了,val 单期 Sharpe 仅 0.10、MinBTL 需 3071 期 ≫ 108)+ **哨兵失败**(洗牌噪声组合 val IR 1.34 > 真组合 0.71 → 强 tilt k=0.2 把噪声放大,val IR 不可信)。
- **如何诚实调和**:四门是「真实 OOS 盈利」判据,dev 门是「假阳性削减器」。一个**脆弱**策略**仍可能在单个 OOS 窗口靠运气过线**。本轮 = test 结果四轮最好(+2.68%)但 dev 稳健证据四轮最弱(DSR 0.007/哨兵不过)→ **不能区分「真稳健分析师 alpha」与「脆弱策略恰好在第 4 次抽样落正」**。
- **原则①不被推翻**:低 DSR ≠「门错了」,而是「此 PASS 低置信」。真稳健 alpha 应**同时**有强 dev 证据(高 DSR)+ test 过;这里只有后者。→ **结论:provisional PASS,须前向确认,不直接上线。**

### 4.3 第 4 次评测 + 跨策略多重检验 ⚠️
这是该锁定测试集**第 4 次**评测(round-1/2/3/4 各一次)。4 个策略试同一 test → 至少一个靠运气过线的概率 > 单次。三个可比的增强指数构造(round-2/3/4)超额 = −0.26 / −4.00 / **+2.68**,均值 −0.53、跨度 ~6.7pp → **+2.68 是最好的,但落在 −0.26/−4.00 的噪声带内**。综合 SPA-vs-passive 0.056(擦边不显著)+ 「+2.68 > 无分析师版的 −0.26/−4.00」→ 证据指向 **一个小的、真实但未被统计钉死的分析师 edge(~2-3pp)**,非铁证的强 alpha。

### 4.4 其他须盯的暴露
- **size_active +0.087**(正,比 round-2 −0.064 / round-3 +0.059 都大)= test 期有轻微小盘 tilt 残留(虽因子 size 中性)→ 部分超额可能来自此,须盯。
- **2026H1 −2.04% 回吐**(2025H2 +27.11% 贡献绝大部分)→ 超额时间上集中,非均匀。
- **激进 tilt(k=0.2/a_max=0.04)**= 高 TE 双向赌注;搜索选了最激进格点 + 哨兵不过 → 稳健性存疑(见下一步:须验弱 tilt 是否也过)。

---

## 5. 结论

**判定 = PASS(四门全过,真兑现,无偷看)= 四轮首个正指数超额。** 这是研究按设计运作的成果:文献先验 → 从零严验 → 锁定 test 兑现。**但它是 provisional PASS**:dev 反过拟合门(DSR/哨兵)未背书 + 系第 4 次评测 → **稳健性未被独立确认**。

按 owner 标准(「保证稳定盈利 = 真·扣成本 OOS 盈利 + 跑赢 CSI300」),**这是四轮里第一个达标的策略**;但按 owner 标准的精神(「只部署可证 OOS 盈利的」+ 反过拟合门是手段),**低 DSR + 第 4 次评测 = 该 PASS 的可证性不足以直接上线** → 操作上诚实的下一步 = **真前向窗口确认**,不是立即 go-live。

---

## 6. 下一步方向(owner 拍板)

1. **(首选)冻结 + 真前向窗口处子 OOS**:策略已 git 冻结(`ffc1db3`)。摄取 `test_end=2026-06-12` 之后新增的行情/`daily_basic`/`adj_factor`/`report_rc`(随时间累积)→ 在**从未被任何轮评测过**的前向窗口跑这个冻结策略一次 = 唯一真处子 OOS。若前向也兑现正超额 → 升为「确认稳健」→ 可议上线(经 owner gate + LiveArtifactRegistry + 45 日 shadow + 人工 pin + 重启)。**这是化解 4.2/4.3 caveat 的唯一干净办法。**
2. **(并行)稳健性体检(train_val,不烧 test)**:① 弱 tilt(k≤0.1)是否也产正 dev IR(回应哨兵不过)；② 分析师因子衰减曲线(staleness 30/60/90/180 扫描已部分做)；③ 覆盖偏大盘的 size 漂移量化(size_active +0.087 来源)；④ 去掉 rev_diff 单因子看分析师块剩余贡献。
3. **(备选)叠加新 PIT 原料**:资金流/北向/龙虎榜(`moneyflow`/`hk_hold`/`top_list`)、事件(`forecast_vip`/`express_vip`)、筹码(`cyq_chips`)—— 均 ¥0 已实探解锁(见 `reference-tushare-entitlements-8000-2026-06-20`)。但 **测试集已 4 次评测 → 再迭代须极谨慎**,优先走前向窗口而非第 5 次。

---

## 7. 诚实保障声明(全程未破)

- **无 data-snooping**:策略 R4-6 前 git 冻结(`ffc1db3` 13:30:49 < test 读 13:33:44);打分用 git 冻结常量(非 artifact);firewall 验证一致;test 只读一次。
- **第 4 次评测显式披露** + **DSR 按累计 N=2348(512+612+612+612)deflate**;PBO/SPA 限本轮 612 池(跨轮多重检验由「第 4 次」披露承担,如实说明此局限)。
- **四门不放宽**(net>0 / 超额≥0 vs CSI300 / MDD≤15% / 夏普≥0.5,= round-3 同 bar)。
- **PIT/无前视**:`report_date<d`(create_time 是 bulk-load 时戳,不当闸)/`ann_date<d`;字节存档+checksum+coverage;退市不删。
- **数据源仅 Tushare 官方 SDK**;离线;LLM 只用于文献(provenance-gated);**永禁真实下单**;**governance EconomicMechanism enum 未动**(分析师机制 analyst_revision/_dispersion/_coverage 故意不注册)。
- **门禁**:383 factor_research 测试绿 + ruff + mypy --strict(30 files)+ redline;`/code-review high`(codex 本包稳定 stall→回退)R4-5(a-d)0 P0-P2、R4-5(f)0 correctness/PIT-leakage。

**FAIL 报 FAIL,PASS 也不夸大 —— 这是 provisional PASS,真兑现但须前向确认。**

---

### 关键 artifact(gitignored data/ 除外)
- 搜索:`data/factor_research/round4_search_result.json`(DSR 0.007/PBO 0.329/SPA-vs-passive 0.056/哨兵不过/CPCV 全正 1.45)
- 成本压力:`data/factor_research/round4_crosscheck_result.json`(base +52.2%→stressed +35.9%,摩擦单调,oracle UNAVAILABLE)
- 判定:`data/factor_research/round4_locked_test_result.json`(本报告 §1)
- 冻结:`scripts/factor_research/round4_locked_test.py` `FROZEN_R4_*`(commit `ffc1db3`)
- manifest:`config/research/round4_experiment_manifest.json`(N 重声明 612 grid + 2348 deflation)
