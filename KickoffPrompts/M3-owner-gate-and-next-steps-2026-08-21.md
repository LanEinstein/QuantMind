# 接手说明：520 已研究到本地证据的尽头 → 四道 owner 门 → 分支剧本

> 日期：2026-08-21
> 工作目录：`/home/ps/papers/QuantMind`
> 分支：`agent/m2-evidence-reconstruction`（领先 origin **18** 个提交，**全部未 push**）
> 最新提交：`a5b1d9b` 跨卡语义检索 + 候选 E 后方向研判 → `eae78eb` 上一份接手文档 → `475404e` 候选 E 结果报告
> 当前恢复点：**`M3-owner-gate-four-questions`**（`data/yeren_research/worklog.jsonl` 尾条，共 81 行）

本文件取代 `KickoffPrompts/M3-post-candidate-e-decide-plan-execute-2026-08-21.md`（其三阶段已全部完成，可删）。

---

## 0. 三十秒摘要

520（卡 8）走完了 M3 全链路：第一轮参数化（证据不足）→ A 语义复核（六成样本不属本卡语义）→ B 复权定案 → C 可成交性审计 → 预注册 → 候选 E walk-forward（**样本内外均通过判据**）→ **工作单元 F 跨卡语义检索**（本次）。

**F 的总结论：M3 在广度方向上当前没有可开工对象。** 18 张卡里只有卡 8 同时具备作者亲口给出、可日线观察的入场与退出触发器；唯一的次近候选卡 1 差一个「趋势失效」的可观察定义，**F 已查明作者从没给过**。其余 16 张的缺口不是"还没查"，是"作者没说"。

**因此下一步不是研究者能自行决定的，而是卡在四道 owner 门上。** 本文件的主体是：把四个问题原封不动交给 owner（§4），并为每种回答写好分支剧本（§5）；同时给出 owner 不答时唯一值得做的不阻塞工作（§6）。

---

## 1. 开工检查（逐条跑，核对预期输出）

```bash
cd /home/ps/papers/QuantMind
git status -sb
git log --oneline -3
tail -1 data/yeren_research/worklog.jsonl | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['status'],'|',d['resume_from'])"
```

预期：

```
## agent/m2-evidence-reconstruction...origin/agent/m2-evidence-reconstruction [领先 18]
a5b1d9b research: cross-card semantics retrieval and post-candidate-E direction decision
eae78eb docs: add post-candidate-E decide/plan/execute handoff
475404e research: report 520 candidate-E walk-forward results
M3-post-candidate-E-decide-plan-execute (work unit F) | completed | M3-owner-gate-four-questions
```

工作树应干净。

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/   # 预期 115 passed
$PY/ruff check backend/ scripts/                                       # 预期 All checks passed!
```

核对候选 E 关键数字（任何分支都会引用）：

```bash
python3 -c "
import json
d = json.load(open('data/yeren_research/inventory/m3-520-candidate-e-walkforward-2026-08-21.json'))
for name in ('in_sample', 'out_of_sample'):
    w = d['windows'][name]
    print(name, w['primary_cohort_s8_c']['trades'], w['primary_cohort_s8_c']['mean_net_return_pct'],
          w['placebo']['upper_tail_p_value'], w['judgment_criteria']['pass'])
"
```

预期：

```
in_sample 66925 3.4448573090790426 0.004975124378109453 True
out_of_sample 45942 4.32347000134882 0.004975124378109453 True
```

核对工作单元 F 的检索工件（12 条查询）：

```bash
FEISHU_INTERACTIVE_ENABLED=false $PY/python -m scripts.yeren_research.cross_card_semantics \
  --output /tmp/f-recheck.json
