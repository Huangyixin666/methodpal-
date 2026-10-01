"""
MethodPal research agent entrypoints.

v0.6.6 migrates the agent orchestration layer to a LangGraph-backed workflow
when LangGraph is installed. The old v0.6.5 implementation is kept in
research_agent_legacy.py as a safe fallback, so the app still runs even before
LangGraph dependencies are installed.
"""

import os
from typing import Callable

from app.schemas.agent import AgentRequest, AgentResponse
from app.services import research_agent_legacy as legacy


GraphFallback = Callable[[AgentRequest], AgentResponse]


def _should_use_langgraph() -> bool:
    raw = os.getenv("METHODPAL_AGENT_BACKEND", "langgraph").strip().lower()
    return raw not in {"legacy", "classic", "rule", "rules", "off", "0", "false"}


def _call_graph(entry: str, req: AgentRequest, fallback: GraphFallback) -> AgentResponse:
    if not _should_use_langgraph():
        resp = fallback(req)
        if entry == "chat":
            return resp
        try:
            resp.source = "legacy_agent"
        except Exception:
            pass
        return resp

    try:
        from app.services.langgraph_agent.executor import run_agent_graph

        return run_agent_graph(req=req, entry=entry)
    except Exception as exc:
        if entry == "chat":
            # Preserve HTTP rate limits and authorization failures.
            from fastapi import HTTPException
            if isinstance(exc, HTTPException):
                raise
            return fallback(req)
        # Keep the MVP robust on Windows / Python 3.9. If LangGraph is not
        # installed, or an API mismatch appears, the old agent still works.
        if os.getenv("METHODPAL_AGENT_STRICT_LANGGRAPH", "0").strip() in {"1", "true", "TRUE"}:
            raise
        if entry == "autopilot":
            # Autonomous analysis must never change the research plan silently.
            # The local graph executor already works without LangGraph; reaching
            # this branch means the new workflow itself failed unexpectedly.
            return AgentResponse(
                ok=False,
                source="autonomous_agent_error",
                action="autopilot",
                mode="autopilot",
                status="failed",
                thread_id=req.thread_id or req.options.get("thread_id"),
                message="自动分析工作流执行失败：{}。没有改用旧流程或其他模型。".format(exc),
                workflow_state={
                    "stage": "execution_failed",
                    "stage_label": "自动分析：工作流错误",
                    "status": "failed",
                    "mode": "autopilot",
                    "project_id": req.context.project_id,
                    "dataset_id": req.context.dataset_id,
                },
            )
        resp = fallback(req)
        try:
            resp.source = "legacy_agent_fallback"
            if isinstance(resp.workflow_state, dict):
                resp.workflow_state["agent_backend"] = "legacy_fallback"
                resp.workflow_state["langgraph_error"] = str(exc)
        except Exception:
            pass
        return resp


def recommend_variable_setup(req: AgentRequest) -> AgentResponse:
    req.action = "recommend_variables"
    return _call_graph("recommend_variables", req, legacy.recommend_variable_setup)


def recommend_method_setup(req: AgentRequest) -> AgentResponse:
    req.action = "recommend_methods"
    return _call_graph("recommend_methods", req, legacy.recommend_method_setup)


def generate_plan(req: AgentRequest) -> AgentResponse:
    req.action = "plan"
    return _call_graph("plan", req, legacy.generate_plan)


def run_autopilot(req: AgentRequest) -> AgentResponse:
    req.action = "autopilot"
    req.mode = "autopilot"
    return _call_graph("autopilot", req, legacy.run_autopilot)


def execute_agent_action(req: AgentRequest) -> AgentResponse:
    req.action = "execute"
    return _call_graph("execute", req, legacy.execute_agent_action)


def handle_agent_request(req: AgentRequest) -> AgentResponse:
    # Keep the public behavior identical to v0.6.5 while routing through the
    # graph runtime whenever available.
    action = (req.action or "plan").strip().lower()
    mode = (req.mode or req.options.get("mode") or "assisted").strip().lower()

    if action == "chat":
        from app.services.conversation_agent import handle_conversation
        return _call_graph("chat", req, handle_conversation)

    if action in {"autopilot", "auto", "automatic", "resume"} or mode in {"autopilot", "auto", "automatic"}:
        return run_autopilot(req)
    if action in {"recommend_variables", "variables"}:
        return recommend_variable_setup(req)
    if action in {"recommend_methods", "methods"}:
        return recommend_method_setup(req)
    if action in {"execute", "run", "run_analysis"}:
        return execute_agent_action(req)
    return generate_plan(req)
