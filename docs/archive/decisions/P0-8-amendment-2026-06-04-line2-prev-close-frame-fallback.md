# P0-8 Amendment — Line-2 prev_close:市场元数据缺失时回退至 pinned 日线 frame T-1 close(2026-06-04)

> Status: ACCEPTED(owner 2026-06-04 选「立刻再重启一次」补救后,实证补救无效 → 根因修复)
> Amends: P0-8(数据情报)/ U-D1(Line-2 生产接线);不触碰 RiskEngine(P0-7)本体。

## 1. 触发事故(2026-06-04 盘中,生产实证)

- 13:27 服务停机(host 端口让渡)→ 14:04 在 `205f8f6` 上重启(端口移至 8001)。
- 14:05:26 Line-2 盘中首个路由 tick:600011 / 605111 **ATR_TRAILING_STOP 保护性止损**触发
  (600011 现价 8.75 < 止损线 9.1886,当日 -4.2%;605111 现价 63.96 < 止损线 64.44)+ 605020 ADD。
- 三单全部被 RiskEngine `limit_up_down_block` 以 **`prev_close unavailable; cannot evaluate
  limit-up/down`** 拒绝(fail-closed,行为正确);runner 当日 dedup(REJECTED 亦记
  `fired_today`,防 30s 重发)随即吞掉当日重试 → **保护性止损建议全天不再发飞书**。
- 14:14 再次重启(清内存 dedup)+ Redis quote 已 warm(`quote:600011` 携 `prev_close=9.13`)
  → 14:16:57 **同样三拒**。补救无效,证明非 warm-up 竞态。

## 2. 根因(确定性 bug,非竞态)

`MongoBackedMarketMetaProvider.get_prev_close` 唯一数据源 = Mongo `kline_daily` 最新日线行。
**生产 `kline_daily` 集合自建库起 0 文档(无任何写入方)** → `get_prev_close` 恒 `None` →
`build_line2_code_contexts` 降级 `prev_close=None` → RiskEngine 对每一笔 Line-2 单恒拒。
即:**Line-2 盘中 + 日线两条 SELL/ADD 路由路径自上线起从未可能通过 limit 检查**
(与 #64 `market_realtime` tier-2 个股行缺口同族:provider 契约引用了无人喂数的集合)。
此前未暴露仅因生产从未真正触发过 SELL/ADD intent;2026-06-04 首批真实止损触发即全军覆没。

## 3. 决议(本 amendment 锁定)

Line-2(盘中 + 日线)`prev_close` 输入链改为三级,**每级语义不变、只补数据源**:

1. `market_meta.get_prev_close`(Mongo `kline_daily`;既有权威路径,有值即用,不变);
2. **新增回退:pinned 日线 frame 的该 code 最新 close**(`parse_held_series(frame)[code].closes[-1]`)。
   - 盘中 30s tick / 日线 09:35 cron / 轮动 09:35 cron 全部在交易日 T 运行,frame = PIT ≤T-1
     已收日线 → 最新 close = T-1 收盘价 = 当日涨跌停基准,语义精确;
   - frame 即触发派生所用同一 pinned 快照(manifest 已记 lineage)→ **PIT 可复现不破**;
   - **trade_date 钉死(review CONFIRMED finding)**:`_ensure_daily_frame` fail-open 会在当日
     组装失败时保留**昨日**缓存 frame(监控连续性,保留);但陈旧 close 会平移涨跌停带 →
     `build_line2_code_contexts` 新增 `expected_trade_date` 钉(main.py 三调用点传
     `prev_trading_day(now)`),不匹配则**仅禁用本级回退**(watchlist 信号照旧);
   - 解析异常 / code 缺行 / 非有限正数 → 本级回退失败,进入第 3 级;
   - 性能:frame(可为全市场 CSV)对全部 codes **一次解析**(review finding:逐 code 重复解析
     曾使 30s tick 从 ~13s 涨至 ~20s);watchlist 信号共用同一次解析。
3. 全缺 → `None`,RiskEngine 照旧 fail-closed 拒单(**安全语义不变**)。

**Provider 级第 2 战线(review CONFIRMED P1)**:`MongoBackedMarketMetaProvider.get_prev_close`
本体新增回退 = **新鲜 Redis `quote:{code}` blob 的 `prev_close` 字段**(同 `redis_freshness`
窗口;过期 blob 携带**前一交易日**的 prev_close、差一天 → 拒;非有限正数 → 拒)。
WHY:MockBroker **at-fill 涨跌停复核**(P1-2 §2.7 第二道防线)直接调 provider,kline_daily
空时复核被静默跳过(`prev_close is None → return None` no-op)→ 自上线起 at-fill 复核形同虚设。
此回退使所有 provider 调用方(含 at-fill 复核)在采集运行期间恢复 prev_close。盘中成交时
采集 30s 刷新 → blob 恒新鲜;窗口外(如盘后)→ None → 复核跳过回到现状(不更差)。

## 4. 不变量(红线全留)

- RiskEngine 纯函数零改动;`backend/risk/` import 隔离不变;14-check 不变。
- 零 LLM、零新 vendor、零新网络调用(frame 本就传入 `build_line2_code_contexts`)。
- fail-closed 保留:任何一级取不到 → None → 拒单(只是不再「永远取不到」)。
- 单一构造点 / 人工 gate / 飞书 display-only 全不变。

## 5. Follow-up(不在本 amendment 内)

- `kline_daily` 喂数管线(EOD 写入官方日线)→ 使第 1 级真正生效;独立任务。
  **⚠️ 硬前置约束(review CONFIRMED P2,PIT)**:当前 RiskEngine 实际评估的 prev_close
  值/来源未入持久化 record(`IntradayTriggerRecord.prev_close` 记的是 spot 报价的
  prev_close,非 context 喂给 RiskEngine 的值)。**今日不歧义**(kline_daily 恒空 →
  meta 级确定性 None → 回退值 = pinned frame,bit-exact 可复算);但**一旦开始喂
  kline_daily,旧/新决策的 prev_close 来源即不可消歧** → 喂数任务实施前**必须**先给
  trigger record 增加 `risk_prev_close` + `prev_close_source` 字段(triggers 版本 bump
  + config_hash;模式同 P0-7-amendment-2026-06-03 的 `effective_drawdown_threshold`)。
- runner「REJECTED 消耗当日 dedup」的设计在输入修复后语义恢复正确(真实风控拒绝才消耗),
  保留;若将来要区分「数据不可用拒」与「实质风控拒」再单独 amendment。
- 2026-06-04 当日已烧 dedup:修复部署重启即清(内存态),收盘前若仍 breach 会重新路由。
  (实证:14:26 部署重启后 14:27:14 tick 即 frame 回退 5/5 命中 → 3 条 SELL `dispatched`
  到飞书决策群:`QM-20260604-142714-{600011,605020,605111}-SELL-00{1,2,3}`。)
