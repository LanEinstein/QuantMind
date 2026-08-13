# M2 阶段 C 接手说明:batch-008 与第二个百条综合(codex)

> 日期:2026-08-13
>
> 工作目录:`/home/ps/papers/QuantMind`
>
> 分支:`agent/m2-evidence-reconstruction`
>
> 当前 HEAD:`e8194d5 docs(research): add M2 G1 handoff`
>
> 当前恢复点:**`M2-C-batch-008`(固定序号 176—200)**
>
> 当前门禁:**G1 已通过、阶段 C 进行中、G2 未到**
>
> 上游接手文档:`KickoffPrompts/M2-phase-C-claude-fable-long-context-handoff-2026-08-13.md`(其中 `resume_from=M2-C-batch-005` 已过期,以本文件和最新 worklog 为准)
>
> 本轮目标:完整处理固定序号 176—200(batch-008),随后基于 1—200 条做**第二个百条人工综合**追加到 casebook;若预算仍充足可继续 batch-009,但绝不能在批次中间留下半成品。

## 0. 先理解这项工作是什么

QuantMind 正在重构为中长线 A 股投研系统。M2 阶段 C 的任务不是写策略代码、不是回测择优,而是把"全能的野人"视频语料逐条还原为可审计、时间隔离、能容纳冲突和未知的证据层。owner 已明确"G1通过,可以进入全量分析",不要重新询问 G1,不要重做阶段 A/B,不要重做已完成的 batch-001—007。

唯一底线:**永禁真实券商程序化下单**。反过度防御(HERO)约定全程生效。

## 1. 干净上下文开始时直接执行什么

1. 按第 2 节顺序完整阅读所有必读文档,不要只读摘要。
2. 核对分支、HEAD、dirty worktree(见第 4 节),不要覆盖现有改动。
3. 从 `metadata.jsonl` 按 `[published_at, aweme_id]` 升序重算固定序号,核对 batch-008 边界(第 5 节)。
4. 由主 agent 亲自逐条阅读第 176—200 条完整转写并分析;**不得用子 agent 或摘要模型代读**。
5. 每条 observation 落盘后立即 Pydantic 校验;25 条完成后跑完整性检查并追加 worklog。
6. 完成第 200 条后,基于 1—200 条做第二个百条人工综合与冲突审查,追加到 `docs/research/yeren-system/casebook.md`(要求见第 11 节)。
7. 不 commit、不 push(owner 明示授权才能 push);禁止 `--no-verify`。

## 2. 必须完整读取的文档

按顺序,标注"全文"的不能只读摘要:

1. `AGENTS.md`(全文)
2. `CLAUDE.md`(全文)
3. 本文件
4. `docs/research/midterm-rearch-action-plan-2026-08-12.md`(全文)
5. `KickoffPrompts/M2-evidence-alignment-and-trading-system-reconstruction-kickoff-2026-08-13.md`
6. `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`(全文)
7. `KickoffPrompts/M2-phase-C-claude-fable-long-context-handoff-2026-08-13.md`(全文;工作方法与完成定义的权威来源,仅进度信息过期)
8. `docs/research/yeren-system/research-methodology.md`(全文)
9. `docs/research/yeren-system/expectation-semantics-owner-direction-2026-08-13.md`(全文;owner 设计方向,不得冒充博主证据)
10. `docs/research/yeren-system/data-and-source-coverage.md`(全文)
11. `docs/research/yeren-system/casebook.md`(全文,尤其"阶段 C 首轮 1—100 条人工综合")
12. `data/yeren_research/worklog.jsonl`(逐行全读;最后一条必须是 `M2-C-batch-007`,`status=completed`,`resume_from=M2-C-batch-008`)
13. `data/yeren_research/hypotheses.jsonl`(逐行全读,共 75 条;理解 revision 链、反证、例外)
14. `data/yeren_research/cases/` 下全部 17 个 case JSON,至少全文读 batch-005/006/007 的六条:
    - `batch005-euphoria-exit-and-fear-reentry-2025-08-10-to-2025-08-15.json`
    - `batch005-yajiang-awave-and-lagging-core-2025-08-06-to-2025-08-15.json`
    - `batch006-slow-bull-heat-cycle-and-top-add-2025-08-17-to-2025-08-22.json`
    - `batch006-ai-plus-realization-and-self-split-2025-08-24-to-2025-08-28.json`
    - `batch007-mode-invalidation-cash-and-node-reentry-2025-08-29-to-2025-09-05.json`
    - `batch007-arb-rotation-and-multi-kill-2025-09-05-to-2025-09-09.json`
