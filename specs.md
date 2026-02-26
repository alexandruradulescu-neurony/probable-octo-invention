RecruitFlow — Final Application Specification

1. Overview
An AI-powered recruiting pipeline built with Django that automates candidate qualification via voice calls (ElevenLabs), scores transcripts with Claude, and collects CVs through email and WhatsApp. Recruiters manage everything through a simple Django-based frontend.

2. Tech Stack
Concern
Tool
Backend
Django
Frontend
Django templates (simple)
Database
PostgreSQL
LLM
Claude (Anthropic API)
Voice calls
ElevenLabs Conversational AI (outbound)
WhatsApp
Whapi
Email outbound
Gmail API
Email inbound (CV)
Gmail API (polling)
Scheduling
django-apscheduler
File storage
Local or S3 (configurable)


3. Django App Structure
project/
├── candidates/       # Candidate model, CSV import, shared lookup helpers
├── positions/        # Position model, per-section prompt auto-generation
├── applications/     # Application model, status machine
├── calls/            # Call model, ElevenLabs integration
├── evaluations/      # LLMEvaluation model, Claude integration
├── messaging/        # Message, CandidateReply, MessageTemplate models; email + WhatsApp services
├── cvs/              # CVUpload model, Gmail inbound processing
├── webhooks/         # Inbound webhook views (ElevenLabs, Whapi)
├── prompts/          # PromptTemplate model, per-section meta-prompt management
├── config/           # Gmail OAuth2 settings, app configuration
└── scheduler/        # All apscheduler job definitions

4. Entities & Database Schema
4.1 Position
Each open role. The ElevenLabs agent is shared across all positions (single agent, configured in env). Two prompts are sent to ElevenLabs dynamically per call: the system prompt (personality, instructions) and the first message (opening line). Both support placeholder variables like {candidate_name} and {position_title} that are replaced at call time. The qualification prompt is sent to Claude after the call, along with the full transcript, for scoring.
ElevenLabs prerequisite: "Allow Overrides" must be enabled in the agent's Security settings in the ElevenLabs dashboard.
Field
Type
Notes
id
int, PK


title
str


description
text


status
ENUM
open, paused, closed
campaign_questions
text
Screening questions for this role, one per line (e.g. "Do you have a driver's license?", "Available for night shifts?"). Used as input for auto-generating all three prompts below.
system_prompt
text, nullable
Injected into ElevenLabs dynamically per call (personality, instructions). Can be auto-generated via "Generate Prompts" button.
first_message
text, nullable
Separate opening prompt sent to ElevenLabs alongside system_prompt. Can be auto-generated.
qualification_prompt
text, nullable
Sent to Claude along with the call transcript and candidate's form answers. Claude determines one of four outcomes: qualified, not_qualified, callback_requested, needs_human. Can be auto-generated.
call_retry_max
int
Max call attempts before marking call_failed (default 2)
call_retry_interval_minutes
int
Wait between retry attempts
calling_hour_start
int
Earliest hour to place calls, 24h format (default 10)
calling_hour_end
int
Latest hour to place calls, 24h format (default 18)
follow_up_interval_hours
int
Gap between CV follow-up messages (qualified only)
rejected_cv_timeout_days
int
Days to wait for rejected candidate's CV before closing (default 7)
created_at
datetime


updated_at
datetime




4.2 Candidate
A person sourced from a Meta lead form CSV. The full_name from Meta is parsed into first/last name on import. Campaign-specific form questions (which vary per campaign) are stored as JSON.
Field
Type
Notes
id
int, PK


first_name
str
Parsed from Meta full_name
last_name
str
Parsed from Meta full_name
full_name
str
Original value from Meta CSV
phone
str, indexed
Cleaned: strip p: prefix from Meta CSV
email
str, indexed


whatsapp_number
str, nullable
If different from phone
source
str
"meta_form", "manual"
meta_lead_id
str, nullable, unique
The id field from Meta CSV (e.g. l:1990233898539318)
meta_created_time
datetime, nullable
Original submission timestamp from Meta
campaign_name
str, nullable
Meta campaign name (e.g. "Sales Representative B2B - stabil")
platform
str, nullable
"fb", "ig", etc.
form_answers
jsonb, nullable
Campaign-specific questions & answers as key-value pairs (see below)
notes
text, nullable


created_at
datetime


updated_at
datetime



form_answers example:
{
  "ai_experiență_în_vânzări_business_to_business?": "da, peste 3 ani",
  "ai_negociat_direct_condiții_comerciale_cu_clienții_(preț,_volume,_termene)?": "da, constant",
  "acest_rol_este_full-time,_cu_contract_de_muncă...": "este ceea ce caut",
  "rolul_presupune_deplasări_regulate_la_clienți...": "da, este în regulă pentru mine",
  "ai_permis_de_conducere_categoria_b?": "da"
}
This approach handles the variable column problem — any columns that aren't in the standard set get stored as JSON key-value pairs. Different campaigns produce different questions, but they all land cleanly in the same field.

4.3 Application
The core workflow entity. Links a candidate to a position and owns all pipeline state. A candidate can have multiple applications across different positions (many-to-many via this table).
Field
Type
Notes
id
int, PK


candidate_id
FK → Candidate


position_id
FK → Position


status
ENUM
See status flow below
qualified
bool, nullable
null until scored
score
int, nullable
0–100
score_notes
text, nullable
Claude's reasoning summary
cv_received_at
datetime, nullable


callback_scheduled_at
datetime, nullable
When to call back (if candidate requested)
needs_human_reason
text, nullable
Why this was escalated to a recruiter
created_at
datetime


