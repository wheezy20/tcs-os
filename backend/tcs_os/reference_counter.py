"""Shared sequential-numbering utility — backs every human-readable
reference number across modules (Inquiry/Application reference numbers,
Student ID roll numbers in admissions; Journal Entry and Expense IDs in
finance). Same shared-code pattern as tcs_os/text_merge.py, with one
necessary difference: text_merge.py held plain functions, but
ReferenceCounter is a real Django Model, which must belong to a
migrated app. Its existing table (admissions_referencecounter, created
by admissions/migrations/0006) and migration history predate this move
and are kept exactly as-is — `Meta.app_label = "admissions"` pins this
model to that app's migration state regardless of which file the Python
source lives in, so this is a pure file relocation with zero schema
change, not a new table or a fresh migration.

Real key formats in use, by caller (the format itself is caller-defined
— this module only guarantees atomic per-key sequencing):
  - admissions: "INQ-{year}" / "APP-{year}" — yearly reset
  - admissions: "STUDENT-{yy:02d}-{classification}" — yearly + per-classification reset
  - finance: "JE-{year}-{month:02d}" / "EXP-{year}-{month:02d}" — monthly reset

select_for_update() makes concurrent increments of the same key safe:
two simultaneous callers can't be handed the same sequence number, since
the second transaction blocks on the row lock until the first commits.
"""

from django.db import models, transaction


class ReferenceCounter(models.Model):
    """One row per (kind, period[, classification]) scope — e.g.
    key="INQ-2026" or key="JE-2026-09". See this module's own docstring
    for the real key formats in use."""

    key = models.CharField(max_length=50, unique=True)
    next_value = models.PositiveIntegerField(default=1)

    class Meta:
        app_label = "admissions"

    def __str__(self):
        return f"{self.key} → {self.next_value}"

    @classmethod
    def next_for(cls, key):
        with transaction.atomic():
            cls.objects.get_or_create(key=key, defaults={"next_value": 1})
            counter = cls.objects.select_for_update().get(key=key)
            value = counter.next_value
            counter.next_value = value + 1
            counter.save(update_fields=["next_value"])
            return value
