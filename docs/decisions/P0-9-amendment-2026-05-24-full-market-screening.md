# P0-9 修订 — 2026-05-24 13 标的固定池 → 全市场量化初筛 universe

> **修订基准**: [P0-9 watchlist 范围 / 频率 / 传统量化为主 / long-only](./P0-9-watchlist-scope-frequency-traditional-quant-primary-long-only.md)
> **总纲**: [R0 双线重构总纲](./R0-two-line-rearch-provenance-and-single-builder-2026-05-24.md) §2 第 1 项
> **修订日期**: 2026-05-24
> **触发**: Owner 判定「锁定 13 标的」定位「完全不够」;Line 1 要在全 A 股 + ETF(5000+)中量化初筛「买得起 × 能赚钱」的几十~百只候选。`AskUserQuestion` 确认推翻 P0-9 §1.1 的 13-code lock。

## 1. 修订前(P0-9 原锁定)

- **universe = 13 个写死的 code**(沪主 4 + 深主 3 + 创业板 3 + 宽基 ETF 3:`510300`/`510500`/`159949`),`watchlist.total_codes=13` 强约束(`watchlist_policy.yaml:61`),runtime 不可改 + 仅 GET API。
- 选股 = 在这 13 只内择时;`slow` 09:00 全 watchlist 多 Agent 辩论 + `fast` 09/11/13/15 盘中。
- 排除四件套(新股 ≤30 / 次新 ≤180 / 流动性 <2 亿 / 单价 >500)在 `InstructionPlanBuilder` 第五道早返,作用于 13 只。
- `cap_allocation` 每日 ≤5 单 = traditional 4 + event 1(14:30 后滑给 traditional)。
- 严格 long-only。

## 2. 修订后(本 amendment 锁定)

### 2.1 universe 定义改为「规则集」而非「code 列表」

- universe = **全 A 股主板 + 创业板 + 宽基/可交易 ETF**(沪深主板 + 创业板个股 + ETF),规模 5000+。**取消 13-code 写死列表**。
- universe 的**边界规则仍 runtime 不可改**(走 amendment + git diff + 重启):板块白名单(沪主 / 深主 / 创业板 / ETF)+ 排除规则(下方 2.2)+ long-only。改的是「不再枚举 13 个 code」,**不是**放开板块白名单——**科创 688 / 北交 8 / ST / 可转债 仍永禁**(P0-7 §2.4 universe 红线不变)。
- `510300` / `510500` / `159949` 从「必备 13 之 3」降为「ETF 白名单成员 + 小资金兜底标的」(见 P0-7 amendment 的 Micro 档),不再是强制持仓名额。

### 2.2 排除规则:从 Builder 第五道早返 → screening 全市场预筛

排除四件套**保留且阈值不变**(`exclusion_rules`:`ipo_min_trading_days=30` / `sub_new_min_trading_days=180` / `min_avg_amount_20d_yuan=2e8` / `max_unit_price_yuan=500`),但执行位置**前移**到 `backend/screening/`(全市场纯量化预筛阶段),作为**硬排除 + fail-closed**(stock_meta 缺失 / 流动性历史 <20 日 / 缺价 → 排除,不乐观保留)。`InstructionPlanBuilder` 第五道早返**保留**作为**最后一道防御**(纵深防御,与 14-check 双层守门同构);两处用**同一** `exclusion_rules` 真相源。

### 2.3 选股链路(Line 1)

`slow` 09:00:**全市场纯量化筛**(`screening` + 排除 + `BudgetTierPolicy` 可负担性)→ top-N(~50-100)→ `CandidateSelector`(确定性,≥3 量化名额,MiroFish 有界重排)→ ≤5 → 多 agent 辩论 → RiskEngine 14-check → 飞书。**LLM 仅在 top-N 小包之后介入**(R0 §8 边界)。`fast` 盘中:盯持仓(Line 2)+ 必要时复算 shortlist;**纯量化轮询,LLM 仅触发式**。

### 2.4 每日指令 cap

