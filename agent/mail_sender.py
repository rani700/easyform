"""SMTP sender for follow-up / review / finalisation emails.

Emails are multipart/alternative:
  - text/plain (fallback for old clients)
  - text/html  (the pretty version with tables)

Outgoing emails carry In-Reply-To and References headers when a parent
Message-ID is supplied so Gmail threads the whole conversation.
"""
from __future__ import annotations

import logging
import os
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import aiosmtplib
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "email_templates"
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(enabled_extensions=("html", "j2")),
    trim_blocks=True,
    lstrip_blocks=True,
)


# ---------- Configuration & subject-thread helpers ----------

def _config() -> dict[str, Any]:
    return {
        "host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "mail_from": os.environ.get("MAIL_FROM") or os.environ.get("SMTP_USER", ""),
    }


def _threaded_subject(reply_to_subject: str | None, fallback: str) -> str:
    if reply_to_subject:
        s = reply_to_subject.strip()
        if not s.lower().startswith("re:"):
            s = "Re: " + s
        return s
    return fallback


# ---------- Categorising the missing list ----------

_DOC_LABELS = {
    "tenth_marksheet": "10th Marksheet",
    "twelfth_marksheet": "12th Marksheet",
    "graduation_marksheet": "Graduation Marksheet",
    "postgraduation_marksheet": "Post-Graduation Marksheet",
    "passport_photo": "Passport-size Photo",
    "signature": "Signature",
    "aadhaar_or_pan": "Aadhaar (or PAN) Card",
}
_LEVEL_LABELS = {
    "tenth": "10th",
    "twelfth": "12th",
    "graduation": "Graduation",
    "postgraduation": "Post-Graduation",
}
_FIELD_LABELS = {
    "specialization": "stream / specialization",
    "year_of_passing": "year of passing",
    "candidate_name": "candidate name on the marksheet",
    "institute_name": "institute name",
    "course_duration_years": "course duration (years)",
    "marital_status": "Marital Status (single / married / …)",
    "nationality": "Nationality",
    "caste": "Category / Caste (General / OBC / SC / ST / EWS)",
    "mobile_number": "Active Mobile Number",
    "disability_status": "Disability Status (or write None)",
    "correspondence_address": "Correspondence Address (where you currently reside)",
    "correspondence_pin_code": "Correspondence PIN Code",
    "permanent_address": "Permanent Address (extracted from Aadhaar)",
    "permanent_pin_code": "Permanent PIN Code (extracted from Aadhaar)",
    "name": "Full Name (extracted from documents)",
    "date_of_birth": "Date of Birth",
}


def categorize_missing(missing: list[str]) -> dict[str, list]:
    """Split a flat missing-fields list into three template-friendly buckets."""
    docs: list[str] = []
    gaps: list[dict[str, str]] = []   # doc uploaded, but a field couldn't be read
    personal: list[dict[str, str]] = []

    for m in missing:
        prefix, _, value = m.partition(":")
        if prefix == "document":
            docs.append(_DOC_LABELS.get(value, value))
        elif prefix == "extraction_gap":
            level, _, field = value.partition(".")
            gaps.append({
                "doc": f"{_LEVEL_LABELS.get(level, level)} marksheet",
                "field": _FIELD_LABELS.get(field, field),
                "key": f"{level}_{field}",
            })
        elif prefix == "field":
            if "." in value:
                level, _, field = value.partition(".")
                gaps.append({
                    "doc": _LEVEL_LABELS.get(level, level),
                    "field": _FIELD_LABELS.get(field, field),
                    "key": f"{level}_{field}",
                })
            else:
                personal.append({
                    "label": _FIELD_LABELS.get(value, value),
                    "key": value,
                })
    return {"docs": docs, "gaps": gaps, "personal": personal}


# ---------- Render + send helpers ----------

def _split_subject(rendered: str, fallback: str) -> tuple[str, str]:
    subject = ""
    body_lines: list[str] = []
    for i, line in enumerate(rendered.splitlines()):
        if i == 0 and line.lower().startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
        else:
            body_lines.append(line)
    return (subject or fallback, "\n".join(body_lines).lstrip("\n"))


def _render(name: str, fallback_subject: str, **ctx) -> tuple[str, str]:
    rendered = _env.get_template(name).render(**ctx)
    return _split_subject(rendered, fallback_subject)


