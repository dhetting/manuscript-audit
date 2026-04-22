from pathlib import Path

from manuscript_audit.parsers import (
    parse_bibtex,
    parse_latex_manuscript,
    parse_manuscript,
)


def test_parse_bibtex_extracts_structured_entries() -> None:
    entries = parse_bibtex(Path("tests/fixtures/manuscripts/latex_equivalence.bib"))
    assert len(entries) == 2
    assert entries[0].key == "schuirmann1987"
    assert entries[0].title == "A comparison of the two one-sided tests procedure"
    assert entries[0].journal == "Journal of Pharmacokinetics and Biopharmaceutics"
    assert entries[0].doi == "10.1007/BF01068419"


def test_parse_latex_manuscript_extracts_sections_and_citations() -> None:
    parsed = parse_latex_manuscript(Path("tests/fixtures/manuscripts/latex_equivalence.tex"))
    assert parsed.source_format == "latex"
    assert parsed.title == "A LaTeX Equivalence Workflow Paper"
    assert "schuirmann1987" in parsed.citation_keys
    assert "Introduction" in parsed.section_titles
    assert parsed.reference_section_present is True


def test_parse_latex_manuscript_extracts_equation_labels_and_references() -> None:
    parsed = parse_latex_manuscript(Path("tests/fixtures/manuscripts/equation_alignment.tex"))
    assert "eq:missing" in parsed.equation_mentions
    assert "eq:unused" in parsed.equation_definitions


def test_parse_dispatch_uses_suffix() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/latex_equivalence.tex"))
    assert parsed.source_format == "latex"
