# M2 接手说明：完成阶段 B pilot 与 G1 证据门禁

> 日期：2026-08-13
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 接手分支：`agent/m2-evidence-reconstruction`
>
> 已推送恢复点：`d1c4288 feat(research): add M2 evidence reconstruction tools`
>
> 上游 kickoff：`KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`
>
> 当前阶段：阶段 A 已完成；阶段 B pilot 进行中；尚未通过 owner 门禁 G1

## 0. 直接开始的任务

不要重新讨论研究方向，也不要重做已经完成的全量资产审计。接手后直接完成剩余挑战性 pilot 的机器可读观察表和所需证据包，补齐 G1 所需的进攻、防守、消息、财报、持仓、加减仓和离场案例，然后向 owner 展示证据粒度与功能覆盖。

本轮目标是达到“可以请 owner 判断 G1”的状态，不是宣布 Base v1 已完成。G1 未通过前，不创建 `backend/playbook/yeren/`，不实现确定性交易执行器，不进入回测优化，更不能接入真实券商下单。

## 1. 开始前读取顺序

必须完整读取：

1. `AGENTS.md`；
2. `CLAUDE.md`；
3. 本文件；
4. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`；
5. `docs/research/midterm-rearch-action-plan-2026-08-12.md`；
6. `docs/research/yeren-system/data-and-source-coverage.md`；
7. `docs/research/yeren-system/research-methodology.md`；
8. `docs/research/yeren-system/casebook.md`。

代码发现继续遵循仓库规则：优先使用 codebase-memory MCP 的 `search_graph`、`trace_path`、`get_code_snippet` 和 `query_graph`；只有字符串、配置、非代码文件或图谱不足时才使用 `rg`。

## 2. 不可突破的边界

1. 永禁真实券商程序化下单；本项目只研究、回放、回测和模拟盘。
2. `data/marketdata_pit/` 和 `data/yeren_corpus/` 是既有 append-only 档案，严禁删改、严禁从零重下。
3. `data/yeren_research/` 也是本地 append-only 研究区。修订使用新文件名或 JSONL 追加记录，不覆盖已有判断。
4. 原始证据、主 agent 的解释和规则假设必须分层；不能把解释改写成博主原话。
5. `decision_bundle` 只能包含截止当时可见的资料；结果只能放进 `outcome_bundle`，不能用后续涨跌选择更赚钱的语义解释。
6. 核心视频理解、实体消歧、规则冲突和消息含义必须由当前主 agent亲自完成。关键词程序只找候选，不调用批量摘要模型代替逐视频判断，也不把这部分工作委派给子 agent。
7. 没有分钟/竞价证据时只做日级或方向性还原，不从日线高低价倒推盘中成交点。
8. 没有龙虎榜、订单流或原始消息时保持未知，不把“量化退出”“某游资参与”或传闻绑定为事实。
9. 只有日期、没有精确时刻的公告，保守从下一交易日开始影响可执行决策。
10. 若补数需要付费、账号或持续成本，先形成覆盖失败证据和选项，请 owner 决定；不要自行购买。

## 3. 已完成工作，不要重复

### 3.1 阶段 A 资产与来源审计

语料实况：

- 1,088 条唯一视频元数据；
- 1,087 份转写文件，50,952 个句段；
- 1 条终态不可得：`7672964034287880290`；
- 两个空文本：`7515275277196889359`、`7570934300965034432`；
- 六条视频存在末句比元数据时长多 2—229 ms 的小偏差，清单已写入覆盖文档和机器 inventory；
- 没有重复元数据、缺失转写或孤儿转写。

两个空文本已定向查看原音频和画面：均没有足够可靠的交易语音，不得补造转写。局部音频、接触表和复核结论保存在 `data/yeren_research/media/`；临时完整视频已经删除。

PIT 实况：

- 23 个 endpoint、23,978 条索引记录；
- `daily`、`daily_basic`、`adj_factor`、`fund_daily` 覆盖至 2026-07-10，共 2,798 个交易日；
- 多数涨跌停、停牌和 QGR 数据只到 2026-06-18；
- 财务报表及指标覆盖 2015Q1—2026Q1，保留 `ann_date` 和修订版本；
- 没有指数日线、分钟线、集合竞价、逐笔、龙虎榜、两融、订单流和完整历史 ST/概念成员；
- 本轮只读了约 30 GB PIT，没有改写或重下。

新闻实况：

- 本地 `quantmind.news_articles` 共 91,317 条，但最早只到 2026-05-29；
- 当前抓取器是 latest-only 聚合，没有严格历史 `as_of` API；
- Mongo 时间缺少逐来源时区契约，解析失败还可能回退到抓取当下时间；
- 2025 雅江开工、山河智能异常波动、西藏天路和 2026-08-02 券商传闻 benchmark 的正确时点召回均为 0；
- 2022—2025 历史消息必须从视频实际提及事件出发，优先寻找交易所、巨潮、政府或新华社原文。

机器清单：

- `data/yeren_research/inventory/assets-2026-08-13-v1.1.json`；
- `data/yeren_research/inventory/news-2026-08-13.json`；
- `data/yeren_research/events/benchmark-events-2026-08-13.jsonl`。

旧版 inventory/schema/financial 文件因为 append-only 原则仍保留；文档指向的 `v1.1` 是当前版本，不要删除旧版。

### 3.2 已实现的离线工具

`scripts/yeren_research/` 当前包含：

- 冻结的 Pydantic 观察表、证据、解释、规则链接、假设修订和 evidence bundle schema；
- 语料/PIT 覆盖审计；
- 本地新闻只读审计；
- 确定性关键词候选发现；
- PIT 日级行情 decision/outcome 分割；
- 按 `ann_date` 和下一存档交易日保守映射的财务 decision bundle；
- JSON schema 导出和单工件校验；
- create-only 的本地工件写入，避免误覆盖 append-only 研究记录。

CLI：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin

$PY/python -m scripts.yeren_research audit
$PY/python -m scripts.yeren_research audit-news
$PY/python -m scripts.yeren_research candidates --require market --require position
$PY/python -m scripts.yeren_research schema observation
$PY/python -m scripts.yeren_research validate observation <path>
$PY/python -m scripts.yeren_research validate bundle <path>
$PY/python -m scripts.yeren_research bundle-market --help
$PY/python -m scripts.yeren_research bundle-financial --help
```

