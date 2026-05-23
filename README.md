# EasyForm

An AI agent (LangChain + LangGraph + GPT-4o vision) that auto-fills Indian government
exam applications from candidate-submitted documents. Self-contained — one Python
service handles the **web portal**, the **API**, the **IMAP inbox**, **follow-up
emails**, and the **SQLite store**. No n8n, no Snowflake required.

```
                                          ┌───────────────────────────────┐
   Candidate sends email                  │  EasyForm service (1 pod)      │
   (marksheets, Aadhaar/PAN,     IMAP     │                                │
    photo, signature, manual    ────────▶ │   mail_poller   ┌───────────┐  │
    fields in body)                       │       │         │  SQLite    │  │
                                          │       ▼         │  store     │  │
   OR uses the web portal      HTTP       │   LangGraph ───▶│ (PVC)      │  │
   at https://<host>/         ────────▶   │   pipeline      │            │  │
                                          │       │         │ candidates │  │
                                          │       ▼         │ pending    │  │
   ◀── follow-up email   SMTP   ────────  │   mail_sender   └───────────┘  │
   (or confirmation)                      │                                │
                                          │   scheduler — every 30 min:    │
                                          │   re-email at 6h gaps, max 3   │
                                          │   attempts, then discard       │
                                          └───────────────────────────────┘
```

## What's in this repo

| Path | Purpose |
| --- | --- |
| `agent/` | FastAPI app + LangGraph state machine + vision prompts |
| `agent/store.py` | SQLite store: candidates, pending_requests, documents_audit |
| `agent/mail_poller.py` | IMAP poller — turns inbound emails into agent runs |
| `agent/mail_sender.py` | SMTP follow-up sender (uses templates in `email_templates/`) |
| `agent/scheduler.py` | 30-min retry scan; bumps attempt counter or discards |
| `web/index.html` | Web portal (served at `/`) |
| `email_templates/` | Jinja templates for attempt-1/2/3 follow-up emails |
| `samples/` | Synthetic test documents + `run_demo.py` test client |
| `Dockerfile` | Container image |
| `.env.example` | All env vars (OpenAI + IMAP/SMTP + cadence) |
| `legacy/` | Original n8n workflows + Snowflake DDL (no longer needed) |

## Two ways for candidates to submit

1. **Web portal** — open `https://<host>/` in a browser. Drag-drop the documents,
   fill the form, review the extracted details, download the confirmed JSON.
2. **By email** — candidates email the documents (and the manual fields in the
   body) to the configured inbox. The service polls the inbox, processes them,
   and replies — with the filled details if complete, or a list of what's still
   needed. If they don't reply within 6 hours, the service follows up; after 3
   attempts the request is discarded.

Both paths run through the same LangGraph pipeline.

## What the agent extracts

From documents:
- **Name, DOB, father/mother name, gender** — cross-validated across all marksheets + Aadhaar + PAN
- **Permanent address + PIN code** — from Aadhaar
- **Education records (10th, 12th, graduation, post-graduation)**: institute, year of passing, CGPA/percentage, specialization, course duration, full/part-time

From the email body (or the portal form):
- `marital_status`, `nationality`, `caste`, `mobile_number`
- `correspondence_address`, `correspondence_pin_code`, `disability_status`

Validation performed:
1. **Name cross-check** — fuzzy match across all identity docs (threshold 0.75)
2. **DOB cross-check** — exact match
3. **Document-type classification** — each upload is independently classified; flags mismatches
4. **Image-quality check** — surfaced from GPT-4o's per-doc confidence + quality_issues list

## Running locally

```bash
cd EasyForm
cp .env.example .env             # set OPENAI_API_KEY; set MAIL_ENABLED=true for the email flow
python -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
set -a && source .env && set +a
uvicorn agent.app:app --reload
```

Then:
- **http://localhost:8000/** — web portal
- **http://localhost:8000/admin/status** — background-task health + DB counts
- **http://localhost:8000/health** — liveness probe

