"""
The matching engine (FR-4, §6.1).

Phase 3's exit criterion, quoted from §12.2:

    A rated opening never matches an unrated item, and an ``LH`` opening never
    matches an ``RH``-only SKU — **regardless of how high the text similarity
    scores**. Below-cutoff matches route to the manual path instead of
    auto-proposing a line.

The "regardless of text similarity" clause is the one worth attacking, so the
hard-constraint tests deliberately construct candidates whose descriptions are
*identical* to what the opening wants. If a soft score could ever overturn a hard
constraint, these are the tests that catch it.
"""

import pytest
from factories import CatalogItemFactory, FinishCodeFactory, OpeningFactory, ProjectFactory
from openings.models import Match

from pipeline.stages.match import (
    CatalogSnapshot,
    MatchCriteria,
    check_division,
    check_handing,
    check_rating,
    match_criteria,
    match_opening,
    match_project,
    persist_matches,
    score_candidate,
    score_finish,
)
from shared.enums import MatchStatus

pytestmark = pytest.mark.django_db


def criteria(**overrides) -> MatchCriteria:
    """A resolved 90-minute LH opening — every zero-tolerance field answered."""
    base = {
        "description": "Full mortise butt hinge",
        "csi_division": "08",
        "fire_rating_minutes": 90,
        "fire_rating_absent": False,
        "handing": "LH",
        "handing_absent": False,
    }
    base.update(overrides)
    return MatchCriteria(**base)


# ---------------------------------------------------------------------------
# THE PHASE 3 EXIT CRITERIA
# ---------------------------------------------------------------------------

class TestHardConstraintsBeatTextSimilarity:
    def test_a_rated_opening_never_matches_an_unrated_item(self):
        """
        The candidate's description is a perfect match. It is still disqualified.

        Rated hardware is a distinct certified product line, not a spec note.
        """
        item = CatalogItemFactory(
            description="Full mortise butt hinge",  # identical text
            fire_rating_minutes=None,
            handing=None,
            csi_division="08",
        )
        candidate = score_candidate(criteria(), item)
        assert candidate.text_score == pytest.approx(1.0)
        assert candidate.rating_ok is False
        assert candidate.eligible is False
        assert candidate.match_confidence == 0.0
        assert "unrated" in candidate.rejection_reason

    def test_an_lh_opening_never_matches_an_rh_only_sku(self):
        item = CatalogItemFactory(
            description="Full mortise butt hinge",  # identical text
            fire_rating_minutes=90,
            handing="RH",
            csi_division="08",
        )
        candidate = score_candidate(criteria(), item)
        assert candidate.text_score == pytest.approx(1.0)
        assert candidate.handing_ok is False
        assert candidate.eligible is False
        assert candidate.match_confidence == 0.0
        assert "separate SKUs" in candidate.rejection_reason

    def test_a_division_10_accessory_never_matches_a_division_08_opening(self):
        item = CatalogItemFactory(
            description="Full mortise butt hinge",
            fire_rating_minutes=90,
            handing=None,
            csi_division="10",
        )
        candidate = score_candidate(criteria(), item)
        assert candidate.division_ok is False
        assert candidate.eligible is False

    def test_a_disqualified_candidate_can_never_outrank_an_eligible_one(self):
        """
        A residual score on a disqualified candidate would let it sort above a
        valid one in any code that forgot to filter. Zero makes that impossible.
        """
        perfect_but_unrated = CatalogItemFactory(
            sku="PERFECT-UNRATED",
            description="Full mortise butt hinge",
            fire_rating_minutes=None,
            handing="LH",
            is_stock=True,
            csi_division="08",
        )
        poor_but_valid = CatalogItemFactory(
            sku="POOR-VALID",
            description="something entirely unrelated",
            fire_rating_minutes=90,
            handing="LH",
            is_stock=False,
            csi_division="08",
        )
        snapshot = CatalogSnapshot([perfect_but_unrated, poor_but_valid])
        result = match_criteria(criteria(), snapshot, cutoff=0.0)
        assert [c.sku for c in result.accepted] == ["POOR-VALID"]

    def test_below_cutoff_routes_to_manual_not_a_proposed_line(self):
        """
        NR-13: do not attempt to price every option permutation.

        The estimator owns the long tail by design, not by failure.
        """
        weak = CatalogItemFactory(
            description="nothing like the opening",
            fire_rating_minutes=90,
            handing="LH",
            is_stock=False,
            csi_division="08",
        )
        result = match_criteria(criteria(), CatalogSnapshot([weak]), cutoff=0.95)
        assert result.status == MatchStatus.MANUAL.value
        assert "below the" in result.manual_reason


