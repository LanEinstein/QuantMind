# Amendment:判据架构重构 —— dev = 选拔,前向 = 认证 + 产品分层(2026-07-04)

> **性质**:决策边界 amendment(CLAUDE.md §1.5:改决策边界先立 amendment 再动代码)。
> **授权**:owner dr.zhang 2026-07-04 对 `docs/handoff/fable5-strategic-assessment-2026-07-04.md` §5 三决定「**全部授权**」。
> **上位文档**:assessment(论证全文)+ `quant-first-gate-rearch-plan-2026-06-21.md`(两层评测框架,本 amendment 是其 promotion 判据的重定,不是推翻)+ 先例 `qgr-criterion-rebar-amendment-2026-06-27-avoid-top-dynamic-exit-swing.md`(弃 MDD≤8% 硬门,同为「门结构不可达」的判据重定)。

---

## §1 改什么(三决定)

### 决定 ①:promotion 判据重构 —— dev = 选拔,前向 = 认证

**依据(算术,非经验)**:AP-0.5 收益盲反解(`alpha-pivot-power-precheck-2026-07-03.md`)证明过 DSR≥0.95 所需年化 Sharpe = 2.67(最松 IID 口径 1.66),该数**只依赖账本 N(只增)/样本 T/矩假设,与候选无关** → 对 D2/D3/D4/分析师动量及一切未来横截面排名候选同等适用;压到可及需 ~70–185 年数据;≤5 长-only A 股书 Sharpe 结构天花板 ~0.5–0.8。**样本内 DSR≥0.95 认证 = 永久不可达**;12+ 候选实测 DSR 0.001–0.05 完全确证。同时前向 95% 认证亦不可行(期望 t ≈ SR·√年;SR 0.62 需 ~7 年)。

**新 promotion 判据(dev 选拔门,样本高效)**:

| 门 | 阈值 | 角色 |
|---|---|---|
| 胜自身暴露匹配随机 placebo(paired-t) | **≥ 2.0,双容器 joint** | **选拔主门** |
| 熊市累计收益 | ≥ 0 | owner 判据(不变) |
| 股灾切片 | 全部不崩(覆盖切片内) | owner 判据(不变) |
| 净盈 | > 0 | owner 判据(不变) |
| MDD | 披露 + 与基线比 | disclosure(criterion-rebar 不变) |
| **DSR / SPA / Romano-Wolf / PBO** | **照算、照披露、账本照 append** | **降为 disclosure,不再作 promotion 门** |

**认证全部移到前向**:dev 选拔胜出的候选 → spec 冻结(hash)→ owner-gated 前向预注册(处子窗口零 deflation 债)→ **存活认证**:预注册风险 kill-switch(MDD 超暴露推出的上界 / 熊市亏损超阈 / 相对最简基线持续劣化即停),收益作监控披露而非 t≥1.645 检验。

**前向弹药纪律**:sealed test 第 5 次不读;FW look-once **不烧在 ffc1db3**(round-4 benchmark-relative 增强指数,≤5 绝对框定装不下)——稀缺前向资源留给本线选拔胜者。

### 决定 ②:产品分层 —— sleeve 地基 + 可选排名层

- **地基层 = 防御 sleeve**:防御宇宙过滤(排除门)+ ≤5 集中 + 永久现金 buffer(部署原型 buf40_5 ≈ 40% gross)。承重主张 = **风险性质**(暴露/MDD 上界近乎机械,数月可验),不主张排名 alpha → placebo/DSR 对其不适用(照披露)。判据 = 绝对净盈 > 0 + 熊市累计 ≥ 0 + 机械 MDD 上界 + 前向确认。证据:D1 buf40_5(MDD 14.78%,熊市正)+ B2 + slot frontier。
- **排名层 = 可选、非承重**:仅当其在 dev 选拔门下胜出(胜自身 placebo)才叠加;否则宇宙内选择退化为最简单确定性规则(如 dv_ratio top-5 等权)。
- **sleeve-only 是合法产品终态**(owner 已接受)。sleeve 产品化(接线/激活)在 D2 裁决后按其分支定形,owner-gated,本 amendment 不含实施。

### 决定 ③:D2 按重定性协议授权(排名层生死一刀)

D2(防御宇宙 × 反转排名)按 `docs/research/ds-d2-implementation-plan-2026-07-04.md` 执行,与原 D2 spec(`defensive-candidate-D2-*.md`)的差异:

1. **问题重定性**:D2 回答「排名层是否配得上叠在 sleeve 上」,不回答「能否过四门」(不能,已证;**预声明 DSR 必 FAIL,照报**)。
2. **必须补 A0 对照臂(证据洞)**:A0 纯反转从未在组合书层与随机 placebo 对比过(QGR-3 验的是 IC 符号;C1a/QGR-4 placebo 全在 EXIT 层;frontier 无 placebo 臂;D1 ablation 明文 DEFERRED)→ D2 ablation 须含 **A0 全宇宙臂 + A0 全宇宙随机 placebo 臂**,与 D2 臂同引擎/同窗口/同 bar source 一次跑齐。
3. **预注册三分支决策树**(评测前入 spec hash;read 仍为诊断面,owner 判):
   - **(a)** D2 胜自身 placebo(joint t≥2)且 owner 判据改善 → 排名层入围,冻结送前向队列;
   - **(b)** D2 不胜 placebo、但防御宇宙容器仍呈 sleeve 风险画像 → 排名层弃,产品 = sleeve-only;
   - **(c)** A0 亦不胜自身随机 placebo → 「反转 = 已验证排名边」被推翻(其历史净盈主要是宇宙/轮动/暴露效应),排名层整体死刑 + 对既往定性出修正记录;若 (c) 与 D2 胜 placebo 并存 → 矛盾如实披露,owner 深究后判。

**D3/D4 默认跳过**(与 D1 同族防御排名,机制预测同命,每刀加账本债);仅当 D2 给出意外证据、owner 明示再回访。**分析师修正动量** = 排名层候选 #2,仅 D2 走分支 (a) 且 owner 定向后按同一选拔协议测。

## §2 不变什么(红线自检)

- **四门不放宽**:DSR/PBO/SPA/RW 每刀**照算、照披露**,阈值一字不改;变的仅是 promotion 决策依据(本 amendment 授权)。
- **账本非清零**(D2 照 append,floor 2418 只增)· **committed spec 评测前 hash 评测后绝不改** · **size/行业中性化删最小 30%** · **train_val only**(sealed test 不读)· **FAIL 报 FAIL** · **push / 摄取 / look-once / live 激活 / sim 恢复全 owner-gated** · 研究零 LLM · PIT 字节存档禁重下 · 永禁真实下单 · codex 代码前置门。全部不变。
