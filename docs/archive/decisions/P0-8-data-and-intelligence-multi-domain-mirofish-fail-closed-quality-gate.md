# P0-8 — 数据与资讯可信度(行情严格主备 + 全 watchlist 30s 快照 + 多域 5 源情报 + MiroFish 双路径接线 + DataQualityState 早返第四种冻结)

## 元数据

| 字段       | 值 |
|-----------|----|
| 决策编号   | P0-8 |
| 决策日期   | 2026-05-09 |
| 状态       | ✅ 已锁定 |
| 决策人     | dr.zhang.xjtu@gmail.com (项目所有者) |
| 关联 audit | `docs/quantmind_project_audit_2026-05-07.md` §4 / §6 / §10 / §12 |
| 关联清单   | `docs/quantmind_owner_decision_points_2026-05-07.md` §P0-8 + §P2-1(MiroFish 使用范围 — 由本决策前置到 P0 范围) |
| 依赖决策   | `docs/decisions/P0-1-simulation-base-feishu-overlay.md`(尤其 §1.6 多 Agent 辩论 + §2 红线 7 数据降级时只发观察/暂停)+ `docs/decisions/P0-3-instruction-plan-strict-schema-and-text-template.md`(尤其 §1.5.2 DataSnapshot.quote_source / quote_latency_ms / news_source / prev_close 字段 + §1.7 数据流)+ `docs/decisions/P0-6-acceptance-45-day-rolling-stability-and-strategy-gates.md`(尤其 §1.2.1 数据延迟与缺失率 ≤ 1% 硬门槛)+ `docs/decisions/P0-7-risk-redlines-position-circuit-universe-llm-immutability.md`(尤其 §1.3.5 check 12 limit-up/down 依赖 prev_close+current_price + §1.7 数据流早返机制)|
| 派生 amendment | `docs/decisions/P0-3-amendment-2026-05-09-multi-domain-news-source.md`(实施期产出;扩展 P0-3 §1.5.2 `news_source: str` 到 `news_sources_by_domain: dict[str, tuple[str, ...]]`;`evidence_ids` 字段语义补充 MiroFish 仿真 ID);`docs/decisions/P2-1-superseded-by-P0-8.md`(实施期产出;原 P2-1 MiroFish 使用范围决策被 P0-8 §1.4 取代)|
| 替代       | `config/data_sources.yaml` 当前 6 行配置整体重写 |

## 决策摘要

QuantMind 第一阶段数据与资讯可信度采用 **严格行情主备 + 全 watchlist 30s 快照 + 多域 5 源情报(财经 2 + 时政 1 + 全球 2)+ MiroFish 双路径接线(事件驱动 + 盘后复盘)+ DataQualityState 早返降级 HOLD 第四种买卖类冻结来源** 架构:

1. **实时行情主备**:adata 主 + akshare 备(沿用)。新增三类硬阈值:`staleness_threshold_seconds=5`(主源延迟阈值)/ `divergence_threshold_pct=0.003`(主备价差阈值,> 0.3% 触发降级)/ `freeze_buy_sell_on_quality_breach=true`(质量违规即冻结买卖类)。第一阶段不引入付费源(tushare pro 等)。

2. **全 watchlist 30s 快照采集**:audit §6.2 揭示"后台每 30 秒只采三大指数,不采全 watchlist 个股"是 P0 缺口。本决策升级为"全 watchlist 个股 + 三大指数同 30s 频率",通过 `akshare.stock_zh_a_spot_em()` 单次返回全市场快照后按 watchlist 过滤。新增 `watchlist_market_snapshots` collection 持久化每分钟快照,与 P0-6 数据缺失率 ≤ 1% 硬门槛对齐。

3. **多域 5 源情报选型**(MiroFish 隐性因果链推演的输入域):
   - **domain_finance**(2 源):`akshare.stock_news_em`(东财财经)+ `akshare.stock_info_global_cls`(财联社快讯)
   - **domain_politics**(1 源):`akshare.news_cctv`(新闻联播文字稿 — 时政/政策核心源)
   - **domain_global**(2 源):`akshare.stock_info_global_em`(东财全球)+ `akshare.stock_info_global_sina`(新浪全球)
   - **domain_military / domain_social** = P1 范围(需国防部 RSS / sina mil / 微博 / 知乎 / 雪球 — 爬虫与合规风险;不在 P0-8 第一阶段)

4. **MiroFish 接线**(原 P2-1 由本决策前置到 P0):双路径触发:
   - **事件驱动**:`backend.mirofish.event_filter` 从多域资讯流识别"重大事件"(severity ≥ HIGH),实时调用 MiroFishSimulator 推演,`runs_per_day_cap=20`
   - **盘后复盘**:每交易日 17:00 Asia/Shanghai 对全 watchlist 跑 MiroFish 一次,`runs_per_day_cap=50`
   - `inflection_points` / `extreme_scenarios` extractor 默认 enabled
   - **MiroFish 输出仅写入 `evidence_collection`,不进 `RiskCheckSummary`**(继承 P0-1 §2 红线 8 / P0-7 §2 红线 11 — LLM 与 MiroFish 不直接产出风控判断)

5. **DataQualityState 早返机制**(第四种买卖类路由冻结来源):
   - 财经主源 `stock_news_em` 断流 ≥ 30 min → 冻结
   - 时政 `news_cctv` 断流 ≥ 6 h → 冻结(新闻联播一日一期,留 6 小时阈值容忍 cron 抖动)
   - 全球域 require_at_least_n_alive=1,任一存活不冻结;双断 ≥ 60 min → 冻结
   - 行情 staleness > 5s 或 divergence > 0.3% → 冻结
   - 停牌(由 `akshare.stock_zh_a_spot_em` 字段判断)→ 单股冻结(本股 InstructionPlan 必降级 HOLD)
   - **DataQualityState 在 InstructionPlanBuilder 早返判定,不进 RiskEngine 14-check**(避免再次扩 check;risk_summary 长度恒 14 不变;P0-7 14-check 与 P0-8 早返机制并列)
   - **不暂停 simulation_auto 分析**(继承 P0-1 §1.1 always-on 底座精神):InstructionPlan 仍按调度生成,但触发早返时降级 HOLD + ledger 留痕

6. **三种现有买卖类路由冻结来源 + 第四种 P0-8 来源 = 共 4 种**(全部独立并行,任一为真即冻结):
   - 切换中(P0-1 §1.4)
   - OPEN/EXPIRED reconciliation_ticket(P0-5 §1.6)
   - 熔断冷却(P0-7 §1.7)
   - **数据质量违规(P0-8 §1.5)— 本决策新增**

7. **本地缓存策略**:行情快照写入 Redis(键 `market:quote:{code}`,TTL=120s)+ MongoDB `watchlist_market_snapshots`(永久);多域资讯写入 MongoDB `news_articles`(扩字段 `domain` + `source`)+ Redis pub/sub(`news:{domain}` 频道实时推送)。

8. **LLM 严禁参与数据质量判定**:`backend/data/data_quality.py` / `backend/data/divergence.py` / `backend/data/staleness.py` 严禁 `import backend.llm.*`(继承 P0-4 / P0-5 / P0-7 LLM 隔离精神);所有阈值判定走纯 Python。MiroFish 内部 LLM 调用不触发数据质量判定路径(MiroFish 输出走 evidence,不走 RiskEngine / DataQualityState)。

## 1. 决策具体内容

