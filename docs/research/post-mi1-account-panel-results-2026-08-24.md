# MI-1 后续:前端分线账本面板接线 — 工程结果

> 日期: 2026-08-24 · 执行: Fable 5(主执行)+ Codex(方案讨论一次 + 唯一一轮 review)
> 依据: `KickoffPrompts/POST-MI1-frontend-panel-and-steady-state-handoff-2026-08-24.md`
> 性质: 工程单元(无预注册)。`real_broker_orders = false`(永久)。

## 一、交付总览

| 交付物 | 状态 | 关键文件 |
|---|---|---|
| 只读 API `GET /api/portfolio/lines` | ✅ | `scripts/account_api.py`(FastAPI 单文件,127.0.0.1:8001) |
| 共享 payload 构造 | ✅ | `backend/portfolio/lines.py::account_view_payload`(CLI `--json` 与 API 同源) |
| 最近账本行读取 | ✅ | `backend/portfolio/mirror_ledger.py::recent_rows`(追加序,最新在前) |
| 前端页面 `/account-lines` | ✅ | `frontend/src/views/AccountLines.vue` + `api/accountLines.ts` + `utils/accountLines.ts` |
| 路由 + 菜单(账本与成交组) | ✅ | `frontend/src/router/{index,menu}.ts` |
| 运维说明 | ✅ | `docs/runbook/systemd-setup.md` §10a |

## 二、方案裁决(Codex 讨论一次,自决)

方案 A(独立最小 API)胜出:账本 append 后即时可见;vite dev/preview 的 `/api` 代理
本就指向 `localhost:8001`,零 vite 改动;只 import 账本读取模块与 drift 函数,不碰
`backend.main`/Mongo/Redis/调度。方案 B(静态 JSON)多一道 cron+部署耦合且非实时,弃。

Codex 提出并已落实的坑:响应必须是前端 envelope `{status,data,error}`(否则
`apiGet` 返回 undefined);payload 直接复用 `account_view --json` 形状(含计算属性
`cost_value`);"最近行"按物理追加序而非回放 `effective_at` 序;三种 kind 字段各异,
不强行统一 schema;drift 端点同步 `def`,保留 `uncovered_fills`;新建 `/account-lines`
独立路由,不改绑旧双线 API/WS 的 `/portfolio`;不含市价/未实现盈亏/整本账/
`external_trade_id`/运行期状态/写端点。

## 三、页面内容(owner 体验)

- **R 线卡**:现金、本金申报状态(未申报时明示"现金仅为累计变动")、持仓成本合计、
  已入账成交笔数、持仓表(代码/股数/含费均价/含费成本)。
- **Z 线卡**:实现收益累计(主数字)+ 打新卖出/转债卖出/现金收益分项 + 中签成本
  (标注"非损益")+ 记录数。
- **最近成交与修正**:最新在前,按回报顺序;成交行显示买卖/股数/价格/净额/费用合计,
  资金行显示入金/出金,修正行显示 ±股数(此刻生效)。
- **月度执行偏差**:可比笔数/未覆盖笔数/偏差元/偏差 %(正 = 实际更差)。
- 账本损坏(回放失败)时页面顶部红条显示具体错误行,不静默。

## 四、验证

- 后端:`tests/portfolio/test_account_api.py` 6 例(envelope 形状/最近行顺序与字段/
  limit/drift/账本缺失/账本损坏);pytest 7490 → **7496 passed / 14 skipped**(旧 X-022 页面数量锁按授权 14→15 登记 AccountLines.vue);ruff 干净。
- 前端:`utils/__tests__/accountLines.spec.ts` + `views/__tests__/AccountLines.spec.ts`
  + menu.spec 更新;type-check / vitest **184 passed** / build 全绿。
- Playwright 体检(真实 API + `vite preview`):真实账本态(现金 15 万/无持仓/1 条入金行)、
  富数据态(2 持仓/4 账本行/1 行 drift,1440 与 800 宽)、错误态三张截图均正常;
  菜单项与页面标题正确;浏览器 console 仅有 AppShell 对旧 `/api/system-status/*`
  的 404(旧后端休眠所致,与本页无关,已在 runbook 注明)。

## 五、Codex 一轮 review(`codex review --uncommitted`)

0 P0 / 0 P1 / **1 P2,记录不修**:端点分三次读镜像账本(`build_account_view` /
`recent_rows` / `monthly_drift`),若监听器恰在毫秒级窗口内追加一行,单次响应可能
"最近行已含新成交而现金/持仓仍是旧账"。判断:面板只读、owner 点一次刷新即自愈,
且修法需把三个按路径读取的接口全改成按行快照传参——为一个不会造成任何错误决定
的瞬态,不值得改三处已正确的代码(反过度防御第 2 禁)。登记于此,不阻塞。

## 六、稳态运营检查(2026-08-24 上午)

- cron 上次运行 2026-08-23 13:07(手动补跑)`sleeve advisory sent (asof 20260821)`;
  今日 17:40 首次周一自动跑,`--dry-run` 当前判定 `event=silent`(book 未变)。
- 监听器:手动实例存活(pid 1893751,09:55 启动);`systemctl is-active
  quantmind-reconcile` = **inactive** → owner 尚未执行 §四 安装命令,提醒一次。
- 镜像账本:现金 150,000 已申报,无持仓,0 成交;Z 账本空。

## 七、不做的事(本单元)

无鉴权/多用户/systemd unit(面板按需手动起);不复活旧运行时;不为 AppShell 旧状态
端点补桩;不新增策略、不动冻结物。
