# M2 阶段 C 长上下文接手说明：Claude Fable 5

> 日期：2026-08-13
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`
>
> 当前 HEAD：`e8194d5 docs(research): add M2 G1 handoff`
>
> 当前恢复点：`M2-C-batch-005`
>
> 当前门禁：**G1 已通过、阶段 C 进行中、G2 未到**
>
> 本轮建议目标：连续完成固定序号 **101—200**（batch-005—008），随后完成第二个百条综合；若工具时间仍充足，可以继续处理后续完整 25 条批次，但绝不能在批次中间留下半成品。

## 0. 先理解这项工作是什么

QuantMind 正在重构为中长线 A 股投研系统。M2 当前任务不是写交易策略代码，也不是回测择优，而是把“全能的野人”视频语料逐条还原为可审计、时间隔离、能容纳冲突和未知的证据层，以便后续再判断哪些内容能进入 Base、战法、风险约束或反证集合。

阶段 A 已完成 PIT 与来源能力盘点；阶段 B 已完成 pilot、黄金案例和 G1 审查；owner 已明确说过：

```text
G1通过，可以进入全量分析.
```

因此现在处于阶段 C 的全量逐视频研究。不要重新询问 G1，不要重做阶段 A，不要重做 pilot，也不要把旧文档中的 “G1 尚未通过” 当成当前门禁。

唯一不可越过的底线是：**永禁真实券商程序化下单**。系统只维护模拟盘。不得创建、试探或预埋任何真实下单能力。

## 1. 干净上下文开始时直接执行什么

1. 按第 2 节顺序完整阅读所有必读文档，不要只读摘要或最后几段。
2. 核对分支、HEAD、dirty worktree 和研究区计数，不要覆盖现有改动。
3. 从 `metadata.jsonl` 按 `[published_at, aweme_id]` 升序重算固定序号。
4. 从第 101 条开始，由主 agent 亲自阅读完整转写并逐条分析。
5. 以 25 条为不可拆分 checkpoint：每完成 25 条就写 observation、必要证据包、case、hypothesis revision、worklog，并跑完整性检查。
6. 利用 1M 上下文保持跨批次连续性，默认连续推进 101—200，而不是只处理 101—125；但不能以批量摘要代替逐视频证据工作。
7. 完成第 200 条后，基于 1—200 条做第二个百条人工综合和冲突审查，追加到 casebook。
8. 如果继续超过 200 条，仍按每 25 条 checkpoint、每新增 100 条综合一次。若时间或工具预算将耗尽，只能停在完整 25 条边界，并把 `resume_from` 指向下一批。

不要等待 owner 再确认。现在的授权范围已经足够推进阶段 C。

## 2. 必须完整读懂的文档与数据

按以下顺序完整读取。带“必须全文”的文件不能只看标题、摘要或当前段落。

### 2.1 仓库规则与总行动纲领

1. `AGENTS.md`：仓库唯一工作守则，必须全文读取。
2. `CLAUDE.md`：与 AGENTS 同步维护，必须全文读取。
3. 本文件：`KickoffPrompts/M2-phase-C-claude-fable-long-context-handoff-2026-08-13.md`。
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`：当前重构的行动纲领，必须全文读取。

注意：`docs/archive/` 中 2026-08-12 之前的旧红线、冻结原则已经作废，只能作历史参考，不能拿来阻止当前行动纲领授权的修改。

### 2.2 M2 起点、方法和阶段 C 规则

