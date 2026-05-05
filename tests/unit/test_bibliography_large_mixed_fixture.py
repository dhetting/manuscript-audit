from pathlib import Path
import copy

from manuscript_audit.parsers import (
    parse_bibtex,
    build_source_records,
    FixtureSourceRegistryClient,
    verify_source_records,
    summarize_source_record_verifications,
    build_bibliography_confidence_summary,
)


def test_large_mixed_fixture_impacts_confidence():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    # ensure at least 4 records
    while len(records) < 4:
        records.append(copy.deepcopy(records[0]))

    # map DOIs to fixtures
    records[0].resolution_strategy = "doi"
    records[0].status = "resolved_canonical_link"
    records[0].identifier_value = "10.1000/mixed1"
    records[0].canonical_source_url = "https://doi.org/10.1000/mixed1"

    records[1].resolution_strategy = "doi"
    records[1].status = "resolved_canonical_link"
    records[1].identifier_value = "10.1000/error1"
    records[1].canonical_source_url = "https://doi.org/10.1000/error1"

    records[2].resolution_strategy = "doi"
    records[2].status = "resolved_canonical_link"
    records[2].identifier_value = "10.1000/mixed2"
    records[2].canonical_source_url = "https://doi.org/10.1000/mixed2"

    records[3].resolution_strategy = "metadata_query"
    records[3].status = "ready_for_lookup"
    records[3].lookup_query = "mixed lookup query one"

    client = FixtureSourceRegistryClient.from_json(Path("tests/fixtures/registries/mixed_large_fixture.json"))
    verifications = verify_source_records(entries, records, client)
    summary = summarize_source_record_verifications(verifications)
    bsum = build_bibliography_confidence_summary(records, verifications)

    assert summary.provider_error_count >= 1
    assert bsum.confidence_score < 100
    assert bsum.confidence_level in {"low", "critical"}
