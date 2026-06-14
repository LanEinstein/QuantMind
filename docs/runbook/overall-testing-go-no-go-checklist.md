# 整体测试 Go / No-Go 检查单

> **用途**:所有纯开发 Phase(K–N MVP + 存量 O…AD + v3 V/W/Y/Z + T)已全部
> 交付,项目进入「整体测试」阶段(owner 锁定『先完整开发,后整体测试』)。
> 本检查单是 owner 启动每条测试路径前的单页决策辅助 —— 列出每条路径的硬门、
> 需 owner 亲自做的动作、以及当前已知的接线缺口。
>
> 维护时间:2026-06-14(readiness 收口 session)。权威任务状态仍以
> [`../plan.html`](../plan.html) 的 `TASKS` + `SESSION_LOG` 为准;本文档是
> 操作视角的汇编,不替代 SSoT。

## 0. 开发完成度基线(已验证)

- ✅ **纯开发 Phase 全部 done**:Phase O(MiroFish 核心)+ Phase T(交易员与
  全栈)是仅剩的纯开发 Phase,均已交付(SESSION_LOG #83 / #84)。
- ✅ **全量回归绿**:`5774 passed / 14 skipped / coverage 90.47%`
  (2026-06-14 在 owner 生产 env 下复跑;与 #84 基线一致)。
- ✅ **本地门禁绿**:ruff + redline-check 全过。
- ✅ **git 同步**:`main` 与 `origin/main` 在 `51d68ee`;工作区仅 `M CLAUDE.md`
  (owner 在途修改,不动)+ 本 readiness session 的 docs/测试改动。

> **测试隔离修复(本 session)**:owner shell 已预设完整生产 env
> (`FEISHU_INTERACTIVE_ENABLED=true` / `QUANTMIND_FEISHU_TIER=pilot` /
> `QUANTMIND_PROD_RUN=1` / `QUANTMIND_OWNER_PROD_AUTHORIZATION` / 决策群 +
> owner allowlist / 5 飞书凭证 / TUSHARE_TOKEN)。这些 ambient export 曾让 3 个
> orchestration 测试误解析为 feishu_interactive 而 fail(非代码回归)。已在
> `tests/conftest.py` 加 autouse fixture 清掉 owner-prod runtime 变量,使套件
> hermetic;想测 interactive/pilot/prod 的用例自己 `setenv` 覆盖。

## 1. ⚠️ 启动前必做:刷新 owner 生产授权

`QUANTMIND_OWNER_PROD_AUTHORIZATION` 当前 `granted_date=2026-05-29`,**已 16 天 >
7 天硬过期**。J-007 gate 在 boot 期 fail-fast,任何 `QUANTMIND_PROD_RUN=1` 的启动
都会以 `OwnerProdAuthorizationError` SystemExit。

**owner 动作**(任一 prod/long-run 路径的共同前置):
```bash
# 把 ~/.bashrc(或 /home/ps/.quantmind.env)里的日期改成今天
export QUANTMIND_OWNER_PROD_AUTHORIZATION="<owner>:20260614"
```
> 这是硬门,不可伪造、不可绕过(P0-6 §2 红线 5 的二级门)。Claude 严禁代设。

## 2. 各测试路径的 Go / No-Go

按依赖与风险从「最就绪」到「最重」排列。每条都需 owner 授权或跑真实系统,
**Claude 不能自主启动任何一条**。

### 2.1 U-E5(B) — 端到端双线真发真回填【已演示,SSoT 记账待收口】

> ⚠️ **不要按旧 plan.html `U-E5` 任务 notes(#50,2026-05-27)行事** —— 那里写
> cond3/cond4 false、要再发一笔 BUY,**已陈旧**。真实状态见下(codex review
> 2026-06-14 抓出的陈旧项)。

- **真实状态**:**MVP(K–N)已于 2026-06-01 真启上线 —— 首笔真实 Line-1 BUY 经飞书
  人工执行全闭环**(CLAUDE.md 头部 + SESSION_LOG)。`config/pilot_readiness.yaml`
  里 **PILOT cond3/4/5/6/7/11 全部 = true**(cond3 owner 审 2026-05-28 / cond4
  真发 smoke owner 确认 2026-05-29 / cond5-7+11 测试签收)。
- **即:真发→人工执行→镜像→对账的闭环已被真实演示过**。`U-E5` 任务名义仍
  `doing` 属 plan.html 记账滞后,非未证路径。
- **当前**:系统自 2026-06-04 起 owner 主动停机(『等开发完毕后集中测试』,见
  SESSION_LOG #71)。镜像已走官方对账通道清零。
- **owner 重启后(在已完成的 O/T 代码上)的复验**:刷新 §1 授权 → 重启(recover
  清零快照,ModeRouter 重启≠switch 已修)→ 开盘时段确认 Line-1/Line-2 闭环 +
  飞书人工执行 + 对账仍通,即可推进更长的验证窗口(§2.2/§2.3)。
- **收口动作**:可把 `U-E5` 状态从 `doing` 收为 `done`(MVP 上线已满足真发真回填
  目标),并同步刷新 plan.html 该任务 notes —— 建议作为下一次 SSoT 记账 commit
  的一部分,避免再次误导。

### 2.2 S-004 — Phase 5B 七天 shadow 真值采集【部署窗口】

- **状态**:`blocked`(缺部署后 7 天真实数据)。
- **硬门**:`QUANTMIND_SHADOW_ENABLED=1` + 部署窗口连续 7 天。
- **验收**:`scripts/phase5b_exit_check.py --days 7 --strict` 输出 7 项 PASS。
- **定位**:与主闭环正交,跑完作 LLM routing 资产验证(不再作真实券商入口)。

### 2.3 I-002 — 45 交易日滚动验收【烧 LLM 预算,owner 直接授权】

- **状态**:`blocked`。**这是端到端烧预算长跑**(日 ¥100 hard cap 内)。
- **硬门**:Phase J 7/7 done(✅)+ §1 授权刷新 + `QUANTMIND_PROD_RUN=1`。
- **前置 runbook**:[`i-002-production-runbook.md`](./i-002-production-runbook.md)
  §1 pre-flight 逐项打勾(含 cold-start smoke + `simulate_n_trading_days --days 45`
  近 7 天 PASS、Mongo/Redis systemd、备份卷、告警群可达)。
- **验收**:5 稳定性 + 3 策略硬门槛全 PASS;5 类 P0 中断重置、对账 freeze 暂停。
- **运行期激活提醒**:见 runbook §2.1 —— 交易员人格随 boot 自动加载;全异动栈
  与 exemplars 默认 OFF,**不要在 45 日窗口中途翻开**(改 config hash 扰动验收)。

### 2.4 I-003 — feishu_interactive 启用 gate【I-002 PASS 后】

- **状态**:`blocked`,依赖 I-002 PASS + owner 书面授权。
- **硬门**:只有 `acceptance.can_switch_to_feishu_on()` 返回 allowed 才可切;
  严禁 env/CLI 绕过。切换走账户生命周期事件 + 初始化对账。

### 2.5 R-003 — 45 日 shadow + 人工 gate【实盘上线前】

- **状态**:`todo`。sim 范围已被 Phase AB 客观晋升替代;**仅剩 feishu_interactive
  实盘模式的人工 gate 语义**,实盘上线前做,与 AB-002/AB-003 合并设计。

## 3. 已知接线缺口(整体测试期补,非阻塞收口)

| 缺口 | 影响 | 现状 |
|------|------|------|
| **owner-prod-auth 过期** | 任何 prod 启动 fail-fast | 见 §1,owner 重签即解 |
| **AC 三层 value_score 真实数据未喂入** | `value_scores=None` → 全 SHORT_TERM 与现状 bit-identical,价值槽空、纯量化照跑 | 需接 event-study 事件日 / 资金流容量 / KG 共振真实数据 |
| **AB ChallengerReplayer 未实现 + rqalpha 未装** | 每晚 22:00 evolution audit skipped;param runtime 落地路径未建 | `rqalpha` 未安装(Apache-2.0 可选依赖、非商用可选、永不 vendor);客观晋升其余链路已就绪 |
| **全异动栈可选依赖未装** | T-003 IsolationForest/ruptures 默认 OFF = N-001 byte-identical | `pip install -e '.[anomaly-stack]'` + env=1 + 45 日 shadow 后启用 |
| **MiroFish/exemplars 冷启动空态** | 首启 advisory=None / 校准 INSUFFICIENT_DATA / 无 off-market briefing | 优雅降级,纯量化照跑;随 EOD 运行积累填充 |

## 4. 推荐启动序(owner 决策)

1. **刷新 §1 授权**(所有 prod 路径共同前置)。
2. **重启 + 闭环复验**(在已完成的 O/T 代码上):recover 清零快照 → 开盘时段确认
   Line-1/Line-2 + 飞书人工执行 + 对账仍通(U-E5 闭环 2026-06-01 已演示,此为
   在新代码上的回归复验,非重发已签的真发流程 —— 见 §2.1)。
3. **S-004** 7 天 shadow(部署窗口,正交可并行)。
4. **I-002** 45 交易日长跑(最重,烧预算;按 runbook §1 逐项打勾后启动)。
5. **I-003** → **R-003**(I-002 PASS 后的实盘切换链)。

> 每条路径启动前重读对应 runbook 小节;Claude 全程可协助 dry-run / smoke /
> 证据采集 / 监控,但**发送、授权、env 设定、真实长跑启动**一律 owner 亲为。
