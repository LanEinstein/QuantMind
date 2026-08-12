# 新 session goal prompt — 修复飞书 WS 入站(websockets 版本不兼容)+ 跑通真飞书往返闭环

> 开工先发一句:`读 docs/next-session-prompt-feishu-inbound-websockets-fix.md,按 GOAL 块执行`。
> 完成判据 A–E 全绿 = 入站打通 + 闭环跑通。其后的「模板优化」是独立任务块(F),按需做。

---

## 0. 开工协议(必做)
按 `docs/SESSION-KICKOFF.md`:先 `grep` `docs/plan.html` 的 `#session-log` + `status="doing"`/`"blocked"` 定位上次停在哪;读 `CLAUDE.md §1/§2/§2.6`(P0-2 飞书红线)+ memory:
- `project_feishu_inbound_websockets_incompat_2026_05_30`(本次根因,**先读**)
- `project_u_d9_feishu_ws_receiver_fix_2026_05_29`(U-D9 接收器修复)
- `feedback_codex_rate_limit_fallback` / `feedback_push_main_gated` / `feedback_report_in_chinese`

HEAD = `f06e5f4`(SSoT #53)。两个 U-D9 commit(`11f0b89`/`f06e5f4`)**未 push**(owner-gated)。`scripts/e2e_interactive_test.py` untracked。

---

## 1. 背景(2026-05-30 owner-in-loop 真飞书往返实测)

U-D9 把飞书 WS 入站接收器的「启动即崩」修好了(daemon 线程 + 独立 loop + 同步桥接),TLS 握手能连上。这次真发了一条测试 BUY,**出站成功**(到决策群 `oc_77e2…`,`message_id=om_x100b6e90c96a44acc443cd7b5353efe`,plan 落 Mongo、outbox `sent`)。**但 owner 回复两次(普通 + @机器人)后,后端 WS 收到 0 个入站事件**(0 `feishu_event_*`、0 `FEISHU_MESSAGE_RECEIVED` audit、0 broker_events、plan 仍 VALIDATED)。

### 根因(高置信,已用独立探针证实)
**依赖版本不兼容,不是配置/代理/我们的代码**:
- 环境装的是 `websockets==15.0.1`;`lark-oapi==1.5.3` 的 `ws/client.py` 用 **websockets 旧版 API**(`websockets.connect` / `websockets.legacy` / `WebSocketClientProtocol` / `websockets.InvalidStatusCode`)。
- 独立探针(构造 `lark.ws.Client` + `register_p2_im_message_receive_v1` + 在 daemon 线程 `start()`)一连就报 `'NoneType' object has no attribute 'send'` —— 新版 websockets 打断旧 API 的典型症状。
- 现象:握手能打印 `connected to wss://msg-frontier.feishu.cn`,但 `_receive_message_loop` 的收发当场断、静默退出 → 入站事件一个都进不来。**完美解释「能发(HTTP API)不能收(WS)」**。
- 已排除:代理(`backend/.../lark_oapi/ws/client.py` 0 处用代理;`127.0.0.1:10808` 的两条连接是行情/新闻/LLM 的 httpx,不是 WS;加 `no_proxy` 无变化)。

---

## GOAL / 任务

### A. 证实根因 + 定位兼容版本
1. `pip show websockets` 看当前版本(预期 15.0.1)。`pip show lark-oapi`(1.5.3,`Requires` 里 websockets **未 pin**)。
2. 查 lark-oapi 1.5.3 发布期(2024 中)兼容的 websockets 版本范围。`websockets` 在 **14.0(2024-11)** 移除/重构了 `legacy` 客户端 API,15.x 进一步。lark 1.5.3 用 legacy → 需 **`websockets>=11,<14`**(建议先试 `websockets==13.1`,这是 14 之前最后一个稳定版)。**以 lark 1.5.3 实际 import 的符号为准**:`grep -nE "websockets\.(connect|legacy|InvalidStatusCode)|WebSocketClientProtocol" $(python -c 'import lark_oapi.ws.client as c;print(c.__file__)')`,确认目标版本仍提供这些符号。
3. 写一个临时独立探针复现失败(连接即 `NoneType.send`),作为 before 对照(跑完删)。

### B. 修复:pin websockets 兼容版本
1. 找到依赖声明处:`requirements*.txt` / `pyproject.toml` / `environment.yml`(`grep -rn websockets requirements* pyproject.toml 2>/dev/null`)。新增/改为 `websockets>=11,<14`(或确定的具体版本)。
2. `/home/ps/anaconda3/envs/zhanglan/bin/pip install 'websockets<14'`(在 zhanglan env)。记录装到的确切版本。
3. **重要**:这是共享依赖,可能影响 uvicorn/httpx/其它。装完立即跑全量 pytest(见 D)看有无回归。

### C. 验证入站(关键,离线测试掩盖过 bug,必须真启 + 真收)
1. 先 `pkill -9 -f "uvicorn.*backend.main:app"; ss -ltn|grep :8000||echo free`。
2. go-live env 在 `~/.bashrc`(已确认全 SET:`FEISHU_INTERACTIVE_ENABLED=true` + 5 飞书凭证 + `FEISHU_OWNER_OPEN_ID` + `FEISHU_DECISION_CHAT_ID` + 3 LLM key + 3 prod-gate env)。
3. 真启:`: > logs/backend-inbound.log; setsid nohup <env> /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> logs/backend-inbound.log 2>&1 </dev/null & disown`。轮询日志(勿阻塞 sleep)等 `Application startup complete` + `feishu_event_receiver_wired`。
4. **复用上次留下的 plan**:Mongo `instruction_plans` 里有 `QM-20260529-093500-510300-BUY-001`(VALIDATED,outbox sent)。让 owner 在决策群按模板回复(发前**不用**再真发,直接复用这条;若已过期则用 harness 重发一条新的):
   `盘后补录 已执行 QM-20260529-093500-510300-BUY-001 买入 510300 100股 成交价 4.00`
   (注:`盘后补录` 前缀因 plan valid_until 已过;若用新 plan 且在 14:55 前可不加)。
5. **判据**:`grep feishu_event logs/backend-inbound.log` 出现入站事件(`feishu_event_dispatch`/`_skipped`/`_dedupe` 之一);`FEISHU_MESSAGE_RECEIVED` audit ≥1;`scripts/e2e_interactive_test.py --verify QM-20260529-093500-510300-BUY-001` 显示 `broker_events (correlation_id) ≥1` + `EXECUTION_REPORT_APPLIED` → **ROUND-TRIP MIRRORED ✓**;Mongo 持仓 +100 510300、现金减(gross+费)。**每次让 owner 真发/真回复前必明示拿确认**。
6. 验证后干净停掉 backend。

### D. 门禁
- `ruff check` 改动文件全绿。
- `bash scripts/redline-check.sh` 全绿(P0-2 飞书红线 / 仅 2 写端点 / M-004)。
- clean-env 全量 pytest(**确认 pin websockets 无回归**):
  ```
  env -u FEISHU_INTERACTIVE_ENABLED -u QUANTMIND_PROD_RUN -u QUANTMIND_OWNER_PROD_AUTHORIZATION -u QUANTMIND_FEISHU_TIER -u FEISHU_DECISION_CHAT_ID \
    /home/ps/anaconda3/envs/zhanglan/bin/pytest -q -p no:cacheprovider
  ```
  基线 **4160 passed / 13 skipped**。
- codex 撞额度(~恢复后)→ 否则回退 `claude /code-review high`(owner 既定),修完 P0/P1/P2 再 commit。

### E. 提交 + SSoT 记账
- 一个 feature commit(依赖 pin + 任何 receiver 适配)。若纯依赖文件改动也算「有代码任务」需过 review 门。
- `docs/plan.html`:新任务条目(建议 `U-D10` — 飞书 WS 入站 websockets 兼容修复)`status=done` + 真 hash + SESSION_LOG 一条 + 修订记录一行。
- **push origin main 待 owner 授权,严禁自行 push**(累积含 `11f0b89`/`f06e5f4` + 本次)。

---

## F. 后续独立任务块(owner 2026-05-30 提的回报模板优化;入站打通后做)

owner 原话:① 回报每个内容单元之间要有明显间隔(空格/括号/换行);② 编号没必要回复;在「信息必要/完整/无歧义」前提下尽量简化。

1. **加分隔(无依赖,可先做)**:改 `backend/integrations/feishu/renderer.py` 的 `_REPORT_TEMPLATE_BLOCK`(回报模板),单元间加清晰分隔;同步前端镜像若有。改后跑 `test_feishu_renderer.py` 快照。
2. **去掉编号(有红线依赖)**:parser 现在靠 `instruction_id` 关联,P0-2/P0-4「严禁猜 instruction_id」。最干净方案 = owner 用飞书**「回复/引用」那条 BUY 消息**:入站事件带被引用消息的 `parent_id`/`root_id` → 用 outbox 存的 `feishu_message_id`(`instruction_outbox.feishu_message_id`)反查 instruction_id。需:
   - 新 amendment `docs/decisions/P0-4-amendment-YYYY-MM-DD-reply-quote-correlation.md`(先写再改代码);
   - `backend/integrations/feishu/events.py` 的 `ReceivedMessage` 补 `parent_message_id` 字段(从 lark P2 envelope 的 `message.parent_id`/`root_id` 提取);
   - parser 增加「按 parent_message_id → outbox 反查 instruction_id」分支,找不到才回退到正则里的编号;
   - 仍 fail-closed:引用关联不到 + 文本也没合法编号 → AMBIGUOUS,绝不猜、绝不改镜像。
   - 先把入站打通(A–C)才能真测这条。

---

## G. 现场遗留(必读)
- **harness `scripts/e2e_interactive_test.py`(untracked)有个 `--send` 小 bug**:用了 `InMemoryLedgerRepository` 但没先 `open_for_plan` → 发送**成功后** `_finalize_dispatch` 抛 `LookupError: decision_ledger has no entry…`(不影响已发出的消息)。修法:dispatch 前 `await ledger.open_for_plan(plan)`,或传一个 no-op ledger。修好后此 harness 可作为入站重测工具(`--preview` 0 写 / `--send --confirm` 真发 / `--verify <id>` 查镜像)。决定是否把它 commit(它是 ops 工具,建议修完 + 过 review 后纳入)。
- **Mongo 遗留**:`instruction_plans` 有测试 plan `QM-20260529-093500-510300-BUY-001`(VALIDATED)+ `instruction_outbox` 对应 `sent`;账户未动(无成交)。可直接复用重测入站;闭环验完用 `ReconciliationApplier.reset_to_snapshot`(P0-5 唯一合法外部写,**禁直接 mutate** `_cash/_positions`)清干净。
- 上个 session 工具/输出通道严重降级(反复截断/乱码),才把这步留到新 session。

## H. 红线提醒
永禁真实券商下单;真发/真收飞书前置 owner 确认内容+目标群;决策群 `oc_77e2…` ≠ 告警群;LLM 不碰决策/回报/对账/数据质量;RiskEngine 纯 14-check;单一构造点 M-004;config runtime 不可改 + hot-reload 全禁;全层 127.0.0.1;`websockets` log 别开 INFO(会把连接 URL 的 ticket/access_key 凭证写日志,§2.9;保持 WARNING);向 owner 报告中文、thinking/代码/commit 英文。

## I. 操作速查
```bash
# 1) pin websockets
/home/ps/anaconda3/envs/zhanglan/bin/pip show websockets lark-oapi
/home/ps/anaconda3/envs/zhanglan/bin/pip install 'websockets<14'
# 2) 全量门禁(clean env)
env -u FEISHU_INTERACTIVE_ENABLED -u QUANTMIND_PROD_RUN -u QUANTMIND_OWNER_PROD_AUTHORIZATION -u QUANTMIND_FEISHU_TIER -u FEISHU_DECISION_CHAT_ID \
  /home/ps/anaconda3/envs/zhanglan/bin/pytest -q -p no:cacheprovider          # 4160 passed/13 skipped
bash scripts/redline-check.sh
# 3) 真启 interactive + 验入站
setsid nohup /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 > logs/backend-inbound.log 2>&1 </dev/null & disown
grep -E "feishu_event|FEISHU_MESSAGE_RECEIVED|Application startup complete" logs/backend-inbound.log
/home/ps/anaconda3/envs/zhanglan/bin/python scripts/e2e_interactive_test.py --verify QM-20260529-093500-510300-BUY-001
```
```
/goal websockets pin 到 lark-oapi 1.5.3 兼容版本后,真启 interactive backend + owner 真发一条测试回报,logs 出现 feishu_event 入站事件 + --verify 显示 broker_events EXECUTION_REPORT_APPLIED(ROUND-TRIP MIRRORED),且 ruff+redline+全量 pytest(4160 passed)无回归;已 feature commit + plan.html 记账。或满 30 轮停。
```
