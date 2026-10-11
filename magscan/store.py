"""SQLite-backed state: one row per file, plus an apply history for undo."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    original_path TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',  -- new | queued | review | applied | skipped | error
    info TEXT,                           -- MagazineInfo JSON from the model
    edits TEXT,                          -- user overrides, same keys as MagazineInfo
    error TEXT,
    page_count INTEGER,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    from_path TEXT NOT NULL,
    to_path TEXT NOT NULL,
    created_files TEXT NOT NULL,
    applied_at REAL NOT NULL,
    undone INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    def __init__(self, db_file: Path):
        db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(db_file, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(SCHEMA)
            # Jobs that were running when the container stopped go back to the queue's start.
            self._db.execute("UPDATE items SET status='new' WHERE status='queued'")
            self._db.commit()

    def _exec(self, sql: str, args=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur

    def add_item(self, path: str) -> Optional[int]:
        cur = self._exec(
            "INSERT OR IGNORE INTO items (path, original_path, updated_at) VALUES (?, ?, ?)",
            (path, path, time.time()),
        )
        return cur.lastrowid if cur.rowcount else None

    def get(self, item_id: int) -> Optional[dict]:
        row = self._exec("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return _row(row) if row else None

    def all_paths(self) -> dict[str, dict]:
        return {r["path"]: _row(r) for r in self._exec("SELECT * FROM items").fetchall()}

    def items(self, status: Optional[str] = None, limit: int = 500, offset: int = 0) -> list[dict]:
        if status:
            rows = self._exec("SELECT * FROM items WHERE status=? ORDER BY path LIMIT ? OFFSET ?", (status, limit, offset))
        else:
            rows = self._exec("SELECT * FROM items ORDER BY path LIMIT ? OFFSET ?", (limit, offset))
        return [_row(r) for r in rows.fetchall()]

    def counts(self) -> dict[str, int]:
        rows = self._exec("SELECT status, COUNT(*) AS n FROM items GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def update(self, item_id: int, **values) -> None:
        for key in ("info", "edits"):
            if key in values and values[key] is not None and not isinstance(values[key], str):
                values[key] = json.dumps(values[key], ensure_ascii=False)
        values["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in values)
        self._exec(f"UPDATE items SET {cols} WHERE id=?", (*values.values(), item_id))

    def delete(self, item_id: int) -> None:
        self._exec("DELETE FROM history WHERE item_id=?", (item_id,))
        self._exec("DELETE FROM items WHERE id=?", (item_id,))

    def add_history(self, item_id: int, from_path: str, to_path: str, created_files: list[str]) -> None:
        self._exec(
            "INSERT INTO history (item_id, from_path, to_path, created_files, applied_at) VALUES (?, ?, ?, ?, ?)",
            (item_id, from_path, to_path, json.dumps(created_files), time.time()),
        )

    def last_history(self, item_id: int) -> Optional[dict]:
        row = self._exec(
            "SELECT * FROM history WHERE item_id=? AND undone=0 ORDER BY id DESC LIMIT 1", (item_id,)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["created_files"] = json.loads(d["created_files"])
        return d

    def mark_undone(self, history_id: int) -> None:
        self._exec("UPDATE history SET undone=1 WHERE id=?", (history_id,))


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["info"] = json.loads(d["info"]) if d["info"] else None
    d["edits"] = json.loads(d["edits"]) if d["edits"] else {}
    return d
