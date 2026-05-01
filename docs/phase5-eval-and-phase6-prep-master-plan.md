# QuantMind Phase 5 评估期 与 Phase 6 实盘前置 主控执行计划(SSoT)

> **文档身份**: Single Source of Truth — 多 Claude Code session 执行依据
> **生成时间**: 2026-05-01
> **生效时段**: Phase 5 起 2026-05-01 ~ Phase 6C 完 ~2026-08-15(预估)
> **基线提交**: `8451ada fix(deploy): support local kimi and long watchlist runs`(本地 main,领先 origin/main 3 commit,push 决策见 P5A-T00)
> **评估模式红线**: `AUTHORIZATION_MODE=suggest` 在 Phase 5 与 Phase 6A/6B 不可越界;Phase 6C 干跑期可升至 `confirm`(单笔确认),实盘 `auto` 仅在 Phase 7 通过用户书面授权后启用
> **风控红线**: `backend/risk/` 严禁 `import backend.llm` / `backend.agents` / `backend.mirofish`,任何 LLM 输出不得越过硬规则
> **作者协议**: 修改本文档需在 §7.4 追加 `修订记录`,记录日期 + 改动摘要 + commit hash;同 commit 内必须更新对应 task marker

---

## 0. Executive Summary

本计划覆盖 14+ 周(2026-05-01 ~ ~2026-08-15)的两阶段路线 + 一阶段纲要:

- **Phase 5** (Week 1-4):4 周 suggest-mode 评估期。子阶段 5A 修生命线 → 5B 上分级 LLM 路由 + Fast/Slow watchlist → 5C 装策略复盘/regime/decay 基础设施 → 5D 多策略 A/B → 5E 评估期收尾。
- **Phase 6** (Week 5-8+):实账户前置准备。子阶段 6A 聚宽历史交叉验证 + MockBroker 校准 → 6B VnPy 微结构 + walk-forward CPCV → 6C ¥10k 微仓位实账户干跑(至少 4 周)。
- **Phase 7** (Week 14+):生产化纲要(本文档不写详细 task,只列预期方向,等 6C 干跑数据出来后再细化)。

核心机械锚点贯穿全程:① **task_id**(P{phase}{letter}-T{NN});② **进度 marker**(⏳/🔧/🚧/✅/🛑);③ **pre-commit gate**(更新 marker → 测试 → codex-review → commit message 模板)。每阶段以 STOP + summary report 收尾,**绝不自动跨阶段**,必须用户书面"授权进入下一阶段"才继续。

Kimi K2.6 thinking-mode 是核心成本-延迟优化旋钮:**分级路由**(cheap-triage Qwen 先跑 → 仅当 confidence < 0.6 或 bull/bear 矛盾时 escalate Kimi)+ **Per-agent thinking 开关**(news/sentiment/data_cleaner/fundamental/technical 关闭 thinking;intelligence/bull/bear/risk/fund 开启 + max_tokens 上限)+ **Fast/Slow watchlist 拆分**(fast cron 4 次/日 8 min 上限、单轮辩论;slow cron 1 次/日 15 min 上限、双轮辩论),目标在保 ≥85% 决策一致率前提下:

| 指标 | Baseline (2026-05-01) | Phase 5B 出口目标 | Phase 5C 出口目标 |
|---|---|---|---|
| 单股 p95 latency | ~25 min | fast ≤ 8 min / slow ≤ 15 min | 不变 |
| 单股 LLM 成本 | ~¥0.34 | fast ≤ ¥0.20 / slow ≤ ¥0.50 | 不变 |
| 日均成本(5 股 watchlist) | ¥1.72 | ≤ ¥1.20 | ≤ ¥1.20 |
| 30 股 watchlist 日成本估算 | ~¥10 | ≤ ¥7 | ≤ ¥7 |
| 决策一致率(vs baseline 9-agent kimi-only) | 100% | ≥ 85% | ≥ 85% |

---

## 1. Phase 分解总览

| 阶段 | 时间窗 | 主题 | 关键交付物 | 出口标准 |
|---|---|---|---|---|
| **5A** | Week 1 (2026-05-01 ~ 05-08) | 评估期生命线 | news_crawler 修复、daily cost hard-cap、AUTHORIZATION_MODE startup assertion、commit 决策 | 4 项 P0 全部 ✅;pytest 全绿;线上 24h 无 5xx 与误熔断 |
| **5B** | Week 1-2 (05-04 ~ 05-15) | 分级 LLM 路由 + Fast/Slow watchlist | `agent_models.yaml` routing/thinking 扩展、`watchlist_policy.yaml`、scheduler 双 cron | p95 fast ≤ 8min、slow ≤ 15min、日成本 ≤ ¥1.20、决策一致率 ≥ 85% |
| **5C** | Week 2-3 (05-11 ~ 05-22) | 策略复盘/regime/decay 基础设施 | `strategy_health.py` 6 维夜检、HMM regime、SignalEvaluator 真实填充、equal-weight control、degraded tag、R3-M1 selector、portfolio e2e | 夜检 7 天无误报;regime 日志可追溯;realistic vs price-only hit-rate diff ≥ 0.05;playwright pass ≥ 95% |
| **5D** | Week 3-4 (05-18 ~ 05-29) | 多策略 A/B (P5-T02) | `MultiStrategyOrchestrator`、3 候选策略并行账户 | 8 交易日 3 账户 PnL 完整;Sharpe 对比报告 |
| **5E** | Week 4 末 (05-25 ~ 05-29) | 评估期收尾 | `phase5-eval-final-2026-05-29.md` + retirement-criteria check | **STOP**,等用户明确授权进 Phase 6 |
| **6A** | Week 5-6 (06-01 ~ 06-12) | JoinQuant 交叉验证 (P5-T03) + MockBroker 校准 | jqdatasdk 历史回测、fill realism diff < 5bps | 历史 vs 评估期 Sharpe 偏差 < 30% |
| **6B** | Week 6-7 (06-15 ~ 06-26) | VnPy 微结构 + walk-forward harness | T+1/涨跌停/集合竞价 fill 模型、CPCV 5 折 | PBO < 0.5 |
| **6C** | Week 8+ (06-29 ~ 08-15) | ¥10k 微仓位实账户干跑 | 真实券商 adapter interface、硬阈值熔断 | **干跑 4 周**,任意熔断条件触发即停 |
| **7 纲要** | Week 14+ | 生产化方向 | (仅纲要,见 §6) | 待 6C 数据出来后细化 |

---

## 2. 跨切面约定(Cross-cutting Conventions)

> 这一节只讲一次,不在每个 task 里重复。

### 2.1 进度标记语法

每个 task 在本文档与 commit message 中带状态前缀:

| Marker | 含义 | 必填字段 |
|---|---|---|
| ⏳ 待做 | 未开始 | — |
| 🔧 推进中 | 已认领,正在做 | `owner_session`(可选) |
| 🚧 阻塞 | 不能往前 | `blocked_by`(必填) |
| ✅ 已完成 | done | `commit_hash`、`test_report`(必填) |
| 🛑 已放弃 | 退役 | `retirement_reason`、`retirement_commit`(必填) |

更新位置:
1. 本文档 task 表(每次 commit 前)
2. 同次 commit message 中 `Status: ⏳→🔧` 或 `🔧→✅` 字段

### 2.2 阶段总结报告模板

文件名: `docs/reviews/phase{N}{letter}-summary-{YYYY-MM-DD}.md`

```markdown
# Phase {N}{letter} Summary — {date}

## 1. 工作清单完成情况
| Task ID | Title | Status | Commit | Test Report |
|---|---|---|---|---|
| P5A-T01 | ... | ✅ 已完成 | abc1234 | docs/reviews/p5a-t01-tests.md |

## 2. 测试基线
- pytest: N passed / M skipped / K failed
- vitest: N / M
- playwright(全量): N / M(标注非回归失败)
- coverage: backend/risk/ {pct}% / overall {pct}%

## 3. 关键决策记录
- 决策点 X:选择 A vs B —— 理由 —— 验证数据

## 4. 阻塞与风险
- 当前阻塞: none / 列表
- 已识别风险: 列表 + 缓解措施

## 5. 下一阶段入口条件
- [ ] 用户授权进入 Phase {N+1}{letter}
- [ ] {gate metric 1} 达成
- [ ] ...
```

### 2.3 测试金字塔约定(每 task 4 类不可缺)

| 层 | 工具 | 数据源 | 阈值 |
|---|---|---|---|
| Unit | pytest | mocks | 行覆盖 changed-file ≥ 70%,risk/ ≥ 95% |
| Integration | pytest + 真实 Mongo/Redis fixture | docker-compose `mongo:6` `redis:7` | 至少 1 happy + 1 sad path |
| Property/Contract | hypothesis | 生成器 | 至少 1 invariant,`max_examples=200` |
| E2E | playwright(前端)/ bash HTTP cycle(后端) | 真实启动的 backend(systemd 或 uvicorn fork) | smoke + 1 核心用户路径 |

通用断言范例:

```python
@pytest.mark.unit
def test_strategy_health_kelly_compliance_rejects_oversized():
    """Kelly compliance 必须 flag 仓位 > 50% 的半 Kelly 偏差。"""
    health = StrategyHealth(kelly_pct=0.10, position_pct=0.30)
    result = compute_health_alerts(health)
    assert any(a.metric == "kelly_compliance" for a in result.alerts)
    assert result.severity == "critical"


from hypothesis import given, settings, strategies as st

@given(
    win_rate=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    payoff=st.floats(min_value=0.5, max_value=3.0, allow_nan=False),
)
@settings(max_examples=200)
def test_kelly_fraction_is_bounded(win_rate, payoff):
    f = kelly_fraction(win_rate, payoff)
    assert -1.0 <= f <= 1.0
```

### 2.4 Pre-commit Gate(每 task 交付前必跑)

按顺序:

1. **更新 marker**: 在本文档把 task status 从 🔧 推进中 改为 ✅ 已完成,填 commit hash 占位
2. **本地测试**(全绿才能下一步):
   ```bash
   cd /home/ps/papers/QuantMind
   /home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-report=term-missing --cov-fail-under=70
   /home/ps/anaconda3/envs/zhanglan/bin/pytest -q backend/risk --cov=backend/risk --cov-fail-under=95
   cd frontend && npm run type-check && npm run test -- --run
   npx playwright test --workers=1 --reporter=line
   npm run build
   ```
