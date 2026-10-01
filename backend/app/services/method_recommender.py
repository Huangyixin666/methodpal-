from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

from app.schemas.method import MethodItem, MethodRecommendResponse
from app.services.method_registry import METHOD_DEFINITIONS
from app.services.store import DATASETS


ROLE_FIELDS = {
    "dependent_variable": "dependent_variable",
    "independent_variables": "independent_variables",
    "control_variables": "control_variables",
    "mediator_variables": "mediator_variables",
    "moderator_variables": "moderator_variables",
    "group_variable": "group_variable",
}


TYPE_ALIASES = {
    "id": "ID型",
    "identifier": "ID型",
    "id型": "ID型",
    "continuous": "连续型",
    "float": "连续型",
    "numeric": "连续型",
    "number": "连续型",
    "integer": "整数型",
    "int": "整数型",
    "count": "计数型",
    "categorical": "分类型",
    "category": "分类型",
    "binary": "二分类",
    "boolean": "二分类",
    "bool": "二分类",
    "ordinal": "有序型",
    "text": "文本型",
    "string": "文本型",
    "unknown": "未知",
}


def _normalize_type(value: Any) -> str:
    text = str(value or "未知").strip()
    return TYPE_ALIASES.get(text.lower(), text)


def _dataset(payload: Dict[str, Any]) -> Dict[str, Any]:
    dataset_id = str(payload.get("dataset_id") or "")
    return DATASETS.get(dataset_id) or {}


def _variable_types(payload: Dict[str, Any], dataset: Dict[str, Any]) -> Dict[str, str]:
    inferred: Dict[str, str] = {}
    profile = dataset.get("profile") if isinstance(dataset.get("profile"), dict) else {}
    for col in profile.get("columns") or []:
        name = str(col.get("name") or "")
        if name:
            inferred[name] = _normalize_type(col.get("type"))
    inferred.update({str(k): _normalize_type(v) for k, v in (payload.get("variable_types") or {}).items()})
    return inferred


def _row_count(dataset: Dict[str, Any]) -> int:
    profile = dataset.get("profile") if isinstance(dataset.get("profile"), dict) else {}
    try:
        return int(profile.get("row_count") or dataset.get("full_row_count") or len(dataset.get("_full_rows") or dataset.get("rows") or []))
    except Exception:
        return 0


def _role_values(payload: Dict[str, Any], role_id: str) -> List[str]:
    field = ROLE_FIELDS.get(role_id, role_id)
    value = payload.get(field)
    if isinstance(value, list):
        return [str(v) for v in value if str(v or "").strip()]
    if value is None or not str(value).strip():
        return []
    return [str(value)]


def _candidate_names(type_map: Dict[str, str], allowed_types: Iterable[str]) -> List[str]:
    allowed = set(allowed_types or [])
    return [name for name, value in type_map.items() if value in allowed]


def _goal_score(method_id: str, goal: str) -> float:
    text = (goal or "").lower()
    score = 0.0
    if any(k in text for k in ["描述", "分布", "缺失", "summary"]):
        score += 0.18 if method_id == "descriptive_statistics" else 0.0
    if any(k in text for k in ["相关", "relation", "correlation"]):
        score += 0.20 if method_id == "correlation_analysis" else 0.0
    if any(k in text for k in ["差异", "比较组", "t检验", "anova"]):
        score += 0.18 if method_id in {"t_test", "one_way_anova", "chi_square_test"} else 0.0
    if any(k in text for k in ["预测", "分类", "accuracy", "auc", "f1", "predict"]):
        score += 0.18 if method_id in {
            "model_comparison", "xgboost_classification", "catboost_classification",
            "random_forest_classification", "svm_classification", "sgd_classification",
            "xgboost_regression", "catboost_regression", "random_forest_regression",
        } else 0.0
    if any(k in text for k in ["影响", "解释", "因素", "显著", "effect", "explain"]):
        score += 0.16 if method_id in {"linear_regression", "logistic_regression", "poisson_regression"} else 0.0
    if any(k in text for k in ["中介", "mediation"]):
        score += 0.28 if method_id == "mediation_analysis" else 0.0
    if any(k in text for k in ["调节", "moderation", "交互"]):
        score += 0.28 if method_id == "moderation_analysis" else 0.0
    explicit = {
        "xgboost": ["xgboost_classification", "xgboost_regression"],
        "catboost": ["catboost_classification", "catboost_regression"],
        "随机森林": ["random_forest_classification", "random_forest_regression"],
        "random forest": ["random_forest_classification", "random_forest_regression"],
        "logistic": ["logistic_regression"],
        "逻辑回归": ["logistic_regression"],
        "lasso": ["lasso_regression"],
        "ridge": ["ridge_regression"],
        "svm": ["svm_classification", "svm_regression"],
    }
    for keyword, ids in explicit.items():
        if keyword in text and method_id in ids:
            score += 0.35
    return score


