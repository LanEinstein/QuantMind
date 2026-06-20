# R4-1 数据权限实探 + report_rc 数据形态(2026-06-20)

> 第 4 轮 R4-1 交付之一(权限实探)。**真探针权威**(docs.qq.com 积分权限表 JS 渲染,
> WebFetch 取不到)。探针只读、¥0、token 不打印;tushare 走 `requests`/`api.tushare.pro`
> 解析为 IPv4 → 不 stall(无需 IPv4 shim)。探针脚本归档于 §6。
> 配套:文献调研 `round4-literature-*.md`(R4-1 另一半);权限 memory
> `reference-tushare-entitlements-8000-2026-06-20`。

---

## 0. 一句话

**8000 积分档极其充足:本轮所需端点全部 `[OK]`(无一 NOPERM/RATE/FAIL)。** 头号 alpha 源
`report_rc`(分析师盈利预测/评级)确认解锁、**21 字段**(比文档多 6 列)、**历史回溯到 2014**
(完整覆盖 train_val)、**单调用 cap=5000(范围查询必分页 / 单日查询天然免 cap)**、`eps`/`np`
覆盖 99.6%、`rating` 100% 但异构需 ordinal 映射、`quarter`=预测目标财年(须按 FY 对齐)。

---

## 1. 完整 8000 档权限图(real-probed 2026-06-20)

探针日期 = 当日 last data date `20260612`(确认交易日);ts_code 用 `600519.SH`(茅台,
卖方覆盖密)。每端点小调用,错误分类(RATE 限频 / NOPERM 无权 / FAIL 其它)。**全部 [OK]。**

| 类别 | 端点 | 探针返回(行×列) | 备注 |
|---|---|---|---|
| **基线(已摄)** | `daily` | 5512×11 | — |
| | `daily_basic` | 5512×18 | pe_ttm/turnover/circ_mv |
| | `fina_indicator_vip` | 7131×109 | profit_dedt/roe/gpm |
| **头号:分析师** | **`report_rc`** | 见 §2 | **本轮头号 alpha 源** |
| **资金流/聪明钱②** | `moneyflow` | 5194×20 | 个股 sm/md/lg/elg 分桶净流 |
| | `moneyflow_hsgt` | 1×7 | north_money/south_money(⚠️见 §4) |
| | `hk_hold` | 944×7 | 北向个股持股(⚠️见 §4) |
| | `hsgt_top10` | 20×11 | 沪深股通十大成交 |
| | `top_list` | 95×15 | 龙虎榜个股 |
| | `top_inst` | 1020×10 | 龙虎榜机构明细 |
| | `margin` | 3×9 | 融资融券汇总(按交易所) |
| | `margin_detail` | 4369×10 | 融资融券个股 |
| | `block_trade` | 318×7 | 大宗交易 |
| | `moneyflow_ind_dc` | 1021×18 | 东财行业资金流 |
| | `moneyflow_mkt_dc` | 1×15 | 东财大盘资金流 |
| **事件③** | `forecast_vip` | 4651×13 | 业绩预告(type/p_change_min/max/net_profit_min/max) |
| | `express_vip` | 1149×17 | 业绩快报(revenue/n_income/diluted_roe/yoy_net_profit) |
| **筹码/技术④** | `cyq_chips` | 175×4 | 筹码分布(price/percent 直方) |
| | `cyq_perf` | 105×11 | 筹码胜率(winner_rate/cost_5..95pct/weight_avg) |
| | `stk_factor_pro` | 105×**261** | 技术因子 pro(qfq/hfq OHLC + 海量指标) |
| | `stk_factor` | 105×35 | 技术因子(adj_factor+macd) |
| | `ccass_hold` | 0×6 | 中央结算持股(茅台范围内空;非 CCASS 标的稀疏) |
| **微结构/治理** | `stk_limit` | 7651×4 | 涨跌停价 |
| | `limit_list_d` | 166×18 | 涨跌停统计 |
| | `suspend_d` | 20×4 | 停复牌 |
| | `dividend` | 59×14 | 分红送股 |
| | `share_float` | 11×7 | 限售解禁 |
| | `stk_holdernumber` | 13×4 | 股东人数 |
| | `stk_holdertrade` | 2×11 | 增减持 |
| | `repurchase` | **2000**×9 | 回购(⚠️ 2000=单调用 cap,需分页) |

> 8000 档对本轮"全部解锁"。下一轮若探更高/单独申请端点(如分钟线、tick),再补探。

---

## 2. report_rc 深挖(R4-2 摄取 + R4-3 因子设计的权威依据)

### 2.1 字段(实测 21 列,比 kickoff/memory 多 6 列)
```
ts_code, name, report_date, report_title, report_type, classify, org_name,
author_name, quarter, op_rt, op_pr, tp, np, eps, pe, rd, roe, ev_ebitda,
rating, max_price, min_price
```
- **新增列**(文档漏):`name`(股票名)/`report_title`(标题)/`report_type`(点评/深度/一般/非个股)/
  `classify`(一般报告/首次关注/首份报告/首次评级)/`rd`(常空)/`ev_ebitda`。
