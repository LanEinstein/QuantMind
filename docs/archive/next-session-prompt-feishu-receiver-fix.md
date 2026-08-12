# 新 session goal prompt — 修复飞书 WS 入站接收器崩溃 + 跑通历史数据真·端到端

> **`/goal` 调查结论(2026-05-29)**:`/goal` 是 Claude Code **真实内置命令**(需 v2.1.139+),语法 `/goal <完成条件>` —— **条件驱动**自主循环(每轮由小模型 Haiku 依**对话内容**检查条件,满足即**自动停**,无需人工)。一个 session 只能一个活跃 goal;条件 ≤4000 字符;条件必须是**对话中能演示**的(如"真启后日志无崩溃 + 测试绿"),Haiku 不独立跑命令、只看对话。与时间驱动的 `/loop` 不同。
>
> **用法**:新 session(**工具通道健康时**)先发一句让我读本文件并按下面 `GOAL` 块执行 —— `读 docs/next-session-prompt-feishu-receiver-fix.md,按 GOAL 块执行`;再设条件循环:
> ```
> /goal 飞书 WS 入站接收器已修:真启 interactive backend 后 60s 内 logs 无 feishu_event_receiver_crashed 且无 "this event loop is already running" 且 WS 连接常驻不崩;ruff+redline-check+pytest 全绿无回归;已 feature commit + plan.html 记账。或满 30 轮停。
> ```
> (完成判据 A–D 是 `/goal` 可自主达成并验证的「修好」终点;之后的 owner-in-loop 真飞书往返不进 `/goal` 条件,达成 A–D 后我会明确交还给你做。)

---

## GOAL

你在 QuantMind(`/home/ps/papers/QuantMind`)继续。先按 `docs/SESSION-KICKOFF.md` 开工:读 `docs/plan.html`(SSoT)+ `CLAUDE.md §1/§2/§2.6`(P0-2 飞书红线)+ memory(`project_monday_mvp_c3_outbox_e2e_2026_05_29` / `project_u_d6_gate_live_probes_2026_05_29` / `feedback_codex_rate_limit_fallback` / `feedback_push_main_gated` / `feedback_report_in_chinese`)。

### 背景(2026-05-29 session #52 实测发现的真 bug)
真启 interactive backend 做历史数据真·端到端时发现:backend 起来、过 11/11 PILOT gate、:8000 listening、mode→feishu_interactive,**但飞书 lark-oapi WebSocket 入站接收器启动即崩**:
```
[Lark] [ERROR] connect failed, err: this event loop is already running.
feishu_event_receiver_crashed app_id_fingerprint=adf1b63f error_class=RuntimeError
[Lark] [ERROR] receive message loop exit, err: sent 1000 (OK); no close frame received
```
→ **系统收不到 owner 的飞书回复 → 人工执行闭环断 → 阻断周一真 BUY**(BUY 能发出但回报无法回传镜像)。
这是「测试通过 ≠ 闭环可用」:#51 过了 11/11 gate、U-D5 把入站 fake 了,**真实飞书 WS 接收从没被真跑验证过**。

**根因假设**:lark-oapi 的 `ws.Client.start()` 是阻塞调用、内部自起 asyncio loop(`asyncio.run()`/`run_until_complete`),在 uvicorn 已运行的 event loop 里调用即抛 `RuntimeError: this event loop is already running`。先对照实际代码 + 安装的 lark-oapi 版本证实/修正。

### 任务
1. **定位**:飞书事件接收器/lark ws Client 的构造与**启动**点 —— `backend/integrations/feishu/`(ws client、event dispatcher handler、`register_p2_im_message_receive_v1` 之类、`.start()`/connect 调用)+ `backend/main.py` lifespan(怎么拉起的:`asyncio.create_task`?协程?线程?)+ `feishu_event_receiver_crashed` 日志发出处的周边启动逻辑。查 lark-oapi 版本(`pip show lark-oapi`)及其 `ws.Client` 的 start/_connect API(有无可 await 的异步 connect,还是只有阻塞 `start()`)。
2. **修复**(保 P0-2 红线:lark-oapi WebSocket 长连接**唯一**入站、严禁 HTTPS 回调入站、3s ack、`tenant_access_token` 仅内存、所有入站仍走既有 InboundGate→renderer→parser→ExecutionReportApplier,**只改启动/生命周期机制,不改消息处理**):把 `client.start()` 丢到**独立 daemon 线程**跑(它在该线程内自管 loop),lifespan 启动时起、关闭时干净停;或若 SDK 提供异步 connect 协程则作 task 调度。加重连韧性(若简单)。
3. **codex/审查门禁**(CLAUDE.md §3,有代码即触发):codex 撞额度时回退 `claude /code-review high`(owner 既定),修完 P0/P1/P2 再 commit。
4. **commit**(一任务一 feature commit)+ **plan.html SSoT 记账**(新任务条目 done + 真 hash + SESSION_LOG 一条 + 修订记录一条)。**push origin main 待 owner 授权,严禁自行 push**。

