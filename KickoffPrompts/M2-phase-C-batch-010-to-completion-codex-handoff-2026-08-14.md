# M2 阶段 C 连续执行接手说明：batch-010 至全部语料完成

> 日期：2026-08-14
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`
>
> 本文件生成前远端基线：`b6a24cc feat(research): advance M2 phase C evidence work`
>
> 唯一恢复点：**`M2-C-batch-010`（固定序号 226—250）**
>
> 当前门禁：**G1 已通过、阶段 C 进行中、G2 未到**
>
> 总目标：从 batch-010 连续自动执行到 batch-044，最终使 1,088 条 metadata 记录全部具有明确分析终态，并完成全语料综合；批次之间不等待 owner 再授权。

## 0. 接手后直接执行

这是一个持续到终点的执行任务，不是只做 batch-010 的单批任务。

1. 按第 2 节完整读取必读材料，核对分支、HEAD、dirty worktree 和最新 worklog。
2. 从 metadata 的 `[published_at, aweme_id]` 升序固定序号 226 开始，由主 agent 亲自阅读全文并完成 batch-010。
3. 每完成一个 25 条工作包，写齐 observation、必要的 bundle/event/case/hypothesis、追加 worklog 并验证。
4. checkpoint 通过后立即开始下一批，不询问“是否继续”，直到 batch-044 和最终综合全部完成。
5. 每到一个整百条边界，追加一次人工综合与冲突审查；不能用批量摘要代替逐条分析。
6. 上下文压缩或自动续跑时，以 `data/yeren_research/worklog.jsonl` 最后一条已完成记录恢复，不重做已经完成的批次。
7. 不允许在一个批次中间主动结束。若遇到单条不可得、实体未知或 ASR 无法消歧，按证据边界记录终态并继续，不让局部未知阻塞其他记录。

若环境提供持久目标或自动续跑机制，应把“完成 M2-C-batch-010 至 batch-044、最终全语料综合和终检”作为一个持续目标；不设置虚构 token 预算，不因上下文切换把它误判为新任务。

唯一底线：**永禁真实券商程序化下单**。G2 前不创建生产战法、不实现确定性状态机、不进入收益择优或回测优化。

## 1. 当前真实基线

以最新 worklog 为机器真相。本文件生成时的已验证基线为：

- metadata 共 1,088 条；固定序号 **1—225 已完成人工分析**；
- observation 工件 236 份、唯一视频 233 个；数量多于 225 是因为阶段 B pilot 位于未来序号，且有 append-only 修订版；
- hypothesis revision 84 条；decision/outcome evidence bundle 94 份（47 对）；case 22 个；
- batch-009 已完成 25/25 条全文阅读，其中 24 条首次 observation，ordinal 216 复用 pilot 并追加 source-exact `v1.1`；
- batch-009 当前版本的 52/52 个 transcript span 与源 `sentences` 逐字逐时戳一致；
- `tests/yeren_research` 为 20 passed；全库研究工件 Pydantic、引用和 cutoff 分区检查通过；
- 最新 worklog：`work_unit=M2-C-batch-009`、`status=completed`、`resume_from=M2-C-batch-010`；
- 跟踪文档已推送至 draft PR #1；`data/yeren_research/` 仍按设计被 Git 忽略、只存在本地 append-only 研究区。不要因 `git status` 看不到它而误判产物不存在。

batch-009 新增的关键当前版本：

- `H-EXPECTATION-FEEDBACK-001-R3`：事件结果、开盘反馈、证券反馈分层；市场判断正确不外推组合表达正确；
- `H-EUPHORIA-EXIT-001-R3`：一致预期事件的过度高开/早盘追逐是先手兑现候选，不是固定阈值；
- `H-WEAK-MARKET-CORE-001-R6`：普通分化可去弱看核心，退潮冰点时核心规则禁用，冰点后弱修复才恢复有限试错；
- `H-PROFIT-LOCK-WITHDRAWAL-001-R6`：不可读、难赚钱且控制不住出手时，提款可作为减少可交易本金的行为摩擦；
- 仍在使用的关键版本还包括 `H-CAPITAL-FIRST-001-R4`、`H-MARKET-STATE-INPUTS-001-R7`、`H-TRADING-HORIZON-LOCK-001-R4`、`H-THEME-CONTINUATION-001-R4`、`H-SYSTEM-PRESET-001-R3`。

最新案例重点是：

- 汽车零部件关税夜间基准→次日上午纠错→下午卖出换低位方向；
- FOMC 结果分支→开盘反馈→炸板弱反馈退出换强；
- 放量退潮→冰点禁用切核心→弱修复试错→再度破碎→节前减仓提款。

## 2. 必须完整读取的材料

干净上下文首次接手时按顺序全文读取，不能只看摘要：

1. `AGENTS.md`
2. `CLAUDE.md`
3. 本文件
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`
5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`
6. `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`
7. `KickoffPrompts/M2-phase-C-claude-fable-long-context-handoff-2026-08-13.md`
8. `KickoffPrompts/M2-phase-C-batch-008-codex-handoff-2026-08-13.md`
9. `docs/research/yeren-system/research-methodology.md`
10. `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`（owner 设计方向，不得冒充博主证据）
11. `docs/research/yeren-system/data-and-source-coverage.md`
12. `docs/research/yeren-system/casebook.md`
13. `data/yeren_research/worklog.jsonl` 全部记录，最后一条必须是 batch-009 完成记录；
14. `data/yeren_research/hypotheses.jsonl` 全部记录，理解 revision 链后再追加；
15. `data/yeren_research/cases/` 全部 case JSON，至少精读 batch-008/009 的 5 条连续案例；
16. `scripts/yeren_research/schema.py`，理解 `VideoObservation`、`EvidenceBundle`、`HypothesisRevision` 的校验边界；
17. `data/yeren_research/events/section-232-auto-parts-inclusions-process-2025-09-16.json`；
18. `data/yeren_research/events/fomc-rate-cut-2025-09-17.json`；
19. 当前批次每条 `data/yeren_corpus/transcripts/<aweme_id>.json` 的完整 `text` 与全部 `sentences`。

代码发现遵守仓库规则，优先 codebase-memory graph 工具；JSON/JSONL、Markdown、转写和固定字符串使用 `jq`/`rg`。不得让子 agent、批量摘要程序或关键词切片替代主 agent 的逐条全文阅读与判断。

## 3. 工作区和数据保护

每次恢复先执行：

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
tail -1 data/yeren_research/worklog.jsonl | jq .
```

