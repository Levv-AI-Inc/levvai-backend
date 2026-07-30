from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("workers", "0001_initial"),
    ]

    # Intentionally empty. Approved work orders proceed directly to supplier
    # acceptance and worker onboarding; they do not create Engagement records.
    operations = []