5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`：理解 M2 为什么先做证据对齐、时间隔离和规则重建。
6. `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`：阶段 C 的长期执行规范，必须全文读取。
7. `KickoffPrompts/M2-phase-C-batch-004-continuation-2026-08-13.md`：最近一个已完成批次的严格工作方法和完成定义，必须全文读取；其中 `resume_from=M2-C-batch-004` 已过期，以本文件和最新 worklog 为准。
8. `docs/research/yeren-system/research-methodology.md`：证据、事实、解释、规则、反证、未知和 outcome 隔离方法，必须全文读取。
9. `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`：owner 对“预期—反馈偏差”的目标系统语义，必须全文读取。它是设计方向，不得冒充成博主已经具备的规则。
10. `docs/research/yeren-system/data-and-source-coverage.md`：现有数据能力、真实缺口和不能伪造的字段，必须全文读取。

### 2.3 已有证据结论、pilot 边界和最新进度

11. `docs/research/yeren-system/casebook.md`：必须全文读取，尤其是“阶段 C 首轮 1—100 条人工综合（batch-004）”。
12. `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`：理解 G1 为什么通过、哪些仍只是候选。
13. `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`：只用于 pilot 细节；其中旧的等待 G1 文字不再有效。
14. `data/yeren_research/worklog.jsonl`：逐行完整读取。最后一条必须是 `M2-C-batch-004`，`status=completed`，`resume_from=M2-C-batch-005`。
15. `data/yeren_research/hypotheses.jsonl`：逐行完整读取。理解 revision 链、支持、反证、例外和 alternative explanation，不能只读最后几条。
16. `data/yeren_research/cases/` 下所有 case JSON，至少必须全文读取 batch-001—004 的八条连续案例，以及与即将分析视频重叠的 pilot case。

最新两条连续案例是：

- `data/yeren_research/cases/batch004-yajiang-role-selection-and-repair-2025-07-25-to-2025-08-05.json`；
- `data/yeren_research/cases/batch004-tech-trend-to-profit-lock-2025-07-24-to-2025-08-04.json`。

### 2.4 开始逐视频前必须了解的机器记录

17. `scripts/yeren_research/schema.py`：读懂 `VideoObservation`、`EvidenceBundle`、`HypothesisRevision` 的 schema 和验证边界。
18. `data/yeren_corpus/metadata.jsonl`：固定排序和批次边界的唯一来源。
19. `data/yeren_corpus/ledger.jsonl`：摄取最终状态；它不能替代 metadata 排序。
20. `data/yeren_corpus/transcripts/<aweme_id>.json`：每条视频必须读取完整 `text` 和完整 `sentences`，不能只抽关键词句。

代码发现遵守仓库规则：项目知识图谱名称为 `home-ps-papers-QuantMind`；查函数、类、调用关系时优先使用 codebase-memory MCP 的 `search_graph`、`trace_path`、`get_code_snippet`、`query_graph`。JSON、JSONL、Markdown、标题和字符串才用 `rg`、`jq`、`sed`。

## 3. 当前真实基线

截至 `M2-C-batch-004` 完成：

- metadata 总记录：1,088；
- 固定序号 1—100 已全部人工分析；
- observation 工件：111；
- 唯一 observation 视频：109；
- hypothesis revision：55；
- decision/outcome bundle：64；
- case：11；
- batch-004 新增 24 个首次 observation，复用第 98 条既有 pilot observation；
- batch-004 新增 5 组 decision/outcome、2 条 case、12 条 hypothesis revision；
- batch-004 的 56 个 transcript span 已逐字、逐时间戳核验；
- `tests/yeren_research`：20 passed；
- `git diff --check`：passed。

最新 worklog 的门禁必须原样延续：

```text
G1 已通过、阶段 C 进行中、G2 未到
```

研究区位于 `data/yeren_research/`，被 Git 忽略。`git status` 看不到 observation、bundle、case、media、hypothesis 和 worklog 的新增，不代表它们不存在。

## 4. 工作区保护

开始前运行：

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
```

当前分支为 `agent/m2-evidence-reconstruction`，HEAD 为 `e8194d5`。当前 tracked/untracked 改动包含 owner 和前序任务成果，必须全部保留：

