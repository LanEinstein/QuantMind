# P5A-T02 R5 安全 + ops 维度最终复核

**判定**: ✅ 通过 (经最终复核 — Codex 独立运行 50 测试无 regression)

## 红线检查

| 红线 | 验证 |
|------|------|
| `backend/risk/` 未导入 backend.llm/agents/mirofish | grep 仅命中 docstring,实际 import 0 |
| `AUTHORIZATION_MODE=suggest` | .env 持有 (P5A-T03 将加 startup assertion) |
| `.env` 未入 git | 既有 .gitignore |
| 端口绑定 127.0.0.1 | 既有 deploy/quantmind-backend.service |

## 数据完整性保障

- `cost_rmb` 双层验证(parser + guard 层)防止伪造数据 bypass cap
- 即使 Redis 被错误写入(本机仅 127.0.0.1 可访问),corrupt entry 被 drop 而非 trusted
- `analysis_records.error` 加 `cost_ceiling_breached:` 前缀,审计可追溯

## ops 可观测性

- `cost_guard_invalid_spent` 错误日志 → 钉钉/grafana 告警
- `cost_soft_breach_active` 警告 → 操作员预警
- `daily_budget_breached` 错误日志 → 立即 page
- `GET /api/monitoring/budget` JSON → operator UI 实时面板

## 部署后跟踪 (Phase 5A 出口检查纳入)

- 24h 内无误熔断 (false positive `cost_ceiling_breached`)
- `/api/monitoring/budget` 返回值与实际 LLM spend 偏差 < 5%
- `cost_guard_invalid_spent` 日志数 == 0

完整记录见 [`p5a-t02-codex-review.md`](./p5a-t02-codex-review.md)。
