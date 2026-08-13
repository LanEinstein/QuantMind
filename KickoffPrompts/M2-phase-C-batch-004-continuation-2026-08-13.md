# M2 接手说明：阶段 C batch-004 与首轮 100 条综合

> 日期：2026-08-13
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`
>
> 远端基线：`e8194d5 docs(research): add M2 G1 handoff`
>
> 上游阶段 C 文档：`KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`
>
> 当前恢复点：`M2-C-batch-004`
>
> 当前门禁：**G1 已通过、阶段 C 进行中、G2 未到**

## 0. 干净上下文直接执行什么

不要重新询问 G1，不要重做阶段 A 的 PIT 审计，不要重做阶段 B pilot，也不要重做已经完成的 batch-001—003。

直接完整处理按 `metadata.jsonl` 固定排序后的第 **76—100 条**：

- 主 agent 亲自阅读 25 条完整转写；
- 为尚无 observation 的 24 条写首次 observation；
- 第 98 条 `7534671965824175412` 已有 current pilot observation，仍须全文阅读以连接本包时间链，但默认复用，不复制；只有新证据改变语义时才新增 `-v1.1`；
- 只创建实际需要的 decision/outcome bundle、case、event 和 hypothesis revision；
- 每条 observation 落盘后立即做 Pydantic 校验；
- 完成 batch-004 后，执行阶段 C 首个序号 1—100 的人工综合与冲突审查；
- 追加 worklog，把 `resume_from` 指向 `M2-C-batch-005`。

不要跨包留下半成品。不得进入生产 playbook、确定性状态机、回测择优或参数优化。真实券商程序化下单永禁。

## 1. 当前真实状态

`M2-C-batch-001`、`002`、`003` 均已完成。最新工作日志是：

```text
work_unit=M2-C-batch-003
status=completed
resume_from=M2-C-batch-004
gate=G1 已通过、阶段 C 进行中、G2 未到
```

当前本地研究区基线：

- observation 工件：87；
- 唯一 observation 视频：85；
- hypothesis revision：43；
- decision/outcome bundle：54；
- batch-003 新增 25 个 observation 工件，其中 24 个新视频、1 个 append-only `v1.1`；
- batch-003 新增 6 组 decision/outcome、2 条 case、8 条 hypothesis revision；
- 全库 Pydantic 通过；
- batch-003 的 58 个 transcript span 与源转写逐字、逐时间戳核验通过；
- `tests/yeren_research`：20 passed；
- `git diff --check`：passed。

最小核对：

```bash
test -f data/yeren_research/worklog.jsonl
test -f data/yeren_research/hypotheses.jsonl
test -f data/yeren_research/observations/7526939325557165347-v1.1.json
test -f data/yeren_research/observations/7534671965824175412.json
test -f data/yeren_research/cases/batch003-yajiang-entry-window-and-reseal-2025-07-19-to-2025-07-24.json
find data/yeren_research/observations -maxdepth 1 -type f | wc -l
wc -l data/yeren_research/hypotheses.jsonl
find data/yeren_research/decision_bundles data/yeren_research/outcome_bundles \
  -maxdepth 1 -type f -name '*.json' | wc -l
tail -n 1 data/yeren_research/worklog.jsonl | jq .
```

预期计数依次为 87、43、54；worklog 最后一条为 `M2-C-batch-003`。

## 2. 开始前必须完整读取

按以下顺序完整读取，不能只看摘要：

1. `AGENTS.md`；
2. `CLAUDE.md`；
3. 本文件；
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`；
5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`；
6. `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`；
7. `docs/research/yeren-system/research-methodology.md`；
8. `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
9. `docs/research/yeren-system/data-and-source-coverage.md`；
10. `docs/research/yeren-system/casebook.md`；
11. `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
12. `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`，只用于 pilot 细节，不用它覆盖本文件的进度。

代码发现继续遵守仓库规则：项目图谱为 `home-ps-papers-QuantMind`，优先使用 codebase-memory MCP 的 `search_graph`、`trace_path`、`get_code_snippet`、`query_graph`；字符串、JSON/JSONL、Markdown 和其他非代码文件才使用 `rg`、`jq`、`sed`。

## 3. 工作区保护

先执行：

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
```

当前 tracked/untracked 工作区包含 owner 和前序任务的改动。必须全部保留，不要用 `checkout`、`reset` 或批量格式化覆盖：

