# P2-2 修订 — 2026-06-14 量化参数进化环:冻结前向 shadow 主门 + 诚实搜索 + 反自欺(P1c)

> **修订基准**: [P2-2-amendment-2026-06-12 sim 客观晋升](./P2-2-amendment-2026-06-12-sim-objective-promotion.md)(AB-002 9 门 / AB-003 intent+activation / AB-005 evolvable 白名单)
> **关联**: 自进化 dossier §3.5 + §8.2/§8.3③⑤⑥ + §8.4/§8.5(codex 对抗审查:门做减法、搜索诚实、反自欺)+ §9(owner 卡片决策)
> **修订日期**: 2026-06-14
> **触发**: 闭合量化参数进化环(第 5 条**确定性**lane,区别于现有 4 条 LLM lane)。codex 双路对抗审查指出原设计三处真实缺陷:① 历史门再多证明不了 edge(头号风险=虚假信心);② 门叠加重复惩罚 → 功效崩盘几乎永不晋升;③ 搜索过拟合被搬到边界选择/重跑而未设防。owner 卡片拍板:晋升主门=冻结前向 shadow、P1 首批=selector/allocation 权重。

## 1. 修订前(AB 已决)

- AB-002 `evaluate_promotion` 9 门(纯函数,含 purged k-fold/DSR/bootstrap CI/多重检验去膨胀/anti-gaming/oracle)为 **sim 域客观晋升判据**;feishu 域人工 gate(AB-003 `build_promotion_intent` feishu 模式 raise)。
- AB-005 `EVOLVABLE_WHITELIST` 15 参数 + clamp + safety-adjacent 单调;`ExperimentRegistry` 逐候选 content-addressed 注册(含失败)。
- 22:00 cron `_evolution_shadow_run_callback` 已接 sub-budget,但 `app.state.evolution_dispatcher` 从不设置 + 无 task producer → 恒空转 DEGRADED;现有 4 lane 全 LLM(prompt/rag/risk/exemplar)。

## 2. 修订后(量化参数 lane + 三阶段晋升 + 反自欺)

### 2.1 晋升架构:三阶段漏斗,**冻结前向 shadow 为主门**(codex critical,owner 卡片①)

- **历史回测 + rqalpha 双 lane + 统计门全降为「预筛」**(prefilter),**不再是晋升判据**。
- **阶段1 预筛(批量)**:MinBTL 准入门(历史长度配不上试验数 N 即拒整批)→ DSR 主门(对累计试验数去膨胀,DSR≥0.95)→ CPCV-PBO/PBO/SPA 作**披露指标**(报告不否决,除非样本量支撑;见 §2.3)。
- **阶段2 逐候选验**:anti-gaming(敞口/信号/换手)+ 双 lane oracle(订单流对账 + golden-vector)+ 封闭不变量(`P2-2-amendment-2026-06-14-deterministic-backtest-harness`)。
- **阶段3 主门 = 45 日冻结前向 shadow**(predeclared metrics + 日历时间下限 + **期间零参数编辑**;复用既有 P0-6/P2-2 shadow,**提升为不可绕主门**)。候选不得因「通过历史门」就上线。
- **阶段4 人工 pin**(amendment + 重启;sim 域仍人工 pin,比 AB-003 sim-auto 保守,owner 卡片②)。

### 2.2 诚实搜索(task producer,codex J4)

- **预声明确定性空间填充**:`scipy.stats.qmc` Sobol/LHS + 固定 `Generator(seed)`(BSD);**拒贝叶斯**(Optuna-TPE/Ax-BoTorch:自适应 → N 不可数、更快过拟合)。理由三条独立:预声明固定 N→DSR 的 N 精确;Sobol→低维投影覆盖;拒贝叶斯→N 不可数(**非「Sobol 更诚实」的错误归因**)。
- 约束确定性满足:sum=1 用 sorted-spacings(Kraemer)变换;单调「只紧不松」用 cumsum/sort 保序变换(每点合法、N 精确;禁拒绝采样)。**先算约束后有效维度**再定点数。
- **搜索边界冻结**:`EVOLVABLE_WHITELIST` clamp 已是 frozen code;**改边界 = amendment + 计入「研究者自由度日志」**。
- **累计 N 跨 session 永不重置**(从 `ExperimentRegistry` 直接算;换 seed/扩边界 = 显式 registry 事件);**参数空间/特征/数据窗的编辑一律计入 trial**(codex 开放问题③,防人工绕门调参)。