### 1.1 实时行情主备 + 偏差/延迟/staleness 阈值

#### 1.1.1 主备源选型(沿用)

| 用途 | 主源 | 备源 | 校验方式 |
|------|------|------|----------|
| 指数实时行情 | `adata.stock.market.get_market_index_current` | `akshare.index_zh_a_hist`(取末行近似) | 偏差 ≤ 0.3% |
| 单股实时行情 | `adata.stock.market.list_market_current(code_list=[code])` | `akshare.stock_zh_a_spot_em()` 后 filter | 偏差 ≤ 0.3% |
| 多股批量实时 | `MarketDataService.get_stock_list_realtime(codes)` | `akshare.stock_zh_a_spot_em()` | 偏差 ≤ 0.3% |
| 历史 K 线 | `adata.stock.market.get_market` | `baostock.query_history_k_data_plus` | 不强制校验(回填用) |
| 板块行情 | `akshare.stock_board_industry_name_em` | 无 | — |
| 北向资金 | `akshare.stock_hsgt_hist_em(symbol="北向资金")` | 无 | — |

#### 1.1.2 三类硬阈值

```yaml
# config/data_sources.yaml(实施期重写)

market_data:
  primary: adata
  fallback: akshare
  refresh_interval_seconds: 30

  # P0-8 新增:质量门槛
  staleness_threshold_seconds: 5           # 主源 quote 时间戳到 now 的最大延迟
  divergence_threshold_pct: 0.003          # 主备同标的价差 / 主源价 > 0.3% 即降级
  freeze_buy_sell_on_quality_breach: true  # 违规则触发 DataQualityState 早返
  minimum_freshness_seconds_for_buy_sell: 60  # InstructionPlanBuilder 拒绝使用 > 60s 的快照

history_data:
  primary: adata
  fallback: baostock
  default_period: 1y
```

#### 1.1.3 staleness / divergence 计算函数

```python
# backend/data/staleness.py(实施期新增,纯 Python 无 LLM 依赖)

from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class StalenessReport:
    quote_source: str
    snapshot_at: datetime
    now: datetime
    age_seconds: float
    threshold_seconds: float
    is_stale: bool


def evaluate_staleness(
    *,
    snapshot_at: datetime,
    now: datetime,
    quote_source: str,
    threshold_seconds: float,
) -> StalenessReport:
    age = (now - snapshot_at).total_seconds()
    return StalenessReport(
        quote_source=quote_source,
        snapshot_at=snapshot_at,
        now=now,
        age_seconds=age,
        threshold_seconds=threshold_seconds,
        is_stale=age > threshold_seconds,
    )
```

```python
# backend/data/divergence.py(实施期新增)

@dataclass(frozen=True)
class DivergenceReport:
    code: str
    primary_price: float
    fallback_price: float | None
    relative_diff: float | None  # |p - f| / p;fallback 缺失时 None
    threshold_pct: float
    is_divergent: bool


def evaluate_divergence(
    *,
    code: str,
    primary_price: float,
    fallback_price: float | None,
    threshold_pct: float,
) -> DivergenceReport:
    if fallback_price is None or primary_price <= 0:
        return DivergenceReport(
            code=code,
            primary_price=primary_price,
            fallback_price=fallback_price,
            relative_diff=None,
            threshold_pct=threshold_pct,
            is_divergent=False,  # 备源缺失不强制降级 — 由其他守门(staleness)接管
        )
    rel = abs(primary_price - fallback_price) / primary_price
    return DivergenceReport(
        code=code,
        primary_price=primary_price,
        fallback_price=fallback_price,
        relative_diff=rel,
        threshold_pct=threshold_pct,
        is_divergent=rel > threshold_pct,
    )
```

#### 1.1.4 minimum_freshness_seconds_for_buy_sell 防线

InstructionPlanBuilder 在派生 Order 前必须验证 stock_meta.snapshot_at 到 now 的差值 ≤ 60s;否则即使 DataQualityState 全局是 acceptable,**单股层面仍 fail-closed REJECTED**(数据陈旧不应进入新增 BUY/SELL 决策)。这是对 P0-7 check 12 涨跌停判定 prev_close + current_price 的进一步加固。

### 1.2 全 watchlist 30s 快照采集

#### 1.2.1 调度器升级(`backend/data/scheduler.py`)

新增第三种 cron job(与现有 `_run_market_job` 并列,但 scope 扩到 watchlist 个股):

```python
# 调度器伪代码

self._scheduler.add_job(
    self._run_watchlist_snapshot_job,
    "interval",
    seconds=30,
    id="watchlist_snapshot_job",
    name="Watchlist stocks 30s snapshot",
)


async def _run_watchlist_snapshot_job(self) -> None:
    """盘中每 30s 拉取全 watchlist 个股快照(批量调用,降低 API 压力)。"""
    if not is_trading_hours():
        return
    try:
        watchlist_codes = await self._watchlist.get_active_codes()
        if not watchlist_codes:
            return
        # 单次 stock_zh_a_spot_em 拉全市场 → filter watchlist
        df = await asyncio.to_thread(akshare.stock_zh_a_spot_em)
        snapshots = self._filter_and_normalize(df, watchlist_codes)
        if snapshots:
            await self._mongodb.save_watchlist_snapshots(snapshots)
            await self._redis_set_quotes(snapshots, ttl_seconds=120)
            await publish_watchlist_update(self._redis, snapshots)
            self._log.debug(
                "watchlist_snapshot_complete",
                count=len(snapshots),
                source="akshare.stock_zh_a_spot_em",
            )
    except Exception as exc:
        self._log.warning("watchlist_snapshot_failed", error=str(exc))
```

#### 1.2.2 watchlist_market_snapshots collection

```python
# backend/models/market.py 实施期新增

class WatchlistSnapshot(BaseModel):
    """单个 watchlist 个股的 30s 快照(by-value,frozen)。"""
    model_config = ConfigDict(frozen=True)

    code: str = Field(pattern=r"^\d{6}$")
    name: str
    snapshot_at: datetime  # 快照时刻(Asia/Shanghai)
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: int
    amount: float
    change: float
    change_pct: float
    is_suspended: bool = False  # 停牌检测(详见 §1.6.1)
    quote_source: str  # e.g. "akshare.stock_zh_a_spot_em"
```

```python
# 索引

watchlist_market_snapshots:
  unique:
    - (code, snapshot_at)  # 同一时刻同一股票仅一条
  indexed:
    - (snapshot_at, -1)   # 按时间倒序查询最新
    - (code, snapshot_at, -1)  # 按 code 查最新快照
```

#### 1.2.3 与 P0-6 数据缺失率 ≤ 1% 的对齐

P0-6 §1.2.1 数据延迟与缺失率 = `1 - (covered_minutes / expected_minutes)`。本决策定义:

- **expected_minutes**:每个交易日 09:30-11:30 + 13:00-15:00 共 240 分钟 × 全 watchlist 个股数(取 `len(active_watchlist) at trade_date`)
- **covered_minutes**:对每个 (code, minute) 桶,该分钟内 watchlist_market_snapshots 至少 1 条记录即算 covered
- 30s 频率下每分钟应有 ≥ 2 条记录;1 条记录算 partial(仍计 covered);0 条算 missing
- 单股停牌期间 minute 桶仍计入 expected(停牌不是数据缺失,标 `is_suspended=true` 即可)

#### 1.2.4 Redis 缓存(本地)