不要为了阶段 B 预建数据库、消息总线、插件、兼容层或迁移框架。只有具体 pilot 证明现有结构无法表达时才修改 schema。

### 3.3 首个完整案例：雅江题材

已完整处理视频 `7534671965824175412`（2025-08-04 18:15:27 发布），最早跟随动作是 2025-08-05 09:30。

本地工件：

- `data/yeren_research/observations/7534671965824175412.json`；
- `data/yeren_research/decision_bundles/pilot-yajiang-2025-08-04.json`；
- `data/yeren_research/decision_bundles/pilot-yajiang-2025-08-04-financial-v1.1.json`；
- `data/yeren_research/outcome_bundles/pilot-yajiang-2025-08-04.json`；
- `data/yeren_research/cases/pilot-yajiang-2025-07-29-to-2025-08-04.json`；
- `data/yeren_research/hypotheses.jsonl`；
- `data/yeren_research/worklog.jsonl`。

当前实体判断：

- “大好河山”较可信地解析为山河智能 `002097.SZ`；
- “天路”解析为西藏天路 `600326.SH`；
- “小群”暂指陈小群，但没有龙虎榜，不能核验其真实参与；
- 雅江事件采用新华社 2025-07-19 16:18 原始发布；
- 山河智能异常波动采用巨潮资讯 2025-07-30 原公告，因只有日期，保守从 7 月 31 日 09:30 可用。

首例只支持以下候选，不得升级为稳定核心：

- `H-POSITION-CONVICTION-001`：模式内条件确认后扩大暴露，比例和作用层级未知；
- `H-EXIT-EXPECTATION-001`：短线次日不及预期及时离场；
- `H-SELECTION-HARD-LOGIC-001`：科技方向的硬逻辑和业绩支撑是过滤器，不是独立买点；
- `H-MICROSTRUCTURE-RELAY-001`：量化退出/游资接力只保留为待验证特例。

关键 ASR 边界：第一处“推仓位”由原 FunASR 和定向 faster-whisper 共同支持；紧接着的“推/退仓位”与上下文冲突，仍保持歧义，不得单独用于动作归纳。

结果隔离已经有变形测试：修改未来 outcome 记录不会改变 decision partition。8 月 5—6 日走势只用于验证，不得倒灌为 8 月 4 日判断依据。

## 4. 挑战性 pilot 清单

共选九条，首条完整观察表已经完成，另外八条需继续生成机器可读观察表和按需证据包：

