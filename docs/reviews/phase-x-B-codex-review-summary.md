# Phase X-B Codex Review Summary (2026-05-18, session #23)

## 摘要

跨模型代码评审(Codex CLI v0.130.0)对 Phase X-B 全部 8 个任务的 5360 行新增代码进行了 1 轮独立 review。发现 6 项 issue,全部修复完成,所有测试和 ruff 仍然全绿。

## Codex 发现及修复

### P1-1 — RagIngester payload 日期与 verifier 不一致(高优先级)

- **位置**:`backend/evolution/rag_ingester.py:379`
- **问题**:`RagIngester._write_payload` 用 `document.published_at` 拼日期目录,而 `ProvenanceVerifier.verify_entry()` 用 `entry.ingested_at` 解析路径。两者对发布日 ≠ 抓取日的文档会在不同目录读写,导致 hash-anchored citation 永远 missing。
- **修复**:`_write_payload` 改为接收 `ingested_at`,与 verifier 完全一致。
- **回归测试**:`tests/test_rag_ingester.py::test_payload_path_uses_ingested_at_date` 用 published_at != ingested_at 调用 verifier 验证。

### P1-2 — AmendmentDrafter 缺少 amendment_id path-traversal 校验(高优先级)

- **位置**:`backend/services/amendment_drafter.py:374-375`
- **问题**:`amendment_id` 直接插入 `Path`,内含 `../` / `/` / `\` 的 id 可写到 `docs/decisions/pending/` 外。
- **修复**:新增 `AMENDMENT_ID_RE = ^[A-Za-z0-9._-]+$` 在 `draft()` 入口拒绝,额外拒绝 `..` 子序列及前导 `.`。
- **回归测试**:`tests/test_amendment_drafter.py::test_amendment_id_path_traversal_rejected` 用 5 种恶意 id 全部 AssertRaises。

### P2-1 — Spotlighting wrapper 可被 body 内嵌 END 标签提前关闭

- **位置**:`backend/evolution/crawlers/spotlighting.py:41`
- **问题**:`wrap_with_spotlight(body=...)` 不转义 body 内的 `[[END UNTRUSTED:...]]`,恶意文档可在 prompt 中提前关闭 wrapper。
- **修复**:新增 `_escape_sentinels` 函数把 body 内任何 `[[BEGIN|END UNTRUSTED:...]]` 替换为视觉等价 `⟦⟦...⟧⟧`,wrap 前先 escape。
- **回归测试**:`tests/test_frontier_crawler.py::TestSpotlighting::test_embedded_end_tag_neutralised` + `test_embedded_begin_tag_neutralised`。

### P2-2 — Sanitiser HTML strip 早于 marker count

- **位置**:`backend/evolution/rag_ingester.py:184-186`
- **问题**:`<system>...</system>` 这类 XML-style prompt 注入 marker 在 HTML strip 阶段被删除,后续 marker count 看不到 → 漏报。
- **修复**:`Sanitiser.sanitise` 先在原文 / NFKC normalized 文本上做一次 marker count(`pre_strip_markers`),再做 HTML strip,最终 marker_flagged = max(pre, post) 以保留两阶段最大值。
- **回归测试**:`tests/test_rag_ingester.py::TestSanitiserDirect::test_xml_style_markers_counted_before_strip`。

### P2-3 — FrontierCrawler 在 summariser budget 超限时 `break` 跳过后续原文 ingest

- **位置**:`backend/evolution/frontier_crawler.py:142-144`
- **问题**:cost_guard 阻塞 summariser 时整个文档循环 `break`,后续文档的 raw ingest 被一并丢弃。但 raw ingest 不消耗 LLM budget,只应跳过 summariser 调用。
- **修复**:`break` 改为 `budget_blocked = True` 标志位,后续循环跳过 summariser 调用但继续 `ingester.ingest()`。
- **回归测试**:`tests/test_frontier_crawler.py::test_summariser_budget_breach_still_ingests_raw` 验证 5/5 fetched 全部 ingested,summariser 调用 0 次。

### P2-4 — RiskParameterProposal `rejected` 状态强制 accepted=True

- **位置**:`backend/models/risk_proposal.py:162-166`
- **问题**:rejected 终态 require `accepted_at`,但另一条 invariant 又 require `accepted_at` 必 `accepted=True` → 人工 reject 后只能存为 `accepted=True`,语义错乱。
- **修复**:`accepted_at` 重新定义为"review timestamp",允许在 `accepted=True`(promotion) 或 `shadow_validation_status='rejected'`(rejection review)任一信号下设置。额外新增两条 cross-state invariant:promoted ⇒ accepted=True;rejected ⇒ accepted=False。
- **回归测试**:`tests/test_risk_proposal_model.py::TestTerminalStates::test_rejected_terminal_keeps_accepted_false` + `test_rejected_with_accepted_true_rejected` + `test_promoted_with_accepted_false_rejected`。

## 验证

- `pytest -q --no-cov` → **2909 passed, 11 skipped**(原 2901,新增 8 条 codex 回归测试)
- `ruff check` 所有 Phase X-B 新增/修改文件全绿
- `scripts/redline-check.sh` 全绿

## 元数据

- Codex CLI 版本:`codex-cli 0.130.0`
- 命令:`codex review --uncommitted`
- 涉及文件:26 (5360+ 新增插入)
- 评审耗时:约 9 分钟
- Cycle:1(无后续 cycle,因 6 项 issue 全部 1 次性修复且测试覆盖)
- Skill:`/home/ps/.claude/skills/codex-review/SKILL.md`(2026-05-12 user lock — manual-only,本次用户显式触发)
