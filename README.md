# EasyForm

[![CI](https://github.com/rani700/easyform/actions/workflows/release-and-publish.yml/badge.svg)](https://github.com/rani700/easyform/actions/workflows/release-and-publish.yml)
[![Release](https://img.shields.io/github/v/release/rani700/easyform)](https://github.com/rani700/easyform/releases)
[![License](https://img.shields.io/github/license/rani700/easyform)](LICENSE)
[![Live demo](https://img.shields.io/badge/live_demo-easyform.codeshare.co.in-4f46e5)](https://easyform.codeshare.co.in/)

An AI agent (LangChain + LangGraph + GPT-4o vision) that auto-fills Indian
government exam applications from candidate-submitted documents. **One Python
service** handles the web portal, REST API, IMAP inbox polling, follow-up
emails, review-and-confirm loop, and PostgreSQL persistence. A separate
**Metabase** instance gives admins dashboards and a SQL editor on top of the
same database.

## Live deployment

| URL | Purpose |
| --- | --- |
| **https://easyform.codeshare.co.in/** | Candidate web portal + agent API |
| **https://easyform-admin.codeshare.co.in/** | Metabase admin dashboard (login-protected) |
| Inbound mailbox | `ranishwe8a@gmail.com` (subject must contain `EasyForm`) |

Deployed on a homelab Kubernetes cluster via ArgoCD; GitHub Actions builds and
publishes the agent image to GHCR on every push to `main`.

### Try it yourself

Anyone can use the live system, two ways:

1. **📧 Email** — send a mail to **`ranishwe8a@gmail.com`** with **`EasyForm`**
   anywhere in the subject (any casing works — `easyform`, `EasyForm applying`,
   etc.). Attach your documents and/or write your details in plain English in
   the body. Even an empty "hi" gets a welcome email listing everything needed.
   The agent replies within ~2 minutes, follows up on what's missing, and
   finalises once you reply `CONFIRM`.
2. **🌐 Web form** — open **https://easyform.codeshare.co.in/**, drag-drop your
   documents, fill the details form, review the extracted profile, and submit.

## Architecture

<img alt="End-to-end architecture — candidate email and web-portal paths flow through the LangGraph pipeline (classify → extract → validate → merge → detect missing) into PostgreSQL, with Metabase for admin analytics" src="https://github.com/user-attachments/assets/b8b114e9-d3e8-45dc-9548-81efcb5f8d2c" />

*End-to-end flow: both candidate paths (email via IMAP poller, web portal via
FastAPI) run the same LangGraph pipeline; results land in PostgreSQL, replies
go out over SMTP, and admins query the same database through Metabase.*

<details>
<summary>Text-only version of the diagram</summary>

```
                                          ┌────────────────────────────────────────┐
   Candidate web portal      ──HTTP─▶     │  EasyForm agent pod                    │
   (easyform.codeshare.co.in)             │  ┌──────────────────────────────────┐  │
                                          │  │ FastAPI                          │  │
                                          │  │  • / (portal)                    │  │
   Candidate email           ──IMAP─▶     │  │  • /process, /process/multipart  │  │
   (subject: "EasyForm")    polls 120s    │  │  • /admin/status                 │  │
                                          │  │                                  │  │
                                          │  │ background tasks                 │  │
                                          │  │  • mail_poller                   │  │
                                          │  │  • scheduler (every 30 min)      │  │
                                          │  └──────────┬───────────────────────┘  │
                                          │             │                          │
                                          │             ▼                          │
                                          │     LangGraph pipeline                 │
                                          │     classify → extract → validate      │
                                          │            → merge → detect_missing    │
                                          │             │                          │
   ◀────HTML email────SMTP────────────    │     ┌───────┴────────┐                 │
   (welcome / followup / review /         │     │  mail_sender   │                 │
   finalised — threaded via               │     └────────────────┘                 │
   In-Reply-To)                           │             │                          │
                                          └─────────────┼──────────────────────────┘
                                                        ▼
                                                ┌───────────────┐
                                                │ PostgreSQL    │
                                                │ (same ns)     │
                                                │  candidates   │◀─ Metabase ─┐
                                                │  pending_     │             │
                                                │   requests    │             │
                                                │  documents_   │     easyform-admin
                                                │   audit       │     .codeshare.co.in
                                                └───────────────┘
```

</details>

## What's in this repo

| Path | Purpose |
| --- | --- |
| `agent/app.py` | FastAPI app — portal + `/process` endpoints, lifespan boots poller + scheduler |
| `agent/graph.py` | LangGraph state machine: classify → extract → validate → merge → detect_missing |
| `agent/nodes/` | Per-stage node implementations + GPT-4o vision wrapper + PDF→image preprocess |
| `agent/store.py` | **PostgreSQL** store (asyncpg pool): candidates, pending_requests, documents_audit |
| `agent/mail_poller.py` | IMAP polling + natural-language manual-field parsing + welcome/review/confirm routing |
| `agent/mail_sender.py` | SMTP sender (multipart/alternative) — plain + HTML templates, threading headers |
| `agent/scheduler.py` | Every 30 min: re-emails pending users at 6h gaps, max 3 attempts, then discard; purges discarded rows after 7 days |
| `agent/prompts/` | Per-doc-type GPT-4o extraction prompts (includes 12th percentage auto-compute) |
| `web/index.html` | Web portal — drag-drop upload UI served at `/` |
| `email_templates/` | Jinja templates — both plain (`*.j2`) and HTML (`*.html.j2`) for every email type |
| `samples/` | Synthetic test documents + `run_demo.py` test client |
| `Dockerfile` | Container image (Python 3.11-slim, copies agent + web + email_templates) |
| `.env.example` | All env vars (OpenAI + IMAP/SMTP + Postgres + cadence) |

## How a candidate uses it

**Either**:
1. **Web portal** — drag-drop documents, fill the form, review the editable
   table, download the confirmed JSON.
2. **Email** — send to the configured inbox with `EasyForm` in the subject and
   either / both: documents attached, personal details in the body (natural
   English is fine — the LLM parses it).

Both paths run through the same LangGraph pipeline. The email path is fully
interactive: the agent replies, follows up, takes corrections, and only
finalises after the candidate explicitly confirms.

### Email conversation flow

```
candidate sends email (subject: EasyForm…)
              │
              ▼
       ┌─────────────────┐
       │ what arrived?    │
       └────────┬────────┘
                │
   ┌────────────┼────────────┬─────────────────────┐
   │            │            │                     │
no docs +     missing      complete profile     just confirms
no manual     items                              (CONFIRM)
   │            │            │                     │
   ▼            ▼            ▼                     ▼
welcome     follow-up    review email          finalise →
email     (categorised)   (HTML table)         candidates
(table     • docs we need                       table + ack
of what's  • details we
needed)      couldn't read
           • personal fields
```

Scheduler re-sends the follow-up at **6h intervals**, max **3 attempts**, then
marks the request **discarded**. Discarded requests are kept for
**`DISCARD_RETENTION_DAYS`** (default 7 days) and then deleted — after that the
address is treated as brand-new and gets the welcome email again.

## What the agent extracts

**From documents**
- Name, DOB, father / mother name, gender — cross-validated across all
  marksheets + Aadhaar + PAN
- Permanent address + PIN code — from Aadhaar
- Education records (10th, 12th, graduation, post-graduation): institute,
  board / university, year of passing, CGPA / percentage, specialization,
  course duration, full/part-time
- Passport-photo and signature validity (vision-based)

**From the email body (natural language)** — the LLM understands prose
- Marital status, nationality, caste, mobile number
- Correspondence address + PIN code, disability status
- Education stream / specialization hints when not readable from the marksheet

**Computed**
- Age (from DOB)
- 12th percentage — if the marksheet doesn't show a consolidated %, the agent
  computes `(sum of top-5 subject marks / 500) × 100` and flags this in
  `quality_issues`
- Course Period — `year_of_passing − course_duration_years` to year_of_passing

## Validation

1. **Name cross-check** — fuzzy match across all identity documents (threshold 0.75)
2. **DOB cross-check** — exact match
3. **Document-type classification** — each upload is independently classified;
   flags mismatches and missing types
4. **Image-quality check** — GPT-4o's per-doc confidence + `quality_issues` list
5. **Photo / signature validity** — vision check

`missing_fields` are categorised into:
- `document:tenth_marksheet` — a whole document is missing
- `extraction_gap:twelfth.specialization` — document is there but a specific
  field couldn't be read (the follow-up email says: "send a clearer scan or
  reply with the value")
- `field:caste` — personal detail not yet provided

## Running locally

```bash
cd EasyForm
cp .env.example .env             # fill in OPENAI_API_KEY and DATABASE_URL
python -m venv .venv && source .venv/bin/activate
pip install -r agent/requirements.txt
set -a && source .env && set +a
uvicorn agent.app:app --reload
```

Then:
- **http://localhost:8000/** — web portal
- **http://localhost:8000/admin/status** — background-task health + DB counts
- **http://localhost:8000/health** — liveness probe
- **http://localhost:8000/docs** — auto-generated Swagger UI

You'll need a PostgreSQL reachable at `DATABASE_URL`. The schema is created
automatically on first connect. For local dev, run a quick Postgres in Docker:

```bash
docker run --rm -d --name easyform-pg -p 5432:5432 \
  -e POSTGRES_USER=easyform -e POSTGRES_PASSWORD=devpw \
  -e POSTGRES_DB=easyform postgres:16-alpine

# then set in .env:
# DATABASE_URL=postgresql://easyform:devpw@localhost:5432/easyform
```

### Docker

```bash
docker build -t easyform .
docker run --rm -p 8000:8000 --env-file .env easyform
```

## API contract

### `POST /process` (JSON)

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
    "correspondence_address": "Flat 12, Sector 5, Noida",
    "correspondence_pin_code": "201301",
    "disability_status": "None",
    "twelfth_specialization": "Science (PCM)"
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
  "extracted": { "name": "...", "tenth": {...}, "twelfth": {...} },
  "missing_fields": [
    "document:signature",
    "extraction_gap:twelfth.specialization",
    "field:mobile_number"
  ],
  "validation_errors": [
    {"code": "name_mismatch", "docs_involved": ["aadhaar","graduation_marksheet"], "detail": "...", "severity": "error"}
  ],
  "documents_received": {"tenth_marksheet": true, "...": false},
  "notes": ["Classified 6 documents: ..."]
}
```

`POST /process/multipart` is identical but accepts real multipart uploads (the
web portal uses this).

### Email-flow status semantics

- **complete + already awaiting confirmation** → finalise (write `candidates`,
  send "Application submitted" email, clear pending)
- **complete + new submission** → send **review email** (full HTML profile
  table, ask for `CONFIRM`), set status `awaiting_confirmation`
- **needs_info** → send categorised follow-up email, attempt counter starts
- **invalid** (blocking validation error: name mismatch, wrong doc, invalid
  photo) → same flow as `needs_info`

## Email format candidates can use

Natural English works fine — the LLM extracts what it can:
> "Hi! I'm single, Indian, OBC. My mobile is 9876543210. My correspondence
> address is Flat 12, Sector 5, Noida — 201301. My 12th stream was Science
> (PCM). No disability."

Attach: 10th / 12th / graduation marksheets, Aadhaar (or PAN), passport-size
photo, signature. PDF and image attachments are both accepted. Multi-page
PDFs get rendered and stacked (first 4 pages).

Strict `key: value` lines still work and take precedence if present.

## Configuration

All via env vars (see `.env.example`).

| Variable | Default | Notes |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | required for vision extraction |
| `OPENAI_MODEL` | `gpt-4o` | vision-capable model |
| `DATABASE_URL` | — | PostgreSQL DSN (`postgresql://user:pass@host:port/db`) |
| `MAIL_ENABLED` | `false` | turn the IMAP poller + scheduler on |
| `IMAP_HOST` / `PORT` / `USER` / `PASSWORD` | `imap.gmail.com:993` | Gmail App Password works |
| `SMTP_HOST` / `PORT` / `USER` / `PASSWORD` / `MAIL_FROM` | `smtp.gmail.com:587` | STARTTLS |
| `MAIL_SUBJECT_FILTER` | `EasyForm` | only inbox subjects containing this are processed (plus replies from known pending senders) |
| `POLL_INTERVAL_SECONDS` | `120` | inbox check cadence |
| `RETRY_INTERVAL_HOURS` | `6` | wait between follow-up emails |
| `SCHEDULER_INTERVAL_SECONDS` | `1800` | retry scan cadence |
| `DISCARD_RETENTION_DAYS` | `7` | how long discarded requests are kept before deletion (then the address starts fresh) |
| `LLM_MAX_RETRIES` | `5` | retry attempts on OpenAI 429/5xx |
| `LLM_MAX_CONCURRENCY` | `4` | semaphore cap on simultaneous OpenAI calls |

## Concurrency model

- FastAPI + asyncio handles many simultaneous HTTP requests per pod.
- PostgreSQL connection pool (asyncpg) — multiple candidates writing at once
  is fully supported.
- Per-message processing inside the pipeline runs OpenAI calls concurrently,
  capped by a semaphore (default 4) to avoid 429s.
- All retry / discard state lives in `pending_requests` keyed on the
  candidate's email — multiple emails from the same person are idempotent.

## File formats

Images (PNG / JPEG / WebP) are sent to GPT-4o directly. **PDFs** are converted
to PNG by `agent/nodes/docprep.py` (PyMuPDF) before extraction; multi-page
PDFs are stacked vertically (first 4 pages). OpenAI vision only accepts image
MIME types, so this conversion step is required.

## Admin & analytics

Two ways to access the data:

1. **Metabase** at `https://easyform-admin.codeshare.co.in/` — login-protected
   web dashboard. SQL editor, charts, scheduled exports. Connected to the
   same PostgreSQL `easyform` database.
2. **Direct SQL** — port-forward Postgres via SSH and connect any client
   (DBeaver / TablePlus / psql):
   ```bash
   ssh -L 15432:postgres.easyform.svc.cluster.local:5432 vishal@<homelab-host>
   # then connect to localhost:15432, db: easyform
   ```

## Roadmap / not built

- Face match between passport photo and Aadhaar photo (skipped per requirements)
- Bounce handling — currently dead addresses just burn 3 retry attempts before
  being discarded; could be wired to mark hard-bounces as `discarded` immediately
- Multi-replica deployment of the agent — would need IMAP polling
  coordination (e.g. only one replica holds a leader lease)

