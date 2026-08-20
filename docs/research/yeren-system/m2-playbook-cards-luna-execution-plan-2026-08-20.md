# M2 战法卡片 Luna 执行计划

> 日期：2026-08-20  
> 计划状态：待下一 session 执行  
> 唯一主线：先复刻「全能的野人」的交易系统、交易逻辑与操作手法，再做验证和优化  
> 起始恢复点：`M2-playbook-cards-delta-corpus`  
> 结束恢复点：`M2-playbook-cards-owner-review-1`

## 一、这次执行要解决什么

下一 session 只完成四件事，顺序不可交换：

1. 把 2026-08-15 以后新增的 18 条野人作品纳入 M2 证据工件；
2. 判断新增证据是否改变 2026-08-15 Base v1，尤其是“满仓后如何处理”和“锁仓退出触发器是否执行”；
3. 基于更新后的证据，形成总体仓位框架和第一批 6 张战法卡片；
4. 把 1 个框架和 6 张卡片提交 owner 逐项确认，然后停止。

本计划不做回测、不推进预测命中率批次、不写 `backend/playbook/`、不改前端、不做系统性优化。任何真实券商程序化下单代码永久禁止。

## 二、2026-08-20 数据刷新后的准确基线

### 2.1 野人语料

- 抖音主页重新枚举得到 1106 条唯一作品，原基线为 1088 条。
- 本次语料管线处理 23 个 pending 项，成功 23、失败 0；其中包含历史 ledger 恢复项和新增作品。
- 阶段 C 的研究工件仍只收口到固定序号 1088，因此下一 session 的真正研究增量是固定序号 **1089—1106，共 18 条**。
- 不能先写卡片再补这 18 条。它们处于 8 月 12 日满仓与锁仓触发器之后，可能直接改变仓位框架和退出卡片。

### 2.2 A 股与研究 PIT 数据

完整收盘边界为 **2026-08-19**；2026-08-20 采集时尚未收盘，未写入当日完整行情。

- 标准日频：`daily`、`adj_factor`、`daily_basic`、`fund_daily` 已补 20260814、20260817、20260818、20260819，共 16 个快照，0 失败。
- 名册与机构研究：`stock_basic_listed`、`stock_basic_delisted`、`index_member_all`、`report_rc` 已更新到 20260819。
- QGR 日频：`stk_limit`、`cyq_perf`、`stk_factor_pro`、`limit_list_d`、`suspend_d` 已补齐到 20260819；`forecast_vip`、`express_vip`、`ths_index`、`index_classify` 已生成 20260819 快照。
- QGR 本次新增 26 个快照、0 失败；20260819 的 `stk_factor_pro` 覆盖率为 0.9986，缺 8 个证券，不把它伪装成 100%。
- 主要指数：上证综指、深证成指、创业板指、沪深 300、上证 50、中证 500、中证 1000 的 `index_daily_major` 已覆盖四个新增交易日。

### 2.3 机构、两融、资金与期指

本次额外写入 39 个 append-only Tushare 快照，0 失败。

20260819 关键水位：

| 数据 | endpoint | 20260819 行数 | 用途边界 |
|---|---|---:|---|
| 全市场资金流 | `moneyflow` | 5541 | 只作可观察资金结果，不反推主体动机 |
| 两融汇总 | `margin` | 3 | 市场杠杆背景 |
| 两融明细 | `margin_detail` | 4433 | 证券级融资融券事实 |
| 龙虎榜 | `top_list` | 80 | 证券级异常交易事实 |
| 龙虎榜机构明细 | `top_inst` | 825 | 有明确席位名称时才可引用 |
| 中金所合约表 | `fut_basic` | 716 | 合约消歧 |
| 中金所期货日线 | `fut_daily` | 64 | IF/IH/IC/IM 等价格、成交和总持仓 |
| 中金所结算参数 | `fut_settle` | 28 | 结算与保证金事实 |
| 中金所会员持仓排名 | `fut_holding` | 767 | 只核实名示合约/会员，不猜“信爷”身份 |
| 期指主力映射 | `fut_mapping` | 16 | IF/IH/IC/IM 连续合约映射 |
| 券商金股 | `broker_recommend` | 236 | 机构偏好背景，不直接生成买点 |

