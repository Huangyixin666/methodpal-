from typing import Any, Dict, List, Optional, Set, Tuple

from app.schemas.agent import AgentMethodSuggestion
from app.services.agent_tools.dataset_tools import get_dataset
from app.services.method_recommender import recommend_methods as rule_recommend_methods
from app.services.method_registry import get_method_definition


CLASSIFICATION_METHODS = {
    "logistic_regression",
    "random_forest_classification",
    "xgboost_classification",
    "catboost_classification",
    "sgd_classification",
    "svm_classification",
    "model_comparison",
}
MULTICLASS_CAPABLE_METHODS = {
    "random_forest_classification",
    "xgboost_classification",
    "catboost_classification",
    "sgd_classification",
    "svm_classification",
    "model_comparison",
}
BINARY_ONLY_METHODS = {"logistic_regression"}

_EMPTY_STRINGS = {"", "na", "nan", "none", "null", "missing", "缺失", "空"}


def _is_empty_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text.lower() in _EMPTY_STRINGS


def _norm_value(value: Any) -> str:
    text = str(value).strip()
    # Keep Chinese/category labels, but normalize numeric-looking labels such as 0.0 -> 0.
    try:
        num = float(text)
        if num.is_integer():
            return str(int(num))
    except Exception:
        pass
    return text


def _column_values(dataset: Dict[str, Any], column_name: str) -> List[Any]:
    rows = dataset.get("_full_rows") or dataset.get("rows") or []
    headers = dataset.get("headers") or []
    values: List[Any] = []
    if not column_name:
        return values

    index: Optional[int] = None
    if isinstance(headers, list) and column_name in headers:
        try:
            index = headers.index(column_name)
        except Exception:
            index = None

    for row in rows:
        if isinstance(row, dict):
            values.append(row.get(column_name))
        elif isinstance(row, list) and index is not None and 0 <= index < len(row):
            values.append(row[index])
    return values


def _summary_column_type(dataset: Dict[str, Any], column_name: str) -> str:
    profile = dataset.get("profile") or {}
    for col in profile.get("columns") or []:
        if col.get("name") == column_name:
            return str(col.get("type") or "")
    return ""


def _summary_unique_count(dataset: Dict[str, Any], column_name: str) -> Optional[int]:
    profile = dataset.get("profile") or {}
    for col in profile.get("columns") or []:
        if col.get("name") == column_name:
            value = col.get("unique_count")
            try:
                return int(value)
            except Exception:
                return None
    return None


