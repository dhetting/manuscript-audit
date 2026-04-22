from pathlib import Path

from manuscript_audit.parsers import (
    FixtureSourceRegistryClient,
    build_source_records,
    parse_bibtex,
    summarize_source_record_verifications,
    verify_source_records,
)


def test_fixture_source_record_verification_matches_and_flags_mismatch() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    client = FixtureSourceRegistryClient.from_json(
        Path("tests/fixtures/registries/source_registry_fixture.json")
    )
    verifications = verify_source_records(entries, records, client)
    summary = summarize_source_record_verifications(verifications)
    by_key = {item.entry_key: item for item in verifications}

    assert by_key["good-ref"].status == "verified"
    assert by_key["bad-year-ref"].status == "verified"
    assert by_key["bad-doi-ref"].status == "metadata_mismatch"
    assert "title_mismatch" in by_key["bad-doi-ref"].issues
    assert by_key["missing-fields-ref"].status == "skipped"
    assert summary.verified_count == 2
    assert summary.metadata_mismatch_count == 1
    assert summary.skipped_count == 1
