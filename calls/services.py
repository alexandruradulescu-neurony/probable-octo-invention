"""
calls/services.py

ElevenLabs Conversational AI integration service.

Architecture:
  ElevenLabsClient  — pure HTTP transport (no Django ORM, no transactions).
                      Independently mockable in tests.
  ElevenLabsService — orchestration + DB persistence.
                      Accepts an optional ``client`` via constructor injection
                      for testability; defaults to a real client from settings.

Spec reference: Section 9 — ElevenLabs Integration Details
  Single call endpoint : POST https://api.elevenlabs.io/v1/convai/twilio/outbound-call
  Batch call endpoint  : POST https://api.elevenlabs.io/v1/convai/batch-calling/submit
  Auth                 : xi-api-key header
  Note                 : "Allow Overrides" must be enabled in the ElevenLabs agent's
                         Security settings for conversation_config_override to work.
"""

import logging

import requests
from django.conf import settings
from django.db import transaction

from calls.models import Call
from applications.models import Application

logger = logging.getLogger(__name__)

ELEVENLABS_OUTBOUND_URL = (
    "https://api.elevenlabs.io/v1/convai/twilio/outbound-call"
)
ELEVENLABS_BATCH_URL = (
    "https://api.elevenlabs.io/v1/convai/batch-calling/submit"
)

BATCH_CHUNK_SIZE = 50

CONVERSATION_ID_KEYS = ("conversation_id", "call_id", "id", "call_sid")


# ── Custom exception ───────────────────────────────────────────────────────────

class ElevenLabsError(Exception):
    """Raised when the ElevenLabs API returns an error or an unexpected response."""


# ── HTTP client (transport only, no Django ORM) ───────────────────────────────

class ElevenLabsClient:
    """
    Thin HTTP client for the ElevenLabs ConvAI API.

    Owns:
      - Credential storage and validation
      - HTTP POST with error handling
      - Response parsing (conversation ID extraction)

    Does NOT own:
      - Database access (Call / Application models)
      - Transaction management
      - Prompt templating
    """

    def __init__(
        self,
        api_key: str | None = None,
        agent_id: str | None = None,
        phone_number_id: str | None = None,
    ):
        self.api_key = api_key or settings.ELEVENLABS_API_KEY
        self.agent_id = agent_id or settings.ELEVENLABS_AGENT_ID
        self.phone_number_id = phone_number_id or settings.ELEVENLABS_PHONE_NUMBER_ID

    def validate_credentials(self) -> None:
        """Raise ElevenLabsError if any required credential is missing."""
        if not self.api_key:
            raise ElevenLabsError("ELEVENLABS_API_KEY is not configured.")
        if not self.agent_id:
            raise ElevenLabsError("ELEVENLABS_AGENT_ID is not configured.")
        if not self.phone_number_id:
            raise ElevenLabsError("ELEVENLABS_PHONE_NUMBER_ID is not configured.")

    def post_outbound_call(self, payload: dict) -> dict:
        """POST to the single-call endpoint. Returns parsed JSON."""
        return self._post(ELEVENLABS_OUTBOUND_URL, payload)

    def post_batch_call(self, payload: dict) -> dict:
        """POST to the batch-calling endpoint. Returns parsed JSON."""
        return self._post(ELEVENLABS_BATCH_URL, payload)

    @staticmethod
    def extract_conversation_id(data: dict) -> str | None:
        """
        Extract the conversation/call identifier from the ElevenLabs response.
        The spec notes the field name may vary across API versions.
        """
        for key in CONVERSATION_ID_KEYS:
            value = data.get(key)
            if value:
                return str(value)
        logger.warning(
            "Could not find conversation ID in ElevenLabs response: %s", data
        )
        return None

    # ── Internal ──────────────────────────────────────────────────────────────

    def _post(self, url: str, payload: dict) -> dict:
        """Execute a POST to any ElevenLabs endpoint and return parsed JSON."""
        headers = {
            "Content-Type": "application/json",
            "xi-api-key": self.api_key,
        }
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
        except requests.RequestException as exc:
            raise ElevenLabsError(f"Network error calling ElevenLabs: {exc}") from exc

        if not resp.ok:
            raise ElevenLabsError(
                f"ElevenLabs API error {resp.status_code}: {resp.text[:500]}"
            )

        try:
            return resp.json()
        except ValueError as exc:
            raise ElevenLabsError(
                f"ElevenLabs returned non-JSON response: {resp.text[:200]}"
            ) from exc


