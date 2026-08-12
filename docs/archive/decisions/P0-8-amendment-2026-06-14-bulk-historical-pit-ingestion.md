# P0-8 修订 — 2026-06-14 Tushare bulk 历史 PIT 摄取 + 幸存者无偏 universe(自进化回测数据地基,P1-DATA)

> **修订基准**: [P0-8 数据情报](./P0-8-data-intelligence-watchlist-multisource.md) + [P0-8-amendment-2026-05-24-tushare-data-source](./P0-8-amendment-2026-05-24-tushare-data-source.md)(K-001 落地:Tushare Pro 全市场扫描层,官方 SDK only)
> **关联**: [R0 §3 PIT 可复现红线](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md)(存原始字节+checksum+复权 pin)+ 自进化 dossier `docs/research/self-evolution-loop-closure-rqalpha-2026-06-14.md` §11(数据实测:`kline_daily`=0、摄取可行性已探针确认)
> **修订日期**: 2026-06-14
> **触发**: #86 自进化战略转向。实测发现 `kline_daily`/`financial_data` 全空 —— 系统无任何历史 PIT 价格数据(仅 ~5 周 go-live 实时快照),量化参数进化环的回测**无米下锅**。Tushare 探针确认 ≥8 年个股 / ≥11 年指数 + 326 退市码 + adj_factor 权限均可达。owner 卡片拍板摄取 scope = **2015-present + 全市场在市 5528 + 退市 326(幸存者无偏)**。

## 1. 修订前(K-001 amendment-2026-05-24 边界)

- Tushare Pro 仅作**实时全市场扫描层**(`ts.pro_api`,官方 SDK only,严禁 MCP/skill 进运行时数据路径);`TUSHARE_TOKEN` 异质凭证不入 LLM 3+飞书 5 池;数据成本不设 ceiling。
- `kline_daily` collection 有 `save_kline`/`query_kline`(`backend/data/database.py`)但**无摄取 feeder**,生产从未喂入(代码注释「never fed」)。
- 无历史日线、无基本面、无退市码历史 → 无法回测、有幸存者偏差。

## 2. 修订后(P1-DATA bulk 历史摄取)

### 2.1 新增能力:离线 bulk 历史 PIT 摄取 job

- **数据源**:沿用 K-001 边界 —— Tushare Pro 官方 SDK only(`ts.pro_api(token)`),端点 = `daily`(全市场 OHLCV,一次 call ~5528 行/交易日)+ `adj_factor`(复权因子)+ `daily_basic`(估值/换手)+ `stock_basic`(universe,含 `list_status='D'` 退市)+ `index_daily`(基准)+ `fund_daily`(ETF)。**严禁** akshare 节假日 API(沿用 P0-6 静态 `config/holidays.yaml`)。
- **范围(owner 卡片 2026-06-14 锁定)**:**时间 = 2015-01-01 至今(~11 年)**;**universe = 全市场在市 5528 + 退市 326(幸存者无偏)**。退市码须摄取其**在市期间**全历史(在当时点纳入后来退市的名,根除幸存者偏差 = codex 头号数据险)。
- **摄取形态**:离线 batch job(非实时路径;不在 13 cron 内,owner 手动或独立脚本触发);Tushare 每分钟频限 → **限速 + 断点续传 + 幂等**(重跑同 trade_date 不重复写;已摄取跳过)。数据成本不设 ceiling(§2.5)。

### 2.2 PIT 存储纪律(R0 §3 红线,严禁 hash-only)

- 存 **原始未复权字节 + checksum**(仿 BrokerSnapshot;每个 (trade_date 或 ts_code) 拉取结果原始 DataFrame 序列化字节 + sha256);**复权因子作独立 artifact pin**(adj_factor 单独序列,查询时 as-of 重建调整视图,绝不持久化「只有调整后价」—— 后发拆分会破坏 PIT,借 zipline/qlib 范式)。
- 落地:`kline_daily`(database.save_kline)存结构化行 + `marketdata_snapshot` SnapshotStore(append-only index.jsonl + payloads/ 原始字节)存原始字节+checksum;两者经 coverage manifest 关联。
- **幸存者无偏 universe** 落库:`stock_basic` 全集(L+D)+ 每码 list_date/delist_date → 回测在任一历史日构造「该日真实可交易集」(含当时在市、后来退市的)。
- **PIT as-of 读**(借 qlib PIT):基本面/估值按公告日键控,回测查「截至该日已知版本」,严禁用最新修订回测(数据泄漏)。

### 2.3 边界与隔离

- 摄取 job **离线 test-time/offline**,产出供回测 harness(`backend/backtest/`,见 `P2-2-amendment-2026-06-14-deterministic-backtest-harness`)+ rqalpha oracle 取数。
- 摄取的 `kline_daily` 同时服务既有实时 `MarketMetaProvider.get_prev_close` fallback(P0-8-amendment-2026-06-04;只读 PIT 数据,不破实时路径)。
- LLM 严禁参与摄取/解析(沿用 §2.5);摄取产物纯量化。

## 3. 实施与门禁

- 本 amendment = 边界文档(无代码)→ 不触 codex(§3 例外:docs)。**实施代码任务(P1-DATA)** commit 前过 codex-review + 全量 pytest + ruff + redline 全绿(TDD:对抗测试先写 —— 幂等重摄不重复写 / checksum 不符 fail-closed 拒存 / 复权 as-of 重建 bit-exact / 退市码在退市后日期不出现在「该日可交易集」)。
- 摄取 job 实跑(拉真实 Tushare 数据、~数千 call、可能数小时)= **owner-gated 运行期操作**(owner 亲自触发;Claude 协助写 job + 干跑小样本验证)。
- `TUSHARE_TOKEN` 已在 ~/.bashrc(len 56,探针确认 daily/adj_factor/stock_basic/index_daily 全可达);不入 .env / 不入 LLM 飞书池(EXPECTED_POOL_SIZE 仍 8)。

## 4. 红线清单(本 amendment 之后)

1. Tushare 官方 SDK only(严禁 MCP/skill 进运行时数据路径);摄取离线 batch,**不入实时路径、不进 13 cron**。
2. PIT 存储:原始字节 + checksum + 复权因子独立 pin(严禁 hash-only / 严禁只存调整后价);幸存者无偏(纳退市码历史);PIT as-of 读防泄漏。
3. 范围 = 2015-present + 全市场 5528+326 退市(owner 卡片锁定;改范围 = 新 amendment)。
4. 数据成本不设 ceiling;限速 + 断点续传 + 幂等;摄取实跑 owner-gated。
5. LLM 严禁参与摄取/解析(§2.5 不变)。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG 同步;plan.html 新增 P1-DATA 任务(Phase 归属待定,自进化 P1 链最前置)。
</content>
