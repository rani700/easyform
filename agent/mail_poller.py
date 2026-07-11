"""IMAP inbox poller — the email-driven entry-point.

Every POLL_INTERVAL_SECONDS the poller:
  1. Connects to IMAP, fetches UNSEEN messages from INBOX.
  2. For each message, parses sender, body (for manual fields), and attachments.
  3. Carries forward any previously-extracted partial profile.
  4. Runs the LangGraph pipeline.
  5. On 'complete'      → upserts candidate, clears pending, sends confirmation.
     On 'needs_info' /
        'invalid'       → upserts pending, sends attempt-1 follow-up.
  6. Marks the message \\Seen on success.
"""
from __future__ import annotations

import asyncio
import base64
import email
import logging
import os
import re
from email.message import Message
from typing import Any

from aioimaplib import aioimaplib

from agent.graph import run_graph
from agent.mail_sender import send_confirmation, send_followup, send_review, send_finalized, send_welcome
from agent.nodes.llm import text_extract
from agent.schemas import (
    IncomingDocument,
    ManualFields,
    ProcessRequest,
    ProcessStatus,
)
from agent.store import Store

logger = logging.getLogger(__name__)

_POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "120"))
_IMAP_HOST = os.environ.get("IMAP_HOST", "imap.gmail.com")
_IMAP_PORT = int(os.environ.get("IMAP_PORT", "993"))
_IMAP_USER = os.environ.get("IMAP_USER", "")
_IMAP_PASSWORD = os.environ.get("IMAP_PASSWORD", "")
# Only fetch emails whose subject contains this string. Avoids touching the
# user's regular inbox traffic. Set to "" to disable the filter.
_SUBJECT_FILTER = os.environ.get("MAIL_SUBJECT_FILTER", "EasyForm")

_MANUAL_FIELD_KEYS = (
    "marital_status",
    "nationality",
    "caste",
    "mobile_number",
    "correspondence_address",
    "correspondence_pin_code",
    "disability_status",
    "tenth_specialization",
    "twelfth_specialization",
    "graduation_specialization",
    "postgraduation_specialization",
)
_ACCEPTED_MIME_PREFIXES = ("image/",)
_ACCEPTED_MIME_EXACT = {"application/pdf"}


def _parse_manual_fields_regex(body: str) -> dict[str, str]:
    """Fast path: pull `key: value` (or `key = value`) lines out of an email body."""
    found: dict[str, str] = {}
    for key in _MANUAL_FIELD_KEYS:
        keypat = key.replace("_", r"[ _\-]")
        m = re.search(
            rf"^\s*{keypat}\s*[:=]\s*(.+?)\s*$",
            body,
            flags=re.IGNORECASE | re.MULTILINE,
        )
        if m:
            found[key] = m.group(1).strip()
    return found


_NL_MANUAL_PROMPT = """You are extracting candidate details from a free-form email body
written by an Indian exam applicant. Read the text and return ONLY a JSON object
with these keys. Use null when a detail is genuinely not mentioned — do not guess.

{
  "marital_status":         "single" | "married" | "divorced" | "widowed" | null,
  "nationality":            string | null,
  "caste":                  "General" | "OBC" | "SC" | "ST" | "EWS" | null,
  "mobile_number":          string | null,
  "correspondence_address": string | null,
  "correspondence_pin_code": 6-digit string | null,
  "disability_status":      "None" | description string | null,
  "tenth_specialization":         string | null,
  "twelfth_specialization":       string | null,
  "graduation_specialization":    string | null,
  "postgraduation_specialization": string | null
}

Rules:
- "scheduled caste" / "schedule caste" / "SC" → "SC".  "OBC" / "backward class" → "OBC".
- "Indian" / "indian" → "Indian".
- "I am single" → marital_status "single". "married", "unmarried" (→ single), etc.
- Mobile number: only digits + optional + prefix.
- Address must be a postal address, not a sentence about caste/nationality.
- Stream / specialization examples: "Science (PCM)", "Commerce", "Arts/Humanities",
  "Computer Science & Engineering", "Mechanical", etc. Pull the stream regardless of
  how it's phrased ("my 12th stream was X", "I did B.Tech in X").
- If the text quotes a previous email (lines starting with ">"), ignore those lines.
- Do NOT extract anything that isn't directly stated.
"""


