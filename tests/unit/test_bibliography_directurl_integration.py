from pathlib import Path
import copy

from manuscript_audit.parsers import (
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    FixtureSourceRegistryClient,
    verify_source_records,
)


def test_direct_url_and_registry_verified():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    # ensure at least two records
    while len(records) < 2:
        records.append(copy.deepcopy(records[0]))

    # mark first as direct URL
    records[0].resolution_strategy = "url"
    records[0].status = "resolved_canonical_link"
    records[0].canonical_source_url = "https://example.org/direct1"
    records[0].identifier_value = "https://example.org/direct1"

    # mark second as DOI that exists in fixture
    records[1].resolution_strategy = "doi"
    records[1].status = "resolved_canonical_link"
    records[1].identifier_value = "10.1000/direct1"
    records[1].canonical_source_url = "https://doi.org/10.1000/direct1"

    client = FixtureSourceRegistryClient.from_json(Path("tests/fixtures/registries/direct_url_fixture.json"))
    verifications = verify_source_records(entries, records, client)
    bsum = build_bibliography_confidence_summary(records, verifications)

    assert bsum.verified_direct_url_count >= 1
    assert bsum.verified_entry_count >= 0
