"""Immutable provenance shared by analysis, charts and reports.

Dataset identity is not its content version. Never trust a client's version
counter as evidence that a stored result belongs to the current data.
"""
import hashlib
import json
from datetime import datetime

ALIASES = {'ols': 'linear_regression', 'xgb_regression': 'xgboost_regression',
           'xgb_classification': 'xgboost_classification', 'xgboost_multiclass': 'xgboost_classification',
           'random_forest_classifier': 'random_forest_classification', 'prediction_model_comparison': 'model_comparison',
           'ordinal_logistic': 'ordinal_logistic_regression', 'lasso': 'lasso_regression'}
ROLES = ('dependent_variable', 'independent_variables', 'control_variables',
         'mediator_variables', 'moderator_variables', 'group_variable', 'variable_types')

def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), default=str).encode('utf-8')).hexdigest()

def data_fingerprint(dataset):
    return digest([dataset.get('headers') or [], dataset.get('_full_rows', dataset.get('rows') or []),
        [(c.get('name'), c.get('type')) for c in (dataset.get('profile') or {}).get('columns', [])]])

def dataset_snapshot(dataset):
    return {'dataset_id': dataset.get('dataset_id'), 'dataset_revision': dataset.get('dataset_revision', 0),
            'data_fingerprint': dataset.get('data_fingerprint') or data_fingerprint(dataset)}

def analysis_config(model, selection, options=None):
    cfg = {key: selection.get(key) or ({} if key == 'variable_types' else [] if key.endswith('variables') else None)
           for key in ROLES}
    cfg['model'] = ALIASES.get(model, model)
    cfg['options'] = dict(options or {})
    return cfg

def source_snapshot(dataset, model, selection, options=None):
    cfg = analysis_config(model, selection, options)
    return {**dataset_snapshot(dataset), 'config': cfg, 'config_fingerprint': digest(cfg),
            'created_at': datetime.now().isoformat(timespec='seconds')}

def analysis_is_current(analysis, dataset, model=None, selection=None, options=None):
    snap = analysis.get('source_snapshot') or {}
    # Legacy results stay accessible as historical records but cannot prove freshness.
    if not snap or analysis.get('artifact_status') == 'stale':
        return False
    current = dataset_snapshot(dataset)
    if any(snap.get(k) != current.get(k) for k in ('dataset_id', 'data_fingerprint')):
        return False
    if model and ALIASES.get(model, model) != ALIASES.get(analysis.get('model'), analysis.get('model')):
        return False
    if selection is not None:
        cfg = analysis_config(model or analysis.get('model'), selection, options)
        return digest(cfg) == snap.get('config_fingerprint')
    return True

def chart_is_current(chart, analysis_id=None, dataset=None):
    meta = chart.get('meta') or {}
    aid = str(chart.get('analysis_id') or meta.get('analysis_id') or '')
    if meta.get('artifact_status') == 'stale' or not dataset:
        return False
    if meta.get('data_fingerprint') != dataset_snapshot(dataset)['data_fingerprint']:
        return False
    if meta.get('scope') == 'dataset' or aid.startswith('dataset:'):
        return meta.get('dataset_id') == dataset.get('dataset_id')
    return bool(analysis_id) and aid == analysis_id