15. `scripts/yeren_research/schema.py`(读懂 `VideoObservation`/`EvidenceBundle`/`HypothesisRevision` 与校验边界)
16. `data/yeren_research/events/ai-plus-action-plan-2025-08-26.json`(batch-006 新增官方事件档案)
17. 每条视频的 `data/yeren_corpus/transcripts/<aweme_id>.json`:完整 `text` 与全部 `sentences`,不许抽读。

代码发现优先 codebase-memory MCP(项目图谱 `home-ps-papers-QuantMind`);JSON/JSONL/Markdown 用 `rg`/`jq`。

## 3. 当前真实基线(截至 M2-C-batch-007 完成)

- metadata 总记录 1,088;固定序号 **1—175 已全部人工分析**;
- observation 工件 **186**(唯一视频 184;含 2 个 append-only `-v1.1` 旧版);
- hypothesis revision **75**;decision/outcome bundle **82**(41 对);case **17**;
- events:新增 `ai-plus-action-plan-2025-08-26.json`(国务院"人工智能+"意见,新华社 2025-08-26,日期精度,最早可交易 2025-08-27T09:30);
- batch-005/006/007 各新增 25 条首次 observation(无复用、无修订);span 逐字核验 87/87、83/83、75/75;
- `tests/yeren_research`:20 passed;`git diff --check`:passed;全库 Pydantic 通过;
- 研究区 `data/yeren_research/` 被 Git 忽略且 append-only;`git status` 看不到研究产物不代表不存在。

batch-005—007 新增的 hypothesis(写 rule_links 前先读原文):

- 新假设:`H-EUPHORIA-EXIT-001`(→R2)、`H-CLEAN-TRADE-001`、`H-CAPITAL-LEADS-NEWS-001`(→R2)、`H-EXIT-BY-DRIVER-TYPE-001`、`H-AUDIENCE-SELF-LAYER-001`、`H-ARB-VS-CONVICTION-001`;
- 关键修订:`H-THEME-CONTINUATION-001-R4`(两介入点最终版)、`H-CAPITAL-FIRST-001-R3`(模式失效判据,stable_core)、`H-SYSTEM-PRESET-001-R3`(预期兑现即打满+反公式化,stable_core)、`H-MARKET-STATE-INPUTS-001-R7`、`H-WEAK-MARKET-CORE-001-R5`、`H-NEWS-STATE-WEIGHT-001-R2`、`H-PROFIT-LOCK-WITHDRAWAL-001-R4`、`H-EXIT-EXPECTATION-001-R2`(batch-004)。

batch-007 留给 batch-008 的优先核对线索(全部来自 worklog open_items,不预判结论):

- 9 月 9 日套利撤退的比例、稳定币埋伏仓与汽车零部件仓的后续;
- "节点信号""确定性买点""分化 vs 分歧"的构成要素是否有新表述;
- 寒武纪("寒王")2025-08-28/29 公告与"又一个被关小黑屋"所指:待核验事件(只有会改变动作语义时才查官方原文);
- "弱势切抱团失效→改变战法"是否兑现;其预估"一周半震荡期"的演化;
- 空仓/满仓的账户范围、"+40%""+7%"的分母仍未知(可明确记 unknown 继续)。

## 4. 工作区保护

开始前运行:

```bash
git status -sb
git log -5 --oneline --decorate
git branch --show-current
```

分支 `agent/m2-evidence-reconstruction`,HEAD `e8194d5`。tracked/untracked 改动全部保留,禁止 `git checkout --`/`git reset`/批量格式化:

