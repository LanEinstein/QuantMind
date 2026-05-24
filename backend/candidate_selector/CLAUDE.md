# backend/candidate_selector/ — 子任务上下文(Phase M)

> 状态:**done(M-001 MVP)**(确定性选择器 + 对抗测试落地,commit `d634fc9`;MiroFish 实际接线在 O-003)。治理:[P0-8-amendment-2026-05-24](../../docs/decisions/P0-8-amendment-2026-05-24-mirofish-advisory-core.md) + R0 §4。任务:plan.html M-001(done)/ O-003(MiroFish 有界重排接入)。

## 职责
**确定性候选选择器**(纯 Python,固定 git 版本权重):读 evidence + 量化特征 → 出候选 ID。是 "MiroFish 建议 vs 决策" 的明线落点 —— **MiroFish 写 evidence,本模块(确定性代码)写候选**。

## 本模块红线
1. **资格判定纯量化**(screening + 排除 + 可负担性)。
2. MiroFish 只能在**已合格集内有界重排**(rank 偏移 **≤1 分位**);**永不否决板块 / 静默剪枝**。
3. **top-N 截断后**仍保 **≥3 个纯量化名额**(重排不能间接挤掉)。
4. MiroFish 缺席 / 降级 / 超预算 → **量化兜底**仍出有效清单;移除 MiroFish 若改变**合格集**(非仅排序)= 违规。
5. 权重 / 阈值 git 版本化,runtime 不可改;`LiveArtifactRegistry` 认 `feature_def_hash`。
6. **对抗测试**:移除 MiroFish evidence → 合格集不变(仅排序变)。

## import 隔离
严禁 `import backend.{llm,agents}`(读 evidence + 量化特征,不调 LLM)。可用:`backend.{screening,budget_policy,risk}` 类型 + evidence_collection 读。

## 接口契约(草案)
- `CandidateSelector.select(quant_candidates, evidence) -> list[CandidateId]`(确定性)。
