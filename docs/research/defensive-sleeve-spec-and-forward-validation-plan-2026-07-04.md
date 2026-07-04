# 防御 sleeve 可部署 spec + 前向验证方案(2026-07-04)

- **性质**:产品化设计文档 + 冻结 spec 指针 + 前向验证协议(研究侧;不碰摄取/不碰 live 激活,均 owner-gated)。
- **触发**:owner 2026-07-04 批准推荐——排名层跨机制双证否(DS-D2 branch c 反转 + DS-AM 分析师动量)+ AP-0.5 算术墙 → 收官排名搜索,把唯一验证过的 sleeve 做成可部署产品并送前向。
- **上位**:`qgr-certification-rearch-amendment-2026-07-04-dev-selection-forward-certification.md`(产品分层:sleeve 地基 = 风险性质承重,placebo/DSR 不适用照披露,认证移前向)+ `defensive-d1-results-2026-07-04.md`(过滤器+buffer 控回撤实证)+ `external-crosscheck-tushare-data-talk-2026-07-04.md`(sleeve 先落地)。
- **冻结 spec**:`scripts/factor_research/defensive_sleeve_spec.py`,`spec_hash=c1d058c36ac0ae0f…`(评测前定死;universe filter drift-guard 到 `defensive_d1_spec.UNIVERSE_FILTERS`,container drift-guard 到 slot_frontier buf40_5)。

---

## 0. 一句话

sleeve = **D1 验证的防御宇宙过滤(红利低波)+ 最简确定性选择(dv_ratio top-5 等权)+ buf40 现金 buffer(≤5 槽 × 8% cap ≈ 40% gross/60% 现金)**,承重主张是**风险性质**(机械 MDD 上界 + 熊市不亏),不是排名 alpha。判据 = 净盈>0 + 熊市累计≥0 + 机械 MDD 上界(披露)+ 胜宇宙内 naive 基线(风险维度);DSR/SPA/RW 照算照披露不作门。认证全移前向:预注册 kill-switch 存活式,不做 t 检验。

## 1. 冻结 spec 内容(`defensive_sleeve_spec.py`,hash c1d058c3)

| 组件 | committed 值 | 来源/理由 |
|---|---|---|
| 防御宇宙过滤 | == D1 `UNIVERSE_FILTERS`(彩票顶decile剔 / ROE>0 / GPM 非底decile / dv_ratio≥中位 / 排除四件套 / 涨跌停剔 / bottom-30% size cut) | **复用 D1 验证过的过滤器,不重调**(drift-guard 测) |
| 宇宙内选择 | **dv_ratio top-5 等权**(SELECTION_FACTOR=dv_ratio / TOP_N=5 / equal_weight) | amendment"最简确定性规则"例;**不用被否的 block ranker,不用被证否的反转/分析师 ranker** |
| 分析师 tilt | **OFF**(enabled=False;factors={np_rev,rev_diff,cover_chg};role=tie_break_only) | DS-AM 薄辅助边——合法作 top 候选内 tie-break,非独立选择器;基础 spec 关闭,启用需重冻+重验前向 |
| 容器 | buf40_5(5 槽 × 8% cap ≈ 40% gross/60% 现金) | 回撤控制机制;byte-anchor slot_frontier;P-E ≥40% 现金门 |
| horizon/节奏 | 20d 月度 | D1 慢腿防御节奏 |
| 科学门 | 净盈>0 + 熊市累计≥0 + MDD 披露上界 0.20 + DSR 披露 | 风险主张非排名;placebo 不要求 |
| 前向 kill-switch | mdd_kill 0.25 / bear_cum_kill −0.05 / baseline_underperf 6 期 / min_forward 8 期 | 预注册;breach 即停(fail-closed);ACCRUING<8 期不裁决 |

## 2. Step 1 — 确认性科学门回测(build-ready,下一步执行)

**目的**:sleeve 的选择规则(dv_ratio top-5,不同于 D1 的 block ranker)在防御宇宙 + buf40 上确认风险性质(D1 的 buf40 MDD 14.78% 是 block ranker 选的;最简规则须单独证)。

