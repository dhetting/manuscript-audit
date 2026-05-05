from manuscript_audit.bibliography_confidence import (
    compute_confidence_summary,
    BibliographyConfidenceSummary,
)


def test_compute_confidence_empty():
    summary = compute_confidence_summary([])
    assert isinstance(summary, dict)
    assert summary["total_entries"] == 0


def test_schema_model_defaults():
    model = BibliographyConfidenceSummary()
    assert model.total_entries == 0
    assert isinstance(model.rationale, list)
