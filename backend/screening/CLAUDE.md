# backend/screening/ — 子任务上下文(Phase L)

> 状态:**done**(L-002 screener+factors / L-005 模块契约+隔离测试)。治理:[P0-9-amendment-2026-05-24](../../docs/decisions/P0-9-amendment-2026-05-24-full-market-screening.md)。任务:plan.html L-002 / L-005。

## 职责
**全市场纯量化初筛**(Line 1 入口):读 K PIT 快照 → 排除四件套硬排除 → Alpha158 子集因子 → 确定性截面排名 → top-N(~50-100)。**0 LLM**;LLM 仅在本模块出 top-N 小包之后(Phase M)介入,永不见全市场、永不扩 shortlist。

## 模块结构(已实现)
| 文件 | 内容 |
|------|------|
| `factors.py` | 纯 stdlib Alpha158 子集(`momentum_20d`/`ma_ratio_5_20`/`volatility_20d`/`rsi_14`/`avg_amount_20d`);历史不足返 `None`(不臆造);`compute_factors` → frozen `FactorVector`。无 qlib 依赖 → PIT replay bit-stable。 |
| `screener.py` | `Screener.screen(snapshot, signal_id) -> ScreenResult`:CSV market-frame 解析 → fail-closed 排除(首中)→ 因子 → 截面百分位加权 composite → top-N(score desc, code asc tie-break)→ 写 `SignalInputManifest`(每解析行皆消费行 + `config_hashes["screening_config"]`)。 |

## 输入契约
快照 payload = UTF-8 CSV,一行一码(→ 一 `ConsumedRow`):`ts_code,name,listed_trading_days,closes,amounts`(`closes`/`amounts` 为 `|` 分隔,oldest→newest)。编排层从 Tushare `daily`/`daily_basic`/`stock_basic` 组装该 frame 后调 screener。

## 本模块红线
1. 排除四件套(新股≤30 / 次新≤180 / 流动性<2亿 / 单价>500)**硬排除 + fail-closed**;`stock_meta` 缺失 / 历史<21 / 缺价 / 非有限 / 不可评分 → 一律排除,不乐观保留。Builder 第五道早返保留为最后防御,**同一 `exclusion_rules` 真相源**(universe_policy)。
2. 科创 688 / 北交 8 / ST / 可转债 **永禁**(复用 `classify_board` `ForbiddenCodeError` + `is_st_name` + board 白名单)。
3. universe = board 白名单规则(非 13-code 写死);board ∉ 白名单 → `board_not_whitelisted`。
4. 读 K 快照 + 写 `SignalInputManifest`(消费行血缘 + 配置哈希),同快照同配置 bit-exact 同 shortlist。
5. top-N 固定上限;超数由确定性 tie-break 收窄,**不调 LLM 补名**。
6. 非有限 token(nan/inf)/ 重复码 → fail-closed(全副本丢弃 + 计 malformed)。

## import 隔离
**严禁** `import backend.{llm,agents,mirofish}`(redline-check `[L-002]` + `tests/screening/test_module_contract.py` AST + ruff TID251 三重守门)。可用:`backend.{marketdata_snapshot, data, services.universe_policy}` + 标准库。`backend.data.stock_metadata` 经 per-line `# noqa: TID251` 合法引入(screening 非 Phase X;TID251 对 llm/agents/mirofish 仍生效)。

## 测试
`tests/screening/`:factors(18)+ screener(31:happy/排除 fail-closed/top-N/排名确定性/manifest)+ 模块契约/隔离(自检 planted violation)。覆盖率 ≥80%。
