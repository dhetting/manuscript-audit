from pathlib import Path
import copy

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


def test_verified_four_ambiguous_is_critical() -> None:
    records = _base_records()
    # ensure at least 4 records to attach ambiguous verifications
    while len(records) < 4:
        records.append(copy.deepcopy(records[0]))
    verifications = []
    for i in range(4):
        verifications.append(
            SourceRecordVerification(
                entry_label=records[i].entry_label,
                strategy="metadata_query",
                status="ambiguous_match",
                provenance="test",
            )
        )
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.ambiguous_entry_count >= 4
    assert summary.confidence_level == "critical"


def test_verified_six_skipped_is_critical() -> None:
    records = _base_records()
    # ensure at least 6 records
    while len(records) < 6:
        records.append(copy.deepcopy(records[0]))
    verifications = []
    for i in range(6):
        verifications.append(
            SourceRecordVerification(
                entry_label=records[i].entry_label,
                strategy="metadata_query",
                status="skipped",
                provenance="test",
            )
        )
    summary = build_bibliography_confidence_summary(records, verifications)
    assert summary.insufficient_metadata_count >= 6
    assert summary.confidence_level == "critical"
