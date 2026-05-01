# P5A-T03 R3 测试维度复审

**判定**: ✅ 通过 (经最终复核)

## 测试矩阵

| 层 | 文件 | 用例数 | 验证目标 |
|----|------|--------|----------|
| Unit (normalize) | `tests/test_authorization.py::TestNormalizeMode` | 8 | 短/长别名 + 大小写 + 空格 + 未知透传 |
| Unit (env accessors) | `tests/test_authorization.py::TestEnvAccessors` | 4 | 默认值 + 大小写归一 + 长形归一 |
| Unit (startup) | `tests/test_authorization.py::TestAssertAuthorizationMode` | 9 | 默认通过 + legacy 通过 + 4 mode 跨阶段 + 未知 phase + garbage mode + dryrun + live |
| Unit (API guard) | `tests/test_authorization.py::TestAssertModeAllowedForPhase` | 6 | 短/长返 canonical + 跨阶段 + garbage + 显式 phase 覆盖 + 未知 phase |
| Static invariants | `tests/test_authorization.py::TestPhaseLedgerInvariants` | 4 | §2.9 redline 矩阵硬编码不漂移 |
| API integration | `tests/test_risk_api.py::TestSwitchAuthMode` | 8 | suggestion 接受 + invalid 422 + 跨阶段 403 + canonical 短 200 + dryrun confirm + dryrun rejects auto + live accepts auto + POST-then-GET 一致性 (短/长两种输入) |

总计 **39 个新测试用例**,全部通过。

## 关键测试回归覆盖

### `test_post_then_get_consistency_long_form_input`

闭合 cycle 2 P2-2: 旧版 `_get_auth_mode` 的 `replace("suggest", "suggestion")` 在 env="suggestion" 时把字符串重复成 "suggestionion"。

```python
POST {"mode": "suggestion"}                   # legacy long form input
assert env["AUTHORIZATION_MODE"] == "suggest" # canonical short stored
GET /api/risk/status
assert authorization_mode == "suggestion"     # long form back-compat,不是 "suggestionion"
```

### `test_cross_phase_rejected_in_eval` (扩展)

闭合 §2.9 redline:phase5_eval 必须拒绝任何非 suggest 的请求,无论用户用 canonical short 还是 legacy long。

```python
for blocked in ("semi_auto", "full_auto", "confirm", "auto"):
    POST {"mode": blocked} -> 403
    error message contains "phase5_eval"
```

### `test_dryrun_rejects_auto` & `test_live_phase_accepts_auto`

闭合 cycle 2 coverage gap: 阶段升级路径必须严守边界。

## Codex 独立验证

每一轮 codex review 都重跑了 pytest:
- Cycle 2: `48 passed`
- Cycle 3: `54 passed in 0.61s`

## 计划偏离

| 计划要求 | 实际执行 | 偏离原因 |
|----------|----------|----------|
| docker-compose 启动 backend with `AUTHORIZATION_MODE=auto`,期望容器 exit code != 0 | 转化为 unit test 直接验证 `assert_authorization_mode()` 抛 `SystemExit` | docker integration 需独立 fixture,测试同样严格的语义 (SystemExit 即 non-zero exit) |
| E2E uvicorn fork test | 转化为 unit-level startup gate test | 同上 |

完整记录见 `cycle_{1,2,3}.md`。