updated_at
datetime



Constraint: UNIQUE TOGETHER (candidate_id, position_id)
Application Status Flow:
pending_call
  │
  └─[recruiter bulk triggers]
  │
call_queued
  │
call_in_progress
  │
  ├─[completed]──────► call_completed
  │                         │
  ├─[no answer,             ▼
  │   retry]             scoring
  │                         │
  └─[retries exhausted]     ├──► not_qualified
  │                         │        │
  │                         │    [send WhatsApp asking for CV for future positions]
  │                         │        │
  │                         │    awaiting_cv_rejected
  │                         │        │
  │                         │        ├─[CV received] ──► cv_received_rejected ──► closed
  │                         │        │
  │                         │        └─[no follow-ups, just wait] ──► closed (after timeout)
  │                         │
  │                         └──► qualified
  │                                  │
  │                              [send email + WhatsApp asking for CV]
  │                                  │
  │                              awaiting_cv
  │                                  │
  │                    [no CV after interval]
  │                                  │
  │                             cv_followup_1
  │                                  │
  │                    [no CV after interval]
  │                                  │
  │                             cv_followup_2
  │                                  │
  │                    [no CV after interval]
  │                                  │
  │                             cv_overdue ──► closed
  │
  ├─[candidate requests callback]──► callback_scheduled
  │                                       │
  │                                  [at scheduled time]
  │                                       │
  │                                  call_queued (re-enters flow)
  │
  └─[candidate refuses bot / needs human]──► needs_human ──► (recruiter handles manually)

             [CV received at any awaiting stage]
                                     │
                         cv_received / cv_received_rejected ──► closed
Key rules:
Qualified: email + WhatsApp CV request, then up to 2 follow-ups
Not qualified: WhatsApp only CV request, NO follow-ups — if they don't respond, application closes after a configurable timeout
Callback requested: candidate asked to be called at a different time — recruiter or system schedules the next call
Needs human: candidate refused the bot or explicitly asked for a person — flagged for recruiter manual handling
Calling hours: calls are only placed between 10:00–18:00 local time. The process_call_queue job skips any calls outside this window.

4.4 Call
One record per call attempt. Multiple attempts possible per Application.
Field
Type
Notes
id
int, PK


application_id
FK → Application


attempt_number
int
1, 2, 3...
eleven_labs_conversation_id
str, unique, nullable


status
ENUM
initiated, in_progress, completed, no_answer, busy, failed
transcript
text, nullable
Full conversation transcript (Agent/User turns). Sent to Claude for qualification.
summary
text, nullable
Auto-generated by ElevenLabs analysis
summary_title
str, nullable
Auto-generated by ElevenLabs analysis
recording_url
str, nullable


duration_seconds
int, nullable


initiated_at
datetime


ended_at
datetime, nullable




4.5 LLMEvaluation
Claude's scoring result for a completed call transcript. Claude receives the transcript, the position's qualification prompt, and the candidate's form answers from Meta. It evaluates not just qualification, but also detects special situations (callback requests, human escalation needed).
Field
Type
Notes
id
int, PK


application_id
FK → Application


call_id
FK → Call


outcome
ENUM
qualified, not_qualified, callback_requested, needs_human
qualified
bool


score
int
0–100
reasoning
text
Claude's explanation
callback_requested
bool, default False
Candidate asked to be called at another time
callback_notes
text, nullable
E.g. "call me tomorrow after 2pm"
needs_human
bool, default False
Candidate refused bot or asked for a person
needs_human_notes
text, nullable
E.g. "candidate got angry, wants to speak to a manager"
raw_response
jsonb
Full response for debugging
evaluated_at
datetime




4.6 Message
Every outbound communication. Full audit trail.
Field
Type
Notes
id
int, PK


application_id
FK → Application


channel
ENUM
email, whatsapp
message_type
ENUM
cv_request, cv_request_rejected, cv_followup_1, cv_followup_2, rejection, other
status
ENUM
pending, sent, delivered, failed
external_id
str, nullable
Gmail message ID or Whapi message ID
body
text


sent_at
datetime, nullable


error_detail
str, nullable




4.7 CVUpload
Received CV files. Separate from Application so multiple versions can be tracked.
Field
Type
Notes
id
int, PK


application_id
FK → Application


file_name
str


file_path
str
Local path or S3 key
source
ENUM
email_attachment, whatsapp_media, manual_upload
match_method
ENUM, nullable
exact_email, exact_phone, subject_id, fuzzy_name, cv_content, manual
needs_review
bool, default False
True if matched via medium-confidence method
received_at
datetime




4.8 UnmatchedInbound
Inbound messages (email or WhatsApp) with attachments that couldn't be matched to any candidate. Held for manual recruiter review.
Field
Type
Notes
id
int, PK


channel
ENUM
email, whatsapp
sender
str
Email address or phone number
subject
str, nullable
Email subject if applicable
body_snippet
text, nullable


attachment_name
str, nullable


raw_payload
jsonb
Full inbound payload
received_at
datetime


resolved
bool, default False


resolved_by_application_id
FK → Application, nullable


resolved_at
datetime, nullable




4.9 CandidateReply
An inbound text message received from a candidate via WhatsApp or email. Stored separately from the outbound Message model because inbound messages have no message_type, no delivery status, and no mandatory application FK. Used to populate the recruiter's Messages inbox (conversation view, grouped by sender).
Field
Type
Notes
id
int, PK