规则：

- 分支应为 `agent/m2-evidence-reconstruction`；不要 reset、checkout 或覆盖 owner 的其他改动；
- `data/marketdata_pit/`、`data/yeren_corpus/`、`data/yeren_research/` 均为 append-only；严禁删除、覆盖、从零重下；
- observation 已存在时复用；需要纠正时新增 `<aweme_id>-v1.1.json`、`v1.2` 等，不改旧件；
- hypothesis 和 worklog 只追加；revision 的 `revision_of` 必须指向该假设当前最新版本；
- `data/yeren_research/` 被 Git 忽略是既定设计，不能用 `git add -f` 擅自改变；
- commit 只在 owner 明示时进行；push 必须有 owner 明示授权，严禁 `--no-verify`；
- API key、token 和飞书秘密不得写入仓库、研究工件、测试或 prompt。

## 4. batch-010 固定边界

必须先自行重算，不要只相信表格：

```bash
jq -s 'sort_by([.published_at, .aweme_id]) | .[225:250] |
  to_entries | map({ordinal:(.key + 226), aweme_id:.value.aweme_id,
                    published_at:.value.published_at,
                    duration_ms:.value.duration_ms})' \
  data/yeren_corpus/metadata.jsonl
```

本文件生成时的预期结果如下。25 条 ledger 均为 `done`、转写均非空、均无既有 observation，因此 batch-010 预期新增 25 条首次 observation。

