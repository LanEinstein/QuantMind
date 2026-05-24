# P0-8 修订 — 2026-05-24 引入 Tushare Pro 全市场数据源(官方 Python SDK)

> **修订基准**: [P0-8 数据与资讯可信度](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md) §1.1.1(主备源选型)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §6(收敛技术栈 — 数据源)+ §3(PIT 数据可复现新红线)
> **修订日期**: 2026-05-24
> **触发**: 双线重构 Line 1 要在全 A 股 + ETF(5000+)中量化初筛,而 P0-8 §1.1 原文锁定「第一阶段不引入付费源(tushare pro 等)」、主备链是 adata/akshare 实时行情 + adata/baostock 历史。全市场单日截面 + 5000 档基本面 vip 是 adata/akshare 逐股拉取无法经济覆盖的;Owner 2026-05-24 确认 Tushare Pro(实测全市场访问通过)。

## 1. 修订前(P0-8 原锁定)

- §1(开篇):「第一阶段不引入付费源(tushare pro 等)」。
- §1.1.1 主备源选型:实时行情 adata 主 + akshare 备;历史 K 线 adata 主 + baostock 备;板块/北向 akshare。
- 数据获取走各 vendor 的 Python 包(`adata` / `akshare` / `baostock`),`asyncio.to_thread` 包同步调用。

## 2. 修订后(本 amendment 锁定)

### 2.1 新增 Tushare Pro 为全市场数据源(不取代既有主备)

- **新增** Tushare Pro 用于 **Line 1 全市场截面 + 基本面**:`pro.daily(trade_date)` / `pro.daily_basic(trade_date)` / `pro.adj_factor(trade_date)`(按 `trade_date` 全市场单次 ~5400 行)+ `pro.fina_indicator_vip(period)`(按 period 全市场单次 ~7194 行,5000 档 vip 确认)+ `pro.index_daily(ts_code)` / `pro.fund_daily(...)`。
- **既有主备链不变**:P0-8 §1.1.1 的 adata/akshare 实时行情主备(staleness ≤5s / divergence ≤0.3%)+ adata/baostock 历史 + akshare 多域新闻 5 源 **全部保留**。Tushare 是**新增的全市场扫描层**,不替换实时行情主备,不替换新闻情报。akshare/baostock/adata 作 Tushare 的**兜底回退**(全市场拉取失败时按需逐股回退,接口语义不同不强制 bit-exact)。

### 2.2 调用方式锁定 = 官方 Python SDK(R0 §6 已锁,本 amendment 落实)

- Tushare 走**官方 `tushare` Python SDK**:`pro = ts.pro_api(os.environ["TUSHARE_TOKEN"])` → `pro.daily/daily_basic/fina_indicator_vip/adj_factor/index_daily/fund_daily`。同步调用包 `asyncio.to_thread`(沿用 adata/akshare/baostock 模式)。
- **严禁 MCP server / agent-skill 等「LLM 推理时取数」模式进运行时数据路径**。撞 4 条红线:① R0 §3 PIT 可复现(LLM 临时取数无法快照/replay);② R0 §4 + P0-10 LLM-数据隔离(`screening`/`marketdata_snapshot` 禁 import LLM);③ L-002 全市场纯量化筛 0 LLM;④ P1-7 ¥20/日成本(5000 标的走 LLM 工具循环成本荒谬)。MCP 顶多作开发期交互探查工具,**不进产品代码路径**。
- 原始 payload(DataFrame → canonical 字节)**直接喂 `MarketDataSnapshot`**(R0 §3 新红线 A),供离线 bit-exact replay。

### 2.3 凭证管理:`TUSHARE_TOKEN` 异质凭证(不入 LLM/飞书池)

