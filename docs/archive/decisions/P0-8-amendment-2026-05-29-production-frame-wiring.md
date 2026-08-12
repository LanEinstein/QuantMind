# P0-8 修订 — 2026-05-29 生产 T-1 EOD frame 真实接通(U-D6c / C1+C2)

> **修订基准**: [P0-8 数据情报 — 行情主备 / 多域资讯 / MiroFish / DataQualityState](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
> **关联**: P0-8-amendment-2026-05-24-tushare-data-source(全市场扫描层=Tushare Pro SDK)/ U-D1b(Line1FrameAssembler)/ U-D4b(dry-run snapshot_at 倒挂修复)/ P0-6-amendment-2026-05-29(同 session gate live-probe)
> **修订日期**: 2026-05-29(周一真 BUY 前扫雷)
> **决策人**: owner(2026-05-29「扫清通往 Monday MVP 的一切障碍,每个环节严格测试」+ 拍板 C3/outbox 满配)

## 0. 触发(SSoT 背离的最深一层)

U-D6 gate 修通后真启验证时,三路只读扫描发现**生产 Line-1/Line-2 cron 是静默 no-op**:`application.state.line2_daily_frame` 在 3 处被读(`main.py::_line1_daily_callback` / `_line2_daily_callback` / `_line2_intraday_callback`)、**全项目从未赋值**;唯一组装 frame 的 `Line1FrameAssembler` 只在 `scripts/dry_run_realdata.py` 被调用,**生产 lifespan/cron 无等价步骤**。周一 09:35 cron 会 `frame is None → 记 line1_daily_skipped_no_frame → return` → 零选股零辩论零 BUY。

这是 U-D3/U-D4「标 done 但只交付 dry-run/冒烟脚本」背离的最深一层:dry-run harness 建了,**生产 frame 源从未接**。seam 注释明写「wired in U-D3」但 U-D3 实际未接。

## 1. C1 — 生产 frame 懒组装 + 缓存(`_ensure_daily_frame`)

新增模块级 `backend/main.py::_ensure_daily_frame(application, frame_lock, now)`,在三个 daily/intraday cron callback 头部调用:

- **as_of = `prev_trading_day(now Asia/Shanghai date)`**(T-1 交易日);`signal_id = LINE1-FRAME-{YYYYMMDD}`。
- 构造 `TushareClient()`(token 来自 env,官方 SDK only,P0-8-amendment-2026-05-24 红线不变)+ `SnapshotStore(root=QUANTMIND_LINE1_FRAME_ROOT 默认 data/line1_frames)` + `Line1FrameAssembler` → `assemble(as_of_date, signal_id)` → `application.state.line2_daily_frame = result.frame_snapshot`。
- **懒加载**:首个当日 cron(09:35 line1 或 line2)触发组装,当日其余 cron 复用(`trade_date == as_of` 幂等早返)。
- **无竞态**:`asyncio.Lock`(per-construction,`_init_line2_runners` 内)串行化 09:35 line1+line2 同槽 cron,只组装一次。
- **fail-open**:任何组装异常 → log `daily_frame_assembly_failed` + frame 留空 → callback 干净跳过(无崩溃、无交易路由)。与 pre-U-D6c seam 行为一致;**数据损坏永不静默交易**(损坏的 frame 不会被组装出来,assemble 内部 `_validate_pull` 已 fail-closed)。
- **不在 boot/gate 路径**:frame 组装只在 cron 触发时跑,不拖慢启动、不进 gate 评估(cond9 仍只探 3 ETF 可达,见 P0-6-amendment-2026-05-29)。

## 2. C2 — `fetch_time_utc` 锚定 T-1 EOD(防 snapshot_at < created_at 倒挂)

把 `t_minus_1_eod_utc(as_of)`(原在 `scripts/dry_run_realdata.py`,U-D4b 引入)**提升为共享 util** `backend/utils/trading_hours.py::t_minus_1_eod_utc`(纯函数,zero backend.{llm,agents,mirofish,data} import,不破 risk 隔离);dry-run 改为 import 它(行为 byte-identical,`__all__` 再导出兼容旧 caller)。

生产 assembler 用 `now_utc=lambda: t_minus_1_eod_utc(as_of)`,把 `fetch_time_utc` 锚到 T-1 15:00 收盘(= 数据真正所属时刻):

- **不变量确定性成立**:`snapshot_at`(T-1 周五 15:00)严格早于 run-day ~09:35 `created_at`(`InstructionPlan` 的 `snapshot_at must be strictly before created_at`,`backend/models/instruction.py`),数小时余量,**消除墙钟竞态**(U-D4b 在 dry-run 修过同款倒挂;生产此前用墙钟默认,首次真跑必踩)。
- **append-only store 复用安全**:`SnapshotStore` 复用键 =(vendor, endpoint, trade_date),`fetch_time_utc` 非复用键;锚定值对给定 as_of **确定**(周五 15:00),当日重组装复用同一帧 → 同一 `fetch_time_utc`,不再有 U-D4b dry-run 那种「复用旧墙钟帧」问题(生产用持久 store 即可,无需 fresh temp)。
- **PIT 契约不破(R0 §3 红线 A)**:`fetch_time_utc` 是纯 provenance,不入 snapshot checksum(只算 raw bytes)也不入 replay 摘要 → bit-exact `replay` 不受影响。

## 3. 不变量(本 amendment 不触碰)

- Tushare 官方 SDK only / token 异质凭证不入 LLM 3+飞书 5 池 / 数据成本不设 ceiling(P0-8-amendment-2026-05-24)不变。
- frame 组装失败 fail-open(infra glitch)、frame 内容损坏 fail-closed(`Line1FrameAssembler._validate_pull` 拒部分拉取)—— 与「fail-closed for data corruption / fail-open for infra glitch」一致。
- 单一构造点 M-004 / RiskEngine 14-check / 飞书人工 / 永禁真实下单等安全地基不变;frame 只是喂给既有 Line-1/Line-2 路径的输入,不改下游守门。

## 4. follow-up(本 amendment 范围外,已知)

- `today_instruction_count` 仍 `main.py` 装配为 0(seam 注释「U-D3 wired」)→ ≤5单/日 cap 同日重启会漏计先前订单。候选后续(与 C3 DataQualityProvider 同批 U-D7)。
- 真·端到端验证(真 Tushare frame + 真 qwen 辩论 + 真发)只能在周一 09:35 实跑确认;本 amendment 的离线 e2e(U-D5)用 fake adapter 覆盖链路结构。
