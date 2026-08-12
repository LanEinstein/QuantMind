# P0-6 修订 — 2026-06-04 cond9 金丝雀探针改 vendor 可达性专用口径(盘前可启动,机制向意图回归)

> **修订基准**: [P0-6-amendment-2026-05-29-pilot-live-probe-wiring](./P0-6-amendment-2026-05-29-pilot-live-probe-wiring.md)(cond9 infra 可达口径的原始决策)
> **关联**: P0-8-amendment-2026-05-28(dual fallback 腿 eastmoney→Tushare-sina,引入严格 PRICE 校验)/ [P0-8-amendment-2026-06-03-watchlist-tushare-sina-primary](./P0-8-amendment-2026-06-03-watchlist-tushare-sina-primary.md)(确认 sina 盘前 PRICE=0 行为)
> **修订日期**: 2026-06-04(#68 ops session;盘前重启被 cond9 fail-closed 拒启暴露)
> **决策人**: owner(本 session 指示「这种问题得本质性解决」)+ 数据诚实实测(独立探针脚本复现:3 金丝雀两腿全 None,sina 腿 vendor 实际有响应)
> **性质**: **机制修复,意图不变**。05-29 amendment §1 同时规定了意图(「时段性伪阶不阻断启动——可盘前启动」)与机制(「复用 `get_stock_realtime_dual`」);2026-06-04 实测证明两者在盘前互相矛盾。本 amendment 把 cond9 探测机制改为与既定意图一致的专用方法,**cond9 语义不变**(供应商全宕 → 拒启)。

## 0. 触发与根因

2026-06-04 09:08 盘前重启(部署 `ff78877` MTM 修复 + `7445f5d` D1-b)被 gate 拒启:

```
Refusing to start: ... (unmet: cond9:data_quality_blocking_breach)
cond9_canaries_unreachable codes=['510300', '510500', '159949']
```

独立探针复现(09:14,盘前):

- **adata 腿**:盘前 `list_market_current` 返回**空 frame** → leg None。
- **sina 腿**:vendor **正常响应**(行含 `TS_CODE`/`NAME`/`PRE_CLOSE`),但盘前无成交 `PRICE=0` → `_tushare_sina_row_to_quote` 交易路径严格校验 fail-closed `ValueError` → leg None(`dual_tushare_sina_failed: no finite positive PRICE (halted / pre-open / parse failure)`)。
- 两腿 None 在 05-29 机制下唯一解读 = `quote_unavailable`(供应商全宕)→ cond9 未达成 → 拒启。

**本质矛盾**:「vendor 活着但还没开盘」与「vendor 全宕」在 `get_stock_realtime_dual` 复用机制下不可区分——交易路径对 PRICE=0 fail-closed 是**正确**的(P0-8:无效价永不进 MTM/divergence/决策),但启动门需要的是**基础设施可达性**语义。05-29 amendment §1.1 明文:「staleness / divergence / freshness / 停牌 等时间点伪阶(盘前实时报价必然 stale)不阻断启动——可盘前启动,不被 09:30 前没有 fresh 报价卡死」;该意图被机制实现击穿。

**为何 05-29 至今未暴露**:dual fallback 腿 05-28 已换 Tushare-sina(严格校验),但 05-29 之后所有真启(06-01 go-live / 06-03 10:17)都发生在**盘中**;2026-06-04 09:08 是换腿后第一次盘前启动。

## 1. 决策

### 1.1 新增探针专用方法(vendor 可达性口径,booleans-only)

- `MarketDataService.probe_quote_vendor_reachability(code) -> tuple[bool, bool]`:每腿「可达」 := 该腿 fetch 为该 code 返回**非空 frame**(**不要求 PRICE 有效**——盘前 PRICE=0 行 = vendor 在为该 code 服务数据);腿抛异常 / 返回空 frame / None → 该腿 False(传输层 fail-closed 不变)。
- 实现委托纯函数 `backend/data/vendor_reachability.py::probe_dual_vendor_reachability(code, primary_fetch, fallback_fetch)`(fetcher 注入,便于测试;`market_data.py` 已 824 行超 800 上限,新逻辑独立小模块)。两腿沿用 `asyncio.gather(asyncio.to_thread(...))` 并发结构。

### 1.2 `pilot_data_probe` 改用新方法

- Protocol `_DualQuoteSource` → `_VendorReachabilityProbe`(structural typing 不变,services 层仍零 `backend.data` import)。
- `_code_reachable` = `primary_ok or fallback_ok`;探针调用异常 → False(不变)。
- `canary_quotes_reachable(market_data, codes)` 签名/聚合语义**不变**:任一 code 两腿全 False → cond9 未达成;`market_data=None` / 空 codes → False。`main.py::_data_quality_clear` 接线零改动。

### 1.3 交易路径零改动(关键红线)

- `get_stock_realtime_dual` / `_tushare_sina_row_to_quote` / `_rows_to_quotes` **一字不动**:PRICE=0 仍永不进 MTM / divergence / 决策 / Line-2 监控路径。
- 新方法**只返回 bool**,只被启动门消费——宽松校验下**不存在报价对象逃逸**的通道(by-construction)。

## 2. 红线影响(全保持)

- **fail-closed 保持**:market_data 未接线 → False;探针异常 → False;两腿真宕(异常/空响应)→ 拒启不变。本修复只把「盘前有行无价」从误判全宕中分离出来。
- 不放宽 staleness ≤5s / divergence ≤0.3% / freshness ≥60s(cond9 本就不门控它们,builder/RiskEngine 路径不变)。
- 零 LLM 参与;纯确定性逻辑(P0-6 验收红线)。
- cond9 在 PILOT 11 条件集中的地位不变;FULL gate(45 交易日)不动;`FEISHU_INTERACTIVE_ENABLED` 仍只选 tier 永不 bypass。

## 3. 测试锚点

- **回归钉死 2026-06-04 场景**:adata 空 frame + sina PRICE=0 行 → `(False, True)` → code 可达 → gate 放行(盘前可启动恢复)。
- vendor 全宕:双腿异常 → `(False, False)` → 拒启;双腿空 frame → 同上。
- 单腿存活各向(adata 行+sina 异常 / 反向)。
- `pilot_data_probe` 全套语义测试迁移到新 Protocol;`main._build_pilot_probe` wiring 测试同步。