| 视频 ID / 日期 | 必须覆盖的维度 | 接手重点 |
|---|---|---|
| `7526939325557165347` / 2025-07-14 | 混沌、退潮、空仓/管住手、止损、试错、业绩硬逻辑 | 区分混沌与退潮；不要把“三天后”机械定成固定窗口 |
| `7534671965824175412` / 2025-08-04 | 仓位、次日纠错、题材、参与者结构 | 已完成；只在新证据确实改变判断时追加修订 |
| `7552015322325699840` / 2025-09-20 | 禁止亏损补仓、原因型离场、一至三成仓示例 | 区分教学示例、真实动作、市场错杀持有和个股失效退出 |
| `7562170397225258280` / 2025-10-17 | 博主自己的系统 ontology、本金、开仓、仓位、卖出 | 数字胜率/仓位可能只是示例，不能直接参数化 |
| `7566602982977376372` / 2025-10-29 | 业绩兑现、无业绩支撑、利空落地 | 若公司实体不能可靠消歧，不用行业代表股补造财务包 |
| `7602233626348496192` / 2026-02-02 | 上午十点清仓、退潮、不信消息、拐点后轻仓试错 | “轻仓试错”是结合全片语境的 ASR 修订，比例未知 |
| `7626700051746734178` / 2026-04-09 | 流动性与消息、PCB/存储业绩落地离场 | 区分指数承接问题和个股/板块财报催化 |
| `7647445389893913039` / 2026-06-04 | 状态输入、主线、龙头、赚钱效应、超预期加仓 | 先形成 ontology，不在缺少阈值时硬写状态机 |
| `7669063381873462208` / 2026-08-02 | 五成阶段仓位上限、周末消息、竞价、后排退出 | PIT 日历止于 7 月 10；券商事件仍未定位，不能虚构下一执行时刻 |

这些视频的完整原转写已经由上一会话阅读过并写入案例簿，但接手 agent 对任何新增语义判断仍须检查完整上下文，不能只读上述摘要或关键词窗口。

## 5. 阶段 B 剩余工作顺序

### 5.1 先恢复和验证本地研究状态

1. 检查当前分支、最近提交和工作区，不改写用户已有变化。
2. 确认 `data/yeren_research/` 中上述工件仍存在；它被 `.gitignore` 忽略，但在当前工作机上应保留。
3. 运行 observation、bundle 和 hypothesis 的结构校验。
4. 若必须修订旧工件，使用 `-v1.2` 等新文件名或 JSONL 追加记录，并同步更新文档引用。

### 5.2 完成余下八个观察表

按发布时间顺序逐条处理。每份观察表至少做到：

- 引用原句和毫秒范围；
- 划分事实、市场状态、个股观点、消息/财报解释、已执行动作、计划、条件规则、复盘、教学和修辞；
- 建立录制区间、引用市场区间、证据可用时间和最早动作时间；
- 记录实体解析、替代候选和会改变交易动作的歧义；
- 将解释和原始证据分开；
- 只把有系统意义的内容链接到假设台账；
- 明确日级、盘中或方向性还原精度。

只有会改变证券、指标、仓位或动作方向的词才触发媒体复核。完整视频只作临时文件；保存必要的短音频或关键帧后清理整段媒体。

### 5.3 补一个明确证券实体的财报/公告黄金案例

首个雅江 financial bundle 证明工具能按 `ann_date` 截断，但视频的“业绩支撑”并未清楚绑定某一公司，因此它不能替代真正的财报语义案例。

从语料中选择一条明确绑定证券、公告或财报并确实改变持仓/选股/离场的案例：

1. 确认公司和证券实体；
2. 找交易所/巨潮原公告或 PIT 中当时可见的结构化财务行；
3. 区分事实、市场原预期、博主解释和实际动作；
4. 保守确定 `information_available_at` 和 `earliest_action_at`；
5. 形成 decision bundle、必要的 outcome bundle 和观察表；
6. 写出消息/财报到底改变了哪个决策。

不要先批量建设历史新闻湖。只有具体案例无法从官方来源重复取得时，再记录源缺口。

### 5.4 解决“禁止浮盈加仓”与“超预期加仓”的边界

至少找一个多日真实操作链，检查：

- 2025-09-20 所说“不能浮盈加仓”究竟禁止什么；
- 2026-06-04 所说“超预期加仓或推仓”触发了什么新证据；
- 两者是系统演化、不同交易类型、不同仓位层级，还是“浮盈本身不能触发，但新的超预期证据可以触发”；
- 是否有真实动作或反例支持，而不是只有教学金句。

在证据不足时保留冲突，不为了画完整状态机强行合并。

### 5.5 处理 2026-08-02 券商事件

