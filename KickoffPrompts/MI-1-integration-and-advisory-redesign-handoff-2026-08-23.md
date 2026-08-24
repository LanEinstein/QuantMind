# 接手 Prompt:MI-1 组合整合 + 推送重设计 + 对账闭环

> 日期:2026-08-23 · 目标模型:Fable 5(主执行)+ Codex(决策讨论 + 唯一一轮正式 review)
> 授权:owner 2026-08-23「push;写接手prompt,我们在干净上下文继续推进 MI-1」
> 上位:`KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md`(现行行动纲领,§5/§6)
> 性质:**工程里程碑**(非研究;无预注册;codex 一轮 review 强制)
> `real_broker_orders = false`(永久)。

---

## 〇、一句话任务

把三层收益来源(R=SLV-1 sleeve / Z=打新+现金地板 / D=行为护栏)接到**一本分线记账的模拟盘**上,
把飞书推送从"每日必发"改成**变更触发、静默默认**,把 owner 自由文本回报 → 模拟盘更新的
**对账闭环**跑通,末尾与 owner 做一次推送演练+对账演练。

**⚠️ 有一件事有时效性**:sleeve cron(owner 已装,每交易日 17:40)从下周一起会**每个交易日推一条**
sleeve 建议(现行去重按 as-of 日,as-of 每日推进)。这与"静默默认"直接冲突,
是 MI-1 第一件要改的事——owner 会先被日报轰炸,改完才安静。

---

## 一、不可动摇的前提

1. **永禁真实券商程序化下单**;系统只维护模拟盘,owner 在券商 App 手工操作后经飞书告知。
2. 系统形态:前端(Vue3,:9276,127.0.0.1)=查看通道;飞书=行动通道,只在需 owner 动手时说话。
3. 所有出站飞书文案必须经 `MessageRenderer`(零 LLM 组 wire 文案);display-only 建议无 QM- id、
   无执行动词,不可被回报 parser 解析。
4. **冻结物不许碰**:SLV-1 spec(hash `c1d058c3…`)、前向注册与 kill-switch、18 张卡、Base v3、
   已封存研究(SLV-2 FAIL、520、右侧波段、P-B 消融 NO_ADOPT)。
5. 秘密只在 `~/.bashrc`;gitleaks pre-commit,严禁 `--no-verify`;commit 落本地,push 须 owner 明示。
6. 回复 owner 中文;代码/注释/commit 英文;conventional commits。

## 二、当前系统状态(2026-08-23 夜,全部已 push 至 `origin/agent/m2-evidence-reconstruction`,HEAD=`76cd10e`)

| 层 | 状态 |
|---|---|
| R:SLV-1 | 前向认证中 **2/8 期**,无 breach,MDD 3.67%;cron 17:40 自动跑(摄取→runner→推送);裁决约 2027-01/02。**SLV-2 科学门 FAIL 封存**(MDD 33.5%/不胜随机),R 层单腿 |
| Z:制度红利 | 协议已生效(`docs/research/institutional-rent-protocol-2026-08-23.md`);打新提醒 cron 08:30 owner 已装(无可申购日静默;破发 kill ≥4/20 自动停发);Z 线账本=`data/institutional_rent/z_ledger.jsonl`(CLI 手工代录,MI-1 并入对账) |
| D:防守 | **P-B 消融 NO_ADOPT 封存**(MDD −4.28pp 但净盈只剩 21.3%;churn 45%)→ P-B/卡4/卡9 以**行为护栏文案**落地(本里程碑的一部分) |
| 账本 | 试验账本 n_trials=2423;worklog 91 行,resume_from=`MD-1-no-adopt-sealed-next-MI-1-owner-nod` |

## 三、MI-1 交付物(计划书 §6 行,细化)

### 3.1 推送重设计(先做,有时效)

现状:`scripts/push_sleeve_advisory.py` 按 as-of 日去重 → cron 下每交易日一条。
`scripts/push_ipo_reminder.py`(MZ-1 新)已是正确形态:无事静默、按日去重、kill 通告送达确认
(`mark_notice_delivered` 模式)——**以它为范本改 sleeve 推送**。

目标语义(计划书 §5④,owner 已批):只在五种时刻推送:
(a) 调仓日且目标书与当前模拟盘有差异;(b) kill-switch 状态变化(含 KILLED,须送达确认不许吞);
(c) 止损/护栏事件(本期只有护栏文案,无机械止损);(d) 打新日(已实现);(e) 对账追问。
其余日子 pipeline 照跑、状态照写、**不发消息**。
实现建议:去重键从 as-of 改为 **(内容哈希, 事件类型)**——内容=holdings(code+weight)+cash+status
的 canonical JSON hash;调仓日判定用 status JSON 里的 `schedule_rebalances`;
KILLED 通告复用打新链路的"送达后才消耗"模式。
文案追加**固定护栏行**(P-B/卡4/卡9,MD-1 结果的落地形态):
"纪律:不补仓亏损股;不在无浮盈时做T;反常下跌先报我再动手。"(措辞可润色,语义保持)。

