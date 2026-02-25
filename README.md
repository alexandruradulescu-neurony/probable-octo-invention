# RecruitFlow

AI-powered recruiting pipeline built with Django 5.2 and the Anthropic Claude API.

---

## Project Structure

```
recruitflow/                  ← Project root (manage.py lives here)
├── recruitflow/              ← Django project package (settings, urls, wsgi, asgi)
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── candidates/               ← Candidate profiles & contact info
├── positions/                ← Job positions / openings
├── applications/             ← Candidate ↔ position linkage
├── calls/                    ← AI phone call sessions (ElevenLabs)
├── evaluations/              ← AI-generated candidate evaluations
├── messaging/                ← WhatsApp messaging via Whapi
├── cvs/                      ← CV upload, storage & PDF parsing
├── webhooks/                 ← Inbound webhooks (Whapi, ElevenLabs, etc.)
├── prompts/                  ← Prompt template management for Claude
├── scheduler/                ← APScheduler job registration & management
├── requirements.txt
├── .env.example              ← Copy to .env and fill in real values
└── README.md
```

---

## Prerequisites

- Python 3.13+
- PostgreSQL 15+

---

## Local Setup

### 1. Clone & enter the project

```bash
git clone <repo-url>
cd recruitflow
```

### 2. Create & activate a virtual environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and fill in all required values (see the comments in `.env.example`).

### 5. Create the PostgreSQL database

```sql
CREATE USER recruitflow_user WITH PASSWORD 'yourpassword';
CREATE DATABASE recruitflow_db OWNER recruitflow_user;
```

Update `DATABASE_URL` in `.env` to match.

### 6. Apply migrations

```bash
python manage.py migrate
```

### 7. Create a superuser

```bash
python manage.py createsuperuser
```

### 8. Run the development server

```bash
python manage.py runserver 8010
```

Visit `http://127.0.0.1:8010/admin/` to verify.

> **ngrok**: point your ngrok tunnel at port 8010 to expose the app at `https://recrutopiaaibot.ngrok.app`:
> ```bash
> ngrok http --url=recrutopiaaibot.ngrok.app 8010
> ```

---

## Environment Variables Reference

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key — keep this secret in production |
| `DEBUG` | `True` for development, `False` for production |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hostnames (include your ngrok domain) |
| `PORT` | Dev server port (default: `8010`) |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated full origin URLs trusted for CSRF — must include `https://your-ngrok-domain` |
| `DATABASE_URL` | PostgreSQL connection string |
| `ANTHROPIC_API_KEY` | Anthropic Claude API key |
| `ELEVENLABS_API_KEY` | ElevenLabs API key for AI voice calls |
| `ELEVENLABS_VOICE_ID` | ElevenLabs voice ID to use for calls |
| `WHAPI_API_KEY` | Whapi API key for WhatsApp messaging |
| `WHAPI_BASE_URL` | Whapi base URL (default: `https://gate.whapi.cloud`) |
| `GMAIL_CLIENT_ID` | Google OAuth2 client ID |
| `GMAIL_CLIENT_SECRET` | Google OAuth2 client secret |
| `GMAIL_REDIRECT_URI` | OAuth2 redirect URI (must match Google Cloud Console) |
| `GMAIL_CREDENTIALS_FILE` | Path to downloaded `credentials.json` |
| `GMAIL_TOKEN_FILE` | Path where the OAuth2 token will be stored |
| `APSCHEDULER_RUN_NOW_TIMEOUT` | APScheduler timeout in seconds (default: 25) |

---

## Database Models

