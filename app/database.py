"""SQLite layer: clients + documents state machine."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .logger import get_logger

log = get_logger("database")

NEW = "NEW"
RECOGNIZED = "RECOGNIZED"
COMPLETED = "COMPLETED"
NO_BARCODE = "NO_BARCODE"
CLIENT_NOT_FOUND = "CLIENT_NOT_FOUND"
ERROR = "ERROR"

TERMINAL_STATUSES = {COMPLETED, NO_BARCODE, CLIENT_NOT_FOUND, ERROR}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_barcode(value: str | None) -> str:
    """Strip leading zeros from numeric barcodes; leave others untouched."""
    if value is None:
        return ""
    value = value.strip()
    if value.isdigit():
        stripped = value.lstrip("0")
        return stripped or "0"
    return value


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.init_schema()

    def init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS clients (
                    client_id INTEGER PRIMARY KEY,
                    full_name TEXT NOT NULL,
                    barcode   TEXT UNIQUE NOT NULL
                );
                CREATE TABLE IF NOT EXISTS documents (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    filename      TEXT NOT NULL,
                    barcode       TEXT,
                    client_id     INTEGER,
                    status        TEXT NOT NULL,
                    source_path   TEXT,
                    metka_path    TEXT,
                    final_path    TEXT,
                    error         TEXT,
                    sha256        TEXT,
                    created_at    TEXT NOT NULL,
                    dispatched_at TEXT,
                    processed_at  TEXT,
                    updated_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
                CREATE INDEX IF NOT EXISTS idx_documents_source ON documents(source_path);
                """
            )

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ clients
    def upsert_client(self, client_id: int, full_name: str, barcode: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO clients (client_id, full_name, barcode) VALUES (?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET full_name = excluded.full_name, barcode = excluded.barcode
                """,
                (client_id, full_name, barcode),
            )

    def find_client_by_barcode(self, barcode: str) -> sqlite3.Row | None:
        raw = (barcode or "").strip()
        normalized = normalize_barcode(raw)
        candidates: list[Any] = [raw, normalized]
        if normalized.isdigit():
            candidates.append(int(normalized))
        seen: set[Any] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            row = self.conn.execute(
                "SELECT client_id, full_name, barcode FROM clients WHERE barcode = ? OR client_id = ? LIMIT 1",
                (str(candidate), candidate if isinstance(candidate, int) else -1),
            ).fetchone()
            if row is not None:
                return row
        return None

    # ---------------------------------------------------------------- documents
    def add_document(self, filename: str, source_path: str, sha256: str | None = None) -> int:
        now = utcnow()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO documents (filename, status, source_path, sha256, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (filename, NEW, source_path, sha256, now, now),
            )
        return int(cur.lastrowid)

    def get_document(self, doc_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM documents WHERE id = ?", (doc_id,)).fetchone()

    def find_document_by_source(self, source_path: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM documents WHERE source_path = ? ORDER BY id DESC LIMIT 1", (source_path,)
        ).fetchone()

    def documents_by_status(self, statuses: Iterable[str]) -> list[sqlite3.Row]:
        statuses = list(statuses)
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        return list(self.conn.execute(
            f"SELECT * FROM documents WHERE status IN ({placeholders}) ORDER BY id", statuses))

    def update_document(self, doc_id: int, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        columns = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [doc_id]
        with self.conn:
            self.conn.execute(f"UPDATE documents SET {columns} WHERE id = ?", values)

    def delete_document(self, doc_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM documents WHERE id = ?", (doc_id,))

    def all_documents(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM documents ORDER BY id"))