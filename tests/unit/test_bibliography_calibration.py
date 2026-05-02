from pathlib import Path

from manuscript_audit.parsers import (
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
)


def test_deterministic_single_insufficient_metadata_is_medium() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    # ensure deterministic baseline
    for r in records:
        r.status = "resolved_canonical_link"
    # single insufficient metadata should be a medium confidence
    records[0].status = "insufficient_metadata"
    summary = build_bibliography_confidence_summary(records)

    assert summary.basis == "deterministic_planning"
    assert summary.insufficient_metadata_count == 1
    assert summary.confidence_level == "medium"


def test_deterministic_two_insufficient_metadata_is_critical() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    for r in records:
        r.status = "resolved_canonical_link"
    # ensure at least two insufficient entries
    if len(records) < 2:
        import copy

        rec_copy = copy.deepcopy(records[0])
        records.append(rec_copy)
        records[-1].status = "insufficient_metadata"
    else:
        records[0].status = "insufficient_metadata"
        records[1].status = "insufficient_metadata"

    summary = build_bibliography_confidence_summary(records)

    assert summary.insufficient_metadata_count >= 2
    assert summary.confidence_level == "critical"
