"""Versioned SQLite persistence; all business queries include an owner."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_ROOT = Path(os.getenv('METHODPAL_DATA_ROOT', str(Path(__file__).resolve().parents[2] / 'storage'))).resolve()
DB_PATH = Path(os.getenv('METHODPAL_DATABASE_PATH', str(DATA_ROOT / 'methodpal.sqlite3'))).resolve()

@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()

def migrate():
    with connect() as db:
        db.execute('PRAGMA journal_mode=WAL')
        if db.execute('PRAGMA user_version').fetchone()[0] > 2:
            raise RuntimeError('Database schema is newer than this application')
        db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions (
          token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          csrf_token TEXT NOT NULL, expires_at INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
        CREATE TABLE IF NOT EXISTS records (
          owner_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
          namespace TEXT NOT NULL, id TEXT NOT NULL, value TEXT NOT NULL,
          PRIMARY KEY(owner_id, namespace, id));
        CREATE TABLE IF NOT EXISTS auth_attempts (
          bucket TEXT PRIMARY KEY, started INTEGER NOT NULL, attempts INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS legacy_imports (
          source TEXT PRIMARY KEY, owner_id TEXT NOT NULL REFERENCES users(id), imported_at INTEGER NOT NULL);
        ''')
        # Explicit columns make the migration safe for pre-existing users/sessions.
        columns = {row['name'] for row in db.execute('PRAGMA table_info(users)')}
        if 'email' not in columns:
            db.execute('ALTER TABLE users ADD COLUMN email TEXT')
        if 'email_verified_at' not in columns:
            db.execute('ALTER TABLE users ADD COLUMN email_verified_at INTEGER')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sessions)')}
        if 'created_at' not in columns:
            db.execute('ALTER TABLE sessions ADD COLUMN created_at INTEGER NOT NULL DEFAULT 0')
        db.executescript('''
        CREATE UNIQUE INDEX IF NOT EXISTS users_email ON users(email COLLATE NOCASE) WHERE email IS NOT NULL;
        CREATE TABLE IF NOT EXISTS auth_captchas (
          id TEXT PRIMARY KEY, answer_hash TEXT NOT NULL, ip_hash TEXT NOT NULL,
          expires_at INTEGER NOT NULL, consumed INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS email_challenges (
          id TEXT PRIMARY KEY, email TEXT NOT NULL, purpose TEXT NOT NULL,
          user_id TEXT, code_hash TEXT NOT NULL, created_at INTEGER NOT NULL,
          expires_at INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
          consumed INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS email_challenges_recipient ON email_challenges(email, created_at);
        PRAGMA user_version=2;
        ''')

if __name__ == '__main__':
    migrate()