### 3.2 统一模拟盘分线账本

- 现有资产:`backend/broker/mock_broker.py`(MockBroker,IBroker 实现)、
  `backend/models/manual_trade.py`(`ExternalExecutionEvent` 不可变 append-only,
  `ManualTradeReason` 枚举)、`backend/broker/persistence/`。
- 要求:R 线(sleeve 镜像持仓)/ Z 线(打新中签与卖出、现金收益)/ 现金 buffer 分线可见;
  owner 真实账户是唯一真相,系统假设价只留研究侧;
  两本账(研究侧 vs 镜像)偏差月度披露一行(执行时点偏差的续测,计划书 §5⑤)。
- **先盘再建**:MockBroker 是旧双线时代资产,进场先读
  `backend/broker/{interface,mock_broker,models}.py` + `tests/test_mock_broker*.py`,
  判断是复用还是绕开(判断句:反过度防御——旧机构里 14-check 风控/对账 ticket 等封存不用,
  别为兼容它们造适配层)。Z 线已有独立 JSONL,不必强行塞进 MockBroker,分线汇总层做在读取侧即可。

### 3.3 LLM 对账闭环

- 语义(计划书 §5⑤):owner 自由文本("买了 5000 股成交 12.3"/"没买"/"清了一半")→ LLM 解析 →
  按 **owner 实际成交价**落镜像账 → 回一条确认摘要(经 renderer);缺要素追问一次;
  次日收盘前无回报 → 默认未成交,当晚重算重推。理解不了就说理解不了,owner 再说一遍即可修正。
- 现有资产:飞书 WS 收=`backend/integrations/feishu/events.py`+`inbound_gate.py`;
  结构化回报 parser=`parser.py::parse_execution_report`(旧格式路径,display-only 建议永远
  parse 不出来——保持这个性质);确认/追问模板=`renderer.py::render_manual_trade_ack/
  render_execution_ack/render_clarification`(已存在!);LLM 路由=`backend/llm/`(router 已有)。
- 新的部分:自由文本 → `ExternalExecutionEvent` 的 LLM 抽取链(prompt+schema 校验+一次追问),
  接到镜像账本。**LLM 只产结构化事件,不产 wire 文案、不碰风控、不碰研究账。**

### 3.4 前端账户面板(可裁剪)

`frontend/src/views/Portfolio.vue` 等 15 个 view 已存在(旧双线时代)。MI-1 只要求:
账户面板能看到分线持仓/现金/Z 线累计——若旧 Portfolio.vue 接新账本代价小就接,
代价大就先出 API(FastAPI 已有骨架)留给下个单元,**如实说明取舍**。
前端动了就要跑 `npm run type-check && npm run test -- --run && npm run build`
+ 既有反馈:build 后 codex+Playwright 体检闭环(memory `feedback_playwright_frontend_exam`)。

### 3.5 验收(owner 门)

工程判据:pytest 全绿(基线 7424 passed/14 skipped,只许增)+ ruff 干净 + codex 一轮 review 修复完。
**演练判据(owner 参与)**:①推送演练——dry-run 展示"有变更才发"三种情形(调仓差异/无变化静默/
KILLED 通告),然后实发一条;②对账演练——owner 在飞书用自然语言报一笔假想成交,
系统解析、落账、回确认,owner 看摘要点头。演练通过 = MI-1 完成。

## 四、工作纪律(强制,全文见计划书 §7 与 CLAUDE.md)

- **codex 一轮 review**:本任务是工程,`codex review --uncommitted` 一轮 + 修一轮即止,
  P0/P1 必修,P2/P3 记录取舍;禁第二轮/复验循环。决策讨论(§7.3 格式:单一推荐+一个备选)
  与 review 是两回事,实质选择(如 MockBroker 复用 vs 绕开)建议先与 Codex 讨论一次。
- 反过度防御四禁;判断句:"这能检测到什么具体故障,我会因此做出什么不同的决定?"
- 跑测试必须 `FEISHU_INTERACTIVE_ENABLED=false`;长任务 `setsid` 全脱离+轮询
  (turn 边界会杀普通后台进程;Bash 工具单次超时上限 10 分钟,等待用循环分段)。
- codex exec 后台必须 `</dev/null`,输出重定向文件,阻塞式 waiter 等退出,别反复读半成品输出。

## 五、开工检查(实测命令与预期)

