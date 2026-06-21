# PIT 数据资产清单 — `data/marketdata_pit/`(全市场 point-in-time 字节存档)

> **🚫 禁从头重下载。** 全市场 PIT 数据已落地本地;后续**只增量更新**(摄取幂等续传,跳过已存键)+ 更新本清单时间戳。从零重摄 = 浪费数小时 + 数十 GB 带宽。
> **最后更新**:2026-06-22(QGR-1 摄取完成)· **位置**:`/home/ps/papers/QuantMind/data/marketdata_pit/`(**gitignored**,`.gitignore:107`)· **总量**:~29 GB / 23,918 快照 / 10,258 coverage manifest。

---

## 1. 路径与结构

```
data/marketdata_pit/                     # gitignored;离线 PIT 字节存档(K-002 SnapshotStore)
├── index.jsonl                          # 快照元数据索引(每行 1 快照:vendor/endpoint/params/trade_date/sha256/version/fetch_time_utc/metadata)
├── payloads/<sha[:2]>/<sha>.bin         # 内容寻址原始字节(canonical CSV);同字节去重
└── coverage/coverage.jsonl              # CoverageManifest(requested vs delivered universe,逐日/逐期)
```

- **不可变 / append-only**:同 `snapshot_id` 拒覆盖;供应商重述 = 新 `version`(旧字节保留)。**严禁手删/改 `payloads/` 或 `index.jsonl`。**
- **复权 pin**:`adj_factor` 存原始未复权价 + 因子;qfq/hfq 由 `marketdata_snapshot/adjust.py` as-of 重建(bit-exact)。
- 仅 Tushare 官方 SDK 摄入;`TUSHARE_TOKEN` 只读 env 不入库。

---

## 2. 已摄端点清单(2026-06-22)

| 端点 | 快照数 | 范围(trade_date/period/asof) | v2 重述 | 来源 phase |
|------|-------:|------------------------------|:------:|-----------|
| `daily` | 2783 | 20150105..20260618 | 0 | historical_ingest(AE-001) |
| `daily_basic` | 2783 | 20150105..20260618 | 0 | historical_ingest |
| `adj_factor` | 2783 | 20150105..20260618 | 0 | historical_ingest |
| `fund_daily` | 2783 | 20150105..20260618 | 0 | historical_ingest |
| `fina_indicator_vip` | 45 | 20150331..20260331(季) | 0 | round2 |
| `income_vip` | 51 | 20150331..20260331 | 6 | round3(+restate) |
| `cashflow_vip` | 68 | 20150331..20260331 | 23 | round3(+restate) |
| `balancesheet_vip` | 63 | 20150331..20260331 | 18 | round3(+restate) |
| `index_weight`(CSI300) | 125 | 20160129..20260529(月) | 0 | round2 |
| `index_member_all`(申万成分 PIT) | 1 | asof 20260618 | 0 | round2 |
| `namechange`(ST 史 PIT) | 38 | 19901231..20260619(年) | 0 | round3 |
| `report_rc`(分析师修正) | 151 | 20140131..20260618(月) | 0 | round4 |
| `stock_basic_listed` / `_delisted`(幸存无偏 roster) | 1 / 1 | asof 20260618 | 0 | round2 |
| **`stk_limit`**(涨跌停价) | **2783** | 20150105..20260618 | 0 | **qgr** |
| **`cyq_perf`**(筹码胜率/成本带) | **2051** | 20180102..20260618(floor 2018) | 0 | **qgr** |
| **`stk_factor_pro`**(技术因子 261 列) | **2783** | 20150105..20260618 | 0 | **qgr** |
| **`limit_list_d`**(涨跌停统计) | **1564** | 20200102..20260618(floor 2020) | 0 | **qgr** |
| **`suspend_d`**(停复牌) | **2783** | 20150105..20260618 | 0 | **qgr** |
| **`forecast_vip`**(业绩预告) | **138** | 20150131..20260618(ann_date 月) | 0 | **qgr** |
| **`express_vip`**(业绩快报) | **138** | 20150131..20260618(ann_date 月) | 0 | **qgr** |
| **`ths_index`**(同花顺概念/行业目录) | **1** | asof 20260618 | 0 | **qgr** |
| **`index_classify`**(申万行业目录 SW2021) | **1** | asof 20260618 | 0 | **qgr** |

