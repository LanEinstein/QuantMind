# Phase 5B Shadow-Test Deployment Runbook (2026-05-02)

> **目的**: 7 天部署窗口里采集 baseline (Kimi-only) vs routed (production triage→escalation) 的成对决策,完成 SSoT §6.972 出口 7 项指标的真值验证。
>
> **触发场景**: 你已经决定开启部署窗口、并接受这一轮的额外 LLM 成本(详见 §3 成本估算)。

---

## 1. 前置检查 (5 分钟)

```bash
# 进入项目目录
cd /home/ps/papers/QuantMind

# 确认 LLM key 已在 shell env(永远不入 .env / git)
echo "DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:0:8}***"
echo "DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:0:8}***"
echo "MOONSHOT_API_KEY=${MOONSHOT_API_KEY:0:8}***"
# 三条都必须显示 prefix*** ; 任一空 → 启动会失败

# 确认 redline 干净
grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish" backend/risk/
# 仅命中 docstring 即可

# 跑一遍 pytest 确认基线
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --tb=no
# 期望: 1139 passed, 11 skipped, 0 failed
```

---

## 2. 启动 7 天部署窗口

**关键 env 变量**(放进 `.env` 或导出到 shell session — 三个 LLM key 已在 `~/.bashrc`):

```bash
# 必填
export QUANTMIND_PHASE=phase5_eval                # 阶段红线
export AUTHORIZATION_MODE=suggest                 # suggest 模式不可越界
export QUANTMIND_DAILY_BUDGET=20.0                # 单日 ¥20 硬上限(cost_guard)

# Phase 5B 出口 shadow-test 专属 (新)
export QUANTMIND_SHADOW_ENABLED=1                 # 默认 0 不启;改 1 才开启 shadow_runner
export QUANTMIND_SHADOW_SAMPLE_RATE=1.0           # [0,1] 抽样率;1.0 = 全量(7 天数据要求)

# 可选(默认即可)
export MONGODB_URI=mongodb://127.0.0.1:27017      # `127.0.0.1` 红线
export MONGODB_DB=quantmind
export REDIS_URL=redis://127.0.0.1:6379/0
```

启动后端:

```bash
/home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000 \
  --host 127.0.0.1 \
  --workers 1   # WEB_CONCURRENCY=1 — shadow_runner 跨进程同步 deferred 到 P5C
```

**首启验证**(另一个 terminal):

```bash
# 1. 后端健康
curl -sk http://127.0.0.1:8000/api/health/detailed | jq .data.status
# 期望: "ok"

# 2. 预算守门
curl -sk http://127.0.0.1:8000/api/monitoring/budget | jq .
# 期望: {data: {status: "ok", spent_today: 0, daily_budget: 20.0, ...}}

# 3. 触发一次手动分析(任意一只 watchlist)
curl -sk -X POST http://127.0.0.1:8000/api/analysis/run \
  -H 'Content-Type: application/json' \
  -d '{"stock_code": "600519"}'
# 等 ~5-15 min(取决于 fast/slow bucket)

# 4. 验证 shadow_decisions 已落 Mongo
mongosh --quiet quantmind --eval 'db.shadow_decisions.countDocuments({})'
# 期望: ≥ 1
mongosh --quiet quantmind --eval 'db.shadow_decisions.findOne()' | head -30
# 应包含 baseline + routed 两个 leg
```

---

## 3. 成本估算

| 假设 | 值 |
|------|---:|
| Watchlist 大小 | 5 stocks |
| Fast cron 频率 | 4 次/日 |
| Slow cron 频率 | 1 次/日 |
| Routed 单股成本(P5B-T03 baseline) | ~¥0.20-0.50 |
| **Shadow 额外成本(每次 baseline replay,kimi-only thinking 8000)** | ~¥0.10-0.15 |
| **每股每日 shadow 增量成本** | (4 fast + 1 slow) × ¥0.12 ≈ **¥0.6** |
| **5 股每日 shadow 增量成本** | ~¥3.0 |
| **7 天 shadow 总增量成本** | ~¥21 |
| Routed 7 天总成本 | ~¥6-12 |
| **7 天总成本** | **~¥27-33** |

