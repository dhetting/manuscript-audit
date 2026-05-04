from pathlib import Path

from manuscript_audit.parsers import (
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
)
from manuscript_audit.schemas.artifacts import SourceRecordVerification


def _base_records():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    for r in records:
        r.status = "resolved_canonical_link"
    return records


def test_deterministic_ready_for_lookup_is_low() -> None:
    records = _base_records()
    # single ready_for_lookup should be considered low confidence after calibration
    records[0].status = "ready_for_lookup"
    summary = build_bibliography_confidence_summary(records)

    assert summary.basis == "deterministic_planning"
    assert summary.manual_review_required_count >= 1
    assert summary.confidence_level == "low"


def test_verified_single_mismatch_is_low() -> None:
    records = _base_records()
    verifications = [
        SourceRecordVerification(
            entry_label=records[0].entry_label,
            strategy="metadata_query",
            status="metadata_mismatch",
            provenance="test",
        )
    ]
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.mismatch_entry_count >= 1
    assert summary.confidence_level == "low"


def test_verified_single_lookup_not_found_is_medium() -> None:
    records = _base_records()
    verifications = [
        SourceRecordVerification(
            entry_label=records[0].entry_label,
            strategy="metadata_query",
            status="lookup_not_found",
            provenance="test",
        )
    ]
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.lookup_not_found_count >= 1
    # single lookup_not_found without mismatches should map to medium
    assert summary.confidence_level == "medium"