- **核心数值**:`eps`(EPS 预测,元)/`np`(净利预测,**万元**)/`op_rt`(营收预测,万元)/
  `op_pr`(营业利润预测,万元,**常空弃用**)/**`tp`(利润总额预测,万元 — 收入表项,⚠️**非目标价**!)**/
  `pe`/`roe`/`ev_ebitda`/`rating`(文本评级)/`max_price`/`min_price`(**目标价;实测仅 `min_price`
  ~30-50% 有值,`max_price`≈0**)。

### 2.2 语义(关键)
- **`report_date` = 研报发布日 = PIT 可得日**。PIT 规则:决策日 d 只用 `report_date < d` 的报告
  (当晚 19-22 点入库 → **D+1 才可交易**)。**`create_time`(入库/更新时戳,须显式 `fields=` 请求,
  默认不返)挡回填行(create_time ≫ report_date 的行在 report_date 当时不可知 → 剔除)。**
- **⚠️ 字段纠错(真值实证)**:`tp` = **利润总额(万元)非目标价**;**目标价 = `min_price`**
  (`max_price`≈0)。kickoff/旧 memory 标"tp=目标价"错,会静默污染 `tp_impl` 因子(详见 §2.1/§2.3)。
- **`quarter` = 预测目标财年**,实测全为 `2023Q4/2024Q4/2025Q4/2026Q4`(**Q4 = 年度全年预测**)。
  一份报告对**每个预测财年出一行** → 单报告 mean **2.85 行**(FY1/FY2/FY3…,max 6)。
  → **修正因子必须按相同 FY 对齐**(标准做法:取 **FY1** = as-of 未披露的最近完整财年),
  并**处理跨年滚动**(FY1 报出后目标滚到下一年,因子不能在滚动点产生人为跳变)。
- **单日单股仅 mean 1.49 家券商** → 同日 consensus 无意义,**必须 trailing 窗口(如 90/180d)
  跨日聚合**(每券商取窗口内最新一份,再中位/均值成 consensus)。

### 2.3 覆盖与 null 率(2024-01 全市场 5000 行样本)
| 字段 | 非空率 | 因子含义 |
|---|---|---|
| `eps` | **99.6%** | eps_rev 主力(最佳覆盖) |
| `np` | **99.6%** | np_rev 主力 |
| `rating` | **100%** | rating_chg(但取值异构,见下) |
| `quarter` | 99.8% | FY 对齐键 |
| `op_rt` | 85.8% | 营收修正(备选) |
| `roe` | 82.8% | 一致预期 ROE |
| `pe` | 77.7% | 一致预期 PE |
| **`tp`** | **74.2%** | **利润总额(万元),⚠️≠目标价!** 备选:利润总额修正因子 |
| **`min_price`** | **33.3%** | **真·目标价**(`tp_impl` 用此;茅台2237/平安13.5/招行41 实证) |
| `op_pr` | 12.1% | **弃**(覆盖太低) |
| `max_price` | 0.1% | **弃** |

- **`rating` 取值高度异构**(各券商话术不同):买入/增持/跑赢行业/无/推荐/强烈推荐/优于大市/
  强推/买进/谨慎推荐/持有/中性/区间操作/谨慎增持/BUY… → **必须建 house-agnostic ordinal 映射**
  (如 强烈推荐·强推·买入·买进·BUY=5 / 增持·推荐·优于大市·跑赢行业·谨慎增持·谨慎推荐=4 /
  持有·中性·区间操作=3 / 减持=2 / 卖出=1 / 无=NaN)。rating_chg 成败系于此映射的稳健性。
- **`report_type=非个股`(19%)= 行业/策略报告 → 须过滤**(只留个股预测);`classify` 的
  `首次关注/首份报告/首次评级`(共 ~12%)= 新覆盖 initiation,可做 coverage_chg 因子的正信号。
- **covered universe ~780 名/月**(卖方覆盖偏大盘/活跃股)→ 因子对全市场大半**无定义**;
  covered/uncovered 处置是核心设计点(见 §3 与 PIT 文献 agent)。**卖方偏大盘恰可能补
  "大盘强势年跑赢 cap 加权 CSI300"的缺口**(三轮 FAIL 的根因)。

### 2.4 历史深度 + 行上限(R4-2 摄取策略)
- **历史回溯 ≤2010**(R4-2 实证 2026-06-20:2014 全 12 月 4146-12906 行/月、无空月;2013/2012/
  2011/2010 各月 8000-12000 行)→ **完整覆盖 train_val 2015-01-05+ 且有暖 trailing 窗口**(故
  R4-2 摄取 first_year=2014;无 index_weight 那样的 2016 边界问题;benchmark-relative 构造仍受
  index_weight 2016 起限制,与 report_rc 无关)。
- **单 report_date 全市场行数**:20160104=443 / 20200106=526 / 20240108=281 / 20260105=239 →
  **每日 ~150-900 行,远低于 cap**(20240108 offset=5000 取 0 行=确认 281 是全量)。