- `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`；
- `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`；
- `backend/marketdata_snapshot/coverage.py`；
- `backend/marketdata_snapshot/store.py`；
- `docs/research/yeren-system/casebook.md`；
- `docs/research/yeren-system/data-and-source-coverage.md`；
- `docs/research/yeren-system/g1-pilot-review-2026-08-13.md`；
- `docs/research/yeren-system/research-methodology.md`；
- `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`；
- `scripts/factor_research/ingest_round2_data.py`；
- `tests/marketdata_snapshot/test_coverage.py`；
- `tests/marketdata_snapshot/test_snapshot_store.py`；
- 本文件。

`data/marketdata_pit/`、`data/yeren_corpus/`、`data/yeren_research/` 都按 append-only 处理。不得删除或覆盖既有档案。研究区被 Git 忽略，所以 `git status` 看不到其中新增产物；不能据此误判数据不存在。

不 commit，不 push；除非 owner 之后明确授权。

## 4. 固定工作包边界

边界固定在 `metadata.jsonl` 按 `[published_at, aweme_id]` 升序后的第 76—100 条。不得先排除已有 pilot、空文本或不可得记录再凑 25 条。

| 序号 | 发布时间 | 视频 ID | 标题摘要 |
|---:|---|---|---|
| 76 | 2025-07-24 20:12:00 | `7530589466420940032` | 股市/交易复盘 |
| 77 | 2025-07-25 11:31:21 | `7530856986536938752` | 午间实盘 |
| 78 | 2025-07-25 17:26:39 | `7530948542933323023` | 收盘复盘 |
| 79 | 2025-07-27 09:49:40 | `7531572943040073000` | 周末内容 |
| 80 | 2025-07-27 15:35:00 | `7531632564613369128` | 周末复盘 |
| 81 | 2025-07-28 11:39:48 | `7531972413706341666` | 午间实盘 |
| 82 | 2025-07-28 17:31:06 | `7532062944509021500` | 收盘实盘 |
| 83 | 2025-07-28 20:09:00 | `7532072664493477178` | 晚间实盘 |
| 84 | 2025-07-29 11:34:33 | `7532342136966188328` | 午间实盘 |
| 85 | 2025-07-29 17:35:47 | `7532435238183652648` | 收盘实盘 |
| 86 | 2025-07-29 17:58:23 | `7532441065262566696` | 交易方法 |
| 87 | 2025-07-30 15:16:33 | `7532770445059034408` | 收盘实盘 |
| 88 | 2025-07-30 18:16:23 | `7532816775504645411` | 晚间复盘 |
| 89 | 2025-07-30 18:35:25 | `7532821683998493992` | 交易方法 |
| 90 | 2025-07-31 15:23:08 | `7533143223889415459` | 收盘复盘 |
| 91 | 2025-07-31 17:49:30 | `7533180932946857256` | 交易心得 |
| 92 | 2025-08-01 11:49:21 | `7533459198417784099` | 午间实盘 |
| 93 | 2025-08-01 15:30:14 | `7533516117052411188` | 收盘实盘 |
| 94 | 2025-08-03 19:03:56 | `7534313382038277411` | 周末复盘 |
| 95 | 2025-08-04 09:46:10 | `7534540719106444578` | 盘中实盘 |
| 96 | 2025-08-04 11:52:18 | `7534573233933782287` | 午间实盘 |
| 97 | 2025-08-04 16:04:13 | `7534638156487085327` | 收盘复盘 |
| 98 | 2025-08-04 18:15:27 | `7534671965824175412` | 已有 pilot current observation |
| 99 | 2025-08-05 11:35:50 | `7534940070185962787` | 午间实盘 |
| 100 | 2025-08-05 15:37:27 | `7535002316338318632` | 收盘实盘 |

只读重算命令：

```bash
jq -s 'sort_by([.published_at, .aweme_id]) | .[75:100] |
  to_entries | map({ordinal:(.key + 76), aweme_id:.value.aweme_id,
                    published_at:.value.published_at, title:.value.title,
                    duration_ms:.value.duration_ms})' \
  data/yeren_corpus/metadata.jsonl
```

首条应为 `7530589466420940032`，末条应为 `7535002316338318632`，数量必须是 25。

## 5. 必须复用的既有产物

第 98 条已经完成 pilot 分析：

- current observation：`data/yeren_research/observations/7534671965824175412.json`；
- 已有局部音频：`data/yeren_research/media/7534671965824175412-109000-133500.mp3`；
- pilot case：`data/yeren_research/cases/pilot-yajiang-2025-07-29-to-2025-08-04.json`；
- decision bundle：`data/yeren_research/decision_bundles/pilot-yajiang-2025-08-04.json`；
- outcome bundle：`data/yeren_research/outcome_bundles/pilot-yajiang-2025-08-04.json`；
- 官方事件：`data/yeren_research/events/benchmark-events-2026-08-13.jsonl#event-yajiang-hydropower-2025-07-19`。