- `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`；
- `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`；
- `KickoffPrompts/M2-phase-C-batch-004-continuation-2026-08-13.md`；
- 本接手文件；
- `backend/marketdata_snapshot/coverage.py`；
- `backend/marketdata_snapshot/store.py`；
- `docs/research/yeren-system/casebook.md`；
- `docs/research/yeren-system/data-and-source-coverage.md`；
- `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
- `docs/research/yeren-system/research-methodology.md`；
- `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
- `scripts/factor_research/ingest_round2_data.py`；
- `tests/marketdata_snapshot/test_coverage.py`；
- `tests/marketdata_snapshot/test_snapshot_store.py`。

禁止 `git checkout --`、`git reset`、批量格式化或其他覆盖性操作。不要把前序 dirty changes 当成自己需要清理的垃圾。

`data/marketdata_pit/`、`data/yeren_corpus/`、`data/yeren_research/` 都按 append-only 处理：

- 不删除、不覆盖既有档案；
- observation 语义改变时新增 `-v1.1`、`-v1.2`，旧版保留；
- hypothesis 用新 revision 追加，禁止回写历史行；
- worklog 只追加；
- 不从零重下约 29 GB PIT；
- 删除任何非临时文件前先确认没有引用。

本任务不 commit、不 push。只有 owner 明确授权后才能 push；禁止 `--no-verify`。

## 5. 长上下文推进策略与固定批次

### 5.1 为什么仍保留每 25 条 checkpoint

Claude 可以在一个上下文中连续分析更多视频，但证据写入、验证和恢复点仍必须每 25 条闭环。长上下文的优势用于：

- 连续追踪跨日持仓、题材和账户演化；
- 及时识别与前 100 条的支持、冲突和反例；
- 减少跨批次遗忘，而不是减少逐条阅读；
- 一次完成多个完整批次和百条综合。

每个 25 条批次都必须独立满足完成定义并追加一条 worklog。不能先粗读 100 条、最后再批量生成 observation；每条 observation 仍要在阅读后落盘并立即校验。

### 5.2 本轮默认处理范围

默认连续完成以下四个固定批次：

| work unit | 固定序号 | 首条 | 末条 | 发布时间范围 |
|---|---:|---|---|---|
| `M2-C-batch-005` | 101—125 | `7535313829762403619` | `7538715221079969059` | 2025-08-06 11:46:15 — 2025-08-15 15:45:21 |
| `M2-C-batch-006` | 126—150 | `7539398044149943592` | `7543550490111397135` | 2025-08-17 11:55:04 — 2025-08-28 18:28:00 |
| `M2-C-batch-007` | 151—175 | `7543698270926376227` | `7547964319943003426` | 2025-08-29 08:02:00 — 2025-09-09 15:59:00 |
| `M2-C-batch-008` | 176—200 | `7548087899808566568` | `7550713207926574376` | 2025-09-09 21:56:06 — 2025-09-16 23:43:42 |

这些边界是当前只读重算结果。实际开始每批前仍必须从 metadata 重算，不得把表格当成不可校验的手工真相：

```bash
jq -s 'sort_by([.published_at, .aweme_id]) | .[100:200] |
  to_entries | map({ordinal:(.key + 101), aweme_id:.value.aweme_id,
                    published_at:.value.published_at, title:.value.title,
                    duration_ms:.value.duration_ms})' \
  data/yeren_corpus/metadata.jsonl
```

第 101—125 条明细如下：