- **单调用 cap = 5000(确认)**:月范围 `report_rc(start=20240101,end=20240131)` offset=0 取满
  5000、offset=5000 又取满 5000、limit=8000 仍只 5000 → **范围查询被静默截断在 5000 行,必分页**
  (同 *_vip 静默截断教训 [[reference-tushare-statement-vip-row-cap]];已有 `_fetch_paginated`
  page_limit=5000 直接适用)。

---

## 3. R4-2/R4-3 设计结论(probe 直接推出)

### R4-2 摄取(owner-gate「开」才真跑)
- **推荐:按 `report_date` 单日摄取**(2014-12 → train_val end,后续判定窗口再补)。理由:
  ① **天然免 cap**(每日 ≤900 行);② **PIT 键 = report_date** 干净(每快照 = 一个可得日);
  ③ 幂等续传(跳过已存 report_date);④ ~2700 调用一次性 owner-gated(同 R3 摄取量级)。
- **备选:月范围 + `_fetch_paginated`**(~125 月 ×1-3 页 ≈ 250-350 调用,快;须断言"末页短"=
  分页完整,防 5000 截断)。
- **完整性模型与统计表不同**:report_rc 是**稀疏流**,不存在固定 survivorship universe →
  coverage-manifest(查"是否取全 roster")**不适用**;改为 **pagination-complete 断言(末页 <
  page_limit)+ 每期行数记录 + 字节存档 + checksum + 幂等**。
- 端点接线:`tushare_client` 加 `report_rc(report_date=… | ts_code=…+range)` method + 进
  `TusharePro` Protocol(同既有模式);范围查询走 `_fetch_paginated`,单日查询走 `_fetch`。

### R4-3 因子族(`R4_FACTORS`,候选;文献校准后定稿)
- `eps_rev` = FY1 consensus EPS 的 trailing 环比修正 /|上期|(eps 99.6% 覆盖,主力)。
- `np_rev` = FY1 consensus 净利修正幅度(np 99.6%)。
- `tp_impl` = median(`min_price`)/现价 − 1(**目标价 = min_price**,~30-50% 覆盖;**绝不用 `tp`=利润总额**)。
- `rating_chg` = trailing 窗口 ordinal 评级净变化(rating 100%,依赖稳健 ordinal 映射)。
- `disp` = FY1 EPS 预测分歧度 std/|mean|(反向因子)。
- (备选)`coverage_chg` = 覆盖券商数/报告数变化;`epe_rev`=一致预期 PE/ROE 修正。
- **共性约束(probe 揭示)**:① trailing 窗口聚合(同日券商太少);② FY1 对齐 + 跨年滚动处理;
  ③ 过滤 `非个股`;④ covered/uncovered 处置;⑤ 走 R2-2 验证协议(行业+市值中性化 |t|≥3 +
  低共线 + 机制注册;弱则如实丢,同 SUE/动量/资产增长)。

---

## 4. 重要数据质量 caveats(用前必验)

1. **北向 post-2024-08 断更风险(direction ② 关键)**:沪深交易所 2024-08-18 起**停止北向资金
   实时/单日披露**(改盘后/季度)。探针 `hk_hold` 20260612 返 944 行、`moneyflow_hsgt` 返 1 行,
   但**无法从行数判断 north_money 是否已 null/stale** → 若走资金流方向,**必先验 2024-08 后北向
   字段非空非冻结**,否则用旧时段或换 `margin`/`moneyflow`(个股主力流,不受此影响)。
2. **`repurchase` 单调用 cap=2000**(探针返回恰 2000=圆整 cap)→ 用前分页。
3. **`ccass_hold` 对非 CCASS(非港股通)标的稀疏/空**(茅台范围内 0 行)。
4. **report_rc `非个股` 行(19%)= 行业报告**,`op_pr`/`max_price` 覆盖极低 → 因子构造须过滤/弃用。

---

## 5. 红线遵守

- 数据源仅 Tushare 官方 SDK(`ts.pro_api`);`TUSHARE_TOKEN` 只读 env、不打印、不入 LLM/飞书池。
- 探针只读、¥0、不写任何 snapshot(真摄取在 R4-2,owner-gate「开」)。
- IPv4-only egress 自然满足(tushare 解析 IPv4,不 stall)。
- 不烧 test:探针用 ts_code/单日/月范围探,与 locked split 无关。

---

## 6. 探针脚本归档(可复现)

`/tmp/probe_round4.py`(全端点 [OK]/[NOPERM]/[RATE]/[FAIL] 分类探针)+
`/tmp/probe_report_rc.py`(report_rc 最早日期 + 月范围 cap + null 率/quarter/rating 形态)。
两者均 `ts.pro_api(os.environ["TUSHARE_TOKEN"])` + 小调用 + 0.7s 限速;结果见本文件 §1/§2。
（探针为一次性只读探测,未纳入确定性离线管线;关键逻辑已转录于此文件,future session 可凭 §1/§2
表格直接进 R4-2,无需重跑。）
