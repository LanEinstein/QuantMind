# QuantMind Phase 5 — Next Work Plan (2026-05-01)

> 目的：替代 `checkpoint-2026-04-27-codex-review-cleared.md`，把当前 Phase 5
> 状态从“已通过 checkpoint”转为“可执行的下一步工作计划”。本计划基于
> 2026-05-01 在本机工作树、测试基线和部署前置状态的复核结果。

---

## 0. TL;DR

| 项 | 当前结论 |
|---|---|
| 基线提交 | `a5f1b9d` `fix(deploy): forward shell-env LLM keys ...` |
| 工作树 | Phase 5 diff 未提交；禁止自动 commit，必须等用户明确授权 |
| 代码门禁 | Phase 5 + `/codex-review` 5 轮 CRITICAL / HIGH / WARN 已清零 |
| 本机复核 | pytest / type-check / vitest / AgentDebate e2e / frontend build 均通过 |
| 全量 Playwright | 66 passed / 3 failed，仍为既有 `portfolio.spec.ts` selector 问题 |
| 立即优先级 | 先清理部署前小风险，再 commit，再部署冒烟，再 V.5 首次分析 |

推荐顺序：

1. **P0 小收口**：修 `R4-M1`、修 `daily-check.sh` 备份目录默认值、处理 `.codex` untracked。
2. **P1 复跑基线**：pytest / type-check / vitest / AgentDebate e2e / frontend build。
3. **P2 等用户授权 commit**：建议单 commit。
4. **P3 部署冒烟**：重建 infra 让 MongoDB/Redis 绑定 `127.0.0.1`，创建私有 env，安装 systemd/nginx。
5. **P4 V.5 首次分析**：加入 watchlist，触发 `analyze-now`，检查 signals/history。
6. **P5 后续 backlog**：`R3-M1` Playwright selector 稳定化，截止 2026-05-23。

---

## 1. 当前已验证事实

### 1.1 代码与测试状态

2026-05-01 复核命令结果：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q
# 669 passed, 11 skipped, 1 warning

cd frontend
npm run type-check
# 0 errors

npm run test -- --run
# Test Files 10 passed / Tests 81 passed

npx playwright test e2e/agent-debate.spec.ts --workers=1 --reporter=line
# 4 passed

npm run build
# built successfully; only Sass deprecation + chunk size warnings
```

全量 Playwright：

```bash
cd frontend
npx playwright test --workers=1 --reporter=line
# 66 passed, 3 failed
```

3 个失败仍是既有 portfolio selector strict-mode 问题：

- `frontend/e2e/portfolio.spec.ts:20`
- `frontend/e2e/portfolio.spec.ts:100`
- `frontend/e2e/portfolio.spec.ts:121`

这些失败不是 Phase 5 AgentDebate / SSE / analysis history 回归，但正式评估期前建议另开小任务修正。

### 1.2 工作树状态

当前 Phase 5 diff 尚未提交。checkpoint 重写后，旧文件路径已废弃：

- 旧文件：`docs/checkpoint-2026-04-27-codex-review-cleared.md`
- 新文件：`docs/phase5-next-work-plan-2026-05-01.md`

必须保持红线：

- 不自动 commit。
- 不自动 push。
- 不把 `.env`、真实 LLM key、`/home/ps/.config/quantmind/llm.env` 纳入 git。
- 不把 MongoDB/Redis 端口暴露回 `0.0.0.0`。
- 不把 `AUTHORIZATION_MODE` 改成 `auto`。

### 1.3 部署前置实际状态

本机部署前置尚未完成：

| 检查项 | 当前结果 | 影响 |
|---|---|---|
| `quantmind-backend.service` | systemd unit 未安装 | backend 无法随系统启动 |
| `http://127.0.0.1:8000/api/health` | 不通 | backend 当前未运行 |
| `/home/ps/.config/quantmind/llm.env` | 不存在 | systemd unit 即使安装也会启动失败 |
| `.env` 权限 | `664` | 部署 SOP 要求 `600` |
| MongoDB/Redis 当前容器端口 | `0.0.0.0:27017/6379` | 与新 `docker-compose.yml` 的 loopback 安全约束不一致 |
| `~/.local/state/quantmind/backups` | 不存在 | 新备份默认目录未初始化 |
| 仓库内旧备份 | `backups/mongodump-quantmind-20260424T142232Z.gz`，权限 `664` | 已被 `.gitignore` 忽略，但应迁移/收紧权限 |

