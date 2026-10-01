from typing import Any, Dict

from app.schemas.agent import AgentRequest, AgentResponse
from app.services.langgraph_agent.graph import build_graph_app, is_langgraph_available, langgraph_import_error
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
    route_after_router,
    router_node,
)
from app.services.langgraph_agent.state import MethodPalGraphState


def _initial_state(req: AgentRequest, entry: str) -> MethodPalGraphState:
    context = req.context
    return {
        "request": req,
        "entry": entry,
        "route": "",
        "response": None,
        "runtime": "langgraph" if is_langgraph_available() else "graph_fallback",
        "trace": [],
        "meta": {},
        "project_id": context.project_id,
        "dataset_id": context.dataset_id,
        "dataset_version": context.dataset_version or 0,
        "data_review_status": context.data_review_status or "pending",
        "selected_model": context.selected_model,
        "model_options": dict(context.model_options or {}),
        "selected_variables": dict(context.selected_variables or {}),
        "analysis_id": context.analysis_id,
        "stage": "",
        "autonomy_route": "",
        "autonomy": {},
        "status": "planning",
        "thread_id": req.thread_id or req.options.get("thread_id"),
    }


def _run_without_langgraph(state: MethodPalGraphState) -> MethodPalGraphState:
    """Small local graph runner used when LangGraph is not installed yet."""
    state = router_node(state)
    route = route_after_router(state)
    if route == "chat":
        return conversation_reply_node(retrieve_help_node(conversation_context_node(state)))
    if route == "recommend_variables":
        return recommend_variables_node(state)
    if route == "recommend_methods":
        return recommend_methods_node(state)
    if route == "autopilot":
        state = autonomous_intake_node(state)
        if state.get("autonomy_route") != "plan":
            return state
        state = autonomous_plan_node(state)
        if state.get("autonomy_route") != "execute":
            return state
        return autonomous_execute_node(state)
    if route == "execute":
        return execute_node(state)
    return plan_node(state)


def _annotate_response(resp: AgentResponse, final_state: Dict[str, Any]) -> AgentResponse:
    runtime = final_state.get("runtime") or "langgraph"
    graph_trace = final_state.get("trace") or []
    # Read-only conversation must not replace a paused analysis workflow or
    # disguise a help-only fallback as an actual LLM answer.
    if resp.action == 'chat':
        return resp
    try:
        resp.source = "langgraph" if runtime == "langgraph" else "langgraph_fallback"
    except Exception:
        pass

    try:
        if isinstance(resp.workflow_state, dict):
            resp.workflow_state["agent_backend"] = resp.source
            resp.workflow_state["graph_runtime"] = runtime
            if runtime != "langgraph" and langgraph_import_error():
                resp.workflow_state["langgraph_import_error"] = langgraph_import_error()
    except Exception:
        pass

    try:
        meta = getattr(resp, "trace", None)
        # Keep original AgentTraceEvent list, and add graph node trace into workflow_state
        # rather than mixing two schemas.
        if isinstance(resp.workflow_state, dict):
            resp.workflow_state["graph_trace"] = graph_trace
    except Exception:
        pass
    return resp


def run_agent_graph(req: AgentRequest, entry: str = "message") -> AgentResponse:
    state = _initial_state(req, entry)

    if is_langgraph_available():
        app = build_graph_app()
        final_state = app.invoke(state)
    else:
        final_state = _run_without_langgraph(state)

    resp = final_state.get("response")
    if resp is None:
        # Extremely defensive fallback. This should not happen unless a node
        # implementation was changed incorrectly.
        from app.services import research_agent_legacy as legacy

        resp = legacy.handle_agent_request(req)
    return _annotate_response(resp, final_state)