# ---------------------------------------------------------------------------
# Fire rating (§5.8, §6.1)
# ---------------------------------------------------------------------------

class TestRatingConstraint:
    def test_exact_rating_passes(self):
        item = CatalogItemFactory(fire_rating_minutes=90)
        ok, _, over = check_rating(criteria(fire_rating_minutes=90), item)
        assert ok and not over

    def test_under_rated_item_fails(self):
        """A 60-minute door in a 90-minute opening is the same failure as unrated."""
        item = CatalogItemFactory(fire_rating_minutes=60)
        ok, reason, _ = check_rating(criteria(fire_rating_minutes=90), item)
        assert ok is False
        assert "under-rated" in reason.lower()

    def test_over_rated_item_passes_with_a_flag(self):
        """Over-specification is a cost issue, not a safety one (§6.1)."""
        item = CatalogItemFactory(fire_rating_minutes=90)
        ok, _, over = check_rating(criteria(fire_rating_minutes=60), item)
        assert ok and over

    def test_unrated_opening_may_take_a_rated_item_with_a_flag(self):
        item = CatalogItemFactory(fire_rating_minutes=90)
        ok, _, over = check_rating(
            criteria(fire_rating_minutes=None, fire_rating_absent=True), item
        )
        assert ok and over

    def test_unrated_opening_and_unrated_item_is_a_clean_match(self):
        item = CatalogItemFactory(fire_rating_minutes=None)
        ok, _, over = check_rating(
            criteria(fire_rating_minutes=None, fire_rating_absent=True), item
        )
        assert ok and not over

    def test_an_unresolved_rating_routes_to_manual_rather_than_matching(self):
        """
        §5.8: never auto-accept a zero-tolerance field that was not resolved.

        "We did not extract a rating" is not the same as "there is no rating", and
        matching around the difference would look like a normal result.
        """
        item = CatalogItemFactory(fire_rating_minutes=90, handing="LH", csi_division="08")
        result = match_criteria(
            criteria(fire_rating_minutes=None, fire_rating_absent=False),
            CatalogSnapshot([item]),
        )
        assert result.status == MatchStatus.MANUAL.value
        assert result.accepted == []
        assert "code-compliance failure" in result.manual_reason


# ---------------------------------------------------------------------------
# Handing (§5.8, §6.1)
# ---------------------------------------------------------------------------

class TestHandingConstraint:
    def test_matching_hand_passes(self):
        ok, _, _ = check_handing(criteria(handing="LH"), CatalogItemFactory(handing="LH"))
        assert ok

    def test_opposite_hand_fails(self):
        ok, reason, _ = check_handing(criteria(handing="LH"), CatalogItemFactory(handing="RH"))
        assert ok is False and "separate SKUs" in reason

    def test_lh_and_lhr_are_not_interchangeable(self):
        """
        The reverse bevel is a different SKU.

        Treating the hand alone as sufficient would put the wrong device on the
        quote. Being strict costs a false negative, which routes to manual — the
        designed-safe outcome.
        """
        ok, _, _ = check_handing(criteria(handing="LH"), CatalogItemFactory(handing="LHR"))
        assert ok is False

    def test_an_unhanded_item_fits_either_way(self):
        ok, _, flagged = check_handing(criteria(handing="LH"), CatalogItemFactory(handing=None))
        assert ok and not flagged

    def test_a_handed_item_for_an_unhanded_opening_is_flagged(self):
        ok, _, flagged = check_handing(
            criteria(handing=None, handing_absent=True), CatalogItemFactory(handing="LH")
        )
        assert ok and flagged

    def test_unresolved_handing_routes_to_manual(self):
        item = CatalogItemFactory(fire_rating_minutes=90, handing=None, csi_division="08")
        result = match_criteria(
            criteria(handing=None, handing_absent=False), CatalogSnapshot([item])
        )
        assert result.status == MatchStatus.MANUAL.value
        assert "separate SKUs" in result.manual_reason