```

预期（与 `data/yeren_research/inventory/m3-cross-card-semantics-2026-08-21.json` 逐条一致）：

```
Q1-moving-average-kind: 0 hits / 0 videos
Q1b-latin-ma: 0 hits / 0 videos
Q2-intraday-or-close: 48 hits / 44 videos
Q2b-ma-cross-timing: 0 hits / 0 videos
Q3-arbitrage-magnitude: 40 hits / 30 videos
Q3b-point-counts: 65 hits / 43 videos
Q4-trend-invalidation: 12 hits / 9 videos
Q4b-break-below-lines: 11 hits / 6 videos
Q1c-ma-vocabulary-census: 16 hits / 12 videos
Q1d-ma-type-census: 0 hits / 0 videos
Q2c-ma-cross-census: 10 hits / 6 videos
Q4c-invalidation-census: 19 hits / 14 videos
```

---

## 2. 不可改变的总纲

### 2.1 唯一底线

**永禁真实券商程序化下单。** 系统只维护模拟盘。任何把研究结果接入真实券商、真实账户、真实订单或自动执行路径的请求都必须拒绝并上报。所有产物带 `real_broker_orders=false`。

### 2.2 主线次序

先复刻「全能的野人」的交易系统、交易逻辑与操作手法，再做系统性优化。**注意：复刻的产出层已经完成**——18 张卡片 + Base v3，32 个 hypothesis 家族无遗漏（F 已核）。剩下的复刻动作是 **owner 逐张审阅第二、三批卡片**，研究者无法代劳。

### 2.3 反过度防御（四禁）

1. 禁写没有实际用途的校验和/指纹/摘要；
2. 禁防御本项目不会出现的输入；
3. 禁用评分表/机械清单/**复验循环**替代人的判断；
4. 禁为想象的未来需求预建功能开关、迁移框架、兼容层。

判断句：**「这能检测到什么具体故障，我会因此做出什么不同的决定？」答不上来就不写。**

### 2.4 🛑 跨模型 review：至多一轮（owner 2026-08-21 新立，强制）

**codex（或任何跨模型）review 与随后的修改，一个任务至多进行一次。** 一轮 review + 一轮修复，然后停止。

**禁止**：复验到「无剩余缺陷」、「连续两轮干净才收工」、R1-R5 多轮轮转、为确认收敛再跑一轮。

**owner 原话**：「有足够的证据表明，多轮 review 会大幅提升 AI 的误判率以及过度纠错。」

**本项目的实测证据（工作单元 F 违规跑了五轮，被 owner 当场叫停）**：复验一（3 P1+5 P2）、复验二（1 P1+2 P2+1 P3）抓到真硬伤——引用跨度按 Python 左闭右开写、违反项目 `sentences[start:end+1]` 闭区间约定（4 段引用出处标错），以及去噪改写未标注冒充引用。**复验三起 P1 归零**，其后三轮 10 条全是同一节「自查披露该写几处偏差」的记账 P2，把数字从 3 推到 10，**没有改变任何结论、数据或引用**。正确收工点是复验一之后。

规则已写入 `CLAUDE.md`、`AGENTS.md` 与 memory `cross-model-review-single-round-cap`。

### 2.5 本阶段边界

- 卡 8（520）维持「研究候选、不可执行」；卡 1 维持 candidate 且已登记「不能进 M3」；
- 不接飞书，不进模拟盘，不创建模拟订单，不进 `backend/playbook/`；
- 不修改 `data/marketdata_pit/` 的既有档案；
- **不因任何新发现回改候选 E 已冻结的口径**（预注册 §8 原样适用）；
- **不 push**（`git push` 需 owner 明示授权）。

---

## 3. 已定案事实（直接引用，不要重新论证）

### 3.1 候选 E 结果

| 窗口 | 主口径 (c) 笔数 | 净收益均值 | 净胜率 | placebo 上尾 p | 判据 |
|---|---:|---:|---:|---:|---|
| 样本内 20150105–20221230 | 66,925 | +3.44% | 60.82% | 0.00498 | **通过** |
| 样本外 20230103–20260819 | 45,942 | +4.32% | 65.18% | 0.00498 | **通过** |

披露对照 (a) 两窗口均为负（−2.79%／−2.74%）。**判据通过只证明「交易级时点信号存在」，不构成组合级盈利证明，不触发晋升 `stable_core`，不触发进入 `backend/playbook/`。**

### 3.2 工作单元 F 的四问结论（`m3-cross-card-semantics-retrieval-2026-08-21.md`）

| 问 | 判定 | 处置 |
|---|---|---|
| **Q1 均线类型** | **全语料无表述**——「加权／指数均线／EMA／MA／参数／复权」合计 **0 次**；作者明说选哪条线「都没有关系」（`7522847855829372194 / sentences[52:58]`） | SMA **冻结为研究代理**，不得称为作者口径。**已关闭，不再可解锁** |
| **Q2 执行时点** | **作者明确**——他在**当日盘中／尾盘**动手；套利语境直接原话「我们就把这个呃时机入场时机啊往后拖到尾盘」＋同条 s19「我就是做套利的」（`7670922299076441832 / sentences[13:18]`） | 候选 E 的「次日开盘执行」**晚半个到一个交易日**，登记为**作用于全部 18 张卡**的系统性偏差；**未回改候选 E**。**这是 F 留下的唯一有研究价值的开口，见 §6** |
| **Q2′ 「即将上穿」盘中判定** | **全语料无表述**（上穿/下穿穷举 10 处无一说明） | X3 维持不可识别，**需分钟级数据 + 作者说明，两者都没有** |
| **Q3 八到十个点** | **作者明确**——「这个地方的差价……这就是我们的套利空间」，次级情形「上车点不好……三到五个点」 | 性质是**描述性价差区间，不是退出阈值**；**不得规则化为止盈线**。已关闭 |
| **Q4 卡 1「趋势失效」** | **无卡 1 语境的可观察定义**（8 次「失效」归类 6/1/1，无一是持仓趋势失效条件） | 查到两条不等价的相邻说法，按 A 单元先例**登记不采信** → **卡 1 不能进 M3**。已关闭 |

### 3.3 逐卡 M3 资格（`m3-post-candidate-e-next-step-decision-2026-08-21.md` §三）

**判据两条**：入场触发器与退出触发器都必须是①作者亲口给出、②可在日线（`daily` + `adj_factor` + `stk_limit` + `suspend_d` + PIT 财报）上计算。

结论：**只有卡 8 两端齐备**（已走完 M3）。卡 1 入场齐、退出缺（Q4 已证作者没给）。其余 16 张至少缺一端，且缺口是"作者没说"。**不要重做这份盘点。**

### 3.4 已被 F 证伪的两个"看起来能做"的方向

- **方向 (a)（补齐 P1–P3 做组合级验证）没有可推进子集**：P2 分母阻塞于 owner 裁决；**P1 证券池与 P3 并发上限阻塞于作者根本没说过**（A-1c 已在 1,110 条 transcript 上检索过，见 `m3-520-semantic-audit-2026-08-21.md` §4.3）。硬做只能由研究者臆造。
- **M2 不需要"收尾"**：卡 13–18 已于 2026-08-20 落盘（`playbook-cards-batch3-2026-08-20.md`），**32 个 hypothesis 家族全部被卡片/Base v3 引用，未成卡家族为 0**。复核方式：

  ```bash
  PY=/home/ps/anaconda3/envs/zhanglan/bin
  $PY/python - <<'EOF'
  import json, re, glob, collections
  rows=[json.loads(l) for l in open('data/yeren_research/hypotheses.jsonl')]
  fams={re.sub(r'-R\d+$','',r['hypothesis_id']) for r in rows}
  text="".join(open(p).read() for p in
      glob.glob('docs/research/yeren-system/playbook-cards-*.md')
      + ['docs/research/yeren-system/base-v3-spec-2026-08-20.md'])
  print("families", len(fams), "| uncited", [f for f in fams if f not in text])
  EOF
  ```

  预期：`families 32 | uncited []`

### 3.5 语料 `text` 字段的真实情况（**F 之后的细化，避免下一个 session 做无用功**）

已知事实是「857/1,110 个 transcript 的 `text` 字段与 `sentences` 拼接不一致」。**本次进一步查明：这 857 处全部是排版差异，没有一处内容分歧**——

```
不一致文件 857
  去标点后完全相同 807 / 去标点后仍不同 50
  那 50 处的差异全部是拉丁字母大小写（'A' vs 'a'、'O' vs 'o'、'N' vs 'n'）