**预算保护**:
- `cost_guard` 单日硬上限 ¥20。如超 ¥20,routed 也会被 hard_breach 阻断,shadow_runner 也会同样 skip(`_budget_allows()` 在 hard_breach 返回 False)。
- 如要让 shadow 不抢 routed 预算,可临时拉高 `QUANTMIND_DAILY_BUDGET=30.0`(7 天可承受);或降低 `QUANTMIND_SHADOW_SAMPLE_RATE=0.3` 把 shadow 抽成 30%(代价:7 天样本量从 ~140 降到 ~42,统计置信度下降)。

---

## 4. 7 天监控

**每日早盘(09:00)**:
```bash
# 当日预算
curl -sk http://127.0.0.1:8000/api/monitoring/budget | jq .

# Routing escalation 比例
curl -sk http://127.0.0.1:8000/api/monitoring/llm/escalations | jq .

# Shadow 入库进度
mongosh --quiet quantmind --eval '
  db.shadow_decisions.aggregate([
    {$group: {_id: "$trade_date", count: {$sum: 1}}},
    {$sort: {_id: -1}}
  ]).toArray()
'

# 实时 shadow 中间报告(查看趋势,不打分)
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  python scripts/shadow_compare.py --days 7
```

**每日收盘(15:30)**:
```bash
BASE_URL=http://127.0.0.1:8000 ./scripts/daily-check.sh
```

**异常处理**:
- `shadow_skipped_backlog_full` 日志 — Kimi 调用变慢导致 backlog 满了,默认 cap=4,超过会丢样本。如频繁出现,把 `_MAX_INFLIGHT_SHADOW` 临时调大(代码改,需重启)。
- `shadow_baseline_call_timeout` — 单次 baseline > 900s,丢弃。如频繁,拉长 `_BASELINE_CALL_TIMEOUT_SEC` 或拉低 sample rate。
- `shadow_skipped_budget` — 当日预算紧,shadow 主动让位给 routed。预期行为。
- 红线告警 (suggest mode 越界、broker 真实下单等)— **立即 STOP** 并报告。

---

## 5. 第 7 天验收(出口判定)

```bash
# 完整 gate 报告(strict 模式: 任一 gate ❌ 则 exit 1)
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  python scripts/phase5b_exit_check.py --days 7 --strict
echo "Exit code: $?"
# Exit 0 → 全部 ✅
# Exit 1 → 至少一项 ❌ 或 ⚠️ no-data
```

**判定结果可能性**:

| Exit code | gates | 行动 |
|-----------|-------|------|
| 0 | ✅ × 7 | 报告 `phase5b-summary-2026-05-15.md` 中出口 7 项标记 ✅;等用户授权进 Phase 5C |
| 1 | 部分 ❌ | 检查具体哪项未达;如成本/延迟超阈值 → routing 设计需要调整(回到 P5B-T03 范畴);如一致率不达 → 检查 shadow_decisions 采样质量 |
| 1 | ⚠️ no-data | 数据采集不完整(deployment 中断 / shadow disabled);补采 |

如全 ✅:把 SSoT §6.972 出口标记 🔧 → ✅,填写最终 commit hash,生成「授权进入 Phase 5C」请求。

---

## 6. 关闭部署窗口

```bash
# 1. 停掉 shadow 收集(避免新 cron 触发)
unset QUANTMIND_SHADOW_ENABLED
# 或 export QUANTMIND_SHADOW_ENABLED=0

# 2. 停掉 backend
pkill -f "uvicorn backend.main:app"

# 3. 备份 shadow_decisions(7 天数据落档)
mongoexport --db quantmind --collection shadow_decisions \
  --out /tmp/shadow_decisions_$(date +%Y%m%d).json

# 4. 数据保留:30 天 TTL 索引会自动清掉旧记录,无需手动 drop。
```

---

## 7. 与 P5C 衔接的 deferred 项

shadow_runner 现在通过 **每次成功分析后 fire-and-forget** 触发,这意味着:
- ✅ 不需要单独的 cron job
- ✅ 不需要修改 watchlist
- ✅ 与 fast/slow 双 cron 自动兼容
- ⚠️ 但 cross-process 同步未实现 — 部署阶段必须 `--workers=1`(WEB_CONCURRENCY=1)。多 worker 部署在 P5C 会专门处理 Redis-backed reservation。

详见 `phase5b-summary-2026-05-15.md` §6 cross-cutting backlog,以及本次提交里 11 项已编入 P5C 的 task。
