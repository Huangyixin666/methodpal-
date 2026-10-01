from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class ProjectCreateRequest(BaseModel):
    name: str = "未命名研究项目"
    description: Optional[str] = None
    research_goal: Optional[str] = None


class ProjectWorkspaceUpdateRequest(BaseModel):
    research_goal: Optional[str] = None
    analysis_goal: Optional[str] = None
    research_spec: Optional[Dict[str, Any]] = None
    dataset_id: Optional[str] = None
    analysis_id: Optional[str] = None
    selected_variables: Optional[Dict[str, Any]] = None
    selected_model: Optional[str] = None
    model_options: Optional[Dict[str, Any]] = None
    data_review_status: Optional[str] = None
    dataset_version: Optional[int] = None
    ai_explanation: Optional[Dict[str, Any]] = None
    progress: Optional[int] = None


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    description: Optional[str] = None
    research_goal: Optional[str] = None
    analysis_goal: Optional[str] = None
    research_spec: Optional[Dict[str, Any]] = None
    progress: int = 0
    dataset_id: Optional[str] = None
    analysis_id: Optional[str] = None
    selected_variables: Optional[Dict[str, Any]] = None
    selected_model: Optional[str] = None
    model_options: Dict[str, Any] = Field(default_factory=dict)
    data_review_status: str = "pending"
    dataset_version: int = 0
    ai_explanation: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
