"""
scheduler/management/commands/run_scheduler.py

Django management command that starts the APScheduler background scheduler
with all RecruitFlow pipeline jobs.

Usage:
    python manage.py run_scheduler

The command blocks until interrupted (Ctrl+C / SIGTERM).  In production,
run it as a long-lived process alongside the web server, e.g.:

    # Procfile (Heroku-style) or systemd unit
    web:       gunicorn recruitflow.wsgi --bind 0.0.0.0:8010
    scheduler: python manage.py run_scheduler

Jobs are persisted in the database via DjangoJobStore, which means:
  - Job execution history is available in Django admin.
  - Missed runs (misfire_grace_time) are tracked.
  - Restarting the process picks up existing job definitions automatically.
"""

import time
import logging

from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from django.conf import settings
from django.core.management.base import BaseCommand
from django_apscheduler.jobstores import DjangoJobStore

from scheduler.jobs import (
    check_cv_followups,
    close_stale_rejected,
    process_call_queue,
    sync_stuck_calls,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Start the APScheduler background scheduler. "
        "Runs all pipeline jobs (call queue, stuck calls, CV follow-ups, stale closures). "
        "Blocks until interrupted."
    )

    def handle(self, *args, **options):
        tz = ZoneInfo(settings.APSCHEDULER_TIMEZONE)

        scheduler = BackgroundScheduler(timezone=tz)
        scheduler.add_jobstore(DjangoJobStore(), "default")

        # ── Job registrations ──────────────────────────────────────────────────
        # replace_existing=True  — update the definition on each restart
        # max_instances=1        — prevent concurrent runs of the same job
        # coalesce=True          — if multiple runs were missed, execute once
        # misfire_grace_time     — seconds after which a missed run is discarded

        scheduler.add_job(
            process_call_queue,
            trigger=IntervalTrigger(minutes=5, timezone=tz),
            id="process_call_queue",
            name="Process Call Queue",
            jobstore="default",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
        )

        scheduler.add_job(
            sync_stuck_calls,
            trigger=IntervalTrigger(minutes=10, timezone=tz),
            id="sync_stuck_calls",
            name="Sync Stuck Calls (ElevenLabs fallback poll)",
            jobstore="default",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=120,
        )

        scheduler.add_job(
            check_cv_followups,
            trigger=IntervalTrigger(minutes=60, timezone=tz),
            id="check_cv_followups",
            name="Check CV Follow-ups",
            jobstore="default",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )

        scheduler.add_job(
            close_stale_rejected,
            trigger=IntervalTrigger(hours=24, timezone=tz),
            id="close_stale_rejected",
            name="Close Stale Rejected Applications",
            jobstore="default",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )

        # ── Start ──────────────────────────────────────────────────────────────
        self.stdout.write(self.style.SUCCESS(
            f"Starting scheduler (timezone={settings.APSCHEDULER_TIMEZONE})"
        ))

        for job in scheduler.get_jobs():
            self.stdout.write(
                f"  • {job.id:<30} next run: {job.next_run_time}"
            )

        scheduler.start()
        self.stdout.write(self.style.SUCCESS(
            "Scheduler running. Press Ctrl+C to stop."
        ))

        try:
            # Keep the main thread alive while the scheduler runs in the background.
            while True:
                time.sleep(5)
        except (KeyboardInterrupt, SystemExit):
            self.stdout.write(self.style.WARNING("Shutting down scheduler…"))
            scheduler.shutdown(wait=True)
            self.stdout.write(self.style.SUCCESS("Scheduler stopped."))
