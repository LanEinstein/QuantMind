# 接手 Prompt:MI-1 后续 — 前端账户面板接线 + 稳态运营

> 日期:2026-08-24 · 目标模型:Fable 5(主执行)+ Codex(决策讨论 + 唯一一轮 review)
> 授权:owner 2026-08-24「push;配 systemd;检查接手文档…我们在新session内继续推进」
> 上位:`KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md`(§6 里程碑表)
> 性质:**工程单元**(无预注册;codex 一轮 review 强制;docs-only 豁免)
> `real_broker_orders = false`(永久)。

---

## 〇、一句话任务

MI-1 已完成(两演练 owner 实测通过),动工里程碑全部收官。本单元把 MI-1 唯一
推迟项——**前端账户面板接线**——做完(最小只读 API + 面板显示分线账本),
同时守住稳态运营(cron/监听器/打新链路)。此后进入等待期:MC-1 认证裁决
(~2027-01/02,日历驱动)前**没有新的研究里程碑可动工**。

## 一、不可动摇的前提

1. 永禁真实券商程序化下单;系统只维护模拟盘,owner 手工操作后飞书回报。
2. 所有出站飞书文案必经 `MessageRenderer`;LLM 只产结构化抽取,不碰 wire 文案/风控/研究账。
3. 冻结物不许碰:SLV-1 spec(`c1d058c3…`)、前向注册与 kill-switch、18 张卡、Base v3、
   已封存研究(SLV-2/520/右侧波段/P-B 消融)。
4. 旧 M4 双线运行时(uvicorn `backend.main:app`、双线调度、mongo/redis 栈)**不复活**;
   前端面板的数据源按需最小(见 §3.1 的两个方案)。
5. 秘密只在 `~/.bashrc` 与 `/home/ps/.quantmind-reconcile.env`;gitleaks pre-commit,
   严禁 `--no-verify`;commit 落本地,push 须 owner 明示。
6. 回复 owner 中文;代码/注释/commit 英文;conventional commits。
7. **跨模型 review 至多一轮**(一轮 review + 一轮修复即止);反过度防御四禁。
8. **飞书建议消息内容规范(owner 2026-08-24,强制,适用一切建议类推送)**:
   含建议股数(本金×权重÷收盘,取整到手)+ 末尾通俗选股逻辑;涉及加/减/清仓时
   附买卖点逻辑。已实现于 `render_sleeve_advisory`,新推送线必须遵守。

## 二、当前系统状态(2026-08-24,HEAD 应为 origin 同步)

| 层 | 状态 |
|---|---|
| R:SLV-1 | 前向认证 2/8 期,无 breach,MDD 3.67%;cron 17:40(摄取→runner→**变更触发推送**);下一调仓日 ~20260908;裁决 ~2027-01/02 |
| 推送 | 变更触发/静默默认已上线:调仓差异 / 状态变化(KILLED 送达确认+此后熄火)/ 执行提醒(awaiting_report)/ 打新。护栏行+选股逻辑+买卖点逻辑随建议输出 |
| 镜像账本 | `data/portfolio/mirror_ledger.jsonl`:**现金 15 万(owner 已申报),无持仓,0 笔成交**(演练账本已归档 `mirror_ledger.drill-20260824.jsonl`) |
| 对账 | 监听器六出口(filled/unfilled/no_action/adjust_position/declare_capital/z_record/unclear)全部 owner 实测通过;systemd unit=`quantmind-reconcile`(见 §四) |
| Z:制度红利 | 打新提醒 cron 08:30;Z 账本空;破发 kill latch 正常 |
| D:防守 | P-B/卡4/卡9 以护栏文案落地(在每条 sleeve 建议尾部) |
| 测试 | 7490 passed / 14 skipped 基线(只许增);ruff 干净 |

## 三、本单元交付物

### 3.1 前端账户面板(主体)

需求:owner 在浏览器(127.0.0.1:9276)看到分线账本——R 线持仓(股数/含费成本)、
现金与是否申报本金、Z 线累计(按类型)、最近成交与修正记录、月度执行偏差行。
数据已有现成读取层:`backend/portfolio/lines.py::build_account_view`
(`scripts/account_view.py --json` 即完整数据形状)。

**两个实现方案(与 Codex 讨论一次后自决,单一推荐+一个备选)**:

- **方案 A(预期推荐):独立最小只读 API**。新建 `scripts/account_api.py`
  (或 `backend/portfolio/api.py`+启动脚本):FastAPI 单文件,只挂
  `GET /api/portfolio/lines`(内容=account_view --json 的形状,外加最近 N 行
  ledger 与 `mirror_drift_report.monthly_drift`),127.0.0.1 only,无 mongo/redis,
  无鉴权(本机+SSH tunnel 前提)。前端:改 `frontend/src/views/Portfolio.vue`
  或新建一个轻量 view + 路由,只读展示。**不要**把旧 backend/main.py 起起来。
- **方案 B(备选):纯静态**。cron/脚本定期把 account_view --json 写成
  `frontend/public/portfolio.json`,前端 fetch 静态文件。零服务;代价=非实时+
  前端与数据文件的部署耦合。

