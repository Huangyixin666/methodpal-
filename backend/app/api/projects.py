import uuid
from datetime import datetime
from typing import List

from fastapi import APIRouter, HTTPException

from app.schemas.project import ProjectCreateRequest, ProjectResponse, ProjectWorkspaceUpdateRequest
from app.services.store import (
    AGENT_CHATS,
    AGENT_STATES,
    ANALYSES,
    CHARTS,
    DATASETS,
    PROJECTS,
    REPORTS,
    VARIABLE_CONFIGS,
    user_storage_dir,
)
from app.services.user_context import get_current_user_id


router = APIRouter()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _to_response(project: dict) -> ProjectResponse:
    return ProjectResponse(**project)


def _model_configuration_ready(project: dict) -> bool:
    model_id = str(project.get("selected_model") or "")
    dataset_id = str(project.get("dataset_id") or "")
    if not model_id or not dataset_id:
        return False
    try:
        from app.services.method_recommender import recommend_methods

        payload = dict(project.get("selected_variables") or {})
        payload.update({
            "dataset_id": dataset_id,
            "selected_model": model_id,
            "research_goal": project.get("research_goal") or "",
        })
        response = recommend_methods(payload)
        item = next((m for m in response.recommended if m.method_id == model_id), None)
        return bool(item and item.enabled)
    except Exception:
        return False


@router.post("", response_model=ProjectResponse)
def create_project(req: ProjectCreateRequest) -> ProjectResponse:
    project_id = "prj_{}".format(uuid.uuid4().hex[:8])
    now = _now()
    project = ProjectResponse(
        project_id=project_id,
        name=req.name,
        description=req.description,
        research_goal=req.research_goal,
        progress=0,
        dataset_id=None,
        analysis_id=None,
        selected_variables=None,
        selected_model=None,
        model_options={},
        data_review_status="pending",
        dataset_version=0,
        created_at=now,
        updated_at=now,
    )
    PROJECTS[project_id] = project.model_dump()
    return project


@router.get("", response_model=List[ProjectResponse])
def list_projects() -> List[ProjectResponse]:
    projects = [_to_response(p) for p in PROJECTS.values()]
    projects.sort(key=lambda p: p.updated_at or p.created_at or "", reverse=True)
    return projects


@router.get("/__user_storage")
def get_user_storage() -> dict:
    from app.services.storage_limits import usage
    return {
        **usage(),
        "user_id": get_current_user_id(),
        "storage_dir": "服务端私有存储",
    }




def _record_matches(value: dict, project_id: str, dataset_ids: set, analysis_ids: set) -> bool:
    if not isinstance(value, dict):
        return False
    if str(value.get("project_id") or "") == project_id:
        return True
    did = str(value.get("dataset_id") or value.get("data_id") or "")
    aid = str(value.get("analysis_id") or value.get("analysisId") or "")
    if did and did in dataset_ids:
        return True
    if aid and aid in analysis_ids:
        return True
    meta = value.get("meta") if isinstance(value.get("meta"), dict) else {}
    if str(meta.get("project_id") or "") == project_id:
        return True
    if str(meta.get("dataset_id") or "") in dataset_ids:
        return True
    if str(meta.get("analysis_id") or "") in analysis_ids:
        return True
    return False


def _cleanup_store(store, project_id: str, dataset_ids: set, analysis_ids: set) -> int:
    deleted = 0
    try:
        items = list(store.items())
    except Exception:
        return 0
    for key, value in items:
        key_s = str(key)
        if key_s == project_id or key_s in dataset_ids or key_s in analysis_ids or _record_matches(value, project_id, dataset_ids, analysis_ids):
            try:
                store.pop(key_s, None)
                deleted += 1
            except Exception:
                pass
    return deleted


@router.delete("/{project_id}")
def delete_project(project_id: str) -> dict:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    dataset_ids = set()
    analysis_ids = set()
    if project.get("dataset_id"):
        dataset_ids.add(str(project.get("dataset_id")))
    if project.get("analysis_id"):
        analysis_ids.add(str(project.get("analysis_id")))

    # 收集同项目下可能存在的更多 analysis/dataset 引用，避免只删当前 active id。
    for aid, analysis in list(ANALYSES.items()):
        if isinstance(analysis, dict) and _record_matches(analysis, project_id, dataset_ids, analysis_ids):
            analysis_ids.add(str(aid))
            if analysis.get("dataset_id"):
                dataset_ids.add(str(analysis.get("dataset_id")))
    for did, dataset in list(DATASETS.items()):
        if isinstance(dataset, dict) and _record_matches(dataset, project_id, dataset_ids, analysis_ids):
            dataset_ids.add(str(did))

    deleted = {
        "projects": 0,
        "datasets": _cleanup_store(DATASETS, project_id, dataset_ids, analysis_ids),
        "analyses": _cleanup_store(ANALYSES, project_id, dataset_ids, analysis_ids),
        "reports": _cleanup_store(REPORTS, project_id, dataset_ids, analysis_ids),
        "charts": _cleanup_store(CHARTS, project_id, dataset_ids, analysis_ids),
        "variable_configs": _cleanup_store(VARIABLE_CONFIGS, project_id, dataset_ids, analysis_ids),
        "agent_chats": 0,
        "agent_states": 0,
    }

    try:
        PROJECTS.pop(project_id, None)
        deleted["projects"] = 1
    except Exception:
        deleted["projects"] = 0

    # Agent chat/state files usually use project_id as key.
    try:
        AGENT_CHATS.pop(project_id, None)
        deleted["agent_chats"] = 1
    except Exception:
        pass
    try:
        AGENT_STATES.pop(project_id, None)
        deleted["agent_states"] = 1
    except Exception:
        pass

    return {
        "ok": True,
        "project_id": project_id,
        "deleted": deleted,
        "dataset_ids": sorted(dataset_ids),
        "analysis_ids": sorted(analysis_ids),
    }

@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str) -> ProjectResponse:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")
    return _to_response(project)


@router.patch("/{project_id}/workspace", response_model=ProjectResponse)
def update_project_workspace(project_id: str, req: ProjectWorkspaceUpdateRequest) -> ProjectResponse:
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="project not found")

    payload = req.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key == "progress" and value is not None:
            value = max(0, min(100, int(value)))
        project[key] = value

    # Light automatic progress for the model-first workflow. Frontend may
    # override by passing progress. Clearing a downstream artifact must not keep
    # the old progress merely because it was once higher.
    if "progress" not in payload:
        has_dataset = bool(project.get("dataset_id"))
        has_model = bool(project.get("selected_model"))
        model_ready = _model_configuration_ready(project) if has_model else False
        if project.get("analysis_id"):
            project["progress"] = 85
        elif has_model and model_ready:
            project["progress"] = 70
        elif has_model:
            project["progress"] = 55
        elif has_dataset:
            project["progress"] = 35
        else:
            project["progress"] = 15

    project["updated_at"] = _now()
    PROJECTS[project_id] = project
    return _to_response(project)
