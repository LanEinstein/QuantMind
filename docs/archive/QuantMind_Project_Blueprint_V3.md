# QuantMind 智网量化交易系统 — 项目蓝图 V3.0

> **三模型协同架构** | DeepSeek + Qwen + MiniMax M2.5
> **开发框架** | ECC (specify-CLI) + Claude Code (Opus 4.6)
> **用途** | 个人股市交易 · 不开源 · 不商用

---

## 0. 项目元信息

```yaml
project_name: quantmind
version: "3.0"
author: Kim
created: 2026-03-17
dev_framework: ECC (specify-CLI)
dev_tool: Claude Code (Opus 4.6)
language_policy:
  code_comments: English
  commit_messages: English
  docs: Chinese
  claude_code_prompts: English
  completion_reports: Chinese
```

---

## 0.1 核心参考开源项目

| 项目 | 地址 | Stars | 本项目中的角色 | 许可证 |
|------|------|-------|---------------|--------|
| **TradingAgents-CN** | https://github.com/hsliuping/TradingAgents-CN | 17k+ | 主体框架：多Agent决策引擎 + Vue 3前端 + FastAPI后端 + A股数据 | 混合（核心Apache-2.0） |
| **TradingAgents**（上游） | https://github.com/TauricResearch/TradingAgents | 29.9k+ | TradingAgents-CN的上游，Bull/Bear辩论机制原始设计 | Apache-2.0 |
| **MiroFish** | https://github.com/666ghj/MiroFish | 28.8k+ | 群体智能仿真引擎，嵌入情报研判员内部 | AGPL-3.0 |
| **OASIS**（MiroFish依赖） | https://github.com/camel-ai/oasis | — | MiroFish的底层仿真引擎，CAMEL-AI团队开发 | Apache-2.0 |
| **adata** | https://github.com/1nchaos/adata | — | 免费A股实时行情+历史数据 | MIT |
| **AKShare** | https://github.com/akfamily/akshare | 10k+ | 备用数据源，财务数据、新闻数据 | MIT |
| **BaoStock** | http://baostock.com | — | 免费A股历史K线数据 | BSD |
| **Backtrader** | https://github.com/mhristache/backtrader | 20k+ | 历史策略回测引擎 | GPL-3.0 |
| **VNPy** | https://github.com/vnpy/vnpy | 27.8k+ | 实盘交易接口（将来Phase 5备选） | MIT |
| **LangGraph** | https://github.com/langchain-ai/langgraph | — | Agent编排框架，TradingAgents-CN核心依赖 | MIT |

**其他参考资源：**
- TradingAgents 学术论文: https://arxiv.org/abs/2412.20138
- MiroFish 在线Demo: https://666ghj.github.io/mirofish-demo/
- 聚宽量化（免费模拟盘对照）: https://www.joinquant.com/
- miniQMT XtQuant文档（将来实盘接口）: 各券商提供
- MiniMax M2.5 API文档: https://www.minimaxi.com/
- DeepSeek API文档: https://platform.deepseek.com/
- 阿里云百炼/DashScope: https://bailian.console.aliyun.com/

---

## 1. 系统愿景

QuantMind 是一套个人A股量化交易系统，融合三大开源项目能力：

| 来源 | 提供的能力 | 在系统中的角色 |
|------|-----------|---------------|
| TradingAgents-CN | 多Agent LLM辩论式交易决策、A股数据接入、Vue 3 Web UI | 主体框架 |
| MiroFish | 群体智能仿真预测（OASIS引擎 + GraphRAG + Zep Cloud） | 嵌入情报研判员Agent内部的推演工具 |
| 自建模块 | Mock Broker全仿真、硬编码风控引擎、LLM路由器、实时监控 | 补齐交易执行与安全层 |

**核心原则：**
- 每个模型做自己最擅长的事，用最合适的成本
- 指令的最终守门人是代码而非LLM——风控引擎硬编码，不可越越
- 系统是"智能决策辅助"而非"自动赚钱机器"

---

## 2. 三模型协同架构

### 2.1 模型能力与成本矩阵

| 模型 | 角色定位 | 核心优势 | 成本 | 接入方式 |
|------|---------|---------|------|---------|
| **DeepSeek** | "勤务兵" | 极低成本、中文优化、推理强 | ~0.2元/百万Token ★极低 | DeepSeek API 直连 |
| **Qwen** | "中文金融专家" | 中文金融语料最强、A股生态完整 | qwen-plus ~1元/百万Token | DashScope API |
| **MiniMax M2.5** | "智能体核心引擎" | Agent能力全球前五、100TPS极速、推理达Opus级 | 输入2.1元/输出8.4元/百万Token | MiniMax API |

**扩展预留：** LLM路由器通过YAML配置，未来可零代码接入Claude API / OpenAI API。

### 2.2 十大Agent角色分配

```yaml
# config/agent_models.yaml — LLM路由器核心配置文件

providers:
  deepseek:
    base_url: "https://api.deepseek.com/v1"
    api_key: "${DEEPSEEK_API_KEY}"
    default_model: "deepseek-chat"
  qwen:
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
    api_key: "${QWEN_API_KEY}"
    default_model: "qwen-plus"
  minimax:
    base_url: "https://api.minimaxi.com/v1"
    api_key: "${MINIMAX_API_KEY}"
    default_model: "MiniMax-M2.5"
  # --- 扩展预留 ---
  # claude:
  #   base_url: "https://api.anthropic.com/v1"
  #   api_key: "${CLAUDE_API_KEY}"
  #   default_model: "claude-sonnet-4-6"
  # openai:
  #   base_url: "https://api.openai.com/v1"
  #   api_key: "${OPENAI_API_KEY}"
  #   default_model: "gpt-4o"

agents:
  news_crawler:
    name: "新闻爬取员"
    provider: deepseek
    model: deepseek-chat
    fallback: { provider: qwen, model: qwen-turbo }
    frequency: "every_5min"
    task: "财经新闻摘要、分类、重要性评分(0-10)"

  sentiment_analyst:
    name: "情绪分析师"
    provider: deepseek
    model: deepseek-chat
    fallback: { provider: qwen, model: qwen-turbo }
    frequency: "every_30min"
    task: "社交媒体情绪、论坛情感、恐慌贪婪指数"

  data_cleaner:
    name: "数据清洗员"
    provider: deepseek
    model: deepseek-chat
    fallback: { provider: qwen, model: qwen-turbo }
    frequency: "realtime"
    task: "原始数据标准化、异常值标记、格式转换"

  fundamental_analyst:
    name: "基本面分析师"
    provider: qwen
    model: qwen-plus
    fallback: { provider: deepseek, model: deepseek-chat }
    frequency: "daily_or_event"
    task: "财报解读、PE/PB估值、行业对比"

  technical_analyst:
    name: "技术分析师"
    provider: qwen
    model: qwen-plus
    fallback: { provider: deepseek, model: deepseek-chat }
    frequency: "daily"
    task: "K线形态、MACD/RSI/布林带、趋势判断"

  intelligence_officer:
    name: "情报研判员（含MiroFish）"
    provider: minimax
    model: MiniMax-M2.5
    fallback: { provider: qwen, model: qwen-plus }
    frequency: "event_triggered"
    task: "信息融合、隐性变量推演、驱动MiroFish仿真"
    mirofish_trigger_threshold: 7  # DeepSeek初筛≥7分才触发仿真

  bull_researcher:
    name: "看多研究员"
    provider: minimax
    model: MiniMax-M2.5
    fallback: { provider: qwen, model: qwen-plus }
    frequency: "per_trading_day"
    task: "构建看多论点、寻找上涨催化剂"

  bear_researcher:
    name: "看空研究员"
    provider: minimax
    model: MiniMax-M2.5
    fallback: { provider: qwen, model: qwen-plus }
    frequency: "per_trading_day"
    task: "构建看空论点、寻找下跌风险"

  risk_officer:
    name: "风控官"
    provider: minimax
    model: MiniMax-M2.5
    fallback: { provider: qwen, model: qwen-plus }
    frequency: "per_trading_day"
    task: "投组风险评估、仓位建议、否决权"

  fund_manager:
    name: "基金经理（终局决策）"
    provider: minimax
    model: MiniMax-M2.5
    fallback: { provider: qwen, model: qwen-plus }
    frequency: "per_trading_day"
    task: "综合所有Agent报告，输出最终买卖信号"
```

