"""Offline admin import. Never automatically claims legacy data by username.
Run with server stopped: python scripts/import_legacy.py --source ... --username ... [--apply]
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.database import connect, migrate, DATA_ROOT

NAMESPACES = ['projects','datasets','analyses','charts','reports','variable_configs','agent_chats','agent_states']

def import_legacy(source, username, apply=False):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ValueError('Source must be a single legacy user directory')
    migrate()
    with connect() as db:
        user = db.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
        if not user:
            raise ValueError('Register the destination account first')
        owner = user[0]
        if db.execute('SELECT 1 FROM legacy_imports WHERE source=?',(str(source),)).fetchone():
            raise ValueError('This directory was already imported')
        if db.execute('SELECT 1 FROM records WHERE owner_id=?',(owner,)).fetchone():
            raise ValueError('Destination account must have an empty workspace')
    records, files = [], []
    target_root = DATA_ROOT / 'files' / owner / 'raw_datasets'
    for namespace in NAMESPACES:
        path = source / (namespace + '.json')
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(data, dict):
            raise ValueError('Invalid namespace: ' + namespace)
        for key, value in data.items():
            if namespace == 'datasets' and isinstance(value, dict) and value.get('_source', {}).get('path'):
                metadata = value['_source']
                # Resolve the basename beneath the selected source, never arbitrary stored absolute paths.
                basename = str(metadata['path']).replace('\\','/').split('/')[-1]
                original = (source / 'raw_datasets' / basename).resolve(strict=True)
                if source not in original.parents:
                    raise ValueError('Unsafe legacy source path')
                target = target_root / basename
                files.append((original,target))
                metadata['path'] = str(target)
            records.append((owner, namespace, str(key), json.dumps(value,ensure_ascii=False)))
    summary = {'records':len(records),'files':len(files),'username':username,'applied':False}
    if not apply:
        return summary
    copied = []
    try:
        for original, target in files:
            target.parent.mkdir(parents=True,exist_ok=True)
            if target.exists():
                raise ValueError('Destination file already exists: ' + target.name)
            shutil.copyfile(original,target)
            copied.append(target)
        with connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM records WHERE owner_id=?',(owner,)).fetchone():
                raise ValueError('Destination changed during import')
            db.executemany('INSERT INTO records VALUES (?,?,?,?)',records)
            db.execute('INSERT INTO legacy_imports VALUES (?,?,?)',(str(source),owner,int(time.time())))
    except Exception:
        for target in copied:
            target.unlink()
        raise
    summary['applied'] = True
    return summary

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',required=True)
    parser.add_argument('--username',required=True)
    parser.add_argument('--apply',action='store_true',help='Without this flag, only validate and preview')
    args = parser.parse_args()
    print(json.dumps(import_legacy(args.source,args.username,args.apply),ensure_ascii=False))
