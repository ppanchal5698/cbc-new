"""
What happens when extraction fails (§9 B8, §10.3 item 8, NFR-2).

All three of these came out of one real log. A bid set was read, normalised and
committed — 12,728 elements — and then extraction hit invalid Bedrock
credentials. The document sat at PROCESSING indefinitely, the locate pass had
degraded to "extract all 95 tables", and nothing cached the calls that had
already been paid for.

Separately each is a papercut. Together, one transient blip on a working
deployment costs ninety-five premium-model calls, three times over, and still
strands the document.
"""

import pytest
from botocore.exceptions import ClientError
from factories import DocumentFactory, ExtractionRunFactory
from openings.models import TableExtraction
from projects.models import PipelineJob

from pipeline import coordinator
from pipeline.stages.run_extraction import (
    TableBudgetExceeded,
    _assert_within_table_budget,
    _remember_extraction,
)
from shared.enums import DocumentStatus, PipelineJobStatus, PipelineStage

pytestmark = pytest.mark.django_db


def bedrock_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, "Converse")


# ---------------------------------------------------------------------------
# 1 — a failed extraction must not leave the document mid-sentence
# ---------------------------------------------------------------------------

class TestTheDocumentNeverStrands:
    def test_bad_credentials_fail_the_document_rather_than_stranding_it(self, monkeypatch):
        """
        `UnrecognizedClientException` will fail identically on every delivery.

        Before the fix this propagated with nothing setting the status, so the
        document stayed PROCESSING — which the UI renders as "Reading", a spinner
        over an extraction that had definitively failed.
        """
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)
        monkeypatch.setattr(
            coordinator, "_extract_and_link", coordinator._extract_and_link
        )

        from pipeline.stages import run_extraction

        monkeypatch.setattr(
            run_extraction, "run",
            lambda _doc: (_ for _ in ()).throw(bedrock_error("UnrecognizedClientException")),
        )

        # Must not raise: retrying settles nothing.
        coordinator._extract_and_link(document)

        document.refresh_from_db()
        assert document.status == DocumentStatus.FAILED.value
        assert "UnrecognizedClientException" in document.status_detail
        extract = PipelineJob.objects.get(document=document, stage=PipelineStage.EXTRACT.value)
        assert extract.status == PipelineJobStatus.FAILED.value

    def test_a_throttle_is_re_raised_so_the_message_is_redelivered(self, monkeypatch):
        """A throttle is exactly what redelivery exists for."""
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)
        from pipeline.stages import run_extraction

        monkeypatch.setattr(
            run_extraction, "run",
            lambda _doc: (_ for _ in ()).throw(bedrock_error("ThrottlingException")),
        )

        with pytest.raises(ClientError):
            coordinator._extract_and_link(document)

        document.refresh_from_db()
        assert document.status == DocumentStatus.PROCESSING.value, "a retryable failure is not final"

    def test_no_model_pinned_still_skips_rather_than_failing(self, monkeypatch):
        """
        C5: with no model ID pinned there is nothing to invoke, but normalisation
        succeeded and the source viewer works. The skip is recorded; the document
        is not failed.
        """
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)
        from pipeline.stages import run_extraction
        from shared.config import ConfigError

        monkeypatch.setattr(
            run_extraction, "run",
            lambda _doc: (_ for _ in ()).throw(ConfigError("BEDROCK_MODEL_ID is not set")),
        )

        coordinator._extract_and_link(document)

        extract = PipelineJob.objects.get(document=document, stage=PipelineStage.EXTRACT.value)
        assert extract.status == PipelineJobStatus.SKIPPED.value
        document.refresh_from_db()
        assert document.status != DocumentStatus.FAILED.value


# ---------------------------------------------------------------------------
# 2 — the locate fallback needs the ceiling preprocessing already has
# ---------------------------------------------------------------------------

class TestTableBudget:
    def test_a_normal_bid_set_passes(self):
        _assert_within_table_budget(6, located=True, ceiling=40)

    def test_the_fallback_cannot_spend_without_a_limit(self):
        """
        The real log: the locate pass failed and all 95 tables were queued.
        """
        with pytest.raises(TableBudgetExceeded, match="Nothing has been spent"):
            _assert_within_table_budget(95, located=False, ceiling=40)

    def test_the_message_names_the_locate_failure_as_the_likely_cause(self):
        with pytest.raises(TableBudgetExceeded, match="locate pass failed"):
            _assert_within_table_budget(95, located=False, ceiling=40)

    def test_a_genuinely_large_document_is_described_as_such(self):
        """Same guard, different cause — and the operator needs to tell them apart."""
        with pytest.raises(TableBudgetExceeded, match="genuinely carries"):
            _assert_within_table_budget(95, located=True, ceiling=40)

    def test_going_over_quarantines_rather_than_extracting_the_first_forty(self, monkeypatch):
        """
        A silently partial read is the omission NFR-2 forbids: the estimator would
        see openings and have no way to know the rest were never looked at.
        """
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)
        from pipeline.stages import run_extraction

        monkeypatch.setattr(
            run_extraction, "run",
            lambda _doc: (_ for _ in ()).throw(TableBudgetExceeded("95 tables, over the guard")),
        )

        coordinator._extract_and_link(document)

        document.refresh_from_db()
        assert document.status == DocumentStatus.QUARANTINED.value
        extract = PipelineJob.objects.get(document=document, stage=PipelineStage.EXTRACT.value)
        assert extract.status == PipelineJobStatus.QUARANTINED.value