candidate_id
FK → Candidate, nullable
Resolved by phone/email matching at ingest time; null if unresolvable
application_id
FK → Application, nullable
Most-recent open application for the matched candidate, if found
channel
ENUM
email, whatsapp
sender
str
Raw phone number or email address of the sender
subject
str, blank
Email subject line (WhatsApp: always empty)
body
text
Message content
received_at
datetime, auto
Indexed
is_read
bool, default False
Indexed; drives the sidebar unread badge
external_id
str, nullable
Whapi message ID or Gmail thread ID

Ingest paths:
Whapi webhook → POST /webhooks/whapi/ — when an inbound message has type "text" the handler calls _save_candidate_reply(). Also triggered for media messages that include a caption.
Gmail poll → poll_cv_inbox job — emails without attachments (text-only) and emails with attachments but containing a non-empty body are both saved as CandidateReply alongside (or instead of) the CV processing path.

4.10 PromptTemplate (Per-Section)
A meta-prompt that instructs Claude to generate exactly one of the three Position prompt fields. Three separate templates are needed — one per section — and each can be independently activated, versioned, and edited.

Previously (v1): a single PromptTemplate generated all three fields at once via a JSON response. This was replaced because it made individual regeneration impossible and parsing JSON was fragile.
Field
Type
Notes
id
int, PK


section
ENUM, nullable
system_prompt, first_message, qualification_prompt. Nullable to preserve legacy records.
name
str
E.g. "System Prompt v2", "Conservative qualifier"
is_active
bool, default False, indexed
One active template per section at a time
meta_prompt
text
The instructions Claude receives. Placeholders: {title}, {description}, {campaign_questions}.
version
int
Auto-incrementing for audit trail
created_at
datetime


updated_at
datetime



How auto-generation works (new flow):
Recruiter fills in Position: title, description, campaign_questions
Recruiter clicks "Generate All" button (or the individual "Regenerate" button on any prompt field)
For each of the three sections, the backend:
  a. Looks up the active PromptTemplate where section = that field's section key
  b. Substitutes {title}, {description}, {campaign_questions} into the meta_prompt
  c. Sends to Claude via ClaudeService.generate_section() — system message instructs Claude to return plain text only, no JSON
  d. Receives plain text — immediately populates the textarea; if editing an existing Position, auto-saves the field to DB
"Generate All" fires three sequential AJAX calls; each field shows a spinner while its call is in progress. Progress is visible per-field.
Individual "Regenerate" buttons next to each field allow re-running a single section without affecting the others.

Claude output contract: For generate_section(), Claude returns plain text only (no JSON wrapping, no markdown fences). No parsing is required. The text is stored directly in Position.system_prompt / first_message / qualification_prompt.

Key design decisions:
One PromptTemplate per section — three active templates total (one per section); activating one automatically deactivates the previous active for that section only
Each section template is independently versioned; the audit trail is per-section
Prompts are always editable after generation — auto-generation is a starting point, not a lock
The Templates list page shows a per-section status dashboard: green if an active template is configured, yellow if missing (generation disabled for that section)

4.11 MessageTemplate
Editable body templates for every outbound message type and channel combination. Used by messaging.services as the primary source of message text; falls back to hardcoded defaults if no active template exists for a given combination.
Field
Type
Notes
id
int, PK


message_type
ENUM
cv_request, cv_request_rejected, cv_followup_1, cv_followup_2, rejection
channel
ENUM
email, whatsapp
subject
str, blank
Email subject (ignored for WhatsApp)
body
text
Message body with optional placeholders
is_active
bool, default True, indexed
unique_together
(message_type, channel)
One active template per type/channel pair
created_at
datetime


updated_at
datetime



Available body placeholders: {first_name}, {position_title}, {application_pk}.
Seeded by: python manage.py seed_message_templates (creates 10 default records for all 5 types × 2 channels).
Management: Templates → Message Templates in the sidebar.

5. CSV Import Specification
Encoding: UTF-16 LE (with BOM)
Delimiter: Tab (\t)
Source: Downloaded from Meta Ads Manager → Lead Ads
Standard Columns (always present)
CSV Column
Maps To
Transformation
id
Candidate.meta_lead_id
Used as-is (e.g. l:1990233898539318)
created_time
Candidate.meta_created_time
Parse ISO 8601 with timezone
campaign_name
Candidate.campaign_name
Used as-is
platform
Candidate.platform
Used as-is (fb, ig)
email
Candidate.email
Lowercase, strip whitespace
full_name
Candidate.full_name + first_name + last_name
Split on first space: first word → first_name, rest → last_name
phone_number
Candidate.phone
Strip p: prefix, keep + and digits only

Ignored Columns (stored in raw import log but not mapped)
ad_id, ad_name, adset_id, adset_name, form_id, form_name, is_organic, inbox_url
Dynamic Columns (campaign-specific questions)
Any column NOT in the standard or ignored set is treated as a campaign-specific form question. These are collected into Candidate.form_answers as a JSON object where:
Key = column header (original, with underscores)
Value = answer text, with underscores replaced by spaces, cleaned up
Import Logic
Read CSV (handle UTF-16 LE encoding)
Identify standard columns by name
Everything else between platform and email = form questions
For each row:
Parse full_name → first_name + last_name
Clean phone number (strip p: prefix)
Collect dynamic columns into form_answers JSON
Upsert Candidate keyed on meta_lead_id (update if exists, create if new)
Create Application linked to the target Position (skip if Application already exists for this candidate + position)
Set Application status to pending_call
Deduplication
Primary key: meta_lead_id — if a candidate with the same Meta lead ID already exists, update their info
Secondary check: if meta_lead_id is new but phone or email matches an existing candidate, flag for recruiter review (possible duplicate from a different campaign)