| 序号 | 发布时间 | 视频 ID | 时长 ms | 标题摘要 |
|---:|---|---|---:|---|
| 101 | 2025-08-06 11:46:15 | `7535313829762403619` | 35688 | 午间实盘 |
| 102 | 2025-08-06 15:38:10 | `7535373610963094836` | 157034 | 收盘实盘/交易知识 |
| 103 | 2025-08-07 11:37:34 | `7535682692894149922` | 28667 | 午间实盘 |
| 104 | 2025-08-07 15:27:53 | `7535742039554002176` | 19016 | 收盘短视频 |
| 105 | 2025-08-08 08:45:35 | `7536009441163627811` | 153900 | 盘前/方法 |
| 106 | 2025-08-08 11:39:07 | `7536054173079964943` | 46834 | 午间实盘 |
| 107 | 2025-08-08 19:39:19 | `7536177910375386408` | 126667 | 晚间实盘 |
| 108 | 2025-08-09 20:55:46 | `7536568708933520674` | 180023 | 周末股票知识 |
| 109 | 2025-08-09 21:00:48 | `7536570006597176611` | 180067 | 周末股票知识 |
| 110 | 2025-08-10 19:31:40 | `7536918117450173730` | 180034 | 周末交易内容 |
| 111 | 2025-08-11 11:38:49 | `7537167342288669967` | 21534 | 午间实盘 |
| 112 | 2025-08-11 15:41:12 | `7537229816945200384` | 40734 | 收盘实盘 |
| 113 | 2025-08-12 12:02:57 | `7537544650644294946` | 90234 | 午间实盘 |
| 114 | 2025-08-12 15:27:03 | `7537597244871527714` | 43300 | 收盘实盘 |
| 115 | 2025-08-13 08:23:54 | `7537859265576684834` | 124034 | 盘前/直播相关 |
| 116 | 2025-08-13 11:37:42 | `7537909214582459700` | 82667 | 午间实盘 |
| 117 | 2025-08-13 15:36:19 | `7537970725798481204` | 89744 | 收盘实盘 |
| 118 | 2025-08-13 20:58:26 | `7538053740763303203` | 180034 | 核心/交易知识 |
| 119 | 2025-08-14 11:33:54 | `7538279333426351375` | 39667 | 午间实盘 |
| 120 | 2025-08-14 12:03:01 | `7538286841238932770` | 98834 | 午间实盘 |
| 121 | 2025-08-14 12:42:30 | `7538297012232490292` | 180034 | 交易知识 |
| 122 | 2025-08-14 15:15:35 | `7538336455774326016` | 44117 | 收盘实盘 |
| 123 | 2025-08-14 23:19:41 | `7538461220945366312` | 180023 | 晚间交易知识 |
| 124 | 2025-08-15 12:04:46 | `7538658370959789364` | 174900 | 午间实盘 |
| 125 | 2025-08-15 15:45:21 | `7538715221079969059` | 63434 | 收盘实盘 |

后续批次只允许从完整边界继续，不得因已有 observation、空转写、短视频或不可得媒体而跳过并从后面补足 25 条。

## 6. 已有 observation 与 pilot 的复用规则

在写每条 observation 前先按 `aweme_id` 检查是否已有文件。已有 observation 默认复用，仍须在当前时间链中全文阅读；只有新上下文、定向音频或原始来源实质改变以下内容时才新增 append-only 版本：

- 证券或题材实体；
- 账户与交易类型；
- 买、卖、加、减、持有或观望方向；
- 仓位数字或分母；
- 时间窗口和最早可行动时刻；
- 会改变候选规则或交易后果的语义。

仅修正标点、让文字更顺、增加不影响动作的背景，不得制造新版本。

已知 pilot/golden 视频包括但不限于：

```text
7526939325557165347
7534671965824175412
7552015322325699840
7562170397225258280
7566602982977376372
7602233626348496192
7626700051746734178
7647445389893913039
7658346105893142863
7660119917865709391
7669063381873462208
```

碰到这些 ID 时必须先读现有 observation、case、bundle 和 media review，禁止重复生产。`7534671965824175412` 在 batch-004 已再次全文复读，核心语义未改变，继续复用原 observation。

## 7. 每条视频必须完成的工作

主 agent 必须亲自完成，不得交给子 agent 或批量摘要模型代读完整转写。

对每条固定记录：

