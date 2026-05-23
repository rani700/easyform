"""Merge per-doc extractions + manual fields into a single CandidateProfile."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from agent.schemas import (
    AgentState,
    CandidateProfile,
    DocumentType,
    EducationRecord,
)


def _age_from_dob(dob_iso: str | None) -> int | None:
    if not dob_iso:
        return None
    try:
        d = date.fromisoformat(dob_iso)
    except ValueError:
        return None
    today = date.today()
    return today.year - d.year - ((today.month, today.day) < (d.month, d.day))


def _first_non_null(*values: Any) -> Any:
    for v in values:
        if v not in (None, "", []):
            return v
    return None


def _build_education(extraction: dict[str, Any] | None) -> EducationRecord | None:
    if not extraction or "_parse_error" in extraction or "_skipped" in extraction:
        return None
    try:
        return EducationRecord(**{k: v for k, v in extraction.items() if not k.startswith("_")})
    except Exception:
        return None


def merge_with_manual(state: AgentState) -> AgentState:
    req = state.request
    classified = state.classified
    extractions = state.per_doc_extractions

    by_type: dict[DocumentType, dict[str, Any]] = {}
    for filename, doc_type in classified.items():
        ex = extractions.get(filename)
        if ex and "_parse_error" not in ex and "_skipped" not in ex:
            by_type.setdefault(doc_type, ex)  # keep first occurrence

    aadhaar = by_type.get(DocumentType.AADHAAR, {})
    pan = by_type.get(DocumentType.PAN, {})
    tenth_ex = by_type.get(DocumentType.TENTH_MARKSHEET, {})
    twelfth_ex = by_type.get(DocumentType.TWELFTH_MARKSHEET, {})
    grad_ex = by_type.get(DocumentType.GRADUATION_MARKSHEET, {})
    pg_ex = by_type.get(DocumentType.POSTGRADUATION_MARKSHEET, {})
    photo = by_type.get(DocumentType.PASSPORT_PHOTO, {})
    signature = by_type.get(DocumentType.SIGNATURE, {})

    # Identity prefers Aadhaar > PAN > graduation > 12th > 10th (assumed most current first)
    name = _first_non_null(
        aadhaar.get("candidate_name"),
        pan.get("candidate_name"),
        grad_ex.get("candidate_name"),
        twelfth_ex.get("candidate_name"),
        tenth_ex.get("candidate_name"),
    )
    dob = _first_non_null(
        aadhaar.get("date_of_birth"),
        pan.get("date_of_birth"),
        tenth_ex.get("date_of_birth"),
        twelfth_ex.get("date_of_birth"),
        grad_ex.get("date_of_birth"),
    )
    father = _first_non_null(
        pan.get("father_name"),
        tenth_ex.get("father_name"),
        twelfth_ex.get("father_name"),
        grad_ex.get("father_name"),
    )
    mother = _first_non_null(
        tenth_ex.get("mother_name"),
        twelfth_ex.get("mother_name"),
        grad_ex.get("mother_name"),
    )

    mf = req.manual_fields

    profile = CandidateProfile(
        user_id=req.user_id,
        email=req.email,
        name=name,
        father_name=father,
        mother_name=mother,
        date_of_birth=dob,
        age=_age_from_dob(dob),
        gender=aadhaar.get("gender"),
        permanent_address=aadhaar.get("permanent_address"),
        permanent_pin_code=aadhaar.get("pin_code"),
        correspondence_address=mf.correspondence_address or aadhaar.get("permanent_address"),
        correspondence_pin_code=mf.correspondence_pin_code or aadhaar.get("pin_code"),
        marital_status=mf.marital_status,
        nationality=mf.nationality,
        caste=mf.caste,
        mobile_number=mf.mobile_number,
        disability_status=mf.disability_status,
        tenth=_build_education(tenth_ex),
        twelfth=_build_education(twelfth_ex),
        graduation=_build_education(grad_ex),
        postgraduation=_build_education(pg_ex),
        passport_photo_valid=bool(photo.get("is_valid")),
        signature_valid=bool(signature.get("is_valid")),
        aadhaar_present=DocumentType.AADHAAR in by_type,
        pan_present=DocumentType.PAN in by_type,
        processed_at=datetime.utcnow(),
    )

    # Carry forward anything that was confirmed in a prior attempt and is now missing here.
    _EDU_FIELDS = {"tenth", "twelfth", "graduation", "postgraduation"}
    if req.previous_extracted:
        for field, prev_val in req.previous_extracted.items():
            cur_val = getattr(profile, field, None)
            if cur_val in (None, "", False) and prev_val not in (None, "", False):
                # Education sub-records come back as plain dicts (serialised JSON);
                # re-hydrate them so `missing` detection can read their fields.
                if field in _EDU_FIELDS and isinstance(prev_val, dict):
                    try:
                        prev_val = EducationRecord(
                            **{k: v for k, v in prev_val.items() if not k.startswith("_")}
                        )
                    except Exception:
                        continue
                try:
                    setattr(profile, field, prev_val)
                except Exception:
                    pass

    # Apply manual stream hints AFTER carry-forward — by now the education
    # records exist (either from this email or carried forward), so the hint
    # can actually fill a missing specialization field.
    for level, hint in (
        ("tenth", mf.tenth_specialization),
        ("twelfth", mf.twelfth_specialization),
        ("graduation", mf.graduation_specialization),
        ("postgraduation", mf.postgraduation_specialization),
    ):
        if not hint:
            continue
        rec = getattr(profile, level, None)
        if rec is not None and not getattr(rec, "specialization", None):
            try:
                rec.specialization = hint
            except Exception:
                pass

    state.profile = profile
    return state
