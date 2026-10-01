"""Deterministic intake policy for the MethodPal autonomous workflow.

This module intentionally depends only on the Python standard library.  The
orchestration layer can therefore be tested even when the optional LangGraph
runtime (or the full scientific Python environment) has not been installed.
"""

import re
from typing import Any, Dict, Iterable, List, Optional


GENERIC_AUTOPILOT_MESSAGES = {
    "",
    "auto",
    "automatic",
    "autopilot",
    "开始自动分析",
    "自动分析",
    "自动完成",
    "自动完成基础分析",
}


ANALYSIS_GOAL_LABELS = {
    "description": "描述数据",
    "explanation": "解释变量关系",
    "prediction": "预测结果",
    "comparison": "比较群体差异",
    "causal": "评估因果效应",
}


def meaningful_research_text(text: str) -> str:
    value = str(text or "").strip()
    if value.lower() in GENERIC_AUTOPILOT_MESSAGES:
        return ""
    return value


def infer_analysis_goal(text: str) -> Optional[str]:
    value = str(text or "").strip().lower()
    if not value:
        return None

    keyword_groups = [
        ("causal", ["因果", "处理效应", "干预效应", "导致", "cause", "causal", "treatment effect"]),
        ("prediction", ["预测", "分类", "准确率", "auc", "机器学习", "xgboost", "xgb", "predict", "forecast"]),
        ("comparison", ["比较", "差异", "对比", "组间", "compare", "difference", "versus", " vs "]),
        ("description", ["描述", "概况", "分布", "画像", "汇总", "describe", "summary", "distribution"]),
        ("explanation", ["影响因素", "哪些因素", "关系", "相关", "解释", "回归", "关联", "relationship", "association", "explain"]),
    ]
    for goal, keywords in keyword_groups:
        if any(keyword in value for keyword in keywords):
            return goal
    return None


def normalize_analysis_goal(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    aliases = {
        "description": "description",
        "describe": "description",
        "描述": "description",
        "描述数据": "description",
        "explanation": "explanation",
        "explain": "explanation",
        "解释": "explanation",
        "解释变量关系": "explanation",
        "关联": "explanation",
        "prediction": "prediction",
        "predict": "prediction",
        "预测": "prediction",
        "预测结果": "prediction",
        "comparison": "comparison",
        "compare": "comparison",
        "比较": "comparison",
        "比较群体差异": "comparison",
        "causal": "causal",
        "因果": "causal",
        "评估因果效应": "causal",
    }
    return aliases.get(text) or infer_analysis_goal(text)


def analysis_goal_label(goal: Optional[str]) -> str:
    return ANALYSIS_GOAL_LABELS.get(str(goal or ""), str(goal or "未确认"))


def requires_target(goal: Optional[str]) -> bool:
    return goal in {"explanation", "prediction", "comparison", "causal"}


def column_names(dataset_summary: Dict[str, Any]) -> List[str]:
    return [
        str(column.get("name"))
        for column in (dataset_summary.get("columns") or [])
        if column.get("name")
    ]


def match_column(value: Any, names: Iterable[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    available = [str(name) for name in names]
    lowered = {name.lower(): name for name in available}
    if text.lower() in lowered:
        return lowered[text.lower()]

    contained = [name for name in available if name.lower() in text.lower()]
    if contained:
        return sorted(contained, key=len, reverse=True)[0]
    return None


def infer_target_from_text(text: str, names: Iterable[str]) -> Optional[str]:
    value = str(text or "").lower()
    matches = [name for name in names if str(name).lower() in value]
    if not matches:
        return None
    return sorted(matches, key=len, reverse=True)[0]


def get_column(dataset_summary: Dict[str, Any], name: str) -> Optional[Dict[str, Any]]:
    for column in dataset_summary.get("columns") or []:
        if str(column.get("name") or "") == str(name or ""):
            return column
    return None


def target_type_is_ambiguous(column: Optional[Dict[str, Any]], confirmed_type: Any = None) -> bool:
    if confirmed_type:
        return False
    if not column:
        return True

    current_type = str(column.get("type") or "未知")
    unique_count = int(column.get("unique_count") or 0)
    if unique_count == 2:
        return False
    if current_type in {"未知", "文本型"}:
        return True
    return current_type in {"整数型", "连续型", "数值型", "有序型"} and 3 <= unique_count <= 7


def normalize_variable_type(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    aliases = {
        "continuous": "连续型",
        "连续": "连续型",
        "连续型": "连续型",
        "binary": "二分类",
        "二分类": "二分类",
        "二元": "二分类",
        "ordinal": "有序型",
        "有序": "有序型",
        "有序型": "有序型",
        "likert": "有序型",
        "categorical": "分类型",
        "category": "分类型",
        "分类": "分类型",
        "分类型": "分类型",
        "count": "计数型",
        "计数": "计数型",
        "计数型": "计数型",
    }
    return aliases.get(text)


def parse_column_list(value: Any, names: Iterable[str]) -> List[str]:
    available = [str(name) for name in names]
    if isinstance(value, list):
        raw_items = [str(item) for item in value]
    else:
        raw_items = re.split(r"[,，、;；\n]+", str(value or ""))

    matched: List[str] = []
    for item in raw_items:
        column = match_column(item, available)
        if column and column not in matched:
            matched.append(column)
    return matched