```

复核脚本：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
$PY/python - <<'EOF'
import json, glob
PUNCT = "，。？！、,.?!;：: 　"
bad=same=0
for p in sorted(glob.glob('data/yeren_corpus/transcripts/*.json')):
    d=json.load(open(p))
    join="".join(s.get("text") or "" for s in d.get("sentences") or [])
    t=d.get("text") or ""
    if t==join: continue
    bad+=1
    if "".join(c for c in t if c not in PUNCT)=="".join(c for c in join if c not in PUNCT):
        same+=1
print(f"不一致 {bad}，其中去标点后相同 {same}")
EOF
```

预期：`不一致 857，其中去标点后相同 807`

**结论与行动**：`text` 是 FunASR 标点更规整的整篇文本，`sentences` 是带时间戳的分段文本，**两者内容一致**。因此：

- **引用与跨度仍然只能用 `sentences`**（跨度由句索引定义，这条不变）；
- **但不存在"语料损坏"，不要去做"修复 `text` 字段"这类任务**——那是纯粹的无用功，违反反过度防御第 1 禁；
- `scripts/yeren_research/corpus.py::find_candidates` 读 `text` 字段**不是 bug**，只在标点/大小写边界上与逐句检索有极小差异；**不要为此改它**，除非某个具体任务真的被它咬到。

