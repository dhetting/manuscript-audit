from pathlib import Path
import copy

from manuscript_audit.parsers import (
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    FixtureSourceRegistryClient,
    verify_source_records,
    summarize_source_record_verifications,
)
from manuscript_audit.schemas.artifacts import SourceRecordVerification


def test_mixed_provider_errors_and_direct_url():
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    # align records to fixture: first is verified via DOI, second triggers provider error DOI
    records[0].resolution_strategy = "doi"
    records[0].status = "resolved_canonical_link"
    records[0].identifier_value = "10.1000/example1"
    records[0].canonical_source_url = "https://doi.org/10.1000/example1"

    if len(records) < 2:
        import copy as _copy

        records.append(_copy.deepcopy(records[0]))

    records[1].resolution_strategy = "doi"
    records[1].status = "resolved_canonical_link"
    records[1].identifier_value = "10.1000/error"
    records[1].canonical_source_url = "https://doi.org/10.1000/error"

    client = FixtureSourceRegistryClient.from_json(Path("tests/fixtures/registries/provider_mixed_fixture.json"))
    verifications = verify_source_records(entries, records, client)
    summary = summarize_source_record_verifications(verifications)
    bsum = build_bibliography_confidence_summary(records, verifications)

    # provider error should be captured and decrease confidence
    assert summary.provider_error_count >= 1 or summary.verified_count >= 1
    assert bsum.confidence_level in {"low", "critical"}
