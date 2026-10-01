from typing import Any, Dict, Literal, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.services.background_jobs import submit, cancel, public_job
from app.services.store import JOBS
from app.services.database import connect
from app.services.user_context import get_current_user_id
import json

router = APIRouter()

class JobRequest(BaseModel):
    kind: Literal['analysis', 'agent', 'report']
    payload: Dict[str, Any]
    idempotency_key: Optional[str] = Field(default=None, max_length=160)

@router.post('')
def create(req: JobRequest):
    return submit(req.kind, req.payload, req.idempotency_key)

@router.get('')
def list_jobs(project_id: Optional[str] = None):
    with connect() as db:
        rows = db.execute("SELECT value FROM records WHERE owner_id=? AND namespace='jobs' AND (? IS NULL OR json_extract(value,'$.project_id')=?) ORDER BY rowid DESC LIMIT 50", (get_current_user_id(), project_id, project_id)).fetchall()
    workspace_cache = {}
    return {'items': [public_job(json.loads(r[0]), include_result=False, workspace_cache=workspace_cache) for r in rows]}

@router.get('/{job_id}')
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, '任务不存在')
    return public_job(job)

@router.post('/{job_id}/cancel')
def cancel_job(job_id: str):
    return cancel(job_id)

@router.post('/{job_id}/retry')
def retry_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, '任务不存在')
    if job['status'] not in ('failed', 'cancelled', 'interrupted'):
        raise HTTPException(409, '只能重试失败、取消或中断的任务')
    return submit(job['kind'], job['payload'])
