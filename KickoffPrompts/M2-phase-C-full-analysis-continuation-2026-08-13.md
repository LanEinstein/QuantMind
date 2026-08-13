# M2 接手说明：阶段 C 全量逐视频分析（G1 已通过）

> 日期：2026-08-13
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`
>
> 远端基线：`e8194d5 docs(research): add M2 G1 handoff`
>
> 上游接手文档：`KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`
>
> 当前阶段：**owner 已明确通过 G1；允许进入 M2 阶段 C 全量逐视频分析**
>
> 当前恢复点：`M2-C-batch-001`

## 0. 新上下文直接执行什么

不要再次询问 G1，不要重做阶段 A 的 30 GB PIT 审计，不要重做阶段 B pilot、江波龙黄金案例或 8 月 2 日券商事件调查。

直接从 `M2-C-batch-001` 开始：按 `metadata.jsonl` 的 `published_at` 从早到晚，以固定 25 条为一个工作包，当前先完整处理第 1—25 条。每个工作包完成 observation、实际需要的 decision/outcome bundle、假设追加、案例链、验证和 worklog 后，再进入下一包；不要跨包留下半成品。

总目标仍是完成全部 1,088 条记录的明确分析状态。每完成约 100 条（四个工作包），做一次阶段综合与冲突审查。无需在每个工作包后等待 owner 重新授权；只有会实质改变交易动作且无法从证据解决的歧义，才向 owner 提一个具体问题。

## 1. G1 的准确含义

owner 于 2026-08-13 明确回复：

> G1通过，可以进入全量分析.

该决定已追加到 `data/yeren_research/worklog.jsonl`，记录名为 `M2-G1-owner-gate-passed`。

G1 只确认：当前 observation、evidence bundle、hypothesis revision 的证据粒度，以及进攻、防守、消息、财报、持仓、加仓/禁止加仓、离场七个功能维度，足以支撑全量研究。

G1 **不代表**：

- Base v1 已冻结；
- 可以创建 `backend/playbook/yeren/` 或确定性状态机；
- 可以开始回测择优或参数优化；
- 可以把 owner 的目标系统增强方向冒充成博主原话；
- 可以接入真实券商自动下单。真实券商程序化下单永禁。

阶段 C 结束并形成完整规则基础规范后，仍须经过 G2，才能进入工程实现与历史复现。

## 2. 开始前必须完整读取

按以下顺序读取，不能只读摘要：

1. `AGENTS.md`；
2. `CLAUDE.md`；
3. 本文件；
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`；
5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`；
6. `docs/research/yeren-system/research-methodology.md`；
7. `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
8. `docs/research/yeren-system/data-and-source-coverage.md`；
9. `docs/research/yeren-system/casebook.md`；
10. `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
11. 上游接手文档 `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`，只用于查 pilot 细节，不以其中历史进度覆盖本文件。

代码发现继续遵守仓库规则：项目图谱为 `home-ps-papers-QuantMind`，优先使用 codebase-memory MCP 的 `search_graph`、`trace_path`、`get_code_snippet` 和 `query_graph`；字符串、JSON/JSONL、Markdown 和其他非代码文件才使用 `rg`、`jq`、`sed`。

## 3. 接手时工作区与恢复点

先执行：

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
```

截至本次接手更新，tracked 工作区包含 G1 通过、PIT 增量摄取性能修复与交接文档修改，尚未 push。必须保留这些改动，不要用 checkout/reset 覆盖：

- `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`；
- `docs/research/yeren-system/casebook.md`；
- `docs/research/yeren-system/data-and-source-coverage.md`；
- `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
- `docs/research/yeren-system/research-methodology.md`；
- `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
- `backend/marketdata_snapshot/coverage.py`；
- `backend/marketdata_snapshot/store.py`；
- `scripts/factor_research/ingest_round2_data.py`；
- `tests/marketdata_snapshot/test_coverage.py`；
- `tests/marketdata_snapshot/test_snapshot_store.py`；
- 本文件。