```bash
cd /home/ps/papers/QuantMind
git status -sb            # ## agent/m2-evidence-reconstruction...origin/... 同步
git log --oneline -3      # 76cd10e / f2d84cf / a9ea490
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q     # 期望 7424 passed / 14 skipped
$PY/ruff check backend/ scripts/ tests/            # All checks passed!
tail -1 data/yeren_research/worklog.jsonl | $PY/python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"
# MI-1-handoff-written | MI-1-integration-owner-approved
$PY/python -c "
import json; d=json.load(open('data/factor_research/defensive_sleeve_forward_status.json'))
print(d['status'], d['forward']['complete_periods'], d['forward']['end'])"
# ACCRUING 2 20260821(或更新——cron 每日推进)
crontab -l | grep -cE "sleeve_trial_daily|ipo_reminder"   # 2(两条 cron 都在)
tail -3 logs/sleeve_trial_daily.log                        # 最近交易日应有 done 行
```

## 六、资产地图(精确路径)

| 路径 | 用途 |
|---|---|
| `scripts/push_sleeve_advisory.py` / `scripts/sleeve_trial_daily.sh` | 现行 sleeve 推送(要改)+ 17:40 cron |
| `scripts/push_ipo_reminder.py` + `scripts/institutional_rent/` | **推送形态范本**(静默默认/送达确认/kill latch)+ Z 线账本 CLI |
| `backend/integrations/feishu/renderer.py` | 唯一文案出口;`render_sleeve_advisory`(:321)/`render_ipo_reminder`/`render_manual_trade_ack`(:871)/`render_execution_ack`(:791)/`render_clarification`(:749) |
| `backend/integrations/feishu/{events,inbound_gate,parser,client,dedupe}.py` | WS 收/入站门/旧回报 parser/发送 |
| `backend/broker/{interface,mock_broker,models,appliers}.py` + `persistence/` | MockBroker 模拟盘(先盘再建) |
| `backend/models/manual_trade.py` | `ExternalExecutionEvent`(frozen/strict/extra=forbid)+ ack 渲染已配套 |
| `backend/llm/` | LLM 路由(DeepSeek/Qwen/MiniMax key 在 ~/.bashrc) |
| `data/factor_research/defensive_sleeve_forward_status.json` | sleeve 状态+advisory(推送的数据源) |
| `data/institutional_rent/{z_ledger.jsonl,break_cache.json,break_kill_state.json,reminder_sent.json}` | Z 层运行态 |
| `frontend/src/views/` | 15 个 view(Portfolio.vue 等,旧双线时代) |
| `docs/research/institutional-rent-protocol-2026-08-23.md` | Z 层协议(§3 kill 规则/§5 账本口径) |
| `docs/research/pb-stop-ablation-results-2026-08-23.md` | P-B NO_ADOPT(护栏文案的依据) |
| `KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md` | 行动纲领(§5 建议设计六答=本里程碑的规格) |

## 七、明确不该做的

1. 不写任何新策略/信号代码;不重跑任何已封存研究(SLV-2、P-B 消融、520、右侧波段)。
2. 不动 SLV-1 冻结物与前向账;`defensive_sleeve_forward.py` 的状态 JSON 只读。
3. 不造真实券商路径;不把 LLM 接进 wire 文案或风控。
4. 不为封存的旧机构(14-check、对账 ticket、验收框架)造兼容层。
5. 不 push(须 owner 明示);不动 `data/marketdata_pit/`、`data/yeren_corpus/`。
6. 旧 M4 双线运行路径(uvicorn 常驻、双线调度)不要顺手"复活"——MI-1 只要账本+推送+对账三件事,
   服务化按需最小。

## 八、坑(实测过的)

- 飞书凭证在 `~/.bashrc` 交互 guard 之后,cron/脚本里用 grep 提取 export 行
  (见 `sleeve_trial_daily.sh` 头部),别 source 整个 bashrc。
- 飞书 uuid 服务端去重只有 1 小时窗,本地 sent-marker 才是真去重;
  KILLED/停发类一次性通告必须"送达成功才标记",否则发送失败会把通告吞掉
  (MZ-1 codex P1 教训,`break_monitor.mark_notice_delivered` 是现成模式)。
- websockets 版本 <14(飞书 WS);uvicorn 若要起服务,owner 自起最稳。
- pytest 不带 `FEISHU_INTERACTIVE_ENABLED=false` 会真连飞书。
- 前端在 `frontend/` 下 npm 命令;type-check 先于 test 先于 build。

## 九、开工口令

> 按 `KickoffPrompts/MI-1-integration-and-advisory-redesign-handoff-2026-08-23.md` 开工。
> 先 §五 开工检查;先做 §3.1 推送重设计(有时效);实质选择与 Codex 讨论后自决,非必要不问 owner;
> 工程完成后 codex 一轮 review + 修复;最后与 owner 做 §3.5 两个演练。
> commit 落本地,push 等 owner 明示。owner 只看最后的报告与演练。
