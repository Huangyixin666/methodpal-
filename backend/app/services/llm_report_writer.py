from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from app.services.report_context_builder import compact_json

try:
    from app.services.llm_client import deepseek_enabled, generate_text
except Exception:
    def deepseek_enabled() -> bool:
        return False

    def generate_text(system_prompt: str, user_prompt: str, temperature: float = 0.2, max_tokens: int = 2200) -> str:
        raise RuntimeError("LLM client is not configured")


_STYLE_GUIDE = {
    "academic": "偏社科论文结果部分：正式、审慎、强调统计含义、图表解释、研究问题联系与局限性。",
    "plain": "通俗解释：少用术语，但不要牺牲统计准确性；每张图都要说明它说明了什么。",
    "concise": "简洁摘要：压缩篇幅，突出主要发现、图表含义和注意事项。",
}


_PREFACE_PATTERNS = [
    r"^好的[，,。！!\s]*这是根据您[^\n]*?报告(?:草稿)?[。！!]?\s*",
    r"^好的[，,。！!\s]*以下是根据您[^\n]*?报告(?:草稿)?[。！!]?\s*",
    r"^下面是根据您[^\n]*?报告(?:草稿)?[。！!]?\s*",
    r"^以下是根据您[^\n]*?报告(?:草稿)?[。！!]?\s*",
    r"^当然[，,。！!\s]*以下是[^\n]*?报告(?:草稿)?[。！!]?\s*",
]


def _strip_code_fence(text: str) -> str:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _strip_report_preface(text: str) -> str:
    """Remove chatty LLM prefaces so report output starts as a report."""
    text = (text or "").strip()
    for pattern in _PREFACE_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    # Remove leading markdown separators or redundant blank separators.
    text = re.sub(r"^(?:[-*_]\s*){3,}\s*", "", text).strip()
    # If the model still produced a preface line followed by the real title, keep from first heading.
    m = re.search(r"(?m)^#\s+", text)
    if m and m.start() > 0:
        prefix = text[:m.start()].strip()
        if len(prefix) < 120 and any(k in prefix for k in ["根据", "生成", "报告", "草稿", "如下"]):
            text = text[m.start():].strip()
    return text.strip()


def llm_available() -> bool:
    try:
        return bool(deepseek_enabled())
    except Exception:
        return False


def _looks_like_bad_chart_refusal(text: str) -> bool:
    lowered = text.replace(" ", "")
    bad_phrases = [
        "未提供任何图表",
        "没有提供任何图表",
        "事实包中未包含变量分布图",
        "无法进行图表解读",
        "当前结果未提供任何图表",
        "未包含任何可视化输出",
    ]
    return any(p in lowered for p in bad_phrases)


def _looks_like_bad_missing_model_claim(text: str) -> bool:
    lowered = text.replace(" ", "")
    bad_phrases = [
        "模型信息缺失",
        "未提供任何模型类型",
        "事实包未提供任何模型类型",
        "无法进行实质性统计推断",
    ]
    return any(p in lowered for p in bad_phrases)


