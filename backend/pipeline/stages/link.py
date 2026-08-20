"""
LINK — citation validation, value grounding, and persistence (§3.3 step 9, §5.6).

    Any field citing an unknown element, or whose value is not grounded in its
    cited text, is **REJECTED and flagged — never repaired, never silently
    dropped.** ``field_provenance`` rows written with composite confidence.

This is the stage where a model's claim becomes a database fact, and it is the
last point at which a fabrication can be stopped. Everything downstream — the
grid, the matcher, the price, the PDF — treats a persisted field as true.

Persistence notes that matter:

* ``page_number`` and the union bbox are **denormalised onto ``field_provenance``**
  so the openings grid reads one table instead of traversing
  ``field_provenance -> field_provenance_elements -> doc_elements`` for every field
  of every opening (bottleneck B12).
* Rejected fields are still written, with ``review_state=REJECTED`` and a reason.
  Dropping them would hide the rejection from the estimator and from the
  citation-rejection metric that is the earliest drift warning (§11.5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pipeline.llm.schemas.extraction import OPENING_FIELDS, ZERO_TOLERANCE_FIELDS
from pipeline.llm.validators.gate import (
    Verdict,
    completeness_penalty,
    compose,
    validate_field,
)
from pipeline.parsers import fire_rating as fire_rating_parser
from pipeline.parsers import handing as handing_parser
from pipeline.parsers import size as size_parser
from shared.config import get_settings
from shared.enums import FireRatingLocation, ReviewState

log = logging.getLogger("cbc.link")


@dataclass
class LinkStats:
    """Counters for ``extraction_metrics`` (§5.6, §11.5)."""

    fields_emitted: int = 0
    fields_accepted: int = 0
    fields_rejected_citation: int = 0
    fields_rejected_grounding: int = 0
    fields_flagged_low_confidence: int = 0
    fields_null_with_citation: int = 0
    openings_written: int = 0

    def as_dict(self) -> dict:
        return {
            "fields_emitted": self.fields_emitted,
            "fields_accepted": self.fields_accepted,
            "fields_rejected_citation": self.fields_rejected_citation,
            "fields_rejected_grounding": self.fields_rejected_grounding,
            "fields_flagged_low_confidence": self.fields_flagged_low_confidence,
            "fields_null_with_citation": self.fields_null_with_citation,
        }


@dataclass
class LinkedField:
    """One validated field, ready to persist."""

    name: str
    value: str | None
    element_ids: list[str] = field(default_factory=list)
    review_state: str = ReviewState.AUTO.value
    rejection_reason: str | None = None
    ocr_confidence: float | None = None
    llm_confidence: float | None = None
    completeness_penalty: float = 1.0
    final_confidence: float | None = None
    grounding_score: float | None = None
    page_number: int | None = None
    bbox: tuple[float, float, float, float] | None = None

    @property
    def accepted(self) -> bool:
        return self.review_state != ReviewState.REJECTED.value


def threshold_for(field_name: str) -> float:
    """
    The confidence floor for one field (§5.9 item 4).

    **Per-field, not one global number.** Fire rating and handing warrant a
    stricter threshold than hardware group because their cost of error is
    categorically different — a wrong hardware group is a pricing error, a wrong
    rating is a code-compliance failure.
    """
    settings_obj = get_settings()
    if field_name == "fire_rating":
        return settings_obj.confidence_threshold_fire_rating
    if field_name == "handing":
        return settings_obj.confidence_threshold_handing
    return settings_obj.confidence_threshold_default


def _union_bbox(elements: list) -> tuple[float, float, float, float] | None:
    boxes = [
        (e.bbox_x_min, e.bbox_y_min, e.bbox_x_max, e.bbox_y_max)
        for e in elements
        if e.bbox_x_min is not None
    ]
    if not boxes:
        return None
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def link_opening(
    record: dict,
    *,
    supplied_elements: dict[str, str],
    element_rows: dict[str, object],
    stats: LinkStats,
) -> list[LinkedField]:
    """
    Validate every field of one opening record and compute its confidences.

    ``supplied_elements`` is exactly the set the model was shown for this table —
    not the whole document. Validating against everything would let a model cite a
    real element it never saw.
    """
    settings_obj = get_settings()
    fields = record.get("fields", {}) or {}

    populated = sum(
        1
        for name in OPENING_FIELDS
        if (fields.get(name) or {}).get("value") not in (None, "")
    )
    penalty = completeness_penalty(populated, len(OPENING_FIELDS))

    linked: list[LinkedField] = []
    for name in OPENING_FIELDS:
        emitted = fields.get(name)
        if emitted is None:
            # The model omitted the key entirely. That is an absence, and it is
            # recorded as one rather than skipped, so the grid can show "not
            # extracted" distinctly from "extracted as null" (FR-8).
            linked.append(
                LinkedField(
                    name=name,
                    value=None,
                    review_state=ReviewState.FLAGGED.value,
                    rejection_reason="field not returned by the model",
                    completeness_penalty=penalty,
                )
            )
            continue

        stats.fields_emitted += 1
        value = emitted.get("value")
        ids = list(emitted.get("source_element_ids") or [])
        llm_confidence = emitted.get("confidence_llm")

        verdict = validate_field(
            value=value,
            source_element_ids=ids,
            supplied_elements=supplied_elements,
            min_ratio=settings_obj.grounding_min_ratio,
        )

        if verdict.verdict is Verdict.REJECT:
            code = verdict.code.value if verdict.code else ""
            if "grounded" in code:
                stats.fields_rejected_grounding += 1
            elif "null value" in code:
                stats.fields_null_with_citation += 1
                stats.fields_rejected_citation += 1
            else:
                stats.fields_rejected_citation += 1

            # Persisted, not dropped. A rejection the estimator cannot see is a
            # silent omission, which is what NFR-2 forbids.
            linked.append(
                LinkedField(
                    name=name,
                    value=value,
                    element_ids=[],
                    review_state=ReviewState.REJECTED.value,
                    rejection_reason=verdict.reason,
                    llm_confidence=llm_confidence,
                    completeness_penalty=penalty,
                    grounding_score=verdict.grounding_score,
                )
            )
            continue

        cited_rows = [element_rows[eid] for eid in ids if eid in element_rows]
        confidence = compose(
            element_confidences=[getattr(r, "ocr_confidence", None) for r in cited_rows],
            llm_confidence=llm_confidence,
            penalty=penalty,
        )

        review_state = ReviewState.AUTO.value
        reason = None
        if verdict.verdict is Verdict.ACCEPT_NULL:
            stats.fields_accepted += 1
            if name in ZERO_TOLERANCE_FIELDS:
                # A missing rating or handing is a finding that must be confirmed,
                # never an accepted silence (§5.8).
                review_state = ReviewState.FLAGGED.value
                reason = f"{name} is absent and must be confirmed, not assumed"
        else:
            stats.fields_accepted += 1
            floor = threshold_for(name)
            if confidence.final is not None and confidence.final < floor:
                review_state = ReviewState.FLAGGED.value
                reason = f"confidence {confidence.final:.2f} below the {name} floor {floor:.2f}"
                stats.fields_flagged_low_confidence += 1
            elif name in ZERO_TOLERANCE_FIELDS and confidence.final is None:
                # No measurable confidence on a zero-tolerance field is not a pass.
                review_state = ReviewState.FLAGGED.value
                reason = f"{name} has no measurable confidence; requires confirmation"
                stats.fields_flagged_low_confidence += 1

        linked.append(
            LinkedField(
                name=name,
                value=value,
                element_ids=ids,
                review_state=review_state,
                rejection_reason=reason,
                ocr_confidence=confidence.ocr,
                llm_confidence=confidence.llm,
                completeness_penalty=penalty,
                final_confidence=confidence.final,
                grounding_score=verdict.grounding_score,
                page_number=cited_rows[0].page_number if cited_rows else None,
                bbox=_union_bbox(cited_rows),
            )
        )

    return linked


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_opening(
    *, project, extraction_run, record: dict, linked: list[LinkedField], stats: LinkStats
):
    """
    Write one ``openings`` row plus its ``field_provenance`` rows.

    The deterministic parsers (§5.7) run here, not in the model: the raw string is
    what the model returned and what was grounded; the typed value is what code
    derived from it. **Both are stored.**
    """
    from django.db import transaction
    from openings.models import FieldProvenance, FieldProvenanceElement, Opening

    by_name = {item.name: item for item in linked}

    def raw(name: str) -> str | None:
        item = by_name.get(name)
        return item.value if item and item.accepted else None

    door_number = raw("door_number") or record.get("opening_id") or ""
    if not door_number:
        log.warning("skipping a record with no door number; everything keys off it")
        return None

    parsed_size = size_parser.parse_size(raw("size"))
    parsed_rating = fire_rating_parser.parse_fire_rating(raw("fire_rating"))
    parsed_handing = handing_parser.parse_handing(raw("handing"))

    model_flagged = bool(record.get("needs_review"))
    any_rejected = any(not item.accepted for item in linked)
    any_flagged = any(item.review_state == ReviewState.FLAGGED.value for item in linked)
    needs_review = (
        model_flagged
        or any_rejected
        or any_flagged
        or parsed_size.needs_review
        or parsed_rating.needs_review
        or parsed_handing.needs_review
    )

    with transaction.atomic():
        opening = Opening.objects.create(
            project=project,
            extraction_run=extraction_run,
            door_number=door_number,
            size_raw=parsed_size.raw or None,
            width_inches=parsed_size.width_inches,
            height_inches=parsed_size.height_inches,
            handing=parsed_handing.value,
            # Explicit booleans, distinct from null: FR-8 needs "absent",
            # "not extracted", and "rejected" to be three different states.
            handing_absent=parsed_handing.absent,
            finish_raw=raw("finish"),
            fire_rating_raw=parsed_rating.raw or None,
            fire_rating_minutes=parsed_rating.minutes,
            fire_rating_absent=parsed_rating.absent,
            fire_rating_source_location=FireRatingLocation.DOOR_SCHEDULE.value,
            hardware_group=raw("hardware_group"),
            alternate_designation=raw("alternate_designation"),
            review_state=(
                ReviewState.FLAGGED.value if needs_review else ReviewState.AUTO.value
            ),
            review_notes="; ".join(
                note
                for note in (
                    record.get("review_reason"),
                    parsed_size.reason,
                    parsed_rating.reason,
                    parsed_handing.reason,
                )
                if note
            ),
        )

        for item in linked:
            provenance = FieldProvenance.objects.create(
                extraction_run=extraction_run,
                opening=opening,
                field_name=item.name,
                extracted_value=item.value,
                ocr_confidence=item.ocr_confidence,
                llm_confidence=item.llm_confidence,
                completeness_penalty=item.completeness_penalty,
                final_confidence=item.final_confidence,
                grounding_score=item.grounding_score,
                page_number=item.page_number,
                bbox_x_min=item.bbox[0] if item.bbox else None,
                bbox_y_min=item.bbox[1] if item.bbox else None,
                bbox_x_max=item.bbox[2] if item.bbox else None,
                bbox_y_max=item.bbox[3] if item.bbox else None,
                review_state=item.review_state,
                rejection_reason=item.rejection_reason,
            )
            # Citations are written ONLY for accepted fields. A rejected field's
            # ids are recorded in the reason text, not as foreign keys — writing
            # them would assert a link the gate just refused.
            for ordinal, element_id in enumerate(item.element_ids):
                FieldProvenanceElement.objects.create(
                    field_provenance=provenance, doc_element_id=element_id, ordinal=ordinal
                )

    stats.openings_written += 1
    return opening
