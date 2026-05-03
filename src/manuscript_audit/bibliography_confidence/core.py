from collections.abc import Iterable
from typing import Any


def compute_confidence_summary(
    parsed_references: Iterable[Any], 
    source_verification_summary=None,
) -> dict:
    """Return a deterministic, minimal bibliography confidence summary.

    This scaffold produces conservative defaults and is deterministic so higher-level
    workflows can depend on its shape while the full implementation is developed.
    """
    total = len(list(parsed_references)) if parsed_references is not None else 0
    return {
        "ambiguous_entry_count": 0,
        "basis": "deterministic_planning",
        "confidence_level": "low",
        "confidence_score": 100,
        "deterministic_canonical_link_count": 0,
        "insufficient_metadata_count": 0,
        "lookup_not_found_count": 0,
        "manual_review_required_count": 0,
        "mismatch_entry_count": 0,
        "provider_error_count": 0,
        "rationale": [],
        "total_entries": total,
        "verified_direct_url_count": 0,
        "verified_entry_count": 0,
    }
