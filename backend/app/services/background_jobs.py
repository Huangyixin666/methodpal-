"""Persistent FIFO with a bounded subprocess worker and real cancellation.

One API process owns this scheduler. On restart running work becomes interrupted
and is explicitly retried, never silently replayed after partial side effects.
"""
import json
import multiprocessing
import os
import threading
import time
from datetime import datetime
from uuid import uuid4
from fastapi import HTTPException
from app.services.database import connect
from app.services.store import JOBS, DATASETS, PROJECTS
from app.services.user_context import get_current_user_id, set_current_user_id, reset_current_user_id
from app.services.artifact_versions import dataset_snapshot, digest, analysis_config

STOP = threading.Event()
LOCK = threading.RLock()
RUNNING = {}
SCHEDULER = None

def now():
    return datetime.now().isoformat(timespec='seconds')

def workspace_fingerprint(project, kind):
    """Only research inputs are conflicts, never timestamps or output pointers.

    Reports additionally depend on which analysis is selected. Analysis and
    Agent tasks publish their own output pointers without invalidating themselves.
    """
    # Polls need the stored content version, not a Python copy of every cell.
    with connect() as db:
        row = db.execute("SELECT json_extract(value,'$.dataset_id'), json_extract(value,'$.dataset_revision'), json_extract(value,'$.data_fingerprint') FROM records WHERE owner_id=? AND namespace='datasets' AND id=?",
            (get_current_user_id(), str(project.get('dataset_id')))).fetchone()
    snapshot = {'dataset_id': row[0], 'dataset_revision': row[1] or 0, 'data_fingerprint': row[2]} if row and row[2] else dataset_snapshot(DATASETS.get(project.get('dataset_id')) or {})
    state = {'dataset': snapshot,
             'config': analysis_config(project.get('selected_model'), project.get('selected_variables') or {}, project.get('model_options'))}
    if kind == 'agent':
        state.update(research_goal=project.get('research_goal'), analysis_goal=project.get('analysis_goal'),
                     research_spec=project.get('research_spec') or {})
    if kind == 'report':
        state['analysis_id'] = project.get('analysis_id')
    return digest(state)

def job_workspace_matches(job, project):
    if not job.get('project_id'):
        return True
    if not project:
        return False
    expected = job.get('workspace_fingerprint')
    actual = workspace_fingerprint(project, job['kind']) if job.get('workspace_guard_version') == 2 else digest(project)
    return actual == expected

def publish_workspace_changes(project_id, changes):
    """Compare-and-publish atomically; retain concurrent edits and display metadata.

    A job's own planned changes advance its guard in the same transaction. Normal
    synchronous Agent calls have no job and simply merge onto the latest project.
    """
    from app.services.job_progress import job_id_context
    if not job_id_context.get():
        project = PROJECTS.get(project_id)
        if not project:
            return None
        project = {**project, **changes}
        PROJECTS[project_id] = project
        return project
    owner = get_current_user_id()
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT value FROM records WHERE owner_id=? AND namespace='projects' AND id=?", (owner, project_id)).fetchone()
        if not row:
            return None
        project = json.loads(row[0])
        job_id = job_id_context.get()
        job_row = db.execute("SELECT value FROM records WHERE owner_id=? AND namespace='jobs' AND id=?", (owner, job_id)).fetchone() if job_id else None
        job = json.loads(job_row[0]) if job_row else None
        if job_id and (not job or job.get('status') == 'cancelled' or not job_workspace_matches(job, project)):
            return None
        project.update(changes)
        encoded = json.dumps(project, ensure_ascii=False)
        from app.services.storage_limits import check_write
        check_write(db, owner, 'projects', project_id, encoded)
        db.execute("UPDATE records SET value=? WHERE owner_id=? AND namespace='projects' AND id=?", (encoded, owner, project_id))
        if job:
            job['workspace_fingerprint'] = workspace_fingerprint(project, job['kind']) if job.get('workspace_guard_version') == 2 else digest(project)
            db.execute("UPDATE records SET value=? WHERE owner_id=? AND namespace='jobs' AND id=?", (json.dumps(job, ensure_ascii=False), owner, job_id))
        return project

