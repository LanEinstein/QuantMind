# “全能的野人”交易系统复原方法

> 版本：初版，2026-08-13
> 适用阶段：M2 阶段 C 全量证据研究；G1 已通过；不是 Base v1 规则说明

## 研究目标和边界

目标是复原博主如何观察市场、处理消息、选择机会、配置资金和结束交易，而不是只判断其观点后来是否正确。忠实度先于回测表现；基础系统冻结前，后续收益不得用来选择一个更“赚钱”的语义解释。

本阶段只生成研究记录、离线回放和模拟盘准备材料。不会创建真实券商自动下单路径，也不会在 owner 通过 G2 前实现 `backend/playbook/yeren/`。

## 三层记录

每个结论固定分为三层：

1. **原始证据**：原句及毫秒偏移、局部音视频、行情快照、公告或财务行；
2. **解释**：主体、时间、动作和交易含义，由主 agent 阅读完整上下文后给出；
3. **规则假设**：解释支持、反驳或修订哪个候选规则。

解释不能冒充原话，规则不能反过来改写解释。观察表不保存回测参数。

证据强度只使用“明确 / 较可信 / 待定”，同时写理由、反证与替代解释；不使用打分表或人为权重求和。

## 单视频流程

1. 按 `published_at` 阅读完整原转写，而不是只看关键词附近句段。
2. 划分事实、市场状态、个股观点、消息/财报解释、已执行动作、计划、条件规则、复盘、教学和修辞。
3. 对股票隐语、人物、题材和“今天/明天/周末”等做消歧；无法确认时保留候选。
4. 建立 `recording_time_interval`、`referenced_market_interval`、`information_available_at` 和 `earliest_action_at`。
5. 只查询原话实际需要的行情、公告、财报或事件，组装 decision bundle。
6. 完成解释和候选规则后，才打开 outcome bundle 做验证与复盘。
7. 将有系统意义的规则追加到 `hypotheses.jsonl`；修订新增记录，不覆盖历史判断。

关键词工具只负责候选发现。核心语义、实体消歧、规则演化和冲突处理均由主 agent 亲自完成，不调用批量摘要模型。

## 时间和可执行性

- 平台时间只证明 `published_at`，不自动等于录制时刻。
- 仅能确定日期时保留日期区间；不使用“发布时间减视频时长”伪造录制开始时间。
- 日线、日度涨停榜等在研究包中保守按收盘后可见。
- 午休发布映射到 13:00，盘后/周末发布映射到下一交易日 09:30；盘中发布只能证明信息在发布后可见，不能假设观看和执行零延迟。
- 只有日期而没有时刻的公告，最保守地从下一交易日开始影响可执行决策；取得交易所精确时间后才能缩窄边界。
- 财报以 `ann_date`/实际披露日进入视野，不以报告期进入视野；修订和更正是新事件。
- A 股 T+1、涨跌停、停牌、整手、跳空和流动性约束在执行阶段单独应用，研究解释不假造可成交价格。

## 三档还原精度

| 精度 | 使用条件 | 禁止声称 |
|---|---|---|
| 日级 | 有日线和收盘证据 | 精确盘中买卖时刻、成交价、竞价反馈 |
| 盘中 | 有分钟数据或可靠画面/时间证据 | 超出证据窗口的盘口过程 |
| 方向性 | 时间或实体仍有歧义 | 唯一证券、唯一动作、精确仓位 |

精度按案例标注，而不是按数据源整体乐观推断。

## ASR 与画面复核

只有会改变股票、指标、仓位或动作方向的词才触发定向媒体复核。流程为：

1. 临时下载该视频；
2. 只截取必要音频或关键帧；
3. 与 M1 原 ASR 和上下文交叉判断；
4. 有研究价值的局部证据保存到 `data/yeren_research/media/`，临时整段视频删除；
5. 只有证据足够时写 `asr_revision` 与 `revision_basis`；否则保留原词和歧义。