### 2.3 每日运营成本估算

| 模型 | 承担角色 | 日估算Token | 日成本 |
|------|---------|-----------|--------|
| DeepSeek | 新闻+情绪+清洗 | ~500万 | ~1元 |
| Qwen | 基本面+技术面 | ~200万 | ~2元 |
| MiniMax M2.5 | 情报+辩论+风控+决策 | ~100万 | ~3-8元 |
| **合计** | | | **~6-11元/天，月约130-250元** |

---

## 3. MiroFish 深度嵌入设计

### 3.1 定位

MiroFish **不是独立微服务**，而是"情报研判员"Agent内部的推演工具。它的触发、执行和输出都由情报研判员控制。

### 3.2 七步工作流

```
┌─────────────────────────────────────────────────────────────────┐
│  Step 1: 联网爬虫 → 实时抓取多源财经信息                           │
│    ↓                                                            │
│  Step 2: DeepSeek初筛 → 摘要+分类+重要性评分(0-10)                │
│    ↓ (≥7分触发MiroFish，<7分直接送Agent)                          │
│  Step 3: MiniMax驱动MiroFish仿真                                 │
│    → GraphRAG构建知识图谱                                        │
│    → 生成200-500个市场参与者Agent（散户/机构/游资/分析师）            │
│    → 运行15-30轮群体演化仿真                                      │
│    ↓                                                            │
│  Step 4: 隐性变量提取                                            │
│    → 群体情绪演变曲线                                             │
│    → 关键拐点时间线                                               │
│    → 极端场景概率分布                                             │
│    → 例如: 推演出"美国对伊朗展开军事打击的可能性为35%"              │
│    ↓                                                            │
│  Step 5: 结构化输出 → JSON格式注入Agent决策上下文                   │
│    ↓                                                            │
│  Step 6: Bull/Bear辩论                                          │
│    → 基于「基本面(Qwen)+技术面(Qwen)+情绪(DeepSeek)+仿真(MiniMax)」 │
│    → 四重证据进行结构化辩论                                        │
│    ↓                                                            │
│  Step 7: 风控官审核 → 硬编码风控校验 → 基金经理终局决策              │
└─────────────────────────────────────────────────────────────────┘
```

### 3.3 MiroFish仿真输出Schema

```json
{
  "event_summary": "央行宣布降准50个基点",
  "simulation_config": {
    "agent_count": 300,
    "rounds": 20,
    "model": "MiniMax-M2.5"
  },
  "sentiment_evolution": [
    { "round": 1, "bullish": 0.45, "bearish": 0.30, "neutral": 0.25 },
    { "round": 10, "bullish": 0.62, "bearish": 0.18, "neutral": 0.20 },
    { "round": 20, "bullish": 0.58, "bearish": 0.22, "neutral": 0.20 }
  ],
  "hidden_variables": [
    {
      "variable": "外资加速流入概率",
      "probability": 0.72,
      "reasoning": "降准信号叠加人民币汇率企稳..."
    },
    {
      "variable": "房地产板块过度反应概率",
      "probability": 0.45,
      "reasoning": "市场可能过度解读为地产利好..."
    }
  ],
  "key_inflection_points": [
    { "day": 3, "event": "情绪高点，获利回吐压力出现" },
    { "day": 7, "event": "真实资金面数据落地，修正预期" }
  ],
  "extreme_scenarios": [
    { "scenario": "超预期利好叠加", "probability": 0.15, "impact": "+3-5%" },
    { "scenario": "利好出尽见光死", "probability": 0.25, "impact": "-1-2%" }
  ],
  "recommended_action": "短期看多，但建议分批建仓，警惕第3日获利回吐"
}
```

### 3.4 触发控制

- **触发条件**: DeepSeek初筛评分 ≥ 7分
- **预计频率**: 每周约2-5次（重大事件才触发）
- **成本控制**: 每次仿真约2-5元，不触发时零成本
- **降级策略**: MiniMax API故障时，跳过仿真，仅传递新闻摘要给Agent

---

## 4. 全仿真模拟操盘系统

### 4.1 Mock Broker 设计

```python
# 核心接口定义 — 所有Broker实现此接口
class IBroker(ABC):
    """统一交易接口，MockBroker/QMTBroker/VNPyBroker均实现此接口"""

    @abstractmethod
    async def place_order(self, code: str, price: float, volume: int,
                          direction: OrderDirection, order_type: OrderType) -> OrderResult: ...

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool: ...

    @abstractmethod
    async def get_positions(self) -> List[Position]: ...

    @abstractmethod
    async def get_account(self) -> AccountInfo: ...

    @abstractmethod
    async def get_orders(self, status: Optional[OrderStatus] = None) -> List[Order]: ...

    @abstractmethod
    async def get_trades(self) -> List[Trade]: ...
```

**MockBroker 仿真能力：**

| 能力 | 实现说明 |
|------|---------|
| 虚拟账户 | 初始资金可配置（默认100万），完整资产/持仓/委托/成交查询 |
| 真实行情撮合 | 使用adata/AKShare实时行情数据撮合订单 |
| A股规则 | 涨跌停限制（±10%/±20%/±30%）、T+1制度、集合竞价/连续竞价时间 |
| 摩擦成本 | 滑点模型(0.1%)、佣金(0.025%)、印花税(0.05%卖出)、过户费 |
| 多账户并行 | 支持多虚拟账户同时运行不同策略，对比绩效 |
| 接口一致性 | 与真实券商API接口完全一致，切换实盘只改配置文件 |

### 4.2 硬编码风控引擎

