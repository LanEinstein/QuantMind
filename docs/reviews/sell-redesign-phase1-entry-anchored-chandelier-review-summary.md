# 卖出重设计第 1 期(E1+E2 入场锚定 chandelier)— review 总结(2026-06-04)

> 范围:`P0-7-amendment-2026-06-04-entry-anchored-chandelier` 全部实现(`derive_entry_anchored_stop` / triggers v9 / `PositionEpisodeStore` / runner 接线+shadow / main.py env / 报告脚本)。
> 流程:codex 持续撞额度(两次尝试)→ 按 [[feedback_codex_rate_limit_fallback]] 回退 `/code-review` high(3 角并行:line-scan / maths-counterfactual / cross-file+altitude)。codex 恢复后下一 commit 起回归 codex 门。

## 确认并已修(全部入 commit)

| # | 级别 | 发现(来源) | 修复 |
|---|---|---|---|
| 1 | **P1(PIT)** | TAKE_PROFIT record 的 `atr_stop_mult` 被写成 chandelier 层乘数(3.0),但 TP 目标价的 R-unit 恒为静态 2.0 → 离线 replay 重算目标价错 50%,直接破坏 bit-exact 契约(maths+line-scan 双确认) | record `atr_stop_mult` 恒写静态 R-unit;chandelier 层乘数独立新键 `trailing_stop_mult` |
| 2 | P2 | 确认窗 `tick_time.hour*60+minute` 无时区归一 → UTC-aware 调用方(测试/replay/重构)会把窗静默漂移 8 小时,浅破止损永不触发 | 镜像 `is_trading_hours` 约定:naive→视为上海,aware→astimezone;+UTC 同时刻回归测试 |
| 3 | P2 | shadow 的 `would_fire_new` 是裸 breach,未含 E2 深破/确认窗门控 → 影子报告夸大新止损触发数,owner 可能据此误判 | 改为 `would_breach_new`+`deep_breach_new` 双字段;报告脚本/文档披露语义(只有 deep 行保证 live 必触发) |
| 4 | P2 | `recent_high` 字段在锚定 ATR record 上被改写成 anchor,与同 tick 其他 kind record 的 v8 语义分叉(跨 kind 溯源歧义) | `recent_high` 全 record 恒持 v8 语义(窗口高);anchor 统一只在 `stop_anchor` |
| 5 | P2(设计) | episode store 仅在 env 开启时构建/同步 → 若 shadow 结束后数周才激活,所有锚从激活日起算(静默低锚断崖) | main.py **无条件**构建,runner 凡 store 注入即每 tick 同步(决策零影响);env 只控评估器喂入与影子日志 |
| 6 | P2(文档陷阱) | amendment 运维手册 seed 示例用 `event_type:"episode_opened"`,代码只认 `opened` → owner 照手册 seed 会被当损坏行静默丢弃 | amendment 修正为 `opened`/`closed` + 显式警告;store fold 测试覆盖 seed 优先 |
| 7 | P3 | 停牌缺口使交易日计数 n 偏大 → 锚膨胀 → **更早**退出;原 docstring 误称 initial-stop 兜底(方向写反) | docstring/amendment 改为诚实方向披露(资本保守向、人工 gate 兜底、接受残余) |
| 8 | P3 | 缺「短历史新仓锚定可触发」测试(v8 recent_high=None 永不触发的新可达态) | 新增回归测试(16 收盘 < 20 窗,锚定深破触发,v8 不触发) |
| 9 | P3 | `deep_band_atr` 未 float() 包裹(风格不一致) | 已包裹 |

## 评审后接受不改(记录在案)

- **大涨后 3.0× 给回多于旧 2.0× 窗口线(约 +1.0×ATR)**:owner 拍板 3.0×(让利润奔跑)对 2.5× 的直接后果,amendment §1.3 已显式披露,shadow 报告会呈现。
- **确认窗止于 14:55 的死区(14:55-15:00)**:刻意——飞书人工执行需 ≥2 分钟下单窗;14:57 后是收盘集合竞价。amendment §1.3 已记录理由。
- **episode 损坏 closed 行 + 服务停机期间换仓的 stale-open 残余**:正常运行下次 tick 的 sync 会自动补 closed;仅「停机窗口内完成清仓+回买」可逃逸,损坏行有 error 日志。接受。

## 门禁

- pytest 全量 4815 passed / 13 skipped / cov 90.79%(≥70)✅;ruff check 全绿 ✅;redline-check 全绿 ✅。
- 测试新增 14:calibration ×4 + triggers ×8(反事实/深浅破/确认窗/tz/短历史/v8 bit-for-bit)+ episode store ×5(独立文件)+ runner ×3(config hash / episode sync / shadow 幂等)。
- v8 bit-for-bit:`chandelier=None` 路径全部既有测试不动全绿。
