# Codex 跨模型代码审查报告 — P5A-T01 R1 架构维度

**项目**: QuantMind
**任务**: P5A-T01 修复 news_crawler 'result' KeyError
**审查时间**: 2026-05-01
**审查模型**: Codex CLI (gpt-5.5, reasoning effort: xhigh)
**审查轮次**: 1 / 2 (因第 1 轮 PASS,第 2 轮按 skill 设计跳过)
**最终判定**: ✅ 通过

---

## 审查概览

| 指标 | 值 |
|------|-----|
| 变更文件数 | 2 |
| 变更行数 | 106 (insertions) / 2 (deletions) |
| 发现问题总数 | 0 |
| 已修复 | 0 |
| 误报排除 | 0 |
| 未解决 | 0 |

## 第 1 轮详情

**Codex 判定**: PASS

**Codex 总结原文**:
> The changes add a targeted tolerant wrapper for the observed Eastmoney
> KeyError path while preserving graceful failure behavior for the service.
> The updated tests pass and I did not identify any introduced correctness
> issues.

### 审查过程

Codex 对变更执行了主动验证:
1. 阅读 `git status` 和 `git diff` 全文
2. 在沙箱中触发真实的 `_fetch_news_eastmoney()`,确认上游确实抛出 `KeyError('result')` 且 `exc.args == ('result',)`(为本次精确匹配方案提供了独立证据)
3. 运行 `pytest tests/test_news_crawler.py` 验证 12 个测试全部通过
4. 跨上下游依赖路径检查:`fetch_latest_news` → `_safe_fetch_news_eastmoney` → 降级路径不破坏 `_parse_news_df` 的契约

### 6 维度结论

| 维度 | 检查项 | 结论 |
|------|--------|------|
| 1. 正确性与逻辑 | KeyError 精确匹配 / 降级路径 / 上层契约 | ✅ 无问题 |
| 2. 安全性 | 异常信息泄露 / 输入信任边界 | ✅ 无问题 |
| 3. 错误处理 | 静默吞吃 / 错误传播 / 超时 | ✅ 设计正确 (info/warning/raise 三态) |
| 4. 性能 | DataFrame 分配开销 | ✅ 仅在异常路径分配 |
| 5. 代码质量 | 函数长度 / 嵌套 / 命名 / DRY | ✅ 函数 11 行,清晰 |
| 6. 语言规范 | 异常基类 / 类型注解 / 上下文管理器 | ✅ 类型注解完整,异常分类合理 |

## R3 测试维度补充说明

按 §2.4 minor-fix 路径要求 R1 + R3 两轮。第 1 轮的 6 维度审查中:
- **Dimension 3 (Error Handling)**: Codex 主动验证三态行为 (`KeyError('result')` → empty + info / 其他 KeyError → raise / 其他 Exception → empty + warning),未发现问题
- **Dimension 5 (Code Quality)**: 测试覆盖率 90%(达标),12 个测试 0 失败

第 2 轮(原计划的 R3 testing 专项)按 skill 设计的 `EXIT_REASON=passed` 路径跳过 — 第 1 轮已对测试做了端到端的执行验证,再跑一轮无新增信号。

详见配套报告: `docs/reviews/p5a-t01-r3-testing.md`(指向同一 cycle_1 输出)。

## 关键设计决策

### 1. KeyError 精确匹配 (`exc.args == ("result",)`) 而非子串包含

原计划草案使用 `"result" in str(exc)`,但 `str(KeyError("not_result")) == "'not_result'"` 包含 `"result"` 子串,会误吞真实的 schema bug。本实现采用 `exc.args == ("result",)` 精确匹配,Codex 通过运行验证脚本独立确认了这一行为差异。

### 2. info 而非 warning 级别

5 分钟一次的 noise 是可预期的上游退化,不是异常。降级到 `log.info` 后线上日志将清掉 24h 内 ~50 条 warning,降低告警噪音,而不会丢失可观测性 — `eastmoney_empty_payload` 事件名仍可被监控系统聚合。

### 3. 降级表头硬编码而非动态推断

`_EXPECTED_NEWS_COLUMNS` 写死 4 个 akshare eastmoney 列名,确保下游 `_parse_news_df` 在空 DataFrame 上也走与正常路径一致的字段映射。如果未来 akshare 改字段,这里会立即在测试 `test_returns_payload_when_no_exception` 中暴露。

## 测试基线对比

| 项 | Before | After |
|---|--------|-------|
| pytest 总数 | 672 passed / 11 skipped | 677 passed / 11 skipped (+5) |
| `tests/test_news_crawler.py` | 7 passed | 12 passed (+5) |
| `backend/data/news_crawler.py` 行覆盖 | 未独立测量 | 90% (达 ≥90% 阈值) |

---

> 本报告对应 cycle 1 输出: `/tmp/codex_review_zevWNw/cycle_1.md`
> 审查模型: Claude Opus 4.7 (修复) + Codex gpt-5.5 (审查,read-only)
