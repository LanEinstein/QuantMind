# Phase AF 长线价值投资分仓 — 接手文件(clean-context handoff)

> **用途**:一个**干净上下文** session 据此**一气扫清 Phase AF 剩余任务**(AF-002 / AF-004 / AF-005-集成 / AF-006 / AF-007)。本文 exhaustive:背景 + 已建契约(file:line)+ 每任务详规 + 红线 + 命令 + 预期输出。
> **权威指针**:`docs/research/value-investing-strategy-2026-06-22.md`(战略设计)+ `docs/decisions/value-sleeve-amendment-2026-06-22-long-term-value-investing.md`(owner 批准的边界)+ `docs/plan.html` Phase AF(TASKS)+ SESSION_LOG 顶部 5 条(立项/AF-001/AF-005/AF-003/codex PIT 门)+ memory `[[project-value-investing-strategy-2026-06-22]]`。
> **CLAUDE.md §1 协议照旧**:开工 grep SESSION_LOG + TASKS,本 session = Phase AF 全套未阻塞任务;一任务一 feature commit + codex 前置门 + 回填 plan.html;结束 docs-only commit 追加 SESSION_LOG;**push 待 owner 授权**。

---

## 0. 一句话状态

owner 委托长线价值投资专项(与 QGR 短线并行)。**价值机器 ~95% 已建,本 Phase = 接线**。已 done:立项 + **AF-001 政策→主题→申万L3 映射(已 frozen)** + **AF-003 质量基本面 compute** + **AF-005 ¥5万分仓 foundation**。**三块 value-engine foundation 全就绪 → 本 session 做 AF-002 assembly 把它们拼成 live `value_score`,再做 AF-004/AF-005-集成/AF-006/AF-007。** sim 暂停;全程 env-OFF / owner-gated / byte-identical-until-activated。

---

## 1. 全局背景

owner 资产成长论 = 两台引擎:**短线/超短线**(较低本金快速复利做大体量;另一 session 的 QGR 域 `scripts/factor_research/`,**不碰**)+ **长线价值投资**(体量到位求稳妥;本 Phase)。**总权益达 ¥5 万触发**价值子账户,锚定**十五五**规划、避开热门炒作、深挖产业链、提前埋伏。

**owner 4 决策(已落 amendment)**:① 架构=**独立资金分仓**(短线 ≤5 槽 + 价值 ≤3 槽,独立资金池;须把 ≤5 总仓红线改 per-sleeve)② 主题=战略新兴+卡脖子+AI+全链(高股息层经 codex PIT 门改作 value 因子,见 §5)③ 持有=容忍短暂回撤 + 可做T摊薄成本 ④ 文档先行 owner-gate。

---

## 2. 已建完成(契约 + file:line)

