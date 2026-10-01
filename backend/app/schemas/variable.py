from __future__ import annotations

from pydantic import BaseModel, Field

class VariableInferRequest(BaseModel):
    dataset_id: str | None = None
    columns: list[str] | None = None
    research_goal: str | None = None

class VariableCandidate(BaseModel):
    name: str
    type: str = "unknown"
    reason: str

class VariableInferResponse(BaseModel):
    dependent_candidates: list[VariableCandidate]
    independent_candidates: list[VariableCandidate]
    control_candidates: list[VariableCandidate]
    message: str

class VariableSaveRequest(BaseModel):
    project_id: str | None = None
    dataset_id: str | None = None
    selected_model: str | None = None
    dependent_variable: str = ""
    independent_variables: list[str] = Field(default_factory=list)
    control_variables: list[str] = Field(default_factory=list)
    mediator_variables: list[str] = Field(default_factory=list)
    moderator_variables: list[str] = Field(default_factory=list)
    group_variable: str | None = None
    variable_types: dict[str, str] = Field(default_factory=dict)
    analysis_goal: str = ""