- `KickoffPrompts/M2-G1-owner-review-continuation-2026-08-13.md`
- `KickoffPrompts/M2-phase-C-full-analysis-continuation-2026-08-13.md`
- `KickoffPrompts/M2-phase-C-batch-004-continuation-2026-08-13.md`
- `KickoffPrompts/M2-phase-C-claude-fable-long-context-handoff-2026-08-13.md`
- 本文件(`KickoffPrompts/M2-phase-C-batch-008-codex-handoff-2026-08-13.md`)
- `backend/marketdata_snapshot/coverage.py`、`backend/marketdata_snapshot/store.py`
- `docs/research/yeren-system/` 下五个 md 与 `expectation-semantics-owner-direction-2026-08-13.md`
- `scripts/factor_research/ingest_round2_data.py`
- `tests/marketdata_snapshot/test_coverage.py`、`tests/marketdata_snapshot/test_snapshot_store.py`

`data/marketdata_pit/`、`data/yeren_corpus/`、`data/yeren_research/` 全部 append-only:不删除、不覆盖;observation 修订用 `-v1.1` 新文件;hypothesis/worklog 只追加;不从零重下 PIT。

## 5. batch-008 固定边界(必须先重算再开工)

重算命令:

```bash
jq -s 'sort_by([.published_at, .aweme_id]) | .[175:200] |
  to_entries | map({ordinal:(.key + 176), aweme_id:.value.aweme_id,
                    published_at:.value.published_at, duration_ms:.value.duration_ms})' \
  data/yeren_corpus/metadata.jsonl
```

预期边界(重算结果不一致时以重算为准并停下来核查):

| 序号 | 发布时间 | 视频 ID | 时长 ms |
|---:|---|---|---:|
| 176 | 2025-09-09 21:56:06 | `7548087899808566568` | 43900 |
| 177 | 2025-09-10 11:02:36 | `7548290577096510754` | 25767 |
| 178 | 2025-09-10 12:17:46 | `7548309940563545384` | 134567 |
| 179 | 2025-09-10 15:29:26 | `7548359337769618723` | 20479 |
| 180 | 2025-09-10 22:53:35 | `7548473778625809664` | 147700 |
| 181 | 2025-09-11 11:48:02 | `7548673357127781667` | 40767 |
| 182 | 2025-09-11 14:44:32 | `7548718842425888052` | 23173 |
| 183 | 2025-09-11 15:13:12 | `7548726230792588544` | 38289 |
| 184 | 2025-09-11 16:32:51 | `7548746717522660642` | 177500 |
| 185 | 2025-09-12 11:45:59 | `7549043915841555727` | 71467 |
| 186 | 2025-09-12 16:02:28 | `7549110022115413300` | 180034 |
| 187 | 2025-09-12 18:25:04 | `7549146756903947520` | 180070 |
| 188 | 2025-09-13 09:13:00 | `7549251939474263311` | 180067 |
| 189 | 2025-09-13 12:19:00 | `7549253535192714496` | 180034 |
| 190 | 2025-09-13 18:33:00 | `7549255510165474612` | 180067 |
| 191 | 2025-09-14 08:32:00 | `7549256935809486095` | 180067 |
| 192 | 2025-09-14 11:59:00 | `7549258284545707304` | 180067 |
| 193 | 2025-09-14 17:11:17 | `7549869925708959028` | 151700 |
| 194 | 2025-09-15 17:03:32 | `7550239007519755520` | 174734 |
| 195 | 2025-09-15 17:52:27 | `7550251610653592872` | 175200 |
| 196 | 2025-09-16 01:41:32 | `7550372452327001379` | 28400 |
| 197 | 2025-09-16 15:06:21 | `7550579892338511104` | 70434 |
| 198 | 2025-09-16 22:08:37 | `7550688719527972130` | 180034 |
| 199 | 2025-09-16 22:54:15 | `7550700465119284495` | 24914 |
| 200 | 2025-09-16 23:43:42 | `7550713207926574376` | 58234 |

这 25 条 ledger 终态均为 `done`、转写均非空、均无既有 observation(已核对);全部需要首次 observation。序号 201 是 pilot `7552015322325699840`(2025-09-20),属 batch-009,不要越界。

注意:第 174/175 条按 `[published_at, aweme_id]` 排序为 15:09 的 `7547983…` 在前、15:59 的 `7547964…` 在后(时间优先于 ID),重算时不要按 ID 误排。

## 6. 每条视频的工作方法(与前七批完全一致)

对每条固定记录:

