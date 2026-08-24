# 接手 Prompt:稳态等待期 — 运营守护 + 日历驱动事件

> 日期:2026-08-24 · 目标模型:Fable 5(主执行)+ Codex(仅工程任务时一轮 review)
> 授权:owner 2026-08-24「push;systemd 我已装好;写下一个接手文档」
> 上位:`KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md`(§6 里程碑表)
> 前一单元:`KickoffPrompts/POST-MI1-frontend-panel-and-steady-state-handoff-2026-08-24.md`(已完成,
> 结果=`docs/research/post-mi1-account-panel-results-2026-08-24.md`,commit `8d65ba6` 已 push)
> `real_broker_orders = false`(永久)。

---

## 〇、一句话任务

**动工里程碑已全部收官,系统进入等待期。** 本 session 没有新的研究/工程里程碑可开:
默认工作 = 稳态运营巡检(§二)+ 只在 owner 报告问题或日历到期(§三)时动手。
**没有事就报告"一切静默"并停止,不要制造工作。**

## 一、不可动摇的前提

1. 永禁真实券商程序化下单;系统只维护模拟盘,owner 手工操作后飞书回报。
2. 所有出站飞书文案必经 `MessageRenderer`;LLM 只产结构化抽取。
3. 冻结物不许碰:SLV-1 spec(`c1d058c3…`)、前向注册与 kill-switch、18 张卡、Base v3、
   已封存研究(SLV-2 / 520 / 右侧波段 / P-B 消融)。
4. 旧 M4 双线运行时(`backend.main:app`、双线调度、mongo/redis)**不复活**。
5. 秘密只在 `~/.bashrc` 与 `/home/ps/.quantmind-reconcile.env`;gitleaks pre-commit,严禁
   `--no-verify`;commit 落本地,push 须 owner 明示。
6. 回复 owner 中文;代码/注释/commit 英文;conventional commits。
7. 跨模型 review 至多一轮;反过度防御四禁;docs-only 任务豁免 review。
8. 飞书建议消息规范(建议股数 + 通俗选股逻辑 + 买卖点逻辑)——已实现于
   `render_sleeve_advisory`,任何新推送线必须遵守。
9. **等待期纪律**:不写新策略/信号,不重跑封存研究,不为想象中的需求预建功能,
   不预写 MC-1 裁决,CB 双低中位价 ≥115 不立项。

## 二、稳态运营(每次 session 的默认巡检)

```bash
cd /home/ps/papers/QuantMind
PY=/home/ps/anaconda3/envs/zhanglan/bin
git status -sb                                   # 应与 origin 同步(8d65ba6 起)
systemctl is-active quantmind-reconcile          # active(owner 2026-08-24 已装)
pgrep -fa "[r]econcile_listener.py"              # 恰好一个实例(systemd 起的);两个 = 双回复风险
tail -3 logs/sleeve_trial_daily.log              # 每交易日 17:40 一行;非调仓日应 "silent"
$PY/python scripts/push_sleeve_advisory.py --dry-run   # decision: event=silent(book 未变)
$PY/python scripts/account_view.py               # 与 owner 券商账户核对时用
tail -1 data/yeren_research/worklog.jsonl | $PY/python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"
# post-MI1-account-panel | POST-MI1-steady-state
```

巡检结论只有三种:
- **全静默** → 一句话报告,结束 session。
- **运营异常**(cron 缺行 / 监听器死 / 推送失败 / 账本回放报错)→ 定位修复,属工程小修,
  改代码则 codex 一轮 review;不改代码(如重启服务)直接处置并记 worklog。
- **owner 报告问题**(对账出口体验、推送文案、面板显示)→ 按 §四 做。

已知各定时任务(owner cron,`crontab -l` 可核):
| 任务 | 时间 | 脚本 | 行为 |
|---|---|---|---|
| sleeve 日线 | 交易日 17:40 | `scripts/sleeve_trial_daily.sh` | 摄取→runner→变更触发推送(默认静默) |
| 打新提醒 | 08:30 | `scripts/ipo_reminder_daily.sh` | 当日有新股/转债申购才推 |
| 对账监听 | 常驻 | `quantmind-reconcile.service` | owner 飞书自由文本→账本→确认摘要 |

关键日期:
- **下一 SLV-1 调仓日 ≈ 20260908**(20 交易日节奏):当日 17:40 应推送含建议股数的调仓建议;
  之后 owner 回报,监听器入账,面板 `/account-lines` 可查。**这是等待期第一个真实全链路事件**,
  如果 owner 反馈任何不顺,那就是本等待期唯一值得做的工程修补。
- 前向认证 8 期满 ≈ **2027-01/02** → MC-1(见 §三)。

