from functools import lru_cache
from typing import Optional

from app.services.langgraph_agent.nodes import (
    conversation_context_node,
    retrieve_help_node,
    conversation_reply_node,
    autonomous_execute_node,
    autonomous_intake_node,
    autonomous_plan_node,
    execute_node,
    plan_node,
    recommend_methods_node,
    recommend_variables_node,
    route_after_autonomous_intake,
    route_after_autonomous_plan,
    route_after_router,
    router_node,
)
from app.services.langgraph_agent.state import MethodPalGraphState

_LANGGRAPH_IMPORT_ERROR: Optional[Exception] = None

try:
    from langgraph.graph import END, StateGraph  # type: ignore
except Exception as exc:  # LangGraph is optional during the migration patch.
    END = None  # type: ignore
    StateGraph = None  # type: ignore
    _LANGGRAPH_IMPORT_ERROR = exc


def is_langgraph_available() -> bool:
    return StateGraph is not None and END is not None


def langgraph_import_error() -> str:
    return "" if _LANGGRAPH_IMPORT_ERROR is None else str(_LANGGRAPH_IMPORT_ERROR)


@lru_cache(maxsize=1)
def build_graph_app():
    if not is_langgraph_available():
        raise RuntimeError("LangGraph is not installed or cannot be imported: {}".format(langgraph_import_error()))

    workflow = StateGraph(MethodPalGraphState)
    workflow.add_node("router", router_node)
    workflow.add_node("conversation_context", conversation_context_node)
    workflow.add_node("retrieve_help", retrieve_help_node)
    workflow.add_node("conversation_reply", conversation_reply_node)
    workflow.add_node("plan", plan_node)
    workflow.add_node("recommend_variables", recommend_variables_node)
    workflow.add_node("recommend_methods", recommend_methods_node)
    workflow.add_node("autonomous_intake", autonomous_intake_node)
    workflow.add_node("autonomous_plan", autonomous_plan_node)
    workflow.add_node("autonomous_execute", autonomous_execute_node)
    workflow.add_node("execute", execute_node)

    workflow.set_entry_point("router")
    workflow.add_conditional_edges(
        "router",
        route_after_router,
        {
            "chat": "conversation_context",
            "plan": "plan",
            "recommend_variables": "recommend_variables",
            "recommend_methods": "recommend_methods",
            "autopilot": "autonomous_intake",
            "execute": "execute",
        },
    )

    workflow.add_conditional_edges(
        "autonomous_intake",
        route_after_autonomous_intake,
        {"wait": END, "plan": "autonomous_plan"},
    )
    workflow.add_conditional_edges(
        "autonomous_plan",
        route_after_autonomous_plan,
        {"wait": END, "execute": "autonomous_execute"},
    )
    workflow.add_edge("autonomous_execute", END)

    workflow.add_edge("plan", END)
    workflow.add_edge("recommend_variables", END)
    workflow.add_edge("recommend_methods", END)
    workflow.add_edge("execute", END)
    workflow.add_edge("conversation_context", "retrieve_help")
    workflow.add_edge("retrieve_help", "conversation_reply")
    workflow.add_edge("conversation_reply", END)
    return workflow.compile()
