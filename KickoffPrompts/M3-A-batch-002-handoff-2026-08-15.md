# M3-A batch-002 接手说明：预测登记表与结算引擎已建成，pilot batch-001 已封板

> 日期：2026-08-15
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`（owner 2026-08-14 已授权本分支 commit & push）
>
> 唯一恢复点：**`M3-A-batch-002`（metadata 固定时间序第 26—50 条，aweme `7519729010668080419` 起，2025-06-25 11:49:12）。**

## 1. 总目标与当前状态

M1 语料管线（1088 条）与 M2 阶段 C（全语料证据研究）已完成；Base v1 G2 草案已提交（`docs/research/yeren-system/base-v1-spec-g2-draft-2026-08-15.md`，§六 G2 六问待 owner 逐项裁决）；owner 2026-08-15 两次令"继续推进"→ **M3 验证已开工**。

M3 分三步：

- **M3-A 发言命中率统计**（当前）：把他每天复盘的观点按发布时间对齐行情，统计可证伪方向预测的命中率——先验证"这个人值不值得学"。按阶段 C 批次推进 batch-001 至 batch-044。
- **M3-B 假想执行收益**：对可结算方向判断做模拟执行 P&L（M3-A 全量后）。
- **M3-C 战法确定性规则化 + Tushare 回测**：每招灵不灵；**须 Base v1 经 owner 确认后才开工**。

worklog 尾条：`M3-A-batch-001 completed → resume_from=M3-A-batch-002`（`data/yeren_research/worklog.jsonl`）。

已 push：`2484d0b`（batch-044 封板+综合）、`5b7f383`（Base v1 G2 草案）、`9378d46`（owner 清理 handoff）、`be2ae7e`（M3-A 引擎+pilot）。

## 2. 已建成且不要重做

- **登记表**：`data/yeren_research/predictions.jsonl`（append-only，不入 Git；现 3 条 pilot 记录，全部 miss，判决已与新引擎复算一致）。
- **结算引擎**：`scripts/yeren_research/prediction.py`（151 行）。`schema.py` 新增 `PredictionRecord`/`PredictionDirection`/`SettlementKind`/`PredictionVerdict`/`MarketSettlement`。
- **测试**：`tests/yeren_research/test_prediction.py`（32 项；全套 `tests/yeren_research/` 52 passed）。已经 `/code-review high` 复核，10 条 findings 全部修复并由测试锁定。
- **Pilot 报告**：`docs/research/yeren-system/m3a-prediction-hitrate-pilot-2026-08-15.md`（方法/登记边界/引擎加固/局限全文）。

### 结算合同（冻结于模块常量，禁止改动语义）

| 合同 | 判定 |
|---|---|
| `BREADTH` UP/DOWN | 涨跌家数多数方向一致即 hit |
| `MEDIAN` UP/DOWN | 全市场 pct_chg 中位符号一致即 hit |
| `BREADTH_MEDIAN` | 广度与中位同时一致才 hit；任一分项不可判定则整体 unsettled |
| `VOLUME_DELTA` UP/DOWN | 成交额较前一交易日增/减；`math.isclose(rel_tol=1e-9)` 相等判 tie |
| `FLAT` on `BREADTH` | \|涨-跌\| / 实际可交易行数 ≤ 0.10 判 hit |

引擎强制（不需提取者操心）：窗口日收盘（15:00 Asia/Shanghai）早于发布时刻 → 结算直接 `ValueError`（防前视）；多日窗口 → unsettled + 注明无聚合合同；pct_chg 全 NaN 日 → 无任何 verdict；手动类（`EVENT_FACT`/`SECURITY_CLOSE`/`NOT_SETTLEABLE`）与条件预测（`branch_trigger`）永不自动判定；重结算不覆盖 analyst rationale；批量用 `settle_records`（共享一次 store 与交易日历）。

## 3. batch-002 工作清单（接手第一步）

1. **恢复验证**（见 §6 命令）。
2. **导出前向语句**：对 `meta[25:50]` 的 25 条 observation，导出 tense ∈ {future, conditional} 的 statement（含 evidence span 逐字原文 + interpretation 摘要），用 §7 的 dump 片段。
3. **逐条人工判定**（主 agent 亲自，不批量摘要）：
   - 可登记：方向可判（up/down/flat）、对象可默认到全市场、窗口可定位（默认发布日下一交易日）。
   - **不登记**：愿望/安抚（"希望下午收复"）；教学规则与条件叙事（"分歧切核心""恐慌盘出来才止跌"）；对象只到题材/证券而 PIT 无对应观测的（theme/security 类无证券池→不登记，或 `NOT_SETTLEABLE` 只登记不判定——按 pilot 纪律，不可结算的**不写进登记表**，留在 observation）；"看不懂"类明确无预测。
   - 同日多条同向判断来自不同视频的，分别登记不合并。
4. **写 `make_predictions_002.py`**（放 session scratchpad，模式见 §7）：`claim_text` 一律由 `sentences[a:b+1]` 无分隔拼接自动生成，绝不手打；`prediction_id = pred-<aweme>-<n>`；`recorded_at` 用当前时刻；先检查 predictions.jsonl 已有 id 拒绝覆盖。
5. **结算并追加**：`settle_market(record, PIT)` 后逐行 append；结算后把 `trade_date` 的 observables 与 PIT 独立复算比对一次（`market_observables` 直读再打印）。
6. **批级统计**：`hit_rate_stats` 跑本批 + 累计（hit/miss/tie/unsettled/beyond_coverage 六项计数与两个命中率口径）。
7. **worklog 追加** `M3-A-batch-002` 条目（status=completed，resume_from=M3-A-batch-003，findings 写本批逐条判定+边界披露），模式照抄 `append_worklog_m3a001.py`。
8. **每完成一批 commit 一次**（若只有数据变化则只 worklog/数据 append；引擎或文档有改动才动 git）。**不需要每批写报告文档**——按时代分层报告在全部批次完成后出（pilot 报告 §七）。

批次对齐：batch-002 = metadata 序号 26—50；batch-003 = 51—75；…… batch-044 = 1076—1088（13 条）。阶段 C 的 `data/yeren_research/observations/` 是提取的权威来源，直接复用，不再读转写全文（除非要核对歧义）。

## 4. 约束（G2 前继续生效）

- 禁生产战法、确定性状态机、收益择优、回测优化和任何真实券商程序化下单；M3-A 只做预测登记与命中率统计，M3-B 假想执行收益在其后。
- 登记表/工件 append-only；`write_new_json` 拒绝覆盖；不修改既有预测行（修订=新行）。
- HERO 反过度防御：不加无用途校验；命中率分母只收真可结算的窄口径，不用代理观测量制造伪精确；判断句"这能检测到什么具体故障,我会因此做出什么不同决定"。
- 回复 owner 用中文；代码/注释/commit 英文；conventional commits；秘密只放 `~/.bashrc`。

## 5. 恢复命令

```bash
cd /home/ps/papers/QuantMind
git status -sb && git branch --show-current
tail -1 data/yeren_research/worklog.jsonl | jq -c '{work_unit,status,resume_from}'
# 应为 M3-A-batch-001 / completed / M3-A-batch-002