3. **codex-review**(根据 task 类别选轮数):
   - **major feature**(新增 module / 改 risk / 改 scheduler / 改 router / 改 graph): 必跑 5 轮(R1 architecture, R2 UX, R3 testing, R4 perf+a11y, R5 security+ops);CRITICAL+HIGH/WARN 必须 0 才能进 commit
   - **minor fix / refactor / small UI**: 跑 R1 + R3 两轮(architecture + testing)
   - 输出存于 `docs/reviews/{task_id}-r{N}-{topic}.md`,在 commit message Codex-Review 字段引用
   - codex-review 触发前先同步 skill: `git pull` https://github.com/LanEinstein/CCodexSkill,rsync 到 `~/.claude/skills/codex-review/`
4. **commit message 模板**:
   ```text
   {type}({scope}): {subject in English ≤72 chars}

   Task: {TASK_ID}
   Status: 🔧→✅
   Tests: pytest {N} passed; vitest {M}; playwright {K}/{T}
   Coverage: backend/risk={p1}% overall={p2}%
   Codex-Review: R1 ✅ R3 ✅ ({task_id}-r1-architecture.md, {task_id}-r3-testing.md)

   {body explaining WHY (not what), 70-char wrap}
   ```
5. **不自动 push**,等用户授权

### 2.5 Cost Ceiling Enforcement(代码级强制,见 P5A-T02)

- **硬上限**: `DAILY_BUDGET_RMB`(默认 ¥20,可经环境变量 `QUANTMIND_DAILY_BUDGET` 覆写)
- **软警戒线**: `SOFT_CEIL_PCT=0.7`(达到后 catch-up 串行化、Kimi thinking 强制 disabled)
- **熔断**: `HARD_CEIL_PCT=1.0`,scheduler 跳过该次运行,写 `analysis_records.status=failed, error="cost_ceiling_breached: …"`

### 2.6 Strategy Retirement Protocol(代码级硬熔断,见 P5C-T04)

每晚 23:00 CST cron 触发 `compute_health()`,6 维度评分:

| 维度 | 计算 | Critical 阈值 | 来源依据 |
|---|---|---|---|
| `rolling_sharpe_20d` | √252 × mean(daily_ret)/std(daily_ret) | < 0.5 | 半 Kelly 标准 |
| `hit_rate_ci_lower` | Wilson 95% CI 下界 | < 0.50 | López de Prado 防过拟合 |
| `regime_correlation` | abs(corr(strategy_pnl, regime_state_indicator)) | > 0.85 | AQR 因子衰减分离 supply/demand |
| `factor_decay` | corr(predictions_t, predictions_{t-30}) | < 0.30 | Asness 因子衰减判定 |
| `max_drawdown_30d` | (peak - trough) / peak | > 0.10 | 半 Kelly 风险预算 |
| `kelly_compliance` | abs(actual_pos_pct - 0.5*kelly) / (0.5*kelly) | > 0.5 | Kelly 半凯利纪律 |

任一指标连续 2 个交易日 critical → 写入 `strategy_health_alerts` collection;**连续 5 个交易日 critical → 自动设 `strategy.active=False` + 钉钉告警(代码级硬熔断);禁止自动启用新策略,必须用户书面授权恢复**。

### 2.7 Fast/Slow Watchlist 分类法(`config/watchlist_policy.yaml`)

```yaml
fast:
  cron: "0 9,11,13,15 * * mon-fri"   # 4 次/日,日内+T+1 短线
  pipeline: fast_pipeline
  max_debate_rounds: 1
  pipeline_timeout_seconds: 480       # 8 分钟硬上限
  default_codes: []                   # 空 = 由用户运行时指定
slow:
  cron: "0 9 * * mon-fri"             # 1 次/日,深度长线分析
  pipeline: slow_pipeline
  max_debate_rounds: 2
  pipeline_timeout_seconds: 900       # 15 分钟硬上限
  default_codes: []

overrides:
  "300750": slow                      # 宁德时代 → 长线深度
  "601318": slow                      # 中国平安 → 长线深度

policy_version: 1
last_updated: 2026-05-01
```

单只股票最多归 1 类(fast 优先于 slow);未在 overrides 中的股票默认 slow。

### 2.8 Tiered LLM Routing 与 Per-Agent Thinking Config

`config/agent_models.yaml` 扩展 schema(向后兼容,未填 `routing` / `thinking` 字段的 agent 走默认):

```yaml
agents:
  bull_researcher:
    name: "看多研究员"
    provider: kimi
    model: kimi-k2.6
    fallback: { provider: qwen, model: qwen3.6-plus }
    routing:
      triage_provider: qwen
      triage_model: qwen3.6-plus
      escalation_condition:
        confidence_lt: 0.6              # confidence < 0.6 时 escalate
        # 或自定义: contradiction_with: bear_researcher
      escalation_provider: kimi
      escalation_model: kimi-k2.6
    thinking:
      type: enabled
      max_tokens: 8000
      keep: all                          # 多轮辩论需保留思维链
    frequency: per_trading_day
    task: 构建看多论点

  news_crawler:
    name: "新闻爬取员"
    provider: deepseek
    model: deepseek-v4-pro
    routing:
      triage_provider: deepseek          # 不分级
    thinking:
      type: disabled                     # 关 thinking 节省成本
      max_tokens: 0
      keep: none
```

新增 Pydantic 模型(`backend/llm/providers.py`):

```python
class RoutingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    triage_provider: str
    triage_model: str
    escalation_provider: str | None = None
    escalation_model: str | None = None
    escalation_condition: dict[str, Any] = Field(default_factory=dict)


class ThinkingConfig(BaseModel):
    model_config = ConfigDict(frozen=True)
    type: Literal["enabled", "disabled"] = "enabled"
    max_tokens: int = 8000
    keep: Literal["all", "last_round", "none"] = "all"


class AgentConfig(BaseModel):
    # 已有字段保留
    routing: RoutingConfig | None = None
    thinking: ThinkingConfig = ThinkingConfig()
```

`backend/llm/router.py::_normalize_provider_kwargs` 翻译为 Kimi/Moonshot SDK:

```python
if provider_name == "kimi" and model.startswith("kimi-k2"):
    thinking_cfg = agent_cfg.thinking
    if thinking_cfg.type == "enabled":
        normalized["thinking"] = {
            "type": "enabled",
            "max_tokens": thinking_cfg.max_tokens,
        }
    else:
        normalized["thinking"] = {"type": "disabled"}
```

### 2.9 红线清单(任何 commit 不得违反)

- `AUTHORIZATION_MODE` 必须 `suggest`(评估期与 6A/6B);仅 6C 干跑可改 `confirm`;实盘 `auto` 仅 Phase 7 用户书面授权
- `backend/risk/*.py` 不得 `import backend.llm` / `backend.agents` / `backend.mirofish`(Phase 5A 加 CI 检查)
- `.env` 永不入 git;LLM key 仅经 shell 环境
- MongoDB / Redis 端口仅绑定 `127.0.0.1`
- 不得跨阶段自动推进(Phase 末必须 STOP)
- 真实下单代码(QMT / VnPy 实盘 adapter)在 6C 之前 **不得激活**,仅留 interface stub

---

## 3. Phase 详细任务分解

> 所有 task 落盘时初始 marker 均为 ⏳ 待做。

### Phase 5A — 评估期生命线(Week 1)

#### P5A-T00 — 提交决策与基线复核 [⏳ 待做]

- **Owner**: 任何 session 首次接手时
- **Dependencies**: 无
- **背景**: 当前本地 main 领先 origin/main 3 commit(`92f73a2`、`dbd0718`、`8451ada`),工作树 clean。需要用户决定是否 push 与 tag。
- **Steps**:
  1. `git status && git log --oneline origin/main..HEAD`
  2. `git diff origin/main..HEAD --stat`
  3. 询问用户是否 push 与是否打 tag `phase5-eval-start`
  4. 用户授权后:
     ```bash
     git push origin main
     git tag phase5-eval-start
     git push origin phase5-eval-start
     ```
- **Tests**: 无(纯 git 操作)
- **Pass thresholds**: `git rev-parse origin/main == git rev-parse HEAD`
- **Pre-commit**: 不产生新 commit
- **Done definition**: 用户显式确认 push 完成或显式选择保持本地

#### P5A-T01 — 修复 news_crawler 'result' KeyError [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5A-T00
- **Bug 位置**: `backend/data/news_crawler.py:30` `_fetch_news_eastmoney()` 调用 `akshare.stock_news_em(symbol="")`。akshare 上游 API 在收到空 symbol 时,JSON 解析阶段对缺失的 `'result'` key raise KeyError,直接传播到 `NewsCrawlerService.fetch_latest_news`,产生每 5 分钟一次的 warning 日志(已观察到 24h 内 50 条)。
- **实现**:

  在 `backend/data/news_crawler.py` 增加容错包装:

  ```python
  _EXPECTED_NEWS_COLUMNS = ["新闻标题", "新闻内容", "新闻链接", "发布时间"]


  def _safe_fetch_news_eastmoney() -> pd.DataFrame:
      """Tolerant wrapper around akshare.stock_news_em(symbol='').

      akshare upstream raises KeyError('result') when its server returns
      an unexpected payload for empty-symbol queries. We treat this as
      'no news' and return an empty DataFrame with the expected columns
      instead of letting the KeyError propagate and pollute the 5-min
      log stream. Other exceptions are still logged as warnings but do
      not crash the scheduler.
      """
      try:
          return _fetch_news_eastmoney()
      except KeyError as exc:
          if "result" in str(exc):
              log.info("eastmoney_empty_payload", reason="upstream_regression")
              return pd.DataFrame(columns=_EXPECTED_NEWS_COLUMNS)
          raise
      except Exception as exc:
          log.warning("eastmoney_news_failed", error=str(exc))
          return pd.DataFrame(columns=_EXPECTED_NEWS_COLUMNS)
  ```

  把 `NewsCrawlerService.fetch_latest_news` 第一个数据源改用 `_safe_fetch_news_eastmoney`。