`data/yeren_research/` 被 Git 忽略且按 append-only 处理。它包含当前 observation、bundle、case、event、media、hypothesis 和 worklog；最新 PIT 机器清单为 `inventory/assets-2026-08-13-v1.3.json`，旧 `v1.1`、`v1.2` 均保留。同一工作区的新上下文可以直接继续。如果换机器或目录缺失，不能仅凭文档重造并覆盖历史，应先恢复本地研究区。

本次 PIT 更新额外暴露并修复了两个已复现瓶颈：coverage 批量比较从“每期重读 1 GB JSONL”改为一次扫描；`SnapshotStore` 在索引文件未变化时复用内存复合键索引，同时继续对读取 payload 做 checksum 校验，并在文件大小/mtime 变化时看见其他进程追加。291 项相关测试通过。不要回退这两处修改，否则 QGR 幂等续跑会再次在上万个既有键上反复解析完整索引。

最小检查：

```bash
test -f data/yeren_research/worklog.jsonl
test -f data/yeren_research/hypotheses.jsonl
test -f data/yeren_research/events/broker-regulatory-actions-2026-07-31.json
test -f data/yeren_research/observations/7669063381873462208-v1.1.json
find data/yeren_research/observations -maxdepth 1 -type f | wc -l
tail -n 3 data/yeren_research/worklog.jsonl
```

预期：

- observation 工件 12 份，对应 11 个唯一视频；
- hypothesis revision 20 条；
- decision/outcome bundle 共 14 份；
- 最新 G1 worklog 为 `M2-G1-owner-gate-passed`，`resume_from=M2-C-batch-001`。

## 4. Corpus 与固定工作包规则

当前 corpus：

- `data/yeren_corpus/metadata.jsonl`：1,088 条；
- `data/yeren_corpus/transcripts/`：1,087 份；
- `data/yeren_corpus/ledger.jsonl`：按每个 `aweme_id` 最后一条状态计，1,087 个 `done`、1 个 `unavailable`；
- 两条转写为空：`7515275277196889359`、`7570934300965034432`；
- 一条终态不可得：`7672964034287880290`。

工作包边界固定在按 `published_at` 升序排列的 **metadata 记录** 上，每 25 条切一包；不要先排除空文本、不可得记录或已完成 pilot 再凑 25 条，否则后续恢复点会漂移。若时间相同，以 `aweme_id` 升序作为稳定次序。

生成任意工作包的只读命令示例：

```bash
BATCH=1
START=$(( (BATCH - 1) * 25 ))
jq -s --argjson start "$START" \
  'sort_by([.published_at, .aweme_id]) | .[$start:$start + 25] |
   map({aweme_id, published_at, title, duration_ms})' \
  data/yeren_corpus/metadata.jsonl
```

遇到已有 current observation 的 pilot 视频时，复用已有分析并在包记录中标记 `already_analyzed`；除非新证据改变语义，否则不要复制 observation。8 月 2 日视频的 current 版本是 `7669063381873462208-v1.1.json`，无后缀旧版仅是 append-only 历史。

## 5. 当前工作包：M2-C-batch-001

固定范围为排序后的第 1—25 条：

