from django.db import migrations, models


def backfill_guardian_names(apps, schema_editor):
    """Split existing Guardian.full_name into first_name/surname before the
    column is dropped, so pre-existing rows (e.g. Phase 1 test submissions)
    don't silently lose their name."""
    Guardian = apps.get_model("admissions", "Guardian")
    for guardian in Guardian.objects.all():
        parts = guardian.full_name.strip().split(" ", 1) if guardian.full_name else [""]
        guardian.first_name = parts[0]
        guardian.surname = parts[1] if len(parts) > 1 else ""
        guardian.save(update_fields=["first_name", "surname"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("admissions", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="application",
            name="month_of_enrollment",
            field=models.CharField(blank=True, default="", max_length=50),
        ),
        migrations.AddField(
            model_name="family",
            name="comments",
            field=models.TextField(blank=True, default="", help_text="Free-text comment/question from the enquiring family"),
        ),
        migrations.AddField(
            model_name="family",
            name="referral_source",
            field=models.CharField(
                blank=True,
                choices=[
                    ("current_parent", "Current parent of TCS"),
                    ("former_parent", "Former parent of TCS"),
                    ("parent_referral", "Parent referral"),
                    ("staff_referral", "Staff referral"),
                    ("website", "Website"),
                    ("friend_colleague", "Friend/Colleague"),
                    ("social_media", "Social Media"),
                    ("other", "Other"),
                ],
                default="",
                max_length=30,
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="referral_source_other",
            field=models.CharField(blank=True, default="", help_text="Set when referral_source is 'other'", max_length=255),
        ),
        migrations.AddField(
            model_name="guardian",
            name="address",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="guardian",
            name="first_name",
            field=models.CharField(default="", max_length=255),
        ),
        migrations.AddField(
            model_name="guardian",
            name="religion",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="guardian",
            name="surname",
            field=models.CharField(default="", max_length=255),
        ),
        migrations.AddField(
            model_name="guardian",
            name="town_city",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="student",
            name="current_grade",
            field=models.CharField(blank=True, default="", help_text="Learner's current grade/class", max_length=50),
        ),
        migrations.AddField(
            model_name="student",
            name="current_school",
            field=models.CharField(blank=True, default="", help_text="e.g. 'N/A' if not yet enrolled anywhere", max_length=255),
        ),
        migrations.RunPython(backfill_guardian_names, noop_reverse),
        migrations.RemoveField(
            model_name="guardian",
            name="full_name",
        ),
        migrations.AlterField(
            model_name="application",
            name="academic_year",
            field=models.CharField(help_text="e.g. 2026", max_length=9),
        ),
        migrations.AlterField(
            model_name="guardian",
            name="relationship",
            field=models.CharField(
                choices=[("mother", "Mother"), ("father", "Father"), ("guardian", "Legal Guardian"), ("other", "Other")],
                max_length=20,
            ),
        ),
    ]