# ── Orchestration service (DB persistence + business logic) ───────────────────

class ElevenLabsService:
    """
    Orchestrates ElevenLabs call initiation and Call/Application persistence.

    The HTTP transport is delegated to ``ElevenLabsClient``, which can be
    injected via the constructor for testing.
    """

    def __init__(self, client: ElevenLabsClient | None = None):
        self.client = client or ElevenLabsClient()

    # ── Public API ─────────────────────────────────────────────────────────────

    def initiate_outbound_call(self, application) -> Call:
        """
        Place an outbound call for the given Application.

        Steps:
          1. Determine the next attempt number.
          2. Resolve the candidate's phone in E.164 format.
          3. Format the system_prompt and first_message with context placeholders.
          4. POST to the ElevenLabs outbound-call endpoint.
          5. Persist a Call record and advance the Application status.

        Returns:
            The newly created Call instance.

        Raises:
            ElevenLabsError: on any API or configuration failure.
        """
        self.client.validate_credentials()

        candidate = application.candidate
        position = application.position

        if not candidate.phone:
            raise ElevenLabsError(
                f"Candidate #{candidate.pk} has no phone number."
            )

        attempt_number = application.calls.count() + 1

        context = _build_placeholder_context(candidate, position)
        system_prompt = _apply_placeholders(position.system_prompt or "", context)
        first_message = _apply_placeholders(position.first_message or "", context)

        payload = {
            "agent_id": self.client.agent_id,
            "agent_phone_number_id": self.client.phone_number_id,
            "to_number": candidate.phone,
            "conversation_initiation_client_data": _build_agent_override(
                system_prompt, first_message
            ),
        }

        logger.info(
            "Initiating ElevenLabs call: application=%s attempt=%s to=%s",
            application.pk,
            attempt_number,
            candidate.phone,
        )

        response_data = self.client.post_outbound_call(payload)
        conversation_id = self.client.extract_conversation_id(response_data)

        with transaction.atomic():
            call = Call.objects.create(
                application=application,
                attempt_number=attempt_number,
                eleven_labs_conversation_id=conversation_id,
                status=Call.Status.INITIATED,
            )
            application.status = Application.Status.CALL_IN_PROGRESS
            application.save(update_fields=["status", "updated_at"])

        logger.info(
            "Call created: call_id=%s conversation_id=%s",
            call.pk,
            conversation_id,
        )
        return call

    def initiate_batch_calls(self, applications: list) -> list:
        """
        Submit all applications as a single batch call to ElevenLabs.

        Because the batch API returns only a `batch_id` (not individual
        conversation IDs), each Call record is created with
        `eleven_labs_conversation_id=None`.  The conversation ID arrives
        later via the per-call post-call webhook; the webhook handler
        performs the late-binding lookup via `application.pk` (passed as
        `user_id` inside `conversation_initiation_client_data`).

        Large queues are automatically split into chunks of BATCH_CHUNK_SIZE
        to avoid payload-size and timeout issues.

        Returns:
            Flat list of all created Call instances across all chunks.

        Raises:
            ElevenLabsError: if credentials are missing or the API rejects a chunk.
        """
        self.client.validate_credentials()

        all_calls: list = []

        for chunk_start in range(0, len(applications), BATCH_CHUNK_SIZE):
            chunk = applications[chunk_start : chunk_start + BATCH_CHUNK_SIZE]
            calls = self._submit_batch_chunk(chunk)
            all_calls.extend(calls)

        return all_calls

    # ── Internal ──────────────────────────────────────────────────────────────

    def _submit_batch_chunk(self, applications: list) -> list:
        """
        Submit one chunk of applications as a single batch-calling request.

        Builds the recipients array, POSTs it, then atomically creates Call
        records and advances each application to CALL_IN_PROGRESS.
        """
        recipients = []
        skipped = []

        for app in applications:
            candidate = app.candidate
            position = app.position

            if not candidate.phone:
                logger.warning(
                    "Skipping application=%s in batch — candidate has no phone number",
                    app.pk,
                )
                skipped.append(app)
                continue

            context = _build_placeholder_context(candidate, position)
            system_prompt = _apply_placeholders(position.system_prompt or "", context)
            first_message = _apply_placeholders(position.first_message or "", context)

            override = _build_agent_override(system_prompt, first_message)
            override["user_id"] = str(app.pk)
            recipients.append({
                "phone_number": candidate.phone,
                "conversation_initiation_client_data": override,
            })

        if not recipients:
            logger.warning("Batch chunk had no valid recipients after phone-number check")
            return []

        payload = {
            "call_name": f"RecruitFlow Batch — {len(recipients)} call(s)",
            "agent_id": self.client.agent_id,
            "agent_phone_number_id": self.client.phone_number_id,
            "recipients": recipients,
        }

        logger.info(
            "Submitting ElevenLabs batch: %s recipient(s)", len(recipients)
        )

        response_data = self.client.post_batch_call(payload)
        batch_id = response_data.get("batch_id") or response_data.get("id")

        if not batch_id:
            raise ElevenLabsError(
                f"ElevenLabs batch API returned no batch_id: {response_data}"
            )

        logger.info("ElevenLabs batch submitted: batch_id=%s", batch_id)

        eligible_apps = [a for a in applications if a not in skipped]

        created_calls: list = []
        with transaction.atomic():
            for app in eligible_apps:
                attempt_number = app.calls.count() + 1
                call = Call.objects.create(
                    application=app,
                    attempt_number=attempt_number,
                    eleven_labs_conversation_id=None,
                    eleven_labs_batch_id=batch_id,
                    status=Call.Status.INITIATED,
                )
                app.status = Application.Status.CALL_IN_PROGRESS
                app.save(update_fields=["status", "updated_at"])
                created_calls.append(call)

        logger.info(
            "Batch call records created: batch_id=%s count=%s",
            batch_id,
            len(created_calls),
        )
        return created_calls