| 序号 | 发布时间 | 视频 ID | 备注 |
|---:|---|---|---|
| 1 | 2022-07-07 20:31:35 | `7117607311631289600` | 日落内容离群记录；只有两句疑似歌词/环境音 ASR，不能强行提炼交易规则 |
| 2 | 2025-06-03 21:56:59 | `7511721750112029991` | 首个 2025 交易视频 |
| 3 | 2025-06-04 15:10:51 | `7511988173670665484` | — |
| 4 | 2025-06-05 15:48:42 | `7512369013927857442` | — |
| 5 | 2025-06-05 22:12:56 | `7512468025557650740` | — |
| 6 | 2025-06-08 14:05:01 | `7513455544188538164` | 约 178 秒，可能包含较完整方法论 |
| 7 | 2025-06-09 11:42:36 | `7513789946668617000` | — |
| 8 | 2025-06-09 15:13:36 | `7513844279061990696` | — |
| 9 | 2025-06-10 11:13:01 | `7514153388309826868` | — |
| 10 | 2025-06-10 16:54:20 | `7514241365988265231` | — |
| 11 | 2025-06-11 11:42:37 | `7514532080572665140` | — |
| 12 | 2025-06-11 15:29:23 | `7514590562638040360` | — |
| 13 | 2025-06-12 15:39:19 | `7514964177527278882` | — |
| 14 | 2025-06-13 11:46:32 | `7515275277196889359` | 空转写；已有定向音画复核，禁止重跑无边界下载 |
| 15 | 2025-06-13 15:36:57 | `7515334679827598592` | — |
| 16 | 2025-06-16 15:16:41 | `7516442700360305920` | — |
| 17 | 2025-06-17 15:33:42 | `7516818145589005602` | — |
| 18 | 2025-06-18 01:05:14 | `7516965322626207028` | 深夜发布，录制时刻不能由发布时间倒推 |
| 19 | 2025-06-19 11:54:15 | `7517503805119630644` | — |
| 20 | 2025-06-19 16:24:53 | `7517573542352129332` | — |
| 21 | 2025-06-20 11:53:47 | `7517874774514945315` | — |
| 22 | 2025-06-23 11:56:25 | `7518988702334405922` | — |
| 23 | 2025-06-23 15:00:43 | `7519036191003086120` | — |
| 24 | 2025-06-24 11:51:25 | `7519358506983689506` | — |
| 25 | 2025-06-24 15:21:12 | `7519412556550475043` | batch-001 终点 |

`7515275277196889359` 已有：

- `data/yeren_research/media/empty-transcript-review-2026-08-13.json`；
- `data/yeren_research/media/7515275277196889359-0-12267.mp3`；
- `data/yeren_research/media/7515275277196889359-contact-sheet.jpg`。

现有结论是音轨没有可靠交易语音，联系图中的账户/收益截图不足以可靠读取。只有当原分辨率字段会改变系统判断时才定向补关键帧；否则以 `blocked_on_media` 或无规则证据的诚实状态收口，不把标题“慢就是快”扩写成规则。

## 6. 每条视频必须怎样分析

主 agent 必须亲自阅读完整转写，不能只看关键词附近，也不能把核心语义委派给子 agent或批量摘要模型。对每条记录依次完成：

1. 确认 transcript 最终状态，并阅读标题、发布时间、时长和完整句段；
2. 区分事实、市场状态、个股观点、消息/财报解释、已执行动作、计划、条件规则、复盘、教学和修辞；
3. 消歧证券、题材、账户、交易类型以及“今天/明天/周末”等时间指代；不能唯一确认时保留 alternatives；
4. 写明 `recording_time_interval`、`referenced_market_intervals`、`information_available_at`、`earliest_action_at` 和还原精度；
5. 只在原话实际需要时查询 PIT 行情、公告、财报或官方事件；没有真实问题时不造空 bundle；
6. 先形成 decision 解释，再打开未来行情写 outcome，禁止未来结果倒灌；
7. 写 interpretation 的依据、反证和替代解释；
8. 只有具有系统意义时才追加 hypothesis support / contradiction / exception / revision；孤立口号不升级规则；
9. 连续日期涉及同一题材、证券、账户或持仓过程时，建立“前提—介入—加减仓—变化—离场—复盘”的 case chain；
10. 保存 observation 后立即做 Pydantic 校验，不把结构错误积压到包尾。

短视频也必须有明确分析状态，但不要求每条都产生规则、bundle 或案例。非交易内容、纯修辞、不可得和空文本都可以诚实地产生零规则证据。

## 7. “预期—反馈偏差”是提取镜头，不是语料结论

owner 已明确目标系统应具备甚至超过博主的综合研判能力：在动作前，根据交易内核与真实可见的场内外信息形成可回查预期，再用指定窗口的反馈划分超预期、符合预期和不及预期；不能以非理性猜测或赌博代替预测。

