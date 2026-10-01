from typing import Any, Dict

from app.schemas.analysis import AnalysisRunRequest
from app.services.analysis_runner import run_analysis


def run_selected_analysis(
    dataset_id: str,
    model_id: str,
    selected_variables: Dict[str, Any],
    options: Dict[str, Any] = None,
) -> Dict[str, Any]:
    selection = selected_variables or {}
    req = AnalysisRunRequest(
        dataset_id=dataset_id,
        model=model_id,
        dependent_variable=selection.get("dependent_variable"),
        independent_variables=selection.get("independent_variables") or [],
        control_variables=selection.get("control_variables") or [],
        mediator_variables=selection.get("mediator_variables") or [],
        moderator_variables=selection.get("moderator_variables") or [],
        group_variable=selection.get("group_variable"),
        variable_types=selection.get("variable_types") or {},
        options=options or {},
    )
    result = run_analysis(req)
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return result.dict()
