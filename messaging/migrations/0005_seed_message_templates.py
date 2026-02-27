"""
Data migration: seed the default message templates (email + WhatsApp).

Uses get_or_create keyed on (message_type, channel) so re-running
migrate on a database that already has these records is a safe no-op.
"""

from django.db import migrations

TEMPLATES = [
    # ── CV Request ─────────────────────────────────────────────────────────
    {
        "message_type": "cv_request",
        "channel": "email",
        "subject": "CV Request — {position_title}",
        "body": (
            "Salut {first_name},\n\n"
            "Vești bune! În urma apelului recent privind poziția de {position_title}, dorim să mergem mai departe cu aplicația ta.\n\n"
            "Te rugăm să ne trimiți CV-ul tău cât mai curând posibil.\n\n"
            "Referința aplicației tale este #{application_pk}.\n\n"
            "Mulțumim!\n"
            "Echipa de recrutare"
        ),
        "is_active": True,
    },
    {
        "message_type": "cv_request",
        "channel": "whatsapp",
        "subject": "",
        "body": (
            "Salut {first_name},\n\n"
            "Vești bune! În urma apelului recent privind poziția de {position_title}, ne-ar face plăcere să mergem mai departe cu aplicația ta.\n\n"
            "Te rugăm să ne trimiți CV-ul tău cât mai curând posibil.\n\n"
            "Referința aplicației tale este #{application_pk}.\n\n"
            "Mulțumim!"
        ),
        "is_active": True,
    },
    # ── CV Follow-up 1 ─────────────────────────────────────────────────────
    {
        "message_type": "cv_followup_1",
        "channel": "email",
        "subject": "Reminder: CV for {position_title}",
        "body": (
            "Salut {first_name},\n\n"
            "Doar un mic reminder că încă așteptăm CV-ul tău pentru rolul de {position_title}.\n\n"
            "Te rugăm să ni-l trimiți cât mai curând posibil pentru a putea continua procesul aplicației tale.\n\n"
            "Cu stimă,\n"
            "Echipa de Recrutare"
        ),
        "is_active": True,
    },
    {
        "message_type": "cv_followup_1",
        "channel": "whatsapp",
        "subject": "",
        "body": (
            "Salut {first_name}, doar un mic reminder, încă așteptăm CV-ul tău pentru rolul de {position_title}. "
            "Te rugăm să ni-l trimiți cât mai curând posibil. 😊"
        ),
        "is_active": True,
    },
    # ── CV Follow-up 2 ─────────────────────────────────────────────────────
    {
        "message_type": "cv_followup_2",
        "channel": "email",
        "subject": "Final Reminder: CV for {position_title}",
        "body": (
            "Salut {first_name},\n\n"
            "Acesta este un ultim reminder privind CV-ul tău pentru poziția de {position_title}.\n\n"
            "Te rugăm să ni-l trimiți cât mai curând posibil pentru a putea continua procesarea aplicației tale. "
            "Dacă nu primim un răspuns în scurt timp, este posibil să fim nevoiți să închidem dosarul tău.\n\n"
            "Cu stimă,\n"
            "Echipa de Recrutare"
        ),
        "is_active": True,
    },
    {
        "message_type": "cv_followup_2",
        "channel": "whatsapp",
        "subject": "",
        "body": (
            "Salut {first_name}, acesta este un ultim reminder privind CV-ul tău pentru poziția de {position_title}. "
            "Te rugăm să ni-l trimiți cât mai curând posibil pentru a putea continua cu aplicația ta."
        ),
        "is_active": True,
    },
    # ── CV Request (rejected / not qualified, keeping CV for future) ────────
    {
        "message_type": "cv_request_rejected",
        "channel": "email",
        "subject": "Thank you — {position_title}",
        "body": (
            "Salut {first_name},\n\n"
            "Îți mulțumim pentru interesul acordat poziției de {position_title} și pentru timpul acordat discuției cu noi.\n\n"
            "Deși acest rol specific poate că nu este cea mai potrivită opțiune în acest moment, ne-ar face plăcere să păstrăm datele tale "
            "pentru oportunități viitoare. Dacă dorești, te rugăm să ne trimiți CV-ul tău.\n\n"
            "Cu stimă,\n"
            "Echipa de Recrutare"
        ),
        "is_active": True,
    },
    {
        "message_type": "cv_request_rejected",
        "channel": "whatsapp",
        "subject": "",
        "body": (
            "Salut {first_name},\n\n"
            "Îți mulțumim că ai discutat cu noi despre poziția de {position_title}. "
            "Deși acest rol poate că nu este cea mai potrivită opțiune în acest moment, ne-ar face plăcere să păstrăm datele tale pentru oportunități viitoare.\n\n"
            "Ne poți trimite CV-ul tău dacă dorești să rămânem în legătură.\n\n"
            "Cu cele mai bune gânduri!"
        ),
        "is_active": True,
    },
    # ── Rejection ──────────────────────────────────────────────────────────
    {
        "message_type": "rejection",
        "channel": "email",
        "subject": "Your application — {position_title}",
        "body": (
            "Salut {first_name},\n\n"
            "Îți mulțumim pentru timpul acordat aplicării la poziția de {position_title} și pentru discuția avută cu noi.\n\n"
            "După o analiză atentă, am decis să mergem mai departe cu alți candidați a căror experiență se aliniază mai bine cu nevoile noastre actuale.\n\n"
            "Îți dorim mult succes în căutarea unui loc de muncă și în parcursul tău profesional.\n\n"
            "Cu stimă,\n"
            "Echipa de Recrutare"
        ),
        "is_active": True,
    },
    {
        "message_type": "rejection",
        "channel": "whatsapp",
        "subject": "",
        "body": (
            "Salut {first_name}, îți mulțumim pentru aplicarea la poziția de {position_title}. "
            "După o analiză atentă, am decis să mergem mai departe cu alți candidați. "
            "Îți dorim mult succes în căutarea unui loc de muncă!"
        ),
        "is_active": True,
    },
]


def seed_message_templates(apps, schema_editor):
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    for tpl in TEMPLATES:
        MessageTemplate.objects.get_or_create(
            message_type=tpl["message_type"],
            channel=tpl["channel"],
            defaults={
                "subject":   tpl["subject"],
                "body":      tpl["body"],
                "is_active": tpl["is_active"],
            },
        )


def unseed_message_templates(apps, schema_editor):
    MessageTemplate = apps.get_model("messaging", "MessageTemplate")
    for tpl in TEMPLATES:
        MessageTemplate.objects.filter(
            message_type=tpl["message_type"],
            channel=tpl["channel"],
        ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("messaging", "0004_add_message_template"),
    ]

    operations = [
        migrations.RunPython(
            seed_message_templates,
            reverse_code=unseed_message_templates,
        ),
    ]
