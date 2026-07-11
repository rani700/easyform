"""FastAPI service exposing the EasyForm document-extraction agent.

Endpoints:
  GET  /              Web portal (document upload UI).
  POST /process       Agent entrypoint (JSON, base64 documents + manual fields).
  POST /process/multipart  Same as /process for real multipart uploads.
  GET  /health        Liveness probe.
  GET  /admin/status  Background-service health: mail poller + scheduler tasks.

Background services started on startup (when MAIL_ENABLED=true):
  - Mail poller: IMAP inbox -> agent graph -> store + reply.
  - Retry scheduler: every 30 min, re-emails pending users; discards after 3 tries.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import pathlib
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse

_WEB_DIR = pathlib.Path(__file__).resolve().parent.parent / "web"

from agent.graph import run_graph
from agent.mail_poller import run_poller
from agent.scheduler import run_scheduler
from agent.schemas import (
    IncomingDocument,
    ManualFields,
    ProcessRequest,
    ProcessResponse,
)
from agent.store import Store

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
logger = logging.getLogger("easyform.api")

_store: Store | None = None
_bg_tasks: list[asyncio.Task] = []


def _mail_enabled() -> bool:
    return os.environ.get("MAIL_ENABLED", "false").lower() in ("1", "true", "yes")


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _store
    _store = Store()
    await _store.init()

    if _mail_enabled():
        logger.info("Starting background services (mail poller + scheduler)")
        _bg_tasks.append(asyncio.create_task(run_poller(_store), name="mail_poller"))
        _bg_tasks.append(asyncio.create_task(run_scheduler(_store), name="scheduler"))
    else:
        logger.info("MAIL_ENABLED is not set; background services NOT started")

    try:
        yield
    finally:
        for t in _bg_tasks:
            t.cancel()
        for t in _bg_tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        _bg_tasks.clear()


app = FastAPI(
    title="EasyForm Agent",
    version="0.2.0",
    description="Self-contained document-extraction service: web portal, /process API, "
                "IMAP inbox poller, follow-up emailer, PostgreSQL store.",
    lifespan=lifespan,
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


@app.get("/admin/status")
async def admin_status() -> dict:
    """Background-service health + counts from the store."""
    bg = {t.get_name(): ("running" if not t.done() else "stopped") for t in _bg_tasks}

    counts: dict[str, int | str] = {}
    if _store is not None:
        try:
            async with _store._conn() as conn:  # noqa: SLF001
                for label, sql in (
                    ("candidates", "SELECT COUNT(*)::int AS c FROM candidates"),
                    ("pending_awaiting", "SELECT COUNT(*)::int AS c FROM pending_requests WHERE status='awaiting_user'"),
                    ("pending_confirmation", "SELECT COUNT(*)::int AS c FROM pending_requests WHERE status='awaiting_confirmation'"),
                    ("pending_discarded", "SELECT COUNT(*)::int AS c FROM pending_requests WHERE status='discarded'"),
                ):
                    row = await conn.fetchrow(sql)
                    counts[label] = row["c"] if row else 0
        except Exception as exc:  # noqa: BLE001
            counts["error"] = str(exc)

    return {
        "mail_enabled": _mail_enabled(),
        "background_tasks": bg,
        "store": counts,
    }


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
    """Same as /process but accepts real multipart uploads."""
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
