"""FastAPI service exposing the EasyForm document-extraction agent.

Endpoints:
  GET  /              Web portal (document upload UI).
  POST /process       Main agent entrypoint. Accepts JSON with base64 documents + manual fields.
  POST /process/multipart  Convenience endpoint for multipart/form-data uploads.
  GET  /health        Liveness probe.

n8n typically calls /process (JSON) so it can include manual fields cleanly.
The web portal at / calls /process/multipart.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import pathlib
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

_WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

from agent.graph import run_graph
from agent.schemas import (
    IncomingDocument,
    ManualFields,
    ProcessRequest,
    ProcessResponse,
)

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("easyform.api")

app = FastAPI(
    title="EasyForm Agent",
    version="0.1.0",
    description="Document-extraction agent for Indian government exam form auto-fill (n8n-driven).",
)


@app.get("/", include_in_schema=False)
async def portal() -> FileResponse:
    """Serve the web portal."""
    index = _WEB_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Web portal not found.")
    return FileResponse(index, media_type="text/html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process", response_model=ProcessResponse)
async def process(request: ProcessRequest) -> ProcessResponse:
    if not request.documents:
        raise HTTPException(status_code=400, detail="At least one document must be provided.")
    if not os.environ.get("OPENAI_API_KEY"):
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not set on the server.")
    logger.info(
        "Processing user_id=%s email=%s docs=%d attempt=%d",
        request.user_id,
        request.email,
        len(request.documents),
        request.attempt_number,
    )
    try:
        response = await run_graph(request)
    except Exception as exc:
        logger.exception("Graph execution failed")
        raise HTTPException(status_code=500, detail=f"Agent failure: {exc}") from exc
    logger.info(
        "Done user_id=%s status=%s missing=%d errors=%d",
        request.user_id,
        response.status.value,
        len(response.missing_fields),
        len(response.validation_errors),
    )
    return response


@app.post("/process/multipart", response_model=ProcessResponse)
async def process_multipart(
    user_id: Annotated[str, Form()],
    email: Annotated[str, Form()],
    manual_fields_json: Annotated[str, Form()] = "{}",
    attempt_number: Annotated[int, Form()] = 1,
    files: Annotated[list[UploadFile], File()] = None,
) -> ProcessResponse:
    """Same as /process but accepts real multipart uploads (handy for curl/Postman testing).

    n8n's HTTP node usually finds it easier to POST JSON, so /process is preferred there.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded.")

    try:
        manual = ManualFields(**json.loads(manual_fields_json or "{}"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Invalid manual_fields_json: {exc}") from exc

    docs: list[IncomingDocument] = []
    for f in files:
        body = await f.read()
        docs.append(
            IncomingDocument(
                filename=f.filename or "unknown",
                content_base64=base64.b64encode(body).decode("ascii"),
                mime_type=f.content_type or "application/octet-stream",
            )
        )

    request = ProcessRequest(
        user_id=user_id,
        email=email,
        documents=docs,
        manual_fields=manual,
        attempt_number=attempt_number,
    )
    return await process(request)


@app.exception_handler(Exception)
async def _unhandled(_, exc: Exception):
    logger.exception("Unhandled exception")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )
