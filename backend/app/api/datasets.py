from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile

from app.schemas.dataset import (
    CleanApplyRequest, DatasetCellPatchRequest, DatasetColumnDeleteRequest, DatasetColumnInsertRequest,
    DatasetHeaderReparseRequest, DatasetResponse, DatasetRowDeleteRequest, DatasetRowInsertRequest,
    DatasetRowsResponse, PasteDatasetRequest,
)
from app.schemas.transformation import DataTransformApplyResponse, DataTransformPreview, DataTransformRequest
from app.services.data_loader import load_upload_file, reparse_uploaded_dataset
from app.services.data_profiler import (
    apply_suggestions,
    build_clean_suggestions,
    dataset_dict_to_response,
    dataset_response,
    dataset_rows_page,
    patch_dataset_cells,
    get_dataset,
    insert_dataset_rows,
    delete_dataset_rows,
    insert_dataset_column,
    delete_dataset_columns,
    update_dataset_from_client,
)
from app.services.sample_data import SAMPLE_HEADERS, SAMPLE_ROWS
from app.services.store import (
    AGENT_STATES,
    owned_file_path,
    ANALYSES,
    CHARTS,
    DATASETS,
    PROJECTS,
    REPORTS,
    VARIABLE_CONFIGS,
)
from app.services.data_transformer import apply_dataset_transform, preview_dataset_transform

router = APIRouter()


@router.post("/sample", response_model=DatasetResponse)
def sample_dataset() -> DatasetResponse:
    return dataset_response(SAMPLE_HEADERS, SAMPLE_ROWS, name="大学生心理健康示例数据")


@router.post("/paste", response_model=DatasetResponse)
def paste_dataset(req: PasteDatasetRequest) -> DatasetResponse:
    if not req.headers:
        raise HTTPException(status_code=400, detail="headers 不能为空")
    return dataset_response(req.headers, req.rows, name=req.name or "pasted_table")


@router.post("/upload", response_model=DatasetResponse)
async def upload_dataset(
    file: UploadFile = File(...),
    header_mode: str = Form("row"),
    header_row: int = Form(1),
) -> DatasetResponse:
    return await load_upload_file(file, header_mode=header_mode, header_row=header_row)


