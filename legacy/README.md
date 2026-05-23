# legacy/

These artifacts are from the original n8n + Snowflake architecture. They have
been replaced by the in-process implementation (`agent/mail_poller.py`,
`agent/mail_sender.py`, `agent/scheduler.py`, `agent/store.py`) and are kept
here only for reference.

You do **not** need any of this to run EasyForm. Set `MAIL_ENABLED=true` and
the relevant `IMAP_*` / `SMTP_*` env vars on the FastAPI service instead.

- `n8n/main_workflow.json` — IMAP trigger → POST /process → branch on status
  → Snowflake INSERT / follow-up email + pending upsert.
- `n8n/retry_cron_workflow.json` — 30-min cron that re-emailed pending
  candidates and marked stale ones discarded.
- `snowflake/ddl.sql` — original Snowflake schema (CANDIDATES,
  PENDING_REQUESTS, DOCUMENTS_AUDIT). The SQLite equivalent now lives in
  `agent/store.py`.
