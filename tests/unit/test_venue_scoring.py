from manuscript_audit.parsers.source_verification import _similarity_score
from manuscript_audit.schemas.artifacts import BibliographyEntry, RegistryMetadataRecord


def _entry(title: str, venue: str) -> BibliographyEntry:
    return BibliographyEntry(
        key="k",
        entry_type="article",
        raw_text="",
        title=title,
        authors=[],
        year="2020",
        journal=venue,
        booktitle=None,
        doi=None,
        url=None,
        source="bibtex",
    )


def _candidate(title: str, venue: str) -> RegistryMetadataRecord:
    return RegistryMetadataRecord(
        title=title,
        authors=[],
        year="2020",
        venue=venue,
        doi=None,
        url=None,
        provider="fixture",
        source_url=None,
    )


def test_venue_exact_vs_partial_score_difference() -> None:
    entry = _entry("Sample Title", "Journal of Testing")
    exact = _candidate("Sample Title", "Journal of Testing")
    partial = _candidate("Sample Title", "Journal of Testing: Special Issue")

    s_exact = _similarity_score(entry, exact)
    s_partial = _similarity_score(entry, partial)

    # exact venue should score exactly one point higher than partial (2.0 vs 1.0)
    assert (s_exact - s_partial) == 1.0


def test_venue_no_match_vs_exact() -> None:
    entry = _entry("Sample Title", "Journal of Testing")
    exact = _candidate("Sample Title", "Journal of Testing")
    other = _candidate("Sample Title", "Different Journal")

    s_exact = _similarity_score(entry, exact)
    s_other = _similarity_score(entry, other)

    # exact venue should add the full venue score over a non-matching venue
    assert (s_exact - s_other) >= 2.0
