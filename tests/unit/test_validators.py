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



# ---------------------------------------------------------------------------
# Phase 16: claim evidence escalation
# ---------------------------------------------------------------------------


def test_claim_evidence_gap_escalates_at_threshold() -> None:
    from manuscript_audit.schemas.findings import Finding, ValidationResult, ValidationSuiteResult
    from manuscript_audit.validators.core import validate_claim_evidence_escalation

    findings = [
        Finding(
            code="citationless-quantitative-claim",
            severity="moderate",
            message="Paragraph has unsupported metric.",
            validator="citationless_quantitative",
        ),
        Finding(
            code="citationless-comparative-claim",
            severity="moderate",
            message="Paragraph has unsupported comparison.",
            validator="citationless_comparative",
        ),
        Finding(
            code="abstract-metric-unsupported",
            severity="moderate",
            message="90% not found in results.",
            validator="abstract_metric_coverage",
        ),
    ]
    suite = ValidationSuiteResult(
        validator_version="test",
        results=[ValidationResult(validator_name="test_v", findings=findings)],
    )
    result = validate_claim_evidence_escalation(suite)
    codes = {f.code for f in result.findings}
    assert "systemic-claim-evidence-gap" in codes
    assert result.findings[0].severity == "major"


def test_below_threshold_does_not_escalate() -> None:
    from manuscript_audit.schemas.findings import Finding, ValidationResult, ValidationSuiteResult
    from manuscript_audit.validators.core import validate_claim_evidence_escalation

    findings = [
        Finding(
            code="citationless-quantitative-claim",
            severity="moderate",
            message="Paragraph has unsupported metric.",
            validator="citationless_quantitative",
        ),
        Finding(
            code="citationless-comparative-claim",
            severity="moderate",
            message="Paragraph has unsupported comparison.",
            validator="citationless_comparative",
        ),
    ]
    suite = ValidationSuiteResult(
        validator_version="test",
        results=[ValidationResult(validator_name="test_v", findings=findings)],
    )
    result = validate_claim_evidence_escalation(suite)
    assert result.findings == []


def test_claim_grounding_fixture_triggers_escalation() -> None:
    from pathlib import Path

    from manuscript_audit.parsers import parse_manuscript
    from manuscript_audit.routing.rules import classify_manuscript
    from manuscript_audit.validators import run_deterministic_validators

    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/claim_grounding.md"))
    classification = classify_manuscript(parsed)
    suite = run_deterministic_validators(parsed, classification)
    major_codes = {f.code for f in suite.all_findings if f.severity == "major"}
    assert "systemic-claim-evidence-gap" in major_codes


# ---------------------------------------------------------------------------
# Phase 20: notation section ordering validator
# ---------------------------------------------------------------------------


def test_notation_section_out_of_order_detected() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_notation_section_ordering

    parsed = ParsedManuscript(
        manuscript_id="order-gap",
        source_path="synthetic",
        source_format="markdown",
        title="Out-of-order notation",
        full_text="",
        sections=[
            Section(title="Introduction", level=1, body=""),
            Section(title="Proof", level=1, body=""),       # content first
            Section(title="Notation", level=1, body=""),   # notation after
            Section(title="Conclusion", level=1, body=""),
        ],
    )
    classification = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="theory_paper",
        evidence_types=["theorem_or_proof"],
        claim_types=["theoretical"],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_notation_section_ordering(parsed, classification)
    codes = [f.code for f in result.findings]
    assert "notation-section-out-of-order" in codes


def test_notation_section_in_correct_order_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_notation_section_ordering

    parsed = ParsedManuscript(
        manuscript_id="order-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Correct notation order",
        full_text="",
        sections=[
            Section(title="Introduction", level=1, body=""),
            Section(title="Preliminaries", level=1, body=""),  # notation first
            Section(title="Proof", level=1, body=""),          # content after
            Section(title="Conclusion", level=1, body=""),
        ],
    )
    classification = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="theory_paper",
        evidence_types=["theorem_or_proof"],
        claim_types=["theoretical"],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_notation_section_ordering(parsed, classification)
    assert result.findings == []


def test_notation_ordering_skipped_for_non_theory() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_notation_section_ordering

    parsed = ParsedManuscript(
        manuscript_id="empirical",
        source_path="synthetic",
        source_format="markdown",
        title="Empirical study",
        full_text="",
        sections=[
            Section(title="Methods", level=1, body=""),
            Section(title="Results", level=1, body=""),
            Section(title="Notation", level=1, body=""),
        ],
    )
    classification = ManuscriptClassification(
        pathway="applied_stats",
        paper_type="applied_stats_paper",
        evidence_types=[],
        claim_types=[],
        high_risk_features=[],
        recommended_stack="standard",
    )
    result = validate_notation_section_ordering(parsed, classification)
    assert result.findings == []


