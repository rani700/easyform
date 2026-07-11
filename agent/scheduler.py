"""Background retry/discard scheduler — replaces the n8n cron workflow.

Every SCHEDULER_INTERVAL_SECONDS:
  - For each pending row whose `next_retry_at` has passed and attempt_count < 3:
      send the appropriate follow-up template (attempt N+1), bump the counter,
      reset the timer to +6h.
  - Mark rows that hit attempt 3 and have lapsed as `discarded`.
  - Delete `discarded` rows older than DISCARD_RETENTION_DAYS (default 7), so
    the address is treated as brand-new (welcome email) if they write again.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from agent.mail_sender import send_followup
from agent.store import Store

logger = logging.getLogger(__name__)

_INTERVAL = int(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "1800"))  # 30 min
_RETRY_HOURS = float(os.environ.get("RETRY_INTERVAL_HOURS", "6"))
_RETENTION_DAYS = float(os.environ.get("DISCARD_RETENTION_DAYS", "7"))


async def _scan_once(store: Store) -> tuple[int, int, int]:
    """Returns (sent, discarded, purged) counts."""
    sent = 0
    due = await store.list_due_for_retry()
    for row in due:
        try:
            extracted = json.loads(row.get("extracted_so_far") or "{}")
            missing = json.loads(row.get("missing_fields") or "[]")
            errors = json.loads(row.get("validation_errors") or "[]")
        except Exception:  # noqa: BLE001
            extracted, missing, errors = {}, [], []
        next_attempt = (row.get("attempt_count") or 1) + 1
        try:
            await send_followup(
                to=row["email"],
                attempt=next_attempt,
                candidate_name=extracted.get("name"),
                user_id=row["user_id"],
                missing_fields=missing,
                validation_errors=errors,
                in_reply_to=row.get("last_inbound_message_id"),
                reply_to_subject=row.get("last_inbound_subject"),
            )
            await store.bump_attempt(row["user_id"], retry_hours=_RETRY_HOURS)
            sent += 1
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Retry email failed for user_id=%s: %s", row.get("user_id"), exc
            )

    discarded = await store.discard_stale()
    purged = await store.purge_discarded(retention_days=_RETENTION_DAYS)
    return sent, discarded, purged


async def run_scheduler(store: Store) -> None:
    """Long-running scheduler. Cancel to stop."""
    logger.info(
        "Scheduler started: interval=%ds retry_hours=%.1f", _INTERVAL, _RETRY_HOURS
    )
    while True:
        try:
            sent, discarded, purged = await _scan_once(store)
            if sent or discarded or purged:
                logger.info(
                    "Scheduler tick: %d retry email(s), %d discarded, %d purged",
                    sent,
                    discarded,
                    purged,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Scheduler tick failed: %s", exc)
        await asyncio.sleep(_INTERVAL)
