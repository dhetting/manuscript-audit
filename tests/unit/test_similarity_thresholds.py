from manuscript_audit.parsers.source_verification import (
    _select_best_candidate,
    _title_score,
)
from manuscript_audit.schemas.artifacts import (
    BibliographyEntry,
    RegistryMetadataRecord,
)


def _entry(title: str) -> BibliographyEntry:
    return BibliographyEntry(
        key="k",
        entry_type="article",
        raw_text="",
        title=title,
        authors=[],
        year="2020",
        journal=None,
        booktitle=None,
        doi=None,
        url=None,
        source="bibtex",
    )


def _candidate(title: str) -> RegistryMetadataRecord:
    return RegistryMetadataRecord(
        title=title,
        authors=[],
        year="2020",
        venue=None,
        doi=None,
        url=None,
        provider="fixture",
        source_url=None,
    )


def test_title_overlap_thresholds() -> None:
    entry = _entry("alpha beta gamma delta")
    assert _title_score(entry.title, "alpha beta gamma delta") == 5.0
    assert _title_score(entry.title, "alpha beta gamma delta plus") == 4.0
    # substring matches are treated as a strong match (score 4.0)
    assert _title_score(entry.title, "alpha beta gamma") == 4.0
    # reordered tokens but same token overlap should map to the overlap thresholds
    assert _title_score(entry.title, "gamma beta alpha") == 3.0
    # re-ordered two-token candidate (not a substring) should yield the 0.5 overlap -> score 2.0
    assert _title_score(entry.title, "beta alpha") == 2.0
    # one shared token plus an unrelated token should not reach the 0.3 overlap threshold
    assert _title_score(entry.title, "alpha zeta") == 0.0


def test_select_best_candidate_ambiguous() -> None:
    entry = _entry("alpha beta gamma delta")
    c1 = _candidate("alpha beta gamma delta x")
    c2 = _candidate("alpha beta gamma delta y")
    candidate, score, issues = _select_best_candidate(entry, [c1, c2])
    assert candidate is None
    assert issues == ["multiple_candidate_matches"]
