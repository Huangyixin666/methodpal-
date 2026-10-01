from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.report import ReportGenerateRequest, ReportResponse
from app.services.report_generator import generate_report
from app.services.store import REPORTS
from fastapi.responses import Response
from pydantic import BaseModel, Field
from app.services.artifact_export import report_export
from app.services.artifact_history import history

router = APIRouter()
alias_router = APIRouter()

@router.get('')
def list_reports(project_id: str, limit: int = 50):
    return history('reports', project_id, limit)

class DraftUpdate(BaseModel):
    content: str = Field(max_length=200000)

@router.put('/{report_id}/draft')
def save_draft(report_id: str, req: DraftUpdate):
    report = REPORTS.get(report_id)
    if not report:
        raise HTTPException(404, '报告不存在')
    report['draft_content'] = req.content
    REPORTS[report_id] = report
    return {'ok': True}

@router.get('/{report_id}/download')
def download(report_id: str, format: str = 'markdown'):
    report = REPORTS.get(report_id)
    if not report:
        raise HTTPException(404, '报告不存在')
    content, mime = report_export(report, format)
    extension = 'md' if format == 'markdown' else format
    return Response(content, media_type=mime, headers={'Content-Disposition': f'attachment; filename="{report_id}.{extension}"'})


@router.post("/generate", response_model=ReportResponse)
def generate(req: ReportGenerateRequest) -> ReportResponse:
    return generate_report(
        project_id=req.project_id,
        analysis_id=req.analysis_id,
        dataset_id=req.dataset_id,
        fmt=req.format,
        style=req.style,
        chart_ids=req.chart_ids,
        charts=req.charts,
        include_charts=req.include_charts,
        title=req.title,
        context=req.context,
        report_mode=req.report_mode,
    )


@router.get("/{report_id}")
def get_report(report_id: str) -> dict:
    report = REPORTS.get(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    return report


# Compatibility aliases: /api/report/generate and /api/report/{report_id}
@alias_router.post("/generate", response_model=ReportResponse)
def generate_alias(req: ReportGenerateRequest) -> ReportResponse:
    return generate(req)


@alias_router.get("/{report_id}")
def get_report_alias(report_id: str) -> dict:
    return get_report(report_id)
