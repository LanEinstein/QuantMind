# U-E5 (A 部分) 代码审查 summary — 2026-05-27

> **任务**: U-E5 缺口2 端到端双线测的**无发送前置**(owner allowlist + 只读 list_feishu_chats + cond5/6/7/11 翻牌)。
> **审查方式**: codex CLI 仍在使用额度窗口(~2026-05-31 恢复)→ 按 owner 既定回退 **claude `/code-review high`**
> (记忆 `feedback_codex_rate_limit_fallback`)。7 维度并行 finder(3 correctness + 3 cleanup + 1 altitude)+ verify。
> **审查范围**(`git diff HEAD` + 新增未跟踪文件):
> - `backend/integrations/feishu/inbound_gate.py`(新,纯函数)
> - `backend/main.py`(`_feishu_dispatch` 接 InboundGate + 启动期 fail-fast)
> - `scripts/list_feishu_chats.py`(新,只读)
> - `config/pilot_readiness.yaml`(cond5/6/7/11 false→true)
> - `tests/test_feishu_inbound_gate.py` / `tests/test_list_feishu_chats.py` / `tests/test_pilot_cond_evidence.py`(新)
> - `tests/test_pilot_readiness.py`(committed-manifest 测试更新)
> - `docs/decisions/P0-2-amendment-2026-05-27-owner-open-id-allowlist.md`(新 amendment)

## 结论

- **Correctness:两个 correctness 角度(line-by-line + removed-behavior;cross-file tracer + gate 影响)均 `[]`,无 bug。**
- 关键安全核查(cross-file tracer 重点)**通过**:翻 cond5/6/7/11 **不会**提前打开 PILOT go-live gate ——
  `PilotReadinessProbe.evaluate()` 聚合全部 11 条;cond3(`dry_run_double_line_pass`)+ cond4
  (`feishu_send_recv_smoke_pass`)仍 False → `_manifest_unmet()` 恒报 2 条 unmet → `can_switch_to_feishu_on(PILOT)`
  恒 `allowed=False`,与 5 条 live-probe(含 cond2 owner auth `QUANTMIND_PROD_RUN`/`OWNER_PROD_AUTHORIZATION`)无关。
- 全仓除 `test_pilot_readiness.py` + `test_pilot_cond_evidence.py`(均已同步更新)外,**无**其他测试加载真 committed manifest
  断言全 False / 特定 unmet-count;`can_switch_to_feishu_on` 的其他测试全用 stub probe / tmp_path manifest,绝缘。
- 新 `InboundGate.from_env` 的 `SystemExit` 在任何现有测试中**不可达**:lifespan interactive 块先经
  `feishu_client is not None` + acceptance gate `allowed`(现被 cond3/4 拒)守门;唯一 runtime `TestClient` 用例
  (`test_llm_routing_escalation.py`)build 全新 `FastAPI()` 不跑真 lifespan;lifespan 回归测试全是 AST 静态文本检查。
- audit 写法 schema 合法:`FEISHU_MESSAGE_RECEIVED` + `FEISHU_USER` + `BLOCKED` 均存在;非 evolution 类(SYSTEM-only
  actor 校验不触发);payload 仅 `compute_fingerprint` 值(plaintext-secret 校验不触发);`message.sender_id` 空已被
  `events.py::_extract_message` 在上游 `_SkipEventError` 丢弃,dispatch 永不见空值。

## Cleanup / altitude findings(4 条,均非 correctness)

| # | 文件 | 摘要 | 处置 |
|---|------|------|------|
| 1 | `backend/main.py` | `FEISHU_DECISION_CHAT_ID` 在 lifespan 块内被读+校验两次(既有 `decision_chat_env` 块 + 新 `InboundGate.from_env`) | **不改**。既有 `decision_chat_env` 块先于 gate 跑(它还做 alert≠decision 校验 + 日志前缀),非死代码;gate 作为可复用纯模块独立持有自己的 fail-closed 不变量是合理的防御层叠。非本次引入。 |
| 2 | `backend/main.py` | `DROP_WRONG_CHAT` 日志仍用 `decision_chat_env[:6]+'***'` 手搓指纹,与 11 行下新用的 `compute_fingerprint` 不一致 | **不改**。该 `[:6]` 切片是**既有原代码**(旧 `_feishu_dispatch` 即如此),非本次引入;改它=改既有日志格式,范围蔓延。 |
| 3 | `tests/test_pilot_readiness.py` + `tests/test_pilot_cond_evidence.py` | 两测试都断言完整 manifest 6-flag 状态;`test_pilot_cond_evidence` docstring 却称前者"只守 schema" → 自相矛盾(本次引入) | **已修**。`test_pilot_readiness` 改为只守 schema(key 集)+ owner-gated 两条(cond3/4)仍 False(防提前签收);完整 4-true/2-false ledger 锁单一归 `test_pilot_cond_evidence`。docstring 与代码现一致。 |
| 4 | `scripts/list_feishu_chats.py` | `_list_chats_real` 重建 lark client 链(与 `FeishuClient._build_acreate` 同款 log_level guard) | **不改**(agent 自评"可接受")。`FeishuClient` 故意只做 `send_message`,只读 ListChat 路径它不覆盖;脚本 docstring 已声明。 |

