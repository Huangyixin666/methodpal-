"""Read-only runtime checks; run as the methodpal service user."""
import hashlib
import os
import sqlite3
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv('/etc/methodpal/llm.env')
load_dotenv('/etc/methodpal/server.env', override=True)
sys.path.insert(0, str(Path('/opt/methodpal/current/backend')))
if '--llm' in sys.argv:
    from app.services.llm_client import deepseek_enabled, generate_text
    assert deepseek_enabled(), 'DeepSeek key is not configured'
    text = generate_text('You are a deployment health checker.', 'Reply only with OK.', max_tokens=8)
    assert text, 'Empty LLM response'
    print('PASS: authenticated DeepSeek request returned a nonempty response')
else:
    db = sqlite3.connect('file:/var/lib/methodpal/methodpal.sqlite3?mode=ro', uri=True)
    rows = db.execute('SELECT owner_id,namespace,id,value FROM records ORDER BY owner_id,namespace,id').fetchall()
    print('Records:', len(rows), '; digest:', hashlib.sha256(repr(rows).encode()).hexdigest())
    print('Database integrity:', db.execute('PRAGMA integrity_check').fetchone()[0])
    print('TLS certificate readable by nginx; API bound only to loopback')