# 登记表现状
wc -l data/yeren_research/predictions.jsonl && jq -c '{id:.prediction_id,verdict}' data/yeren_research/predictions.jsonl
# 应为 3 行:pred-7512369013927857442-1 miss / pred-7512468025557650740-1 miss / pred-7513844279061990696-1 miss

# 全套测试
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/pytest -q tests/yeren_research/
# 应为 52 passed
/home/ps/anaconda3/envs/zhanglan/bin/ruff check scripts/yeren_research/ tests/yeren_research/
# All checks passed
```

## 6. 工具与坑

- **前向语句 dump 片段**（batch-001 用过的，直接改 `meta[:25]` 为 `meta[25:50]`）：

```python
import json, sys
sys.path.insert(0, ".")
from pathlib import Path
from scripts.yeren_research.inventory import read_jsonl
meta = list(read_jsonl(Path("data/yeren_corpus/metadata.jsonl")))
out = []
for row in meta[25:50]:
    aid = row["aweme_id"]
    obs = json.loads(Path(f"data/yeren_research/observations/{aid}.json").read_text())
    tp = Path(f"data/yeren_corpus/transcripts/{aid}.json")
    rows = json.loads(tp.read_text())["sentences"] if tp.exists() else []
    by_id = {e["evidence_id"]: e for e in obs.get("evidence", [])}
    out.append(f"=== [{row.get('published_at','?')}] {aid} title={obs.get('title','')[:40]}")
    for st in obs.get("statements", []):
        if st["tense"] not in ("future", "conditional"):
            continue
        texts = []
        for eid in st["evidence_ids"]:
            ev = by_id.get(eid)
            if ev and ev.get("transcript_span"):
                s = ev["transcript_span"]
                texts.append("".join(r["text"] for r in rows[s["sentence_index"]:s["end_sentence_index"]+1]))
        out.append(f"  [{st['statement_id']}] {st['statement_type']}:{st['tense']} dir={st['action_direction']} cond={str(st.get('condition'))[:60]}")
        out.append(f"      TEXT: {' / '.join(texts)[:160]}")
    for it in obs.get("interpretations", []):
        out.append(f"  INTERP: {it['interpretation_id']} | {it['text'][:100]}")
print("\n".join(out))
```

- **登记与追加模式**：`/tmp/claude-1000/-home-ps-papers-QuantMind/<session-scratchpad>/scratchpad/m3a/make_predictions_001.py`（若 scratchpad 丢失按此文件逻辑重建：`raw_statement_text` 用 span 拼接；`settle_market`；append 前检查 prediction_id 不重复）。
- **防前视已被引擎强制**：若 settle 时报 `ValueError: lookahead window ...`，说明窗口写错了（发布日收盘前）——改窗口重跑，不绕过。
- 多日窗口目前无聚合合同：**提取时优先把窗口收到单交易日**；真正多日的按 unsettled 留档并在 findings 注明。
- 量能合同的 `prev_trade_date` 字段会显示实际使用的前一存档日——若该日与日历前一交易日不符（存档缺口），在 findings 披露，不修数据。
- codex CLI 本 session 挂起（`codex review --uncommitted` 280s 无输出）→ 按老规矩回退 `/code-review high`；docs-only commit 豁免 review。
- 全量 pytest 会超时（>2min），只跑 `tests/yeren_research/`；pytest 必须带 `FEISHU_INTERACTIVE_ENABLED=false`。
- PIT 日线覆盖至 20260813；2026-08-13 之后的窗口（如宇树上市日、锁仓触发第二日）结算会 BEYOND_COVERAGE——如实留档，不回补。

## 7. 完成后去向

M3-A batch-002..044 全部完成后：按时代/对象/方向分层出命中率总报告 → M3-B 假想执行收益（可结算方向的模拟执行，先出方案）→ M3-C 战法规则化+回测（**等 owner 确认 Base v1 G2 草案后才开工**）。G2 六问（草案 §六）owner 未逐项答复，推进按草案默认值，owner 可随时修订。