当前 benchmark 已正式记录为未解析：

- 本地新闻在 2026-07-31 至 8 月 2 日没有可靠匹配；
- 公开搜索未可靠定位原始事件；
- 本地 PIT 交易日历只到 2026-07-10，不能从当前档案确定下一可执行时刻；
- 事件不得绑定具体券商或形成买卖规则。

接手时可做一次针对性的官方/可靠来源检索。如果仍无法定位，保留为“不可复原 benchmark”，不要反复搜索或用相似事件代替。

### 5.6 更新案例簿、ontology 和 G1 展示

当八个观察表和黄金案例完成后：

1. 追加/修订 `hypotheses.jsonl`，保留反证、例外和替代解释；
2. 更新跨视频案例链；
3. 更新 `docs/research/yeren-system/casebook.md`；
4. 仅在真实流程变化时更新 `research-methodology.md`；
5. 准备一份紧凑的 G1 展示，至少覆盖进攻、防守、消息、财报、持仓、加减仓和离场；
6. 明确哪些是稳定核心候选、阶段规则、战法特例和仍待确认项；
7. 请 owner 判断证据粒度和功能维度是否足够，不自行宣布通过 G1。

## 6. 已知实现语义

- 日线和日级榜单保守按当日 15:00 可见；盘中不能使用当日收盘数据。
- 午休发布映射到 13:00；盘后/周末发布映射到下一存档交易日 09:30。
- 财务 bundle 只选 cutoff 前已公告的最新 `fina_indicator_vip` 版本；仅有日期时不让同日交易使用，并映射到下一存档 A 股交易日 09:30。
- 证据包只保存所请求代码的关键行与全市场摘要，不复制完整 PIT frame。
- 请求的 endpoint、证券或日期缺失时写入 `omissions`，不能静默当作空信号。
- 当前 deterministic backtest 已是 T 日收盘决策、T+1 开盘成交，可留给 G2 后复用。
- 现有 `classify_regime` 只是 20 日均线的 BULL/BEAR/NEUTRAL，对照价值有限，绝不能作为博主市场状态模型的先验。
- 现有 `BackendStatementPIT` 能按 `ann_date`、报告期和修订版本选择历史可见财务，可继续复用。

## 7. 验证命令与当前基线

Python 一律使用：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
```

相关验证：

```bash
$PY/ruff check backend/ scripts/ tests/yeren_research/
$PY/mypy --explicit-package-bases scripts/yeren_research
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research
git diff --check
```

恢复点基线：

- Ruff：通过；
- Mypy：8 个源文件无问题；
- Pytest：17 passed；
- 1 份 observation、4 个 bundle、4 条 hypothesis revision 均已通过 Pydantic 校验。

改动什么就补什么测试，不为了覆盖率写无意义断言。若修改前端，才运行前端 type-check、vitest 和 build；本阶段当前没有前端改动。

## 8. G1 就绪条件

只有同时满足以下条件，才可以向 owner 申请 G1 审查：

1. 九条 pilot 都有明确分析状态；可用视频均有机器可读观察表；
2. decision/outcome 隔离在实际案例中持续成立；
3. 至少有一个公司实体明确、时间严格的公告/财报黄金案例；
4. 进攻、防守、消息、财报、持仓、加仓/禁止加仓和离场均有可定位证据；
5. 第一版 ontology 能表达真实案例，但没有伪造阈值或参数；
6. 候选规则、阶段规则、特例、反证和未知被明确分层；
7. 重要 ASR/实体/时间歧义都写明会造成什么交易差异；
8. 测试通过，研究工件可从 `worklog.jsonl` 恢复；
9. 没有创建生产战法、真实下单路径或未经 owner 允许的付费数据依赖。

G1 只判断证据粒度和功能覆盖。即使 G1 通过，也只是允许进入全量逐视频分析；仍不得跳到 Base v1 执行实现，后者必须等待全量研究、基础规范和 owner 门禁 G2。

## 9. 本轮结束时的汇报格式

向 owner 明确报告：

- 新完成了哪些视频、观察表和案例链；
- 哪些公告/财报/行情来源在正确历史时点可用；
- 新增、修订、反驳或仍冲突的规则；
- 哪些问题因证据缺失保持未知；
- G1 的七个功能维度如何被实际案例覆盖；
- 是否需要 owner 决定数据授权或重要语义；
- 测试结果和恢复点；
- 明确声明尚未通过 G1 或已提交 owner 审查，不能自行把“展示完成”写成“门禁通过”。