- **Tests**:

  Unit (`tests/test_news_crawler.py`):

  ```python
  def test_safe_fetch_returns_empty_on_keyerror_result(monkeypatch):
      def fake() -> pd.DataFrame:
          raise KeyError("result")
      monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
      df = news_crawler._safe_fetch_news_eastmoney()
      assert df.empty
      assert list(df.columns) == news_crawler._EXPECTED_NEWS_COLUMNS


  def test_safe_fetch_propagates_unrelated_keyerror(monkeypatch):
      def fake():
          raise KeyError("not_result")
      monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
      with pytest.raises(KeyError):
          news_crawler._safe_fetch_news_eastmoney()


  def test_safe_fetch_swallows_general_exception(monkeypatch, caplog):
      def fake():
          raise RuntimeError("network down")
      monkeypatch.setattr(news_crawler, "_fetch_news_eastmoney", fake)
      df = news_crawler._safe_fetch_news_eastmoney()
      assert df.empty
      assert "eastmoney_news_failed" in caplog.text
  ```

  Integration: 用 `vcr.py` 录制 akshare 真实 503/200 响应,断言 service 不 crash。

  Contract: hypothesis 生成 1KB 随机响应字典,断言 `_safe_fetch` 不 raise。

  E2E: 启动 backend,跑 24h,grep `journalctl -u quantmind-backend` 不再出现 `eastmoney_news_failed: 'result'`(0 次)。

- **Pass thresholds**:
  - 新单测全绿;`tests/test_news_crawler.py` 行覆盖 ≥ 90%
  - 24h 线上日志连续 0 次 `eastmoney_news_failed: 'result'`
  - 整体 pytest pass rate 不下降(基线 669)
- **Pre-commit**: §2.4 minor-fix 路径(R1 + R3 两轮 codex-review)
- **Done**: 24h 监控通过 + commit + marker 改 ✅

#### P5A-T02 — Daily LLM Cost Hard-Cap (cost_guard) [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5A-T01
- **背景**: 当前 `analysis_scheduler` catch-up 不感知日成本;catch-up 异常重启或长 watchlist 都可能在一日内累计大额 LLM 花费。需要代码级硬熔断 + 软警戒线。
- **实现**:

  新建 `backend/services/cost_guard.py`:

  ```python
  """Daily LLM cost ceiling enforcement.

  Single source of truth for whether the next pipeline run is allowed.
  Read-only against Redis aggregations; never mutates spend data.
  """
  from __future__ import annotations

  import os
  from dataclasses import dataclass

  import structlog

  from backend.llm.cost_tracker import aggregate_costs

  log = structlog.get_logger(component="cost_guard")


  @dataclass(frozen=True)
  class BudgetState:
      daily_budget: float
      spent_today: float
      soft_ceiling: float
      hard_ceiling: float
      remaining: float
      status: str  # "ok" | "soft_breach" | "hard_breach"


  class DailyBudgetExceededError(RuntimeError):
      """Raised when the next call would exceed the daily hard ceiling."""


  async def get_budget_state(redis_client) -> BudgetState:
      budget = float(os.environ.get("QUANTMIND_DAILY_BUDGET", "20.0"))
      soft_pct = float(os.environ.get("QUANTMIND_SOFT_CEIL_PCT", "0.7"))
      summary = await aggregate_costs(redis_client, days=1)
      today = (
          next(iter(summary.daily_totals.values()), 0.0)
          if summary.daily_totals else 0.0
      )
      soft = budget * soft_pct
      if today >= budget:
          status = "hard_breach"
      elif today >= soft:
          status = "soft_breach"
      else:
          status = "ok"
      return BudgetState(
          daily_budget=budget,
          spent_today=today,
          soft_ceiling=soft,
          hard_ceiling=budget,
          remaining=max(0.0, budget - today),
          status=status,
      )


  async def assert_budget_allows(redis_client, *, agent_name: str) -> BudgetState:
      state = await get_budget_state(redis_client)
      if state.status == "hard_breach":
          log.error(
              "daily_budget_breached",
              agent=agent_name,
              spent=state.spent_today,
              budget=state.daily_budget,
          )
          raise DailyBudgetExceededError(
              f"Daily budget {state.daily_budget:.2f} CNY exceeded "
              f"(spent {state.spent_today:.2f}); skipping {agent_name}"
          )
      return state
  ```

  在 `backend/data/analysis_scheduler.py::_run_and_persist` 入口处:

  ```python
  from backend.services.cost_guard import (
      DailyBudgetExceededError,
      assert_budget_allows,
  )

  async def _run_and_persist(self, stock_code: str) -> TradingSignal | None:
      if self._redis is not None:
          try:
              state = await assert_budget_allows(self._redis, agent_name="pipeline")
          except DailyBudgetExceededError as exc:
              record = AnalysisRecord(
                  run_id=str(uuid.uuid4()),
                  stock_code=stock_code,
                  stock_name=stock_code,
                  trade_date=datetime.now(SHANGHAI).strftime("%Y-%m-%d"),
                  status="failed",
                  error=f"cost_ceiling_breached: {exc}",
              )
              await self._mongodb.save_analysis_record(record.model_dump(mode="json"))
              return None
          if state.status == "soft_breach":
              # 软警戒:Phase 5B 接入"强制 thinking disabled + 串行化"
              log.warning("cost_soft_breach_active", spent=state.spent_today)
      try:
          ...
  ```

  `backend/api/monitoring.py` 增加 `/api/monitoring/budget` 暴露 `BudgetState` JSON。

- **Tests**:

  Unit (`tests/test_cost_guard.py`):

  ```python
  @pytest.mark.asyncio
  async def test_hard_breach_raises(fake_redis_with_cost, monkeypatch):
      monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "10.0")
      # fake_redis 注入今天已花 ¥12
      with pytest.raises(DailyBudgetExceededError):
          await assert_budget_allows(fake_redis_with_cost, agent_name="pipeline")


  @pytest.mark.asyncio
  async def test_soft_breach_returns_state(fake_redis_with_cost, monkeypatch):
      monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
      monkeypatch.setenv("QUANTMIND_SOFT_CEIL_PCT", "0.7")
      # 注入 ¥15
      state = await assert_budget_allows(fake_redis_with_cost, agent_name="x")
      assert state.status == "soft_breach"


  @pytest.mark.asyncio
  async def test_ok_when_below_soft(fake_redis_with_cost, monkeypatch):
      monkeypatch.setenv("QUANTMIND_DAILY_BUDGET", "20.0")
      # 注入 ¥3
      state = await assert_budget_allows(fake_redis_with_cost, agent_name="x")
      assert state.status == "ok"
  ```

  Integration (`tests/test_analysis_scheduler_budget.py`): 真实 Redis fixture,灌入 ¥25 spent,scheduler `_run_and_persist` 必须返回 None 并写 `analysis_records.status=failed, error startswith "cost_ceiling_breached"`。

  Contract: hypothesis `daily_budget ∈ [1, 1000]`、`spent ∈ [0, 2000]`、`soft_pct ∈ [0.1, 0.9]`,断言 `status` 状态机一致(spent ≥ budget → hard;spent ≥ soft 且 < budget → soft;else → ok)。

  E2E:
  ```bash
  # 模拟 budget 已用尽
  redis-cli -p 6379 HSET 'cost_daily_total:2026-05-01' total_cost_rmb 25.0

  curl -sk -X POST https://quantmind.local/api/watchlist/analyze-now | jq .
  # 期望: 立即返回, status=failed, history record 有 cost_ceiling_breached
  curl -sk "https://quantmind.local/api/analysis/history?limit=1" | jq '.data[0].error'
  # 期望: "cost_ceiling_breached: ..."
  ```

- **Pass thresholds**:
  - 单测覆盖 `cost_guard.py` ≥ 95%
  - integration 1 happy + 2 sad(soft / hard)
  - E2E 至少触发一次硬熔断且 history 可见
  - 7 天线上跑无误熔断
- **Pre-commit**: §2.4 major-feature 路径(5 轮 codex-review,改 scheduler)
- **Done**: 7 天线上无误熔断 + commit

#### P5A-T03 — AUTHORIZATION_MODE Startup Assertion [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5A-T01
- **背景**: 红线 `AUTHORIZATION_MODE=suggest` 当前靠 .env 默认值守护;若误改为 `auto`,自动下单链路被打开。需要启动期 fail-fast。
- **实现**:

  在 `backend/main.py` 顶部:

  ```python
  ALLOWED_MODES_BY_PHASE: dict[str, set[str]] = {
      "phase5_eval": {"suggest"},
      "phase6_prep": {"suggest"},
      "phase6_dryrun": {"suggest", "confirm"},
      "phase7_live": {"suggest", "confirm", "auto"},
  }


  def _assert_authorization_mode() -> tuple[str, str]:
      phase = os.environ.get("QUANTMIND_PHASE", "phase5_eval")
      mode = os.environ.get("AUTHORIZATION_MODE", "suggest").lower()
      allowed = ALLOWED_MODES_BY_PHASE.get(phase)
      if allowed is None:
          raise SystemExit(
              f"Unknown QUANTMIND_PHASE={phase!r}; refusing to start"
          )
      if mode not in allowed:
          raise SystemExit(
              f"Refusing to start: AUTHORIZATION_MODE={mode!r} "
              f"not allowed in phase {phase!r} (allowed: {sorted(allowed)})"
          )
      return phase, mode


  @asynccontextmanager
  async def lifespan(app: FastAPI):
      phase, mode = _assert_authorization_mode()
      log.info("authorization_assertion_passed", phase=phase, mode=mode)
      ...
  ```

  在 `.env.example` 新增 `QUANTMIND_PHASE=phase5_eval`。
  在 `deploy/quantmind-backend.service` 的 `Environment=` 段新增 `QUANTMIND_PHASE=phase5_eval`。
  `backend/api/risk.py::set_auth_mode` 改用同一逻辑,禁止跨 phase 升级:

  ```python
  def set_auth_mode(new_mode: str, phase: str | None = None) -> None:
      phase = phase or os.environ.get("QUANTMIND_PHASE", "phase5_eval")
      allowed = ALLOWED_MODES_BY_PHASE.get(phase, {"suggest"})
      if new_mode not in allowed:
          raise PermissionError(
              f"Mode {new_mode!r} not permitted in phase {phase!r}"
          )
      ...
  ```