不要重复创建这些文件，不要重新做无边界媒体下载。若 batch-004 的连续语料改变第 98 条语义，只新增 `7534671965824175412-v1.1.json`；否则 worklog 标记 `already_analyzed/reused`。

已有 pilot 的三项关键歧义仍未解决：

- “左手龙一右手龙二”是否分别指山河智能和西藏天路；
- 句 45 是“推仓位”还是“退仓位”；
- “确定性买点”和“不及预期”的可观察条件。

只有本包上下文或已有媒体能实质改变证券、仓位或动作方向时才消歧；否则继续保留 alternatives。

batch-003 的 current 连续案例必须先读：

- `data/yeren_research/cases/batch003-chaos-retreat-and-account-separation-2025-07-10-to-2025-07-18.json`；
- `data/yeren_research/cases/batch003-yajiang-entry-window-and-reseal-2025-07-19-to-2025-07-24.json`。

后者是 batch-004 的直接前情，不得把 7 月 25 日以后结果倒灌成 7 月 24 日回封判断的先验依据。

## 6. 当前规则基线与 batch-004 检验重点

最新 hypothesis 追加在 `data/yeren_research/hypotheses.jsonl` 末尾，重点包括：

- `H-MARKET-CHAOS-RETREAT-001-R4`：混沌关闭新入口但不自动清存量仓；确认退潮后才止损；约三天不是机械计时器；
- `H-TRADING-HORIZON-LOCK-001-R2`：账户与交易类型同时锁定，公开小账户不能代表完整组合；
- `H-SELECTION-HARD-LOGIC-001-R2`：硬逻辑只生成候选，真实反馈可以否决；
- `H-THEME-CONTINUATION-001`：题材首日和次日分歧使用不同输入；
- `H-FIRST-ENTRY-WINDOW-001`：首日错过后等待分歧，但入口会因连续一致上涨而过期；
- `H-MARKET-LIQUIDITY-PRIORITY-001-R3`：总量、题材吸收规模与机构/游资风格共同约束容量，口语阈值不永久化；
- `H-MICROSTRUCTURE-RELAY-001-R2`：前日烂板—次日弱转强反包—板上再分歧—回封只是待证券级验证的 special case；
- `H-POSITION-CONVICTION-001-R4`：同票加仓、同题材新证券和组合新增题材必须分开；“重拳”不等于固定仓位。

batch-004 要主动寻找支持、反例、例外和时间演化，尤其关注：

1. 7 月 24 日回封后的次日反馈是否在原预期窗口内达标，不能用全市场结果替代目标证券；
2. 雅江题材从首次分歧、换手核心到后续持有/退出的完整动作链；
3. “推仓位”究竟作用于单票、同题材子仓还是组合总暴露；
4. 大、小账户与短线、波段的对应关系是否获得新证据；
5. 不及预期、弱转强、回封、核心、中军、充分换手是否出现更明确的观测定义；
6. 规则是得到重复支持，还是只适用于 2025 年 7—8 月的特定题材阶段。

不要为了形成漂亮状态机压平冲突，不要用后续收益选择解释。

## 7. 每条视频的工作方法

对 25 条记录逐条完成：

1. 读取 metadata、ledger 最终状态、标题、发布时间、时长和完整转写；
2. 区分事实、市场状态、证券观点、消息/财报解释、已执行动作、计划、条件规则、复盘、教学和修辞；
3. 消歧证券、题材、账户、交易类型及今天/明天/周末；无法唯一确认就记录 alternatives 与交易后果；
4. 写清 `recording_time_interval`、`referenced_market_intervals`、`information_available_at`、`earliest_action_at` 和还原精度；
5. transcript 原文只能放 `transcript_span.raw_text`，必须与源句段逐字匹配；不要在 transcript evidence 中写 `content`；
6. 先在 cutoff 下完成 decision 解释，再打开未来行情写 outcome，二者物理分文件；
7. 只有原话实际提出行情、事件、公告或财报问题时才建最小 bundle；短视频、修辞和非交易内容可以零 bundle、零规则；
8. interpretation 写依据、强度、反证和替代解释；
9. 只有改变系统理解时才追加 hypothesis support/refute/revision；孤立口号不升级；
10. 连续日期涉及同一题材、证券或持仓时建 case chain，不拆成孤立金句；
11. observation 保存后立即执行 schema 校验。

不得用子 agent 或批量摘要模型代替主 agent 阅读完整转写。

## 8. 数据、消息、时间和媒体边界