```yaml
redis:
  quote_cache:
    key_pattern: "market:quote:{code}"
    ttl_seconds: 120
    payload: "JSON of WatchlistSnapshot"
  pub_channel: "watchlist:update"
```

InstructionPlanBuilder 优先读 Redis 缓存;Redis miss / TTL 过期 → 读 MongoDB 最近 1 条 `watchlist_market_snapshots`;两者都缺 → DataQualityState 标该股 `quote_unavailable=true`,InstructionPlan 必降级 HOLD。

### 1.3 多域 5 源情报选型

#### 1.3.1 五源接入清单

| 域 | 源 | akshare 接口 | 调度频率 | 域可信度评级 |
|----|----|----|----------|-------------|
| domain_finance | 东财财经 | `stock_news_em(symbol="")` | 5 min | high |
| domain_finance | 财联社快讯 | `stock_info_global_cls()` | 5 min | high |
| domain_politics | 新闻联播文字稿 | `news_cctv(date=YYYYMMDD)` | 60 min(每日 19:30 之后才有当日稿;盘前 09:00 触发) | high(官方权威) |
| domain_global | 东财全球 | `stock_info_global_em()` | 15 min | medium |
| domain_global | 新浪全球 | `stock_info_global_sina()` | 15 min | medium |

第一阶段不纳入:`stock_info_global_ths`(同花顺;接口稳定性较低)/ `futures_news_baidu`(衍生品资讯;A 股关联弱)/ 微博 / 知乎 / 雪球 / 国防部 RSS(需爬虫与合规评估)— 全部留 P1。

#### 1.3.2 NewsArticle 模型扩展

```python
# backend/models/market.py(实施期修改)

class NewsArticle(BaseModel):
    """多域资讯条目(by-value,frozen)。"""
    model_config = ConfigDict(frozen=True)

    article_id: str       # hash(title + published_at + source)
    domain: str           # "finance" | "politics" | "global" | "military" | "social"
    source: str           # e.g. "akshare.stock_news_em" / "akshare.news_cctv"
    title: str
    content: str
    url: str | None = None
    published_at: datetime
    fetched_at: datetime
    related_codes: tuple[str, ...] = ()  # 正则提取的 6 位 A 股代码
    importance_score: float | None = None  # 0-1;由 MiroFish event_filter 估值
```

#### 1.3.3 调度器扩展

```python
# backend/data/scheduler.py(实施期扩展)

# 现有 _run_news_job → 改为多源调度器

async def _run_news_finance_job(self) -> None:
    """5 min 拉取财经域:stock_news_em + stock_info_global_cls。"""
    await self._fetch_and_save("finance", [
        ("akshare.stock_news_em", self._news_crawler.fetch_eastmoney),
        ("akshare.stock_info_global_cls", self._news_crawler.fetch_cls),
    ])


async def _run_news_politics_job(self) -> None:
    """每日多次盘前 + 盘后采集 news_cctv。"""
    today = datetime.now(tz=SHANGHAI).strftime("%Y%m%d")
    await self._fetch_and_save("politics", [
        ("akshare.news_cctv", lambda: self._news_crawler.fetch_cctv(date=today)),
    ])


async def _run_news_global_job(self) -> None:
    """15 min 拉取全球域:stock_info_global_em + stock_info_global_sina。"""
    await self._fetch_and_save("global", [
        ("akshare.stock_info_global_em", self._news_crawler.fetch_global_em),
        ("akshare.stock_info_global_sina", self._news_crawler.fetch_global_sina),
    ])
```

```yaml
# config/data_sources.yaml(实施期重写)

news:
  domain_finance:
    sources:
      - akshare.stock_news_em
      - akshare.stock_info_global_cls
    refresh_interval_seconds: 300    # 5 min
    importance_threshold: 0.3

  domain_politics:
    sources:
      - akshare.news_cctv
    refresh_cron:                     # 一日多次
      - "09:00 Asia/Shanghai"          # 盘前
      - "15:30 Asia/Shanghai"          # 盘后(可能有当日预告)
      - "20:00 Asia/Shanghai"          # 新闻联播之后(主要拉取窗口)
    importance_threshold: 0.5         # 时政域权重稍低门槛(信号本就稀疏)

  domain_global:
    sources:
      - akshare.stock_info_global_em
      - akshare.stock_info_global_sina
    refresh_interval_seconds: 900    # 15 min
    importance_threshold: 0.4

  # P1 范围(本决策不实现):
  # domain_military: [国防部 RSS / sina mil]
  # domain_social: [微博热搜 / 知乎 / 雪球]
```

#### 1.3.4 多源去重

```python
# backend/data/news_dedupe.py(实施期新增)

def dedupe_articles(
    articles: tuple[NewsArticle, ...],
    *,
    title_window_seconds: int = 60,
) -> tuple[NewsArticle, ...]:
    """同一 domain 内,标题相同 + published_at 差 ≤ 60s → 视为重复,保留最早。
    跨 domain 不去重(同一事件多域评价是 MiroFish 输入价值)。
    """
```

### 1.4 MiroFish 接线策略(原 P2-1 前置到 P0-8)

#### 1.4.1 现状缺口与本决策范围

audit §10.2 揭示:
> `backend/agents/intelligence_officer.py` 只有当 `services.mirofish_simulator is not None` 时才会运行 MiroFish。但 `backend/main.py` 和 `backend/api/analysis.py` 构造 `AnalysisServices` 时没有创建并传入 `MiroFishSimulator`。

本决策锁定:**实施期必须为 `AnalysisServices` 注入 `MiroFishSimulator`**;原 P2-1 决策由本决策第 §1.4 取代,实施期产出 `docs/decisions/P2-1-superseded-by-P0-8.md` 文档显式记录。

#### 1.4.2 双路径触发

```yaml
# config/mirofish.yaml(实施期扩展)

trigger_paths:
  realtime_event:
    enabled: true
    upstream: backend.mirofish.event_filter
    severity_threshold: HIGH    # 仅 HIGH 与 EXTREME 触发实时仿真
    runs_per_day_cap: 20
    runs_per_minute_cap: 3      # 防爆发(单分钟内多个 HIGH 事件)
    timeout_seconds: 60
    fail_action: SKIP_AND_LOG   # 失败不冻结买卖,只记录

  postclose_review:
    enabled: true
    cron: "17:00 Asia/Shanghai"  # 对齐 P0-4 / P0-5 cutoff 之后
    scope: watchlist_all
    runs_per_day_cap: 50
    timeout_seconds_per_stock: 30
    fail_action: SKIP_AND_LOG

# extractor 默认 enabled
extractors:
  hidden_variables:
    enabled: true
  inflection_points:
    enabled: true
  extreme_scenarios:
    enabled: true
```

#### 1.4.3 输出路径

MiroFish 输出统一写入 `evidence_collection`(P0-3 §1.5.4 by-reference 链接的目标 collection),**绝不**写入 `RiskCheckSummary`:

```python
# backend/mirofish/output_writer.py(实施期新增伪代码)

async def write_simulation_evidence(
    sim_result: SimulationResult,
    *,
    evidence_repo: EvidenceRepo,
    related_codes: tuple[str, ...],
) -> str:
    """把 MiroFish 仿真结果写入 evidence_collection,返回 evidence_id。

    InstructionPlanBuilder 后续构造 InstructionPlan 时把该 evidence_id
    加入 evidence_ids tuple(by-reference)。LLM 与 MiroFish 不直接产出
    RiskCheckSummary 的任一字段(继承 P0-7 §2 红线 11)。
    """
    evidence_id = f"MIROFISH-{sim_result.run_id}"
    await evidence_repo.upsert(
        evidence_id=evidence_id,
        kind="mirofish_simulation",
        related_codes=related_codes,
        payload=sim_result.model_dump(),
    )
    return evidence_id
```

