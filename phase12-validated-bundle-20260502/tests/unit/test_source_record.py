from pathlib import Path

from manuscript_audit.parsers import (
    build_source_record_candidates,
    build_source_records,
    parse_bibtex,
    summarize_source_records,
)


def test_source_record_candidates_cover_doi_lookup_and_insufficient_metadata() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    candidates = build_source_record_candidates(entries)
    by_key = {candidate.entry_key: candidate for candidate in candidates}

    assert by_key["good-ref"].status == "ready_via_doi"
    assert by_key["good-ref"].preferred_identifier_type == "doi"
    assert by_key["bad-year-ref"].status == "needs_metadata_lookup"
    assert by_key["bad-year-ref"].lookup_query is not None
    assert by_key["missing-fields-ref"].status == "insufficient_metadata"


def test_source_record_enrichment_normalizes_identifiers_and_summarizes() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    summary = summarize_source_records(records)
    by_key = {record.entry_key: record for record in records}

    assert by_key["good-ref"].status == "resolved_canonical_link"
    assert by_key["good-ref"].canonical_source_url == "https://doi.org/10.1234/example-doi"
    assert by_key["bad-year-ref"].status == "ready_for_lookup"
    assert by_key["missing-fields-ref"].status == "insufficient_metadata"
    assert summary.total_entries == 4
    assert summary.resolved_canonical_link_count == 1
    assert summary.ready_for_lookup_count == 2
    assert summary.insufficient_metadata_count == 1
