from pathlib import Path

from manuscript_audit.parsers import build_bibliography_confidence_summary, build_source_records, parse_bibtex
from manuscript_audit.schemas.artifacts import SourceRecordVerification


def _base_records():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    for r in records:
        r.status = "resolved_canonical_link"
    return records


def test_verified_two_mismatches_is_critical() -> None:
    records = _base_records()
    # attach two metadata_mismatch verifications
    verifications = []
    verifications.append(
        SourceRecordVerification(entry_label=records[0].entry_label, strategy="metadata_query", status="metadata_mismatch", provenance="test")
    )
    if len(records) > 1:
        verifications.append(
            SourceRecordVerification(entry_label=records[1].entry_label, strategy="metadata_query", status="metadata_mismatch", provenance="test")
        )
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.mismatch_entry_count >= 1
    assert summary.confidence_level == "critical"


def test_provider_error_triggers_critical() -> None:
    records = _base_records()
    verifications = [
        SourceRecordVerification(entry_label=records[0].entry_label, strategy="doi", status="provider_error", provenance="test")
    ]
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.provider_error_count >= 1
    assert summary.confidence_level == "critical"
