# 接手说明：四门已清、520 封存 → owner 逐张审阅剩余 11 张卡（研究侧支援）

> 日期：2026-08-22
> 工作目录：`/home/ps/papers/QuantMind`
> 分支：`agent/m2-evidence-reconstruction`（**已与 origin 同步至 `d90373b`**；本接手文档自身的 commit 按惯例留本地，下个 session 随 owner 授权一并 push）
> 当前恢复点：**`M2-owner-card-review`**（`data/yeren_research/worklog.jsonl` 尾条）
> 本文件取代 `KickoffPrompts/M3-owner-gate-and-next-steps-2026-08-21.md`（四道 owner 门已全部执行完毕，原文件已删；其内容由本文件 §3、`docs/research/yeren-system/m3-owner-gate-results-and-three-runs-2026-08-22.md` 与 memory 继承）。

---

## 0. 三十秒摘要

owner 已对四道门全部表态并执行完毕（push／分母可援引／卡 8 通过＋F 落卡／佣金万 1.5＋5 元地板），三个预注册衍生运行（费率重跑、执行时点敏感度、组合级验证）已跑完并报告。520 的证据链封存在三层终态上：**交易级双窗口通过（对佣金假设不敏感）、组合级必要条件不过（−3.01% 年化）、半天执行偏差 ≈0.2pp 均值/中位≈0**。owner 拍板下一步＝**(iii)：逐张审阅第二、三批剩余 11 张卡**（batch2 的 7/9/10/11/12——卡 8 已单独审过；batch3 的 13–18）。

**本阶段性质：owner-only 的审阅，研究侧只做支援**——呈现卡片与证据、当场落 owner 点名的修订、记录确认、产出确认稿。**研究侧不代审、不预设结论、不借机开新研究。** 若 owner 不在场，本 session 没有可做的研究工作，直接停（§7）。

---

## 1. 开工检查（逐条跑，核对预期输出）

```bash
cd /home/ps/papers/QuantMind
git status -sb          # 预期: ## agent/m2-evidence-reconstruction...origin/agent/m2-evidence-reconstruction [领先 1]
git log --oneline -2    # 预期顶部两条: 本接手文档 commit / d90373b docs: report owner-gate execution...
tail -1 data/yeren_research/worklog.jsonl | python3 -c \
  "import sys,json;d=json.load(sys.stdin);print(d['work_unit'],'|',d['resume_from'])"
# 预期: M3-owner-gate-execute-and-three-preregistered-runs | M2-owner-card-review

PY=/home/ps/anaconda3/envs/zhanglan/bin
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q tests/yeren_research/   # 预期 126 passed
$PY/ruff check backend/ scripts/ tests/                               # 预期 All checks passed!
```

核对三个运行的工件（任何讨论都会引用）：

```bash
python3 - <<'EOF'
import json
fee = json.load(open('data/yeren_research/inventory/m3-520-candidate-e-feerun-2026-08-22.json'))
sens = json.load(open('data/yeren_research/inventory/m3-520-exec-timing-sensitivity-2026-08-22.json'))
port = json.load(open('data/yeren_research/inventory/m3-520-portfolio-validation-2026-08-22.json'))
o = port['windows']['out_of_sample']
print('fee OOS delta_pp:', fee['windows']['out_of_sample']['primary_cohort_s8_c']['mean_net_return_pct']
      - 4.32347000134882)
print('sens OOS mean_pp:', sens['windows']['out_of_sample']['per_trade_delta_pp_signal_close_minus_next_open']['mean_pp'])
print('portfolio OOS main annualized:', o['main_curve_all_signals']['metrics']['annualized_return_pct'])
print('portfolio OOS attribution annualized:', o['primary_attribution_disclosure_post_hoc']['metrics']['annualized_return_pct'])
EOF
# 预期: fee OOS delta_pp ≈ 0.00087 | sens OOS mean_pp ≈ 0.1759
#       portfolio OOS main ≈ -3.0058 | attribution ≈ 6.8671
```

---

## 2. 不可改变的总纲

1. **唯一底线**：永禁真实券商程序化下单。所有产物带 `real_broker_orders=false`。
2. **主线次序（owner 2026-08-22 拍板）**：现在的主线就是 **owner 审卡**。研究侧的角色限定为：呈报材料 → 落 owner 点名的修订 → 记录确认 → 产出确认稿。**不代 owner 审卡；不因审卡间隙"顺手"开新研究；520 的任何再启动（含 S8 研究假设路线）须 owner 明示，走新预注册。**
3. **反过度防御四禁**：禁无用途的校验/指纹；禁防御不存在的输入；禁用评分表/复验循环替代人的判断；禁为想象需求预建框架。判断句：「这能检测到什么具体故障，我会因此做出什么不同的决定？」答不上来就不写。
4. **跨模型 review 至多一轮**（owner 2026-08-21 立，强制）：一轮 review＋一轮修复即止。禁复验到干净、禁多轮轮转。
5. **本阶段边界**：不进 `backend/playbook/`、模拟盘、飞书、任何券商路径；不改 `data/marketdata_pit/` 既有档案；不回改候选 E 及三个运行的任何已发布口径；**push 需 owner 明示**（本 session 产出的 commit 先落本地）。

