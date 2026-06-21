# QGR-1 数据摄取(coverage-only)— 交付记录 + owner-gate 报告 + 接手手册

> **状态**:代码全 done + 本地门禁全绿 + 真 smoke 验证 + codex 代码门 PASS(1 cycle:2 P2 全修,verification「No actionable correctness issues」)。**摄取代码已落,真摄取(~2 万 SDK call)= owner-gated,待你「开」。** push 待 owner 授权。
> **作者**:Claude(Opus 4.8 1M)· **日期**:2026-06-21 · **上游**:`docs/research/quant-first-gate-rearch-plan-2026-06-21.md` §5/§7 QGR-1 + amendment `docs/decisions/quant-gate-rebar-amendment-2026-06-21.md` §2.3。
> **本文档 = QGR-1 唯一交付锚点**:含 探针实证 / 端点设计 / 2 处实证修正 / 陷阱标注 / dry-run 计划 / 磁盘足迹 / 精确命令 / owner-gate 决策项 / 后续 checkpoint。

---

## 0. 一句话

扩 `tushare_client` 9 端点 + `ingest_round2_data.py --phase qgr`,摄取 8000 短线+主旋律 PIT 集(字节+checksum+coverage+陷阱标注);全离线/幂等续传/分页防 5000 静默截断/PIT 红线全留;**真摄取待 owner「开」**。

---

## 1. 只读探针实证(2026-06-21,¥0,token 不打印;R4-1 先例)

写代码前先探针定死「查询模式 / 行上限 / 历史起点 / 列形」——R4-1 已探的列形复用,本轮补探 cyq*/stk_factor_pro 的**全市场查询模式 + 5000 cap**(R4-1 是按单股探的,不足以定 ingest 设计)。探针脚本:`/tmp/probe_qgr1.py` + `/tmp/probe_qgr1_floor.py`(一次性只读,关键结论已转录于此,future session 凭本节直接动手,无需重跑)。

| 端点 | 查询模式 | 单调用行数 | 5000 cap? | 历史起点 | 关键列 |
|------|---------|-----------|-----------|---------|--------|
| `stk_limit` | 全市场 by trade_date | 7651(含基金) | **撞(n@5000=2651)→ 必分页** | 2015(全) | up_limit/down_limit |
| `cyq_perf` | 全市场 by trade_date | 5512 | **撞(512)→ 必分页** | **2018-01**(之前空) | cost_5/15/50/85/95pct,winner_rate,weight_avg |
| `stk_factor_pro` | 全市场 by trade_date | 5512 × **261 列** | **撞(512)→ 必分页** | 2015(全) | qfq/hfq/bfq OHLC + MACD/KDJ/RSI/BOLL/ATR… |
| `limit_list_d` | 全市场 by trade_date | 166(稀疏) | 否 | **2020-01**(之前空) | first_time/limit_times/up_stat… |
| `suspend_d` | 全市场 by trade_date | 20(稀疏) | 否 | 2015(全) | suspend_timing/suspend_type |
| `forecast_vip` | by period **或 ann_date 范围** | 5875(period)/≤5509(ann月) | **撞→ 必分页** | 2015 | type/p_change_min/max/net_profit_min/max/ann_date |
| `express_vip` | by period **或 ann_date 范围** | 1445(period)/0-159(ann月) | 否(但分页统一) | 2015 | revenue/n_income/diluted_roe/yoy_net_profit/ann_date |
| `ths_index` | 目录(全量一拉) | 1725(type=N 概念 412) | 否 | as-of | ts_code/name/count/type |
| `index_classify` | SW 目录(src=SW2021) | 31(L1)/511(全级) | 否 | as-of | index_code/industry_name/level/parent_code |

---

## 2. 两处实证修正(owner/方案原列端点的偏差,须知会)