async def send_email(
    *,
    to: str,
    subject: str,
    plain_body: str,
    html_body: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> None:
    cfg = _config()
    if not cfg["user"] or not cfg["password"]:
        raise RuntimeError("SMTP_USER / SMTP_PASSWORD not configured")

    msg = EmailMessage()
    msg["From"] = cfg["mail_from"]
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    msg.set_content(plain_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    logger.info(
        "Sending email to=%s subject=%r html=%s threaded=%s",
        to,
        subject,
        bool(html_body),
        bool(in_reply_to),
    )
    await aiosmtplib.send(
        msg,
        hostname=cfg["host"],
        port=cfg["port"],
        username=cfg["user"],
        password=cfg["password"],
        start_tls=True,
        timeout=30,
    )


# ---------- Public API ----------

async def send_followup(
    *,
    to: str,
    attempt: int,
    candidate_name: str | None,
    user_id: str,
    missing_fields: list[str],
    validation_errors: list[dict[str, Any]],
    in_reply_to: str | None = None,
    reply_to_subject: str | None = None,
) -> None:
    n = max(1, min(3, attempt))
    cats = categorize_missing(missing_fields)
    plain_subject, plain_body = _render(
        f"attempt_{n}.j2",
        "EasyForm — action needed",
        candidate_name=candidate_name,
        user_id=user_id,
        missing_fields=missing_fields,
        validation_errors=validation_errors,
        cats=cats,
    )
    _, html_body = _render(
        f"attempt_{n}.html.j2",
        plain_subject,
        candidate_name=candidate_name,
        user_id=user_id,
        cats=cats,
        validation_errors=validation_errors,
    )
    subject = _threaded_subject(reply_to_subject, plain_subject)
    await send_email(
        to=to,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        in_reply_to=in_reply_to,
    )


async def send_review(
    *,
    to: str,
    candidate_name: str | None,
    user_id: str,
    profile: dict[str, Any],
    in_reply_to: str | None = None,
    reply_to_subject: str | None = None,
) -> None:
    plain_subject, plain_body = _render(
        "review.j2",
        "EasyForm — please review and confirm your details",
        candidate_name=candidate_name,
        user_id=user_id,
        p=profile,
    )
    _, html_body = _render(
        "review.html.j2",
        plain_subject,
        candidate_name=candidate_name,
        user_id=user_id,
        p=profile,
    )
    subject = _threaded_subject(reply_to_subject, plain_subject)
    await send_email(
        to=to,
        subject=subject,
        plain_body=plain_body,
        html_body=html_body,
        in_reply_to=in_reply_to,
    )


async def send_finalized(
    *,
    to: str,
    candidate_name: str | None,
    user_id: str,
    in_reply_to: str | None = None,
    reply_to_subject: str | None = None,
) -> None:
    template_subject = "EasyForm — your application has been submitted"
    subject = _threaded_subject(reply_to_subject, template_subject)
    plain = (
        f"Hi {candidate_name or 'there'},\n\n"
        "Your application has been finalised with the details you confirmed. "
        "We will use this information to fill your government exam application.\n\n"
        f"Reference ID: {user_id}\n\n"
        "— EasyForm"
    )
    html = f"""<!DOCTYPE html><html><body style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:640px;margin:0 auto;padding:24px;color:#0f172a">
<div style="background:linear-gradient(135deg,#4f46e5,#9333ea);padding:24px;border-radius:14px 14px 0 0;color:#fff">
  <h1 style="margin:0;font-size:22px">✅ Application submitted</h1>
</div>
<div style="padding:24px;background:#fff;border:1px solid #e2e8f0;border-top:0;border-radius:0 0 14px 14px">
  <p>Hi {candidate_name or 'there'},</p>
  <p>Your application has been finalised with the details you confirmed.
     We will use this information to fill your government exam application.</p>
  <p style="font-size:12px;color:#64748b;margin-top:24px">Reference ID: {user_id}</p>
  <p style="font-size:12px;color:#64748b">— EasyForm</p>
</div></body></html>"""
    await send_email(to=to, subject=subject, plain_body=plain, html_body=html, in_reply_to=in_reply_to)


# Backwards-compat shim
async def send_confirmation(**kwargs):
    return await send_finalized(**kwargs)
