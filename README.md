# RecruitFlow

AI-powered recruiting pipeline built with Django 5.2 and the Anthropic Claude API.

---

## Project Structure

```
recruitflow/                  ← Project root (manage.py lives here)
├── recruitflow/              ← Django project package (settings, urls, wsgi, asgi)
│   ├── settings.py
│   ├── urls.py
│   ├── context_processors.py ← Sidebar counts (positions, candidates, applications, unread messages)
│   ├── views.py              ← Dashboard + GlobalSearchView
│   └── text_utils.py         ← Shared text helpers (strip_json_fence, build_full_name, etc.)
├── candidates/               ← Candidate profiles, CSV import, shared phone/email lookup helpers
├── positions/                ← Job positions; per-section prompt auto-generation via Claude
├── applications/             ← Candidate↔position linkage, 18-state status machine, status audit trail
├── calls/                    ← AI phone call sessions (ElevenLabs ConvAI)
├── evaluations/              ← LLM evaluation (ClaudeService: generate_section + evaluate_call)
├── messaging/                ← Message (outbound), CandidateReply (inbound), MessageTemplate models
├── cvs/                      ← CVUpload, UnmatchedInbound models; CV smart-matching service
├── webhooks/                 ← Inbound webhook views (ElevenLabs, Whapi)
├── prompts/                  ← PromptTemplate model (per-section meta-prompts)
├── config/                   ← Gmail OAuth2 settings page, app configuration
├── scheduler/                ← APScheduler job registration & all background jobs
├── static/css/styles.css     ← Custom design system CSS
├── templates/                ← All Django HTML templates
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

Edit `.env` and fill in all required values (see comments in `.env.example`).

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

### 7. Seed default message templates

```bash
python manage.py seed_message_templates
```

This creates 10 default `MessageTemplate` records (5 message types × 2 channels). Safe to re-run.

### 8. Create a superuser

```bash
python manage.py createsuperuser
```

### 9. Run the development server

```bash
python manage.py runserver 8010
```

Visit `http://127.0.0.1:8010/` to verify.

> **ngrok**: expose the app at your ngrok domain for webhook testing:
> ```bash
> ngrok http --url=recrutopiaaibot.ngrok.app 8010
> ```

### 10. Run the background scheduler (separate terminal)

```bash
python manage.py run_scheduler
```

---

## Environment Variables Reference

| Variable | Required | Description |
|---|---|---|
| `SECRET_KEY` | ✓ | Django secret key — keep secret in production |
| `DEBUG` | ✓ | `True` for development, `False` for production |
| `ALLOWED_HOSTS` | ✓ | Comma-separated hostnames (include ngrok domain) |
| `PORT` | | Dev server port (default: `8010`) |
| `CSRF_TRUSTED_ORIGINS` | ✓ | Full origin URLs trusted for CSRF — include `https://your-ngrok-domain` |
| `DATABASE_URL` | ✓ | PostgreSQL connection string |
| `TIME_ZONE` | | Django/scheduler timezone (default: `Europe/Bucharest`) |
| `APSCHEDULER_TIMEZONE` | | APScheduler timezone — must match `TIME_ZONE` |
| `ANTHROPIC_API_KEY` | ✓ | Anthropic Claude API key |
| `ANTHROPIC_MODEL` | | Main Claude model (default: `claude-sonnet-4-6`) |
| `ANTHROPIC_FAST_MODEL` | | Fast Claude model for CV parsing (default: `claude-haiku-4-5`) |
| `ANTHROPIC_MAX_TOKENS` | | Max tokens for Claude responses (default: `8192`; increase if prompts truncate) |
| `ELEVENLABS_API_KEY` | ✓ | ElevenLabs API key |
| `ELEVENLABS_AGENT_ID` | ✓ | ElevenLabs ConvAI agent ID |
| `ELEVENLABS_PHONE_NUMBER_ID` | ✓ | ElevenLabs outbound phone number ID |
| `ELEVENLABS_WEBHOOK_SECRET` | ✓ | Shared secret for ElevenLabs webhook validation |
| `WHAPI_TOKEN` | ✓ | Whapi channel token |
| `WHAPI_API_URL` | | Whapi base URL (default: `https://gate.whapi.cloud`) |
| `WHAPI_WEBHOOK_SECRET` | | Whapi webhook token (optional; skipped in DEBUG mode if empty) |
| `GOOGLE_CLIENT_ID` | ✓ | Google OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | ✓ | Google OAuth2 client secret |
| `GOOGLE_REFRESH_TOKEN` | ✓ | Google OAuth2 refresh token (generated via one-time flow) |
| `GOOGLE_REDIRECT_URI` | | OAuth2 redirect URI (must match Google Cloud Console) |
| `GMAIL_INBOX_LABEL` | | Gmail label to poll (default: `CVs`) |
| `GMAIL_PROCESSED_LABEL` | | Gmail label applied after processing (default: `CVs-Processed`) |
| `GMAIL_POLL_ENABLED` | | Enable Gmail polling job (default: `True`) |
| `GMAIL_POLL_MINUTES` | | Polling interval in minutes (default: `15`) |
| `MEDIA_ROOT` | | Local media storage path (default: `media/`) |

---

## Database Models

