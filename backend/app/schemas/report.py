from __future__ import annotations

from typing import Any, Dict, List, Optional, Literal

from pydantic import BaseModel


class ReportGenerateRequest(BaseModel):
    project_id: Optional[str] = None
    analysis_id: Optional[str] = None
    dataset_id: Optional[str] = None
    title: Optional[str] = None
    format: Literal['markdown', 'docx', 'pdf'] = "markdown"
    style: str = "academic"
    include_charts: bool = True
    chart_ids: Optional[List[str]] = None
    charts: Optional[List[Dict[str, Any]]] = None
    context: Optional[Dict[str, Any]] = None
    # v0.7.4: "template" is deterministic; "ai" uses DeepSeek over a strict fact pack.
    report_mode: str = "template"


class ReportResponse(BaseModel):
    source_snapshot: Optional[Dict[str, Any]] = None
    dataset_id: Optional[str] = None
    created_at: Optional[str] = None
    report_id: str
    project_id: Optional[str] = None
    analysis_id: Optional[str] = None
    title: str
    format: str
    content: str
    sections: List[Dict[str, Any]]
    charts: Optional[List[Dict[str, Any]]] = None
    download_url: Optional[str] = None
    source: Optional[str] = None
    report_mode: Optional[str] = None
    warnings: Optional[List[str]] = None
    fact_pack: Optional[Dict[str, Any]] = None