前端动了就要跑:`cd frontend && npm run type-check && npm run test -- --run && npm run build`,
build 后 codex+Playwright 体检闭环(memory `feedback_playwright_frontend_exam`)。

### 3.2 稳态运营检查(轻,顺手)

- 周一(20260824 起)确认 cron 推送**静默**(book 未变):`tail logs/sleeve_trial_daily.log`
  应见 "no push event … silent";
- 打新提醒照常;监听器 `systemctl status quantmind-reconcile` 应 active
  (若 owner 尚未执行 §四 的安装命令,提醒一次)。

### 3.3 可选(owner 提出才做)

- 「申报本金」等对账出口的体验打磨;CB 双低条件候选(**中位价<115 才立项**,
  立项=新研究里程碑,须 owner 点头+预注册);MC-1 裁决报告等日历到期。

## 四、systemd 监听器(若 owner 尚未装)

unit 与安装器已备好(`deploy/quantmind-reconcile.service` +
`scripts/install_reconcile_service.sh`;runbook §10)。env 文件已生成
(`/home/ps/.quantmind-reconcile.env`,9 vars,chmod 600)。owner 一条命令:

```bash
sudo bash scripts/install_reconcile_service.sh --enable --start
# 安装器会先 SIGTERM 手动启动的监听器(避免双实例双回复),再起服务
systemctl status quantmind-reconcile   # 期望 active (running)
```

## 五、开工检查(实测命令与预期)

```bash
cd /home/ps/papers/QuantMind
git status -sb          # 与 origin/agent/m2-evidence-reconstruction 同步
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q   # ≥7490 passed / 14 skipped
$PY/ruff check backend/ scripts/ tests/          # All checks passed!
$PY/python scripts/account_view.py               # R线: 现金 150,000.00,无持仓;Z线 0
$PY/python scripts/push_sleeve_advisory.py --dry-run   # decision: event=silent(book 未变时)
pgrep -f "[r]econcile_listener.py" || systemctl is-active quantmind-reconcile
# 两者其一存活;都无 → 按 §四 提醒 owner 或临时手动拉起(命令见 worklog/结果doc)
tail -1 data/yeren_research/worklog.jsonl | $PY/python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"
# post-MI1-handoff | POST-MI1-frontend-panel
```

## 六、资产地图(本单元相关)

| 路径 | 用途 |
|---|---|
| `backend/portfolio/{mirror_ledger,z_ledger_io,lines,reconcile,sleeve_push_state}.py` | 分线账本+对账核心(lines.build_account_view=面板数据源) |
| `scripts/account_view.py` | CLI 视图;`--json` 即 API 应返回的形状 |
| `scripts/mirror_drift_report.py` | 月度执行偏差(面板可展示) |
| `scripts/push_sleeve_advisory.py` | 变更触发推送(decide/augment_holdings/建议股数) |
| `scripts/reconcile_listener.py` + `deploy/quantmind-reconcile.service` | 监听器+systemd |
| `frontend/src/views/` + `frontend/src/router/` | Vue3 前端(:9276;旧 view 绑旧双线 API,谨慎复用) |
| `docs/research/mi1-integration-results-2026-08-23.md` | MI-1 全结果(§六演练记录) |
| `docs/runbook/systemd-setup.md` §10 | 监听器 unit 运维 |

## 七、明确不该做的

1. 不写任何新策略/信号;不重跑封存研究;不动 SLV-1 冻结物与前向账。
2. 不复活旧 M4 runtime(`backend/main.py` uvicorn、双线调度、mongo/redis 依赖)——
   面板 API 必须独立最小。
3. 不做鉴权/多用户/部署框架(127.0.0.1+SSH tunnel 已是边界;反过度防御)。
4. 不 push(须 owner 明示);不动 `data/marketdata_pit/`、`data/yeren_corpus/`。
5. MC-1 未到期不预写裁决;CB 双低未过条件(中位价<115)不立项。

## 八、坑(实测过的)

- `pgrep -f` 会自匹配包含模式的命令行——用 `pgrep -f "[r]econcile_listener.py"`。
- advisory 是 asof 当日裸 top-5 重算(无 buffer),内容 hash 非调仓日会漂移;
  推送判定锚 `last_advised_rebalance` 指针,别改回只看 hash。
- 镜像修正(adjust)必须"此刻生效"(effective_at=now);回溯生效会撞当日已入账成交
  (2026-08-24 owner 演练实翻过车)。
- 跑测试必须 `FEISHU_INTERACTIVE_ENABLED=false`;长任务 setsid+轮询;
  codex exec 后台必须 `</dev/null`;`codex review --base <branch>` 不能带 prompt 参数。
- 飞书群消息必须 @机器人 才会推给系统(应用无群全量消息权限)。
- 前端 npm 命令在 `frontend/` 下;type-check → test → build 顺序。

## 九、开工口令

> 按 `KickoffPrompts/POST-MI1-frontend-panel-and-steady-state-handoff-2026-08-24.md` 开工。
> 先 §五 开工检查;§3.1 方案 A/B 与 Codex 讨论一次后自决;工程完成后 codex 一轮
> review + 修复;前端动了跑全套构建+Playwright 体检。commit 落本地,push 等 owner 明示。