- `TUSHARE_TOKEN` 是**异质凭证**(数据源 PAT,非 LLM key 非飞书凭证),**不计入** P1-6 §1.1 的 LLM 3 + 飞书 5 池(`EXPECTED_POOL_SIZE` 仍 = 8)。与 `GITHUB_TOKEN`(P2-2 §1.13 Q3)同款 `HETEROGENEOUS_CREDENTIAL_NAMES` 模式处理。
- 仅 `os.environ` 读取,**严禁** `.env`(走 `secrets_validator` 的 `.env` 禁忌前缀扫描软警告)+ 严禁持久化 plaintext;日志只写 `SHA256[:8]` fingerprint(P1-6 §1.2 不变)。
- `secrets_validator` 启动期对 `TUSHARE_TOKEN` 做**软警告**(set 但 shape 明显错时 warn,不阻断启动 —— 与 GITHUB_TOKEN 同级,因数据源不在 always-required 池)。

### 2.4 数据成本不设 ceiling(P1-7 不变)

- P1-7 §1 锁定「数据 / 运维不设 ceiling」(akshare/adata/baostock 免费 + 自托管)。Tushare Pro 积分制赞助费用归**数据成本**,**不设 ceiling**,不计入 LLM ¥20/日 hard。`cost_guard` 不监控数据源。

## 3. 实施期任务调整

### 3.1 `backend/data/tushare_client.py`(新,K-001)

- `ts.pro_api` 全市场单次拉取封装 + `asyncio.to_thread` + 兜底回退 + `TUSHARE_TOKEN` 仅 `os.environ` + fingerprint log。原始 DataFrame/payload 直接喂 K-002 `MarketDataSnapshot`。

### 3.2 `config/data_sources.yaml`(走 git diff + 重启)

- 新增 `tushare` 段(endpoint 名 / vip 标记 / 兜底链),runtime 不可改(改走 amendment + git diff + 重启,沿用 P0-7 配置红线)。

### 3.3 `backend/services/secrets_validator.py`(K-001)

- `HETEROGENEOUS_CREDENTIAL_NAMES` 加 `TUSHARE_TOKEN` + 其 shape 正则;`.env` assignment 扫描正则加 `TUSHARE_TOKEN`(`.env` 禁放)。`EXPECTED_POOL_SIZE` 仍 = 8 不变。

## 4. 红线清单(本 amendment 之后)

1. Tushare Pro **新增**为全市场扫描层;P0-8 §1.1.1 实时行情主备(adata/akshare)+ 历史(adata/baostock)+ 多域新闻 5 源 **全部不变**。
2. Tushare **仅走官方 Python SDK + `asyncio.to_thread`**;**严禁 MCP/skill 等 LLM 取数模式进运行时数据路径**(撞 PIT 可复现 + LLM-数据隔离 + 纯量化筛 + 成本 4 红线)。
3. Tushare 原始 payload **直接喂 `MarketDataSnapshot`**(R0 §3 存原始字节 + checksum + 用前校验),支持离线 bit-exact replay。
4. `TUSHARE_TOKEN` 异质凭证,**不入** LLM 3 + 飞书 5 池(`EXPECTED_POOL_SIZE` 仍 = 8);仅 `os.environ`,严禁 `.env` + plaintext 持久化;日志仅 fingerprint。
5. 数据成本**不设 ceiling**(P1-7 不变);Tushare 积分费用不计入 LLM ¥20/日 hard,`cost_guard` 不监控。
6. `config/data_sources.yaml` 的 `tushare` 段 runtime 不可改(改走 amendment + git diff + 重启)。
7. `backend/data/tushare_client.py` + `backend/marketdata_snapshot/` 严禁 `import backend.{llm,agents,mirofish}`(纯数据层;继承 P0-10 隔离)。

## 5. 修订记录追加

`docs/plan.html` Phase K 任务(K-001)已含本 amendment 指针;修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.5 的「第一阶段不引入 tushare」表述由本 amendment 修订为「Tushare Pro 全市场扫描层 + adata/akshare/baostock 兜底」。