`fut_daily`、`fut_settle`、`fut_holding` 均已覆盖 20260814、20260817、20260818、20260819。

### 2.4 消息面

- 本地 MongoDB 和 Redis 均在 `127.0.0.1` 健康运行。
- 五源聚合调用有上游阻塞，因此改为来源级 35 秒上限执行。
- `global_em` 成功 upsert 200 条，`global_sina` 成功 upsert 20 条。
- 东方财富国内财经源返回空载荷；CCTV 当日返回 0 条；财联社源 35 秒超时。
- 这三个缺口不阻塞 M2 卡片。某张卡片若依赖具体事件，优先取交易所、政府或法定披露原文；不得用“新闻源当前不可用”推断事件不存在。

## 三、Luna 工作协议

### 3.1 每次只做一个清晰工作单元

Luna 不要一次吞下 18 条转写或整本 casebook。每个工作单元必须有：输入文件、固定作品范围、要回答的问题、输出文件和停止条件。完成一个单元后先写 worklog，再进入下一个。

### 3.2 证据纪律

- 每条作品必须阅读完整 transcript，关键词搜索只用于定位，不能代替全文理解。
- 原话只能由 `sentences[start:end+1]` 无分隔拼接生成，不能手打 `raw_text`。
- 每条引用必须带 `aweme_id`、`evidence_id`，并尽可能带 `statement_id` 或 `interpretation_id`。
- 事实、解释、规则假设三层分开。作者对“主力、机构、吸筹、纯多”的解释不是市场事实。
- 新证据只允许追加 observation、hypothesis revision、case/event/worklog；不覆盖旧研究判断。
- 没有证据支持的阈值统一写“未冻结”，不得补出仓位百分比、均线阈值或止损幅度。

### 3.3 数据使用纪律

- 价格、广度、成交、涨跌停、两融、龙虎榜、期指持仓分别陈述，不合成一个“资金意图分数”。
- 期指席位只有在原话给出可消歧的会员名、合约和日期时才核对 `fut_holding`。
- “信爷”仍不可识别时，保留 H-CAPITAL-LEADS-NEWS 的 X1 边界；不能在 767 行会员数据里事后挑一行冒充对应主体。
- 新闻以原始发布为先，Mongo 新闻只作候选发现和历史背景。
- 行情截止 20260819。超出边界的反馈写“尚不可结算”，不以盘中数据替代完整日线。

### 3.4 主线边界

- 不推进 `M3-A-batch-002`。
- 不统计博主总体命中率。
- 不把卡片转成确定性状态机或生产执行器。
- 不因回测方便而改变博主语义。
- 不新增评分表、置信度加权器、指纹、迁移层或未来功能开关。

## 四、执行总览

| 阶段 | 工作单元 | 主要产出 | 完成后恢复点 |
|---|---|---|---|
| A | 恢复点纠正与增量清单 | worklog + delta inventory | `M2-delta-observation-1089-1091` |
| B | 18 条新增作品研究，6 批 × 3 条 | 18 个 observation + 必要假设修订 | `M2-delta-synthesis` |
| C | 增量综合与 Base v2 草案 | delta casebook + Base 新版本 | `M2-playbook-framework` |
| D | 总体仓位框架 | 1 个 owner 确认单元 | `M2-playbook-card-01` |
| E | 第一批 6 张卡片 | 卡片确认稿 | `M2-playbook-cards-owner-review-1` |
| F | 交付前聚合检查 | worklog + owner 审阅索引 | 停止，等待 owner |

## 五、阶段 A：恢复点纠正与增量清单

### A1. 只读恢复检查

先读以下文件，读一次即可：

