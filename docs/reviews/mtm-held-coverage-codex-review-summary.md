# Codex Review — MTM held-position coverage gap (#63 leftover)

> 任务: 让 30s 行情采集覆盖 broker 在持仓,使 intraday MTM 能给在持仓标价
> Amendment: `P0-8-amendment-2026-06-03-collect-held-positions`
> 变更文件: `backend/data/scheduler.py` / `backend/main.py` / `tests/test_scheduler.py` / `tests/test_held_position_codes.py`
> 命令: `codex review --uncommitted`（cycle 1 + cycle 2 verify;均前台 `</dev/null`,codex-cli 0.133.0）

## Cycle 1 — 1×P1（已修)

- **[P1] 采集→MTM 消费断链** — `backend/data/scheduler.py`
  union 把在持码收进 30s 采集集,但采集产物**不被 `MongoBackedMarketMetaProvider` 读取**:
  - tier-1 Redis `quote:{code}`:`_cache_quotes_to_redis` 写的 blob 时间字段是 `snapshot_at`,而 `_parse_redis_quote` 要求 `timestamp` → `KeyError` → 恒 miss。
  - tier-2 Mongo:30s 采集落 `watchlist_market_snapshots`,provider 读 `market_realtime`(仅指数 000300)→ 个股恒 miss。
  - ⇒ 仅 union 不足以让在持仓被标价;原 `intraday_mtm_build_failed` 照旧。**绿测试只断言 union、没断言消费侧**(印证 [[feedback_codex_findings_real]]「绿测试 ≠ 闭环可用」)。

  **修复**(amendment §1.4):`_cache_quotes_to_redis` **非破坏性**镜像 `timestamp = snapshot_at`(保留 `snapshot_at`)→ 恢复 provider 文档化契约 `quote:{code}` = `{"price","timestamp"}` → tier-1 对每个被采集码(含在持)出新鲜价。新增 **producer→consumer 往返测试**(`_cache_quotes_to_redis` 产物喂进真 `_parse_redis_quote` 断言出价),补上 codex 指出的消费侧盲区。
  tier-2(Mongo `market_realtime` 个股行)= **既有缺口**(先于本任务,非本改动引入),列「安全硬化窗口」follow-up,正常运行下 tier-1 Redis(30s 刷新)已足。

## Cycle 2 — verify（COMMIT-SAFE)

> "No discrete correctness issues were found in the staged, unstaged, or untracked changes. The held-position union and Redis quote timestamp contract are covered by targeted tests and do not appear to break existing behavior."

P1 已解决,无新增问题,无回归。

## 门禁

- 全量 pytest(`FEISHU_INTERACTIVE_ENABLED=false`):**4606 passed / 13 skipped**(基线 4590 → +16 新测试)、coverage **90.61%** > 70%。
- ruff(4 改动文件)clean;`scripts/redline-check.sh` ALL PASS。

## 安全地基(一条未破)

数据层不 import `backend.broker`(只收注入 `Callable`);held_codes_provider fail-open(infra glitch,非 data corruption);PIT 可复现(在持码同走 `get_watchlist_snapshot`,同存原始行 + checksum + 同一 `snapshot_at`);数据源 Tushare-Sina 主 + adata 备不变;阈值不放宽;LLM 不进数据路径;数据成本不设 ceiling;`quote:{code}` 唯一消费者 = provider,blob 改动非破坏性。
