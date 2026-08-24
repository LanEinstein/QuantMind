# MI-1 组合整合 + 推送重设计 + 对账闭环 — 工程结果

> 日期: 2026-08-23 · 执行: Fable 5(主执行)+ Codex(决策讨论一次 + 唯一一轮 review)
> 依据: `KickoffPrompts/MI-1-integration-and-advisory-redesign-handoff-2026-08-23.md`
> 上位: `KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md` §5/§6
> 性质: 工程里程碑(非研究,无预注册)。`real_broker_orders = false`(永久)。

## 一、交付总览

| 交付物 | 状态 | 关键文件 |
|---|---|---|
| §3.1 推送重设计(变更触发,静默默认) | ✅ | `scripts/push_sleeve_advisory.py` + renderer |
| §3.2 分线镜像账本(R/Z/现金) | ✅ | `backend/portfolio/{mirror_ledger,z_ledger_io,lines}.py` |
| §3.3 LLM 对账闭环 | ✅ | `backend/portfolio/reconcile.py` + `scripts/reconcile_listener.py` |
| §3.4 账户面板 | ✅(CLI)/前端接线**推迟** | `scripts/account_view.py`(`--json` 即未来 API 形状) |
| 月度两本账偏差披露(计划书 §5⑤) | ✅ | `scripts/mirror_drift_report.py` + advisory history |
| §3.5 验收 | 测试绿 + codex 一轮已修;**owner 两演练待做** | 见 §六 |

工程判据:pytest 基线 7424 → **7476+ passed / 14 skipped(只增)**;ruff 全干净;
codex 一轮 review **5 P1 全修**(见 §五)。

## 二、§3.1 推送语义(生效后 owner 体验)

时效性问题已解除:cron(17:40)照跑、状态照写,但**只在四种时刻发消息**:

1. **调仓日且目标书有差异**——差异 = holdings(code+weight)+cash 的 canonical hash
   相对**上次送达**不同;非调仓日的裸 top-5 日内漂移(advisory 每日重算,无 buffer)
   **不触发**。调仓日 cron 失败次日自动补(锚定 `last_advised_rebalance` 指针,自愈)。
2. **前向状态变化**(ACCRUING/SURVIVING/KILLED)——任意日,文案带
   "⚠️ 前向状态变化: X → Y" 行;**KILLED 通告送达成功才落状态**(发送失败次日重试,
   不吞通告——MZ-1 教训);KILLED 送达后一切调仓/提醒事件**永久熄火**(codex P1)。
3. **执行提醒**——已送达的调仓建议若次日收盘仍无回报(对账闭环未清除
   `awaiting_report`),按最新收盘重算重推,文案带提醒行;owner 回一笔成交或
   「不跟」即停。
4. 打新日(MZ-1 已有,不变)。

每条 sleeve 建议尾部追加固定护栏行(MD-1 P-B NO_ADOPT 的落地形态):
**"纪律: 不补仓亏损股;不在无浮盈时做T;反常下跌先报我再动手。"**

状态文件 `data/factor_research/sleeve_push_state.json` 已用 2026-08-23 实发的书 seed,
**周一起无变更即静默**。旧 per-asof marker 保留(打新脚本复用其函数)。

## 三、§3.2 分线账本(MockBroker 绕开,Codex 同意)

**决策**:绕开 MockBroker/ManualTradeApplier(会拖入 BrokerEventStore/AuditStore/
nameplate 等封存机构,违反"不为封存机构造兼容层")。镜像只需"记录→回放→展示"
——owner 已在真实券商动手,系统只记账。

- `backend/portfolio/mirror_ledger.py`:append-only JSONL
  (`data/portfolio/mirror_ledger.jsonl`),行类型 fill / cash / adjust;
  按 `(executed_at, seq)` 生效时序回放为不可变 `MirrorBook`;
  **预写校验 = 把候选行并入后整本回放**(补录乱序也不可能写坏账本,codex P1);
  费用复用 `cost_calculator.calculate_cost`(万1.5 佣金 5 元地板/印花税/过户费,
  `apply_slippage_model=False`,BUY 含费均价,清仓重置成本);
  超卖 fail-closed(`MirrorDriftError` → 追问,不入账);
  现金可为负并披露"本金未申报"(owner 真实账户是唯一真相)。
- `ExternalExecutionEvent`:SELL 放开整百限制(真实账户存在零股),BUY 仍整百。
- Z 线不动:纯 IO 迁至 `backend/portfolio/z_ledger_io.py`(CLI re-export 兼容),
  读取侧聚合在 `backend/portfolio/lines.py`。

## 四、§3.3 对账闭环(LLM 只产结构化事件)

链路:`FeishuEventReceiver`(WS)→ `InboundGate`(决策群+owner open_id,fail-closed)
→ **一次 LLM 抽取**(agent `execution_reconciler`,deepseek-v4-flash,temperature 0,
thinking off;prompt 带当前建议书标的+镜像持仓做上下文)→ 确定性入账 →
**renderer 组回执**。监听器 = `scripts/reconcile_listener.py`(独立进程,无 uvicorn,
不复活旧 M4;文件持久化去重 `data/portfolio/reconcile_dedupe.json`,重启后仍认得
重投递,codex P1)。

