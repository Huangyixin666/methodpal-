from __future__ import annotations

from fastapi import APIRouter

from app.schemas.variable import VariableInferRequest, VariableInferResponse, VariableSaveRequest
from app.services.variable_inferencer import infer_variables, save_variable_config

router = APIRouter()


@router.post("/infer", response_model=VariableInferResponse)
def infer(req: VariableInferRequest) -> VariableInferResponse:
    return infer_variables(req.dataset_id, req.columns, req.research_goal)


@router.post("/save")
def save(req: VariableSaveRequest) -> dict:
    return save_variable_config(req.model_dump())