| 序号 | 发布时间 | 视频 ID | 时长 ms |
|---:|---|---|---:|
| 226 | 2025-09-24 11:40:42 | `7553495587510291727` | 29047 |
| 227 | 2025-09-24 12:19:01 | `7553505455348239631` | 178000 |
| 228 | 2025-09-24 15:36:17 | `7553556292632218932` | 180034 |
| 229 | 2025-09-24 17:50:00 | `7553590745221205288` | 180070 |
| 230 | 2025-09-25 11:58:34 | `7553871276931648803` | 62934 |
| 231 | 2025-09-25 12:12:47 | `7553874939661765940` | 64500 |
| 232 | 2025-09-25 15:59:47 | `7553933426521427200` | 180100 |
| 233 | 2025-09-25 23:41:19 | `7554052378891160867` | 180038 |
| 234 | 2025-09-26 11:10:10 | `7554229872060992802` | 28420 |
| 235 | 2025-09-26 12:01:53 | `7554243212165369123` | 180067 |
| 236 | 2025-09-26 12:39:15 | `7554252840907820322` | 91800 |
| 237 | 2025-09-26 15:28:10 | `7554296369827368227` | 178834 |
| 238 | 2025-09-26 20:20:00 | `7554319783018335522` | 180034 |
| 239 | 2025-09-27 14:28:57 | `7554652187809451279` | 180034 |
| 240 | 2025-09-27 19:45:57 | `7554733886778936576` | 180067 |
| 241 | 2025-09-27 20:11:26 | `7554740444888108303` | 174634 |
| 242 | 2025-09-28 18:28:00 | `7554775884928290082` | 180067 |
| 243 | 2025-09-28 22:16:26 | `7555143730056776960` | 180067 |
| 244 | 2025-09-29 11:42:56 | `7555351583400512768` | 102400 |
| 245 | 2025-09-29 12:02:00 | `7555356485234216226` | 158434 |
| 246 | 2025-09-29 18:56:13 | `7555463245357042984` | 180030 |
| 247 | 2025-09-29 20:14:40 | `7555483462703582499` | 180067 |
| 248 | 2025-09-30 11:48:15 | `7555724035201109288` | 26267 |
| 249 | 2025-09-30 15:06:37 | `7555775157483736320` | 15510 |
| 250 | 2025-09-30 18:51:19 | `7555833063575670031` | 16267 |

## 5. 后续批次总表和自动续跑规则

固定公式：batch-N 的常规范围为 `25*(N-1)+1` 至 `25*N`；最后一批不足 25 条。每批开始仍须从 metadata 重算，若语料发生 append，不改变既有 1—1,088 固定研究范围，除非 owner 明确扩展目标。

| 批次 | 固定序号 | 批后附加任务 |
|---|---:|---|
| batch-010 | 226—250 | 继续 |
| batch-011 | 251—275 | 继续 |
| batch-012 | 276—300 | 第三个百条（201—300）人工综合 |
| batch-013 | 301—325 | 继续 |
| batch-014 | 326—350 | 继续 |
| batch-015 | 351—375 | 继续 |
| batch-016 | 376—400 | 第四个百条（301—400）人工综合 |
| batch-017 | 401—425 | 继续 |
| batch-018 | 426—450 | 继续 |
| batch-019 | 451—475 | 继续 |
| batch-020 | 476—500 | 第五个百条（401—500）人工综合 |
| batch-021 | 501—525 | 继续 |
| batch-022 | 526—550 | 继续 |
| batch-023 | 551—575 | 继续 |
| batch-024 | 576—600 | 第六个百条（501—600）人工综合 |
| batch-025 | 601—625 | 继续 |
| batch-026 | 626—650 | 继续 |
| batch-027 | 651—675 | 继续 |
| batch-028 | 676—700 | 第七个百条（601—700）人工综合 |
| batch-029 | 701—725 | 继续 |
| batch-030 | 726—750 | 继续 |
| batch-031 | 751—775 | 继续 |
| batch-032 | 776—800 | 第八个百条（701—800）人工综合 |
| batch-033 | 801—825 | 继续 |
| batch-034 | 826—850 | 继续 |
| batch-035 | 851—875 | 继续 |
| batch-036 | 876—900 | 第九个百条（801—900）人工综合 |
| batch-037 | 901—925 | 继续 |
| batch-038 | 926—950 | 继续 |
| batch-039 | 951—975 | 继续 |
| batch-040 | 976—1000 | 第十个百条（901—1000）人工综合 |
| batch-041 | 1001—1025 | 继续 |
| batch-042 | 1026—1050 | 继续 |
| batch-043 | 1051—1075 | 继续 |
| batch-044 | 1076—1088 | 最后 88 条（1001—1088）综合、全语料冲突审查和终检 |

