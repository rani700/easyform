# EasyForm

An AI agent (LangChain + LangGraph + GPT-4o vision) that auto-fills Indian government exam
applications from candidate-submitted documents. Designed to run **headlessly behind n8n** —
candidates email their documents, n8n forwards them to the agent, and confirmed profiles land
in Snowflake. Missing or invalid uploads trigger up to 3 follow-up emails (6 h apart) before
the request is discarded.

```
   Candidate email                                  ┌────────────────────────┐
   (10th, 12th, grad/PG,                            │  Snowflake.CANDIDATES  │
    Aadhaar/PAN, photo,         needs_info /        │  (final, confirmed)    │
    signature, manual fields)   invalid             └─────────▲──────────────┘
        │                            │                        │ complete
        ▼                            ▼                        │
   ┌─────────┐  HTTP   ┌────────────────────┐   needs_info  ┌─┴────────────────────┐
   │  n8n    │ ──────▶ │  EasyForm Agent    │ ────────────▶ │  Snowflake.          │
   │ (IMAP)  │ ◀────── │  FastAPI+LangGraph │   /invalid    │  PENDING_REQUESTS    │
   └─────────┘   JSON  └────────────────────┘               └──────────────────────┘
        │                                                            │
        │           every 30 min cron                                 │
        ├────────────────────────────────────────────────────────────┘
        │   re-email at 6h gaps, max 3 attempts, then discard
        ▼
   Follow-up email to candidate
```

## What's in this repo

| Path | Purpose |
| --- | --- |
| `agent/` | FastAPI service + LangGraph state machine + per-document vision prompts |
| `web/index.html` | Web portal — drag-drop upload UI, served by FastAPI at `/` |
| `samples/` | Synthetic test documents + `run_demo.py` test client |
| `Dockerfile` | Container image for the agent |
| `snowflake/ddl.sql` | Schema for `CANDIDATES`, `PENDING_REQUESTS`, `DOCUMENTS_AUDIT` + helper views |
| `n8n/main_workflow.json` | IMAP-triggered intake workflow (importable) |
| `n8n/retry_cron_workflow.json` | 30-min cron that re-sends reminders and discards stale users |
| `email_templates/` | Reference text for follow-up emails (the n8n workflows inline the same content) |
| `.env.example` | Required environment variables |

## Two ways to use it

1. **Web portal** — open `http://localhost:8000/` in a browser. Upload documents,
   fill the extra fields, review the extracted details in an editable table, download
   the confirmed JSON. Good for individual / interactive use.
2. **n8n + email** — candidates email their documents; n8n calls the agent and writes
   to Snowflake. Good for automated batch processing. See the n8n section below.

Both use the same agent.

## What the agent extracts

From documents:
- **Name, DOB, father/mother name, gender** — cross-validated across all marksheets + Aadhaar + PAN
- **Permanent address + PIN code** — from Aadhaar
- **Education records (10th, 12th, graduation, post-graduation)**: institute, year of passing, CGPA/percentage, specialization, course duration, full/part-time

From email body (key: value lines):
- `marital_status`, `nationality`, `caste`, `mobile_number`
- `correspondence_address`, `correspondence_pin_code`, `disability_status`

Validation performed:
1. **Name cross-check** — fuzzy match across all identity docs (threshold 0.75)
2. **DOB cross-check** — exact match
3. **Document-type classification** — each upload is independently classified; flags mismatches
4. **Image-quality check** — surfaced from GPT-4o's per-doc confidence + quality_issues list

## Running the agent locally

```bash
cd EasyForm
cp .env.example .env             # then edit .env, set OPENAI_API_KEY
docker build -t easyform-agent .
docker run --rm -p 8000:8000 --env-file .env easyform-agent
```

Then open **http://localhost:8000/** for the web portal, or smoke-test the API:
```bash
curl http://localhost:8000/health
```

Process a request with curl (multipart):
```bash
curl -X POST http://localhost:8000/process/multipart \
  -F user_id=candidate@example.com \
  -F email=candidate@example.com \
  -F 'manual_fields_json={"marital_status":"single","nationality":"Indian","caste":"General","mobile_number":"9876543210","correspondence_address":"...","correspondence_pin_code":"110001","disability_status":"None"}' \
  -F files=@samples/10th.pdf \
  -F files=@samples/12th.pdf \
  -F files=@samples/grad.pdf \
  -F files=@samples/aadhaar.jpg \
  -F files=@samples/photo.jpg \
  -F files=@samples/signature.jpg
```

## API contract

### `POST /process` (JSON — preferred for n8n)

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
- **complete**: ready to write to `CANDIDATES`
- **needs_info**: some required fields/documents are absent — email user, upsert into `PENDING_REQUESTS`
- **invalid**: blocking validation error (wrong doc, name mismatch, invalid photo) — same flow as needs_info, error list explains what's wrong

## Snowflake setup

```bash
snowsql -f snowflake/ddl.sql
```

This creates `EASYFORM.APP.CANDIDATES`, `PENDING_REQUESTS`, `DOCUMENTS_AUDIT`,
and two helper views: `PENDING_DUE_FOR_RETRY`, `PENDING_TO_DISCARD`.

## n8n setup

1. **Credentials** — create three in n8n: an `IMAP` for the inbox, an `SMTP` for outbound,
   and a `Snowflake` account. Note their IDs.
2. **Environment variables** — set on the n8n instance:
   - `EASYFORM_AGENT_URL` — e.g. `http://easyform-agent:8000`
   - `EASYFORM_FROM_EMAIL` — the from-address for follow-up emails
3. **Import workflows** — `n8n/main_workflow.json` and `n8n/retry_cron_workflow.json`.
4. **Replace credential IDs** — open each Snowflake/IMAP/SMTP node and swap the placeholder
   `REPLACE_WITH_*_CREDENTIAL_ID` for the IDs you noted in step 1.
5. **Activate** both workflows.

### Email format candidates should use

Plain text body with one field per line:
```
marital_status: Single
nationality: Indian
caste: General
mobile_number: 9876543210
correspondence_address: Flat 12, Building A, Sector 5, Noida
correspondence_pin_code: 201301
disability_status: None
```
Attachments: the marksheets, Aadhaar, PAN, passport-size photo, signature.

## How concurrency works

- The agent is **stateless**; FastAPI + uvicorn (2 workers in the Dockerfile) handles
  parallel requests cleanly.
- n8n queues IMAP-triggered executions, one per email; multiple candidates are processed in
  parallel up to your n8n concurrency limit.
- Retry state lives in `PENDING_REQUESTS` keyed on user email — no collisions even if two
  emails for the same user race (Snowflake `MERGE` keeps it idempotent).

## Local development without Docker

```bash
cd EasyForm
python -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
export OPENAI_API_KEY=sk-...
uvicorn agent.app:app --reload
```

## File formats

Images (PNG/JPEG/WebP) are sent to GPT-4o directly. **PDFs** are auto-converted to
images by `agent/nodes/docprep.py` (PyMuPDF) before extraction — multi-page PDFs are
rendered and stacked vertically (first 4 pages). OpenAI's vision API only accepts image
MIME types, so this conversion step is required.

## Roadmap / not built

- Face match between passport photo and Aadhaar photo (skipped per requirements — easy to
  add with `face_recognition` or DeepFace if needed later)
- HTML-formatted follow-up emails
