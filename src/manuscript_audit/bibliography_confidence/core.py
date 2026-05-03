from collections.abc import Iterable
from typing import Any

from manuscript_audit.schemas.artifacts import BibliographyConfidenceSummary


def compute_confidence_summary(
    parsed_references: Iterable[Any] | None,
    source_verification_summary=None,
) -> BibliographyConfidenceSummary:
    """Return a deterministic, minimal bibliography confidence summary.

    This scaffold produces conservative defaults and returns the canonical
    BibliographyConfidenceSummary Pydantic model defined in
    manuscript_audit.schemas.artifacts so other modules can consume it.
    """
    total = len(list(parsed_references)) if parsed_references is not None else 0
    return BibliographyConfidenceSummary(
        total_entries=total,
        verified_entry_count=0,
        verified_direct_url_count=0,
        deterministic_canonical_link_count=0,
        manual_review_required_count=0,
        mismatch_entry_count=0,
        ambiguous_entry_count=0,
        lookup_not_found_count=0,
        provider_error_count=0,
        insufficient_metadata_count=0,
        confidence_score=100,
        confidence_level="low",
        basis="deterministic_planning",
        rationale=[],
    )
