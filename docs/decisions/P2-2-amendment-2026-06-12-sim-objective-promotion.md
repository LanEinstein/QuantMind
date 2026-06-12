# P2-2 amendment(2026-06-12):模拟盘范围客观判据自动晋升(sim objective promotion)

> 状态:**已锁定**(owner 2026-06-12 AskUserQuestion 拍板:"客观判据全自动晋升" + "分层窗口+样本量双门槛")。
> 推翻对象:P2-2 / P2-2-amendment-2026-05-24 中"**全人工 gate** + 飞书批准 + 人工起草 amendment + git commit 才生效"与"统一 **45 日 shadow**",以及 7 禁中的"**自动 mutate config**"——**仅限模拟盘(simulation_auto)范围**。
> 不动:实盘/feishu_interactive 模式的晋升仍全人工 gate;7 禁其余 6 条(fine-tune / online learning / RLHF / DPO / continual SFT / 新 LLM provider / LLM 自动决策权)全留;安全地基红线全留。
> 设计依据:`docs/research/sim-autopilot-objective-evolution-frontend-style-2026-06-12.md` §3.1/§5 + codex 红队 P0-1..P0-5。

## 1. 决策(锁定)

### 1.1 客观晋升引擎(ObjectivePromotionEngine,确定性纯函数姿态)
- 进化产物(prompt 版本 / 白名单阈值集 / 策略变体)一律先入 **ExperimentRegistry**(append-only,**含全部失败实验**;晋升门槛随累计试验次数校正,防多重检验)。
- challenger 在 shadow 中与 incumbent 同 PIT 输入对照(反事实 replay + rqalpha 差分 oracle〔R-002 前置〕),晋升判据**全部预定义、确定性、可 replay**:
  1. **分层窗口 + 样本量双门槛**(owner 拍板):阈值参数 ≥15 交易日 **且** ≥30 个触发器级样本;prompt/LLM harness ≥20 交易日 **且** ≥15 笔独立成交;新策略代码 45 交易日不变;
  2. challenger 相对 incumbent 超额收益过 **bootstrap 置信区间**显著(现有 ShadowChain CI 接入判定,不再忽略);
  3. acceptance 8 门不劣化 + 最大回撤不恶化;
  4. anti-gaming 门:最低敞口 / 最少信号数 / 换手边界(防空仓策略靠"不交易"赢回撤);
  5. shadow 评估采用**更严苛成交假设**(涨跌停不成交 / ADV 容量上限 / 价格冲击 / 陈旧报价拒绝),防对 MockBroker 怪癖过拟合。
- **LLM 永不参与晋升判定**(判据输入全为确定性指标);晋升/降级动作 actor=SYSTEM,审计全留痕。

### 1.2 激活机制(git 不作运行时控制面;codex P0-4)
- 晋升 = append-only **PromotionIntent** + 内容寻址 **activation manifest**(全套 artifact 哈希)+ 原子 `next_boot.lock` 切换 + **08:30 受控重启窗口**(外部 supervisor;距 09:35 ≥65min)+ 开机哈希/健康断言;断言失败**自动回退上一 manifest**。
- git 仅事后镜像激活记录(daily 批量),**严禁** runtime 进程自行 git commit/push。
- "config runtime 不可改 + 重启生效"语义保留——变的只是批准主体(人工 → 确定性判据),不是生效机制。
- LiveArtifactRegistry 语义保留:实时只认 lockfile pin 哈希;本 amendment 授权 **PromotionEngine 成为 lockfile 的唯一程序化写入方**(写入仅经 PromotionIntent 链,append-only 审计)。

### 1.3 回滚与持仓(codex P0-5)
- 晋升后 incumbent **继续 shadow 跑**(live counterfactual 基线);challenger 上线 K 日相对劣化 → **自动降级**回滚。
- 持仓铭牌:每持仓记 `entry_policy_hash` + style + 卖出栈版本;**降级只影响未来入场**,存量持仓沿用入场时卖出栈;退役 artifact 永久保留可回放。
- 晋升冷却期(同一参数族晋升后 N 日内不再晋升),防震荡。

### 1.4 冻结不可进化集(frozen non-evolvable;违者=红线违规,对抗测试钉死)
安全地基全部(永禁真实下单 / 飞书人工〔实盘〕 / 127.0.0.1 / LLM 权限 4 类 / 单一构造点 / PIT / RiskEngine 纯函数)+ 仓位三连 + ≤5 槽 cap + 熔断 4 件 + 预算 4 常量 + universe 排除四件套 + 14-check 存在性与语义 + 对账阈值 + 模式开关。

### 1.5 可进化白名单(每参数带 immutable clamp,clamp 本身不可进化)
触发器系数(atr_stop_mult / r_multiple / time-stop 天数 / drawdown 分位参数 / 强势卖出族阈值)、selector 因子权重(归一约束)、风格槽位配额(价值槽 0..2 整数域)、题材 tier 权重(序约束 tier1≥tier2≥tier3≥tier4)、prompt 措辞/exemplars(≤3;SOP/人格卡骨架 frozen)。
**safety-adjacent 分类**:任何能推迟 SELL 的参数 → 更紧 clamp + 沿用"止损只紧不松"单调约束。
**prompt artifact = 政策 artifact**(codex P0-3):禁类 lint(禁出现指令类/决策类语句)+ 请求/响应字节捕获 + replay record,走同一客观晋升门。

### 1.6 绩效按 policy 分段(codex P0-1)
EquityPoint / acceptance / performance 全部挂 `policy_hash`(activation manifest 哈希);晋升即开新段;"实盘就绪"判定以当前段为准;前端分段展示 + 标注切换点。

## 2. 范围边界
- 本 amendment 授权域 = **simulation_auto 模拟盘**。`FEISHU_INTERACTIVE_ENABLED=true`(实盘人工执行模式)下的任何晋升仍走 P2-2 原人工 gate(飞书批准 + 人工 amendment + git + restart)。
- 模式切换(sim ↔ feishu)时:in-flight PromotionIntent 冻结,待 owner 处理。

## 3. 实施
Phase AB(AB-001..AB-008)+ AA-004;前置 R-002(rqalpha 差分 oracle)。R-003 的人工 gate 语义在 sim 范围由本 amendment 替代(实盘保留);R-004 的 22:00 cron + sub-budget 由 AB 接线吸收。