---

## 4. 四道 owner 门（原文照抄，可直接交给 owner）

以下四个问题在 `m3-post-candidate-e-next-step-decision-2026-08-21.md` §六 已收窄成一句话可答的形式。**本 session 不得自行拍板任何一项。**

**Q-owner-1｜是否 push？**
本分支 `agent/m2-evidence-reconstruction` 有 **18 个本地提交全部未 push**，内容全是研究文档与研究脚本（`scripts/yeren_research/`、`docs/research/yeren-system/`）与两处守则更新（`CLAUDE.md`、`AGENTS.md`），**不含任何生产代码、不含 `backend/playbook/`、不含券商路径**。
→ 请回答：**「push」** 或 **「先不 push」**。

**Q-owner-2｜跨卡的 10% 分母能不能用到 520？**
语料里有两处仓位分母表述（无确定性的套利/试错 ≤ 总仓位 10%、套利仓「不能重仓」「是清仓的」），但**都不是 520 语境**（一处讲反包试错，一处讲题材龙头套利）。520 自己的证券池与并发上限**全语料无表述**。
→ 请三选一：**「可以援引，写进卡 8 第 5 条」** ／ **「不可援引」** ／ **「维持现状：只登记为候选证据，不写进卡面」**（现状即第三项）。
→ **后果提示：回答"不可援引"或"维持现状"，等于 520 的组合级验证（方向 (a)）永久不开工**，卡 8 会长期停在交易级结论上。这不是坏结果，但请知悉。

**Q-owner-3｜卡 8 现在这一版是否通过？**
卡 8 的第 4/5/6/8/9/10 条已按 A、B、C、E 四个单元的结论修订，三处实质改动：① 第 4 条改成「离场规则适用于金叉完全成立之后」并新增 S8 缺口；② 第 6 条登记「三十天」是 ASR 误识别、实为「三四天」；③ 第 8 条把复权口径从未冻结项移出（后复权 `close × adj_factor`）。**F 之后还应再加三条**（见 §5.3 的修订建议）。
→ 请回答：**「通过」** 或 **点出要改的条号**。

**Q-owner-4｜实际券商费率是多少？**
候选 E 用的是研究者假设费率：**佣金 0.025%（单笔最低 5 元）、过户费 0.001%、印花税卖出侧 0.1%、滑点单边 0.1%**，让毛收益到净收益掉了约 0.7–0.75 个百分点。
→ 若与实际差不多：回答 **「按你写的就行」**；若不同：**给出佣金率与最低佣金**（其余三项是法定或市场惯例）。改了需重新预注册并重跑，成本约几分钟。

---

## 5. 分支剧本（按 owner 的回答执行）

### 5.1 若 Q-owner-1 = 「push」

```bash
cd /home/ps/papers/QuantMind
git log --oneline origin/agent/m2-evidence-reconstruction..HEAD | wc -l   # 预期 18
git push origin agent/m2-evidence-reconstruction
```

**注意**：gitleaks pre-commit 已装，**严禁 `--no-verify`**。push 后在 worklog 追加一条记录实际 push 的 commit 范围。若 owner 未明示 push，**commit 只落本地**。

### 5.2 若 Q-owner-2 = 「可以援引」→ 方向 (a) 部分解锁

这是唯一能让 520 走向「能不能实盘用」的路径，但**只解锁 P2（分母），P1（证券池）与 P3（并发上限）仍然是作者没说过的空白**。因此：

