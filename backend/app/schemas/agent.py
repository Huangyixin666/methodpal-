from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AgentContext(BaseModel):
    project_id: Optional[str] = None
    project_name: Optional[str] = None
    research_goal: Optional[str] = None
    analysis_goal: Optional[str] = None
    research_spec: Optional[Dict[str, Any]] = None
    dataset_id: Optional[str] = None
    analysis_id: Optional[str] = None
    page: str = "home"
    selected_variables: Optional[Dict[str, Any]] = None
    selected_model: Optional[str] = None
    model_options: Dict[str, Any] = Field(default_factory=dict)
    data_review_status: str = "pending"
    dataset_version: int = 0
    analysis_result: Optional[Dict[str, Any]] = None
    dataset_summary: Optional[Dict[str, Any]] = None
    variable_options: Optional[List[Dict[str, Any]]] = None


class AgentRequest(BaseModel):
    message: str = Field(default="", max_length=6000)
    action: str = "plan"
    mode: str = "assisted"
    thread_id: Optional[str] = None
    answers: Dict[str, Any] = Field(default_factory=dict)
    context: AgentContext = Field(default_factory=AgentContext)
    options: Dict[str, Any] = Field(default_factory=dict)


class AgentAction(BaseModel):
    id: str
    label: str
    type: str = "suggestion"
    payload: Dict[str, Any] = Field(default_factory=dict)
    requires_confirmation: bool = True
    disabled: bool = False
    disabled_reason: Optional[str] = None


class AgentVariableSuggestion(BaseModel):
    variable: str
    current_type: Optional[str] = None
    suggested_type: Optional[str] = None
    suggested_role: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""


class AgentMethodSuggestion(BaseModel):
    method_id: str
    name: str
    category: str = ""
    score: float = 0.0
    enabled: bool = True
    selectable: bool = True
    availability: str = "ready"
    missing_roles: List[str] = Field(default_factory=list)
    reason: str = ""
    outputs: List[str] = Field(default_factory=list)


class AgentPlanStep(BaseModel):
    step: int
    title: str
    detail: str
    tool: Optional[str] = None
    status: str = "pending"


class AgentQuestionOption(BaseModel):
    value: str
    label: str
    description: str = ""


class AgentQuestion(BaseModel):
    id: str
    question: str
    kind: str = "text"
    reason: str = ""
    required: bool = True
    options: List[AgentQuestionOption] = Field(default_factory=list)


class AgentWorkflowState(BaseModel):
    stage: str = "no_dataset"
    stage_label: str = "等待上传数据"
    mode: str = "assisted"
    project_id: Optional[str] = None
    dataset_id: Optional[str] = None
    analysis_id: Optional[str] = None
    allowed_actions: List[str] = Field(default_factory=list)
    next_step: str = ""
    selected_variables: Optional[Dict[str, Any]] = None
    selected_model: Optional[str] = None
    primary_method: Optional[str] = None
    method_availability: Optional[str] = None
    missing_roles: List[str] = Field(default_factory=list)
    data_review_status: str = "pending"
    dataset_version: int = 0
    auto_applied_variables: bool = False
    status: str = "idle"
    thread_id: Optional[str] = None
    questions: List[Dict[str, Any]] = Field(default_factory=list)
    research_spec: Dict[str, Any] = Field(default_factory=dict)


class AgentTraceEvent(BaseModel):
    step: int
    name: str
    status: str = "done"
    detail: str = ""


class AgentResponse(BaseModel):
    ok: bool = True
    source: str = "agent"
    action: str = "plan"
    mode: str = "assisted"
    message: str = ""
    llm_used: bool = False
    conversation_intent: Optional[str] = None
    knowledge_sources: List[Dict[str, str]] = Field(default_factory=list)
    dataset_summary: Dict[str, Any] = Field(default_factory=dict)
    variable_suggestions: List[AgentVariableSuggestion] = Field(default_factory=list)
    method_suggestions: List[AgentMethodSuggestion] = Field(default_factory=list)
    plan: List[AgentPlanStep] = Field(default_factory=list)
    actions: List[AgentAction] = Field(default_factory=list)
    execution_result: Optional[Dict[str, Any]] = None
    warnings: List[str] = Field(default_factory=list)
    workflow_state: Dict[str, Any] = Field(default_factory=dict)
    allowed_actions: List[str] = Field(default_factory=list)
    trace: List[AgentTraceEvent] = Field(default_factory=list)
    status: str = "completed"
    thread_id: Optional[str] = None
    questions: List[AgentQuestion] = Field(default_factory=list)
    research_spec: Dict[str, Any] = Field(default_factory=dict)
    analysis_plan: Dict[str, Any] = Field(default_factory=dict)


class AgentChatMessage(BaseModel):
    role: str
    content: str
    time: Optional[str] = None
    actions: List[Dict[str, Any]] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class AgentChatHistoryResponse(BaseModel):
    project_id: str
    messages: List[AgentChatMessage] = Field(default_factory=list)
    updated_at: Optional[str] = None


class AgentChatHistoryUpdateRequest(BaseModel):
    project_id: str
    messages: List[AgentChatMessage] = Field(default_factory=list)


class AgentActionExecuteRequest(BaseModel):
    action: AgentAction
    context: AgentContext = Field(default_factory=AgentContext)
    options: Dict[str, Any] = Field(default_factory=dict)