```python
# 风控规则 — 纯代码实现，LLM不可越越
class RiskEngine:
    """独立于所有LLM Agent的硬编码风控引擎"""

    # ===== 可配置参数（config/risk.yaml）=====
    MAX_SINGLE_POSITION_RATIO = 0.20   # 单只股票 ≤ 总资产20%
    MAX_TOTAL_POSITION_RATIO = 0.80    # 总持仓 ≤ 总资产80%
    STOP_LOSS_PER_STOCK = -0.08        # 个股止损 -8%
    DAILY_LOSS_CIRCUIT_BREAKER = -0.03  # 日内熔断 -3%
    PRICE_DEVIATION_LIMIT = 0.05       # 价格偏离限制 ±5%
    LLM_TIMEOUT_SECONDS = 30           # LLM超时阈值
    LLM_MAX_CONSECUTIVE_FAILURES = 3   # LLM最大连续失败次数

    def validate_order(self, order: Order, account: AccountInfo, market: MarketData) -> ValidationResult:
        """每笔交易指令必须通过的校验链"""
        checks = [
            self._check_code_validity,       # 股票代码合法性
            self._check_price_reasonability,  # 价格合理性（±5%偏离）
            self._check_volume_validity,      # 数量整百校验
            self._check_fund_sufficiency,     # 资金充足性
            self._check_position_limit,       # 单股仓位上限
            self._check_total_position_limit, # 总仓位上限
            self._check_trading_time,         # 交易时间校验
        ]
        for check in checks:
            result = check(order, account, market)
            if not result.passed:
                return result
        return ValidationResult(passed=True)
```

### 4.3 三级授权机制

| 模式 | 行为 | 适用阶段 | 配置 |
|------|------|---------|------|
| 🟢 建议模式 | 仅展示信号，不执行任何操作 | Phase 1-4全程 | `mode: suggest` |
| 🟡 确认模式 | 系统提交订单→人工审批→执行 | Phase 5验证期 | `mode: confirm` |
| 🔴 自动模式 | 全自动执行，受硬编码风控约束 | 充分验证后谨慎开启 | `mode: auto` |

---

## 5. 数据层（全免费方案）

| 数据类型 | 数据源 | Python包 | 说明 |
|---------|--------|---------|------|
| 实时行情 | adata + AKShare | `pip install adata akshare` | 指数/个股/板块实时行情，Level1 |
| 历史K线 | adata + BaoStock | `pip install adata baostock` | 日/周/月K线，复权，可回溯多年 |
| 财务数据 | AKShare + BaoStock | 同上 | 财报、EPS、PE/PB |
| 新闻资讯 | 自建爬虫服务 | `requests + beautifulsoup4` | 东方财富/新浪财经/微博/Reuters |
| 回测引擎 | Backtrader | `pip install backtrader` | 历史策略回测，MIT协议 |
| 模拟盘对照 | 聚宽量化 | Web平台 | 注册即用，免费模拟交易环境 |

---