`total_daily_cap=5` **保留**(镜像 `RiskConfig.max_daily_new_instructions`,P0-7 不变)。`traditional 4 + event 1` 拆分**保留语义**,但 `event` 路径现由 **MiroFish 建议核心**驱动(P0-8 amendment),不再是「加分项硬 cap=1」——event cap 仍 =1,14:30 后未用滑给 traditional(规则不变)。小资金档实际可成交笔数受 `BudgetTierPolicy` 进一步约束(常 ≤1-2)。

### 2.5 long-only + 方向锁

`direction_policy`(long_only + 6 forbidden_sides + `etf_arbitrage_enabled=false`)**完全不变**。

## 3. 实施期任务调整

### 3.1 `config/watchlist_policy.yaml` 改写(走 git diff + 重启)

- 删除 `watchlist.total_codes=13` / `composition` 13 拆分 / `required_etfs` 强制三元组 / `constraints.watchlist_size_must_equal=13` / `slow.default_codes` 的 3 ETF 写死。
- 新增 `universe`:`board_whitelist`(sh_main / sz_main / chuangye / etf)+ `forbidden_boards`(kechuang_688 / beijiao_8 + ST + 可转债)。
- 保留 `exclusion_rules` / `cap_allocation` / `direction_policy`。
- `policy_version: 2 → 3`(loader 拒旧版本)。
- 文件改名建议:`watchlist_policy.yaml → universe_policy.yaml`(语义已从「关注列表」变「全市场 universe 规则」);若改名,`backend/services/watchlist_policy.py` + 引用同步。**本 amendment 锁定语义,具体改名在 plan.html Phase L 任务执行**。

### 3.2 `backend/screening/`(新模块,Phase L)

全市场拉取(Tushare `daily`/`daily_basic`/`fina_indicator_vip` 全市场单次)→ 排除四件套 → 因子(Alpha158 子集)→ 可负担性(`BudgetTierPolicy`)→ top-N。读 PIT 快照(R0 §3),写 `SignalInputManifest`。

### 3.3 `backend/services/instruction_plan_builder.py`

第五道早返**保留**(最后防御)。新增上游 `CandidateSelector`(Phase M)不取代它。

### 3.4 验收 / 调度

P0-6 45 交易日滚动 acceptance 框架**不变**;但「信号生成 ≥95%」等指标的分母从「13 只」变「当日 shortlist」——指标定义在 P0-6 amendment(若需)细化,本 amendment 仅锁 universe 边界。

## 4. 红线清单(本 amendment 之后)

1. universe = 全 A 股主板 + 创业板 + ETF(规则集),**取消 13-code 写死**;板块白名单 + 排除规则 + long-only **仍 runtime 不可改**(改走 amendment + 重启)。
2. **科创 688 / 北交 8 / ST / 可转债 永禁**(P0-7 §2.4 不变);全市场筛**不得**纳入。
3. 排除四件套阈值不变,执行前移到 `screening` 硬排除 + fail-closed;Builder 第五道早返保留为最后防御;两处同一真相源。
4. `total_daily_cap=5`(traditional 4 + event 1,14:30 滑动)不变;event 由 MiroFish 建议核心驱动(P0-8 amend);小资金档由 `BudgetTierPolicy` 进一步收紧。
5. long-only + 6 forbidden_sides + `etf_arbitrage_enabled=false` 不变。
6. LLM 仅在全市场纯量化筛出 top-N(~50-100)小包之后介入;**超固定候选数不调 LLM**(R0 §8 + 成本红线)。
7. `watchlist*.py` / `universe*.py` API 仍**仅 GET**(P0-9 §1.3 / P1-5 §2 红线 1 不变)。
8. `backend/screening/` 严禁 `import backend.{llm,agents,mirofish}`(纯量化预筛;继承 P0-10 隔离)。

## 5. 修订记录追加

`docs/plan.html` Phase L 任务 + 修订记录 + SESSION_LOG 同步追加。CLAUDE.md §2.4 / §2.9 的「Watchlist 锁 13 标的」表述改写为「全市场 universe 规则 + 排除四件套 + long-only」。
