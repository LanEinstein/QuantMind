# QuantMind Phase 5 Code Complete Checkpoint

**Date**: 2026-04-13
**Branch**: `main`
**Latest Commit**: `3665edd`
**Author**: LanEinstein

---

## 项目进度总览

| Phase | 内容 | 状态 |
|-------|------|------|
| Phase 1 | 后端核心架构 (LLM Router, Risk Engine, Mock Broker) | ✅ 完成 |
| Phase 2 | 多Agent分析管线 (9-Agent LangGraph Pipeline) | ✅ 完成 |
| Phase 3 | MiroFish 群体智能仿真 | ✅ 完成 |
| Phase 4 | 前端6个页面 (Dashboard, Debate, MiroFish, Portfolio, Performance, Settings) | ✅ 完成 |
| Phase 5 | 建议模式基础设施 (P5-T01) — **代码部分** | ✅ 完成 |
| Phase 5 | 部署 + 4周实盘评估 — **运营部分** | ⏳ 待启动 |
| Phase 5 | P5-T02 多策略A/B测试 | 📋 计划中 (第3-4周) |
| Phase 5 | P5-T03 JoinQuant交叉验证 | 📋 计划中 (第4周+) |

---

## P5-T01 交付清单 (7/7 Gap 全部完成)

### Session A — 2026-04-13 上午

| Commit | Gap | 交付物 | 新增测试 |
|--------|-----|--------|---------|
| `dc571d9` | — | 清理 frontend/coverage/ 等构建产物, 更新 .gitignore | — |
| `d4854cf` | G2 | 信号持久化: `save_signal`, `query_signals`, `get_signal_by_id`, 唯一索引 `(stock_code, trade_date)`, 自动持久化到 POST /api/analysis/stock, 查询端点 GET /api/analysis/signals | 9 |
| `d4854cf` | G1 | 自选股服务: `WatchlistService` (软删除), REST API (5个端点), 每日分析调度器 `AnalysisScheduler` (09:45 CST cron, 限速10s, 容错续跑, Redis发布) | 16 |

### Session B+C — 2026-04-13 下午

| Commit | Gap | 交付物 | 新增测试 |
|--------|-----|--------|---------|
| `41a56a8` | G3 | CSI300基准数据: `get_index_history()` (akshare), `save_index_prices`/`get_index_prices`, 替换平线基准为真实沪深300, 前向填充缺失日期 | 6 |
| `41a56a8` | G4 | 信号准确率评估: `SignalEvaluator` (买入涨=正确, 卖出跌=正确, 持有排除), GET /api/analysis/signal-accuracy | 6 |
| `41a56a8` | G5 | 详细健康监控: GET /api/health/detailed, 检查 MongoDB/Redis/LLM/调度器/运行时间, ok/degraded/critical 三级状态 | 5 |
| `41a56a8` | G6 | LLM成本持久化: `flush_to_mongodb()` Redis→MongoDB, `cost_tracking` 集合, 唯一索引 `(date, agent, provider)` | 4 |
| `41a56a8` | G7 | 结构化日志: structlog JSON 配置, `TimedRotatingFileHandler` 每日轮转30天保留, Docker logs volume | 3 |
| `3665edd` | G4 | Codex审查修复: 跳过未到达评估窗口 (`trade_date + horizon > today`) 的信号 | — |

---

## 测试统计

| 模块 | 测试数 | 状态 |
|------|--------|------|
| 后端 (pytest) | 580 | 577 通过, 3 已知遗留失败 (test_hidden_variable_extraction) |
| 前端 (vitest) | 81 | 全部通过 |
| **总计** | **661** | — |

遗留失败说明: `test_hidden_variable_extraction.py` 中 3 个测试断言 `"simulated crowd wisdom" in hv.reasoning`, 但 mock 数据返回中文推理文本。非本次变更引入, 需单独修复 mock 数据。

---

## 新增文件清单

### 业务模块 (6个)
```
backend/data/watchlist.py           — 自选股服务 (MongoDB 软删除)
backend/data/analysis_scheduler.py  — 每日分析调度器 (APScheduler cron)
backend/api/watchlist.py            — 自选股 REST API (5个端点)
backend/api/health.py               — 详细健康监控端点
backend/services/signal_evaluator.py — 信号准确率评估服务
backend/logging_config.py           — structlog JSON 日志配置
```

### 测试文件 (8个)
```
tests/test_signal_persistence.py    — 信号持久化 (9 tests)
tests/test_watchlist.py             — 自选股服务 (6 tests)
tests/test_analysis_scheduler.py    — 分析调度器 (10 tests)
tests/test_benchmark_data.py        — 基准数据 (6 tests)
tests/test_signal_evaluator.py      — 信号评估 (6 tests)
tests/test_health_detailed.py       — 健康监控 (5 tests)
tests/test_cost_persistence.py      — 成本持久化 (4 tests)
tests/test_logging_config.py        — 日志配置 (3 tests)
```