```text
KickoffPrompts/M2-playbook-cards-handoff-2026-08-15.md
docs/research/midterm-rearch-action-plan-2026-08-12.md
docs/research/yeren-system/base-v1-spec-g2-draft-2026-08-15.md
docs/research/yeren-system/m2-playbook-cards-luna-execution-plan-2026-08-20.md
```

然后执行：

```bash
cd /home/ps/papers/QuantMind
PY=/home/ps/anaconda3/envs/zhanglan/bin

tail -1 data/yeren_research/worklog.jsonl | jq -c \
  '{work_unit,status,resume_from}'

$PY/python -m scripts.yeren_research audit \
  --output data/yeren_research/inventory/asset-audit-20260820-post-refresh.json

$PY/python -m scripts.yeren_research audit-news \
  --output data/yeren_research/inventory/news-audit-20260820-post-refresh.json
```

停止条件：确认语料清单为 1106 条，且研究 observation 覆盖只到旧基线 1088。若数量不同，不猜原因，先把实际差异写入 worklog，再按“旧研究覆盖集合与当前 metadata 的集合差”生成 delta。

### A2. 追加方向纠正 worklog

如果 worklog 中尚无该条，追加：

```json
{
  "work_unit": "M2-course-correction-2026-08-20",
  "status": "completed",
  "resume_from": "M2-playbook-cards-delta-corpus",
  "findings": [
    "主线恢复为先复刻交易系统、总体仓位框架和战法卡片，M3-A 暂停。",
    "语料已增至1106，旧M2证据只覆盖1088，必须先研究新增18条。",
    "市场、机构、期指和消息数据已刷新，完整行情边界为20260819。"
  ],
  "real_broker_orders": false
}
```

实际写入时补 `recorded_at`，沿用现有 worklog 字段风格，不覆盖旧行。

### A3. 生成 delta inventory

输出新文件：

```text
data/yeren_research/inventory/delta-corpus-1089-1106-20260820.json
```

文件必须按 `create_time` 升序列出每条新增作品：

- `fixed_ordinal`
- `aweme_id`
- `published_at`
- `title`
- transcript 路径
- transcript 是否存在
- 是否已有 observation
- 是否存在 ledger 终态

不要假定固定序号，仅靠 metadata 行顺序；按与旧 observation 的 `aweme_id` 集合差确认 18 条。

停止条件：delta 每个 `aweme_id` 唯一，且全部明确归为“待研究 / 已有 observation / 无 transcript”。正常预期是 18 条待研究。

## 六、阶段 B：新增 18 条作品的证据研究

### B1. 固定分批

按 delta inventory 的时间顺序固定为 6 批，每批 3 条：

| 批次 | 固定序号 | work_unit |
|---|---|---|
| B1 | 1089—1091 | `M2-C-delta-045a` |
| B2 | 1092—1094 | `M2-C-delta-045b` |
| B3 | 1095—1097 | `M2-C-delta-046a` |
| B4 | 1098—1100 | `M2-C-delta-046b` |
| B5 | 1101—1103 | `M2-C-delta-047a` |
| B6 | 1104—1106 | `M2-C-delta-047b` |

如果实际 delta 不是 18 条，仍保持每批最多 3 条，并按真实序号重命名；不要把多余作品塞进一批。

### B2. 每条作品的固定处理顺序

对每个 aweme 严格执行：

1. 读 metadata 和完整 transcript。
2. 写一段不超过 200 字的“本条在交易系统中的作用”，先判断它是否包含系统证据。
3. 标出所有会改变动作的原话 span：市场状态、候选/入场、仓位、加减仓、退出、事件、已执行动作、预期反馈、教学规则、修辞。
4. 对每个 span 区分 `statement` 与 `interpretation`；主体或股票无法消歧时保留候选。
5. 只为本条实际主张查询市场、期指、机构或新闻证据，不做全数据扫描。
6. 写新 observation JSON，并运行 schema validate。
7. 判断它对既有 32 个家族是 `support / counterexample / scope_narrowing / wording_revision / no_change`。
8. 只有规则正文、适用范围、反例或分类发生实质变化时，才向 `hypotheses.jsonl` 追加新 revision。
9. 每批结束后追加一条 worklog，写明 3 条作品、observation 数、hypothesis revisions、case/event 工件和下一个恢复点。

