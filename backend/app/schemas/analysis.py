from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class AnalysisRunRequest(BaseModel):
    project_id: Optional[str] = None
    dataset_id: str
    # v0.5.1: model id is intentionally string-based because tools are registered dynamically.
    # This makes future agent tool-calling easier: the agent can call /api/analysis/run with any registered model id.
    model: str = "linear_regression"
    dependent_variable: Optional[str] = None
    independent_variables: List[str] = Field(default_factory=list)
    control_variables: List[str] = Field(default_factory=list)

    mediator_variables: List[str] = Field(default_factory=list)
    moderator_variables: List[str] = Field(default_factory=list)
    group_variable: Optional[str] = None

    variable_types: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)


class RegressionRow(BaseModel):
    variable: str
    coefficient: Optional[float] = None
    std_error: Optional[float] = None
    t_value: Optional[float] = None
    z_value: Optional[float] = None
    p_value: Optional[float] = None
    ci_lower: Optional[float] = None
    ci_upper: Optional[float] = None
    standardized_beta: Optional[float] = None
    odds_ratio: Optional[float] = None
    variable_type: Optional[str] = None
    original_variable: Optional[str] = None


class AnalysisResponse(BaseModel):
    project_id: Optional[str] = None
    source_snapshot: Dict[str, Any] = Field(default_factory=dict)
    artifact_status: str = "current"
    analysis_id: str
    dataset_id: str
    model: str
    model_name: str
    method: str
    dependent_variable: Optional[str] = None
    independent_variables: List[str] = Field(default_factory=list)
    control_variables: List[str] = Field(default_factory=list)
    mediator_variables: List[str] = Field(default_factory=list)
    moderator_variables: List[str] = Field(default_factory=list)
    group_variable: Optional[str] = None
    variable_types: Dict[str, str] = Field(default_factory=dict)
    sample_size: int = 0
    stats: Dict[str, Any] = Field(default_factory=dict)
    regression_table: List[RegressionRow] = Field(default_factory=list)
    descriptive_statistics: List[Dict[str, Any]] = Field(default_factory=list)
    correlation_matrix: List[Dict[str, Any]] = Field(default_factory=list)
    result_table: List[Dict[str, Any]] = Field(default_factory=list)
    diagnostics: Dict[str, Any] = Field(default_factory=dict)
    prediction_metrics: Dict[str, Any] = Field(default_factory=dict)
    model_comparison: List[Dict[str, Any]] = Field(default_factory=list)
    feature_importance: List[Dict[str, Any]] = Field(default_factory=list)
    prediction_table: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    summary: str = ""
