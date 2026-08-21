"""
The fixed size rule (§5.7, §1.3).

A deterministic parser is used here precisely so it can be pinned by tests. The
rule is right 100% of the time or it is wrong 100% of the time, and a wrong
opening size propagates into a wrong frame, a wrong door, and a wrong price.

The split-column case below came out of the first real bid set: schedules
commonly carry SIZE as separate W and H columns, extraction prompt v1 returned
the width alone, and every opening arrived as a flagged null.
"""

from __future__ import annotations

import pytest

from pipeline.parsers.size import parse_size

W = "'"  # keeps the feet mark out of the surrounding quoting


@pytest.mark.parametrize(
    ("raw", "width", "height"),
    [
        # The canonical shorthand: first two digits width, last two height.
        ("3070", 36, 84),
        ("3670", 42, 84),
        # Explicit feet-inches, with and without the separator.
        (f'3{W}-0" x 7{W}-0"', 36, 84),
        (f'3{W}-0" X 7{W}-0"', 36, 84),
        # Split W and H columns, joined by a single space by prompt v2 rule 3.
        # The space is the separator because it is what grounding sees when it
        # concatenates the two cited cells; an inserted "x" scores 76.9 against a
        # floor of 90 and the whole field is rejected.
        (f'3{W} - 6" 7{W} - 0"', 42, 84),
        (f'3{W} - 0" 7{W} - 0"', 36, 84),
        # The walk-in cooler door: square, and a real row in the golden set.
        (f'3{W} - 0" 3{W} - 0"', 36, 36),
    ],
)
def test_sizes_that_parse(raw, width, height):
    parsed = parse_size(raw)
    assert (parsed.width_inches, parsed.height_inches) == (width, height)
    assert not parsed.needs_review
    assert parsed.raw == raw, "the raw string is always preserved alongside the typed value"


@pytest.mark.parametrize(
    "raw",
    [
        # Half a value. This is the v1 failure: a width with no height must NOT
        # parse, because a size that silently means "3 feet by nothing" is worse
        # than an honest flag.
        f'3{W} - 6"',
        "30",
        # Not a size at all.
        "SEE PLAN",
        "",
        None,
        # 14 inches is a transcription error, not a door.
        f'3{W}-14" x 7{W}-0"',
        # A zero-foot dimension is not a door either.
        "0070",
        # Deliberately unsupported: 5- and 6-digit codes might encode thickness or
        # a pair width. A plausible wrong size is worse than a flag (§5.7).
        "30700",
    ],
)
def test_sizes_that_are_refused(raw):
    parsed = parse_size(raw)
    assert parsed.needs_review
    assert parsed.width_inches is None and parsed.height_inches is None
    assert parsed.reason, "a refusal must say why, because an estimator has to act on it"


def test_the_raw_string_survives_a_refusal():
    """§5.7 stores both. The estimator needs to see what the schedule actually said."""
    parsed = parse_size("SEE PLAN")
    assert parsed.raw == "SEE PLAN"
