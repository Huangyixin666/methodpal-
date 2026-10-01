import json
from typing import Any, Dict, Optional

from app.schemas.ai import ExplainResponse
from app.services.llm_client import LLMError, deepseek_enabled, generate_text


try:
    from app.services.store import ANALYSES
except Exception:
    ANALYSES = {}


EXPLAIN_TEXT = {
    "academic": "回归分析结果显示，本研究构建的多元线性回归模型具有良好的整体拟合效果。在控制相关变量后，主要解释变量能够预测因变量变化。需要注意的是，该结果反映的是统计相关关系，不应直接解释为严格因果关系。",
    "plain": "简单来说，模型结果可以帮助我们判断哪些变量和因变量关系更强。不过，这只能说明它们之间存在统计关系，不能直接说明一个变量导致了另一个变量。",
    "concise": "模型结果用于判断变量之间的统计关系。请注意，相关不等于因果。",
}


def _style_instruction(style: str) -> str:
    if style == "plain":
        return "请用通俗易懂的中文解释，适合社科学生理解，不要堆砌统计术语。"
    if style == "concise":
        return "请用简洁中文解释，控制在 150 字以内。"
    return "请用中文论文结果部分的学术表达方式解释，语气正式、客观、克制。"


def _safe_dict(obj: Any) -> Dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    if hasattr(obj, "dict"):
        return obj.dict()
    return {}


def _get_analysis_from_store(analysis_id: Optional[str] = None) -> Dict[str, Any]:
    if not ANALYSES:
        return {}

    if analysis_id and analysis_id in ANALYSES:
        return _safe_dict(ANALYSES[analysis_id])

    # 如果前端没有传 analysis_id，就默认取最近一次分析。
    try:
        latest_key = list(ANALYSES.keys())[-1]
        return _safe_dict(ANALYSES[latest_key])
    except Exception:
        return {}


def _hydrate_analysis_result(
    analysis_id: Optional[str],
    analysis_result: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    result = _safe_dict(analysis_result)

    # 前端传了空对象 {}，也视为没传。
    if result:
        return result

    return _get_analysis_from_store(analysis_id)


def _extract_key_findings(result: Dict[str, Any]) -> list:
    stats = _safe_dict(result.get("stats"))
    table = result.get("regression_table") or []

    findings = []

    r2 = stats.get("r2") or stats.get("R2") or stats.get("r_squared")
    adjusted_r2 = stats.get("adjusted_r2") or stats.get("adj_r2")
    p_value = stats.get("p_value") or stats.get("model_p_value")

    if r2 is not None:
        findings.append(
            {"label": "模型解释力", "value": "R² = {}".format(r2), "color": "green"}
        )

    if adjusted_r2 is not None:
        findings.append(
            {"label": "调整后解释力", "value": "Adjusted R² = {}".format(adjusted_r2), "color": "primary"}
        )

    if p_value is not None:
        findings.append(
            {"label": "模型显著性", "value": "p = {}".format(p_value), "color": "primary"}
        )

    strongest = None
    for row in table:
        if not isinstance(row, dict):
            continue
        beta = row.get("standardized_beta")
        if beta is None:
            beta = row.get("beta")
        if beta is None:
            beta = row.get("coefficient")
        try:
            score = abs(float(beta))
        except Exception:
            continue
        if strongest is None or score > strongest[0]:
            strongest = (score, row)

    if strongest is not None:
        row = strongest[1]
        name = row.get("variable") or row.get("term") or row.get("name") or "变量"
        beta = row.get("standardized_beta")
        if beta is None:
            beta = row.get("beta")
        if beta is None:
            beta = row.get("coefficient")
        findings.append(
            {"label": "主要变量", "value": "{}（系数 = {}）".format(name, beta), "color": "primary"}
        )

    if not findings:
        findings = [
            {"label": "AI 解释来源", "value": "DeepSeek", "color": "primary"},
            {"label": "注意事项", "value": "相关不等于因果", "color": "red"},
        ]

    return findings[:3]


def _fallback_response(style: str, result: Optional[Dict[str, Any]] = None) -> ExplainResponse:
    result = result or {}
    key_findings = _extract_key_findings(result)

    return ExplainResponse(
        style=style,
        text=EXPLAIN_TEXT.get(style, EXPLAIN_TEXT["academic"]),
        key_findings=key_findings,
        warnings=["该解释基于统计相关关系，不应直接表述为因果结论。"],
        suggested_next_steps=["检查模型假设", "补充稳健性分析", "在报告中区分相关与因果表述"],
    )


def explain_result(
    style: str = "academic",
    analysis_id: Optional[str] = None,
    analysis_result: Optional[Dict[str, Any]] = None,
) -> ExplainResponse:
    result = _hydrate_analysis_result(
        analysis_id=analysis_id,
        analysis_result=analysis_result,
    )

    if not result:
        return ExplainResponse(
            style=style,
            text=(
                "当前还没有可解释的统计分析结果。请先运行一次分析，或在请求中提供 analysis_id。"
                "为了避免编造统计量，AI 不会在缺少 R²、p 值、系数等结果时生成论文式结论。"
            ),
            key_findings=[
                {"label": "分析结果", "value": "未找到", "color": "red"},
                {"label": "建议", "value": "先运行分析", "color": "primary"},
                {"label": "原则", "value": "不编造统计量", "color": "green"},
            ],
            warnings=["当前请求没有提供 analysis_result，后端也没有找到可用的 analysis_id。"],
            suggested_next_steps=["先在模型选择页运行分析", "确认前端传递 analysis_id", "再次点击 AI 解释"],
        )

    if not deepseek_enabled():
        return _fallback_response(style, result)

    system_prompt = (
        "你是 MethodPal 的科研统计解释助手，面向人文社科学生。"
        "你的任务是基于后端统计工具已经计算出的真实结果进行解释。"
        "你不能编造 R²、p 值、回归系数、样本量或诊断结果。"
        "如果结果中没有提供某个数值，就说当前结果未提供。"
        "不要把相关关系直接写成因果关系，除非用户明确提供实验或因果识别设计。"
    )

    result_json = json.dumps(result, ensure_ascii=False, indent=2)

    user_prompt = (
        "{}\n\n"
        "下面是后端统计分析结果 JSON。请基于它生成解释文本。\n\n"
        "{}\n\n"
        "输出要求：\n"
        "1. 只输出解释正文，不要输出 JSON。\n"
        "2. 不要编造未提供的统计量。\n"
        "3. 如果有 R²、调整后 R²、p 值、回归系数，请准确引用。\n"
        "4. 需要提醒用户这是统计相关，不等于严格因果。\n"
    ).format(_style_instruction(style), result_json)

    try:
        text = generate_text(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.25,
            max_tokens=1200,
        )
    except LLMError:
        return _fallback_response(style, result)

    return ExplainResponse(
        style=style,
        text=text,
        key_findings=_extract_key_findings(result),
        warnings=["该解释由 DeepSeek 基于后端统计结果生成，请结合原始数据和研究设计判断。"],
        suggested_next_steps=["检查模型假设", "补充稳健性分析", "在报告中区分相关与因果表述"],
    )