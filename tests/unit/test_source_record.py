from pathlib import Path

from manuscript_audit.parsers import build_source_record_candidates, parse_bibtex


def test_source_record_candidates_cover_doi_lookup_and_insufficient_metadata() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    candidates = build_source_record_candidates(entries)
    by_key = {candidate.entry_key: candidate for candidate in candidates}

    assert by_key["good-ref"].status == "ready_via_doi"
    assert by_key["good-ref"].preferred_identifier_type == "doi"
    assert by_key["bad-year-ref"].status == "needs_metadata_lookup"
    assert by_key["bad-year-ref"].lookup_query is not None
    assert by_key["missing-fields-ref"].status == "insufficient_metadata"
