# 防御 Sleeve 前向试运营启动记录(SLV-1 Step 2,2026-07-12)

- **性质**:SLV-1 预注册前向存活验证的**启动记录 + 每日运行手册**(研究侧 + display-only 飞书推送)。
- **触发**:owner 2026-07-12 指令「向着可稳定盈利复利的选股+买卖点系统推进,争取明天(2026-07-13 周一)开盘之前试运营」= SLV-1 owner-gate 开门(增量摄取 + 前向启动 + 飞书推送授权)。
- **上位**:`defensive-sleeve-spec-and-forward-validation-plan-2026-07-04.md` §3(预注册协议)+ `qgr-certification-rearch-amendment-2026-07-04`(认证=前向存活,非显著性)+ SLV-0 科学门 PASS(`defensive-sleeve-science-gate-results-2026-07-04.md`)。
- **红线**:永禁真实下单;推送 = display-only 研究建议(经 `renderer.py`,无 QM- id、无执行动词,不可被回报 parser 解析);owner 人工执行;零 LLM;PIT 字节存档;sealed test 永不读。

---

## 1. 本次启动做了什么(2026-07-12 晚)

| 步骤 | 内容 | 产物 |
|---|---|---|
| ① 增量摄取 | `ingest_historical_pit.py --start 20260619 --end 20260710`(幂等,15 个交易日 × 4 端点 = 60 快照,failed=0;20260619 为节假日) | `data/marketdata_pit/`(日历末 20260618 → **20260710**) |
| ② 前向 runner | 新建 `scripts/factor_research/defensive_sleeve_forward.py`(存活式 kill-switch;详 §2) | 状态 JSON + 注册 JSON(见 ③④) |
| ③ 预注册冻结 | 首跑 `--register` 写入并此后 fail-closed 校验 | `data/factor_research/defensive_sleeve_forward_registration.json`:spec_hash `c1d058c3…`、test_end `20260612`、**forward_start `20260615`**、baseline `sleeve_eq_5`、kill-switch {mdd 0.25 / bear −0.05 / 连6期 / min 8 期} |
| ④ 首次前向读 | 19 个前向交易日(20260615..20260710) | `data/factor_research/defensive_sleeve_forward_status.json`:**status=ACCRUING**(0/8 期)、无 breach、实现 MDD **3.70%**(eq_5 无 buffer 9.28% —— buffer 生效)、schedule 首期 20260615、**下一 rebalance = 20260713(明天)** |
| ⑤ 明日建议 | 基于 20260710 收盘的 dv_ratio top-5(防御宇宙 463 只) | 见 §3;已实况复核 5 票均在市、非 ST、20260710 未停牌 |
| ⑥ 飞书推送 | 新 renderer 方法 `render_sleeve_advisory` + `scripts/push_sleeve_advisory.py` → FEISHU_DECISION_CHAT_ID | display-only digest(非指令声明 + kill-switch 披露) |
| ⑦ 每日 pipeline | `scripts/sleeve_trial_daily.sh`(摄取→runner→推送) | owner 自装 cron(§4) |

## 2. 前向 runner 设计(`defensive_sleeve_forward.py`)

- **窗口**:`forward_trade_dates(root, test_end=20260612)` — 严格 > test_end 的处子数据;rebalance 日全部断言 > test_end(fail-closed);盘前 100 td 仅作 trailing 特征历史(beta 60+1 / vol 21 / 流动性 20,`build_forward_panel_r4` 先例,非泄漏)。
- **财报期 clamp**:`last_period_date = min(前向末日, 四类报表端点店内最新期)`(当前 20260331;Q2 未披露期无快照属 PIT 正常,状态 JSON `statement_periods_through` 披露)。
- **注册 fail-closed**:spec_hash / kill-switch / test_end 任一漂移即 abort(预注册作废保护)。
- **kill-switch(spec 原文,不重解释)**:实现 MDD>0.25;前向熊市(csi300_hold 期收益 `_classify_regimes`)累计 <−0.05;**连 6 个完整期跑输 naive 宇宙内 dv-top5 基线(eq_5 满仓,raw 口径)**;<8 完整期 = ACCRUING 不裁决,但 **breach 随时可杀**(KILLED 优先于 ACCRUING)。
- **收益只披露不检验**(无 t 检验;认证=存活)。
- **advisory**:最新前向收盘日的 gates + dv_ratio top-5 等权(8%×5 + 60% 现金);advisory 日不注入回测账(schedule 之外的日期不产生 off-cadence rebalance)。
- 单测 14 项:注册漂移三连、kill-switch 三类 breach、trailing streak 语义、ACCRUING/SURVIVING/KILLED 转移、advisory top-5 构成、fail-closed 空宇宙。

