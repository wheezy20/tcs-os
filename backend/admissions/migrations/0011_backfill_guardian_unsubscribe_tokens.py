import secrets

from django.db import migrations


def backfill_tokens(apps, schema_editor):
    # Unconditional, not filtered by isnull — the preceding AddField
    # migration's callable default gets computed *once* by Django and
    # applied as the same literal value to every existing row (a known
    # Django gotcha for AddField + a callable default on a table with
    # existing rows), so every row already has a *non-null* but duplicate
    # value at this point, not a null one. Confirmed against the real data:
    # all 26 existing Guardians had gotten the identical token.
    Guardian = apps.get_model("admissions", "Guardian")
    for guardian in Guardian.objects.all():
        guardian.bulk_email_unsubscribe_token = secrets.token_urlsafe(32)
        guardian.save(update_fields=["bulk_email_unsubscribe_token"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("admissions", "0010_phase6_bulk_email_models"),
    ]

    operations = [
        migrations.RunPython(backfill_tokens, noop_reverse),
    ]
