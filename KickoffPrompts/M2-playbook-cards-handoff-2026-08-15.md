# M2 战法卡片提炼接手说明：主线已拉回"先复刻交易系统"，从 Base v1 出卡片供 owner 逐张确认

> 日期：2026-08-15
>
> 工作目录：`/home/ps/papers/QuantMind`
>
> 分支：`agent/m2-evidence-reconstruction`（owner 2026-08-14 已授权本分支 commit & push）
>
> 唯一恢复点：**`M2-playbook-cards-extraction`（战法卡片提炼第一批：总体仓位框架 + 6 张核心卡片，产出 owner 逐张确认稿）。**

## 1. 主线次序（owner 2026-08-15 重申，最高优先）

**先复刻「全能的野人」的交易系统、交易逻辑、操作手法（战法卡片 + 总体仓位状态框架，逐张 owner 确认），再做系统性优化。考察博主预测精度不是目标；发言命中率统计只是辅助验证之一，排在复刻与确认之后，不得抢主线。**

该次序已写入 `CLAUDE.md`（新系统四块 §2 下）与 `AGENTS.md`（新增"主线次序"节）。行动计划书 §四 的原始次序与本条一致：第一步提炼（卡片+框架，owner 逐张过目确认）→ 第二步验证（转确定性规则回测）→ 第三步固化。上一 session 曾把第二步的辅助项（预测命中率统计）提前当主线推进（M3-A），已按 owner 指示纠正。

## 2. 当前状态（已建成，不要重做）

- **M1 语料管线**：完成，1088 条 metadata + 转写（`data/yeren_corpus/`）。
- **M2 阶段 C 全量证据研究**：收口。observation 工件 1112（唯一 aweme 1088/1088）、hypothesis 341 条（32 家族）、bundle 532、case 122、event 90；casebook 十一轮综合 + 全语料冲突审查完成（`docs/research/yeren-system/casebook.md`）。
- **Base v1 G2 草案**：`docs/research/yeren-system/base-v1-spec-g2-draft-2026-08-15.md`（三层分层；七维语义冻结、参数归 M3；§六 G2 六问待 owner 逐项裁决）。**它是语义骨架，不是卡片交付物。**
- **M3-A 预测登记表/引擎**：`scripts/yeren_research/prediction.py` + `data/yeren_research/predictions.jsonl`（3 条 pilot 记录）。代码保留（52 tests 全过，可用作日后辅助验证），**批次推进暂停，不占主线**。
- worklog 尾条：`M3-A-batch-001 completed → resume_from=M3-A-batch-002`；**接手第一步先追加方向纠正条目并把恢复点改为 `M2-playbook-cards-extraction`**（见 §6 命令）。
- 已 push：`2484d0b`、`5b7f383`、`9378d46`、`be2ae7e`、`7e04e82`（旧 M3-A handoff，本文件取代它）。

## 3. 主线工作清单：战法卡片提炼（接手第一步）

产出物 = **战法卡片确认稿**（新文件 `docs/research/yeren-system/playbook-cards-draft-2026-08-15.md`），结构：

1. **总体交易框架**：仓位状态机「空仓 → 试错 → 加仓 → 锁仓 → 推仓」（计划书 §四 点名的框架雏形）+ 状态间迁移条件 + 每个状态的语料证据与反例（空仓=模式失效/H-CAPITAL-FIRST、试错=拐点轻仓/H-REENTRY-LIGHT-TRIAL、加仓=失败撤回/H-POSITION-CONVICTION、锁仓=8月10—12日锁仓叙事、推仓=8月12日满仓极值）。
2. **战法卡片**，每张固定六段（计划书格式）：
   - 名称；适用市况；入场条件；加减仓与退出规则；仓位约束；原话引用与视频出处。
   - 附：证据分类（stable_core/candidate/playbook_special_case，来自 hypotheses.jsonl 家族终态）与**未冻结参数**（禁止伪造数值）。