async def _parse_manual_fields(body: str) -> dict[str, Any]:
    """Combine fast-path regex + LLM natural-language extraction."""
    regex_found = _parse_manual_fields_regex(body)

    # Skip the LLM call entirely if the body is empty/trivial.
    if not body or len(body.strip()) < 4:
        return regex_found

    llm_found = await text_extract(prompt=_NL_MANUAL_PROMPT, text=body[:4000])
    if "_parse_error" in llm_found:
        return regex_found  # graceful fallback

    # Regex hits take precedence (user wrote explicit `key: value`).
    merged: dict[str, Any] = {}
    for k in _MANUAL_FIELD_KEYS:
        v = regex_found.get(k) or llm_found.get(k)
        if v not in (None, ""):
            merged[k] = v
    return merged


def _extract_text_body(msg: Message) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = (part.get("Content-Disposition") or "").lower()
            if ctype == "text/plain" and "attachment" not in disp:
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        return (msg.get_payload(decode=True) or b"").decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:  # noqa: BLE001
        return ""


def _extract_attachments(msg: Message) -> list[IncomingDocument]:
    docs: list[IncomingDocument] = []
    if not msg.is_multipart():
        return docs
    for part in msg.walk():
        disp = (part.get("Content-Disposition") or "").lower()
        filename = part.get_filename()
        if not filename or "attachment" not in disp:
            continue
        ctype = (part.get_content_type() or "").lower()
        if not (ctype.startswith(_ACCEPTED_MIME_PREFIXES) or ctype in _ACCEPTED_MIME_EXACT):
            logger.info("Skipping attachment %r (mime=%s)", filename, ctype)
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        docs.append(
            IncomingDocument(
                filename=filename,
                content_base64=base64.b64encode(payload).decode("ascii"),
                mime_type=ctype,
            )
        )
    return docs


def _sender_email(msg: Message) -> str:
    raw = msg.get("From", "")
    m = re.search(r"<([^>]+)>", raw)
    if m:
        return m.group(1).strip().lower()
    return raw.strip().lower()