1. 读取 metadata、ledger 状态、标题、发布时间、时长，以及完整 transcript `text` 和所有 `sentences`。
2. 区分：可验证事实、市场状态、证券观点、消息/财报解释、已执行动作、计划动作、条件规则、复盘、教学和修辞。
3. 消歧证券、题材、账户、交易类型，以及“今天、明天、周末、下周”等时间指代。无法唯一确认就写 alternatives、依据和交易后果，不猜。
4. 写清 `recording_time_interval`、`referenced_market_intervals`、每条证据的 `information_available_at`、`earliest_action_at` 和 `reconstruction_precision`。
5. transcript 原文只放 `transcript_span.raw_text`；`sentence_index`、`end_sentence_index`、`start_ms`、`end_ms` 必须与源文件逐字逐时戳一致。transcript evidence 的 `content` 必须为 `null`。
6. 先在 publication cutoff 下完成 decision 解释，再打开未来行情写 outcome；decision/outcome 必须物理分文件。
7. 只有原话实际提出行情、公告、财报、事件或证券级验证问题时才建最小必要 bundle。修辞、闲聊、重复教学可以零 bundle、零 hypothesis。
8. statement 只陈述原话支持的事实或动作；interpretation 单独写依据、强度、反证和 alternative explanation。
9. rule link 只能引用已存在或本批将追加的 hypothesis ID，fragment 必须真实存在。
10. 只有改变系统理解时才追加 hypothesis support/refute/revision；孤立口号不升级，revision 必须正确填写 `revision_of`。
11. 同一证券、题材、账户或仓位跨日连续时建立最小 case chain，不拆成彼此失联的金句。
12. observation 落盘后立即执行 Pydantic 校验；不要等 25 条全部生成后才发现 schema 或引用错误。

空文本或不可得记录也不能跳出固定包：根据 ledger 和既有复核证据写明确 `analysis_status`；只有会改变证券、指标、仓位或动作方向的问题才定向补媒体。

## 8. 当前必须继续检验的系统问题

前 100 条综合已经得到以下窄结论。后续不是为了证明它们，而是主动找支持、反例、例外和时间演化：

1. 指数、全市场广度、短线情绪、题材、证券和个人账户必须分层；任一层强势都不自动授权另一层进攻。
2. 混沌或退潮主要关闭新短线风险，不自动清掉所有存量波段仓；状态、账户和交易周期要同时记录。
3. 题材首日错过后等待反馈，但入口会因连续一致上涨而过期；分歧、核心或回封标签都不是独立买点。
4. 右侧趋势仓偏向在上沿或加速阶段减仓，而不是第一次普通回撤；工业富联只是一个已消歧单例，不能外推成“触板必卖”。
5. 本金和已得利润优先；减仓、轮动和提现是不同组合动作，不能只看其中一笔推断净暴露。
6. “预期—反馈—动作”已有连续支持，但评估窗口、阈值、账户分母和失败撤回仍不完整。

必须主动复核的冲突包括：

- “利好是好事、利空也是好事”是否仍是无失败条件的不可证伪叙事；
- “混沌不动”与“硬逻辑持有”能否继续由账户/周期分层解释；
- “每次回调可买”与“加速退出”的趋势阶段边界；
- 换手核心和一字核心是否存在稳定排序，还是仅为雅江阶段特例；
- 弱修复对已有仓和场外者的动作是否持续不同；
- 同日退潮—回流、跨日弱—强修复是否出现可观察但不过拟合的迁移条件；
- 规则是否随 2025 年市场阶段、作者账户规模或表达方式发生演化。

不要为了形成漂亮状态机而抹平冲突。无法解释的冲突可以作为 counterevidence 或 unresolved 保留。

## 9. 数据、事件、PIT 与媒体边界

