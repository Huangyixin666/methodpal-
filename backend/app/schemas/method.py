from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MethodRoleRequirement(BaseModel):
    role: str
    label: str
    min_count: int = 1
    max_count: Optional[int] = None
    allowed_types: List[str] = Field(default_factory=list)
    description: str = ""


class MethodRequirement(BaseModel):
    summary: str = ""
    required_roles: List[MethodRoleRequirement] = Field(default_factory=list)
    optional_roles: List[MethodRoleRequirement] = Field(default_factory=list)
    min_rows: int = 2


class MethodRecommendRequest(BaseModel):
    dataset_id: Optional[str] = None
    dependent_variable: Optional[str] = None
    independent_variables: List[str] = Field(default_factory=list)
    control_variables: List[str] = Field(default_factory=list)
    mediator_variables: List[str] = Field(default_factory=list)
    moderator_variables: List[str] = Field(default_factory=list)
    group_variable: Optional[str] = None
    variable_types: Dict[str, str] = Field(default_factory=dict)
    research_goal: Optional[str] = None
    selected_model: Optional[str] = None


class MethodItem(BaseModel):
    method_id: str
    id: str
    name: str
    category: str
    family: str = "basic"
    scene: str = ""
    score: float
    reason: str
    assumptions: List[str] = Field(default_factory=list)
    outputs: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    requirement: MethodRequirement = Field(default_factory=MethodRequirement)
    default_options: Dict[str, Any] = Field(default_factory=dict)
    implemented: bool = True
    # enabled means the current role configuration is executable now.
    enabled: bool = False
    # selectable means the user may choose the method before configuring roles.
    selectable: bool = True
    availability: str = "selectable"
    missing_roles: List[str] = Field(default_factory=list)
    validation_messages: List[str] = Field(default_factory=list)
    disabled_reason: Optional[str] = None


class MethodCatalogResponse(BaseModel):
    methods: List[MethodItem]


class MethodRecommendResponse(BaseModel):
    recommended: List[MethodItem]
    message: str
