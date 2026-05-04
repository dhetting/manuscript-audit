from pathlib import Path

from manuscript_audit.parsers import (
    CrossrefSourceRegistryClient,
    FixtureSourceRegistryClient,
    SourceRegistryLookupError,
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    summarize_source_record_verifications,
    verify_source_records,
)
from manuscript_audit.schemas import RegistryMetadataRecord


class AmbiguousQueryClient:
    def lookup_doi(self, doi: str) -> RegistryMetadataRecord | None:
        return None

    def lookup_bibliographic_candidates(self, query: str) -> list[RegistryMetadataRecord]:
        return [
            RegistryMetadataRecord(
                title="Reference with a bad year",
                authors=["Amy Adams"],
                year="2021",
                venue="Metadata Quarterly",
                doi="10.5555/metadata-quarterly.2021.1",
                url="https://doi.org/10.5555/metadata-quarterly.2021.1",
                provider="fixture_crossref",
                source_url="https://doi.org/10.5555/metadata-quarterly.2021.1",
            ),
            RegistryMetadataRecord(
                title="Reference with a bad year",
                authors=["Amy Adams"],
                year="2021",
                venue="Metadata Quarterly",
                doi="10.5555/metadata-quarterly.2021.2",
                url="https://doi.org/10.5555/metadata-quarterly.2021.2",
                provider="fixture_crossref",
                source_url="https://doi.org/10.5555/metadata-quarterly.2021.2",
            ),
        ]


class ErrorClient:
    def lookup_doi(self, doi: str) -> RegistryMetadataRecord | None:
        raise SourceRegistryLookupError("provider unavailable")

    def lookup_bibliographic_candidates(self, query: str) -> list[RegistryMetadataRecord]:
        raise SourceRegistryLookupError("provider unavailable")


class FakeCrossrefClient(CrossrefSourceRegistryClient):
    def _request_json(self, url: str) -> dict:
        return {
            "message": {
                "items": [
                    {
                        "title": ["Candidate One"],
                        "container-title": ["Journal A"],
                        "author": [{"given": "Alex", "family": "Doe"}],
                        "issued": {"date-parts": [[2022]]},
                        "DOI": "10.1111/example.1",
                        "URL": "https://doi.org/10.1111/example.1",
                    },
                    {
                        "title": ["Candidate Two"],
                        "container-title": ["Journal B"],
                        "author": [{"given": "Blair", "family": "Roe"}],
                        "issued": {"date-parts": [[2021]]},
                        "DOI": "10.1111/example.2",
                        "URL": "https://doi.org/10.1111/example.2",
                    },
                ]
            }
        }


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
    assert summary.issue_type_counts["title_mismatch"] == 1


def test_metadata_query_ambiguous_match_is_reported() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    entry = next(item for item in entries if item.key == "bad-year-ref")
    record = next(
        item for item in build_source_records(entries) if item.entry_key == "bad-year-ref"
    )
    verifications = verify_source_records([entry], [record], AmbiguousQueryClient())
    summary = summarize_source_record_verifications(verifications)

    assert verifications[0].status == "ambiguous_match"
    assert verifications[0].candidate_count == 2
    assert "multiple_candidate_matches" in verifications[0].issues
    assert summary.ambiguous_match_count == 1
    assert summary.issue_type_counts["multiple_candidate_matches"] == 1


def test_provider_error_is_reported() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    entry = next(item for item in entries if item.key == "good-ref")
    record = next(item for item in build_source_records(entries) if item.entry_key == "good-ref")
    verifications = verify_source_records([entry], [record], ErrorClient())
    summary = summarize_source_record_verifications(verifications)

    assert verifications[0].status == "provider_error"
    assert verifications[0].issues == ["provider_error"]
    assert summary.provider_error_count == 1


def test_crossref_client_returns_multiple_candidates_from_payload() -> None:
    client = FakeCrossrefClient(mailto=None)
    records = client.lookup_bibliographic_candidates("Candidate query")

    assert len(records) == 2
    assert records[0].title == "Candidate One"
    assert records[1].doi == "10.1111/example.2"


def test_bibliography_confidence_summary_from_verified_results_is_low() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/bibliography_metadata.bib"))
    records = build_source_records(entries)
    client = FixtureSourceRegistryClient.from_json(
        Path("tests/fixtures/registries/source_registry_fixture.json")
    )
    verifications = verify_source_records(entries, records, client)

    summary = build_bibliography_confidence_summary(records, verifications)

    assert summary.basis == "verified_source_records"
    assert summary.confidence_level == "low"
    assert summary.manual_review_required_count == 2
    assert summary.mismatch_entry_count == 1
    assert summary.insufficient_metadata_count == 1


def test_bibliography_confidence_summary_from_deterministic_planning_can_be_high() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/latex_equivalence.bib"))
    records = build_source_records(entries)

    summary = build_bibliography_confidence_summary(records)

    assert summary.basis == "deterministic_planning"
    assert summary.confidence_level == "high"
    assert summary.manual_review_required_count == 0
