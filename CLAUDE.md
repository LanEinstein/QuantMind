# QuantMind 智网量化交易系统

## 项目概述
个人A股量化交易系统，融合TradingAgents-CN多Agent决策 + MiroFish群体智能仿真。
三模型协同：DeepSeek（高频低成本）+ Qwen（中文金融专家）+ MiniMax M2.5（智能体核心引擎）。

## 核心参考项目
- TradingAgents-CN: https://github.com/hsliuping/TradingAgents-CN
- MiroFish: https://github.com/666ghj/MiroFish
- 完整蓝图: ./docs/QuantMind_Project_Blueprint_V3.md

## 技术栈
- Backend: Python 3.11+ / FastAPI / LangGraph
- Frontend: Vue 3 + Element Plus + ECharts + TypeScript (基于TradingAgents-CN前端)
- Database: MongoDB + Redis
- LLM: DeepSeek API + Qwen (DashScope) + MiniMax M2.5 API
- Testing: pytest, >95% coverage for risk engine, >70% for others

## 编码规范
- 代码注释和commit message用英文
- 用户界面文本和文档用中文
- 所有public function必须有type hints和docstring
- 配置通过YAML，密钥通过.env，禁止硬编码
- LLM调用必须try/except，降级而非崩溃
- 风控引擎代码禁止依赖任何LLM输出

## 安全红线
- 风控引擎(backend/risk/)是纯Python硬编码，任何LLM输出不可越越风控规则
- .env文件永远不提交到git
- 所有API Key通过环境变量注入

## Repository
- Remote: https://github.com/LanEinstein/QuantMind
