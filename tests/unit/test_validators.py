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


def test_notation_section_alignment_flags_equation_without_context_section() -> None:
    parsed = parse_markdown_manuscript(Path("tests/fixtures/manuscripts/notation_section_gap.md"))
    classification = classify_manuscript(parsed)
    results = run_deterministic_validators(parsed, classification)
    assert any(
        finding.code == "missing-notation-context-section" for finding in results.all_findings
    )


def test_citationless_quantitative_claims_detected() -> None:
    from manuscript_audit.validators.core import validate_citationless_quantitative_claims

    parsed = parse_markdown_manuscript(Path("tests/fixtures/manuscripts/claim_grounding.md"))
    result = validate_citationless_quantitative_claims(parsed)
    codes = [f.code for f in result.findings]
    assert codes.count("citationless-quantitative-claim") >= 2
    locations = {f.location for f in result.findings}
    assert "abstract" in locations
    assert "Introduction" in locations


def test_citationless_comparative_claims_detected() -> None:
    from manuscript_audit.validators.core import validate_citationless_comparative_claims

    parsed = parse_markdown_manuscript(Path("tests/fixtures/manuscripts/claim_grounding.md"))
    result = validate_citationless_comparative_claims(parsed)
    codes = [f.code for f in result.findings]
    assert len(codes) >= 2
    assert all(c == "citationless-comparative-claim" for c in codes)
    locations = {f.location for f in result.findings}
    assert "abstract" in locations


def test_cited_quantitative_and_comparative_claims_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import (
        validate_citationless_comparative_claims,
        validate_citationless_quantitative_claims,
    )

    parsed = ParsedManuscript(
        manuscript_id="cited-only",
        source_path="synthetic",
        source_format="markdown",
        title="Cited Claims",
        abstract="",
        full_text="",
        sections=[
            Section(
                title="Results",
                level=2,
                body=(
                    "Our method is 30% faster than the baseline [@smith2020], "
                    "which is state-of-the-art [@jones2021]."
                ),
            )
        ],
    )
    q_result = validate_citationless_quantitative_claims(parsed)
    c_result = validate_citationless_comparative_claims(parsed)
    assert q_result.findings == []
    assert c_result.findings == []


def test_abstract_metric_unsupported_detected() -> None:
    from manuscript_audit.validators.core import validate_abstract_metric_coverage

    parsed = parse_markdown_manuscript(
        Path("tests/fixtures/manuscripts/cross_artifact_consistency.md")
    )
    result = validate_abstract_metric_coverage(parsed)
    codes = [f.code for f in result.findings]
    assert codes.count("abstract-metric-unsupported") == 2
    flagged_values = {f.evidence[0] for f in result.findings}
    assert "95%" in flagged_values
    assert "3x" in flagged_values


def test_abstract_metric_present_in_results_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abstract_metric_coverage

    parsed = ParsedManuscript(
        manuscript_id="consistent",
        source_path="synthetic",
        source_format="markdown",
        title="Consistent Manuscript",
        abstract="Our method achieves 95% accuracy on the benchmark.",
        full_text="",
        sections=[
            Section(title="Results", level=2, body="The model achieved 95% accuracy."),
        ],
    )
    result = validate_abstract_metric_coverage(parsed)
    assert result.findings == []


def test_abstract_metric_coverage_skips_without_support_sections() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abstract_metric_coverage

    parsed = ParsedManuscript(
        manuscript_id="no-support",
        source_path="synthetic",
        source_format="markdown",
        title="No Results Section",
        abstract="Our method achieves 95% accuracy.",
        full_text="",
        sections=[
            Section(title="Introduction", level=2, body="We propose a new method."),
            Section(title="Methods", level=2, body="We use gradient descent."),
        ],
    )
    result = validate_abstract_metric_coverage(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 15: unlabeled-equation validator
# ---------------------------------------------------------------------------


def test_unlabeled_equation_in_theory_paper_detected() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_unlabeled_equations

    parsed = ParsedManuscript(
        manuscript_id="unlabeled-theory",
        source_path="synthetic",
        source_format="latex",
        title="A Theorem",
        full_text="",
        equation_blocks=["a + b = c", r"x = y \label{eq:y}"],
    )
    classification = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="theory_paper",
        evidence_types=[],
        claim_types=[],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_unlabeled_equations(parsed, classification)
    codes = [f.code for f in result.findings]
    assert "equation-missing-label" in codes
    # Only the first block (without \label) should be flagged
    assert len(result.findings) == 1


def test_labeled_equation_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_unlabeled_equations

    parsed = ParsedManuscript(
        manuscript_id="labeled-theory",
        source_path="synthetic",
        source_format="latex",
        title="A Theorem",
        full_text="",
        equation_blocks=[r"a + b = c \label{eq:sum}", r"x = y \label{eq:y}"],
    )
    classification = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="theory_paper",
        evidence_types=[],
        claim_types=[],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_unlabeled_equations(parsed, classification)
    assert result.findings == []


def test_unlabeled_equation_skipped_for_non_theory() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_unlabeled_equations

    parsed = ParsedManuscript(
        manuscript_id="empirical-latex",
        source_path="synthetic",
        source_format="latex",
        title="An Experiment",
        full_text="",
        equation_blocks=["a + b = c"],  # no \label, but not a theory paper
    )
    classification = ManuscriptClassification(
        pathway="applied_stats",
        paper_type="applied_stats_paper",
        evidence_types=[],
        claim_types=[],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_unlabeled_equations(parsed, classification)
    assert result.findings == []