- **Tests**:

  Unit:
  ```python
  def test_startup_rejects_auto_in_eval(monkeypatch):
      monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
      monkeypatch.setenv("AUTHORIZATION_MODE", "auto")
      with pytest.raises(SystemExit):
          _assert_authorization_mode()


  def test_startup_allows_suggest_in_eval(monkeypatch):
      monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
      monkeypatch.setenv("AUTHORIZATION_MODE", "suggest")
      phase, mode = _assert_authorization_mode()
      assert phase == "phase5_eval" and mode == "suggest"


  def test_set_auth_mode_blocks_cross_phase(monkeypatch):
      monkeypatch.setenv("QUANTMIND_PHASE", "phase5_eval")
      with pytest.raises(PermissionError):
          set_auth_mode("auto")
  ```

  Integration: docker-compose 启动 backend with `AUTHORIZATION_MODE=auto`,期望容器 exit code != 0 且 stderr 含 "Refusing to start"。

  E2E:
  ```bash
  # negative test
  ! AUTHORIZATION_MODE=auto QUANTMIND_PHASE=phase5_eval timeout 10 \
    /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app \
    --port 8001 2>err.log
  grep -q "Refusing to start" err.log || exit 1

  # positive test
  AUTHORIZATION_MODE=suggest QUANTMIND_PHASE=phase5_eval timeout 10 \
    /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app \
    --port 8002 &
  sleep 3
  curl -sS http://127.0.0.1:8002/api/health | jq -r .status  # ok
  ```

- **Pass thresholds**: assertion fail-fast;negative + positive E2E 各通过一次
- **Pre-commit**: minor-fix(R1 + R3)
- **Done**: deploy SOP 更新 + commit

#### Phase 5A 出口检查 [⏳ 待做]

- pytest 全绿,coverage 不下降
- 24h 线上无 KeyError('result')、无误熔断、无 auto-mode 启动
- 输出 `docs/reviews/phase5a-summary-2026-05-08.md`(模板见 §2.2)
- **STOP**,等用户授权进入 Phase 5B

---

### Phase 5B — 分级 LLM 路由 + Fast/Slow Watchlist(Week 1-2)

#### P5B-T01 — agent_models.yaml schema 扩展 + Per-Agent Thinking Config [⏳ 待做]

- **Owner**: 任意,建议同 session 接 T02-T03
- **Dependencies**: Phase 5A 全部 ✅
- **核心**: §2.8 中描述的 `RoutingConfig` / `ThinkingConfig` Pydantic 模型 + Kimi SDK 翻译。
- **改动文件**:
  - `backend/llm/providers.py`: 加两个模型;嵌入 `AgentConfig`
  - `backend/llm/router.py::_normalize_provider_kwargs`: 新增 thinking 翻译;**删除当前硬编码 `temperature=1, max_tokens=16000`**(此为遗留缺陷,见基线 cost log,Kimi thinking 默认无上限)
  - `backend/llm/router.py::complete`: 新增 `should_escalate(state, response)` hook,routing.escalation_condition 满足时再升级;与 fallback 独立分支
  - `config/agent_models.yaml`: 9 个 agent 全部填 `thinking` 段(分布):

| Agent | thinking.type | max_tokens | keep | 理由 |
|---|---|---|---|---|
| news_crawler | disabled | 0 | none | 摘要类不需要思维链 |
| sentiment_analyst | disabled | 0 | none | 情绪打分不需要 |
| data_cleaner | disabled | 0 | none | 格式转换 |
| fundamental_analyst | disabled | 0 | none | qwen 已够用 |
| technical_analyst | disabled | 0 | none | qwen + 形态识别已够用 |
| intelligence_officer | enabled | 10000 | last_round | MiroFish 仿真融合需深度 |
| bull_researcher | enabled | 8000 | all | 多轮辩论需保留思维链 |
| bear_researcher | enabled | 8000 | all | 同上 |
| risk_officer | enabled | 6000 | last_round | 综合判定 |
| fund_manager | enabled | 8000 | last_round | 终局决策 |

- **Tests**:

  Unit (`tests/test_llm_router_thinking.py`):
  ```python
  @pytest.mark.unit
  def test_normalize_kimi_thinking_disabled():
      kw = LLMRouter._normalize_provider_kwargs(
          provider_name="kimi", model="kimi-k2.6",
          base_kwargs={"max_tokens": 4000},
          thinking=ThinkingConfig(type="disabled", max_tokens=0, keep="none"),
      )
      assert kw["thinking"] == {"type": "disabled"}


  @pytest.mark.unit
  def test_normalize_kimi_thinking_caps_tokens():
      kw = LLMRouter._normalize_provider_kwargs(
          provider_name="kimi", model="kimi-k2.6",
          base_kwargs={},
          thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
      )
      assert kw["thinking"]["type"] == "enabled"
      assert kw["thinking"]["max_tokens"] == 8000


  @pytest.mark.unit
  def test_thinking_ignored_for_non_kimi():
      kw = LLMRouter._normalize_provider_kwargs(
          provider_name="qwen", model="qwen3.6-plus",
          base_kwargs={},
          thinking=ThinkingConfig(type="enabled", max_tokens=8000, keep="all"),
      )
      assert "thinking" not in kw
  ```

  Contract: hypothesis 生成随机 thinking type/max_tokens 组合,断言 schema validate 拒绝非法 keep 值。

  Integration: 修改 `agent_models.yaml` 后做一次便宜 prompt 真实 Kimi 调用,断言 `response.usage.completion_tokens ≤ max_tokens + thinking.max_tokens`。

  E2E: 触发完整分析,统计每 agent token 消耗,断言 thinking-disabled agents 的 reasoning_tokens=0。

- **Pass thresholds**:
  - 单测 cov ≥ 70%
  - 24h 实测对比:单股 token 消耗较 baseline ↓ ≥ 20%
  - 决策一致率(action+confidence 区间)vs baseline ≥ 90%
- **Pre-commit**: 5 轮 codex-review(major,改 router)
- **Done**: 24h 实测对比报告 `docs/reviews/p5b-t01-thinking-impact.md`

#### P5B-T02 — Fast/Slow Watchlist 拆分 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5B-T01
- **实现**:
  1. 落 `config/watchlist_policy.yaml`(模板见 §2.7)
  2. 新建 `backend/services/watchlist_policy.py`:
     ```python
     from dataclasses import dataclass
     from pathlib import Path
     import yaml


     @dataclass(frozen=True)
     class WatchlistPolicy:
         fast_codes: tuple[str, ...]
         slow_codes: tuple[str, ...]
         fast_cron: str
         slow_cron: str
         fast_pipeline_timeout: int
         slow_pipeline_timeout: int
         fast_max_rounds: int
         slow_max_rounds: int
         policy_version: int


     def load_policy(path: Path) -> WatchlistPolicy: ...


     def assign_category(
         code: str, policy: WatchlistPolicy, all_watchlist_codes: set[str]
     ) -> str:
         """Return 'fast' or 'slow' for a code, applying overrides + defaults."""
         ...
     ```
  3. 修改 `backend/data/analysis_scheduler.py`:
     - `__init__` 接 `WatchlistPolicy`
     - `start()` 注册 2 个 cron jobs:`fast_analysis_job`(policy.fast_cron)+ `slow_analysis_job`(policy.slow_cron)
     - 各自构造独立 `PipelineConfig(max_debate_rounds=...)` + `analysis_timeout_seconds=...`
  4. `backend/api/watchlist.py` 新增:
     ```python
     @router.post("/api/watchlist/{code}/category")
     async def set_category(code: str, body: dict): ...
     ```

- **Tests**:
  - Unit: load_policy 拒绝重复 code;assign_category 优先 overrides;空 fast 默认 slow
  - Integration: 真 Mongo + apscheduler `freezegun` 冻结 09:00 → 仅 slow_job 触发;冻结 11:00 → 仅 fast_job
  - Contract: hypothesis 生成 1-100 codes + overrides,断言每只 code 恰好属于一类
  - E2E: 启动 backend,5 只 watchlist 各设 fast/slow,触发对应 cron,断言只跑各自列表

- **Pass thresholds**:
  - p95 fast pipeline latency ≤ 8 min
  - p95 slow pipeline latency ≤ 15 min
  - 7 天 cron 执行成功率 ≥ 95%
- **Pre-commit**: 5 轮 codex-review(major,改 scheduler)
- **Done**: 7 天分布数据 + commit

#### P5B-T03 — Tiered Triage→Escalation Routing [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5B-T01
- **目标**: 在 5 个 Kimi-using agent(intelligence/bull/bear/risk/fund)上启用:先 qwen3.6-plus 做 triage,若结构化输出 `confidence < 0.6` 或 bull/bear 两边一致(无对抗信号)则不升级,否则 escalate Kimi。
- **实现**:

  `backend/llm/router.py::complete` 改造:

  ```python
  async def complete(self, agent_name, messages, **kwargs):
      ...
      agent_cfg = config.agents[agent_name]
      routing = agent_cfg.routing

      triage_provider = routing.triage_provider if routing else agent_cfg.provider
      triage_model = routing.triage_model if routing else agent_cfg.model
      try:
          triage_resp = await self._call_provider(
              provider_name=triage_provider, model=triage_model,
              messages=messages, agent_name=f"{agent_name}/triage", **call_kwargs,
          )
      except RETRYABLE_EXCEPTIONS:
          # fallback path 不变
          ...
      should_esc, reason = self._should_escalate(triage_resp, routing)
      if not should_esc or routing is None or routing.escalation_provider is None:
          return triage_resp
      await track_escalation(
          self._redis, agent_name,
          triage_provider, routing.escalation_provider, reason,
      )
      return await self._call_provider(
          provider_name=routing.escalation_provider,
          model=routing.escalation_model,
          messages=messages,
          agent_name=f"{agent_name}/escalation",
          **call_kwargs,
      )
  ```

  `_should_escalate(response, routing)` 实现:

  ```python
  @staticmethod
  def _should_escalate(response, routing) -> tuple[bool, str]:
      if routing is None:
          return False, "no_routing"
      cond = routing.escalation_condition or {}
      content = response.choices[0].message.content
      try:
          parsed = json.loads(content)
      except json.JSONDecodeError:
          return True, "parse_failed"  # 保守
      conf_lt = cond.get("confidence_lt")
      if conf_lt is not None:
          conf = parsed.get("confidence", 0.0)
          if conf < conf_lt:
              return True, "low_confidence"
      return False, "ok"
  ```

  `backend/llm/fallback.py` 新增 `track_escalation(redis, agent, src, dst, reason)`:写 Redis hash `llm:escalations:{date}:{agent}` 字段 `count, reason_low_confidence, reason_parse_failed`。

  `backend/api/monitoring.py` 新增 `/api/monitoring/llm/escalations` 暴露当日升级率。

