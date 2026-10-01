from fastapi import APIRouter

from app.schemas.analysis import AnalysisResponse, AnalysisRunRequest
from app.services.analysis_runner import get_analysis, list_model_tools, run_analysis


router = APIRouter()

@router.get('')
def list_analyses(project_id: str, limit: int = 50):
    from app.services.artifact_history import history
    return history('analyses', project_id, limit)


@router.post("/run", response_model=AnalysisResponse)
def run(req: AnalysisRunRequest) -> AnalysisResponse:
    return run_analysis(req)


@router.get("/tools")
def tools() -> dict:
    return {"tools": list_model_tools()}


@router.get("/{analysis_id}", response_model=AnalysisResponse)
def get(analysis_id: str) -> AnalysisResponse:
    return get_analysis(analysis_id)