1. 读 metadata、标题、发布时间、时长,读完整 transcript `text` 与所有 `sentences`;
2. 区分:可验证事实、市场状态、证券观点、消息/财报解释、已执行动作、计划动作、条件规则、复盘、教学、修辞;
3. 消歧证券、题材、账户、交易类型与时间指代;无法唯一确认写 alternatives+依据+交易后果,不猜;
4. 写 `recording_time_interval`、`referenced_market_intervals`、`information_available_at`、`earliest_action_at`、`reconstruction_precision`(午间发布→13:00 可行动;收盘/晚间/周末→下一交易日 09:30);
5. transcript 原文只放 `transcript_span.raw_text`,`content` 必须为 null;`raw_text` = 源 `sentences[a..b]` 的 `text` 直接拼接(无分隔符),`start_ms`/`end_ms` 取首末句时间戳,必须逐字逐时戳一致;
6. 先在 cutoff 下写 decision 解释,再开未来行情写 outcome;两者物理分文件;
7. 只有原话实际提出行情/公告/财报/事件验证问题才建最小 bundle;修辞、闲聊、重复教学可以零 bundle 零 hypothesis;
8. statement 只陈述原话支持的事实/动作;interpretation 单独写依据、强度(explicit/credible/tentative)、反证、替代解释;
9. rule_link 只引用已存在或本批将追加的 hypothesis ID;
10. 只有改变系统理解才追加 hypothesis(revision 必须正确填 `revision_of` 指向最新一版);孤立口号不升级;
11. 跨日连续操作建最小 case chain(结构参考 batch-007 两个 case:state_action_chain + decision/outcome_bundle_refs + current_findings + unresolved + outcome_note;case 中 `published_at` 必须与 observation 完全一致);
12. observation 落盘后立即校验,不积压。

ASR 处理惯例(前七批已定):

- 高频短语同音误写可做语境 `asr_revision` 并写明 `revision_basis`(先例:"分期转移制/支/转一只"→"分歧转一致";"清仓试错/接入"→"轻仓";"达芬奇"→"大分歧");凡不改变证券/仓位/动作方向的破损词只记 ambiguity,不下载媒体;
- 只有会改变动作语义的 ASR/画面歧义才触发定向媒体复核;
- 已知绰号:"大好河山"=山河智能(已消歧);"寒王/韩王"=疑似寒武纪(候选,未核验);"工业母鸡"=工业母机;"HR零"=H20。

### 观察表生成辅助器(保证 span 逐字一致,建议照抄)

在临时目录创建 `mkobs.py`(不要放进仓库):

```python
"""Build one VideoObservation JSON with transcript spans copied verbatim from source."""
import json, sys
from pathlib import Path

ROOT = Path("/home/ps/papers/QuantMind")
TRANSCRIPTS = ROOT / "data/yeren_corpus/transcripts"
METADATA = ROOT / "data/yeren_corpus/metadata.jsonl"
OUT = ROOT / "data/yeren_research/observations"

_meta = {}
for _line in METADATA.read_text(encoding="utf-8").splitlines():
    if _line:
        _rec = json.loads(_line)
        _meta[_rec["aweme_id"]] = _rec

def span(aweme_id, a, b=None, asr_revision=None, revision_basis=None):
    sentences = json.loads((TRANSCRIPTS / f"{aweme_id}.json").read_text(encoding="utf-8"))["sentences"]
    b = a if b is None else b
    seg = sentences[a:b + 1]
    return {
        "sentence_index": a,
        "end_sentence_index": None if b == a else b,
        "start_ms": seg[0]["start_ms"],
        "end_ms": seg[-1]["end_ms"],
        "raw_text": "".join(x["text"] for x in seg),
        "asr_revision": asr_revision,
        "revision_basis": revision_basis,
        "media_location": None,
    }

def tev(eid, aweme_id, a, b=None, avail=None, **kw):
    return {
        "evidence_id": eid,
        "kind": "transcript",
        "source_ref": f"data/yeren_corpus/transcripts/{aweme_id}.json",
        "content": None,
        "transcript_span": span(aweme_id, a, b, **kw),
        "information_available_at": avail or _meta[aweme_id]["published_at"],
    }

def write(obs):
    meta = _meta[obs["aweme_id"]]
    obs.setdefault("schema_version", 1)
    obs.setdefault("title", meta["title"])
    obs.setdefault("published_at", meta["published_at"])
    obs.setdefault("duration_ms", meta["duration_ms"])
    path = OUT / f"{obs['aweme_id']}.json"
    if path.exists():
        raise SystemExit(f"refusing to overwrite existing observation: {path}")
    text = json.dumps(obs, ensure_ascii=False, indent=2) + "\n"
    sys.path.insert(0, str(ROOT))
    from scripts.yeren_research.schema import VideoObservation
    VideoObservation.model_validate_json(text)
    path.write_text(text, encoding="utf-8")
    print(f"OK {path.name}")
```