## 3. 明日(2026-07-13 周一)目标持仓建议(基于 20260710 收盘)

| 代码 | 名称 | 股息率 dv_ratio | 20260710 收盘 | 目标权重 |
|---|---|---:|---:|---:|
| 002271.SZ | 东方雨虹 | 16.09 | 11.50 | 8% |
| 002304.SZ | 洋河股份 | 12.02 | 38.65 | 8% |
| 600256.SH | 广汇能源 | 11.89 | 5.23 | 8% |
| 601919.SH | 中远海控 | 11.42 | 13.98 | 8% |
| 000858.SZ | 五粮液 | 11.29 | 73.69 | 8% |
| — | **现金 buffer** | | | **60%** |

**口径披露**:正式前向账的第 2 期 rebalance 特征日 = 20260713 收盘;本建议以 20260710 收盘特征近似(dv_ratio 慢因子,月度节奏下漂移极小);每日 17:40 pipeline 会以当日收盘重算并推送,偏差如实反映在下一条推送。执行为 owner 人工决策(P-A 确认门适用);¥1万 执行可行性资本(整手约束下 8% 权重≈¥800/槽 → 广汇能源 100 股≈¥523 可行,五粮液 100 股≈¥7369 超槽 —— **owner 按 P-E 置信集中原则自行取舍**,或以更大模拟资本记账;研究账以 ¥100万 记)。

## 4. 每日试运营协议(owner 操作)

```bash
# 一次性:安装每日 17:40(周一至五)pipeline(Claude 无权限装 cron,需 owner 亲自)
(crontab -l 2>/dev/null; \
 echo '40 17 * * 1-5 /home/ps/papers/QuantMind/scripts/sleeve_trial_daily.sh >> /home/ps/papers/QuantMind/logs/sleeve_trial_daily.log 2>&1') | crontab -

# 手动跑一次(任意时刻,幂等;当日收盘数据 ~16:30 后可用)
bash /home/ps/papers/QuantMind/scripts/sleeve_trial_daily.sh
```

- 推送 dedup:同一 as-of 日重复跑只发一条(Feishu uuid 1h 窗口)。
- **KILLED 处置**:推送/状态出现 `KILLED` = 预注册 kill-switch breach → 停止执行建议、上报 owner、结果如实落账(FAIL 报 FAIL);绝不调阈值续跑。
- 每月(每 20 td)一次真 rebalance;期间推送内容变化很小属正常(dv_ratio 慢腿)。
- 财报季(7 月末起 Q2 密集披露)需增量摄取 `fina_indicator_vip` 等报表端点(`ingest_round2_data.py --phase round2/round3`),否则 ROE/GPM 门用 Q1 值(PIT 合法但趋陈旧;runner 状态 JSON `statement_periods_through` 可见)。

## 5. 与 go-live 门的关系

本试运营 = **§3 Step 2 前向 shadow 的研究侧起跑**(登记起点 + kill-switch 监控 + display-only 推送)。它**不是** P0-6 go-live:45 日滚动生产管线 shadow replay(SR-001)+ owner 人工 pin 仍在后面;sim/backend 运行路径不因本次改动(runner/推送均研究侧脚本 + renderer 新增纯方法)。分析师 tilt 仍 OFF。

## 6. 涉及文件

- `scripts/factor_research/defensive_sleeve_forward.py` + `tests/factor_research/test_defensive_sleeve_forward.py`(新)
- `backend/integrations/feishu/renderer.py`(新增 `render_sleeve_advisory`)+ `tests/test_feishu_renderer.py`(新增 `TestSleeveAdvisory` 6 项)
- `scripts/push_sleeve_advisory.py`、`scripts/sleeve_trial_daily.sh`(新)
- `data/factor_research/defensive_sleeve_forward_{registration,status}.json`(gitignored 数据产物)