### 完成判据(全满足才算「修好」,逐条验证)
- **A 代码**:根因证实 + 修复实现;`grep` 确认不再有「在已运行 loop 里阻塞 start」的路径。
- **B 实启验证(关键,离线测试掩盖过此 bug,必须真启)**:用 go-live env(在 `~/.bashrc`,session #52 已确认全 SET)真启 interactive backend:
  - 先 `pkill -f "uvicorn.*backend.main:app"; ss -ltnp|grep :8000||echo free`;
  - `: > logs/backend-fix.log; <env> uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> logs/backend-fix.log 2>&1 &`;轮询日志(勿用阻塞 sleep)≥60s。
  - 判据:`Application startup complete` + 过 gate + **无 `feishu_event_receiver_crashed`** + **无 `this event loop is already running`** + 日志可见飞书 WS **连接成功并常驻 ≥60s 不崩/不进重连死循环**(引用日志行佐证)。验证后干净停掉测试 backend。
- **C 门禁**:`ruff check` 改动文件 + `bash scripts/redline-check.sh` 全绿(P0-2 飞书红线、仅 2 写端点、M-004 不破)+ `pytest`(飞书/orchestration 相关 + 全量无回归,基线 4157 passed/13 skipped,clean env 见下)。
- **D 提交+记账**:feature commit + plan.html done+hash + SESSION_LOG/修订记录。

> 判据 A–D 是**可自主达成**的「修好」终点 —— `/goal`/`/loop` 跑到 A–D 全绿即停。

### 之后(owner-in-loop,非自主循环范围 —— 达成 A–D 后明确交给 owner)
历史数据真·端到端(owner 已于 #52 授权此测试形态,但**每次真发飞书必前置 owner 确认确切内容+目标群**):
1. harness `scripts/e2e_interactive_test.py`(#52 子代理建,untracked;注意其 `--preview` 当前会写 Mongo,先改成纯 0 写预览再用)`--preview` → 把渲染原文 + instruction_id + 目标群(决策群 `FEISHU_DECISION_CHAT_ID`,**非**告警群)摆给 owner 确认。
2. owner 点「发」后 `--send` 真发 → owner 在飞书按模板回复 → 运行中 backend WS 入站 → parser → ExecutionReportApplier → MockBroker 镜像。
3. 用 Mongo/audit 核对:`instruction_plans` 该 id status→FILLED、`broker_events` 有 fill、持仓 +量、现金减(gross+费)。
4. 跑完用 `ReconciliationApplier.reset_to_snapshot`(P0-5/P1-2.A 唯一合法外部写;**禁直接 mutate** `_cash/_positions`)把账户重置到干净基线 + 清掉测试 plan,为周一实时全链路留干净状态。

### 红线提醒
永禁真实券商下单;真发飞书前置 owner 确认内容+目标群;决策群 `oc_77e23…` ≠ 告警群 `oc_9edd…`;LLM 不碰决策/回报/对账/数据质量;RiskEngine 纯 14-check;单一构造点 M-004;config runtime 不可改 + hot-reload 全禁;全层 127.0.0.1;fail-closed for data corruption / fail-open for infra glitch;向 owner 报告中文、thinking/代码/commit 英文。

### 操作速查
```bash
# clean-env 跑全量(避免 go-live env 让 3 个 orchestration test env-induced fail)
env -u FEISHU_INTERACTIVE_ENABLED -u QUANTMIND_PROD_RUN -u QUANTMIND_OWNER_PROD_AUTHORIZATION -u QUANTMIND_FEISHU_TIER -u FEISHU_DECISION_CHAT_ID \
  /home/ps/anaconda3/envs/zhanglan/bin/pytest -q -p no:cacheprovider      # 基线 4157 passed/13 skipped
bash scripts/redline-check.sh
# 真启 interactive(go-live env 在 ~/.bashrc;过 11/11 gate;权威看 audit)
/home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/backend-fix.log 2>&1 &
grep -E "feishu_event_receiver_crashed|this event loop is already|Application startup complete|brokerscheduler_started" logs/backend-fix.log
```

### 现状(session #52 收尾)
HEAD `f7c3f30`(SSoT #52)。三障碍 C3(U-D7 `b43eee8`)/outbox(U-D8 `b5283ce`)/U-D5(`a70e8c7`)+ U-D6c docs(`65113ff`)全 done+committed,4157 passed;**均未 push(owner-gated)**。端口空、无残留进程、Mongo `instruction_plans`/`outbox` 已清空。harness `scripts/e2e_interactive_test.py` untracked。**本接收器 bug 是周一前 #1 必修项。**
