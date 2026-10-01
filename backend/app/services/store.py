import json
from collections.abc import MutableMapping
from pathlib import Path
from app.services.database import DATA_ROOT, connect
from app.services.user_context import get_current_user_id

STORAGE_ROOT = DATA_ROOT / 'files'
_MISSING = object()

class PersistentSqliteDict(MutableMapping):
    """IDs never grant access: every SQL query is scoped to a trusted owner."""
    def __init__(self, namespace):
        self.namespace = namespace

    def __getitem__(self, key):
        with connect() as db:
            row = db.execute('SELECT value FROM records WHERE owner_id=? AND namespace=? AND id=?', (get_current_user_id(), self.namespace, str(key))).fetchone()
        if row is None:
            raise KeyError(key)
        return json.loads(row[0])

    def __setitem__(self, key, value):
        if self.namespace == 'datasets':
            from app.services.artifact_versions import data_fingerprint
            previous = self.get(key) or {}
            value = dict(value)
            fingerprint = data_fingerprint(value)
            old_fingerprint = previous.get('data_fingerprint') or (data_fingerprint(previous) if previous else None)
            value['data_fingerprint'] = fingerprint
            value['dataset_revision'] = int(previous.get('dataset_revision') or 0) + int(fingerprint != old_fingerprint)
        encoded = json.dumps(value, ensure_ascii=False)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            from app.services.storage_limits import check_write
            check_write(db, get_current_user_id(), self.namespace, str(key), encoded)
            db.execute('INSERT INTO records VALUES (?,?,?,?) ON CONFLICT(owner_id,namespace,id) DO UPDATE SET value=excluded.value', (get_current_user_id(), self.namespace, str(key), encoded))

    def __delitem__(self, key):
        with connect() as db:
            count = db.execute('DELETE FROM records WHERE owner_id=? AND namespace=? AND id=?', (get_current_user_id(), self.namespace, str(key))).rowcount
        if not count:
            raise KeyError(key)

    def __iter__(self):
        with connect() as db:
            rows = db.execute('SELECT id FROM records WHERE owner_id=? AND namespace=?', (get_current_user_id(), self.namespace)).fetchall()
        return iter([r[0] for r in rows])

    def __len__(self):
        with connect() as db:
            return db.execute('SELECT count(*) FROM records WHERE owner_id=? AND namespace=?', (get_current_user_id(), self.namespace)).fetchone()[0]

    def pop(self, key, default=_MISSING):
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT value FROM records WHERE owner_id=? AND namespace=? AND id=?', (get_current_user_id(), self.namespace, str(key))).fetchone()
            if row is not None:
                db.execute('DELETE FROM records WHERE owner_id=? AND namespace=? AND id=?', (get_current_user_id(), self.namespace, str(key)))
                return json.loads(row[0])
        if default is _MISSING:
            raise KeyError(key)
        return default

    def clear(self):
        with connect() as db:
            db.execute('DELETE FROM records WHERE owner_id=? AND namespace=?', (get_current_user_id(), self.namespace))

def user_storage_dir():
    return str((STORAGE_ROOT / get_current_user_id()).resolve())

def owned_file_path(value):
    root = Path(user_storage_dir()).resolve()
    path = Path(value).resolve()
    if root not in path.parents:
        raise ValueError('File is outside the current user storage')
    return path

DATASETS = PersistentSqliteDict('datasets')
PROJECTS = PersistentSqliteDict('projects')
ANALYSES = PersistentSqliteDict('analyses')
REPORTS = PersistentSqliteDict('reports')
VARIABLE_CONFIGS = PersistentSqliteDict('variable_configs')
AGENT_CHATS = PersistentSqliteDict('agent_chats')
AGENT_STATES = PersistentSqliteDict('agent_states')
JOBS = PersistentSqliteDict('jobs')
CHARTS = PersistentSqliteDict('charts')
