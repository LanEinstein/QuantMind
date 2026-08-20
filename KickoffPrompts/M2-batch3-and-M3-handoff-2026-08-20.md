# 接手说明：第三批战法卡片 → M3 参数化验证（2026-08-20 交班）

> 日期：2026-08-20
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`（owner 2026-08-14 已授权本分支 commit；**push 仍需 owner 明示**）
>
> 恢复点：**`M2-playbook-cards-batch3`**（worklog 尾条已写入）
>
> 本文件取代 `KickoffPrompts/M2-playbook-cards-handoff-2026-08-15.md`（该文件描述的第一批任务已完成，可由 owner 清理）。

---

## 0. 三十秒摘要

第一批 6 张卡片 + 总体仓位框架**已定稿**，Base 已升到 **v3**，第二批 6 张卡片（卡 7—12）**已产出**。你要做的是：

1. **第三批 6 张卡片**（卡 13—18，候选清单在 §4，含每张卡的证据起点）；
2. 做完后进 **M3 参数化验证第一步**（§5，从卡 8「520 战法」开始，只做研究验证，不进生产）。

开工前必须先读 §2 的两条总纲和 §3 的证据纪律——**这两节是本轮新增的硬约束，违反会直接产出错误卡片。**

---

## 1. 主线次序（owner 2026-08-15 重申，最高优先，未变）

**先复刻「全能的野人」的交易系统、交易逻辑、操作手法（战法卡片 + 总体仓位框架），再做系统性优化。** 考察博主预测精度不是目标；发言命中率统计只是辅助验证之一，不得抢主线（M3-A 引擎保留但批次暂停）。该次序在 `CLAUDE.md` 与 `AGENTS.md` 里。

**唯一底线**：永禁真实券商程序化下单。系统只维护模拟盘。

---

## 2. 两条总纲（owner 2026-08-20 授权，优先于 Base 其余条目）

### V 版本政策 ——「复刻按最新版」

owner 原话：**「复刻系统肯定是按照最新的，因为博主自己也在不断提升和进化」**。

规则：同一主题出现跨期冲突时，**最新表述进入 Base，旧表述降 `phase_rule` 只读存档**——保留原文与日期、不参与执行与统计、不回写现行卡面、不删除；日后语料若重新出现旧口径，可按新证据重新启用。

当前三条活跃存档（不要误当成现行规则）：

| 存档 | 旧版 | 取代它的最新版 |
|---|---|---|
| 主动补仓例外 | 2025-10-30（浮亏 5—8 点 + 反转信号可补） | 2026-08-17/18「跌的时候是不能补的，只有超预期的时候才能是加啊」 |
| 机构回调豁免 | 2025-08-17「不要轻易的下车每一次的回调，都，是买点」 | 2025-11-10「现在并不觉得每一次的回调就是买入的机会了」 |
| 入场措辞 | 2025-07—09「红肥绿瘦」 | 2026 年零出现，已移出卡面 |

**做第三批时的操作要求**：每张卡定稿前，把该卡核心词在全语料做一次时间分布核对（命令见 §6.3）。若某措辞 2026 年已不再出现，移出卡面、写进存档，并在卡片第 10 项说明。

### N 仓位性质第一分叉

**任何行为词、战法卡、规则先归属套利仓 / 波段仓 / 组合层，其次才谈条件。** 卡片第 1 项必须写明性质。

- **波段仓**：可重仓、格局/锁仓、持有跨波动（2025-10-30「我是，会拿它三个月一直到主生的这就是波段的仓」）。
- **套利仓**：不谈格局、反馈窗口以天计、挣一口就走（2026-08-14「就是轮动行情啊电，风扇轮动行情不要谈，歌局啊红麦绿麦就这，样子啊」）。
- **组合层**：管总暴露，跨两支。

已归位：卡 1/4/5/7 = 波段线；卡 2/3/8 = 套利线；卡 6/12 = 组合层与兑现窗口；卡 9/10/11 通用或按情形标注。

---

## 3. 证据纪律（本轮新增，必读）

### 3.1 引用取最高版本 observation 文件

`data/yeren_research/observations/` 有 **1135 个文件但只有 1111 个唯一 aweme_id**，差额是 schema 升级留下的并存版本：

```
7669063381873462208.json          ← v1（旧，raw_text 已过时）
7669063381873462208-v1.1.json
7669063381873462208-v1.2.json     ← 用这个
```

全量核查结果：2790 条 evidence span 中 43 条 raw_text 与 transcript 不符，**全部落在有更高版本后继的 10 个作品的旧文件上**。细节与受影响 id 清单见 `docs/research/yeren-system/evidence-integrity-note-2026-08-20.md`。

> **任何按 `observations/*.json` 通配读取的脚本，必须先按 aweme_id 归并再取最高版本**（`-v1.2` > `-v1.1` > 无后缀），否则会同时读到新旧两份。§6.2 给了现成函数。

### 3.2 原话只能由 `sentences` 拼接，不能取 `text` 字段

1110 个 transcript 里 **857 个**的 `text` 字段与 `sentences` 拼接结果不一致（断句与标点位置不同）。**引用一律用 `sentences[start:end+1]` 无分隔拼接**。

现成工具（本轮已过 codex review 并提交，`7ab1df9`）：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/python -m scripts.yeren_research.evidence_quote \
  data/yeren_research/observations/7675567648260797327.json 767556-4000-obsession
```

预期输出（单行 JSON）：

```json
{"quote": "那这里啊我是也有罪的啊，因为我我是有执念的，就是说我非要摸到四千点之后我才跑。……", "aweme_id": "7675567648260797327", "evidence_id": "767556-4000-obsession", "statement_ids": ["767556-statement-trigger-drift"], "interpretation_ids": ["767556-interpret-trigger-not-executed"]}
```

它会在 raw_text 与 transcript 不符、span 越界、aweme 不匹配时抛 `ValueError`——**报错说明你用了过时的旧版文件，换高版本文件，不要放宽校验**。

### 3.3 卡片定稿前跑一次引用自检

把新卡片里所有 `「…」` 引用与全语料逐条比对，命令见 §6.4。本轮用它抓出并修正了 4 处自身笔误，**不要跳过**。

---

## 4. 第三批卡片任务（卡 13—18）

产出文件：`docs/research/yeren-system/playbook-cards-batch3-2026-08-20.md`（若跨日则用当日日期）。

格式与第二批完全一致（十项），参考 `playbook-cards-batch2-2026-08-20.md`：

```
1. 名称／仓位性质      2. 适用市况        3. 入场条件
4. 加减仓与退出        5. 仓位约束        6. 原话与出处（aweme_id / evidence_id）
7. 证据分类（家族+终态修订号）              8. 未冻结参数
9. 反例与边界          10. 本轮改动 / 与其他卡的关系
```

### 六张候选卡与证据起点

| # | 卡片 | 家族 | 起点 observation（已核对最高版本） |
|---|---|---|---|
| 13 | 硬逻辑只生成候选、不生成买点 | `H-SELECTION-HARD-LOGIC-001-R2` | `7526862820366421248.json`、`7527214207910972707.json`、`7527955022601424163.json`、`7529432405641202944.json` |
| 14 | 财报两步读法（先看预期已交易多少，再看质量） | `H-EARNINGS-EXPECTATION-001` + `H-EARNINGS-QUALITY-001` | `7566602982977376372-v1.1.json`、`7626700051746734178-v1.1.json`、`7658346105893142863-v1.1.json`、`7674117462406189440.json`（2026-08-15 茅台中报，evidence `767411-report-method` / `767411-profit-decline` / `767411-contract-liability` / `767411-cashflow-interpretation`）；另有 `case:pilot-jiangbolong-earnings-2026-07-03-to-2026-07-08` |
| 15 | 题材容量下钻到证券角色与次日供给 | `H-MARKET-LIQUIDITY-PRIORITY-001-R4` | `7530589466420940032.json`、`7530948542933323023.json`（7/24 预判前排缺接盘 → 7/25 山河智能收跌而西藏天路涨停） |
| 16 | 直接点名的可核验利空优先降暴露 | `H-NEWS-DIRECT-HARM-EXIT-001`（playbook_special_case） | `7669063381873462208-v1.2.json`（**必须用 v1.2**）；反证 `event:claim-broker-negative-2026-08-02-recheck-2026-08-13` |
| 17 | ETF 只表达方向、不消除市场风险 | `H-ETF-INDEX-EXPRESSION-001`（playbook_special_case） | `7566602982977376372-v1.1.json`、`7669063381873462208-v1.2.json`、`7674890673190899520.json`（evidence `767489-theme-and-etf`） |
| 18 | 退潮/清仓后的轻仓试错重入 | `H-REENTRY-LIGHT-TRIAL-001-R2` | `7526939325557165347-v1.1.json`、`7602233626348496192-v1.1.json`、`7671135038796520360.json`（`520360-s5-11` 强反弹默认不做、`520360-s15-20` 极致性价比＝日内浮盈十个点以上）、`7522847855829372194.json`（`752284-s59-70` 十层仓位买一至两层） |

### 已替你先跑过的词频（2026-08-20 实测，直接用）

| 词 | 总次数 | 时间跨度 | 2026-06 之后 | 对卡片的含义 |
|---|---:|---|---:|---|
| 硬逻辑 | 47 | 2025-07..2026-06 | 1 | 卡 13 的锚词在 2026 年几乎不再出现，**定稿前必须判断是措辞退役还是规则退役**，并按版本政策处理 |
| 接不动 | 7 | 2025-07..2026-04 | 0 | 卡 15 的锚词是 2025—2026H1 措辞，卡面不要直接用它，改用它描述的机制（前排缺接盘资金／次日供给） |
| ETF | 35 | 2025-10..2026-08 | 23 | 卡 17 的锚词仍活跃，可直接作卡面 |
| 试错 | 55 | 2025-07..2026-06 | 6 | 卡 18 的锚词仍在用 |
| 极致性价比 | 3 | 2026-08..2026-08 | 3 | 卡 18 的例外口径是**最新出现的**，但只有 3 次、单月，证据强度低，卡面必须写明 |

卡 14（财报）与卡 16（直接利空）没有单一锚词，按证据起点逐条读。

### 卡 18 的特别提示

`H-REENTRY-LIGHT-TRIAL-001-R2` 本轮已把「极致性价比」从**完全未冻结**改为**有作者口径、待验证**（日内浮盈十个点以上）。卡面必须同时写出两件事：① 抢反弹**默认不做**（「那这反弹啊它是你是不能做的，你为什么不能做？因为它没有确定性……」）；② 例外有口径但未验证，复刻中默认关闭。不要把它写成一般抄底条件。

### 完成条件

六张卡写完 → 跑 §6.4 引用自检 → 追加 worklog（`work_unit=M2-playbook-cards-batch3`，`resume_from=M3-parameterization-520`）→ 更新 `docs/research/yeren-system/playbook-cards-owner-review-2026-08-20.md` 的索引表 → commit（docs-only 免 codex review）→ **停下来向 owner 汇报**。

---

## 5. 第三批之后：M3 参数化验证第一步

**第三批交付并向 owner 汇报后**才开始，且必须先向 owner 说明再动手。

### 为什么从卡 8（520 战法）开始

它是全部 18 张卡里**唯一自带完整可观察条件**的一张：均线关系（5/20/30 日线）＋ 明确入场点（5 日线即将上穿 20 日线）＋ 明确退出点（5 日线一拐头就走 / 完全离场＝5 日线下穿 20 日线）。其余卡片的核心条件（「确定性」「符合预期」「退潮」「人声鼎沸」）目前都没有可观察定义，**参数化会变成编数字**。

### M3 第一步的边界（严格）

- 只做**研究侧验证**：把 520 转成确定性规则 → 用 `data/marketdata_pit/` 做样本内外检验（真赚钱 + 回撤可接受 + 非运气）。
- **不写 `backend/playbook/`**，不接飞书，不进模拟盘，不碰真实下单。
- **禁止收益择优改语义**：若回测不好，结论是「该战法未通过验证」，**不是**「把参数调到通过」。忠实度先于表现（Base v3 §一）。
- 数值来源必须是语料里有的（止跌天数、均线周期、8—10 个点的套利空间口径）；语料没有的一律作为待检验的敏感性参数，并在报告里标明是我们补的，不是他说的。
- 防前视：视频发布在收盘后，发言只能预测次日及之后；Tushare 仅官方 SDK；`*_vip` 端点必须 limit+offset 分页。

### 交付物

`docs/research/yeren-system/m3-520-parameterization-2026-XX-XX.md`：规则化定义 → 数据与时间口径 → 检验设计（含随机 placebo 对照）→ 结果 → 结论（通过/未通过/证据不足）→ 未解决问题。

---

## 6. 恢复与常用命令

### 6.1 恢复检查

```bash
cd /home/ps/papers/QuantMind
git status -sb && git branch --show-current
tail -1 data/yeren_research/worklog.jsonl | jq -c '{work_unit,status,resume_from}'
```

预期输出：

```
## agent/m2-evidence-reconstruction
{"work_unit":"M2-playbook-cards-confirm-batch1-and-batch2","status":"completed","resume_from":"M2-playbook-cards-batch3"}
```

计数基线（用于确认没读错文件）：

```bash
wc -l data/yeren_research/hypotheses.jsonl data/yeren_research/worklog.jsonl
ls data/yeren_research/observations | wc -l
ls data/yeren_corpus/transcripts | wc -l
```

预期：hypotheses **355**、worklog **73**、observation 文件 **1135**（唯一 aweme 1111）、transcripts **1110**。

### 6.2 按最高版本读 observation（复制即用）

```python
import glob, os, re, json

def latest_observation(aweme_id: str) -> dict:
    """Read the highest-version observation file for one work."""
    paths = glob.glob(f"data/yeren_research/observations/{aweme_id}*.json")

    def version(path: str) -> tuple[int, int]:
        m = re.search(r"-v(\d+)\.(\d+)\.json$", os.path.basename(path))
        return (int(m.group(1)), int(m.group(2))) if m else (1, 0)

    return json.load(open(max(paths, key=version), encoding="utf-8"))
```

### 6.3 措辞的时间分布核对（版本政策用）

```bash
/home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
import json, glob, os, datetime, collections
TERMS = ["硬逻辑", "接不动", "ETF", "试错", "极致性价比"]   # 换成你要查的词
meta = {str(json.loads(l)["aweme_id"]): json.loads(l)["create_time"]
        for l in open("data/yeren_corpus/metadata.jsonl", encoding="utf-8")}
cnt = collections.defaultdict(collections.Counter)
for f in glob.glob("data/yeren_corpus/transcripts/*.json"):
    aid = os.path.basename(f)[:-5]
    txt = "".join(x["text"] for x in json.load(open(f, encoding="utf-8"))["sentences"])
    ym = datetime.datetime.fromtimestamp(meta[aid]).strftime("%Y-%m") if aid in meta else "?"
    for t in TERMS:
        if txt.count(t):
            cnt[t][ym] += txt.count(t)
for t in TERMS:
    months = sorted(cnt[t])
    total = sum(cnt[t].values())
    recent = sum(v for k, v in cnt[t].items() if k >= "2026-06")
    print(f"{t}: total={total} span={months[0] if months else '-'}..{months[-1] if months else '-'} 2026-06+={recent}")
PY
```

判读：`2026-06+` 为 0 且总数很少 → 该措辞属旧版，移出卡面、写进 phase_rule 存档。

### 6.4 卡片引用自检（定稿前必跑）

```bash
/home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
import json, re, glob
DOC = "docs/research/yeren-system/playbook-cards-batch3-2026-08-20.md"   # 换成你的文件
allt = "\n".join("".join(x["text"] for x in json.load(open(f, encoding="utf-8"))["sentences"])
                 for f in glob.glob("data/yeren_corpus/transcripts/*.json"))
meta = open("data/yeren_corpus/metadata.jsonl", encoding="utf-8").read()
miss = []
for ln, line in enumerate(open(DOC, encoding="utf-8").read().split("\n"), 1):
    for q in re.findall(r"「([^」]{10,})」", line):
        for seg in re.split(r"……|…", q.replace("**", "")):
            seg = seg.strip()
            if len(seg) >= 10 and seg not in allt and seg not in meta:
                miss.append((ln, seg[:70]))
print("suspect:", len(miss))
for m in miss:
    print(m)
PY
```

预期：输出里只剩你自己写的概念短语（如「候选—激活—回踩」），**任何标着作者原话的条目都不能出现在列表里**。出现了就去 transcript 里取回逐字文本。

### 6.5 假设修订校验

```bash
# 把新写的 revision 单独写成一个 json 文件再校验
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/python \
  -m scripts.yeren_research validate hypothesis /tmp/H-XXX-001-R1.json
```

必填字段：`hypothesis_id / recorded_at / rule_text / conditions / classification / supporting_refs / first_seen_at / trading_consequence_if_wrong`；`classification ∈ {stable_core, phase_rule, playbook_special_case, candidate}`。

### 6.6 测试与 lint

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/    # 必带该 env
$PY/ruff check backend/ scripts/
```

---

## 7. 当前状态（已建成，不要重做）

| 项 | 状态 |
|---|---|
| M1 语料管线 | 完成，1111 条唯一作品，transcripts 1110 |
| M2 全量证据 | observation 覆盖 1111/1111；hypotheses 355 行 |
| 第一批 6 卡 + 总体框架 | **已定稿** `playbook-cards-confirmed-batch1-2026-08-20.md` |
| Base | **v3 现行** `base-v3-spec-2026-08-20.md`（G2 六问已裁决；stable_core 仍 D1+B5） |
| 第二批 6 卡（卡 7—12） | **已产出** `playbook-cards-batch2-2026-08-20.md` |
| 证据层核查 | `evidence-integrity-note-2026-08-20.md` |
| 引用工具 | `scripts/yeren_research/evidence_quote.py`（4 tests、ruff 干净、codex review P1 已修） |
| M3-A 命中率引擎 | 保留但暂停，不占主线 |
| 本地 commit | `196c383`、`11c96fa`、`7ab1df9` —— **均未 push，push 需 owner 明示授权** |

### 第一批的关键裁定（做第三批时要保持一致）

- 五个仓位词（空仓/试错/加仓/推仓/锁仓）是**作者原词枚举**，作行为语言，不建状态机。
- **锁仓四义**：规则内持有 / 被迫滞留（作者原词「被迫锁仓」）/ 承诺・执念驱动 / 主力锁仓（逐出仓位框架归 X1）。登记必须带驱动来源标签。
- **推仓位＝退仓位＝相对常规开仓量提高暴露**（词条已消歧，不要再逐条写「ASR 不可消歧」）；「全军出击」是叙事驱动的极值，单独作反例。
- **D1 = 禁亏损补仓 + 禁浮盈加仓**，是 Base 第一内核；条件例外已存档、当前不启用，但卡面要写出来。
- **卡 5 五层分账**：事前规则 / 触发是否成立 / 作者声称动作 / 是否按规则执行 / 真实回单。永远不能用「说过」冒充「做过」。
- 仓位百分比**全部不冻结**（作者 2026-08-19 已宣布不再公布仓位配比，分母未来也补不齐）。

---

## 8. 全程强制约束

- 禁生产战法、确定性状态机、收益择优、回测优化；**永禁真实券商程序化下单**。
- 复刻忠实度先于回测表现；博主 Base 层 / 目标系统增强层 / owner 方向层三层分开，禁止倒灌。
- 卡片不伪造数值：语料没有的阈值一律标「未冻结」。
- 工件 append-only；不覆盖旧研究判断；新证据只追加 observation / hypothesis revision / case / event / worklog。
- HERO 反过度防御：不加评分表、机械清单、无用校验和、为想象需求预建的框架。判断句：「这能检测到什么具体故障，我会因此做出什么不同的决定？」答不上来就不写。
- 秘密只在 `~/.bashrc`；gitleaks pre-commit 已装，严禁 `--no-verify`。
- 代码任务 commit 前必过 codex review（`codex review --uncommitted`，超时回退 `/code-review high`）；docs-only 豁免。
- commit 落本地，**push 需 owner 明示授权**；回复 owner 用中文，代码/注释/commit 用英文。

## 9. 明确不要做的事

1. 不要重做第一批或改写已定稿的卡片语义（要改就写新版本文件并说明理由）。
2. 不要推进 M3-A 命中率批次。
3. 不要为了让 520 回测好看而调参数或改语义。
4. 不要用 `observations/*.json` 通配统计（会数到 1135 并读到过时旧版）。
5. 不要从 transcript 的 `text` 字段取引用。
6. 不要在第三批交付前进 M3。