---

## 3. 已定案事实（直接引用，不要重新论证）

### 3.1 520 三层证据终态（2026-08-22 报告 `m3-owner-gate-results-and-three-runs-2026-08-22.md`）

| 层 | 结论 |
|---|---|
| 交易级（候选 E，发布版） | 样本内 66,925 笔 +3.44%、样本外 45,942 笔 +4.32%，placebo p≈0.005，判据双过 |
| 交易级（实际费率版） | 净均值仅 **+0.0009pp** 差异（5 元地板在 ¥33 以下票上生效抵消费率下降），判据双过；**后续引用以实际费率版为基准** |
| 组合级 | OOS 主曲线（全部机械信号×10%敞口/5只×2%/实际费率）**年化 −3.01%，必要条件不过**；金叉子集事后归因 +6.87%/回撤 1.36%（不可执行的事后披露）；**S8 缺口的经济代价被量化**；敞口上限真实约束（held/equity 中位 7.87%） |
| 执行偏差 | 半天时点偏差＝每笔均值 +0.24(IS)/+0.18(OOS)pp、**中位≈0**（尾部拉动）；signal-close 含前视，永不进卡面 |

### 3.2 卡片与语义

- **18 张卡里只有卡 8 两端触发器齐备**；卡 1 已登记**不能进 M3**（趋势失效无可观察定义）；其余 16 张缺口是"作者没说"不是"还没查"。
- **卡 8 已完成 owner 审定**（2026-08-22「通过＋F 建议落卡」），卡面含：分母援引写入第 5 条、均线类型定案（SMA 仅为研究代理）、收益区间定性（描述性区间非止盈线）、执行时点登记。
- **全卡执行时点登记**已写进三份卡片文档边界（batch1 §四、batch2 边界声明、batch3 批次交付边界）：作者动作时点＝当日盘中/尾盘或次日开盘；任何日线重建的"次日开盘执行"是晚于作者的保守代理。
- **引用纪律**：只引 `sentences[start:end+1]`（**闭区间**），程序生成、机检通过；`text` 字段与 sentences 无内容分歧，不要"修语料"。
- M2 广度产出已完成：18 卡＋Base v3，32 个 hypothesis 家族 0 遗漏（复核脚本见旧接手文档 §3.4，仍有效）。

### 3.3 审卡对象的现状

| 批次 | 卡 | 状态 |
|---|---|---|
| batch2（`playbook-cards-batch2-2026-08-20.md`） | 7 确定性推仓 / 9 干净交易禁做T / 10 事件题材首入口 / 11 下跌先诊断 / 12 兑现窗口分层退出 | **待 owner 首审**（卡 8 已审毕） |
| batch3（`playbook-cards-batch3-2026-08-20.md`） | 13 逻辑只出候选 / 14 财报两步读法 / 15 题材容量下钻 / 16 点名利空降暴露 / 17 ETF 表达 / 18 退潮轻仓试错重入 | **待 owner 首审** |

batch1（卡 1–6）已有确认稿体例可循：`playbook-cards-confirmed-batch1-2026-08-20.md`。

---

## 4. 本阶段任务与流程

### 4.1 建议节奏（一次一张，不批量）

1. **（可选但推荐的第一步）机械抽取审卡包**：从两份卡片文档逐卡摘出——条款全文、未冻结清单、关键证据出处（aweme_id/sentences 跨度）、反例与边界、与其他卡的分离依据——汇成 `docs/research/yeren-system/card-review-pack-2026-08-XX.md`。**纯抽取不加新论断**，让 owner 一屏看一张卡。
2. owner 逐卡表态：
   - **「通过」** → 在确认稿登记该卡通过＋日期；
   - **点条号要改** → 研究者只改被点条目，改完复述差异请 owner 复核该卡；涉及语义的改动要检查 Base v3 是否需要联动（改完跑 32 家族覆盖核对）；
   - **deferred** → 允许跳过，标记"暂缓"，不阻塞其他卡。
3. 全部有结论后：更新 `playbook-cards-owner-review-2026-08-20.md` 交付表状态；产出 `playbook-cards-confirmed-batch2-2026-08-XX.md` 与 `playbook-cards-confirmed-batch3-2026-08-XX.md`（比照 batch1 确认稿体例，含"两条贯穿全稿的裁定"式的总边界）。

### 4.2 审卡期间的研究侧边界

