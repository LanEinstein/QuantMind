# 卖出重设计第 0 期(Line-2 ops 加固)— review 总结(2026-06-04)

> 范围:`P0-10-amendment-line2-2026-06-04-intraday-ops-hardening` 全部实现(FiredTriggerStore / 双 runner dedup+告警接线 / 同日 SELL→ADD 互斥 / 告警词汇 +1)。
> 流程:codex cycle-1(1 finding)→ codex 撞额度(19:45 恢复)→ 按 [[feedback_codex_rate_limit_fallback]] 回退 `/code-review` high(5 角并行:line-scan / removed-behavior / cross-file / cleanup / altitude)。

## 确认并已修(全部入 commit)

| # | 级别 | 发现(来源) | 修复 |
|---|---|---|---|
| 1 | P2 | `send_failed`(owner 未收到)被记 ROUTED 并持久化 → 重启后保护性 SELL 全天被吞(codex cycle-1) | dedup 只认「确认送达」;send_failed 下一 tick 重试 |
| 2 | P2 | `skipped_duplicate`(outbox 已 SENT)不入 dedup → 已送达的 SELL 每 30s 重建+重持久快照(angle B/C 双确认) | 送达集合纳入 `skipped_duplicate` |
| 3 | P2 | send_failed 无界重试:飞书持续宕机 → N 码 × 每 30s 重建/重发/重持久,整 session 风暴(angle B) | `_MAX_UNDELIVERED_ATTEMPTS_PER_DAY=5` 封顶 → 进程内 dedup + error 日志;不持久(重启重试) |
| 4 | P2 | REJECTED 持久化后,拒单原因盘中消除/operator 修复重启也无法重试(angle A;旧恢复手册被破坏) | **持久层=仅送达**;REJECTED 仅进程内 dedup |
| 5 | P1(结构) | `Line2DailyRunner` 既无重启 dedup 也无 REJECTED 告警——同一事故类在 09:35 日线路径原样复现(misfire_grace 重跑 + 新铸 id 不可 outbox 去重)(altitude) | daily runner 接同一 store + hook;新增 `SellRouteOutcome.DEDUP_SKIPPED`;测试×2 |
| 6 | P3 | store 无限增长 + 每日全量重扫(angle A/B/cleanup 三确认,慢性) | `prune_before`(保留 7 日),当日首 tick 调用 |
| 7 | P3 | 空 code/kind 损坏行经互斥误压 ADD(angle B) | load 校验跳过 + 测试 |
| 8 | P3 | main.py hook 在 dispatcher 未就绪时静默 no-op(angle A/C 双确认) | warning 日志(绝不静默) |

## 评审后接受不改(记录在案)

- **EARLY_RETURN SELL 不进同日互斥**(angle A):冻结解除后 SELL 条件若仍成立会同 tick 重发并压制 ADD(自愈);条件不成立则 ADD 不矛盾。REFUTED。
- **未送达 SELL 不压同日 ADD**(angle C):退出建议从未到达任何人;且飞书宕机时 ADD 也发不出。接受,语义一致。
- **JSONL plumbing 与 takeprofit_ledger 重复 ~40 行**(cleanup):fail-open/fail-closed 语义刻意相反,合并会模糊安全边界;接受重复。
- **FileLock 同步写在 async tick 内**(cleanup):与 takeprofit_ledger 既有先例一致,量级极小;不改。
- **dry-run 不注入 store**(angle C):默认 None 即正确;无 namespacing 风险现实可达路径。

## 门禁

- pytest 全量 4794 passed / 13 skipped / cov 90.82%(≥70)✅;`backend/risk` 未触碰。
- ruff check 全绿 ✅;redline-check 全绿 ✅(`InstructionPlan(` 构造集未扩大;告警词汇 +1 经 amendment)。
- 测试新增 14:store ×9 + intraday runner ×7(重启 dedup / 跨日 / 互斥单向 / REJECTED 告警+不持久 / send_failed 重试+封顶 / hook 异常)+ daily runner ×2。
