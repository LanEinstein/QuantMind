# 新 session goal prompt — 实测飞书入站成功确认回执(U-D12 ack)+ 复验真往返闭环

> 开工先发一句:`读 docs/next-session-prompt-feishu-ack-livetest.md,按 GOAL 块执行`。
> 这是一次 **owner-in-loop 真飞书实测**(不是纯代码任务):需要 owner 亲自在决策群发回报。每次真发/真收前必先明示内容+目标群拿 owner 确认。

---

## 0. 开工协议
按 `docs/SESSION-KICKOFF.md`:`grep docs/plan.html` 的 `#session-log` + `status="doing"`/`"blocked"` 定位上次;读 `CLAUDE.md §1/§2/§2.6`(P0-2/P0-4 飞书红线)+ memory:
- `project_feishu_inbound_websockets_incompat_2026_05_30`(**先读** — U-D10/U-D11/U-D12 全过程 + ops 坑)
- `feedback_report_in_chinese` / `feedback_push_main_gated` / `feedback_codex_rate_limit_fallback`

HEAD 应在 `6964166`(SSoT #54 cont.2)。本 session 前置 7 个 commit(`994de7c`/`3ac0bde`/`60d9f5d`/`488a3a1`/`9958d0e`/`28b2643`/`6964166`)**未 push**(owner-gated)。

---

## 1. 背景(2026-05-30 #54 已完成)
人工执行真往返闭环已跑通:owner @机器人 飞书回报 → WS 入站(U-D10 `websockets<14` pin 修传输 + U-D11 剥离 `@_user_N` 占位符)→ 严格正则 FILLED v2 → ExecutionReportApplier → MockBroker 镜像。**U-D12 新增成功确认回执**:apply 成功后发一条 `【QuantMind 已记录】` 回决策群,契约=每条 owner 回报必有且仅有一条回复(成功 ack / 失败澄清)。

**唯一未实启验证的点**:U-D12 的 ack —— 上个 session 内后台 uvicorn 被即时回收,无法常驻 backend 真发观察(全单测覆盖 + 底层 send 路径已 U-D11 实启过,残留仅「编排成功路径确调 _send_ack 且 owner 收到」布线观察,低风险)。**本 session 就补这条实测。**

---

## GOAL / 任务(达成即收工)

### A. 起 backend(注意 ops 坑)
1. 清端口:`pkill -9 -f "uvicorn.*backend.main:app"; sleep 2; ss -ltn | grep :8000 || echo free`。
2. **ops 坑(上个 session 实测)**:`python -m uvicorn` 形式带全env会静默崩;后台(setsid/nohup/run_in_background)启 uvicorn 可能被即时回收(前台/binary 可起;后台 `sleep` 正常→uvicorn 专属)。**先试**标准后台 binary 起法:
   ```
   cd /home/ps/papers/QuantMind
   setsid bash -c 'source ~/.bashrc; cd /home/ps/papers/QuantMind; exec /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 >> logs/backend-acktest.log 2>&1 </dev/null' & disown
   ```
   轮询 `logs/backend-acktest.log` 等 `Application startup complete` + `feishu_event_receiver_wired`(勿阻塞 sleep)。
3. **若后台仍被回收(log 空/进程没了)**:改让 **owner 在自己的终端**起(不受会话进程管理影响),给 owner 这条:
   ```
   source ~/.bashrc && /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
   ```
4. 确认 WS 连上 feishu msg-frontier:`PID=$(pgrep -f "uvicorn.*backend.main:app"|grep -v grep|head -1); ss -tnp | grep pid=$PID | grep ESTAB | grep -v 127.0.0.1`(应见到 `120.222.x.x:443` 或 `111.13.110.x:443`)。
5. 确认账户干净基线:`curl -s http://127.0.0.1:8000/api/trading/account`(mode_switch 自动 reset → `available_cash 100000`, `market_value 0`)。

### B. 为「完整成功 ack」准备 plan
复用 Mongo 里 `QM-20260529-093500-510300-BUY-001`(VALIDATED)。**但幂等保护会拦重投**:若该报文内容此前已应用,会收到「【QuantMind 已收到】…未重复入账」而非完整「已记录」。要看**完整成功 ack**,二选一:
- (a) 清掉该报文的幂等键:
  ```
  /home/ps/anaconda3/envs/zhanglan/bin/python -c "import asyncio,redis.asyncio as r;
  async def m():
   c=r.Redis(host='127.0.0.1',port=6379,decode_responses=True)
   ks=[k async for k in c.scan_iter(match='broker:applied_report:*')];
   print('del',len(ks));
   await c.delete(*ks) if ks else None; await c.aclose()
  asyncio.run(m())"
  ```
  (清完重启 backend → mode_switch reset 账户 → 干净)。
- (b) 让 owner 回报里用**不同成交价**(如 `成交价 4.01`)→ 幂等键不同 → 全新应用。

### C. 真测(关键,owner-in-loop)
1. **先明示 owner 内容 + 目标群拿确认**:决策群 `oc_77e2…`(≠ 告警群 `oc_9edd…`),@机器人 发:
   ```
   盘后补录 已执行 QM-20260529-093500-510300-BUY-001 买入 510300 100股 成交价 4.00
   ```
   (`盘后补录` 前缀因 plan valid_until 已过;群消息须 @机器人才送达 bot,U-D11 会自动剥 `@_user_N`。)
2. **判据(全绿=U-D12 实测通过)**:
   - **owner 飞书里收到一条 `【QuantMind 已记录】`**(指令编号 + 买入 510300 100股 @4.00 + 账本现金变动 + 持仓变动 + 账本序号)—— 这是本次要补的核心观察。
   - 日志:`grep feishu_send_message logs/backend-acktest.log` 出现一条发往决策群 fp(`chat_id_fingerprint=4a5553c9`)的 ok=True。
   - 镜像:`/home/ps/anaconda3/envs/zhanglan/bin/python scripts/e2e_interactive_test.py --verify QM-20260529-093500-510300-BUY-001` 见 `broker_events execution_report_applied` + audit `execution_report_submitted=success`;`curl /api/trading/positions` 见 `510300 ×100 cost_price 4.05`、`/api/trading/account` 现金 99595。
   - **重投复测(可选,验幂等 ack)**:owner 再发**同一条** → 应收到 `【QuantMind 已收到】…未重复入账`,且账本不再变动(broker_events 不新增成交)。
3. **失败排查**:0 入站事件→确认 owner 真 @了机器人 + 是决策群 + backend 还活着;收到澄清而非 ack→看 raw_text 是否仍带未剥离的 @(U-D11)或正则不中(P0-4 fail-closed,正常)。

### D. 收尾
1. 验完干净停 backend;测试 fill 会随下次 interactive 启动 mode_switch 自动 reset 回 100000/0(append-only 事件留痕,正常)。
2. SSoT:`docs/plan.html` 把 U-D12 的 notes 补一句「ack 实启实测通过(owner 收到 【QuantMind 已记录】+ 日志 feishu_send_message ok)」+ SESSION_LOG #55 一条 + 修订记录一行(**纯文档实测记录,无代码改动 → docs-only commit,不需 codex/review 门**)。
3. **若实测中发现 ack 任何 bug → 有代码改动 → 走 review 门**(codex 撞额度至 5-31 → 回退 `claude /code-review high`,修完 P0/P1/P2 再 commit)。

---

## E. 红线提醒
永禁真实券商下单;真发/真收飞书前置 owner 确认内容+目标群;决策群 `oc_77e2…` ≠ 告警群;LLM 不碰回报/对账/数据质量;ack 经 renderer 防注入 / fail-open 不回滚已落账(P0-5 镜像权威);`websockets` 日志别开 INFO(连接 URL ticket/access_key 泄漏 §2.9,保持 WARNING);全层 127.0.0.1;向 owner 报告中文、thinking/代码/commit 英文。

## F. 后续(实测通过后可选)
- **owner 模板优化**(owner 2026-05-30 诉求):① 回报每内容单元加分隔符(改 renderer `_REPORT_TEMPLATE_BLOCK`,无依赖);② 去掉编号改用飞书「回复/引用」那条 BUY 消息关联 → 入站事件带 `parent_id` → 经 outbox `feishu_message_id` 反查 instruction_id(需新 P0-4 amendment + events.py 补 `parent_message_id` + parser 反查分支;@mention 既已剥离,引用关联是进一步简化)。
- **周一(下个交易日)09:35 真 cron 真 BUY** 为生产最终验证(Line-1 多候选辩论 → RiskEngine → 飞书真发 → owner @机器人回报 → ack + 镜像 → 16:00 对账)。

## G. push
**push origin main 待 owner 授权**:累积含 `994de7c`/`3ac0bde`/`60d9f5d`/`488a3a1`/`9958d0e`/`28b2643`/`6964166`(+ 更早 #51-#53 的 `11f0b89`/`f06e5f4` 等)。
