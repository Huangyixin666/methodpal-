from typing import Any, Dict

from app.schemas.agent import AgentRequest
from app.services import research_agent_legacy as legacy
from app.services.langgraph_agent.autonomous_workflow import (
    execute_autonomous_plan,
    prepare_autonomous_intake,
    prepare_autonomous_plan,
)
from app.services.langgraph_agent.state import MethodPalGraphState


AUTOPILOT_ALIASES = {"autopilot", "auto", "automatic"}


def _copy_state(state: MethodPalGraphState) -> MethodPalGraphState:
    copied: MethodPalGraphState = dict(state)
    copied["trace"] = list(state.get("trace") or [])
    copied["meta"] = dict(state.get("meta") or {})
    return copied


def _trace(state: MethodPalGraphState, node: str, detail: str = "") -> None:
    trace = state.setdefault("trace", [])
    trace.append({"node": node, "detail": detail})


def _request(state: MethodPalGraphState) -> AgentRequest:
    req = state.get("request")
    if req is None:
        return AgentRequest()
    return req



def _attach_response(state: MethodPalGraphState, response: Any) -> None:
    state["response"] = response
    workflow = getattr(response, "workflow_state", None) or {}
    if not isinstance(workflow, dict):
        return
    for key in [
        "project_id", "dataset_id", "dataset_version", "data_review_status",
        "selected_model", "selected_variables", "analysis_id", "stage",
    ]:
        if key in workflow and workflow.get(key) is not None:
            state[key] = workflow.get(key)


def _route_from_request(req: AgentRequest, entry: str = "message") -> str:
    action = (entry or req.action or "plan").strip().lower()
    if action == "message":
        action = (req.action or "plan").strip().lower()
    mode = (req.mode or req.options.get("mode") or "assisted").strip().lower()

    if action == "chat":
        return "chat"

    if action == "resume" or action in AUTOPILOT_ALIASES or mode in AUTOPILOT_ALIASES:
        return "autopilot"
    if action in {"recommend_variables", "variables"}:
        return "recommend_variables"
    if action in {"recommend_methods", "methods"}:
        return "recommend_methods"
    if action in {"execute", "run", "run_analysis"}:
        return "execute"
    return "plan"


def router_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    route = _route_from_request(req, next_state.get("entry") or "message")
    next_state["route"] = route
    _trace(next_state, "router", "route={}".format(route))
    return next_state


def route_after_router(state: MethodPalGraphState) -> str:
    return state.get("route") or "plan"


def conversation_context_node(state: MethodPalGraphState) -> MethodPalGraphState:
    from app.services.conversation_agent import conversation_context
    next_state = _copy_state(state)
    next_state['conversation'] = conversation_context(_request(state))
    _trace(next_state, 'conversation_context', 'owner-scoped metadata; no raw rows')
    return next_state


def retrieve_help_node(state: MethodPalGraphState) -> MethodPalGraphState:
    from app.services.conversation_agent import retrieve_help
    next_state = _copy_state(state)
    next_state['help_documents'] = retrieve_help(_request(state).message)
    _trace(next_state, 'retrieve_help', ','.join(d['id'] for d in next_state['help_documents']))
    return next_state


def conversation_reply_node(state: MethodPalGraphState) -> MethodPalGraphState:
    from app.services.conversation_agent import generate_conversation, validated_response
    next_state = _copy_state(state)
    req = _request(state)
    result, used, warning = generate_conversation(req, state['conversation'], state['help_documents'])
    next_state['response'] = validated_response(req, state['conversation'], state['help_documents'], result, used, warning)
    _trace(next_state, 'conversation_reply', 'LLM' if used else 'knowledge_fallback')
    return next_state


def plan_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    req.action = "plan"
    _attach_response(next_state, legacy.generate_plan(req))
    _trace(next_state, "plan_node", "generated guarded plan")
    return next_state


def recommend_variables_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    req.action = "recommend_variables"
    _attach_response(next_state, legacy.recommend_variable_setup(req))
    _trace(next_state, "variable_node", "recommended variables")
    return next_state


def recommend_methods_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    req.action = "recommend_methods"
    _attach_response(next_state, legacy.recommend_method_setup(req))
    _trace(next_state, "method_node", "recommended methods")
    return next_state


def autopilot_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    req.action = "autopilot"
    req.mode = "autopilot"
    _attach_response(next_state, legacy.run_autopilot(req))
    _trace(next_state, "autopilot_node", "ran automated workflow")
    return next_state


def autonomous_intake_node(state: MethodPalGraphState) -> MethodPalGraphState:
    """Inspect the workspace and pause when essential information is missing."""
    next_state = _copy_state(state)
    outcome = prepare_autonomous_intake(_request(next_state))
    next_state["autonomy"] = outcome
    next_state["autonomy_route"] = outcome.get("route") or "wait"
    session = outcome.get("session") or {}
    next_state["status"] = session.get("status") or "planning"
    next_state["thread_id"] = session.get("thread_id")
    next_state["stage"] = session.get("stage") or "autonomous_intake"
    if outcome.get("response") is not None:
        _attach_response(next_state, outcome["response"])
    _trace(next_state, "autonomous_intake", "route={}".format(next_state["autonomy_route"]))
    return next_state


def route_after_autonomous_intake(state: MethodPalGraphState) -> str:
    return state.get("autonomy_route") or "wait"


def autonomous_plan_node(state: MethodPalGraphState) -> MethodPalGraphState:
    """Build and validate a concrete model/variable plan without legacy delegation."""
    next_state = _copy_state(state)
    previous = next_state.get("autonomy") or {}
    outcome = prepare_autonomous_plan(
        _request(next_state),
        previous.get("session") or {},
        previous.get("dataset") or {},
    )
    next_state["autonomy"] = outcome
    next_state["autonomy_route"] = outcome.get("route") or "wait"
    session = outcome.get("session") or {}
    next_state["status"] = session.get("status") or "planning"
    next_state["thread_id"] = session.get("thread_id")
    next_state["stage"] = session.get("stage") or "analysis_planning"
    next_state["selected_model"] = session.get("selected_model")
    next_state["selected_variables"] = session.get("selected_variables") or {}
    if outcome.get("response") is not None:
        _attach_response(next_state, outcome["response"])
    _trace(next_state, "autonomous_plan", "route={}".format(next_state["autonomy_route"]))
    return next_state


def route_after_autonomous_plan(state: MethodPalGraphState) -> str:
    return state.get("autonomy_route") or "wait"


def autonomous_execute_node(state: MethodPalGraphState) -> MethodPalGraphState:
    """Execute the validated plan and surface failures instead of silently changing it."""
    next_state = _copy_state(state)
    outcome = next_state.get("autonomy") or {}
    response = execute_autonomous_plan(
        _request(next_state),
        outcome.get("session") or {},
        outcome.get("dataset") or {},
    )
    _attach_response(next_state, response)
    next_state["status"] = response.status
    next_state["thread_id"] = response.thread_id
    _trace(next_state, "autonomous_execute", "status={}".format(response.status))
    return next_state


def execute_node(state: MethodPalGraphState) -> MethodPalGraphState:
    next_state = _copy_state(state)
    req = _request(next_state)
    req.action = "execute"
    _attach_response(next_state, legacy.execute_agent_action(req))
    _trace(next_state, "execute_node", "executed selected analysis action")
    return next_state
