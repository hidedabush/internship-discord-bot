"""SQLite database helpers for internship storage and duplicate detection."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.tags import add_company_classification_tag

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "internships.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_value(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def build_dedupe_key(company: str, title: str, application_url: str) -> str:
    raw = "|".join([
        normalize_value(company),
        normalize_value(title),
        normalize_value(application_url),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def init_db() -> None:
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS internships (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dedupe_key TEXT UNIQUE NOT NULL,
                company TEXT NOT NULL,
                title TEXT NOT NULL,
                location TEXT,
                application_url TEXT,
                source_url TEXT,
                source_type TEXT,
                tags TEXT,
                uploaded_at TEXT,
                first_seen TEXT NOT NULL,
                last_seen TEXT NOT NULL,
                posted_to_discord INTEGER DEFAULT 0,
                status TEXT DEFAULT 'unknown'
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        _ensure_column(conn, "internships", "uploaded_at", "TEXT")
        conn.commit()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    columns = {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


def upsert_internship(internship: Dict[str, Any]) -> Tuple[int, bool]:
    """
    Insert or update an internship.

    Returns (database_id, is_new). is_new is True only the first time a posting is seen.
    """
    init_db()
    company = internship.get("company") or "Unknown Company"
    title = internship.get("title") or "Unknown Internship"
    application_url = internship.get("application_url") or ""
    dedupe_key = build_dedupe_key(company, title, application_url)
    current_time = now_iso()
    tags_list = add_company_classification_tag(internship.get("tags", []), company)
    internship["tags"] = tags_list
    tags = ",".join(tags_list)
    uploaded_at = internship.get("uploaded_at") or internship.get("date_found") or ""

    with _connect() as conn:
        existing = conn.execute(
            "SELECT id FROM internships WHERE dedupe_key = ?",
            (dedupe_key,),
        ).fetchone()

        if existing:
            conn.execute(
                """
                UPDATE internships
                SET last_seen = ?, location = COALESCE(NULLIF(?, ''), location),
                    source_url = COALESCE(NULLIF(?, ''), source_url),
                    source_type = COALESCE(NULLIF(?, ''), source_type),
                    tags = COALESCE(NULLIF(?, ''), tags),
                    uploaded_at = COALESCE(NULLIF(?, ''), uploaded_at)
                WHERE dedupe_key = ?
                """,
                (
                    current_time,
                    internship.get("location", ""),
                    internship.get("source_url", ""),
                    internship.get("source_type", ""),
                    tags,
                    uploaded_at,
                    dedupe_key,
                ),
            )
            conn.commit()
            return int(existing["id"]), False

        cursor = conn.execute(
            """
            INSERT INTO internships (
                dedupe_key, company, title, location, application_url, source_url,
                source_type, tags, uploaded_at, first_seen, last_seen, posted_to_discord, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                dedupe_key,
                company,
                title,
                internship.get("location", ""),
                application_url,
                internship.get("source_url", ""),
                internship.get("source_type", "unknown"),
                tags,
                uploaded_at,
                current_time,
                current_time,
                internship.get("status", "unknown"),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid), True


def mark_posted(internship_ids: Iterable[int]) -> None:
    ids = list(internship_ids)
    if not ids:
        return
    placeholders = ",".join("?" for _ in ids)
    with _connect() as conn:
        conn.execute(
            f"UPDATE internships SET posted_to_discord = 1 WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()


def list_internships(limit: int = 100, status: Optional[str] = None) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM internships WHERE status = ? ORDER BY first_seen DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM internships ORDER BY first_seen DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def get_unposted(limit: int = 20) -> List[Dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM internships
            WHERE posted_to_discord = 0
            ORDER BY first_seen ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def update_internship_status(internship_id: int, status: str) -> None:
    allowed = {"active", "closed", "unknown", "applied", "ignored", "saved"}
    if status not in allowed:
        raise ValueError(f"Unsupported status: {status}")
    with _connect() as conn:
        conn.execute("UPDATE internships SET status = ? WHERE id = ?", (status, internship_id))
        conn.commit()


def set_state(key: str, value: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO app_state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()


def get_state(key: str, default: str = "") -> str:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def stats() -> Dict[str, Any]:
    init_db()
    with _connect() as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM internships").fetchone()["count"]
        unposted = conn.execute("SELECT COUNT(*) AS count FROM internships WHERE posted_to_discord = 0").fetchone()["count"]
        applied = conn.execute("SELECT COUNT(*) AS count FROM internships WHERE status = 'applied'").fetchone()["count"]
    return {
        "total": total,
        "unposted": unposted,
        "applied": applied,
        "last_scan_time": get_state("last_scan_time", "Never"),
        "last_scan_found_count": get_state("last_scan_found_count", "0"),
    }


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    data = dict(row)
    data["tags"] = [tag for tag in (data.get("tags") or "").split(",") if tag]
    return data