def write_llm_report(fact_pack: Dict[str, Any], style: str = "academic") -> Tuple[bool, str, str]:
    """Return (ok, content, reason).

    The LLM is used only as a writer over a structured fact pack. It must not
    invent statistics. Callers keep a deterministic template fallback.
    """
    if not llm_available():
        return False, "", "LLM 未配置或 DEEPSEEK_API_KEY 不可用，已回退到规则模板。"

    flags = fact_pack.get("raw_available_flags") or {}
    has_charts = bool(flags.get("has_charts") or flags.get("chart_count"))
    has_model_info = bool(flags.get("has_model_info") or (fact_pack.get("analysis") or {}).get("model_name"))
    title = ((fact_pack.get("report") or {}).get("title") or "MethodPal 分析报告").strip()

    system_prompt = """
你是 MethodPal 的统计报告写作助手。你的任务不是聊天回复，而是直接输出一份可以复制到 Word/Overleaf/Notion 的中文 Markdown 分析报告。

输出格式硬性要求：
1. 第一行必须直接是一级标题：# {title}
2. 不要写任何寒暄、前缀或元说明，例如“好的”“以下是”“这是根据您提供的……生成的报告草稿”。
3. 标题前不要输出 `---`、分割线、代码块或 JSON。
4. 正文使用 Markdown，但不要包在代码块里。
5. 建议章节顺序：数据与变量、模型方法、主要结果、图表解读、结论与建议、局限性。

事实使用硬性规则：
1. 只能使用事实包中出现的模型、变量、指标、图表和警告；不能编造样本量、p 值、置信区间、系数、AUC、准确率或任何数字。
2. 如果事实包没有提供某类统计量，才可以写“当前结果未提供”；如果事实包已经提供模型/图表/指标，不得说“未提供”。
3. 变量角色必须以 fact_pack.variables 为准，尤其是 dependent_variable、independent_variables、control_variables；不要根据聊天历史、变量推荐或图表标题重新猜变量角色。
4. 模型信息必须以 fact_pack.analysis.model_name / model_key / method 为准。只要这些字段存在，就不要写“模型信息缺失”。
5. 可以参考 fact_pack.ai_explanation，但如果 AI 解释和结构化事实冲突，以结构化事实为准。

图表解读硬性规则：
1. 图表解读必须基于 fact_pack.charts 里的底层数据，而不是基于图片本身。即使看不到图片，也要根据 chart.data、matrix_facts、ranked_feature_importance、prediction_scatter_summary、descriptive_values 等字段解释。
2. 如果 fact_pack.raw_available_flags.has_charts=true，必须逐图解读“图 1、图 2 ...”，不得写“当前结果未提供任何图表”或“无法进行图表解读”。
3. 每张图都不要只复述统计值，必须写出三个层次：
   - 读图方式：这张图怎么看，这个指标/矩阵/点云代表什么；
   - 主要发现：结合图表底层数据指出最重要的模式；
   - 研究含义与边界：结合研究目标和因变量说明它对研究问题有什么启发，以及不能推断什么。
4. 对特征重要性图：列出前几个重要变量及其数值，并说明它们是在预测因变量时的相对贡献；不得写成“显著影响”或“因果影响”。
5. 对混淆矩阵：说明行/列含义、对角线正确预测、非对角线混淆，指出主要混淆类别对及其对模型可靠性的含义。
6. 对相关矩阵：说明绝对相关较高的变量对，解释其可能提示共线性、变量聚类或后续建模注意事项；相关不等于因果。
7. 对预测值 vs 真实值图：说明点越接近对角线表示预测越接近真实值；MAE 是平均绝对误差，RMSE 对大误差更敏感，二者越小越好；结合数值判断拟合/泛化情况。
8. 对描述统计均值图：说明均值只是描述性比较；若变量单位不同，不要把均值大小当成变量重要性。
9. 如果模型是 XGBoost、CatBoost、随机森林、SVM、SGD 等机器学习模型，必须把“预测贡献/变量重要性”和“因果效应/统计显著性”区分开。
10. 如果指标接近 1.0 或事实包提示数据泄露/过拟合风险，需要明确提醒。
""".format(title=title).strip()

    user_prompt = """
写作风格：{style_guide}

下面是结构化统计事实包。请直接输出报告正文，第一行必须是 `# {title}`。不要输出“好的”“以下是”“---”等前缀。

请特别注意：图表信息在 `charts` 字段，里面包含每张图背后的矩阵或数据行；请逐图解释其读法、主要发现、与研究目标的关系和解释边界。

{fact_pack}
""".format(
        style_guide=_STYLE_GUIDE.get(style, _STYLE_GUIDE["academic"]),
        title=title,
        fact_pack=compact_json(fact_pack, max_chars=30000),
    ).strip()

    try:
        text = generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.12,
            max_tokens=3800,
        )
        text = _strip_report_preface(_strip_code_fence(text))
        if not text:
            return False, "", "LLM 返回为空，已回退到规则模板。"
        if len(text) < 200:
            return False, "", "LLM 返回内容过短，已回退到规则模板。"
        if text.lstrip().startswith("{") and "fact_pack" in text[:300]:
            return False, "", "LLM 返回了结构化数据而不是报告正文，已回退到规则模板。"
        if has_charts and _looks_like_bad_chart_refusal(text):
            return False, "", "LLM 忽略了已选图表数据，已回退到规则模板。"
        if has_model_info and _looks_like_bad_missing_model_claim(text):
            return False, "", "LLM 忽略了模型信息，已回退到规则模板。"
        if not text.lstrip().startswith("#"):
            text = "# {}\n\n{}".format(title, text.strip())
        return True, text.strip() + "\n", "llm_report_writer"
    except Exception as exc:
        return False, "", "LLM 报告生成失败，已回退到规则模板：{}".format(exc)
