"""Account-scoped history; SQLite queries avoid loading every user's data."""
import json
from app.services.database import connect
from app.services.user_context import get_current_user_id
from app.services.store import PROJECTS, DATASETS, ANALYSES
from app.services.artifact_versions import analysis_is_current, chart_is_current, dataset_snapshot
from fastapi import HTTPException

def history(namespace, project_id, limit=50):
    project = PROJECTS.get(project_id)
    if not project:
        raise HTTPException(404, '项目不存在')
    did = project.get('dataset_id')
    dataset = DATASETS.get(did) or {}
    with connect() as db:
        rows = db.execute('SELECT value FROM records WHERE owner_id=? AND namespace=? '
            'AND (json_extract(value,\'$.project_id\')=? OR '
            '(json_extract(value,\'$.project_id\') IS NULL AND '
            '(json_extract(value,\'$.dataset_id\')=? OR json_extract(value,\'$.meta.dataset_id\')=? '
            'OR json_extract(value,\'$.analysis_id\')=?))) ORDER BY rowid DESC LIMIT ?',
            (get_current_user_id(), namespace, project_id, did, did, 'dataset:' + str(did), min(max(limit, 1), 100))).fetchall()
    items = [json.loads(r[0]) for r in rows]
    for item in items:
        if namespace == 'analyses':
            current = analysis_is_current(item, dataset, project.get('selected_model'),
                project.get('selected_variables') or {}, project.get('model_options') or {})
        elif namespace == 'charts':
            current = chart_is_current(item, project.get('analysis_id'), dataset)
        else:
            snap = item.get('source_snapshot') or {}
            current = (snap.get('data_fingerprint') == dataset_snapshot(dataset)['data_fingerprint']
                and item.get('analysis_id') == project.get('analysis_id')
                and analysis_is_current(ANALYSES.get(item.get('analysis_id')) or {}, dataset,
                    project.get('selected_model'), project.get('selected_variables') or {}, project.get('model_options') or {}))
        item['artifact_status'] = 'current' if current else 'stale'
    return {'items': items, 'project_id': project_id}
