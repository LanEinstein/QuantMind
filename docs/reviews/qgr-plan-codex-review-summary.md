# QGR 重做方案 · codex 方法学评审 summary

> **日期**:2026-06-21 · **被审**:`docs/research/quant-first-gate-rearch-plan-2026-06-21.md`(量化第一闸门重做方案)
> **审者**:codex `codex exec --sandbox read-only`(codex-cli 0.141.0;外部核验 NBER 日动量 / Dwork reusable holdout / Hardt-Ullman / 2024 北向披露变更)
> **裁定**:**REVISE**(reframe 方向正确,但 1 P0 + 7 P1 须在 owner 签字前钉死)→ **全部采纳,已定稿于方案 §4.4**

## 总评(codex 原话精炼)
pivot 方向正确:量化作"第一闸门选股过滤器"优于 benchmark-relative 增强指数;对 ≤5 long-only 选股器,去掉"必须跑赢 CSI300"硬门是 sound 的,CSI300 仅作披露 + regime/机会成本背景。

## 处置表(finding → 定稿)
| 严重度 | finding | 定稿处置(§4.4) |
|--------|---------|-----------------|
| **P0** | legacy mining 债不可清零:改判据不重置 data-mining 债;须预置 legacy trial 块,DSR/MinBTL 用 `max(legacy_N, ONC有效N)` 非零 | ✅ 账本预置 R1-R4 名义网格+诊断+消融+符号检验+4 次 test 读;`max(legacy_N, ONC)` |
| **P1** | "可复用"措辞过强(无限免费)→ DSR/SPA/PBO 只惩罚复用不让免费 | ✅ 改"有限、记账、对自适应复用计惩罚" |
| **P1** | 事件循环回测排除 LLM 辩论/全 RiskEngine/Line-2 → 是量化 proxy 非全系统验证;go-live 须真管线 shadow replay | ✅ 标注"量化机制 proxy",go-live 须 45 日 shadow replay |
| **P1** | `walk_forward_eval.py` 报 held-out combinations 非 stitched CPCV paths;重叠路径不当独立样本 | ✅ QGR-2 实现真 CPCV 路径拼接或改名;不当独立喂 DSR |
| **P1** | PBO 非通用 p<.05 门(是 search-overfit 诊断,按真实选择规则算);DSR 须自相关校正 SR 方差(重叠持仓);SPA/Romano-Wolf 须预声明 family + 时序 bootstrap | ✅ protocol 全收紧(HAC/Newey-West;预声明 family;block bootstrap) |
| **P1** | 绝对 P&L 可成新 long-beta 陷阱 → 须可部署 baseline 面板甄别技艺 | ✅ baseline = 随机 top-5/现役 screener/纯流动性/ETF-only/CSI300-ETF;须稳定击败 |
| **P1** | 主指标未冻 → QGR-3/4 前须冻 | ✅ 主=事件循环净 P&L/效用+MDD+换手约束;precision@K/rank-IC 仅诊断 |
| **P1** | ETF 不能留 open → 分轨或仅预算 fallback | ✅ ETF 单独 lane,不混入个股截面 |
| **P2** | 陷阱补:停复牌/复权泄漏/低价壳彩票/同日 limit_list_d 误用 | ✅ §3.6 补 4 行 |
| **P2** | 前向"20-40 期"过松 → 定义期 + 预注册最小观测 + alpha-spending | ✅ 非重叠完整 5td bet 为独立观测 + alpha-spending |
| **P2** | QGR 排序对,但摄取须 coverage-only,先冻 metrics/账本再看因子结果 | ✅ QGR-1 coverage-only |

## codex 收尾原话
"proceed with the reframe, but do not owner-sign the current draft until legacy trial accounting, metric freeze, ETF treatment, and the 'quant proxy vs full live system' boundary are made explicit." —— 这四项均已在 §4.4 explicit 化。

## 复审状态
本轮为方法学评审(非代码);方案为 docs-only(无生产代码)。QGR-2 起的实现代码任务,commit 前各自再走 codex 代码评审前置门(撞额度→/code-review high,[[feedback_codex_rate_limit_fallback]])。