注意：`docker compose config` 已显示目标配置是 `127.0.0.1`，但运行中的旧容器尚未重建，所以 `docker compose ps` 仍显示 `0.0.0.0`。

---

## 2. P0：提交前小收口

目标：在不扩大 Phase 5 范围的前提下，消除已经确认的 deploy/readiness 小风险。

### 2.1 修复 R4-M1：catch-up 批量查询

**原因**：`backend/data/analysis_scheduler.py:_compute_catch_up_targets()` 当前对 watchlist 中每只股票各发一次 `query_signals(stock_code=code, days=1)`。watchlist 上限 500 时，启动 catch-up 会产生数百次 Mongo round-trip。

**截止日期**：2026-05-02。

**建议实现**：

1. 在 `_compute_catch_up_targets()` 中一次性获取 watchlist codes。
2. 用单次 Mongo 查询取当天已覆盖的 signals。
3. 构建 `covered_codes = {signal["stock_code"] ...}`。
4. 返回 `[code for code in codes if code not in covered_codes]`。

优先实现方式：

- 如果沿用 `MongoDBManager.query_signals(stock_code=None, days=1)`，需要注意它按 UTC 日期计算 cutoff，不等价于上海交易日当天。
- 更稳妥的方式是新增一个精确查询方法，例如：

```python
async def query_signals_for_trade_date(
    self,
    trade_date: str,
    stock_codes: list[str],
) -> list[dict[str, Any]]:
    ...
```

查询条件：

```python
{
    "trade_date": trade_date,
    "stock_code": {"$in": stock_codes},
}
```

该查询可复用现有 `trading_signals` 的 `(stock_code, trade_date)` unique index。虽然 `$in`
会按 stock_code 扩展扫描，但一次 round-trip 足以解决当前 performance backlog。

**测试要求**：

- 更新 `tests/test_analysis_scheduler_catchup.py` 的 fake Mongo，使其支持批量查询。
- 新增断言：3 只股票只调用一次 signal 查询。
- 保留已有行为：
  - cutoff 前返回空。
  - 周末返回空。
  - 空 watchlist 返回空。
  - stale signal 不算当天覆盖。
  - 只有缺失股票进入 catch-up。

**验收命令**：

```bash
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q tests/test_analysis_scheduler_catchup.py tests/test_analysis_scheduler.py
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q
```

### 2.2 修正 `daily-check.sh` 默认备份目录

**原因**：`scripts/backup.sh` 默认写到 `~/.local/state/quantmind/backups`，但
`scripts/daily-check.sh` 当前仍默认检查 `$ROOT/backups`。部署后日检会看不到真实备份。

**改动范围**：

- `scripts/daily-check.sh`
  - 默认 `BACKUP_DIR="${HOME}/.local/state/quantmind/backups"`。
  - 注释同步说明备份默认在仓库外。
- 可选修正 `scripts/backup.sh` 顶部注释中“default repo `backups/`”的过期描述。

**验收命令**：

```bash
./scripts/daily-check.sh
```

在 backend 尚未部署前，预期仍会因为 dashboard 不通返回 WARN；但 recent backups 部分应指向新的默认目录。

### 2.3 处理 `.codex` untracked

当前 `.codex` 是 0-byte 本地文件，仍显示 untracked。

建议：

```gitignore
# Codex local scratch/cache
.codex
.codex/
```

这样后续 commit 不会误收本地 Codex 文件。若用户明确允许，也可以直接删除空 `.codex` 文件；但删除属于清理本地文件，默认不作为必要动作。

### 2.4 可选：备份权限与旧备份迁移

部署前建议执行：

```bash
mkdir -p ~/.local/state/quantmind/backups
chmod 700 ~/.local/state/quantmind/backups

# 若要保留旧演练备份：
mv backups/mongodump-quantmind-20260424T142232Z.gz ~/.local/state/quantmind/backups/
chmod 600 ~/.local/state/quantmind/backups/mongodump-quantmind-20260424T142232Z.gz
```

说明：

- `mv` 旧备份会改变本机文件布局，但不会影响 git，因为 `backups/` 已 ignore。
- 如果不需要保留 578-byte 演练备份，也可以在用户授权后删除。