阶段 C 遇到“预期”表述时，重点提取：

- 预期对象与决策截止时点；
- 次日竞价、开盘、盘中、收盘或多日的评估窗口；
- 当时真实可见的场内与场外依据；
- 基准情景和三个反馈分支；
- 每个分支的预设动作与新增暴露撤回条件；
- 该表述是事前计划还是事后命名。

但 owner 方向必须继续与博主 Base 证据分层。不能因为目标系统需要这些字段，就声称博主已经完整定义；语料缺少阈值、窗口或优先级时保持未知。

同票浮盈边界当前仍是：浮盈/浮亏本身不是触发器；只有相对动作前预期出现新的模式内证据，才有讨论新增暴露的资格，且失效后撤回新增部分。全量研究要主动寻找支持、反例和系统演化，不能只收集印证。

## 8. 数据、消息和媒体边界

- `data/marketdata_pit/`、`data/yeren_corpus/`、`data/yeren_research/` 均 append-only；严禁删改既有档案或从零重下 PIT。
- PIT 已在 2026-08-13 收盘后幂等增量更新：A 股 `daily`、`adj_factor`、`daily_basic` 及 `stk_limit`、`limit_list_d`、`suspend_d`、`forecast_vip`、`express_vip`、`report_rc` 等已到 2026-08-13。截至 16:04，Tushare 的当日 `fund_daily`、`cyq_perf`、`stk_factor_pro` 仍返回空帧，fail-closed 拒绝落盘，水位保持在 2026-08-12；当日 QGR coverage manifest 也因两个必需端点缺失而未生成。机器清单使用 `data/yeren_research/inventory/assets-2026-08-13-v1.3.json`；没有分钟/竞价证据时只做日级或方向性复原。
- 本地新闻不是严格历史 `as_of` 库。事件优先找交易所/巨潮、政府或监管原文；二手媒体只作补充。
- 明确公告只有日期而无时刻时，保守从下一交易日 09:30 起进入决策；不能猜公告盘前已知。
- 量化、游资、主力、传闻主体没有龙虎榜、订单流或原始来源时保持未知。
- 只有会改变证券、指标、仓位或动作方向的 ASR/画面歧义才触发定向媒体复核；保存必要局部，临时整段视频删除。
- 联网调查必须围绕具体语料事件，不建设无边界通用新闻湖。

本次刷新已验证 312 个新增 payload 的文件存在性、size 与 SHA256，失败为 0；索引没有重复 snapshot ID 或复合键版本。118 条新增 coverage manifest 均通过 Pydantic 校验且无重复键。全量 `ruff check backend/ scripts/` 通过，pytest 为 `7240 passed, 14 skipped`。后续只需在 Tushare 发布 8 月 13 日的 `fund_daily`、`cyq_perf`、`stk_factor_pro` 后幂等补当天，不要重新跑历史全量下载。

已复原的 8 月 2 日券商事件不要重查。当前结论是八家事件族高置信、博主所指精确子集未知，机器记录为 `data/yeren_research/events/broker-regulatory-actions-2026-07-31.json`。

## 9. Append-only 修订规则

- 首次 observation 使用 `observations/<aweme_id>.json`；修订使用 `-v1.1`、`-v1.2` 等新文件，不能覆盖旧文件。
- event 和 worklog 在既有 JSONL 末尾追加新记录，不编辑历史行。
- hypothesis 修订以新 `HypothesisRevision` 追加，不覆盖原假设。
- decision/outcome bundle 修订使用新版本文件名；案例只引用 current 版本。
- RawEvidence 中转写原句只放 `transcript_span.raw_text`，不要把可读化改写塞回 `content` 冒充原话。
- `content` 可用于外部公告、行情等非转写证据的简洁事实说明；解释仍放 interpretation。

## 10. 当前稳定基线与关键未知

稳定核心候选仍只有候选身份，不是 Base v1：