def test_notation_ordering_via_fixture() -> None:
    from pathlib import Path

    from manuscript_audit.parsers import parse_manuscript
    from manuscript_audit.routing.rules import classify_manuscript
    from manuscript_audit.validators import run_deterministic_validators

    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/notation_ordering_gap.md"))
    classification = classify_manuscript(parsed)
    suite = run_deterministic_validators(parsed, classification)
    codes = {f.code for f in suite.all_findings}
    assert "notation-section-out-of-order" in codes


# ---------------------------------------------------------------------------
# Phase 21: abstract length and section body completeness validators
# ---------------------------------------------------------------------------


def test_overlong_abstract_detected() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_abstract_length

    long_abstract = " ".join(["word"] * 360)
    parsed = ParsedManuscript(
        manuscript_id="overlong",
        source_path="synthetic",
        source_format="markdown",
        title="Overlong abstract test",
        abstract=long_abstract,
        full_text="",
    )
    result = validate_abstract_length(parsed)
    codes = [f.code for f in result.findings]
    assert "overlong-abstract" in codes
    assert result.findings[0].severity == "minor"


def test_normal_abstract_length_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_abstract_length

    parsed = ParsedManuscript(
        manuscript_id="normal",
        source_path="synthetic",
        source_format="markdown",
        title="Normal abstract",
        abstract="This study investigates the effect of X on Y in a controlled setting.",
        full_text="",
    )
    result = validate_abstract_length(parsed)
    assert result.findings == []


def test_underdeveloped_section_detected() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_section_body_completeness

    parsed = ParsedManuscript(
        manuscript_id="thin-sections",
        source_path="synthetic",
        source_format="markdown",
        title="Thin sections test",
        full_text="",
        sections=[
            Section(title="Methods", level=2, body="We used gradient descent."),
            Section(title="Results", level=2, body="See Table 1."),
            Section(title="Discussion", level=2, body=(
                "These results suggest a meaningful improvement over the baseline "
                "approach. The method generalizes well across multiple conditions "
                "and demonstrates robustness to parameter variations in repeated "
                "experiments, confirming the reliability of our approach."
            )),
        ],
    )
    result = validate_section_body_completeness(parsed)
    flagged = {f.location for f in result.findings}
    assert "section 'Methods'" in flagged
    assert "section 'Results'" in flagged
    assert "section 'Discussion'" not in flagged


def test_substantial_section_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_section_body_completeness

    body = "This is a well-developed methods section with sufficient detail. " * 5
    parsed = ParsedManuscript(
        manuscript_id="full-sections",
        source_path="synthetic",
        source_format="markdown",
        title="Full sections test",
        full_text="",
        sections=[
            Section(title="Methods", level=2, body=body),
        ],
    )
    result = validate_section_body_completeness(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 22: fatal escalation tier (critical-structural-claim-failure)
# ---------------------------------------------------------------------------


def _make_suite_with_codes(*codes: str):
    """Build a minimal ValidationSuiteResult containing one finding per code."""
    from manuscript_audit.schemas.findings import Finding, ValidationResult, ValidationSuiteResult

    results = [
        ValidationResult(
            validator_name=f"synthetic_{code}",
            findings=[
                Finding(
                    code=code,
                    severity="major",
                    message=f"Synthetic finding for {code}",
                    validator=f"synthetic_{code}",
                )
            ],
        )
        for code in codes
    ]
    return ValidationSuiteResult(validator_version="test", results=results)


def test_fatal_escalation_fires_when_both_conditions_met() -> None:
    from manuscript_audit.validators.core import validate_critical_escalation

    suite = _make_suite_with_codes(
        "systemic-claim-evidence-gap", "missing-required-section"
    )
    result = validate_critical_escalation(suite)
    codes = [f.code for f in result.findings]
    assert "critical-structural-claim-failure" in codes
    assert result.findings[0].severity == "fatal"


def test_fatal_escalation_no_fire_without_claim_gap() -> None:
    from manuscript_audit.validators.core import validate_critical_escalation

    suite = _make_suite_with_codes("missing-required-section")
    result = validate_critical_escalation(suite)
    assert result.findings == []


def test_fatal_escalation_no_fire_without_missing_section() -> None:
    from manuscript_audit.validators.core import validate_critical_escalation

    suite = _make_suite_with_codes("systemic-claim-evidence-gap")
    result = validate_critical_escalation(suite)
    assert result.findings == []


def test_fatal_escalation_no_fire_with_neither() -> None:
    from manuscript_audit.validators.core import validate_critical_escalation

    suite = _make_suite_with_codes("minor-citation-issue")
    result = validate_critical_escalation(suite)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 24: passive voice density validator
# ---------------------------------------------------------------------------


def _methods_manuscript(body: str):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="pv-test",
        source_path="synthetic",
        source_format="markdown",
        title="Passive voice test",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )


