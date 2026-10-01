from contextvars import ContextVar
from datetime import datetime

job_id_context = ContextVar('methodpal_job_id', default=None)

def progress(label, percent=None):
    from app.services.store import JOBS
    job_id = job_id_context.get()
    if not job_id:
        return
    job = JOBS.get(job_id)
    if not job or job.get('status') != 'running':
        return
    job['stage'] = label
    if percent is not None:
        job['progress'] = percent
    job['updated_at'] = datetime.now().isoformat(timespec='seconds')
    JOBS[job_id] = job