#### 1.4.4 多 Agent 辩论中的 MiroFish 角色

`intelligence_officer` 在汇总阶段把 MiroFish 输出作为"隐性变量 + 拐点 + 极端场景"证据并入辩论上下文;`bull_researcher` / `bear_researcher` 可在辩论中引用,但**最终 InstructionPlan 字段仍由确定性代码 + RiskEngine 14-check 决定**(继承 P0-1 §1.6 / P0-7 §2 红线 11)。

### 1.5 DataQualityState 早返机制(第四种买卖类冻结)

#### 1.5.1 DataQualityState 模型

```python
# backend/data/data_quality.py(实施期新增,纯 Python + 无 LLM)

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class DataQualityState:
    """数据质量门槛聚合状态。

    InstructionPlanBuilder 在调用 RiskEngine 之前装配此对象;
    is_acceptable=False 即触发早返降级 HOLD(本决策第四种买卖类冻结来源)。
    """

    evaluated_at: datetime

    # === 行情维度 ===
    quote_staleness_breach: bool     # staleness_threshold_seconds 违规
    quote_divergence_breach: bool    # divergence_threshold_pct 违规
    quote_unavailable: bool          # Redis miss + MongoDB miss

    # === 资讯维度 ===
    finance_primary_outage_minutes: float  # stock_news_em 距上次成功拉取的分钟数
    politics_outage_hours: float           # news_cctv 距上次成功拉取的小时数
    global_alive_count: int                # global 域当前存活源数(0-2)

    finance_breach: bool   # finance_primary_outage_minutes >= 30
    politics_breach: bool  # politics_outage_hours >= 6
    global_breach: bool    # global_alive_count < 1 持续 >= 60 min

    # === 单股维度(仅 BUY/SELL InstructionPlan 装配时填入)===
    stock_suspended: Optional[bool] = None  # 当前单股是否停牌

    # === 总聚合(由 builder 计算)===
    @property
    def is_acceptable(self) -> bool:
        breached = (
            self.quote_staleness_breach
            or self.quote_divergence_breach
            or self.quote_unavailable
            or self.finance_breach
            or self.politics_breach
            or self.global_breach
            or (self.stock_suspended is True)
        )
        return not breached

    @property
    def degradation_reason(self) -> str:
        """触发降级的人读理由(供 HOLD InstructionPlan rejection_reason 字段)。"""
        reasons = []
        if self.quote_staleness_breach:
            reasons.append("quote_stale")
        if self.quote_divergence_breach:
            reasons.append("quote_divergence")
        if self.quote_unavailable:
            reasons.append("quote_unavailable")
        if self.finance_breach:
            reasons.append(f"finance_outage>{30}min")
        if self.politics_breach:
            reasons.append(f"politics_outage>{6}h")
        if self.global_breach:
            reasons.append("global_double_outage>60min")
        if self.stock_suspended:
            reasons.append("stock_suspended")
        return "+".join(reasons) if reasons else "ok"
```

#### 1.5.2 阈值锁定

```yaml
# config/data_sources.yaml(实施期续写)

data_quality:
  quote:
    staleness_threshold_seconds: 5
    divergence_threshold_pct: 0.003
    minimum_freshness_seconds_for_buy_sell: 60

  news:
    finance_primary_outage_minutes: 30
    politics_outage_hours: 6
    global_outage_minutes: 60
    global_require_at_least_n_alive: 1

  freeze_buy_sell_on_quality_breach: true
  pause_simulation_auto_on_quality_breach: false  # always-on 底座不暂停
```

#### 1.5.3 InstructionPlanBuilder 早返路径

```python
# backend/services/instruction_plan_builder.py(P0-7 §1.5 已规划,本决策扩展)

async def build_instruction_plan(
    fund_manager_record: FundManagerRecord,
    *,
    mock_broker: MockBroker,
    instruction_plan_repo: InstructionPlanRepo,
    circuit_breaker_repo: CircuitBreakerRepo,
    data_quality_provider: DataQualityProvider,  # P0-8 新增
    quote_provider: QuoteProvider,
    stock_meta_provider: StockMetaProvider,
    risk_engine: RiskEngine,
    config: RiskConfig,
    now: datetime,
) -> InstructionPlan:
    """构建 InstructionPlan;早返链(任一为真即降级 HOLD,不进 RiskEngine):

    1. 切换中(P0-1 §1.4)
    2. OPEN/EXPIRED reconciliation_ticket(P0-5 §1.6)
    3. CircuitBreakerState.is_in_halt + (BUY 或 apply_to_sell_orders=true)(P0-7 §1.7)
    4. *** DataQualityState.is_acceptable=False(P0-8 §1.5)— 本决策新增 ***

    全部通过后才装配 daily_state + stock_meta + 派生 Order + 调用
    risk_engine.validate_order(14-check)。
    """
    # ... P0-1 / P0-5 / P0-7 早返链 ...

    # P0-8 新增早返(放最后,因为它需要 stock_code 来判定停牌)
    data_quality = await data_quality_provider.evaluate(
        stock_code=fund_manager_record.stock_code,
        now=now,
    )
    if not data_quality.is_acceptable:
        log.info(
            "instruction_plan_degraded_to_hold",
            reason=data_quality.degradation_reason,
            stock_code=fund_manager_record.stock_code,
        )
        return _build_hold_plan(
            fund_manager_record=fund_manager_record,
            reason=f"data_quality:{data_quality.degradation_reason}",
            now=now,
        )

    # ... 装配 daily_state + stock_meta + 14-check ...
```

#### 1.5.4 不暂停 simulation_auto

`pause_simulation_auto_on_quality_breach=false`(锁定):多 Agent 辩论 + signal 生成 + InstructionPlan 装配仍按调度运行;早返触发时仅"降级为 HOLD InstructionPlan",P0-3 §1.6.4 HOLD 不路由不发飞书,但仍入 `instruction_plans` collection 与 `decision_ledger` 留痕。

理由:
- 继承 P0-1 §1.1 "always-on simulation_auto 底座"精神
- equity_curve mark-to-market 不依赖 InstructionPlan,可独立持续(P0-6 §1.3.1 最大回撤计算不受影响)
- HOLD ledger 留痕便于复盘"为什么这天没生成买卖指令"

#### 1.5.5 与 P0-6 指标的衔接

| P0-6 指标 | P0-8 早返机制的影响 |
|----------|-------------------|
| 数据延迟与缺失率 ≤ 1% | 全 watchlist 30s 快照 + Redis TTL 120s 缓冲使该指标可达 |
| 指令完整率 ≥ 95% | HOLD InstructionPlan 不计入分母(P0-6 §1.2.1 终态分布仅含 BUY/SELL),早返 HOLD **不会**冲击 95% 门槛 |
| 信号生成成功率 ≥ 95% | 多 Agent 辩论持续运行(simulation_auto 不暂停),信号生成路径不受 P0-8 早返影响 |
| 风控拦截率(观察) | DataQualityState 早返不进 RiskEngine,不计入"拦截率";独立统计为 `data_quality_breach_rate`(P0-6 §1.4 七项观察指标外可补一项) |