单条验证命令：

```bash
$PY/python -m scripts.yeren_research validate observation \
  data/yeren_research/observations/<aweme_id>.json
```

### B3. 新增语料优先回答的六个问题

每条都要检查，但没有证据就写“本条无证据”，不要强行填满：

1. 8 月 12 日“全仓突击”后，作者是否披露实际减仓、清仓、继续锁仓或改口？
2. “周四五不摸 4000 则减仓”的事前触发器是否出现执行证词？触发与执行必须分开。
3. 8 月 13 日大跌后，作者将失败归因于规则、市场、主力还是修辞自嘲？归因不能替代动作。
4. `空仓—试错—加仓—锁仓—推仓` 中哪些是主动状态，哪些只是描述被套、流动性或情绪？
5. 是否出现对“禁亏损补仓”“被套反弹减半”“只有 B 和 S”的反例或范围修订？
6. 是否给出新的明确证券、会员席位、合约、公告或仓位分母，使过去不可识别的说法变得可核验？

### B4. 数据调用边界

- 需要 8 月 14—19 价格反馈时，使用 PIT 的 `daily`、`stk_factor_pro`、`limit_list_d` 和 `index_daily_major`。
- 需要核对量能时，明确“总成交额”和“相对前日增量”两个口径。
- 需要核对期指时，先用 `fut_mapping` 确认 IF/IH/IC/IM 当日主力合约，再查 `fut_daily`；只有明确会员名才查 `fut_holding` 对应行。
- 需要核对机构活动时，`top_inst` 只证明上榜席位交易，`report_rc` 和 `broker_recommend` 只证明公开机构观点，不证明作者所称主体参与。
- 需要核对消息时，Mongo 新闻先找线索，再回到官方原文；若官方原文不可得，明确标记来源级别。

### B5. 阶段 B 完成条件

- 18 个新增 aweme 全部有 observation 或明确 unavailable 终态。
- 新增 evidence quote 均可由 transcript span 重新生成。
- 每个实质性反例均进入假设 revision，不只写在 worklog。
- 不因新增行情结果改变旧原话含义。
- 最后恢复点为 `M2-delta-synthesis`。

## 七、阶段 C：增量综合与 Base 更新

### C1. 新建增量综合文件

新建：

```text
docs/research/yeren-system/casebook-delta-2026-08-20.md
```

固定结构：

1. 新增作品范围与数据边界；
2. 满仓—锁仓—退出的连续 case chain；
3. 仓位状态语言的新证据；
4. 六张首批卡片相关家族的新支持与反例；
5. 期指/机构/消息主张的可核验与不可核验边界；
6. 对 32 家族的逐项影响表；
7. 仍未冻结的参数；
8. 对 Base v1 的修改建议。

影响表只写判断与证据，不做评分。每个家族只允许四种结论：维持、收窄、修订、出现重大反例。

### C2. 生成 Base 新版本，不覆盖旧稿

新建：

```text
docs/research/yeren-system/base-v1-spec-g2-draft-2026-08-20.md
```

以 2026-08-15 版本为基础，但只改新增证据实际影响的段落。文首增加 revision note，逐项列出：

- 哪条语义变化；
- 新 observation / hypothesis revision；
- 为什么会改变或不改变交易动作；
- 是否仍为未冻结参数。

特别检查：

- B2 仓位上限漂移是否获得后续执行证词；
- B4 清仓/退潮后的轻仓试错是否有新样本；
- B5 本金优先是否在满仓后仍成立，还是只保留规范性口径；
- D1 禁亏损补仓是否出现真实反例；
- D7 锁仓退出从“事前触发器”推进到“已执行”还是仍未完成；
- X1 期指主体叙事是否仍不可识别。

若新增 18 条不改变某段，原文保持不动；不要为了显示工作量改写。

### C3. Base 更新门槛

只有以下情况才改 Base 正文：