### 修改文件 (8个)
```
.gitignore                          — +4 patterns (coverage, components.d.ts, logs)
backend/data/database.py            — +3 collections (trading_signals, index_prices, cost_tracking)
backend/data/market_data.py         — +get_index_history()
backend/api/analysis.py             — +信号持久化 + /signals + /signal-accuracy
backend/api/performance.py          — +真实基准曲线 (前向填充)
backend/llm/cost_tracker.py         — +flush_to_mongodb()
backend/main.py                     — +watchlist/scheduler/health 初始化
docker-compose.yml                  — +logs volume
```

---

## API 端点清单 (新增 8个)

| Method | Path | 功能 |
|--------|------|------|
| GET | `/api/watchlist` | 列出活跃自选股 |
| POST | `/api/watchlist` | 添加自选股 |
| DELETE | `/api/watchlist/{code}` | 移除自选股 (软删除) |
| POST | `/api/watchlist/analyze-now` | 手动触发全部自选股分析 |
| POST | `/api/watchlist/analyze/{code}` | 手动触发单只股票分析 |
| GET | `/api/analysis/signals` | 查询历史交易信号 |
| GET | `/api/analysis/signal-accuracy` | 信号准确率评估 |
| GET | `/api/health/detailed` | 详细系统健康状态 |

---

## MongoDB 集合清单 (新增 3个)

| 集合 | 唯一索引 | 用途 |
|------|---------|------|
| `trading_signals` | `(stock_code, trade_date)` | 每日交易信号持久化 |
| `index_prices` | `(index_code, date)` | 指数历史价格 (CSI300) |
| `cost_tracking` | `(date, agent_name, provider)` | LLM 调用成本持久化 |

已有集合: `market_realtime`, `kline_daily`, `financial_data`, `news_articles`, `simulations`, `watchlist`

---

## 定时任务清单

| 调度器 | 触发器 | 时间 | 功能 |
|--------|--------|------|------|
| DataScheduler | interval | 每30s (交易时段) | 市场行情采集 |
| DataScheduler | interval | 每300s | 新闻采集 |
| AnalysisScheduler | cron | 09:45 CST Mon-Fri | 自选股每日分析 |
| 待接入 | cron | 15:30 CST Mon-Fri | CSI300 指数收盘价采集 |
| 待接入 | cron | 23:00 CST | LLM 成本 Redis→MongoDB 刷新 |

注: 后两个 cron 任务的代码已实现 (database 方法 + flush_to_mongodb), 但 DataScheduler 的 `start()` 中尚未注册这两个 job, 需在部署时补充或下一迭代添加。

---

## 下一步: 部署 & 启动评估

### 立即可做 (Step 3: 基础设施部署)

1. 配置 `.env` 文件 (API Key, MongoDB URI, Redis URL, `AUTHORIZATION_MODE=suggest`)
2. `docker compose up -d mongodb redis`
3. `uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload`
4. `cd frontend && npm run dev`
5. 连通性测试: `curl http://localhost:8000/api/health/detailed`

### 评估启动 (Step 4: 自选股 & 4周评估)

1. 添加 5-10 只初始自选股 (POST /api/watchlist)
2. 手动触发一次分析 (POST /api/watchlist/analyze-now)
3. 验证信号已持久化 (GET /api/analysis/signals?days=1)
4. 开始每日监控 (日志、健康、成本)

### 后续迭代 (P5-T02 / P5-T03)

- **P5-T02** (第3-4周): 多策略 A/B 测试 — 3个虚拟账户对比不同模型组合
- **P5-T03** (第4周+): JoinQuant 交叉验证 — 对比 MockBroker 与聚宽模拟盘

### 遗留技术债

| 项 | 优先级 | 说明 |
|----|--------|------|
| DataScheduler 注册 index + cost_flush 两个 cron job | HIGH | 代码已实现, 需在 scheduler.py 的 start() 中添加 |
| 修复 test_hidden_variable_extraction 3个遗留失败 | MEDIUM | mock 数据中的 reasoning 文本需包含 "simulated crowd wisdom" |
| SignalEvaluator N+1 查询优化 | LOW | 当前可接受, 信号量级 <100 时无性能问题 |
| performance.py 中 points 列表原地修改 | LOW | 与已有代码模式一致, 非回归 |

---

## 代码审查记录

| 审查方式 | 时间 | 发现 | 修复 |
|---------|------|------|------|
| Codex CLI (gpt-5.4) | 2026-04-13 | 2 个 P2 | 1 已在前一 commit 修复 (前向填充), 1 当场修复 (horizon 检查) |
| Claude Code 自审 6维度 | 2026-04-13 | 9 个 (2 CRITICAL, 4 WARNING, 3 INFO) | 2 修复, 5 误报排除, 2 INFO 留存 |