def validate_outcome_for_methods(selection: Dict[str, Any], dataset_id: str = "") -> Dict[str, Any]:
    """Validate outcome-variable conditions that cannot be inferred from type labels alone.

    The key case is a binary-looking dependent variable that only contains one observed
    class, e.g. all 0. Logistic regression and classifiers require at least two
    non-missing classes, so the agent should not recommend or silently run them.
    """
    selection = selection or {}
    dep = selection.get("dependent_variable") or ""
    variable_types = selection.get("variable_types") or {}
    dep_type = str(variable_types.get(dep) or "")
    dataset = get_dataset(dataset_id)

    warnings: List[str] = []
    disabled_methods: Set[str] = set()
    unique_values: List[str] = []
    non_missing_count = 0

    if not dataset or not dep:
        return {
            "warnings": warnings,
            "disabled_methods": list(disabled_methods),
            "unique_values": unique_values,
            "non_missing_count": non_missing_count,
            "dependent_variable": dep,
            "dependent_type": dep_type,
        }

    if not dep_type:
        dep_type = _summary_column_type(dataset, dep)

    raw_values = _column_values(dataset, dep)
    clean_values = [_norm_value(v) for v in raw_values if not _is_empty_value(v)]
    non_missing_count = len(clean_values)
    unique_values = sorted(set(clean_values), key=lambda x: (len(x), x))

    # Fallback to profile unique_count when rows are unavailable in a legacy store.
    summary_unique = _summary_unique_count(dataset, dep)
    unique_count = len(unique_values) if unique_values else (summary_unique or 0)

    if dep_type == "二分类":
        if unique_count < 2:
            disabled_methods.update(CLASSIFICATION_METHODS)
            value_text = "、".join(unique_values[:5]) if unique_values else "无有效类别"
            warnings.append(
                "因变量「{}」当前只有 {} 个非缺失类别（{}）。二元 Logistic 回归和分类模型至少需要两个类别，因此本轮不能运行这些模型。".format(
                    dep, unique_count, value_text
                )
            )
        elif unique_count > 2:
            disabled_methods.update(BINARY_ONLY_METHODS)
            warnings.append(
                "因变量「{}」被标记为二分类，但检测到 {} 个类别。二元 Logistic 不适用；可改用 XGBoost/CatBoost/随机森林/SVM/SGD 多分类模型，或先用数据转换工具重编码为 0/1。".format(dep, unique_count)
            )

    if dep_type in {"分类型", "有序型"}:
        if unique_count < 2:
            disabled_methods.update(CLASSIFICATION_METHODS)
            warnings.append("因变量「{}」当前少于两个有效类别，分类模型无法运行。".format(dep))

    if non_missing_count > 0 and unique_count <= 1 and dep_type in {"连续型", "整数型", "计数型", "有序型"}:
        disabled_methods.update({
            "linear_regression",
            "poisson_regression",
            "lasso_regression",
            "ridge_regression",
            "elastic_net_regression",
            "random_forest_regression",
            "svm_regression",
            "model_comparison",
        })
        warnings.append(
            "因变量「{}」几乎没有变化（仅 {} 个唯一值），回归/预测模型无法估计有效关系，建议先检查变量编码或改用描述统计。".format(dep, unique_count)
        )

    return {
        "warnings": warnings,
        "disabled_methods": sorted(disabled_methods),
        "unique_values": unique_values[:20],
        "non_missing_count": non_missing_count,
        "dependent_variable": dep,
        "dependent_type": dep_type,
    }


def get_method_validation_warnings(selection: Dict[str, Any], dataset_id: str = "") -> List[str]:
    return list(validate_outcome_for_methods(selection, dataset_id).get("warnings") or [])


def recommend_methods_for_selection(selection: Dict[str, Any], dataset_id: str = "", research_goal: str = "") -> List[AgentMethodSuggestion]:
    payload = dict(selection or {})
    payload["dataset_id"] = dataset_id
    payload["research_goal"] = research_goal

    try:
        resp = rule_recommend_methods(payload)
        methods = resp.recommended
    except Exception:
        methods = []

    validation = validate_outcome_for_methods(selection, dataset_id)
    disabled_methods = set(validation.get("disabled_methods") or [])
    validation_reason = "；".join(validation.get("warnings") or [])

    result: List[AgentMethodSuggestion] = []
    for item in methods:
        enabled = bool(item.enabled)
        reason = item.reason or item.disabled_reason or ""
        if item.method_id in disabled_methods:
            enabled = False
            reason = validation_reason or "因变量当前不满足该模型的可执行条件。"
        result.append(
            AgentMethodSuggestion(
                method_id=item.method_id,
                name=item.name,
                category=item.category,
                score=item.score,
                enabled=enabled,
                selectable=bool(getattr(item, "selectable", enabled)),
                availability=str(getattr(item, "availability", "ready" if enabled else "incompatible")),
                missing_roles=list(getattr(item, "missing_roles", []) or []),
                reason=reason,
                outputs=item.outputs,
            )
        )
    return result


