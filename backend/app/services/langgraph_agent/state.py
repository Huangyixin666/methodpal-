from typing import Any, Dict, List, Optional, TypedDict

from app.schemas.agent import AgentRequest, AgentResponse


class MethodPalGraphState(TypedDict, total=False):
    """Shared state passed through the MethodPal LangGraph workflow.

    Workspace fields are explicit so graph nodes and the legacy compatibility
    layer evaluate the same model-first state instead of reconstructing it from
    page order.
    """

    request: AgentRequest
    entry: str
    route: str
    response: Optional[AgentResponse]
    runtime: str
    trace: List[Dict[str, Any]]
    meta: Dict[str, Any]
    project_id: Optional[str]
    dataset_id: Optional[str]
    dataset_version: int
    data_review_status: str
    selected_model: Optional[str]
    model_options: Dict[str, Any]
    selected_variables: Dict[str, Any]
    analysis_id: Optional[str]
    stage: str
    autonomy_route: str
    autonomy: Dict[str, Any]
    status: str
    thread_id: Optional[str]
    conversation: Dict[str, Any]
    help_documents: List[Dict[str, Any]]