---

## 3. P1：复跑提交前基线

P0 完成后，按以下顺序复跑：

```bash
cd /home/ps/papers/QuantMind
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q

cd frontend
npm run type-check
npm run test -- --run
npx playwright test e2e/agent-debate.spec.ts --workers=1 --reporter=line
npm run build
```

可选复核：

```bash
cd frontend
npx playwright test --workers=1 --reporter=line
```

预期仍是 66/69，3 个 portfolio 旧失败。如果失败数量扩大，必须先调查，不进入 commit/deploy。

---

## 4. P2：Commit 决策

CLAUDE.md 与项目 git 约束禁止自动 commit。必须等用户明确授权。

### 4.1 推荐策略：单 commit

原因：

- Phase 5 diff 已作为一个整体通过 5 轮 `/codex-review`。
- 代码、测试、部署模板、review 文档彼此强耦合。
- 拆 commit 需要 hunk 级 staging，容易制造人为错配。

建议 commit message：

```text
feat(phase5): production-readiness sprint and codex-review gate

- persist AnalysisRecord history/detail data and expose history API
- add live AnalysisStreamHub jobs/SSE pipeline
- add deployment templates for systemd/nginx/docker compose
- add monitoring dashboard, alerter, backup, and daily checks
- harden LLM preflight, scheduler catch-up, and circuit-breaker approval flow
- resolve codex-review R1-R5 critical/high/warning findings
- keep medium backlog documented with owners and deadlines

Tests:
- pytest 669 passed, 11 skipped
- frontend type-check passed
- vitest 81 passed
- AgentDebate Playwright 4 passed
- frontend build passed
```

### 4.2 替代策略：多 commit

仅在用户要求更细审计时采用：

1. base Phase 5 features
2. R1 architecture/data-flow fixes
3. R2 UX/a11y fixes
4. R3 tests/correctness fixes
5. R4 perf/a11y fixes
6. R5 security/ops fixes

风险：

- 多个文件跨轮次修改，拆分会花更多时间。
- 需要 hunk staging，容易遗漏测试或文档片段。

---

## 5. P3：部署冒烟计划

部署冒烟需要用户参与，部分步骤需要 sudo，且需要真实 LLM key。

### 5.1 部署前安全收口

1. 确认当前 compose 目标配置：

```bash
docker compose config
```

必须看到：

```text
127.0.0.1:27017:27017
127.0.0.1:6379:6379
```

2. 重建 infra，让当前运行容器应用新端口绑定：

```bash
docker compose up -d --force-recreate mongodb redis
docker compose ps
docker ps --format '{{.Names}} {{.Ports}}'
```

验收：MongoDB/Redis 端口显示 `127.0.0.1`，不得显示 `0.0.0.0`。

3. 初始化私有 LLM env：

```bash
mkdir -p /home/ps/.config/quantmind
cp deploy/llm.env.example /home/ps/.config/quantmind/llm.env
chmod 600 /home/ps/.config/quantmind/llm.env
```

然后手动填入真实 key。禁止把真实 key 写入仓库。

4. 收紧 `.env` 权限：

```bash
chmod 600 /home/ps/papers/QuantMind/.env
```

5. 初始化备份目录：

```bash
mkdir -p ~/.local/state/quantmind/backups
chmod 700 ~/.local/state/quantmind/backups
```

### 5.2 systemd backend

```bash
sudo cp deploy/quantmind-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantmind-backend
sudo systemctl status quantmind-backend --no-pager
```

本地健康检查：

```bash
curl -sS http://127.0.0.1:8000/api/health
curl -sS "http://127.0.0.1:8000/api/analysis/history?limit=1"
```

若失败，优先看：

```bash
journalctl -u quantmind-backend --no-pager -n 100
tail -100 logs/backend.stderr.log
```

### 5.3 frontend + nginx + HTTPS

构建：

```bash
cd /home/ps/papers/QuantMind/frontend
npm run build
```

安装 nginx/mkcert 配置：