def choose_primary_method(methods: List[AgentMethodSuggestion], goal: str = "") -> str:
    """Choose a primary method, respecting explicit user model requests.

    Earlier versions ranked traditional explanatory models first. That is still
    reasonable as a default, but it caused a mismatch in the Agent side panel:
    the LLM could say "I recommend XGBoost" after the user explicitly asked for
    xgb-like feature contribution, while the structured action payload still
    carried the old default Logistic model.

    This function is intentionally rule-based: if the user names a concrete
    model family and that model is enabled, the returned method id must match the
    user's request. The LLM may polish the wording, but it is not allowed to
    silently change the executable model.
    """
    goal_text = (goal or "").lower()

    def first_enabled(candidates: List[str]) -> str:
        for mid in candidates:
            for m in methods:
                if m.method_id == mid and m.enabled:
                    return m.method_id
        for mid in candidates:
            for m in methods:
                if m.method_id == mid and getattr(m, "selectable", False):
                    return m.method_id
        return ""

    explicit_groups: List[Tuple[List[str], List[str]]] = [
        (
            ["xgboost", "xgb", "极端梯度", "提升树"],
            ["xgboost_classification", "xgb_classification", "xgboost_regression", "xgb_regression", "xgboost_multiclass"],
        ),
        (
            ["catboost", "cat boost"],
            ["catboost_classification", "catboost_regression", "catboost_multiclass"],
        ),
        (
            ["随机森林", "random forest", "rf"],
            ["random_forest_classification", "random_forest_classifier", "random_forest_regression"],
        ),
        (
            ["sgd", "随机梯度"],
            ["sgd_classification", "sgd_classifier", "sgd_regression"],
        ),
        (
            ["svm", "支持向量"],
            ["svm_classification", "svm_regression"],
        ),
        (
            ["lasso"],
            ["lasso_regression"],
        ),
        (
            ["ridge", "岭回归"],
            ["ridge_regression"],
        ),
        (
            ["elastic", "弹性网", "elasticnet"],
            ["elastic_net_regression"],
        ),
        (
            ["逻辑回归", "logistic"],
            ["logistic_regression"],
        ),
    ]
    for keywords, candidates in explicit_groups:
        if any(k in goal_text for k in keywords):
            chosen = first_enabled(candidates)
            if chosen:
                return chosen

    if "变量重要" in goal_text or "特征重要" in goal_text or "贡献度" in goal_text or "feature importance" in goal_text or "shap" in goal_text:
        chosen = first_enabled([
            "xgboost_classification",
            "catboost_classification",
            "random_forest_classification",
            "xgboost_regression",
            "catboost_regression",
            "random_forest_regression",
        ])
        if chosen:
            return chosen

    if "预测" in goal_text or "prediction" in goal_text or "compare" in goal_text or "对比" in goal_text:
        chosen = first_enabled([
            "model_comparison",
            "xgboost_classification",
            "catboost_classification",
            "random_forest_classification",
            "xgboost_regression",
            "catboost_regression",
            "random_forest_regression",
            "lasso_regression",
        ])
        if chosen:
            return chosen

    for preferred in ["linear_regression", "logistic_regression", "xgboost_classification", "catboost_classification", "random_forest_classification", "mediation_analysis", "moderation_analysis", "model_comparison"]:
        for m in methods:
            if m.method_id == preferred and m.enabled:
                return m.method_id

    for m in methods:
        if m.enabled:
            return m.method_id
    for m in methods:
        if getattr(m, "selectable", False):
            return m.method_id
    return "descriptive_statistics"


def get_method_configuration(method_id: str, selection: Dict[str, Any], dataset_id: str = "", research_goal: str = "") -> Dict[str, Any]:
    """Return the centralized requirement evaluation for one selected method."""
    payload = dict(selection or {})
    payload.update({
        "dataset_id": dataset_id,
        "research_goal": research_goal,
        "selected_model": method_id,
    })
    try:
        response = rule_recommend_methods(payload)
        for item in response.recommended:
            if item.method_id == method_id:
                if hasattr(item, "model_dump"):
                    return item.model_dump()
                if hasattr(item, "dict"):
                    return item.dict()
                return dict(item)
    except Exception:
        pass
    return {
        "method_id": method_id,
        "enabled": False,
        "selectable": False,
        "availability": "incompatible",
        "reason": "无法校验当前模型 requirement。",
        "missing_roles": [],
        "validation_messages": ["无法校验当前模型 requirement。"],
    }



