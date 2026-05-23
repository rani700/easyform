"""Detect which required fields/documents are still missing on the merged profile.

The merged profile carries forward whatever was extracted in previous attempts,
so document/field presence is judged against the profile — not the current
email's attachments — to avoid re-requesting things the candidate already sent.
"""
from __future__ import annotations

from agent.schemas import AgentState

REQUIRED_FIELDS_FROM_DOCS = [
    "name",
    "date_of_birth",
    "permanent_address",
    "permanent_pin_code",
]

REQUIRED_MANUAL_FIELDS = [
    "marital_status",
    "nationality",
    "caste",
    "mobile_number",
    "disability_status",
    "correspondence_address",
    "correspondence_pin_code",
]

REQUIRED_EDUCATION_DETAILS = {
    "tenth":   ["candidate_name", "year_of_passing", "institute_name"],
    "twelfth": ["candidate_name", "year_of_passing", "institute_name", "specialization"],
    "graduation": [
        "candidate_name", "year_of_passing", "institute_name", "specialization",
        "course_duration_years",
    ],
}


def _get(record, field):
    """Read a field from an EducationRecord OR a plain dict (carried forward)."""
    if record is None:
        return None
    if isinstance(record, dict):
        return record.get(field)
    return getattr(record, field, None)


def detect_missing(state: AgentState) -> AgentState:
    profile = state.profile
    missing: list[str] = []

    # Required documents — judged against the profile (carry-forward aware).
    if profile.tenth is None:
        missing.append("document:tenth_marksheet")
    if profile.twelfth is None:
        missing.append("document:twelfth_marksheet")
    if profile.graduation is None:
        missing.append("document:graduation_marksheet")
    if not profile.passport_photo_valid:
        missing.append("document:passport_photo")
    if not profile.signature_valid:
        missing.append("document:signature")
    if not (profile.aadhaar_present or profile.pan_present):
        missing.append("document:aadhaar_or_pan")

    # Top-level fields derived from docs
    for field in REQUIRED_FIELDS_FROM_DOCS:
        if not getattr(profile, field, None):
            missing.append(f"field:{field}")

    # Manual fields
    for field in REQUIRED_MANUAL_FIELDS:
        if not getattr(profile, field, None):
            missing.append(f"field:{field}")

    # Education sub-fields. When the document IS present but a field couldn't be
    # extracted, emit it as `extraction_gap:` so the email can say "we got your
    # 12th marksheet but couldn't read the stream" instead of treating it like a
    # missing document.
    for level, fields in REQUIRED_EDUCATION_DETAILS.items():
        record = getattr(profile, level, None)
        if record is None:
            continue  # whole doc was flagged above as document:
        for f in fields:
            if not _get(record, f):
                missing.append(f"extraction_gap:{level}.{f}")

    state.missing_fields = missing
    return state
