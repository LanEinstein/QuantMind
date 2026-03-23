"""Chinese prompt templates for MiroFish simulation LLM calls."""

PERSONA_GENERATION_PROMPT = """\
你是A股市场群体智能仿真引擎。给定一个金融事件，你需要模拟市场参与者的初始反应。

请分析以下金融事件，生成市场参与者群体的初始情绪分布。
参与者类型包括：散户（占60%）、机构投资者（占20%）、游资（占10%）、分析师（占10%）。

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "event_summary": "事件一句话总结",
    "initial_sentiment": {
        "bullish": 0到1之间的小数,
        "bearish": 0到1之间的小数,
        "neutral": 0到1之间的小数
    },
    "participant_breakdown": "各类参与者的初始反应描述"
}
注意：bullish + bearish + neutral 必须等于1.0"""

EVOLUTION_SIMULATION_PROMPT = """\
你是A股市场群体智能仿真引擎。基于给定的金融事件和初始情绪，模拟多轮情绪演变。

考虑以下因素进行逐轮推演：
- 信息扩散效应（第1-3轮：消息快速传播）
- 羊群效应（第4-8轮：跟风行为加剧）
- 理性修正（第9-15轮：机构投资者开始理性分析）
- 新均衡（第16轮以后：市场找到新的情绪均衡点）

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "sentiment_evolution": [
        {"round": 1, "bullish": 0.xx, "bearish": 0.xx, "neutral": 0.xx},
        {"round": 2, "bullish": 0.xx, "bearish": 0.xx, "neutral": 0.xx}
    ]
}
注意：每轮的 bullish + bearish + neutral 必须等于1.0
必须生成指定轮数的数据，每轮一条记录。"""

EXTRACTION_PROMPT = """\
你是A股市场群体智能仿真分析师。基于金融事件和情绪演变数据，提取深层洞察。

你需要提取：
1. 隐性变量：市场表面数据背后的隐藏驱动因素，每个附带概率和推理
2. 关键拐点：情绪或市场方向可能发生转变的时间节点
3. 极端场景：小概率但高影响的情景
4. 综合建议：基于仿真结果的操作建议

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "hidden_variables": [
        {"variable": "变量名称", "probability": 0到1之间, "reasoning": "推理依据"}
    ],
    "key_inflection_points": [
        {"day": 天数, "event": "拐点描述"}
    ],
    "extreme_scenarios": [
        {"scenario": "场景描述", "probability": 0到1之间, "impact": "影响幅度如+3%"}
    ],
    "recommended_action": "综合操作建议"
}"""

EVENT_EXTRACTION_PROMPT = """\
你是A股财经新闻分析师。请从以下新闻分析报告中提取关键金融事件。

对每个事件评估重要性（0-10分）：
- 7-10分：重大事件（央行政策、行业重大变革、突发黑天鹅）
- 4-6分：一般事件（常规数据发布、企业日常公告）
- 0-3分：低影响事件（市场噪音、重复消息）

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "events": [
        {
            "title": "事件标题",
            "content": "事件详细描述（50-200字）",
            "importance_score": 0到10的整数,
            "sectors": ["涉及板块1", "涉及板块2"],
            "stocks": ["涉及个股代码1", "涉及个股代码2"]
        }
    ]
}
注意：只提取与目标股票相关或影响市场整体的事件。"""
