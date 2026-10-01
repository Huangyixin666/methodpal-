import json
from typing import Any, Dict, Optional, Tuple

from app.services.llm_client import LLMError, deepseek_enabled, generate_text


PAGE_LABELS = {
    "home": "工作台",
    "upload": "数据工作区",
    "variables": "数据工作区 · 表头变量配置",
    "models": "模型选择",
    "results": "分析结果",
    "explain": "AI 解释",
    "report": "图表与报告",
    "unknown": "未知页面",
}


def _rule_based_reply(message: str, page: str = "home") -> str:
    q = message.strip()
    low = q.lower()
    page_name = PAGE_LABELS.get(page, page)

    if "缺失" in q or "missing" in low:
        return "如果缺失值比例较低，可以考虑均值填补、中位数填补或删除个别样本。如果缺失值超过 10%，建议先判断缺失机制，再考虑多重插补。"
    if "异常" in q or "outlier" in low:
        return "异常值建议先区分录入错误和真实极端值。量表题优先检查是否超出合法范围；连续变量可用 IQR、3σ 或领域规则辅助判断。"
    if "logistic" in low or "逻辑回归" in q:
        return "如果因变量是连续得分，优先考虑线性回归；如果因变量是 0/1 二分类结果，才更适合 Logistic 回归。"
    if "回归" in q:
        return "当前示例数据的因变量是连续型心理健康得分，自变量包括家庭支持、学业压力和睡眠时间，因此多元线性回归是一个合理的第一选择。"
    if "r²" in q or "r2" in low or "解释力" in q:
        return "R² 表示模型解释因变量方差的比例。示例中的 R² = 0.523 表示模型约解释 52.3% 的心理健康得分差异，但这不等同于因果解释。"
    if "下一步" in q or "开始" in q or "分析项目" in q or "怎么做" in q:
        return "当前页面是「{}」。建议你先创建或选择一个研究项目，然后按流程推进：上传数据 → 检查缺失值和异常值 → 选择统计模型 → 按模型 requirement 配置变量 → 运行分析 → 生成 AI 解释和图表报告。".format(page_name)

    return "我已经连接到 Python 后端。当前页面是「{}」。你刚才的问题是：{}。后续这里可以接入 DeepSeek，并让它调用数据诊断、模型推荐和统计分析工具。".format(page_name, q)


def chat_reply(
    message: str,
    page: str = "home",
    context: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    page_name = PAGE_LABELS.get(page, page)

    if not deepseek_enabled():
        return _rule_based_reply(message, page), "backend"

    system_prompt = (
        "你是 MethodPal 的 AI 研究助手，面向不太擅长统计和机器学习的人文社科学生。"
        "你需要帮助用户理解数据清洗、变量选择、统计方法、模型结果和论文式表达。"
        "重要规则：\n"
        "1. 不能编造统计结果。如果用户问具体 R²、p 值、系数，而上下文没有提供，就说明需要先运行分析。\n"
        "2. 统计计算必须由后端工具完成，不能凭空生成。\n"
        "3. 回答要中文，清楚、实用、不要太长。\n"
        "4. 不要把相关关系直接说成因果关系。\n"
        "5. 用户如果问下一步，就结合当前页面给出流程建议。\n"
    )

    safe_context = context or {}
    context_text = json.dumps(safe_context, ensure_ascii=False, indent=2)

    user_prompt = (
        "当前页面：{}。\n"
        "页面英文ID：{}。\n"
        "当前上下文 JSON：{}\n\n"
        "用户问题：{}\n\n"
        "请直接回答用户。"
    ).format(page_name, page, context_text, message)

    try:
        reply = generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.35,
            max_tokens=1000,
        )
        return reply, "llm"
    except LLMError as e:
        fallback = _rule_based_reply(message, page)
        return "{}\n\n（DeepSeek 调用失败，已使用后端规则回复。错误：{}）".format(fallback, e), "fallback"