每批完成记录的 `resume_from` 必须指向下一批。batch-044 完成后写 `resume_from=M2-C-complete-awaiting-G2-owner-review`，只宣布“可提交 G2 owner 审查”，不得自行宣告 G2 通过。

## 6. 未来序号中的既有 pilot

阶段 B 已为下列未来序号建立 observation。轮到这些序号时必须完整重读连续上下文并复用当前版本；只有证据、span 或语义需要纠正时才追加版本，不能覆盖或重复创建首次 observation。

| 固定序号 | 所属批次 | 视频 ID | 当前文件 |
|---:|---|---|---|
| 298 | batch-012 | `7562170397225258280` | `7562170397225258280.json` |
| 344 | batch-014 | `7566602982977376372` | `7566602982977376372.json` |
| 636 | batch-026 | `7602233626348496192` | `7602233626348496192.json` |
| 805 | batch-033 | `7626700051746734178` | `7626700051746734178.json` |
| 910 | batch-037 | `7647445389893913039` | `7647445389893913039.json` |
| 969 | batch-039 | `7658346105893142863` | `7658346105893142863.json` |
| 975 | batch-039 | `7660119917865709391` | `7660119917865709391.json` |
| 1049 | batch-042 | `7669063381873462208` | 当前版 `7669063381873462208-v1.1.json`，旧版保留 |

ordinal 1083（`7672964034287880290`，2026-08-12 10:19:15）ledger 终态为 `unavailable`，原因是作品删除、隐藏或不可见。batch-044 到达时保留不可得事实和来源错误，不伪造转写；按当时 schema/方法学记录明确分析终态，并继续完成余下记录。

## 7. 每条视频的固定工作方法

对当前 25 条依发布时间顺序逐条处理：

1. 读 metadata、标题、发布时间、时长、ledger，再读完整 transcript `text` 和全部 `sentences`；
2. 区分可验证事实、市场状态、证券观点、消息/财报解释、已执行动作、计划动作、条件规则、复盘、教学、修辞；
3. 分离市场、题材、证券、账户和交易周期；同一视频的相反动作优先检查是否属于不同账户、持仓位置或窗口，不为消除矛盾而猜；
4. 所有 statement 只写原话支持的内容；interpretation 单独写依据、强度、替代解释和交易后果；
5. 证券、题材、账户、仓位、成本或时间指代不能唯一确认时，写 alternatives、证据与后果，不借行业代表股补造实体；
6. 记录 `recording_time_interval`、`referenced_market_intervals`、`information_available_at`、`earliest_action_at` 和 `reconstruction_precision`；
7. 午间发布通常从 13:00 可行动；收盘、夜间、周末从下一 A 股交易日 09:30 可行动；盘中已发布内容可从发布时间起进入后续动作；
8. `transcript_span.raw_text` 必须等于源 `sentences[a:b+1].text` 无分隔拼接，`start_ms/end_ms` 取首末句原时戳；不得手工整理标点；
9. transcript evidence 的 `content` 必须为 null；ASR 修订只放 `asr_revision`，同时给 `revision_basis`，原 `raw_text` 永远保留；
10. 高频同音词只有在连续语境足够且不虚构证券/仓位时才能语境修订；会改变动作方向且两套 ASR 无法解决时，才做定向音画复核；
11. 每条 observation 写入后立即用 `VideoObservation` 校验，不积压到第 25 条；
12. 重复教学、修辞和无验证问题的视频允许零 bundle、零 hypothesis；不得为表格完整制造行情查询或规则。

