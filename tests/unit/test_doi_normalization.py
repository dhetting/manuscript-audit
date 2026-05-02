from pathlib import Path

from manuscript_audit.parsers import (
    build_source_records,
    parse_bibtex,
)


def test_doi_normalization_variants() -> None:
    path = Path("tests/fixtures/manuscripts/bib_edge_cases.bib")
    entries = parse_bibtex(path)
    records = build_source_records(entries)
    by_key = {r.entry_key: r for r in records}

    assert by_key["doi-var1"].status == "resolved_canonical_link"
    assert by_key["doi-var1"].canonical_source_url == "https://doi.org/10.5678/edge-doi"
    assert by_key["doi-var2"].canonical_source_url == "https://doi.org/10.9012/edge-doi2"
    assert by_key["doi-var3"].canonical_source_url == "https://doi.org/10.3333/edge-doi3"
