"""
The validation gate (§5.6) — the load-bearing part of the whole system.

    Textract produces deterministic geometry. The model produces semantic
    interpretation and must cite Textract-normalised element IDs for every field
    it emits. A field whose citation cannot be validated is **rejected, not
    repaired**. "Show me the source" is a database join, never a second inference.

**Two checks, not one.** Every source document specified citation-existence
validation. None specified value grounding, and that is a real hole: a model can
cite a perfectly valid ``element_id`` and still emit a value that does not appear
in it. The citation passes, the value is fabricated, and the estimator sees a
confident wrong answer with a clickable source that does not say what the system
claims it says. That is worse than no citation at all.

Rejection behaviour, non-negotiable:

* Rejected fields are **flagged for estimator review**, never repaired, never
  silently dropped, never retried into acceptance.
* One schema-repair retry is permitted **only** for output that fails JSON-schema
  validation. Never for a semantic rejection. Never loop.
* Rejection counts per prompt version are a monitored metric — a rising rejection
  rate is the earliest warning that a prompt change or a model bump has degraded
  quality.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum

from rapidfuzz import fuzz


class Verdict(StrEnum):
    ACCEPT = "ACCEPT"
    ACCEPT_NULL = "ACCEPT_NULL"
    REJECT = "REJECT"


class RejectionCode(StrEnum):
    """Why a field was refused. Recorded so rejections can be counted by cause."""

    UNKNOWN_ELEMENT = "cited element_id not in supplied set"
    NULL_WITH_CITATION = "null value with non-empty citation"
    VALUE_WITHOUT_CITATION = "value with no citation"
    NOT_GROUNDED = "value not grounded in cited element text"


@dataclass
class FieldVerdict:
    """The gate's decision about one field."""

    verdict: Verdict
    code: RejectionCode | None = None
    detail: str = ""
    grounding_score: float | None = None
    unknown_ids: list[str] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.verdict in (Verdict.ACCEPT, Verdict.ACCEPT_NULL)

    @property
    def reason(self) -> str:
        if self.code is None:
            return ""
        return f"{self.code.value}{': ' + self.detail if self.detail else ''}"


# ---------------------------------------------------------------------------
# Check 2 — value grounding
# ---------------------------------------------------------------------------