**做法(复用为纲,最小新代码)**:
- panel:复用 `panel_train_val_defensive_d1.csv`(已有 dv_ratio/vol/roe/gpm/industry/log_size + fwd_ret_20d,月度 cadence,firewalled)。
- ablation:仿 `defensive_d1_ablation.py`,但 ranker 换成**最简 dv_ratio top-5 等权选择器**(新 ~20 行:防御宇宙过滤〔D1 exclusion gates 复用〕→ dv_ratio 降序 top-5 → 等权);容器只跑 buf40_5(+ eq_5 披露对照);placebo = 宇宙内 random top-5 + sizematched(暴露匹配);baseline = **naive 宇宙内 dv-top5 无 buffer**(风险维度对照)。
- 判据:净盈>0 + 熊市累计≥0 + MDD ≈ D1 buf40 区间(~15-20%,披露)+ 六股灾切片(2015/2016 空缺同 D1 accr 限制,披露)。**DSR/SPA/RW 照算披露(不作门——sleeve 不主张排名边,胜 placebo 非必须;披露 sleeve vs random 是为看"过滤+buffer"的风险贡献,非选股 alpha)**。账本 append `ds.defensive_sleeve`(kind=ablation)。
- 命令(预期):
  ```bash
  PY=/home/ps/anaconda3/envs/zhanglan/bin/python
  export FEISHU_INTERACTIVE_ENABLED=false
  # 新建 scripts/factor_research/defensive_sleeve_ablation.py(仿 d1,ranker=dv_ratio top5 eq)
  $PY -m pytest tests/factor_research/test_defensive_sleeve_*.py -q   # 冻结 spec + ranker 单测
  setsid nohup $PY -m scripts.factor_research.defensive_sleeve_ablation \
    --out data/factor_research/defensive_sleeve_result.json > logs/sleeve_run.log 2>&1 &
  ```
- 预期产出:确认 sleeve buf40 MDD 机械上界 + 熊市≥0 + 净盈>0 → 科学门过 → 冻结送前向队列;或若最简规则风险性质不如 D1 block(不太可能,buffer 主导),披露差异。code-review 前置门。

## 3. Step 2 — 预注册前向验证协议(owner-gated,cert=存活非显著性)

**原则(amendment)**:sleeve 风险性质数月可验;认证 = 前向存活(kill-switch 不 breach),收益作监控披露,**不做 t≥1.645 显著性检验**(样本内 DSR≥0.95 已证不可达,前向 95% 亦需数年)。

1. **预注册冻结**:Step 1 过后,git 冻结 sleeve spec(hash c1d058c3)+ kill-switch 阈值 + naive baseline 定义;登记前向起点(test_end 2026-06-12 之后新增数据)。**零 deflation 债**(处子窗口)。
2. **前向 shadow 运行**(owner-gated 摄取增量数据 + owner 重启):月度 rebalance sleeve on 前向数据,记录 net/MDD/bear/vs-baseline;<8 期 = ACCRUING 不裁决。
3. **kill-switch 监控**(fail-closed,任一 breach 即停 + 上报 owner):
   - 实现 MDD > **0.25**(机械上界破 = 过滤/buffer 失效);
   - 前向熊市累计 < **−0.05**(防御主张证否);
   - 连 **6** 期跑输 naive 宇宙内 dv-top5 基线(sleeve 无增量);
4. **go-live 门**:前向存活 + P0-6 **45 日滚动 shadow replay**(生产管线 bit-exact 复现)+ owner 人工 pin → 可上线(仍模拟实盘/飞书人工,永禁真实下单)。执行可行性用 owner ¥1万 资本做真 shadow;alpha 研究资本 ¥100万 证。
5. **季度复检**:filter/selection 是否漂移;分析师 tilt 是否值得启用(需重冻+重验)。

## 4. 复用件速查

| 需要 | 复用 |
|---|---|
| 防御宇宙过滤 | `defensive_d1_ranker.apply_exclusion_gates`(D1 exclusion gates) |
| panel | `data/factor_research/panel_train_val_defensive_d1.csv`(已有) |
| 事件循环/容器/placebo | `defensive_d1_ablation`(DefensiveArm/_run/_paired_t/size_matched_scores)+ `arena_ablation.ledger_n_trials` |
| 前向 runner 模板 | `round4_forward_test.py`(look-once/ACCRUING 语义)→ 改造为存活式 kill-switch |
| go-live shadow | P0-6 验收协议(backend 运行期,owner 重启后) |

## 5. 红线速查(违反即停)

train_val only(sealed test 永不读)· spec hash 冻结后绝不改 · 账本只增 · size/行业中性化删最小 30%(panel 内建)· 研究零 LLM · PIT 字节存档禁重下(本 Step 1 零摄取,复用 D1 panel)· **前向摄取 / live 激活 / sim 恢复 / push 全 owner-gated** · codex 代码前置门 · 永禁真实下单 · FAIL 报 FAIL(sleeve 若前向 kill-switch breach 如实停 + 报)· 报告中文/代码 commit 英文。

## 6. 当前状态

- ✅ **冻结 sleeve spec**(`defensive_sleeve_spec.py` + test,hash c1d058c3,code-review 前置门后 commit)。
- ⏭️ **Step 1 确认性科学门回测** = 下一执行步(build-ready,~45min,复用 D1 panel;需 owner 授权动手或下 session 首步)。
- ⏭️ **Step 2 前向验证** = owner-gated(摄取 + 重启 + go-live)。