- **Tests**:

  Unit:
  ```python
  def test_should_escalate_low_confidence():
      resp = make_resp(content='{"confidence":0.4,"action":"买入"}')
      ok, reason = LLMRouter._should_escalate(resp, routing(confidence_lt=0.6))
      assert ok is True and reason == "low_confidence"


  def test_should_not_escalate_high_confidence():
      resp = make_resp(content='{"confidence":0.85,"action":"买入"}')
      ok, _ = LLMRouter._should_escalate(resp, routing(confidence_lt=0.6))
      assert ok is False


  def test_should_escalate_on_parse_failure():
      resp = make_resp(content='not-json')
      ok, reason = LLMRouter._should_escalate(resp, routing(confidence_lt=0.6))
      assert ok is True and reason == "parse_failed"


  def test_no_routing_no_escalation():
      resp = make_resp(content='{"confidence":0.1}')
      ok, _ = LLMRouter._should_escalate(resp, None)
      assert ok is False
  ```

  Integration: 真实 router + fake_redis,100 个 prompt,统计 `escalations.count / total_calls ≤ 0.5`(至少一半被便宜模型解决)。

  Contract: hypothesis confidence ∈ [0,1]、threshold ∈ [0,1],断言 `should_escalate(c, t) == c < t`(在 confidence_lt 仅 condition 时)。

  E2E: 完整分析后 `/api/monitoring/llm/escalations` 返回 `{count > 0, count < total_calls}`。

  Shadow Test(7 天): 同 prompt 同时跑 baseline(kimi-only)与 routing 路径,记录两份决策结果到 `analysis_records.shadow_*` 字段;脚本 `scripts/shadow_compare.py` 输出不一致率。

- **Pass thresholds**:
  - 单股成本 ↓ ≥ 40%(vs baseline kimi-only)
  - 7 天 shadow 一致率(action 维度) ≥ 85%;confidence 区间偏差 < 0.15
  - 不一致率超阈值时回滚(documented escape valve)
- **Pre-commit**: 5 轮 codex-review
- **Done**: shadow 对比报告 `docs/reviews/p5b-t03-routing-quality.md` + commit

#### Phase 5B 出口检查 [⏳ 待做]

- 单股成本: fast ≤ ¥0.20, slow ≤ ¥0.50
- 单股延迟: fast p95 ≤ 8 min, slow p95 ≤ 15 min
- 日均成本 ≤ ¥1.20
- 决策一致率 ≥ 85%
- 输出 `docs/reviews/phase5b-summary-2026-05-15.md`
- **STOP**,等用户授权进入 Phase 5C

---

### Phase 5C — 策略复盘/regime/decay 基础设施(Week 2-3)

#### P5C-T01 — SignalEvaluator + MockBroker fill-realism 整合 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: Phase 5B 全部 ✅
- **背景**: 现 `backend/services/signal_evaluator.py` 用收盘价裸看方向,忽略涨跌停、停牌、T+1、滑点。需要把 hit-rate 升级为"模拟下单后 PnL 是否为正"。
- **实现**:

  新建 `backend/services/realistic_evaluator.py`:

  ```python
  from dataclasses import dataclass
  from datetime import date, timedelta


  @dataclass(frozen=True)
  class EvaluationResult:
      pnl_pct: float
      fill_status: str  # "filled" | "rejected_upper_limit" | "rejected_lower_limit" | "rejected_suspended"
      slippage_bps: float
      blocked_by_limit: bool


  class RealisticEvaluator:
      def __init__(self, mock_broker, history_data):
          self._broker = mock_broker
          self._hist = history_data

      async def evaluate_signal(
          self, signal: dict, *, horizon_days: int = 5
      ) -> EvaluationResult:
          """Simulate executing the signal next-open + closing T+horizon close.

          1. fetch next-trading-day open price from history_data
          2. check 涨跌停: if action=买入 and (open/prev_close - 1) >= 0.097 -> rejected_upper_limit
          3. check 停牌: if next-day quote missing -> rejected_suspended
          4. else broker.place_order(market, action, qty=1lot, price=open*(1+slip)) - T+1 settlement
          5. fetch T+horizon close, broker.close_position
          6. compute pnl_pct = (close - open*(1+slip)) / open*(1+slip) * direction
          7. return EvaluationResult
          """
          ...
  ```

  `SignalEvaluator.evaluate` 接受 `mode: Literal["price_only", "realistic"]`,保留 price_only 作对照 baseline。

  增加 `tests/fixtures/historical_quotes.csv` 与 conftest fixture:5 watchlist × 30 个交易日 OHLC + 涨跌停 + 停牌标记。

- **Tests**:

  Unit:
  ```python
  def test_realistic_evaluator_rejects_upper_limit(realistic_evaluator):
      signal = {"stock_code": "300750", "action": "买入", "trade_date": "2026-04-01"}
      # 历史 fixture 标记次日开盘价 ≈ 涨停
      result = await realistic_evaluator.evaluate_signal(signal, horizon_days=5)
      assert result.fill_status == "rejected_upper_limit"
      assert result.blocked_by_limit is True


  def test_realistic_evaluator_handles_suspension(realistic_evaluator):
      signal = {"stock_code": "600519", "action": "买入", "trade_date": "2026-04-15"}
      # 历史 fixture: 次日停牌
      result = await realistic_evaluator.evaluate_signal(signal, horizon_days=5)
      assert result.fill_status == "rejected_suspended"


  def test_realistic_pnl_includes_slippage(realistic_evaluator):
      signal = {"stock_code": "000858", "action": "买入", "trade_date": "2026-03-10"}
      result = await realistic_evaluator.evaluate_signal(signal, horizon_days=5)
      assert result.fill_status == "filled"
      assert result.slippage_bps > 0
      # pnl 应低于 price-only 因为扣了滑点
  ```

  Integration: 5 watchlist × 30 天历史,对比 price_only vs realistic hit_rate;断言 diff 在合理区间。

  Contract: hypothesis prev_close ∈ [1, 1000],open ∈ [0.9, 1.1] × prev_close,断言涨跌停判定准确。

  E2E:
  ```bash
  curl -sk -X POST "https://quantmind.local/api/analysis/evaluate" \
    -H 'Content-Type: application/json' \
    -d '{"mode":"realistic","horizon_days":5,"lookback_days":30}' | jq .
  # 期望返回 hit_rate_realistic, hit_rate_price_only, slippage_avg
  ```

- **Pass thresholds**:
  - 两种模式 hit_rate diff ≥ 0.05(说明 fill realism 真的生效)
  - realistic 单次评估 < 30s(价格缓存)
- **Pre-commit**: 5 轮 codex-review(major,引入 evaluator pathway)
- **Done**: commit + 对比报告

#### P5C-T02 — Equal-Weight Hold 控制组 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5C-T01
- **背景**: 评估期需要"对照组"避免幸存者偏差。等权持有 watchlist + 月末 rebalance 是最简基准。
- **实现**:
  1. 新建 `backend/services/control_strategy.py`:
     ```python
     class EqualWeightHoldStrategy:
         """Buy-and-hold equal-weight portfolio rebalanced monthly."""
         async def daily_pnl(self, date: str) -> float: ...
         async def cumulative_return(self, start: str, end: str) -> float: ...
     ```
  2. `backend/api/performance.py` 新增 `/api/performance/control` 返回每日 NAV
  3. `frontend/src/views/Performance.vue` 加双线图(本系统 vs equal-weight)

- **Tests**: unit 等权 PnL 算式;integration 5 股 30 天回测;e2e curl 双线返回。
- **Pass**: 30 天回测两线偏差 ≤ 0.5%(与手算);月末 rebalance commission < 0.05% 总资产
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + 控制组接入前端

#### P5C-T03 — analysis_records `degraded` Tag [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5C-T01
- **背景**: 当前 `status ∈ {running, completed, failed}` 三态。实际还有"完成但降级"场景:fallback 用了、thinking 强制 disabled(soft_breach)、agent 返回 `[agent error: …]` placeholder ≥ 30%。这些应记 `degraded` 并在前端可视化区分。
- **实现**:
  1. `backend/agents/records.py`:`AnalysisRunStatus` 加 `degraded`;新增 `degraded_reasons: list[str]`
  2. `backend/agents/collector.py::finalize` 增加 `degraded_check`:
     ```python
     def _check_degraded(self) -> tuple[bool, list[str]]:
         reasons = []
         empty_pct = sum(1 for s in self._steps if not s.evidence) / max(1, len(self._steps))
         if empty_pct > 0.30:
             reasons.append(f"empty_evidence_ratio={empty_pct:.2f}")
         if any("fallback" in s.model_label for s in self._steps):
             reasons.append("fallback_used")
         if self._budget_soft_breach_active:
             reasons.append("budget_soft_breach")
         return (bool(reasons), reasons)
     ```
  3. 前端 `AgentDebate.vue` 列表 item 加黄色标签 "降级 X reasons":
     ```vue
     <el-tag v-if="record.status === 'degraded'" type="warning" data-testid="degraded-tag">
       降级 ({{ record.degraded_reasons.length }})
     </el-tag>
     ```

- **Tests**: 对应 unit + integration + e2e(playwright 断言 `[data-testid="degraded-tag"]` 可见且 hover 显示 reasons)。
- **Pass**: marker 与 reasons 一致;前端可见
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit

#### P5C-T04 — strategy_health.py 6 维度夜检 + 自动 retire [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5C-T01, P5C-T02
- **核心交付**: `backend/services/strategy_health.py`,每晚 23:00 CST cron,6 维评分,代码级硬熔断。

  指标定义见 §2.6。