## 门禁(commit 前)

- `pytest -q --cov=backend --cov-fail-under=70` → **4009 passed / 13 skipped,覆盖率 90.58%**。
- `ruff check` + `ruff format --check` → ALL PASS(3 文件已 format)。
- `bash scripts/redline-check.sh` → **All redline checks passed**(含 InstructionPlan 单一构造点 M-004 / Line-2 隔离 /
  cost_guard 隔离 / 127.0.0.1 等;新模块 `inbound_gate.py` 纯函数零 `backend.{llm,agents,mirofish}` import)。
- 只读 `scripts/list_feishu_chats.py` 实跑核对:机器人在 2 群,`FEISHU_DECISION_CHAT_ID`→"QuantMind决策执行群" present、
  与告警群不同(`decision_is_alert=false`),verdict OK。**零发送**。

## 仍待 owner / 开盘(U-E5 未 done 部分)

- **cond3**(dry_run_double_line_pass):需开盘时段(09:35 后)重跑 `scripts/dry_run_double_line.py` 渲出真 BUY + 判据,
  owner 审阅后翻。本 session 22:05 已收盘,做不了。eastmoney 代理隐患:现复测直连+代理均 200(瞬时态),建议把
  eastmoney 域名加进 `no_proxy` 以免开盘走代理路由瞬断 → 全降级。
- **cond4 + (B) 真发**:owner 设 `FEISHU_INTERACTIVE_ENABLED=true` + `FEISHU_OWNER_OPEN_ID`(新增,owner open_id)
  + go-live gate 全过后,真发 1 条 BUY 到决策群 → owner 按 v2 模板回填 → WS(鉴权 + allowlist)→ parser → applier →
  镜像 → 16:00 对账。真发前必停下来向 owner 明示内容 + 目标群拿确认。

---

# U-E5 (B 前置) 代码审查 summary R2 — 2026-05-28

> **任务**: U-E5(B) cond3 dry-run unblocker — 实时 dual-source 备腿换 Tushare `realtime_quote(src=sina)` 替换被 eastmoney
> 限流的 akshare 全市场批量端点(`P0-8-amendment-2026-05-28-tushare-sina-realtime-backup`)。
> **触发**: 开盘后(10:00 周四,A 股连续竞价中)重跑 `scripts/dry_run_double_line.py` 全 5 候选 `line1_quote_degraded` → 0 BUY。
> 诊断:akshare 备腿 `stock_zh_a_spot_em()` 全市场批量(58 页)被 eastmoney 反爬 `RemoteDisconnected` **持续重置**
> (3/3 直测 + 5 候选 5/5;`no_proxy` 加 eastmoney 后仍如此 — 非代理瞬时,是 eastmoney 对重 clist 限流)。
> Tushare 自带 `ts.realtime_quote(src=sina)` 实测稳定供单标的 L1 + 五档,且 sina 源避开 eastmoney。owner 5-28 决策:实时备腿改 sina。
> **审查方式**: codex 仍撞额度 → 回退 **claude `/code-review high`** 7 维度并行 finder + verify。
> **审查范围**(`git diff HEAD` + 新建 amendment):
> - `backend/data/market_data.py`(新增 3 helper + 改 `get_stock_realtime_dual` 备腿)
> - `backend/services/line1_context_provider.py`(degrade-reason 字串 akshare → tushare-sina)
> - `tests/test_market_data.py`(3 个 dual 测试 + 新 sina fixture)
> - `tests/test_market_data_tushare_sina.py`(新)
> - `docs/decisions/P0-8-amendment-2026-05-28-tushare-sina-realtime-backup.md`(新)

## 结论

7 维度并行(3 correctness + 3 cleanup + 1 altitude)→ verify 后保留 10 finding:

