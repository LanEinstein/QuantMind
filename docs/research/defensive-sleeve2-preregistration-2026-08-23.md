# SLV-2 质量 sleeve 科学门预注册(MR-1,2026-08-23)

> 状态:**预注册,运行前提交**(commit 先于实现与运行;运行后本文件一字不改)。
> 上位:`KickoffPrompts/ACTION-PLAN-loss-avoidance-durable-return-2026-08-23.md` §4.1/§6(owner 2026-08-23 批准);
> 决策讨论:Codex 2026-08-23 两项均采纳主建议(宇宙原样复用/单因子 gpm),记录于本文件 §9。
> 判据、种子、算子、并列处理全部在此写死;失败即封存,不补救、不换定义、不重跑。
> `real_broker_orders = false`。sealed test 永不读。

---

## 1. 主张与角色

SLV-2 检验一个主张:**在已验证的防御宇宙(D1 gates)与 buf40_5 现金 buffer 之内,
以"毛利率(gpm)最高的 5 只"替代"股息率最高的 5 只"作选择,是一条独立于 SLV-1 的
可部署风险产品腿**——判据要求它不但具备 SLV-1 同级的风险性质(净盈/熊市/回撤),
还必须**胜过同宇宙随机选择**(否则"质量选择"没有存在价值,R 层维持单腿即可)。

与 SLV-1 的区别声明:SLV-1 的科学门是纯风险主张(placebo 只披露不作门);
SLV-2 的存在理由是**选择本身**,因此 placebo 判据升格为硬门(计划书 §6 已承诺)。

## 2. 冻结的构造(spec 常量,实现于 `defensive_sleeve2_spec.py`)

| 项 | 值 | 来源 |
|---|---|---|
| product | `defensive_sleeve2_v1` | 新 |
| 宇宙过滤 | **D1 gates 原样复用**(彩票顶 decile 剔 / ROE>0 / GPM 非底 decile / dv_ratio ≥ 当日中位 / 四件套排除 / 涨跌停不可成交剔 / 底部 30% 市值剔),drift-guard 到 `defensive_d1_spec.UNIVERSE_FILTERS`,一个参数不改 | Codex 决策 1 |
| **SLV-1 书排除**(重叠纪律) | 每个调仓日,在过 gates 且 `dv_ratio` 有限的名单内,按 (`dv_ratio` 降序, `ts_code` 升序) 取前 5 = 该日 SLV-1 委托书,**从 SLV-2 候选中剔除**;两书按构造持仓不相交 | 计划书 §4.1 |
| 选择规则 | 剔除后按**原始 `gpm` 降序取前 5,等权**;并列以 `ts_code` 升序打破;`gpm` 非有限值的行先删 | Codex 决策 2 |
| 容器 | `buf40_5`(5 槽 × 8% cap ≈ 40% 总敞口 / 60% 现金),与 SLV-1 逐字段一致 | 复用 |
| 调仓/持有 | 20 交易日(月度),HORIZON=20 | 复用 |
| 初始资金 | ¥1,000,000 | 复用 |
| 分析师 tilt | 无(不引入) | — |
| 行业中性化 | **无**(gpm 的行业集中如实接受并披露,与 SLV-1 接受 dv_ratio 集中同理) | Codex 决策 2 |

spec 以 canonical-JSON SHA256 冻结(`defensive_sleeve2_spec.spec_hash()`),运行输出必须回显。

## 3. 数据与窗口(与 SLV-1 科学门逐项相同)

- 面板:`data/factor_research/panel_train_val_defensive_d1.csv`(train_val only,
  2015-02→2025-04,125 个调仓日;**不重建、不扩窗**)。
- 切分锁:`config/research/test_set_lock.json`(`LockedSplit`);实际读 bar 窗口断言 ⊆ train_val ∪ embargo;
  sealed test 永不读。
- 行情:`data/marketdata_pit/` PIT 字节档,`run_gate_backtest` 冻结事件循环
  (T+1、分板块滑点、¥5 佣金、20d 月度),与 SLV-1 完全同一装置。
- 宽度 fail-closed:任一调仓日剔除 SLV-1 书后候选 < 10(2×top_n)即中止报错,不静默缩槽。

## 4. 运行的臂(共 6 条)

| 臂 | 内容 | 角色 |
|---|---|---|
| `sleeve2_buf40_5` | gpm top-5 eq,buf40_5 | **被判定的部署臂** |
| `sleeve2_eq_5` | gpm top-5 eq,满仓(5×100%) | naive 基线(未来前向 kill-switch 的跟踪对象)+ 无 buffer MDD 披露 |
| `placebo_random_buf40_5` | **剔除 SLV-1 书后的同一候选池**内随机 top-5,buf40_5,seed 见 §6 | **硬门对照** |
| `placebo_sizematched_buf40_5` | 同池、按 log_circ_mv 贪心匹配 sleeve2 选中者的 size 对照,buf40_5 | 披露(size 归因) |
| `sleeve1_buf40_5_disclosure` | dv_ratio top-5 eq,buf40_5(同装置重算 SLV-1 书) | **仅披露**:与 sleeve2 的期收益相关性、持仓/行业重叠;不参与任何判据 |
| `csi300_hold` | 510300.SH 买入持有 | beta 门披露 |

