"""Extract structured fields per document using GPT-4o vision."""
from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

from agent.nodes.llm import vision_extract
from agent.prompts import PROMPT_BY_TYPE
from agent.schemas import (
    AadhaarRecord,
    AgentState,
    DocumentType,
    EducationRecord,
    PANRecord,
    PhotoRecord,
)

logger = logging.getLogger(__name__)


_MODEL_BY_TYPE: dict[DocumentType, type[Any]] = {
    DocumentType.TENTH_MARKSHEET: EducationRecord,
    DocumentType.TWELFTH_MARKSHEET: EducationRecord,
    DocumentType.GRADUATION_MARKSHEET: EducationRecord,
    DocumentType.POSTGRADUATION_MARKSHEET: EducationRecord,
    DocumentType.AADHAAR: AadhaarRecord,
    DocumentType.PAN: PANRecord,
    DocumentType.PASSPORT_PHOTO: PhotoRecord,
    DocumentType.SIGNATURE: PhotoRecord,
}


async def _extract_one(filename: str, content_b64: str, mime_type: str, doc_type: DocumentType):
    if doc_type == DocumentType.UNKNOWN:
        return {"_skipped": "unknown_type"}

    prompt = PROMPT_BY_TYPE.get(doc_type)
    if not prompt:
        return {"_skipped": f"no_prompt_for_{doc_type.value}"}

    image_bytes = base64.b64decode(content_b64)
    raw = await vision_extract(prompt=prompt, image_bytes=image_bytes, mime_type=mime_type)

    if "_parse_error" in raw:
        return raw

    model_cls = _MODEL_BY_TYPE.get(doc_type)
    if model_cls is None:
        return raw
    try:
        return model_cls(**raw).model_dump()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Validation failed for %s as %s: %s", filename, doc_type.value, exc)
        return {"_parse_error": f"pydantic_validation_failed: {exc}", "_raw": raw}


async def extract_per_doc(state: AgentState) -> AgentState:
    """Run vision extraction for every classified doc, in parallel."""
    tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
    for doc in state.request.documents:
        doc_type = state.classified.get(doc.filename, DocumentType.UNKNOWN)
        tasks[doc.filename] = asyncio.create_task(
            _extract_one(doc.filename, doc.content_base64, doc.mime_type, doc_type)
        )

    extractions: dict[str, Any] = {}
    for filename, task in tasks.items():
        extractions[filename] = await task

    state.per_doc_extractions = extractions
    return state
