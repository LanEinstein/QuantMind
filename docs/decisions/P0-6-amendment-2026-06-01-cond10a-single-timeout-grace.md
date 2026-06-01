# P0-6 修订 — 2026-06-01 cond10a live-probe 单次瞬态超时宽限(low-volume 早晨假阳性根治)

> **修订基准**: [P0-6 验收 — simulation_auto 45 交易日滚动 + 5 稳定性 + 3 策略硬门槛](./P0-6-acceptance-rolling-window-stability-strategy-gates.md)
> **关联**: P0-6-amendment-2026-05-25(PILOT 11 条最小集 + `can_switch_to_feishu_on("pilot")`)/ **P0-6-amendment-2026-05-29(cond9/cond10a/cond10b 三 live-probe 真实接通)** — 本 amendment 直接精化其 §2 cond10a 的比率判定 / P0-10(LLM 单调用 30s + 0 重试)
> **修订日期**: 2026-06-01(周一 09:35 MVP 完整真启,首次以 `FEISHU_INTERACTIVE_ENABLED=true` 走完整 interactive backend 启动暴露)
> **决策人**: owner(2026-06-01 本 session 拍板:正式修复 cond10a 比率判定 + 清零当日计数器 + 今天就启动)

## 0. 触发(真启才暴露的 live-probe 设计缺陷)

2026-06-01 周一 09:30,owner 要求重启完整 MVP 等 09:35 cron 真启。停掉旧 sim backend 后以 go-live env(`FEISHU_INTERACTIVE_ENABLED=true` / `QUANTMIND_PROD_RUN=1` / `QUANTMIND_FEISHU_TIER=pilot`)真启,PILOT acceptance gate 在 async live-probe 复检阶段 **fail-closed `SystemExit`**,**唯一**未达成条件:

```
SystemExit: ... pilot acceptance gate did not pass (unmet: cond10a:llm_timeout_rate_above_ceiling)
```

其余 10 条全过(cond1 SIM 账户 / cond2 owner 授权 valid / cond8 对账无 OPEN / cond9 数据 canary / cond10b cost_guard / 6 项 manifest 签字)。直接查 Redis 定位根因:

| 项 | 值(UTC 2026-06-01) |
|---|---|
| `llm:calls:2026-06-01` | 18 |
| `llm:timeouts:2026-06-01` | 1 |
| rate | **1/18 = 5.56% > 5% ceiling** |

这 18 次调用是**昨夜至今旧 sim backend 的分析 cron 跑出来的**,1 次是瞬态超时(17/18 成功 = 94.4% 健康)。**LLM 本身完全健康**,纯粹是**小样本把单次瞬态超时的比率顶过了 5% 红线**。

### 0.1 这是 2026-05-29 cond10a 设计的自然后果,不是新 bug 类别

P0-6-amendment-2026-05-29 §2.1 把 cond10a 定义为 `rate = timeouts / max(calls, 1) ≤ 0.05`,并写明"冷启动 0 调用 → 0/1 = 0 ≤ 0.05 → MET(无超时证据即放行)"。其**隐含假设是 go-live 启动时计数器接近冷启动**。但 `llm:{calls,timeouts}:{utc_date}` 是 **UTC 当日计数器、跨进程共享**——go-live 进程启动时,当日计数器已被同机 sim backend 的调用填充。于是在 **low-volume 早晨**(分母只有十几),**任一单次瞬态超时**(`1/n, n<20`)都会让 `1/n > 5%`,把 gate 永久卡死:gate 失败 → backend 不启动 → 不再产生新调用拉大分母 → 死锁。每个低量早晨真启都会撞上。

## 1. 决策:cond10a live-probe 加"单次瞬态超时宽限",**45 日 acceptance 5% 阈值不动**

### 1.1 语义
cond10a(**仅** PILOT live 当日探针)MET 判定改为:

```
healthy = (timeouts <= 1) OR (timeouts / max(calls, 1) <= ceiling)   # ceiling = 0.05 不变
```

口径 = **单次瞬态超时永不触发 gate;两次及以上才按 5% 比率裁决**。等价于"允许 `max(1, ceiling*calls)` 次超时"——`calls ≤ 20` 时恒允许 1 次,`calls > 20` 时即标准 5% 比率(40 次允许 2 次,以此类推)。

