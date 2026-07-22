"""
Download history persistence.
Records every successful download to SQLite so the UI can list, re-download,
and delete past entries even after server restart.
"""
import os
import sqlite3
import threading

_LOCK = threading.Lock()


def _db_path(root):
    return os.path.join(root, "history.db")


def _conn(root):
    c = sqlite3.connect(_db_path(root), timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db(root):
    with _conn(root) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            format TEXT,               -- 'video' | 'audio'
            format_id TEXT,
            filename TEXT,
            file_path TEXT,
            start_time TEXT,
            end_time TEXT,
            file_size INTEGER,
            source_site TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_history_created ON history(created_at DESC);
        """)
        c.commit()


def add(root, *, url, title=None, format="video", format_id=None,
        filename=None, file_path=None, start_time=None, end_time=None,
        file_size=None, source_site=None):
    """Insert a completed download into history. Returns the new row id."""
    with _LOCK, _conn(root) as c:
        cur = c.execute("""
            INSERT INTO history
            (url, title, format, format_id, filename, file_path, start_time, end_time, file_size, source_site)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, (url, title, format, format_id, filename, file_path,
              start_time, end_time, file_size, source_site))
        c.commit()
        return cur.lastrowid


def list_all(root, limit=100, offset=0):
    with _conn(root) as c:
        rows = c.execute("""
            SELECT * FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?
        """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get(root, hid):
    with _conn(root) as c:
        row = c.execute("SELECT * FROM history WHERE id=?", (hid,)).fetchone()
    return dict(row) if row else None


def delete(root, hid):
    with _LOCK, _conn(root) as c:
        c.execute("DELETE FROM history WHERE id=?", (hid,))
        c.commit()


def clear_all(root):
    with _LOCK, _conn(root) as c:
        c.execute("DELETE FROM history")
        c.commit()


def count(root):
    with _conn(root) as c:
        r = c.execute("SELECT COUNT(*) FROM history").fetchone()
    return r[0] if r else 0
