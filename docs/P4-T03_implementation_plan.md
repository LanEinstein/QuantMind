# P4-T03 · MiroFish 可视化实施规划（团队评审版）

> **状态**：规划确认 · 9 Phases · 预计 9 人日
> **分支**：`feature/p4-t03-mirofish-viz`
> **前置条件**：P3-T02 隐变量提取管道已落地（`backend/mirofish/extractors/`）
> **解锁下游**：P5-T01 Suggest 模式 4 周验收
> **完整细节与代码样例**：见 `~/.claude/plans/snappy-meandering-quail.md`

---

## 1 · 问题陈述 / Problem Statement

MiroFish 提取管道已产出丰富的群体心理数据——`SentimentRound.dominant_narrative/intensity`、`MomentumShift`、`EnrichedHiddenVariable.agent_consensus_ratio`、`EnrichedInflectionPoint.inflection_type/before_sentiment/after_sentiment/confidence`、`EnrichedExtremeScenario.direction/trigger_conditions/early_warning_signals`——但 `backend/mirofish/extractors/__init__.py:153-241` 的 `to_simulation_result()` 把它们全部扁平化为字符串塞进 `reasoning`/`event` 字段，导致前端只能拿到被降级的贫血数据。

前端 `/simulation` 页与 4 个子组件（`SentimentChart` / `HiddenVariableMatrix` / `InflectionTimeline` / `ExtremeScenarioPie`）并非空壳，已有 ~80% 静态脚手架。真正缺失的 5 件事：**后端数据瓶颈**、**无全局时钟同步**、**无键盘契约 / 焦点模式**、**视觉主次倒置**（推荐栏过响）、**无前端单元测试层**。

---

## 2 · 设计原则 / Design Principles

页面严格遵循 Michael Polanyi 默会知识理论（"我们知道的，远比我们能说出来的多得多"）：

1. **默会优先 (Tacit First)** — 用户 3 秒内凭身体直觉知道如何操作，零教程、零 tooltip 长文
2. **辅助→焦点 (Subsidiary → Focal)** — 界面 chrome 降为背景感知，运动的内容成为焦点
3. **不可言说 (Ineffability)** — 一切规则通过视觉隐喻、动作暗示、节奏反馈、空间关系传达
4. **个人承诺 (Personal Commitment)** — 用户感觉在"阅读群体心灵"，而非"操作工具"

---

## 3 · 四区可视化设计 / Four-Zone Visualization

| 区 | 隐喻 | 默会线索 |
|---|---|---|
| **A · The Pulse**（左上，14 栅格） | 群体心跳 | 堆叠面积图；`intensity` → 区域透明度；`MomentumShift` → 240ms 放大；now 线随全局时钟扫动 |
| **B · The Whisper Network**（右上，10 栅格） | 加权耳语 | 横向概率条；共识率底衬作为物理分数；`is_absent_from_original` → 左侧虚线外框（无文字说明） |
| **C · The Turning Points**（左下，10 栅格） | 珠串 | 圆点颜色 = `inflection_type`；半径 = `confidence * 8 + 8`；悬停浮现 before/after 迷你 donut |
| **D · The Tail Risks**（右下，14 栅格） | 多未来扇形 | 环形图按 `direction` 锚定左/右半圆；物理质量不对称 = 情绪偏向；点击开侧栏显示 `early_warning_signals` |

全局**自动播放 + 键盘契约**（`Space` 暂停 / `← →` 步进 / `Home End` 跳首尾 / `F` 焦点模式 / `R` 重播 / `?` 键位字形闪现）。一根 scrubber 拖动同步驱动四区——**这份同步性即核心默会线索**。推荐栏降级为底部 12px 斜体页脚（图表先说话，推荐后入眼）。

---

## 4 · 后端扩展 / Backend Extensions

**决策：原地扩展 `SimulationResult`**（无新路由）。Pydantic 默认值保证旧 MongoDB 文档无缝反序列化，零迁移。

**改动文件**：

| 文件 | 改动 |
|---|---|
| `backend/mirofish/schemas.py` | 扩展 5 个模型（`SentimentSnapshot` + `HiddenVariable` + `InflectionPoint` + `ExtremeScenario` + `SimulationResult`）+ 新增 `MomentumShift` |
| `backend/mirofish/extractors/__init__.py` | 重写 `to_simulation_result()`（lines 153-241）真实映射 enriched 字段；移除 `[动量转换: ...]` 字符串拼接 |
| `backend/api/simulation.py` | 仅 docstring 注释；无行为变更 |