命名惯例:evidence_id = `<aweme前6位>-s<起>-<止>`(如 `754808-s2-9`);statement_id = `<前6位>-statement-<slug>`;interpretation_id = `<前6位>-interpret-<slug>`。市场类证据可引用 bundle 记录(`kind:"market"`,`source_ref` 指向 bundle 文件+record_id,`content` 写简短事实)。

### bundle 生成 CLI

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/python -m scripts.yeren_research bundle-market \
  --case-id <case-id> --video-id <aweme_id> \
  --decision-cutoff 2025-09-XXT15:XX:XX+08:00 \
  --start-date 202509XX --end-date 202509XX \
  --endpoint daily --endpoint limit_list_d [--code 000000.SZ ...]
```

自动写 decision/outcome 两个文件并按 cutoff 切分(日线在当日 15:00 可见)。市况参考:PIT 覆盖 20250910—20250916 的交易日为 9/10、9/11、9/12、9/15、9/16;9/13—9/14 为周末。

### hypothesis 追加

以 JSON 行追加到 `data/yeren_research/hypotheses.jsonl`,先用 `HypothesisRevision.model_validate_json` 校验;`revision_of` 必须指向当前最新版本 ID(如再修订两介入点应 `revision_of: "H-THEME-CONTINUATION-001-R4"`,新 ID 为 `...-R5`);supporting_refs 用 `observation:<aweme_id>#<interpretation_id>`、`case:<case_id>`、`event:<event_id>` 格式,引用必须真实存在。

## 7. 每 25 条 checkpoint 的验证(必须全部执行)

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/python - <<'PY'
from pathlib import Path
from scripts.yeren_research.schema import EvidenceBundle, HypothesisRevision, VideoObservation
root = Path("data/yeren_research")
observations = sorted((root / "observations").glob("*.json"))
for path in observations:
    VideoObservation.model_validate_json(path.read_text(encoding="utf-8"))
hypotheses = [l for l in (root / "hypotheses.jsonl").read_text(encoding="utf-8").splitlines() if l]
for line in hypotheses:
    HypothesisRevision.model_validate_json(line)
bundles = []
for folder in ("decision_bundles", "outcome_bundles"):
    for path in sorted((root / folder).glob("*.json")):
        EvidenceBundle.model_validate_json(path.read_text(encoding="utf-8"))
        bundles.append(path)