临时 observation 生成器放在 `/tmp/quantmind-batchNNN/`，通过读取源 transcript 自动复制 span；写入函数必须拒绝覆盖已有目标。不要把临时生成器提交到仓库。

## 8. 预期、事件和 PIT 隔离

所有需要结果检验的链条严格执行：

1. 在 cutoff 下写清对象、方向、预期、条件分支和最早反馈窗口；
2. 只查询原话实际需要的行情、公告、财报或官方事件；
3. 先生成 decision bundle，再读取 cutoff 后结果并生成 outcome bundle；
4. decision 记录必须满足 `information_available_at <= decision_cutoff`；outcome 必须严格晚于 cutoff；
5. 日线只在当日 15:00 可见，不从当日高低价倒推盘中成交；
6. 未识别证券时，全市场广度只能验证市场环境，不能证明作者持仓收益；
7. 官方事件优先原始监管、交易所、公司公告或政府文件；技术问题只依赖官方文档/源码；联网出站遵守 IPv4-only；
8. 新闻标题、作者因果、参与者身份和官方事实分别记录，不把“主力出货”“机构砸盘”等叙事当已验证事实；
9. 新信息只允许更新其真实到达后的未来分支，不得倒灌成更早动作依据。

行情 bundle 命令模板：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/python -m scripts.yeren_research bundle-market \
  --case-id <case-id> \
  --video-id <aweme-id> \
  --decision-cutoff <ISO-8601+08:00> \
  --start-date <YYYYMMDD> --end-date <YYYYMMDD> \
  --endpoint daily --endpoint limit_list_d
```

只在证券实体可靠时增加 `--code`；没有分钟/竞价数据时明确保留日级精度。

## 9. hypothesis、case 与百条综合

Hypothesis：

- 只有新证据改变系统理解、条件、例外或反证时才追加；孤立口号不升级；
- 新规则先为 candidate，跨阶段重复、反证和边界足够后才考虑 stable_core；
- revision 必须从该假设最新 ID 延伸，不能从过期版本分叉；
- `supporting_refs`、`counterevidence_refs`、case/event 引用必须真实存在；
- owner 的“预期—反馈偏差”是目标系统方向，只能指导提取问题，不可列为博主 supporting evidence。

Case：

- 跨日连续动作建立最小 `state_action_chain`；
- 每步 `published_at` 与 observation/metadata 完全一致；
- decision/outcome bundle、官方 event、当前发现、反例、未知和 outcome 边界必须分别列出；
- 不把多个互不相关的证券或账户硬拼为一条成功故事。

每到 300、400、500、600、700、800、900、1000 和最终 1088，在 `docs/research/yeren-system/casebook.md` 追加人工综合，至少回答：

- 哪些规则获得跨阶段重复支持；
- 哪些只适用于短线/波段、套利/趋势、小/大账户或特定市场状态；
- 哪些被后续动作反驳、出现口径漂移或可能只是事后解释；
- 预期窗口、状态输入、仓位和退出的当前最窄可执行语义；
- 哪些数据缺口仍阻止证券级、分钟级或账户级复原；
- 是否出现新的来源类别，还是既有 coverage 缺口的具体实例。

综合只能在逐条 observation 与案例完成后做；不能用后续结果倒改早期 decision。

## 10. 每批 checkpoint

每 25 条完成后必须执行：

1. 当前批 25 条（含复用 pilot 的当前版本）全部 `VideoObservation` Pydantic 通过；
2. 当前批每个 transcript span 的 raw text、起止时戳与源 sentences 精确一致；
3. 全部 hypothesis JSONL 通过 `HypothesisRevision`，revision_of 存在且指向正确当前链；
4. 全部 decision/outcome bundle 通过 `EvidenceBundle`，cutoff 分区正确；
5. 新 observation rule links、新 hypothesis refs、新 case/event/bundle 路径全部存在；
6. case JSON、event JSON 和整个 worklog JSONL 可逐行解析；
7. `FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research`；
8. 若改动 Python，再跑对应 pytest、ruff 和必要的前端检查；
9. `git diff --check`；
10. 追加一条 `status=completed` worklog，包含 fixed ordinals、全文阅读数、新增/复用/修订数、bundle 对、case、event、hypothesis、验证计数、gate、下一恢复点和 open items。

全库 Pydantic 模板：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/python - <<'PY'
from pathlib import Path
from scripts.yeren_research.schema import EvidenceBundle, HypothesisRevision, VideoObservation

root = Path("data/yeren_research")
observations = sorted((root / "observations").glob("*.json"))
for path in observations:
    VideoObservation.model_validate_json(path.read_text(encoding="utf-8"))
hypotheses = [
    line for line in (root / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines()
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
```

