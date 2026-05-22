"""Format the final ProcessResponse from AgentState."""
from __future__ import annotations

from agent.schemas import (
    AgentState,
    DocumentType,
    ProcessResponse,
    ProcessStatus,
)


def format_response(state: AgentState) -> ProcessResponse:
    has_blocking_errors = any(e.severity == "error" for e in state.validation_errors)
    has_missing = bool(state.missing_fields)

    if has_blocking_errors:
        status = ProcessStatus.INVALID
    elif has_missing:
        status = ProcessStatus.NEEDS_INFO
    else:
        status = ProcessStatus.COMPLETE

    documents_received = {dt.value: False for dt in DocumentType if dt != DocumentType.UNKNOWN}
    for doc_type in state.classified.values():
        if doc_type != DocumentType.UNKNOWN:
            documents_received[doc_type.value] = True

    return ProcessResponse(
        status=status,
        user_id=state.request.user_id,
        extracted=state.profile,
        missing_fields=state.missing_fields,
        validation_errors=state.validation_errors,
        documents_received=documents_received,
        notes=state.notes,
    )
