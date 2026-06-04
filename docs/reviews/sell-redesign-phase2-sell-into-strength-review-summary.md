# 卖出重设计第 2 期(E3 强势卖出触发族)— review 总结(2026-06-04)

> 范围:`P0-10-amendment-line2-2026-06-04-sell-into-strength` 全部实现(triggers v10:4 kinds + `StrengthSellConfig` + `limit_up_price` + `SEALED_LIMIT_HOLD` / runner 透传+record / main.py env)。
> 流程:codex 仍限额 → `/code-review` 回退(单 deep-agent 综合角:line-scan + maths + cross-file,8 个定向问题全查)。

## 发现与处置(4 项,全轻微)

| # | 级别 | 发现 | 处置 |
|---|---|---|---|
| 1 | P3 | VOLUME_CLIMAX 无时段守卫:累计额判据越近收盘越敏感 | **接受为设计**(3× 全日均额在任何时段都是真天量;滞涨+高位双条件 + 盈利前置 + 人工 gate);amendment §1.1 增披露条目 |
| 2 | P3 | 模块级 `_STRENGTH_KINDS` 死代码(逻辑走 if/elif 链,runner 有自己的类级 tuple) | 已删除 |
| 3 | P3 | `_strength_record_params` 重算 limit_up 依赖「同一 spot 对象贯穿」隐式耦合 | 已加 INVARIANT 注释(record 永不接受重取报价) |
| 4 | P3 | 单手即超 ¥50k 的极高价票 strength 整族跳过(镜像 thesis 先例) | **接受**(universe 极罕见);amendment §1.1 显式披露 |

定向验证全过:thesis `continue` 重构无遗漏 / sealed 边界 / tranche round 语义 / amounts 为日频全日额(parse_held_series 同源)/ SURGE_FADE 与 LIMIT_BREAK 优先级一致 / main.py 接线顺序 / strength=None bit-for-bit(`is_long_term` 提前定义无行为差)。

## 门禁

- pytest 394(monitoring+orchestration)+ 全量 4825 passed / cov 90.77% ✅;ruff 全绿 ✅;redline 全绿 ✅。
- 测试新增 11:limit ratio 前缀 / 炸板无盈利门 / 冲高回落盈利门双例 / 放量滞涨 / 乖离 / 封死抑制 TP+E3 但硬顶 trim 照触发 / 长持豁免 / 保护性优先 / 1 手跳过 / v9 bit-for-bit。