- **关键实现片段**:

  ```python
  # backend/services/strategy_health.py
  from dataclasses import dataclass
  from datetime import date

  import numpy as np


  @dataclass(frozen=True)
  class HealthMetric:
      name: str
      value: float
      threshold: float
      severity: str   # "ok" | "warning" | "critical"
      rationale: str


  @dataclass(frozen=True)
  class StrategyHealthReport:
      strategy_id: str
      computed_at: date
      metrics: tuple[HealthMetric, ...]
      overall_severity: str
      retirement_recommendation: bool


  def wilson_lower(success: int, total: int, z: float = 1.96) -> float:
      if total == 0:
          return 0.0
      p = success / total
      denom = 1 + z**2 / total
      center = p + z**2 / (2 * total)
      margin = z * ((p * (1 - p) + z**2 / (4 * total)) / total) ** 0.5
      return (center - margin) / denom


  def _sev(value: float, threshold: float, *, lower: bool) -> str:
      """lower=True: value < threshold => critical."""
      if lower:
          if value < threshold:
              return "critical"
          if value < threshold * 1.2:
              return "warning"
          return "ok"
      else:
          if value > threshold:
              return "critical"
          if value > threshold * 0.8:
              return "warning"
          return "ok"


  async def compute_health(
      strategy_id: str, mongodb, history_data, regime_detector
  ) -> StrategyHealthReport:
      metrics = []

      # 1. rolling_sharpe_20d
      pnl = await _load_daily_pnl(mongodb, strategy_id, days=20)
      if len(pnl) >= 20:
          if np.std(pnl) > 0:
              sharpe = (np.mean(pnl) / np.std(pnl)) * np.sqrt(252)
          else:
              sharpe = 0.0
          metrics.append(HealthMetric(
              "rolling_sharpe_20d", sharpe, 0.5,
              _sev(sharpe, 0.5, lower=True),
              "20d annualized; threshold derived from half-Kelly discipline (Asness AQR)",
          ))

      # 2. hit_rate_ci_lower (Wilson 95%)
      signals = await mongodb.query_signals(days=30, strategy_id=strategy_id)
      decisive = [s for s in signals if s["action"] != "持有"]
      correct = sum(1 for s in decisive if await _check_correct(s, history_data))
      ci_low = wilson_lower(correct, len(decisive))
      metrics.append(HealthMetric(
          "hit_rate_ci_lower", ci_low, 0.50,
          _sev(ci_low, 0.50, lower=True),
          "Wilson 95% CI lower bound; below 50% means no statistical edge (López de Prado)",
      ))

      # 3. regime_correlation (over-dependence on a single regime)
      regime_corr = await _compute_regime_correlation(
          mongodb, strategy_id, regime_detector, days=30
      )
      metrics.append(HealthMetric(
          "regime_correlation", regime_corr, 0.85,
          _sev(regime_corr, 0.85, lower=False),
          "abs(corr) > 0.85 means strategy lives or dies on one regime (Asness)",
      ))

      # 4. factor_decay (signal staleness)
      decay = await _compute_factor_decay(mongodb, strategy_id, lag_days=30)
      metrics.append(HealthMetric(
          "factor_decay", decay, 0.30,
          _sev(decay, 0.30, lower=True),
          "corr(predictions_t, predictions_{t-30}) < 0.3 means signal forgotten itself",
      ))

      # 5. max_drawdown_30d
      mdd = await _compute_max_drawdown(mongodb, strategy_id, days=30)
      metrics.append(HealthMetric(
          "max_drawdown_30d", mdd, 0.10,
          _sev(mdd, 0.10, lower=False),
          "30d max drawdown > 10% breaches half-Kelly risk budget",
      ))

      # 6. kelly_compliance
      kelly_dev = await _compute_kelly_deviation(mongodb, strategy_id)
      metrics.append(HealthMetric(
          "kelly_compliance", kelly_dev, 0.50,
          _sev(kelly_dev, 0.50, lower=False),
          "abs(actual - 0.5*kelly)/(0.5*kelly) > 50% breaks position discipline",
      ))

      crit = any(m.severity == "critical" for m in metrics)
      warn = any(m.severity == "warning" for m in metrics)
      overall = "critical" if crit else ("warning" if warn else "ok")
      return StrategyHealthReport(
          strategy_id=strategy_id,
          computed_at=date.today(),
          metrics=tuple(metrics),
          overall_severity=overall,
          retirement_recommendation=crit,
      )
  ```

  Cron 注册(`backend/data/scheduler.py`):

  ```python
  scheduler.add_job(
      _run_strategy_health_check,
      trigger="cron", hour=23, minute=0, timezone="Asia/Shanghai",
      id="strategy_health_nightly", replace_existing=True,
  )
  ```

  自动 retire 逻辑(`backend/services/strategy_health.py::_apply_retirement`):

  ```python
  async def _apply_retirement(mongodb, report: StrategyHealthReport) -> None:
      # 持续 5 天 critical → strategy.active = False
      recent_5 = await mongodb.recent_health_reports(report.strategy_id, days=5)
      if len(recent_5) >= 5 and all(r.overall_severity == "critical" for r in recent_5):
          await mongodb.set_strategy_active(report.strategy_id, False)
          await dingtalk_alert(
              level="critical",
              title=f"Strategy {report.strategy_id} auto-retired",
              detail=f"5 consecutive days critical: "
                     f"{[m.name for m in report.metrics if m.severity=='critical']}",
          )
  ```

- **Tests**:
  - Unit: 每维独立单测(fixture pnl/signals);Wilson 单调性;`_sev` 边界
  - Integration: 真 Mongo 灌 30 天 + 5 天 critical 历史,end-to-end 跑 cron,断言 `strategy.active=False` + 告警发送(用 mock httpx)
  - Contract: hypothesis Wilson `success ↑ → ci_low ↑`(单调)
  - E2E: scheduler fake-clock 跳到 23:00,grep `journalctl` 含 `strategy_health_nightly executed successfully`

- **Pass thresholds**:
  - 6 维全部产出
  - retirement_recommendation 与 overall_severity 一致
  - cron 7 天连续无异常
  - 单测 cov `strategy_health.py` ≥ 90%
- **Pre-commit**: 5 轮 codex-review
- **Done**: 7 天 cron + commit

#### P5C-T05 — A-share Regime Detector(HMM)[⏳ 待做]

- **Owner**: 任意
- **Dependencies**: P5C-T04
- **特征向量**(daily): `[csi300_5d_logret, csi300_20d_logret, csi300_volatility_20d, north_capital_5d_net_inflow, dragon_tiger_institutional_share]`
- **模型**: `hmmlearn.GaussianHMM(n_components=4, covariance_type="diag", n_iter=200)`
- **4 态**: `bull / bear / range_up / range_down`(后验排序 by mean csi300 5d return)
- **训练数据**: jqdatasdk 拉 CSI300 + 北向资金 + 龙虎榜 2020-01 ~ 2026-04 离线训练,pickle 到 `backend/services/regime_model.pkl`
- **Cron**: daily 09:30 CST 调 `predict_state`,写 Redis `regime:current`(过期 6h)+ Mongo `regime_history`
- **集成**: fund_manager 与 risk_officer prompt 拼接当日 regime,让 LLM 知情(`{regime: "bull"}` 注入 system prompt 末尾)

- **实现**:
  ```python
  # backend/services/regime_detector.py
  from hmmlearn.hmm import GaussianHMM
  import numpy as np
  import joblib


  class ARegimeDetector:
      REGIMES = ("bull", "range_up", "range_down", "bear")

      def __init__(self, n_components: int = 4):
          self.model = GaussianHMM(
              n_components=n_components,
              covariance_type="diag", n_iter=200, random_state=42,
          )
          self._regime_order: list[int] = []  # 训练后排序

      def fit(self, features: np.ndarray, ret_series: np.ndarray) -> None:
          self.model.fit(features)
          # 用 ret_series 给 hidden state 排序: 平均 ret 高 → bull,低 → bear
          predictions = self.model.predict(features)
          state_means = []
          for s in range(self.model.n_components):
              mask = predictions == s
              state_means.append((s, ret_series[mask].mean() if mask.any() else 0.0))
          self._regime_order = [s for s, _ in sorted(state_means, key=lambda x: -x[1])]

      def predict_state(self, features: np.ndarray) -> str:
          state = int(self.model.predict(features[-1:])[0])
          rank = self._regime_order.index(state)
          return self.REGIMES[rank]

      def predict_proba(self, features: np.ndarray) -> dict[str, float]:
          probs = self.model.predict_proba(features[-1:])[0]
          return {self.REGIMES[self._regime_order.index(s)]: float(probs[s])
                  for s in range(self.model.n_components)}

      def save(self, path: str) -> None:
          joblib.dump({"model": self.model, "order": self._regime_order}, path)

      @classmethod
      def load(cls, path: str) -> "ARegimeDetector":
          d = joblib.load(path)
          inst = cls(n_components=d["model"].n_components)
          inst.model = d["model"]
          inst._regime_order = d["order"]
          return inst
  ```

- **Tests**:
  - Unit: 训练已知 bull 区间,predict ≥ 60% 是 bull;predict_proba 之和 = 1
  - Integration: cron run 1 次,state 写 Redis;`/api/monitoring/regime` 返回当日 state
  - Contract: hypothesis 输入随机 5 维 OHLC,断言 regime ∈ {4 状态}
  - E2E: 触发分析,grep LLM 调用日志含 `regime=` 字段

- **Pass thresholds**:
  - 历史 OOS test:regime 间日均 return diff > 1%
  - cron daily 7 天稳定
- **Pre-commit**: 5 轮 codex-review
- **Done**: OOS 报告 + commit

#### P5C-T06 — R3-M1 Playwright selector 稳定化 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: 无(独立)
- **截止**: 2026-05-23(已答应)
- **实现**: 同 `docs/phase5-next-work-plan-2026-05-01.md` §7.1
  - `frontend/src/views/AgentDebate.vue` 加 testid: `agent-debate-layout` / `stock-selector` / `start-analysis-button` / `analysis-history-list` / `analysis-history-item` / `debate-content`
  - `frontend/e2e/agent-debate.spec.ts` 改用 `getByTestId`,移除 `.debate-layout` / `.history-item` / `.stock-selector` class selector