# ---------------------------------------------------------------------------
# Division
# ---------------------------------------------------------------------------

class TestDivisionConstraint:
    def test_same_division_passes(self):
        assert check_division(criteria(), CatalogItemFactory(csi_division="08"))[0]

    def test_different_division_fails(self):
        ok, reason = check_division(criteria(), CatalogItemFactory(csi_division="10"))
        assert ok is False and "Division 10" in reason

    def test_unknown_division_is_not_a_known_mismatch(self):
        """Uncategorised is a data-quality problem, not a proven incompatibility."""
        item = CatalogItemFactory(csi_division=None)
        assert check_division(criteria(), item)[0] is True
        candidate = score_candidate(criteria(), item)
        assert any("no CSI division" in note for note in candidate.notes)


# ---------------------------------------------------------------------------
# Finish scoring (§1.3)
# ---------------------------------------------------------------------------

class TestFinishScoring:
    def test_exact_finish_scores_highest(self):
        finish = FinishCodeFactory(us_code="US26D", bhma_code="626", base_metal="brass")
        item = CatalogItemFactory(finish_code=finish)
        score, ok = score_finish(
            criteria(finish_code_id=str(finish.id), finish_base_metal="brass"), item
        )
        assert score == 1.0 and ok

    def test_us19_and_us26d_score_near_zero_against_each_other(self):
        """
        **US19 and US26D must never collapse to the same row.**

        Both are 'satin'. Scoring keys on the finish id and base metal, never on
        the description text, so there is no path by which the word can bring them
        together.
        """
        us26d = FinishCodeFactory(us_code="US26D", bhma_code="626", base_metal="brass")
        us19 = FinishCodeFactory(us_code="US19", bhma_code="622", base_metal="steel")
        item = CatalogItemFactory(finish_code=us19)
        score, ok = score_finish(
            criteria(finish_code_id=str(us26d.id), finish_base_metal="brass"), item
        )
        assert score <= 0.05 and not ok

    def test_same_base_metal_scores_between(self):
        wanted = FinishCodeFactory(us_code="US26D", bhma_code="626", base_metal="brass")
        other = FinishCodeFactory(us_code="US26", bhma_code="625", base_metal="brass")
        item = CatalogItemFactory(finish_code=other)
        score, ok = score_finish(
            criteria(finish_code_id=str(wanted.id), finish_base_metal="brass"), item
        )
        assert 0.05 < score < 1.0 and not ok

    def test_no_finish_on_the_opening_is_neutral_not_a_match(self):
        item = CatalogItemFactory(finish_code=FinishCodeFactory())
        score, ok = score_finish(criteria(finish_code_id=None), item)
        assert score == 0.5 and not ok


# ---------------------------------------------------------------------------
# Vendor and direct equals (§1.4)
# ---------------------------------------------------------------------------