## 6. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    Layer 4: 展示层                               │
│  Vue 3 + Element Plus + ECharts + WebSocket                     │
│  大盘监控 | 持仓总览 | Agent辩论可视化 | MiroFish仿真可视化 |       │
│  交易日志 | 绩效报告 | 风控状态面板                                │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 3: 执行层                               │
│  硬编码风控引擎 | IBroker抽象接口(Mock/QMT/VNPy) |                │
│  三级授权机制 | 订单管理器                                        │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 2: 分析层                               │
│  TradingAgents-CN LangGraph多Agent决策引擎                       │
│  MiroFish仿真组件（嵌入情报研判员内部）                            │
│  LLM路由器（DeepSeek/Qwen/MiniMax智能分发，预留Claude/GPT扩展）   │
├─────────────────────────────────────────────────────────────────┤
│                    Layer 1: 数据层                               │
│  adata/AKShare实时行情 | 新闻爬虫服务 | BaoStock历史数据 |         │
│  MongoDB持久化 | Redis消息队列+缓存                               │
└─────────────────────────────────────────────────────────────────┘
```

---

## 7. 前端界面设计（基于TradingAgents-CN深度定制）

### 7.1 基座继承：TradingAgents-CN已有前端能力

TradingAgents-CN v1.0.0-preview 已提供完整的 Vue 3 + Element Plus + Vite + TypeScript 前端框架，我们在其基础上进行扩展而非重写。以下是继承与新增的清晰划分：

| 能力 | TradingAgents-CN已有 | QuantMind新增/改造 |
|------|--------------------|--------------------|
| 技术栈 | Vue 3 + Element Plus + Vite + TypeScript | 不变，新增ECharts + WebSocket |
| 用户认证 | ✅ 完整的登录/注册/角色管理 | 继承，不改 |
| LLM配置中心 | ✅ 可视化模型配置、持久化、快速切换 | 改造为三模型路由器配置界面 |
| 批量分析 | ✅ 多股票并发分析、进度追踪 | 继承，增加Agent模型分配显示 |
| 股票筛选 | ✅ 多维度筛选（财务/技术/行业） | 继承，增加MiroFish事件影响筛选 |
| 个股详情 | ✅ 基本面/技术面/资金面/新闻舆情 | 改造，增加Agent分析意见面板 |
| 自选股管理 | ✅ 分组/标签/监控/提醒 | 继承，增加风控状态标记 |
| 模拟交易 | ✅ 虚拟账户/交易记录/持仓分析 | 大幅增强为全仿真Mock Broker |
| 专业报告导出 | ✅ 支持 | 继承，增加MiroFish仿真报告 |
| Docker部署 | ✅ 多架构支持 | 继承 |
| **大盘实时监控** | ❌ 无 | 🆕 全新页面 |
| **Agent辩论可视化** | ❌ 无（仅文本进度） | 🆕 全新页面 |
| **MiroFish仿真可视化** | ❌ 无 | 🆕 全新页面 |
| **风控仪表盘** | ❌ 无 | 🆕 全新页面 |
| **绩效分析面板** | ❌ 基础 | 🆕 大幅增强 |
| **三级授权交互** | ❌ 无 | 🆕 确认模式审批界面 |

### 7.2 页面规划总览（共8个核心页面）

```
┌────────────────────────────────────────────────────────────────┐
│  顶部导航栏：QuantMind Logo | 授权模式指示灯(🟢🟡🔴) | 时间 | 用户 │
├──────┬─────────────────────────────────────────────────────────┤
│ 侧边 │                                                        │
│ 导航 │  ① 大盘监控  ──── 实时行情总览（首页/默认页）              │
│      │  ② 智能分析  ──── Agent多维度分析（继承+增强）             │
│      │  ③ Agent辩论 ──── Bull/Bear辩论过程可视化（🆕）          │
│      │  ④ 事态推演  ──── MiroFish仿真可视化（🆕）               │
│      │  ⑤ 模拟操盘  ──── 持仓/交易/订单管理（大幅增强）           │
│      │  ⑥ 绩效报告  ──── 收益分析/风险指标（🆕）                │
│      │  ⑦ 风控中心  ──── 风控状态/规则配置（🆕）                 │
│      │  ⑧ 系统设置  ──── LLM配置/数据源/参数（继承+改造）        │
├──────┴─────────────────────────────────────────────────────────┤
│  底部状态栏：LLM连接状态(3灯) | 数据源状态 | 今日成本 | 风控状态   │
└────────────────────────────────────────────────────────────────┘
```

### 7.3 各页面详细设计

#### ① 大盘监控（Dashboard）— 🆕 全新页面，系统首页

```
┌─────────────────────────────────────────────────────────────┐
│ 三大指数实时曲线（上证 / 深证 / 创业板）       [ECharts K线图] │
│   带分时线 + 成交量柱状图 + MA均线叠加                        │
├────────────────────┬────────────────────────────────────────┤
│ 涨跌家数统计        │ 板块热力图                              │
│  涨: 2847  跌: 1523 │  [ECharts TreeMap]                     │
│  涨停: 45  跌停: 12 │  颜色=涨跌幅 面积=成交额                │
│  [实时WebSocket推送] │  点击板块展开个股列表                    │
├────────────────────┼────────────────────────────────────────┤
│ 北向资金流向         │ 重大新闻Feed（带重要性评分色标）          │
│  今日净流入: +32亿   │  🔴 9分: 央行宣布降准50bp              │
│  [ECharts面积图]    │  🟡 7分: 美联储议息会议纪要              │
│                    │  ⚪ 4分: 某上市公司季报预告               │
│                    │  [DeepSeek评分 + 点击展开MiroFish分析]   │
├────────────────────┴────────────────────────────────────────┤
│ Agent决策速报                                                │
│  "基金经理建议: 今日减仓创业板ETF 5%，理由..." [点击进入辩论详情] │
└─────────────────────────────────────────────────────────────┘
```

**技术要点：**
- 行情数据通过WebSocket实时推送（adata → Redis PubSub → FastAPI WS → 前端）
- ECharts K线图组件封装为`<MarketChart>`可复用组件
- 板块热力图使用ECharts TreeMap，数据每30秒刷新
- 新闻Feed按DeepSeek评分色标排列，≥7分标红（已触发MiroFish仿真的带特殊图标）

#### ② 智能分析 — 继承TradingAgents-CN + 增强

继承TradingAgents-CN已有的批量分析、股票筛选、个股详情功能，做以下增强：

- **个股详情页新增「Agent意见面板」**：展示每个Agent（基本面/技术面/情绪/情报/看多/看空）对该股票的独立评价，附带模型标签（如"Qwen分析"、"MiniMax推理"）
- **分析结果新增「置信度热力条」**：Agent共识度可视化（全部看多=深绿，分歧大=黄色，全部看空=深红）
- **筛选条件新增「MiroFish事件影响」维度**：可按"受降准利好影响概率>60%"等条件筛选

#### ③ Agent辩论可视化 — 🆕 全新页面（本项目核心差异化）

```
┌─────────────────────────────────────────────────────────────┐
│  分析标的: 贵州茅台(600519)    日期: 2026-03-17               │
├──────────────────────┬──────────────────────────────────────┤
│                      │                                      │
│   🟢 看多研究员       │    🔴 看空研究员                      │
│   (MiniMax M2.5)     │    (MiniMax M2.5)                    │
│                      │                                      │
│  "茅台一季度营收增长   │  "当前PE 32倍处于历史                 │
│   15%，批价坚挺..."   │   高位区间，外资持仓                  │
│                      │   占比下降3个百分点..."                │
│  论据:               │  论据:                               │
│  · 基本面(Qwen): ✅  │  · 技术面(Qwen): ⚠️                 │
│  · 情绪(DeepSeek): ✅│  · 情报(MiniMax): ⚠️                │
│  · 仿真(MiroFish): ✅│  · 资金面: ❌                        │
│                      │                                      │
├──────────────────────┴──────────────────────────────────────┤
│  辩论轮次: ████████░░ Round 3/4                              │
│  [时间线形式展示每轮观点演变，可展开/折叠]                      │
├─────────────────────────────────────────────────────────────┤
│  👔 风控官审核 (MiniMax M2.5)                                │
│  "仓位合规✅ 止损设置✅ 集中度合规✅ 建议仓位: ≤15%"           │
├─────────────────────────────────────────────────────────────┤
│  🎯 基金经理决策 (MiniMax M2.5)                              │
│  综合评分: 72/100 (偏多)                                     │
│  决策: 建议买入5%仓位，目标价2150，止损1850                    │
│  [建议模式🟢: 仅展示] [确认模式🟡: 点击审批执行]               │
└─────────────────────────────────────────────────────────────┘
```

**技术要点：**
- 辩论过程通过SSE（Server-Sent Events）流式推送，实时显示Agent思考过程
- 每个Agent的论据来源标注模型名称（便于评估各模型贡献度）
- 辩论历史可回溯，支持按日期/股票代码检索

#### ④ 事态推演（MiroFish仿真可视化）— 🆕 全新页面

```
┌─────────────────────────────────────────────────────────────┐
│  事件: 央行宣布降准50个基点                DeepSeek评分: 9/10  │
│  仿真状态: ✅ 已完成 (300 Agents × 20 Rounds, 耗时4分钟)     │
├──────────────────────┬──────────────────────────────────────┤
│ 群体情绪演变曲线      │ 隐性变量矩阵                          │
│ [ECharts面积图]      │                                      │
│                      │ 外资加速流入     72% ████████░░       │
│  看多 ████████▓▓     │ 房地产过度反应   45% █████░░░░░       │
│  看空 ███▓░░░░░░     │ 游资抢筹创业板   58% ██████░░░░       │
│  中性 ██░░░░░░░░     │ 央行后续降息     33% ███░░░░░░░       │
│                      │                                      │
│  ← Round 1   20 →   │ [概率条形图，点击展开推理链]             │
├──────────────────────┼──────────────────────────────────────┤
│ 关键拐点时间线        │ 极端场景分布                           │
│ [ECharts Timeline]   │ [ECharts饼图/雷达图]                  │
│                      │                                      │
│ Day 1: 情绪高涨 →    │  超预期利好叠加  15%                   │
│ Day 3: 获利回吐 →    │  符合预期       60%                   │
│ Day 7: 数据落地 →    │  利好出尽见光死  25%                   │
│                      │                                      │
├──────────────────────┴──────────────────────────────────────┤
│ 仿真结论: "短期看多，建议分批建仓，警惕第3日获利回吐"           │
│ [查看完整报告] [导出PDF] [注入Agent辩论]                       │
└─────────────────────────────────────────────────────────────┘
```

**技术要点：**
- 群体情绪演变使用ECharts stacked area chart，支持按轮次回放动画
- 隐性变量矩阵使用进度条+概率数值，点击可展开MiniMax的完整推理链
- 历史仿真记录可检索对照，支持"这次降准 vs 上次降准"对比视图

#### ⑤ 模拟操盘 — 大幅增强（基于TradingAgents-CN模拟交易模块）

继承TradingAgents-CN的虚拟账户/交易记录功能，做以下大幅增强：

```
┌─────────────────────────────────────────────────────────────┐
│  账户总览                                                    │
│  总资产: ¥1,032,450  今日盈亏: +¥3,280 (+0.32%)             │
│  持仓市值: ¥826,000 (80.0%)  可用资金: ¥206,450 (20.0%)     │
│  [资产走势曲线 - ECharts]                                    │
├─────────────────────────────────────────────────────────────┤
│  持仓列表                                                    │
│  代码   名称    持仓  成本    现价    盈亏     止损线  状态    │
│  600519 贵州茅台 200  1980   2050   +¥14000  1820   🟢正常  │
│  000858 五粮液   500  158    152    -¥3000   145    🟡接近  │
│  300750 宁德时代 100  225    218    -¥700    207    🟢正常  │
│                                                [风控引擎实时标注] │
├──────────────────────┬──────────────────────────────────────┤
│ 今日委托              │ 成交记录                              │
│ [实时更新]            │ [可筛选/导出]                         │
│ 10:15 买入 600519    │ 10:15 600519 买入200股 ¥1980 已成交   │
│   200股 ¥1980 已成交  │ 14:30 000001 卖出100股 ¥15.2 已成交   │
│ 14:30 卖出 000001    │                                      │
│   100股 ¥15.2 待成交  │                                      │
├──────────────────────┴──────────────────────────────────────┤
│ 🟡 确认模式待审批队列（仅确认模式显示）                          │
│  Agent建议买入 601318中国平安 300股@¥52.3  [✅批准] [❌拒绝]   │
│  Agent建议卖出 000858五粮液 200股@¥152    [✅批准] [❌拒绝]    │
└─────────────────────────────────────────────────────────────┘
```

**关键增强点：**
- 持仓列表实时显示风控引擎状态（止损距离、仓位占比、预警等级）
- 确认模式下显示待审批订单队列，支持一键批准/拒绝
- 支持多虚拟账户Tab切换（不同策略并行对比）

#### ⑥ 绩效报告 — 🆕 全新页面

```
┌─────────────────────────────────────────────────────────────┐
│  时间范围: [本周 ▼]  对照基准: [沪深300 ▼]                    │
├──────────────────────┬──────────────────────────────────────┤
│ 净值曲线             │ 核心指标卡片                           │
│ [ECharts双线对比图]  │  年化收益: +18.5%  ↗                  │
│                      │  Sharpe比: 1.32                      │
│ ── QuantMind         │  最大回撤: -6.2%                     │
│ ── 沪深300           │  胜率: 62.3%                         │
│                      │  盈亏比: 1.85                        │
│                      │  换手率: 15.2%/月                    │
├──────────────────────┼──────────────────────────────────────┤
│ 回撤曲线             │ 模型贡献度分析                         │
│ [ECharts面积图]      │  DeepSeek信号准确率: 58%              │
│                      │  Qwen分析采纳率: 71%                 │
│                      │  MiniMax决策胜率: 64%                │
│                      │  MiroFish仿真命中率: 55%              │
├──────────────────────┴──────────────────────────────────────┤
│ [导出日报] [导出周报] [导出月报]                               │
└─────────────────────────────────────────────────────────────┘
```

#### ⑦ 风控中心 — 🆕 全新页面

```
┌─────────────────────────────────────────────────────────────┐
│  风控状态总览                                                 │
│  系统状态: 🟢 正常运行    授权模式: 🟢 建议模式                │
│  今日触发止损: 0次     今日熔断: 否    LLM校验拦截: 2次        │
├──────────────────────┬──────────────────────────────────────┤
│ 仓位监控雷达图        │ 风控规则配置                           │
│ [ECharts雷达图]      │                                      │
│  · 总仓位: 80% →     │  单股上限:    [20]%  [保存]           │
│  · 最大单股: 15% ✅  │  总仓位上限:  [80]%  [保存]           │
│  · 行业集中: 35% ⚠️ │  个股止损:    [-8]%  [保存]           │
│  · 日亏损: -0.5% ✅  │  日内熔断:    [-3]%  [保存]           │
│                      │  LLM超时:     [30]秒 [保存]           │
├──────────────────────┴──────────────────────────────────────┤
│ 风控事件日志（时间倒序）                                       │
│ 14:32 ⚠️ LLM指令校验: 拦截异常价格委托 (600519 @¥99999)     │
│ 11:15 ℹ️ 仓位预警: 总仓位达78%, 接近80%上限                  │
│ 09:35 ✅ 日初检查: 所有风控规则正常                            │
└─────────────────────────────────────────────────────────────┘
```

#### ⑧ 系统设置 — 继承TradingAgents-CN + 改造

继承TradingAgents-CN的配置管理中心，做以下改造：
- **LLM配置页改造**：从单一模型选择改为三模型路由器配置界面，展示每个Agent→模型的映射关系，支持拖拽修改
- **新增数据源状态页**：显示adata/AKShare/BaoStock/爬虫各数据源的实时连接状态和延迟
- **新增MiroFish配置页**：仿真参数（Agent数量、轮次、触发阈值）可视化配置
- **新增成本统计页**：按模型/按Agent/按日展示LLM API调用量和费用

### 7.4 前端技术约定

```yaml
frontend_stack:
  framework: "Vue 3 (Composition API + <script setup>)"
  ui_library: "Element Plus"
  charts: "ECharts 5 (所有图表统一使用)"
  state_management: "Pinia"
  build_tool: "Vite + TypeScript"
  realtime: "WebSocket (行情推送) + SSE (Agent思考流式输出)"
  style: "TailwindCSS + Element Plus主题定制"
  