### 2.1 `cyq_chips` → `cyq_perf`(全市场可行性)
- **`cyq_chips` 只能按单股查**(探针:`必填参数, ts_code`)→ 返回价格直方(175 行/票/日)。全市场摄取 = ~5000 票 × 2783 天 ≈ 千万级调用 = **不可行**。
- **`cyq_perf` 全市场按 trade_date 可查**(5512 行/日),且正好给 §3.8「站稳筹码成本带上方」要的**筹码成本带分位**(cost_5/15/50/85/95pct + winner_rate + weight_avg)。→ **QGR-1 摄 `cyq_perf`(全市场摘要),不摄 `cyq_chips`**(原始直方留 QGR-3 按需对个别股查)。
- **判断**:cyq_perf 在功能上**更贴** owner 的筹码底部确认意图(直接给成本带,不用自己从直方算),且唯一可行。

### 2.2 `ths_member`(概念成分)非 PIT → 主旋律 PIT 锚定改走申万行业
- **`ths_member`(概念成分)无 in/out 日期**(探针:列仅 ts_code/con_code/con_name)= **当前快照、非 PIT** → 用它做历史主题成分 = hindsight 前视(方案 §3.8E 早标的最易自欺类)。
- **QGR-1 摄**:`ths_index`(概念/行业**目录**,带 list_date=PIT 稳定)+ `index_classify`(申万行业目录)+ 既有 `index_member_all`(申万成分,**带 in/out 日期 = PIT✓**)。
- **主旋律 PIT「场」锚定 = 申万行业(index_member_all,PIT)+ QGR-3 预注册「政策→主题」映射(政策发布日溯源)**;THS 概念成分(非 PIT)的用法**留 QGR-3** 配政策日门处理(届时只对映射里的战略概念按需拉,不投机全摄 412)。
- **判断**:不摄非-PIT 概念成分进 PIT 库,避免给未来 session 一个看似 PIT 实则前视的成分表。

---

## 3. 摄取设计(`scripts/factor_research/ingest_round2_data.py --phase qgr`)

四类(按查询形态 + 完整性模型):

1. **全市场 daily(分页 <5000 + require_non_empty + 逐日幸存无偏 coverage)**:`stk_limit` / `cyq_perf`(floor 2018) / `stk_factor_pro`。
2. **稀疏 daily(单调用 + require_non_empty=False + 空日存可重放空帧〔钉列〕+ 无 coverage)**:`limit_list_d`(floor 2020) / `suspend_d`。
3. **事件流(按 ann_date 月范围分页,**非**按目标 period;无 coverage)**:`forecast_vip`(require_non_empty=True,每月皆有) / `express_vip`(require_non_empty=False,淡月真空 → 存钉列空帧)。
   - **为何按 ann_date 而非 period(codex P2-1)**:锁定日历止于期中(20260618)时,按 period 枚举会漏掉「已按 ann_date 提前披露的未来目标期预告」(实证:`forecast_vip(20260630)` 有 ann_date 20260615/20260618 的行;`forecast_vip(20261231)` 有 ann_date 20260123 的行)。按 ann_date 月范围(复用 `report_rc_month_ranges`)精确捕获「按发布日可得」的全部事件,PIT 键 = ann_date。
4. **主旋律目录(as-of 单拉)**:`ths_index` / `index_classify`。

**分页铁律**:全市场/事件端点撞 5000 cap → `_fetch_paginated(page_limit=4000)`(严格 <cap,防短页停误判静默截断;沿用 R3 *_vip 教训 + report_rc 3000 同理)。throttle 每页一 token,`_ingest_one(rate_limiter=None)` 不重复限速。

**coverage(逐日,仅 3 个全市场 daily)**:`granularity="daily"`,requested=`universe.tradable_asof(d)`,delivered=快照 ts_code 集(镜像 `historical_ingest.job` 的 `daily/daily` 约定)。fail-closed:缺快照/无 roster → FAILED result。**需 round-2 stock_basic L/D rosters 在 `asof`(默认=日历末 20260618,与盘上已有 rosters 对齐;行上限 memo 的 asof 对齐坑)**。新 `CoverageStore.iter_keys()` 一次性预载已存键 → 续跑跳过 manifest 构造(不重解析超宽 stk_factor_pro CSV;codex P2-2)。