---

## 5 · 前端实施 / Frontend Implementation

| 文件 | 动作 |
|---|---|
| `src/types/simulation.ts` | 新字段镜像后端（全部 `readonly` 可选） |
| `src/stores/simulation.ts` | 新 computed：`momentumShifts` / `enrichedInflections` / `upside/downsideScenarios` / `totalRounds` |
| `src/stores/transformers/simulation.ts` (NEW) | 纯 transformer 函数（Vitest 独立可测） |
| `src/composables/usePlayback.ts` (NEW) | 全局时钟 + `PLAYBACK_KEY` injection |
| `src/composables/useKeyboardShortcuts.ts` (NEW) | 键盘契约绑定 |
| `src/composables/useFocusMode.ts` (NEW) | 焦点模式切换 |
| `src/composables/useAmbientTicks.ts` (NEW) | 可选 80Hz 环境音（默认关） |
| `src/components/charts/SentimentChart.vue` | 移除本地播放状态，inject 全局时钟；`intensity` 驱动渐变；now 线 markLine |
| `src/components/charts/HiddenVariableMatrix.vue` | 共识底衬 + 虚线外框（基于 enriched 字段） |
| `src/components/charts/InflectionTimeline.vue` | 使用后端 `inflection_type` / before/after；点击 emit `seek` |
| `src/components/charts/MiniSentimentDonut.vue` (NEW) | 36px ECharts donut 子组件 |
| `src/components/charts/ExtremeScenarioPie.vue` | 左/右半圆分向；emit `open-scenario` |
| `src/views/Simulation.vue` | 注入全局时钟 + 键盘 + 焦点 + scrubber + drawer + 推荐栏降级 |
| `src/styles/motion.scss` (NEW) | 缓动曲线 + `@keyframes pulse-soft / shimmer / glow-once` |
| `vite.config.ts` | SCSS `additionalData` 引入 motion.scss |

---

## 6 · 测试金字塔 / Testing Pyramid

| 层 | 框架 | 新增测试数 | 文件 |
|---|---|---|---|
| **后端单元** | pytest + pytest-asyncio | 9 | `tests/test_mirofish_schemas.py` |
| **后端集成** | pytest | 7 | `tests/test_mirofish_integration.py` |
| **后端 API** | pytest + httpx AsyncClient | 6 | `tests/test_api_simulation.py` |
| **前端单元** (NEW) | **Vitest + @vue/test-utils** | 48 | `src/components/charts/__tests__/` + `src/composables/__tests__/` + `src/stores/transformers/__tests__/` |
| **冒烟** | bash | 5-step | `scripts/smoke_p4_t03.sh` |
| **E2E** | **Playwright + @playwright/mcp** | 18 | `frontend/e2e/simulation.spec.ts` |

**覆盖率目标**：
- 后端 `backend/mirofish/` ≥ 80%（新增代码 100%）
- 前端 `src/components/charts/` + `src/composables/` + `src/stores/transformers/` ≥ 70%

---

## 7 · Playwright MCP 安装 / MCP Installation

```bash
npm install -g @playwright/mcp
npx playwright install chrome
```

在 `~/.claude.json` 的 `mcpServers` 节点下添加：

```json
{
  "mcpServers": {
    "playwright": {
      "command": "npx",
      "args": ["-y", "@playwright/mcp@latest", "--browser", "chrome"]
    }
  }
}
```

重启 Claude Code 后 `mcp__playwright__*` 工具将出现在 deferred tools 列表；`frontend/playwright.config.ts` 改为 `channel: 'chrome'`。

---

## 8 · 分阶段执行 / Phased Execution