design_principles:
  - "深色主题为主（金融终端风格），浅色主题可选"
  - "信息密度高但不杂乱——参考Bloomberg Terminal的分区布局"
  - "关键数据（盈亏、风控状态）使用红绿色标，色盲友好模式可选"
  - "所有图表支持响应式，适配1920×1080和2560×1440两个主要分辨率"
  - "Agent输出使用Markdown渲染，代码块和表格正确显示"
  - "加载状态和空状态都要有优雅的占位UI"
```

### 7.5 前端相关ECC任务补充

```yaml
# Phase 4 前端任务细化

task_id: P4-T01
title: "Dashboard: real-time market monitoring page"
description: |
  Build the main dashboard page inheriting TradingAgents-CN layout.
  Add: index K-line charts (ECharts), sector heatmap (TreeMap), 
  advance/decline stats, northbound capital flow, news feed with 
  DeepSeek importance scores, agent decision summary card.
  All real-time data via WebSocket from FastAPI backend.
acceptance_criteria:
  - Three index charts update in real-time via WebSocket
  - Sector heatmap renders 30+ sectors with color-coded performance
  - News feed sorted by importance score, ≥7 highlighted red
  - Bottom status bar shows LLM connection status for all 3 providers
estimated_effort: "8-10 hours"

task_id: P4-T02
title: "Agent debate visualization page"
description: |
  Build the Bull/Bear debate visualization with SSE streaming.
  Show each agent's arguments, evidence sources (with model labels),
  debate rounds timeline, risk officer review, and fund manager decision.
  Include authorization mode controls (suggest/confirm buttons).