**PIT 红线全留**:仅官方 SDK / 字节存档+sha256 / coverage fail-closed / 幸存无偏 / 复权 pin(daily 层已有)/ 离线 / LLM 零参与 / 永禁真实下单 / governance enum 不动。

---

## 4. 陷阱标注(写进设计 + 本文档;花 8000 积分也别这样用)

| 陷阱 | 处置 |
|------|------|
| 日频 `hk_hold` 北向 2024-08-19 改季度 = **日频 PIT 已死** | **QGR-1 不摄**(陷阱清单 #1);仅季度持股可做慢因子 |
| `moneyflow`「主力净流入」无日频预测力 | QGR-1 不摄(对照非核心) |
| 龙虎榜 `top_list` 买入信号无干净 OOS | QGR-1 不摄 |
| 高换手/MAX/高量当利好(符号反) | §3.2 overlay,**剔除**用,QGR-3 验符号 |
| 同日 `limit_list_d` 当特征 = 当日盘后才齐 = 前视 | 只用 `<d`(factor builder 守,非 ingest) |
| `cyq_perf` 模型派生(非纯 PIT)| 谨慎当风控/底部确认,不当 BUY 触发;字节存档存疑已知 |
| 风险 overlay `margin`/`margin_detail`/`moneyflow_hsgt`/`hsgt_top10` | **QGR-1 暂不摄**(本 session 任务范围 = 8 端点 + 申万;margin 不受北向死影响、可后补;hsgt 北向死) |

---

## 5. dry-run 计划 + 磁盘足迹(owner-gate 依据)

**命令**:`$PY -m scripts.factor_research.ingest_round2_data --phase qgr --dry-run`(零网络)

```
calendar 2783 td (20150105..20260618)
full-market daily (分页 ~1-2 call/day + 逐日 coverage):
  stk_limit:      2783 day snapshots (20150105..20260618)
  cyq_perf:       2051 day snapshots (20180102..20260618) (floor 20180101)
  stk_factor_pro: 2783 day snapshots (20150105..20260618)
sparse daily (单调用,空日 ok):
  limit_list_d:   1564 day snapshots (20200102..20260618) (floor 20200101)
  suspend_d:      2783 day snapshots (20150105..20260618)
earnings events (forecast_vip, express_vip): 276 ann_date-month snapshots (20150131..20260618; 分页)
theme catalogs (ths_index, index_classify) as-of 20260618
estimated SDK calls (upper bound): ~20135   (真值约 12-15k:早年全市场 1 页/日)
```

**磁盘足迹估计(gitignored `data/marketdata_pit/`,现 4.6 GB)**:
| 端点 | 单日字节(2026) | 估总 |
|------|---------------|------|
| **`stk_factor_pro`** | **11.6 MB/日**(261 列) | **~20-30 GB(主导)** |
| cyq_perf | 0.35 MB | ~0.7 GB |
| stk_limit | 0.23 MB | ~0.5 GB |
| forecast/express | ~5 MB/月·≤0.2 MB/月 | ~0.3 GB |
| limit_list_d/suspend_d | 小 | <0.1 GB |
| coverage(逐日 × 3 端点 × 全宇宙列表) | — | ~0.8 GB |
| **合计** | | **≈ 22-33 GB** |

> ⚠️ **stk_factor_pro 是足迹大头(~20-30 GB)**。它 261 列里大半与已摄 daily/daily_basic 重叠;独有价值 = 预算技术指标(MACD/KDJ/RSI/BOLL/ATR)供 §3.8 底部确认「无新技术破位」。**owner 可选**:① 全摄(future-proof,字节存档红线最干净)② 后续若紧张,改 `fields=` 钉指标子集(~3-5 GB,但耦合 QGR-3 因子设计,且子集即该 query 的 raw 仍合红线)③ 暂不摄 stk_factor_pro(QGR-3 用 daily/daily_basic 派生指标)。**默认 = 全摄**;若你要省盘,告诉我改子集/缓摄。

---

## 6. 精确命令

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# 1) dry-run(零网络,验快照/调用数)
$PY -m scripts.factor_research.ingest_round2_data --phase qgr --dry-run
# 2) 真摄取(owner-gated;~2 万 call,~1 小时;字节+checksum;exit 0=全绿,非0=有 FAILED→续跑重试)
$PY -m scripts.factor_research.ingest_round2_data --phase qgr
#   可后台跑:加 nohup / & ;断点续传安全(幂等 skip 已存键)
# 3) 续跑(同命令,跳过已存快照/coverage,只补缺口)
$PY -m scripts.factor_research.ingest_round2_data --phase qgr
#   asof 默认=日历末(20260618,与既有 rosters 对齐);如 rosters 换日,传 --asof <rosters日期>
```

**门禁(已全绿,2026-06-21)**:`pytest tests/test_tushare_client.py tests/factor_research/test_ingest_round2_data.py tests/marketdata_snapshot/test_coverage.py`(164 passed)+ 广测 583 passed + ruff + mypy strict + `scripts/redline-check.sh` 全绿 + 真 smoke(9 端点真拉,分页装配过 5000:7651/5512/5512;空 express 月可重放)+ codex 1 cycle(2 P2 全修,verification PASS)。

---

## 7. owner-gate 决策项(需你拍板)

1. **「开」真摄取?**(~2 万 call / ~22-33 GB / ~1 小时,后台可)——coverage-only,摄取期不看任何因子结果(评测口径+累计账本 QGR-2 冻)。
2. **stk_factor_pro 足迹**(§5):全摄(默认)/ 指标子集(省盘)/ 暂不摄?
3. **(知会非决策)** 两处实证修正(§2:cyq_chips→cyq_perf;ths_member 非 PIT→申万锚定)——如你坚持原端点请喊停,否则按修正执行。

---

## 8. 后续 checkpoint(别忘)

- **QGR-2**:评测竞技场(真 CPCV 路径 + 冻主指标 + baseline 面板 + 累计 trial 账本含 legacy 块)。**先冻评测口径再进 QGR-3。**
- **QGR-3(关键人工 gate)**:预注册冻结「政策→主题」映射(战略主题清单 + 每主题政策发布日依据,防 hindsight)+ THS 概念成分(非 PIT)的受控用法 → **须先拿 owner 确认再冻结**。主旋律/底部确认因子走专门 codex PIT-soundness 门。
- 风险 overlay(margin/margin_detail)如需,可单独小 session 补摄(不受北向死影响)。

---

## 9. 变更清单(本次 commit)

- `backend/data/tushare_client.py`:+9 端点方法(stk_limit/limit_list_d/suspend_d/cyq_perf/stk_factor_pro/forecast_vip/express_vip/ths_index/index_classify)+ `_FULLMARKET_PAGE_LIMIT=4000` + `_pull_period_or_ann_range` helper + Protocol 扩。
- `backend/marketdata_snapshot/coverage.py`:+`CoverageStore.iter_keys()`(一次性键迭代,O(n²)→O(n) bulk 幂等)。
- `scripts/factor_research/ingest_round2_data.py`:`--phase qgr` + `ingest_fullmarket_daily`/`ingest_sparse_daily`/`ingest_event_stream`/`ingest_theme_catalogs`/`build_daily_coverage_manifests`/`_build_qgr_coverage`/`ingest_qgr` + floors/陷阱/dry-run + `_make_throttle` DRY(refactor 既有 2 处 inline)。
- 测试:`tests/test_tushare_client.py`(+QGR 端点/分页/range/校验)+ `tests/factor_research/test_ingest_round2_data.py`(+全市场/稀疏/事件流/coverage/orchestrator)+ `tests/marketdata_snapshot/test_coverage.py`(+iter_keys)。
