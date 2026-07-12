# SLV-1 前向试运营代码评审 Summary(2026-07-12)

- **范围**:`defensive_sleeve_forward.py`(新前向存活 runner)+ `push_sleeve_advisory.py`(新)+ `sleeve_trial_daily.sh`(新)+ `renderer.py::render_sleeve_advisory`(新方法)+ 两个测试文件。
- **方式**:codex CLI 不稳(既往 MCP 挂),按既定回退 → `/code-review high`(多 agent 工作流:4 finder + 12 verifier + reporter,18 agents)。
- **结果**:10 findings(6 CONFIRMED 正确性 + 4 cleanup)。**全部处置**如下,处置后测试 76 passed + ruff 全绿 + runner 端到端复跑一致。

| # | 级别 | 位置 | 问题 | 处置 |
|---|---|---|---|---|
| 1 | P0 | `sleeve_trial_daily.sh:19` | cron 非交互 shell 下 `source ~/.bashrc` 在交互守卫处提前 return,凭证永远加载不到 → 每日 pipeline 天天挂 | ✅ 改为 grep 提取所需 `export` 行 + `eval`,并对 4 个必需变量 fail-fast(exit 3) |
| 2 | P0 | `sleeve_trial_daily.sh:24` | 固定 7 天回看 → 停机 >7 天留永久静默数据洞,污染 kill-switch MDD/streak 账 | ✅ 摄取起点改为**店内最后 daily trade_date**(自愈式续传,任意停机长度全回填) |
| 3 | P0 | `defensive_sleeve_forward.py` | `load_or_register` 不校验 forward_start → 回填快照可静默重锚定评估网格 | ✅ 增加 forward_start 漂移 abort + 单测 |
| 4 | P1 | `sleeve_trial_daily.sh:32` | 注册 JSON 在 gitignored 目录,零溯源可重铸;新机器 pipeline 硬挂 | ✅ `git add -f` 注册 JSON 入库(见 commit),预注册获得 git 溯源 |
| 5 | P1 | `push_sleeve_advisory.py` | “幂等”仅靠 Feishu uuid(1 小时窗)→ 节假日重跑会把上周五的书当今天重发 | ✅ 本地 sent-marker(`sleeve_advisory_sent.json`,merge-write)按 as-of 去重 + `--force` + 单测 3 项 |
| 6 | P1 | `renderer.py:384` | kill-switch 阈值硬编码在治理性消息文本里,与预注册值可脱节 | ✅ 阈值参数化(mdd/bear/期数由 status JSON 的预注册块传入)+ 单测 |
| 7 | P2 | `defensive_sleeve_forward.py:353` | `_run_arm` 逐字复制 science gate 的实现,可静默分叉 | ✅ 改 import `_sg_run_arm` 复用 + `_forward_arm` 薄封装 |
| 8 | P2 | `defensive_sleeve_forward.py` | `run_forward` ~139 行超函数上限,混多职责 | ✅ 抽出 `_accrued_window_arms()`(回测块整体外移) |
| 9 | P2 | `sleeve_trial_daily.sh:21` | `LOG_DIR` 死变量 | ✅ 删除;`mkdir -p logs` 保留并注释理由(手动跑时保证 cron 重定向目标存在) |
| 10 | P2 | `defensive_sleeve_forward.py:209` | 每日全窗重算,工作量随窗口线性增长 | ✅ 接受并在 `_accrued_window_arms` docstring 披露理由:月度节奏下数年内窗口仍小,确定性全量重算 < 增量状态的腐化风险 |

**未处置项**:无。
