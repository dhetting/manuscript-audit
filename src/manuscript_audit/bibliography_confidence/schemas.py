from dataclasses import dataclass, field
from typing import List


@dataclass
class BibliographyConfidenceSummary:
    total_entries: int = 0
    verified_entry_count: int = 0
    verified_direct_url_count: int = 0
    deterministic_canonical_link_count: int = 0
    manual_review_required_count: int = 0
    mismatch_entry_count: int = 0
    ambiguous_entry_count: int = 0
    lookup_not_found_count: int = 0
    provider_error_count: int = 0
    insufficient_metadata_count: int = 0
    confidence_score: int = 100
    confidence_level: str = "low"
    basis: str = "deterministic_planning"
    rationale: List[str] = field(default_factory=list)


__all__ = ["BibliographyConfidenceSummary"]