# ── Payload helpers ────────────────────────────────────────────────────────────

def _build_agent_override(system_prompt: str, first_message: str) -> dict:
    """
    Build the `conversation_initiation_client_data` dict that overrides the
    agent's system prompt and first message for a single call.

    Used by both the single-call and batch-call paths. For batch calls the
    caller adds a `user_id` key to the returned dict before use.

    Requires "Allow Overrides" to be enabled in the ElevenLabs agent's
    Security settings. Spec § 9.
    """
    return {
        "conversation_config_override": {
            "agent": {
                "prompt": {"prompt": system_prompt},
                "first_message": first_message,
            }
        }
    }


# ── Placeholder helpers ────────────────────────────────────────────────────────

def _build_placeholder_context(candidate, position) -> dict:
    """
    Build the full substitution dictionary for prompt template variables.
    Spec § 9 — Prompt Templating.
    """
    return {
        "candidate_name": f"{candidate.first_name} {candidate.last_name}".strip(),
        "candidate_first_name": candidate.first_name or "",
        "candidate_email": candidate.email or "",
        "position_title": position.title or "",
        "position_description": position.description or "",
        "form_answers": _format_form_answers(candidate.form_answers),
    }


def _apply_placeholders(template: str, context: dict) -> str:
    """
    Replace {placeholder} tokens in a prompt template string.
    Unknown placeholders are left as-is to avoid KeyError on user-defined vars.
    """
    for key, value in context.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def _format_form_answers(form_answers: dict | None) -> str:
    """
    Render a form_answers dict as a human-readable Q&A block for injection
    into the ElevenLabs system prompt.

    Example output:
        Q: Do you have a driver's license?
        A: Yes

        Q: Available for night shifts?
        A: No
    """
    if not form_answers:
        return "No pre-screening answers available."

    lines = []
    for key, value in form_answers.items():
        question = key.replace("_", " ").strip().capitalize()
        lines.append(f"Q: {question}\nA: {value}")

    return "\n\n".join(lines)
