"""Read-only deploy guard. Never log owners, payloads, records or credentials."""
import sqlite3
import sys

with sqlite3.connect('file:/var/lib/methodpal/methodpal.sqlite3?mode=ro', uri=True) as db:
    count = db.execute("SELECT count(*) FROM records WHERE namespace='jobs' AND json_extract(value,'$.status') IN ('queued','running')").fetchone()[0]
print('Pending or running jobs:', count)
if count:
    sys.exit('Deploy deferred to avoid interrupting analysis work.')
