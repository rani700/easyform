"""PostgreSQL store for candidate profiles, pending-retry state, and document audits.

Backed by asyncpg with a connection pool. Schema is created on first connect.
Reads & writes are concurrent-safe (unlike the previous SQLite implementation),
which matters when multiple candidates submit at once.
"""
from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL", "")

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
    tenth_json                JSONB,
    twelfth_json              JSONB,
    graduation_json           JSONB,
    postgraduation_json       JSONB,
    passport_photo_valid      BOOLEAN DEFAULT FALSE,
    signature_valid           BOOLEAN DEFAULT FALSE,
    aadhaar_present           BOOLEAN DEFAULT FALSE,
    pan_present               BOOLEAN DEFAULT FALSE,
    extracted_raw             JSONB,
    created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_requests (
    user_id                  TEXT PRIMARY KEY,
    email                    TEXT NOT NULL,
    last_status              TEXT NOT NULL,
    missing_fields           JSONB,
    validation_errors        JSONB,
    extracted_so_far         JSONB,
    attempt_count            INTEGER NOT NULL DEFAULT 1,
    last_email_sent_at       TIMESTAMPTZ NOT NULL,
    next_retry_at            TIMESTAMPTZ NOT NULL,
    status                   TEXT NOT NULL DEFAULT 'awaiting_user',
    last_inbound_message_id  TEXT,
    last_inbound_subject     TEXT,
    created_at               TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS pending_due_idx
    ON pending_requests(status, next_retry_at);

CREATE TABLE IF NOT EXISTS documents_audit (
    audit_id        BIGSERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL,
    attempt_number  INTEGER NOT NULL,
    filename        TEXT,
    declared_type   TEXT,
    classified_type TEXT,
    extraction      JSONB,
    parse_error     TEXT,
    confidence      DOUBLE PRECISION,
    processed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _plus_hours(hours: float) -> datetime:
    return _now() + timedelta(hours=hours)


def _j(value: Any) -> str | None:
    """JSON-encode for JSONB columns; None passes through."""
    if value is None:
        return None
    return json.dumps(value, default=str)


class Store:
    """Async wrapper around an asyncpg connection pool for the EasyForm tables."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or DATABASE_URL
        self._pool: asyncpg.Pool | None = None

    async def init(self) -> None:
        if not self.dsn:
            raise RuntimeError("DATABASE_URL is not set")
        self._pool = await asyncpg.create_pool(
            self.dsn, min_size=1, max_size=10, command_timeout=30,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)
        logger.info("PostgreSQL store initialised")

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @asynccontextmanager
    async def _conn(self):
        assert self._pool is not None, "Store.init() not called"
        async with self._pool.acquire() as conn:
            yield conn

    # ----- candidates -----
    async def upsert_candidate(self, profile: dict[str, Any]) -> None:
        cols_plain = [
            "user_id", "email", "name", "father_name", "mother_name",
            "date_of_birth", "age", "gender",
            "permanent_address", "permanent_pin_code",
            "correspondence_address", "correspondence_pin_code",
            "marital_status", "nationality", "caste", "mobile_number",
            "disability_status",
            "passport_photo_valid", "signature_valid",
            "aadhaar_present", "pan_present",
        ]
        cols_json = [
            "tenth_json", "twelfth_json", "graduation_json", "postgraduation_json",
            "extracted_raw",
        ]
        values_plain = [profile.get(c) for c in cols_plain]
        values_json = [
            _j(profile.get("tenth")),
            _j(profile.get("twelfth")),
            _j(profile.get("graduation")),
            _j(profile.get("postgraduation")),
            _j(profile),
        ]
        cols_all = cols_plain + cols_json
        placeholders = ", ".join(f"${i+1}" for i in range(len(cols_all)))
        set_clause = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in cols_all if c != "user_id"
        )
        sql = (
            f"INSERT INTO candidates ({', '.join(cols_all)}) "
            f"VALUES ({placeholders}) "
            f"ON CONFLICT (user_id) DO UPDATE SET {set_clause}, updated_at=NOW()"
        )
        async with self._conn() as conn:
            await conn.execute(sql, *(values_plain + values_json))

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
        now = _now()
        next_retry = _plus_hours(retry_hours)
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT attempt_count FROM pending_requests WHERE user_id=$1", user_id
            )
            if row:
                await conn.execute(
                    """UPDATE pending_requests SET
                        email=$1,
                        last_status=$2,
                        missing_fields=$3::jsonb,
                        validation_errors=$4::jsonb,
                        extracted_so_far=$5::jsonb,
                        attempt_count=1,
                        last_email_sent_at=$6,
                        next_retry_at=$7,
                        status=$8,
                        last_inbound_message_id=COALESCE($9, last_inbound_message_id),
                        last_inbound_subject=COALESCE($10, last_inbound_subject),
                        updated_at=NOW()
                       WHERE user_id=$11""",
                    email,
                    last_status,
                    _j(missing_fields),
                    _j(validation_errors),
                    _j(extracted_so_far),
                    now,
                    next_retry,
                    status,
                    last_inbound_message_id,
                    last_inbound_subject,
                    user_id,
                )
            else:
                await conn.execute(
                    """INSERT INTO pending_requests
                       (user_id, email, last_status, missing_fields, validation_errors,
                        extracted_so_far, attempt_count, last_email_sent_at, next_retry_at,
                        status, last_inbound_message_id, last_inbound_subject)
                       VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, 1, $7, $8, $9, $10, $11)""",
                    user_id,
                    email,
                    last_status,
                    _j(missing_fields),
                    _j(validation_errors),
                    _j(extracted_so_far),
                    now,
                    next_retry,
                    status,
                    last_inbound_message_id,
                    last_inbound_subject,
                )
            return 1

    async def clear_pending(self, user_id: str) -> None:
        async with self._conn() as conn:
            await conn.execute("DELETE FROM pending_requests WHERE user_id=$1", user_id)

    async def get_pending(self, user_id: str) -> dict[str, Any] | None:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM pending_requests WHERE user_id=$1", user_id
            )
            if not row:
                return None
            d = dict(row)
            # asyncpg returns JSONB as parsed Python (depending on codec). Force str
            # representation so callers that json.loads() on these don't crash.
            for k in ("missing_fields", "validation_errors", "extracted_so_far"):
                v = d.get(k)
                if v is not None and not isinstance(v, str):
                    d[k] = json.dumps(v, default=str)
            return d

    async def list_pending_senders(self) -> list[str]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT email FROM pending_requests "
                "WHERE status IN ('awaiting_user', 'awaiting_confirmation')"
            )
            return [r["email"] for r in rows]

    async def list_due_for_retry(self) -> list[dict[str, Any]]:
        async with self._conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM pending_requests "
                "WHERE status='awaiting_user' AND attempt_count < 3 "
                "AND next_retry_at <= NOW()"
            )
            out: list[dict[str, Any]] = []
            for r in rows:
                d = dict(r)
                for k in ("missing_fields", "validation_errors", "extracted_so_far"):
                    v = d.get(k)
                    if v is not None and not isinstance(v, str):
                        d[k] = json.dumps(v, default=str)
                out.append(d)
            return out

    async def bump_attempt(self, user_id: str, *, retry_hours: float = 6.0) -> int:
        async with self._conn() as conn:
            row = await conn.fetchrow(
                """UPDATE pending_requests
                   SET attempt_count=attempt_count+1,
                       last_email_sent_at=$1,
                       next_retry_at=$2,
                       updated_at=NOW()
                   WHERE user_id=$3
                   RETURNING attempt_count""",
                _now(),
                _plus_hours(retry_hours),
                user_id,
            )
            return row["attempt_count"] if row else 0

    async def discard_stale(self) -> int:
        async with self._conn() as conn:
            result = await conn.execute(
                """UPDATE pending_requests SET status='discarded', updated_at=NOW()
                   WHERE status='awaiting_user' AND attempt_count >= 3
                   AND next_retry_at <= NOW()"""
            )
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    async def purge_discarded(self, *, retention_days: float = 7.0) -> int:
        """Delete discarded rows older than the retention window, so the sender
        is treated as a brand-new address (welcome email) next time they write.

        `updated_at` doubles as the discard timestamp: discard_stale() touches it
        and discarded rows are never updated again (a real re-submission revives
        the row to 'awaiting_user' via upsert_pending first).
        """
        async with self._conn() as conn:
            result = await conn.execute(
                """DELETE FROM pending_requests
                   WHERE status='discarded'
                   AND updated_at <= NOW() - ($1 * INTERVAL '1 day')""",
                retention_days,
            )
            try:
                return int(result.split()[-1])
            except (ValueError, IndexError):
                return 0

    # ----- documents_audit -----
    async def record_audit(
        self,
        *,
        user_id: str,
        attempt_number: int,
        per_doc: dict[str, dict[str, Any]],
        classified: dict[str, str],
    ) -> None:
        if not per_doc:
            return
        rows = []
        for filename, extraction in per_doc.items():
            rows.append(
                (
                    user_id,
                    attempt_number,
                    filename,
                    None,
                    classified.get(filename),
                    _j(extraction),
                    extraction.get("_parse_error") if isinstance(extraction, dict) else None,
                    extraction.get("confidence") if isinstance(extraction, dict) else None,
                )
            )
        async with self._conn() as conn:
            await conn.executemany(
                """INSERT INTO documents_audit
                   (user_id, attempt_number, filename, declared_type, classified_type,
                    extraction, parse_error, confidence)
                   VALUES ($1, $2, $3, $4, $5, $6::jsonb, $7, $8)""",
                rows,
            )
