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


def test_ambiguous_fixture_yields_ambiguous_and_reduces_confidence():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    while len(records) < 2:
        records.append(copy.deepcopy(records[0]))

    # force metadata_query lookup for both
    records[0].resolution_strategy = "metadata_query"
    records[0].status = "ready_for_lookup"
    records[0].lookup_query = "ambiguous query example"

    records[1].resolution_strategy = "metadata_query"
    records[1].status = "ready_for_lookup"
    records[1].lookup_query = "ambiguous query example"

    client = FixtureSourceRegistryClient.from_json(Path("tests/fixtures/registries/ambiguous_fixture.json"))
    verifications = verify_source_records(entries, records, client)
    summary = summarize_source_record_verifications(verifications)
    bsum = build_bibliography_confidence_summary(records, verifications)

    assert summary.ambiguous_match_count >= 1
    assert bsum.confidence_level in {"low", "critical", "medium"}