### 2.3 门做减法(codex J5,批量一等对象)

- **批量成为一等 registry 对象**(不可变候选集 + 共享数据窗 + 共享 null);PBO/SPA 是批量联合统计,挂批量对象上,不破逐候选注册。
- **只 DSR 有否决权**(对累计 N 去膨胀,最匹配多重检验诉求);**CPCV-PBO/PBO 降披露**;purged k-fold = 产 OOS 序列的方法非门;**SPA 基准 = 现役 pinned 参数**(语义=防把噪声当代际改进,独特不重复;`arch` NCSA)。
- 防小样本 A 股下「众门 AND → 几乎永不晋升」的功效崩盘。

### 2.4 反自欺机制(codex critical + owner 诚实前提)

- **null-edge sentinel 对照组**:每批 Sobol 候选掺已知无 edge 哨兵(随机阈值/shuffle 信号/现役镜像扰动);**门放行任何 sentinel → 门坏了;sentinel 永拒但真候选也永拒 → 空间无 alpha**(把「机器是否有用」变可观测)。
- **机制假设人工门(非统计)**:每个晋升候选须附「为何样本外继续有效」的经济机制(动量延续/均值回归/流动性溢价…);无机制的纯数据胜出 = 默认过拟合,拒。
- **诚实仪表盘**:owner pin 前必看「累计 N / edge 样本外衰减 / sentinel 通过率 / 距上次晋升天数」。设计目标 = 让机器持续给 owner 不相信它的理由。

### 2.5 lane + boot 接线 + task producer(补空转缺口)

- **新增第 5 条确定性量化参数 lane**(现有 4 LLM lane 原样不动,留 P3);直接喂 `evaluate_promotion`(预筛侧),不绕 ShadowChain。
- **boot 接线**:lifespan 启动期构造量化 lane 组件(harness + ExperimentRegistry + oracle runner + producer)挂 `app.state.evolution_dispatcher`。
- **task producer**:`ParamExperimentProducer` 确定性派生候选(Sobol/LHS,§2.2);每夜候选**硬上界**(量化 Line-2/selector 回测零 LLM → 真实界是 wall-clock/计算,非 ¥100 LLM cap;`reserve_evolution_run` 仍包住留痕)+ **如实 log 被丢弃候选**(no silent caps)。
- **P1 首批进环参数 = selector/allocation 权重**(owner 卡片③):selector 5 权重(sum=1)/ value 槽配额 / theme tier 权重;日线节奏 + **确定性代理入场**(买排序 top-N 经 RiskEngine+排除四件套)只做预筛,**真正验证靠阶段3 冻结前向 shadow**(代理入场 ≠ 实盘 LLM-gated 入场,故主门必须是实盘路径 shadow)。**Line-2 盘中参数不进环**(归非-alpha 风控,harness amendment §2.3)。

## 3. 实施与门禁

- 本 amendment = 边界文档 → docs 例外。**实施(P1c)** commit 前 codex-review + 全量 pytest + ruff + redline。TDD 对抗先写:sentinel 被放行 → 测试失败;无机制假设 → 拒晋升;历史门过但冻结 shadow 未过 → 不晋升;累计 N 重置 → 拒;Sobol 同 seed → bit-identical 候选序列。
- **依赖 P1-DATA + P1-0/P1b 完成**;sim 仍人工 pin;feishu 域人工 gate 不变。

## 4. 红线清单

1. **晋升主门 = 45 日冻结前向 shadow**(零参数编辑 + predeclared);历史回测+rqalpha+统计门仅预筛;**人工 pin 不拆**(sim 亦然)。
2. 搜索 = scipy QMC 预声明固定 N + 边界冻结(改=amendment)+ 累计 N 永不重置 + 一切编辑计入 trial;拒贝叶斯。
3. 门:DSR 主门否决;CPCV-PBO/PBO 披露;SPA 基准=现役;MinBTL 准入;批量一等对象。
4. 反自欺:sentinel 对照组 + 机制假设门 + 诚实仪表盘(强制)。
5. 第 5 条确定性 lane(4 LLM lane 不动);P1 首批=selector/allocation 权重;Line-2 不进环;LLM 零参与晋升判定(全数值)。

## 5. 修订记录追加

`docs/plan.html` 修订记录 + SESSION_LOG;plan.html P1c 任务。
</content>