class TestVendorAndDirectEqual:
    def test_a_different_vendor_is_marked_a_direct_equal(self):
        """
        The system RECORDS a substitution; it never decides one.

        Choosing an equal is estimator judgment.
        """
        item = CatalogItemFactory(vendor="Allegion", fire_rating_minutes=90, handing="LH")
        candidate = score_candidate(criteria(vendor="Hager"), item)
        assert candidate.is_direct_equal is True

    def test_the_specified_vendor_is_not_a_direct_equal(self):
        item = CatalogItemFactory(vendor="Hager", fire_rating_minutes=90, handing="LH")
        candidate = score_candidate(criteria(vendor="Hager"), item)
        assert candidate.is_direct_equal is False

    def test_a_different_series_from_the_same_vendor_scores_lower(self):
        """Hager 3400 vs 3500 is Grade 1 vs Grade 2 — materially different (§1.3)."""
        same = CatalogItemFactory(sku="S1", vendor="Hager", series="3400", fire_rating_minutes=90, handing="LH")
        other = CatalogItemFactory(sku="S2", vendor="Hager", series="3500", fire_rating_minutes=90, handing="LH")
        wanted = criteria(vendor="Hager", series="3400")
        assert score_candidate(wanted, same).vendor_score > score_candidate(wanted, other).vendor_score

    def test_an_exact_part_number_short_circuits_scoring(self):
        """The architect named this exact item (§1.3 — the normal case)."""
        item = CatalogItemFactory(
            part_number="ND53PD", vendor="Schlage", fire_rating_minutes=90, handing="LH",
            description="unrelated wording", is_stock=False,
        )
        candidate = score_candidate(criteria(part_number="ND53PD"), item)
        assert candidate.match_confidence >= 0.95
        assert "exact manufacturer part number" in " ".join(candidate.notes)

    def test_a_part_number_does_not_bypass_a_hard_constraint(self):
        """An explicit callout is not a licence to fit an unrated door."""
        item = CatalogItemFactory(
            part_number="ND53PD", fire_rating_minutes=None, handing="LH", csi_division="08"
        )
        candidate = score_candidate(criteria(part_number="ND53PD"), item)
        assert candidate.eligible is False
        assert candidate.match_confidence == 0.0


# ---------------------------------------------------------------------------
# Ranking and persistence
# ---------------------------------------------------------------------------