抽取六出口(strict pydantic,不合 schema 一律降级追问):

| owner 说 | outcome | 系统动作 |
|---|---|---|
| "东方雨虹买了50手,成交11.2" | filled | 手→股(×100,单位不明**绝不猜**)→ UT- 事件入 R 线 → 清 awaiting → 已记录回执(含费用/现金变动) |
| "没买到" | unfilled | 回执;awaiting 保留 → 明晚重推 |
| "这周先不跟了" | no_action | 回执;awaiting 清除,不再提醒 |
| "002271我实际持有300股" | adjust_position | 修正镜像(记 adjust 行,生效时间回溯当日 0 点,之后重报的盘中卖出可正常入账,codex P1)→ 回执 |
| "中签的电科思仪卖了,赚了两万一千八百五" | z_record | 入 Z 线账本 → Z 线回执(LLM 正确解析中文数字金额,实测) |
| 读不懂/多笔/缺字段 | unclear | **一次**追问(缺什么点名什么),owner 重述即重启流程(无会话状态机) |

超卖 → "超过镜像持仓 X 股"追问 → owner 申报实际持仓(adjust)→ 重报成交,闭环可达。
真实 LLM 端到端冒烟已跑(临时账本):四类输入全部正确落账/回执。

## 五、codex 一轮 review(5 P1 全修,无遗留 P0/P1)

| # | Finding | 修复 |
|---|---|---|
| 1 | 超卖校验用当前持仓而非生效时序回放,补录场景可写入毁账本的行 | 预写整本回放校验(`_replay(rows+候选)`) |
| 2 | 监听器内存去重,重启+重投递会重复入账 | `FileEventDedupe`(7 天 TTL,文件持久化) |
| 3 | KILLED 后 stale awaiting/新调仓仍会推行动类消息 | KILLED 送达后 rebalance/reminder 全熄火,awaiting 清除(含静默路径兜底) |
| 4 | drift 修正流程承诺了但不可达 | 新 outcome `adjust_position` + `render_reconcile_adjust_ack`,修正行回溯当日 0 点 |
| 5 | 调仓退出标的无当日参考价,exit 卖出对着几周前的旧价算偏差 | advisory history 在剔除交付时记 `exits`(PIT 当日收盘,查不到记 None→按未覆盖披露,绝不用旧价) |

## 六、owner 验收演练(待 owner 到场)

**①推送演练**(dry-run 三情形已预跑通过,见下;然后实发一条 pilot):

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
# 三情形 dry-run(合成状态文件,不动真实状态):调仓差异→rebalance;
# 无变化→silent;KILLED→status_change(⚠️行)。实测输出见 MI-1 报告。
$PY/python scripts/push_sleeve_advisory.py --dry-run            # 今天:silent
$PY/python scripts/push_sleeve_advisory.py --dry-run --force    # 看全文
$PY/python scripts/push_sleeve_advisory.py --force --pilot      # 实发一条(试点横幅)
```

**②对账演练**(owner 在飞书说一句自然语言,看回执):

```bash
cd /home/ps/papers/QuantMind
eval "$(grep -E '^export (FEISHU_|DEEPSEEK_|DASHSCOPE_|TUSHARE_)' ~/.bashrc)"
FEISHU_INTERACTIVE_ENABLED=false \
  /home/ps/anaconda3/envs/zhanglan/bin/python scripts/reconcile_listener.py
# owner 在决策群发:"东方雨虹买了100股,成交11.2"(演练用小额,或先声明本金:
# 直接说 "R线本金10万" 目前不支持——本金申报走 CLI:见下)→ 收到【已记录】回执
# → python scripts/account_view.py 查看分线账本 → Ctrl-C 停监听。
```

R 线本金申报(一次性,建议演练前做):
`$PY/python -c "from backend.portfolio.mirror_ledger import *; from datetime import datetime,UTC; append_cash(DEFAULT_LEDGER, amount=<金额>, note='opening', recorded_at=datetime.now(UTC).isoformat())"`

**演练通过 = MI-1 完成。**

## 七、取舍与遗留(如实)

1. **前端 Portfolio.vue 未接线**(§3.4 允许):旧 Portfolio.vue 面向旧双线 API,
   接新账本需要动 FastAPI 旧 app(与"不复活 M4 运行时"冲突)。已交付 CLI +
   `--json`(即未来 API 的形状),前端接线留给下个工程单元。
2. 监听器为手动启动(演练即此形态);长期驻留可按 `docs/runbook/systemd-setup.md`
   加 service,留 owner 决定。
3. 现金本金申报走 CLI 一次性命令(见 §六);要不要把"申报本金"也做成对账闭环
   的一个 outcome,视 owner 使用体验再定(反过度防御:先别建)。
4. 科创板代码(688)不在锁定成本模型支持板块内,报此类成交会得到追问而非入账
   (R 线宇宙不含科创板,实际不会遇到;如实披露)。
5. 佣金按锁定的 BrokerConfig 默认(万1.5、5 元地板);与 owner 实际费率一致
   (M3 owner 四答已确认此口径)。