@router.post("/{dataset_id}/transform/preview", response_model=DataTransformPreview)
def transform_preview(dataset_id: str, req: DataTransformRequest) -> DataTransformPreview:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        data = preview_dataset_transform(dataset_id, req.operation, req.selected_variables)
        return DataTransformPreview(**data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/transform/apply", response_model=DataTransformApplyResponse)
def transform_apply(dataset_id: str, req: DataTransformRequest) -> DataTransformApplyResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        data = apply_dataset_transform(
            dataset_id,
            req.operation,
            selected_variables=req.selected_variables,
            create_new_dataset=req.create_new_dataset,
        )
        return DataTransformApplyResponse(**data)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset_detail(dataset_id: str) -> DatasetResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset_dict_to_response(dataset)


@router.get("/{dataset_id}/rows", response_model=DatasetRowsResponse)
def get_dataset_rows(
    dataset_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
) -> DatasetRowsResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return DatasetRowsResponse(**dataset_rows_page(dataset, offset=offset, limit=limit))


@router.patch("/{dataset_id}/cells", response_model=DatasetResponse)
def patch_cells(dataset_id: str, req: DatasetCellPatchRequest) -> DatasetResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return patch_dataset_cells(dataset_id, req.changes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/header/reparse", response_model=DatasetResponse)
def reparse_header(dataset_id: str, req: DatasetHeaderReparseRequest) -> DatasetResponse:
    if not get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return reparse_uploaded_dataset(dataset_id, req.header_mode, req.header_row)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/rows/insert", response_model=DatasetResponse)
def insert_rows(dataset_id: str, req: DatasetRowInsertRequest) -> DatasetResponse:
    if not get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return insert_dataset_rows(dataset_id, req.index, req.count)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/rows/delete", response_model=DatasetResponse)
def remove_rows(dataset_id: str, req: DatasetRowDeleteRequest) -> DatasetResponse:
    if not get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return delete_dataset_rows(dataset_id, req.indices)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/columns/insert", response_model=DatasetResponse)
def insert_column(dataset_id: str, req: DatasetColumnInsertRequest) -> DatasetResponse:
    if not get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return insert_dataset_column(dataset_id, req.name, req.index, req.default_value)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{dataset_id}/columns/delete", response_model=DatasetResponse)
def remove_columns(dataset_id: str, req: DatasetColumnDeleteRequest) -> DatasetResponse:
    if not get_dataset(dataset_id):
        raise HTTPException(status_code=404, detail="dataset not found")
    try:
        return delete_dataset_columns(dataset_id, req.indices)
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _record_references_dataset(value: object, dataset_id: str, analysis_ids: set[str], project_ids: set[str]) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("dataset_id") or value.get("data_id") or "") == dataset_id:
        return True
    if str(value.get("analysis_id") or value.get("analysisId") or "") in analysis_ids:
        return True
    if str(value.get("project_id") or "") in project_ids:
        return True
    meta = value.get("meta") if isinstance(value.get("meta"), dict) else {}
    if str(meta.get("dataset_id") or "") == dataset_id:
        return True
    if str(meta.get("analysis_id") or "") in analysis_ids:
        return True
    if str(meta.get("project_id") or "") in project_ids:
        return True
    return False


def _delete_matching_records(store, predicate) -> int:
    deleted = 0
    for key, value in list(store.items()):
        if not predicate(str(key), value):
            continue
        try:
            store.pop(str(key), None)
            deleted += 1
        except Exception:
            pass
    return deleted


@router.delete("/{dataset_id}")
def delete_dataset(dataset_id: str) -> dict:
    dataset = DATASETS.get(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")

    affected_project_ids: set[str] = set()
    analysis_ids: set[str] = set()

    # First detach the dataset from every project that still references it.
    # This also invalidates model/variable/result state so an Agent cannot keep
    # operating on a deleted dataset.
    for project_id, project_value in list(PROJECTS.items()):
        if not isinstance(project_value, dict):
            continue
        if str(project_value.get("dataset_id") or "") != dataset_id:
            continue
        affected_project_ids.add(str(project_id))
        if project_value.get("analysis_id"):
            analysis_ids.add(str(project_value.get("analysis_id")))
        project = dict(project_value)
        project.update(
            {
                "dataset_id": None,
                "analysis_id": None,
                "selected_variables": None,
                "selected_model": None,
                "model_options": {},
                "data_review_status": "pending",
                "dataset_version": int(project.get("dataset_version") or 0) + 1,
                "ai_explanation": None,
                "progress": 15,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        PROJECTS[str(project_id)] = project
        try:
            AGENT_STATES.pop(str(project_id), None)
        except Exception:
            pass

    # Collect analyses that directly reference the dataset before removing
    # downstream reports/charts that may only carry an analysis id.
    for analysis_id, analysis in list(ANALYSES.items()):
        if isinstance(analysis, dict) and str(analysis.get("dataset_id") or "") == dataset_id:
            analysis_ids.add(str(analysis_id))

    deleted = {
        "datasets": 0,
        "analyses": _delete_matching_records(
            ANALYSES,
            lambda key, value: key in analysis_ids
            or _record_references_dataset(value, dataset_id, analysis_ids, affected_project_ids),
        ),
        "reports": _delete_matching_records(
            REPORTS,
            lambda _key, value: _record_references_dataset(
                value, dataset_id, analysis_ids, affected_project_ids
            ),
        ),
        "charts": _delete_matching_records(
            CHARTS,
            lambda _key, value: _record_references_dataset(
                value, dataset_id, analysis_ids, affected_project_ids
            ),
        ),
        "variable_configs": _delete_matching_records(
            VARIABLE_CONFIGS,
            lambda _key, value: _record_references_dataset(
                value, dataset_id, analysis_ids, affected_project_ids
            ),
        ),
        "agent_states": len(affected_project_ids),
    }

    source = dataset.get("_source") if isinstance(dataset, dict) else None
    source_path_value = str((source or {}).get("path") or "")
    if source_path_value:
        try:
            source_path = owned_file_path(source_path_value)
            if source_path.exists():
                source_path.unlink()
        except Exception:
            pass

    try:
        DATASETS.pop(dataset_id, None)
        deleted["datasets"] = 1
    except Exception:
        deleted["datasets"] = 0

    return {
        "ok": True,
        "dataset_id": dataset_id,
        "affected_project_ids": sorted(affected_project_ids),
        "analysis_ids": sorted(analysis_ids),
        "deleted": deleted,
    }


@router.put("/{dataset_id}", response_model=DatasetResponse)
def update_dataset(dataset_id: str, req: PasteDatasetRequest) -> DatasetResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    if not req.headers:
        raise HTTPException(status_code=400, detail="headers 不能为空")
    return update_dataset_from_client(dataset_id, req.headers, req.rows, name=req.name or dataset.get("name") or "dataset")


@router.get("/{dataset_id}/profile")
def dataset_profile(dataset_id: str) -> dict:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return dataset.get("profile") or {}


@router.post("/{dataset_id}/clean/suggest")
def clean_suggest(dataset_id: str) -> dict:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    suggestions = build_clean_suggestions(dataset)
    return {"dataset_id": dataset_id, "suggestions": suggestions, "count": len(suggestions)}


@router.post("/{dataset_id}/clean/apply", response_model=DatasetResponse)
def clean_apply(dataset_id: str, req: Optional[CleanApplyRequest] = None) -> DatasetResponse:
    dataset = get_dataset(dataset_id)
    if not dataset:
        raise HTTPException(status_code=404, detail="dataset not found")
    return apply_suggestions(dataset)
