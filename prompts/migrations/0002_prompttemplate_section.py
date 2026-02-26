# Generated manually — adds section field to PromptTemplate.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("prompts", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="prompttemplate",
            name="section",
            field=models.CharField(
                blank=True,
                choices=[
                    ("system_prompt",        "System Prompt"),
                    ("first_message",        "First Message"),
                    ("qualification_prompt", "Qualification Prompt"),
                ],
                db_index=True,
                max_length=30,
                null=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="prompttemplate",
            options={
                "ordering": ["section", "-version"],
                "verbose_name": "Prompt Template",
                "verbose_name_plural": "Prompt Templates",
            },
        ),
    ]