acceptance_criteria:
  - Debate process streams in real-time via SSE
  - Each argument attributed to specific model (DeepSeek/Qwen/MiniMax)
  - Debate history searchable by date and stock code
  - Confirm mode shows approval/reject buttons on final decision
estimated_effort: "8-10 hours"

task_id: P4-T03
title: "MiroFish simulation visualization page"
description: |
  Build the simulation results visualization: sentiment evolution
  stacked area chart, hidden variable matrix with probability bars,
  key inflection timeline, extreme scenario distribution.
  Support playback animation by simulation round.
acceptance_criteria:
  - Sentiment evolution chart with round-by-round animation
  - Hidden variables displayed as progress bars with expandable reasoning
  - Historical simulations searchable and comparable
  - Export to PDF supported
estimated_effort: "6-8 hours"

task_id: P4-T04
title: "Enhanced portfolio management with risk engine integration"
description: |
  Enhance TradingAgents-CN's simulated trading page. Add real-time
  risk status per position, stop-loss distance indicators, confirmation
  mode approval queue, and multi-account tab switching.
acceptance_criteria:
  - Each position shows risk status (green/yellow/red)
  - Stop-loss distance calculated and displayed in real-time
  - Confirm mode shows pending order queue with approve/reject
  - Multiple virtual accounts switchable via tabs
estimated_effort: "6-8 hours"

task_id: P4-T05
title: "Performance analytics and risk control center pages"
description: |
  Build performance report page (equity curve, Sharpe, drawdown,
  win rate, model contribution analysis) and risk control center
  (position radar, rule configuration, event log).
acceptance_criteria:
  - Equity curve compared against benchmark (沪深300)
  - Model contribution breakdown (accuracy per model)
  - Risk radar chart with real-time position monitoring
  - Risk rules editable via UI with save to risk.yaml
estimated_effort: "6-8 hours"

task_id: P4-T06
title: "System settings: LLM router config and cost dashboard"
description: |
  Adapt TradingAgents-CN settings page for three-model router.
  Add agent-to-model mapping visualization, data source status,
  MiroFish config panel, and daily/monthly cost statistics.
acceptance_criteria:
  - Agent-model mapping displayed as visual diagram
  - Data source connectivity status with latency indicators
  - MiroFish simulation parameters configurable via UI
  - Cost statistics broken down by model, agent, and date
estimated_effort: "4-5 hours"
```

---

## 8. 项目目录结构

```
quantmind/
├── .ecc/                          # ECC框架配置
│   ├── spec.yaml                  # specify-CLI主配置
│   └── tasks/                     # ECC任务定义
├── config/
│   ├── agent_models.yaml          # LLM路由器：Agent-模型分配配置
│   ├── risk.yaml                  # 风控引擎参数配置
│   ├── broker.yaml                # 交易接口配置（mock/qmt/vnpy切换）
│   ├── data_sources.yaml          # 数据源配置
│   └── mirofish.yaml              # MiroFish仿真参数配置
├── backend/
│   ├── main.py                    # FastAPI入口
│   ├── api/                       # REST API路由
│   │   ├── market.py              # 行情API
│   │   ├── trading.py             # 交易API
│   │   ├── analysis.py            # 分析结果API
│   │   └── websocket.py           # WebSocket实时推送
│   ├── agents/                    # Agent层（基于LangGraph）
│   │   ├── graph.py               # LangGraph状态图定义
│   │   ├── news_crawler.py        # 新闻爬取员
│   │   ├── sentiment_analyst.py   # 情绪分析师
│   │   ├── data_cleaner.py        # 数据清洗员
│   │   ├── fundamental_analyst.py # 基本面分析师
│   │   ├── technical_analyst.py   # 技术分析师
│   │   ├── intelligence_officer.py # 情报研判员（含MiroFish调用）
│   │   ├── bull_researcher.py     # 看多研究员
│   │   ├── bear_researcher.py     # 看空研究员
│   │   ├── risk_officer.py        # 风控官
│   │   └── fund_manager.py        # 基金经理
│   ├── broker/                    # 交易接口层
│   │   ├── interface.py           # IBroker抽象接口
│   │   ├── mock_broker.py         # 全仿真模拟交易
│   │   ├── qmt_broker.py          # miniQMT实盘接口（Phase 5）
│   │   └── vnpy_broker.py         # VNPy实盘接口（备选）
│   ├── risk/                      # 风控引擎
│   │   ├── engine.py              # 硬编码风控规则
│   │   ├── stop_loss.py           # 止损逻辑
│   │   └── circuit_breaker.py     # 熔断逻辑
│   ├── data/                      # 数据层
│   │   ├── market_data.py         # adata/AKShare行情接口
│   │   ├── news_crawler.py        # 新闻爬虫服务
│   │   ├── history_data.py        # BaoStock历史数据
│   │   └── backtest.py            # Backtrader回测集成
│   ├── llm/                       # LLM路由器
│   │   ├── router.py              # 核心路由逻辑
│   │   ├── providers.py           # 各Provider适配器
│   │   └── fallback.py            # 降级与故障转移
│   ├── mirofish/                  # MiroFish集成层
│   │   ├── simulator.py           # 仿真调度器
│   │   ├── graph_rag.py           # GraphRAG知识图谱构建
│   │   ├── agent_generator.py     # 市场参与者Agent生成
│   │   └── report_parser.py       # 仿真报告解析与结构化
│   └── models/                    # 数据模型
│       ├── order.py
│       ├── position.py
│       ├── account.py
│       └── signal.py
├── frontend/                      # Vue 3 + Element Plus + Vite + TypeScript
│   ├── src/
│   │   ├── views/
│   │   │   ├── Dashboard.vue      # ① 大盘监控（首页）
│   │   │   ├── Analysis.vue       # ② 智能分析（继承TradingAgents-CN）
│   │   │   ├── StockDetail.vue    # ② 个股详情（增强Agent意见面板）
│   │   │   ├── StockScreener.vue  # ② 股票筛选（增强MiroFish维度）
│   │   │   ├── AgentDebate.vue    # ③ Agent辩论可视化（🆕）
│   │   │   ├── Simulation.vue     # ④ MiroFish事态推演可视化（🆕）
│   │   │   ├── Portfolio.vue      # ⑤ 模拟操盘/持仓管理（增强）
│   │   │   ├── OrderApproval.vue  # ⑤ 确认模式审批队列（🆕）
│   │   │   ├── Performance.vue    # ⑥ 绩效报告（🆕）
│   │   │   ├── RiskCenter.vue     # ⑦ 风控中心（🆕）
│   │   │   ├── Settings.vue       # ⑧ 系统设置（改造）
│   │   │   ├── LLMRouter.vue      # ⑧ LLM路由器配置（🆕）
│   │   │   └── CostDashboard.vue  # ⑧ 成本统计（🆕）
│   │   ├── components/
│   │   │   ├── charts/
│   │   │   │   ├── MarketChart.vue     # 可复用K线图组件
│   │   │   │   ├── SectorHeatmap.vue   # 板块热力图
│   │   │   │   ├── SentimentChart.vue  # 情绪演变面积图
│   │   │   │   ├── RiskRadar.vue       # 风控雷达图
│   │   │   │   └── EquityCurve.vue     # 净值曲线对比图
│   │   │   ├── agent/
│   │   │   │   ├── DebatePanel.vue     # 辩论对话面板
│   │   │   │   ├── AgentCard.vue       # Agent角色卡片（带模型标签）
│   │   │   │   └── DecisionChain.vue   # 决策链追溯组件
│   │   │   ├── trading/
│   │   │   │   ├── PositionTable.vue   # 持仓表格（含风控状态列）
│   │   │   │   ├── OrderQueue.vue      # 待审批订单队列
│   │   │   │   └── TradeHistory.vue    # 成交历史
│   │   │   └── common/
│   │   │       ├── StatusBar.vue       # 底部状态栏
│   │   │       ├── AuthModeIndicator.vue # 授权模式指示灯
│   │   │       └── NewsFeed.vue        # 新闻Feed（带评分色标）
│   │   ├── composables/
│   │   │   ├── useWebSocket.ts    # WebSocket行情连接
│   │   │   ├── useSSE.ts          # SSE Agent流式输出
│   │   │   └── useRiskStatus.ts   # 风控状态实时计算
│   │   ├── stores/
│   │   │   ├── market.ts          # 行情数据Store
│   │   │   ├── portfolio.ts       # 持仓数据Store
│   │   │   ├── agent.ts           # Agent状态Store
│   │   │   └── risk.ts            # 风控状态Store
│   │   ├── styles/
│   │   │   └── theme-dark.css     # 深色金融终端主题
│   │   └── router/
│   │       └── index.ts
│   ├── package.json
│   └── vite.config.ts
├── tests/
│   ├── test_risk_engine.py        # 风控引擎测试（最高优先级）
│   ├── test_mock_broker.py        # 模拟交易测试
│   ├── test_llm_router.py         # LLM路由器测试
│   └── test_agents.py             # Agent集成测试
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 9. ECC框架开发任务规划

