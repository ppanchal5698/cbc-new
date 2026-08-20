"""
The Django-to-worker trigger (§3.3 step 2, §3.2 rule 2).

Enqueue happens on the transition INTO ``READY_FOR_PROCESSING`` and on no other
save. Firing on every save of a READY document would re-enqueue on each status
poll; firing on creation would enqueue documents whose upload had not completed.
"""

import logging

from django.db import transaction
from django.db.models.signals import post_init, post_save
from django.dispatch import receiver

from shared.enums import DocumentStatus

from .models import Document
from .queue_ops import enqueue_document_ready

log = logging.getLogger("cbc.signals")


@receiver(post_init, sender=Document)
def _remember_status(sender, instance: Document, **kwargs):
    """Snapshot the loaded status so post_save can see a genuine transition."""
    instance._loaded_status = instance.status


@receiver(post_save, sender=Document)
def enqueue_on_ready(sender, instance: Document, created: bool, **kwargs):
    previous = None if created else getattr(instance, "_loaded_status", None)
    if instance.status != DocumentStatus.READY_FOR_PROCESSING.value:
        instance._loaded_status = instance.status
        return
    if previous == DocumentStatus.READY_FOR_PROCESSING.value:
        return  # already enqueued on the transition that set it

    # on_commit, not inline: the worker is a separate process and can consume the
    # message before this transaction commits, finding no document row.
    transaction.on_commit(lambda: _safe_enqueue(instance))
    instance._loaded_status = instance.status


def _safe_enqueue(document: Document) -> None:
    try:
        enqueue_document_ready(document)
    except Exception:
        # The PipelineJob row is written before the publish, so the work remains
        # discoverable and redrivable. Failing the estimator's upload request
        # because a queue was briefly unreachable would be the worse outcome.
        log.exception("failed to enqueue document %s; job row remains PENDING", document.id)