### 1.6 停牌检测与 evidence_ids 多域支持

#### 1.6.1 停牌检测(单股层面)

```python
# backend/data/suspension.py(实施期新增,纯 Python)

def is_suspended_from_spot_em(row: dict) -> bool:
    """从 akshare.stock_zh_a_spot_em 单行字段判断是否停牌。

    规则:
    - 价格字段(最新价 / 昨收)缺失 / 0 → 停牌
    - 成交量 == 0 且非新股次新 → 停牌(成交量/成交额持续为 0)
    - 涨跌幅 NaN → 停牌

    任一规则匹配即标 is_suspended=True;由调度器在 watchlist_snapshot_job
    生成 WatchlistSnapshot 时填入。
    """
```

InstructionPlanBuilder 在 §1.5.3 早返链中通过 `DataQualityProvider.evaluate(stock_code=...)` 查询该股最近一条 WatchlistSnapshot 的 `is_suspended` 字段;True 即触发早返降级 HOLD。

#### 1.6.2 evidence_ids 多域支持

P0-3 §1.5.4 InstructionPlan 已有 `evidence_ids: tuple[str, ...]` 字段,本决策不改 schema,但**约定 evidence_id 命名前缀**:

| 前缀 | 用途 |
|------|------|
| `NEWS-` | 多域资讯条目(`NEWS-{article_id}`) |
| `MIROFISH-` | MiroFish 仿真结果(`MIROFISH-{run_id}`) |
| `MARKET-` | 行情快照特征(`MARKET-{code}-{snapshot_at}`) |
| `RISK-` | RiskCheckSummary 详细 ValidationResult(`RISK-{instruction_id}`) |
| `DEBATE-` | 多 Agent 辩论 round 详情(`DEBATE-{run_id}-r{n}`) |

InstructionPlanBuilder 装配 evidence_ids 时按上述前缀分类,前端 InstructionCenter 可按前缀过滤显示。

#### 1.6.3 P0-3 amendment 范围(派生)

P0-3 §1.5.2 `news_source: str` 字段(单值)无法表达多域多源。派生 `P0-3-amendment-2026-05-09-multi-domain-news-source.md`:

- `news_source: str` → `news_sources_by_domain: dict[str, tuple[str, ...]]`
  - 例:`{"finance": ("stock_news_em", "stock_info_global_cls"), "politics": ("news_cctv",), "global": ("stock_info_global_em", "stock_info_global_sina")}`
- `news_window_seconds: int | None`(单值,沿用)— 含义改为"任一域最长资讯窗口"
- `evidence_ids` 字段语义补充:必须遵循 §1.6.2 前缀约定

### 1.7 数据流总览

```
真实行情 + 多域资讯采集
        │
        ├── 行情:adata 主 + akshare 备(每 30s)
        │     └── DivergenceReport / StalenessReport(纯 Python,无 LLM)
        │     └── watchlist_market_snapshots collection + Redis quote cache
        │
        ├── 多域资讯:5 源调度
        │     ├── domain_finance(5 min):stock_news_em + stock_info_global_cls
        │     ├── domain_politics(09:00 / 15:30 / 20:00):news_cctv
        │     └── domain_global(15 min):stock_info_global_em + stock_info_global_sina
        │     └── news_articles collection(扩 domain + source 字段)
        │
        ├── MiroFish 双路径(P0-8 §1.4)
        │     ├── 事件驱动(severity ≥ HIGH;runs_per_day_cap=20)
        │     └── 盘后复盘(17:00;watchlist_all;runs_per_day_cap=50)
        │     └── evidence_collection(MIROFISH-{run_id})
        │
        └── 停牌检测(akshare.stock_zh_a_spot_em 字段判断)
              └── WatchlistSnapshot.is_suspended

                            │
                            ▼
        多 Agent 辩论(P0-1 §1.6)使用多域证据
                            │
                            ▼
        FundManagerRecord(parse_ok=True)
                            │
                            ▼
InstructionPlanBuilder 早返链(任一为真即降级 HOLD):
  ├── 1. P0-1 切换中
  ├── 2. P0-5 OPEN/EXPIRED reconciliation_ticket
  ├── 3. P0-7 CircuitBreakerState.is_in_halt + (BUY 或 apply_to_sell)
  └── 4. *** P0-8 DataQualityState.is_acceptable=False ***
        ├── quote staleness > 5s
        ├── quote divergence > 0.3%
        ├── quote 不可得(Redis + MongoDB 双 miss)
        ├── finance_primary 断流 ≥ 30 min
        ├── politics 断流 ≥ 6 h
        ├── global 双断 ≥ 60 min
        └── 单股停牌
                            │
                            ▼
       全部通过 → 装配 daily_state + stock_meta(P0-7 §1.5)
                            │
                            ▼
           RiskEngine.validate_order(14-check,P0-7 §1.3)
                            │
                            ▼
           InstructionPlan(VALIDATED / REJECTED) → ModeRouter
```

## 2. 红线(立即生效)

1. **多域 5 源情报选型锁定**(`stock_news_em` + `stock_info_global_cls` + `news_cctv` + `stock_info_global_em` + `stock_info_global_sina`):新增/删除任何源必须先走 `P0-8-amendment-{date}-{原因}.md`;微博 / 知乎 / 雪球 / 国防部等需爬虫的源在 P0-8 第一阶段严禁实现(合规风险与稳定性未评估)。

2. **行情主备 + 三类硬阈值锁定**:`staleness_threshold_seconds=5` / `divergence_threshold_pct=0.003` / `minimum_freshness_seconds_for_buy_sell=60`;调整任一阈值必须先走 amendment;实施期 lint rule 阻止常量被覆写。

3. **DataQualityState 早返是第四种买卖类路由冻结来源**:与 P0-1 切换冻结 / P0-5 ticket 冻结 / P0-7 熔断冷却独立并行;任一为真即冻结买卖类 InstructionPlan;严禁绕过任一种冻结。

4. **`pause_simulation_auto_on_quality_breach=false` 锁定**:数据质量违规**不**暂停 simulation_auto;multi-Agent 辩论 + signal 生成 + InstructionPlan 装配持续运行,只是 Builder 早返降级 HOLD;改为 true 必须先走 amendment(违反 P0-1 §1.1 always-on 精神)。

5. **MiroFish 双路径触发锁定**:事件驱动(severity ≥ HIGH;runs_per_day_cap=20;runs_per_minute_cap=3)+ 盘后复盘(17:00 cron;watchlist_all;runs_per_day_cap=50);新增触发路径(如盘前 / 多 Agent 辩论中实时调用)必须先走 amendment;实施期必须为 `AnalysisServices` 注入 `MiroFishSimulator`(audit §10.2 缺口必修)。

6. **MiroFish 输出严禁进 `RiskCheckSummary`**:输出仅写入 `evidence_collection`,通过 `evidence_ids` by-reference 关联(继承 P0-1 §2 红线 8 / P0-7 §2 红线 11);任何把 MiroFish 输出字段映射到 RiskCheckSummary.passed / threshold / actual 的代码即红线违规。

7. **LLM 严禁参与数据质量判定**:`backend/data/data_quality.py` / `backend/data/divergence.py` / `backend/data/staleness.py` / `backend/data/suspension.py` 严禁 `import backend.llm.*`(继承 P0-4 §2 红线 2 / P0-5 §2 红线 6 / P0-6 §2 红线 3 / P0-7 §2 红线 11)。所有阈值判定走纯 Python。