| Phase | 交付 | 完成标准 |
|---|---|---|
| **P1** | 本文档 + 后端 schema + `to_simulation_result` 重写 | `test_mirofish_schemas.py` 全绿；无字符串化 |
| **P2** | 后端集成 + API 回归 + curl 冒烟 | `/api/simulation/latest` 返回 enriched；legacy 测试绿 |
| **P3** | 前端 types + store + transformers + mocks | `npm run type-check` 绿 |
| **P4** | Vitest 基建 + `usePlayback` + `useKeyboardShortcuts` | `npm run test` 绿 |
| **P5** | `HiddenVariableMatrix` + `InflectionTimeline` + `MiniSentimentDonut` | 组件 spec 绿 |
| **P6** | `ExtremeScenarioPie` + `SentimentChart` 全局时钟 + scrubber | 拖拽同步四区 |
| **P7** | `motion.scss` + 焦点模式 + 推荐栏降级 + 环境音 + `?` 字形层 | 视觉评审通过 |
| **P8** | Playwright MCP 安装 + E2E 扩展 + Polanyi 第三方验收 | 18 E2E 全绿；5 秒理解测试通过 |
| **P9** | `/codex-review` 5 轮跨模型审查 | R1-R5 全部落地；CRITICAL/HIGH 清零 |

---

## 9 · Polanyi 默会验收 / Tacit Acceptance

由**未见过设计**的第三方冷启动页面，观察：

1. **5 秒理解测试** — 不读任何文字能回答"群体在最后一轮总体看多还是看空？"（Zone A 面积质量）
2. **5 秒方向感知测试** — 能回答"尾部风险偏上还是偏下？"（Zone D 左右不对称）
3. **键盘直觉测试** — 无提示下伸手按 `Space` 暂停、`← →` 步进
4. **时间线同步感知** — 点击 inflection 圆点时察觉四区同步停驻
5. **30 秒无援测试** — 使用 30 秒后没有打开任何 tooltip 或帮助叠层（设计上不存在）

**任一失败：设计错，不补 tooltip。** 回到 §2-§3 重新调整隐喻、运动节奏、空间关系。

---

## 10 · Phase 9 · Codex 跨模型审查 / Cross-Model Code Review

**前置**：从 `https://github.com/LanEinstein/CCodexSkill` 拉取最新 codex-review skill 到 `~/.claude/skills/codex-review/`。

**5 轮轮转焦点**：

| 轮 | 焦点 | 产物 |
|---|---|---|
| R1 | 架构一致性 & 数据流 | `docs/reviews/p4-t03-r1-architecture.md` |
| R2 | 默会知识 UX 对齐 | `docs/reviews/p4-t03-r2-polanyi-ux.md` |
| R3 | 测试覆盖与正确性 | `docs/reviews/p4-t03-r3-testing.md` |
| R4 | 性能 & 可访问性 | `docs/reviews/p4-t03-r4-perf-a11y.md` |
| R5 | 安全 & 运维就绪 | `docs/reviews/p4-t03-r5-security-ops.md` |

**闭环规则**：
- CRITICAL / HIGH 必须在进入下一轮前修复
- MEDIUM 写入 `docs/reviews/p4-t03-backlog.md` 带负责人 + 期限
- R5 收尾生成 `docs/reviews/p4-t03-summary.md` 并重跑 `scripts/smoke_p4_t03.sh`

---

## 11 · 风险矩阵 / Risk Matrix

| # | 风险 | 级别 | 缓解 |
|---|---|---|---|
| 1 | 旧 MongoDB 文档缺字段导致反序列化失败 | M | Pydantic 默认值 + `test_legacy_document_without_enriched_fields_validates` 护栏 |
| 2 | 4 图表 × autoplay 重绘掉帧 | M | 静态 `chartOption` 提出 computed；scrubber `requestAnimationFrame` 节流 |
| 3 | CI 缺 Chrome channel | L | `npx playwright install chrome` + 回退 `browserName: 'chromium'` |
| 4 | **Polanyi 5 秒测试不通过** | **H** | P7 结束即第三方冷启动验收；失败回到 §2 重调隐喻，**绝不用 tooltip 打补丁** |
| 5 | `AudioContext` 在 headless 下报警 | L | 懒实例化 + try/catch；默认关闭；E2E 跳过音频路径 |

---

## 12 · 验收清单 / Acceptance Checklist

```bash
# 后端
pytest tests/test_mirofish_schemas.py tests/test_mirofish_integration.py tests/test_api_simulation.py -v
ruff check backend/mirofish/ && mypy backend/mirofish/

# 前端
cd frontend && npm run type-check && npm run build && npm run test && npm run test:coverage

# E2E
npx playwright test simulation.spec.ts --project=chrome

# 端到端冒烟
bash scripts/smoke_p4_t03.sh
```

全部退出码 0 + 覆盖率达标 + Polanyi 第三方验收通过 + Phase 9 Codex 5 轮 CRITICAL/HIGH 清零 = **合格**。
