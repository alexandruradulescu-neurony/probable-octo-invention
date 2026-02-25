from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('calls', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='call',
            name='eleven_labs_batch_id',
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
    ]
