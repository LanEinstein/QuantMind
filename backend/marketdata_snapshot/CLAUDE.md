# backend/marketdata_snapshot/ — 子任务上下文(Phase K,模块 0)

> 状态:**done**(Phase K K-001..K-006 已落地;先于一切读它的模块,可独立测)。治理:[R0 §3 新红线 A](../../docs/decisions/R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) + [P0-8-amendment-2026-05-24-tushare-data-source](../../docs/decisions/P0-8-amendment-2026-05-24-tushare-data-source.md)(Tushare 接入)。任务:plan.html K-001..K-006。

## 职责
全市场数据的 **point-in-time 可复现地基**。回测 / 45 日 shadow / 实时信号解释三个消费者都按 `snapshot_id` 经 `Replayer` 读它;`replay <signal_id>` 离线 bit-exact 重建当时特征输入。

## 模块结构(已实现)
| 文件 | 内容 | 任务 |
|------|------|------|
| `snapshot.py` | `MarketDataSnapshot`(frozen/strict/forbid,存 `raw_payload: bytes` + 全 sha256 + size + encoding + compression,自校验) | K-002 |
| `store.py` | `SnapshotStore` 文件式 content-addressed append-only(`payloads/<sha[:2]>/<sha>.bin` + `index.jsonl`,verify-before-adopt,同 id 拒覆盖,重述=新版本保旧字节) | K-002 |
| `coverage.py` | `CoverageManifest`(requested vs delivered universe,`completeness`/`missing_symbols` 派生属性)+ `CoverageStore` | K-003 |
| `signal_input_manifest.py` | `SignalInputManifest`(消费行血缘:`ConsumedRow{snapshot_id,row_key,row_sha256}` + feature_code_version + config_hashes)+ `reconstruct_consumed` 漂移/缺失 fail-closed + Store | K-003 |
| `adjust.py` | `AdjustFactorArtifact`(pin 因子表字节 + 算法版本 + 精度 + 舍入)+ `adjusted_close` Decimal bit-exact + `policy_for_use`(因子/回测 qfq、可负担/下单价 raw)+ Store | K-004 |
| `replay.py` | `Replayer.replay(signal_id)` 离线 bit-exact + `feature_input_digest` + `CsvRowParser` + `replay_signal` 包装(CLI 见 `scripts/replay_signal.py`) | K-005 |
| `_jsonl.py` | append-only JSONL 共享助手(`append_row` filelock + `load_rows` 离线) | K-003 |

## 本模块红线(违反 = 数据不可复现 = P0-6 验收在验证噪声)
1. **存原始字节,不只哈希**(`MarketDataSnapshot.raw_payload: bytes`);供应商重述(尤 `fina_indicator_vip`)= 新 append-only 版本(新 `snapshot_id` + 大 `version`),保留旧字节。redline-check.sh `[K-006]` 子检守门。
2. **coverage manifest**:`completeness < 1.0` / `missing_symbols` 非空标记部分抓取,不让 `row_count` 冒充全量。
3. **SignalInputManifest 消费行血缘**:replay 精确重建消费行集(非快照全集);行漂移/缺失 fail-closed。
4. **复权 pin artifact**:因子表字节 + 算法版本 + 精度 + 舍入;`adjust_policy` 按用途。
5. **离线 bit-exact replay**:无网络,从原始字节 + pin config 逐 bit 重建;三消费者同 `snapshot_id` 入口。
6. append-only(继承 P1-2.A 8 红线);checksum 失败 fail-closed 拒自动恢复。

## import 隔离(由 redline-check.sh `[K-006]` + `tests/marketdata_snapshot/test_module_contract.py` AST 守门)
**纯模块**:严禁 `import backend.{llm,agents,mirofish}`,且**不 import 任何 `backend.*` 子包**(含 `backend.data`)。取数由编排层(`backend.data.tushare_client` → K-002 快照)完成后把 payload 传入;模块 0 只存储 / 校验 / replay,不取数、不做业务逻辑。依赖仅:标准库 + pydantic + structlog + filelock。

## 测试
`tests/marketdata_snapshot/`:snapshot store(11)+ coverage(9)+ signal manifest(11)+ adjust(15)+ replay+CLI(10)+ 模块契约/隔离(26)。模块覆盖率 ≥95%。
