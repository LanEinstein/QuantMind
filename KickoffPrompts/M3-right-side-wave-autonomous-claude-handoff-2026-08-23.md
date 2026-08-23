# Claude 接手 Prompt：M3 右侧波段闭环自主推进（Codex 决策讨论＋单轮审计）

> 日期：2026-08-23
>
> 主执行者：Claude
>
> 决策讨论与正式审计：Codex
>
> owner 最新授权（2026-08-23）：让 Claude 主力推进，过程中与 Codex 讨论决策；全程自动推进，
> 非必要情况不得要求 owner 干涉或做决定；任何 review / audit 必须由 Codex 执行，且至多一轮
> “审计→意见→修改→测试修改情况→确保修改到位”，禁止多轮迭代。
>
> 技术主纲：`KickoffPrompts/M3-systematic-optimization-and-validation-plan-2026-08-23.md`
>（commit `eb7f015`，当前本地、未 push）。
>
> 本接手 prompt 只改变主纲中**谁来拍板、何时停门**的执行机制，不改变其科学纪律、M3/M4
> 边界、预注册要求、结果分支或唯一底线。与主纲的 owner 门冲突时，以本文件的“owner 已将
> M3 研究决策委托给 Claude＋Codex”授权为准。
>
> `real_broker_orders=false`。

---

## 〇、给 Claude 的一句话任务

按最新计划完成右侧波段端到端 M3：冻结作者复刻层，在独立的目标系统增强层补齐可观察合同，
预注册后实现并一次运行交易级验证；交易级通过则自动进入独立组合级预注册与一次运行，失败则
如实封存。过程中由 Claude 提案并作最终执行决定，重大研究判断与 Codex 讨论；不要把可自行
判断的问题丢给 owner。正式缺陷审计由 Codex 独占且整个接手任务累计至多一轮。M3 结束后停止，
不自动进入 M4。

---

## 一、权限、职责与优先级

### 1.1 指令优先级

1. 唯一底线：永禁真实券商程序化下单；系统只允许研究与模拟盘。
2. `AGENTS.md` / `CLAUDE.md`：主线、反过度防御、单轮 review 上限、数据与 git 纪律。
3. owner 本次自主推进授权（本文件顶部原文）。
4. 最新计划书 `M3-systematic-optimization-and-validation-plan-2026-08-23.md`。
5. 既有 Base、确认卡、预注册和研究报告。

### 1.2 Claude 的职责

Claude 是主执行者，负责：

- 读取现有事实并形成单一推荐，不把参数菜单抛给 owner；
- 编写增强合同、预注册、代码、测试、原始工件、结果报告；
- 对 Codex 的决策意见作最终取舍，并在文档中说明采用/不采用理由；
- 对 Codex 正式审计 findings 作一次集中修订和测试收口；
- 按预注册结果自动进入下一分支或停止；
- 更新 worklog、memory，落本地 conventional commits；
- 不 push，除非 owner 再次明示。

### 1.3 Codex 的职责

Codex 有两个角色，必须分开：

1. **决策讨论者**：在增强合同、预注册、风险参数与结果解释出现实质选择时，回答清晰的决策题。
   这是方案讨论，不是缺陷 review，不得借机展开全仓审计或追求“无瑕疵”。
2. **唯一正式审计者**：在本任务所有预注册运行与实现完成后，进行累计唯一一轮正式审计。
   Claude、其他模型或 Agent 不得另起正式 review/audit 与之叠加。

Codex 的建议不是 owner 原话。被采用的增强项登记为 `owner-delegated`（owner 将研究决策权委托给
Claude＋Codex）或 `researcher-added`，不得写成作者 Base。

### 1.4 owner 不应被询问的事项

以下全部由 Claude 决定，并在重要节点与 Codex 讨论；**不得要求 owner 回答**：

- 右侧波段是否作为下一对象（计划已推荐，本授权允许直接采用）；
- 证券宇宙、动态 ST、上市时长、候选过滤的研究建议；
- “底部/横盘、大阳线、回踩、支撑、趋势失效”的目标系统可观察定义；
- 日线次日开盘保守代理是否用于第一版研究；
- 预注册样本切分、seed、placebo reps、报告结构；
- 交易级通过后是否进入组合级（按本文件自动分支）；
- 研究用单票/并发/总暴露与最大回撤门槛；
- P2/P3 finding 是否值得修；
- 结果失败后是否“再试一个参数”（答案固定为不试、封存）。

### 1.5 只有这些情况才找 owner

仅当出现下列真正需要新授权或外部动作的情况，才暂停并报告 owner：