3. **第一批卡片清单**（6 张核心，从 Base v1 草案七维语义还原）：
   ① 右侧波段入场（候选—激活—回踩，H-RIGHT-SIDE-TREND）；② 分歧切核心 / 弱势抱团（H-MARKET-CHAOS-RETREAT）；③ 红卖绿买修复轮动（H-THEME-CONTINUATION 轮动节奏）；④ 禁亏损补仓 + 被套反弹减半（stable_core 对）；⑤ 事前锁定退出触发器（H-TRADING-HORIZON-LOCK，时间窗×点位）；⑥ 混沌—退潮—恐慌盘防守链（H-MARKET-CHAOS-RETREAT）。
   第二批起补：事件首入口、分层退出、套利/波段双仓、财报预期差、兑现窗口、ETF 表达、直接点名利空退出、圆弧底 75a（只进 ontology）等。
4. **原话出处纪律**：引用一律由 `sentences[a:b+1]` 无分隔拼接自动生成（evidence span），**绝不手打 raw_text**；每处引用带 aweme_id + statement_id/interpretation_id。已核实的引用素材见 §7。
5. **owner 逐张确认流程**：第一批 1 框架 + 6 卡片写完 → worklog 写 `resume_from=M2-playbook-cards-owner-review-1` → 交 owner 过目 → 按确认/修订意见更新卡片（append-only，修订=新行或新版本号）→ 继续下一批。确认过的卡片才进入"转确定性规则回测"（G2 六问答复后）。

## 4. 约束（全程强制）

- G2 前禁生产战法、确定性状态机、收益择优、回测优化和任何真实券商程序化下单；卡片只是文档，不写 `backend/playbook/` 代码。
- 复刻忠实度先于回测表现：禁止为了让某战法"更好回测"而改写语义；博主 Base 层、目标系统增强层、owner 方向层三层分开（见 Base v1 草案 §一）。
- 卡片不伪造数值：语料没有的阈值（均线参数、"30%""10%"的基准、仓位百分比）一律标"未冻结"归 M3 验证。
- HERO 反过度防御：不加评分表/机械清单；判断句"这能检测到什么具体故障,我会因此做出什么不同的决定"。
- 工件 append-only；`write_new_json` 拒绝覆盖；回复 owner 中文，代码/注释/commit 英文。

## 5. 恢复命令

```bash
cd /home/ps/papers/QuantMind
git status -sb && git branch --show-current
tail -1 data/yeren_research/worklog.jsonl | jq -c '{work_unit,status,resume_from}'
# 接手后先追加方向纠正条目:work_unit=M2-course-correction-2026-08-15, status=completed,
# resume_from=M2-playbook-cards-extraction (内容:主线次序确认/CLAUDE.md+AGENTS.md 已改/M3-A 暂停)

# 素材:32 家族终态(卡片来源)
/home/ps/anaconda3/envs/zhanglan/bin/python - <<'PY'
import json, re
from pathlib import Path
lines = [json.loads(l) for l in Path("data/yeren_research/hypotheses.jsonl").open()]
fam = {}
for r in lines:
    m = re.match(r"(H-[A-Z0-9-]+)-\d+", r["hypothesis_id"])
    if m: fam.setdefault(m.group(1), []).append(r)
def rev(r):
    m = re.search(r"R(\d+)$", r["hypothesis_id"])
    return int(m.group(1)) if m else 0
for k in sorted(fam):
    latest = max(fam[k], key=rev)
    print(latest["hypothesis_id"], latest["classification"])
    print(" ", latest["rule_text"][:200])
PY

# 全套测试(引擎保持可用,但不推 M3-A 批次)
FEISHU_INTERACTIVE_ENABLED=false /home/ps/anaconda3/envs/zhanglan/bin/pytest -q tests/yeren_research/
# 应为 52 passed
/home/ps/anaconda3/envs/zhanglan/bin/ruff check scripts/yeren_research/ tests/yeren_research/
```

## 6. 已核实的引用素材（本 session 从转写逐字提取，可直接用于卡片）