- **Tests**: `npx playwright test e2e/agent-debate.spec.ts --workers=1`,4/4 全绿
- **Pass thresholds**: 全量 playwright pass rate ≥ 95%
- **Pre-commit**: 2 轮 codex-review(R2 UX + R3 testing)
- **Done**: commit

#### P5C-T07 — Portfolio e2e 3 个旧失败修复 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: 无(独立)
- **失败位置**: `frontend/e2e/portfolio.spec.ts:20/100/121` strict-mode selector 命中多个相同中文 label
- **实现**: 限定到 position-table / drawer 内的 locator chain
- **Pass thresholds**: portfolio.spec.ts 全绿 + 全量 playwright pass rate ≥ 95%
- **Pre-commit**: 1 轮 codex-review(R3 testing)
- **Done**: commit

#### Phase 5C 出口检查 [⏳ 待做]

- 6 维度夜检 cron 7 天无误报
- regime detector 7 天 OOS 稳定
- realistic vs price-only hit-rate diff ≥ 0.05
- 全量 playwright pass rate ≥ 95%
- 输出 `docs/reviews/phase5c-summary-2026-05-22.md`
- **STOP**

---

### Phase 5D — 多策略 A/B (P5-T02)(Week 3-4)

#### P5D-T01 — MultiStrategyOrchestrator + 3 候选策略并行账户 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: Phase 5C 全部 ✅
- **3 候选策略**:
  1. **strategy_baseline**: 当前 9-agent 完整 debate,kimi-thinking-on
  2. **strategy_lean**: triage-only(无 escalation),fast pipeline,debate 1 轮
  3. **strategy_consensus**: 仅当 bull/bear 两边一致(consensus)时下单,其他全 hold

- **实现**:
  1. 新建 `backend/services/multi_strategy.py`:
     ```python
     from typing import Protocol


     class StrategyAdapter(Protocol):
         strategy_id: str
         async def run(self, stock_code: str) -> "TradingSignal": ...


     class MultiStrategyOrchestrator:
         def __init__(self, strategies: dict[str, StrategyAdapter]):
             self._strategies = strategies

         async def run_all(self, stock_code: str) -> dict[str, "TradingSignal"]:
             """Run all strategies in parallel for one stock."""
             import asyncio
             tasks = {sid: asyncio.create_task(s.run(stock_code))
                      for sid, s in self._strategies.items()}
             return {sid: await t for sid, t in tasks.items()}
     ```
  2. 每 strategy 独立 paper account(Mongo collection `paper_accounts.{strategy_id}`)
  3. PnL/Sharpe 对比表前端 `frontend/src/views/MultiStrategyAB.vue`
  4. 任一 strategy 6 维度夜检 critical → orchestrator 自动 retire(从 strategies dict 删除)

- **Tests**: 3 strategies 对同股产出不一致信号;账户独立;PnL 算式对齐;orchestrator retire 流程

- **Pass thresholds**:
  - 3 账户跑 8 个交易日(Phase 5D 评估窗口)无 crash
  - Sharpe 对比报告 + 显著性检验(Wilcoxon 符号秩 / proportion z-test)
  - 任一 strategy critical → 自动 retire
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + A/B 报告 `docs/reviews/p5d-t01-ab.md`

#### Phase 5D 出口检查 [⏳ 待做]

- 3 策略 8 交易日 PnL 数据完整
- 输出 `docs/reviews/phase5d-summary-2026-05-29.md`
- **STOP**

---

### Phase 5E — 评估期收尾(Week 4 末)

#### P5E-T01 — 4 周评估期最终报告 [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: Phase 5D 全部 ✅
- **必含 7 项**:
  1. 4 周 cumulative return vs equal-weight benchmark + CSI300
  2. realistic hit_rate by action(买入/卖出/持有)+ by regime(bull/bear/range_up/range_down)
  3. 6 维度健康指标 4 周趋势曲线
  4. 三策略 A/B 结论 + 显著性 + retirement 建议
  5. **Howard Marks "luck vs skill" 测试**: bootstrap 1000 次 resample,Sharpe CI lower bound > 0.5 才算"非运气"
  6. **Cliff Asness 因子衰减检测**: factor_decay 30 天 vs 60 天 vs 90 天对比
  7. **López de Prado PBO 估计**: 简化版(Phase 6B 完整 CPCV 之前的预警)

- **输出**: `docs/reviews/phase5-eval-final-2026-05-29.md`
- **STOP**,等用户授权进入 Phase 6

---

### Phase 6A — JoinQuant 交叉验证 + MockBroker 校准(Week 5-6)

#### P6A-T01 — jqdatasdk 历史数据导入 + 离线回测(P5-T03) [⏳ 待做]

- **Owner**: 任意
- **Dependencies**: Phase 5E STOP 通过 + 用户授权
- **范围**: jqdatasdk 拉 2024-01-01 ~ 2026-04-30 全 watchlist 历史,strategy_baseline 在该区间离线回放,与 4 周评估期 Sharpe 对比
- **实现**:
  1. 新建 `backend/data/joinquant_loader.py`:登录 + 缓存 parquet 到 `data/jq_cache/{code}.parquet`
  2. 新建 `scripts/backtest_offline.py`:读 cache → MockBroker 执行 → 输出 metrics
  3. 与 Phase 5 评估期 metrics 比对,产出 `docs/reviews/p6a-t01-jq-backtest.md`

- **Pass thresholds**:
  - 2 年历史 Sharpe vs 4 周评估 Sharpe 偏差 < 30%
  - 否则触发"过拟合"警告,记录差异原因(可能是 regime mismatch / 样本偏差)
  - 单股回测 < 60s(缓存)
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + 回测对比报告

#### P6A-T02 — MockBroker fill realism 校准 [⏳ 待做]

- **Dependencies**: P6A-T01
- **校准维度**: slippage_bps + commission + 涨跌停拒单 + 停牌拒单 + 集合竞价价
- **实现**: jqdatasdk tick 数据回算 4 周评估期实际成交价 vs MockBroker 预测价,diff > 5bps 即调参
- **Pass thresholds**:
  - 校准后 fill realism diff 中位数 < 5bps
  - P95 diff < 15bps
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + 校准报告

#### Phase 6A 出口检查 [⏳ 待做]

- 输出 `docs/reviews/phase6a-summary-2026-06-12.md`
- **STOP**

---

### Phase 6B — VnPy 微结构 + walk-forward harness(Week 6-7)

#### P6B-T01 — T+1 / 涨跌停 / 集合竞价 fill model [⏳ 待做]

- **Dependencies**: 6A 全部
- **实现**: 借鉴 VnPy v4.0 微结构,扩展 `backend/broker/mock_broker.py`:
  - `_can_sell_today(position, today)` 强制 T+1
  - `_resolve_call_auction_price(code, date)` 集合竞价 9:25 价
  - `_check_suspended(code, date)` 停牌
  - 涨跌停已有,新增对 ChiNext/STAR 的 20%/30% 限幅 + ST 5%
- **Pass thresholds**: jqdatasdk 已知历史日期回放,所有 fill_status 与实际盘面一致(100% match)
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + match 报告

#### P6B-T02 — Walk-forward CPCV harness [⏳ 待做]

- **Dependencies**: P6B-T01
- **实现**: López de Prado purged CPCV
  - 5 折,每折训练区间 + purge gap(2d)+ test 区间
  - 输出 PBO(Probability of Backtest Overfitting)
  - 工具: 自实现或借 `mlfinlab.cross_validation.combinatorial_purged_kfold`
- **Pass thresholds**: PBO < 0.5(过拟合概率低于一半)
- **Pre-commit**: 5 轮 codex-review
- **Done**: commit + PBO 报告

#### Phase 6B 出口检查 [⏳ 待做]

- 输出 `docs/reviews/phase6b-summary-2026-06-26.md`
- **STOP**

---

### Phase 6C — ¥10k 微仓位实账户干跑(Week 8+,至少 4 周)

#### P6C-T01 — 真实券商 adapter interface(仅 stub) [⏳ 待做]

- **Dependencies**: 6B 全部 ✅
- **重要**: 仅接 interface,不在此阶段 commit 实盘 key
- **`AUTHORIZATION_MODE`**: 切 `confirm`(每单需用户点击确认)
- **实现**: `backend/broker/real_broker_iface.py` Protocol;`backend/broker/qmt_adapter_stub.py` 假实现(返回 mock 成交);Phase 7 才上 QMT 真实 adapter
- **Pass thresholds**: interface 单测通过;stub 不发出真实订单
- **Pre-commit**: 5 轮 codex-review(security 重)
- **Done**: commit

#### P6C-T02 — ¥10k 微仓位实账户干跑 [⏳ 待做]

- **Dependencies**: P6C-T01
- **硬阈值熔断**(任一触发即停):
  - 单日亏损 > ¥500(5%)
  - 累计 3 单连续亏损
  - 任一 strategy_health 维度 critical
  - LLM 日成本 > ¥3
  - 风控引擎拒单率 > 20%
- **持续**: 至少 4 周;每周输出干跑报告 `docs/reviews/p6c-week-{N}-{date}.md`

#### Phase 6C 出口(也是 Phase 6 结束) [⏳ 待做]

- 4 周干跑数据完整
- 输出 `docs/reviews/phase6c-final-2026-08-15.md`
- **STOP**:本计划结束,进入"是否升级生产"独立讨论(Phase 7 详细计划)

---

## 4. 风险登记(Risk Register)