8. **MiroFish 内部 LLM 调用不触发数据质量判定路径**:MiroFish 仿真本身可调用 LLM(MiroFish 是项目亮点的核心),但 MiroFish 输出走 evidence,不走 RiskEngine / DataQualityState / RiskCheckSummary;LLM 隔离在数据质量判定层面与 MiroFish 推演层面**独立守门**。

9. **行情主备价差超 0.3% 即降级**:不允许"价差大但仍发指令"的乐观回退(继承 P0-7 §2 红线 13 fail-closed 精神);触发降级时 DataQualityState.quote_divergence_breach=true,InstructionPlanBuilder 早返降级 HOLD。

10. **全 watchlist 30s 快照采集是 P0 实施期必修**:audit §6.2 揭示的"只采三大指数"缺口必须在实施期填补,否则 P0-6 数据缺失率 ≤ 1% 硬门槛不可达;`watchlist_snapshot_job` 必须按 30s 频率运行盘中。

11. **停牌检测必经 `backend/data/suspension.py` 纯函数**:严禁在 `backend/risk/` / `backend/services/` 内重复实现停牌识别逻辑(单一真相源原则,继承 P0-7 §2 红线 15 板块/ST 识别经独立模块精神);严禁基于 LLM 推断停牌。

12. **5 源情报严禁强制要求全部存活**:`global_require_at_least_n_alive=1`(任一存活即继续);**禁止**把"5 源全断"或"任一域单源故障即冻结全市场"作为更严格的阈值(过严会让 P0-6 数据缺失率 ≤ 1% 硬门槛常态超阈,丧失实战意义)。

13. **`news_cctv` 日内多次拉取(09:00 / 15:30 / 20:00)+ 6h 阈值锁定**:新闻联播一日一期,真实更新仅在 19:30+,但 6h 阈值容忍 cron 抖动 + 跨日盘前数据未刷新场景;调整必须先走 amendment;严禁强行升级到分钟级阈值(违反时政域信号本质)。

14. **`evidence_ids` 前缀约定锁定**:`NEWS-` / `MIROFISH-` / `MARKET-` / `RISK-` / `DEBATE-` 五类前缀;新增前缀必须先走 amendment;严禁混用或自定义前缀绕过分类。

15. **`watchlist_market_snapshots` collection 索引锁定**:`(code, snapshot_at)` 唯一 + `(snapshot_at, -1)` 倒序 + `(code, snapshot_at, -1)`;严禁删除任一索引(影响 InstructionPlanBuilder 查询性能 + P0-6 数据覆盖度计算)。

16. **多源去重严格按 domain + 标题 + 时间窗 60s**:跨 domain 不去重(同一事件多域评价是 MiroFish 输入价值);严禁实施期"为减少存储成本而跨域去重"的乐观优化。

17. **`DataQualityState` / `WatchlistSnapshot` / `NewsArticle` / `StalenessReport` / `DivergenceReport` 是 frozen Pydantic v2 / @dataclass(frozen=True) 模型**:就地 mutation 红线违规(继承 P0-3 §2 红线 12 / P0-4 §2 红线 16 / P0-5 §2 红线 16 / P0-6 §2 红线 14 / P0-7 §2 红线 16 immutability 原则)。

18. **第一阶段排除项**:付费行情源(tushare pro / iFinD / wind)/ HTTPS 公网爬虫 / 微博 / 知乎 / 雪球 / 国防部 RSS / sina mil / 同花顺全球 / 百度衍生 / 卡片交互式资讯展示 — 全部留 P1 / amendment 范围;实施期任何引入即红线违规。

## 3. 影响范围(实施期统一执行)

### 3.1 新模块

- `backend/data/staleness.py` — `StalenessReport` + `evaluate_staleness()` 纯函数
- `backend/data/divergence.py` — `DivergenceReport` + `evaluate_divergence()` 纯函数
- `backend/data/suspension.py` — `is_suspended_from_spot_em()` 纯函数
- `backend/data/news_dedupe.py` — 多源去重纯函数
- `backend/data/data_quality.py` — `DataQualityState` + `DataQualityProvider`(汇总 staleness + divergence + outage 计算)
- `backend/mirofish/output_writer.py` — `write_simulation_evidence()` evidence 写入器
- `backend/data/news_multi_domain.py`(可选独立)— 多源 fetch 包装,合并 finance / politics / global 三种调度

### 3.2 修改模块

- `backend/data/scheduler.py` — 新增 `_run_watchlist_snapshot_job`(30s)+ 拆分 `_run_news_job` 为 `_run_news_finance_job` / `_run_news_politics_job` / `_run_news_global_job`
- `backend/data/news_crawler.py` — 新增 `fetch_cls / fetch_cctv / fetch_global_em / fetch_global_sina`;现有 `fetch_eastmoney` 保留;支持 domain 字段标记
- `backend/data/market_data.py` — `get_stock_realtime` 调用后追加 `evaluate_divergence()` + `evaluate_staleness()`,结果写入 `WatchlistSnapshot.quote_source / staleness_age_seconds / divergence_pct`(扩字段)
- `backend/agents/intelligence_officer.py` — 移除 "if mirofish_simulator is not None" 守门(P0-8 锁定必注入);辩论上下文加多域 evidence
- `backend/main.py` + `backend/api/analysis.py` — `AnalysisServices` 构造时**必须**注入 `MiroFishSimulator`(否则启动失败 fail-closed)
- `backend/services/instruction_plan_builder.py` — 早返链加第四步 `data_quality.is_acceptable` 检查(P0-7 §1.5 已规划基础上扩展)
- `backend/models/market.py` — `WatchlistSnapshot` 模型新增 + `NewsArticle` 模型扩 `domain` / `source` 字段
- `config/data_sources.yaml` — 整体重写(行情阈值 + 多域 5 源 + 数据质量阈值)
- `config/mirofish.yaml` — 扩 `trigger_paths` + `extractors` 显式 enabled

### 3.3 新 collection

- `watchlist_market_snapshots` — 全 watchlist 个股 30s 快照;索引 `(code, snapshot_at)` 唯一 + `(snapshot_at, -1)` + `(code, snapshot_at, -1)`
- `news_articles` — 现有 collection 扩字段 `domain` + `source` + `importance_score`;索引 `(domain, published_at, -1)` + `(article_id)` 唯一
- `evidence_collection` — 已有(P0-3 by-reference 目标);本决策约定前缀分类
- `data_quality_logs` — 早返触发 ledger(可选;每次 DataQualityState.is_acceptable=False 写入一条;包含 evaluated_at / degradation_reason / triggering_instruction_plan_id)

### 3.4 新 API

- `GET /api/data-quality/current` — 当前 DataQualityState 实时状态(只读;前端 DataQualityCenter 用)
- `GET /api/data-quality/history?from=...&to=...` — 早返触发历史(`data_quality_logs` collection 查询)
- `GET /api/news/by-domain?domain=politics&limit=50` — 按域过滤的资讯列表
- `GET /api/mirofish/simulations?from=...&to=...` — 已有 simulations 列表 endpoint 沿用(audit §10.2 揭示已存在);加 `trigger_path` 过滤参数
- 不新增 `POST /api/data-quality/*`:DataQualityState 是计算结果,不接受用户写入

### 3.5 新前端视图