1. 需要 push、合并分支或改写已 push 历史；
2. 需要 owner 登录、提供凭证、购买数据或操作外部账户；
3. 需要删除/覆盖非临时数据，尤其 `data/marketdata_pit/` 或 `data/yeren_corpus/`；
4. 需要越过 M3 进入 M4 生产接线、飞书动作通道或模拟盘执行器；
5. 任何真实券商下单方向——此项不是询问许可，而是直接拒绝并上报；
6. 出现本地证据无法消解、且不同选择会实质改变项目目标而非仅改变研究实现的歧义；
7. Codex 正式审计在合理重试后仍无法运行，而本任务已有必须审计的代码改动。

“我不确定选 20 还是 30”“哪种定义更漂亮”“结果不好看”都不属于必要情况。

---

## 二、必须继承、不得重做的事实

### 2.1 M2 已完成

- 18 张卡全部有 owner 确认；2026-08-23 剩余 11 张全部按现卡面通过，无修改、无暂缓。
- Base v3 是作者复刻层现行基线；V 版本政策与 N 仓位性质第一分叉继续优先。
- 空仓、试错、加仓、推仓、锁仓是行为语言，不是生产状态机。
- 32 个 hypothesis 家族已 0 遗漏，不再跑覆盖度清单来制造工作。

### 2.2 520 已封存

- 交易级实际费率版双窗口通过；组合级 OOS 主曲线年化约 −3.01%，必要条件不过；半天执行
  偏差均值约 0.2pp、中位约 0。
- 卡 8 维持研究候选、不可执行，不重启、不补 S8、不进入 `backend/playbook/`。

### 2.3 卡 1 的边界

- 卡 1 是 520 之后唯一“次近可规则化”对象，但作者没有给出“趋势失效”的可观察定义；
  底部/横盘、激活、回踩、支撑与证券池也存在目标系统参数缺口。
- 这些缺口不能写回作者卡面，只能在目标系统增强合同中解决。
- 其余卡中，禁令/流程/审查/表达类卡不应各自制造独立买点和 P&L。

---

## 三、开工检查

```bash
cd /home/ps/papers/QuantMind
git status -sb
git log --oneline -4

tail -1 data/yeren_research/worklog.jsonl | /home/ps/anaconda3/envs/zhanglan/bin/python -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"

PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/
$PY/ruff check backend/ scripts/ tests/
```

预期：

- 分支 `agent/m2-evidence-reconstruction`；
- `eb7f015 docs: plan m3 systematic optimization` 在本地顶部附近；
- worklog 恢复点：`M3-right-side-wave-enhancement-contract`；
- yeren 测试基线 126 passed，ruff 全绿；
- 工作区若有用户改动，保留并绕开，不清理、不 reset。

先读：

1. `AGENTS.md`、`CLAUDE.md`
2. `KickoffPrompts/M3-systematic-optimization-and-validation-plan-2026-08-23.md`
3. `docs/research/yeren-system/base-v3-spec-2026-08-20.md`
4. 三份 confirmed batch 卡片
5. `m3-post-candidate-e-next-step-decision-2026-08-21.md` §三
6. `m3-owner-gate-results-and-three-runs-2026-08-22.md`

---

## 四、Codex 决策讨论协议（不是正式审计）

### 4.1 何时必须讨论

Claude 至少在三个节点与 Codex 讨论一次实质决策：

1. **增强合同冻结前**：证券宇宙、入场链、趋势失效、是否关闭第一版推仓、执行代理；
2. **预注册冻结前**：主口径、样本切分、placebo、判据、失败分支；
3. **结果分支时**：允许的结论、是否满足自动进入组合级的既定条件、限制如何表述。

组合级若启动，再讨论一次研究用风险预算与最大回撤门。讨论次数不机械限制，但每次必须是新的
决策问题；禁止把同一问题换措辞反复问到满意为止。

### 4.2 怎么提问

给 Codex 的决策题必须包含：

- 现有事实与禁止改动项；
- Claude 的单一推荐；
- 最多一个真正可行的备选；
- 两者会导致的具体差异；
- 要 Codex明确回答“采纳主建议 / 采纳备选 / 指出遗漏的实质约束”。

不得问“帮我全面 review”“还有没有问题”“请复验到无缺陷”。

### 4.3 如何落记录

每次讨论只在对应合同、预注册或报告中留一段：

- Claude 原建议；
- Codex 意见；
- 最终决定；
- 来源归属（`owner-delegated` / `researcher-added`）；
- 若未采纳 Codex 意见，写一条具体理由。

不要保存冗长思维链，不做评分表，不让 Codex 替 owner 原话背书。

---

## 五、自主执行顺序

### 阶段 A：目标系统增强合同

