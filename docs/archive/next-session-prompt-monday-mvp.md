# 交接 Prompt — 周一(2026-06-01)MVP 可启动可跑通:剩余障碍清扫

> 你在 QuantMind(`/home/ps/papers/QuantMind`)接手。SSoT = `docs/plan.html`;红线 = `CLAUDE.md §1/§2/§2.0`;
> memory 必读:`project_u_d6_gate_live_probes_2026_05_29` / `feedback_codex_rate_limit_fallback` / `feedback_push_main_gated` / `feedback_report_in_chinese` / `feedback_codex_exec_stdin_deadlock`。
> **总目标(owner 2026-05-29 锁定)**:周一开盘前 backend 能一次性干净启动、09:35 cron 真跑出 Line-1 BUY 真发飞书全链路。**扫清一切障碍,每个环节严格测试**。owner 已对 C3/outbox 选「满配:周一前必做」。

## 0. 当前精确状态(本 session #51 已完成,均**已本地 commit、未 push**)

| commit | 内容 |
|---|---|
| `defd551` | U-D6 — PILOT gate 三 live-probe 真实接通(cond9 ETF 可达 / cond10a live timeout 计数 / cond10b `state.status` 修)。amendment `P0-6-amendment-2026-05-29` |
| `c62003f` | U-D6b — broker recovery `_rehydrate_event_doc` 复活 StrEnum event_type(真 Mongo/BSON 把 StrEnum 降级 str,strict 拒;FakeCollection 掩盖) |
| `36df727` | docs SSoT #51(U-D6/U-D6b 进 plan.html + U-D3/U-D4 背离更正 note) |
| `4ac0585` | U-D6c — 生产 T-1 EOD frame 接通(C1 `_ensure_daily_frame` 懒组装+锁+fail-open+health_critical 告警 / C2 `t_minus_1_eod_utc` 提共享 util)。amendment `P0-8-amendment-2026-05-29-production-frame-wiring` |

**已验证**:interactive backend 真启**过 11/11 PILOT gate**(2 次冷启复现;audit `owner_prod_authorization_granted`+`brokerscheduler_started` 8 cron 含 line1_runner;recovery 干净回放累积 mode_switch_reset)。**backend 现已停**(owner 周一重启)。mongo 容器 `quantmind-mongodb-1` 已 `--replSet rs0`+`rs.initiate()`(数据保留,跨容器重启存活)。go-live env 在 `~/.bashrc`。
**⚠️ plan.html 尚未记 U-D6c**(commit 在但 SSoT task/SESSION_LOG 未补)——见任务 0。

## 1. 本 session 三路扫描的关键发现(已修 / 待办)