- 新原话直接改变动作方向；
- 新动作证词解决旧歧义；
- 新反例迫使适用范围收窄；
- 新证据把某条从叙事推进为可观察条件。

仅有后续涨跌、作者邀功、自嘲或责任切割时，不改 Base 规则，只登记结果或修辞层。

停止条件：形成可供卡片引用的最新语义骨架，恢复点为 `M2-playbook-framework`。

## 八、阶段 D：总体仓位框架确认稿

### D1. 输出方式

在新文件中先写总体框架，再写六张卡片：

```text
docs/research/yeren-system/playbook-cards-draft-2026-08-20.md
```

总体框架标题使用：

```text
总体仓位语言：空仓 → 试错 → 加仓 → 锁仓 → 推仓（待 owner 确认）
```

这里是**博主行为语言的复刻稿**，不是确定性状态机。不要写枚举类、转移函数或执行代码。

### D2. 每个状态固定写七项

1. 博主原词及其上下文含义；
2. 进入前提；
3. 允许动作；
4. 禁止动作；
5. 离开或转入下一状态的证据；
6. 反例、漂移和口径冲突；
7. 原话 span 与出处。

### D3. 五个状态的最低证据要求

#### 空仓

- 关联 H-CAPITAL-FIRST、H-PROFIT-LOCK-WITHDRAWAL。
- 区分模式失效空仓、风险占优暂停新仓、现金为主、卖清可卖仓和 T+1 残仓。
- 不得把所有风险提示都写成机械清仓。

#### 试错

- 关联 H-REENTRY-LIGHT-TRIAL、H-EXIT-EXPECTATION。
- 明确轻仓试错依赖情绪拐点或有限修复，不按固定天数重入。
- “轻仓”的百分比未冻结。

#### 加仓

- 关联 H-POSITION-CONVICTION、H-RIGHT-SIDE-TREND、H-SYSTEM-PRESET。
- 区分计划内支撑加仓、超预期新增暴露和禁止的亏损补仓。
- 必须写新增暴露失败后撤回新增风险。

#### 锁仓

- 同时呈现主动持有、被迫躺平、流动性叙事和时间窗退出触发器，不能先验合并成一个正面状态。
- 重点纳入 8 月 10—12 日叙事及新增 18 条的后续证据。
- 如果新增证据显示“锁仓”只是修辞或事后解释，应在 owner 问题中明确提出。

#### 推仓

- 关联 H-PHASE-EXPOSURE-CAP、H-CAPITAL-LEADS-NEWS。
- 写清三至五成→八成→满仓的口径漂移、利润垫理由、无回单和 8 月 13 日即时反反馈。
- 不把“满仓”写成目标系统推荐动作，不冻结任何比例。

### D4. 状态迁移表

只写证据支持的迁移，不补齐一个漂亮闭环。表格列：

| 当前语言 | 可观察前提 | 作者声称的动作 | 下一反馈窗口 | 失败时动作 | 证据 | 未冻结项 |
|---|---|---|---|---|---|---|

如果某迁移只有叙事没有动作，明确写“仅叙事，不能形成迁移规则”。

### D5. owner 对总体框架的四个问题

1. 是否接受把五个词作为“行为语言”而非生产状态机？
2. “锁仓”是否应拆成主动持有与被迫锁仓两个概念？
3. “推仓”是否只保留为风险极值/反例，而不作为目标系统正向状态？
4. 是否同意把仓位比例全部留待 M3，而当前只冻结动作关系？

## 九、阶段 E：第一批六张战法卡片

### E0. 引用辅助工具

如果仓库仍没有稳定的 evidence quote 导出入口，先新增一个最小研究工具：

```text
scripts/yeren_research/evidence_quote.py
tests/yeren_research/test_evidence_quote.py
```

它只做一件事：输入 observation 路径和 `evidence_id`，读取其 transcript span，从对应 transcript 的 `sentences[start:end+1]` 拼接原话，并输出 quote、aweme_id、evidence_id、statement/interpretation ID。它不摘要、不改写、不评分。

