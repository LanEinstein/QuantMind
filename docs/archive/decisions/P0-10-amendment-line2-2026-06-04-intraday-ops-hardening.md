# P0-10 修订(Line-2)— 2026-06-04 盘中 runner ops 加固(持久去重 / REJECTED 告警 / 同日 SELL→ADD 互斥)

> **修订基准**: [P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction](./P0-10-amendment-2026-05-25-line2-monitoring-deterministic-construction.md)(Line-2 确定性监控路径)+ [P0-2-amendment-2026-05-16](./P0-2-amendment-2026-05-16-no-custom-bot-five-credentials.md) §4(告警词汇表锁定)
> **关联**: `docs/research/sell-timing-deep-dive-and-redesign-2026-06-04.md` §5 E6 / §1 实证复盘(06-03/06-04 生产事故链)
> **修订日期**: 2026-06-04(#70 session,第 0 期)
> **决策人**: owner(2026-06-04 拍板「全四期顺序推进」)
> **性质**: ops 加固,**无策略语义变化**(不引入新交易触发、不改任何阈值);默认启用(非 env 门控——修的是基础设施缺陷,与 bug fix 同性质)。

## 0. 意图与现状(实证)

2026-06-03/04 生产事故链暴露 3 个 ops 缺口(详见 dossier §1):

1. **内存去重重启即重置**:`Line2IntradayRunner._fired`(每日 `(code, trigger_kind)` 去重)只存内存 → 06-04 两次重启(14:26 / 14:44)各重置一次 → **同一批 3 笔 SELL 在 18 分钟内向飞书重复发出两遍**(QM-20260604-142714-* 与 QM-20260604-144529-*)。
2. **REJECTED 静默**:RiskEngine 拒单的 Line-2 SELL 只落 audit/日志,无任何主动可见性 → `prev_close unavailable` 数据缺口把全部止盈/止损建议吞了**两个交易日**(06-03 全天 6 笔 + 06-04 早盘 3 笔)才被人工发现。
3. **同日 SELL→ADD 矛盾建议**:同 tick 的 SELL 抑制 ADD(codex U-C3 P1)只管当 tick;跨 tick 无互斥 → 06-04 14:27 建议卖 605020、14:50 又建议补仓 605020。

## 1. 决策

### 1.1 持久化每日去重(`FiredTriggerStore`,**双 runner 共用**)

- 新纯模块 `backend/orchestration/fired_trigger_store.py`:append-only JSONL(`data/line2_intraday_state/fired_triggers.jsonl`,`FileLock`,镜像 `takeprofit_ledger` 模式),行 = `{trade_date, code, kind, signal_id}`;装载时跳过空 code/kind 损坏行(防 `""` 经 §1.3 互斥误压全部 ADD);`prune_before(date)` 保留近 7 日(当日首 tick 调用,防 append-only 文件无限增长 + 每日全量重扫)。
- **记账语义(review 三轮收敛)**:
  - **持久层 = 仅「确认送达」**(dispatched / simulation_routed / dry_run_rendered / **skipped_duplicate**〔outbox 已 SENT,owner 已有卡片〕)。
  - **REJECTED → 仅进程内 dedup**(本进程不再 30s 重复拒单),**不持久**——operator 修复拒单根因(如 prev_close 数据缺口)后**重启即重试**,这是既有恢复手册;持久化会把「拒单原因盘中已消除」的保护性退出压制到收盘(review angle A)。
  - **`send_failed`(飞书 API 确定性失败,owner 没收到)不记账 → 下一 tick 重试**(codex P2;dispatcher 本就为此释放 outbox);但**封顶 `_MAX_UNDELIVERED_ATTEMPTS_PER_DAY=5` 次/日/键**,到顶进进程内 dedup + error 日志——防飞书持续宕机时整 session 每 30s 重建+重发+重持久快照的重试风暴(review angle B)。封顶同样不持久:宕机恢复后重启即重试。
  - `skipped_in_flight` 不记账(per-tick 唯一 instruction_id 使其实际不可达;保守方向 = 可重试)。
- **fail-open(方向论证)**:store 读/写/prune 异常 → 空集/跳过 + error 日志,**绝不**让 tick 失败——去重是 UX 层(防重复消息),不是安全层(每笔订单仍经 RiskEngine 14-check + 飞书人工 gate;重复消息的最坏后果是 owner 多看一条,而 fail-closed 的最坏后果是保护性止损停摆)。与 `takeprofit_ledger` 的 fail-closed 形成对照:那边管「会不会多卖一档」(安全层),这边管「会不会多发一条」(UX 层)。已知残余:store 文件不可读时退化为旧内存语义(重启可能重发,error 日志可见)。
- **`Line2DailyRunner` 同样注入**(review altitude angle):09:35 日线 SELL 与 30s 盘中是同一事故类——cron `misfire_grace` 重启重跑 + 每次新铸 instruction_id 使 outbox 无法去重 → 不接 store 就会整批重发;daily 用 `(code, AnomalyKind.value)` 键(与盘中 kind 天然不冲突),新增 `SellRouteOutcome.DEDUP_SKIPPED`;daily 的 SELL fire 顺带喂给盘中 §1.3 互斥(同一 store)。
- `main.py` 对两个 runner 恒注入(默认启用);路径 env `QUANTMIND_LINE2_FIRED_STORE_ROOT` 可覆盖(测试用)。

### 1.2 REJECTED SELL 告警(新告警词汇 `line2_protective_sell_rejected`)

- runner `_route_one` 的 REJECTED 分支(`plan.status ≠ VALIDATED`):**SELL side** 触发注入的 `reject_alert_hook`(BUY/ADD 拒单不告警——Line-1/补仓拒单是正常 fallthrough)。hook 异常吞掉(fail-open,告警失败绝不影响 tick)。**`Line2DailyRunner._route_sell` 同样接入**(altitude angle:06-03 事故同时杀了两条 Line-2 路径,日线 SELL 拒单同样不能静默)。main.py hook 在 `alert_dispatcher` 未就绪时打 warning 日志(绝不静默,review angle A)。
- 告警词汇表新增 **1 条**(按 P0-2-amendment-2026-05-16 §4 锁定程序,本 amendment 即修订文件):
  - `ALERT_MATRIX["line2_protective_sell_rejected"]`:audit_event_type=**`RISK_ENGINE_CHECK_REJECTED`(复用既有 34 类,不新增 audit 类型)**,fire_to_feishu=True,severity=critical,reason_namespace=`line2_protective_sell_rejected`。
  - `FeishuAlerter.ALERT_TYPES` 同步加入。dedup 15min(`dedup_key` 带 code+kind,不同标的/触发独立告警)。
- 红线辨析:这是**系统异常告警**(「保护性退出被风控吞掉,需排查」),不是买卖/对账/澄清消息(那些仍走 `FeishuMessenger` 决策群)——`FORBIDDEN_DECISION_PATH_ALERTS` 不变,告警文本不含可执行指令要素(无价/量指令语义),走告警群。
- 若 06-03 已有此告警:`prev_close unavailable` 在 10:19 即到飞书告警群,潜伏期从 2 天 → 15 分钟。

### 1.3 同日 SELL→ADD 单向互斥

- 当日已触发任意 **SELL kind**(含 REJECTED;含 take_profit/weight_trim)的 code → 当日剩余 tick **禁 ADD**(从 `fired_today` 派生 `(code, kind≠"add")`,持久化后跨重启依然成立)。
- **单向**:ADD 已触发**不**反向禁 SELL——保护性止损永不被抑制(只增卖压红线);同 tick 互斥(SELL 胜)不变。
- 语义论证:同日先卖后补 = 自相矛盾的建议流(owner 实证反感);卖后低吸回补属第 3 期 RE_ENTRY(次日开盘,经独立 gate),不属当日 ADD。

## 2. 红线影响(全保持)

- 零 LLM / 单一构造点 / RiskEngine 14-check / 飞书人工 gate / 仓位三连 / 熔断:不变(本期不触碰任何触发 maths)。
- 告警词汇 +1(走锁定程序);audit 34 类**不变**(复用 `RISK_ENGINE_CHECK_REJECTED`)。
- 备用 webhook 红线不变:告警经 `AlertDispatcher` → `FeishuAlerter`(自建应用 OpenAPI → FEISHU_ALERT_CHAT_ID),非 custom-bot。
- runner import 隔离不变:`fired_trigger_store` 零 `backend.*` 子包 import;alert hook 由 main.py 闭包注入(runner 不 import alert_dispatcher)。
- PIT:去重持久化不进 manifest/config_hash(非决策输入,只是「已发过」状态);`FEATURE_CODE_VERSION` 不 bump(触发 maths 零变化)。ADD 互斥改变的是「发不发」而非「算不算」,与既有同 tick 抑制同级,记入 runner 文档。

## 3. 测试锚点

- store:空文件/缺文件 → 空集;读写往返;损坏行 fail-open 空集 + error 日志;按日过滤;FileLock 并发安全(单进程内多次实例)。
- runner:重启模拟(新 runner 实例 + 同 store)→ 已触发 kind 不再路由(DEDUP_SKIPPED);store 注入 None = 现行为 bit-for-bit。
- REJECTED SELL → hook 被调(code/kind/instruction_id);BUY 拒单不调;hook 抛异常 tick 不死。
- 同日 SELL 已 fired(内存或 store 装载)→ 该 code ADD 被滤掉;ADD fired 不影响 SELL;dispatcher/alerter 词汇表测试更新。
