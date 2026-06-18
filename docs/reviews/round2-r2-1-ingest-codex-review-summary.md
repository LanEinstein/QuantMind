# R2-1(第 2 轮 PIT 数据摄取)codex 代码评审 summary(2026-06-18)

> 任务:R2-1 = 第 2 轮基准相对研究的 PIT 数据扩充摄取(`index_weight` / `index_member_all` 申万行业 / `fina_indicator_vip` + `stock_basic` L/D 幸存无偏)。
> 评审器:codex-cli 0.137.0 / gpt-5.5 / xhigh / sandbox read-only。3 cycle(cycle-1 → verify → final verify),`</dev/null` + prompt 作位置参数。
> 改动:`scripts/factor_research/ingest_round2_data.py`(新)+ `backend/data/tushare_client.py`(+`index_weight` 支持 range + `index_member_all`)+ 两个测试文件。

## 结论
**无 P0。cycle-1 的 2 P1 + 3 P2、verify 的 1 P1 + 2 P2 全部已修并测试。final verify:三点闭合,可以 commit。** 门禁:283 passed(factor_research + client + historical_ingest + marketdata_snapshot + 依赖方)/ ruff / mypy strict / redline-check 全绿。

## cycle-1 findings(全修)
| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| 1 | P1 | fina coverage 跨期 `ts_code` union 掩盖单期/整期缺失(伪装 completeness=1.0) | 改 `build_fina_coverage_manifests`:**每 period 一个 manifest**,requested=`universe.tradable_asof(period)`,delivered=该期 snapshot ts_code;**period snapshot 缺失抛 FileNotFoundError 不 continue** |
| 2 | P1 | 月枚举把未完当月当月末 → 半月 key(20260612)+ 月内 rerun 产生同月多份快照 | `month_end_trade_dates` 默认丢最高(可能不完整)月;`include_partial_last` 才保留 → 每月 key 稳定 + 幂等(dry-run 138→137) |
| 3 | P2 | coverage 非幂等(每 rerun 无条件 append) | `_put_coverage_idempotent`:同 (endpoint,session_end) 内容相同则跳过 |
| 4 | P2 | 无限速,真实多百次调用易被频控 | 复用 `RateLimiter`,`_ingest_one` fetch 前 `acquire`(skip-if-present 在限速前不耗预算);`--max-per-minute` 默认 400 |
| 5 | P2 | `index_weight` 同传 date+range 不 fail-closed | XOR 校验(二选一)+ 缺端/`start>end` 报错 |

## verify findings(全修)
| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| 6 | P1 | coverage fail-closed 在 orchestrator 被弱化(build 抛错只 warning,report.failed 仍 0) | 抽 `_build_coverage` 助手:build 失败返回 `status=failed` 的 `EndpointResult(endpoint="coverage")` → report.failed>0;`main()` `raise SystemExit(1 if failed)`;新测试 `test_coverage_section_fails_closed_on_missing_period` |
| 7 | P2 | `_put_coverage_idempotent` 仅比 requested/delivered 非完整身份 | 改比完整 `model_dump(mode="json")` |
| 8 | P2 | coverage 路径与 AE-001 不一致 | `main()` 改 `CoverageStore(Path(snapshot_root)/"coverage")` 对齐 AE-001 |

## codex 已确认无问题(不再列)
摄取本身无前视;`fina` 按 report-period end_date 存原始字节可接受(R2-2 reader 必按 ann_date+lag+vintage gate);`tradable_asof(period)` 用 `list_date≤period<delist_date` 不误判退市股、正确标当期未报码 missing;丢最高月对 R2-6 前向窗口安全(摄取到判定日之后窗口月已完整);限速顺序正确;byte-exact / append-only / 幸存无偏 / import 隔离未破坏。

## 残留(非阻塞,codex 明示不影响 commit)
CLI `main()` 的 coverage 路径 + `SystemExit` 无直接测试(`_build_coverage` 助手已直测)。后续可补 CLI 端到端测试。

## 验收
门禁全绿 + codex 三轮签字 → R2-1 可 commit。**真实重活摄取(~186 Tushare 调用:137 index_weight + 45 fina_indicator + 1 index_member + 2 stock_basic)= owner-gated,`--dry-run` 已验证 plumbing。**