```bash
sudo apt install mkcert libnss3-tools nginx
mkcert -install

sudo mkdir -p /etc/ssl/quantmind
cd /etc/ssl/quantmind
sudo mkcert -cert-file quantmind.local.crt -key-file quantmind.local.key \
    quantmind.local localhost 127.0.0.1

grep -q 'quantmind.local' /etc/hosts || \
  echo '127.0.0.1 quantmind.local' | sudo tee -a /etc/hosts

sudo cp /home/ps/papers/QuantMind/deploy/nginx-quantmind.conf \
    /etc/nginx/sites-available/quantmind
sudo ln -sf /etc/nginx/sites-available/quantmind /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

HTTPS 验收：

```bash
curl -k https://quantmind.local/api/health
curl -k "https://quantmind.local/api/analysis/history?limit=1"
```

浏览器验收：

- `https://quantmind.local/agent-debate`
- 选择股票后能发起分析。
- SSE 过程中能看到实时辩论文本。
- 完成后 history/detail 能回放。

### 5.4 备份与日检

备份：

```bash
cd /home/ps/papers/QuantMind
./scripts/backup.sh
ls -l ~/.local/state/quantmind/backups
```

恢复演练：

```bash
docker compose exec -T mongodb mongorestore \
  --archive --gzip \
  --nsFrom='quantmind.*' \
  --nsTo='quantmind_restore_test.*' \
  < ~/.local/state/quantmind/backups/<archive>.gz
```

日检：

```bash
BASE_URL=https://quantmind.local ./scripts/daily-check.sh
```

部署冒烟通过标准：

- systemd backend active。
- MongoDB/Redis healthy 且仅绑定 `127.0.0.1`。
- nginx `-t` 通过。
- HTTPS `/api/health` 200。
- `/api/analysis/history?limit=1` 200。
- backup 可生成，权限 600。
- restore 到 `quantmind_restore_test` 成功。
- daily-check 无 critical。

### 5.5 重启自愈

```bash
sudo reboot
```

重启后 15 分钟内检查：

```bash
cd /home/ps/papers/QuantMind
docker compose ps
sudo systemctl status quantmind-backend --no-pager
curl -k https://quantmind.local/api/health
BASE_URL=https://quantmind.local ./scripts/daily-check.sh
```

---

## 6. P4：V.5 首次分析初始化

前提：

- 部署冒烟通过。
- 真实 LLM key 可用。
- `AUTHORIZATION_MODE=suggest`。
- 风控/审批链路未绕过。

初始化 watchlist：

```bash
BASE_URL="https://quantmind.local"
for stock in \
  '{"code":"600519","name":"贵州茅台"}' \
  '{"code":"000858","name":"五粮液"}' \
  '{"code":"601318","name":"中国平安"}' \
  '{"code":"000001","name":"平安银行"}' \
  '{"code":"300750","name":"宁德时代"}'; do
  curl -sk -X POST "$BASE_URL/api/watchlist" \
    -H "Content-Type: application/json" -d "$stock" | python -m json.tool
done
```

触发分析：

```bash
curl -sk -X POST "$BASE_URL/api/watchlist/analyze-now" | python -m json.tool
```

验收：

```bash
curl -sk "$BASE_URL/api/analysis/signals?days=1" | python -m json.tool
curl -sk "$BASE_URL/api/analysis/history?limit=5" | python -m json.tool
```

成功标准：

- 每只 watchlist 股票至少有一条当天 analysis record 或明确失败 record。
- 前端 AgentDebate history 能看到分析记录。
- 若 LLM provider 全挂，接口应返回 503，并有可追踪告警，而不是静默成功。
- 若某只股票失败，scheduler 继续处理剩余股票。

---

## 7. P5：后续 backlog

### 7.1 R3-M1：Playwright selector 稳定化

**截止日期**：2026-05-23。

目标：

- 给 AgentDebate 关键 app-level 控件加稳定 `data-testid`。
- e2e 优先使用 `getByRole` / `getByLabel` / `getByTestId`。
- 只在 Element Plus dropdown 无可靠语义 handle 时保留 `.el-select-dropdown__item`，并写注释说明。

建议改动：

- `frontend/src/views/AgentDebate.vue`
  - `data-testid="agent-debate-layout"`
  - `data-testid="stock-selector"`
  - `data-testid="start-analysis-button"`
  - `data-testid="analysis-history-list"`
  - `data-testid="analysis-history-item"`
  - `data-testid="debate-content"`
- `frontend/e2e/agent-debate.spec.ts`
  - 移除 `.debate-layout`、`.history-item`、`.stock-selector` 依赖。