Or with Docker:
```bash
docker build -t easyform .
docker run --rm -p 8000:8000 -v $PWD/data:/data --env-file .env easyform
```

(`-v $PWD/data:/data` persists SQLite outside the container.)

## API contract

### `POST /process` (JSON)

Request:
```json
{
  "user_id": "candidate@example.com",
  "email": "candidate@example.com",
  "attempt_number": 1,
  "manual_fields": {
    "marital_status": "single",
    "nationality": "Indian",
    "caste": "General",
    "mobile_number": "9876543210",
    "correspondence_address": "Flat 12, Building A, ...",
    "correspondence_pin_code": "110001",
    "disability_status": "None"
  },
  "documents": [
    {"filename": "10th.pdf", "content_base64": "...", "mime_type": "application/pdf"}
  ],
  "previous_extracted": null
}
```

Response (`200`):
```json
{
  "status": "complete | needs_info | invalid",
  "user_id": "...",
  "extracted": { "name": "...", "...": "..." },
  "missing_fields": ["field:caste", "document:signature"],
  "validation_errors": [
    {"code": "name_mismatch", "docs_involved": ["aadhaar","graduation_marksheet"], "detail": "...", "severity": "error"}
  ],
  "documents_received": {"tenth_marksheet": true, "...": false},
  "notes": ["Classified 6 documents: ..."]
}
```

`status` semantics:
- **complete**: written to `candidates`; confirmation email sent.
- **needs_info**: required field/document is absent — upserted into `pending_requests`; attempt-1 follow-up email sent.
- **invalid**: blocking validation error (wrong doc, name mismatch, invalid photo) — same flow as `needs_info`.

`POST /process/multipart` is identical but takes real multipart uploads (the web portal uses this).

## Email format candidates should use

Plain-text body with one `key: value` per line for the fields not on the documents:
```
marital_status: Single
nationality: Indian
caste: General
mobile_number: 9876543210
correspondence_address: Flat 12, Building A, Sector 5, Noida
correspondence_pin_code: 201301
disability_status: None
```
Attach: 10th/12th/graduation marksheets, Aadhaar (or PAN), passport-size photo,
signature. PDF and image attachments are both accepted.

## Configuration

All via env vars (see `.env.example` for the full list):

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | required for vision extraction |
| `OPENAI_MODEL` | `gpt-4o` | vision-capable model |
| `MAIL_ENABLED` | `false` | turn the IMAP poller + scheduler on |
| `IMAP_HOST` / `PORT` / `USER` / `PASSWORD` | Gmail defaults | Gmail App Password works on `587/STARTTLS` SMTP and `993/SSL` IMAP |
| `SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `MAIL_FROM` | — | for follow-up emails |
| `POLL_INTERVAL_SECONDS` | `120` | inbox check cadence |
| `RETRY_INTERVAL_HOURS` | `6` | wait between follow-up emails |
| `SCHEDULER_INTERVAL_SECONDS` | `1800` | retry scan cadence |
| `SQLITE_PATH` | `/data/easyform.db` | DB file; mount a PVC at `/data` in k8s |

## Concurrency

- FastAPI + asyncio handle many simultaneous requests per pod.
- The mail poller processes one inbox poll at a time; per-message processing
  inside the pipeline runs concurrent OpenAI calls capped by a semaphore (default 4).
- All retry/discard state lives in `pending_requests` keyed on the candidate's
  email — multiple emails from the same person are idempotent.

## File formats

Images (PNG/JPEG/WebP) are sent to GPT-4o directly. **PDFs** are converted to PNG
by `agent/nodes/docprep.py` (PyMuPDF) before extraction; multi-page PDFs are
stacked vertically (first 4 pages). OpenAI vision only accepts image MIME types,
so this conversion step is required.

## Roadmap / not built

- Face match between passport photo and Aadhaar photo (skipped per requirements)
- HTML-formatted follow-up emails
- Multi-replica deployment (current store is SQLite; switch to PostgreSQL if you scale out)
