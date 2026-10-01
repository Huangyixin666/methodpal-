from typing import Any, Dict, List, Optional

from app.schemas.agent import AgentVariableSuggestion


BINARY_TOKENS = {"0", "1", "是", "否", "男", "女", "yes", "no", "true", "false"}


def _column_type(column: Dict[str, Any]) -> str:
    return str(column.get("type") or "未知")


def _top_values(column: Dict[str, Any]) -> List[str]:
    vals = []
    for item in column.get("top_values") or []:
        value = str(item.get("value", "")).strip()
        if value:
            vals.append(value)
    return vals


def infer_type_suggestion(column: Dict[str, Any]) -> Optional[str]:
    name = str(column.get("name") or "")
    current = _column_type(column)
    unique = int(column.get("unique_count") or 0)
    top = _top_values(column)
    top_lower = {v.lower() for v in top}

    if current == "ID型" or "编号" in name or name.lower().endswith("id"):
        return "ID型"

    if unique == 2:
        if all(v in BINARY_TOKENS or v.lower() in BINARY_TOKENS for v in top_lower):
            return "二分类"
        return "二分类"

    if current in {"整数型", "连续型"} and 3 <= unique <= 7:
        if any(k in name for k in ["等级", "程度", "满意", "频率", "风险", "压力", "同意"]):
            return "有序型"

    if current == "文本型" and unique <= 20:
        return "分类型"

    return current


def infer_role_suggestion(column: Dict[str, Any], research_goal: str = "") -> str:
    name = str(column.get("name") or "")
    ctype = infer_type_suggestion(column) or _column_type(column)
    goal = research_goal or ""

    if ctype == "ID型":
        return "id"

    if name and name in goal:
        if any(k in name for k in ["得分", "评分", "结果", "结局", "风险", "是否", "心理健康", "满意度", "绩效"]):
            return "dependent"

    if any(k in name for k in ["得分", "分数", "评分", "结局", "结果", "心理健康", "满意度", "绩效", "风险"]):
        return "dependent"

    if any(k in name for k in ["年龄", "性别", "年级", "专业", "收入", "教育", "地区", "生源", "婚姻"]):
        return "control"

    if ctype in {"连续型", "整数型", "计数型", "分类型", "二分类", "有序型"}:
        return "independent"

    return "ignore"


def recommend_variables(dataset_summary: Dict[str, Any], research_goal: str = "") -> List[AgentVariableSuggestion]:
    suggestions: List[AgentVariableSuggestion] = []
    if not dataset_summary.get("found"):
        return suggestions

    for column in dataset_summary.get("columns") or []:
        name = str(column.get("name") or "")
        if not name:
            continue
        current_type = _column_type(column)
        suggested_type = infer_type_suggestion(column)
        suggested_role = infer_role_suggestion(column, research_goal)

        confidence = 0.62
        reason_parts: List[str] = []

        if suggested_type != current_type:
            confidence += 0.15
            reason_parts.append("自动类型可能需要调整：{} → {}".format(current_type, suggested_type))
        else:
            reason_parts.append("变量类型与当前数据分布基本一致。")

        if suggested_role == "dependent":
            confidence += 0.12
            reason_parts.append("变量名看起来像结果/结局指标。")
        elif suggested_role == "control":
            confidence += 0.08
            reason_parts.append("变量名看起来像常见控制变量。")
        elif suggested_role == "id":
            confidence += 0.10
            reason_parts.append("该变量更像样本标识，不建议进入模型。")

        suggestions.append(
            AgentVariableSuggestion(
                variable=name,
                current_type=current_type,
                suggested_type=suggested_type,
                suggested_role=suggested_role,
                confidence=round(min(0.95, confidence), 2),
                reason=" ".join(reason_parts),
            )
        )

    suggestions.sort(key=lambda x: (x.suggested_role != "dependent", -x.confidence, x.variable))
    return suggestions


def build_default_variable_selection(suggestions: List[AgentVariableSuggestion]) -> Dict[str, Any]:
    dependent = ""
    independent: List[str] = []
    controls: List[str] = []
    variable_types: Dict[str, str] = {}

    for s in suggestions:
        variable_types[s.variable] = s.suggested_type or s.current_type or "未知"
        if not dependent and s.suggested_role == "dependent":
            dependent = s.variable

    for s in suggestions:
        if s.variable == dependent:
            continue
        if s.suggested_role == "control":
            controls.append(s.variable)
        elif s.suggested_role == "independent":
            independent.append(s.variable)

    return {
        "dependent_variable": dependent,
        "independent_variables": independent[:8],
        "control_variables": controls[:8],
        "mediator_variables": [],
        "moderator_variables": [],
        "group_variable": None,
        "variable_types": variable_types,
    }
