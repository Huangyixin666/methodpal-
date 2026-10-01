"""Per-account limits, including original uploads and database artifacts."""
import os
from pathlib import Path
from fastapi import HTTPException
from app.services.database import DATA_ROOT, connect
from app.services.user_context import get_current_user_id

def quota_bytes():
    return int(os.getenv('METHODPAL_ACCOUNT_QUOTA_BYTES', str(500 * 1024 * 1024)))

def raw_bytes(owner):
    root = DATA_ROOT / 'files' / owner
    return sum(path.stat().st_size for path in root.rglob('*') if path.is_file()) if root.is_dir() else 0

def usage():
    owner = get_current_user_id()
    with connect() as db:
        total = db.execute('SELECT COALESCE(SUM(length(CAST(value AS BLOB))),0) FROM records WHERE owner_id=?', (owner,)).fetchone()[0]
        counts = dict((r[0],r[1]) for r in db.execute('SELECT namespace,count(*) FROM records WHERE owner_id=? GROUP BY namespace',(owner,)))
    source = raw_bytes(owner)
    return {'used_bytes':total+source, 'database_bytes':total, 'file_bytes':source,
        'quota_bytes':quota_bytes(), 'project_count':counts.get('projects',0),'dataset_count':counts.get('datasets',0),
        'project_limit':int(os.getenv('METHODPAL_ACCOUNT_PROJECT_LIMIT','50')),
        'dataset_limit':int(os.getenv('METHODPAL_ACCOUNT_DATASET_LIMIT','100'))}

def ensure_upload_space(size):
    current = usage()
    if current['used_bytes']+size > current['quota_bytes']:
        raise HTTPException(413, '账号存储空间不足，请清理不用的数据或联系管理员')

def check_write(db, owner, namespace, key, encoded):
    previous = db.execute('SELECT length(CAST(value AS BLOB)) FROM records WHERE owner_id=? AND namespace=? AND id=?',(owner,namespace,key)).fetchone()
    if previous is None and namespace in ('projects','datasets'):
        limit = int(os.getenv('METHODPAL_ACCOUNT_PROJECT_LIMIT','50')) if namespace == 'projects' else int(os.getenv('METHODPAL_ACCOUNT_DATASET_LIMIT','100'))
        if db.execute('SELECT count(*) FROM records WHERE owner_id=? AND namespace=?',(owner,namespace)).fetchone()[0] >= limit:
            raise HTTPException(413, '账号项目或数据集数量已达上限，请清理不用的项目或联系管理员')
    total = db.execute('SELECT COALESCE(SUM(length(CAST(value AS BLOB))),0) FROM records WHERE owner_id=?',(owner,)).fetchone()[0]
    # Decreasing existing records must remain possible even for legacy over-quota accounts.
    addition = len(encoded.encode('utf-8'))-(previous[0] if previous else 0)
    if addition > 0 and total+addition+raw_bytes(owner) > quota_bytes():
        raise HTTPException(413, '账号存储空间不足，请清理不用的数据或联系管理员')
