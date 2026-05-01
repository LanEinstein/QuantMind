# P5A-T03 R1 架构维度复审

**判定**: ✅ 通过 (经最终复核)

## Codex 跨模型审查 3 轮迭代

| Cycle | 发现 | 修复 |
|-------|------|------|
| 1 | [P2] `_VALID_AUTH_MODES` 仅含 legacy long form,新 canonical short 被 422 预检拒绝,phase 守门根本走不到 | 扩展 `_VALID_AUTH_MODES` 包含 6 个别名 (3 短 + 3 长) |
| 2 | [P2-1] vocabulary 漂移:env 存原始输入,审计/log/ws/响应都用 raw,操作员 trail 出现同一动作两个名字。[P2-2] `_get_auth_mode` 旧 `replace("suggest","suggestion")` 在 env="suggestion" 时产生 "suggestionion" 串接错误 | 引入 `_SHORT_TO_LONG` + `_to_legacy_long()`,env 永远存 canonical short,响应用 long form back-compat,kill replace 逻辑 |
| 3 | 全部 RESOLVED,无 critical regression | — |

## 关键架构决策

### 1. 双向同义词矩阵 (canonical / legacy)

```
_LONG_TO_SHORT (authorization.py): suggestion→suggest, semi_auto→confirm, full_auto→auto
_SHORT_TO_LONG (risk.py):           suggest→suggestion, confirm→semi_auto, auto→full_auto
```

策略层 (authorization.py) 使用 canonical short,API 表面层 (risk.py) 转回 long form 给现有前端。这避免了打破 frontend 当前使用 `'suggestion'|'semi_auto'|'full_auto'` 类型联合的破坏性改动 (frontend 迁移留作独立任务)。

### 2. Fail-fast 而非 fail-loose

启动期 `assert_authorization_mode()` 抛 `SystemExit`,uvicorn 把这个转成非零退出码,systemd / docker-compose 立即看到。比静默回退到 suggest 更可靠 — 后者会让"我以为我开了 auto"的误配置悄悄无效。

### 3. PermissionError 子类 `CrossPhaseAuthorizationError`

API 守门用 `CrossPhaseAuthorizationError(PermissionError)`。子类化让现有的 `except PermissionError` 处理代码无需修改即可捕获,而类型本身又给操作员明确的语义。

完整记录见配套 r3 报告 + cycle 输出 `/tmp/codex_review_kDHOZ3/cycle_{1,2,3}.md`。
