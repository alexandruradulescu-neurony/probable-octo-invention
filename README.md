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
python manage.py runserver
```

Visit `http://127.0.0.1:8000/admin/` to verify.

---

## Environment Variables Reference

| Variable | Description |
|---|---|
| `SECRET_KEY` | Django secret key — keep this secret in production |
| `DEBUG` | `True` for development, `False` for production |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hostnames |
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
| Config | django-environ |
