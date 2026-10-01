"""Read-only aggregate diagnostics; never prints accounts, fields, rows or secrets."""
import collections
import datetime
import json
import sqlite3
from pathlib import Path
import argparse

parser=argparse.ArgumentParser()
parser.add_argument('--database',default='/var/lib/methodpal/methodpal.sqlite3')
args=parser.parse_args()
db=sqlite3.connect(Path(args.database).resolve().as_uri()+'?mode=ro',uri=True)
rows=db.execute("SELECT value FROM records WHERE namespace='jobs' ORDER BY rowid DESC LIMIT 100").fetchall()
db.close()
jobs=[json.loads(row[0]) for row in rows]
print(json.dumps({'recent_job_count':len(jobs),'statuses':dict(collections.Counter(j.get('status') for j in jobs)),
    'workspace_changed_count':sum(bool(j.get('workspace_changed')) for j in jobs)},ensure_ascii=False))
for job in jobs[:20]:
    payload=job.get('payload') or {}
    result=job.get('result') or {}
    if isinstance(result,dict): result=result.get('execution_result') or result
    model=result.get('model') if isinstance(result,dict) else None
    model=model or payload.get('model') or (payload.get('context') or {}).get('selected_model')
    elapsed=None
    try: elapsed=round((datetime.datetime.fromisoformat(job['updated_at'])-datetime.datetime.fromisoformat(job['created_at'])).total_seconds(),2)
    except (KeyError,ValueError): pass
    print(json.dumps({'kind':job.get('kind'),'status':job.get('status'),'model':model,
        'elapsed_including_queue_s':elapsed,'workspace_changed':bool(job.get('workspace_changed')),
        'progress':job.get('progress'),'created_at':job.get('created_at')},ensure_ascii=False))