# ---------------------------------------------------------------------------
# 3 — §9 B8: a retry storm cannot double-bill
# ---------------------------------------------------------------------------

class TestExtractionIsPaidForOnce:
    def test_an_answer_is_kept_for_the_next_delivery(self):
        document = DocumentFactory()
        payload = [{"opening_id": "101", "fields": {}}]

        class Response:
            input_tokens, output_tokens, cache_read_tokens = 900, 120, 0

        _remember_extraction(document, "table-1", "v2", "model-premium", payload, Response())

        row = TableExtraction.objects.get(document=document, table_id="table-1")
        assert row.payload == payload
        assert row.prompt_version == "v2"
        assert row.model_id == "model-premium", "a cached answer stays attributable (C5)"

    def test_a_second_delivery_overwrites_rather_than_colliding(self):
        """
        Two workers racing a redelivered message must not turn a cost optimisation
        into an integrity error.
        """
        document = DocumentFactory()
        for tokens in (100, 200):
            class Response:
                input_tokens, output_tokens, cache_read_tokens = tokens, 0, 0

            _remember_extraction(document, "table-1", "v2", "m", [{"n": tokens}], Response())

        assert TableExtraction.objects.filter(document=document, table_id="table-1").count() == 1

    def test_a_new_prompt_version_is_a_different_question(self):
        """
        §8.2: prompts are versioned artefacts. An answer to v2 is not an answer to
        v3, and reusing it would attribute a value to a prompt that never produced it.
        """
        document = DocumentFactory()

        class Response:
            input_tokens = output_tokens = cache_read_tokens = 0

        _remember_extraction(document, "table-1", "v2", "m", [{"v": 2}], Response())
        _remember_extraction(document, "table-1", "v3", "m", [{"v": 3}], Response())

        assert TableExtraction.objects.filter(document=document, table_id="table-1").count() == 2

    def test_the_cache_is_scoped_to_its_document(self):
        run = ExtractionRunFactory()

        class Response:
            input_tokens = output_tokens = cache_read_tokens = 0

        _remember_extraction(run.document, "table-1", "v2", "m", [{"a": 1}], Response())
        other = DocumentFactory()
        assert not TableExtraction.objects.filter(document=other).exists()


# ---------------------------------------------------------------------------
# 4 — the status a document ends on must be the one extraction left
# ---------------------------------------------------------------------------

class TestNormaliseDoesNotOverwriteASettledStatus:
    """
    The first cut of the fix above created its own misreport.

    Extraction correctly quarantined the document, and then `normalise_document`
    set PROCESSED on the way out regardless — so the board read "Ready" over a
    bid set whose schedules had never been looked at. That is worse than the
    stranding it replaced: a spinner is at least honest about being unfinished.
    """

    @staticmethod
    def _normalise(document, monkeypatch, outcome):
        from pipeline.stages import normalize as normalize_stage

        monkeypatch.setattr(coordinator, "_routed_pages", lambda _doc: [(1, "TABLES")])
        monkeypatch.setattr(normalize_stage, "parse_blocks", lambda *a, **k: [])
        monkeypatch.setattr(normalize_stage, "bulk_insert_elements", lambda *a, **k: 12728)
        monkeypatch.setattr(coordinator, "_extract_and_link", outcome)
        return coordinator.normalise_document(document, {"Blocks": []})

    def test_a_quarantined_extraction_is_not_reported_as_processed(self, monkeypatch):
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)

        def quarantine(doc):
            from pipeline.db import repository as repo

            repo.set_document_status(doc, DocumentStatus.QUARANTINED, "over the table guard")
            return True

        written = self._normalise(document, monkeypatch, quarantine)

        assert written == 12728, "the elements are committed either way"
        document.refresh_from_db()
        assert document.status == DocumentStatus.QUARANTINED.value

    def test_a_clean_run_still_lands_on_processed(self, monkeypatch):
        document = DocumentFactory(status=DocumentStatus.PROCESSING.value)

        self._normalise(document, monkeypatch, lambda _doc: False)

        document.refresh_from_db()
        assert document.status == DocumentStatus.PROCESSED.value
