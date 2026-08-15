# AGENTS.md — codex 工作守则

本文件是 codex(及其他编码 agent)在本仓库工作的唯一守则,与 `CLAUDE.md` 同步维护。
项目背景:QuantMind 中长线 A 股投研系统,2026-08-12 完全重构中;
行动纲领 = `docs/research/midterm-rearch-action-plan-2026-08-12.md`;
2026-08-12 之前的旧红线/冻结原则已全部作废(归档在 `docs/archive/`),不要引用它们来阻止改动。

## 唯一底线

永禁真实券商程序化下单。系统只维护模拟盘;凡是往真实交易方向写的代码一律拒绝并上报。

## 主线次序(owner 2026-08-15 重申)

**先复刻「全能的野人」的交易系统、交易逻辑、操作手法(战法卡片 + 总体仓位状态框架,
逐张 owner 确认),再做系统性优化。** 考察博主预测精度不是目标;发言命中率统计只是辅助
验证之一,排在复刻与确认之后,不得抢主线。做任何新任务前先对照这条次序。

## 反过度防御(最重要的行为准则)

参考 https://github.com/wanshuiyin/HERO-Anti-OverDefense ,四禁:
1. 禁写没有实际用途的校验和/指纹/摘要;
2. 禁防御本项目不会出现的输入;
3. 禁用评分表/机械清单/复验循环替代人的判断;
4. 禁为想象的未来需求预建功能开关、迁移框架、兼容层。

写每段防御性代码前自问:"这能检测到什么具体故障,我会因此做出什么不同的决定?"
答不上来就不写。review 时:搜索不受限(真问题都可以报),修复受限
(只修与当前任务主线相关的;主线外的 findings 写进报告即可,不要求修复,不阻塞)。

## 目录速查

| 路径 | 说明 |
|------|------|
| `backend/` | FastAPI 后端;`playbook/`(新建)战法池;`feishu/` 收发通道;`broker/` 模拟盘 |
| `frontend/` | Vue 3 前端,127.0.0.1:9276 |
| `scripts/` | 摄取、研究、运维脚本;`factor_research/` 旧回测代码可借鉴 |
| `data/marketdata_pit/` | ~29 GB Tushare PIT 字节档,append-only,**严禁删改、严禁从零重下** |
| `data/yeren_corpus/` | (新建)视频语料库:转写文本+元数据,append-only |
| `docs/archive/` | 已作废的旧治理文档,只读参考 |
| `tests/` | pytest 测试树 |

## 环境与命令

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin           # Python 一律用 conda env zhanglan
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q    # 跑测试必须带该 env,否则会连飞书
$PY/ruff check backend/ scripts/
FEISHU_INTERACTIVE_ENABLED=false $PY/uvicorn backend.main:app --port 8000
cd frontend && npm run type-check && npm run test -- --run && npm run build
```

## 硬性规则

- **秘密**:API key 全在 `~/.bashrc`(`TUSHARE_TOKEN`、`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、
  `MOONSHOT_API_KEY`、`FEISHU_*`),严禁写进代码、`.env`、测试夹具;gitleaks pre-commit
  已启用,严禁 `--no-verify` 绕过。
- **网络**:出站一律 IPv4-only(httpx 传 `local_address="0.0.0.0"`);服务只监听 127.0.0.1。
- **Tushare**:仅官方 SDK `ts.pro_api()`;`*_vip` 端点单调用行上限会静默截断,必须
  limit+offset 分页;`report_rc.tp` 是利润总额,目标价字段是 `min_price`。
- **git**:conventional commits,message 用英文;commit 只落本地,push 需 owner 明示授权;
  严禁改写已 push 的历史。
- **数据**:`data/` 下的既有档案 append-only;删除任何非临时文件前先确认它未被引用。

## 代码风格

- Python:type hints + docstring(讲 WHY 不是 WHAT);不可变结构优先
  (frozen dataclass / Pydantic frozen);文件 200-400 行典型、800 上限;函数 <50 行;嵌套 <4 层。
- 注释与 commit 英文;面向 owner 的文档与 UI 文案中文。
- 测试:pytest;改什么测什么,不为覆盖率凑无意义断言(见反过度防御)。
- 前端:Vue 3 + TypeScript,提交前 type-check 与 vitest 须绿。

## review 输出约定

按严重度分级:P0(会造成错误决策/资损/数据损坏)、P1(功能错误)、P2(明确改进)、
P3(主线外,只记录)。P0/P1 必须给出可复现场景;拿不出具体故障场景的发现降级 P3。
