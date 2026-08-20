"""
Tool schemas — the extraction contract (§5.4, §5.5).

    Enforce structure with tool use / JSON schema mode so free text is
    impossible. **The schema is the contract; prose is not accepted.**

Every field carries a mandatory ``source_element_ids`` array. That is not a
convenience for the UI: it is the mechanism by which "show me the source" becomes
a database join rather than a second inference (§5.1). A schema without it would
make the traceability contract unenforceable no matter how good the prompt.
"""

from __future__ import annotations

#: The seven extraction targets (FR-2, §1.3). Order matters only for readability;
#: completeness_penalty counts against this list.
OPENING_FIELDS = (
    "door_number",
    "size",
    "handing",
    "finish",
    "fire_rating",
    "hardware_group",
    "alternate_designation",
)

#: Fields whose absence is a finding rather than a gap, and which get the stricter
#: per-field threshold (§5.8).
ZERO_TOLERANCE_FIELDS = ("fire_rating", "handing")


def _cited_field(description: str) -> dict:
    """
    One field: a raw value, its citations, and the model's own confidence.

    ``value`` is nullable and ``source_element_ids`` defaults to empty, because
    "this opening genuinely has no fire rating" is a legitimate, labelled result
    (§5.10) — and the validation gate treats a null *with* a citation as
    incoherent rather than as a near miss.
    """
    return {
        "type": "object",
        "properties": {
            "value": {
                "type": ["string", "null"],
                "description": (
                    f"{description} EXACTLY as written in the source. Do not "
                    f"normalise, expand, convert, or correct. Null if not present."
                ),
            },
            "source_element_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "element_id(s) of the exact source cells or words this value "
                    "came from. Cite only ids present in the input; never invent "
                    "one. Empty only when value is null."
                ),
            },
            "confidence_llm": {
                "type": ["number", "null"],
                "minimum": 0,
                "maximum": 1,
                "description": "Your own confidence in this field, in [0,1].",
            },
        },
        "required": ["value", "source_element_ids"],
    }


EXTRACTION_TOOL = {
    "name": "record_openings",
    "description": (
        "Record every opening found in this door schedule table, with a citation "
        "for every populated field."
    ),
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "openings": {
                    "type": "array",
                    "description": "One record per opening. Empty if this table has none.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "opening_id": {
                                "type": "string",
                                "description": "The door number as written, e.g. '101' or 'D-12'.",
                            },
                            "needs_review": {
                                "type": "boolean",
                                "description": (
                                    "True if any cell was ambiguous, illegible, or "
                                    "spanned merged rows you could not resolve."
                                ),
                            },
                            "review_reason": {
                                "type": ["string", "null"],
                                "description": "Why this opening needs a human look.",
                            },
                            "fields": {
                                "type": "object",
                                "properties": {
                                    "door_number": _cited_field("The opening identifier."),
                                    "size": _cited_field("The size, e.g. '3070'."),
                                    "handing": _cited_field("Handing, e.g. 'LH'."),
                                    "finish": _cited_field("Finish code, e.g. 'US26D' or '626'."),
                                    "fire_rating": _cited_field(
                                        "Fire rating, e.g. '90 MIN' or 'B LABEL'."
                                    ),
                                    "hardware_group": _cited_field(
                                        "Hardware set such as 'HW-3', or an explicit "
                                        "manufacturer part/series."
                                    ),
                                    "alternate_designation": _cited_field(
                                        "Base bid vs 'Alternate 1', as written."
                                    ),
                                },
                                "required": ["door_number"],
                            },
                        },
                        "required": ["opening_id", "fields"],
                    },
                }
            },
            "required": ["openings"],
        }
    },
}


LOCATE_TOOL = {
    "name": "classify_tables",
    "description": "Classify each table in the inventory so only relevant ones are extracted.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "tables": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table_id": {"type": "string"},
                            "classification": {
                                "type": "string",
                                "enum": [
                                    "DOOR_SCHEDULE",
                                    "FRAME_SCHEDULE",
                                    "HARDWARE_SETS",
                                    "FINISH_LEGEND",
                                    "IRRELEVANT",
                                ],
                            },
                            "uncertain": {"type": "boolean"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["table_id", "classification"],
                    },
                }
            },
            "required": ["tables"],
        }
    },
}


HARDWARE_TOOL = {
    "name": "resolve_hardware_sets",
    "description": "Resolve named hardware-set callouts to their component lists.",
    "inputSchema": {
        "json": {
            "type": "object",
            "properties": {
                "sets": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "hardware_group": {"type": "string"},
                            "resolved": {
                                "type": "boolean",
                                "description": (
                                    "False when the callout appears in the door "
                                    "schedule but its definition is not in this input. "
                                    "Do NOT supply what the set usually contains."
                                ),
                            },
                            "explicit_part": {
                                "type": "boolean",
                                "description": (
                                    "True when the architect named a manufacturer part "
                                    "or series instead of a set. Not a failure (§1.3)."
                                ),
                            },
                            "components": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "quantity": _cited_field("Quantity per opening."),
                                        "description": _cited_field("Item noun, e.g. 'Hinge'."),
                                        "manufacturer": _cited_field("Manufacturer as written."),
                                        "part_number": _cited_field("Part number or series."),
                                        "finish": _cited_field("Finish code as written."),
                                    },
                                    "required": ["description"],
                                },
                            },
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["hardware_group", "resolved", "components"],
                    },
                }
            },
            "required": ["sets"],
        }
    },
}
