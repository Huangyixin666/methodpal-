from fastapi import APIRouter

from app.schemas.ai import ChatRequest, ChatResponse
from app.services.agent_orchestrator import chat_reply


router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    reply, source = chat_reply(
        message=req.message,
        page=req.page,
        context=req.context,
    )
    return ChatResponse(reply=reply, source=source)