def test_high_passive_voice_density_detected() -> None:
    from manuscript_audit.validators.core import validate_passive_voice_density

    # 5 passive sentences out of 6 total = 83% > 45% threshold
    body = (
        "The data was collected from participants. "
        "Samples were processed using centrifugation. "
        "Results were analyzed by two independent raters. "
        "The protocol was approved by the ethics board. "
        "Measurements were taken at three time points. "
        "We then reviewed all outputs."  # active sentence
    )
    result = validate_passive_voice_density(_methods_manuscript(body))
    codes = [f.code for f in result.findings]
    assert "high-passive-voice-density" in codes
    assert result.findings[0].severity == "minor"


def test_low_passive_voice_density_not_flagged() -> None:
    from manuscript_audit.validators.core import validate_passive_voice_density

    # Mostly active voice: 1 passive out of 5 = 20% < 45%
    body = (
        "We collected data from fifty participants. "
        "Our team processed the samples using centrifugation. "
        "Two raters analyzed the transcripts independently. "
        "The study ran from January to March. "
        "The protocol was approved by the ethics board."  # one passive
    )
    result = validate_passive_voice_density(_methods_manuscript(body))
    assert result.findings == []


def test_passive_voice_skips_non_methods_section() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_passive_voice_density

    body = (
        "The data was collected. Samples were processed. "
        "Results were analyzed. The protocol was approved. "
        "Measurements were taken."
    )
    parsed = ParsedManuscript(
        manuscript_id="non-methods",
        source_path="synthetic",
        source_format="markdown",
        title="Non-methods test",
        full_text="",
        sections=[Section(title="Introduction", level=2, body=body)],
    )
    result = validate_passive_voice_density(parsed)
    assert result.findings == []


def test_passive_voice_skips_short_sections() -> None:
    from manuscript_audit.validators.core import validate_passive_voice_density

    # Only 3 sentences — below minimum sentence count of 4
    body = "Data was collected. Samples were processed. Results were analyzed."
    result = validate_passive_voice_density(_methods_manuscript(body))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 27: sentence-level claim localization
# ---------------------------------------------------------------------------


def test_quantitative_claim_evidence_contains_trigger_sentence() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_citationless_quantitative_claims

    # Paragraph with two sentences; only the second triggers the claim
    body = (
        "We describe our approach in this section.\n"
        "Our model achieves 94% accuracy on the benchmark dataset."
    )
    parsed = ParsedManuscript(
        manuscript_id="sent-loc",
        source_path="synthetic",
        source_format="markdown",
        title="Test",
        full_text="",
        sections=[Section(title="Results", level=2, body=body)],
    )
    result = validate_citationless_quantitative_claims(parsed)
    assert result.findings, "Expected at least one finding"
    evidence = result.findings[0].evidence
    assert evidence, "Expected evidence to be populated"
    assert "94%" in evidence[0], "Trigger sentence should appear in evidence"


def test_comparative_claim_evidence_contains_trigger_sentence() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_citationless_comparative_claims

    body = (
        "This section summarizes our experimental results.\n"
        "Our method outperforms all prior approaches on this task."
    )
    parsed = ParsedManuscript(
        manuscript_id="comp-sent",
        source_path="synthetic",
        source_format="markdown",
        title="Test",
        full_text="",
        sections=[Section(title="Discussion", level=2, body=body)],
    )
    result = validate_citationless_comparative_claims(parsed)
    assert result.findings, "Expected at least one finding"
    evidence = result.findings[0].evidence
    assert evidence
    assert "outperforms" in evidence[0]


# ---------------------------------------------------------------------------
# Phase 28: duplicate quantitative claim detection
# ---------------------------------------------------------------------------


def _dup_claim_manuscript(sections: list[tuple[str, str]]):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="dup-test",
        source_path="synthetic",
        source_format="markdown",
        title="Dup claim test",
        full_text="",
        sections=[Section(title=t, level=2, body=b) for t, b in sections],
    )


def test_duplicate_claim_detected_across_sections() -> None:
    from manuscript_audit.validators.core import validate_duplicate_claims

    parsed = _dup_claim_manuscript([
        ("Results", "Our model achieves 94% accuracy on the test set."),
        ("Discussion", "The 94% accuracy result confirms our hypothesis."),
    ])
    result = validate_duplicate_claims(parsed)
    codes = [f.code for f in result.findings]
    assert "duplicate-quantitative-claim" in codes


def test_unique_claims_not_flagged() -> None:
    from manuscript_audit.validators.core import validate_duplicate_claims

    parsed = _dup_claim_manuscript([
        ("Results", "Model A achieves 94% accuracy on the test set."),
        ("Discussion", "Model B achieves 87% accuracy in cross-validation."),
    ])
    result = validate_duplicate_claims(parsed)
    assert result.findings == []


def test_duplicate_claim_skips_abstract_section() -> None:
    from manuscript_audit.validators.core import validate_duplicate_claims

    parsed = _dup_claim_manuscript([
        ("Abstract", "Our approach achieves 94% accuracy."),
        ("Results", "We report 94% accuracy on the held-out test set."),
    ])
    # Abstract is in _SKIP_SECTIONS; only Results matches, so no duplicate
    result = validate_duplicate_claims(parsed)
    assert result.findings == []