6. Scheduled Jobs (django-apscheduler)
Job
Interval
Responsibility
process_call_queue
every 5 min
Find call_queued and callback_scheduled applications. Only initiate calls between Position.calling_hour_start and calling_hour_end. For callbacks, only process if callback_scheduled_at has passed and is within calling hours.
sync_stuck_calls
every 10 min
Poll ElevenLabs API for calls stuck in initiated / in_progress (webhook fallback)
check_cv_followups
every 60 min
Find qualified applications in awaiting_cv / cv_followup_1 / cv_followup_2 past their follow-up interval. Send next follow-up or mark cv_overdue. Does NOT apply to rejected candidates.
close_stale_rejected
every 24 hrs
Find awaiting_cv_rejected applications older than Position.rejected_cv_timeout_days with no CV received. Close them.
poll_cv_inbox
every 15 min
Gmail API — scan for unread emails. Emails with attachments: smart-match to candidates (see CV Matching), save CVUpload or log as unmatched. Emails without attachments (text-only replies): save as CandidateReply for recruiter inbox. Emails with both attachments and a non-empty body: process CV attachment AND save body as CandidateReply.


7. Triggered Actions (event-driven, called directly)
Event
Action
ElevenLabs webhook — call ended
Save transcript to Call → run Claude evaluation → update Application
Claude returns qualified=True
Send CV request via email + WhatsApp → advance to awaiting_cv
Claude returns qualified=False
Send WhatsApp asking for CV (for future positions) → advance to awaiting_cv_rejected. No follow-ups.
Claude detects callback request
Set callback_scheduled_at on Application → advance to callback_scheduled
Claude detects human needed
Set needs_human_reason on Application → advance to needs_human. Recruiter handles manually.
Whapi webhook — inbound media message (CV)
Smart-match sender → save CVUpload → advance Application or log as unmatched
Whapi webhook — inbound text message
Save as CandidateReply (resolve to Candidate + Application where possible) → appears in recruiter Messages inbox with unread badge
Whapi webhook — inbound media message with caption
Save CVUpload (media) AND save CandidateReply (caption text)
Gmail poll finds attachment
Smart-match sender → save CVUpload → advance Application or log as unmatched
Gmail poll finds text-only email (no attachment)
Save as CandidateReply → appears in recruiter Messages inbox
Gmail poll finds email with both attachment and body text
Process CVUpload AND save body as CandidateReply
Recruiter manually resolves unmatched
Assign to Application → create CVUpload → advance status
Recruiter handles needs_human
Manual call/contact → recruiter updates status as appropriate


8. Webhook Endpoints
POST /webhooks/elevenlabs/     Call ended — receives conversation_id + transcript + status
POST /webhooks/whapi/          Inbound message — receives sender, message body, media metadata
Both validate a shared secret in the request headers before processing.

9. ElevenLabs Integration Details
Learned from existing codebase — exact API specifics for the call initiation and data retrieval.
Single Outbound Call API (used for manual "Call Now" and scheduled callbacks)
Endpoint: POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
Headers:
Content-Type: application/json
xi-api-key: {ELEVENLABS_API_KEY}
Payload:
{
  "agent_id": "{ELEVENLABS_AGENT_ID from env}",
  "agent_phone_number_id": "{ELEVENLABS_PHONE_NUMBER_ID from env}",
  "to_number": "{candidate phone in E.164 format}",
  "conversation_initiation_client_data": {
    "conversation_config_override": {
      "agent": {
        "prompt": {
          "prompt": "{Position.system_prompt — with placeholders replaced}"
        },
        "first_message": "{Position.first_message — with placeholders replaced}"
      }
    }
  }
}
Important: "Allow Overrides" must be enabled in the agent's Security settings in the ElevenLabs dashboard. Without this, sending conversation_config_override will throw an error.
Response: returns call_id (or id, call_sid, conversation_id) which is stored as Call.eleven_labs_conversation_id.

Batch Calling API (used for queued applications in process_call_queue)
Endpoint: POST https://api.elevenlabs.io/v1/convai/batch-calling/submit
Headers:
Content-Type: application/json
xi-api-key: {ELEVENLABS_API_KEY}
Payload:
{
  "call_name": "RecruitFlow Batch — N call(s)",
  "agent_id": "{ELEVENLABS_AGENT_ID from env}",
  "agent_phone_number_id": "{ELEVENLABS_PHONE_NUMBER_ID from env}",
  "recipients": [
    {
      "phone_number": "{candidate phone in E.164 format}",
      "conversation_initiation_client_data": {
        "user_id": "{application.pk}",
        "conversation_config_override": {
          "agent": {
            "prompt": { "prompt": "{personalized system_prompt}" },
            "first_message": "{personalized first_message}"
          }
        }
      }
    }
  ]
}
Response: returns { "batch_id": "..." } — individual conversation IDs are NOT returned.
The batch_id is stored on each created Call record (eleven_labs_batch_id) for auditing.

Queues are split into chunks of 50 recipients maximum to stay within API payload and timeout limits.

Webhook Linkage for Batch Calls:
ElevenLabs fires one post-call webhook per conversation after each call ends.
Because the batch response carries no individual conversation IDs, Call records are
created immediately after batch submission with eleven_labs_conversation_id=NULL.
When the webhook fires, the handler extracts application.pk from
conversation_initiation_client_data.user_id (echoed back by ElevenLabs),
finds the matching unbound INITIATED Call, and atomically binds the conversation_id
to it. Processing then continues identically to single-call webhooks.
Prompt Templating
Both system_prompt and first_message support placeholder variables that are replaced at call time with candidate/position context:
Placeholder
Source
{candidate_name}
Candidate.first_name + last_name
{candidate_first_name}
Candidate.first_name
{candidate_email}
Candidate.email
{position_title}
Position.title
{position_description}
Position.description
{form_answers}
Candidate.form_answers (formatted as readable text)