| App | Model(s) | Key Fields |
|---|---|---|
| `positions` | `Position` | `status` (open/paused/closed), prompt fields, call scheduling config |
| `candidates` | `Candidate` | `phone`/`email` (indexed), `meta_lead_id` (unique), `form_answers` (JSONField) |
| `applications` | `Application` | `status` (18-state flow), `qualified`, `score`, unique_together (candidate, position) |
| `applications` | `StatusChange` | `from_status`, `to_status`, `changed_by` (FK User), `note` (audit trail) |
| `calls` | `Call` | `eleven_labs_conversation_id` (unique), `status`, `transcript`, `attempt_number` |
| `evaluations` | `LLMEvaluation` | `outcome` (4 values), `score`, `raw_response` (JSONField), callback/human flags |
| `messaging` | `Message` | `channel` (email/whatsapp), `message_type`, `status`, `external_id` |
| `cvs` | `CVUpload` | `source`, `match_method`, `needs_review` flag, `file_path` |
| `cvs` | `UnmatchedInbound` | `channel`, `raw_payload` (JSONField), resolution tracking |
| `prompts` | `PromptTemplate` | `meta_prompt`, `is_active`, `version` (audit trail) |

### Application Status Flow (18 states)

```
pending_call → call_queued → call_in_progress → call_completed → scoring
  scoring → qualified → awaiting_cv → cv_followup_1 → cv_followup_2 → cv_overdue → closed
  scoring → not_qualified → awaiting_cv_rejected → cv_received_rejected → closed
  [any awaiting stage] → cv_received → closed
  call_in_progress → call_failed
  scoring → callback_scheduled → (re-enters call_queued)
  scoring → needs_human → (recruiter handles manually)
  any → closed
```

---

## Frontend Screens

| Screen | URL | Description |
|---|---|---|
| Dashboard | `/` | Summary metrics, activity feed, attention-required items |
| Positions | `/positions/` | List, create, edit positions with "Generate Prompts" AJAX button |
| Candidates | `/candidates/` | Searchable list, detail with editable contact + notes, CSV import |
| Applications | `/applications/` | Filterable list with "Trigger Calls" bulk action, detail with timeline |
| Application Detail | `/applications/<pk>/` | Actions: status override, add note, schedule callback, trigger follow-up, manual CV upload. Full timeline of status changes. |
| CV Inbox | `/cvs/` | Unmatched and needs-review tabs with manual assignment forms |
| Prompt Templates | `/prompts/` | CRUD with "Test Generate" preview (admin section) |

---

## Services & Integrations

| Service | Module | Description |
|---|---|---|
| `ElevenLabsService` | `calls/services.py` | Outbound AI phone calls via ElevenLabs ConvAI |
| `ClaudeService` | `evaluations/services.py` | Prompt generation + call transcript evaluation |
| `WhapiService` | `messaging/services.py` | Send WhatsApp messages via Whapi REST API |
| `GmailService` | `messaging/services.py` | Send emails + poll inbox via Gmail API (OAuth2) |
| `process_inbound_cv` | `cvs/services.py` | CV smart matching cascade (5 priority levels) |
| `send_cv_request` | `messaging/services.py` | Post-evaluation CV request orchestrator |
| `send_followup` | `messaging/services.py` | Timed follow-up orchestrator |

---

## Scheduled Jobs

| Job | Interval | Description |
|---|---|---|
| `process_call_queue` | 5 min | Process call_queued and callback_scheduled applications |
| `sync_stuck_calls` | 10 min | Poll ElevenLabs for stuck calls |
| `check_cv_followups` | 60 min | Send follow-ups for qualified candidates past their interval |
| `close_stale_rejected` | 24 hrs | Close rejected applications past CV timeout |
| `poll_cv_inbox` | 15 min | Poll Gmail inbox for CV attachments |

Start the scheduler alongside the Django server:

```bash
python manage.py run_scheduler
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 5.2 |
| Database | PostgreSQL + psycopg2-binary |
| AI / LLM | Anthropic Claude (`anthropic`) |
| Voice | ElevenLabs |
| Messaging | Whapi (WhatsApp) |
| Email | Gmail API (OAuth2) |
| PDF Parsing | pdfplumber |
| Scheduling | django-apscheduler |
| Frontend | Bootstrap 5.3 + Bootstrap Icons |
| Config | django-environ |