> 以下任务按Phase组织，每个Task对应一个ECC spec，适合Claude Code单次会话完成。

### Phase 1: 基座搭建（第1-3周）

```yaml
# Task 1.1: 项目初始化与基础设施
task_id: P1-T01
title: "Project scaffolding and infrastructure setup"
description: |
  Initialize the quantmind project with pyproject.toml, Docker Compose
  (MongoDB + Redis + FastAPI), directory structure, and .env configuration.
acceptance_criteria:
  - docker-compose up starts MongoDB, Redis, and FastAPI backend
  - Health check endpoint returns 200
  - .env.example contains all required variables
estimated_effort: "2-3 hours"

# Task 1.2: LLM路由器
task_id: P1-T02
title: "LLM Router with multi-provider support"
description: |
  Build the LLM router that reads agent_models.yaml configuration and routes
  each agent's request to the assigned LLM provider. Implement OpenAI SDK
  compatible interface for DeepSeek, Qwen (DashScope), and MiniMax.
  Include automatic fallback on API failure.
acceptance_criteria:
  - All 3 providers respond to test prompts
  - Fallback triggers when primary provider is unreachable
  - Token usage and cost tracked per agent per day
  - YAML config hot-reload without restart
estimated_effort: "4-5 hours"
dependencies: [P1-T01]

# Task 1.3: 数据层搭建
task_id: P1-T03
title: "Data layer: real-time quotes + historical data + news crawler"
description: |
  Integrate adata and AKShare for real-time A-share market data.
  Integrate BaoStock for historical K-line data.
  Build news crawler service for eastmoney, sina finance, weibo.
  Store in MongoDB, push real-time updates via Redis pub/sub.
acceptance_criteria:
  - Real-time quotes for Shanghai/Shenzhen indices update every 5 seconds
  - Historical daily K-lines for any A-share stock retrievable
  - News crawler returns structured articles with timestamp, source, content
  - All data persisted to MongoDB
estimated_effort: "5-6 hours"
dependencies: [P1-T01]

# Task 1.4: TradingAgents-CN本地部署与适配
task_id: P1-T04
title: "Deploy and adapt TradingAgents-CN for local use"
description: |
  Clone TradingAgents-CN, deploy locally, replace its LLM calls with our
  LLM Router. Verify multi-agent A-share analysis pipeline works with
  DeepSeek/Qwen/MiniMax routing.
acceptance_criteria:
  - TradingAgents-CN multi-agent analysis runs end-to-end for a sample stock
  - LLM calls routed through our LLM Router (verified via logs)
  - Bull/Bear debate produces structured output
estimated_effort: "6-8 hours"
dependencies: [P1-T02, P1-T03]

# Task 1.5: MiroFish组件验证
task_id: P1-T05
title: "Deploy MiroFish backend and validate financial simulation"
description: |
  Deploy MiroFish OASIS simulation engine locally. Test with a sample
  financial scenario (e.g., "央行降准"). Verify GraphRAG construction,
  agent generation, and simulation output.
acceptance_criteria:
  - MiroFish simulation runs with 200 agents, 15 rounds
  - Output includes sentiment evolution and hidden variables
  - MiniMax M2.5 successfully drives the simulation
estimated_effort: "4-5 hours"
dependencies: [P1-T02]
```

### Phase 2: 模拟操盘与风控（第3-6周）

```yaml
# Task 2.1: Mock Broker全仿真交易引擎
task_id: P2-T01
title: "Mock Broker: full simulation trading engine"
description: |
  Build MockBroker implementing IBroker interface. Support virtual account,
  real-time quote matching, A-share rules (涨跌停, T+1, trading hours),
  friction costs (slippage, commission, stamp tax).
acceptance_criteria:
  - Virtual account with configurable initial capital
  - Orders matched against real-time adata/AKShare quotes
  - 涨跌停 limits enforced (10%/20%/30% by board)
  - T+1 rule enforced (cannot sell stocks bought today)
  - Commission (0.025%), stamp tax (0.05% sell), slippage (0.1%) calculated
  - Full order lifecycle: pending → partially_filled → filled / cancelled
  - Unit tests cover all edge cases
estimated_effort: "8-10 hours"
dependencies: [P1-T03]

# Task 2.2: 硬编码风控引擎
task_id: P2-T02
title: "Hard-coded risk control engine"
description: |
  Build RiskEngine class with all validation rules. This is pure Python
  logic, completely independent of LLM. Every trade instruction must pass
  through this engine before execution.
acceptance_criteria:
  - Single position limit (20%) enforced
  - Total position limit (80%) enforced
  - Per-stock stop loss (-8%) triggers forced sell
  - Daily loss circuit breaker (-3%) halts all trading
  - LLM instruction validation (code, price, volume, funds)
  - API failure protection (timeout, consecutive failures)
  - All parameters configurable via risk.yaml
  - Comprehensive unit tests (>95% coverage for this module)
estimated_effort: "5-6 hours"
dependencies: [P2-T01]

# Task 2.3: 三级授权机制
task_id: P2-T03
title: "Three-tier authorization mechanism"
description: |
  Implement suggest/confirm/auto modes. In suggest mode, signals are
  displayed but no orders placed. In confirm mode, orders await human
  approval via Web UI. In auto mode, orders execute with risk engine
  constraints.
acceptance_criteria:
  - Mode switchable via config without restart
  - Suggest mode: signals logged and displayed, zero orders
  - Confirm mode: orders queued, Web UI shows approval interface
  - Auto mode: orders execute immediately after risk validation
estimated_effort: "3-4 hours"
dependencies: [P2-T01, P2-T02]

# Task 2.4: Backtrader回测集成
task_id: P2-T04
title: "Backtrader integration for historical backtesting"
description: |
  Integrate Backtrader with BaoStock historical data. Support running
  agent strategies against historical data with full performance metrics.
acceptance_criteria:
  - Backtest runs for configurable date ranges
  - Output: annualized return, Sharpe ratio, max drawdown, win rate, turnover
  - Visual charts generated (equity curve, drawdown curve)
estimated_effort: "4-5 hours"
dependencies: [P1-T03]
```