The {form_answers} placeholder is particularly useful — it injects the candidate's pre-screening answers from the Meta form into the system prompt, so the ElevenLabs agent can reference their responses during the call (e.g. "I see you mentioned you have over 3 years of B2B sales experience...").
Transcript Format
ElevenLabs returns the transcript as a list of turn objects. Each turn has a role (agent/user) and message/content/text. The app formats this as:
Agent: Hello, this is a call regarding the Marketing Manager position...

User: Yes, hello, I applied last week...

Agent: Great. Can you tell me about your experience with...
This formatted transcript is stored in Call.transcript and sent to Claude for qualification.
Analysis Data
ElevenLabs also returns auto-generated analysis:
analysis.transcript_summary → stored in Call.summary
analysis.call_summary_title → stored in Call.summary_title
These are supplementary — Claude does its own evaluation from the raw transcript.
Fallback Polling
When the ElevenLabs webhook doesn't fire (network issues, webhook misconfiguration), the sync_stuck_calls scheduled job polls the ElevenLabs API directly:
Endpoints tried (in order):
GET /v1/convai/conversations/{conversation_id}
GET /v1/convai/calls/{conversation_id}
GET /v1/conversations/{conversation_id}
GET /v1/calls/{conversation_id}
Status mapping from ElevenLabs:
ElevenLabs Status
App Call Status
done / completed
completed
failed
failed
no_answer
no_answer

If a call has been in initiated or in_progress for more than a configurable threshold (e.g. 15 minutes), the job polls ElevenLabs, updates the Call record with transcript/summary/status, and triggers the Claude evaluation if the call completed.

10. End-to-End Flow
1. Recruiter uploads CSV
   → Management command / admin action upserts Candidates
   → Creates Applications (status: pending_call) linked to target Position

2. Recruiter selects batch in Application List → clicks "Trigger Calls"
   → Applications move to call_queued

3. process_call_queue job (every 5 min)
   → **Only operates between Position.calling_hour_start and calling_hour_end (default 10:00–18:00)**
   → Skips all calls outside this window — they stay in queue until next valid window
   → Queue 1 (CALL_QUEUED — batch):
     → Collect all eligible call_queued Applications within calling hours
     → For each: replace placeholders in Position.system_prompt + Position.first_message
     → Submit ALL eligible applications as a single batch to ElevenLabs batch-calling API
       (POST /v1/convai/batch-calling/submit) in chunks of 50
     → Save one Call record per application (eleven_labs_conversation_id=NULL, batch_id stored)
     → Application status → call_in_progress
     → conversation_id is bound later via post-call webhook (see Section 9 — Batch Calling API)
   → Queue 2 (CALLBACK_SCHEDULED — individual):
     → Process callback_scheduled Applications where callback_scheduled_at has passed
       and current time is within calling hours
     → Each callback is submitted individually via /v1/convai/twilio/outbound-call (one-off, as before)

