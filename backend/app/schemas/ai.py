from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel


class ExplainRequest(BaseModel):
    analysis_id: Optional[str] = None
    style: Literal["academic", "plain", "concise"] = "academic"
    analysis_result: Optional[Dict[str, Any]] = None


class KeyFinding(BaseModel):
    label: str
    value: str
    color: str


class ExplainResponse(BaseModel):
    style: str
    text: str
    key_findings: List[KeyFinding]
    warnings: List[str] = []
    suggested_next_steps: List[str] = []


class ChatRequest(BaseModel):
    message: str
    page: str = "home"
    context: Optional[Dict[str, Any]] = None


class ChatResponse(BaseModel):
    reply: str
    source: Literal["backend", "llm", "fallback"] = "backend"