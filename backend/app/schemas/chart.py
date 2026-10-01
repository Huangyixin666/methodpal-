from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChartRecommendRequest(BaseModel):
    analysis_id: Optional[str] = None
    dataset_id: Optional[str] = None
    max_charts: int = 8
    context: Dict[str, Any] = Field(default_factory=dict)


class ChartGenerateRequest(BaseModel):
    analysis_id: Optional[str] = None
    dataset_id: Optional[str] = None
    chart_type: str
    options: Dict[str, Any] = Field(default_factory=dict)


class ChartRecommendation(BaseModel):
    chart_type: str
    title: str
    description: str = ""
    reason: str = ""
    priority: int = 50
    available: bool = True
    disabled_reason: Optional[str] = None


class ChartSpec(BaseModel):
    chart_id: str
    analysis_id: str
    chart_type: str
    title: str
    subtitle: str = ""
    x_key: Optional[str] = None
    y_key: Optional[str] = None
    x_label: Optional[str] = None
    y_label: Optional[str] = None
    layout: Optional[str] = None
    data: List[Dict[str, Any]] = Field(default_factory=list)
    series: List[Dict[str, Any]] = Field(default_factory=list)
    matrix: List[List[Any]] = Field(default_factory=list)
    row_labels: List[str] = Field(default_factory=list)
    column_labels: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)


class ChartRecommendResponse(BaseModel):
    ok: bool = True
    analysis_id: Optional[str] = None
    dataset_id: Optional[str] = None
    recommendations: List[ChartRecommendation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class ChartGenerateResponse(BaseModel):
    ok: bool = True
    chart: ChartSpec
    warnings: List[str] = Field(default_factory=list)