### 1.2 为何选"单次宽限"而非"最小样本下限"
候选过两个方案:
- **最小样本下限**(`calls < N` 一律放行):会让 `5/8 = 62%`、`3/5 = 60%` 这类**灾难性小样本**也漏过 → gate 对"LLM 启动期就系统性宕机"产生**安全盲区** ❌。
- **单次瞬态超时宽限**(本决策):`1/18`✅放行(根治)、`1/20=5%`✅、`2/20=10%`❌拦、`5/5=100%`❌拦、`3/5=60%`❌拦 → **既根治假阳性,又保留对灾难性失败的检测**(灾难性失败必然 ≥2 次超时且比率高)✅。

故选单次宽限:它是两方案里**严格更安全**的一个,且最小改动(只改 1 个现有测试 + 加回归测试)。

### 1.3 为何不弱化安全
- **`ceiling = 0.05` 数值不变**;45 日 acceptance 报告的 `llm_timeout_rate ≤ 5%` 稳定性门槛(P0-6 §2 红线 5)**完全不动**——本 amendment 只精化 PILOT **live 当日**探针在小分母下的判定,不碰大样本 acceptance 口径。
- 单次宽限不创造灾难盲区:LLM 系统性宕机会瞬间累积多次超时(`≥2` 且高比率)→ 仍被 cond10a 拦;且 gate 之外另有实时分层防御——LLM 单调用 30s + 0 重试 → 必经 Agent 失败降级 HOLD(P0-10)、LLM 全停 1h 触发 P0-6 系统级中断(§2.2)。cond10a 是**稳定性指标门**,稳定性指标本就只在有意义样本量下成立。
- gate 永不可绕过的契约不变:`FEISHU_INTERACTIVE_ENABLED` 只选 tier 不改 verdict;`_safe_await` fail-closed(Redis None / 读失败仍 → 未达成)不变。

### 1.4 配套(owner 2026-06-01 拍板,本次一次性):清零当日计数器 = go-live 生命周期卫生
切换到 feishu_interactive 是**账户生命周期事件**(§2.1:归档 + MockBroker reset + 飞书初始化对账)。其语义一致延伸:**go-live 启动时清零当日 `llm:{calls,timeouts}:{utc_date}` sim 期遗留计数**,让 go-live 的 LLM 健康从本生命周期 0 开始度量。这是**运维卫生**而非绕过 gate(有了 §1.1 的单次宽限,即便不清零 `1/18` 也已放行;清零只是让 go-live 计数干净起步)。一次性手动操作,记录在 SESSION_LOG;**不**写进自动启动逻辑(避免每次重启静默重置遥测掩盖真实超时趋势)。

## 2. 落地

- `backend/services/pilot_readiness.py` 新增纯函数 `is_llm_timeout_rate_acceptable(timeouts, calls, *, ceiling) -> bool`(零 LLM import,纯 int/float 逻辑,模块本就 import-clean of LLM 栈)。
- `backend/main.py::_llm_timeout_ok()`:`return is_llm_timeout_rate_acceptable(timeouts, calls, ceiling=_PILOT_LLM_TIMEOUT_CEILING)`(替换 `(t/max(c,1)) <= 0.05` 内联式),注释更新说明单次宽限。`_PILOT_LLM_TIMEOUT_CEILING = 0.05` 常量不变。
- 测试:`tests/test_pilot_readiness.py` 加 `is_llm_timeout_rate_acceptable` 纯函数单测(冷启动 / 单次宽限各分母 / 边界 / 灾难性小样本拦截 / 大样本超阈拦截);`tests/test_pilot_live_probes.py` 更新 `test_cond10a_unmet_above_ceiling`(改用 `≥2` 超时表达"超阈未达成"语义)+ 新增 `test_cond10a_single_transient_timeout_grace`(`1/18` 回归 → MET)+ `test_cond10a_catastrophic_small_sample_unmet`(`5/5` → UNMET,守灾难盲区)。

## 3. 不变量(本 amendment 不触碰)

- `ceiling = 0.05`、45 日 acceptance `llm_timeout_rate ≤ 5%` 稳定性门槛、PILOT 11 条结构、`can_switch_to_feishu_on` / `PilotReadinessProbe.evaluate` / `_safe_await` fail-closed 契约、6 manifest ledger、其余 live-probe(cond1/2/8/9/10b)全不变。
- gate 永不可绕过;`FEISHU_INTERACTIVE_ENABLED` 只选 tier。
- LLM 严禁参与验收路径:`is_llm_timeout_rate_acceptable` 是确定性纯整数/比率逻辑,零 LLM 参与。
- 计数器 UTC date 基 + `_utc_date_str()` 单一真相源、router best-effort 计数(fail-open infra glitch)不变。
