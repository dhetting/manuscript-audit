from collections.abc import Iterable
from typing import Any


def compute_confidence_summary(
    parsed_references: Iterable[Any] | None,
    source_verification_summary=None,
) -> dict:
    """Return a deterministic, minimal bibliography confidence summary as a dict.

    This lightweight scaffold is intended for package-level tests and
    examples; the authoritative implementation used in workflows is
    manuscript_audit.parsers.build_bibliography_confidence_summary.
    """
    total = len(list(parsed_references)) if parsed_references is not None else 0
    return {
        "total_entries": total,
        "verified_entry_count": 0,
        "verified_direct_url_count": 0,
        "deterministic_canonical_link_count": 0,
        "manual_review_required_count": 0,
        "mismatch_entry_count": 0,
        "ambiguous_entry_count": 0,
        "lookup_not_found_count": 0,
        "provider_error_count": 0,
        "insufficient_metadata_count": 0,
        "confidence_score": 100,
        "confidence_level": "low",
        "basis": "deterministic_planning",
        "rationale": [],
    }