1. **先写预注册**，把研究者必须补的两个假设明确标成 **researcher-added**，不得混进作者口径：
   - P1 证券池：候选 E 的工作母集 5,434 只（`security_count_with_30_rows` 5,782 − 78 只因子历史不可用 − 270 只 `.BJ`），ST 逐笔 PIT 作废；
   - P3 并发上限：**必须写死一个数并说明这是研究者选择**，不得事后挑（参照候选 E 对 `stop_days` 的处理：owner 拍板 3，然后不许再改）；
   - P2 分母：按 owner 授权的总仓位 10%，「套利不能重仓」。
2. 预注册须包含：组合回撤 P4 的定义、是否允许同证券重复持仓、资金不足时的信号取舍规则、以及**判据**（组合级判据不能沿用交易级 placebo）。
3. **预注册写完必须 owner 点头再跑**（候选 E 的先例：owner 一句「stop_days 选 3，S8 按 (c)，费用先按你写的走」解除了三道门槛）。
4. 跑完只报告一次，**不得按结果回改任何口径**。

**若 Q-owner-2 = 「不可援引」或「维持现状」**：在卡 8 第 5 条与 `m3-cross-card-semantics-retrieval-2026-08-21.md` §七 登记「方向 (a) 按 owner 裁决永久不开工」，520 的研究就此封存在交易级结论上。**这是一个合法的终态，不要绕道自己补分母。**

### 5.3 若 Q-owner-3 = 需要改动 → 按 F 的修订建议改卡

F 已提出但**尚未落到卡面**的建议（卡片是 owner 确认稿，必须 owner 点头才改）：

**卡 8（520）**

| 条 | 现状 | 建议改为 |
|---|---|---|
| 第 8 项 | 「均线类型（SMA/EMA，作者全程未提）」列为**未冻结** | **已检索定案：全语料无表述**，研究代理＝SMA，**不得称为作者口径** |
| 第 8 项 | 「『即将上穿』的精确判定」列为未冻结 | 拆两半：**执行时点**＝作者明确（盘中／尾盘），研究代理晚半日到一日，已登记；**盘中精确判定**＝仍不可识别（X3） |
| 第 8 项 | 「收益区间」列为未冻结 | **已定性：八到十个点／三到五个点是描述性价差区间，不是退出阈值** |
| 第 5 项 | 无 | 补：作者在套利语境明说入场时机可「往后拖到尾盘」（`7670922299076441832 / sentences[13:18]`），与本卡「次日开盘执行」的研究代理有已登记差异 |

**卡 1（右侧波段入场）**

| 条 | 建议改为 |
|---|---|
| 第 6 项 | 补第三条跨作品互证 `7657506492748903528 / sentences[23:25]`（2026-07-01），入场端互证达三条、跨度逾一年 |
| 第 8 项 | **已检索定案：全语料无卡 1 语境的「趋势失效」可观察定义**；附登记两条不采信的相邻说法及理由 |
| 第 10 项 | 新增：**本卡当前不能进入 M3**——入场端可日线观察，退出端作者未给定义，硬上等于研究者发明规则 |

**全卡通用**（加在卡片文档总边界处）：**执行时点登记**——作者的动作时点为当日盘中／尾盘或次日开盘；任何用日线重建其动作的 M3 工作，其「收盘后成立、次日开盘执行」是**晚于作者**的保守代理，偏差方向已知、量级不可由日线判定。

### 5.4 若 Q-owner-4 给出了不同费率

改 `scripts/yeren_research/m3_520_candidate_e.py` 的 `CostModel` 参数 → **重新预注册（新文件，不覆盖旧的）** → owner 点头 → 重跑。**旧结果保留，不删不改**，新结果作为并列的一次运行报告。运行约几分钟。

```bash
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.yeren_research.m3_520_candidate_e
```

输出须含 `preregistered_parameters_used=true` 自证不是冒烟跑。

---

## 6. owner 未答时唯一值得做的不阻塞工作

**先说不值得做的**（避免下一个 session 浪费时间）：

- ❌ 修复语料 `text` 字段 —— §3.5 已证内容一致，纯排版差异，**无故障可检测**；
- ❌ 重做逐卡 M3 资格盘点 —— §3.3 已做完；
- ❌ 再检索一遍 520 的证券池／并发上限 —— A-1c 已做，作者没说过；
- ❌ 挑一张卡硬上 M3 —— 必须由研究者发明规则，违反主线次序；
- ❌ 再跑一轮 codex 复核已交付的文档 —— **违反 §2.4**。