_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace. Nothing semantic."""
    return _SPACE.sub(" ", _PUNCT.sub(" ", str(text).casefold())).strip()


def grounding_score(value: str, cited_text: str) -> float:
    """
    How present ``value`` is in ``cited_text``, as 0-100.

    A **normalised containment test** with a similarity floor to tolerate OCR noise
    and hyphenation — and **never** a semantic comparison. It is checking that the
    string is *there*, not that it *means the same thing*. A model that returns
    "satin chrome" citing a cell reading "US26D" must fail this, even though the
    two describe the same finish, because the system's claim is that the value came
    from that cell.
    """
    needle = normalise(value)
    haystack = normalise(cited_text)
    if not needle:
        return 0.0
    if not haystack:
        return 0.0
    if needle in haystack:
        return 100.0
    return float(fuzz.partial_ratio(needle, haystack))


def grounded(value: str, cited_text: str, *, min_ratio: int = 90) -> tuple[bool, float]:
    score = grounding_score(value, cited_text)
    return score >= min_ratio, score


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------

def validate_field(
    *,
    value: str | None,
    source_element_ids: list[str],
    supplied_elements: dict[str, str],
    min_ratio: int = 90,
) -> FieldVerdict:
    """
    Run both gates over one extracted field.

    ``supplied_elements`` maps ``element_id`` to its text and is **exactly the set
    handed to the model** — not every element in the document. Validating against
    the whole document would let a model cite a real element it was never shown,
    which is a hallucination that happens to land on something true.
    """
    # -- Check 1: citation existence ---------------------------------------
    unknown = [eid for eid in source_element_ids if eid not in supplied_elements]
    if unknown:
        return FieldVerdict(
            Verdict.REJECT,
            RejectionCode.UNKNOWN_ELEMENT,
            f"{len(unknown)} unknown id(s): {unknown[:3]}",
            unknown_ids=unknown,
        )

    # -- Null handling ------------------------------------------------------
    if value is None or str(value).strip() == "":
        if source_element_ids:
            # Citing a source for "nothing is here" is incoherent: either the cell
            # says something, or there is nothing to point at.
            return FieldVerdict(
                Verdict.REJECT,
                RejectionCode.NULL_WITH_CITATION,
                f"null value cited {len(source_element_ids)} element(s)",
            )
        # A genuine absence. "This opening has no fire rating" is a labelled fact,
        # not a missing answer (§5.10).
        return FieldVerdict(Verdict.ACCEPT_NULL)

    if not source_element_ids:
        return FieldVerdict(
            Verdict.REJECT,
            RejectionCode.VALUE_WITHOUT_CITATION,
            f"value {str(value)[:40]!r} has no citation",
        )

    # -- Check 2: value grounding ------------------------------------------
    cited_text = " ".join(supplied_elements[eid] for eid in source_element_ids)
    is_grounded, score = grounded(str(value), cited_text, min_ratio=min_ratio)
    if not is_grounded:
        return FieldVerdict(
            Verdict.REJECT,
            RejectionCode.NOT_GROUNDED,
            f"{str(value)[:40]!r} scores {score:.0f} against cited text "
            f"{cited_text[:60]!r} (floor {min_ratio})",
            grounding_score=score,
        )

    return FieldVerdict(Verdict.ACCEPT, grounding_score=score)


# ---------------------------------------------------------------------------
# Composite confidence (§5.9)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Confidence:
    """
    Composite confidence with every component retained.

    Properties this guarantees, all asserted in tests:

    * ``final`` can never exceed either input. A confident model reading a blurry
      cell does not produce a confident result.
    * Every component is **stored**, not just the product, so a score can be
      explained rather than merely displayed.
    * A missing expected field drags the score down through the penalty rather
      than being invisible.
    """

    ocr: float | None
    llm: float | None
    completeness_penalty: float
    final: float | None

    def as_dict(self) -> dict:
        return {
            "ocr_confidence": self.ocr,
            "llm_confidence": self.llm,
            "completeness_penalty": self.completeness_penalty,
            "final_confidence": self.final,
        }


def completeness_penalty(fields_populated: int, fields_expected: int) -> float:
    """
    ``f(populated / expected)``, stored rather than merely applied.

    Linear rather than clever: the point is that a half-empty opening cannot score
    as highly as a complete one, and any monotonic function achieves that. A
    complicated curve would be a tuning parameter nobody calibrated.
    """
    if fields_expected <= 0:
        return 1.0
    return max(0.0, min(1.0, fields_populated / fields_expected))


def compose(
    *,
    element_confidences: list[float | None],
    llm_confidence: float | None,
    penalty: float,
) -> Confidence:
    """
    ``final = min(ocr, llm) * completeness_penalty`` (§5.9).

    ``ocr`` is the **minimum** across cited elements, not the mean: a field whose
    value spans a crisp cell and a smudged one is only as trustworthy as the
    smudged one.

    A ``None`` element confidence means the text was read from the PDF's own text
    layer rather than recognised (the NATIVE_TEXT route). Those are skipped rather
    than treated as 1.0 — claiming perfect OCR confidence for something never
    OCR'd would inflate the score past what any measurement supports.
    """
    measured = [c for c in element_confidences if c is not None]
    ocr = min(measured) if measured else None

    if ocr is None and llm_confidence is None:
        final = None
    elif ocr is None:
        final = llm_confidence * penalty
    elif llm_confidence is None:
        final = ocr * penalty
    else:
        final = min(ocr, llm_confidence) * penalty

    return Confidence(ocr=ocr, llm=llm_confidence, completeness_penalty=penalty, final=final)