只写能检测这三个具体故障的测试：span 越界、aweme 不匹配、拼接文本与 sentences 不一致。不要加入指纹、兼容层或批量迁移。

### E1. 每张卡片固定结构

每张卡片必须包含：

1. 名称；
2. 适用市况；
3. 入场条件；
4. 加减仓与退出规则；
5. 仓位约束；
6. 原话引用与视频出处；
7. 证据分类；
8. 未冻结参数；
9. 反例与适用边界；
10. owner 待确认问题。

前六项沿用行动计划规定，后四项用于防止把候选语义伪装成已确认规则。

### E2. 卡片 1：右侧波段入场

主要家族：`H-RIGHT-SIDE-TREND`。

必须写清：

- 候选：底部/低位长期横盘、红肥绿瘦、20 日线向上、基本面未实质恶化；
- 激活：大阳线或涨停；
- 首次入口：次日回调或 5 日线附近震荡；
- 后续增加：只在事前定义支撑的小回调且趋势未失效；
- 与亏损补仓的区别；
- 均线、底部、红肥绿瘦、大阳线、失效阈值均未冻结。

owner 问题：是否认可“候选—激活—回踩”为博主 Base 语义，而具体 5/20 日参数留待 M3？

### E3. 卡片 2：分歧切核心 / 弱势抱团

主要家族：`H-WEAK-MARKET-CORE`、`H-MARKET-CHAOS-RETREAT`、`H-MICROSTRUCTURE-RELAY`。

必须区分：

- 弱市已有核心仓可持有，不等于空仓者可追入；
- 分歧中的换手核心只证明本轮角色竞争，不是永久龙头；
- 无证券池时，“核心”只保留角色定义，不能事后选赢家；
- 次日修复失败仍是独立淘汰条件。

owner 问题：该卡是否应限定为短线题材层，而不适用于机构主导的波段仓？

### E4. 卡片 3：红卖绿买修复轮动

主要家族：`H-THEME-CONTINUATION`、`H-ARB-VS-CONVICTION`、`H-TIERED-EXPECTATION-EXIT`。

必须写清：

- 它是修复/轮动窗口的节奏，不是“所有红盘卖、所有绿盘买”；
- 先弹的先进入结束阶段，补涨的最后结束，只是相对进度口径；
- 后排在修复中优先淘汰；
- 证券池、核心/中军/后排定义未冻结；
- 套利仓与波段仓不可混写。

owner 问题：现有证据是否足以独立成卡，还是应作为“分层退出”的子规则？

### E5. 卡片 4：禁亏损补仓 + 被套反弹减半

主要家族：`H-AVERAGING-DOWN-BAN`，分类 `stable_core`。

必须写清：

- 禁令理由：无法区分反转与反弹，继续补可能形成不可控重仓；
- 下跌原因诊断不改变禁补仓方向；
- 被套后出现反弹时方向是减而非补；
- “不足 10% 减一半”的基准不明，只冻结方向，不冻结数值；
- 计划内趋势支撑加仓不是亏损后临时摊低成本。

owner 问题：是否确认“禁亏损补仓”为第一条 Base 内核，并接受反弹减半只冻结方向？

### E6. 卡片 5：事前锁定退出触发器

主要家族：`H-TRADING-HORIZON-LOCK`、`H-SYSTEM-PRESET`。

必须写清：

- 最完整样本是时间窗×点位：“锁仓两天 + 周四五不摸 4000 则减仓”；
- 触发成立、作者声称执行、实际回单三者分开；
- 用新增 18 条作品决定执行状态是“已执行 / 未执行 / 仍不可知”；
- 4000 点是样本参数，不是跨期阈值；
- 事后延长窗口或改写目标不进入规则。

owner 问题：是否确认“所有交易在入场前绑定退出触发器”的结构语义，并把具体时间/点位交给 M3？

### E7. 卡片 6：混沌—退潮—恐慌盘防守链

主要家族：`H-MARKET-CHAOS-RETREAT`、`H-CAPITAL-FIRST`。