**唯一有价值且不阻塞的开口：Q2 执行时点偏差的量级界定。**

F 用作者原话证明了：**作者在当日盘中／尾盘动手，而候选 E 在次日开盘执行**，两者差半个到一个交易日。这个偏差的**方向已知（代理晚于作者）、量级未知**。日线数据虽然无法复现盘中判定（X3 不可识别），**但可以给出一个上下界**：把执行价从「次日 open」换成「当日 close」，其余口径一字不动，得到的差异就是这半个交易日的量级。

**这件事必须按预注册纪律做，否则就是择优：**

1. **先写预注册文档**（新文件，不改候选 E 的预注册），写死：只改执行价一项、其余六组口径逐条沿用、样本切分与种子沿用、**并且明写「本次运行的结果是披露性敏感度，不替换候选 E 的已发布结果，不得据此回改任何冻结口径」**；
2. 预注册写完 **owner 点头再跑**；
3. 跑一次，报告一次，**不得按结果调整**；
4. 结论只能是「这半个交易日的量级是 X」，**不能是「哪个执行口径更好」**。

**风险提示（必须写进预注册）**：当日 close 执行**引入了候选 E 刻意回避的前视嫌疑**——信号在收盘后才成立，用同日收盘价成交在现实中做不到。所以这条线**只能作为偏差量级的上界估计，永远不能成为一个可执行口径**。若判断这个风险大于收益，**登记「不做」并说明理由也是合法产出**。

**如果连这件事也判断不值得做**：本 session 的正确形态就是「把四个问题交给 owner，等回答」，**不要为了有产出而制造工作**。

---

## 7. 通用纪律与本轮踩到的坑

### 7.1 证据纪律（F 的复验一在这里抓到硬伤，务必照做）

- **引用跨度是闭区间**：`sentences[a:b]` 表示 `sentences[a]` 到 `sentences[b]` **含 b**，与 `evidence_quote.py` 的 `range(start, end+1)` 一致。**不要按 Python 左闭右开写**——F 的初稿四段引用因此标错。
- **引用只由 `sentences[start:end+1]` 无分隔拼接生成**，用 `scripts/yeren_research/evidence_quote.py`，不手打、不去噪、不补标点。
- **去噪读法必须显式标注**「去掉 ASR 断句噪声后的字面序列（researcher-added，非引用）」，不得与引用混排。
- **observation 按 `aweme_id` 取最高版本**（`-v1.1`/`-v1.2` 与基础文件并存，1135 文件/1111 唯一 id）。
- 引用自检脚本（写完文档跑一遍，比 codex 更快更可靠）：

  ```bash
  PY=/home/ps/anaconda3/envs/zhanglan/bin
  $PY/python - <<'EOF'
  import json, re, pathlib
  DOC='docs/research/yeren-system/<你的报告>.md'
  doc=pathlib.Path(DOC).read_text(encoding='utf-8')
  def sents(aid):
      return [s["text"] for s in json.load(open(f'data/yeren_corpus/transcripts/{aid}.json'))["sentences"]]
  n=bad=0
  for m in re.finditer(r'`(\d{19})\s*/\s*sentences\[(\d+):(\d+)\]`[^「]*「([^」]*)」', doc):
      aid,a,b,q=m.group(1),int(m.group(2)),int(m.group(3)),m.group(4); n+=1
      if "".join(sents(aid)[a:b+1])!=q:
          bad+=1; print("MISMATCH",aid,a,b)
  print(f"span-quotes={n} mismatch={bad}")
  EOF
  ```

  预期 `mismatch=0`。

### 7.2 分栏纪律

事实 / 解释（researcher-added）/ 研究代理 / 待 owner 决策**分栏写**。数字没有作者来源时标记 **researcher-added**。

### 7.3 工程坑（本轮实际踩到）