def public_job(job, include_result=True, workspace_cache=None):
    result = {k: v for k, v in job.items() if k not in ('payload', 'owner_id', 'idempotency_key', 'workspace_fingerprint')}
    if not include_result and isinstance(result.get('result'), dict):
        # TaskPanel uses status for navigation; full results belong to GET /{id}.
        result['result'] = {k: result['result'][k] for k in ('status','analysis_id','report_id','model') if k in result['result']}
    if job.get('status') == 'completed' and job.get('project_id'):
        # This cache is request-local: no cross-user reuse or stale global state.
        cache = workspace_cache if workspace_cache is not None else {}
        project_key = ('project', job['project_id'])
        if project_key not in cache:
            cache[project_key] = PROJECTS.get(job['project_id'])
        project = cache[project_key]
        guard_key = ('guard', job['project_id'], job['kind'], job.get('workspace_guard_version'))
        if guard_key not in cache:
            cache[guard_key] = (workspace_fingerprint(project, job['kind']) if job.get('workspace_guard_version') == 2 else digest(project)) if project else None
        result['workspace_changed'] = (job.get('workspace_changed', False) or
            not project or cache[guard_key] != job.get('workspace_fingerprint'))
    return result

def submit(kind, payload, idempotency_key=None):
    if kind not in ('analysis', 'agent', 'report'):
        raise HTTPException(422, '不支持的任务类型')
    project_id = payload.get('project_id') or (payload.get('context') or {}).get('project_id')
    did = payload.get('dataset_id') or (payload.get('context') or {}).get('dataset_id')
    owner = get_current_user_id()
    with LOCK:
        with connect() as db:
            rows = db.execute("SELECT value FROM records WHERE owner_id=? AND namespace='jobs' ORDER BY rowid DESC LIMIT 100", (owner,)).fetchall()
        old = [json.loads(row[0]) for row in rows]
        with connect() as db:
            pending = db.execute("SELECT count(*) FROM records WHERE namespace='jobs' AND json_extract(value,'$.status') IN ('queued','running')").fetchone()[0]
        for job in old:
            if idempotency_key and job.get('idempotency_key') == idempotency_key:
                if job['kind'] != kind or digest(job['payload']) != digest(payload):
                    raise HTTPException(409, '同一任务标识不能用于不同请求')
                return public_job(job)
        if pending >= 32:
            raise HTTPException(429, '服务器任务队列已满，请稍后重试。')
        if sum(j['status'] in ('queued', 'running') for j in old) >= 3:
            raise HTTPException(429, '当前账号已有 3 个待执行任务，请等待或取消后再提交。')
        job = {'job_id': 'job_' + uuid4().hex, 'owner_id': owner, 'kind': kind,
            'project_id': project_id, 'dataset_id': did, 'payload': payload, 'idempotency_key': idempotency_key,
            'expected_data_fingerprint': dataset_snapshot(DATASETS.get(did) or {}).get('data_fingerprint') if did else None,
            'workspace_fingerprint': workspace_fingerprint(PROJECTS.get(project_id) or {}, kind) if project_id else None,
            'workspace_guard_version': 2,
            'status': 'queued', 'stage': '等待执行', 'progress': 0, 'created_at': now(), 'updated_at': now()}
        JOBS[job['job_id']] = job
        return public_job(job)