class TestRankingAndPersistence:
    def test_top_n_candidates_are_returned_ranked(self):
        """Mirrors the validated estimator behaviour: 'here are 3 close matches'."""
        for index in range(6):
            CatalogItemFactory(
                sku=f"HINGE-{index}", description="Full mortise butt hinge",
                fire_rating_minutes=90, handing="LH", csi_division="08",
                is_stock=index % 2 == 0,
            )
        result = match_criteria(criteria(), CatalogSnapshot.load(), top_n=3, cutoff=0.0)
        assert len(result.accepted) == 3
        confidences = [c.match_confidence for c in result.accepted]
        assert confidences == sorted(confidences, reverse=True)

    def test_stock_items_are_preferred(self):
        """NR-13: automate the stock and top-N items."""
        stocked = CatalogItemFactory(
            sku="STOCK", description="Full mortise butt hinge",
            fire_rating_minutes=90, handing="LH", is_stock=True, csi_division="08",
        )
        special = CatalogItemFactory(
            sku="SPECIAL", description="Full mortise butt hinge",
            fire_rating_minutes=90, handing="LH", is_stock=False, csi_division="08",
        )
        result = match_criteria(criteria(), CatalogSnapshot([stocked, special]), cutoff=0.0)
        assert result.accepted[0].sku == "STOCK"

    def test_per_constraint_verdicts_are_persisted_individually(self):
        """
        §6.1: a rejection must explain *which* constraint failed, not score low.
        """
        finish = FinishCodeFactory()
        opening = OpeningFactory(
            fire_rating_minutes=90, fire_rating_absent=False,
            handing="LH", handing_absent=False, finish_code=finish,
        )
        CatalogItemFactory(
            description="Full mortise butt hinge", fire_rating_minutes=90,
            handing="LH", csi_division="08", finish_code=finish,
        )
        result = match_opening(opening, CatalogSnapshot.load(), cutoff=0.0)
        persist_matches(opening, result)

        match = Match.objects.get(opening=opening)
        assert match.rating_ok is True
        assert match.handing_ok is True
        assert match.division_ok is True
        assert match.finish_ok is True
        assert match.rank == 1
        assert 0.0 <= match.match_confidence <= 1.0

    def test_manual_routing_records_why_on_the_opening(self):
        """The estimator must be able to see why nothing was proposed."""
        opening = OpeningFactory(fire_rating_minutes=90, fire_rating_absent=False, handing="LH")
        CatalogItemFactory(
            description="grab bar", fire_rating_minutes=None, handing=None, csi_division="10"
        )
        result = match_opening(opening, CatalogSnapshot.load())
        persist_matches(opening, result)

        opening.refresh_from_db()
        assert result.status == MatchStatus.MANUAL.value
        assert opening.review_notes

    def test_rematching_replaces_rather_than_accumulates(self):
        finish = FinishCodeFactory()
        opening = OpeningFactory(fire_rating_minutes=90, fire_rating_absent=False, handing="LH", finish_code=finish)
        CatalogItemFactory(
            description="Full mortise butt hinge", fire_rating_minutes=90,
            handing="LH", csi_division="08", finish_code=finish,
        )
        snapshot = CatalogSnapshot.load()
        for _ in range(3):
            persist_matches(opening, match_opening(opening, snapshot, cutoff=0.0))
        assert Match.objects.filter(opening=opening).count() == 1

    def test_match_project_counts_proposed_and_manual(self):
        project = ProjectFactory()
        finish = FinishCodeFactory()
        CatalogItemFactory(
            description="Full mortise butt hinge", fire_rating_minutes=90,
            handing="LH", csi_division="08", finish_code=finish, is_stock=True,
        )
        OpeningFactory(
            project=project, fire_rating_minutes=90, fire_rating_absent=False,
            handing="LH", finish_code=finish, hardware_group="Full mortise butt hinge",
        )
        OpeningFactory(
            project=project, fire_rating_minutes=None, fire_rating_absent=False, handing="LH"
        )
        counts = match_project(project)
        assert counts["openings"] == 2
        assert counts["manual"] >= 1


class TestNoLlmInTheDecision:
    def test_the_module_imports_nothing_that_can_call_a_model(self):
        """
        §6.1: no LLM in the accept/reject decision.

        A match the estimator cannot interrogate is a match they will not trust.

        Checked against the parsed import graph rather than the source text: a
        substring scan matches prose, and the module docstring legitimately
        explains why the previous embedding-based matcher was removed.
        """
        import ast
        import inspect

        from pipeline.stages import match

        tree = ast.parse(inspect.getsource(match))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
                imported.update(f"{node.module}.{alias.name}" for alias in node.names)

        forbidden = ("bedrock", "openai", "boto3", "pgvector", "anthropic")
        offenders = [
            name for name in imported if any(token in name.lower() for token in forbidden)
        ]
        assert not offenders, f"the matcher must not import {offenders}"

    def test_no_model_call_appears_in_executable_code(self):
        """Belt and braces: scan the code with docstrings and comments stripped."""
        import ast
        import inspect

        from pipeline.stages import match

        tree = ast.parse(inspect.getsource(match))
        # Drop every docstring so prose about the old design cannot trip this.
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                body = node.body
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    node.body = body[1:] or [ast.Pass()]

        code = ast.unparse(tree).lower()
        for token in ("invoke_tool", "cosinedistance", "chat.completions", "converse("):
            assert token not in code, f"{token} must not appear in the matcher"

    def test_scoring_is_pure_and_repeatable(self):
        item = CatalogItemFactory(fire_rating_minutes=90, handing="LH", csi_division="08")
        first = score_candidate(criteria(), item)
        second = score_candidate(criteria(), item)
        assert first.match_confidence == second.match_confidence
