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

# --- Extraction pipeline prompts (P3-T02) ---

SENTIMENT_CLASSIFICATION_PROMPT = """\
你是A股市场情绪分析专家。给定一个金融事件和多轮情绪演变数据，为每一轮提供深度分析。

对每一轮分析：
1. dominant_narrative: 该轮最主要的市场叙事主题（如"央行宽松预期"、"外资流入"等）
2. intensity: 情绪强度0-1之间（0=观望为主，1=极端恐慌或狂热）

判断intensity的依据：
- 看多或看空比例超过70%时intensity > 0.7
- 比例变化超过10%时intensity适当提高
- 中性占比超过50%时intensity < 0.3

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "rounds": [
        {
            "round": 1,
            "dominant_narrative": "主导叙事",
            "intensity": 0.0到1.0之间的小数
        }
    ]
}
注意：必须为每一轮都生成分析。"""

HIDDEN_VARIABLE_EXTRACTION_PROMPT = """\
你是A股市场隐性变量分析专家。基于金融事件和群体仿真数据，提取原始事件中\
未直接提及但从群体互动中涌现的隐性变量。

分析流程：
1. 仔细阅读原始事件描述
2. 分析仿真中各类参与者（散户、机构、游资、分析师）的反应模式
3. 识别仿真中出现但原始事件中未提及的关注点、预测和行动
4. 将这些涌现的关注点聚类为主题
5. 评估每个主题的概率和影响

重点关注：
- 隐含的政策联想（如"降准"→联想到"房地产宽松"）
- 资金面推演（如"利率下降"→"外资流入加速"）
- 产业链传导（如"原材料涨价"→"下游利润压缩"）
- 历史类比推演（如"类似2015年..."→"可能重演..."）

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "hidden_variables": [
        {
            "variable": "隐性变量名称",
            "probability": 0到1之间,
            "reasoning": "推理链条，说明为什么从群体互动中推断出此变量",
            "agent_consensus_ratio": 0到1之间,
            "is_absent_from_original": true或false
        }
    ]
}
注意：
- 必须至少产出2个隐性变量
- probability是模拟群体智慧估计，非统计概率
- is_absent_from_original为true表示原始事件中未直接提及"""

INFLECTION_POINT_PROMPT = """\
你是A股市场拐点分析专家。基于情绪演变数据和隐性变量，识别市场动态的关键转折点。

检测方法：
1. 情绪反转：看多/看空比例跨越50%的轮次
2. 叙事趋同：超过60%参与者对齐同一主题的轮次
3. 级联触发：某个隐性变量在参与者间"病毒式传播"的轮次
4. 耗竭点：情绪强度急剧下降的轮次（参与者"无话可说"=市场定价完成）

将仿真轮次映射到现实世界天数（每轮约等于1-2个交易日）。

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "inflection_points": [
        {
            "day": 天数,
            "event": "拐点描述",
            "inflection_type":
                "sentiment_reversal|narrative_convergence|cascade_trigger|exhaustion",
            "before_sentiment": {"bullish": 0.xx, "bearish": 0.xx, "neutral": 0.xx},
            "after_sentiment": {"bullish": 0.xx, "bearish": 0.xx, "neutral": 0.xx},
            "confidence": 0到1之间
        }
    ]
}
注意：必须识别至少1个拐点。"""

EXTREME_SCENARIO_PROMPT = """\
你是A股市场极端场景分析专家。基于仿真数据识别尾部风险和异常值场景。

分析流程：
1. 从情绪演变中识别偏离中位数预期的异常路径
2. 对每个异常场景评估触发条件和预警信号
3. 必须包含至少1个上行极端和1个下行极端

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "extreme_scenarios": [
        {
            "scenario": "场景描述",
            "probability": 0到1之间,
            "impact": "影响幅度如+5%或-3%",
            "direction": "upside或downside",
            "trigger_conditions": "触发此场景需要发生什么",
            "early_warning_signals": "在真实市场中应监测的预警指标"
        }
    ]
}
注意：必须至少产出1个upside和1个downside场景。"""

RECOMMENDED_ACTION_PROMPT = """\
你是A股量化交易顾问。基于完整的群体智能仿真结果，生成综合操作建议。

你将收到：情绪演变、动量转换、隐性变量、拐点分析、极端场景分析。
请综合所有信息生成一段简明的操作建议（100-200字）。

你必须严格按照以下JSON格式输出（不要输出其他内容）：
{
    "recommended_action": "综合操作建议"
}"""