| App | Model | Key Fields |
|---|---|---|
| `positions` | `Position` | `status` (open/paused/closed), `system_prompt`, `first_message`, `qualification_prompt`, call scheduling config |
| `candidates` | `Candidate` | `phone`/`email` (indexed), `meta_lead_id` (unique), `form_answers` (JSONField) |
| `applications` | `Application` | `status` (18-state flow), `qualified`, `score`, unique_together (candidate, position) |
| `applications` | `StatusChange` | `from_status`, `to_status`, `changed_by` (FK User), `note` (audit trail) |
| `calls` | `Call` | `eleven_labs_conversation_id` (unique), `status`, `transcript`, `attempt_number` |
| `evaluations` | `LLMEvaluation` | `outcome` (4 values), `score`, `raw_response` (JSONField), callback/human flags |
| `messaging` | `Message` | `channel` (email/whatsapp), `message_type`, `status`, `external_id` — outbound only |
| `messaging` | `CandidateReply` | `channel`, `sender`, `body`, `is_read`, `candidate` FK, `application` FK — inbound only |
| `messaging` | `MessageTemplate` | `message_type`, `channel`, `subject`, `body` (with placeholders), `is_active` — unique per type/channel |
| `cvs` | `CVUpload` | `source`, `match_method`, `needs_review` flag, `file_path` |
| `cvs` | `UnmatchedInbound` | `channel`, `raw_payload` (JSONField), resolution tracking |
| `prompts` | `PromptTemplate` | `section` (system_prompt/first_message/qualification_prompt), `meta_prompt`, `is_active`, `version` |

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
| Positions | `/positions/` | List, create, edit positions |
| Position Form | `/positions/create/` or `/<pk>/edit/` | "Generate All" (3 sequential Claude calls) + individual "Regenerate" buttons per prompt field |
| Candidates | `/candidates/` | Searchable list, detail with editable contact + notes, CSV import |
| Applications | `/applications/` | Filterable list with "Trigger Calls" bulk action, detail with full timeline |
| Application Detail | `/applications/<pk>/` | Status override, add note, schedule callback, trigger follow-up, manual CV upload |
| CV Inbox | `/cvs/` | Unmatched and needs-review tabs with manual assignment forms |
| Messages | `/messages/` | Inbound candidate replies (WhatsApp + email), grouped by conversation with expand/collapse and delete |
| Templates — AI Prompts | `/prompts/` | PromptTemplate CRUD with per-section status dashboard; "Test Generate" preview per template |
| Templates — Message | `/messages/templates/` | MessageTemplate edit with live preview; one template per type/channel |
| Settings | `/settings/` | Gmail OAuth2 setup, polling controls, live service status |
| Global Search | `/search/` | AJAX JSON endpoint for header search dropdown (candidates, positions, applications) |

---

## Services & Integrations

| Service | Module | Description |
|---|---|---|
| `ElevenLabsService` | `calls/services.py` | Outbound AI phone calls via ElevenLabs ConvAI (single + batch) |
| `ClaudeService.generate_section()` | `evaluations/services.py` | Generate one Position prompt section via Claude (plain text, no JSON) |
| `ClaudeService.evaluate_call()` | `evaluations/services.py` | Score completed call transcript; 4 possible outcomes |
| `WhapiService` | `messaging/services.py` | Send WhatsApp messages via Whapi REST API |
| `GmailService` | `messaging/services.py` | Send emails + poll inbox via Gmail API (OAuth2) |
| `process_inbound_cv` | `cvs/services.py` | CV smart matching cascade (5 priority levels) |
| `send_cv_request` | `messaging/services.py` | Post-evaluation CV request orchestrator (uses MessageTemplate) |
| `send_followup` | `messaging/services.py` | Timed follow-up orchestrator (uses MessageTemplate) |
| `lookup_candidate_by_phone/email` | `candidates/services.py` | Shared candidate resolution (normalised phone, case-insensitive email) |
| `GlobalSearchView` | `recruitflow/views.py` | AJAX search across candidates, positions, applications |

---

## Scheduled Jobs

| Job | Interval | Description |
|---|---|---|
| `process_call_queue` | 5 min | Process `call_queued` and `callback_scheduled` applications within calling hours |
| `sync_stuck_calls` | 10 min | Poll ElevenLabs for calls stuck in `initiated`/`in_progress` |
| `check_cv_followups` | 60 min | Send follow-ups for qualified candidates past their interval |
| `close_stale_rejected` | 24 hrs | Close rejected applications past CV timeout |
| `poll_cv_inbox` | 15 min | Poll Gmail: process CV attachments + save text replies as `CandidateReply` |

Start the scheduler alongside the Django server:

```bash
python manage.py run_scheduler
```

---

## Key Design Decisions

**Per-section prompt templates**: Three `PromptTemplate` records are needed — one per section (`system_prompt`, `first_message`, `qualification_prompt`). Each is independently versioned and activated. Claude returns plain text per section (no JSON parsing required).

**Inbound message storage**: All inbound WhatsApp text messages and email replies are stored as `CandidateReply` records (separate from the outbound `Message` model). The recruiter inbox (`/messages/`) shows these grouped by sender as conversations.

**Message templates**: Outbound message bodies are managed via `MessageTemplate` in the UI (Templates → Message Templates). Services fall back to hardcoded defaults if no active template exists for a given type/channel.

**Status transitions**: All `Application` status changes go through `applications/transitions.py` helpers, which call `Application.change_status()` for consistent audit entries in `StatusChange`.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Framework | Django 5.2 |
| Database | PostgreSQL + psycopg2-binary |
| AI / LLM | Anthropic Claude (`anthropic`, `json-repair` for fallback parsing) |
| Voice | ElevenLabs ConvAI |
| Messaging | Whapi (WhatsApp) |
| Email | Gmail API (OAuth2) |
| PDF Parsing | pdfplumber |
| Scheduling | django-apscheduler |
| Frontend | Custom CSS design system (Bootstrap-inspired tokens) |
| Config | django-environ |
