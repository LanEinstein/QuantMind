# QuantMind 智网量化交易系统

## 1. 这是什么系统

个人 A 股量化交易系统,4 周 suggest-mode 评估期 + 实盘前置 + ¥10k 干跑分阶段推进。融合 TradingAgents-CN 多 Agent 决策与 MiroFish 群体智能仿真。

- **三模型协同**:DeepSeek V4 Pro(高频低成本数据/摘要)+ Qwen 3.6 Plus(中文金融分析)+ Kimi K2.6(辩论/决策智能体核心)
- **当前阶段**:Phase 5B 进行中 (T01 ✅ thinking config c95e004;T02 ✅ Fast/Slow watchlist split 07a19ea;T03 ✅ tiered triage→escalation routing 2026-05-02 — 仅 fund_manager 启用 routing;Phase 5B 出口检查仍 ⏳)
- **绝对红线**:`AUTHORIZATION_MODE=suggest` 在评估期与 Phase 6A/6B 不可越界;实盘 `auto` 仅 Phase 7 用户书面授权;不跨阶段自动推进,Phase 末必须 STOP + summary 报告

## 2. 怎么组织

```
backend/
  agents/           # 9-Agent LangGraph pipeline + AnalysisRecord
  api/              # FastAPI routers (analysis/risk/monitoring/...)
  data/             # MongoDB / Redis / scheduler / news_crawler
  llm/              # router + cost_tracker + fallback + providers
  risk/             # 纯 Python 硬编码,严禁 import LLM/agents/mirofish
  services/         # cost_guard / authorization / signal_evaluator / ...
  broker/           # MockBroker + (Phase 6C+) real broker stub
frontend/           # Vue 3 + Element Plus + ECharts (port 9276)
config/             # agent_models.yaml / risk.yaml / broker.yaml / ...
docs/
  phase5-eval-and-phase6-prep-master-plan.md   # SSoT,执行依据
  reviews/                                       # codex review + 阶段 summary
tests/              # pytest 全部测试
```

**SSoT (Single Source of Truth)**:`docs/phase5-eval-and-phase6-prep-master-plan.md`。任何 task 推进前先读此文件 §5 自验证 7 步,marker 状态以 SSoT 为准。

## 3. 怎么运行

```bash
# 后端 (suggest-mode redline 启动断言会校验 QUANTMIND_PHASE × AUTHORIZATION_MODE)
QUANTMIND_PHASE=phase5_eval AUTHORIZATION_MODE=suggest \
  /home/ps/anaconda3/envs/zhanglan/bin/uvicorn backend.main:app --port 8000

# 前端 (避开 Open WebUI 占用的 3000)
cd frontend && npm run dev   # listens on :9276

# Docker 一键 (compose 从 host shell 转发 LLM key)
docker-compose up -d

# 日检 / 监控
BASE_URL=https://quantmind.local ./scripts/daily-check.sh
```

**LLM key 永远走 shell env**(`~/.bashrc`),不入 .env、不入 git:`DEEPSEEK_API_KEY` / `DASHSCOPE_API_KEY` / `MOONSHOT_API_KEY`。`.env` 仅放非密配置(MONGODB_URI、`QUANTMIND_PHASE`、`QUANTMIND_DAILY_BUDGET` 等)。

## 4. 怎么验证

| 层 | 命令 | 阈值 |
|----|------|------|
| Backend 全量 | `/home/ps/anaconda3/envs/zhanglan/bin/pytest -q --cov=backend --cov-fail-under=70` | >70% non-risk / >95% risk |
| 风控引擎单测 | `pytest -q backend/risk --cov=backend/risk --cov-fail-under=95` | 强制 ≥95% |
| Frontend | `cd frontend && npm run type-check && npm run test -- --run && npm run build` | 全绿 |
| Playwright E2E | `npx playwright test --workers=1 --reporter=line` | pass rate ≥95% |
| 红线静态检查 | `grep -rn "from backend.llm\|from backend.agents\|from backend.mirofish" backend/risk/` | 仅命中 docstring,无真 import |
| 实时健康 | `curl -sk https://quantmind.local/api/health/detailed \| jq .data.status` | `ok` |
| 实时预算 | `curl -sk https://quantmind.local/api/monitoring/budget \| jq .` | `status ∈ {ok,soft_breach,hard_breach}` |

## 5. 进度管理(必读)

**所有 task 推进遵循 SSoT 协议**:

1. **接手前**:跑 SSoT §5 自验证 7 步(读 SSoT、找 ⏳/🔧 task、`git log`、`pytest`、红线 grep、`/api/monitoring/budget`、把 marker 改 🔧 推进中)
2. **推进中**:用 TaskCreate/TaskUpdate 跟踪步骤;markers ⏳→🔧→✅(或 🚧/🛑)
3. **完成后 pre-commit gate**(顺序不可颠倒):
   - 把 SSoT marker 从 🔧 改为 ✅,**填真实 commit hash**(不要留 "(pending)")
   - 跑测试金字塔 + ruff(全绿)
   - codex-review:**major** 跑 5 轮 R1-R5(architecture/UX/testing/perf/security)、**minor** 跑 R1+R3 两轮;输出存 `docs/reviews/{task_id}-r{N}-{topic}.md`
   - commit message 用 §2.4 模板(Task / Status / Tests / Coverage / Codex-Review 字段必填)
4. **不自动 push**,等用户授权;不自动跨阶段,Phase 末必须 STOP + summary 报告
5. **报告前必须先更新 SSoT 状态并明确说"做了什么、改了哪些 marker、commit hash 是多少"**;不准发完报告再补 marker 或事后追 hash

**Codex Review 同步源**:`https://github.com/LanEinstein/CCodexSkill`,触发前 `git pull` 同步到 `~/.claude/skills/codex-review/`。

## 6. 原则与经验

**编码**
- 注释 / commit message 用英文;UI 文本与文档用中文
- public function 必须有 type hints + docstring(WHY,不是 WHAT)
- 配置走 YAML;LLM 调用必须 try/except,降级而非崩溃
- 不可变数据结构优先(`@dataclass(frozen=True)` / `NamedTuple`)
- 文件 200-400 行典型,800 行上限;函数 <50 行;嵌套 <4 层

**安全 / 红线**
- `backend/risk/` 严禁 import `backend.llm` / `backend.agents` / `backend.mirofish`
- `.env` 永远不入 git;LLM key 仅走 shell env
- MongoDB / Redis 端口仅绑定 `127.0.0.1`
- 真实下单代码在 Phase 6C 之前不得激活,仅留 interface stub

**质量取舍**(从过往 P5A-T02 5 轮 codex 学到的)
- **完整升级路径优先**:不为省工作量妥协系统可用性(用户明确授权)
- **Fail-closed for data corruption / fail-open for infra glitches**:NaN/Inf/负值在 cost_rmb / spent_today 等数据层与守门层做双层校验,Redis ConnectionError 让 scheduler 兜底通过
- **抽出独立模块换取可测性**:authorization / cost_guard 都从原本散落的逻辑提到 `backend/services/`
- **Codex review 是 hard gate**,不可跳;P5A-T02 经 5 轮发现 6 个 issue,P5A-T03 经 3 轮发现 3 个 P2,印证投资回报率

**进度管理**
- TaskCreate/TaskUpdate 全程跟踪;in_progress 严格只挂 1 个
- 每个 task 完成后立刻 mark completed,不批量
- 跨 session 接手第一件事:读 SSoT + `MEMORY.md` 索引,不重新发明状态
