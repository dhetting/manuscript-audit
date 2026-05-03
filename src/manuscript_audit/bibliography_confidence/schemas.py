from pydantic import BaseModel


class BibliographyConfidenceSummary(BaseModel):
    ambiguous_entry_count: int = 0
    basis: str = "deterministic_planning"
    confidence_level: str = "low"
    confidence_score: int = 0
    deterministic_canonical_link_count: int = 0
    insufficient_metadata_count: int = 0
    lookup_not_found_count: int = 0
    manual_review_required_count: int = 0
    mismatch_entry_count: int = 0
    provider_error_count: int = 0
    rationale: list[str] = []
    total_entries: int = 0
    verified_direct_url_count: int = 0
    verified_entry_count: int = 0
