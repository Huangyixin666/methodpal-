from __future__ import annotations

from fastapi import APIRouter

from app.schemas.method import MethodCatalogResponse, MethodRecommendRequest, MethodRecommendResponse
from app.services.method_recommender import method_catalog, recommend_methods

router = APIRouter()


@router.get("/catalog", response_model=MethodCatalogResponse)
def catalog() -> MethodCatalogResponse:
    return MethodCatalogResponse(methods=method_catalog())


@router.post("/recommend", response_model=MethodRecommendResponse)
def recommend(req: MethodRecommendRequest) -> MethodRecommendResponse:
    return recommend_methods(req.model_dump())
