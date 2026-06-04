# 卖出重设计第 3 期(E4 时间止损 + E5 次日回补)— review 总结(2026-06-04)

> 范围:`P0-10-amendment-line2-2026-06-04-reentry-and-time-stop` 全部实现(triggers v11:STALE_EXIT + `evaluate_reentry_add_intents` / fired-store 卖价行扩展 + `delivered_sales` / runner 接线 / main.py 双 env)。
> 流程:codex 仍限额(19:19 实测)→ `/code-review` 回退(单 deep-agent,9 个定向问题全查)。

## 发现与处置(6 项)

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | **P1** | 日初 prune(7 天)先于回补资格读取,且长假后上一交易日可在窗外 → **春节/国庆后回补全日静默失效**(行先被删) | **已修**:读取先于 prune;prune 截止 = min(今日−7天, 上一交易日);+长假回归测试 |
| 2 | P2 | STALE 持有日数代理被帧深度(~30 收盘)+ episode 起算日截断 → 超长持仓的「入场后新高」检验只看观测窗;方向恒保守(只迟不早) | 接受 + amendment §1.1 披露 |
| 3 | P2 | bootstrap 宽限:激活日起算 → 真僵尸仓再等 ~10 交易日 | 接受 + 披露(owner 可 seed 真实入场日消除) |
| 4 | P2 | `sold_volume` 记建议单非实际成交;owner 未执行的派发卖单次日仍获回补资格(极端把仓位顶回卖前规模) | 接受 + 披露(镜像 D1-d「已派发未执行」caveat;15% 硬顶 + 人工 gate 双兜底;深修留 P0-4 回报链路) |
| 5 | P3 | `delivered_sales` volume 守卫只认 int,float 行静默失格 | 已修(镜像 price 守卫,接受 int/float) |
| 6 | P3 | `_reentry_sales_day` 死字段 | 已删 |

定向验证全过:held-days 代理只低估不高估(数学论证)/ stale-only 激活时 chandelier 路径 bit-for-bit / 同 tick SELL+回补被 sell_codes 滤掉 / 昨日 kind 不漏入今日 mutex(load_fired 按日)/ config hash 三态各异 / main.py env 接线。

## 门禁

- pytest 全量 4836 passed / cov 90.76% ✅;ruff 全绿 ✅;redline 全绿 ✅。
- 测试新增 12:STALE 三条件+四反例 / RE_ENTRY 七 gate(保护性不回补/窗口/折价/破位/全退/thesis/UTC tz)/ headroom clamp / runner 卖价落账 / 跨日回补端到端 / 同日互斥 / 长假 prune 回归 / config hash。
