"""Detect which required fields/documents are still missing after extraction + merge."""
from __future__ import annotations

from agent.schemas import AgentState, DocumentType

# Documents that MUST be present for a complete application.
REQUIRED_DOCS = [
    DocumentType.TENTH_MARKSHEET,
    DocumentType.TWELFTH_MARKSHEET,
    DocumentType.GRADUATION_MARKSHEET,
    DocumentType.PASSPORT_PHOTO,
    DocumentType.SIGNATURE,
    DocumentType.AADHAAR,  # OR PAN, handled below
]

# Fields that MUST be filled (either from docs or manual).
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
    "tenth": ["candidate_name", "year_of_passing", "institute_name"],
    "twelfth": ["candidate_name", "year_of_passing", "institute_name", "specialization"],
    "graduation": [
        "candidate_name",
        "year_of_passing",
        "institute_name",
        "specialization",
        "course_duration_years",
    ],
}


def detect_missing(state: AgentState) -> AgentState:
    profile = state.profile
    missing: list[str] = []

    # Required documents
    present_types = set(state.classified.values())
    for required in REQUIRED_DOCS:
        if required == DocumentType.AADHAAR:
            if DocumentType.AADHAAR not in present_types and DocumentType.PAN not in present_types:
                missing.append("document:aadhaar_or_pan")
        elif required not in present_types:
            missing.append(f"document:{required.value}")

    # Top-level fields derived from docs
    for field in REQUIRED_FIELDS_FROM_DOCS:
        if not getattr(profile, field, None):
            missing.append(f"field:{field}")

    # Manual fields
    for field in REQUIRED_MANUAL_FIELDS:
        if not getattr(profile, field, None):
            missing.append(f"field:{field}")

    # Education sub-fields
    for level, fields in REQUIRED_EDUCATION_DETAILS.items():
        record = getattr(profile, level, None)
        if record is None:
            # Already covered by the missing-doc check above for tenth/twelfth/graduation.
            continue
        for f in fields:
            if not getattr(record, f, None):
                missing.append(f"field:{level}.{f}")

    state.missing_fields = missing
    return state