- **`pgrep -f "<字符串>"` 做等待循环会自匹配**——监控进程自己的命令行含该字符串，导致永不退出。改用 `while [ -d /proc/<pid> ]; do sleep 20; done`。本轮踩了一次。
- **`ruff format` 不要对整个目录跑**——本轮对 `scripts/yeren_research/` 整目录跑 format，带进 7 个无关文件的排版改动，混进了 commit，只能回退再 amend。**只对本次改动的文件跑。**
- **codex review 读大 JSON 工件会耗尽预算**——本轮 `codex review --uncommitted` 跑满 32 分钟没输出结论（在读 74KB 的检索工件）。若要跑 review，考虑先把大工件排除在审查范围外，或直接用 `codex exec --sandbox read-only` 定向提问。
- **codex 沙箱里跑不了 pytest**（无可用临时目录，`FileNotFoundError: No usable temporary directory`），这是沙箱限制不是测试问题，本地跑即可。
- `data/yeren_research/` **整个目录被 gitignore**——worklog、hypotheses、observations、inventory 都不进 git。报告里引用这些路径是正常的项目惯例，但要知道它们不在版本控制里。

### 7.4 环境

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin           # conda env: zhanglan
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q    # 跑测试必带该 env
$PY/ruff check backend/ scripts/
```

面向 owner 的回复用中文；代码、注释、commit message 用英文；conventional commits。

---

## 8. 交付与停止条件

无论走哪个分支，本 session 至少要留下：

1. 实际产出（分支剧本对应的文档／预注册／代码，或「本轮只交问题给 owner」的明确记录）；
2. `data/yeren_research/worklog.jsonl` 追加一条（含 inputs/outputs/findings/`real_broker_orders=false`/`resume_from`）；
3. 本地 commit（conventional commit，英文 message）；**push 只在 Q-owner-1 = 「push」时做**；
4. memory 更新：`MEMORY.md` 首条 hook + `project-midterm-rearch-yeren-playbook-2026-08-12.md` 详情。

**停止条件**：

- 四个 owner 问题已明确交付且未被研究者自行拍板；
- 没有为了"有产出"而制造工作（§6 的五条 ❌ 一条都没做）；
- **跨模型 review 至多一轮**（§2.4），未做第二轮复验；
- 卡 8 仍是「研究候选、不可执行」，卡 1 仍登记「不能进 M3」，未进 `backend/playbook/`、模拟盘、飞书或任何券商路径。

---

## 9. 先读的文件（按顺序）

1. `CLAUDE.md`、`AGENTS.md` —— 反过度防御四禁、主线次序、**跨模型 review 至多一轮**
2. `docs/research/yeren-system/m3-post-candidate-e-next-step-decision-2026-08-21.md` —— 方向研判，§三 逐卡 M3 资格盘点、§六 四个 owner 问题
3. `docs/research/yeren-system/m3-cross-card-semantics-retrieval-2026-08-21.md` —— 工作单元 F 结果，§六 卡片修订建议、§七 未解决项与解锁条件
4. `docs/research/yeren-system/m3-520-candidate-e-results-2026-08-21.md` —— 候选 E 结果与边界（§五 限制、§七 积压项，两处有 2026-08-21 追加说明）
5. `docs/research/yeren-system/m3-520-preregistration-2026-08-21.md` —— 预注册全文，若走 §5.2 或 §6 需照抄其结构
6. `docs/research/yeren-system/playbook-cards-batch2-2026-08-20.md` 卡 8、`playbook-cards-confirmed-batch1-2026-08-20.md` 卡 1
7. `scripts/yeren_research/cross_card_semantics.py` + `cross_card_semantics_queries.py` —— F 的检索实现，若还要做语料检索直接复用
8. `scripts/yeren_research/m3_520_candidate_e.py` —— 候选 E 实现，若走 §5.4 或 §6 需改其 `CostModel` 或执行价口径

---

## 10. 给 owner 的一句话

520 这张卡已经研究到本地证据的尽头了：作者没说过均线是哪种、没说过「即将上穿」怎么在盘中判、没说过 520 用在哪些票、最多同时拿几只；卡 1 那条「趋势失效」我全语料翻过了，他也从没给过可观察的说法。所以**接下来做什么，取决于你对四个问题的回答**（§4，每个一句话就能答）。另外查到一件对所有卡片都有影响的事：**他是当天盘中、尾盘动手的，而我们的程序是次日开盘执行，晚了半个到一个交易日**——这个偏差我只做了登记，没有动候选 E 的任何口径。