## 5. 判据(四项全过才 PASS;算子逐字冻结)

对 `sleeve2_buf40_5`:

1. `net_pnl_yuan > 0`(严格大于);
2. 熊市累计收益 `bear_regime_cumulative >= 0` 且 `bear_regime_n > 0`
   (regime 划分 = `_classify_regimes(csi300_hold 期收益)`,与 SLV-1 同函数);
3. `max_drawdown_pct <= 0.20`(**硬门**,非披露;SLV-1 实测 19.58% 说明该上界可达);
4. 与 `placebo_random_buf40_5` 的期收益配对 t(`_paired_t`,同 SLV-1 函数)`t >= 2.0`。

任一不过 → **FAIL,封存**。四项判定不设任何"接近就算过"的裁量。

**只披露、不作门**:DSR(HAC,非清零账本 n_trials 通缩)、SPA、Romano-Wolf、
vs sizematched t、股灾切片表、行业集中度、sleeve1 相关性/重叠、各臂守恒检查。

## 6. 随机性与统计口径(运行前写死)

- placebo seed = **20260823**(`random_top_n_scores` 的确定性 hash-score 机制,逐日取 top-5)。
- 配对 t:两臂对齐期收益(等长,机制保证),`_paired_t` 现行实现,不改。
- DSR 通缩 n_trials:`mfi_trial_ledger.jsonl` 非清零累计,family=`ds.defensive_sleeve2`,
  round_label=`slv2-science-gate`,ledger_date=`2026-08-23`,正式跑 persist=True。
- 结果 JSON 落 `data/factor_research/defensive_sleeve2_result.json`。

## 7. 运行纪律

- **正式运行一次**(全 125 期)。`--smoke-periods N` 的小窗冒烟仅用于工程自检
  (账本不落、结果不引用、不构成运行)。
- 正式运行必须在:本预注册 commit 之后 → 实现 + 测试全绿 + codex 一轮 review 修复完成之后。
  codex review 属工程审计,若其修复改变语义,以修复后代码跑**唯一一次**正式运行
  (与 M3 右侧波段 audit-induced rerun 同一先例,但本次 review 在运行前,不产生 rerun)。
- 运行后:任何数字不满意都不得改 spec/判据/种子重跑;探索性观察须标注非预注册。

## 8. 结果分支

- **PASS** → 产出结果报告 `defensive-sleeve2-science-gate-results-*.md`;
  下一步=**owner 门**:是否给 SLV-2 开独立前向注册(自己的 kill-switch,与 SLV-1 平行)。
  PASS 不自动进前向,不触碰 SLV-1 任何冻结物。
- **FAIL** → 封存为"研究候选/未通过",R 层维持 SLV-1 单腿;计划书 §9.3 分支生效;
  不换选择因子重来(那是新研究,须新预注册且须先说明为何证据结构变了)。
- **数据/工程故障**(fail-closed 报错)→ 修工程后重启运行不算第二次运行
  (判据未被读到的中止不消耗运行次数;已产出判据数字后不得再跑)。

## 9. 决策讨论记录(Codex,2026-08-23)

- 决策 1(宇宙):采纳主建议——D1 过滤器原样保留其已验证的回撤控制;持仓级正交性
  已由机械排除保证。备选(去掉红利门)被拒:未经验证的宇宙会让失败无法归因。
- 决策 2(选择):采纳主建议——原始 gpm 是最简单且证据最强的盈利质量规则,
  避免复合权重这一未验证的自由度。备选(gpm×低应计复合)被拒:宇宙 gates
  (ROE>0/GPM 非底/四件套)已内含部分应计防护。

## 10. 已知限制(诚实声明,运行前承认)

1. 两条 sleeve 同为 A 股多头,资产层相关性必然高;本研究只主张持仓/因子层正交,
   相关性数字如实披露(§4 sleeve1 披露臂)。
2. gpm 行业集中(白酒/软件/医药)不做中性化,集中度如实披露;若 PASS,
   集中风险由组合层的 5 槽×8% cap 与 60% 现金承接。
3. 质量溢价"温和"(外部证据),t≥2 的 placebo 硬门可能恰好不过——这正是硬门的目的:
   选择不胜随机就不值得为它开第二条腿。
4. 面板 gpm 来自 `FundamentalsPIT.asof`(ann_date 门控),与 SLV-1 同一 PIT 纪律;
   本研究不重审面板构建(D1 已审)。