### 2.1 AF-001 政策→主题→申万L3 映射 registry — **已 frozen**(`backend/theme_mapping/`)
- `models.py`:`PolicyTheme`(theme_id/name_cn/tier:ThemeTier/effective_from:YYYYMMDD/policy_source/sw_l3_codes)+ `PolicyThemeRegistry`(version/frozen/themes;`active(date)` 按 effective_from 过滤)。fail-closed 校验。
- `registry.py`:`load_policy_theme_registry(path=config/policy_themes.yaml) -> PolicyThemeRegistry`(fail-closed loader,拒 present-but-null)。`PolicyThemeConfigError`。
- `sector_pit.py`:`SectorMembershipPIT`(申万 L3 PIT 成分,`l3_asof(code, date) -> frozenset[str]`,`in_date<=d<out_date`;`from_store(store, asof)` 读 index_member_all;`from_frame(df)`)。
- `resolver.py`:`ThemeResolver(registry, membership, tier_weights=ThemeTierWeights(), allow_draft=False)`。三 fail-closed 门:`decision_date` 必 `YYYYMMDD`、registry 必 frozen(除非 allow_draft)、tilt 须 active theme AND PIT L3 当日。**核心 API**:`theme_coverage(code, decision_date) -> float | None`(tier-weighted [0,1]=AF-002 喂 `ValueScoreInputs.theme_coverage`;**None=该码无 PIT L3 当日=数据缺口→AF-002 丢该 component 不臆造 0.0**)。`code_theme_ids(code, date) -> tuple[str,...]`。
- **`config/policy_themes.yaml`**:**frozen**,12 主题(6 national_event:semiconductor/machine_tools/basic_software/ai_plus/advanced_materials/bio_manufacturing;6 policy:new_energy/intelligent_nev/robotics/aerospace/innovative_pharma/high_end_equipment)。再改须 amendment + 重跑 codex PIT 门 + 重冻。
- **= QGR-3 ⑧ 共享单一真相源**:scripts/factor_research 可 `import backend.theme_mapping`。
- **数据 caveat(诚实)**:现 `index_member_all` 是 current-roster(全开窗,out_date 空)→ 历史重分类/退市未全捕获,L3 PIT 保真度受源表限(同 round-2 ~66%)。路线 B 回测反 hindsight 只与源表一样强;真历史(闭窗)成分表是未来增量。

### 2.2 AF-003 质量基本面 compute(`backend/quality_fundamentals/`)
- `quality.py`:`QualityMetric`(ROE/GPM/EP_TTM 高优;ACCRUALS 低优)。`fundamentals_scores(records_by_code: Mapping[code, Mapping[QualityMetric, Sequence[(ann_date, value)]]], as_of_date) -> dict[code, float | None]`(PIT ann_date 选 + 截面 percentile 复合→[0,1];无 PIT metric→None)。`quality_pit_values(records, as_of)`。
- **缺口(AF-002 接)**:snapshot→metric-records reader —— 从 income/cashflow/balancesheet/fina_indicator PIT 快照算每码每 metric 的 `(ann_date, value)` 记录(roe/gpm 直读 fina_indicator;ep_ttm=ttm 净利/总市值;accruals=(净利−经营现金流)/总资产)。研究侧 `scripts/factor_research/statements_pit.py` 有 PIT vintage 读法**可借鉴但不可被 backend import**(它在 scripts/);backend 须自建薄 reader(读 `backend.marketdata_snapshot.store.SnapshotStore`,同 sector_pit 范式)。

### 2.3 AF-005 ¥5万分仓 foundation(`backend/sleeve_policy/`)
- `policy.py`:`SleevePolicy(config)`。`is_value_sleeve_active(equity, latched=False) -> bool`(双门:`enabled` AND equity≥activate OR latched;单向 latch)。`value_target_capital_yuan(equity, latched)`(glide path,短线 floor 先留)。`assign_sleeve(StyleTag) -> Sleeve`(VALUE→VALUE 否则 SHORT)。`cap_for(sleeve, equity, latched) -> int`、`position_admissible(sleeve, held_in_sleeve, equity, latched) -> bool`(per-sleeve cap:短≤5/价值≤3;**休眠=value cap 0+单一≤5 池=byte-identical**)。`load_sleeve_policy_config(path=config/sleeve_policy.yaml)`。
- `config/sleeve_policy.yaml`:**`enabled: false`**(双门休眠 byte-identical)+ ¥5万触发 + glide path(<5万0%/5-10万20%/10-30万40%/≥30万60%)+ caps(短5/价值3)。
- **缺口(AF-005 集成,见 §4.3)**:RiskEngine check#6 per-sleeve 接线 + per-position sleeve 会计(deferred,couples AF-002)。

---

## 3. 架构(三 foundation → AF-002 → 解锁其余)

