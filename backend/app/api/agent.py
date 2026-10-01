from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.agent import (
    AgentChatHistoryResponse,
    AgentChatHistoryUpdateRequest,
    AgentChatMessage,
    AgentRequest,
    AgentResponse,
)
from app.services.research_agent import (
    execute_agent_action,
    generate_plan,
    handle_agent_request,
    recommend_method_setup,
    recommend_variable_setup,
    run_autopilot,
)
from app.services.store import AGENT_CHATS, AGENT_STATES


router = APIRouter()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _history_key(project_id: str) -> str:
    return str(project_id or "no_project")


def _normalize_messages(messages: List[AgentChatMessage]) -> List[dict]:
    # Keep the last 120 messages to avoid huge JSON files during MVP stage.
    cleaned = []
    for msg in messages[-120:]:
        role = msg.role if msg.role in {"user", "ai", "assistant", "system"} else "ai"
        if role == "assistant":
            role = "ai"
        cleaned.append(
            {
                "role": role,
                "content": msg.content or "",
                "time": msg.time,
                "actions": msg.actions or [],
                "meta": msg.meta or {},
            }
        )
    return cleaned


@router.post("/message", response_model=AgentResponse)
def agent_message(req: AgentRequest) -> AgentResponse:
    return handle_agent_request(req)


@router.post("/plan", response_model=AgentResponse)
def agent_plan(req: AgentRequest) -> AgentResponse:
    req.action = "plan"
    return generate_plan(req)


@router.post("/recommend-variables", response_model=AgentResponse)
def agent_recommend_variables(req: AgentRequest) -> AgentResponse:
    req.action = "recommend_variables"
    return recommend_variable_setup(req)


@router.post("/recommend-methods", response_model=AgentResponse)
def agent_recommend_methods(req: AgentRequest) -> AgentResponse:
    req.action = "recommend_methods"
    return recommend_method_setup(req)


@router.post("/execute", response_model=AgentResponse)
def agent_execute(req: AgentRequest) -> AgentResponse:
    req.action = "execute"
    return execute_agent_action(req)


@router.post("/resume", response_model=AgentResponse)
def agent_resume(req: AgentRequest) -> AgentResponse:
    """Resume a paused autonomous analysis with structured user answers."""
    req.action = "resume"
    req.mode = "autopilot"
    return run_autopilot(req)


@router.get("/history/{project_id}", response_model=AgentChatHistoryResponse)
def get_agent_history(project_id: str) -> AgentChatHistoryResponse:
    key = _history_key(project_id)
    stored = AGENT_CHATS.get(key)
    if not stored:
        return AgentChatHistoryResponse(project_id=key, messages=[], updated_at=None)
    return AgentChatHistoryResponse(
        project_id=key,
        messages=stored.get("messages", []),
        updated_at=stored.get("updated_at"),
    )


@router.put("/history/{project_id}", response_model=AgentChatHistoryResponse)
def save_agent_history(project_id: str, req: AgentChatHistoryUpdateRequest) -> AgentChatHistoryResponse:
    key = _history_key(project_id)
    if req.project_id and req.project_id != key:
        raise HTTPException(status_code=400, detail="project_id in path and body do not match")

    stored = {
        "project_id": key,
        "messages": _normalize_messages(req.messages),
        "updated_at": _now(),
    }
    AGENT_CHATS[key] = stored
    return AgentChatHistoryResponse(**stored)


@router.delete("/history/{project_id}", response_model=AgentChatHistoryResponse)
def clear_agent_history(project_id: str) -> AgentChatHistoryResponse:
    key = _history_key(project_id)
    try:
        AGENT_CHATS.pop(key, None)
    except Exception:
        pass
    return AgentChatHistoryResponse(project_id=key, messages=[], updated_at=_now())

@router.get("/state/{project_id}")
def get_agent_state(project_id: str) -> dict:
    key = _history_key(project_id)
    stored = AGENT_STATES.get(key)
    if not stored:
        return {
            "project_id": key,
            "stage": "no_dataset",
            "stage_label": "等待上传数据",
            "mode": "assisted",
            "allowed_actions": ["go_upload"],
            "next_step": "upload_dataset",
        }
    return stored


@router.put("/state/{project_id}")
def save_agent_state(project_id: str, payload: dict) -> dict:
    key = _history_key(project_id)
    data = dict(payload or {})
    data["project_id"] = key
    AGENT_STATES[key] = data
    return data


@router.delete("/state/{project_id}")
def clear_agent_state(project_id: str) -> dict:
    key = _history_key(project_id)
    try:
        AGENT_STATES.pop(key, None)
    except Exception:
        pass
    return {"project_id": key, "stage": "no_dataset", "cleared": True}
