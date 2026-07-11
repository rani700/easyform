"""Classify each uploaded document into a DocumentType."""
from __future__ import annotations

import asyncio
import base64
import logging

from agent.nodes.llm import vision_extract
from agent.prompts import CLASSIFY_PROMPT
from agent.schemas import AgentState, DocumentType

logger = logging.getLogger(__name__)


async def _classify_one(filename: str, content_b64: str, mime_type: str) -> DocumentType:
    image_bytes = base64.b64decode(content_b64)
    result = await vision_extract(
        prompt=CLASSIFY_PROMPT,
        image_bytes=image_bytes,
        mime_type=mime_type,
        max_tokens=200,
    )
    raw_type = result.get("document_type", "unknown")
    try:
        return DocumentType(raw_type)
    except ValueError:
        logger.warning("Unknown document_type from classifier: %r for %s", raw_type, filename)
        return DocumentType.UNKNOWN


async def classify_documents(state: AgentState) -> AgentState:
    """Populate state.classified: filename -> DocumentType.

    Uses the declared_type if the caller supplied one AND it isn't UNKNOWN; otherwise calls the LLM.
    Runs all LLM calls concurrently.
    """
    pending: list[tuple[str, asyncio.Task[DocumentType]]] = []
    classified: dict[str, DocumentType] = {}

    for doc in state.request.documents:
        if doc.declared_type and doc.declared_type != DocumentType.UNKNOWN:
            classified[doc.filename] = doc.declared_type
        else:
            pending.append(
                (
                    doc.filename,
                    asyncio.create_task(
                        _classify_one(doc.filename, doc.content_base64, doc.mime_type)
                    ),
                )
            )

    for filename, task in pending:
        classified[filename] = await task

    state.classified = classified
    state.notes.append(
        f"Classified {len(classified)} documents: "
        + ", ".join(f"{f}={t.value}" for f, t in classified.items())
    )
    return state