- `frontend/src/views/DataQualityCenter.vue`(新建)— 实时面板:行情 staleness / divergence / 全 watchlist 快照覆盖率 / 多域 5 源存活状态 / 早返触发历史折线
- `frontend/src/views/MultiDomainNews.vue`(新建)— 多域资讯流:按 domain 分栏 finance / politics / global;每条标 source + importance_score;支持点开看 MiroFish 关联仿真
- `frontend/src/views/MiroFishCenter.vue`(audit §11 已规划升级)— 双路径触发记录 + 仿真结果展示 + evidence 链接

### 3.6 派生 amendment

`docs/decisions/P0-3-amendment-2026-05-09-multi-domain-news-source.md`(实施期同步产出):

- §1.5.2 `DataSnapshot.news_source: str` → `news_sources_by_domain: dict[str, tuple[str, ...]]`
- `news_window_seconds: int | None` 含义改为"任一域最长资讯窗口"
- `evidence_ids` 字段语义补充五前缀约定
- 在 P0-3 决策文档顶部加 `> 已被 amendment-2026-05-09-multi-domain-news-source 修订` 提示

`docs/decisions/P2-1-superseded-by-P0-8.md`(实施期同步产出):

- 原 P2-1 MiroFish 使用范围(日常每只股票 / 仅重大事件触发 / 仅盘后复盘 / 仅研究展示)由 P0-8 §1.4 双路径接线决策**取代**
- 显式记录"P2-1 不再独立决策,纳入 P0-8 §1.4"
- README 命名约定要求 superseded 文档保留(只新增不删除)

### 3.7 测试覆盖

- `backend/data/staleness.py` ≥ 95% — 边界 case:age==0 / age==threshold / age>threshold / 时区差
- `backend/data/divergence.py` ≥ 95% — 边界 case:fallback=None / primary=0 / primary=fallback / |diff|=threshold
- `backend/data/suspension.py` ≥ 95% — 6 类停牌特征
- `backend/data/data_quality.py` ≥ 90% — 7 种 breach 单独触发 + 多种 breach 同时触发
- `backend/data/news_dedupe.py` ≥ 90% — 同 domain 跨 domain 多源场景
- `backend/services/instruction_plan_builder.py` ≥ 90%(继续沿用 P0-7 基线)— 新增第四步早返 case
- `backend/agents/intelligence_officer.py` ≥ 80% — MiroFish 注入后辩论上下文测试
- 端到端测试 `tests/e2e/test_data_quality_p0_8.py` — 覆盖"行情陈旧 → 早返 HOLD" / "财经主源断流 30min → 早返" / "时政断流 6h → 早返" / "全球双断 60min → 早返" / "停牌单股 → 早返"

### 3.8 静态扫描 lint rule

- 阻止 `backend/data/data_quality.py` / `backend/data/staleness.py` / `backend/data/divergence.py` / `backend/data/suspension.py` 出现 `import backend.llm` / `from backend.llm`
- 阻止 `backend/main.py` / `backend/api/analysis.py` 构造 `AnalysisServices` 时 `mirofish_simulator=None`(改为编译期断言或 startup-time fail-closed)
- 阻止 `staleness_threshold_seconds` / `divergence_threshold_pct` / `finance_primary_outage_minutes` / `politics_outage_hours` 等阈值常量被赋值改写
- 阻止 `frontend/` 出现 `domain_military` / `domain_social` 字符串(P0-8 第一阶段排除项守门)
- 阻止任何 `RiskCheckSummary` 字段从 MiroFish 输出映射(grep MiroFish + RiskCheckSummary 同行 / 同函数)

## 4. 决策依据

### 4.1 为什么行情主备选「严格 0.3% / 5s」

- **0.3% 价差**对应 A 股一手交易常规波动的下限(短期 ±5% 涨跌幅内的 6%);超过 0.3% 通常意味着主备一方延迟到分钟级,继续发指令风险高
- **5s 延迟**与 30s refresh 间隔保持 17% 容忍度;主源持续延迟 5s+ 通常预示 API 限流或网络抖动,降级而非赌
- **0.3% / 5s 联合监控**比单一阈值更鲁棒;实测三大指数 + watchlist 个股 99%+ 时间在阈值内
- 不引入付费源(tushare pro / iFinD)避免成本与凭证管理(继承 P0-2 §2 红线 5 凭证仅 shell env)

### 4.2 为什么全 watchlist 30s 快照(而非 60s 或触发式)

- audit §6.2 明确"后台每 30 秒只采三大指数,不采全 watchlist 个股"是 P0 缺口;不补则 P0-7 check 12 涨跌停判定 current_price 数据源缺失
- 60s 频率会让 P0-6 数据缺失率 ≤ 1% 硬门槛风险增大(每分钟 1 条记录,网络抖动一次即破阈值)
- 触发式拉取在 InstructionPlanBuilder 调用时增加串行延迟,且 equity_curve mark-to-market 缺乏背景数据
- 30s 频率与 `akshare.stock_zh_a_spot_em` 单次返回全市场快照 well-fitted(API 不需多次调用,降低限流风险)

### 4.3 为什么多域选 5 源(而非 2 源极简或 7 源全)

- **2 源(stock_news_em + news_cctv)极简**:虽降低集成成本但 MiroFish 多域因果链推演输入过窄,违反项目亮点定位(全域信息推演 → 高可信度股市走向)
- **7 源(加 ths + 百度衍生)全纳入**:`stock_info_global_ths`(同花顺)接口在 akshare 历史 fix 频率显著高于其他源(audit 检查 akshare changelog 揭示不稳定);`futures_news_baidu` 衍生品资讯与 A 股 watchlist 关联弱;集成 ROI 偏低
- **5 源(财经 2 + 时政 1 + 全球 2)中性平衡**:每域至少 1 个高可信度源 + 财经/全球域双备(单点故障不冻结全市场);news_cctv 时政域官方权威性最高(继承用户澄清"政治/政策核心")
- 微博 / 知乎 / 雪球 / 国防部留 P1 因爬虫合规风险与稳定性需要专项评估;不在 P0-8 第一阶段冒进

### 4.4 为什么 MiroFish 双路径(而非单事件驱动或包含日度 watchlist)

- **单事件驱动**:仅在 HIGH/EXTREME 事件后跑;漏掉中等事件长期累积影响 + 盘后复盘缺失 → MiroFish 价值打折扣
- **包含日度 watchlist 重点扫描**:每日 5-10 只 + 事件 boost = LLM 调用量上升 50-100% → 成本与延迟双升;实战 ROI 不及双路径
- **双路径(事件 + 盘后)**:实时响应 + 全 watchlist 收盘后系统性复盘;LLM 调用控制在每日 70 次以内(20 + 50);对齐 P0-1 §1.6 多 Agent 辩论 + P0-6 §1.2.1 LLM 超时率 ≤ 5% 容忍度
- runs_per_day_cap 硬限避免 LLM 成本失控(尤其 Kimi thinking 单次 0.1-0.3 元);超 cap 跳过并 log,不阻塞买卖路径

### 4.5 为什么 DataQualityState 早返(而非进 RiskEngine 第 15 个 check)

