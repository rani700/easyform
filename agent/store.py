"""SQLite store for candidate profiles, pending-retry state, and document audits.

Replaces the original Snowflake schema with a local file DB so the service runs
self-contained on the homelab (no external warehouse / n8n required).
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("SQLITE_PATH", "/data/easyform.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    user_id                   TEXT PRIMARY KEY,
    email                     TEXT NOT NULL,
    name                      TEXT,
    father_name               TEXT,
    mother_name               TEXT,
    date_of_birth             TEXT,
    age                       INTEGER,
    gender                    TEXT,
    permanent_address         TEXT,
    permanent_pin_code        TEXT,
    correspondence_address    TEXT,
    correspondence_pin_code   TEXT,
    marital_status            TEXT,
    nationality               TEXT,
    caste                     TEXT,
    mobile_number             TEXT,
    disability_status         TEXT,
    tenth_json                TEXT,
    twelfth_json              TEXT,
    graduation_json           TEXT,
    postgraduation_json       TEXT,
    passport_photo_valid      INTEGER DEFAULT 0,
    signature_valid           INTEGER DEFAULT 0,
    aadhaar_present           INTEGER DEFAULT 0,
    pan_present               INTEGER DEFAULT 0,
    extracted_raw             TEXT,
    created_at                TEXT DEFAULT (datetime('now')),
    updated_at                TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS pending_requests (
    user_id             TEXT PRIMARY KEY,
    email               TEXT NOT NULL,
    last_status         TEXT NOT NULL,
    missing_fields      TEXT,            -- JSON array
    validation_errors   TEXT,            -- JSON array
    extracted_so_far    TEXT,            -- JSON object
    attempt_count       INTEGER NOT NULL DEFAULT 1,
    last_email_sent_at  TEXT NOT NULL,
    next_retry_at       TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'awaiting_user',
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS pending_due_idx
    ON pending_requests(status, next_retry_at);

CREATE TABLE IF NOT EXISTS documents_audit (
    audit_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    filename        TEXT,
    declared_type   TEXT,
    classified_type TEXT,
    extraction      TEXT,        -- JSON
    parse_error     TEXT,
    confidence      REAL,
    processed_at    TEXT DEFAULT (datetime('now'))
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _plus_hours(hours: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


class Store:
    """Thin async wrapper around aiosqlite for the EasyForm tables."""

    def __init__(self, path: str = DB_PATH):
        self.path = path

    async def init(self) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(_SCHEMA)
            # Idempotent column additions for older DBs.
            for stmt in (
                "ALTER TABLE pending_requests ADD COLUMN last_inbound_message_id TEXT",
                "ALTER TABLE pending_requests ADD COLUMN last_inbound_subject TEXT",
            ):
                try:
                    await db.execute(stmt)
                except aiosqlite.OperationalError as exc:
                    if "duplicate column" not in str(exc).lower():
                        raise
            await db.commit()
        logger.info("SQLite store initialised at %s", self.path)

    @asynccontextmanager
    async def _conn(self):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA foreign_keys=ON;")
            yield db

    # ----- candidates -----
    async def upsert_candidate(self, profile: dict[str, Any]) -> None:
        cols = [
            "user_id", "email", "name", "father_name", "mother_name",
            "date_of_birth", "age", "gender",
            "permanent_address", "permanent_pin_code",
            "correspondence_address", "correspondence_pin_code",
            "marital_status", "nationality", "caste", "mobile_number",
            "disability_status",
            "passport_photo_valid", "signature_valid",
            "aadhaar_present", "pan_present",
        ]
        values = [profile.get(c) for c in cols]
        # JSON columns
        values += [
            json.dumps(profile.get("tenth")) if profile.get("tenth") else None,
            json.dumps(profile.get("twelfth")) if profile.get("twelfth") else None,
            json.dumps(profile.get("graduation")) if profile.get("graduation") else None,
            json.dumps(profile.get("postgraduation")) if profile.get("postgraduation") else None,
            json.dumps(profile, default=str),
        ]
        cols_all = cols + [
            "tenth_json", "twelfth_json", "graduation_json", "postgraduation_json",
            "extracted_raw",
        ]
        placeholders = ",".join("?" * len(cols_all))
        set_clause = ",".join(f"{c}=excluded.{c}" for c in cols_all if c != "user_id")
        sql = (
            f"INSERT INTO candidates ({','.join(cols_all)}) VALUES ({placeholders}) "
            f"ON CONFLICT(user_id) DO UPDATE SET {set_clause}, updated_at=datetime('now')"
        )
        async with self._conn() as db:
            await db.execute(sql, values)
            await db.commit()

    # ----- pending_requests -----
    async def upsert_pending(
        self,
        *,
        user_id: str,
        email: str,
        last_status: str,
        missing_fields: list[str],
        validation_errors: list[dict[str, Any]],
        extracted_so_far: dict[str, Any],
        last_inbound_message_id: str | None = None,
        last_inbound_subject: str | None = None,
        retry_hours: float = 6.0,
        status: str = "awaiting_user",
    ) -> int:
        """Insert OR update pending row. `status` may be 'awaiting_user' or
        'awaiting_confirmation' depending on the stage."""
        async with self._conn() as db:
            row = await (
                await db.execute(
                    "SELECT attempt_count FROM pending_requests WHERE user_id=?",
                    (user_id,),
                )
            ).fetchone()
            now = _now()
            next_retry = _plus_hours(retry_hours)
            if row:
                await db.execute(
                    "UPDATE pending_requests SET email=?, last_status=?, missing_fields=?, "
                    "validation_errors=?, extracted_so_far=?, attempt_count=1, "
                    "last_email_sent_at=?, next_retry_at=?, status=?, "
                    "last_inbound_message_id=COALESCE(?, last_inbound_message_id), "
                    "last_inbound_subject=COALESCE(?, last_inbound_subject), "
                    "updated_at=datetime('now') WHERE user_id=?",
                    (
                        email,
                        last_status,
                        json.dumps(missing_fields),
                        json.dumps(validation_errors),
                        json.dumps(extracted_so_far, default=str),
                        now,
                        next_retry,
                        status,
                        last_inbound_message_id,
                        last_inbound_subject,
                        user_id,
                    ),
                )
            else:
                await db.execute(
                    "INSERT INTO pending_requests (user_id, email, last_status, "
                    "missing_fields, validation_errors, extracted_so_far, attempt_count, "
                    "last_email_sent_at, next_retry_at, status, "
                    "last_inbound_message_id, last_inbound_subject) "
                    "VALUES (?,?,?,?,?,?,1,?,?,?,?,?)",
                    (
                        user_id,
                        email,
                        last_status,
                        json.dumps(missing_fields),
                        json.dumps(validation_errors),
                        json.dumps(extracted_so_far, default=str),
                        now,
                        next_retry,
                        status,
                        last_inbound_message_id,
                        last_inbound_subject,
                    ),
                )
            await db.commit()
            return 1

    async def clear_pending(self, user_id: str) -> None:
        async with self._conn() as db:
            await db.execute("DELETE FROM pending_requests WHERE user_id=?", (user_id,))
            await db.commit()

    async def get_pending(self, user_id: str) -> dict[str, Any] | None:
        async with self._conn() as db:
            row = await (
                await db.execute(
                    "SELECT * FROM pending_requests WHERE user_id=?", (user_id,)
                )
            ).fetchone()
            return dict(row) if row else None

    async def list_pending_senders(self) -> list[str]:
        """Emails of users with an open pending_request (awaiting more info OR
        awaiting confirmation) — replies from them should be processed even if
        the subject doesn't carry our marker."""
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT email FROM pending_requests "
                "WHERE status IN ('awaiting_user', 'awaiting_confirmation')"
            )
            return [r["email"] for r in await cur.fetchall()]

    async def list_due_for_retry(self) -> list[dict[str, Any]]:
        async with self._conn() as db:
            cur = await db.execute(
                "SELECT * FROM pending_requests WHERE status='awaiting_user' "
                "AND attempt_count < 3 AND next_retry_at <= datetime('now')"
            )
            return [dict(r) for r in await cur.fetchall()]

    async def bump_attempt(self, user_id: str, *, retry_hours: float = 6.0) -> int:
        """Increment attempt_count and reset last_email_sent_at / next_retry_at."""
        async with self._conn() as db:
            await db.execute(
                "UPDATE pending_requests SET attempt_count=attempt_count+1, "
                "last_email_sent_at=?, next_retry_at=?, updated_at=datetime('now') "
                "WHERE user_id=?",
                (_now(), _plus_hours(retry_hours), user_id),
            )
            row = await (
                await db.execute(
                    "SELECT attempt_count FROM pending_requests WHERE user_id=?",
                    (user_id,),
                )
            ).fetchone()
            await db.commit()
            return row["attempt_count"] if row else 0

    async def discard_stale(self) -> int:
        """Mark requests that reached attempt 3 and whose retry window has elapsed."""
        async with self._conn() as db:
            cur = await db.execute(
                "UPDATE pending_requests SET status='discarded', updated_at=datetime('now') "
                "WHERE status='awaiting_user' AND attempt_count >= 3 "
                "AND next_retry_at <= datetime('now')"
            )
            n = cur.rowcount
            await db.commit()
            return n

    # ----- documents_audit -----
    async def record_audit(
        self,
        *,
        user_id: str,
        attempt_number: int,
        per_doc: dict[str, dict[str, Any]],
        classified: dict[str, str],
    ) -> None:
        rows = []
        for filename, extraction in per_doc.items():
            rows.append(
                (
                    user_id,
                    attempt_number,
                    filename,
                    None,
                    classified.get(filename),
                    json.dumps(extraction, default=str),
                    extraction.get("_parse_error") if isinstance(extraction, dict) else None,
                    extraction.get("confidence") if isinstance(extraction, dict) else None,
                )
            )
        if not rows:
            return
        async with self._conn() as db:
            await db.executemany(
                "INSERT INTO documents_audit (user_id, attempt_number, filename, "
                "declared_type, classified_type, extraction, parse_error, confidence) "
                "VALUES (?,?,?,?,?,?,?,?)",
                rows,
            )
            await db.commit()
