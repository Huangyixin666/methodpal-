from fastapi import APIRouter
from fastapi.responses import Response
from app.services.artifact_export import render_chart
from app.services.artifact_history import history

from app.schemas.chart import ChartGenerateRequest, ChartGenerateResponse, ChartRecommendRequest, ChartRecommendResponse, ChartSpec
from app.services.chart_tools.chart_runner import generate_chart, get_chart, recommend_charts


router = APIRouter()

@router.get('')
def list_charts(project_id: str, limit: int = 50):
    return history('charts', project_id, limit)

@router.get('/{chart_id}/download')
def download(chart_id: str, format: str = 'png'):
    chart = get_chart(chart_id).model_dump()
    return Response(render_chart(chart, format), media_type='image/png' if format == 'png' else 'image/svg+xml',
        headers={'Content-Disposition': f'attachment; filename="{chart_id}.{format}"'})


@router.post("/recommend", response_model=ChartRecommendResponse)
def recommend(req: ChartRecommendRequest) -> ChartRecommendResponse:
    recs, warnings = recommend_charts(req.analysis_id, req.max_charts, dataset_id=req.dataset_id, context=req.context or {})
    return ChartRecommendResponse(ok=True, analysis_id=req.analysis_id, dataset_id=req.dataset_id, recommendations=recs, warnings=warnings)


@router.post("/generate", response_model=ChartGenerateResponse)
def generate(req: ChartGenerateRequest) -> ChartGenerateResponse:
    options = dict(req.options or {})
    if req.dataset_id:
        options["dataset_id"] = req.dataset_id
    chart = generate_chart(req.analysis_id, req.chart_type, options)
    return ChartGenerateResponse(ok=True, chart=chart, warnings=[])


@router.get("/{chart_id}", response_model=ChartSpec)
def get(chart_id: str) -> ChartSpec:
    return get_chart(chart_id)
