"""
LangChain-compatible wrappers around MethodPal tools.

The v0.6.6 graph runtime uses deterministic nodes first. These wrappers make the
existing backend functions ready for future LangChain tool-calling / RAG nodes
without changing the statistical model implementation.
"""

from typing import Any, Dict, List

from app.services.agent_tools.analysis_tools import run_selected_analysis
from app.services.agent_tools.dataset_tools import get_dataset_summary
from app.services.agent_tools.method_tools import choose_primary_method, recommend_methods_for_selection
from app.services.agent_tools.variable_tools import build_default_variable_selection, recommend_variables
from app.services.data_transformer import apply_dataset_transform, detect_transform_intent, preview_dataset_transform


def tool_get_dataset_profile(dataset_id: str) -> Dict[str, Any]:
    return get_dataset_summary(dataset_id)


def tool_recommend_variables(dataset_id: str, research_goal: str = "") -> Dict[str, Any]:
    dataset = get_dataset_summary(dataset_id)
    suggestions = recommend_variables(dataset, research_goal)
    return {
        "dataset": dataset,
        "suggestions": [s.model_dump() if hasattr(s, "model_dump") else s.dict() for s in suggestions],
        "selection": build_default_variable_selection(suggestions),
    }


def tool_recommend_methods(dataset_id: str, selected_variables: Dict[str, Any], research_goal: str = "") -> Dict[str, Any]:
    methods = recommend_methods_for_selection(selected_variables or {}, dataset_id, research_goal)
    primary = choose_primary_method(methods, research_goal)
    return {
        "primary_method": primary,
        "methods": [m.model_dump() if hasattr(m, "model_dump") else m.dict() for m in methods],
    }


def tool_run_analysis(dataset_id: str, model_id: str, selected_variables: Dict[str, Any], options: Dict[str, Any] = None) -> Dict[str, Any]:
    return run_selected_analysis(dataset_id=dataset_id, model_id=model_id, selected_variables=selected_variables or {}, options=options or {})


def tool_detect_data_transform(message: str, dataset_id: str, selected_variables: Dict[str, Any] = None) -> Dict[str, Any]:
    op = detect_transform_intent(message or "", dataset_id, selected_variables or {})
    return {"operation": op, "matched": bool(op)}


def tool_preview_data_transform(dataset_id: str, operation: Dict[str, Any], selected_variables: Dict[str, Any] = None) -> Dict[str, Any]:
    return preview_dataset_transform(dataset_id, operation or {}, selected_variables or {})


def tool_apply_data_transform(dataset_id: str, operation: Dict[str, Any], selected_variables: Dict[str, Any] = None) -> Dict[str, Any]:
    return apply_dataset_transform(dataset_id, operation or {}, selected_variables or {})


def get_langchain_tools() -> List[Any]:
    """Return LangChain StructuredTool objects when langchain-core is installed."""
    try:
        from langchain_core.tools import StructuredTool  # type: ignore
    except Exception:
        return []

    return [
        StructuredTool.from_function(
            func=tool_get_dataset_profile,
            name="get_dataset_profile",
            description="Read the current MethodPal dataset profile and quality summary by dataset_id.",
        ),
        StructuredTool.from_function(
            func=tool_recommend_variables,
            name="recommend_variables",
            description="Recommend variable roles and variable types for the current dataset and research goal.",
        ),
        StructuredTool.from_function(
            func=tool_recommend_methods,
            name="recommend_methods",
            description="Recommend statistical or machine-learning methods based on selected variable roles.",
        ),
        StructuredTool.from_function(
            func=tool_run_analysis,
            name="run_analysis",
            description="Run a selected MethodPal statistical or machine-learning analysis tool.",
        ),
        StructuredTool.from_function(
            func=tool_detect_data_transform,
            name="detect_data_transform",
            description="Parse a natural-language data cleaning or recoding request into a safe MethodPal data transformation operation.",
        ),
        StructuredTool.from_function(
            func=tool_preview_data_transform,
            name="preview_data_transform",
            description="Preview a safe data transformation before applying it.",
        ),
        StructuredTool.from_function(
            func=tool_apply_data_transform,
            name="apply_data_transform",
            description="Apply a confirmed data transformation and create a new dataset version.",
        ),
    ]