验收：

```bash
cd frontend
npm run type-check
npx playwright test e2e/agent-debate.spec.ts --workers=1 --reporter=line
```

### 7.2 Portfolio e2e 旧失败

这不是 Phase 5 阻塞项，但会持续污染全量 Playwright。

建议另开小修：

- 将 `page.locator('text=代码')` 改为限定在 position table 内。
- 将 drawer 断言限定在 `.el-drawer` 内。
- 避免 strict mode 命中多个相同中文 label。

验收：

```bash
cd frontend
npx playwright test e2e/portfolio.spec.ts --workers=1 --reporter=line
npx playwright test --workers=1 --reporter=line
```

---

## 8. 风险与回滚

### 8.1 P0 代码修复风险

| 风险 | 缓解 |
|---|---|
| catch-up 批量查询误判当天覆盖 | 用上海时区 `trade_date` 精确匹配，不用 UTC cutoff 代替 |
| 新 Mongo helper 影响其他查询 | 新增专用方法，不改 `query_signals()` 现有契约 |
| 测试 fake 与真实 Mongo 行为不一致 | 测试断言查询参数、返回缺失集合、保留 stale case |

### 8.2 部署风险

| 风险 | 缓解 |
|---|---|
| 重建 compose 影响现有数据 | `docker compose up -d --force-recreate` 不删除 named volumes；禁止 `down -v` |
| systemd 因 env 缺失启动失败 | 安装前确认 `.env` 与 `llm.env` 存在且权限正确 |
| nginx HTTPS curl 失败 | 先确认 backend loopback health，再查 nginx `-t` 与 cert/hosts |
| SSE 被 nginx buffering | 保留 `proxy_buffering off` 和 `X-Accel-Buffering no` |
| 重启后服务未恢复 | 必跑重启自愈，不通过则不进入 V.5 |

### 8.3 回滚原则

- 代码回滚只通过新 commit 或用户明确授权的 git 操作执行。
- 禁止 `git reset --hard`、`git checkout -- <file>` 这类会丢弃用户改动的命令，除非用户明确要求。
- 部署回滚优先：
  - `sudo systemctl disable --now quantmind-backend`
  - 恢复 nginx site symlink 或停用 quantmind site
  - `docker compose up -d mongodb redis` 保留 volumes

---

## 9. 最终完成定义

Phase 5 下一步工作完成，需要同时满足：

1. P0 小收口已完成并有测试覆盖。
2. pytest / type-check / vitest / AgentDebate e2e / frontend build 全通过。
3. Phase 5 diff 已按用户授权 commit。
4. MongoDB/Redis 运行态端口只绑定 `127.0.0.1`。
5. systemd backend active，nginx HTTPS 可访问。
6. backup + restore + daily-check 通过。
7. 重启自愈通过。
8. V.5 watchlist 首次分析有 history/signals 可查。
9. `R3-M1` 已有排期，不晚于 2026-05-23 完成。

---

## 10. 快速命令清单

本地验证：

```bash
cd /home/ps/papers/QuantMind
/home/ps/anaconda3/envs/zhanglan/bin/pytest -q

cd frontend
npm run type-check
npm run test -- --run
npx playwright test e2e/agent-debate.spec.ts --workers=1 --reporter=line
npm run build
```

部署前置：

```bash
cd /home/ps/papers/QuantMind
docker compose up -d --force-recreate mongodb redis
docker compose ps

mkdir -p /home/ps/.config/quantmind
cp deploy/llm.env.example /home/ps/.config/quantmind/llm.env
chmod 600 /home/ps/.config/quantmind/llm.env
chmod 600 .env

mkdir -p ~/.local/state/quantmind/backups
chmod 700 ~/.local/state/quantmind/backups
```

systemd：

```bash
sudo cp deploy/quantmind-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now quantmind-backend
sudo systemctl status quantmind-backend --no-pager
```

HTTPS smoke：

```bash
curl -k https://quantmind.local/api/health
curl -k "https://quantmind.local/api/analysis/history?limit=1"
```

V.5：

```bash
curl -sk -X POST "https://quantmind.local/api/watchlist/analyze-now" | python -m json.tool
curl -sk "https://quantmind.local/api/analysis/history?limit=5" | python -m json.tool
```
