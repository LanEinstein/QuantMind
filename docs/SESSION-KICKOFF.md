# QuantMind 开工协议(每个新 session 通用)

> **用法**:新 session 开始时,对 Claude 说"读 `docs/SESSION-KICKOFF.md` 开工"(或直接粘贴下方代码块)。
> 本协议**通用、不写死 Phase** —— 靠"最早含 todo 的字母 Phase"自动定位,所以每次都用同一段即可无缝衔接上一次工作。
> 与 `CLAUDE.md §1`(进度管理协议)+ `§2/§2.0`(红线)一致;此处把"检查上次节点 / 开工改状态 / 完成改 done+记录 / 末尾一句话指下一步"显式拎出,确保每次可靠触发。

---

```
你在 QuantMind 项目(/home/ps/papers/QuantMind)继续工作。本项目以 docs/plan.html 为进度 SSoT,
严格按下列协议无缝衔接上一次工作——先读、再动、勤记账:

【1. 开工前调查(动手前必做)】
- 读 docs/plan.html(SSoT:TASKS 数组 + SESSION_LOG + #protocol)、CLAUDE.md(§1 协议 + §2 红线)、
  记忆 MEMORY.md。
- 看 SESSION_LOG 顶部条目的 next 字段(上次指向的下一步);grep TASKS 里 status:"doing"/"blocked"
  找在途任务,确认上次停在哪。
- 确定本次活动 Phase = 最早一个含 status:"todo" 的字母 Phase;该 Phase 内所有 depends 已满足的任务
  = 本次 session 全套范围(整个 Phase,不是单个子任务)。依赖未满足的标 blocked + notes 写 blocked_by:。
- 先把"上次停在哪 / 本次活动 Phase / 要做哪些任务"讲我一句,再开工。

【2. 开工即更新状态】把本次要做的任务在 TASKS 数组里 status 改 "todo"→"doing" + 填 session_date:"YYYY-MM-DD"。

【3. 每完成一个任务】
- TDD 实现(测试先行;非 risk 覆盖率>70%,risk>95%)。
- commit 前本地门禁全绿:pytest + ruff + scripts/redline-check.sh(动到前端再加 npm run type-check && npm run test)。
  codex 绝不自动跑,只在我明说时跑。
- 一任务一 feature commit;然后该任务 status 改 "doing"→"done" + 回填真实 commit:(7 位 hash)
  + notes:(做了什么+为什么,1-2 句)。

【4. 改决策边界先 amendment】任何偏离已锁红线(§2 / docs/decisions/)的行为,先写
   docs/decisions/*-amendment-YYYY-MM-DD-{原因}.md 再改代码;无 amendment 的行为差异=违规。

【5. 结束 session(整个 Phase 做完或确实无法继续后)】一次性 docs-only commit:SESSION_LOG 顶部
   追加一条覆盖整个 Phase 的 {date,session,owner,state_in,actions(每任务+commit hash),commit,next};
   "修订记录"加一行;next 字段用一句话指向下一步;然后 push origin main。

【红线】遵守 CLAUDE.md §2(永禁真实下单 / 飞书人工 / 127.0.0.1 / LLM 不写决策 / RiskEngine 纯函数 /
   人工 gate / config runtime 不可改)+ §2.0 双线重构新红线(PIT 数据可复现 / InstructionPlan 单一构造点)。
   能自动化的不要让我手动干预;需我拍板的决策点停下来问我。

现在执行第 1 步:告诉我上次停在哪、本次活动 Phase、要做哪些任务,然后开始。
```

---

**备注**
- 第 5 步默认带 `push origin main`。若想保持"push 由 owner 手动控制",删掉该半句即可。
- 当前会自动落到 **Phase K**(SESSION_LOG 顶部条目 `next` 已指向 K-001;Phase K 全部 `todo`)。
- 进度状态以 `plan.html` 的 `TASKS` 数组 `status` 字段为准(不是浏览器 localStorage);改状态 = 编辑该数组。