4. ElevenLabs webhook → POST /webhooks/elevenlabs/
   → Match via Call.eleven_labs_conversation_id
   → Save transcript, summary, summary_title, recording URL to Call
   → Application status → call_completed → scoring
   → Trigger Claude evaluation
   (Fallback: sync_stuck_calls job polls ElevenLabs API if webhook doesn't fire)

5. Claude evaluation
   → Send Call.transcript + Position.qualification_prompt + Candidate.form_answers to Claude
   → Claude evaluates and returns one of four outcomes:
     a. **qualified** → Application → qualified → awaiting_cv
        → Send CV request via email (Gmail API) + WhatsApp (Whapi)
     b. **not_qualified** → Application → not_qualified → awaiting_cv_rejected
        → Send WhatsApp only asking for CV for future positions. NO follow-ups.
     c. **callback_requested** → Application → callback_scheduled
        → Set callback_scheduled_at based on Claude's interpretation of the candidate's request
        → Will re-enter call_queued when the scheduled time arrives (within calling hours)
     d. **needs_human** → Application → needs_human
        → Set needs_human_reason from Claude's notes
        → Appears in recruiter's "Attention Required" dashboard panel
        → Recruiter handles manually (call, email, or reassign)

6. check_cv_followups job (every 60 min)
   → Find **qualified-only** Applications in awaiting_cv / cv_followup_1 / cv_followup_2
     where last message sent_at + Position.follow_up_interval_hours has passed
   → Send follow-up message (email + WhatsApp), advance status
   → After cv_followup_2 with no response → cv_overdue → closed
   → **Does NOT follow up with rejected candidates**

6b. close_stale_rejected job (every 24 hrs)
   → Find awaiting_cv_rejected Applications older than Position.rejected_cv_timeout_days
   → Close them silently (no message sent)

7. CV received (two inbound paths, smart matching):
   a. poll_cv_inbox job (every 15 min)
      → Gmail API: list unread messages with attachments
      → Run smart matching chain (see CV Matching Logic):
        exact email → exact phone → subject ID → fuzzy name → CV content extraction
      → High confidence: auto-assign CVUpload, advance Application
      → Medium confidence: auto-assign but flag needs_review=True for recruiter
      → No match: save to UnmatchedInbound for manual assignment
      → **If candidate has multiple open applications across positions,
        attach CV to ALL of them**
   b. Whapi webhook → POST /webhooks/whapi/
      → Same smart matching logic
      → Same multi-application attachment rule
   c. No match → save to UnmatchedInbound for recruiter review

11. Gmail API Setup
Google Cloud Console project with Gmail API enabled
OAuth2 credentials with one-time auth flow to generate a refresh token
App uses the refresh token to obtain short-lived access tokens automatically
Two Gmail labels used to keep polling idempotent
CV Matching Logic (Smart Matching):
Candidates may send CVs from a different email than what's on file. The system uses a multi-layer matching approach:
Priority
Method
Confidence
1
Sender email → exact match on Candidate.email
High
2
Sender phone (WhatsApp) → exact match on Candidate.phone / whatsapp_number
High
3
Subject line contains application ID or candidate reference number
High
4
Sender name / email display name → fuzzy match on Candidate.first_name + last_name
Medium
5
CV content analysis: extract raw text (pdfplumber/python-docx) → pass to Claude Haiku for JSON extraction (Name/Email/Phone) → fuzzy match against Candidates | Medium
Medium
6
No match found
—

Matching rules:
High-confidence matches (priority 1–3): auto-assign CVUpload to Application, advance status
Medium-confidence matches (priority 4–5): auto-assign but flag for recruiter review (add a needs_review flag on CVUpload)
No match: save to UnmatchedInbound for manual recruiter assignment
CV content extraction (Priority 5): CV layouts are unpredictable (multiple columns, weird fonts). Use pdfplumber (for PDFs) or python-docx to extract the raw text from the first 1-2 pages. Send this raw, unformatted text to a fast LLM (Claude Haiku) with a system prompt instructing it to return a strict JSON object containing first_name, last_name, email, and phone (returning null for missing values). Take this parsed JSON and fuzzy-match the extracted details against candidates who currently have applications in any awaiting_cv state.
Multi-application rule: When a candidate has multiple open applications across different positions, a single CV submission is attached to ALL of them. Each Application advances to cv_received / cv_received_rejected independently.

12. Frontend Screens
12.1 Login
Simple email + password form. No self-registration — recruiter accounts created by superuser in Django admin.

12.2 Dashboard (Home)
High-level pipeline overview. Contents:
Summary cards per position: total candidates broken down by status group (pending calls, in progress, awaiting CV, completed)
Activity feed: calls made today, CVs received today, follow-ups sent today
Attention required: call failures, cv_overdue applications, needs_human applications (candidate refused bot or needs recruiter), callback_scheduled coming up today, unmatched inbound items, CVs flagged needs_review (medium-confidence match)
Quick actions: "Upload CSV", "Go to Applications"

12.3 Positions
Position List Table of all positions. Columns: title, status badge, open applications count, created date. Actions: create new, edit, view applications for position.
Position Create / Edit Form fields: title, description, status, campaign questions (screening questions for this role, one per line), system prompt, first message, qualification prompt, call retry max, call retry interval minutes, calling hours start/end (default 10:00–18:00), follow-up interval hours (qualified only), rejected CV timeout days.
"Generate All" button: After filling in title, description, and campaign questions, the recruiter clicks this button. Three sequential Claude calls are fired — one per prompt section — each using the active PromptTemplate for that section. Fields populate one-by-one as each call completes (per-field spinner visible during generation). When editing an existing Position, each field is auto-saved to the database immediately as it arrives. The recruiter reviews and edits before the final form save.
Individual "Regenerate" buttons: A small regenerate icon button sits next to each of the three prompt field labels (System Prompt, First Message, Qualification Prompt), allowing a single section to be re-generated without affecting the others.
Includes a note reminding the recruiter that the ElevenLabs agent and voice are configured in ElevenLabs directly — these fields only control what the agent says for this specific position.

12.4 Candidates
Candidate List Searchable, filterable table. Filters: position, status, source. Columns: name, phone, email, number of applications, created date. Click through to detail.
Candidate Detail Contact info (editable). Meta lead info: campaign name, platform, submission date. Form answers from Meta displayed as a readable Q&A list. List of all applications across positions with status, score, last activity. Notes field. Links to each Application Detail.
CSV Import Step 1: Select target position, upload Meta CSV file (handles UTF-16 LE encoding + tab delimiter automatically). Preview shows parsed rows: name, phone, email, campaign, form answers count. Step 2: Confirm import — shows result summary: X new candidates created, Y existing candidates updated, Z applications created, W duplicates skipped. Also flags potential duplicates (same phone/email, different meta_lead_id) for recruiter review.

12.5 Applications
Application List (main daily-use screen) Filterable by position, status, date range, qualified/not qualified. Columns: candidate name, position, status badge, score, last activity. Bulk select + "Trigger Calls" button. Row click → Application Detail.
Application Detail (most important screen) Full timeline of everything that happened for this candidate/position pairing.
Sections:
Header: candidate name, position, current status badge, score, qualified flag
Contact bar: phone, email, WhatsApp — each as quick-action links
Call history: each attempt with status, duration, ElevenLabs summary, expandable transcript
LLM Evaluation: score, qualified result, Claude's reasoning. Shows callback/human flags if applicable.
Messages sent: table of all outbound messages — channel, type, sent at, delivery status
CV: download link if received, source, match method, needs_review flag. Manual upload option.
Timeline: chronological log of all status changes with timestamps
Actions: manual status override, add note, manually trigger a follow-up, schedule a callback, assign to recruiter (for needs_human)

12.6 CV Inbox — Unmatched & Review Items
Two tabs:
Unmatched: Inbound emails and WhatsApp messages with attachments that couldn't be auto-matched. Columns: received at, channel, sender, subject/snippet, attachment name, resolved status. Action per row: "Assign to Application" — recruiter searches/selects the correct application, which creates CVUpload and advances the status.
Needs Review: CVs that were auto-assigned via medium-confidence matching (fuzzy name or CV content extraction). Columns: received at, candidate name, match method, confidence note, assigned application. Action per row: "Confirm" (removes flag) or "Reassign" (move to correct application).

12.7 Templates (Admin)
The Templates section (accessible via sidebar "Templates") is a two-tab hub:

Tab 1 — AI Prompts (PromptTemplate management):
Section coverage dashboard: Three status cards at the top — one per section (System Prompt, First Message, Qualification Prompt). Green = active template configured; Yellow = no active template (generation disabled for that section).
List: All PromptTemplate records with section badge, name, version, active status, last updated. Records without a section assigned show a warning badge.
Create / Edit form: Select section (required), enter name, write meta_prompt. Available placeholders: {title}, {description}, {campaign_questions}. Help text notes that Claude will respond with plain text only (not JSON).
Test Generate panel (edit view only): Enter sample title/description/questions; click "Run Test" to call ClaudeService.generate_section() against the saved template without saving to any position. Result displayed as plain text.
Activate / Deactivate: Activating a template deactivates any other active template within the same section. Each section maintains its own independent active template.
Save creates a new version (increments version counter); old versions retained for audit trail.

Tab 2 — Message Templates (MessageTemplate management):
List: All 10 outbound message templates grouped by message type (cv_request, cv_request_rejected, cv_followup_1, cv_followup_2, rejection) × channel (email, whatsapp). Shows subject (email only), body preview, active status.
Edit form: Edit subject (email only), body (with placeholder documentation), and active toggle. Available placeholders: {first_name}, {position_title}, {application_pk}.
Live preview panel: Shows rendered output with sample data as the recruiter types.

12.8 Messages Inbox
Recruiter inbox for inbound candidate replies (WhatsApp text messages and email replies). Accessible at /messages/ via sidebar "Messages" item with unread badge.
Conversation-grouped view: Messages are grouped by sender (phone number or email address) into conversations. Each conversation row shows:
  Sender (linked to Candidate if matched) and channel icons
  Linked application (if resolved)
  Last message preview and timestamp
  Total message count and unread count
Expand/collapse individual conversations inline to read the full message thread
Per-conversation actions: "Mark all read" (updates is_read on all messages in the conversation) and "Delete conversation" (removes all CandidateReply records for that sender)
Per-message "Mark read" toggle
Sidebar badge: Shows total unread CandidateReply count; notification bell icon in top bar also links to /messages/ with the same badge.

12.9 Screen Build Priority
Priority
Screen
Reason
1
Position Create/Edit
Nothing works without a position
2
CSV Import
How candidates enter the system
3
Application List
Main daily working screen
4
Application Detail
Where all pipeline activity is reviewed
5
Dashboard
Useful once real data exists
6
Candidate Detail
Secondary, referenced from applications
7
CV Inbox / Unmatched & Review
Important for data integrity and matching
8
Prompt Templates (admin)
Needed once, then occasionally tuned
9
Call Log (optional)
Debugging and ops visibility


13. Environment Variables
# ────────────────────────────────────────────
# DJANGO
# Generate key: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
# ────────────────────────────────────────────
SECRET_KEY=
DEBUG=True
ALLOWED_HOSTS=your-subdomain.ngrok.app,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://your-subdomain.ngrok.app
PORT=8010
SECURE_SSL_REDIRECT=False
SECURE_HSTS_SECONDS=0
LOG_LEVEL=INFO

# ────────────────────────────────────────────
# DATABASE
# ────────────────────────────────────────────
DATABASE_URL=postgres://recruitflow_user:yourpassword@localhost:5432/recruitflow_db

# ────────────────────────────────────────────
# ANTHROPIC (Claude)
# https://console.anthropic.com
# ────────────────────────────────────────────
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-4-6
ANTHROPIC_FAST_MODEL=claude-haiku-4-5
# Increase if generate_section() responses are truncated (stop_reason = max_tokens).
# Recommended: 8192 for prompt generation, 4096 may suffice for evaluations.
ANTHROPIC_MAX_TOKENS=8192

# ────────────────────────────────────────────
# ELEVENLABS
# https://elevenlabs.io
# Single agent shared across all positions.
# System prompt + first message injected dynamically per call from Position model.
# ────────────────────────────────────────────
ELEVENLABS_API_KEY=
ELEVENLABS_AGENT_ID=
ELEVENLABS_PHONE_NUMBER_ID=
ELEVENLABS_WEBHOOK_SECRET=

# ────────────────────────────────────────────
# WHAPI (WhatsApp)
# https://whapi.cloud
# ────────────────────────────────────────────
WHAPI_TOKEN=
WHAPI_API_URL=https://gate.whapi.cloud
# Optional: set to the token configured in Whapi Dashboard → Channel → Webhooks → Token.
# Inbound webhooks are accepted without validation in DEBUG mode if left empty.
WHAPI_WEBHOOK_SECRET=

# ────────────────────────────────────────────
# GMAIL API (outbound email + CV inbox polling)
# https://console.cloud.google.com
# Run one-time OAuth2 flow locally to generate REFRESH_TOKEN
# ────────────────────────────────────────────
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REFRESH_TOKEN=
# Must match the Authorised Redirect URI in Google Cloud Console
GOOGLE_REDIRECT_URI=http://localhost:8010/settings/gmail/callback/
GMAIL_INBOX_LABEL=CVs
GMAIL_PROCESSED_LABEL=CVs-Processed
GMAIL_POLL_ENABLED=True
GMAIL_POLL_MINUTES=15

# ────────────────────────────────────────────
# STORAGE
# Default: local. Uncomment S3 block for production.
# ────────────────────────────────────────────
MEDIA_ROOT=media/

# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_STORAGE_BUCKET_NAME=

# ────────────────────────────────────────────
# SCHEDULER & TIMEZONE
# All scheduling and Django datetime display use this timezone.
# ────────────────────────────────────────────
TIME_ZONE=Europe/Bucharest
APSCHEDULER_TIMEZONE=Europe/Bucharest

13. Maintainability and Hardening Notes

13.1 Centralized status transitions
- Application status transitions are centralized in `applications/transitions.py`.
- Services and jobs should call transition helpers (e.g. `set_call_in_progress`, `set_scoring`, `set_call_failed`, `set_callback_scheduled`, `set_cv_received`) instead of assigning `application.status` directly.
- Transition helpers route through `Application.change_status(...)` for consistent audit entries in `StatusChange`.
- For user-initiated operations, pass `changed_by` through transition helpers so `StatusChange.changed_by` remains populated.
- Multi-step helpers that update side fields before status transition (`set_callback_scheduled`, `set_needs_human`, `set_cv_received`) execute inside one `transaction.atomic()` block to prevent partial state writes.

13.2 CV matching DRY rules
- Shared CV constants and flow helpers live in:
  - `cvs/constants.py` (`AWAITING_CV_STATUSES`)
  - `cvs/helpers.py` (`advance_application_status`, `channel_to_source`)
- `cvs/views.py` and `cvs/services.py` both consume these shared helpers.

13.3 Shared text and LLM parsing utilities
- Shared text helpers are defined in `recruitflow/text_utils.py`:
  - `strip_json_fence(raw)` for markdown-fenced JSON responses
  - `build_full_name(first_name, last_name)`
  - `humanize_form_question(key)`
- LLM and formatting consumers in `cvs/services.py`, `evaluations/services.py`, `calls/services.py`, and UI views must use these helpers.

13.4 Production security and logging defaults
- `recruitflow/settings.py` includes environment-aware security defaults:
  - `SECURE_SSL_REDIRECT`
  - `SESSION_COOKIE_SECURE`
  - `CSRF_COOKIE_SECURE`
  - `SECURE_CONTENT_TYPE_NOSNIFF`
  - `SECURE_HSTS_*` options
- Structured console logging is configured via `LOGGING` and `LOG_LEVEL`.

13.5 Indexing strategy
- High-frequency workflow filters are indexed on:
  - `Application.status`, `Application.qualified`, `Application.callback_scheduled_at`
  - `Call.status`
  - `Message.status`
  - `CVUpload.needs_review`
  - `UnmatchedInbound.resolved`
- Migrations:
  - `applications/migrations/0003_alter_application_callback_scheduled_at_and_more.py`
  - `calls/migrations/0003_alter_call_status.py`
  - `messaging/migrations/0002_alter_message_status.py`
  - `cvs/migrations/0002_alter_cvupload_needs_review_and_more.py`

13.6 Scheduler batch-failure transition mode
- In `scheduler/jobs.py`, batch submit failures are handled with per-application transition helpers (`set_call_failed`) instead of bulk SQL updates.
- This preserves one audit `StatusChange` per affected application.
- The failure path guards writes by first selecting only applications still in `CALL_QUEUED`, reducing unnecessary transition calls during concurrent state changes.

13.7 Claude JSON robustness (json-repair)
- `evaluations/services.py` uses a two-pass JSON parsing strategy for `evaluate_call()`:
  1. Strict `json.loads()` after stripping markdown fences.
  2. If that fails (e.g. unescaped quotes in multilingual text), fall back to `json_repair.repair_json()` before re-parsing.
- For `generate_section()` no JSON parsing is needed — Claude returns plain text only for each section.
- `ANTHROPIC_MAX_TOKENS` is configurable via env. The service raises `ClaudeServiceError` with a clear message if `stop_reason == "max_tokens"` (truncation detected). Recommended value: 8192.

13.8 Shared candidate lookup service
- Phone number normalisation and candidate lookup logic lives in `candidates/services.py`:
  - `lookup_candidate_by_phone(phone)` — normalises digits, handles leading `+` and country code variants
  - `lookup_candidate_by_email(email)` — case-insensitive exact match
- Consumed by `cvs/services.py` (CV matching), `webhooks/views.py` (Whapi inbound), and `scheduler/jobs.py` (Gmail inbound).
- This prevents duplicate normalisation logic scattered across the codebase.

13.9 CandidateReply ingest pattern
- All inbound text messages (WhatsApp via Whapi and email replies via Gmail) are saved as `CandidateReply` records.
- The `_save_candidate_reply()` helper in `webhooks/views.py` and `_save_email_reply()` in `scheduler/jobs.py` both:
  1. Attempt to resolve the sender to a `Candidate` via `lookup_candidate_by_phone` / `lookup_candidate_by_email`.
  2. If matched, also resolve the most recent open `Application` for that candidate.
  3. Create the `CandidateReply` record with `is_read=False`.
- Outbound messages (`from_me=True` in Whapi) are explicitly skipped to prevent self-echoes.

13.10 Per-section PromptTemplate activation rule
- `ToggleActiveView` in `prompts/views.py` deactivates only templates with the same `section` value, not all templates globally.
- This allows independent activation per section: e.g. activating a new System Prompt template does not affect the active First Message or Qualification templates.
- Legacy templates with `section=None` fall back to the old global deactivation behaviour for backward compatibility.
- The Templates list page shows a per-section health dashboard (3 status cards) so coverage gaps are immediately visible.