- **C1(已修 U-D6c)**:`app.state.line2_daily_frame` 此前**从未赋值**→ 09:35 cron 静默 no-op 零 BUY。最深一层 U-D3/U-D4 背离(只交付 dry-run,生产 frame 源没接)。
- **C2(已修 U-D6c)**:生产 assembler 墙钟 `fetch_time_utc` 撞 `created_at` → `snapshot_at<created_at` 倒挂(U-D4b dry-run 同款,生产没修)→ 现锚 T-1 15:00。
- **C3(待办 task #12)**:生产 builder 端 `data_quality_provider` **从未挂 app.state** → Line-1/Line-2 交易时 per-code 数据质量门走 `clean_data_quality()` 兜底**空转**。cond9(pilot_data_probe)不依赖它。
- **outbox(待办 task #13)**:`main.py:464` 是 `InMemoryOutboxRepository` → 派发幂等不跨重启。
- **S2(follow-up)**:`build_line1_run_state` 的 `today_instruction_count` 装配为 0 → ≤5单/日 cap 同日重启漏计。可与 C3 同批收。
- **strict-Mongo 普查结论**:仅 broker_events 是活雷(已修);其余 strict 模型读都带 `strict=False`(安全,但靠 call-site flag,可加 lint/回归守)。

## 2. 剩余任务(每个:amendment 若动边界 → TDD(非 risk>70%/risk>95%)→ 本地全绿 → `/code-review high` → 修 P0/P1/P2 → feature commit → plan.html done+hash)

### 任务 0 — 补 U-D6c 进 SSoT(docs-only,先做)
`docs/plan.html`:U-D6b task 块后加 U-D6c(done,commit `4ac0585`);更新 SESSION_LOG #51 actions/next(现 entry 写于发现 C1 前、措辞偏乐观,补「cron 曾是 no-op + C1/C2 修复 + C3/outbox/e2e 仍 todo」)。

### 任务 1 — C3:完整 DataQualityProvider + 4 具体探针(大)
- 现状:`backend/data/data_quality.py` 中 4 个仅 **Protocol** 无实现:`PrimaryBackupQuoteProbe`(~:89)/`WatchlistSnapshotAgeProbe`(~:111)/`NewsAvailabilityProbe`(~:125)/`MiroFishHealthProbe`(~:137);`DataQualityProvider`(~:274)`evaluate(stock_code, now)→DataQualityState`(~:150,7 breach+3 计数;阈值 staleness 5s / divergence 0.3% / freshness 60s)。
- 做:实现 4 具体探针(数据层:`MarketDataService.get_stock_realtime_dual` 主备→staleness/divergence/quote_unavailable;`get_watchlist_snapshot` 快照龄;5 域新闻可用性;MiroFish 健康)+ lifespan 构造 `DataQualityProvider` 挂 `application.state.data_quality_provider` + 传入 `build_line1/line2_code_contexts(data_quality_provider=...)`(`backend/main.py` 现传 `getattr(...,None)`)。
- 消费侧:`line1_context_provider.py`(~:298-300 `data_quality or clean_data_quality()`)、`line2_context_providers.py`(~:513-522 None→clean)接真 provider 后 per-code 门生效。
- amendment:解除 `P0-6-amendment-2026-05-29 §4` follow-up。**红线**:provider 严禁 import backend.{llm,agents};DataQualityState schema 锁 7+3(P1-2.B §2 红线 10);staleness/divergence/freshness 阈值不放宽。
- TDD:每探针真值/异常 fail-closed/单源降级;provider 聚合;builder 早返在真 breach 时降 HOLD。

### 任务 2 — outbox:Mongo 持久 OutboxRepository(中)
- 现状:`backend/orchestration/instruction_dispatcher.py` `InMemoryOutboxRepository` + `OutboxEntry`(frozen dataclass ~:71,claim/release at-most-once);`main.py:464` 用内存版。
- 做:Mongo-backed(跨重启幂等;append-only 继承 P1-2.A 8 红线;镜像 `BrokerEventStore` 模式)替换 main.py:464。
- **TDD 必含真-Mongo round-trip 测试**(防 U-D6b 同类雷:确认序列化/反序列化对齐;若改 Pydantic strict + StrEnum/UUID 字段,验证裸 str/BSON 读回不崩)。

### 任务 3 — U-D5:离线生产路径 e2e(task #9)
0-network/0-LLM/fake adapter 串:lifespan wiring → `_ensure_daily_frame`(fake assembler)→ `_line1_daily_callback` → RouteCoordinator dispatch(fake feishu)→ 入站 reply → InboundGate → parser → ExecutionReportApplier → MockBroker 镜像 → reconcile。**逐环节断言**(每环节非 no-op、M-004 单一构造点不破、镜像前后持仓/现金正确)。落 U-D5 done;`scripts/redline-check.sh` 可加 orchestration 隔离断言。

### 任务 4 — 收官
真启再验证(停占端口进程 → go-live env 启 → audit 确认 11/11 gate + 8 cron);plan.html SESSION_LOG #52 + 修订记录;memory 更新。**push origin main 待 owner 授权**。

## 3. 操作速查 + 红线提醒

```bash
# 启 backend(go-live env 在 ~/.bashrc;lifespan ~10s)
/home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/backend-startup.log 2>&1 &
# 过 gate 标志(权威看 audit,非 uvicorn stdout):
grep -E "owner_prod_authorization_granted|brokerscheduler_started" logs/audit.jsonl | tail
# 本地门禁(测试用 clean env 避免 go-live env 让 3 个 orchestration test env-induced fail):
env -u FEISHU_INTERACTIVE_ENABLED -u QUANTMIND_PROD_RUN -u QUANTMIND_OWNER_PROD_AUTHORIZATION -u QUANTMIND_FEISHU_TIER -u FEISHU_DECISION_CHAT_ID \
  /home/ps/anaconda3/envs/zhanglan/bin/pytest -q -p no:cacheprovider     # 基线 4086 passed / 13 skipped
bash scripts/redline-check.sh
```
- **真 Mongo str-downgrade 教训**:测试 FakeCollection 保留 Python 对象,真 Mongo 把 StrEnum 降级 str → 任何新 Mongo strict 模型必加「裸 str 读回」测试。
- **codex 撞额度至 ~5-31** → 代码任务前置审查回退 `/code-review high`(Skill code-review args=high,owner 既定)。
- **向 owner 报告中文**,thinking 英文,代码/commit 英文。
- **永禁真实券商下单**;真发飞书必前置 owner 确认;告警群 `oc_9edda`≠决策群 `oc_77e23`;LLM 不碰决策/回报/对账/验收/数据质量;RiskEngine 纯 14-check;单一构造点 M-004;config runtime 不可改+hot-reload 全禁;全层 127.0.0.1;fail-closed for data corruption / fail-open for infra glitch。
- **push origin main 受 auto-mode gate**,owner 授权才推。

## 4. 周一 owner 自助启动(若你不在场代跑)
owner 开盘前(09:35 前)`uvicorn ... &` → recovery 越过 mode_switch_reset(U-D6b)→ 过 11/11 gate → 09:35 `line1_runner` 自动拉 T-1 frame(U-D6c)→ 选股辩论 → 真发决策群 → owner 飞书审+v2 回填。**飞书 BUY 30min EXPIRED**,owner 须 09:35–10:05 在场。