验证通过后立即开始下一批。若某个检查失败，修复本批引入的问题并重跑；历史主线外问题按 P3 记录，不在当前批无限扩修。

## 11. 最终完成定义

batch-044 后只有同时满足以下条件，才可把阶段 C 标记为完成：

1. metadata 固定序号 1—1,088 均有明确终态：可得转写全部由主 agent 全文分析；不可得记录保留真实不可得原因；
2. 所有已存在 observation 的当前版本通过 schema，所有 transcript span 精确匹配源；
3. 所有 hypothesis revision 链、rule links、case/event refs 和 bundle 路径完整；
4. 所有 decision/outcome cutoff 物理隔离，无未来结果倒灌；
5. casebook 已包含每个百条阶段综合、最后 1001—1088 综合以及一次全语料冲突审查；
6. 全语料综合明确 stable core、candidate、反例、账户/周期边界、不可复原项和 coverage 缺口，不把未知填成参数；
7. `tests/yeren_research`、必要的相关测试、Pydantic、JSONL、span、引用、cutoff 和 `git diff --check` 全部通过；
8. worklog 追加 `M2-C-batch-044 completed` 和一条最终全量验证记录，`resume_from=M2-C-complete-awaiting-G2-owner-review`；
9. 向 owner 报告“阶段 C 证据研究完成、可进行 G2 审查”，但不自行通过 G2，也不实现生产交易系统；
10. 全程没有真实券商程序化下单代码、调用或授权扩大。

## 12. 当前开放项与 batch-010 优先线索

以下是待后续语料主动核对的线索，不是必须在 batch-010 一次解决的阻塞项：

- 汽车零部件、炸板换强、CPU/机器人和税改方向的证券实体、账户、仓位与成交；
- 9 月 18—24 日开盘、午后跳水、修复/轮动的分钟、竞价、题材成交与订单流；
- 大小账户与短线/波段映射，节前提款金额和实际覆盖范围；
- 退潮冰点、弱修复、核心重启、分歧/分化、节点信号和赚钱效应的可复算阈值；
- “弱势切抱团”从失效、冰点禁用到后续是否再次恢复；
- FOMC 后事件一致预期、盘中追逐与兑现候选是否有跨事件样本；
- 市场状态判断正确但组合表达错误的后续纠错方式；
- 既有 pilot 到达其固定序号时的连续语义、反例和 source-exact 复验。

开始 batch-010 时先重算第 226—250 条，确认 25 个目标仍无 observation，然后从 `7553495587510291727` 全文开始。完成 batch-010 checkpoint 后直接进入 batch-011，持续执行，直到本文件第 11 节全部满足。