按最新计划 §三工作单元 A，产出：

`docs/research/yeren-system/m3-right-side-wave-enhancement-contract-2026-08-XX.md`

必须覆盖：证券宇宙、候选生成、底部/横盘、激活、首次入口、趋势失效、新增暴露、执行时点、
费用、组合风险。每项写：作者 Base、缺口、Claude 单一建议、Codex 决策意见、最终定义与归属。

**本文件原计划要求写完后停 owner 门；本次授权已替代该门。** Claude 在 Codex 决策讨论后，
将最终选择登记为 `owner-delegated`，直接进入 B。不得虚构“owner 逐项确认”。

若第一版推仓会显著增加不可观察自由度，默认关闭卡 7 的推仓，只验证基础开仓—持有—退出闭环；
这是缩小研究自由度，不是删除卡 7。关闭与否由 Claude 提案、Codex讨论后决定。

### 阶段 B：预注册

产出：

`docs/research/yeren-system/m3-right-side-wave-preregistration-2026-08-XX.md`

逐项冻结最新计划 §三 B1 的 12 项。Claude 与 Codex 讨论后即视为本次 owner 委托范围内的运行授权，
**不再请求 owner 说“批准运行一次”**。

要求：

- 一个主口径，不列参数网格；
- 完整跨期加载面板，窗口用参数限定；
- OOS 不参与参数选择；
- 费用沿用 owner 已给的佣金万 1.5＋最低 5 元，其余按既有实际费率版；
- 研究用最大回撤门由 Claude 给出单一推荐、Codex 讨论、运行前冻结，标 `owner-delegated`；
- 写明交易级失败即封存，交易级通过才自动进入组合级；
- 所有无法识别项明写，不能藏在实现默认值里。

### 阶段 C：最小实现与本地验证

预计新增：

- `scripts/yeren_research/m3_right_side_wave.py`
- `tests/yeren_research/test_m3_right_side_wave.py`

优先复用主计划已列资产：`load_priced_panel`、调整后价格、同证券同持有期 placebo 设计、已有
动态 ST/涨跌停/T+1/无下一成交事实语义。只借鉴 `run_portfolio` 的账本语义，不继承 520 的
`TradeE`、10%/5×2% 或信号。

本地先绿：

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/
$PY/ruff check scripts/yeren_research/ tests/yeren_research/
```

此时**不要启动正式 Codex 审计**；正式审计留到本任务所有自动结果分支完成后一次做完。

### 阶段 D：交易级一次性运行

按预注册主规格运行一次，原始工件落：

`data/yeren_research/inventory/m3-right-side-wave-2026-08-XX.json`

报告落：

`docs/research/yeren-system/m3-right-side-wave-results-2026-08-XX.md`

自动分支：

- 主必要条件或 placebo 不过 → 标记未通过，禁止调参补救，跳到阶段 F 单轮审计与收口；
- 交易级通过 → 不询问 owner，自动进入 E；
- 可复现实现/数据故障 → 记录作废原因，修复仍留给阶段 F 的唯一修订批次；不得偷偷先改再跑。

### 阶段 E：组合级预注册与一次性运行（仅 D 通过）

先写独立组合预注册，再实现最小资金重放。风险预算与最大回撤门已在 B 冻结或在 E 运行前经
Claude＋Codex决策讨论冻结，不问 owner。

输出：

- `docs/research/yeren-system/m3-right-side-wave-portfolio-preregistration-2026-08-XX.md`
- `data/yeren_research/inventory/m3-right-side-wave-portfolio-2026-08-XX.json`
- `docs/research/yeren-system/m3-right-side-wave-portfolio-results-2026-08-XX.md`

任一必要条件不过即封存；通过也只得到“M4 候选”资格，不进入 M4。

### 阶段 F：Codex 唯一一轮正式审计、一次修订、测试收口

这是本接手任务**唯一允许的正式 review/audit**。此前的决策讨论不得演变为缺陷审计。

#### F1. 审计时点

- 若 D 失败：D 报告完成后审计 A—D 的全部产物；
- 若 D 通过并进入 E：E 报告完成后审计 A—E 的全部产物。

审计对象包括从基线 `eb7f015` 之后与本任务有关的代码、测试、预注册、报告和小型 JSON 工件；
大 JSON 不直接喂给 Codex，提供 schema、关键统计与生成代码。

#### F2. 一轮的精确定义

唯一一轮包含且仅包含：

```text
Codex 审计一次
  → 输出一次 findings（P0/P1/P2/P3）
  → Claude 集中判断并修改一次
  → 跑定向回归测试＋相关完整测试
  → Claude 对照 findings、diff 与测试结果确认修改到位
  → 结束