- 所有行情、公告、财报、事件必须以当时真实可见时间为边界。
- decision record 要满足 `information_available_at <= decision_cutoff`；outcome record 必须 `information_available_at > decision_cutoff`。
- 公告只有日期没有时刻时，保守从下一交易日 09:30 可用。
- 没有历史分钟、竞价、封单、炸板路径时，只做日级或方向性复原，不伪造盘中精度。
- “量化、游资、主力、埋伏盘、资金干净、洗盘、机构一直买”没有席位、持仓或订单流时只能作为口播解释。
- 具体历史事件优先交易所、巨潮、政府、监管、公司原文；新闻聚合只能辅助检索。
- Tushare 仅使用官方 SDK `ts.pro_api()`；`*_vip` 必须 limit+offset 分页；`report_rc.tp` 是利润总额，目标价字段是 `min_price`。
- 出站请求使用 IPv4-only，例如 httpx 的 `local_address="0.0.0.0"`；服务只监听 `127.0.0.1`。
- 只有会改变动作语义的 ASR/画面歧义才下载必要局部媒体。已有局部音频优先复用；不做无边界整库媒体下载。
- 临时完整视频在截出必要局部后不留在研究档案；保留的局部媒体和复核 JSON 要写清来源区间、问题、结论、依据和剩余未知。
- 秘密只从现有 shell 环境读取，严禁把任何 API key 写进代码、文档、测试或 `.env`。

只有出现新的来源类别或真正新的数据缺口时才更新 `data-and-source-coverage.md`。证券实体未消歧、分钟数据缺失、真实账户分母未知通常是既有缺口的具体实例，不要为了显得完整重复扩表。

## 10. 每 25 条批次的完成定义

每个 `M2-C-batch-NNN` 必须同时具备：

- 固定 25 条均有明确分析状态；
- 所有可用转写均由主 agent 全文读取；
- 无 observation 的视频新增首次 observation；已有 observation 明确记录 reuse 或有证据的版本修订；
- 必要 decision/outcome bundle 严格未来隔离；
- 连续操作建立最小必要 case；
- hypothesis 只追加系统级变化；
- 本包所有 transcript span 与源句段、起止时戳和 raw text 完全一致；
- statement、interpretation、rule link、revision、observation fragment、case 和 bundle 引用无悬空；
- worklog 追加本批边界、输出、主要发现、反例、数据缺口、媒体复核、验证、开放项和下一恢复点；
- 门禁精确写作“G1 已通过、阶段 C 进行中、G2 未到”。

不得跨包留下“已读但未写 observation”或“已写 observation 但未校验”的半成品。如果本轮只能完成 101—175，就必须完整收口 batch-007，再把 `resume_from` 指向 batch-008；不要开始第 176 条。

## 11. 每 100 条的综合要求

完成第 200 条后，在 `docs/research/yeren-system/casebook.md` 追加“阶段 C 第二轮 101—200 条人工综合”。不要机械重述 100 个 observation，必须回答：

1. 前 100 条候选规则在 101—200 中哪些得到跨状态、跨题材、跨账户重复支持；
2. 哪些出现直接反证、例外、账户层级差异或时间演化；
3. 新形成了哪些完整的前提—介入—加减仓—变化—离场—复盘案例；
4. “预期—反馈偏差”新增了哪些动作前基准、评估窗口、分支和撤回证据；
5. 哪些看似冲突可由市场层、题材层、证券层、账户层或交易周期解释，哪些仍不能解释；
6. 新缺口是否属于已有来源类别，是否真的需要扩展 coverage；
7. 是否有一个会实质改变交易动作、且公开证据无法解决的问题必须询问 owner。

第二个百条综合仍不冻结 Base v1，不确定状态参数，不进入阶段 D，不提交 G2。完成综合后再决定是否继续 batch-009。

## 12. 验证命令与只读完整性检查

Python 一律使用：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
```

每个新 observation 立即校验：

```bash
$PY/python -m scripts.yeren_research validate observation \
  data/yeren_research/observations/<aweme_id>.json
```

每个 25 条 checkpoint 至少运行：

```bash
FEISHU_INTERACTIVE_ENABLED=false $PY/python - <<'PY'
from pathlib import Path

from scripts.yeren_research.schema import (
    EvidenceBundle,
    HypothesisRevision,
    VideoObservation,
)

root = Path("data/yeren_research")
observations = sorted((root / "observations").glob("*.json"))
for path in observations:
    VideoObservation.model_validate_json(path.read_text(encoding="utf-8"))