必须写清：

- 混沌/退潮首先关闭追逐轮动的新入口；
- 一次局部回流不结束退潮；
- 风险占优时先停止或延后新仓，仍参与只允许少量试探；
- 机构主导、硬逻辑且趋势未失效的波段仓可有限保留；
- “没有恐慌盘不会止跌”是候选观察，不是无条件抄底信号；
- 退潮、恐慌盘、修复强弱阈值均未冻结。

owner 问题：是否接受该卡是组合防守卡，而卡片 2 是弱市机会选择卡，两者不合并？

### E8. 卡片写作顺序

严格按 E2—E7 顺序逐张完成。每写完一张，先检查它是否：

- 能回到 Base 新版本至少一处；
- 能回到 observation 至少一处自动生成的原话 span；
- 明确标出反例和未冻结参数；
- 没有混入目标系统增强层或 owner 方向层。

这是交付完整性检查，不是评分。发现证据不足时写“证据不足，建议并入某卡或延期”，不要为了凑六张而扩大语义。

## 十、阶段 F：交付、测试与停止

### F1. 只跑与本次改动直接相关的检查

如果只改文档和 append-only 研究工件：

```bash
$PY/python -m scripts.yeren_research audit
```

如果新增 `evidence_quote.py`：

```bash
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q \
  tests/yeren_research/test_evidence_quote.py
$PY/ruff check scripts/yeren_research/evidence_quote.py \
  tests/yeren_research/test_evidence_quote.py
```

不要跑全仓测试。若改了既有 yeren_research 代码，再运行：

```bash
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/
$PY/ruff check scripts/yeren_research/ tests/yeren_research/
```

### F2. 最终 worklog

追加一条：

```text
work_unit=M2-playbook-cards-extraction-batch-1
status=completed
resume_from=M2-playbook-cards-owner-review-1
```

`outputs` 至少列出：

- 18 个 delta observation；
- hypothesis revisions；
- delta casebook；
- Base 2026-08-20 新版本；
- 总体仓位框架；
- 六张卡片；
- evidence quote 工具（如新增）；
- `real_broker_orders=false`。

### F3. owner 审阅索引

最终回复只给 owner 以下内容：

1. 数据与新增语料处理摘要；
2. Base 因新增证据发生的实质变化；
3. 总体仓位框架的 4 个确认问题；
4. 六张卡片各自的单一确认问题；
5. 明确声明未做回测、未推进命中率、未写生产战法、未涉及真实下单。

然后停止。未收到 owner 对总体框架和卡片的逐项意见前，不进入第二批卡片，不进入 G2，不进入 M3。

## 十一、Luna 自检用的完成定义

以下条件全部满足才可声称本计划完成：

- 当前 1106 条语料与研究 observation 的差集已处理完，不能仍有未解释的新增作品。
- 8 月 12 日满仓、8 月 13 日反馈、8 月 14—19 后续动作形成连续证据链。
- Base 新版本只包含新增证据导致的必要修订。
- 总体框架明确说明五个词是待确认的行为语言，不是生产状态机。
- 六张卡片均有自动生成原话、出处、证据分类、未冻结参数和反例边界。
- 卡片 2 与卡片 6 的职责边界清楚，卡片 3 证据不足时允许降为子规则。
- 期指数据没有被用于猜测“信爷”身份或主力动机。
- 没有真实券商程序化下单代码。
- 恢复点准确停在 `M2-playbook-cards-owner-review-1`。

## 十二、明确留到后续的事项

以下事项不属于下一 session：

- 第二批卡片：事件首入口、分层退出、套利/波段双仓、财报预期差、兑现窗口、ETF 表达、直接点名利空退出、75a ontology；
- G2 六问的最终 owner 裁决；
- 确定性规则化、Tushare 回测和 walk-forward；
- 博主发言命中率与假想收益；
- `backend/playbook/`、飞书行动通道和前端页面；
- 修复财联社新闻超时或东方财富空载荷；这些是主线外数据运维问题，记录但不阻塞首批卡片。