def execute_worker(owner, job_id):
    set_current_user_id(owner)
    from app.services.job_progress import job_id_context, progress
    job_id_context.set(job_id)
    job = JOBS[job_id]
    try:
        if job.get('dataset_id') and dataset_snapshot(DATASETS.get(job['dataset_id']) or {})['data_fingerprint'] != job['expected_data_fingerprint']:
            raise HTTPException(409, '排队期间数据已经变化，请按最新数据重新提交。')
        progress('正在检查并执行', 10)
        payload = job['payload']
        if job['kind'] == 'analysis':
            from app.schemas.analysis import AnalysisRunRequest
            from app.services.analysis_runner import run_analysis
            result = run_analysis(AnalysisRunRequest(**payload))
            if job.get('project_id') and workspace_unchanged() and result.artifact_status == 'current':
                publish_workspace_changes(job['project_id'], {'analysis_id': result.analysis_id, 'ai_explanation': None, 'progress': 85})
        elif job['kind'] == 'agent':
            from app.schemas.agent import AgentRequest
            from app.services.research_agent import handle_agent_request
            result = handle_agent_request(AgentRequest(**payload))
        else:
            from app.schemas.report import ReportGenerateRequest
            from app.api.reports import generate
            result = generate(ReportGenerateRequest(**payload))
        job = JOBS[job_id]
        job.update(status='completed', stage='执行完成', progress=100,
                   workspace_changed=not workspace_unchanged(),
                   result=result.model_dump() if hasattr(result, 'model_dump') else result, updated_at=now())
    except Exception as exc:
        job = JOBS[job_id]
        detail = getattr(exc, 'detail', None)
        # Do not return tracebacks, environment values or arbitrary provider errors.
        job.update(status='failed', stage='执行失败', error=str(detail) if detail else '任务执行失败，请检查配置后重试。', updated_at=now())
    JOBS[job_id] = job

def workspace_unchanged():
    from app.services.job_progress import job_id_context
    job = JOBS.get(job_id_context.get()) if job_id_context.get() else None
    return not job or job_workspace_matches(job, PROJECTS.get(job.get('project_id')) if job.get('project_id') else None)

def cancel(job_id):
    with LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(404, '任务不存在')
        if job['status'] in ('queued', 'running'):
            process = RUNNING.get(job_id)
            if process:
                process.terminate()
                process.join(timeout=5)
            job.update(status='cancelled', stage='已取消', updated_at=now())
            JOBS[job_id] = job
        return public_job(job)

def scheduler_loop():
    while not STOP.wait(.4):
        with LOCK:
            for job_id, process in list(RUNNING.items()):
                if not process.is_alive():
                    process.join(timeout=0)
                    RUNNING.pop(job_id)
                    with connect() as db:
                        row = db.execute("SELECT owner_id,value FROM records WHERE namespace='jobs' AND id=?", (job_id,)).fetchone()
                    if row:
                        job = json.loads(row['value'])
                        if job['status'] == 'running':
                            token = set_current_user_id(row['owner_id'])
                            try:
                                job.update(status='failed', stage='工作进程异常退出', error='请降低任务规模或重试。', updated_at=now())
                                JOBS[job_id] = job
                            finally:
                                reset_current_user_id(token)
            if RUNNING:
                continue
            with connect() as db:
                row = db.execute("SELECT owner_id,id,value FROM records WHERE namespace='jobs' AND json_extract(value,'$.status')='queued' ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                continue
            token = set_current_user_id(row['owner_id'])
            try:
                job = JOBS[row['id']]
                job.update(status='running', stage='开始执行', progress=5, updated_at=now())
                JOBS[row['id']] = job
                process = multiprocessing.get_context('spawn').Process(target=execute_worker, args=(row['owner_id'], row['id']), daemon=True)
                process.start()
                RUNNING[row['id']] = process
            finally:
                reset_current_user_id(token)

def start():
    global SCHEDULER
    if os.getenv('METHODPAL_JOB_WORKER', '1') == '0':
        return
    if SCHEDULER and SCHEDULER.is_alive():
        return
    with connect() as db:
        rows = db.execute("SELECT owner_id,id,value FROM records WHERE namespace='jobs' AND json_extract(value,'$.status')='running'").fetchall()
        for row in rows:
            job = json.loads(row['value'])
            job.update(status='interrupted', stage='服务重启，任务已中断', error='请点击重试；已生成的历史记录保留。', updated_at=now())
            db.execute('UPDATE records SET value=? WHERE owner_id=? AND namespace=? AND id=?', (json.dumps(job, ensure_ascii=False), row['owner_id'], 'jobs', row['id']))
    STOP.clear()
    SCHEDULER = threading.Thread(target=scheduler_loop, daemon=True)
    SCHEDULER.start()

def stop():
    global SCHEDULER
    STOP.set()
    if SCHEDULER:
        SCHEDULER.join(timeout=2)
    SCHEDULER = None
    with LOCK:
        for process in RUNNING.values():
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
        RUNNING.clear()
