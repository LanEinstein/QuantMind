# AE-002 代码审查 summary — rqalpha 子进程隔离 oracle + 摩擦校准门

**任务**: AE-002(P1a)rqalpha 子进程隔离 oracle 接线 + PIT 同源导出(Option B)+ 摩擦校准门 ≤25bps
**边界**: `docs/decisions/R-002-amendment-2026-06-14-rqalpha-subprocess-oracle.md`(无新 amendment)
**审查日期**: 2026-06-15
**审查方式**: codex CLI(`codex review --uncommitted`)→ **撞 usage limit(至 2026-06-18)** → 回退 `/code-review high`(3 correctness finder × 6 candidate → 1-vote verify),印证 [[feedback_codex_rate_limit_fallback]]

## 门禁(commit 前,全绿)

- 全量 pytest(主 env):**5887 passed / 14 skipped**(基线 5859 → +28)
- 覆盖率:TOTAL **90%**;`pit_export.py` 95% / `rqalpha_protocol.py` 100% / `backtest_oracle.py` 94%(`rqalpha_entry/*` 在 venv 跑,不入主 env 覆盖)
- ruff(全仓)+ mypy strict(新模块)+ `scripts/redline-check.sh`(`[R-002]` allowlist + `[BACKTEST]`)全绿
- **venv 实跑校准门**:`test_rqalpha_oracle_integration`(CS 600519 + ETF 510300 两参数)真子进程跑 rqalpha 6.1.5 → vs golden_replay 同摩擦 → **CONSISTENT(≤25bps)**

## findings 与处置

### 已修(5)

| # | 严重 | 文件 | 问题 | 修复 |
|---|------|------|------|------|
| 1 | **HIGH** | `rqalpha_entry/friction.py` | 印花税只对 CS 征收(豁免 ETF),但 broker `cost_calculator` 对**所有 SELL** 征收 → ETF 卖出两引擎差 ~100bps → 假 DIVERGENT | 改为所有 SELL 征印花税(对齐 broker,非现实 ETF 豁免规则);**新增 ETF 参数化 integration test 钉死** |
| 2 | **HIGH** | `backtest_oracle.py` + `rqalpha_entry/__main__.py` | checksum 仅 `if sidecar.exists()` 才校验 → 子进程在写 result.json 后、写 sidecar 前被杀 → 无 sidecar 的 result 被免检采纳(非 fail-closed) | runner **强制要求 sidecar**(缺即 UNAVAILABLE);entry **先写 sidecar 再原子发布 result.json**(杀于两写之间只会留 {无 result.json} 或 {result+匹配 sidecar});flip 测试 + 新增 missing-sidecar 降级测试 |
| 3 | MED | `backtest_oracle.py` | runner 校验 strategy_hash/bars_sha256 但不校验 `engine == "rqalpha"` → 标错引擎的 result(哈希对)被当 oracle 采纳,架空跨引擎差分 | 断言 `result.engine == ENGINE` 否则 UNAVAILABLE;新增 mislabelled-engine 降级测试 |
| 4 | MED | `rqalpha_entry/friction.py` | slippage 板块查不到时静默回 0.0(broker 回退 legacy 标量)→ 欠收摩擦可掩盖发散为假 CONSISTENT | 板块查不到 **fail-closed raise**(传到非零退出 → UNAVAILABLE,比静默 0 或猜测标量更安全) |
| 5 | LOW | `pit_export.py` | `limit_up/down` Decimal 乘积未 re-quantize(6dp vs OHLC 4dp)→ content-addressing 精度不一致(行为无害:合成限价够宽) | quantize 到 4dp;`BrokerFriction` docstring 修正(transfer_fee_rate 源自 `cost_calculator.TRANSFER_FEE_RATE_SZ` 常量,非 broker.yaml 键) |

### 已查证为误报 / 实证驳回(1)

- **friction-not-applied 假 CONSISTENT 风险**(Finder B):疑 `sys_transaction_cost.enabled=False` 时注入的 decider 不被消费 → 摩擦静默归零 → 校准假过。**实证驳回**:用 10% commission 跑 entry → day1 total_equity = 989,978(= 1M − gross − 10% 佣金),证明 `env.set_transaction_cost_decider` 注入的 decider **确被撮合路径消费**(与 mod 启停无关)→ 校准门有效。

### 已记录暂缓(latent / 文档化,非本任务 bug)

- `BacktestRunResult` 加 Mapping 字段使 frozen 模型不可 hash(当前无 caller hash,latent;EquityDay/DayDiff 仍可 hash)。
- qfq asof anchor = 导出窗口末日;production 跨核需 caller 保证 oracle 与 MockBroker shadow 同窗(R0 §3 字节同源已保,anchor 同窗是 caller 契约)。
- `_build_instruments` 硬编 `board_type="MainBoard"`(限价当前合成 ±21%/−79% 故 dormant;真 stk_limit 限价 = AE-004)。
- ts_code 非 `.SH/.SZ` 后缀 → PitExportError → UNAVAILABLE(fail-closed 方向正确)。
- rqalpha 版本未 pin(venv owner 管;版本漂移 → 子进程崩 → UNAVAILABLE fail-closed;`engine_fingerprint` 事后留痕)。

## 安全地基红线核对(一条未破)

rqalpha **永不入实时路径**(仅 test-time;主 env 零 import,AST 契约 + `[R-002]` allowlist 钉死)/ 永不 vendor(NOASSERTION 非商用 Apache 2.0)/ 子进程隔离 venv 主 env 零污染(numpy 2.2.6 主 vs 2.4.6 venv,版本指纹入 result)/ 全失败路径 fail-closed → ORACLE_UNAVAILABLE 绝不假 pass / `rqalpha_entry` 零 backend import(venv 无 backend,`-m rqalpha_entry` + PYTHONPATH=backend/backtest 顶层解析)。
