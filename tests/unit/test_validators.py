from pathlib import Path

from manuscript_audit.parsers import parse_bibtex, parse_manuscript, parse_markdown_manuscript
from manuscript_audit.routing.rules import classify_manuscript
from manuscript_audit.validators import run_deterministic_validators
from manuscript_audit.validators.core import (
    validate_bibliography_source_record_readiness,
    validate_citation_bibliography_alignment,
    validate_claim_section_alignment,
    validate_duplicate_bibliography_entries,
    validate_equation_notation_coverage,
    validate_equation_reference_coverage,
    validate_orphaned_equation_definitions,
    validate_orphaned_figure_table_definitions,
)


def test_placeholder_fixture_generates_major_and_moderate_findings() -> None:
    parsed = parse_markdown_manuscript(Path("tests/fixtures/manuscripts/placeholder_manuscript.md"))
    classification = classify_manuscript(parsed)
    results = run_deterministic_validators(parsed, classification)
    messages = [finding.message for finding in results.all_findings]
    assert any("Unresolved placeholder" in message for message in messages)
    assert any(finding.severity == "major" for finding in results.all_findings)
    assert any(finding.code == "low-citation-density" for finding in results.all_findings)
    assert any(finding.code == "missing-figure-definition" for finding in results.all_findings)


def test_duplicate_bibtex_keys_generate_findings() -> None:
    parsed = parse_markdown_manuscript(
        Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md")
    )
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/duplicate_refs.bib")
    )
    result = validate_duplicate_bibliography_entries(parsed)
    assert len(result.findings) == 1
    assert result.findings[0].code == "duplicate-bibliography-key"


def test_citation_bibliography_alignment_detects_missing_and_unused_entries() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/citation_alignment.tex"))
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/citation_alignment.bib")
    )
    parsed.reference_section_present = True
    result = validate_citation_bibliography_alignment(parsed)
    codes = {finding.code for finding in result.findings}
    assert "missing-bibliography-entry-for-citation" in codes
    assert "uncited-bibliography-entry" in codes


def test_orphaned_figure_table_definitions_are_detected() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/orphaned_figures.md"))
    result = validate_orphaned_figure_table_definitions(parsed)
    codes = {finding.code for finding in result.findings}
    assert "orphaned-figure-definition" in codes
    assert "orphaned-table-definition" in codes


def test_equation_reference_validators_detect_missing_and_orphaned_equations() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/equation_alignment.tex"))
    missing_result = validate_equation_reference_coverage(parsed)
    orphaned_result = validate_orphaned_equation_definitions(parsed)
    assert {finding.code for finding in missing_result.findings} == {"missing-equation-definition"}
    assert {finding.code for finding in orphaned_result.findings} == {
        "orphaned-equation-definition"
    }


def test_equation_notation_validator_detects_undefined_symbol() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/notation_coverage.tex"))
    result = validate_equation_notation_coverage(parsed)
    assert {finding.code for finding in result.findings} == {"undefined-equation-symbol"}
    assert any("b" in finding.message for finding in result.findings)


def test_source_record_readiness_detects_lookup_and_metadata_gaps() -> None:
    parsed = parse_markdown_manuscript(
        Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md")
    )
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/bibliography_metadata.bib")
    )
    result = validate_bibliography_source_record_readiness(parsed)
    codes = {finding.code for finding in result.findings}
    assert "bibliography-source-record-needs-lookup" in codes
    assert "bibliography-source-record-insufficient-metadata" in codes


def test_claim_section_alignment_detects_unsupported_equivalence_claim() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/claim_alignment.md"))
    classification = classify_manuscript(parsed)
    result = validate_claim_section_alignment(parsed, classification)
    assert {finding.code for finding in result.findings} == {"claim-section-misalignment"}
