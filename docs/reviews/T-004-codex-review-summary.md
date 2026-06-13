# T-004 Codex 跨模型代码审查报告

**任务**: T-004 — Reflexion + exemplars(≤3)行为进化 + 人格稳定
**审查时间**: 2026-06-13
**审查轮次**: 1 cycle + 1 read-only 最终复核
**最终判定**: ✅ 通过(经最终复核)

## 审查范围
- `backend/strategy_evolution/reflexion.py`(新)— curate_exemplars + ExemplarArtifact + propose_persona_card_version + is_promotable
- `tests/strategy_evolution/test_reflexion.py`(新)+ `backend/strategy_evolution/CLAUDE.md`(接口契约)

## 发现的问题(cycle 1)— 3 × P2,全修(均 provenance/审计)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | reflexion.py | `propose_persona_card_version` 只查 persona_id,不查 YAML `version` == `artifact.base_version` → 哈希记录的 base 与实际卡 skeleton 不一致,人工 gate 无法复现审计 | **FIXED**:fail-closed 校验 `doc.version == artifact.base_version` |
| 2 | P2 | reflexion.py | `yaml.safe_dump()` 整卡重写 → 剥离治理注释 + 改 identity/mandate/output_contract 的 block-scalar 表示 → 破坏冻结段字节保全(人工 review diff 巨大) | **FIXED**:改**定向文本手术**(只替换唯一 `version:` 行 + 末尾 `exemplars:` 块)+ 重解析校验(仅 version/exemplars 变、冻结段字节相等、top-level key 集不变) |
| 3 | P2 | reflexion.py | `build_artifact` 剥离 `Exemplar.persona_id` 按传入 persona_id 建 artifact → 跨人格污染(A 交易员示范可被烤进 B 卡) | **FIXED**:构造前校验每个 exemplar.persona_id == 目标 persona_id,不符 raise |

## 最终复核(read-only,codex exec)
- 三项全部 **RESOLVED**(无 safe_dump;version+persona 双 fail-closed;手术后重解析 + key-drop guard)。
- 新增 P1 回归:**NONE**。

## 门禁
- `ruff` ✅ / `mypy` strict(reflexion.py)✅ / **22 passed**(含 3 新对抗)/ strategy_evolution 包 196 passed / 模块 AST 隔离测试绿 / redline 全绿。

## 守住安全地基
人格卡 frozen(身份)/exemplars(好输出)分离;**仅 RiskEngine-passed AND profitable 案例** + AB-006 lint(带量买卖/订单/注入文本剔除)+ FinMem 递减 + **≤3 cap**(构造期 fail-closed);content-addressed artifact;`propose` **只产出永不自动应用**,新卡 sha256 须人工 pin 为 `LiveArtifactRegistry.PROMPT_VERSION`(amendment+重启)经既有 persona_registry(require_pinned)闭环;手术保证冻结身份段字节保全(人工 review 最小 diff);模块隔离零 agents_team import。