例如，首例一处“推仓位”已确认，后一处“推/退仓位”仍未确认；后者不得用于确定动作方向。

## 决策与结果隔离

一个案例形成两个物理文件：

- `decision_bundles/<case>.json`：`information_available_at <= decision_cutoff`；
- `outcome_bundles/<case>.json`：只含 cutoff 之后记录。

分割只取决于可用时间，不取决于记录后来是否“有用”。测试会修改未来结果并断言 decision partition 不变。缺少 outcome 不得阻碍 decision bundle 生成。

首例截止 2025-08-04 18:15:27，最早跟随动作是 2025-08-05 09:30。8 月 5 日以后的山河智能/西藏天路价格只存在 outcome bundle，不能用于证明 8 月 4 日的实体或规则解释。

## 跨视频归纳

每条假设记录：规则正文、条件、首次出现、支持证据、反证、例外、替代解释、当前类别，以及误解会改变什么交易动作。

当前类别：

- `candidate`：一次或少量表达，尚待跨阶段复核；
- `playbook_special_case`：只适用于特定题材、参与者结构或交易类型；
- `phase_rule`：在某段市场/个人系统版本有效；
- `stable_core`：多个独立市场阶段重复表达，并检查真实动作和重大反例后才可使用。

同一交易过程跨多日时建立 case chain，按“前提—介入—加减仓—消息/状态变化—离场—复盘”排序。孤立金句不直接升级为系统规则。

同时保留两条时间线：

- **完整考古版**用于解释最终稳定核心；
- **当时可知版**只在公开证据累积到足够程度后启用规则，用于 walk-forward 和样本外评价。

## 消息与财报分析

重要事件逐条回答：事实、预期差、传导路径、影响对象、持续期、是否已定价、最早可交易时刻、验证/失效条件，以及它改变了哪个决策。

来源优先级为交易所/法定披露、政府原始发布、带公告日的结构化财务、可靠历史财经新闻、最后才是研报与传闻。二手解读不能替代原始事实；传闻、公告和澄清分别成事件。

若原文只说“业绩好”“利好兑现”但没有可消歧公司，不会为了凑财务包任意选择股票。若博主声称量化或某游资参与，而本地没有龙虎榜，则只记录“博主作出该解释”。

## 本地工件与恢复点

```text
data/yeren_research/
├── inventory/
├── observations/
├── decision_bundles/
├── outcome_bundles/
├── cases/
├── events/
├── media/
├── hypotheses.jsonl
└── worklog.jsonl
```

该目录 append-only 且不入 Git；版本控制只保存方法、案例结论、工具与测试。新版本使用新文件名或 JSONL 新记录，不覆盖已有研究判断。

常用离线命令：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin

$PY/python -m scripts.yeren_research audit
$PY/python -m scripts.yeren_research audit-news
$PY/python -m scripts.yeren_research candidates --require market --require position
$PY/python -m scripts.yeren_research validate observation \
  data/yeren_research/observations/7534671965824175412.json
$PY/python -m scripts.yeren_research bundle-market \
  --case-id <case> --video-id <id> \
  --decision-cutoff <ISO-8601-with-timezone> \
  --start-date YYYYMMDD --end-date YYYYMMDD \
  --endpoint daily --code 000001.SZ
$PY/python -m scripts.yeren_research bundle-financial \
  --case-id <case> --video-id <id> \
  --decision-cutoff <ISO-8601-with-timezone> --code 000001.SZ
```

`worklog.jsonl` 保存已完成范围、输出和下一恢复点；单条不可得或空文本不阻塞其余任务。

## 当前研究门禁

owner 已于 2026-08-13 明确通过 G1，当前按每 25 条推进阶段 C 全量研究。G1 只确认现有证据粒度和功能维度足以表达真实材料；不会在阶段 C 定 Base 参数、实现状态机或通过收益筛选解释。G2 确认 Base 规范后才开始工程实现和历史复现。
