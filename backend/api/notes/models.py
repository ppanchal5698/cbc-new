"""
Calls and notes against a bid (the estimator's own record).

§1.6 phase 5 is "judgment, reuse, RFIs" and it happens almost entirely on the
phone. The GC asks for the FRP scope broken out; the architect concedes that the
schedule and the elevation disagree and raises an RFI. None of that touches
email, and today none of it touches the bid file either — it lives in whoever
took the call.

That matters beyond tidiness. §1.6 records knowledge continuity as a *confirmed
priority*: estimating knowledge is concentrated in three people, and capturing
the rules and the reasoning is part of the mandate rather than a side effect. A
note attached to the bid is the cheapest possible form of that capture.

Deliberately not a workflow. There is no assignment, no due date and no status —
a note is a record of something that already happened, and anything more would be
a task tracker nobody asked for.
"""

import uuid

from django.conf import settings
from django.db import models
from projects.models import Project, TimestampedModel

from shared.enums import NoteKind


class Note(TimestampedModel):
    """One logged call or note, against a bid."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="notes")

    kind = models.CharField(
        max_length=32,
        choices=NoteKind.choices(),
        default=NoteKind.INTERNAL.value,
        db_index=True,
    )
    who = models.CharField(
        max_length=255, blank=True, default="", help_text="Who was on the call."
    )
    org = models.CharField(
        max_length=255, blank=True, default="", help_text="Their company."
    )
    body = models.TextField(help_text="What was said. The whole point of the row.")

    #: What it is about — an opening mark, a price book, the bid itself. Free text
    #: rather than a foreign key on purpose: a call ranges across a bid set faster
    #: than a schema can, and refusing to record one because its subject does not
    #: resolve to a row would lose the note entirely.
    ref = models.CharField(max_length=255, blank=True, default="")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notes_logged",
    )

    class Meta:
        indexes = [models.Index(fields=["project", "-created_at"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.kind} · {self.who or 'internal'} · {self.body[:40]}"
