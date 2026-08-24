# QuantMind — 中长线投研系统(2026-08-12 完全重构)

QuantMind = 中长线 A 股投研系统:以抖音「全能的野人」的交易体系为蓝本建战法池,
用 Tushare 数据验证与生成信号,飞书推送操作建议,owner 人工执行,系统维护模拟盘镜像。

**行动纲领 = `docs/research/midterm-rearch-action-plan-2026-08-12.md`**(owner 已定稿的计划书,五个里程碑)。
2026-08-12 之前的全部红线、冻结原则、amendment 流程已作废,归档于 `docs/archive/`;
旧代码可借鉴复用,但不构成约束。

## 唯一底线

**永禁真实券商程序化下单。** 系统只维护模拟盘;真实操作永远由 owner 本人在券商 App 完成,
再通过飞书告知系统同步状态。

## 工程约定:反过度防御(全程强制)

参考 [HERO-Anti-OverDefense](https://github.com/wanshuiyin/HERO-Anti-OverDefense),禁止:
1. 写没有实际用途的校验和/指纹/摘要;
2. 防御本项目根本不会出现的输入;
3. 用评分表、机械清单、复验循环替代人的判断;
4. 为想象中的未来需求预建功能开关、迁移框架、兼容层。

判断句:"这能检测到什么具体故障,我会因此做出什么不同的决定?"答不上来就不写。
搜索不受限(发现任何真问题都报告),修复受限(只修主线范围内的);
codex review 的主线外 findings 记录即可,不阻塞进度。

## 跨模型 review:至多一轮(owner 2026-08-21,强制)

**codex(或任何跨模型)review 与随后的修改,一个任务至多进行一次。** 一轮 review + 一轮修复,然后停止。

**禁止**:复验到"无剩余缺陷"、"连续两轮干净才收工"、R1-R5 多轮轮转、为确认收敛再跑一轮。

**理由(owner 原话)**:「有足够的证据表明,多轮 review 会大幅提升 AI 的误判率以及过度纠错。」
真缺陷集中在前一轮;之后的边际产出会退化成记账式吹毛求疵,而每次"修复"都在改动已经正确的内容,
净效果是引入风险。这与上一节反过度防御第 3 禁(禁用复验循环替代人的判断)是同一条原则。

**做法**:本地栈先绿 → 跑一轮 review → 按该轮 findings 修一轮(P0/P1 必修,P2/P3 按判断取舍,不必清零)
→ 修完即止,剩余 findings 记录进报告/worklog 即可。docs-only 任务仍豁免 review。

## 新系统四块

1. **语料流水线**:从最早视频起逐条"下载→FunASR 本地转写→存文本+元数据+发布时间戳→删视频→下一条";
   恒定磁盘占用;处理台账断点续跑;语料落 `data/yeren_corpus/`。目标主页:抖音号 203775400,1086 条作品。
2. **战法池** `backend/playbook/`:LLM 从语料提炼战法卡片(名称/市况/入场/加减仓/退出/仓位,附原话出处)
   → owner 逐张确认 → 转确定性规则用 Tushare 回测验证(真赚钱+回撤可接受+非运气)→ 过门才 validated。
   防前视:视频发布在收盘后,发言只能预测次日及之后。
   **目标次序(owner 2026-08-15 重申,最高优先):先复刻他的交易系统、交易逻辑、操作手法**
   (战法卡片+总体仓位状态框架,逐张 owner 确认),**再做系统性优化**。
   考察博主预测精度不是目标;发言命中率统计只是辅助验证之一,排在复刻与确认之后,不得抢主线。
3. **信号生成**:盘后按已验证战法扫描全市场出建议;数据仅 Tushare 官方 SDK。
4. **交互**:飞书=行动通道,只在需 owner 动手时推送(买入/调仓/增配/减清仓),其余静默;
   推文具体到个股、总分结构、通俗自然。owner 自由文本回复 → LLM 理解 → 更新模拟盘 → 回确认摘要,
   缺信息(如成交价)追问。前端=查看通道:战法池/语料浏览器/对照分析/账户面板,127.0.0.1 only。

## 可复用旧资产

- **`data/marketdata_pit/`(~29 GB,23 端点,2015-2026)**:全市场 PIT 字节档,append-only,
  **禁从零重下**;清单与增量更新协议 = `docs/research/data-inventory-marketdata-pit-2026-06-21.md`。
- 回测/统计检验代码:`scripts/factor_research/`、`backend/` 相关模块,按需借鉴。
- 飞书收发通道:`backend/feishu/`(WS 长连接收、OpenAPI 发)。
- 前端骨架:`frontend/`(Vue 3,:9276)。
- 模拟盘账本:`backend/broker/` MockBroker。
- 其余旧模块(多 Agent 辩论、14-check 风控、对账 ticket、验收框架)封存不用,不删除。

## Tushare 坑(实测,踩过的)

- 仅官方 SDK `ts.pro_api()`;`TUSHARE_TOKEN` 在 `~/.bashrc`(≈8000 积分档)。
- `*_vip` 财报端点单调用有行上限且**静默截断**,必须 limit+offset 分页。
- `report_rc` 的 `tp` 是利润总额不是目标价(目标价=`min_price`);北向 `hk_hold` 2024-08 后断更。
- 出站必须 IPv4-only(httpx 用 `local_address="0.0.0.0"`)。

## 常用命令

```bash
PY=/home/ps/anaconda3/envs/zhanglan/bin           # conda env: zhanglan
FEISHU_INTERACTIVE_ENABLED=false $PY/uvicorn backend.main:app --port 8000
FEISHU_INTERACTIVE_ENABLED=false $PY/pytest -q    # 跑测试必带该 env
$PY/ruff check backend/
cd frontend && npm run dev                        # :9276
cd frontend && npm run type-check && npm run test -- --run && npm run build
```

## 风格与流程

- 回复 owner 用中文;代码、注释、commit message 用英文;conventional commits。
- 不可变数据结构优先;文件 200-400 行典型 / 800 上限;函数 <50 行。
- 秘密只放 `~/.bashrc`(LLM 3 key + FEISHU_* 5 凭证 + TUSHARE_TOKEN),严禁入 `.env` 和代码;
  gitleaks pre-commit 已装,严禁 `--no-verify`。
- commit 落本地;`git push` 必须 owner 明示授权。
- 全栈只监听 127.0.0.1,远程走 SSH tunnel。
- 里程碑推进:M1 语料管线 → M2 战法提炼 → M3 验证 → M4 执行器+飞书+前端 → M5 新章程;
  每步结束 owner 点头再进下一步。跨 session 状态见 memory(`MEMORY.md` 索引)。

## 文档地图

| 路径 | 用途 |
|------|------|
| `docs/research/midterm-rearch-action-plan-2026-08-12.md` | 行动纲领(当前唯一有效计划) |
| `docs/research/data-inventory-marketdata-pit-2026-06-21.md` | PIT 数据档清单与更新协议 |
| `docs/research/` 其余 | 旧研究结果(参考价值保留) |
| `docs/archive/` | 已作废的旧治理文档、决策、交接记录 |
| `docs/runbook/` | systemd 部署等运维手册 |
| `AGENTS.md` | codex 工作守则 |