hypotheses = [
    line
    for line in (root / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
    if line
]
for line in hypotheses:
    HypothesisRevision.model_validate_json(line)

bundles = []
for folder in ("decision_bundles", "outcome_bundles"):
    for path in sorted((root / folder).glob("*.json")):
        EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
        bundles.append(path)

print(
    f"observation_artifacts={len(observations)} "
    f"hypotheses={len(hypotheses)} bundles={len(bundles)}"
)
PY

FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research
git diff --check
git status --short
```

此外必须自己写只读完整性检查，逐项验证：

- metadata 固定边界与本批首末 ID；
- 本包每个 transcript span 的句段范围、`start_ms`、`end_ms`、`raw_text` 与源转写完全一致；
- statement/interpretation/rule link 本地引用存在；
- rule link 的 hypothesis ID 存在；
- hypothesis 的 `revision_of` 指向此前已有 revision；
- hypothesis 的 observation fragment、case 和 event 引用存在；
- decision/outcome cutoff 条件成立；
- case 引用的 observation 和 bundle 文件存在，case 中的 `published_at` 与 observation 完全一致；
- 每个 decision bundle 有需要的 outcome 配对，反之亦然；
- worklog 每一行都是合法 JSON，最后一条 work unit、gate 和 `resume_from` 正确；
- 所有必要局部媒体和 media review 文件真实存在。

阶段 C 正常只写研究数据和中文文档，不应修改生产代码。若确实修了 schema 或研究工具，才额外运行对应 ruff、mypy 和相关测试，并在 worklog 说明为什么修改。没有前端任务，不运行前端构建。

## 13. 反过度防御和研究质量红线

- 不写无实际用途的校验和、指纹或摘要。
- 不为本项目不会出现的输入写防御。
- 不用评分表或机械清单代替人的语义判断。
- 不为想象的未来需求预建开关、兼容层、迁移框架或状态机。
- 搜索发现不受限，但修复只围绕当前阶段 C 主线；主线外问题记录即可，不阻塞。
- 不因为某个解释漂亮、后续收益好或容易编码就选择它。
- 不把作者修辞、情绪安慰、事后归因或单次盈利升级为稳定规则。
- 不用未来结果反向消歧当时未知的证券、账户、动作或事件。
- 不用全市场涨跌替代目标证券反馈，也不用单票结果代表完整账户。

每一段防御性代码或额外流程前问：它能检测什么具体故障，发现后会改变什么决定？答不上来就不要写。

## 14. 何时询问 owner

默认自主推进，不要因普通歧义停下。只有同时满足以下条件时才向 owner 提一个具体问题：

1. 问题会实质改变买、卖、加、减、持有、观望或风险上限；
2. 已穷尽连续转写、已有局部媒体、PIT、官方公告和现有 case；
3. 两种解释仍都可信；
4. 不回答就无法在不伪造证据的前提下继续当前完整批次。

账户分母未知、证券角色未消歧或分钟阈值缺失通常可以明确记为 unknown 后继续，不必每次打断 owner。

## 15. 最终汇报格式

本轮结束时向 owner 简洁但完整汇报：

- 实际完成的固定序号、日期范围、批次数和逐条处理状态；
- 每批新增/复用/修订的 observation 数量；
- decision/outcome 组数、case、event、hypothesis revision、媒体复核数量；
- 哪些规则得到支持、反证、修订，哪些显示账户或时间演化；
- 若到 200 条，给出第二个百条综合的核心结论；
- 哪些实体、账户、ASR、时点、阈值和数据仍未知，是否改变动作；
- schema、引用、span、cutoff、case、worklog、pytest 和 git 检查结果；
- 当前工作区未提交、未推送；
- 精确的下一 `resume_from`；
- 当前门禁：**G1 已通过、阶段 C 进行中、G2 未到**。

现在直接从 `M2-C-batch-005` 开始。先完整读取必读材料，再重算 101—200 边界；不要重新讨论是否进入阶段 C，也不要等待确认。
