# backend/marketdata_snapshot/ — 子任务上下文(Phase K,模块 0)

> 状态:**todo**(Phase K,先于一切读它的模块,可独立测)。治理决策:[R0 §3 新红线 A](../../docs/decisions/R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) + `P0-9-amendment-2026-05-24`(Tushare 接入)。任务:plan.html K-001..K-006。

## 职责
全市场数据的 **point-in-time 可复现地基**。回测 / 45 日 shadow / 实时信号解释三个消费者都按 `snapshot_id` 读它;`replay <signal_id>` 离线 bit-exact 重建当时特征输入。

## 本模块红线(违反 = 数据不可复现 = P0-6 验收在验证噪声)
1. **存原始字节,不只哈希**。仿 `backend/broker/persistence/snapshots.py` BrokerSnapshot(存完整 canonical payload + checksum + 用前校验);**严禁** hash-only 变体。供应商重述(尤 `fina_indicator_vip`)= 新 append-only 版本,保留旧字节。
2. **coverage manifest**:granularity / session 窗 / endpoint+params / requested vs delivered universe / missing_symbols / completeness。仅 `row_count` 会让部分抓取冒充全量。
3. **SignalInputManifest**:按 `signal_id` 记 {snapshot_ids, 消费行 hashes, feature_code_version, config_hashes, join/filter params} —— 消费行血缘。
4. **复权 pin artifact**:复权因子表存字节 + 公司行动原始行 + 算法版本 + 精度 + 舍入;`adjust_policy` 按用途(因子/回测 qfq、可负担/下单价 raw)。
5. **离线 bit-exact replay**:无网络,从原始字节 + pin config 哈希逐 bit 重建。
6. append-only(继承 P1-2.A 8 红线);checksum 失败 fail-closed 拒自动恢复。

## import 隔离
严禁 `import backend.{llm,agents,mirofish}`。可用:`backend.data`(取数)+ 标准库。httpx 出站 `local_address="0.0.0.0"`(IPv4-only egress)。

## 接口契约(草案,实施期细化)
- `MarketDataSnapshot`(frozen Pydantic strict + extra=forbid)+ `SnapshotStore.put/get/verify`。
- `SignalInputManifest` + `replay(signal_id) -> FeatureMatrix`(离线)。
- `scripts/replay_signal.py` CLI。