- PIT 行情、公告、财报和事件按当时真实可见时间截断；
- cutoff 后记录只能进入 outcome bundle；
- 公告只有日期无时刻时，保守从下一交易日 09:30 可用；
- 没有历史分钟、竞价、封单或炸板路径时，只做日级或方向性复原；
- “量化、游资、主力、埋伏盘、资金干净、洗盘”没有席位、持仓或订单流时只能作为口播解释；
- 本地新闻不是严格历史 `as_of` 库；具体事件优先交易所、巨潮、政府、监管和公司原文；
- 雅江 7 月 19 日官方事件已经存在，除非出现新的具体事实问题，不要重复联网调查；
- 只有会改变证券、指标、仓位或动作方向的 ASR/画面歧义才做定向媒体复核；已有局部优先复用；
- 严禁从零重下 `data/marketdata_pit/`，严禁删除 corpus 或研究产物。

## 9. batch-004 完成后的首轮 100 条综合

完成序号 76—100 后，再基于 batch-001—004 已完成的 observation、case、hypothesis 和 worklog 做一次人工综合。不要重做前 75 条逐视频分析；只有解决明确冲突时才回看原转写。

综合必须回答：

1. 哪些规则得到跨市场状态、跨题材或跨账户的重复支持；
2. 哪些规则出现反证、例外、账户层级差异或时间演化；
3. 已形成哪些完整的前提—介入—加减仓—变化—离场—复盘 case；
4. “预期—反馈偏差”在前 100 条中有哪些真实支持，哪些关键窗口和阈值仍缺失；
5. 新增了哪些真实数据/来源缺口，哪些只是已有缺口的重复；
6. 是否有会实质改变交易动作且证据无法解决的问题需要向 owner 提一个具体问题。

把综合结论写入 `docs/research/yeren-system/casebook.md` 和 batch-004 worklog。只有出现新的来源类别或新的真实缺口时才更新 `data-and-source-coverage.md`；不要为显得完整而扩建数据源或机械列评分表。

综合仍不冻结 Base v1，不确定参数，不进入阶段 D，不提交 G2。

## 10. 完成定义

`M2-C-batch-004` 完成时必须具备：

- 固定序号 76—100 的 25 条记录均有明确分析状态；
- 25 条可用转写均由主 agent 全文读取；
- 24 条无 observation 的视频拥有首次 observation；第 98 条明确记录复用或有证据的版本修订；
- 必要 decision/outcome bundle 保持未来隔离；
- 连续操作串成最小必要 case；
- hypothesis 只追加系统级变化；
- 完成序号 1—100 的人工综合与冲突审查，并更新 casebook；
- `worklog.jsonl` 追加 `M2-C-batch-004`，写清固定边界、复用项、输出、综合、验证和开放项；
- `resume_from=M2-C-batch-005`；
- 门禁仍精确写作“G1 已通过、阶段 C 进行中、G2 未到”；
- 没有生产战法、回测优化、真实下单代码、commit 或 push。

## 11. 验证

Python 一律使用：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
```

单条 observation：

```bash
$PY/python -m scripts.yeren_research validate observation \
  data/yeren_research/observations/<aweme_id>.json
```

完成后验证全库：

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
git status --short
```

此外必须做只读完整性检查：

- 本包每个 transcript span 的句段范围、`start_ms`、`end_ms`、`raw_text` 与源转写完全一致；
- statement/interpretation/rule link 引用无悬空；
- hypothesis 的 `revision_of`、observation fragment 和 case 引用存在；
- decision records `information_available_at <= decision_cutoff`；
- outcome records `information_available_at > decision_cutoff`；
- case 引用的 observation 和 bundle 文件存在；
- worklog 每行都是合法 JSON。

本阶段没有前端任务，不运行前端构建。测试必须保持 `FEISHU_INTERACTIVE_ENABLED=false`。

## 12. 汇报格式

完成后向 owner 简洁汇报：

- 固定序号 76—100、日期范围和 25 条处理状态；
- 新增与复用的 observation、bundle、case、event、hypothesis 数量；
- 哪些规则得到支持、反证、修订或出现时间/账户演化；
- 前 100 条综合结论；
- 哪些实体、ASR、时点和数据仍未知，以及是否改变交易动作；
- decision/outcome 隔离、schema、引用、span、pytest 和 git 检查结果；
- 当前工作区未提交、未推送；
- `resume_from=M2-C-batch-005`；
- 当前门禁：**G1 已通过、阶段 C 进行中、G2 未到**。

现在直接开始 `M2-C-batch-004`，不要再等待确认。