- **进 RiskEngine 第 15 个 check**:需再走 P0-3 amendment(7→14 之后再 14→15;risk_summary 长度恒变);对实施期改动面大且让 RiskEngine 与数据质量判定职责混淆
- **早返(builder 层)**:与 P0-7 切换冻结 / ticket 冻结 / 熔断冻结同层设计,语义统一(都是"InstructionPlan 路径冻结的来源");14-check 保持纯账户/订单层面;risk_summary 长度恒 14 不变
- 早返触发 HOLD 不计入 P0-6 §1.2.1 指令完整率分母,避免数据质量违规反向冲击指令完整率指标(若进 RiskEngine 会变 REJECTED 计入分子,过度敏感)
- 早返实现简单且与现有 P0-7 builder 早返链一致(从 3 步变 4 步)

### 4.6 为什么不暂停 simulation_auto

- P0-1 §1.1 明确 "always-on simulation_auto 底座"是项目架构精神
- 暂停 simulation_auto 会让 equity_curve 出现空白(P0-6 §1.2.3 mark-to-market 必须每日记录),违反 P0-6 §2 红线 6
- 数据质量违规期间多 Agent 辩论仍可运行(辩论本就基于已有 evidence,不必依赖最新分钟级数据);只是 InstructionPlan 不发买卖,改 HOLD 留痕
- 用户在 Q4-下显式选择"Builder 早返降级 HOLD / 不暂停 simulation_auto" — 完全对齐这一原则

### 4.7 为什么时政域 6h 阈值(而非 30min 或 24h)

- 新闻联播一日一期(19:30 直播 + 文字稿 20:30 之后),日内拉取窗口稀疏
- 30min 阈值会让"盘前 09:00 拉取(还没新一期)+ 15:30 盘后拉取间隔 6.5h"误触发降级;阈值过严
- 24h 阈值无法识别"连续两天 news_cctv 接口故障"(罕见但严重);阈值过松
- 6h 阈值与 cron 抖动容忍 + 跨日盘前两次拉取间隔(20:00 → 09:00 = 13h,但 fetched_at 仍在 6h 内)契合 — fetched_at 是"最近一次成功拉取"而非"最近一期内容时间";6h 是 fetched_at 阈值

### 4.8 为什么 MiroFish 输出不进 RiskCheckSummary

- 继承 P0-1 §2 红线 8(LLM 不决定股数/价格/风控边界)与 P0-7 §2 红线 11(LLM 严禁产出 RiskCheckSummary 结果)
- MiroFish 内部使用 LLM 推演,但其输出本质是"叙事性场景演化"(隐性变量 / 拐点 / 极端场景),不可直接量化为风控边界
- 让 MiroFish 输出走 evidence,InstructionPlanBuilder 多 Agent 辩论环节读取 evidence 间接影响信号生成,**仓位计算 + 风控边界仍由确定性代码 + 14-check 完成**
- 这种 indirection 让 MiroFish 价值在"决策叙事 + 复盘"层面发挥,而不是冒险绕开硬编码风控

### 4.9 为什么 MiroFish 接线由 P2-1 前置到 P0-8

- 用户澄清明确"MiroFish 是项目亮点的核心",不接线则项目核心定位无法落地
- audit §10.2 揭示当前默认运行时不触发 MiroFish — 这是 P0 级缺口,与"决策对齐期完成后基于决策结果重写 CLAUDE.md + 生成新执行计划"的节奏冲突(若延后到 P2 则实施期前 MiroFish 仍未接)
- P0-8 多域资讯接入是 MiroFish 的输入前提,顺势把 MiroFish 接线锁在同一决策内合理

## 5. 后续动作

实施期开工前(全部 P0 锁定后)的 checklist:

### 5.1 数据采集与质量(高优先级)

- [ ] `config/data_sources.yaml` 整体重写(行情三阈值 + 多域 5 源 + 数据质量四阈值)
- [ ] `backend/data/scheduler.py` 新增 `_run_watchlist_snapshot_job`(30s)+ 拆分 news job 为 finance/politics/global
- [ ] `backend/data/news_crawler.py` 新增 `fetch_cls / fetch_cctv / fetch_global_em / fetch_global_sina`
- [ ] 新增 `backend/data/staleness.py` / `backend/data/divergence.py` / `backend/data/suspension.py` / `backend/data/news_dedupe.py` / `backend/data/data_quality.py`
- [ ] `backend/models/market.py` 新增 `WatchlistSnapshot` + 扩 `NewsArticle.domain / source / importance_score`

### 5.2 MiroFish 接线(高优先级)

- [ ] `backend/main.py` + `backend/api/analysis.py` `AnalysisServices` 构造时**必须**注入 `MiroFishSimulator`(startup fail-closed)
- [ ] `backend/agents/intelligence_officer.py` 移除 mirofish_simulator None 守门;辩论上下文加多域 evidence
- [ ] `config/mirofish.yaml` 扩 `trigger_paths`(realtime_event + postclose_review)+ `extractors` 显式 enabled
- [ ] `backend/mirofish/output_writer.py` 实现 evidence 写入 + `MIROFISH-{run_id}` 前缀
- [ ] `backend/mirofish/event_filter.py`(已存在)实施期对接多域 NewsArticle.domain 字段

### 5.3 InstructionPlanBuilder 早返扩展(中优先级)

- [ ] `backend/services/instruction_plan_builder.py` 早返链加第四步 `data_quality.is_acceptable` 检查
- [ ] 装配阶段调用 `DataQualityProvider.evaluate(stock_code=..., now=...)` 并把结果传入早返判断
- [ ] HOLD InstructionPlan rejection_reason 字段填 `data_quality:{degradation_reason}`(详见 §1.5.1)

### 5.4 派生 amendment(高优先级,与本决策同期产出)

- [ ] 新建 `docs/decisions/P0-3-amendment-2026-05-09-multi-domain-news-source.md`
- [ ] amendment 内容:`news_source: str` → `news_sources_by_domain: dict[str, tuple[str, ...]]`;`evidence_ids` 五前缀约定
- [ ] 在 P0-3 决策文档顶部加 `> 已被 amendment-2026-05-09-multi-domain-news-source 修订` 提示
- [ ] 新建 `docs/decisions/P2-1-superseded-by-P0-8.md`(原 P2-1 MiroFish 使用范围决策被 P0-8 §1.4 取代)

### 5.5 lint 与前端(中优先级)

- [ ] `scripts/lint_data_quality_immutability.py` 新建 + ci 集成(继承 P0-7 lint_risk_immutability 模式)
- [ ] 新增 `frontend/src/views/DataQualityCenter.vue`(实时质量面板)
- [ ] 新增 `frontend/src/views/MultiDomainNews.vue`(多域资讯流)
- [ ] 升级 `frontend/src/views/MiroFishCenter.vue`(双路径触发 + evidence)

### 5.6 测试覆盖

- [ ] 新增模块单测目标 ≥ 90% / ≥ 95%(详见 §3.7)
- [ ] 端到端测试 `tests/e2e/test_data_quality_p0_8.py` 覆盖 5 种 breach 场景

### 5.7 文档同步(本次锁定时即可完成)

- [x] 写入 `docs/decisions/P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md`(本文)
- [ ] 同步 `CLAUDE.md` §1.3 进度 + §2.1 P0-8 行 + §2.3 P2-1 标记为 superseded + §3.1 数据质量红线 + §3.4 操作速查
- [ ] 同步 `MEMORY.md` 索引(项目记忆 + 自记忆文件)
- [ ] commit 后写下 P0-9 handoff prompt

---

> 本决策一旦定稿不就地修改;阈值松紧调整、源切换、MiroFish 触发路径变更必须新建 `P0-8-amendment-{date}-{原因}.md` 并在本文顶部加 `> 已被 amendment-XXX 修订` 提示。