def build_selection_for_method(
    method_id: str,
    selection: Optional[Dict[str, Any]] = None,
    dataset_id: str = "",
    research_goal: str = "",
) -> Tuple[Dict[str, Any], List[Any]]:
    """Build a conservative, model-specific variable-role proposal.

    The old Agent always created a generic Y/X/control setup before it knew the
    model.  This helper reverses that dependency: it reads the selected method's
    requirement schema and fills only the roles required by that method.  The
    proposal is still reviewable and never mutates the project by itself.
    """
    from app.services.agent_tools.variable_tools import recommend_variables

    current = dict(selection or {})
    dataset = get_dataset(dataset_id) or {}
    definition = get_method_definition(method_id) or {}
    requirement = definition.get("requirement") or {}
    suggestions = recommend_variables({
        "found": bool(dataset),
        "columns": ((dataset.get("profile") or {}).get("columns") or []),
    }, research_goal)

    profile_types = {
        str(c.get("name")): str(c.get("type") or "未知")
        for c in ((dataset.get("profile") or {}).get("columns") or [])
        if c.get("name")
    }
    variable_types = {**profile_types, **(current.get("variable_types") or {})}
    all_names = list(profile_types.keys())
    suggestion_rank = {s.variable: i for i, s in enumerate(suggestions)}
    suggestion_role = {s.variable: s.suggested_role for s in suggestions}

    result: Dict[str, Any] = {
        "dependent_variable": current.get("dependent_variable") or "",
        "independent_variables": list(current.get("independent_variables") or []),
        "control_variables": list(current.get("control_variables") or []),
        "mediator_variables": list(current.get("mediator_variables") or []),
        "moderator_variables": list(current.get("moderator_variables") or []),
        "group_variable": current.get("group_variable") or None,
        "variable_types": variable_types,
    }

    def values(role_id: str) -> List[str]:
        value = result.get(role_id)
        if isinstance(value, list):
            return [str(v) for v in value if v in variable_types]
        return [str(value)] if value and str(value) in variable_types else []

    def assign(role_id: str, chosen: List[str]) -> None:
        if role_id in {"dependent_variable", "group_variable"}:
            result[role_id] = chosen[0] if chosen else ("" if role_id == "dependent_variable" else None)
        else:
            result[role_id] = chosen

    used: Set[str] = set()
    for rid in ["dependent_variable", "group_variable", "mediator_variables", "moderator_variables"]:
        used.update(values(rid))

    role_requirements = list(requirement.get("required_roles") or []) + list(requirement.get("optional_roles") or [])
    for role_req in role_requirements:
        role_id = str(role_req.get("role") or "")
        min_count = int(role_req.get("min_count") or 0)
        max_count = role_req.get("max_count")
        allowed = set(role_req.get("allowed_types") or [])
        existing = [name for name in values(role_id) if not allowed or variable_types.get(name) in allowed]

        # Optional roles are preserved but not automatically filled.  This keeps
        # control/mediator/moderator choices conservative unless the model truly
        # requires them.
        if min_count <= 0:
            assign(role_id, existing)
            used.update(existing)
            continue

        need = max(0, min_count - len(existing))
        if need:
            candidates = [
                name for name in all_names
                if name not in used and name not in existing and (not allowed or variable_types.get(name) in allowed)
            ]

            def priority(name: str) -> Tuple[int, int, int]:
                suggested = suggestion_role.get(name, "")
                if role_id == "dependent_variable":
                    role_priority = 0 if suggested == "dependent" else 2
                elif role_id == "group_variable":
                    role_priority = 0 if variable_types.get(name) == "二分类" else (1 if suggested in {"control", "independent"} else 2)
                else:
                    role_priority = 0 if suggested == "independent" else (1 if suggested == "control" else 2)
                return (role_priority, suggestion_rank.get(name, 10_000), all_names.index(name))

            candidates.sort(key=priority)
            existing.extend(candidates[:need])

        if max_count is not None:
            existing = existing[: int(max_count)]
        elif role_id == "independent_variables":
            # Offer a useful but reviewable set instead of silently selecting all
            # columns in wide datasets.
            existing = existing[:8]
        assign(role_id, existing)
        used.update(existing)

    # Remove accidental overlap.  A column should not occupy multiple model
    # roles in the generated proposal; users can still change it manually.
    singletons = {result.get("dependent_variable"), result.get("group_variable")}
    singletons.discard(None)
    singletons.discard("")
    result["independent_variables"] = [v for v in result.get("independent_variables") or [] if v not in singletons]
    result["control_variables"] = [v for v in result.get("control_variables") or [] if v not in singletons and v not in result["independent_variables"]]
    result["mediator_variables"] = [v for v in result.get("mediator_variables") or [] if v not in singletons]
    result["moderator_variables"] = [v for v in result.get("moderator_variables") or [] if v not in singletons]
    return result, suggestions
