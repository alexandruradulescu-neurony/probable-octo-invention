"""
Management command: import_meta_csv

Imports a Meta Ads Lead Form CSV export into Candidates and Applications.

Usage:
    python manage.py import_meta_csv --file path/to/leads.csv --position-id 1

The CSV must be UTF-16 LE encoded and tab-delimited (the default Meta export
format from Ads Manager → Lead Ads).
"""

from django.core.management.base import BaseCommand, CommandError

from candidates.services import import_meta_csv


class Command(BaseCommand):
    help = (
        "Import a Meta Ads Lead Form CSV export (UTF-16 LE, tab-delimited) "
        "into Candidates and Applications."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            type=str,
            required=True,
            metavar="PATH",
            help="Path to the Meta CSV file (UTF-16 LE, tab-delimited).",
        )
        parser.add_argument(
            "--position-id",
            type=int,
            required=True,
            metavar="ID",
            help="Primary key of the Position to link all imported candidates to.",
        )

    def handle(self, *args, **options):
        file_path = options["file"]
        position_id = options["position_id"]

        self.stdout.write(
            f"Importing: {file_path!r}  →  Position #{position_id} …"
        )

        try:
            summary = import_meta_csv(file_path, position_id)
        except ValueError as exc:
            raise CommandError(str(exc))
        except FileNotFoundError:
            raise CommandError(f"File not found: {file_path!r}")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"Could not decode the file — ensure it is UTF-16 LE encoded.\n{exc}"
            )

        # ── Results ──────────────────────────────────────────────────────────
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Import complete"))
        self.stdout.write(f"  Total rows processed   : {summary['total_rows']}")
        self.stdout.write(f"  Candidates created     : {summary['created']}")
        self.stdout.write(f"  Candidates updated     : {summary['updated']}")
        self.stdout.write(f"  Applications created   : {summary['applications_created']}")
        self.stdout.write(f"  Applications skipped   : {summary['applications_skipped']}")

        if summary["potential_duplicates"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING(
                    f"  Potential duplicates   : {len(summary['potential_duplicates'])}"
                )
            )
            self.stdout.write(
                self.style.WARNING(
                    "  These candidates share a phone or email with an existing record "
                    "from a different campaign. Review them in the admin."
                )
            )
            for dup in summary["potential_duplicates"]:
                self.stdout.write(
                    self.style.WARNING(
                        f"    • New #{dup['new_candidate_id']} "
                        f"(lead {dup['new_meta_lead_id']}) matches "
                        f"existing #{dup['matching_candidate_id']} "
                        f"(lead {dup['matching_meta_lead_id']}) "
                        f"by {dup['match_reason']}"
                    )
                )

        if summary["errors"]:
            self.stdout.write("")
            self.stdout.write(
                self.style.ERROR(f"  Errors                 : {len(summary['errors'])}")
            )
            for err in summary["errors"]:
                self.stdout.write(self.style.ERROR(f"    ✗ {err}"))

        if not summary["errors"]:
            self.stdout.write("")
            self.stdout.write(self.style.SUCCESS("No errors."))