## 三、日历驱动事件(到期才做)

### 3.1 MC-1:SLV-1 前向认证裁决(≈2027-01/02)

触发条件:`round4_forward_test` 状态达到 8 个 rebalance 期且无 breach(kill-switch 预注册:
mdd 0.25 / bear −0.05 / underperf 6 / min 8)。到期后:
1. 读前向账(`data/factor_research/` 下 sleeve forward 状态与 history),按预注册判据出裁决报告
   `docs/research/mc1-slv1-forward-certification-<date>.md`;**判据一字不改,不事后加减**。
2. PASS → 按计划书 §6 进入 owner 决策(是否加仓/是否升级实盘规模);FAIL → 封存,R 层无腿,
   回到「少亏」底线只留 Z 线与现金。
3. 期间任意日 KILLED → 推送已自动送达并熄火;session 只需确认 owner 已知并记录。

### 3.2 CB 双低条件候选(条件触发)

只在 **可转债市场双低中位价 < 115** 时立项(用 Tushare `cb_daily` 全市场当日算中位);
立项 = 新研究里程碑,须 owner 点头 + 预注册先行(体例照 `docs/research/preregistration-*.md`)。
不满足条件不写任何代码,巡检时**不必**每次去算——owner 提起时算一次即可。

## 四、可选(owner 提出才做)

- 对账出口体验打磨(追问措辞、确认摘要格式)——改文案走 renderer,改抽取走 reconcile 测试。
- 面板 `/account-lines` 增项(如按调仓期分组、持仓与最近建议并排)——仍走
  `scripts/account_api.py` 单文件,不复活旧运行时;前端动了要跑 type-check→test→build +
  Playwright 体检。
- 面板 API 常驻化(systemd unit)——目前按需手动起(runbook §10a);owner 觉得麻烦再做。
- codex 记录未修的 P2:`account_api.py` 单次响应三次读账本(毫秒级不一致,刷新自愈)——
  仅当 owner 真的看到过自相矛盾的页面才修。

## 五、资产地图

| 路径 | 用途 |
|---|---|
| `backend/portfolio/{mirror_ledger,z_ledger_io,lines,reconcile,sleeve_push_state}.py` | 分线账本 + 对账核心 |
| `scripts/account_api.py` / `scripts/account_view.py` | 面板 API(:8001)/ CLI 视图 |
| `frontend/src/views/AccountLines.vue`(+`api/accountLines.ts`,`utils/accountLines.ts`) | 面板页 `/account-lines` |
| `scripts/push_sleeve_advisory.py` | 变更触发推送(decide / augment_holdings / 建议股数) |
| `scripts/mirror_drift_report.py` | 月度执行偏差(面板已展示) |
| `scripts/reconcile_listener.py` + `deploy/quantmind-reconcile.service` | 监听器 + systemd |
| `docs/runbook/systemd-setup.md` §10 / §10a | 监听器 unit / 面板 API 启动法 |
| `docs/research/mi1-integration-results-2026-08-23.md` | MI-1 全结果 |
| `docs/research/post-mi1-account-panel-results-2026-08-24.md` | 面板单元结果(含 codex P2 登记) |
| `KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md` | 计划书(§6 里程碑表) |

## 六、坑(实测过的,沿用)

- `pgrep -f` 自匹配 → 用 `pgrep -f "[r]econcile_listener.py"`。
- 推送判定锚 `last_advised_rebalance` 指针,不要改回只看内容 hash(非调仓日裸 top-5 会漂移)。
- 镜像修正(adjust)必须此刻生效(effective_at=now)。
- 跑测试必须 `FEISHU_INTERACTIVE_ENABLED=false`;**起着 vite preview / account_api 时跑全套
  pytest 会唤醒旧 Playwright e2e 出 6 个环境性失败——先停服务再跑**。
- codex exec 后台必须 `</dev/null`;`codex review --uncommitted` 不能带 prompt。
- 飞书群消息必须 @机器人 才会推给系统。
- 前端 npm 命令在 `frontend/` 下;type-check → test → build 顺序;X-022 页面数量锁现为 15,
  再加页面需同步改 `tests/test_x_022_frontend_page_lock.py` 并在 commit 说明授权。
- 本机沙箱分类器可能拦 `systemctl`/`pgrep` 组合命令——拆成单条重试,仍拦则请 owner
  用 `! <command>` 在会话里跑。

## 七、开工口令

> 按 `KickoffPrompts/STEADY-STATE-waiting-period-handoff-2026-08-24.md` 开工。
> 先 §二 巡检;全静默则一句话报告即止;运营异常或 owner 报告问题才动手;
> MC-1 / CB 双低只在 §三 条件到期时做。commit 落本地,push 等 owner 明示。