- **不做新的语料检索**，除非 owner 就某张卡提出具体问题且定向小检索能回答（沿用 A/F 单元方法与引用纪律，结果只服务该卡，不外溢）。
- **不把审卡变成研究**：owner 若问"这张卡能不能回测"，答案引用 §3.1/§3.2 的既定盘点（§三逐卡资格表在 `m3-post-candidate-e-next-step-decision-2026-08-21.md`），不现场重做。
- owner 的修订涉及**删除/降级**证据时，按 V 版本政策处理（最新版进 Base，旧版降 phase_rule 只读存档），不物理删除。

### 4.3 分支剧本（常见情形）

| owner 说 | 研究者做 |
|---|---|
| 「通过」 | 登记确认（卡号＋日期＋"按现卡面"） |
| 「第 N 条改成 X」 | 只改第 N 条；引用机检；复述差异；Base v3 联动检查 |
| 「这条证据我不信」 | 不辩解；登记 owner 裁决与理由（owner 裁定权高于证据强度分层）；若牵动证据分类（candidate/stable_core）按 Base v3 定义落 |
| 「这张卡先放着」 | 标记 deferred，继续下一张 |
| 「520 那个 S8 我想授权研究假设」 | 停下审卡节奏，走新预注册流程（写文档→owner 点头→跑一次报一次）；审卡可之后继续 |

---

## 5. 工程坑（本 session 实测新增，务必照做）

- **永远按预注册全跨期加载面板、用参数限定窗口**（`load_priced_panel` 一次载 `PREREGISTERED_START_DATE..PREREGISTERED_END_DATE`，窗口用 start/end 参数传）。本 session 的调试探针把面板截到样本切分日，边界成交语义改变，造出 667 笔幻影流差异（814 vs 825 之谜），浪费了半小时排查。
- **引用机检正则的贪心陷阱**：`出处→「引文」` 必须紧邻（中间不得有其他反引号或「），否则自检脚本会把后面的无关引文配给出处产生误报。写卡面时出处前置、引文紧跟。
- `data/marketdata_pit/index.lock` 是 `SnapshotStore` 自己的 FileLock 残留（`store.py::_LOCK_NAME`），任何读进程都会创建，**不是外部活动，不要去"清理"它**。
- 旧坑仍有效：`ruff format` 只对本次改动文件跑；`pgrep -f` 等待循环会自匹配（改用 `/proc/<pid>` 探测）；codex 沙箱跑不了 pytest；大 JSON 工件别喂 `codex review`（先排除或用 `codex exec --sandbox read-only` 定向提问）；`data/yeren_research/` 整目录 gitignore，worklog/工件不进版本控制。
- 环境：`PY=/home/ps/anaconda3/envs/zhanglan/bin`；pytest 必带 `FEISHU_INTERACTIVE_ENABLED=false`；面向 owner 的回复用中文，代码/commit 英文，conventional commits。

---

## 6. 交付与停止条件

本 session 至少留下（按实际进度）：

1. 审卡包（若做了）与逐卡确认记录；
2. 两份确认稿（或其中一份，若 owner 只审完一批）＋交付表更新；
3. `data/yeren_research/worklog.jsonl` 追加（inputs/outputs/findings/`real_broker_orders=false`/`resume_from`）；
4. memory 更新（`MEMORY.md` 首条 hook ＋ `project-midterm-rearch-yeren-playbook-2026-08-12.md` 详情）；
5. 本地 commit（conventional，英文）；**push 等 owner 明示**。

**停止条件**：11 张卡全部有结论（通过／修改后通过／deferred），或 owner 中止；owner 不在场时**直接停**，不为凑产出制造工作（不重做 §3 的任何既定事实，不跑新一轮 codex 复验，不碰 520 的封存口径）。

---

## 7. 先读的文件（按顺序）

1. `CLAUDE.md`、`AGENTS.md` —— 四禁、主线次序、review 单轮上限
2. `docs/research/yeren-system/m3-owner-gate-results-and-three-runs-2026-08-22.md` —— 三个运行结果与 520 终态（§四有三个下一步选项的原文，本阶段已选 iii）
3. `playbook-cards-batch2-2026-08-20.md`、`playbook-cards-batch3-2026-08-20.md` —— 审卡对象本体
4. `playbook-cards-confirmed-batch1-2026-08-20.md` —— 确认稿体例范本（尤其开头"两条贯穿全稿的裁定"）
5. `docs/research/yeren-system/m3-post-candidate-e-next-step-decision-2026-08-21.md` §三 —— 逐卡 M3 资格盘点（owner 问"能不能回测"时引用）
6. `base-v3-spec-2026-08-20.md` —— 证据分类定义（stable_core/candidate/phase_rule）与 V 版本政策

---

## 8. 给 owner 的一句话

研究侧能自己跑的路已经跑到头了，520 封存在三层证据上；现在最有价值的事就是你对剩下 11 张卡的逐张审阅——你审到哪张，我支援到哪张，你不在场我就停。