- `H-SYSTEM-PRESET-001`：开仓、仓位、卖出和预期分支成套预设；
- `H-CAPITAL-FIRST-001`：本金与回撤优先，系统/节奏失效时允许停手；
- `H-AVERAGING-DOWN-BAN-001`：不以摊低成本为理由在下跌中补第二、第三笔。

必须主动检验的开放项包括：

- 博主是否存在同票已有浮盈后因新超预期证据继续加仓的真实链；
- “推仓位”作用于单票、策略子仓还是组合总暴露；
- 短线、波段、中长线及多个账户的状态边界；
- 混沌、退潮、赚钱效应、主线、情绪拐点的观测量、优先级和演化；
- 竞价/开盘/盘中/收盘的预期反馈窗口；
- 消息、财报、资金与市场状态冲突时的优先级；
- 规则随时间演化，还是早期表达不完整。

发现反例时直接追加 contradiction / exception / revision。不要为了形成漂亮状态机压平冲突，也不要用收益高低选择解释。

## 11. Batch-001 的完成定义

完成首包时应具备：

- 第 1—25 条每条都有明确分析状态；
- 所有可用转写均由主 agent 完整阅读；
- 首次 observation 使用 25 个固定 ID 对应的文件名，已有媒体缺口按事实记录；
- 原话确实涉及行情/公告/财报的条目拥有最小必要 decision bundle；需要观察后续反馈时另建 outcome bundle；
- 发现的多日连续操作已串成 case，而不是拆成孤立金句；
- `hypotheses.jsonl` 只追加真正改变系统理解的支持、反证、例外或修订；
- `worklog.jsonl` 追加 `M2-C-batch-001`，写清实际范围、输出、验证、开放项，并把 `resume_from` 指向 `M2-C-batch-002`；
- 若 batch-001 揭示新的真实数据缺口，更新 coverage；否则不为显得完整而扩建数据源；
- 没有生产战法、状态机、回测优化或真实交易代码。

## 12. 验证与命令

Python 一律使用：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
```

单条 observation：

```bash
$PY/python -m scripts.yeren_research validate observation \
  data/yeren_research/observations/<aweme_id>.json
```

工作包完成后至少验证全部 observation、hypothesis 和 bundle：

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

hypotheses = (root / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
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
```

如果修改了 `scripts/yeren_research/` 或 schema，再运行：

```bash
$PY/ruff check scripts/yeren_research/ tests/yeren_research/
$PY/mypy --explicit-package-bases scripts/yeren_research
```

本阶段没有前端任务，不需要运行前端测试。不得通过 `FEISHU_INTERACTIVE_ENABLED=true` 触发飞书。

## 13. 每四包的阶段综合

完成 batch-004、008、012……时，对约 100 条做一次人工综合：

1. 哪些规则得到跨市场状态重复支持；
2. 哪些规则出现反证、账户层级差异或时间演化；
3. 新增了哪些真实 case chain；
4. 哪些消息/财报类别暴露新的来源缺口；
5. owner 的“预期—反馈偏差”语义在语料中有哪些真实支持与缺口；
6. 哪些问题会改变交易动作，需要向 owner 提具体问题。

更新 casebook、coverage 和 worklog，但仍不冻结参数或 Base v1。阶段 C 全部结束后才进入阶段 D 规范整理，并提交 G2。

## 14. 汇报格式

每个工作包结束时，向 owner 简洁说明：

- 本包固定序号、日期范围和 25 个视频的处理状态；
- 新增 observation、bundle、case、event、hypothesis 数量；
- 哪些规则被支持、反驳、修订或发现阶段演化；
- 哪些实体、ASR、时点或数据仍未知，以及为何会或不会改变交易动作；
- decision/outcome 是否继续隔离；
- 验证结果、当前 git 状态，以及下一 `resume_from`；
- 当前门禁必须写作“G1 已通过、阶段 C 进行中、G2 未到”，不能自行升级。

当前直接开始 `M2-C-batch-001`，不要再等待确认。
