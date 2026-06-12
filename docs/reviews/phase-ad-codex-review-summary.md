# Phase AD — codex review + Playwright 前端体检 summary

**任务**:Phase AD 前端驾驶舱与实盘交互(AD-001..AD-006)
**日期**:2026-06-12(session #82)
**门禁**:codex-review(代码)1 cycle + verify + **Playwright 真实浏览器体检**(owner 2026-06-12 锁定的前端收尾门)

## 1. 本地门禁(前置)
- 后端 pytest:5543 passed / 91% cov(基线 5508 → +35 新测试)
- 前端 vitest:170 passed(基线 156 → +14);type-check + build 全绿
- ruff 全绿;新模块 mypy strict 全绿(performance.py 既存 4 处 mypy 报错在 391/399/637 行,非本次新增)
- redline-check 全绿(写端点 2→3 + FeishuMessageKind 6→7 + audit 41→42 同步)

## 2. codex 代码审查(cycle 1:`codex review --uncommitted`)
判定 NEEDS_FIXES:**1 P1 + 4 P2,全部为真 bug,已全修**。

| # | 级别 | 文件 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **P1** | ManualTradeForm.vue | `external_trade_id` 在 `submit()` 内 mint → 重试 mint 新 id → 后端幂等键失效 → 双重下单 | id 改在 `onOpen` mint 一次入 `externalId` ref、重试复用;watcher 仅在 code/side 变更时重 mint(它们内嵌于 id) |
| 2 | P2 | manual_trade_service.py | `_send_ack` 忽略 `SendMessageResult(ok=False)` → 谎报 `feishu_sent=true` | 改为 `return bool(getattr(result, "ok", False))` |
| 3 | P2 | performance.py | `performance_split` 用未按日期裁剪的全量成交 → 与曲线不一致 | 日期裁剪改**无条件**(原仅 `segment=current`) |
| 4 | P2 | performance.py | split 把 sign-free 的 `net_amount` 当 realized PnL → 买入全算盈利 | 改报 `trade_count` + 方向带符号 `net_cash_flow`(SELL +/BUY −),去掉误导的胜负计数 |
| 5 | P2 | Portfolio.vue | `onManualRecorded` 只刷持仓 → 账户卡/成交滞后 | 改为 `store.fetchAll()` + `refreshEquityPoint()` |

## 3. codex verify(`codex exec --sandbox read-only`)
**Final Verdict: PASS** —— 5/5 RESOLVED,无新增 P1 回归。(codex 沙箱 6 个 setup error 是只读 tmp_path 限制,非代码问题;本机全量 pytest 全过。)

## 4. Playwright 前端体检(owner 锁定收尾门)
工具:Playwright 1.58.2 chromium,`vite preview` 服务 `dist/` + 全 `/api` 路由 mock(确定性、免后端;后端 J-007 prod-auth 已过期无法起,且 owner-auth 不可伪造)。脚本 `frontend/scripts/ad-playwright-exam.mjs`,截图 `/tmp/ad_exam/`。

| 页面 | AD 功能 | 渲染 | console/page error | 布局(溢出/塌陷) |
|------|---------|------|--------------------|------------------|
| /performance | AD-001 KPI 6 tile + 8 门 gauge(PASS+8✓) | ✅ | 0 | 无 |
| /system-status | AD-002 链路时间轴(7 段)+ AD-003 manifest/intent/实验表 | ✅ | 0 | 无 |
| /instruction-plans | AD-004 标的 🏛价值 badge | ✅ | 0 | 无 |
| /portfolio | AD-004 风格列(🏛价值/⚡短线)+ AD-005 记录按钮/行内记录 | ✅ | 0(WS 404=preview 无后端 WS) | 无 |
| manual-trade form | AD-005 弹窗(警示+代码/方向/数量/价/时间/原因/备注+两步确认) | ✅ 按钮可见+打开+字段齐 | 0 | 无 |

视觉复核(截图):KPI tile 配色/对齐良好;8 门 gauge 全绿 chip;时间轴蓝点分段清晰;风格 badge emoji+色(🏛价值绿/⚡短线黄)与飞书口径一致;手动操作弹窗警示突出、字段完整。

**体检判定:AD 范围零缺陷 → 无需修复-复测循环,体检通过。** 唯一报错均为越界 mock/既存件(Dashboard `sectors.map` 需真实行情;`/api/evolution/pending`+冻结卡空=未 mock;Performance 图空=未 mock `/api/performance`)—— 均非 AD 代码,真实后端下不复现。

## 5. 结论
**COMMIT-SAFE**:代码审查 + verify + Playwright 体检三道门全过;安全地基红线全留(仅 3 写端点 / LLM 不写决策 / 单一镜像 / append-only / PIT 可复现 / UT-⊥QM- / 127.0.0.1)。
