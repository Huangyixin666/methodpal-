from fastapi import APIRouter

from app.schemas.ai import ExplainRequest, ExplainResponse
from app.services.ai_explainer import explain_result


router = APIRouter()


@router.post("/explain", response_model=ExplainResponse)
def explain(req: ExplainRequest) -> ExplainResponse:
    return explain_result(
        style=req.style,
        analysis_id=req.analysis_id,
        analysis_result=req.analysis_result,
    )