def _evaluate(definition: Dict[str, Any], payload: Dict[str, Any], dataset: Dict[str, Any]) -> MethodItem:
    method_id = definition["method_id"]
    implemented = bool(definition.get("implemented", True))
    type_map = _variable_types(payload, dataset)
    requirement = definition.get("requirement") or {}
    required_roles = requirement.get("required_roles") or []
    row_count = _row_count(dataset)
    dataset_present = bool(dataset)

    missing_roles: List[str] = []
    validation_messages: List[str] = []
    impossible_roles: List[str] = []

    for role_req in required_roles:
        role_id = str(role_req.get("role") or "")
        label = str(role_req.get("label") or role_id)
        min_count = int(role_req.get("min_count") or 0)
        max_count = role_req.get("max_count")
        allowed_types = list(role_req.get("allowed_types") or [])
        values = _role_values(payload, role_id)

        if len(values) < min_count:
            missing_roles.append(role_id)
            candidates = _candidate_names(type_map, allowed_types)
            if dataset_present and len(candidates) < min_count:
                impossible_roles.append(role_id)
                validation_messages.append(
                    "数据中缺少足够的{}候选字段（需要 {} 个，当前可识别 {} 个）。".format(label, min_count, len(candidates))
                )
            continue

        if max_count is not None and len(values) > int(max_count):
            validation_messages.append("{}最多选择 {} 个字段。".format(label, max_count))

        for name in values:
            current_type = type_map.get(name, "未知")
            if allowed_types and current_type not in allowed_types:
                validation_messages.append(
                    "{}「{}」当前类型为“{}”，该模型要求：{}。".format(label, name, current_type, "、".join(allowed_types))
                )

    min_rows = int(requirement.get("min_rows") or 2)
    if dataset_present and row_count and row_count < min_rows:
        validation_messages.append("样本量不足：至少需要 {} 行数据。".format(min_rows))
        impossible_roles.append("row_count")

    if not implemented:
        availability = "unsupported"
        selectable = False
        enabled = False
        reason = "当前版本保留入口，后续实现。"
    elif not dataset_present:
        availability = "incompatible"
        selectable = False
        enabled = False
        reason = "请先上传数据，再选择模型。"
    elif impossible_roles:
        availability = "incompatible"
        selectable = False
        enabled = False
        reason = validation_messages[0] if validation_messages else "当前数据缺少该模型所需的字段类型。"
    elif missing_roles or validation_messages:
        availability = "needs_configuration"
        selectable = True
        enabled = False
        if validation_messages:
            reason = validation_messages[0]
        else:
            labels = [str(r.get("label") or r.get("role")) for r in required_roles if r.get("role") in missing_roles]
            reason = "可以先选择；随后需要配置：{}。".format("、".join(labels))
    else:
        availability = "ready"
        selectable = True
        enabled = True
        reason = "变量配置满足要求，可以运行。"

    base = 0.62
    if availability == "ready":
        base += 0.22
    elif availability == "needs_configuration":
        base += 0.10
    elif availability in {"incompatible", "unsupported"}:
        base -= 0.28
    if payload.get("selected_model") == method_id:
        base += 0.12
    base += _goal_score(method_id, str(payload.get("research_goal") or ""))

    return MethodItem(
        **definition,
        score=round(max(0.0, min(1.0, base)), 4),
        reason=reason,
        enabled=enabled,
        selectable=selectable,
        availability=availability,
        missing_roles=missing_roles,
        validation_messages=validation_messages,
        disabled_reason=reason if not selectable else None,
    )


def method_catalog() -> List[MethodItem]:
    # Catalog is metadata-only. Methods remain selectable until a dataset-specific
    # recommendation evaluates their compatibility.
    items: List[MethodItem] = []
    for definition in METHOD_DEFINITIONS:
        implemented = bool(definition.get("implemented", True))
        reason = "选择后根据模型要求配置变量。" if implemented else "当前版本保留入口，后续实现。"
        items.append(MethodItem(
            **definition,
            score=0.5 if implemented else 0.1,
            reason=reason,
            enabled=False,
            selectable=implemented,
            availability="selectable" if implemented else "unsupported",
            disabled_reason=None if implemented else reason,
        ))
    return items


def recommend_methods(payload: Dict[str, Any]) -> MethodRecommendResponse:
    dataset = _dataset(payload)
    items = [_evaluate(definition, payload, dataset) for definition in METHOD_DEFINITIONS]
    items.sort(key=lambda item: (item.selectable, item.enabled, item.score), reverse=True)
    return MethodRecommendResponse(
        recommended=items,
        message="已根据数据字段类型、当前变量角色、研究目标和模型 requirement 统一评估方法。模型可先选择，缺少的角色会在数据工作区中补充。",
    )
