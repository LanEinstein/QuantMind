# U-D10 代码审查总结 — 飞书 WS 入站 websockets 兼容修复

> 任务:`U-D10` — pin `websockets>=11,<14` 修复 lark-oapi 1.5.3 长连接入站接收器
> 日期:2026-05-30 · 审查方式:codex 撞额度(至 5-31 10:12)→ 回退 `claude /code-review high`(owner 既定)
> 审查范围:`git diff HEAD`(`pyproject.toml` + `tests/test_data_quality_probes.py`)

## 改动

1. **`pyproject.toml`** — 在 `lark-oapi` 依赖下新增显式约束 `websockets>=11,<14` + 解释注释。
   - 根因:lark-oapi 1.5.3 `ws/client.py` 用 legacy websockets 客户端 API(`websockets.connect` / `WebSocketClientProtocol` / `InvalidStatusCode`)。websockets 14.0 把 `connect` 改为新 asyncio 实现,15.0.1 下入站 receive loop 静默崩 `'NoneType' object has no attribute 'send'`(能发 HTTP API、不能收 WS 事件)。装到 13.1(legacy API native,无 deprecation)。
2. **`tests/test_data_quality_probes.py`** — `TestNonBlockingMarkers` 两个用例改用墙钟 `fresh = datetime.now(UTC)` 给行情打时间戳(替代 import 时刻常量 `NOW`)。
   - 预存 flaky(与 websockets 无关,15.0.1/13.1 下同样失败):probe 按真实墙钟算 quote age,全量套件跑到这两个用例时已过 5s staleness 阈值 → 误翻 `is_acceptable_for_buy_sell=False`。

## 审查结论

### 正确性(3 angle:line-by-line / removed-behavior / cross-file)— **0 bug**
- 版本约束正确:13.1 提供 lark 需要的全部 legacy 符号;与 `uvicorn[standard]`(`websockets>=10.4` 无上界)无冲突;`pip check` 干净。
- 测试改动正确且必要:P0-8 §2 红线 11(news/mirofish 非阻断)断言强度未削弱;两用例仍断言 breach=True + is_acceptable=True。
- cross-file:`grep backend/ 'import websockets' / 'websockets.'` 零命中——后端无代码用 websockets 14+ API,pin `<14` 不破任何东西。

### 清理 / altitude — 3 findings(全 **P3**,无 P0/P1/P2)
1. `fresh` 范式在两用例重复(可抽 helper/fixture)。
2. 模块级 `NOW`-as-fresh footgun 仍在(未来新用例可能重新踩坑)。
3. 同一根因两套补救约定并存:两个 sibling 用例(`test_provider_uses_news_probe_correctly` / `test_five_alive_no_outage`)当初是删掉 is_acceptable 断言,而本次两个用例改成 stamp fresh + 保留断言。

> 处置:三条均 P3 清理/一致性,无正确性/MEDIUM 问题;fix 已在正确 altitude(probe 故意用墙钟,test 侧 fresh 时间戳是对的,注入时钟反而错)。门禁(fix P0/P1/P2)已满足,直接提交;P3 留作后续可选统一(若再加非阻断 marker 用例时一并抽 helper)。

## 门禁
- clean-env 全量 pytest:**4160 passed / 13 skipped**(pin 后无回归;reinstall 15.0.1 复现同样 2 失败 → 证实 flaky 与本改动无关)。
- `ruff check`(改动文件)+ `scripts/redline-check.sh` 全绿。
