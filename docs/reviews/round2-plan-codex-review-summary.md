# 第 2 轮方案 codex 对抗评审 summary(2026-06-18)

> 评审对象:`docs/research/factor-strategy-round2-plan-2026-06-18.md`(书面方案,非代码)。
> 评审器:codex-cli 0.137.0 / gpt-5.5 / reasoning effort xhigh / sandbox read-only。
> 协议:第 2 轮 kickoff §E 阶段 2(方案 → codex 对抗 → 修完 P0/P1/P2 → 定稿 → 执行)。
> 调用:`codex exec --sandbox read-only --skip-git-repo-check "$(cat /tmp/codex_round2_plan_review.txt)" </dev/null`
> (前台 + `</dev/null` 防 stdin deadlock;prompt 作位置参数避免 `</dev/null` 清空 heredoc。)

## 总判
**无 P0。** 大方向成立:现有 2015-2026-06 数据无真处子 OOS;CPCV/walk-forward 在方案里明确只是开发证据非 PASS/FAIL;四门判据未被 IR/TE 替换;参考臂明确排除在 PASS 判定外。
**5 P1 + 3 P2 已全部在方案稿修订。** codex 总评:"方案方向可以,无 P0,但 P1 影响诚实性/可审计性,先修方案再执行。"

## P1(HIGH/应修)— 全部已修

1. **前向窗口"选时/摄取时间"漏洞** — 摄取时间可人为延后 ≠ 数据存在时间;窗口长度/判定时机不该等到判定时才定。
   → 修:§2.3 firewall 改钉**数据存在/公开时间 > freeze commit UTC**,排除冻结前已公开日(含 2026-06-13..冻结);冻结时**预声明**窗口长度(120/250td 二选一)+ 触发规则,写进 lock firewall 段。

2. **多重检验 N 未覆盖全自由度;"单假设"过强** — 两臂/因子族/变换/中性化/rebalance/tilt/a_max/TE/lag/成本网格/选择指标/tie-break/失败变体都是 DoF。
   → 修:§5 R2-4 前冻结 `experiment_manifest.json`(全自由度含失败+人工中止变体);DSR/PBO 用累计 N;软化"单假设"为"N 选 1 的胜者,须 deflate";参考臂若影响主臂选择须计入 N + 预声明交互规则。

3. **PIT 不够硬,尤其 `fina_indicator` ann_date ≠ 全 PIT** — 重述/修订版本会后验污染;`index_weight` 当日公布当日用有前视;行业不能用最新分类 backfill。
   → 修:§4.1 `index_weight` 加可得性 lag(≤d−1 或 release<开盘);`fina_indicator` 优先首次公告 vintage,只返修订值则降级/剔除 + vintage 审计;行业记录 effective/release 规则。

4. **成本/容量搜索期低估;判定引擎应为主口径** — 买卖应分开计,搜索期就要保守;portfolio-sort 只能是开发近似。
   → 修:§3.2(4) 买卖分拆 `Σmax(±Δw,0)` 各套真实费率(含佣金底/印花/过户/分板块滑点/整手/涨跌停·T+1 拒单/容量冲击),搜索期即用;§5 全引擎=R2-6 判定主口径,rqalpha 差分>25bps fail-closed。

5. **幸存无偏只是原则非验收标准** — 须可校验,禁从当前在市域派生财报/行业覆盖。
   → 修:§4.1 R2-1 加 coverage manifest fail-closed:`fina_indicator` 按 `SurvivorshipUniverse.all_codes()`(在市+退市)枚举;`index_weight` 覆盖历史调入/调出;行业每 code-date 给 PIT 行业或显式 unknown→fail-closed(剔出,不臆造)。

## P2(MEDIUM/建议)— 全部已修

1. **中性化 + long-only clip 后主动暴露未保证净零** → §3.2(3) 约束后再投影使主动腿净零 + 披露 residual beta/行业/市值 active/实现 TE。
2. **"限非 SOE"无 PIT 所有制数据** → §4.2 删除该条件(避免 current SOE 标签前视;要做须先补 PIT 所有制并计入 N)。
3. **投资域与 CSI300 成分可能不一致** → §3.2(3) 被排除成分名强制持 0=满额低配 active,计入 TE 单列披露;过大则截基准到可投资交集。

## 结论
方案已逐条修订(对照表见方案 §11),**定稿**,待 owner 批准后进 R2-1(PIT 数据扩充摄取)执行。
