"""Cross-validate extracted records across documents.

Checks:
  - Name consistency (fuzzy) across 10th, 12th, graduation, PG, Aadhaar, PAN
  - DOB consistency (exact match on ISO date) across the same set
  - Image quality flags surfaced by per-doc extractors
  - Wrong-document-type signals (e.g. declared 10th but classified as 12th)
"""
from __future__ import annotations

import logging
import re
from difflib import SequenceMatcher

from agent.schemas import AgentState, DocumentType, ValidationError

logger = logging.getLogger(__name__)


_IDENTITY_DOC_TYPES = {
    DocumentType.TENTH_MARKSHEET,
    DocumentType.TWELFTH_MARKSHEET,
    DocumentType.GRADUATION_MARKSHEET,
    DocumentType.POSTGRADUATION_MARKSHEET,
    DocumentType.AADHAAR,
    DocumentType.PAN,
}


def _normalize_name(name: str | None) -> str:
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"\b(mr|mrs|ms|miss|shri|smt|kumari|km)\.?\b", "", s)
    s = re.sub(r"[^a-z\s]", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, _normalize_name(a), _normalize_name(b)).ratio()


def cross_validate(state: AgentState) -> AgentState:
    errors: list[ValidationError] = []

    # Gather (doc_type, candidate_name, dob) for every successfully extracted identity doc.
    identity_records: list[tuple[DocumentType, str, str | None, str | None]] = []
    for filename, extraction in state.per_doc_extractions.items():
        doc_type = state.classified.get(filename, DocumentType.UNKNOWN)

        if "_parse_error" in extraction:
            errors.append(
                ValidationError(
                    code="extraction_failed",
                    docs_involved=[doc_type],
                    detail=f"Could not extract data from {filename}: {extraction['_parse_error']}",
                    severity="error",
                )
            )
            continue

        if doc_type in _IDENTITY_DOC_TYPES:
            identity_records.append(
                (
                    doc_type,
                    filename,
                    extraction.get("candidate_name"),
                    extraction.get("date_of_birth"),
                )
            )

        # Image quality surfaced by the extractor.
        quality_issues = extraction.get("quality_issues") or []
        if quality_issues:
            errors.append(
                ValidationError(
                    code="poor_image_quality",
                    docs_involved=[doc_type],
                    detail=f"{filename}: {', '.join(quality_issues)}",
                    severity="warning",
                )
            )

        # Low overall confidence -> ask for a clearer scan.
        confidence = extraction.get("confidence")
        if isinstance(confidence, (int, float)) and confidence < 0.4 and doc_type != DocumentType.UNKNOWN:
            errors.append(
                ValidationError(
                    code="low_extraction_confidence",
                    docs_involved=[doc_type],
                    detail=f"{filename}: extraction confidence {confidence:.2f} is below threshold",
                    severity="warning",
                )
            )

        # Photo / signature validity flag
        if doc_type in {DocumentType.PASSPORT_PHOTO, DocumentType.SIGNATURE}:
            if extraction.get("is_valid") is False:
                errors.append(
                    ValidationError(
                        code="invalid_photo_or_signature",
                        docs_involved=[doc_type],
                        detail=f"{filename}: {extraction.get('reason') or 'flagged as invalid'}",
                        severity="error",
                    )
                )

    # Wrong-doc-type: any document classified as UNKNOWN
    for filename, doc_type in state.classified.items():
        if doc_type == DocumentType.UNKNOWN:
            errors.append(
                ValidationError(
                    code="wrong_doc_type",
                    docs_involved=[DocumentType.UNKNOWN],
                    detail=f"{filename}: could not identify document type. Please re-upload the correct file.",
                    severity="error",
                )
            )

    # Name cross-check
    name_threshold = 0.75
    if len(identity_records) >= 2:
        names = [(dt, fn, n) for dt, fn, n, _ in identity_records if n]
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                sim = _name_similarity(names[i][2], names[j][2])
                if sim < name_threshold:
                    errors.append(
                        ValidationError(
                            code="name_mismatch",
                            docs_involved=[names[i][0], names[j][0]],
                            detail=(
                                f"Name on {names[i][1]} ({names[i][2]!r}) does not match "
                                f"name on {names[j][1]} ({names[j][2]!r}). "
                                f"Similarity={sim:.2f}. The documents may belong to different people."
                            ),
                            severity="error",
                        )
                    )

    # DOB cross-check (only when both sides have a value)
    dobs = [(dt, fn, d) for dt, fn, _, d in identity_records if d]
    for i in range(len(dobs)):
        for j in range(i + 1, len(dobs)):
            if dobs[i][2] != dobs[j][2]:
                errors.append(
                    ValidationError(
                        code="dob_mismatch",
                        docs_involved=[dobs[i][0], dobs[j][0]],
                        detail=(
                            f"DOB on {dobs[i][1]} ({dobs[i][2]}) does not match "
                            f"DOB on {dobs[j][1]} ({dobs[j][2]})."
                        ),
                        severity="error",
                    )
                )

    state.validation_errors.extend(errors)
    return state