| # | 严重 | 文件:行 | 摘要 | 处置 |
|---|------|---------|------|------|
| 1 | P0 correctness | market_data.py:339 | NaN PRICE/OHLC 透 `float(row.get(X,0) or 0)` 进 StockQuote(NaN 为真值,短路保留 NaN;`max(nan, 0.0)` IEEE-754 返 nan)| **已修**:`_positive_or_none` 复用做 PRICE 校验;NaN/inf/≤0 抛 `ValueError`;dual handler fail-closed missing-data。OHLC 同 helper(缺 → 0.0 informational)。同根因 codex U-E2 cycle1 P1 修过,本次不再踩雷。 |
| 2 | P0 correctness | market_data.py:124 | `_to_tushare_ts_code` 自表绕过 universe(688 STAR 错路 .SH、110/113 SH 可转债错路 .SZ、4/8/92 北交错合法化)| **已修**:defer 到 `backend.data.stock_metadata.classify_board` 单一真相源 → `ForbiddenCodeError` (STAR/BJ/CB/B-share) + `UnknownCodeError`(malformed/unknown)各按稳定 audit reason namespace。 |
| 3 | P0 correctness | market_data.py:142 | `ValueError` 被 `except Exception` 吞成 `dual_tushare_sina_failed`(假装 vendor 外断,误导 ops)| **已修**:`get_stock_realtime_dual` 新 `dual_fallback_input_error` 分键单独捕 `isinstance(..., ValueError)`,与 vendor 外断分清。 |
| 4 | P1 cleanup | market_data.py:20 | 模块顶 `import tushare as ts` 破坏文件 lazy-import 一致约定;pytest collect / uvicorn cold start 永远拉入整套 Tushare 栈(tqdm/lxml/bs4)| **已修**:函数内 `import tushare as ts`;tests 改 `patch("tushare.realtime_quote", ...)`(不再依赖模块级 `ts` 符号)。 |
| 5 | P1 correctness | line1_context_provider.py:111/532/549/555 | log key 改名 `dual_akshare_failed→dual_tushare_sina_failed` 已迁,但 4 处人类可见 degrade-reason 字串仍说 "backup leg (akshare) unavailable" / "vs akshare {fallback.price}"(operator 排查方向被误导;audit 搜 "akshare" 漏掉新结构化 key)| **已修**:4 处迁 "tushare-sina";`_DIVERGENCE_THRESHOLD_PCT` 注释附 amendment-pointer。 |
| 6 | P2 cleanup | market_data.py:343 | `timestamp` 主腿(adata=`now(UTC)` 抓取时刻)vs 备腿(sina DATE+TIME 交易所时钟)语义不同锚;per-leg staleness 比较会误判 | **已修**:备腿改 `datetime.now(tz=UTC)` 同主腿语义;DATE+TIME 解析删除(若未来要捕交易所时钟需新 StockQuote 字段 + 另一 amendment)。 |
| 7 | P2 cleanup | market_data.py:37 | `_ASIA_SHANGHAI = timezone(timedelta(hours=8))` 重复发明(已有 `backend/utils/trading_hours.SHANGHAI = ZoneInfo(...)` 等 3+ 处),且唯一用 fixed-offset 而非 ZoneInfo | **已修**:常量整体删除(由 #6 顺带消化;不再需要 Asia/Shanghai tz)。 |
| 8 | P2 efficiency | market_data.py:621 | dual 两腿串行(两次 `await asyncio.to_thread`),老的"怕 hammer eastmoney 批量"原因在 sina 单标的下不成立 | **已修**:`asyncio.gather(..., return_exceptions=True)`;两腿并发,wall-clock 减半,腾出 5s staleness 预算。 |
| 9 | P2 altitude | market_data.py:175 | tushare SDK `@require_permission` 每次调用先 HTTPS POST `api.tushare.pro/dataapi/sdk-event` 验 token 再访问 sina — 新外部依赖 + 100-300ms 永久开销 | **不修**(SDK 协议;缓解记 amendment §2.2)— TUSHARE_TOKEN 已启动期 fail-fast 校验;`dual_tushare_sina_failed` audit + alert 兜底监测此通道。 |
| 10 | P2 altitude | market_data.py:41 | `_BARE_CODE_RE = r"\\A\\d{6}\\Z"` 是项目第 7+ 份 `\\d{6}` 校验(已有 stock_metadata._validate_code_shape 等)| **已修**:常量整体删除(由 #2 顺带消化 — classify_board 内 _validate_code_shape 接管,fail-closed 升级到 UnknownCodeError 项目 audit reason)。 |

**Deferred(独立 amendment 收敛,不在 cond3 阻塞路径)**:
- `get_stock_realtime` 单源路径 + `_fetch_stock_list_akshare`(watchlist 多码备腿)+ `history_data.py:90` 都还在用 `stock_zh_a_spot_em` 全市场批量端点 —— 同款 eastmoney 限流隐患在,但不在 cond3 阻塞路径。amendment §5 标记 follow-up。

## 门禁(commit 前)

- `pytest -q --cov=backend --cov-fail-under=70` → **4048 passed / 13 skipped,覆盖率 90.58%**(净新增 +10 测试覆盖 forbidden/unknown/NaN/inf/zero 五种 fail-closed)。
- `ruff check` + `ruff format` → **ALL PASS**(2 source + 2 test 文件已 format)。
- `bash scripts/redline-check.sh` → **All redline checks passed**(含 M-004 单一构造点 / Line-2 隔离 / cost_guard 隔离 / 127.0.0.1 / K-006 PIT 隔离 / L-002 纯量化隔离;`backend/data/market_data.py` 新增 `import tushare as ts`(lazy)+ `from backend.data.stock_metadata import Board, classify_board` 在数据层 import-isolation 红线允许范围内 — `marketdata_snapshot/` 仍纯模块零 `backend.*` import,L-002 隔离不破)。

## 仍待 owner / 开盘 — 不变

- **cond3 翻牌**:本 commit 把 dry-run-blocker(akshare 备腿 vs eastmoney)修了;owner 接下重跑 `dry_run_double_line.py`
  应能渲出真 BUY + 判据,审完后翻 `dry_run_double_line_pass: false → true`。
- **cond4 + 真发** 仍 owner-gated,与本 commit 无关。