```

最后一步“确认修改到位”是同一轮内的**定向测试与人工映射**，不是第二轮 Codex 复验。禁止：

- 再跑 `codex review`；
- 再让 `codex exec` 检查 findings 是否修复；
- 换另一个模型重新审；
- 以“确保干净”为由重新扫描全部产物；
- 要求连续两轮无缺陷。

#### F3. Finding 处置

- P0/P1：必须给具体故障场景，集中修；
- P2/P3：由 Claude 按主线判断，未修项写入最终报告，不阻塞；
- 若 finding 会使已发布结果无效：按原预注册修复并重跑**受影响的运行一次**，报告明确
  “audit-induced correctness rerun”；这不是调参，也不得触发第二轮审计；
- 若 Codex 审计无 finding：直接进入收口，不为确认而再跑。

#### F4. Codex 不可用

允许一次正常重试（例如进程启动失败、网络瞬断），但不能以不同 prompt 反复采样意见。若仍不可用，
且本任务有代码改动，则属于必要 owner 阻塞：报告现状并停止，不得用 Claude 自审或其他模型替代。

### 阶段 G：最终报告、台账与接手

完成：

- 根据审计后最终数字更新结果报告；
- 写清通过/失败、允许结论、未处置 findings 与 M4 是否具备候选资格；
- 更新 worklog、memory；
- conventional commits 只落本地；
- 若 M3 通过，另写 M4 计划/接手 prompt，但**不实现 M4、不 push**；
- 若 M3 失败，写明合法下一选项，不自动切事件题材链继续搜索。

---

## 六、自动决策规则

| 情形 | Claude 自动动作 |
|---|---|
| 增强定义有多个看似合理值 | 以最少研究自由度、最贴确认语义、可由 PIT 观察为序选一个；与 Codex 讨论后冻结 |
| 作者没说 | 标为增强层，绝不写回 Base |
| OOS 不好看 | 不调参、不换口径、不加过滤，按预注册失败分支封存 |
| 交易级通过 | 自动进入组合级独立预注册 |
| 组合级失败 | 封存为研究候选/未通过，进入唯一审计与收口 |
| 组合级通过 | 标为 M4 候选，进入唯一审计与收口；不自动生产化 |
| 某约束无法由现有数据计算 | 判断它是否是闭环必要项；必要则缩小合同或记录真实阻塞，不用代理偷偷替换 |
| 发现主线外缺陷 | 报告 P3，不修、不阻塞 |
| Codex P2/P3 与 Claude 判断不同 | Claude作最终决定并写理由，不发给 owner 投票 |

---

## 七、明确不做

1. 不要求 owner 逐项确认增强合同、预注册或继续分支；本次已经授权委托。
2. 不把 Codex 决策讨论伪装成 owner 原话或作者证据。
3. 不重启 520，不补 S8，不重跑封存三层证据。
4. 不统计博主总体预测准确率来抢主线。
5. 不做参数网格、收益择优、评分表或自动轮转下一战法。
6. 不把行为语言建成生产状态机。
7. 不在 M3 通过前进入 `backend/playbook/`、飞书、模拟盘接线或前端 M4。
8. 不改 `data/marketdata_pit/`、`data/yeren_corpus/` 既有档案。
9. 不运行第二轮 Codex review/audit/复验；定向测试收口仍属于唯一一轮。
10. 不 push，除非 owner 再次明示。
11. 永不创建真实券商程序化下单路径。

---

## 八、最终停止条件

满足任一研究结果分支并完成阶段 F/G 后停止：

1. 交易级失败并完成单轮审计收口；
2. 交易级通过、组合级失败并完成单轮审计收口；
3. 交易级与组合级均通过，已产出 M4 候选结论和下一计划，但未进入 M4；
4. 出现 §1.5 的必要 owner 阻塞；
5. 唯一 Codex 审计无法完成且本任务有代码改动。

不要在停止点后“顺手”尝试事件题材、ETF 或另一个趋势失效定义。

---

## 九、给 Claude 的开工口令

> 按 `KickoffPrompts/M3-right-side-wave-autonomous-claude-handoff-2026-08-23.md`
> 开工。你是主执行者；M3 范围内的研究决策已由 owner 委托给你，重要选择与 Codex 讨论并
> 留简洁决策记录，不再把非必要问题交给 owner。严格预注册并自动推进结果分支。正式审计只能
> 由 Codex 完成，整个接手任务累计至多一轮“审计→意见→一次修改→定向测试与确认”闭环，
> 禁止任何第二轮 review、复验或换模型重审。M3 完成即停，不进 M4，不 push，
> `real_broker_orders=false`。