```
[AF-001 frozen 映射]──ThemeResolver.theme_coverage──┐
[AF-003 quality compute]──fundamentals_scores───────┤
[新 statement reader (AF-002)]──quality records─────┤
[新 高股息/低PE value 因子 (AF-002)]────────────────┼──► AF-002 assembly:
[value_factors 既有 mid/surface helpers]────────────┤      每候选 → ValueScoreInputs
[AF-004 底部确认门 + 不追涨]────────────────────────┘      → compute_value_score → value_scores
                                                            → selector.select(quant, value_scores=…)
[AF-005 SleevePolicy caps]──RiskEngine check#6 per-sleeve(AF-005 集成)
                                                            → 风格分型 VALUE → 软层/豁免/做T/监控
```

依赖:**AF-002 是枢纽**。AF-004(底部确认)喂 AF-002 的入场门;AF-005 集成(check#6)在 AF-002 产出 value 候选后才有 per-sleeve 持仓可校验;AF-006/AF-007 在 AF-005 之上。建议序:**AF-002 →(并)AF-004 → AF-005 集成 → AF-006 → AF-007**。

---

## 4. 剩余任务详规

### 4.1 AF-002 — value_score 接线(枢纽,最大)
**目标**:把 AF-001 theme_coverage + AF-003 fundamentals + 既有 value_factors mid/surface + AF-004 底部确认 + **新增高股息/低PE value 因子** 组装成每候选的 `ValueScoreInputs` → `compute_value_score`(`backend/screening/value_score.py` 已建)→ `value_scores: dict[code,float]` → 传 `CandidateSelector.select(quant, advisory=…, value_scores=value_scores, value_gate=0.60)`。现 `line1_runner.py:533` 调 `select(quant, advisory=advisory)` 不传 value_scores → None → bit-identical 旧路径。
**子件**:
- (a) **backend statement reader**(§2.2 缺口):新 `backend/fundamentals_pit/` 或扩 `backend/quality_fundamentals/`,读 income/cashflow/balancesheet/fina_indicator PIT 快照 → 每码 4 metric 的 `(ann_date,value)` 记录。严格 PIT(ann_date<d)、fail-closed、0 LLM、import 隔离。**`*_vip` 单调用行上限静默截断**(见 memory `[[reference-tushare-statement-vip-row-cap]]`):数据已 round-3 分页重摄 coverage 99.83%,读时仍按 ann_date 取 as-known。
- (b) **高股息/低PE value 因子**(替代被删的传统层,§5,**别漏**):股息率(高优)+ 低 PE/PB(低估值优)→ 截面 percentile → 进 ValueScoreInputs 的 surface 或新 component。从 `daily_basic`(pe/pb/dv_ratio)PIT 读。
- (c) **assembly orchestrator**:新 `backend/value_assembly/`(或扩 screening):给定候选截面 + decision_date + frozen ThemeResolver + statement reader → 每码 ValueScoreInputs(theme_coverage from resolver;mid via value_factors;fundamentals via AF-003;底部确认 via AF-004;高股息 via (b))→ compute_value_score → value_scores dict。
- (d) **wire**:`line1_runner` + `candidate_selector` 传 value_scores;**默认仍 None=bit-identical**(value sleeve `enabled:false` 且映射消费 owner-gated;先 env-OFF 落地,运行期激活待 owner 重启)。
**红线**:0 LLM;PIT 可复现;resolver 用 frozen registry(不 allow_draft);value_scores=None 时 byte-identical 回归测试守门。
**测试**:assembly bit-exact replay / None→byte-identical / theme_coverage+fundamentals+高股息 进 ValueScoreInputs 正确 / 风格分型 VALUE 门(≥0.60)。

### 4.2 AF-004 — 客观底部确认门 + 不追涨高位剔除
**目标**:提前埋伏入场门(quant-gate-rebar §2.3.C/D 落地价值仓)。多指标综合判健康筑底 vs 洗盘:缩量 / 站稳筹码成本带上方(`cyq_perf` PIT)/ 资金流企稳 / 无技术破位 / 无困境(非 ST/无停牌/无退市审计风险)/ 基本面质量地板。叠『不追涨』=高换手/MAX/IVOL/52周高分位 剔除(主题内)。
**scope**:新 `backend/value_entry/` 或扩 value_factors;数据 cyq_perf/daily/moneyflow(均在 `data/marketdata_pit/`)。**符号/阈值从零验不假设**(对齐 R2-2 协议)。**与 QGR-3 慢腿底部确认共享思路**(协调,但 backend 实现独立)。
**测试**:底部确认确定性 / 洗盘 vs 健康筑底可分 / 高位剔除 / 困境 fail-closed。

### 4.3 AF-005 集成 — RiskEngine check#6 per-sleeve + per-position sleeve 会计
**目标**:把 `backend/risk/engine.py:542 _check_total_position_limit`(现单一 `max_total_positions=5`)改 per-sleeve:用 `SleevePolicy.position_admissible(sleeve, held_in_sleeve, equity, latched)`。**休眠(enabled:false 或 equity<¥5万)= 单一 ≤5 池 = byte-identical**(回归测试守门:risk 95 测试 + 全量)。
**关键**:RiskEngine 是纯函数,须知每持仓 + order 的 sleeve。sleeve = 入场风格(VALUE/SHORT)。**须给 `Order`/`Position` 加 sleeve tag**(frozen Pydantic,经**单一构造点** builder 写;`side/volume/limit_price` 仍确定性派生不来自 LLM)→ 这是 §2.0 单一构造点红线区,**对抗测试先写**。**仓位三连(单股≤15%/总仓≤70%/单次≤5万)+ 熔断 + 连亏仍按合并总权益,不放松**(不碰那些 check)。
**测试**:休眠 byte-identical / 价值仓 active 时 per-sleeve cap / 合并总杠杆不放松 / sleeve tag 单一构造点不破。

### 4.4 AF-006 — 价值仓持有 + 做T降成本 overlay(env-OFF)
**目标**:核心底仓长持(只 thesis-break/硬风控退出,容忍短暂回撤,复用 `thesis_break.py` intact 豁免 + 1.5× 宽止盈带)。**做T overlay**(确定性 0 LLM env-OFF):底仓地板(默认目标股数 60% 永不被做T卖破)+ 可做T份额(≤40%)按确定性参考成本带(cyq_perf/移动参考)高减低补,**严格 T+1**(只卖昨日已结算股),round-trip 有界。**单一构造点不破**(订单经同一 builder + 14-check)。
**红线**:env-OFF 默认(关时价值仓=纯长持 byte-identical);阈值 config amendment-gated;**最新颖最需 codex 审查**。
**测试**:OFF→byte-identical / 底仓地板保护 / T+1 合规 / 单一构造点不破 / 成本摊薄正确。

### 4.5 AF-007 — 价值仓监控接线 + 只读前端 panel
**目标**:价值仓接 `backend/monitoring/thesis_break.py`(已建已接线;日内 ANCHOR_DRAWDOWN+TIME_STOP 豁免、SCORE_DECAY 日终)+ 季度 thesis 复检 cron。前端价值仓 display-only panel(现有页内 tab,GET-only,127.0.0.1,**仅 2 写端点不变**;前端动了须 `npm run type-check && npm run test && npm run build` + codex+Playwright 体检,见 memory `[[feedback_playwright_frontend_exam]]`)。
**测试**:thesis-break 价值仓接线 / 季度复检 / 前端 vitest / GET-only 红线。

---

## 5. 重要遗留 TODO(别漏)
1. **高股息/低 PE value 因子**:codex PIT 门把 `traditional_upgrade_highdiv` 判 BACK-FITTED → owner 拍板**移出政策映射、改作 value_score 的 value 因子**。AF-002 §4.1(b) **必须把它加回 value_score**(股息率高优+低 PE/PB),否则 owner 要的『稳健压舱石』丢失。数据 `daily_basic`(pe/pb/dv_ratio)。
2. **RiskEngine check#6 per-sleeve**(AF-005 §4.3 deferred):couples AF-002,价值仓真有持仓才咬合。
3. **backend statement reader**(AF-002 §4.1a):AF-003 compute 的数据入口。
4. **数据 caveat**:index_member_all current-roster(§2.1);真历史闭窗成分表是未来增量,影响路线 B 回测保真度——披露不藏。

---

## 6. 安全地基红线(全保留,一条不破)
永禁真实下单(只 MockBroker/SimulationExecutor)/ feishu_interactive 人工 / 127.0.0.1 / LLM 不写决策字段 / RiskEngine 纯函数无 IO 无 `import backend.{llm,agents,mirofish,data}` / **InstructionPlan 单一构造点**(`grep "InstructionPlan(" ⊆ {model,builder,tests}`)/ PIT 可复现 / fail-closed / 排除四件套(ST/科创/北交/可转债永禁)/ 仓位三连+熔断+连亏按合并总权益不放松 / governance enum 不动 / config runtime 不可改+hot-reload 禁 / 新 backend 量化模块 import 隔离(theme_mapping/quality_fundamentals/sleeve_policy 均已 AST+测试守门,新模块照做)/ sim 暂停直到 go-live gate(owner+LiveArtifactRegistry+45 日真管线 shadow replay+人工 pin+重启)。

---

## 7. 命令速查 + 预期输出
```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
# 本地门禁(每任务 commit 前全绿)
$PY/python -m pytest tests/<新模块>/ -q              # 全 pass
$PY/ruff format <backend/新模块> <tests/新模块> && $PY/ruff check 同上   # All checks passed!
$PY/mypy <backend/新模块>                            # Success: no issues found
bash scripts/redline-check.sh                        # All redline checks passed.
$PY/python -m pytest tests/risk tests/budget_policy tests/screening tests/candidate_selector -q  # 回归 byte-identical
# codex 前置门(有代码任务 commit 前;codex CLI stdin 死锁→必 `</dev/null`,见 memory)
OUT=$(mktemp); timeout 600 codex exec --sandbox read-only "<prompt>" </dev/null >"$OUT" 2>/dev/null; cat "$OUT"
#   codex 超时/不可用 → 回退 `/code-review high`(见 memory [[feedback_codex_rate_limit_fallback]])
# plan.html JS 校验(改 TASKS/SESSION_LOG 后)— node 提取两数组 eval 解析,确认 PARSE OK
```
**数据**:`data/marketdata_pit/`(~29GB,**禁从头重下**,增量协议见 `docs/research/data-inventory-marketdata-pit-2026-06-21.md`)。读 PIT 用 `backend.marketdata_snapshot.store.SnapshotStore("data/marketdata_pit")` + `_latest_snapshot_key`(参考 `scripts/factor_research/build_qgr_panel.py`)。

---

## 8. 协调 / 边界
- **不碰 `scripts/factor_research/`**(另一 session 的 QGR 域)。价值仓全在 `backend/`。
- **政策映射 frozen** = AF-001/QGR-3 ⑧ 共享单一真相源;QGR-4 消费同一 `backend.theme_mapping`,不重复造。
- **价值慢腿**可入 QGR-2 已冻评测竞技场(`docs/research/qgr-2-eval-arena-freeze-spec-2026-06-22.md`)做同场公平比。
- **sim 暂停**;运行期激活(value sleeve enabled / 重启 / 真实 PIT 全窗口实跑 / 飞书审批)全待 owner。**push 待 owner 授权**(本地 commit 累积,别并进 push)。

---

## 9. 诚实 caveat
盈利不保证(by design);主题映射已过 codex PIT 门但 current-roster 数据限保真度;做T 与纯长线张力(有界/确定性/env-OFF/底仓地板);固定历史无限次干净确认不可得,go-live 须真前向 + shadow replay。**FAIL 报 FAIL,PASS 不夸大。**