| 风险 | 等级 | 概率 | 影响 | 缓解 |
|---|---|---|---|---|
| akshare 上游 API 频繁变化 | 高 | 高 | 中 | safe_fetch + 多源 fallback;24h 监控 |
| Kimi K2.6 thinking 成本超预期 | 高 | 中 | 高 | thinking.max_tokens 硬上限 + cost_guard 熔断 |
| triage→escalation 决策一致率 < 80% | 中 | 中 | 高 | 7 天 shadow 双线对比;不一致率超阈值回滚 |
| Fast/Slow watchlist cron 互扰 | 中 | 低 | 中 | apscheduler 单进程;fast 持锁,slow 排队 |
| HMM regime detector 在 2026 新数据失准 | 中 | 中 | 中 | 滑动 6 个月再训;切换告警人工复核 |
| jqdatasdk token 受限 / API 收费 | 中 | 中 | 高 | 缓存 parquet 减少调用;token rotation |
| MockBroker fill diff > 15bps | 高 | 中 | 高 | Phase 6A 强制校准;持续超阈值则推迟 6C |
| Phase 6C 实账户接入冒险 | 高 | 低 | 极高 | confirm-mode;¥10k 上限;5 项硬阈值熔断 |
| 自动跨阶段推进 | 高 | 低 | 高 | 每阶段 STOP;用户书面授权 |
| Codex CLI 离线 / API 超时 | 中 | 中 | 中 | review 报告本地保存草稿;网络恢复后重跑 |
| 春季躁动后(2-3 月)信号衰减 | 中 | 高 | 中 | factor_decay 维度持续监控;regime 切换告警 |
| 北向资金 / 龙虎榜数据源失效 | 中 | 低 | 中 | 多源备份;健康检查降级到 OHLC-only |

---

## 5. 计划自验证机制(每个新 session 接手时必跑)

**7 步检查**:

1. **读 SSoT**:
   ```bash
   cat /home/ps/papers/QuantMind/docs/phase5-eval-and-phase6-prep-master-plan.md | head -200
   ```
2. **找当前 Phase**:
   ```bash
   grep -nE "🔧 推进中|⏳ 待做" /home/ps/papers/QuantMind/docs/phase5-eval-and-phase6-prep-master-plan.md | head -10
   ```
3. **校验 git 状态对齐**:
   ```bash
   cd /home/ps/papers/QuantMind && git log --oneline -10
   ```
   比对最近 commit 的 `Task:` 字段是否对应文档已完成项。
4. **校验测试基线**:
   ```bash
   /home/ps/anaconda3/envs/zhanglan/bin/pytest -q --tb=no
   ```
   任何意外失败 → 先调查再继续。
5. **校验红线**:
   ```bash
   grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish" backend/risk/
   # 必须为空
   echo "AUTHORIZATION_MODE=$AUTHORIZATION_MODE QUANTMIND_PHASE=$QUANTMIND_PHASE"
   # 必须 suggest + 当前 phase
   curl -sk https://quantmind.local/api/health/detailed | jq '.data.status'
   # 必须 ok
   ```
6. **校验 cost ceiling 在线**:
   ```bash
   curl -sk https://quantmind.local/api/monitoring/budget | jq .
   # 必须返回 BudgetState JSON,status ∈ {ok, soft_breach, hard_breach}
   ```
7. **任务接手前**: 把待做 task marker 改为 🔧 推进中,commit message 体现 `Status: ⏳→🔧`

任一异常 → **不得继续推进**,必须在新一段 conversation 报告异常并等待用户。

---

## 6. Phase 7 纲要(仅记录预期方向,不做详细计划)

> Phase 6C 4 周干跑数据出来后再细化为详细 task。

预期方向 8 条:

1. **资金规模逐步放大**: ¥10k → ¥50k → ¥200k → ¥1M;每档放大需 4 周稳定数据 + 用户书面授权
2. **真实券商集成**: QMT(中泰)/CTP/华泰 etc.;实账户接入需走"金融合规 + 风险揭示书 + 投资者适当性测试"流程
3. **多账户策略仓库**: `strategy_registry` 模块,支持策略上线/下线/灰度发布/A/B promotion 自动化
4. **持续 A/B promotion 标准**: 候选策略需连续 8 周 Sharpe > baseline + 显著性 p < 0.05 + 6 维健康全 ok 才能 promote
5. **合规 + 审计日志保留**: 所有真实下单 + LLM prompt + 决策依据保留 5 年(中国证券基金业协会要求);加密存储
6. **实盘风险 dashboard**: 实时持仓 / 风险敞口 / 当日 PnL / 异常预警(钉钉 + 手机推送)
7. **灾备与故障切换**: 主备 backend(冷备主→热备 < 5min);Redis 持久化 + Mongo replica set
8. **税务与成本优化**: 券商佣金谈判;印花税 / 红利税核算自动化;并入 cost_tracker

预期触发 Phase 7 详细计划的条件:

- Phase 6C 干跑通过(4 周硬阈值熔断 0 次)
- 4 周累计 PnL ≥ 0(允许微亏 ≤ 5%)
- 6 维度健康全程 ok(无 critical 自动 retire)
- 用户书面授权进入 Phase 7

---

## 7. 附录

### 7.1 关键文件路径速查

| 模块 | 路径 |
|---|---|
| Master plan SSoT | `/home/ps/papers/QuantMind/docs/phase5-eval-and-phase6-prep-master-plan.md` |
| LLM router | `backend/llm/router.py` |
| LLM providers | `backend/llm/providers.py` |
| Cost tracker | `backend/llm/cost_tracker.py` |
| Cost guard(新建) | `backend/services/cost_guard.py` |
| Strategy health(新建) | `backend/services/strategy_health.py` |
| Regime detector(新建) | `backend/services/regime_detector.py` |
| Realistic evaluator(新建) | `backend/services/realistic_evaluator.py` |
| Multi-strategy(新建) | `backend/services/multi_strategy.py` |
| Watchlist policy yaml(新建) | `config/watchlist_policy.yaml` |
| Watchlist policy module(新建) | `backend/services/watchlist_policy.py` |
| Agent models config | `config/agent_models.yaml` |
| Risk engine | `backend/risk/engine.py` |
| MockBroker | `backend/broker/mock_broker.py` |
| Real broker iface(新建) | `backend/broker/real_broker_iface.py` |
| QMT adapter stub(新建) | `backend/broker/qmt_adapter_stub.py` |
| Analysis scheduler | `backend/data/analysis_scheduler.py` |
| Data scheduler | `backend/data/scheduler.py` |
| News crawler | `backend/data/news_crawler.py` |
| Signal evaluator | `backend/services/signal_evaluator.py` |
| Analysis graph | `backend/agents/graph.py` |
| Analysis records | `backend/agents/records.py` |
| Lifespan main | `backend/main.py` |
| Monitoring API | `backend/api/monitoring.py` |
| Performance API | `backend/api/performance.py` |
| AgentDebate view | `frontend/src/views/AgentDebate.vue` |
| Performance view | `frontend/src/views/Performance.vue` |
| Multi-strategy A/B view(新建) | `frontend/src/views/MultiStrategyAB.vue` |

### 7.2 测试命令速查

```bash
# Backend full
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-report=term-missing

# Risk engine only(必 >95%)
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q backend/risk --cov=backend/risk --cov-fail-under=95

# Frontend
cd /home/ps/papers/QuantMind/frontend
npm run type-check
npm run test -- --run
npx playwright test --workers=1 --reporter=line
npm run build

# Agent debate e2e(smoke)
npx playwright test e2e/agent-debate.spec.ts --workers=1 --reporter=line

# Codex review(major feature 5 轮)
# 在会话中: /codex-review --task=P5B-T01 --rounds=R1,R2,R3,R4,R5

# 红线静态检查
grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish" backend/risk/ && exit 1 || echo "ok"

# 健康/预算/regime/escalation 实时
curl -sk https://quantmind.local/api/health/detailed | jq .
curl -sk https://quantmind.local/api/monitoring/budget | jq .
curl -sk https://quantmind.local/api/monitoring/regime | jq .
curl -sk https://quantmind.local/api/monitoring/llm/escalations | jq .

# 日检
BASE_URL=https://quantmind.local ./scripts/daily-check.sh
```

### 7.3 阶段 STOP 标准模板

每阶段末 session 必须输出且用户必须回复授权才继续:

```text
[阶段名] STOP — 入口下一阶段需要您的明确授权。

入口条件:
- [ ] 当前阶段 summary 报告已生成: docs/reviews/{name}.md
- [ ] 测试基线全绿(pytest N / vitest M / playwright K/T,coverage 不下降)
- [ ] 退出指标全部达成(详见上文每阶段出口检查)
- [ ] 无 P0/P1 阻塞

如同意进入下一阶段,请回复:"授权进入 [下一阶段名]"。
否则请指明需要补做的事项。
```

### 7.4 修订记录(每次修改必填)

| 日期 | 修改人(session) | commit | 摘要 |
|---|---|---|---|
| 2026-05-01 | claude-opus-4-7-1m | (待 commit) | 初版 SSoT 落盘 |

### 7.5 联网调研引用源(节选,核心 12 条)

- TauricResearch/TradingAgents v0.2.4 — https://github.com/TauricResearch/TradingAgents
- Microsoft Qlib + RD-Agent — https://github.com/microsoft/qlib + https://github.com/microsoft/RD-Agent
- VnPy v4.0 — https://github.com/vnpy/vnpy
- FinMem (ICLR 2024) — https://github.com/pipiku915/FinMem-LLM-StockTrading
- HMM regime detection — https://github.com/Sakeeb91/market-regime-detection
- Kimi K2 thinking guide — https://platform.kimi.ai/docs/guide/use-kimi-k2-thinking-model
- xRouter cost-aware orchestration — https://arxiv.org/html/2510.08439v1
- Howard Marks Oaktree memos — https://www.oaktreecapital.com/insights/memo/the-best-of
- AQR Asness "Fact, Fiction and Factor Investing" — https://www.aqr.com/-/media/AQR/Documents/Journal-Articles/AQRJPMQuant23FactFictionandFactorInvesting.pdf
- López de Prado PBO — https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253
- Combinatorial Purged CV — https://www.quantbeckman.com/p/with-code-combinatorial-purged-cross
- Bridgewater All Weather — https://www.bridgewater.com/research-and-insights/the-all-weather-story

---

> **执行红线再次申明**:本计划 SSoT 一旦落盘,新会话必须先读 §5 自验证 7 步,再操作;任何阶段末 STOP 必须等用户书面授权;`AUTHORIZATION_MODE=suggest` + 风控引擎隔离 + 127.0.0.1 端口绑定三条红线在所有阶段不可越过。

> **风险声明**:任何 AI 交易系统都不能保证盈利。LLM 在金融预测上不具备确定性优势。本系统价值在于多视角分析框架与严格风控保护,不可替代人类判断。始终小仓位起步,数据驱动决策。
