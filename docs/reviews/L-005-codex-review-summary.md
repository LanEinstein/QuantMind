# L-005 Codex 跨模型代码审查报告

**任务**: L-005 — screening + budget_policy 模块 CLAUDE.md + tier 校准 + import 隔离/契约测试(P0-7-amendment-2026-05-24 §2.5)
**审查时间**: 2026-05-24
**审查模型**: Claude Opus 4.7(实现/修复)+ Codex CLI gpt-5.5(独立审查)
**审查轮次**: 1 cycle review + 1 read-only final verification
**最终判定**: ✅ 通过(经最终复核 PASS,1 P2 RESOLVED,0 新 P1 回归)

---

## 审查范围

`codex review --uncommitted`,7 文件:`backend/budget_policy/calibration.py`(新)、`__init__.py`、两模块 `CLAUDE.md`(todo→done)、`tests/screening/test_module_contract.py`、`tests/budget_policy/{test_calibration,test_module_contract}.py`。

## 发现的问题(1 全修)

| # | 严重度 | 文件 | 问题 | 处理 |
|---|--------|------|------|------|
| 1 | P2 | test_module_contract.py(两份) | AST 隔离扫描的 `ImportFrom` 分支只查 `node.module`,漏 `from backend import llm`(禁包在 `node.names`)与 `from .. import agents`(相对 + names)→ L-002 隔离守门可被绕过。 | **FIXED** |

### 修复详解

两 `_forbidden_backend_imports` 的 `ImportFrom` 分支**新增 `node.names` 检查**:① level==0 且 module=='backend' 且任一 alias ∈ {llm,agents,mirofish} → 违规;② level>0 且任一 alias ∈ 该集合 → 违规(叠加原 module-path 检查)。新增 parametrized 自检覆盖 `from backend import llm` / `from backend import agents,data` / `from .. import mirofish/agents` / `from ..mirofish import y`。

## tier 校准(§2.5,本任务核心交付)

`calibration.calibrate_tiers(per_lot_costs, max_single_stock_pct)` 从 universe 1 手成本分布派生阈值:`micro = p10_lot / 15%`、`small = median_lot / 15%`(阈值落在"最便宜/中位 1 手刚好满足 15%"的点)。对 p10≈¥300 / median≈¥1500 复现 shipped ¥2000/¥10000 —— 锁定值是**校准来的、非硬编码**。离线助手,不改 config、不入运行路径;非有限/非正 fail-closed drop,分布过窄(micro≥small)raise。

## 最终验证(read-only 复核)

`codex exec -s read-only`(前台 `</dev/null`):**PASS**,两扫描器现捕获全部 import 形式,API/根检查干净,无新 P1 回归。

## 门禁

- pytest 全量 **3352 passed / 11 skipped**(screening 36 + budget_policy 59:policy 32 / calibration 11 / 两模块契约)。
- 模块覆盖率:screening 96-100% / budget_policy 97-100%(**TOTAL 97%**,远超 ≥80% gate)。
- ruff:本任务触及文件全绿(2 个既存 E501 在未触及的 `backend/mirofish/`,session #29 已记录,本 session 不动)。
- `scripts/redline-check.sh`:全绿(28 ok),`[L-002]` 隔离子检覆盖两模块。

## 红线确认

- **import 隔离**:两模块 AST 契约测试(含 absolute/`from backend import`/相对三类 planted 自检)+ redline `[L-002]` + ruff TID251 多重守门;无 `backend.{llm,agents,mirofish}`。
- **公开 API 契约**:`__init__.__all__` 与实际导出一致(测试锁)。
- **tier 阈值派生**:`calibrate_tiers` 提供可解释推导;CLAUDE.md 锁红线 + 标注"初值从 lot 成本分布派生"。
- **CLAUDE.md**:两模块状态 done,准确反映已建契约 + 红线 + 测试。

---

> 本报告由 Claude Code(Opus 4.7)+ Codex CLI(gpt-5.5)协同生成。