> **QGR-1 摄取(2026-06-21 11:26..18:34 UTC,owner-gated,2 段:首跑 3h 超时 → resume #1 exit 0)**:12,242 新快照,`failed=0`,全端点对齐 dry-run 计划。日历末 = `20260618`(= 锁定 test_end 20260612 之后 4 个前向交易日已含)。

---

## 3. ⚠️ coverage 完整率解读(别被「6693 incomplete」吓到)

QGR run 报告:`qgr coverage: 7617 period manifests, 6693 incomplete; worst stk_factor_pro 20150709 completeness=0.4815`。**这是良性的**,非截断:

- coverage 的 `requested` = `tradable_asof(d)`(**所有上市未退市**股);但 `cyq_perf`/`stk_factor_pro` 只对**当日真交易**的股有行(停牌/未上市无行)→ `delivered < requested` 是「上市但未交易」的天然缺口。
- **已验证(vs 受信 `daily` 端点,同交易宇宙)**:`stk_factor_pro`/`cyq_perf` 的 delivered 在**每个抽样日 ≈ `daily` delivered**。worst 日 20150709:`daily` 仅 1363 股(因 **1466 股停牌** —— 2015 股灾停牌潮,`suspend_d` 实证),`stk_factor_pro` 1384 ≈ daily。近端高量日(20240108/20260612)`stk_factor_pro` delivered **>5000**(5281/5253)≈ daily → **分页正确装配过 5000 cap,零截断**。
- `stk_limit` delivered > daily(涨跌停价对所有上市含停牌+基金都有)→ completeness 更高。
- **结论**:incompleteness = 上市-vs-交易缺口(stk_limit 更小,trading-conditional 端点更大),**与 daily 同源**,非数据丢失。粗截断仍会被抓(截断日 stk_factor_pro 会 << daily,实际没有)。
- **QGR-2 可选精化**:trading-conditional 端点(cyq_perf/stk_factor_pro)的 coverage 用 `daily`-delivered(真交易集)作分母,使 completeness 成为纯截断探测器(当前 backstop 已够,不必回补)。

---

## 4. 🔁 增量更新协议(只增量,禁从零)

存储幂等(skip-if-present)+ append-only。增量步骤:

### 4.1 补新交易日(日历末 20260618 之后新增数据)
```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin/python
# (1) 先扩日历:摄新 daily/adj_factor/daily_basic/fund_daily(authoritative 日历=index.jsonl 的 daily 行)
$PY scripts/ingest_historical_pit.py --start <YYYYMMDD> --end <YYYYMMDD>   # 先 --dry-run 验
# (2) 再增量摄各 phase(幂等,只拉新日历缺口;catalogs 用新 asof)
$PY -m scripts.factor_research.ingest_round2_data --phase qgr --dry-run    # 验快照/调用数
$PY -m scripts.factor_research.ingest_round2_data --phase qgr              # 真摄(后台+续传安全)
#   round2/round3/round4 同理按需(新季报期随 last_date 推进自动纳入)
# (3) 更新本清单 §2 的 hi 日期 + §0 时间戳
```

### 4.2 重跑/修补一个已有窗口
```bash
# 直接重跑该 phase:跳过已存键,只补 FAILED/缺失,不重下已存(零额外带宽)
$PY -m scripts.factor_research.ingest_round2_data --phase qgr
# *_vip 财报截断修复(append v2,保留旧字节):--phase round3-restate
```

### 4.3 要点
- **catalogs(ths_index/index_classify/index_member_all/stock_basic L/D)keyed by asof** → 新 asof = 新快照(旧保留);`--asof <新日期>`(qgr 默认 = 日历末,须与 round2 rosters 同 asof 否则 coverage fail-closed,见行上限 memo)。
- **断点续传**:任何 run 中断 → 同命令重跑续上(stk_factor_pro 32GB 传输受带宽限,~4.5s/天 → 全量 ~4-5h;单 run 设超时兜底,超时后重跑续传)。
- **严禁**:删/改 payloads；hash-only;非官方 SDK；摄取期看因子结果(QGR coverage-only)。

---

## 5. 完整性自检命令

```bash
# 各端点快照数 + 日期范围(对齐本清单 §2)
$PY - <<'PYEOF'
import json,collections
c=collections.Counter()
for l in open("data/marketdata_pit/index.jsonl"): c[json.loads(l)["endpoint"]]+=1
print(dict(sorted(c.items())))
PYEOF
# coverage 最差完整率(run 报告已打印;粗截断会显著低于 daily 同日 delivered)
# 离线 bit-exact replay(K-005):scripts/replay_signal.py <signal_id>
du -sh data/marketdata_pit/          # ~29 GB
```

---

## 6. 关联
- 摄取代码 + 设计:`docs/research/qgr-1-data-ingest-2026-06-21.md`(QGR-1 交付锚点)。
- 模块红线:`backend/marketdata_snapshot/CLAUDE.md`(K-002/003/004/005)。
- 行上限/分页/asof 坑:memory `reference-tushare-statement-vip-row-cap` + `reference-tushare-entitlements-8000-2026-06-20`。
- 主文档:`docs/research/quant-first-gate-rearch-plan-2026-06-21.md`(QGR;摄完进 QGR-2 评测竞技场)。