async def _dispatch_result(
    store: Store,
    response,
    candidate_email: str,
    *,
    inbound_message_id: str | None = None,
    inbound_subject: str | None = None,
) -> None:
    user_id = response.user_id
    # mode='json' so enums (e.g., MaritalStatus.SINGLE) serialise to their `.value`
    # ("single") instead of leaking the repr through json.dumps(default=str).
    extracted_dict = response.extracted.model_dump(mode="json")
    name = response.extracted.name

    if response.status == ProcessStatus.COMPLETE:
        # Nothing missing — send a REVIEW email and wait for explicit confirmation
        # before writing to the final `candidates` table.
        await store.upsert_pending(
            user_id=user_id,
            email=candidate_email,
            last_status="awaiting_confirmation",
            missing_fields=[],
            validation_errors=[],
            extracted_so_far=extracted_dict,
            last_inbound_message_id=inbound_message_id,
            last_inbound_subject=inbound_subject,
            status="awaiting_confirmation",
        )
        try:
            await send_review(
                to=candidate_email,
                candidate_name=name,
                user_id=user_id,
                profile=extracted_dict,
                in_reply_to=inbound_message_id,
                reply_to_subject=inbound_subject,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("Review email failed: %s", exc)
        return

    # needs_info or invalid → upsert pending, send attempt-1 follow-up
    attempt = await store.upsert_pending(
        user_id=user_id,
        email=candidate_email,
        last_status=response.status.value,
        missing_fields=response.missing_fields,
        validation_errors=[v.model_dump() for v in response.validation_errors],
        extracted_so_far=extracted_dict,
        last_inbound_message_id=inbound_message_id,
        last_inbound_subject=inbound_subject,
    )
    try:
        await send_followup(
            to=candidate_email,
            attempt=attempt,
            candidate_name=name,
            user_id=user_id,
            missing_fields=response.missing_fields,
            validation_errors=[v.model_dump() for v in response.validation_errors],
            in_reply_to=inbound_message_id,
            reply_to_subject=inbound_subject,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Follow-up email failed: %s", exc)


# Strip quoted-reply lines (prefixed with `>`) so we judge confirmation on the
# user's own words, not the quoted review we sent them.
_QUOTED_LINE = re.compile(r"^\s*(?:>|On .+ wrote:|-- ?$)", re.MULTILINE)

_CONFIRM_PATTERNS = [
    re.compile(r"\b(confirm(?:ed|ing)?|confirmation)\b", re.IGNORECASE),
    re.compile(r"\b(approve(?:d)?|approval)\b", re.IGNORECASE),
    re.compile(r"\b(submit(?: it| this| my)?(?: now)?|please submit)\b", re.IGNORECASE),
    re.compile(r"\ball (?:looks?|is) (?:good|correct|fine|right|ok)\b", re.IGNORECASE),
    re.compile(r"\b(?:looks|seems) good\b", re.IGNORECASE),
    re.compile(r"\b(?:everything|all) (?:is )?correct\b", re.IGNORECASE),
    re.compile(r"^\s*(?:yes|yep|yeah|y)\s*[.!]?\s*$", re.IGNORECASE | re.MULTILINE),
]


def _looks_like_confirmation(body: str) -> bool:
    """Decide whether a reply is a CONFIRM (vs corrections / new info)."""
    if not body:
        return False
    # Strip quoted lines from prior emails
    clean_lines = []
    for line in body.splitlines():
        if line.lstrip().startswith(">"):
            continue
        if line.strip().lower().startswith(("on ", "from:")) and "wrote:" in line.lower():
            break  # Outlook/Gmail quote header — stop here
        clean_lines.append(line)
    clean = "\n".join(clean_lines).strip()
    if not clean:
        return False
    # If the user wrote ANY new key:value style line, treat as corrections.
    if re.search(r"^\s*\w+\s*[:=]\s*\S", clean, re.MULTILINE):
        return False
    return any(p.search(clean) for p in _CONFIRM_PATTERNS)


async def _process_message(store: Store, raw_bytes: bytes) -> None:
    msg = email.message_from_bytes(raw_bytes)
    sender = _sender_email(msg)
    if not sender:
        logger.warning("Could not determine sender; skipping")
        return

    inbound_message_id = (msg.get("Message-ID") or "").strip() or None
    inbound_subject = (msg.get("Subject") or "").strip() or None
    body = _extract_text_body(msg)

    # If the candidate is awaiting confirmation, check first whether this reply
    # is a CONFIRM (finalise) or corrections (re-process).
    pending = await store.get_pending(sender)
    if pending and pending.get("status") == "awaiting_confirmation":
        if _looks_like_confirmation(body):
            import json as _json
            try:
                extracted = _json.loads(pending["extracted_so_far"])
            except Exception:  # noqa: BLE001
                extracted = {}
            await store.upsert_candidate(extracted)
            await store.clear_pending(sender)
            try:
                await send_finalized(
                    to=sender,
                    candidate_name=extracted.get("name"),
                    user_id=sender,
                    in_reply_to=inbound_message_id,
                    reply_to_subject=inbound_subject,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Finalised email failed: %s", exc)
            logger.info("FINALISED candidate user_id=%s", sender)
            return
        # Otherwise: fall through, treat the email as corrections.

    manual = await _parse_manual_fields(body)
    docs = _extract_attachments(msg)
    if not docs and not manual:
        # Body-only email from someone who hasn't started yet —
        # send a friendly welcome listing what to send instead of silently skipping.
        if not pending:
            logger.info("Body-only email from %s; sending welcome", sender)
            try:
                await send_welcome(
                    to=sender,
                    user_id=sender,
                    in_reply_to=inbound_message_id,
                    reply_to_subject=inbound_subject,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Welcome email failed: %s", exc)
        else:
            logger.info("Body-only email from %s with existing pending; skipping", sender)
        return
    manual["email"] = sender

    # Carry forward what we already have from earlier attempts.
    prev = await store.get_pending(sender)
    previous_extracted = None
    attempt_number = 1
    if prev and prev.get("extracted_so_far"):
        try:
            import json as _json
            previous_extracted = _json.loads(prev["extracted_so_far"])
        except Exception:  # noqa: BLE001
            previous_extracted = None
        attempt_number = (prev.get("attempt_count") or 1)

    request = ProcessRequest(
        user_id=sender,
        email=sender,
        documents=docs,
        manual_fields=ManualFields(**{k: v for k, v in manual.items() if v}),
        attempt_number=attempt_number,
        previous_extracted=previous_extracted,
    )

    logger.info(
        "Processing inbound email from=%s docs=%d msg_id=%s",
        sender,
        len(docs),
        inbound_message_id,
    )
    response = await run_graph(request)
    logger.info(
        "Done from=%s status=%s missing=%d errors=%d",
        sender,
        response.status.value,
        len(response.missing_fields),
        len(response.validation_errors),
    )
    await _dispatch_result(
        store,
        response,
        sender,
        inbound_message_id=inbound_message_id,
        inbound_subject=inbound_subject,
    )


async def _poll_once(store: Store) -> int:
    """One IMAP polling pass. Returns number of messages processed."""
    client = aioimaplib.IMAP4_SSL(host=_IMAP_HOST, port=_IMAP_PORT)
    await client.wait_hello_from_server()
    try:
        await client.login(_IMAP_USER, _IMAP_PASSWORD)
        await client.select("INBOX")

        # Build the set of UIDs to process from two sources:
        #   1. UNSEEN emails whose subject contains our marker (fresh submissions).
        #   2. UNSEEN emails from anyone with an open pending_request (replies,
        #      whose subject is usually "Re: ...").
        uid_set: set[bytes] = set()

        async def _collect(criteria: str) -> None:
            r = await client.uid_search(criteria)
            if r.result == "OK" and r.lines:
                for u in (r.lines[0] or b"").split():
                    uid_set.add(u)

        if _SUBJECT_FILTER:
            await _collect(f'(UNSEEN SUBJECT "{_SUBJECT_FILTER}")')
        else:
            await _collect("UNSEEN")

        for sender in await store.list_pending_senders():
            # quote-escape: keep simple — pending senders are emails, no quotes
            await _collect(f'(UNSEEN FROM "{sender}")')

        if not uid_set:
            return 0
        uids = sorted(u.decode() for u in uid_set)
        logger.info("IMAP poll: %d UNSEEN message(s) to process", len(uids))

        processed = 0
        for uid in uids:
            fetch = await client.uid("fetch", uid, "(RFC822)")
            if fetch.result != "OK":
                logger.warning("Fetch failed for uid=%s", uid)
                continue
            # aioimaplib returns lines as a list; the raw message body is the
            # element that's `bytes` and not a small status line.
            raw = b""
            for line in fetch.lines:
                if isinstance(line, (bytes, bytearray)) and len(line) > 500:
                    raw = bytes(line)
                    break
            if not raw:
                logger.warning("Empty fetch payload for uid=%s", uid)
                continue
            try:
                await _process_message(store, raw)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Processing uid=%s failed: %s", uid, exc)
                continue
            # Mark seen only after successful processing
            await client.uid("store", uid, "+FLAGS", r"(\Seen)")
            processed += 1
        return processed
    finally:
        try:
            await client.logout()
        except Exception:  # noqa: BLE001
            pass


async def run_poller(store: Store) -> None:
    """Long-running poller task. Cancel to stop."""
    if not _IMAP_USER or not _IMAP_PASSWORD:
        logger.warning("IMAP_USER / IMAP_PASSWORD not configured; poller will idle")
        # Idle gracefully — keeps the task alive without spinning.
        while True:
            await asyncio.sleep(3600)

    logger.info(
        "Mail poller started: host=%s user=%s interval=%ds",
        _IMAP_HOST,
        _IMAP_USER,
        _POLL_INTERVAL,
    )
    while True:
        try:
            n = await _poll_once(store)
            if n:
                logger.info("Poll cycle processed %d message(s)", n)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("Poll cycle failed: %s", exc)
        await asyncio.sleep(_POLL_INTERVAL)
