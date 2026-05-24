# P0-8 修订 — 2026-05-24 MiroFish 加分项 → Line 1 建议核心(仍 evidence-only)

> **修订基准**: [P0-8 数据情报 multi-domain + MiroFish + fail-closed 质量门](./P0-8-data-and-intelligence-multi-domain-mirofish-fail-closed-quality-gate.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 3 项 + §8 MiroFish 边界
> **修订日期**: 2026-05-24
> **触发**: Owner 把 MiroFish(隐性因果链推演)升为 Line 1 选股核心引擎,推翻记忆 [[feedback_mirofish_supplementary_not_core]] 与 P0-9「加分项·硬 cap=1·非核心」。Codex 警告:**升核心 ≠ 让 LLM 决策**——板块排名若**硬剪枝 universe**(某股因 MiroFish 没排其板块而出局)就是 LLM 在做资格判定。

## 1. 修订前(P0-8 / P0-9 原锁定)

- MiroFish 事件驱动(severity≥HIGH,cap=1)+ 17:00 复盘双路径;**加分项非核心**,严禁占 traditional 4 cap。
- 输出**仅入** `evidence_collection`(`MIROFISH-` 前缀),**不入** `RiskCheckSummary`(P0-8 §2 红线 11);`output_writer.py` **by-construction** 强制(无 risk-summary 管线)。

## 2. 修订后(本 amendment 锁定)

### 2.1 MiroFish 角色升级

- **输入**: 信息汇总文档(市场趋势 / 情绪 / 政治舆论 / 板块热度 / 关联板块 + 国内外指数 / 板块涨跌 + 历史)。
- **输出**: 「哪些板块的股票 / ETF 大概率涨」的**板块涨概率预测报告** + 因果链 + 引用 + **有界板块分**(bounded sector-score)+ **不确定性字段**。
- MiroFish 报告 + 信息汇总报告 = **场外信息**,与候选股信息结合进多 agent 辩论(R0 §1)。
- `event` cap 路径(P0-9 amendment §2.4)现由 MiroFish 建议驱动;不再标「加分非核心」。

### 2.2 evidence-only **不变**(安全核心)

- MiroFish **仍只写** `evidence_collection`(`MIROFISH-` 前缀),`output_writer.py` 的 by-construction 强制(无 RiskCheckSummary 管线)**保留**。
- MiroFish 写**零**候选纳入字段 / size / entry-exit / 方向 / 风控字段。它写 evidence,确定性代码写候选。

### 2.3 建议 vs 决策的明线(配 R0 §4 CandidateSelector)

- 新增**纯 Python `CandidateSelector`**(Phase M)在**固定 git 版本权重 / 阈值**下读 evidence → 出候选 ID。
- **资格判定纯量化**(screening + 排除 + 可负担性);MiroFish **只能在已合格集内有界重排**(rank 偏移 ≤1 分位)。
- **强制量化名额**:最终候选清单(top-N 截断**之后**)必须保留 **≥3** 个纯量化选出的名额,即便 MiroFish 给其板块低分——**MiroFish 永不能否决整个板块 / 静默剪枝**(本 amendment 的核心防泄漏不变量)。
- **量化兜底**:MiroFish 缺席 / 降级 / 超预算时,`CandidateSelector` 仍出有效清单。若移除 MiroFish 改变了**合格集**(而非仅排序),即越线 = 违规。

## 3. 实施期任务调整

- `backend/mirofish/`(扩展,Phase O):新增板块涨概率预测路径(输入信息汇总文档 → 输出 sector-score + 因果链 + 不确定性),仍走 `output_writer.py` evidence-only。
- `backend/candidate_selector/`(新模块,Phase M):确定性,读 evidence + 量化特征,固定 git 版本权重;≥3 量化名额 + 有界重排 + 量化兜底;**对抗测试**:移除 MiroFish evidence,断言合格集不变(仅排序变)。
- `evidence_id` 5 前缀(`NEWS-`/`MIROFISH-`/`MARKET-`/`RISK-`/`DEBATE-`)**不变**(P0-8 §2 红线 14)。

## 4. 红线清单(本 amendment 之后)

1. MiroFish **仍仅写** `evidence_collection`(`MIROFISH-` 前缀),`output_writer.py` by-construction 无 RiskCheckSummary 管线**不变**;写零决策 / 候选纳入 / size / 方向 / 风控字段。
2. 候选**资格纯量化**;MiroFish **只能合格集内有界重排 ≤1 分位**;top-N 截断后保 **≥3 量化名额**;**永不否决板块 / 静默剪枝**。
3. MiroFish 缺席有**量化兜底**;移除 MiroFish 若改变合格集(非仅排序)= 违规。
4. `CandidateSelector` 纯 Python + 固定 git 版本权重;严禁 `import backend.{llm,agents}`(读 evidence + 量化特征,不调 LLM)。
5. `evidence_id` 5 前缀不变;多域 5 源新闻 + 主备行情质量门(staleness≤5s / divergence≤0.3%)不变。

## 5. 修订记录追加

`docs/plan.html` Phase M/O 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.5 的「MiroFish 加分非核心」改写为「MiroFish 升 Line 1 建议核心,仍 evidence-only + 有界重排 + 不否决板块」。记忆 [[feedback_mirofish_supplementary_not_core]] 标注被本次推翻。