### Phase 3: MiroFish深度嵌入（第6-9周）

```yaml
# Task 3.1: MiroFish嵌入情报研判员
task_id: P3-T01
title: "Embed MiroFish into Intelligence Officer agent"
description: |
  Integrate MiroFish core components (GraphRAG, OASIS, ReportAgent) into
  the intelligence_officer agent. Build the complete pipeline:
  news → DeepSeek filter → MiniMax-driven MiroFish simulation →
  structured output → inject into agent debate context.
acceptance_criteria:
  - DeepSeek scores news 0-10, ≥7 triggers MiroFish
  - MiroFish simulation produces JSON matching defined schema
  - Hidden variables extracted and structured
  - Simulation report injected into Bull/Bear debate as additional context
  - Cost per simulation tracked and logged
estimated_effort: "10-12 hours"
dependencies: [P1-T04, P1-T05]

# Task 3.2: 隐性变量提取引擎
task_id: P3-T02
title: "Hidden variable extraction from MiroFish simulation"
description: |
  Build the extraction layer that processes MiroFish simulation results
  to identify: sentiment evolution curves, key inflection points,
  extreme scenario probabilities, and emergent hidden variables.
acceptance_criteria:
  - Sentiment evolution tracked per round
  - Key inflection points identified with reasoning
  - Extreme scenarios with probability estimates
  - Hidden variables (e.g., geopolitical escalation probability) extracted
  - All outputs conform to the defined JSON schema
estimated_effort: "6-8 hours"
dependencies: [P3-T01]
```

### Phase 4: 前端仪表盘（第9-12周）

> **详细任务定义见第7.5节「前端相关ECC任务补充」**，共6个Task（P4-T01至P4-T06），涵盖大盘监控、Agent辩论可视化、MiroFish仿真可视化、模拟操盘增强、绩效报告与风控中心、系统设置改造。
> 
> 前端开发以TradingAgents-CN已有的Vue 3 + Element Plus + Vite + TypeScript代码库为基座，采用增量开发而非重写策略。Claude Code在执行Phase 4任务时，应首先`git clone`TradingAgents-CN前端代码，理解其现有路由、组件、Store结构后，再进行扩展。

### Phase 5: 验证与迭代（第12-20周）

```yaml
# Task 5.1: 建议模式运行4周验证
task_id: P5-T01
title: "Run suggest mode for 4 weeks and collect performance metrics"
description: |
  Run complete system in suggest mode. Track signal accuracy,
  hypothetical P&L, and compare against benchmark (沪深300).
  Log all agent decisions for review.

# Task 5.2: 多策略A/B测试
task_id: P5-T02
title: "Multi-strategy A/B testing with parallel virtual accounts"
description: |
  Run multiple virtual accounts with different model configurations
  to compare performance. E.g., Account A (3-model) vs Account B
  (single MiniMax for all agents).

# Task 5.3: 聚宽模拟盘对照验证
task_id: P5-T03
title: "Cross-validate with JoinQuant paper trading"
description: |
  Run same signals on JoinQuant's free paper trading platform.
  Compare execution results to validate MockBroker accuracy.
```

---

## 10. Claude Code 协作指南

### 9.1 会话管理策略

```
每个ECC Task对应一个Claude Code会话：
- 会话开始: 提供Task ID + 完整context
- 会话中: Claude Code自主编码、测试、调试
- 会话结束: 生成中文完成报告，列出已完成/待办/已知问题
```

### 9.2 Claude Code会话模板

```markdown
## Task: {task_id} - {title}

### Context
- Project: QuantMind quantitative trading system
- Current phase: Phase {n}
- Dependencies completed: {list}
- Working directory: ~/quantmind/

### Requirements
{paste acceptance criteria from blueprint}

### Constraints
- Python 3.11+, FastAPI, LangGraph
- All config via YAML, no hardcoded secrets
- Type hints required on all public functions
- Docstrings in English, user-facing text in Chinese
- Tests required for risk-critical modules

### Start
Please read the existing codebase first, then implement.
```

### 9.3 关键约定

| 约定 | 说明 |
|------|------|
| 分支策略 | `main` + `dev` + `feature/{task_id}` |
| Commit格式 | `feat(P1-T02): implement LLM router with fallback` |
| 测试要求 | 风控引擎 >95% coverage，其余模块 >70% |
| 配置管理 | 所有参数通过YAML，`.env`仅存密钥 |
| 日志规范 | structlog，JSON格式，包含agent_name和model字段 |
| 错误处理 | LLM调用必须try/except，降级而非崩溃 |

### 9.4 环境变量模板

```bash
# .env.example
# === LLM API Keys ===
DEEPSEEK_API_KEY=sk-xxx
QWEN_API_KEY=sk-xxx           # DashScope API Key
MINIMAX_API_KEY=xxx            # MiniMax API Key
# CLAUDE_API_KEY=sk-ant-xxx   # 扩展预留
# OPENAI_API_KEY=sk-xxx       # 扩展预留

# === Database ===
MONGODB_URI=mongodb://localhost:27017/quantmind
REDIS_URL=redis://localhost:6379/0

# === MiroFish ===
ZEP_API_KEY=xxx                # Zep Cloud (MiroFish记忆系统)

# === Broker ===
BROKER_MODE=mock               # mock / qmt / vnpy
MOCK_INITIAL_CAPITAL=1000000   # 模拟初始资金

# === System ===
AUTHORIZATION_MODE=suggest     # suggest / confirm / auto
LOG_LEVEL=INFO
```

---

## 11. 风险声明

> **任何AI交易系统都不能保证盈利。** LLM在金融预测上不具备确定性优势。本系统的价值在于提供多视角分析框架和严格的风控保护，而非替代人类判断。始终以小仓位起步，用数据而非信仰驱动决策。

---

*QuantMind V3.0 | 三模型协同架构 | 2026-03-17 | 仅供个人研究参考*