| 素材 | 原话（节选,以 span 拼接为准） | aweme |
|---|---|---|
| 右侧入场 | "当一个个股在底部,红盘伴随着量价移动的五日线上穿二十日线的时候…拿住不要着急…追高割肉都是你亏损的根源" | 7513455544188538164（2025-06-08） |
| 分歧切核心 | "两句话分歧的时候切核心弱势行情,切抱团" | 7518988702334405922（2025-06-23） |
| 大赚离场 | "大赚的时候要离场,大亏的时候要进场,要管住手…如果明天卖飞了也不要急着进去" | 7519412556550475043（2025-06-24） |
| 恐慌盘止跌 | "混沌期它就它就会杀透…没有恐慌盘出来,他是不会止跌的…管住手,第二个控制好仓位" | 7517573542352129332（2025-06-19） |
| 弱市防守 | "大盘这几天在震荡期间啊大家还是要管住手或者说铆定主线去做其他的杂毛就不要碰了" | 7516818145589005602（2025-06-17） |
| 卖点系统化 | "你如果没有稳定之前啊,那你比如说到了百分之三十啊,我无条件减仓百分之三十或者减仓一半" | 7672786452422015823（2025-08-11晚,序号1082） |
| 被套减半 | "如果我是你被套的啊如果说现在后面的反弹啊没反弹嗯百分之十我会减一半的仓位出来…等到确定信息" | 7671390801470395112（序号1058,2025-08-08） |
| 锁仓触发器 | "锁仓两天+周四五不摸4000则减仓"（statement `693135-statement-lockup-two-days-exit-trigger`） | 7673039516600693135（序号1085,2025-08-12） |
| 红周必跌候选 | "红盘三至四天不上攻则红周必跌"（statement `693135-statement-red-days-decline-rule`） | 7673039516600693135 |
| 反弹排序 | 高位科技反弹进度表（CPO约两成/PCB两三成/光纤已过/存储未反弹）→"先弹的结束、补涨的最后结束"；"存储也是最后一个补长反弹的需求就结束了" | 7672416505577862755（1077）/7672786452422015823（1082） |
| 干净交易 | "只有B和S"；做 T 四类故障（猜最低最高点/T+1尾部/主升浪丢仓/下降趋势被动满仓） | H-CLEAN-TRADE-001（2025-08-06 语境） |
| 首入口窗口 | 首日未参与不在次日一致区追价,等第一次可交易分歧中的核心(雅江案例族) | H-FIRST-ENTRY-WINDOW-001 |

引用的权威来源永远是 observation 工件（`data/yeren_research/observations/<aid>.json` 的 evidence/statement/interpretation），上表只是索引。

## 7. 工具与坑

- **证据 span 拼接**（卡片引用的唯一合法来源）：`by_id[eid]["transcript_span"]` → `"".join(rows[s["sentence_index"]:s["end_sentence_index"]+1]["text"])`；示例脚本见旧 session scratchpad `/tmp/claude-1000/-home-ps-papers-QuantMind/*/scratchpad/m3a/make_predictions_001.py` 的 `raw_statement_text`（若丢失按 §6 逻辑重建）。
- casebook 十一轮（`docs/research/yeren-system/casebook.md`）与 Base v1 草案是卡片语义的两个上游；卡片必须能回溯到其中至少一处 + 至少一处原话 span。
- codex CLI 会挂起（`codex review --uncommitted` 280s 无输出）→ 回退 `/code-review high`；docs-only commit 豁免 review。
- 全量 pytest 超时（>2min），只跑 `tests/yeren_research/`，必须带 `FEISHU_INTERACTIVE_ENABLED=false`。
- PIT 日线覆盖至 20260813；卡片引用行情反馈时不得越过该边界。

## 8. 完成后去向

第一批卡片 owner 逐张确认后：继续第二批卡片 → 全部确认完 → 待 owner 答复 G2 六问 → 转确定性规则 + Tushare 回测（真赚钱+回撤可接受+非运气）→ 过门 validated → M4 固化 `backend/playbook/`。系统性优化排在复刻完成之后；发言命中率统计只在优化阶段作辅助验证之一。