print(f"observation_artifacts={len(observations)} hypotheses={len(hypotheses)} bundles={len(bundles)}")
PY
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research
git diff --check
git status --short
```

另写只读完整性检查(batch-005—007 的模式,逐项断言):

- metadata 固定边界与本批首末 ID 一致;
- 本批每个 transcript span 的句段范围、`start_ms`、`end_ms`、`raw_text` 与源转写完全一致(用 `"".join(sentences[a:b+1].text)` 对照);
- rule_link 的 hypothesis ID 存在;hypothesis `revision_of` 指向已有 revision;新 hypothesis 的 observation/case/event 引用可解析(fragment 必须真实存在);
- decision records `information_available_at <= decision_cutoff`;outcome records `> decision_cutoff`;每对 bundle cutoff 相同且均非空;
- case 引用的 observation/bundle 文件存在,case 中 `published_at` 与 observation 逐字一致;
- worklog 每行合法 JSON,最后一条 work_unit/gate/resume_from 正确。

完成定义(缺一不可):25 条均有明确分析状态、全部转写由主 agent 全文读、25 条首次 observation、必要 bundle 严格未来隔离、连续操作成 case、hypothesis 只追加系统级变化、span 全量核验、引用无悬空、worklog 追加(门禁原文"G1 已通过、阶段 C 进行中、G2 未到",`resume_from` 指向 `M2-C-batch-008-synthesis` 或直接在同一条 worklog 里含综合,参照 batch-004 的做法把综合并入 batch-008 worklog 亦可,但 casebook 综合必须真实完成后才算收口)。

## 8. 完成第 200 条后:第二个百条综合(101—200)

在 `docs/research/yeren-system/casebook.md` 追加"阶段 C 第二轮 101—200 条人工综合"(tracked 文件,这是本轮唯一应修改的 tracked 文件)。不要机械重述 observation,必须回答:

1. 前 100 条候选规则在 101—200 中哪些得到跨状态、跨题材、跨账户重复支持(重点检验:鼎沸兑现/人群温度、两介入点、分歧切核心弱势切抱团、模式失效停手、套利仓 vs 波段仓、预期兑现打满、利好兑现窗口);
2. 哪些出现直接反证、例外、账户层级差异或时间演化(已知张力:8/13 清仓 vs 8/18 一致看多拿着不动;8/20 山顶加仓 vs 人群温度框架;"拿着不动"教学 vs 本人高频轮动;"弱势切抱团失效"自述);
3. 新形成了哪些完整的前提—介入—加减仓—变化—离场—复盘案例;
4. "预期—反馈偏差"新增了哪些动作前基准、评估窗口(竞价检验、午后确认、次日溢价)、分支和撤回证据;
5. 哪些冲突可由市场层/题材层/证券层/账户层/交易周期解释,哪些仍不能(unresolved 保留);
6. 新缺口是否属于已有来源类别(通常是),是否真的需要扩 coverage(默认不扩);
7. 是否有会实质改变交易动作、公开证据无法解决、必须问 owner 的问题(按第 9 节四条件判断)。

综合仍不冻结 Base v1、不定参数、不进阶段 D、不提交 G2。综合完成后把结论同时写入 worklog,再决定是否继续 batch-009(201—225;注意 201=pilot `7552015322325699840` 已有 observation,须复用规则处理)。

## 9. 数据、媒体与 owner 边界(不变)

- 行情/公告/事件按当时真实可见时间截断;公告只有日期→下一交易日 09:30 可用;
- 无分钟/竞价/封单数据只做日级或方向性复原;"量化、游资、主力"无席位/订单流一律只作口播解释;
- 具体历史事件优先官方原文(交易所/巨潮/政府/新华社);联网调查只围绕具体语料事件(先例:AI+文件已建档;寒武纪公告若 batch-008 语料再次指向且影响动作语义,可做一次定向核验并建最小 event 档案);
- Tushare 仅官方 SDK;出站 IPv4-only;秘密只在 `~/.bashrc`;
- 默认自主推进;只有同时满足(会改变买卖/仓位方向 + 已穷尽语料与官方来源 + 两解释都可信 + 不回答无法不伪造地继续)才向 owner 提一个具体问题。账户分母、证券实体未消歧等照例记 unknown 继续。

## 10. 已知开放项(不要擅自填平)

见 `worklog.jsonl` 最后三条的 `open_items`。特别注意:

- 8/27 盘前视频(`7542945248910232884`)的收益曲线画面是否含账户金额,为候选定向复核项(可能部分解决账户分母缺口;若只是收益率曲线则无价值,不要无边界下载);
- 情绪票切仓"一点 vs 一大部分"自述冲突、9/9 撤退比例、稳定币埋伏结果等,如 batch-008 语料给出答案,在 observation/case 中接上,不要提前编造;
- 两个空文本视频、六个末句偏移异常、pilot 视频清单等旧边界照旧(batch-008 不含这些 ID)。

## 11. 汇报格式

结束时向 owner 中文汇报:实际完成序号与日期范围、每批新增 observation/bundle/case/event/hypothesis 数量、哪些规则获支持/反证/修订、第二个百条综合核心结论、仍未知项及是否改变动作、全部验证结果(span/引用/cutoff/pytest/git)、工作区未提交未推送、精确的下一 `resume_from`、门禁原文:**G1 已通过、阶段 C 进行中、G2 未到**。

现在直接从 `M2-C-batch-008` 的序号 176 开始。先完整读取必读材料,再重算边界;不要重新讨论是否进入阶段 C,也不要等待确认。
