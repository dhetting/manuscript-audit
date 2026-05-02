from pathlib import Path

from manuscript_audit.parsers import parse_bibtex, parse_manuscript, parse_markdown_manuscript
from manuscript_audit.routing.rules import classify_manuscript
from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
from manuscript_audit.schemas.routing import ManuscriptClassification
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

    # 150-word abstract — within normal range
    sentence = "This study investigates the effect of X on Y in a controlled setting. "
    parsed = ParsedManuscript(
        manuscript_id="normal",
        source_path="synthetic",
        source_format="markdown",
        title="Normal abstract",
        abstract=sentence * 12,
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


# ---------------------------------------------------------------------------
# Phase 30: Hedging language density validator
# ---------------------------------------------------------------------------


def _discussion_manuscript(body: str):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="hedge-test",
        source_path="synthetic",
        source_format="markdown",
        title="Hedging test",
        full_text="",
        sections=[Section(title="Discussion", level=2, body=body)],
    )


def test_excessive_hedging_detected() -> None:
    from manuscript_audit.validators.core import validate_hedging_density

    # 5 out of 6 sentences hedged = 83% > 25%
    body = (
        "Our results may suggest a modest improvement. "
        "This could be due to increased regularization. "
        "Perhaps the model benefits from larger context windows. "
        "It is possible that the gain diminishes at scale. "
        "The effect might not generalize to other domains. "
        "We observed a consistent trend across runs."  # no hedge
    )
    result = validate_hedging_density(_discussion_manuscript(body))
    codes = [f.code for f in result.findings]
    assert "excessive-hedging-language" in codes
    assert result.findings[0].severity == "minor"


def test_low_hedging_density_not_flagged() -> None:
    from manuscript_audit.validators.core import validate_hedging_density

    body = (
        "Our method outperforms the baseline on all benchmarks. "
        "The improvement is statistically significant at p < 0.01. "
        "These results confirm our hypothesis about regularization. "
        "The model may generalize to related tasks. "  # one hedge
        "Future experiments will explore additional domains."
    )
    result = validate_hedging_density(_discussion_manuscript(body))
    assert result.findings == []


def test_hedging_skips_methods_section() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_hedging_density

    body = (
        "We may preprocess data with normalisation. "
        "This could reduce variance. "
        "Perhaps dropout improves generalisation here. "
        "It is possible to tune the learning rate. "
        "Results might vary by seed."
    )
    parsed = ParsedManuscript(
        manuscript_id="methods-hedge",
        source_path="synthetic",
        source_format="markdown",
        title="Methods hedge",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_hedging_density(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 31: Missing related work section validator
# ---------------------------------------------------------------------------


def _classification(paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.routing import ManuscriptClassification

    return ManuscriptClassification(
        paper_type=paper_type, pathway="data_science", recommended_stack="maximal"
    )


def test_missing_related_work_flagged_for_empirical_paper() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_related_work_coverage

    parsed = ParsedManuscript(
        manuscript_id="no-rw",
        source_path="synthetic",
        source_format="markdown",
        title="No related work",
        full_text="",
        sections=[
            Section(title="Introduction", level=2, body="We study X."),
            Section(title="Methods", level=2, body="We use Y."),
            Section(title="Results", level=2, body="We find Z."),
        ],
    )
    result = validate_related_work_coverage(parsed, _classification("empirical_paper"))
    codes = [f.code for f in result.findings]
    assert "missing-related-work-section" in codes


def test_related_work_present_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_related_work_coverage

    parsed = ParsedManuscript(
        manuscript_id="has-rw",
        source_path="synthetic",
        source_format="markdown",
        title="Has related work",
        full_text="",
        sections=[
            Section(title="Related Work", level=2, body="Prior studies show ..."),
        ],
    )
    result = validate_related_work_coverage(parsed, _classification())
    assert result.findings == []


def test_related_work_skipped_for_theory_paper() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_related_work_coverage

    parsed = ParsedManuscript(
        manuscript_id="theory",
        source_path="synthetic",
        source_format="markdown",
        title="Theory paper",
        full_text="",
    )
    result = validate_related_work_coverage(parsed, _classification("theory_paper"))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 32: Missing limitations section validator
# ---------------------------------------------------------------------------


def test_missing_limitations_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_limitations_coverage

    parsed = ParsedManuscript(
        manuscript_id="no-lim",
        source_path="synthetic",
        source_format="markdown",
        title="No limitations",
        full_text="",
        sections=[
            Section(title="Results", level=2, body="We achieved good results."),
            Section(title="Discussion", level=2, body="Our approach works well."),
        ],
    )
    result = validate_limitations_coverage(parsed, _classification())
    codes = [f.code for f in result.findings]
    assert "missing-limitations-section" in codes


def test_limitations_in_discussion_body_accepted() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_limitations_coverage

    parsed = ParsedManuscript(
        manuscript_id="lim-in-disc",
        source_path="synthetic",
        source_format="markdown",
        title="Limitations in discussion",
        full_text="",
        sections=[
            Section(
                title="Discussion",
                level=2,
                body="Our method has a clear limitation: it requires large datasets.",
            )
        ],
    )
    result = validate_limitations_coverage(parsed, _classification())
    assert result.findings == []


def test_dedicated_limitations_section_accepted() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_limitations_coverage

    parsed = ParsedManuscript(
        manuscript_id="has-lim-section",
        source_path="synthetic",
        source_format="markdown",
        title="Has limitations section",
        full_text="",
        sections=[
            Section(title="Limitations", level=2, body="We note several constraints."),
        ],
    )
    result = validate_limitations_coverage(parsed, _classification())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 33: Acronym consistency validator
# ---------------------------------------------------------------------------


def _acronym_manuscript(sections: list[tuple[str, str]], abstract: str = ""):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="acro-test",
        source_path="synthetic",
        source_format="markdown",
        title="Acronym test",
        abstract=abstract,
        full_text="",
        sections=[Section(title=t, level=2, body=b) for t, b in sections],
    )


def test_acronym_used_before_definition_flagged() -> None:
    from manuscript_audit.validators.core import validate_acronym_consistency

    # BERT used in abstract before definition appears in Introduction
    parsed = _acronym_manuscript(
        abstract="We fine-tune BERT on our dataset.",
        sections=[
            ("Introduction",
             "Bidirectional Encoder Representations from Transformers (BERT) has become standard."),
        ],
    )
    result = validate_acronym_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "acronym-used-before-definition" in codes
    assert any("BERT" in f.message for f in result.findings)


def test_acronym_defined_before_use_not_flagged() -> None:
    from manuscript_audit.validators.core import validate_acronym_consistency

    parsed = _acronym_manuscript(sections=[
        ("Introduction",
         "Bidirectional Encoder Representations from Transformers (BERT) has become standard."),
        ("Methods", "We apply BERT techniques to the corpus."),
    ])
    result = validate_acronym_consistency(parsed)
    early = [f for f in result.findings if f.code == "acronym-used-before-definition"]
    assert not early


def test_undefined_acronym_flagged() -> None:
    from manuscript_audit.validators.core import validate_acronym_consistency

    # BERT used throughout but never defined
    parsed = _acronym_manuscript(sections=[
        ("Methods", "We fine-tune BERT on our dataset."),
        ("Results", "BERT achieves the best performance."),
    ])
    result = validate_acronym_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "undefined-acronym" in codes
    assert any("BERT" in f.message for f in result.findings)


def test_common_acronyms_exempted() -> None:
    from manuscript_audit.validators.core import validate_acronym_consistency

    parsed = _acronym_manuscript(sections=[
        ("Methods", "We use the API to fetch data via HTTP and store as JSON."),
    ])
    result = validate_acronym_consistency(parsed)
    # API, HTTP, JSON should not be flagged as undefined
    flagged = {f.evidence[0] for f in result.findings if f.evidence}
    assert "API" not in flagged
    assert "HTTP" not in flagged
    assert "JSON" not in flagged


# ---------------------------------------------------------------------------
# Phase 34: Methods tense consistency validator
# ---------------------------------------------------------------------------


def _methods_tense_manuscript(body: str):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="tense-test",
        source_path="synthetic",
        source_format="markdown",
        title="Tense test",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )


def test_present_tense_heavy_methods_flagged() -> None:
    from manuscript_audit.validators.core import validate_methods_tense_consistency

    # 5 present-only sentences out of 5 tense-bearing = 100%
    body = (
        "We use gradient descent to optimize the model. "
        "The learning rate is set to 0.001. "
        "We apply dropout with probability 0.3. "
        "The model has three hidden layers. "
        "We train for 50 epochs on a single GPU."
    )
    result = validate_methods_tense_consistency(_methods_tense_manuscript(body))
    codes = [f.code for f in result.findings]
    assert "inconsistent-methods-tense" in codes


def test_past_tense_methods_not_flagged() -> None:
    from manuscript_audit.validators.core import validate_methods_tense_consistency

    body = (
        "We used gradient descent to optimize the model. "
        "The learning rate was set to 0.001. "
        "We applied dropout with probability 0.3. "
        "The model had three hidden layers. "
        "We trained for 50 epochs on a single GPU."
    )
    result = validate_methods_tense_consistency(_methods_tense_manuscript(body))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 35: Sentence length outlier validator
# ---------------------------------------------------------------------------


def test_overlong_sentence_detected() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_sentence_length_outliers

    long_sentence = "word " * 65  # 65 words > 60 threshold
    parsed = ParsedManuscript(
        manuscript_id="long-sent",
        source_path="synthetic",
        source_format="markdown",
        title="Long sentence test",
        full_text="",
        sections=[Section(title="Discussion", level=2, body=long_sentence.strip())],
    )
    result = validate_sentence_length_outliers(parsed)
    codes = [f.code for f in result.findings]
    assert "overlong-sentence" in codes


def test_normal_sentences_not_flagged() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_sentence_length_outliers

    body = (
        "We evaluated the model on three datasets. "
        "The results show consistent improvement. "
        "These findings align with prior work."
    )
    parsed = ParsedManuscript(
        manuscript_id="normal-sent",
        source_path="synthetic",
        source_format="markdown",
        title="Normal sentences",
        full_text="",
        sections=[Section(title="Results", level=2, body=body)],
    )
    result = validate_sentence_length_outliers(parsed)
    assert result.findings == []


def test_sentence_length_capped_per_section() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import (
        _FINDINGS_PER_SECTION_CAP,
        validate_sentence_length_outliers,
    )

    # 5 overlong sentences — should only produce _FINDINGS_PER_SECTION_CAP findings
    long = ("word " * 65).strip()
    body = ". ".join([long] * 5)
    parsed = ParsedManuscript(
        manuscript_id="capped",
        source_path="synthetic",
        source_format="markdown",
        title="Cap test",
        full_text="",
        sections=[Section(title="Discussion", level=2, body=body)],
    )
    result = validate_sentence_length_outliers(parsed)
    assert len(result.findings) <= _FINDINGS_PER_SECTION_CAP


# ---------------------------------------------------------------------------
# Phase 37 – citation cluster gap
# ---------------------------------------------------------------------------

def _citation_gap_manuscript(
    sections: list[tuple[str, str]],
    paper_type: str = "empirical_paper",
):  # type: ignore[return]
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="gap-test",
        source_path="synthetic",
        source_format="markdown",
        title="Gap test",
        full_text="",
        sections=[Section(title=t, level=2, body=b) for t, b in sections],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_citation_cluster_gap_fires() -> None:
    from manuscript_audit.validators.core import validate_citation_cluster_gap

    # 10 sentences, first 8 have no citation, last 2 have one
    uncited = "This observation supports the hypothesis. " * 8
    cited = "As shown in [1], results confirm. As described by Smith et al. 2020, further. "
    body = uncited + cited
    parsed, clf = _citation_gap_manuscript([("Results", body)])
    result = validate_citation_cluster_gap(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "citation-cluster-gap" in codes


def test_citation_cluster_gap_short_section_skipped() -> None:
    from manuscript_audit.validators.core import validate_citation_cluster_gap

    # Only 6 sentences (below 8 minimum) — should not fire
    body = "Sentence one. Sentence two. Sentence three. Sentence four. Sentence five. Sentence six."
    parsed, clf = _citation_gap_manuscript([("Results", body)])
    result = validate_citation_cluster_gap(parsed, clf)
    assert result.findings == []


def test_citation_cluster_gap_theory_skipped() -> None:
    from manuscript_audit.validators.core import validate_citation_cluster_gap

    uncited = "This observation supports the hypothesis. " * 10
    parsed, clf = _citation_gap_manuscript([("Results", uncited)], paper_type="math_stats_theory")
    result = validate_citation_cluster_gap(parsed, clf)
    assert result.findings == []


def test_citation_cluster_gap_no_fire_when_citations_interspersed() -> None:
    from manuscript_audit.validators.core import validate_citation_cluster_gap

    # Citations every 3 sentences — no gap of 5+
    sentence = "We observe the following. As shown in [1], results hold. These support the model. "
    body = sentence * 4
    parsed, clf = _citation_gap_manuscript([("Discussion", body)])
    result = validate_citation_cluster_gap(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "citation-cluster-gap" not in codes


# ---------------------------------------------------------------------------
# Phase 38 – power-word overuse
# ---------------------------------------------------------------------------

def test_power_word_overuse_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section  # noqa: F401
    from manuscript_audit.validators.core import validate_power_word_overuse

    abstract = (
        "We present a novel novel novel novel approach to this novel novel problem."
    )
    parsed = ParsedManuscript(
        manuscript_id="pw-test",
        source_path="synthetic",
        source_format="markdown",
        title="Power words",
        full_text="",
        abstract=abstract,
        sections=[],
    )
    result = validate_power_word_overuse(parsed)
    codes = [f.code for f in result.findings]
    assert "power-word-overuse" in codes
    assert any("novel" in f.message for f in result.findings)


def test_power_word_overuse_no_fire_below_threshold() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_power_word_overuse

    abstract = "We present a novel approach. This is a significant advance."
    parsed = ParsedManuscript(
        manuscript_id="pw-ok",
        source_path="synthetic",
        source_format="markdown",
        title="OK",
        full_text="",
        abstract=abstract,
        sections=[],
    )
    result = validate_power_word_overuse(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 39 – number format consistency
# ---------------------------------------------------------------------------

def test_number_format_inconsistency_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_number_format_consistency

    body = "We processed 10000 samples and 10,000 controls in the same batch."
    parsed = ParsedManuscript(
        manuscript_id="nf-test",
        source_path="synthetic",
        source_format="markdown",
        title="Numbers",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_number_format_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "number-format-inconsistency" in codes


def test_number_format_consistent_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_number_format_consistency

    body = "We processed 10,000 samples and 20,000 controls."
    parsed = ParsedManuscript(
        manuscript_id="nf-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Numbers OK",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_number_format_consistency(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 40 – abstract keyword coverage
# ---------------------------------------------------------------------------

def test_abstract_keyword_coverage_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abstract_keyword_coverage

    abstract = (
        "We introduce a fine-tuning method for Neural Networks using "
        "Stochastic Gradient Descent and back-propagation."
    )
    # Body mentions none of those technical terms
    parsed = ParsedManuscript(
        manuscript_id="kw-test",
        source_path="synthetic",
        source_format="markdown",
        title="KW",
        full_text="",
        abstract=abstract,
        sections=[
            Section(title="Methods", level=2, body="We do stuff with data and compute results."),
        ],
    )
    result = validate_abstract_keyword_coverage(parsed)
    codes = [f.code for f in result.findings]
    assert "abstract-body-disconnect" in codes


def test_abstract_keyword_coverage_passes_when_terms_present() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abstract_keyword_coverage

    abstract = (
        "We introduce fine-tuning for Neural Networks using "
        "Stochastic Gradient Descent."
    )
    body = (
        "We apply fine-tuning to Neural Networks. "
        "Stochastic Gradient Descent is used throughout our experiments. "
        "Results confirm the method."
    )
    parsed = ParsedManuscript(
        manuscript_id="kw-ok",
        source_path="synthetic",
        source_format="markdown",
        title="KW OK",
        full_text="",
        abstract=abstract,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_abstract_keyword_coverage(parsed)
    assert result.findings == []


def test_abstract_keyword_coverage_sparse_abstract_skipped() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abstract_keyword_coverage

    abstract = "We study data."  # fewer than _ABSTRACT_KEYWORD_MIN_TERMS terms
    parsed = ParsedManuscript(
        manuscript_id="kw-sparse",
        source_path="synthetic",
        source_format="markdown",
        title="Sparse",
        full_text="",
        abstract=abstract,
        sections=[Section(title="Methods", level=2, body="Some content.")],
    )
    result = validate_abstract_keyword_coverage(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 42 – contribution claim count
# ---------------------------------------------------------------------------

def test_contribution_claim_fires_when_fewer_items_in_body() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_contribution_claim_count

    abstract = "We make three key contributions to this field."
    # Only 1 enumerated item in body
    body = "1. We propose a method. This method is efficient."
    parsed = ParsedManuscript(
        manuscript_id="contrib-test",
        source_path="synthetic",
        source_format="markdown",
        title="Contrib",
        full_text="",
        abstract=abstract,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_contribution_claim_count(parsed)
    codes = [f.code for f in result.findings]
    assert "contribution-count-mismatch" in codes


def test_contribution_claim_passes_when_items_match() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_contribution_claim_count

    abstract = "We make two contributions."
    body = "1. First contribution.\n2. Second contribution."
    parsed = ParsedManuscript(
        manuscript_id="contrib-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Contrib OK",
        full_text="",
        abstract=abstract,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_contribution_claim_count(parsed)
    assert result.findings == []


def test_contribution_claim_skipped_when_no_claim() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_contribution_claim_count

    parsed = ParsedManuscript(
        manuscript_id="no-claim",
        source_path="synthetic",
        source_format="markdown",
        title="No claim",
        full_text="",
        abstract="We study something interesting.",
        sections=[Section(title="Methods", level=2, body="We do things.")],
    )
    result = validate_contribution_claim_count(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 43 – first-person consistency
# ---------------------------------------------------------------------------

def test_first_person_inconsistency_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_first_person_consistency

    body = (
        "We conducted the experiment. We gathered data. We analyzed results. "
        "We discussed findings. I believe this is important. I confirmed the outcome. "
        "I computed the statistics."
    )
    parsed = ParsedManuscript(
        manuscript_id="fp-test",
        source_path="synthetic",
        source_format="markdown",
        title="FP test",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_first_person_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "first-person-inconsistency" in codes


def test_first_person_consistent_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_first_person_consistency

    body = "We conducted the experiment. We gathered data. We analyzed results."
    parsed = ParsedManuscript(
        manuscript_id="fp-ok",
        source_path="synthetic",
        source_format="markdown",
        title="FP OK",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_first_person_consistency(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 44 – caption quality
# ---------------------------------------------------------------------------

def test_short_caption_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_caption_quality

    parsed = ParsedManuscript(
        manuscript_id="cap-test",
        source_path="synthetic",
        source_format="markdown",
        title="Caption test",
        full_text="",
        figure_definitions=["Short cap."],
        sections=[],
    )
    result = validate_caption_quality(parsed)
    codes = [f.code for f in result.findings]
    assert "short-caption" in codes


def test_caption_missing_period_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_caption_quality

    caption = "A detailed description of the experimental setup and results shown here"
    parsed = ParsedManuscript(
        manuscript_id="cap-period",
        source_path="synthetic",
        source_format="markdown",
        title="Caption period",
        full_text="",
        table_definitions=[caption],
        sections=[],
    )
    result = validate_caption_quality(parsed)
    codes = [f.code for f in result.findings]
    assert "caption-missing-period" in codes


def test_good_caption_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_caption_quality

    caption = (
        "Distribution of test accuracy across all 10 experimental runs with standard deviation."
    )
    parsed = ParsedManuscript(
        manuscript_id="cap-good",
        source_path="synthetic",
        source_format="markdown",
        title="Good caption",
        full_text="",
        figure_definitions=[caption],
        sections=[],
    )
    result = validate_caption_quality(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 45 – reference staleness
# ---------------------------------------------------------------------------

def _staleness_manuscript(years: list[str], paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification

    entries = [
        BibliographyEntry(
            key=f"ref{i}",
            raw_text=f"Ref {i}",
            year=y,
            source="bibtex",
        )
        for i, y in enumerate(years)
    ]
    parsed = ParsedManuscript(
        manuscript_id="stale-test",
        source_path="synthetic",
        source_format="markdown",
        title="Stale test",
        full_text="",
        bibliography_entries=entries,
        sections=[],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_stale_references_fires() -> None:
    from manuscript_audit.validators.core import validate_reference_staleness

    # 12 entries all from 2000 (>10 years old)
    years = ["2000"] * 12
    parsed, clf = _staleness_manuscript(years)
    result = validate_reference_staleness(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "stale-reference-majority" in codes


def test_stale_references_no_fire_recent() -> None:
    import datetime

    from manuscript_audit.validators.core import validate_reference_staleness
    current = datetime.date.today().year
    # 12 entries all from last 5 years
    years = [str(current - i % 5) for i in range(12)]
    parsed, clf = _staleness_manuscript(years)
    result = validate_reference_staleness(parsed, clf)
    assert result.findings == []


def test_stale_references_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_reference_staleness

    years = ["2000"] * 12
    parsed, clf = _staleness_manuscript(years, paper_type="math_stats_theory")
    result = validate_reference_staleness(parsed, clf)
    assert result.findings == []


def test_stale_references_skipped_too_few_entries() -> None:
    from manuscript_audit.validators.core import validate_reference_staleness

    years = ["2000"] * 5  # below _STALE_MIN_ENTRIES
    parsed, clf = _staleness_manuscript(years)
    result = validate_reference_staleness(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 47 – terminology drift
# ---------------------------------------------------------------------------

def test_terminology_drift_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_terminology_drift

    body_a = "We use fine-tuning to adapt the model. " * 3
    body_b = "The fine tuning procedure is described below. " * 2
    parsed = ParsedManuscript(
        manuscript_id="drift-test",
        source_path="synthetic",
        source_format="markdown",
        title="Drift",
        full_text="",
        sections=[
            Section(title="Methods", level=2, body=body_a),
            Section(title="Results", level=2, body=body_b),
        ],
    )
    result = validate_terminology_drift(parsed)
    codes = [f.code for f in result.findings]
    assert "terminology-drift" in codes


def test_terminology_drift_consistent_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_terminology_drift

    body = "We use fine-tuning throughout. Fine-tuning is applied consistently. Fine-tuning works."
    parsed = ParsedManuscript(
        manuscript_id="drift-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Consistent",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_terminology_drift(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 48 – introduction structure
# ---------------------------------------------------------------------------

def test_intro_structure_fires_missing_arcs() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_introduction_structure

    # Intro with no gap statement and no contribution statement
    intro = (
        "Machine learning is important for many tasks. "
        "Many researchers have studied this topic. "
        "Results show improvements across benchmarks. "
        "The field continues to advance rapidly with new methods. "
        "Several approaches have been proposed over the years. "
        "Deep learning has shown strong performance on vision and language tasks. "
        "This paper focuses on classification problems that arise in practice. "
    ) * 3
    parsed = ParsedManuscript(
        manuscript_id="intro-test",
        source_path="synthetic",
        source_format="markdown",
        title="Intro test",
        full_text="",
        sections=[Section(title="Introduction", level=2, body=intro)],
    )
    result = validate_introduction_structure(parsed)
    codes = [f.code for f in result.findings]
    assert "missing-introduction-arc" in codes


def test_intro_structure_passes_with_all_arcs() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_introduction_structure

    intro = (
        "A key challenge in NLP is handling long-range dependencies. "
        "Despite many attempts, no prior work has solved this efficiently. "
        "However, existing methods lack scalability for large corpora. "
        "We propose a novel attention mechanism that addresses this gap. "
        "In this paper, we present extensive experiments demonstrating the approach. "
        "The method is evaluated on multiple benchmarks and achieves strong results. "
        "Our contributions include a new model, dataset, and evaluation protocol. "
        "We describe the implementation in detail for reproducibility purposes. "
    ) * 2
    parsed = ParsedManuscript(
        manuscript_id="intro-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Intro OK",
        full_text="",
        sections=[Section(title="Introduction", level=2, body=intro)],
    )
    result = validate_introduction_structure(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 49 – reproducibility checklist
# ---------------------------------------------------------------------------

def _repro_manuscript(body: str, paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="repro-test",
        source_path="synthetic",
        source_format="markdown",
        title="Repro",
        full_text=body,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_reproducibility_fires_for_missing_elements() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_checklist

    # No dataset, no code, no seed, no hyperparams
    body = "We performed analysis on collected samples and computed statistics."
    parsed, clf = _repro_manuscript(body)
    result = validate_reproducibility_checklist(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-reproducibility-element" in codes
    assert len(result.findings) >= 2  # at least dataset and seed missing


def test_reproducibility_skipped_for_theory() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_checklist

    body = "We performed analysis on collected samples and computed statistics."
    parsed, clf = _repro_manuscript(body, paper_type="math_stats_theory")
    result = validate_reproducibility_checklist(parsed, clf)
    assert result.findings == []


def test_reproducibility_no_fire_when_present() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_checklist

    body = (
        "We used the MNIST dataset for training. "
        "Code is available at https://github.com/example/repo. "
        "We fixed the random seed to 42 for reproducibility. "
        "The learning rate was set to 0.001 with batch size 32. "
        "We ran for 100 epochs with dropout of 0.5. "
    )
    parsed, clf = _repro_manuscript(body)
    result = validate_reproducibility_checklist(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 50 – self-citation ratio
# ---------------------------------------------------------------------------

def test_self_citation_fires() -> None:
    from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript
    from manuscript_audit.validators.core import validate_self_citation_ratio

    entries = []
    for i in range(10):
        authors = ["Smith, John", "Jones, Alice"] if i < 7 else ["Brown, Bob"]
        entries.append(
            BibliographyEntry(
                key=f"ref{i}", raw_text=f"Ref {i}", year="2020",
                authors=authors, source="bibtex",
            )
        )
    parsed = ParsedManuscript(
        manuscript_id="selfcite-test",
        source_path="synthetic",
        source_format="markdown",
        title="Self-cite",
        full_text="",
        bibliography_entries=entries,
        sections=[],
    )
    result = validate_self_citation_ratio(parsed)
    codes = [f.code for f in result.findings]
    assert "high-self-citation-ratio" in codes


def test_self_citation_no_fire_diverse_authors() -> None:
    from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript
    from manuscript_audit.validators.core import validate_self_citation_ratio

    names = ["Smith", "Jones", "Brown", "Davis", "Wilson", "Taylor", "Anderson", "Thomas"]
    entries = [
        BibliographyEntry(
            key=f"ref{i}", raw_text=f"Ref {i}", year="2020",
            authors=[f"{n}, A."], source="bibtex",
        )
        for i, n in enumerate(names)
    ]
    parsed = ParsedManuscript(
        manuscript_id="selfcite-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Diverse",
        full_text="",
        bibliography_entries=entries,
        sections=[],
    )
    result = validate_self_citation_ratio(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 51 – conclusion scope
# ---------------------------------------------------------------------------

def test_conclusion_scope_fires_on_novel_metrics() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_conclusion_scope

    abstract = "We achieve 85% accuracy on the benchmark."
    conclusion = (
        "In this work we achieve 92% accuracy, 3x speedup, and 47% reduction in error. "
        "Our results show 2.5x improvement over baseline with 18% cost reduction. "
        "We demonstrate 99% precision on the held-out test set."
    )
    parsed = ParsedManuscript(
        manuscript_id="conc-scope",
        source_path="synthetic",
        source_format="markdown",
        title="Conclusion scope",
        full_text="",
        abstract=abstract,
        sections=[
            Section(title="Results", level=2, body="Accuracy reached 85% on the benchmark."),
            Section(title="Conclusion", level=2, body=conclusion),
        ],
    )
    result = validate_conclusion_scope(parsed)
    codes = [f.code for f in result.findings]
    assert "conclusion-scope-creep" in codes


def test_conclusion_scope_no_fire_when_metrics_established() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_conclusion_scope

    abstract = "We achieve 85% accuracy and 3x speedup."
    results_body = "Accuracy is 85%. Speedup is 3x over baseline. Error rate is 15%."
    conclusion = "In summary, we achieve 85% accuracy and 3x speedup as shown in results."
    parsed = ParsedManuscript(
        manuscript_id="conc-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Conclusion OK",
        full_text="",
        abstract=abstract,
        sections=[
            Section(title="Results", level=2, body=results_body),
            Section(title="Conclusion", level=2, body=conclusion),
        ],
    )
    result = validate_conclusion_scope(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 53 – equation density
# ---------------------------------------------------------------------------

def _eq_density_manuscript(eq_count: int, section_count: int, pathway: str = "math_stats_theory"):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    sections = [
        Section(title=f"Section {i}", level=2, body="Some content here with words.")
        for i in range(section_count)
    ]
    parsed = ParsedManuscript(
        manuscript_id="eq-test",
        source_path="synthetic",
        source_format="markdown",
        title="EQ test",
        full_text="",
        equation_blocks=[f"E_{i} = x" for i in range(eq_count)],
        sections=sections,
    )
    clf = ManuscriptClassification(
        paper_type="math_stats_theory",
        pathway=pathway,
        recommended_stack="standard",
    )
    return parsed, clf


def test_equation_density_fires() -> None:
    from manuscript_audit.validators.core import validate_equation_density

    # 4 sections, 0 equations → ratio 0.0 < 0.5
    parsed, clf = _eq_density_manuscript(eq_count=0, section_count=4)
    result = validate_equation_density(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "low-equation-density" in codes


def test_equation_density_no_fire_sufficient() -> None:
    from manuscript_audit.validators.core import validate_equation_density

    # 4 sections, 4 equations → ratio 1.0 ≥ 0.5
    parsed, clf = _eq_density_manuscript(eq_count=4, section_count=4)
    result = validate_equation_density(parsed, clf)
    assert result.findings == []


def test_equation_density_skipped_non_theory() -> None:
    from manuscript_audit.validators.core import validate_equation_density

    parsed, clf = _eq_density_manuscript(eq_count=0, section_count=4, pathway="applied_stats")
    clf.paper_type = "empirical_paper"  # type: ignore[assignment]
    result = validate_equation_density(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 54 – abstract structure
# ---------------------------------------------------------------------------

def test_abstract_structure_fires_missing_result() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_abstract_structure

    abstract = (
        "Machine learning is widely used. We propose a new approach to classification "
        "that leverages deep neural networks. Our framework processes input sequences "
        "and produces structured outputs via attention layers. "
        "The architecture consists of an encoder and decoder module. "
        "We train the system on standard benchmarks using cross-entropy loss. "
        "The method is described in detail in the following sections. "
        "This approach handles long-range dependencies effectively and efficiently."
    )
    parsed = ParsedManuscript(
        manuscript_id="abs-struct",
        source_path="synthetic",
        source_format="markdown",
        title="Abs struct",
        full_text="",
        abstract=abstract,
        sections=[],
    )
    result = validate_abstract_structure(parsed)
    codes = [f.code for f in result.findings]
    assert "missing-abstract-component" in codes


def test_abstract_structure_passes_complete() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.validators.core import validate_abstract_structure

    abstract = (
        "Machine learning is widely used for structured prediction tasks. "
        "We propose a new attention mechanism for sequence-to-sequence tasks. "
        "Our framework processes variable-length inputs efficiently. "
        "We show that our approach achieves state-of-the-art accuracy on three benchmarks. "
        "Results demonstrate a 5% improvement over baseline methods on all tasks. "
        "The system is computationally efficient and scales to large datasets effectively."
    )
    parsed = ParsedManuscript(
        manuscript_id="abs-ok",
        source_path="synthetic",
        source_format="markdown",
        title="Abs OK",
        full_text="",
        abstract=abstract,
        sections=[],
    )
    result = validate_abstract_structure(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 55 – URL format
# ---------------------------------------------------------------------------

def test_url_format_malformed_fires() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_url_format

    body = "See www.example.com for details and www.other.org for more."
    parsed = ParsedManuscript(
        manuscript_id="url-test",
        source_path="synthetic",
        source_format="markdown",
        title="URL test",
        full_text=body,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_url_format(parsed)
    codes = [f.code for f in result.findings]
    assert "malformed-url" in codes


def test_url_format_valid_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_url_format

    body = "See https://example.com for details."
    parsed = ParsedManuscript(
        manuscript_id="url-ok",
        source_path="synthetic",
        source_format="markdown",
        title="URL OK",
        full_text=body,
        sections=[Section(title="Methods", level=2, body=body)],
    )
    result = validate_url_format(parsed)
    codes = [f.code for f in result.findings]
    assert "malformed-url" not in codes


# ---------------------------------------------------------------------------
# Phase 56 – figure/table balance
# ---------------------------------------------------------------------------

def _fig_balance_manuscript(n_figs: int, n_tabs: int, paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="fig-balance",
        source_path="synthetic",
        source_format="markdown",
        title="Fig balance",
        full_text="",
        figure_mentions=[f"Figure {i}" for i in range(n_figs)],
        table_mentions=[f"Table {i}" for i in range(n_tabs)],
        sections=[
            Section(title=t, level=2, body="Some content.")
            for t in ["Introduction", "Methods", "Results", "Discussion"]
        ],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_insufficient_figures_fires() -> None:
    from manuscript_audit.validators.core import validate_figure_table_balance

    parsed, clf = _fig_balance_manuscript(n_figs=0, n_tabs=2)
    result = validate_figure_table_balance(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "insufficient-figures" in codes


def test_table_heavy_fires() -> None:
    from manuscript_audit.validators.core import validate_figure_table_balance

    parsed, clf = _fig_balance_manuscript(n_figs=2, n_tabs=8)
    result = validate_figure_table_balance(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "table-heavy" in codes


def test_figure_balance_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_table_balance

    parsed, clf = _fig_balance_manuscript(n_figs=3, n_tabs=2)
    result = validate_figure_table_balance(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 57 – section ordering
# ---------------------------------------------------------------------------

def _ordering_manuscript(titles: list[str], paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="order-test",
        source_path="synthetic",
        source_format="markdown",
        title="Order test",
        full_text="",
        sections=[Section(title=t, level=2, body="Content here.") for t in titles],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_section_ordering_violation_fires() -> None:
    from manuscript_audit.validators.core import validate_section_ordering

    # Results before Methods — violation
    parsed, clf = _ordering_manuscript(
        ["Introduction", "Results", "Methods", "Discussion"]
    )
    result = validate_section_ordering(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "section-order-violation" in codes


def test_section_ordering_correct_no_fire() -> None:
    from manuscript_audit.validators.core import validate_section_ordering

    parsed, clf = _ordering_manuscript(
        ["Introduction", "Methods", "Results", "Discussion"]
    )
    result = validate_section_ordering(parsed, clf)
    assert result.findings == []


def test_section_ordering_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_section_ordering

    parsed, clf = _ordering_manuscript(
        ["Introduction", "Results", "Methods", "Discussion"],
        paper_type="math_stats_theory",
    )
    result = validate_section_ordering(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 59 – author keyword coverage
# ---------------------------------------------------------------------------

def test_keyword_coverage_fires_for_absent_keyword() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_keyword_section_coverage

    full_text = "Keywords: neural networks, optimization, convergence\n\nIntro text."
    parsed = ParsedManuscript(
        manuscript_id="kw-cov",
        source_path="synthetic",
        source_format="markdown",
        title="KW cov",
        full_text=full_text,
        sections=[Section(title="Methods", level=2, body="We use neural networks to optimize.")],
    )
    result = validate_keyword_section_coverage(parsed)
    codes = [f.code for f in result.findings]
    assert "missing-keyword-coverage" in codes
    assert any("convergence" in f.evidence[0] for f in result.findings)


def test_keyword_coverage_no_fire_when_all_present() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_keyword_section_coverage

    full_text = "Keywords: neural networks, optimization\n\nIntro text."
    parsed = ParsedManuscript(
        manuscript_id="kw-cov-ok",
        source_path="synthetic",
        source_format="markdown",
        title="KW cov ok",
        full_text=full_text,
        sections=[
            Section(title="Methods", level=2, body="We use neural networks and optimization."),
        ],
    )
    result = validate_keyword_section_coverage(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 60 – statistical test reporting
# ---------------------------------------------------------------------------

def _stat_manuscript(body: str, paper_type: str = "empirical_paper"):
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="stat-test",
        source_path="synthetic",
        source_format="markdown",
        title="Stat",
        full_text="",
        sections=[Section(title="Methods", level=2, body=body)],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_stat_reporting_fires_when_test_no_pvalue() -> None:
    from manuscript_audit.validators.core import validate_statistical_test_reporting

    body = "We used a t-test to compare groups. Differences were considered significant."
    parsed, clf = _stat_manuscript(body)
    result = validate_statistical_test_reporting(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-p-value-report" in codes


def test_stat_reporting_no_fire_with_pvalue() -> None:
    from manuscript_audit.validators.core import validate_statistical_test_reporting

    body = "We used a t-test to compare groups (p < 0.05). Differences were significant."
    parsed, clf = _stat_manuscript(body)
    result = validate_statistical_test_reporting(parsed, clf)
    assert result.findings == []


def test_stat_reporting_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_statistical_test_reporting

    body = "We used a t-test to compare groups."
    parsed, clf = _stat_manuscript(body, paper_type="math_stats_theory")
    result = validate_statistical_test_reporting(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 61 – effect size reporting
# ---------------------------------------------------------------------------

def test_effect_size_fires_pvalue_no_effect() -> None:
    from manuscript_audit.validators.core import validate_effect_size_reporting

    body = "Group A differed from B (p < 0.01). The comparison was statistically significant."
    parsed, clf = _stat_manuscript(body)
    result = validate_effect_size_reporting(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-effect-size" in codes


def test_effect_size_no_fire_when_reported() -> None:
    from manuscript_audit.validators.core import validate_effect_size_reporting

    body = (
        "Group A differed from B (p < 0.01). Cohen's d = 0.8 indicating large effect. "
        "Results confirm the hypothesis."
    )
    parsed, clf = _stat_manuscript(body)
    result = validate_effect_size_reporting(parsed, clf)
    assert result.findings == []


def test_effect_size_no_fire_no_pvalue() -> None:
    from manuscript_audit.validators.core import validate_effect_size_reporting

    body = "We observed differences between groups in all conditions tested."
    parsed, clf = _stat_manuscript(body)
    result = validate_effect_size_reporting(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 62 – acknowledgments presence
# ---------------------------------------------------------------------------

def _ack_manuscript(
    sections: list[tuple[str, str]],
    full_text: str = "",
    n_bib: int = 6,
    paper_type: str = "empirical_paper",
):
    from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    entries = [
        BibliographyEntry(key=f"r{i}", raw_text=f"Ref {i}", year="2020", source="bibtex")
        for i in range(n_bib)
    ]
    parsed = ParsedManuscript(
        manuscript_id="ack-test",
        source_path="synthetic",
        source_format="markdown",
        title="Ack",
        full_text=full_text,
        bibliography_entries=entries,
        sections=[Section(title=t, level=2, body=b) for t, b in sections],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="applied_stats",
        recommended_stack="standard",
    )
    return parsed, clf


def test_missing_acknowledgments_fires() -> None:
    from manuscript_audit.validators.core import validate_acknowledgments_presence

    parsed, clf = _ack_manuscript([("Methods", "We ran experiments."),
                                    ("Results", "We found results.")])
    result = validate_acknowledgments_presence(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-acknowledgments" in codes


def test_acknowledgments_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_acknowledgments_presence

    parsed, clf = _ack_manuscript([
        ("Methods", "We ran experiments."),
        ("Acknowledgments", "This work was supported by NSF grant 12345."),
    ])
    result = validate_acknowledgments_presence(parsed, clf)
    assert result.findings == []


def test_acknowledgments_funding_in_text_no_fire() -> None:
    from manuscript_audit.validators.core import validate_acknowledgments_presence

    parsed, clf = _ack_manuscript(
        [("Methods", "We ran experiments.")],
        full_text="This research was funded by NSF.",
    )
    result = validate_acknowledgments_presence(parsed, clf)
    assert result.findings == []


def test_acknowledgments_skipped_few_refs() -> None:
    from manuscript_audit.validators.core import validate_acknowledgments_presence

    parsed, clf = _ack_manuscript([("Methods", "We ran experiments.")], n_bib=3)
    result = validate_acknowledgments_presence(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 64 – Conflict of interest disclosure
# ---------------------------------------------------------------------------


def _coi_manuscript(
    sections: list[tuple[str, str]],
    full_text: str = "",
    n_bib: int = 8,
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import (
        BibliographyEntry,
        ParsedManuscript,
        Section,
    )
    from manuscript_audit.schemas.routing import ManuscriptClassification

    bib = [
        BibliographyEntry(
            key=f"r{i}", raw_text=f"Ref {i}.", year="2020", source="bibtex"
        )
        for i in range(n_bib)
    ]
    ms = ParsedManuscript(
        manuscript_id="coi-test",
        source_path="coi.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract text.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        bibliography_entries=bib,
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_missing_coi_fires() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    parsed, clf = _coi_manuscript(
        [("Methods", "We recruited participants from local clinics."),
         ("Results", "We found a significant effect.")]
    )
    result = validate_conflict_of_interest(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-coi-statement" in codes


def test_coi_present_in_section_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    parsed, clf = _coi_manuscript(
        [("Methods", "We ran experiments."),
         ("Conflict of Interest", "The authors declare no competing interests.")]
    )
    result = validate_conflict_of_interest(parsed, clf)
    assert result.findings == []


def test_coi_in_full_text_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    parsed, clf = _coi_manuscript(
        [("Methods", "We ran experiments.")],
        full_text="There are no conflicts of interest to declare.",
    )
    result = validate_conflict_of_interest(parsed, clf)
    assert result.findings == []


def test_coi_skipped_theory_paper() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    parsed, clf = _coi_manuscript(
        [("Methods", "We ran experiments.")],
        paper_type="math_stats_theory",
    )
    result = validate_conflict_of_interest(parsed, clf)
    assert result.findings == []


def test_coi_skipped_few_refs() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    parsed, clf = _coi_manuscript(
        [("Methods", "We ran experiments.")],
        n_bib=3,
    )
    result = validate_conflict_of_interest(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 65 – Data availability statement
# ---------------------------------------------------------------------------


def _data_avail_manuscript(
    sections: list[tuple[str, str]],
    full_text: str = "",
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="data-avail-test",
        source_path="data.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_missing_data_availability_fires() -> None:
    from manuscript_audit.validators.core import validate_data_availability

    parsed, clf = _data_avail_manuscript(
        [("Methods", "We collected data from hospitals."),
         ("Results", "The analysis showed improvements.")]
    )
    result = validate_data_availability(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "missing-data-availability" in codes


def test_data_availability_zenodo_no_fire() -> None:
    from manuscript_audit.validators.core import validate_data_availability

    parsed, clf = _data_avail_manuscript(
        [("Methods", "Data are available on Zenodo at https://zenodo.org/123.")]
    )
    result = validate_data_availability(parsed, clf)
    assert result.findings == []


def test_data_availability_in_full_text_no_fire() -> None:
    from manuscript_audit.validators.core import validate_data_availability

    parsed, clf = _data_avail_manuscript(
        [("Methods", "We ran experiments.")],
        full_text="Data availability: the dataset is available upon reasonable request.",
    )
    result = validate_data_availability(parsed, clf)
    assert result.findings == []


def test_data_availability_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_data_availability

    parsed, clf = _data_avail_manuscript(
        [("Methods", "We prove theorems.")],
        paper_type="math_stats_theory",
    )
    result = validate_data_availability(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 66 – Ethics/IRB statement
# ---------------------------------------------------------------------------


def _ethics_manuscript(sections: list[tuple[str, str]], full_text: str = "") -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="ethics-test",
        source_path="ethics.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full_text,
    )


def test_missing_ethics_human_study_fires() -> None:
    from manuscript_audit.validators.core import validate_ethics_statement

    parsed = _ethics_manuscript(
        [("Methods", "Participants completed questionnaires about their health."),
         ("Results", "Survey responses showed significant trends.")]
    )
    result = validate_ethics_statement(parsed)
    codes = [f.code for f in result.findings]
    assert "missing-ethics-statement" in codes


def test_ethics_irb_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_statement

    parsed = _ethics_manuscript(
        [("Methods",
          "Participants completed questionnaires. "
          "The study was approved by the Institutional Review Board.")]
    )
    result = validate_ethics_statement(parsed)
    assert result.findings == []


def test_ethics_animal_study_fires() -> None:
    from manuscript_audit.validators.core import validate_ethics_statement

    parsed = _ethics_manuscript(
        [("Methods", "Mice were administered the drug at 10 mg/kg.")]
    )
    result = validate_ethics_statement(parsed)
    codes = [f.code for f in result.findings]
    assert "missing-ethics-statement" in codes


def test_ethics_no_human_animal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_statement

    parsed = _ethics_manuscript(
        [("Methods", "We trained a neural network on benchmark datasets.")]
    )
    result = validate_ethics_statement(parsed)
    assert result.findings == []


def test_ethics_iacuc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_statement

    parsed = _ethics_manuscript(
        [("Methods",
          "Mice were used. All procedures were approved by the IACUC committee.")]
    )
    result = validate_ethics_statement(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 67 – Citation style consistency
# ---------------------------------------------------------------------------


def _cite_style_manuscript(body_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="cite-test",
        source_path="cite.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Introduction", level=1, body=body_text)],
        full_text=body_text,
    )


def test_mixed_citation_styles_fires() -> None:
    from manuscript_audit.validators.core import validate_citation_style_consistency

    # 4 numbered + 4 author-year = 8 total, well above 5 min; 50% each
    body = (
        "Regression is common [1][2][3][4]. "
        "Smith 2020 showed gains. Jones 2018 confirmed it. "
        "Brown 2019 extended this. Davis 2021 further verified."
    )
    parsed = _cite_style_manuscript(body)
    result = validate_citation_style_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "citation-style-inconsistency" in codes


def test_uniform_numbered_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_style_consistency

    body = "As shown in [1], [2], [3], [4], [5], the method works."
    parsed = _cite_style_manuscript(body)
    result = validate_citation_style_consistency(parsed)
    assert result.findings == []


def test_few_citations_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_style_consistency

    body = "As shown in [1], the method works."
    parsed = _cite_style_manuscript(body)
    result = validate_citation_style_consistency(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 68 – Cross-reference integrity
# ---------------------------------------------------------------------------


def _xref_manuscript(body_text: str, n_figs: int = 0, n_tabs: int = 0) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="xref-test",
        source_path="xref.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=body_text)],
        figure_definitions=[f"Caption {i}." for i in range(1, n_figs + 1)],
        table_definitions=[f"Caption {i}." for i in range(1, n_tabs + 1)],
        full_text=body_text,
    )


def test_figure_ref_out_of_range_fires() -> None:
    from manuscript_audit.validators.core import validate_cross_reference_integrity

    parsed = _xref_manuscript("See Figure 3 for details.", n_figs=2)
    result = validate_cross_reference_integrity(parsed)
    codes = [f.code for f in result.findings]
    assert "cross-reference-out-of-range" in codes


def test_figure_ref_in_range_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cross_reference_integrity

    parsed = _xref_manuscript("See Figure 2 for details.", n_figs=3)
    result = validate_cross_reference_integrity(parsed)
    assert result.findings == []


def test_table_ref_out_of_range_fires() -> None:
    from manuscript_audit.validators.core import validate_cross_reference_integrity

    parsed = _xref_manuscript("As shown in Table 5.", n_figs=0, n_tabs=2)
    result = validate_cross_reference_integrity(parsed)
    codes = [f.code for f in result.findings]
    assert "cross-reference-out-of-range" in codes


def test_no_definitions_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cross_reference_integrity

    parsed = _xref_manuscript("See Figure 99 for the overview.", n_figs=0)
    result = validate_cross_reference_integrity(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 69 – Decimal precision consistency
# ---------------------------------------------------------------------------


def _decimal_manuscript(body_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="decimal-test",
        source_path="dec.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=body_text)],
        full_text=body_text,
    )


def test_decimal_inconsistency_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_decimal_precision_consistency,
    )

    body = (
        "Accuracy was 85%, recall was 90%, precision was 85.23%, "
        "and F1 was 87.50%."
    )
    parsed = _decimal_manuscript(body)
    result = validate_decimal_precision_consistency(parsed)
    codes = [f.code for f in result.findings]
    assert "decimal-precision-inconsistency" in codes


def test_decimal_consistent_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_decimal_precision_consistency,
    )

    body = "Accuracy was 85.10%, recall was 90.20%, precision was 88.30%, F1 was 87.40%."
    parsed = _decimal_manuscript(body)
    result = validate_decimal_precision_consistency(parsed)
    assert result.findings == []


def test_decimal_too_few_values_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_decimal_precision_consistency,
    )

    body = "Accuracy was 85% and recall was 85.23%."
    parsed = _decimal_manuscript(body)
    result = validate_decimal_precision_consistency(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 70 – Future-work balance
# ---------------------------------------------------------------------------


def _future_work_manuscript(body_text: str, section_title: str = "Conclusion") -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="fw-test",
        source_path="fw.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=section_title, level=1, body=body_text)],
        full_text=body_text,
    )


def test_future_work_heavy_fires() -> None:
    from manuscript_audit.validators.core import validate_future_work_balance

    body = (
        "We will explore new datasets. "
        "Future work will investigate model compression. "
        "We plan to apply this to clinical settings. "
        "We intend to extend the framework. "
        "Future research should investigate causal effects. "
        "We will examine additional baselines in future studies. "
        "We found good results overall."
    )
    parsed = _future_work_manuscript(body)
    result = validate_future_work_balance(parsed)
    codes = [f.code for f in result.findings]
    assert "future-work-heavy" in codes


def test_future_work_balanced_no_fire() -> None:
    from manuscript_audit.validators.core import validate_future_work_balance

    body = (
        "We demonstrated strong performance on the benchmark. "
        "The approach reduces computation time by 30%. "
        "Ablation studies confirmed the contribution of each component. "
        "The results generalise across three datasets. "
        "We validated on held-out test data. "
        "Future work will extend this to video data."
    )
    parsed = _future_work_manuscript(body)
    result = validate_future_work_balance(parsed)
    assert result.findings == []


def test_future_work_skips_non_discussion() -> None:
    from manuscript_audit.validators.core import validate_future_work_balance

    body = (
        "We will explore new datasets. Future work will investigate this. "
        "We plan to apply this. We intend to extend. Future research here. "
        "Future directions are clear."
    )
    parsed = _future_work_manuscript(body, section_title="Introduction")
    result = validate_future_work_balance(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 71 – Null result acknowledgment
# ---------------------------------------------------------------------------


def _null_result_manuscript(
    sections: list[tuple[str, str]],
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    full = " ".join(b for _, b in sections)
    ms = ParsedManuscript(
        manuscript_id="null-test",
        source_path="null.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_missing_null_result_fires() -> None:
    from manuscript_audit.validators.core import validate_null_result_acknowledgment

    results_body = (
        "The model achieved 92% accuracy.\n\n"
        "All experiments showed improvements.\n\n"
        "Performance was consistently superior.\n\n"
        "The method outperformed all baselines."
    )
    parsed, clf = _null_result_manuscript(
        [("Results", results_body),
         ("Discussion", "The results confirm our hypothesis.\n\n"
          "These findings suggest strong generalization.\n\n"
          "The approach is effective across domains.\n\n"
          "The performance gains are robust.")]
    )
    result = validate_null_result_acknowledgment(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "no-negative-results-acknowledged" in codes


def test_null_result_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_acknowledgment

    results_body = (
        "The model achieved 92% accuracy.\n\n"
        "The method failed to improve on dataset B.\n\n"
        "No significant difference was found on task C.\n\n"
        "Performance was generally strong."
    )
    parsed, clf = _null_result_manuscript([("Results", results_body)])
    result = validate_null_result_acknowledgment(parsed, clf)
    assert result.findings == []


def test_null_result_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_null_result_acknowledgment

    results_body = (
        "Theorem 1 holds.\n\nProof follows.\n\nCorollary applies.\n\nQED."
    )
    parsed, clf = _null_result_manuscript(
        [("Results", results_body)],
        paper_type="math_stats_theory",
    )
    result = validate_null_result_acknowledgment(parsed, clf)
    assert result.findings == []


def test_null_result_skipped_few_paragraphs() -> None:
    from manuscript_audit.validators.core import validate_null_result_acknowledgment

    parsed, clf = _null_result_manuscript(
        [("Results", "The model achieved high accuracy. All tests passed.")]
    )
    result = validate_null_result_acknowledgment(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 73 – Hedging language density
# ---------------------------------------------------------------------------


def _hedge_manuscript(
    abstract: str = "",
    intro_body: str = "",
    conclusion_body: str = "",
) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    sections = []
    if intro_body:
        sections.append(Section(title="Introduction", level=1, body=intro_body))
    if conclusion_body:
        sections.append(Section(title="Conclusion", level=1, body=conclusion_body))
    return ParsedManuscript(
        manuscript_id="hedge-test",
        source_path="hedge.md",
        source_format="markdown",
        title="Test",
        abstract=abstract,
        sections=sections,
        full_text=(abstract + " " + intro_body + " " + conclusion_body).strip(),
    )


def test_hedging_dense_fires() -> None:
    from manuscript_audit.validators.core import validate_hedging_language

    abstract = (
        "This study possibly suggests a new method for sequence classification. "
        "The approach perhaps could be useful in certain clinical contexts. "
        "Preliminary results may indicate a broad pattern in the data. "
        "It seems to support the main hypothesis of the paper. "
        "The model appears to explain some of the observed variance to some extent. "
        "We believe the results would seem to generalise across domains."
    )
    parsed = _hedge_manuscript(abstract=abstract)
    result = validate_hedging_language(parsed)
    codes = [f.code for f in result.findings]
    assert "hedging-language-dense" in codes


def test_hedging_low_no_fire() -> None:
    from manuscript_audit.validators.core import validate_hedging_language

    abstract = (
        "This study introduces a novel method for classification. "
        "We demonstrate state-of-the-art results on three benchmarks. "
        "The approach reduces inference time by 30%. "
        "Our method outperforms existing baselines. "
        "This work provides a framework for future research. "
        "Perhaps this extends to other domains. "
        "Results confirm the hypothesis."
    )
    parsed = _hedge_manuscript(abstract=abstract)
    result = validate_hedging_language(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 74 – Duplicate section content
# ---------------------------------------------------------------------------


def _dup_manuscript(sections: list[tuple[str, str]]) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    full = " ".join(b for _, b in sections)
    return ParsedManuscript(
        manuscript_id="dup-test",
        source_path="dup.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full,
    )


def test_duplicate_sections_fires() -> None:
    from manuscript_audit.validators.core import validate_duplicate_section_content

    shared = (
        "The model was trained on ImageNet data using SGD optimizer. "
        "We applied data augmentation including random cropping and flipping. "
        "Early stopping was used to prevent overfitting. "
        "The learning rate was set to 0.001 with cosine annealing schedule. "
    )
    parsed = _dup_manuscript([
        ("Introduction",
         "We present a deep learning approach. " + shared +
         "This paper makes three contributions."),
        ("Methods",
         "Standard methodology was used. We split data 80/20. "
         "All hyperparameters were tuned via cross-validation. "
         "Statistical significance was assessed using t-tests."),
        ("Discussion",
         "The results confirm our hypothesis. " + shared +
         "Future work will extend this to other domains."),
    ])
    result = validate_duplicate_section_content(parsed)
    codes = [f.code for f in result.findings]
    assert "duplicate-section-content" in codes


def test_no_duplication_no_fire() -> None:
    from manuscript_audit.validators.core import validate_duplicate_section_content

    parsed = _dup_manuscript([
        ("Introduction",
         "Neural networks have achieved remarkable results in vision. "
         "We propose a novel architecture for sequence modeling. "
         "This work addresses the problem of long-range dependencies. "
         "Our contributions include a new attention mechanism."),
        ("Methods",
         "We train on 50000 samples using Adam optimizer. "
         "The model uses 12 transformer layers with 768 hidden units. "
         "Training takes 24 hours on 8 V100 GPUs."),
        ("Discussion",
         "Results show consistent improvements across all benchmarks. "
         "Ablation studies reveal the importance of positional encoding. "
         "The approach generalises to low-resource settings."),
    ])
    result = validate_duplicate_section_content(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 75 – Abstract length
# ---------------------------------------------------------------------------


def _abstract_manuscript(abstract: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="ablen-test",
        source_path="ablen.md",
        source_format="markdown",
        title="Test",
        abstract=abstract,
        full_text=abstract,
    )


def test_abstract_too_short_fires() -> None:
    from manuscript_audit.validators.core import validate_abstract_length

    abstract = "This paper proposes a new algorithm for graph classification."
    parsed = _abstract_manuscript(abstract)
    result = validate_abstract_length(parsed)
    codes = [f.code for f in result.findings]
    assert "abstract-too-short" in codes


def test_abstract_too_long_fires() -> None:
    from manuscript_audit.validators.core import validate_abstract_length

    # Generate 360-word abstract
    sentence = "This study evaluates deep neural networks on large datasets. "
    abstract = sentence * 40
    parsed = _abstract_manuscript(abstract)
    result = validate_abstract_length(parsed)
    codes = [f.code for f in result.findings]
    assert "overlong-abstract" in codes


def test_abstract_normal_length_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_length

    sentence = "This study presents a novel method for image segmentation. "
    abstract = sentence * 22  # ~176 words — in range
    parsed = _abstract_manuscript(abstract)
    result = validate_abstract_length(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 76 – Methods depth
# ---------------------------------------------------------------------------


def _methods_depth_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="methods-test",
        source_path="methods.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_thin_methods_fires() -> None:
    from manuscript_audit.validators.core import validate_methods_depth

    parsed, clf = _methods_depth_manuscript(
        "We collected data and ran a regression. Standard errors were reported."
    )
    result = validate_methods_depth(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "thin-methods" in codes


def test_adequate_methods_no_fire() -> None:
    from manuscript_audit.validators.core import validate_methods_depth

    body = "We collected survey data from 1200 participants across 5 sites. " * 15
    parsed, clf = _methods_depth_manuscript(body)
    result = validate_methods_depth(parsed, clf)
    assert result.findings == []


def test_methods_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_methods_depth

    parsed, clf = _methods_depth_manuscript(
        "We define the algorithm formally.", paper_type="math_stats_theory"
    )
    result = validate_methods_depth(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 77 – Passive voice ratio
# ---------------------------------------------------------------------------


def _passive_manuscript(methods_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="passive-test",
        source_path="passive.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )


def test_passive_dominant_fires() -> None:
    from manuscript_audit.validators.core import validate_passive_voice_density

    body = (
        "The data was collected from hospitals. "
        "Samples were processed overnight. "
        "Results were recorded daily. "
        "Statistical tests were performed. "
        "Outliers were removed manually. "
        "The model was trained on GPU. "
        "Parameters were tuned extensively. "
        "The experiment was repeated three times. "
        "Findings were validated independently. "
        "The protocol was approved by the board."
    )
    parsed = _passive_manuscript(body)
    result = validate_passive_voice_density(parsed)
    codes = [f.code for f in result.findings]
    assert "high-passive-voice-density" in codes


def test_mixed_voice_no_fire() -> None:
    from manuscript_audit.validators.core import validate_passive_voice_density

    body = (
        "We collected data from 200 participants. "
        "Participants were randomly assigned to conditions. "
        "We recorded responses using a digital device. "
        "The intervention was administered over 4 weeks. "
        "We analysed results using linear mixed models. "
        "All models were fitted in R. "
        "We report 95% confidence intervals. "
        "Data are publicly available online."
    )
    parsed = _passive_manuscript(body)
    result = validate_passive_voice_density(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 78 – List overuse
# ---------------------------------------------------------------------------


def _list_manuscript(section_body: str, section_title: str = "Discussion") -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="list-test",
        source_path="list.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=section_title, level=1, body=section_body)],
        full_text=section_body,
    )


def test_list_heavy_fires() -> None:
    from manuscript_audit.validators.core import validate_list_overuse

    body = (
        "Our results show the following:\n"
        "- The accuracy improved significantly\n"
        "- The method is computationally efficient\n"
        "- Generalisation to new domains was confirmed\n"
        "- The approach is scalable\n"
        "- Results are reproducible\n"
        "- Performance exceeds baselines\n"
        "Summary: this is a good result.\n"
    )
    parsed = _list_manuscript(body)
    result = validate_list_overuse(parsed)
    codes = [f.code for f in result.findings]
    assert "list-heavy-section" in codes


def test_list_sparse_no_fire() -> None:
    from manuscript_audit.validators.core import validate_list_overuse

    body = (
        "Our results demonstrate clear improvements across domains. "
        "The proposed method achieves strong performance with low overhead. "
        "Ablation studies confirm the contribution of each component:\n"
        "- Attention module improves accuracy by 2%\n"
        "- Data augmentation reduces overfitting\n"
        "Overall the approach is well-supported by the evidence."
    )
    parsed = _list_manuscript(body)
    result = validate_list_overuse(parsed)
    assert result.findings == []


def test_list_skips_methods_section() -> None:
    from manuscript_audit.validators.core import validate_list_overuse

    body = (
        "- Step 1: collect data\n"
        "- Step 2: preprocess\n"
        "- Step 3: train model\n"
        "- Step 4: evaluate\n"
        "- Step 5: report results\n"
        "- Step 6: validate\n"
        "- Step 7: publish\n"
    )
    parsed = _list_manuscript(body, section_title="Methods")
    result = validate_list_overuse(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 79 – Section balance
# ---------------------------------------------------------------------------


def _balance_manuscript(
    sections: list[tuple[str, str]],
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    full = " ".join(b for _, b in sections)
    ms = ParsedManuscript(
        manuscript_id="balance-test",
        source_path="balance.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_section_imbalance_fires() -> None:
    from manuscript_audit.validators.core import validate_section_balance

    big_body = "The results showed significant improvement. " * 50
    parsed, clf = _balance_manuscript([
        ("Introduction", "Brief intro. We propose a method."),
        ("Methods", "We used linear regression."),
        ("Results", big_body),
        ("Discussion", "The findings are important."),
    ])
    result = validate_section_balance(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "section-length-imbalance" in codes


def test_balanced_sections_no_fire() -> None:
    from manuscript_audit.validators.core import validate_section_balance

    balanced_body = "The results showed improvement. " * 8
    parsed, clf = _balance_manuscript([
        ("Introduction", balanced_body),
        ("Methods", balanced_body),
        ("Results", balanced_body),
        ("Discussion", balanced_body),
    ])
    result = validate_section_balance(parsed, clf)
    assert result.findings == []


def test_section_balance_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_section_balance

    big_body = "We prove the theorem. " * 50
    parsed, clf = _balance_manuscript(
        [("Introduction", "Short intro."),
         ("Proof", big_body),
         ("Discussion", "Brief.")],
        paper_type="math_stats_theory",
    )
    result = validate_section_balance(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 81 – Related work recency
# ---------------------------------------------------------------------------


def _related_work_manuscript(
    rw_body: str,
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="rw-test",
        source_path="rw.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Related Work", level=1, body=rw_body)],
        full_text=rw_body,
    )
    clf = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, clf


def test_related_work_stale_fires() -> None:
    from manuscript_audit.validators.core import validate_related_work_recency

    # 7 old citations (2005-2012) and 1 recent — >50% stale
    body = (
        "Smith 2005 proposed the first method. Jones 2007 extended it. "
        "Brown 2009 introduced a variant. Davis 2010 showed limitations. "
        "Wilson 2011 proposed improvements. Taylor 2012 benchmarked results. "
        "Anderson 2013 reviewed the field. Chen 2023 provided recent work."
    )
    parsed, clf = _related_work_manuscript(body)
    result = validate_related_work_recency(parsed, clf)
    codes = [f.code for f in result.findings]
    assert "related-work-stale" in codes


def test_related_work_recent_no_fire() -> None:
    from manuscript_audit.validators.core import validate_related_work_recency

    body = (
        "Smith 2019 proposed the method. Jones 2020 extended it. "
        "Brown 2021 introduced improvements. Davis 2022 benchmarked. "
        "Wilson 2023 reviewed the field. Taylor 2023 showed recent results."
    )
    parsed, clf = _related_work_manuscript(body)
    result = validate_related_work_recency(parsed, clf)
    assert result.findings == []


def test_related_work_skipped_theory() -> None:
    from manuscript_audit.validators.core import validate_related_work_recency

    body = "Smith 2000. Jones 2001. Brown 2002. Davis 2003. Wilson 2004. Taylor 2005."
    parsed, clf = _related_work_manuscript(body, paper_type="math_stats_theory")
    result = validate_related_work_recency(parsed, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 82 – Introduction length balance
# ---------------------------------------------------------------------------


def _intro_length_manuscript(
    sections: list[tuple[str, str]],
) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    full = " ".join(b for _, b in sections)
    return ParsedManuscript(
        manuscript_id="introbal-test",
        source_path="ib.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full,
    )


def test_introduction_too_long_fires() -> None:
    from manuscript_audit.validators.core import validate_introduction_length

    long_intro = "This paper introduces a new framework for analysis. " * 20
    short_sec = "Brief section content about the methodology used. " * 10
    parsed = _intro_length_manuscript([
        ("Introduction", long_intro),
        ("Methods", short_sec),
        ("Results", short_sec),
        ("Discussion", short_sec),
    ])
    result = validate_introduction_length(parsed)
    codes = [f.code for f in result.findings]
    assert "introduction-too-long" in codes


def test_introduction_balanced_no_fire() -> None:
    from manuscript_audit.validators.core import validate_introduction_length

    balanced = "This section contains a balanced amount of content. " * 5
    parsed = _intro_length_manuscript([
        ("Introduction", balanced),
        ("Methods", balanced),
        ("Results", balanced),
        ("Discussion", balanced),
    ])
    result = validate_introduction_length(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 83 – Unquantified comparative claims
# ---------------------------------------------------------------------------


def _unquantified_manuscript(body_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="unq-test",
        source_path="unq.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=body_text)],
        full_text=body_text,
    )


def test_unquantified_comparison_fires() -> None:
    from manuscript_audit.validators.core import validate_unquantified_comparisons

    body = (
        "The proposed method is significantly better than the baseline. "
        "Training is much faster without any loss of accuracy. "
        "The approach is considerably higher performing on all tasks."
    )
    parsed = _unquantified_manuscript(body)
    result = validate_unquantified_comparisons(parsed)
    codes = [f.code for f in result.findings]
    assert "unquantified-comparison" in codes


def test_quantified_comparison_no_fire() -> None:
    from manuscript_audit.validators.core import validate_unquantified_comparisons

    body = (
        "The proposed method is significantly better than the baseline "
        "by 3.2 percentage points. "
        "Training is much faster at 4.5× speedup."
    )
    parsed = _unquantified_manuscript(body)
    result = validate_unquantified_comparisons(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 84 – Footnote overuse
# ---------------------------------------------------------------------------


def _footnote_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="fn-test",
        source_path="fn.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_footnote_overuse_fires() -> None:
    from manuscript_audit.validators.core import validate_footnote_overuse

    # 9 markdown-style footnote definitions
    text = "\n".join(f"[^{i}]: Footnote {i} explains extra detail." for i in range(1, 10))
    parsed = _footnote_manuscript(text)
    result = validate_footnote_overuse(parsed)
    codes = [f.code for f in result.findings]
    assert "footnote-heavy" in codes


def test_few_footnotes_no_fire() -> None:
    from manuscript_audit.validators.core import validate_footnote_overuse

    text = "\n".join(f"[^{i}]: Footnote {i}." for i in range(1, 5))
    parsed = _footnote_manuscript(text)
    result = validate_footnote_overuse(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 85 – Abbreviation list consistency
# ---------------------------------------------------------------------------


def _abbrev_manuscript(
    abbrev_body: str,
    body_sections: list[tuple[str, str]],
) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    all_sections = [Section(title="Abbreviations", level=1, body=abbrev_body)]
    all_sections += [Section(title=t, level=1, body=b) for t, b in body_sections]
    full = abbrev_body + " " + " ".join(b for _, b in body_sections)
    return ParsedManuscript(
        manuscript_id="abbrev-test",
        source_path="abbrev.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=all_sections,
        full_text=full,
    )


def test_unused_abbreviation_fires() -> None:
    from manuscript_audit.validators.core import validate_abbreviation_list

    abbrev_body = (
        "CNN: Convolutional Neural Network\nRNN: Recurrent Neural Network\nXYZ: Unused term\n"
    )
    parsed = _abbrev_manuscript(
        abbrev_body,
        [("Methods", "We used CNN and RNN for the experiments.")]
    )
    result = validate_abbreviation_list(parsed)
    codes = [f.code for f in result.findings]
    assert "unused-abbreviation" in codes
    # XYZ should be flagged
    abbrevs = [f.evidence[0] for f in result.findings]
    assert any("XYZ" in e for e in abbrevs)


def test_all_abbreviations_used_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abbreviation_list

    abbrev_body = "CNN: Convolutional Neural Network\nRNN: Recurrent Neural Network\n"
    parsed = _abbrev_manuscript(
        abbrev_body,
        [("Methods", "We used CNN and RNN throughout the experiments.")]
    )
    result = validate_abbreviation_list(parsed)
    assert result.findings == []


def test_no_abbreviation_section_no_fire() -> None:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.validators.core import validate_abbreviation_list

    ms = ParsedManuscript(
        manuscript_id="no-abbrev",
        source_path="na.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body="We used CNN and RNN.")],
        full_text="We used CNN and RNN.",
    )
    result = validate_abbreviation_list(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 86 – Abstract tense consistency
# ---------------------------------------------------------------------------


def _tense_manuscript(abstract: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="tense-test",
        source_path="tense.md",
        source_format="markdown",
        title="Test",
        abstract=abstract,
        full_text=abstract,
    )


def test_abstract_tense_mixed_fires() -> None:
    from manuscript_audit.validators.core import validate_abstract_tense

    abstract = (
        "We present a novel framework for text classification. "
        "The model was trained on 50000 examples. "
        "Results showed a 5% improvement over the baseline. "
        "This work demonstrates the effectiveness of transfer learning. "
        "Experiments were conducted on three standard benchmarks. "
        "The approach is competitive with state-of-the-art methods."
    )
    parsed = _tense_manuscript(abstract)
    result = validate_abstract_tense(parsed)
    codes = [f.code for f in result.findings]
    assert "abstract-tense-mixed" in codes


def test_abstract_past_tense_only_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_tense

    abstract = (
        "We trained a novel framework for text classification. "
        "The model was tested on 50000 examples. "
        "Results showed a 5% improvement over the baseline. "
        "Experiments were conducted on three benchmarks. "
        "Findings confirmed our hypothesis."
    )
    parsed = _tense_manuscript(abstract)
    result = validate_abstract_tense(parsed)
    assert result.findings == []


def test_abstract_too_short_for_tense_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_tense

    abstract = (
        "We present a framework. Results were collected. "
        "The approach is effective."
    )
    parsed = _tense_manuscript(abstract)
    result = validate_abstract_tense(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 87 – Claim strength escalation
# ---------------------------------------------------------------------------


def _claim_strength_manuscript(section_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="cs-test",
        source_path="cs.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Discussion", level=1, body=section_body)],
        full_text=section_body,
    )


def test_overstrong_claim_fires() -> None:
    from manuscript_audit.validators.core import validate_claim_strength_escalation

    body = (
        "The results prove that our approach is superior. "
        "This definitively shows the method works better than all alternatives. "
        "The evidence is conclusive and beyond any doubt."
    )
    parsed = _claim_strength_manuscript(body)
    result = validate_claim_strength_escalation(parsed)
    codes = [f.code for f in result.findings]
    assert "overstrong-claim" in codes


def test_normal_claim_language_no_fire() -> None:
    from manuscript_audit.validators.core import validate_claim_strength_escalation

    body = (
        "The results suggest that our approach performs better than the baseline. "
        "These findings indicate a statistically significant improvement. "
        "The evidence supports the hypothesis that the method is effective."
    )
    parsed = _claim_strength_manuscript(body)
    result = validate_claim_strength_escalation(parsed)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 88 – Sample size reporting
# ---------------------------------------------------------------------------


def _sample_size_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="ss-test",
        source_path="ss.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_missing_sample_size_fires() -> None:
    from manuscript_audit.validators.core import validate_sample_size_reporting

    body = (
        "We conducted a randomized controlled experiment on participants. "
        "Participants were assigned to one of two conditions. "
        "Data were collected over six weeks."
    )
    ms, clf = _sample_size_manuscript(body)
    result = validate_sample_size_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-sample-size" in codes


def test_explicit_sample_size_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sample_size_reporting

    body = (
        "We recruited N = 120 participants for the study. "
        "Participants were randomly assigned to treatment or control. "
        "Data were collected over six weeks."
    )
    ms, clf = _sample_size_manuscript(body)
    result = validate_sample_size_reporting(ms, clf)
    assert result.findings == []


def test_sample_size_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sample_size_reporting

    body = "We present a new algorithm for graph traversal."
    ms, clf = _sample_size_manuscript(body, paper_type="software_workflow_paper")
    result = validate_sample_size_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 89 – Limitations section presence
# ---------------------------------------------------------------------------


def _limitations_presence_manuscript(
    sections: list[tuple[str, str]],
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    full = " ".join(b for _, b in sections)
    ms = ParsedManuscript(
        manuscript_id="lim-test",
        source_path="lim.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_missing_limitations_section_fires() -> None:
    from manuscript_audit.validators.core import validate_limitations_section_presence

    ms, clf = _limitations_presence_manuscript([
        ("Methods", "We conducted an experiment with 50 subjects."),
        ("Results", "The results showed significant improvement."),
        ("Discussion", "The method is effective and generalizes well."),
    ])
    result = validate_limitations_section_presence(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-limitations-section" in codes


def test_dedicated_limitations_section_no_fire() -> None:
    from manuscript_audit.validators.core import validate_limitations_section_presence

    ms, clf = _limitations_presence_manuscript([
        ("Methods", "We conducted an experiment with 50 subjects."),
        ("Results", "The results showed significant improvement."),
        ("Discussion", "The method is effective."),
        ("Limitations", "The study is limited to English-language texts."),
    ])
    result = validate_limitations_section_presence(ms, clf)
    assert result.findings == []


def test_inline_limitations_discussion_no_fire() -> None:
    from manuscript_audit.validators.core import validate_limitations_section_presence

    ms, clf = _limitations_presence_manuscript([
        ("Methods", "We conducted an experiment."),
        ("Results", "Significant improvement was observed."),
        (
            "Discussion",
            "The study has several limitations including sample size constraints.",
        ),
    ])
    result = validate_limitations_section_presence(ms, clf)
    assert result.findings == []


def test_limitations_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_limitations_section_presence

    ms, clf = _limitations_presence_manuscript(
        [("Methods", "We implemented an algorithm.")],
        paper_type="software_workflow_paper",
    )
    result = validate_limitations_section_presence(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 90 – Author contribution statement
# ---------------------------------------------------------------------------


def _contrib_manuscript(full_text: str, sections: list[tuple[str, str]]) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="contrib-test",
        source_path="contrib.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title=t, level=1, body=b) for t, b in sections],
        full_text=full_text,
    )


def test_missing_author_contributions_fires() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    ms = _contrib_manuscript(
        "We present a new algorithm for classification.",
        [("Methods", "Algorithm was implemented."), ("Results", "Performance improved.")],
    )
    result = validate_author_contribution_statement(ms)
    codes = [f.code for f in result.findings]
    assert "missing-author-contributions" in codes


def test_credit_statement_no_fire() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    contrib_text = (
        "Author Contributions: J.S. contributed to conceptualization and methodology. "
        "K.L. was responsible for data curation and formal analysis. "
        "All authors reviewed the manuscript."
    )
    ms = _contrib_manuscript(
        contrib_text,
        [
            ("Methods", "Algorithm was implemented."),
            ("Author Contributions", contrib_text),
        ],
    )
    result = validate_author_contribution_statement(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 91 – Preregistration mention
# ---------------------------------------------------------------------------


def _prereg_manuscript(
    full_text: str,
    paper_type: str = "clinical_trial_report",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="prereg-test",
        source_path="prereg.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=full_text)],
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_rct_without_preregistration_fires() -> None:
    from manuscript_audit.validators.core import validate_preregistration_mention

    body = (
        "We conducted a randomized controlled trial where participants were randomly "
        "assigned to treatment or placebo groups in a double-blind design."
    )
    ms, clf = _prereg_manuscript(body)
    result = validate_preregistration_mention(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-preregistration" in codes


def test_rct_with_preregistration_no_fire() -> None:
    from manuscript_audit.validators.core import validate_preregistration_mention

    body = (
        "This randomized controlled trial was preregistered on ClinicalTrials.gov "
        "(registration number NCT12345678). Participants were randomly assigned."
    )
    ms, clf = _prereg_manuscript(body)
    result = validate_preregistration_mention(ms, clf)
    assert result.findings == []


def test_non_rct_no_fire() -> None:
    from manuscript_audit.validators.core import validate_preregistration_mention

    body = "We developed a machine learning algorithm for text classification."
    ms, clf = _prereg_manuscript(body, paper_type="software_workflow_paper")
    result = validate_preregistration_mention(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 92 – Reviewer response completeness
# ---------------------------------------------------------------------------


def _revision_manuscript(title: str, abstract: str, full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="rev-resp-test",
        source_path="rev_resp.md",
        source_format="markdown",
        title=title,
        abstract=abstract,
        full_text=full_text,
    )


def test_revision_without_reviewer_response_fires() -> None:
    from manuscript_audit.validators.core import validate_reviewer_response_completeness

    ms = _revision_manuscript(
        title="Revised manuscript: A study of...",
        abstract="This is a revised version of our manuscript.",
        full_text="This is a revised version. We updated the methods section.",
    )
    result = validate_reviewer_response_completeness(ms)
    codes = [f.code for f in result.findings]
    assert "missing-reviewer-response" in codes


def test_revision_with_reviewer_response_no_fire() -> None:
    from manuscript_audit.validators.core import validate_reviewer_response_completeness

    ms = _revision_manuscript(
        title="Revised manuscript: A study of...",
        abstract="This is a revised version of our manuscript.",
        full_text=(
            "This is a revised version. Response to reviewer 1: "
            "We thank the reviewer for the insightful comment. "
            "We have addressed the concern by expanding the methods section."
        ),
    )
    result = validate_reviewer_response_completeness(ms)
    assert result.findings == []


def test_non_revision_manuscript_no_fire() -> None:
    from manuscript_audit.validators.core import validate_reviewer_response_completeness

    ms = _revision_manuscript(
        title="A study of machine learning methods",
        abstract="We present a new approach to classification.",
        full_text="Methods were evaluated on standard benchmarks.",
    )
    result = validate_reviewer_response_completeness(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 93 – Novelty overclaiming
# ---------------------------------------------------------------------------


def _novelty_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="novelty-test",
        source_path="novelty.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_novelty_overclaim_fires() -> None:
    from manuscript_audit.validators.core import validate_novelty_overclaim

    text = (
        "This is the first ever method to achieve this result. "
        "The approach is unprecedented in the literature."
    )
    ms = _novelty_manuscript(text)
    result = validate_novelty_overclaim(ms)
    codes = [f.code for f in result.findings]
    assert "novelty-overclaim" in codes


def test_novelty_with_contrast_no_fire() -> None:
    from manuscript_audit.validators.core import validate_novelty_overclaim

    text = (
        "Unlike previous methods, our approach achieves state-of-the-art results. "
        "Compared to prior work, we improve accuracy by 5%. "
        "This is the first to demonstrate the approach at this scale."
    )
    ms = _novelty_manuscript(text)
    result = validate_novelty_overclaim(ms)
    assert result.findings == []


def test_no_novelty_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_novelty_overclaim

    text = (
        "We present a new method for classification. "
        "The method improves on baseline approaches by 5%."
    )
    ms = _novelty_manuscript(text)
    result = validate_novelty_overclaim(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 94 – Figure/table minimum
# ---------------------------------------------------------------------------


def _fig_table_manuscript(
    full_text: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="ft-test",
        source_path="ft.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_no_figures_tables_empirical_fires() -> None:
    from manuscript_audit.validators.core import validate_figure_table_minimum

    text = (
        "We conducted a study of 120 participants. "
        "Results showed significant improvement. "
        "The method outperforms the baseline."
    )
    ms, clf = _fig_table_manuscript(text)
    result = validate_figure_table_minimum(ms, clf)
    codes = [f.code for f in result.findings]
    assert "no-figures-or-tables" in codes


def test_figure_reference_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_table_minimum

    text = "See Figure 1 for the results. Table 1 summarizes the parameters."
    ms, clf = _fig_table_manuscript(text)
    result = validate_figure_table_minimum(ms, clf)
    assert result.findings == []


def test_non_empirical_no_fig_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_table_minimum

    text = "We present a new algorithm."
    ms, clf = _fig_table_manuscript(text, paper_type="software_workflow_paper")
    result = validate_figure_table_minimum(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 95 – Multiple comparisons correction
# ---------------------------------------------------------------------------


def _multi_test_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="mt-test",
        source_path="mt.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_multiple_tests_without_correction_fires() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparisons_correction

    body = (
        "We performed multiple comparisons across all outcome measures. "
        "Several statistical tests were conducted for each primary endpoint."
    )
    ms, clf = _multi_test_manuscript(body)
    result = validate_multiple_comparisons_correction(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-multiple-comparisons-correction" in codes


def test_multiple_tests_with_bonferroni_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparisons_correction

    body = (
        "We performed multiple comparisons with Bonferroni correction "
        "to control the family-wise error rate."
    )
    ms, clf = _multi_test_manuscript(body)
    result = validate_multiple_comparisons_correction(ms, clf)
    assert result.findings == []


def test_single_test_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparisons_correction

    body = "We performed a paired t-test to compare the two groups."
    ms, clf = _multi_test_manuscript(body)
    result = validate_multiple_comparisons_correction(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 96 – Supplementary material indication
# ---------------------------------------------------------------------------


def _suppl_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="suppl-test",
        source_path="suppl.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_suppl_reference_without_availability_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_supplementary_material_indication,
    )

    text = (
        "See supplementary data for additional results. "
        "Supplementary figures show the complete analysis."
    )
    ms = _suppl_manuscript(text)
    result = validate_supplementary_material_indication(ms)
    codes = [f.code for f in result.findings]
    assert "unindicated-supplementary-material" in codes


def test_suppl_with_availability_statement_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_supplementary_material_indication,
    )

    text = (
        "Supplementary material is available online at the journal website. "
        "See online supplementary figures for additional analysis."
    )
    ms = _suppl_manuscript(text)
    result = validate_supplementary_material_indication(ms)
    assert result.findings == []


def test_no_suppl_reference_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_supplementary_material_indication,
    )

    text = "We present the full analysis in the main text."
    ms = _suppl_manuscript(text)
    result = validate_supplementary_material_indication(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 97 – Conclusion scope creep
# ---------------------------------------------------------------------------


def _conclusion_manuscript(conclusion_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="conc-test",
        source_path="conc.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Conclusions", level=1, body=conclusion_body)],
        full_text=conclusion_body,
    )


def test_conclusion_scope_creep_fires() -> None:
    from manuscript_audit.validators.core import validate_conclusion_scope_creep

    body = (
        "In conclusion, the method performs well on all benchmarks. "
        "Furthermore, we also show that the approach generalizes to new domains "
        "not previously examined. Additionally, we find that the training time "
        "is significantly reduced compared to baseline methods. Moreover, future "
        "directions include applying this method to additional tasks."
    )
    ms = _conclusion_manuscript(body)
    result = validate_conclusion_scope_creep(ms)
    codes = [f.code for f in result.findings]
    assert "conclusion-scope-creep" in codes


def test_summary_conclusion_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conclusion_scope_creep

    body = (
        "In summary, we presented a new method for text classification. "
        "The approach achieves state-of-the-art results on standard benchmarks. "
        "The main contributions include a novel architecture and training procedure."
    )
    ms = _conclusion_manuscript(body)
    result = validate_conclusion_scope_creep(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 98 – Discussion-Results alignment
# ---------------------------------------------------------------------------


def _discussion_manuscript(discussion_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="disc-test",
        source_path="disc.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Discussion", level=1, body=discussion_body)],
        full_text=discussion_body,
    )


def test_discussion_without_results_reference_fires() -> None:
    from manuscript_audit.validators.core import validate_discussion_results_alignment

    body = (
        "The method is interesting and could be applied to many domains. "
        "Future directions include extending the approach to new modalities. "
        "The technique has broad implications for the field of machine learning "
        "and could influence how researchers approach similar problems in the future. "
        "The computational efficiency is noteworthy and worth examining further. "
        "These properties make it an attractive candidate for large-scale deployment."
    )
    ms = _discussion_manuscript(body)
    result = validate_discussion_results_alignment(ms)
    codes = [f.code for f in result.findings]
    assert "discussion-lacks-results-reference" in codes


def test_discussion_with_results_reference_no_fire() -> None:
    from manuscript_audit.validators.core import validate_discussion_results_alignment

    body = (
        "These results demonstrate that the method is effective. "
        "Our findings suggest that the approach generalizes well. "
        "The data suggest that performance improvements are consistent across domains. "
        "We interpret the accuracy gains as evidence of better feature representation."
    )
    ms = _discussion_manuscript(body)
    result = validate_discussion_results_alignment(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 99 – Open data statement
# ---------------------------------------------------------------------------


def _open_data_manuscript(
    full_text: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="od-test",
        source_path="od.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_missing_open_data_statement_fires() -> None:
    from manuscript_audit.validators.core import validate_open_data_statement

    text = (
        "We conducted a randomized controlled experiment with 120 participants. "
        "All procedures were approved by the ethics board."
    )
    ms, clf = _open_data_manuscript(text)
    result = validate_open_data_statement(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-open-data-statement" in codes


def test_data_availability_statement_no_fire() -> None:
    from manuscript_audit.validators.core import validate_open_data_statement

    text = (
        "Data availability statement: The data are available at zenodo.org. "
        "All code is available on github.com/example/repo."
    )
    ms, clf = _open_data_manuscript(text)
    result = validate_open_data_statement(ms, clf)
    assert result.findings == []


def test_non_empirical_no_open_data_no_fire() -> None:
    from manuscript_audit.validators.core import validate_open_data_statement

    text = "We present a new algorithm for graph traversal."
    ms, clf = _open_data_manuscript(text, paper_type="software_workflow_paper")
    result = validate_open_data_statement(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 100 – Redundant phrases
# ---------------------------------------------------------------------------


def _redundant_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="red-test",
        source_path="red.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_redundant_phrases_fires() -> None:
    from manuscript_audit.validators.core import validate_redundant_phrases

    text = (
        "Due to the fact that the results were significant, in order to "
        "confirm our hypothesis, it is important to note that the approach "
        "succeeded. Furthermore, with regard to the limitations, it should "
        "be noted that the sample was small."
    )
    ms = _redundant_manuscript(text)
    result = validate_redundant_phrases(ms)
    codes = [f.code for f in result.findings]
    assert "redundant-phrases" in codes


def test_concise_text_no_fire() -> None:
    from manuscript_audit.validators.core import validate_redundant_phrases

    text = (
        "Results were significant. To confirm our hypothesis we ran additional tests. "
        "Note that the sample was small."
    )
    ms = _redundant_manuscript(text)
    result = validate_redundant_phrases(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 101 – Abstract quantitative results
# ---------------------------------------------------------------------------


def _abstract_quant_manuscript(
    abstract: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="aq-test",
        source_path="aq.md",
        source_format="markdown",
        title="Test",
        abstract=abstract,
        full_text=abstract,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_abstract_no_quantitative_result_fires() -> None:
    from manuscript_audit.validators.core import validate_abstract_quantitative_results

    abstract = (
        "This paper presents a novel deep learning approach for image classification. "
        "We evaluate the method on standard benchmarks and demonstrate improved "
        "performance compared to prior methods. The approach is scalable and achieves "
        "state-of-the-art results while maintaining computational efficiency. "
        "These findings have important implications for practical deployment "
        "in real-world systems across multiple domains and application scenarios."
    )
    ms, clf = _abstract_quant_manuscript(abstract)
    result = validate_abstract_quantitative_results(ms, clf)
    codes = [f.code for f in result.findings]
    assert "abstract-no-quantitative-result" in codes


def test_abstract_with_quantitative_result_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_quantitative_results

    abstract = (
        "This paper presents a deep learning approach for image classification. "
        "We evaluate the method on ImageNet and achieve 92.3% top-1 accuracy, "
        "representing a 3.5% improvement over the prior state-of-the-art. "
        "The approach achieves these results with 40% lower computational cost "
        "compared to comparable architectures with similar capacity."
    )
    ms, clf = _abstract_quant_manuscript(abstract)
    result = validate_abstract_quantitative_results(ms, clf)
    assert result.findings == []


def test_non_empirical_abstract_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_quantitative_results

    abstract = (
        "We present a new open-source workflow tool for reproducible manuscript "
        "vetting. The tool provides deterministic validators and structured "
        "artifact outputs for academic manuscripts."
    )
    ms, clf = _abstract_quant_manuscript(
        abstract, paper_type="software_workflow_paper"
    )
    result = validate_abstract_quantitative_results(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 102 – Missing confidence intervals
# ---------------------------------------------------------------------------


def _ci_manuscript(
    results_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="ci-test",
        source_path="ci.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=results_body)],
        full_text=results_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_effect_size_without_ci_fires() -> None:
    from manuscript_audit.validators.core import validate_confidence_interval_reporting

    body = (
        "The intervention showed a significant effect (Cohen's d = 0.72, p < 0.001). "
        "The odds ratio was 2.4, indicating elevated risk in the treatment group."
    )
    ms, clf = _ci_manuscript(body)
    result = validate_confidence_interval_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-confidence-intervals" in codes


def test_effect_size_with_ci_no_fire() -> None:
    from manuscript_audit.validators.core import validate_confidence_interval_reporting

    body = (
        "The effect was significant (Cohen's d = 0.72, 95% CI [0.45, 0.99]). "
        "The odds ratio was 2.4 (CI: 1.8, 3.2)."
    )
    ms, clf = _ci_manuscript(body)
    result = validate_confidence_interval_reporting(ms, clf)
    assert result.findings == []


def test_non_empirical_no_ci_no_fire() -> None:
    from manuscript_audit.validators.core import validate_confidence_interval_reporting

    body = "The algorithm runs in O(n log n) time."
    ms, clf = _ci_manuscript(body, paper_type="software_workflow_paper")
    result = validate_confidence_interval_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 103 – Bayesian prior justification
# ---------------------------------------------------------------------------


def _bayesian_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="bayes-test",
        source_path="bayes.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_bayesian_without_prior_fires() -> None:
    from manuscript_audit.validators.core import validate_bayesian_prior_justification

    body = (
        "We used a Bayesian hierarchical model implemented in Stan. "
        "Posterior distributions were estimated using MCMC sampling with NUTS. "
        "Credible intervals were computed from 4000 posterior samples."
    )
    ms, clf = _bayesian_manuscript(body)
    result = validate_bayesian_prior_justification(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-prior-justification" in codes


def test_bayesian_with_prior_justification_no_fire() -> None:
    from manuscript_audit.validators.core import validate_bayesian_prior_justification

    body = (
        "We used a Bayesian hierarchical model. Weakly informative priors "
        "were specified following Gelman et al. (2017). "
        "The normal prior (mu=0, sigma=2.5) was chosen based on prior sensitivity analysis."
    )
    ms, clf = _bayesian_manuscript(body)
    result = validate_bayesian_prior_justification(ms, clf)
    assert result.findings == []


def test_non_bayesian_no_fire() -> None:
    from manuscript_audit.validators.core import validate_bayesian_prior_justification

    body = "We conducted a paired t-test comparing the two groups."
    ms, clf = _bayesian_manuscript(body)
    result = validate_bayesian_prior_justification(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 104 – Software version pinning
# ---------------------------------------------------------------------------


def _software_version_manuscript(
    methods_body: str,
    paper_type: str = "software_workflow_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="sv-test",
        source_path="sv.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_software_without_version_fires() -> None:
    from manuscript_audit.validators.core import validate_software_version_pinning

    body = (
        "Analysis was performed using Python with numpy and pandas. "
        "Machine learning models were implemented in scikit-learn."
    )
    ms, clf = _software_version_manuscript(body)
    result = validate_software_version_pinning(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-software-versions" in codes


def test_software_with_version_no_fire() -> None:
    from manuscript_audit.validators.core import validate_software_version_pinning

    body = (
        "Analysis was performed using Python 3.11.2 with numpy (version 1.24.3) "
        "and pandas (version 2.0.1). "
        "Models were trained using scikit-learn version 1.2.2."
    )
    ms, clf = _software_version_manuscript(body)
    result = validate_software_version_pinning(ms, clf)
    assert result.findings == []


def test_no_software_mentioned_no_fire() -> None:
    from manuscript_audit.validators.core import validate_software_version_pinning

    body = "We conducted interviews with 30 participants using a semi-structured protocol."
    ms, clf = _software_version_manuscript(body, paper_type="empirical_paper")
    result = validate_software_version_pinning(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 105 – Measurement scale reporting
# ---------------------------------------------------------------------------


def _scale_manuscript(
    methods_body: str,
    paper_type: str = "survey_study",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="scale-test",
        source_path="scale.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_scale_without_reliability_fires() -> None:
    from manuscript_audit.validators.core import validate_measurement_scale_reporting

    body = (
        "Participants completed a 20-item Likert scale measuring burnout. "
        "The questionnaire was adapted from the Maslach Burnout Inventory. "
        "Items were rated on a 7-point scale from 1 (strongly disagree) to 7 (strongly agree)."
    )
    ms, clf = _scale_manuscript(body)
    result = validate_measurement_scale_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-scale-reliability" in codes


def test_scale_with_cronbach_no_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_scale_reporting

    body = (
        "Participants completed a 20-item Likert scale. "
        "Internal consistency was excellent (Cronbach alpha = 0.91). "
        "Reliability confirmed adequate validity."
    )
    ms, clf = _scale_manuscript(body)
    result = validate_measurement_scale_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 106 – SEM fit indices
# ---------------------------------------------------------------------------


def _sem_manuscript(
    results_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="sem-test",
        source_path="sem.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=results_body)],
        full_text=results_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_sem_without_fit_indices_fires() -> None:
    from manuscript_audit.validators.core import validate_sem_fit_indices

    body = (
        "We specified a structural equation model with three latent variables. "
        "The CFA model showed acceptable fit with significant factor loadings. "
        "All paths were statistically significant at p < 0.05."
    )
    ms, clf = _sem_manuscript(body)
    result = validate_sem_fit_indices(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-sem-fit-indices" in codes


def test_sem_with_fit_indices_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sem_fit_indices

    body = (
        "The CFA model showed excellent fit: CFI = 0.97, TLI = 0.96, "
        "RMSEA = 0.042 (90% CI: 0.031, 0.053), SRMR = 0.048."
    )
    ms, clf = _sem_manuscript(body)
    result = validate_sem_fit_indices(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 107 – Regression variance explanation
# ---------------------------------------------------------------------------


def _regression_manuscript(
    results_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="reg-test",
        source_path="reg.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=results_body)],
        full_text=results_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_regression_without_r_squared_fires() -> None:
    from manuscript_audit.validators.core import validate_regression_variance_explanation

    body = (
        "Multiple linear regression showed that age significantly predicted "
        "burnout (beta = 0.23, p < 0.001). Gender was also a significant "
        "predictor (beta = -0.18, p = 0.004)."
    )
    ms, clf = _regression_manuscript(body)
    result = validate_regression_variance_explanation(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-variance-explained" in codes


def test_regression_with_r_squared_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_variance_explanation

    body = (
        "Multiple linear regression explained 34% of the variance in burnout "
        "(R-squared = 0.34, F(3, 116) = 19.8, p < 0.001). "
        "Age was a significant predictor (beta = 0.23, p < 0.001)."
    )
    ms, clf = _regression_manuscript(body)
    result = validate_regression_variance_explanation(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 108 – Normality assumption check
# ---------------------------------------------------------------------------


def _normality_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="norm-test",
        source_path="norm.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_parametric_without_normality_fires() -> None:
    from manuscript_audit.validators.core import validate_normality_assumption

    body = (
        "Group differences were analyzed using independent samples t-test. "
        "Post-hoc comparisons used one-way ANOVA with Bonferroni correction."
    )
    ms, clf = _normality_manuscript(body)
    result = validate_normality_assumption(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-normality-check" in codes


def test_parametric_with_shapiro_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normality_assumption

    body = (
        "Normality was confirmed using the Shapiro-Wilk test for all variables. "
        "Group differences were analyzed using independent samples t-test."
    )
    ms, clf = _normality_manuscript(body)
    result = validate_normality_assumption(ms, clf)
    assert result.findings == []


def test_nonparametric_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normality_assumption

    body = (
        "We used the Wilcoxon signed-rank test as a nonparametric alternative. "
        "Mann-Whitney U tests compared the distributions."
    )
    ms, clf = _normality_manuscript(body)
    result = validate_normality_assumption(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 109 – Attrition reporting
# ---------------------------------------------------------------------------


def _attrition_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="attr-test",
        source_path="attr.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_longitudinal_without_attrition_fires() -> None:
    from manuscript_audit.validators.core import validate_attrition_reporting

    body = (
        "Participants completed assessments at baseline and 6-month follow-up. "
        "The longitudinal design allowed tracking of changes over time."
    )
    ms, clf = _attrition_manuscript(body)
    result = validate_attrition_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-attrition-report" in codes


def test_longitudinal_with_attrition_no_fire() -> None:
    from manuscript_audit.validators.core import validate_attrition_reporting

    body = (
        "Participants completed assessments at baseline and 6-month follow-up. "
        "Attrition was 12%: 15 participants dropped out due to time constraints. "
        "Missing data were handled using multiple imputation."
    )
    ms, clf = _attrition_manuscript(body)
    result = validate_attrition_reporting(ms, clf)
    assert result.findings == []


def test_cross_sectional_no_fire() -> None:
    from manuscript_audit.validators.core import validate_attrition_reporting

    body = "Participants completed a one-time survey about their work habits."
    ms, clf = _attrition_manuscript(body)
    result = validate_attrition_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 110 – Generalizability overclaim
# ---------------------------------------------------------------------------


def _generalize_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="gen-test",
        source_path="gen.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_generalizability_overclaim_fires() -> None:
    from manuscript_audit.validators.core import validate_generalizability_overclaim

    text = (
        "The method generalizes to all clinical populations and "
        "is universally applicable across all contexts and settings."
    )
    ms = _generalize_manuscript(text)
    result = validate_generalizability_overclaim(ms)
    codes = [f.code for f in result.findings]
    assert "generalizability-overclaim" in codes


def test_hedged_generalizability_no_fire() -> None:
    from manuscript_audit.validators.core import validate_generalizability_overclaim

    text = (
        "The method generalizes to all clinical populations. "
        "However, further research is needed to confirm external validity. "
        "The study is limited by our specific sample characteristics."
    )
    ms = _generalize_manuscript(text)
    result = validate_generalizability_overclaim(ms)
    assert result.findings == []


def test_no_generalizability_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_generalizability_overclaim

    text = "The method improved accuracy on the held-out test set."
    ms = _generalize_manuscript(text)
    result = validate_generalizability_overclaim(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 111 – Interrater reliability
# ---------------------------------------------------------------------------


def _irr_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="irr-test",
        source_path="irr.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_coding_without_irr_fires() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    body = (
        "Two independent raters coded all transcripts for themes. "
        "Disagreements were resolved by discussion until consensus was reached."
    )
    ms, clf = _irr_manuscript(body)
    result = validate_interrater_reliability(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-interrater-reliability" in codes


def test_coding_with_kappa_no_fire() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    body = (
        "Two independent coders rated all transcripts. "
        "Inter-rater reliability was acceptable (Cohen's kappa = 0.82). "
        "Disagreements were resolved through discussion."
    )
    ms, clf = _irr_manuscript(body)
    result = validate_interrater_reliability(ms, clf)
    assert result.findings == []


def test_no_coding_no_fire() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    body = "We analyzed questionnaire responses using structural equation modeling."
    ms, clf = _irr_manuscript(body)
    result = validate_interrater_reliability(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 112 – Spurious precision
# ---------------------------------------------------------------------------


def _spurious_precision_manuscript(results_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="sp-test",
        source_path="sp.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Results", level=1, body=results_body)],
        full_text=results_body,
    )


def test_spurious_precision_fires() -> None:
    from manuscript_audit.validators.core import validate_spurious_precision

    body = (
        "The mean accuracy was 0.91234567 and standard error was 0.00123456. "
        "Effect size Cohen's d = 0.72345678."
    )
    ms = _spurious_precision_manuscript(body)
    result = validate_spurious_precision(ms)
    codes = [f.code for f in result.findings]
    assert "spurious-precision" in codes


def test_reasonable_precision_no_fire() -> None:
    from manuscript_audit.validators.core import validate_spurious_precision

    body = "The mean accuracy was 0.912 and the standard error was 0.023."
    ms = _spurious_precision_manuscript(body)
    result = validate_spurious_precision(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 113 – Vague temporal claims
# ---------------------------------------------------------------------------


def _temporal_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="temp-test",
        source_path="temp.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_vague_temporal_fires() -> None:
    from manuscript_audit.validators.core import validate_vague_temporal_claims

    text = (
        "Recently, deep learning has shown impressive results. "
        "In recent years, the field has grown rapidly. "
        "Lately, researchers have focused on interpretability. "
        "In recent months there have been many new advances."
    )
    ms = _temporal_manuscript(text)
    result = validate_vague_temporal_claims(ms)
    codes = [f.code for f in result.findings]
    assert "vague-temporal-claims" in codes


def test_anchored_temporal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_vague_temporal_claims

    text = (
        "Recently, deep learning has shown impressive results. "
        "In recent years the field has grown, especially since 2017. "
        "Between 2018 and 2023, multiple large models were released. "
        "In recent months new benchmarks have been proposed."
    )
    ms = _temporal_manuscript(text)
    result = validate_vague_temporal_claims(ms)
    assert result.findings == []


def test_few_temporal_references_no_fire() -> None:
    from manuscript_audit.validators.core import validate_vague_temporal_claims

    text = (
        "Recently, the field has advanced. "
        "The approach is now state-of-the-art."
    )
    ms = _temporal_manuscript(text)
    result = validate_vague_temporal_claims(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 114 – Exclusion criteria
# ---------------------------------------------------------------------------


def _exclusion_manuscript(
    methods_body: str,
    paper_type: str = "empirical_paper",
) -> tuple[object, object]:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="excl-test",
        source_path="excl.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        sections=[Section(title="Methods", level=1, body=methods_body)],
        full_text=methods_body,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_inclusion_without_exclusion_fires() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria

    body = (
        "Inclusion criteria: participants must be aged 18-65 and English-speaking. "
        "Eligible participants were recruited from community centers."
    )
    ms, clf = _exclusion_manuscript(body)
    result = validate_exclusion_criteria(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-exclusion-criteria" in codes


def test_inclusion_and_exclusion_no_fire() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria

    body = (
        "Inclusion criteria: adults aged 18-65. "
        "Exclusion criteria: participants with prior neurological diagnosis "
        "were excluded from the study."
    )
    ms, clf = _exclusion_manuscript(body)
    result = validate_exclusion_criteria(ms, clf)
    assert result.findings == []


def test_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria

    body = "We implemented a new algorithm for graph traversal."
    ms, clf = _exclusion_manuscript(body, paper_type="software_workflow_paper")
    result = validate_exclusion_criteria(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 115 – Title length
# ---------------------------------------------------------------------------


def _title_manuscript(title: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="title-test",
        source_path="title.md",
        source_format="markdown",
        title=title,
        abstract="Abstract.",
        full_text="Content.",
    )


def test_title_too_long_fires() -> None:
    from manuscript_audit.validators.core import validate_title_length

    title = (
        "A comprehensive investigation into the long-term effects of "
        "machine learning on organizational decision-making processes in "
        "large multinational corporations across diverse industry sectors"
    )
    ms = _title_manuscript(title)
    result = validate_title_length(ms)
    codes = [f.code for f in result.findings]
    assert "title-too-long" in codes


def test_title_too_short_fires() -> None:
    from manuscript_audit.validators.core import validate_title_length

    ms = _title_manuscript("AI methods")
    result = validate_title_length(ms)
    codes = [f.code for f in result.findings]
    assert "title-too-short" in codes


def test_normal_title_no_fire() -> None:
    from manuscript_audit.validators.core import validate_title_length

    ms = _title_manuscript(
        "Deterministic validators for adversarial manuscript vetting"
    )
    result = validate_title_length(ms)
    assert result.findings == []



# ---------------------------------------------------------------------------
# Phase 116 – validate_statistical_power
# ---------------------------------------------------------------------------


def _power_manuscript(methods_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="power-test",
        source_path="power.md",
        source_format="markdown",
        title="Power Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )


def test_statistical_power_fires_on_empirical_without_power() -> None:
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_statistical_power

    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    ms = _power_manuscript(
        "We recruited 50 participants. Data were analyzed using t-tests. "
        "Significance threshold was set at alpha = 0.05. All analyses in R 4.3."
    )
    result = validate_statistical_power(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-power-analysis" in codes


def test_statistical_power_passes_with_power_analysis() -> None:
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_statistical_power

    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    ms = _power_manuscript(
        "We conducted a power analysis using G*Power with power = 0.80 "
        "and alpha = 0.05. The required sample size was determined to be 64."
    )
    result = validate_statistical_power(ms, clf)
    assert result.findings == []


def test_statistical_power_skips_non_empirical() -> None:
    from manuscript_audit.schemas.routing import ManuscriptClassification
    from manuscript_audit.validators.core import validate_statistical_power

    clf = ManuscriptClassification(
        paper_type="literature_review",
        pathway="unknown",
        recommended_stack="standard",
    )
    ms = _power_manuscript("We searched PubMed and Scopus for relevant studies.")
    result = validate_statistical_power(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 117 – validate_keywords_present
# ---------------------------------------------------------------------------


def _keywords_manuscript(
    abstract: str = "Abstract.",
    section_titles: list | None = None,
) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    sections = [
        Section(title=t, level=1, body="body text here.")
        for t in (section_titles or ["Introduction", "Methods", "Results"])
    ]
    return ParsedManuscript(
        manuscript_id="kw-test",
        source_path="kw.md",
        source_format="markdown",
        title="Test Study",
        abstract=abstract,
        full_text="",
        sections=sections,
    )


def test_keywords_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_keywords_present

    ms = _keywords_manuscript(
        abstract="This is an abstract with no keywords.",
        section_titles=["Introduction", "Methods", "Results"],
    )
    result = validate_keywords_present(ms)
    codes = [f.code for f in result.findings]
    assert "missing-keywords" in codes


def test_keywords_passes_with_section() -> None:
    from manuscript_audit.validators.core import validate_keywords_present

    ms = _keywords_manuscript(
        section_titles=["Introduction", "Methods", "Results", "Keywords"],
    )
    result = validate_keywords_present(ms)
    assert result.findings == []


def test_keywords_passes_with_inline_abstract() -> None:
    from manuscript_audit.validators.core import validate_keywords_present

    ms = _keywords_manuscript(
        abstract="This study examined X. Keywords: machine learning; statistics"
    )
    result = validate_keywords_present(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 118 – validate_overlong_sentences
# ---------------------------------------------------------------------------


def _overlong_manuscript(section_title: str, body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="long-test",
        source_path="long.md",
        source_format="markdown",
        title="Overlong Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title=section_title, level=1, body=body)],
    )


def test_overlong_sentence_fires_in_results() -> None:
    from manuscript_audit.validators.core import validate_overlong_sentences

    long_sent = " ".join(["word"] * 65)
    ms = _overlong_manuscript("Results", f"Short sentence. {long_sent}.")
    result = validate_overlong_sentences(ms)
    codes = [f.code for f in result.findings]
    assert "overlong-sentence" in codes


def test_overlong_sentence_no_fire_in_introduction() -> None:
    from manuscript_audit.validators.core import validate_overlong_sentences

    long_sent = " ".join(["word"] * 65)
    ms = _overlong_manuscript("Introduction", f"{long_sent}.")
    result = validate_overlong_sentences(ms)
    assert result.findings == []


def test_short_sentences_no_fire() -> None:
    from manuscript_audit.validators.core import validate_overlong_sentences

    ms = _overlong_manuscript(
        "Results", "We found significant effects. The p-value was 0.01."
    )
    result = validate_overlong_sentences(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 119 – validate_heading_capitalization_consistency
# ---------------------------------------------------------------------------


def _heading_manuscript(titles: list) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="head-test",
        source_path="head.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title=t, level=1, body="body text") for t in titles],
    )


def test_mixed_heading_case_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_heading_capitalization_consistency,
    )

    # Two title-case headings + two sentence-case headings
    ms = _heading_manuscript([
        "Introduction Background Context",
        "Methods And Participants Study",
        "Results from findings analysis here",
        "Discussion implications limitations here",
    ])
    result = validate_heading_capitalization_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "inconsistent-heading-capitalization" in codes


def test_consistent_title_case_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_heading_capitalization_consistency,
    )

    ms = _heading_manuscript([
        "Introduction Background Study",
        "Methods And Participants Study",
        "Results And Analysis Study",
        "Discussion And Conclusions Study",
    ])
    result = validate_heading_capitalization_consistency(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 120 – validate_research_question_addressed
# ---------------------------------------------------------------------------


def _rq_manuscript(intro_body: str, results_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="rq-test",
        source_path="rq.md",
        source_format="markdown",
        title="RQ Test",
        abstract="Abstract.",
        full_text="",
        sections=[
            Section(title="Introduction", level=1, body=intro_body),
            Section(title="Results", level=1, body=results_body),
        ],
    )


def test_unanswered_rq_fires() -> None:
    from manuscript_audit.validators.core import validate_research_question_addressed

    ms = _rq_manuscript(
        intro_body=(
            "The central question is whether treatment A improves outcomes. "
            "RQ1: Does intervention X change behavior?"
        ),
        results_body="Table 1 shows descriptive statistics for all groups.",
    )
    result = validate_research_question_addressed(ms)
    codes = [f.code for f in result.findings]
    assert "unanswered-research-question" in codes


def test_answered_rq_no_fire() -> None:
    from manuscript_audit.validators.core import validate_research_question_addressed

    ms = _rq_manuscript(
        intro_body=(
            "The central question is whether treatment A improves outcomes."
        ),
        results_body=(
            "We found that participants in the treatment group showed "
            "significantly better outcomes (p < 0.05). "
            "Our results indicate a positive effect of treatment A."
        ),
    )
    result = validate_research_question_addressed(ms)
    assert result.findings == []


def test_rq_no_fire_when_no_research_question() -> None:
    from manuscript_audit.validators.core import validate_research_question_addressed

    ms = _rq_manuscript(
        intro_body="This paper presents a new method for image classification.",
        results_body="Table 1 shows the accuracy metrics across all benchmarks.",
    )
    result = validate_research_question_addressed(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 121 – validate_conflict_of_interest
# ---------------------------------------------------------------------------


def _coi_manuscript_simple(body: str, paper_type: str = "empirical_paper") -> tuple:
    from manuscript_audit.schemas.artifacts import (
        BibliographyEntry,
        ParsedManuscript,
        Section,
    )
    from manuscript_audit.schemas.routing import ManuscriptClassification

    # Need >= 5 bibliography entries to trigger the existing COI validator
    bib = [
        BibliographyEntry(
            key=f"ref{i}",
            raw_text=f"Author {i} (202{i}). Title.",
            source="markdown_reference_list",
        )
        for i in range(6)
    ]
    ms = ParsedManuscript(
        manuscript_id="coi-test",
        source_path="coi.md",
        source_format="markdown",
        title="Test Study",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=body)],
        bibliography_entries=bib,
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_coi_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    ms, clf = _coi_manuscript_simple("We used regression models. Sample size was 200.")
    result = validate_conflict_of_interest(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-coi-statement" in codes


def test_coi_passes_with_declaration() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    ms, clf = _coi_manuscript_simple(
        "Methods text. The authors declare no competing interests."
    )
    result = validate_conflict_of_interest(ms, clf)
    assert result.findings == []


def test_coi_skips_non_empirical() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest

    ms, clf = _coi_manuscript_simple("Literature review.", paper_type="literature_review")
    result = validate_conflict_of_interest(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 122 – validate_citations_in_abstract
# ---------------------------------------------------------------------------


def _citations_abstract_ms(abstract: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="cite-abs-test",
        source_path="cite.md",
        source_format="markdown",
        title="Test",
        abstract=abstract,
        full_text="",
    )


def test_citations_in_abstract_fires() -> None:
    from manuscript_audit.validators.core import validate_citations_in_abstract

    ms = _citations_abstract_ms(
        "This paper extends Smith et al., 2020 and Jones (2019) to show effects."
    )
    result = validate_citations_in_abstract(ms)
    codes = [f.code for f in result.findings]
    assert "citations-in-abstract" in codes


def test_clean_abstract_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citations_in_abstract

    ms = _citations_abstract_ms(
        "We present a new method for regression analysis with improved performance."
    )
    result = validate_citations_in_abstract(ms)
    assert result.findings == []


def test_no_abstract_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citations_in_abstract

    ms = _citations_abstract_ms("")
    result = validate_citations_in_abstract(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 123 – validate_funding_statement
# ---------------------------------------------------------------------------


def _funding_manuscript(body: str, paper_type: str = "empirical_paper") -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="fund-test",
        source_path="fund.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=body)],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_funding_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_funding_statement

    ms, clf = _funding_manuscript("We collected data from 100 participants.")
    result = validate_funding_statement(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-funding-statement" in codes


def test_funding_passes_with_statement() -> None:
    from manuscript_audit.validators.core import validate_funding_statement

    ms, clf = _funding_manuscript(
        "This work was supported by NSF grant number 123456."
    )
    result = validate_funding_statement(ms, clf)
    assert result.findings == []


def test_funding_skips_non_empirical() -> None:
    from manuscript_audit.validators.core import validate_funding_statement

    ms, clf = _funding_manuscript(
        "We describe the software architecture.",
        paper_type="math_theory_paper",
    )
    result = validate_funding_statement(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 124 – validate_discussion_section_presence
# ---------------------------------------------------------------------------


def _discussion_presence_manuscript(
    sections: list,
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="disc-test",
        source_path="disc.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[
            Section(title=t, level=1, body=b) for t, b in sections
        ],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_missing_discussion_fires() -> None:
    from manuscript_audit.validators.core import validate_discussion_section_presence

    ms, clf = _discussion_presence_manuscript([
        ("Methods", "We used regression."),
        ("Results", "We found a significant effect (p < 0.05)."),
    ])
    result = validate_discussion_section_presence(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-discussion-section" in codes


def test_discussion_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_discussion_section_presence

    ms, clf = _discussion_presence_manuscript([
        ("Methods", "We used regression."),
        ("Results", "We found a significant effect."),
        ("Discussion", "Our results suggest that X causes Y."),
    ])
    result = validate_discussion_section_presence(ms, clf)
    assert result.findings == []


def test_discussion_skips_non_empirical() -> None:
    from manuscript_audit.validators.core import validate_discussion_section_presence

    ms, clf = _discussion_presence_manuscript(
        [("Results", "Table 1 shows metrics.")],
        paper_type="math_theory_paper",
    )
    result = validate_discussion_section_presence(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 125 – validate_pvalue_notation_consistency
# ---------------------------------------------------------------------------


def _pvalue_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="pval-test",
        source_path="pval.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_inconsistent_pvalue_fires() -> None:
    from manuscript_audit.validators.core import validate_pvalue_notation_consistency

    ms = _pvalue_manuscript(
        "Group A showed p < 0.05 improvement. "
        "Group B showed P < 0.01 difference. "
        "p-value < 0.001 for the interaction term."
    )
    result = validate_pvalue_notation_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "inconsistent-pvalue-notation" in codes


def test_consistent_pvalue_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pvalue_notation_consistency

    ms = _pvalue_manuscript(
        "Group A showed p < 0.05. Group B showed p < 0.01. Interaction p < 0.001."
    )
    result = validate_pvalue_notation_consistency(ms)
    assert result.findings == []


def test_few_pvalues_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pvalue_notation_consistency

    ms = _pvalue_manuscript("The effect was P < 0.05 and p < 0.01.")
    result = validate_pvalue_notation_consistency(ms)
    # Only 2 occurrences total < threshold of 3, should not fire even if mixed
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 126 – validate_methods_section_presence
# ---------------------------------------------------------------------------


def _methods_presence_manuscript(
    section_titles: list,
    paper_type: str = "empirical_paper",
) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="meth-pres-test",
        source_path="meth.md",
        source_format="markdown",
        title="Test Study",
        abstract="Abstract.",
        full_text="",
        sections=[
            Section(title=t, level=1, body="body text.") for t in section_titles
        ],
    )
    clf = ManuscriptClassification(
        paper_type=paper_type,
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_missing_methods_fires() -> None:
    from manuscript_audit.validators.core import validate_methods_section_presence

    ms, clf = _methods_presence_manuscript(
        ["Introduction", "Results", "Discussion"]
    )
    result = validate_methods_section_presence(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-methods-section" in codes


def test_methods_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_methods_section_presence

    ms, clf = _methods_presence_manuscript(
        ["Introduction", "Methods", "Results", "Discussion"]
    )
    result = validate_methods_section_presence(ms, clf)
    assert result.findings == []


def test_methods_skips_non_empirical() -> None:
    from manuscript_audit.validators.core import validate_methods_section_presence

    ms, clf = _methods_presence_manuscript(
        ["Introduction", "Results", "Discussion"],
        paper_type="math_theory_paper",
    )
    result = validate_methods_section_presence(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 127 – validate_conclusion_section_presence
# ---------------------------------------------------------------------------


def _conclusion_presence_manuscript(section_titles: list) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="conc-pres-test",
        source_path="conc.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[
            Section(title=t, level=1, body="body text.") for t in section_titles
        ],
    )


def test_missing_conclusion_fires() -> None:
    from manuscript_audit.validators.core import validate_conclusion_section_presence

    ms = _conclusion_presence_manuscript(
        ["Introduction", "Methods", "Results", "Discussion"]
    )
    result = validate_conclusion_section_presence(ms)
    codes = [f.code for f in result.findings]
    assert "missing-conclusion-section" in codes


def test_conclusion_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conclusion_section_presence

    ms = _conclusion_presence_manuscript(
        ["Introduction", "Methods", "Results", "Discussion", "Conclusion"]
    )
    result = validate_conclusion_section_presence(ms)
    assert result.findings == []


def test_short_manuscript_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conclusion_section_presence

    ms = _conclusion_presence_manuscript(["Introduction", "Methods"])
    result = validate_conclusion_section_presence(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 128 – validate_participant_demographics
# ---------------------------------------------------------------------------


def _demographics_manuscript(methods_body: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="demog-test",
        source_path="demog.md",
        source_format="markdown",
        title="Test Study",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_demographics_fires_when_participants_no_details() -> None:
    from manuscript_audit.validators.core import validate_participant_demographics

    ms, clf = _demographics_manuscript(
        "We recruited participants online. All participants completed the survey."
    )
    result = validate_participant_demographics(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-participant-demographics" in codes


def test_demographics_passes_with_details() -> None:
    from manuscript_audit.validators.core import validate_participant_demographics

    ms, clf = _demographics_manuscript(
        "We recruited 120 participants (60 female, mean age = 24.3 years). "
        "All participants completed the survey online."
    )
    result = validate_participant_demographics(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 129 – validate_conflicting_acronym_definitions
# ---------------------------------------------------------------------------


def _conflicting_acronym_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="acr-test",
        source_path="acr.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_duplicate_acronym_fires() -> None:
    from manuscript_audit.validators.core import validate_conflicting_acronym_definitions

    text = (
        "We used Natural Language Processing (NLP) techniques. "
        "Later, Neural Language Prediction (NLP) was evaluated. "
        "The NLP pipeline showed improvements."
    )
    ms = _conflicting_acronym_manuscript(text)
    result = validate_conflicting_acronym_definitions(ms)
    codes = [f.code for f in result.findings]
    assert "inconsistent-acronym-definition" in codes


def test_consistent_acronym_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflicting_acronym_definitions

    text = (
        "We used Natural Language Processing (NLP) techniques. "
        "The NLP pipeline showed impressive results. "
        "NLP methods were applied to the dataset."
    )
    ms = _conflicting_acronym_manuscript(text)
    result = validate_conflicting_acronym_definitions(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 130 – validate_percentage_notation_consistency
# ---------------------------------------------------------------------------


def _percentage_manuscript(results_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="pct-test",
        source_path="pct.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Results", level=1, body=results_body)],
    )


def test_inconsistent_percentage_fires() -> None:
    from manuscript_audit.validators.core import validate_percentage_notation_consistency

    ms = _percentage_manuscript(
        "Accuracy was 85%. Recall was 90 percent. "
        "Precision was 88 per cent. F1 was 87%."
    )
    result = validate_percentage_notation_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "inconsistent-percentage-notation" in codes


def test_consistent_percentage_no_fire() -> None:
    from manuscript_audit.validators.core import validate_percentage_notation_consistency

    ms = _percentage_manuscript(
        "Accuracy was 85%. Recall was 90%. Precision was 88%. F1 was 87%."
    )
    result = validate_percentage_notation_consistency(ms)
    assert result.findings == []


def test_few_percentages_no_fire() -> None:
    from manuscript_audit.validators.core import validate_percentage_notation_consistency

    ms = _percentage_manuscript("Accuracy was 85%. Recall was 90 percent.")
    result = validate_percentage_notation_consistency(ms)
    # Only 2 occurrences < threshold of 4
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 131 – validate_figure_label_consistency
# ---------------------------------------------------------------------------


def _fig_label_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="fig-label-test",
        source_path="fig.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_inconsistent_figure_labels_fires() -> None:
    from manuscript_audit.validators.core import validate_figure_label_consistency

    ms = _fig_label_manuscript(
        "See Figure 1 for the overview. "
        "Fig. 2 shows the distribution. "
        "Figure 3 displays the results. "
        "fig. 4 is the comparison."
    )
    result = validate_figure_label_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "inconsistent-figure-labels" in codes


def test_consistent_figure_labels_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_label_consistency

    ms = _fig_label_manuscript(
        "See Figure 1 for the overview. "
        "Figure 2 shows the distribution. "
        "Figure 3 displays the results."
    )
    result = validate_figure_label_consistency(ms)
    assert result.findings == []


def test_few_fig_refs_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_label_consistency

    ms = _fig_label_manuscript("See Fig. 1 and Figure 2 for details.")
    result = validate_figure_label_consistency(ms)
    # Only 2 refs < threshold of 3, should not fire
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 132 – validate_draft_title_markers
# ---------------------------------------------------------------------------


def _draft_title_manuscript(title: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="draft-test",
        source_path="draft.md",
        source_format="markdown",
        title=title,
        abstract="Abstract.",
        full_text="",
    )


def test_draft_title_fires() -> None:
    from manuscript_audit.validators.core import validate_draft_title_markers

    ms = _draft_title_manuscript("DRAFT: Effects of X on Y")
    result = validate_draft_title_markers(ms)
    codes = [f.code for f in result.findings]
    assert "draft-title-marker" in codes


def test_placeholder_title_fires() -> None:
    from manuscript_audit.validators.core import validate_draft_title_markers

    ms = _draft_title_manuscript("[Title] of the Manuscript")
    result = validate_draft_title_markers(ms)
    codes = [f.code for f in result.findings]
    assert "draft-title-marker" in codes


def test_clean_title_no_fire() -> None:
    from manuscript_audit.validators.core import validate_draft_title_markers

    ms = _draft_title_manuscript(
        "Deterministic Validation of Academic Manuscripts"
    )
    result = validate_draft_title_markers(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 133 – validate_study_period_reporting
# ---------------------------------------------------------------------------


def _study_period_manuscript(methods_body: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="period-test",
        source_path="period.md",
        source_format="markdown",
        title="Test Study",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_study_period_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_study_period_reporting

    ms, clf = _study_period_manuscript(
        "We recruited 200 participants from an online platform. "
        "All participants provided written consent."
    )
    result = validate_study_period_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-study-period" in codes


def test_study_period_passes_when_present() -> None:
    from manuscript_audit.validators.core import validate_study_period_reporting

    ms, clf = _study_period_manuscript(
        "We recruited 200 participants from 2019 to 2021. "
        "Data collection was conducted between January and June 2020."
    )
    result = validate_study_period_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 134 – validate_scale_anchor_reporting
# ---------------------------------------------------------------------------


def _scale_anchor_manuscript(methods_body: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="scale-test",
        source_path="scale.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_scale_anchor_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_scale_anchor_reporting

    ms, clf = _scale_anchor_manuscript(
        "Items were rated on a 5-point Likert scale. "
        "Higher scores indicate greater agreement."
    )
    result = validate_scale_anchor_reporting(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-scale-anchors" in codes


def test_scale_anchor_passes_with_endpoints() -> None:
    from manuscript_audit.validators.core import validate_scale_anchor_reporting

    ms, clf = _scale_anchor_manuscript(
        "Items were rated on a 5-point Likert scale anchored from "
        "1 = strongly disagree to 5 = strongly agree."
    )
    result = validate_scale_anchor_reporting(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 135 – validate_model_specification
# ---------------------------------------------------------------------------


def _model_spec_manuscript(methods_body: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="model-spec-test",
        source_path="model.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_model_spec_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_model_specification

    ms, clf = _model_spec_manuscript(
        "We used logistic regression to predict outcomes. "
        "All analyses were performed in R 4.3."
    )
    result = validate_model_specification(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-model-specification" in codes


def test_model_spec_passes_with_predictors() -> None:
    from manuscript_audit.validators.core import validate_model_specification

    ms, clf = _model_spec_manuscript(
        "We used logistic regression with age, gender, and treatment as predictors. "
        "The dependent variable was 30-day readmission."
    )
    result = validate_model_specification(ms, clf)
    assert result.findings == []


def test_model_spec_no_fire_when_no_model() -> None:
    from manuscript_audit.validators.core import validate_model_specification

    ms, clf = _model_spec_manuscript(
        "We used descriptive statistics to summarize the data. "
        "Frequencies and percentages are reported for categorical variables."
    )
    result = validate_model_specification(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 136 – validate_effect_direction_reporting
# ---------------------------------------------------------------------------


def _effect_direction_manuscript(results_body: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

    return ParsedManuscript(
        manuscript_id="eff-dir-test",
        source_path="eff.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Results", level=1, body=results_body)],
    )


def test_missing_effect_direction_fires() -> None:
    from manuscript_audit.validators.core import validate_effect_direction_reporting

    ms = _effect_direction_manuscript(
        "Intervention and control conditions showed a statistically significant "
        "difference (p < 0.05). "
        "The effect was significant (p = 0.03). "
        "Results were significant across all conditions (p < 0.01)."
    )
    result = validate_effect_direction_reporting(ms)
    codes = [f.code for f in result.findings]
    assert "missing-effect-direction" in codes


def test_effect_direction_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_direction_reporting

    ms = _effect_direction_manuscript(
        "Group A scored significantly higher than Group B (p < 0.05, d = 0.72). "
        "The treatment group showed greater improvement compared to control (p = 0.03)."
    )
    result = validate_effect_direction_reporting(ms)
    assert result.findings == []


def test_few_sig_results_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_direction_reporting

    ms = _effect_direction_manuscript(
        "Descriptive statistics are shown in Table 1."
    )
    result = validate_effect_direction_reporting(ms)
    # No significance mentions at all — should not fire
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 137 – validate_citation_format_consistency
# ---------------------------------------------------------------------------


def _citation_format_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="cite-fmt-test",
        source_path="cite.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_mixed_citation_format_fires() -> None:
    from manuscript_audit.validators.core import validate_citation_format_consistency

    ms = _citation_format_manuscript(
        "Previous work [1] established this. "
        "Smith et al. (2020) extended these findings. "
        "Jones (2019) confirmed the effect. "
        "See also [2] and [3] for details."
    )
    result = validate_citation_format_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "mixed-citation-format" in codes


def test_consistent_numeric_cites_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_format_consistency

    ms = _citation_format_manuscript(
        "Previous work [1] established this. Extended [2]. "
        "See [3] and [4] for details."
    )
    result = validate_citation_format_consistency(ms)
    assert result.findings == []


def test_citation_format_few_refs_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_format_consistency

    ms = _citation_format_manuscript(
        "See [1] and Smith (2020) for details."
    )
    result = validate_citation_format_consistency(ms)
    # Only 2 total < threshold of 4
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 138 – validate_imputation_sensitivity
# ---------------------------------------------------------------------------


def _imputation_manuscript(full_text: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="impute-test",
        source_path="impute.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_imputation_without_sensitivity_fires() -> None:
    from manuscript_audit.validators.core import validate_imputation_sensitivity

    ms, clf = _imputation_manuscript(
        "Missing data were handled using multiple imputation with MICE. "
        "Twenty imputed datasets were created and pooled using Rubin's rules."
    )
    result = validate_imputation_sensitivity(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-imputation-sensitivity" in codes


def test_imputation_with_sensitivity_no_fire() -> None:
    from manuscript_audit.validators.core import validate_imputation_sensitivity

    ms, clf = _imputation_manuscript(
        "Missing data were handled using multiple imputation with MICE. "
        "A sensitivity analysis using complete-case analysis confirmed results."
    )
    result = validate_imputation_sensitivity(ms, clf)
    assert result.findings == []


def test_no_imputation_no_fire() -> None:
    from manuscript_audit.validators.core import validate_imputation_sensitivity

    ms, clf = _imputation_manuscript(
        "All participants completed the survey. No missing data were observed."
    )
    result = validate_imputation_sensitivity(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 139 – validate_computational_environment
# ---------------------------------------------------------------------------


def _computation_manuscript(methods_body: str) -> tuple:
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.routing import ManuscriptClassification

    ms = ParsedManuscript(
        manuscript_id="comp-env-test",
        source_path="comp.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text="",
        sections=[Section(title="Methods", level=1, body=methods_body)],
    )
    clf = ManuscriptClassification(
        paper_type="empirical_paper",
        pathway="data_science",
        recommended_stack="maximal",
    )
    return ms, clf


def test_computational_env_fires_when_absent() -> None:
    from manuscript_audit.validators.core import validate_computational_environment

    ms, clf = _computation_manuscript(
        "We trained a neural network to classify the images. "
        "Cross-validation was performed with 5 folds."
    )
    result = validate_computational_environment(ms, clf)
    codes = [f.code for f in result.findings]
    assert "missing-computational-environment" in codes


def test_computational_env_passes_with_details() -> None:
    from manuscript_audit.validators.core import validate_computational_environment

    ms, clf = _computation_manuscript(
        "We trained a neural network using Python 3.9 with TensorFlow 2.10. "
        "Cross-validation used 5 folds on an NVIDIA GPU."
    )
    result = validate_computational_environment(ms, clf)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 140 – validate_table_captions
# ---------------------------------------------------------------------------


def _table_captions_manuscript(full_text: str) -> object:
    from manuscript_audit.schemas.artifacts import ParsedManuscript

    return ParsedManuscript(
        manuscript_id="tbl-cap-test",
        source_path="tbl.md",
        source_format="markdown",
        title="Test",
        abstract="Abstract.",
        full_text=full_text,
    )


def test_missing_table_captions_fires() -> None:
    from manuscript_audit.validators.core import validate_table_captions

    ms = _table_captions_manuscript(
        "Results are shown in Table 1. Additional data in Table 2. "
        "See Table 3 for sensitivity results."
    )
    result = validate_table_captions(ms)
    codes = [f.code for f in result.findings]
    assert "missing-table-captions" in codes


def test_table_captions_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_table_captions

    ms = _table_captions_manuscript(
        "Table 1: Descriptive statistics for all study variables.\n"
        "Table 2: Regression model results showing coefficients.\n"
        "See Table 1 and Table 2 for details."
    )
    result = validate_table_captions(ms)
    assert result.findings == []


def test_single_table_no_fire() -> None:
    from manuscript_audit.validators.core import validate_table_captions

    ms = _table_captions_manuscript("Results in Table 1 show the means.")
    result = validate_table_captions(ms)
    # Only 1 table ref < threshold of 2
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 141 – Raw data description
# ---------------------------------------------------------------------------


def _raw_data_ms(methods_text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="raw-data-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=methods_text)],
        full_text=methods_text,
    )


def _raw_data_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_raw_data_description_fires() -> None:
    from manuscript_audit.validators.core import validate_raw_data_description

    ms = _raw_data_ms(
        "We used a large dataset from the registry. The dataset contains patient records. "
        "The dataset was pre-processed before analysis."
    )
    result = validate_raw_data_description(ms, _raw_data_clf())
    codes = [f.code for f in result.findings]
    assert "missing-raw-data-description" in codes


def test_raw_data_with_format_no_fire() -> None:
    from manuscript_audit.validators.core import validate_raw_data_description

    ms = _raw_data_ms(
        "We used a dataset from the registry (available at zenodo doi: 10.5281/zenodo.12345). "
        "The dataset was stored as CSV files. The dataset was pre-processed."
    )
    result = validate_raw_data_description(ms, _raw_data_clf())
    assert result.findings == []


def test_raw_data_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_raw_data_description

    ms = _raw_data_ms(
        "We used a large dataset from the registry. The dataset contains records. "
        "The dataset was pre-processed before analysis."
    )
    result = validate_raw_data_description(ms, _raw_data_clf("math_theory_paper"))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 142 – Multiple outcomes / multiple comparisons correction
# ---------------------------------------------------------------------------


def _multi_outcome_ms(body: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="multi-outcome-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=body)],
        full_text=body,
    )


def _multi_outcome_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_multiple_outcomes_correction_fires() -> None:
    from manuscript_audit.validators.core import validate_multiple_outcomes_correction

    ms = _multi_outcome_ms(
        "The primary outcome measure was depression severity. The secondary outcome "
        "was anxiety. A third outcome variable was quality of life. The fourth "
        "outcome measure was functional impairment. We compared these outcomes "
        "across groups."
    )
    result = validate_multiple_outcomes_correction(ms, _multi_outcome_clf())
    codes = [f.code for f in result.findings]
    assert "missing-multiple-outcomes-correction" in codes


def test_multiple_outcomes_with_correction_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_outcomes_correction

    ms = _multi_outcome_ms(
        "The primary outcome measure was depression severity. The secondary outcome "
        "was anxiety. A third outcome variable was quality of life. The fourth "
        "outcome measure was functional impairment. We applied Bonferroni correction "
        "for multiple comparisons."
    )
    result = validate_multiple_outcomes_correction(ms, _multi_outcome_clf())
    assert result.findings == []


def test_few_outcomes_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_outcomes_correction

    ms = _multi_outcome_ms(
        "The primary outcome was depression severity. The dependent variable was "
        "measured at baseline."
    )
    result = validate_multiple_outcomes_correction(ms, _multi_outcome_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 143 – Replication dataset reporting
# ---------------------------------------------------------------------------


def _replication_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="replication-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _replication_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_replication_dataset_fires() -> None:
    from manuscript_audit.validators.core import validate_replication_dataset

    ms = _replication_ms(
        "We collected data from 500 participants and trained a logistic regression model. "
        "All participants were from a single hospital."
    )
    result = validate_replication_dataset(ms, _replication_clf())
    codes = [f.code for f in result.findings]
    assert "missing-replication-dataset" in codes


def test_replication_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_replication_dataset

    ms = _replication_ms(
        "We trained the model on a discovery cohort and evaluated it on an independent "
        "validation dataset of 200 participants from a different center."
    )
    result = validate_replication_dataset(ms, _replication_clf())
    assert result.findings == []


def test_replication_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_replication_dataset

    ms = _replication_ms(
        "We present a mathematical proof. No empirical data were collected."
    )
    result = validate_replication_dataset(ms, _replication_clf("math_theory_paper"))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 144 – Appendix reference consistency
# ---------------------------------------------------------------------------


def _appendix_ms(text: str, has_appendix_section: bool = False) -> ParsedManuscript:
    sections: list[Section] = [Section(title="Introduction", level=2, body=text)]
    full = text
    if has_appendix_section:
        sections.append(Section(title="Appendix A", level=2, body="Supplementary tables."))
        full = text + "\n\nAppendix A\nSupplementary tables."
    return ParsedManuscript(
        manuscript_id="appendix-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=sections,
        full_text=full,
    )


def test_missing_appendix_section_fires() -> None:
    from manuscript_audit.validators.core import validate_appendix_reference_consistency

    ms = _appendix_ms(
        "See Appendix for additional results. The supplementary materials include tables."
    )
    result = validate_appendix_reference_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "missing-appendix-section" in codes


def test_appendix_section_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_appendix_reference_consistency

    ms = _appendix_ms(
        "See Appendix for additional results.",
        has_appendix_section=True,
    )
    result = validate_appendix_reference_consistency(ms)
    assert result.findings == []


def test_no_appendix_reference_no_fire() -> None:
    from manuscript_audit.validators.core import validate_appendix_reference_consistency

    ms = _appendix_ms("We analyzed the data using regression methods.")
    result = validate_appendix_reference_consistency(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 145 – Open science / data availability statement
# ---------------------------------------------------------------------------


def _open_science_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="open-science-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _open_science_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_open_science_statement_fires() -> None:
    from manuscript_audit.validators.core import validate_open_science_statement

    ms = _open_science_ms(
        "We collected survey data and analyzed it. All participants provided informed consent."
    )
    result = validate_open_science_statement(ms, _open_science_clf())
    codes = [f.code for f in result.findings]
    assert "missing-open-science-statement" in codes


def test_data_availability_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_open_science_statement

    ms = _open_science_ms(
        "Data availability: The data are available upon request from the corresponding author."
    )
    result = validate_open_science_statement(ms, _open_science_clf())
    assert result.findings == []


def test_github_link_no_fire() -> None:
    from manuscript_audit.validators.core import validate_open_science_statement

    ms = _open_science_ms(
        "Code is available at github.com/user/repo."
    )
    result = validate_open_science_statement(ms, _open_science_clf())
    assert result.findings == []


def test_open_science_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_open_science_statement

    ms = _open_science_ms(
        "We present a theoretical framework without empirical data."
    )
    result = validate_open_science_statement(ms, _open_science_clf("math_theory_paper"))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 146 – Cohort attrition reporting
# ---------------------------------------------------------------------------


def _attrition_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="attrition-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _attrition_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_attrition_reporting_fires() -> None:
    from manuscript_audit.validators.core import validate_cohort_attrition

    ms = _attrition_ms(
        "We conducted a longitudinal study with baseline and follow-up assessments. "
        "Participants completed questionnaires at each time point. "
        "The study used a prospective cohort design."
    )
    result = validate_cohort_attrition(ms, _attrition_clf())
    codes = [f.code for f in result.findings]
    assert "missing-attrition-reporting" in codes


def test_attrition_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cohort_attrition

    ms = _attrition_ms(
        "We conducted a longitudinal study. At follow-up, 23 participants were lost "
        "to follow-up due to relocation. Attrition was 8% overall."
    )
    result = validate_cohort_attrition(ms, _attrition_clf())
    assert result.findings == []


def test_attrition_cross_sectional_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cohort_attrition

    ms = _attrition_ms(
        "We recruited 300 participants for a cross-sectional survey study. "
        "Participants completed a one-time questionnaire."
    )
    result = validate_cohort_attrition(ms, _attrition_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 147 – Blinding procedure reporting
# ---------------------------------------------------------------------------


def _blinding_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="blinding-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _blinding_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_blinding_procedure_fires() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure

    ms = _blinding_ms(
        "We conducted a randomized controlled trial comparing drug A vs placebo. "
        "Participants were allocated to treatment group or control group."
    )
    result = validate_blinding_procedure(ms, _blinding_clf())
    codes = [f.code for f in result.findings]
    assert "missing-blinding-procedure" in codes


def test_blinding_described_no_fire() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure

    ms = _blinding_ms(
        "We conducted a randomized controlled trial. The study was double-blind: "
        "participants and assessors were masked to treatment allocation."
    )
    result = validate_blinding_procedure(ms, _blinding_clf())
    assert result.findings == []


def test_no_intervention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure

    ms = _blinding_ms(
        "We surveyed 400 adults about their health behaviors. "
        "All responses were anonymous and confidential."
    )
    result = validate_blinding_procedure(ms, _blinding_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 148 – Floor/ceiling effects
# ---------------------------------------------------------------------------


def _floor_ceiling_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="floor-ceiling-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _floor_ceiling_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_floor_ceiling_discussion_fires() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effects

    ms = _floor_ceiling_ms(
        "We administered three Likert-scale questionnaires measuring wellbeing. "
        "Each questionnaire is a validated psychometric instrument. "
        "The scale scores were analyzed using linear regression."
    )
    result = validate_floor_ceiling_effects(ms, _floor_ceiling_clf())
    codes = [f.code for f in result.findings]
    assert "missing-floor-ceiling-discussion" in codes


def test_floor_ceiling_discussed_no_fire() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effects

    ms = _floor_ceiling_ms(
        "We administered three Likert-scale questionnaires using a validated "
        "psychometric instrument. We checked for floor effects and ceiling effects "
        "before analysis; none were detected."
    )
    result = validate_floor_ceiling_effects(ms, _floor_ceiling_clf())
    assert result.findings == []


def test_few_scale_refs_no_fire() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effects

    ms = _floor_ceiling_ms(
        "We used a brief Likert scale to measure satisfaction."
    )
    result = validate_floor_ceiling_effects(ms, _floor_ceiling_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 149 – Negative result framing
# ---------------------------------------------------------------------------


def _neg_result_ms(results_body: str, discussion_body: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="neg-result-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[
            Section(title="Results", level=2, body=results_body),
            Section(title="Discussion", level=2, body=discussion_body),
        ],
        full_text=results_body + "\n" + discussion_body,
    )


def test_negative_result_underreported_fires() -> None:
    from manuscript_audit.validators.core import validate_negative_result_framing

    ms = _neg_result_ms(
        results_body=(
            "The association was not significant (p = 0.42). "
            "No significant difference was found between groups (p > 0.05). "
            "The intervention did not reach significance."
        ),
        discussion_body=(
            "Our findings suggest that the intervention was effective in several domains. "
            "Future research should explore additional mechanisms."
        ),
    )
    result = validate_negative_result_framing(ms)
    codes = [f.code for f in result.findings]
    assert "negative-result-underreported" in codes


def test_negative_result_acknowledged_no_fire() -> None:
    from manuscript_audit.validators.core import validate_negative_result_framing

    ms = _neg_result_ms(
        results_body=(
            "The association was not significant (p = 0.42). "
            "No significant difference was found between groups (p > 0.05). "
        ),
        discussion_body=(
            "The null result for the primary outcome may reflect insufficient "
            "power. These negative results should be interpreted cautiously."
        ),
    )
    result = validate_negative_result_framing(ms)
    assert result.findings == []


def test_no_results_section_no_fire() -> None:
    from manuscript_audit.validators.core import validate_negative_result_framing

    ms = ParsedManuscript(
        manuscript_id="no-results",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Introduction", level=2, body="Background context.")],
        full_text="Background context.",
    )
    result = validate_negative_result_framing(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 150 – Abstract–results consistency
# ---------------------------------------------------------------------------


def _abstract_results_ms(abstract: str, results_body: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="abstract-results-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        abstract=abstract,
        sections=[Section(title="Results", level=2, body=results_body)],
        full_text=abstract + "\n" + results_body,
    )


def test_abstract_results_mismatch_fires() -> None:
    from manuscript_audit.validators.core import validate_abstract_results_consistency

    ms = _abstract_results_ms(
        abstract=(
            "We found that treatment significantly improved outcomes. "
            "Results show significantly higher scores in the treatment group. "
            "Our findings demonstrate a significant reduction in symptoms."
        ),
        results_body="Participants reported improved wellbeing after treatment.",
    )
    result = validate_abstract_results_consistency(ms)
    codes = [f.code for f in result.findings]
    assert "abstract-results-mismatch" in codes


def test_abstract_results_consistent_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_results_consistency

    ms = _abstract_results_ms(
        abstract=(
            "We found that treatment significantly improved outcomes. "
            "Results show significantly higher scores."
        ),
        results_body=(
            "Scores were significantly higher in the treatment group (p < 0.001). "
            "The intervention was significantly more effective than control (p < 0.01)."
        ),
    )
    result = validate_abstract_results_consistency(ms)
    assert result.findings == []


def test_abstract_consistency_no_abstract_no_fire() -> None:
    from manuscript_audit.validators.core import validate_abstract_results_consistency

    ms = ParsedManuscript(
        manuscript_id="no-abstract",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        abstract="",
        sections=[Section(title="Results", level=2, body="Means were compared.")],
        full_text="Means were compared.",
    )
    result = validate_abstract_results_consistency(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 151 – Measurement invariance
# ---------------------------------------------------------------------------


def _invariance_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="invariance-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _invariance_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_measurement_invariance_fires() -> None:
    from manuscript_audit.validators.core import validate_measurement_invariance

    ms = _invariance_ms(
        "We compared groups on a Likert scale measuring wellbeing. "
        "Comparison between groups showed significant differences. "
        "The questionnaire was administered to both samples."
    )
    result = validate_measurement_invariance(ms, _invariance_clf())
    codes = [f.code for f in result.findings]
    assert "missing-measurement-invariance" in codes


def test_measurement_invariance_tested_no_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_invariance

    ms = _invariance_ms(
        "We compared groups on a Likert scale. Comparison between groups "
        "was preceded by a test of measurement invariance. Scalar invariance "
        "was supported (CFI difference < 0.01)."
    )
    result = validate_measurement_invariance(ms, _invariance_clf())
    assert result.findings == []


def test_no_group_comparison_no_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_invariance

    ms = _invariance_ms(
        "We administered a Likert scale to all participants and analyzed "
        "the relationship between scores and outcomes."
    )
    result = validate_measurement_invariance(ms, _invariance_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 152 – Effect size confidence intervals
# ---------------------------------------------------------------------------


def _es_ci_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="es-ci-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Results", level=2, body=text)],
        full_text=text,
    )


def _es_ci_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_effect_size_ci_fires() -> None:
    from manuscript_audit.validators.core import validate_effect_size_confidence_intervals

    ms = _es_ci_ms(
        "The effect size was Cohen's d = 0.45. "
        "For the secondary outcome, Cohen's d = 0.32."
    )
    result = validate_effect_size_confidence_intervals(ms, _es_ci_clf())
    codes = [f.code for f in result.findings]
    assert "missing-effect-size-ci" in codes


def test_effect_size_ci_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_size_confidence_intervals

    ms = _es_ci_ms(
        "The effect size was Cohen's d = 0.45 (95% CI [0.21, 0.69]). "
        "For the secondary outcome, Cohen's d = 0.32."
    )
    result = validate_effect_size_confidence_intervals(ms, _es_ci_clf())
    assert result.findings == []


def test_no_effect_size_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_size_confidence_intervals

    ms = _es_ci_ms(
        "The mean score in the treatment group was 4.2 (SD = 0.8)."
    )
    result = validate_effect_size_confidence_intervals(ms, _es_ci_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 153 – Preregistration statement
# ---------------------------------------------------------------------------


def _prereg_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="prereg-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _prereg_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_preregistration_fires() -> None:
    from manuscript_audit.validators.core import validate_preregistration_statement

    ms = _prereg_ms(
        "We conducted a randomized controlled trial. We hypothesized that the "
        "intervention would improve outcomes significantly. Participants were "
        "allocated to treatment group or control group."
    )
    result = validate_preregistration_statement(ms, _prereg_clf())
    codes = [f.code for f in result.findings]
    assert "missing-preregistration" in codes


def test_preregistered_study_no_fire() -> None:
    from manuscript_audit.validators.core import validate_preregistration_statement

    ms = _prereg_ms(
        "This randomized controlled trial was preregistered at ClinicalTrials.gov "
        "(NCT12345678). We hypothesized that the intervention would improve outcomes."
    )
    result = validate_preregistration_statement(ms, _prereg_clf())
    assert result.findings == []


def test_exploratory_study_no_fire() -> None:
    from manuscript_audit.validators.core import validate_preregistration_statement

    ms = _prereg_ms(
        "We conducted an exploratory survey study examining associations between "
        "lifestyle factors and health outcomes."
    )
    result = validate_preregistration_statement(ms, _prereg_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 154 – Cross-validation reporting
# ---------------------------------------------------------------------------


def _cv_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="cv-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _cv_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_cross_validation_fires() -> None:
    from manuscript_audit.validators.core import validate_cross_validation_reporting

    ms = _cv_ms(
        "We trained a random forest model on the full dataset. "
        "The model achieved high accuracy in predicting the outcome."
    )
    result = validate_cross_validation_reporting(ms, _cv_clf())
    codes = [f.code for f in result.findings]
    assert "missing-cross-validation" in codes


def test_cross_validation_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cross_validation_reporting

    ms = _cv_ms(
        "We trained a random forest model using 10-fold cross-validation. "
        "Performance was evaluated on the held-out fold."
    )
    result = validate_cross_validation_reporting(ms, _cv_clf())
    assert result.findings == []


def test_no_ml_model_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cross_validation_reporting

    ms = _cv_ms(
        "We compared group means using ANOVA and post-hoc tests."
    )
    result = validate_cross_validation_reporting(ms, _cv_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 155 – Sensitivity analysis reporting
# ---------------------------------------------------------------------------


def _sensitivity_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="sensitivity-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _sensitivity_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_sensitivity_analysis_fires() -> None:
    from manuscript_audit.validators.core import validate_sensitivity_analysis_reporting

    ms = _sensitivity_ms(
        "The primary analysis used a generalized linear model. "
        "Our primary outcome was depression severity at 12 weeks."
    )
    result = validate_sensitivity_analysis_reporting(ms, _sensitivity_clf())
    codes = [f.code for f in result.findings]
    assert "missing-sensitivity-analysis" in codes


def test_sensitivity_analysis_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sensitivity_analysis_reporting

    ms = _sensitivity_ms(
        "The primary analysis used a generalized linear model. "
        "As a robustness check, we repeated the analysis excluding outliers."
    )
    result = validate_sensitivity_analysis_reporting(ms, _sensitivity_clf())
    assert result.findings == []


def test_no_primary_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sensitivity_analysis_reporting

    ms = _sensitivity_ms(
        "We described the frequency distributions and basic statistics."
    )
    result = validate_sensitivity_analysis_reporting(ms, _sensitivity_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 156 – Regression diagnostics
# ---------------------------------------------------------------------------


def _regression_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="regression-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _regression_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_regression_diagnostics_fires() -> None:
    from manuscript_audit.validators.core import validate_regression_diagnostics

    ms = _regression_ms(
        "We used multiple regression to predict depression scores from age, "
        "gender, and treatment. The regression model was significant (F = 12.3)."
    )
    result = validate_regression_diagnostics(ms, _regression_clf())
    codes = [f.code for f in result.findings]
    assert "missing-regression-diagnostics" in codes


def test_regression_diagnostics_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_diagnostics

    ms = _regression_ms(
        "We used multiple regression. We checked multicollinearity (VIF < 3 for "
        "all predictors) and residual normality via Q-Q plots."
    )
    result = validate_regression_diagnostics(ms, _regression_clf())
    assert result.findings == []


def test_no_regression_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_diagnostics

    ms = _regression_ms(
        "We compared group means using an independent-samples t-test."
    )
    result = validate_regression_diagnostics(ms, _regression_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 157 – Sample representativeness
# ---------------------------------------------------------------------------


def _sample_rep_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="sample-rep-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _sample_rep_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_non_representative_sample_fires() -> None:
    from manuscript_audit.validators.core import validate_sample_representativeness

    ms = _sample_rep_ms(
        "We recruited from a single university campus using a convenience sample. "
        "Our results are generalizable to young adults broadly applicable "
        "to the general population."
    )
    result = validate_sample_representativeness(ms, _sample_rep_clf())
    codes = [f.code for f in result.findings]
    assert "non-representative-sample" in codes


def test_sample_rep_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sample_representativeness

    ms = _sample_rep_ms(
        "We recruited from a single university campus using a convenience sample. "
        "Our results may be broadly applicable but a limitation is that results "
        "may not generalize beyond our convenience sample."
    )
    result = validate_sample_representativeness(ms, _sample_rep_clf())
    assert result.findings == []


def test_no_single_site_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sample_representativeness

    ms = _sample_rep_ms(
        "We collected data from 12 hospitals across 4 countries. "
        "The multi-center design enhances external validity."
    )
    result = validate_sample_representativeness(ms, _sample_rep_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 158 – Variable operationalization
# ---------------------------------------------------------------------------


def _var_ops_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="var-ops-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _var_ops_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_variable_operationalization_fires() -> None:
    from manuscript_audit.validators.core import validate_variable_operationalization

    ms = _var_ops_ms(
        "The independent variable was socioeconomic status. The dependent variable "
        "was academic achievement. A covariate was included for age. "
        "The predictor variable was parental education."
    )
    result = validate_variable_operationalization(ms, _var_ops_clf())
    codes = [f.code for f in result.findings]
    assert "missing-variable-operationalization" in codes


def test_variable_operationalization_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_variable_operationalization

    ms = _var_ops_ms(
        "The independent variable was socioeconomic status, operationalized as "
        "household income tercile. The dependent variable was measured using "
        "standardized test scores. The covariate was coded as years of age."
    )
    result = validate_variable_operationalization(ms, _var_ops_clf())
    assert result.findings == []


def test_few_variable_mentions_no_fire() -> None:
    from manuscript_audit.validators.core import validate_variable_operationalization

    ms = _var_ops_ms(
        "The dependent variable was BMI. The independent variable was diet quality."
    )
    result = validate_variable_operationalization(ms, _var_ops_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 159 – Interrater reliability
# ---------------------------------------------------------------------------


def _irr_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="irr-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _irr_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_interrater_reliability_fires() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    ms = _irr_ms(
        "Two independent raters coded all responses using a predefined coding scheme. "
        "Discrepancies were resolved by discussion."
    )
    result = validate_interrater_reliability(ms, _irr_clf())
    codes = [f.code for f in result.findings]
    assert "missing-interrater-reliability" in codes


def test_irr_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    ms = _irr_ms(
        "Two independent raters coded all responses. Cohen's kappa = 0.87 "
        "indicating strong inter-rater agreement."
    )
    result = validate_interrater_reliability(ms, _irr_clf())
    assert result.findings == []


def test_no_independent_coding_no_fire() -> None:
    from manuscript_audit.validators.core import validate_interrater_reliability

    ms = _irr_ms(
        "Survey responses were analyzed using descriptive statistics."
    )
    result = validate_interrater_reliability(ms, _irr_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 160 – Control variable justification
# ---------------------------------------------------------------------------


def _control_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="control-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _control_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_control_justification_fires() -> None:
    from manuscript_audit.validators.core import validate_control_variable_justification

    ms = _control_ms(
        "We controlled for age, gender, and education. Controlling for these "
        "variables, the main effect remained significant."
    )
    result = validate_control_variable_justification(ms, _control_clf())
    codes = [f.code for f in result.findings]
    assert "missing-control-justification" in codes


def test_control_justification_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_control_variable_justification

    ms = _control_ms(
        "We controlled for age and gender based on prior research demonstrating "
        "their role as confounders. Controlling for these theoretically motivated "
        "variables, the effect was maintained."
    )
    result = validate_control_variable_justification(ms, _control_clf())
    assert result.findings == []


def test_few_control_mentions_no_fire() -> None:
    from manuscript_audit.validators.core import validate_control_variable_justification

    ms = _control_ms(
        "We controlled for age in the regression model."
    )
    result = validate_control_variable_justification(ms, _control_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 161 – Prospective vs. retrospective design consistency
# ---------------------------------------------------------------------------


def _prospective_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="prospective-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _prospective_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_retrospective_design_claim_fires() -> None:
    from manuscript_audit.validators.core import validate_prospective_vs_retrospective

    ms = _prospective_ms(
        "We conducted a prospective study. Data were extracted from existing "
        "administrative records that had been previously collected over 5 years."
    )
    result = validate_prospective_vs_retrospective(ms, _prospective_clf())
    codes = [f.code for f in result.findings]
    assert "retrospective-design-claim" in codes


def test_true_prospective_no_fire() -> None:
    from manuscript_audit.validators.core import validate_prospective_vs_retrospective

    ms = _prospective_ms(
        "We conducted a prospective cohort study. Participants were enrolled and "
        "followed forward in time from January to December 2023."
    )
    result = validate_prospective_vs_retrospective(ms, _prospective_clf())
    assert result.findings == []


def test_no_prospective_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_prospective_vs_retrospective

    ms = _prospective_ms(
        "We conducted a retrospective analysis of administrative records "
        "covering a 5-year period."
    )
    result = validate_prospective_vs_retrospective(ms, _prospective_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 162 – CONSORT elements for RCTs
# ---------------------------------------------------------------------------


def _consort_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="consort-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=text)],
        full_text=text,
    )


def _consort_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_consort_elements_fires() -> None:
    from manuscript_audit.validators.core import validate_clinical_trial_consort

    ms = _consort_ms(
        "We conducted a randomized controlled trial comparing drug A vs placebo. "
        "Participants were allocated to treatment group or control group."
    )
    result = validate_clinical_trial_consort(ms, _consort_clf())
    codes = [f.code for f in result.findings]
    assert "missing-consort-elements" in codes


def test_consort_complete_no_fire() -> None:
    from manuscript_audit.validators.core import validate_clinical_trial_consort

    ms = _consort_ms(
        "We conducted a randomized controlled trial. A computer-generated randomization "
        "sequence with allocation concealment via sealed envelopes was used. "
        "The CONSORT flow diagram shows screened for eligibility: 250; allocated to "
        "receive treatment: 125; allocated to control: 125."
    )
    result = validate_clinical_trial_consort(ms, _consort_clf())
    assert result.findings == []


def test_no_rct_design_no_fire() -> None:
    from manuscript_audit.validators.core import validate_clinical_trial_consort

    ms = _consort_ms(
        "We recruited 300 participants for a cross-sectional survey study."
    )
    result = validate_clinical_trial_consort(ms, _consort_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 163 – Ecological validity
# ---------------------------------------------------------------------------


def _ecological_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="ecological-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Discussion", level=2, body=text)],
        full_text=text,
    )


def _ecological_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_ecological_validity_fires() -> None:
    from manuscript_audit.validators.core import validate_ecological_validity

    ms = _ecological_ms(
        "We conducted a laboratory experiment examining attention. "
        "Results have real-world applicability and practical implications "
        "for educational settings."
    )
    result = validate_ecological_validity(ms, _ecological_clf())
    codes = [f.code for f in result.findings]
    assert "missing-ecological-validity" in codes


def test_ecological_validity_discussed_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ecological_validity

    ms = _ecological_ms(
        "We conducted a laboratory experiment. Results have real-world applicability. "
        "However, a limitation of ecological validity should be noted: the artificial "
        "laboratory setting may not reflect naturalistic conditions."
    )
    result = validate_ecological_validity(ms, _ecological_clf())
    assert result.findings == []


def test_no_lab_study_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ecological_validity

    ms = _ecological_ms(
        "We surveyed participants in their homes about their daily habits."
    )
    result = validate_ecological_validity(ms, _ecological_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 164 – Non-peer-reviewed citations
# ---------------------------------------------------------------------------


def _media_citation_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="media-citation-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Introduction", level=2, body=text)],
        full_text=text,
    )


def test_non_peer_reviewed_citation_fires() -> None:
    from manuscript_audit.validators.core import validate_media_source_citations

    ms = _media_citation_ms(
        "According to Wikipedia (wikipedia.org/wiki/Depression), depression affects "
        "many people. A recent article in the New York Times (nytimes.com) reported "
        "similar statistics."
    )
    result = validate_media_source_citations(ms)
    codes = [f.code for f in result.findings]
    assert "non-peer-reviewed-citation" in codes


def test_peer_reviewed_citations_no_fire() -> None:
    from manuscript_audit.validators.core import validate_media_source_citations

    ms = _media_citation_ms(
        "Depression is a common mental health condition (Smith et al., 2020; "
        "Jones & Brown, 2019). See doi:10.1001/jamapsych.2020.1234 for details."
    )
    result = validate_media_source_citations(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 165 – Competing model comparison
# ---------------------------------------------------------------------------


def _model_proposal_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="model-proposal-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Results", level=2, body=text)],
        full_text=text,
    )


def _model_proposal_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_model_comparison_fires() -> None:
    from manuscript_audit.validators.core import validate_competing_model_comparison

    ms = _model_proposal_ms(
        "We propose a novel method for predicting outcomes. Our model achieves "
        "high accuracy on the test set. The proposed approach is computationally efficient."
    )
    result = validate_competing_model_comparison(ms, _model_proposal_clf())
    codes = [f.code for f in result.findings]
    assert "missing-model-comparison" in codes


def test_model_comparison_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_competing_model_comparison

    ms = _model_proposal_ms(
        "We propose a novel method for predicting outcomes. Our model outperformed "
        "baseline methods and compared favorably to existing approaches."
    )
    result = validate_competing_model_comparison(ms, _model_proposal_clf())
    assert result.findings == []


def test_no_model_proposal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_competing_model_comparison

    ms = _model_proposal_ms(
        "We analyzed survey data using descriptive statistics and correlation analysis."
    )
    result = validate_competing_model_comparison(ms, _model_proposal_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 166 – Causal language in observational studies
# ---------------------------------------------------------------------------


def _causal_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="causal-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Discussion", level=2, body=text)],
        full_text=text,
    )


def _causal_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_unsupported_causal_claim_fires() -> None:
    from manuscript_audit.validators.core import validate_causal_language

    ms = _causal_ms(
        "In this cross-sectional survey, the effect of exercise on mental health "
        "was examined. Exercise leads to lower depression scores."
    )
    result = validate_causal_language(ms, _causal_clf())
    codes = [f.code for f in result.findings]
    assert "unsupported-causal-claim" in codes


def test_causal_claim_with_framework_no_fire() -> None:
    from manuscript_audit.validators.core import validate_causal_language

    ms = _causal_ms(
        "In this cross-sectional survey, the effect of exercise on mental health "
        "was examined using a causal inference framework with a directed acyclic graph."
    )
    result = validate_causal_language(ms, _causal_clf())
    assert result.findings == []


def test_no_observational_design_no_fire() -> None:
    from manuscript_audit.validators.core import validate_causal_language

    ms = _causal_ms(
        "We conducted an RCT and found that the treatment leads to better outcomes."
    )
    result = validate_causal_language(ms, _causal_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 167 – Missing standard errors
# ---------------------------------------------------------------------------


def _std_errors_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="std-errors-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Results", level=2, body=text)],
        full_text=text,
    )


def _std_errors_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_standard_errors_fires() -> None:
    from manuscript_audit.validators.core import validate_missing_standard_errors

    ms = _std_errors_ms(
        "The regression results are shown in Table 1. Unstandardized coefficient "
        "for age was β = 0.45, p < 0.001. The standardized coefficient for "
        "education was β = 0.32."
    )
    result = validate_missing_standard_errors(ms, _std_errors_clf())
    codes = [f.code for f in result.findings]
    assert "missing-standard-errors" in codes


def test_standard_errors_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_missing_standard_errors

    ms = _std_errors_ms(
        "The regression results are shown in Table 1. The unstandardized coefficient "
        "for age was β = 0.45 (SE = 0.12), p < 0.001."
    )
    result = validate_missing_standard_errors(ms, _std_errors_clf())
    assert result.findings == []


def test_no_regression_table_no_fire() -> None:
    from manuscript_audit.validators.core import validate_missing_standard_errors

    ms = _std_errors_ms(
        "We compared group means using t-tests. Mean scores were 4.2 and 3.8."
    )
    result = validate_missing_standard_errors(ms, _std_errors_clf())
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 168 – Unhedged subjective claims
# ---------------------------------------------------------------------------


def _subjective_ms(disc_text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="subjective-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Discussion", level=2, body=disc_text)],
        full_text=disc_text,
    )


def test_unhedged_subjective_claim_fires() -> None:
    from manuscript_audit.validators.core import validate_subjective_claim_hedging

    ms = _subjective_ms(
        "It is crucial that policymakers adopt these findings immediately. "
        "This clearly demonstrates that our intervention is the most effective approach. "
        "The key finding is that early intervention undoubtedly prevents relapse."
    )
    result = validate_subjective_claim_hedging(ms)
    codes = [f.code for f in result.findings]
    assert "unhedged-subjective-claim" in codes


def test_hedged_claims_no_fire() -> None:
    from manuscript_audit.validators.core import validate_subjective_claim_hedging

    ms = _subjective_ms(
        "It is crucial that policymakers consider these findings. "
        "This clearly demonstrates a pattern that suggests our intervention "
        "may be effective. Results indicate that early intervention might help."
    )
    result = validate_subjective_claim_hedging(ms)
    assert result.findings == []


def test_no_discussion_section_no_fire() -> None:
    from manuscript_audit.validators.core import validate_subjective_claim_hedging

    ms = ParsedManuscript(
        manuscript_id="no-discussion",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Introduction", level=2, body="Background text.")],
        full_text="Background text.",
    )
    result = validate_subjective_claim_hedging(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 169 – Target population definition
# ---------------------------------------------------------------------------


def _population_ms(methods_text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="population-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Methods", level=2, body=methods_text)],
        full_text=methods_text,
    )


def _population_clf(paper_type: str = "empirical_paper") -> ManuscriptClassification:
    return ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )


def test_missing_population_definition_fires() -> None:
    from manuscript_audit.validators.core import validate_population_definition

    ms = _population_ms(
        "We conducted an online survey. Participants completed questionnaires "
        "and data were analyzed using linear regression."
    )
    result = validate_population_definition(ms, _population_clf())
    codes = [f.code for f in result.findings]
    assert "missing-population-definition" in codes


def test_population_defined_no_fire() -> None:
    from manuscript_audit.validators.core import validate_population_definition

    ms = _population_ms(
        "We recruited adults aged 18-65 with a diagnosis of depression. "
        "Inclusion criteria: primary diagnosis of MDD. Exclusion criteria: "
        "psychotic symptoms, substance use disorder."
    )
    result = validate_population_definition(ms, _population_clf())
    assert result.findings == []


def test_population_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_population_definition

    ms = _population_ms(
        "We present a theoretical framework for analyzing survey data."
    )
    result = validate_population_definition(ms, _population_clf("math_theory_paper"))
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 170 – Pilot study overclaiming
# ---------------------------------------------------------------------------


def _pilot_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="pilot-test",
        source_path="synthetic",
        source_format="markdown",
        title="T",
        sections=[Section(title="Discussion", level=2, body=text)],
        full_text=text,
    )


def test_overclaimed_pilot_study_fires() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_claims

    ms = _pilot_ms(
        "This pilot study demonstrates that the intervention is effective. "
        "Our findings definitively show that the treatment improves outcomes. "
        "Results are generalizable to the general population."
    )
    result = validate_pilot_study_claims(ms)
    codes = [f.code for f in result.findings]
    assert "overclaimed-pilot-study" in codes


def test_pilot_study_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_claims

    ms = _pilot_ms(
        "This pilot study demonstrates initial promise for the intervention. "
        "A larger randomized controlled trial is needed to confirm these findings."
    )
    result = validate_pilot_study_claims(ms)
    assert result.findings == []


def test_non_pilot_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_claims

    ms = _pilot_ms(
        "This definitive multi-center trial conclusively proves that the treatment "
        "is effective and results are broadly generalizable."
    )
    result = validate_pilot_study_claims(ms)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 171 – validate_exclusion_criteria_reporting
# ---------------------------------------------------------------------------


def _excl_ms(methods_body: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="excl-1",
        source_path="excl.md",
        source_format="markdown",
        title="Exclusion Criteria Study",
        full_text="",
        sections=[Section(title="Methods", level=2, body=methods_body)],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_exclusion_criteria_no_rationale_fires() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria_reporting

    ms, cl = _excl_ms(
        "We excluded participants who had prior diagnoses. "
        "The sample consisted of 200 adults."
    )
    result = validate_exclusion_criteria_reporting(ms, cl)
    assert any(f.code == "missing-exclusion-criteria-rationale" for f in result.findings)


def test_exclusion_criteria_with_rationale_no_fire() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria_reporting

    ms, cl = _excl_ms(
        "We excluded participants who had prior diagnoses to ensure a clean baseline. "
        "This was done to avoid confounds. Sample n=200."
    )
    result = validate_exclusion_criteria_reporting(ms, cl)
    assert result.findings == []


def test_exclusion_criteria_no_exclusion_no_fire() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria_reporting

    ms, cl = _excl_ms("All 200 adults were recruited from the community.")
    result = validate_exclusion_criteria_reporting(ms, cl)
    assert result.findings == []


def test_exclusion_criteria_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_exclusion_criteria_reporting

    ms, cl = _excl_ms(
        "We excluded participants who had prior diagnoses.",
        paper_type="math_theory_paper",
    )
    result = validate_exclusion_criteria_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 172 – validate_normal_distribution_assumption
# ---------------------------------------------------------------------------


def _norm_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="norm-1",
        source_path="norm.md",
        source_format="markdown",
        title="Normality Test Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_parametric_no_normality_fires() -> None:
    from manuscript_audit.validators.core import validate_normal_distribution_assumption

    ms, cl = _norm_ms(
        "We used an independent-samples t-test to compare group means. "
        "Results were significant (p < .05)."
    )
    result = validate_normal_distribution_assumption(ms, cl)
    assert any(f.code == "untested-normality-assumption" for f in result.findings)


def test_parametric_with_normality_test_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normal_distribution_assumption

    ms, cl = _norm_ms(
        "We used a t-test. The Shapiro-Wilk test confirmed normal distribution "
        "(W=0.98, p=.43) before analysis."
    )
    result = validate_normal_distribution_assumption(ms, cl)
    assert result.findings == []


def test_parametric_nonparametric_fallback_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normal_distribution_assumption

    ms, cl = _norm_ms(
        "Given non-parametric data, we used t-test with Wilcoxon non-parametric fallback."
    )
    result = validate_normal_distribution_assumption(ms, cl)
    assert result.findings == []


def test_no_parametric_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normal_distribution_assumption

    ms, cl = _norm_ms("We used chi-square and Fisher exact tests throughout.")
    result = validate_normal_distribution_assumption(ms, cl)
    assert result.findings == []


def test_normality_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_normal_distribution_assumption

    ms, cl = _norm_ms("We used an ANOVA to compare group means.", "math_theory_paper")
    result = validate_normal_distribution_assumption(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 173 – validate_figure_axes_labeling
# ---------------------------------------------------------------------------


def _fig_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="fig-1",
        source_path="fig.md",
        source_format="markdown",
        title="Figure Study",
        full_text=text,
        sections=[],
    )


def test_figures_no_axes_fires() -> None:
    from manuscript_audit.validators.core import validate_figure_axes_labeling

    ms = _fig_ms(
        "As shown in Figure 1, the results are clear. "
        "Figure 2 shows the distribution."
    )
    result = validate_figure_axes_labeling(ms)
    assert any(f.code == "unlabeled-figure-axes" for f in result.findings)


def test_figures_with_axes_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_axes_labeling

    ms = _fig_ms(
        "As shown in Figure 1, with x-axis representing time and y-axis "
        "representing score. Figure 2 shows the distribution."
    )
    result = validate_figure_axes_labeling(ms)
    assert result.findings == []


def test_single_figure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_axes_labeling

    ms = _fig_ms(
        "As shown in Figure 1, the results are clear. "
        "Figure 1 shows the pipeline diagram."
    )
    result = validate_figure_axes_labeling(ms)
    assert result.findings == []


def test_no_figures_no_fire() -> None:
    from manuscript_audit.validators.core import validate_figure_axes_labeling

    ms = _fig_ms("Results are reported in Table 1 and Table 2.")
    result = validate_figure_axes_labeling(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 174 – validate_duplicate_reporting
# ---------------------------------------------------------------------------


def _dup_ms(text: str) -> ParsedManuscript:
    return ParsedManuscript(
        manuscript_id="dup-1",
        source_path="dup.md",
        source_format="markdown",
        title="Duplicate Report Study",
        full_text=text,
        sections=[],
    )


def test_duplicate_reporting_fires() -> None:
    from manuscript_audit.validators.core import validate_duplicate_reporting

    ms = _dup_ms(
        "As reported in Table 1, the values are M=3.5, SD=0.8. "
        "The statistics presented in Table 1 show the same results as above."
    )
    result = validate_duplicate_reporting(ms)
    assert any(f.code == "duplicate-reporting" for f in result.findings)


def test_no_duplicate_reporting_no_fire() -> None:
    from manuscript_audit.validators.core import validate_duplicate_reporting

    ms = _dup_ms(
        "Results are presented in Table 1. Briefly, the mean score was higher "
        "in the intervention group."
    )
    result = validate_duplicate_reporting(ms)
    assert result.findings == []


def test_no_table_no_fire() -> None:
    from manuscript_audit.validators.core import validate_duplicate_reporting

    ms = _dup_ms("The mean was 3.5 and SD was 0.8 as noted above.")
    result = validate_duplicate_reporting(ms)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 175 – validate_response_rate_reporting
# ---------------------------------------------------------------------------


def _survey_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="surv-1",
        source_path="surv.md",
        source_format="markdown",
        title="Survey Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_survey_no_response_rate_fires() -> None:
    from manuscript_audit.validators.core import validate_response_rate_reporting

    ms, cl = _survey_ms(
        "We used an online survey to collect data from 300 participants. "
        "The survey was distributed via email."
    )
    result = validate_response_rate_reporting(ms, cl)
    assert any(f.code == "missing-response-rate" for f in result.findings)


def test_survey_with_response_rate_no_fire() -> None:
    from manuscript_audit.validators.core import validate_response_rate_reporting

    ms, cl = _survey_ms(
        "We used an online survey. The response rate was 72% (300/417). "
        "Invitees were contacted by email."
    )
    result = validate_response_rate_reporting(ms, cl)
    assert result.findings == []


def test_non_survey_no_fire() -> None:
    from manuscript_audit.validators.core import validate_response_rate_reporting

    ms, cl = _survey_ms(
        "We recruited participants from local clinics for an in-person experiment."
    )
    result = validate_response_rate_reporting(ms, cl)
    assert result.findings == []


def test_survey_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_response_rate_reporting

    ms, cl = _survey_ms(
        "We used an online questionnaire to explore patterns.",
        paper_type="math_theory_paper",
    )
    result = validate_response_rate_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 176 – validate_longitudinal_attrition_bias
# ---------------------------------------------------------------------------


def _long_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="long-1",
        source_path="long.md",
        source_format="markdown",
        title="Longitudinal Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_longitudinal_no_attrition_fires() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_attrition_bias

    ms, cl = _long_ms(
        "This longitudinal cohort study tracked participants over 5 years. "
        "Follow-up assessments were conducted at 12, 24, and 60 months."
    )
    result = validate_longitudinal_attrition_bias(ms, cl)
    assert any(f.code == "missing-attrition-bias-analysis" for f in result.findings)


def test_longitudinal_with_attrition_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_attrition_bias

    ms, cl = _long_ms(
        "In this longitudinal study, attrition bias analysis revealed "
        "that dropouts did not differ from completers on key variables. "
        "Data were missing at random (MAR)."
    )
    result = validate_longitudinal_attrition_bias(ms, cl)
    assert result.findings == []


def test_non_longitudinal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_attrition_bias

    ms, cl = _long_ms(
        "This cross-sectional study measured all variables at one time point."
    )
    result = validate_longitudinal_attrition_bias(ms, cl)
    assert result.findings == []


def test_longitudinal_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_attrition_bias

    ms, cl = _long_ms(
        "The longitudinal growth model was analyzed.", "math_theory_paper"
    )
    result = validate_longitudinal_attrition_bias(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 177 – validate_continuous_variable_dichotomization
# ---------------------------------------------------------------------------


def _dichot_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="dichot-1",
        source_path="dichot.md",
        source_format="markdown",
        title="Dichotomization Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_median_split_no_justification_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_continuous_variable_dichotomization,
    )

    ms, cl = _dichot_ms(
        "Depression scores were dichotomized using a median split to create "
        "high vs. low groups for subsequent analysis."
    )
    result = validate_continuous_variable_dichotomization(ms, cl)
    assert any(f.code == "unjustified-dichotomization" for f in result.findings)


def test_dichotomize_clinical_cutoff_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_continuous_variable_dichotomization,
    )

    ms, cl = _dichot_ms(
        "Scores were dichotomized using the clinically validated cutoff of >=10 "
        "established by prior research for this diagnostic threshold."
    )
    result = validate_continuous_variable_dichotomization(ms, cl)
    assert result.findings == []


def test_no_dichotomization_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_continuous_variable_dichotomization,
    )

    ms, cl = _dichot_ms(
        "Depression scores were retained as continuous variables in all regression models."
    )
    result = validate_continuous_variable_dichotomization(ms, cl)
    assert result.findings == []


def test_dichotomize_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_continuous_variable_dichotomization,
    )

    ms, cl = _dichot_ms(
        "We dichotomized the variable for the theoretical illustration.",
        "math_theory_paper",
    )
    result = validate_continuous_variable_dichotomization(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 178 – validate_outcome_measure_validation
# ---------------------------------------------------------------------------


def _measure_ms(methods_body: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="meas-1",
        source_path="meas.md",
        source_format="markdown",
        title="Outcome Measure Study",
        full_text="",
        sections=[Section(title="Methods", level=2, body=methods_body)],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_outcome_no_validity_fires() -> None:
    from manuscript_audit.validators.core import validate_outcome_measure_validation

    ms, cl = _measure_ms(
        "The primary outcome measure was the PHQ-9 scale used to assess depression."
    )
    result = validate_outcome_measure_validation(ms, cl)
    assert any(f.code == "missing-measure-validity" for f in result.findings)


def test_outcome_with_validity_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outcome_measure_validation

    ms, cl = _measure_ms(
        "The PHQ-9 was used as the primary outcome measure. "
        "This validated scale has demonstrated high internal consistency "
        "(Cronbach alpha = 0.89) and good test-retest reliability."
    )
    result = validate_outcome_measure_validation(ms, cl)
    assert result.findings == []


def test_no_measure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outcome_measure_validation

    ms, cl = _measure_ms(
        "Participants completed a 30-minute behavioral task. No scales were used."
    )
    result = validate_outcome_measure_validation(ms, cl)
    assert result.findings == []


def test_measure_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outcome_measure_validation

    ms, cl = _measure_ms(
        "The primary outcome measure was derived from the theoretical model.",
        "math_theory_paper",
    )
    result = validate_outcome_measure_validation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 179 – validate_outlier_handling_disclosure
# ---------------------------------------------------------------------------


def _outlier_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="out-1",
        source_path="out.md",
        source_format="markdown",
        title="Outlier Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_outlier_no_handling_fires() -> None:
    from manuscript_audit.validators.core import validate_outlier_handling_disclosure

    ms, cl = _outlier_ms(
        "Data screening revealed several outliers in the performance scores. "
        "These were noted but the analysis proceeded as planned."
    )
    result = validate_outlier_handling_disclosure(ms, cl)
    assert any(f.code == "missing-outlier-handling" for f in result.findings)


def test_outlier_removal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_handling_disclosure

    ms, cl = _outlier_ms(
        "Three outliers were removed based on z-scores > 3.0. "
        "Outliers were identified and removed prior to the main analysis."
    )
    result = validate_outlier_handling_disclosure(ms, cl)
    assert result.findings == []


def test_no_outlier_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_handling_disclosure

    ms, cl = _outlier_ms(
        "All data points were retained for the analysis. "
        "Descriptive statistics were inspected before the main analysis."
    )
    result = validate_outlier_handling_disclosure(ms, cl)
    assert result.findings == []


def test_outlier_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_handling_disclosure

    ms, cl = _outlier_ms(
        "Outlier detection algorithms are described theoretically.", "math_theory_paper"
    )
    result = validate_outlier_handling_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 180 – validate_main_effect_confidence_interval
# ---------------------------------------------------------------------------


def _main_effect_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="me-1",
        source_path="me.md",
        source_format="markdown",
        title="Main Effect Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_main_effect_no_ci_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_main_effect_confidence_interval,
    )

    ms, cl = _main_effect_ms(
        "The main effect of condition was significant (F(1,98)=12.3, p=.001). "
        "The primary outcome showed improved scores in the treatment group."
    )
    result = validate_main_effect_confidence_interval(ms, cl)
    assert any(f.code == "missing-main-effect-ci" for f in result.findings)


def test_main_effect_with_ci_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_main_effect_confidence_interval,
    )

    ms, cl = _main_effect_ms(
        "The main effect of condition was significant (F=12.3, p=.001, "
        "95% CI [0.15, 0.45]). Confidence intervals are provided for all effects."
    )
    result = validate_main_effect_confidence_interval(ms, cl)
    assert result.findings == []


def test_no_main_effect_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_main_effect_confidence_interval,
    )

    ms, cl = _main_effect_ms(
        "Exploratory analyses revealed associations between variables."
    )
    result = validate_main_effect_confidence_interval(ms, cl)
    assert result.findings == []


def test_main_effect_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_main_effect_confidence_interval,
    )

    ms, cl = _main_effect_ms(
        "The main effect in this theoretical model is derived analytically.",
        "math_theory_paper",
    )
    result = validate_main_effect_confidence_interval(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 181 – validate_covariate_justification
# ---------------------------------------------------------------------------


def _cov_ms(methods_body: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="cov-1",
        source_path="cov.md",
        source_format="markdown",
        title="Covariate Study",
        full_text="",
        sections=[Section(title="Methods", level=2, body=methods_body)],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_covariate_no_justification_fires() -> None:
    from manuscript_audit.validators.core import validate_covariate_justification

    ms, cl = _cov_ms(
        "Age and gender were included as covariates in all regression models. "
        "ANCOVA was used to control for baseline differences."
    )
    result = validate_covariate_justification(ms, cl)
    assert any(f.code == "missing-covariate-justification" for f in result.findings)


def test_covariate_with_justification_no_fire() -> None:
    from manuscript_audit.validators.core import validate_covariate_justification

    ms, cl = _cov_ms(
        "Age was included as a covariate because prior research indicates it is a "
        "known confounder of cognitive performance. "
        "ANCOVA was used to control for baseline differences."
    )
    result = validate_covariate_justification(ms, cl)
    assert result.findings == []


def test_no_covariates_no_fire() -> None:
    from manuscript_audit.validators.core import validate_covariate_justification

    ms, cl = _cov_ms(
        "Independent samples t-tests were used to compare group means. "
        "No additional variables were included in the model."
    )
    result = validate_covariate_justification(ms, cl)
    assert result.findings == []


def test_covariate_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_covariate_justification

    ms, cl = _cov_ms(
        "Covariates are included in the theoretical regression specification.",
        "math_theory_paper",
    )
    result = validate_covariate_justification(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 182 – validate_gender_sex_conflation
# ---------------------------------------------------------------------------


def _gs_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="gs-1",
        source_path="gs.md",
        source_format="markdown",
        title="Gender Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_gender_sex_conflation_fires() -> None:
    from manuscript_audit.validators.core import validate_gender_sex_conflation

    ms, cl = _gs_ms(
        "Participant gender was male or female. "
        "Gender was used as a covariate in the regression model."
    )
    result = validate_gender_sex_conflation(ms, cl)
    assert any(f.code == "gender-sex-conflation" for f in result.findings)


def test_gender_sex_distinct_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gender_sex_conflation

    ms, cl = _gs_ms(
        "We measured biological sex (male/female) and gender identity separately. "
        "Biological sex and gender identity are distinct constructs in this study."
    )
    result = validate_gender_sex_conflation(ms, cl)
    assert result.findings == []


def test_gender_only_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gender_sex_conflation

    ms, cl = _gs_ms(
        "Participants were diverse in gender, age, and educational background."
    )
    result = validate_gender_sex_conflation(ms, cl)
    assert result.findings == []


def test_gender_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gender_sex_conflation

    ms, cl = _gs_ms(
        "Gender was male or female in this theoretical example.",
        "math_theory_paper",
    )
    result = validate_gender_sex_conflation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 183 – validate_multicollinearity_reporting
# ---------------------------------------------------------------------------


def _multi_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="multi-1",
        source_path="multi.md",
        source_format="markdown",
        title="Regression Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_regression_no_vif_fires() -> None:
    from manuscript_audit.validators.core import validate_multicollinearity_reporting

    ms, cl = _multi_ms(
        "Multiple linear regression was used to predict depression from stress, "
        "social support, and coping strategies."
    )
    result = validate_multicollinearity_reporting(ms, cl)
    assert any(f.code == "missing-multicollinearity-check" for f in result.findings)


def test_regression_with_vif_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multicollinearity_reporting

    ms, cl = _multi_ms(
        "Multiple regression was conducted. Variance inflation factors (VIF < 3.0) "
        "indicated no multicollinearity among predictors."
    )
    result = validate_multicollinearity_reporting(ms, cl)
    assert result.findings == []


def test_no_regression_multicollinearity_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multicollinearity_reporting

    ms, cl = _multi_ms(
        "Chi-square tests and Wilcoxon signed-rank tests were used throughout."
    )
    result = validate_multicollinearity_reporting(ms, cl)
    assert result.findings == []


def test_multicollinearity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multicollinearity_reporting

    ms, cl = _multi_ms(
        "Multicollinearity in regression is analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_multicollinearity_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 184 – validate_control_group_description
# ---------------------------------------------------------------------------


def _rct_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="rct-1",
        source_path="rct.md",
        source_format="markdown",
        title="RCT Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_rct_no_control_type_fires() -> None:
    from manuscript_audit.validators.core import validate_control_group_description

    ms, cl = _rct_ms(
        "Participants were randomly assigned to treatment or control conditions. "
        "This randomized controlled trial evaluated the intervention effect."
    )
    result = validate_control_group_description(ms, cl)
    assert any(f.code == "missing-control-group-type" for f in result.findings)


def test_rct_with_placebo_no_fire() -> None:
    from manuscript_audit.validators.core import validate_control_group_description

    ms, cl = _rct_ms(
        "This RCT compared CBT (treatment) to a placebo control condition. "
        "Participants were randomly assigned to treatment arm vs. control."
    )
    result = validate_control_group_description(ms, cl)
    assert result.findings == []


def test_non_rct_observational_no_fire() -> None:
    from manuscript_audit.validators.core import validate_control_group_description

    ms, cl = _rct_ms(
        "This observational cohort study followed participants for 2 years."
    )
    result = validate_control_group_description(ms, cl)
    assert result.findings == []


def test_rct_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_control_group_description

    ms, cl = _rct_ms(
        "The RCT design is analyzed in this theoretical framework.",
        "math_theory_paper",
    )
    result = validate_control_group_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 185 – validate_heteroscedasticity_testing
# ---------------------------------------------------------------------------


def _hetero_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="hetero-1",
        source_path="hetero.md",
        source_format="markdown",
        title="Heteroscedasticity Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_ols_no_hetero_check_fires() -> None:
    from manuscript_audit.validators.core import validate_heteroscedasticity_testing

    ms, cl = _hetero_ms(
        "Ordinary least squares regression was used to estimate the model. "
        "Predictors included age, income, and education."
    )
    result = validate_heteroscedasticity_testing(ms, cl)
    assert any(f.code == "missing-heteroscedasticity-check" for f in result.findings)


def test_ols_with_breusch_pagan_no_fire() -> None:
    from manuscript_audit.validators.core import validate_heteroscedasticity_testing

    ms, cl = _hetero_ms(
        "Multiple linear regression was conducted using OLS. "
        "The Breusch-Pagan test confirmed homoscedasticity of residuals."
    )
    result = validate_heteroscedasticity_testing(ms, cl)
    assert result.findings == []


def test_no_ols_no_fire() -> None:
    from manuscript_audit.validators.core import validate_heteroscedasticity_testing

    ms, cl = _hetero_ms(
        "A Bayesian hierarchical model was used with MCMC estimation."
    )
    result = validate_heteroscedasticity_testing(ms, cl)
    assert result.findings == []


def test_hetero_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_heteroscedasticity_testing

    ms, cl = _hetero_ms(
        "Heteroscedasticity in OLS regression is analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_heteroscedasticity_testing(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 186 – validate_interaction_effect_interpretation
# ---------------------------------------------------------------------------


def _inter_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="inter-1",
        source_path="inter.md",
        source_format="markdown",
        title="Interaction Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_interaction_no_probing_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_interaction_effect_interpretation,
    )

    ms, cl = _inter_ms(
        "A significant two-way interaction was found between stress and support "
        "(F(1,98)=7.4, p=.008). The interaction effect was significant."
    )
    result = validate_interaction_effect_interpretation(ms, cl)
    assert any(f.code == "missing-interaction-probing" for f in result.findings)


def test_interaction_with_simple_slopes_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_interaction_effect_interpretation,
    )

    ms, cl = _inter_ms(
        "A significant interaction was found (F=7.4). "
        "Simple slope analysis revealed that at high levels of support, "
        "stress was not related to depression."
    )
    result = validate_interaction_effect_interpretation(ms, cl)
    assert result.findings == []


def test_no_interaction_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_interaction_effect_interpretation,
    )

    ms, cl = _inter_ms(
        "Main effects of stress and social support were both significant. "
        "No interaction terms were included."
    )
    result = validate_interaction_effect_interpretation(ms, cl)
    assert result.findings == []


def test_interaction_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_interaction_effect_interpretation,
    )

    ms, cl = _inter_ms(
        "Interaction effects in regression are discussed theoretically.",
        "math_theory_paper",
    )
    result = validate_interaction_effect_interpretation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 187 – validate_post_hoc_framing
# ---------------------------------------------------------------------------


def _posthoc_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="ph-1",
        source_path="ph.md",
        source_format="markdown",
        title="Post-hoc Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_post_hoc_not_labelled_fires() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_framing

    ms, cl = _posthoc_ms(
        "Additional analyses revealed a significant association between "
        "age and outcome. We also explored whether education moderated this effect."
    )
    result = validate_post_hoc_framing(ms, cl)
    assert any(f.code == "post-hoc-not-labelled" for f in result.findings)


def test_post_hoc_labelled_exploratory_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_framing

    ms, cl = _posthoc_ms(
        "Additional exploratory analyses revealed associations. "
        "These results are exploratory and should be considered hypothesis-generating. "
        "They must be confirmed in future studies."
    )
    result = validate_post_hoc_framing(ms, cl)
    assert result.findings == []


def test_no_post_hoc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_framing

    ms, cl = _posthoc_ms(
        "The primary hypotheses were supported. "
        "Results are consistent with theoretical predictions."
    )
    result = validate_post_hoc_framing(ms, cl)
    assert result.findings == []


def test_post_hoc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_framing

    ms, cl = _posthoc_ms(
        "We also explored additional properties of the model theoretically.",
        "math_theory_paper",
    )
    result = validate_post_hoc_framing(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 188 – validate_multiple_comparison_correction
# ---------------------------------------------------------------------------


def _mcc_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="mcc-1",
        source_path="mcc.md",
        source_format="markdown",
        title="Multiple Comparisons Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_multiple_comparisons_no_correction_fires() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc_ms(
        "We conducted 12 tests comparing group differences across all outcomes. "
        "Multiple comparisons were made across all outcome variables."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert any(f.code == "missing-multiple-comparison-correction" for f in result.findings)


def test_multiple_comparisons_with_bonferroni_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc_ms(
        "Multiple comparisons were addressed using Bonferroni correction. "
        "The adjusted alpha was set at .004 (= .05/12)."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


def test_single_comparison_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc_ms(
        "We tested a single primary hypothesis using an independent t-test."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


def test_mcc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc_ms(
        "Multiple comparisons are analyzed theoretically.", "math_theory_paper"
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 189 – validate_publication_bias_statement
# ---------------------------------------------------------------------------


def _meta_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="meta-1",
        source_path="meta.md",
        source_format="markdown",
        title="Meta-Analysis",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_meta_analysis_no_pub_bias_fires() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_statement

    ms, cl = _meta_ms(
        "This meta-analysis pooled effect sizes from 28 studies using a "
        "random-effects model. The forest plot showed consistent effects."
    )
    result = validate_publication_bias_statement(ms, cl)
    assert any(
        f.code == "missing-publication-bias-statement" for f in result.findings
    )


def test_meta_analysis_with_funnel_plot_no_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_statement

    ms, cl = _meta_ms(
        "This meta-analysis used a random-effects model. "
        "Publication bias was assessed using a funnel plot and Egger's test, "
        "which showed no evidence of bias (p=.42)."
    )
    result = validate_publication_bias_statement(ms, cl)
    assert result.findings == []


def test_non_meta_no_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_statement

    ms, cl = _meta_ms(
        "This experimental study used a between-subjects design."
    )
    result = validate_publication_bias_statement(ms, cl)
    assert result.findings == []


def test_meta_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_statement

    ms, cl = _meta_ms(
        "The meta-analysis framework is analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_publication_bias_statement(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 190 – validate_degrees_of_freedom_reporting
# ---------------------------------------------------------------------------


def _df_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="df-1",
        source_path="df.md",
        source_format="markdown",
        title="Degrees of Freedom Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_stats_no_df_fires() -> None:
    from manuscript_audit.validators.core import validate_degrees_of_freedom_reporting

    ms, cl = _df_ms(
        "The t-test revealed a significant difference (t = 3.45, p < .001). "
        "An F = 12.4, p=.001 was also found."
    )
    result = validate_degrees_of_freedom_reporting(ms, cl)
    assert any(f.code == "missing-degrees-of-freedom" for f in result.findings)


def test_stats_with_df_no_fire() -> None:
    from manuscript_audit.validators.core import validate_degrees_of_freedom_reporting

    ms, cl = _df_ms(
        "The t-test revealed a significant difference (t(98) = 3.45, p < .001). "
        "ANOVA yielded F(2, 147) = 12.4, p=.001."
    )
    result = validate_degrees_of_freedom_reporting(ms, cl)
    assert result.findings == []


def test_no_stat_tests_no_fire() -> None:
    from manuscript_audit.validators.core import validate_degrees_of_freedom_reporting

    ms, cl = _df_ms(
        "Descriptive statistics are presented in Table 1. "
        "Means and standard deviations are reported."
    )
    result = validate_degrees_of_freedom_reporting(ms, cl)
    assert result.findings == []


def test_df_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_degrees_of_freedom_reporting

    ms, cl = _df_ms(
        "Degrees of freedom in t-tests are analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_degrees_of_freedom_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 191 – validate_power_analysis_reporting
# ---------------------------------------------------------------------------


def _power_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="pow-1",
        source_path="pow.md",
        source_format="markdown",
        title="Power Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_sample_size_no_power_fires() -> None:
    from manuscript_audit.validators.core import validate_power_analysis_reporting

    ms, cl = _power_ms(
        "A total of 120 participants were recruited for this study. "
        "n=120 adults completed the survey."
    )
    result = validate_power_analysis_reporting(ms, cl)
    assert any(f.code == "missing-power-analysis" for f in result.findings)


def test_sample_size_with_power_no_fire() -> None:
    from manuscript_audit.validators.core import validate_power_analysis_reporting

    ms, cl = _power_ms(
        "A total of n=120 participants were recruited. "
        "Sample size was determined using a priori power analysis (G*Power) "
        "with 80% power to detect a medium effect."
    )
    result = validate_power_analysis_reporting(ms, cl)
    assert result.findings == []


def test_no_sample_size_no_fire() -> None:
    from manuscript_audit.validators.core import validate_power_analysis_reporting

    ms, cl = _power_ms(
        "Data were collected from the national registry over a 5-year period."
    )
    result = validate_power_analysis_reporting(ms, cl)
    assert result.findings == []


def test_power_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_power_analysis_reporting

    ms, cl = _power_ms(
        "A sample of n=50 was used in the theoretical illustration.",
        "math_theory_paper",
    )
    result = validate_power_analysis_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 192 – validate_demographic_description
# ---------------------------------------------------------------------------


def _demog_ms(methods_body: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="dem-1",
        source_path="dem.md",
        source_format="markdown",
        title="Demographic Study",
        full_text="",
        sections=[Section(title="Methods", level=2, body=methods_body)],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_participants_no_demographics_fires() -> None:
    from manuscript_audit.validators.core import validate_demographic_description

    ms, cl = _demog_ms(
        "Participants were recruited from the university community. "
        "A total of 150 participants completed the study."
    )
    result = validate_demographic_description(ms, cl)
    assert any(f.code == "missing-demographic-description" for f in result.findings)


def test_participants_with_demographics_no_fire() -> None:
    from manuscript_audit.validators.core import validate_demographic_description

    ms, cl = _demog_ms(
        "Participants were 150 adults (mean age = 32.4 years, 62% female). "
        "The sample was recruited from the university community."
    )
    result = validate_demographic_description(ms, cl)
    assert result.findings == []


def test_no_participants_no_fire() -> None:
    from manuscript_audit.validators.core import validate_demographic_description

    ms, cl = _demog_ms(
        "Data were extracted from the national census database for analysis."
    )
    result = validate_demographic_description(ms, cl)
    assert result.findings == []


def test_demographics_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_demographic_description

    ms, cl = _demog_ms(
        "The participants in this theoretical example were 50 adults.",
        "math_theory_paper",
    )
    result = validate_demographic_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 193 – validate_randomization_procedure
# ---------------------------------------------------------------------------


def _rand_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="rand-1",
        source_path="rand.md",
        source_format="markdown",
        title="Randomization Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_random_assign_no_method_fires() -> None:
    from manuscript_audit.validators.core import validate_randomization_procedure

    ms, cl = _rand_ms(
        "Participants were randomly assigned to one of two conditions. "
        "Group assignment was random."
    )
    result = validate_randomization_procedure(ms, cl)
    assert any(f.code == "missing-randomization-procedure" for f in result.findings)


def test_random_assign_with_method_no_fire() -> None:
    from manuscript_audit.validators.core import validate_randomization_procedure

    ms, cl = _rand_ms(
        "Participants were randomly assigned using computer-generated randomization. "
        "Block randomization with allocation concealment was used."
    )
    result = validate_randomization_procedure(ms, cl)
    assert result.findings == []


def test_no_randomization_no_fire() -> None:
    from manuscript_audit.validators.core import validate_randomization_procedure

    ms, cl = _rand_ms(
        "This observational study followed participants without any assignment."
    )
    result = validate_randomization_procedure(ms, cl)
    assert result.findings == []


def test_randomization_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_randomization_procedure

    ms, cl = _rand_ms(
        "Participants were randomly assigned in this theoretical simulation.",
        "math_theory_paper",
    )
    result = validate_randomization_procedure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 194 – validate_generalizability_caveat
# ---------------------------------------------------------------------------


def _gen_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="gen-1",
        source_path="gen.md",
        source_format="markdown",
        title="Generalizability Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_strong_generalize_no_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_generalizability_caveat

    ms, cl = _gen_ms(
        "These results can be generalized to all populations of adults. "
        "The findings are broadly applicable."
    )
    result = validate_generalizability_caveat(ms, cl)
    assert any(f.code == "overclaimed-generalizability" for f in result.findings)


def test_generalize_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_generalizability_caveat

    ms, cl = _gen_ms(
        "These results can be generalized to the general population with caution. "
        "Limitations of this study include the restricted sample."
    )
    result = validate_generalizability_caveat(ms, cl)
    assert result.findings == []


def test_no_strong_generalize_no_fire() -> None:
    from manuscript_audit.validators.core import validate_generalizability_caveat

    ms, cl = _gen_ms(
        "Results should be interpreted cautiously given the sample characteristics."
    )
    result = validate_generalizability_caveat(ms, cl)
    assert result.findings == []


def test_generalize_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_generalizability_caveat

    ms, cl = _gen_ms(
        "Results can be generalized to all cases in this mathematical proof.",
        "math_theory_paper",
    )
    result = validate_generalizability_caveat(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 195 – validate_software_version_reporting
# ---------------------------------------------------------------------------


def _sw_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="sw-1",
        source_path="sw.md",
        source_format="markdown",
        title="Software Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_software_no_version_fires() -> None:
    from manuscript_audit.validators.core import validate_software_version_reporting

    ms, cl = _sw_ms(
        "All analyses were conducted in R. "
        "Python was used for data preprocessing."
    )
    result = validate_software_version_reporting(ms, cl)
    assert any(f.code == "missing-software-version" for f in result.findings)


def test_software_version_present_no_fire() -> None:
    from manuscript_audit.validators.core import validate_software_version_reporting

    ms, cl = _sw_ms(
        "All analyses were conducted in R version 4.3.2. "
        "Python 3.11 was used for data preprocessing."
    )
    result = validate_software_version_reporting(ms, cl)
    assert result.findings == []


def test_no_software_use_no_fire() -> None:
    from manuscript_audit.validators.core import validate_software_version_reporting

    ms, cl = _sw_ms(
        "Descriptive statistics are presented in Table 1."
    )
    result = validate_software_version_reporting(ms, cl)
    assert result.findings == []


def test_software_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_software_version_reporting

    ms, cl = _sw_ms(
        "All analyses were conducted in R for the theoretical example.",
        "math_theory_paper",
    )
    result = validate_software_version_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 196 – validate_ethics_approval_statement
# ---------------------------------------------------------------------------


def _ethics_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="eth-1",
        source_path="eth.md",
        source_format="markdown",
        title="Ethics Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_human_subjects_no_ethics_fires() -> None:
    from manuscript_audit.validators.core import validate_ethics_approval_statement

    ms, cl = _ethics_ms(
        "A total of 200 participants completed the study. "
        "Subjects were recruited from the community."
    )
    result = validate_ethics_approval_statement(ms, cl)
    assert any(f.code == "missing-ethics-approval" for f in result.findings)


def test_human_subjects_with_irb_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_approval_statement

    ms, cl = _ethics_ms(
        "A total of 200 participants completed the study. "
        "The IRB of the University approved this protocol. "
        "All participants provided informed consent."
    )
    result = validate_ethics_approval_statement(ms, cl)
    assert result.findings == []


def test_no_human_subjects_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_approval_statement

    ms, cl = _ethics_ms(
        "Data were extracted from publicly available administrative records."
    )
    result = validate_ethics_approval_statement(ms, cl)
    assert result.findings == []


def test_ethics_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ethics_approval_statement

    ms, cl = _ethics_ms(
        "The theoretical participants in this model require no consent.",
        "math_theory_paper",
    )
    result = validate_ethics_approval_statement(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 197 – validate_prisma_reporting
# ---------------------------------------------------------------------------


def _sys_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="sys-1",
        source_path="sys.md",
        source_format="markdown",
        title="Systematic Review",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_systematic_review_no_prisma_fires() -> None:
    from manuscript_audit.validators.core import validate_prisma_reporting

    ms, cl = _sys_ms(
        "A systematic review was conducted. PubMed was searched for relevant studies. "
        "A total of 28 studies were identified and eligible studies were included."
    )
    result = validate_prisma_reporting(ms, cl)
    assert any(f.code == "missing-prisma-elements" for f in result.findings)


def test_systematic_review_with_prisma_no_fire() -> None:
    from manuscript_audit.validators.core import validate_prisma_reporting

    ms, cl = _sys_ms(
        "A systematic review was conducted following PRISMA guidelines. "
        "PubMed was searched. A flow diagram depicting the screening process "
        "is included. Inclusion and exclusion criteria were applied."
    )
    result = validate_prisma_reporting(ms, cl)
    assert result.findings == []


def test_non_systematic_review_no_fire() -> None:
    from manuscript_audit.validators.core import validate_prisma_reporting

    ms, cl = _sys_ms(
        "This cross-sectional study used survey data from 300 adults."
    )
    result = validate_prisma_reporting(ms, cl)
    assert result.findings == []


def test_prisma_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_prisma_reporting

    ms, cl = _sys_ms(
        "A systematic review of the theoretical literature was conducted.",
        "math_theory_paper",
    )
    result = validate_prisma_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 198 – validate_mediation_analysis_transparency
# ---------------------------------------------------------------------------


def _med_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="med-1",
        source_path="med.md",
        source_format="markdown",
        title="Mediation Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_mediation_no_bootstrap_fires() -> None:
    from manuscript_audit.validators.core import validate_mediation_analysis_transparency

    ms, cl = _med_ms(
        "Mediation analysis revealed that anxiety mediated the relationship between "
        "stress and depression. The indirect effect was significant."
    )
    result = validate_mediation_analysis_transparency(ms, cl)
    assert any(f.code == "missing-mediation-bootstrap" for f in result.findings)


def test_mediation_with_bootstrap_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediation_analysis_transparency

    ms, cl = _med_ms(
        "Mediation analysis using Hayes PROCESS macro with bootstrapping (5000 samples) "
        "revealed a significant indirect effect (95% CI [0.12, 0.48])."
    )
    result = validate_mediation_analysis_transparency(ms, cl)
    assert result.findings == []


def test_no_mediation_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediation_analysis_transparency

    ms, cl = _med_ms(
        "Main effects of stress and social support were both significant."
    )
    result = validate_mediation_analysis_transparency(ms, cl)
    assert result.findings == []


def test_mediation_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediation_analysis_transparency

    ms, cl = _med_ms(
        "Mediation effects are analyzed theoretically in this paper.",
        "math_theory_paper",
    )
    result = validate_mediation_analysis_transparency(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 199 – validate_latent_variable_model_fit
# ---------------------------------------------------------------------------


def _cfa_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="cfa-1",
        source_path="cfa.md",
        source_format="markdown",
        title="CFA Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_cfa_no_fit_indices_fires() -> None:
    from manuscript_audit.validators.core import validate_latent_variable_model_fit

    ms, cl = _cfa_ms(
        "Confirmatory factor analysis (CFA) was conducted to examine the factor "
        "structure. The measurement model was tested using SEM."
    )
    result = validate_latent_variable_model_fit(ms, cl)
    assert any(f.code == "missing-model-fit-indices" for f in result.findings)


def test_cfa_with_fit_indices_no_fire() -> None:
    from manuscript_audit.validators.core import validate_latent_variable_model_fit

    ms, cl = _cfa_ms(
        "Confirmatory factor analysis showed good model fit (CFI = 0.96, "
        "RMSEA = 0.048, SRMR = 0.05). The measurement model was tested using SEM."
    )
    result = validate_latent_variable_model_fit(ms, cl)
    assert result.findings == []


def test_no_cfa_no_fire() -> None:
    from manuscript_audit.validators.core import validate_latent_variable_model_fit

    ms, cl = _cfa_ms(
        "Multiple regression analysis was used to predict the outcome."
    )
    result = validate_latent_variable_model_fit(ms, cl)
    assert result.findings == []


def test_cfa_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_latent_variable_model_fit

    ms, cl = _cfa_ms(
        "CFA model fit indices are analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_latent_variable_model_fit(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 200 – validate_pilot_study_disclosure
# ---------------------------------------------------------------------------


def _pilot_disc_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="pd-1",
        source_path="pd.md",
        source_format="markdown",
        title="Pilot Disclosure Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_pilot_based_size_no_disclosure_fires() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_disclosure

    ms, cl = _pilot_disc_ms(
        "Sample size was based on a pilot study that estimated the effect size. "
        "The effect size from pilot results informed the power calculation."
    )
    result = validate_pilot_study_disclosure(ms, cl)
    assert any(f.code == "undisclosed-pilot-study" for f in result.findings)


def test_pilot_based_size_with_disclosure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_disclosure

    ms, cl = _pilot_disc_ms(
        "Sample size was based on a pilot study (pilot study was published; "
        "see Appendix A for pilot data). The effect size from pilot results "
        "informed the a priori power analysis."
    )
    result = validate_pilot_study_disclosure(ms, cl)
    assert result.findings == []


def test_no_pilot_size_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_disclosure

    ms, cl = _pilot_disc_ms(
        "Sample size was determined using G*Power with 80% power and alpha=.05."
    )
    result = validate_pilot_study_disclosure(ms, cl)
    assert result.findings == []


def test_pilot_disclosure_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_disclosure

    ms, cl = _pilot_disc_ms(
        "Sample size was based on a pilot study for this simulation.",
        "math_theory_paper",
    )
    result = validate_pilot_study_disclosure(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 201 – validate_autocorrelation_check
# ---------------------------------------------------------------------------


def _ts_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="ts-1",
        source_path="ts.md",
        source_format="markdown",
        title="Time Series Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_time_series_no_autocorr_fires() -> None:
    from manuscript_audit.validators.core import validate_autocorrelation_check

    ms, cl = _ts_ms(
        "An autoregressive AR(1) model was fitted to the time series data. "
        "Lagged dependent variables were included in the panel regression."
    )
    result = validate_autocorrelation_check(ms, cl)
    assert any(f.code == "missing-autocorrelation-check" for f in result.findings)


def test_time_series_with_durbinwatson_no_fire() -> None:
    from manuscript_audit.validators.core import validate_autocorrelation_check

    ms, cl = _ts_ms(
        "An AR(1) model was fitted. The Durbin-Watson statistic (d=1.98) "
        "indicated no serial correlation in the residuals."
    )
    result = validate_autocorrelation_check(ms, cl)
    assert result.findings == []


def test_no_time_series_no_fire() -> None:
    from manuscript_audit.validators.core import validate_autocorrelation_check

    ms, cl = _ts_ms(
        "A cross-sectional survey was conducted at one time point."
    )
    result = validate_autocorrelation_check(ms, cl)
    assert result.findings == []


def test_autocorr_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_autocorrelation_check

    ms, cl = _ts_ms(
        "The time series ARIMA model is analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_autocorrelation_check(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 202 – validate_mixed_methods_integration
# ---------------------------------------------------------------------------


def _mm_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="mm-1",
        source_path="mm.md",
        source_format="markdown",
        title="Mixed Methods Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_mixed_methods_no_integration_fires() -> None:
    from manuscript_audit.validators.core import validate_mixed_methods_integration

    ms, cl = _mm_ms(
        "This mixed-methods study combined qualitative interviews with "
        "quantitative survey data to examine patient experiences."
    )
    result = validate_mixed_methods_integration(ms, cl)
    assert any(f.code == "missing-mixed-methods-integration" for f in result.findings)


def test_mixed_methods_with_integration_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mixed_methods_integration

    ms, cl = _mm_ms(
        "This mixed-methods study used triangulation. "
        "Qualitative findings illuminated the quantitative results, "
        "providing context for the survey data patterns."
    )
    result = validate_mixed_methods_integration(ms, cl)
    assert result.findings == []


def test_no_mixed_methods_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mixed_methods_integration

    ms, cl = _mm_ms(
        "This quantitative survey study used regression analysis."
    )
    result = validate_mixed_methods_integration(ms, cl)
    assert result.findings == []


def test_mixed_methods_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mixed_methods_integration

    ms, cl = _mm_ms(
        "Mixed-methods frameworks are analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_mixed_methods_integration(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 203 – validate_qualitative_rigor_reporting
# ---------------------------------------------------------------------------


def _qual_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="qual-1",
        source_path="qual.md",
        source_format="markdown",
        title="Qualitative Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_qualitative_no_rigor_fires() -> None:
    from manuscript_audit.validators.core import validate_qualitative_rigor_reporting

    ms, cl = _qual_ms(
        "Semi-structured interviews were conducted with 15 participants. "
        "Thematic analysis was used to identify patterns in the qualitative data."
    )
    result = validate_qualitative_rigor_reporting(ms, cl)
    assert any(f.code == "missing-qualitative-rigor" for f in result.findings)


def test_qualitative_with_member_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_qualitative_rigor_reporting

    ms, cl = _qual_ms(
        "Semi-structured interviews were conducted. "
        "Trustworthiness was established through member checking and peer debriefing. "
        "Data saturation was reached after 15 interviews."
    )
    result = validate_qualitative_rigor_reporting(ms, cl)
    assert result.findings == []


def test_no_qualitative_no_fire() -> None:
    from manuscript_audit.validators.core import validate_qualitative_rigor_reporting

    ms, cl = _qual_ms(
        "A randomized controlled trial with quantitative outcomes was conducted."
    )
    result = validate_qualitative_rigor_reporting(ms, cl)
    assert result.findings == []


def test_qualitative_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_qualitative_rigor_reporting

    ms, cl = _qual_ms(
        "Qualitative research methods are analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_qualitative_rigor_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 204 – validate_subgroup_analysis_labelling
# ---------------------------------------------------------------------------


def _sg_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="sg-1",
        source_path="sg.md",
        source_format="markdown",
        title="Subgroup Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_subgroup_no_label_fires() -> None:
    from manuscript_audit.validators.core import validate_subgroup_analysis_labelling

    ms, cl = _sg_ms(
        "We also examined whether the effect differed by age group. "
        "Subgroup analysis showed stronger effects in older participants."
    )
    result = validate_subgroup_analysis_labelling(ms, cl)
    assert any(f.code == "unlabelled-subgroup-analysis" for f in result.findings)


def test_subgroup_prespecified_no_fire() -> None:
    from manuscript_audit.validators.core import validate_subgroup_analysis_labelling

    ms, cl = _sg_ms(
        "Pre-specified subgroup analyses were conducted by age and sex. "
        "These subgroup analyses were pre-registered and planned."
    )
    result = validate_subgroup_analysis_labelling(ms, cl)
    assert result.findings == []


def test_no_subgroup_no_fire() -> None:
    from manuscript_audit.validators.core import validate_subgroup_analysis_labelling

    ms, cl = _sg_ms(
        "The primary analysis compared treatment vs. control groups."
    )
    result = validate_subgroup_analysis_labelling(ms, cl)
    assert result.findings == []


def test_subgroup_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_subgroup_analysis_labelling

    ms, cl = _sg_ms(
        "Subgroup analysis is analyzed theoretically.", "math_theory_paper"
    )
    result = validate_subgroup_analysis_labelling(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 205 – validate_null_result_power_caveat
# ---------------------------------------------------------------------------


def _null_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="null-1",
        source_path="null.md",
        source_format="markdown",
        title="Null Result Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_null_result_no_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_null_result_power_caveat

    ms, cl = _null_ms(
        "There is no significant effect of treatment on outcomes. "
        "There was no association between stress and depression."
    )
    result = validate_null_result_power_caveat(ms, cl)
    assert any(
        f.code == "null-result-without-power-caveat" for f in result.findings
    )


def test_null_result_with_power_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_power_caveat

    ms, cl = _null_ms(
        "There is no significant effect of treatment. However, the study "
        "may have been underpowered to detect small effects. "
        "Type II error cannot be excluded."
    )
    result = validate_null_result_power_caveat(ms, cl)
    assert result.findings == []


def test_no_null_result_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_power_caveat

    ms, cl = _null_ms(
        "The treatment significantly improved outcomes (p < .001, d = 0.6)."
    )
    result = validate_null_result_power_caveat(ms, cl)
    assert result.findings == []


def test_null_result_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_power_caveat

    ms, cl = _null_ms(
        "There is no effect in this theoretical null model.",
        "math_theory_paper",
    )
    result = validate_null_result_power_caveat(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 206 – validate_mean_sd_reporting
# ---------------------------------------------------------------------------


def _mean_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="mean-1",
        source_path="mean.md",
        source_format="markdown",
        title="Mean SD Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_mean_no_sd_fires() -> None:
    from manuscript_audit.validators.core import validate_mean_sd_reporting

    ms, cl = _mean_ms(
        "The mean age was 34.2 years. The mean score was 72.1 and the "
        "average rating of 4.3 was observed across conditions."
    )
    result = validate_mean_sd_reporting(ms, cl)
    assert any(f.code == "missing-sd-for-mean" for f in result.findings)


def test_mean_with_sd_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mean_sd_reporting

    ms, cl = _mean_ms(
        "The mean age was 34.2 years (SD = 8.1). "
        "Scores averaged M = 72.1 (SD = 12.3)."
    )
    result = validate_mean_sd_reporting(ms, cl)
    assert result.findings == []


def test_no_mean_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mean_sd_reporting

    ms, cl = _mean_ms(
        "Frequencies and proportions are reported for all categorical variables."
    )
    result = validate_mean_sd_reporting(ms, cl)
    assert result.findings == []


def test_mean_sd_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mean_sd_reporting

    ms, cl = _mean_ms(
        "The mean value is 3.5 in this theoretical example.", "math_theory_paper"
    )
    result = validate_mean_sd_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 207 – validate_intervention_description
# ---------------------------------------------------------------------------


def _intv_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="intv-1",
        source_path="intv.md",
        source_format="markdown",
        title="Intervention Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_intervention_no_detail_fires() -> None:
    from manuscript_audit.validators.core import validate_intervention_description

    ms, cl = _intv_ms(
        "The intervention group received CBT treatment protocol for 8 weeks. "
        "The treatment condition included group sessions."
    )
    result = validate_intervention_description(ms, cl)
    assert any(
        f.code == "insufficient-intervention-description" for f in result.findings
    )


def test_intervention_with_detail_no_fire() -> None:
    from manuscript_audit.validators.core import validate_intervention_description

    ms, cl = _intv_ms(
        "The intervention group received 8 weekly sessions of CBT. "
        "Each session lasted 50 minutes. Session content followed the protocol manual. "
        "Treatment fidelity was monitored by supervisors."
    )
    result = validate_intervention_description(ms, cl)
    assert result.findings == []


def test_no_intervention_observational_no_fire() -> None:
    from manuscript_audit.validators.core import validate_intervention_description

    ms, cl = _intv_ms(
        "This observational study collected data from existing health records."
    )
    result = validate_intervention_description(ms, cl)
    assert result.findings == []


def test_intervention_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_intervention_description

    ms, cl = _intv_ms(
        "The intervention group is analyzed theoretically.", "math_theory_paper"
    )
    result = validate_intervention_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 208 – validate_baseline_equivalence
# ---------------------------------------------------------------------------


def _base_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="base-1",
        source_path="base.md",
        source_format="markdown",
        title="Baseline Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_rct_no_baseline_fires() -> None:
    from manuscript_audit.validators.core import validate_baseline_equivalence

    ms, cl = _base_ms(
        "This randomized controlled trial assigned 120 participants to "
        "treatment or control. Outcomes were assessed at 3 months."
    )
    result = validate_baseline_equivalence(ms, cl)
    assert any(f.code == "missing-baseline-equivalence" for f in result.findings)


def test_rct_with_baseline_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_baseline_equivalence

    ms, cl = _base_ms(
        "This RCT randomly assigned participants. "
        "Baseline characteristics were comparable across groups. "
        "Groups were similar at baseline on all key variables."
    )
    result = validate_baseline_equivalence(ms, cl)
    assert result.findings == []


def test_non_rct_cross_sectional_no_fire() -> None:
    from manuscript_audit.validators.core import validate_baseline_equivalence

    ms, cl = _base_ms(
        "This cross-sectional survey measured all variables at one time point."
    )
    result = validate_baseline_equivalence(ms, cl)
    assert result.findings == []


def test_baseline_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_baseline_equivalence

    ms, cl = _base_ms(
        "The RCT baseline equivalence is analyzed theoretically.",
        "math_theory_paper",
    )
    result = validate_baseline_equivalence(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 209 – validate_likert_distribution_check
# ---------------------------------------------------------------------------


def _likert_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="lik-1",
        source_path="lik.md",
        source_format="markdown",
        title="Likert Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_likert_no_distribution_fires() -> None:
    from manuscript_audit.validators.core import validate_likert_distribution_check

    ms, cl = _likert_ms(
        "Outcomes were measured using a 5-point Likert scale ranging from "
        "strongly disagree to strongly agree. Parametric tests were used."
    )
    result = validate_likert_distribution_check(ms, cl)
    assert any(
        f.code == "missing-likert-distribution-check" for f in result.findings
    )


def test_likert_with_distribution_no_fire() -> None:
    from manuscript_audit.validators.core import validate_likert_distribution_check

    ms, cl = _likert_ms(
        "Outcomes used a 5-point Likert-type scale. "
        "Skewness and ceiling effects were examined. "
        "The distribution of responses was approximately normal."
    )
    result = validate_likert_distribution_check(ms, cl)
    assert result.findings == []


def test_no_likert_no_fire() -> None:
    from manuscript_audit.validators.core import validate_likert_distribution_check

    ms, cl = _likert_ms(
        "Outcomes were measured using objective performance tests."
    )
    result = validate_likert_distribution_check(ms, cl)
    assert result.findings == []


def test_likert_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_likert_distribution_check

    ms, cl = _likert_ms(
        "The 5-point Likert scale is analyzed theoretically.", "math_theory_paper"
    )
    result = validate_likert_distribution_check(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 210 – validate_reproducibility_statement
# ---------------------------------------------------------------------------


def _repro_ms(text: str, paper_type: str = "empirical_paper") -> tuple:
    ms = ParsedManuscript(
        manuscript_id="rep-1",
        source_path="rep.md",
        source_format="markdown",
        title="Reproducibility Study",
        full_text=text,
        sections=[],
    )
    cl = ManuscriptClassification(
        pathway="applied_stats",
        paper_type=paper_type,
        recommended_stack="standard",
    )
    return ms, cl


def test_repro_claim_no_link_fires() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_statement

    ms, cl = _repro_ms(
        "All code and data are available from the corresponding author upon request. "
        "Scripts are available as supplementary materials."
    )
    result = validate_reproducibility_statement(ms, cl)
    assert any(f.code == "missing-reproducibility-link" for f in result.findings)


def test_repro_claim_with_url_no_fire() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_statement

    ms, cl = _repro_ms(
        "All code is available at https://github.com/user/repo. "
        "Data are deposited at osf.io/abc123."
    )
    result = validate_reproducibility_statement(ms, cl)
    assert result.findings == []


def test_no_repro_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_statement

    ms, cl = _repro_ms(
        "The study followed standard analysis protocols. "
        "No additional materials are provided."
    )
    result = validate_reproducibility_statement(ms, cl)
    assert result.findings == []


def test_repro_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_reproducibility_statement

    ms, cl = _repro_ms(
        "Code is available from the authors for this theoretical example.",
        "math_theory_paper",
    )
    result = validate_reproducibility_statement(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 211 – validate_missing_data_handling
# ---------------------------------------------------------------------------

def _missing_data_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-missing",
            source_path="/tmp/missing.md",
            source_format="markdown",
            title="Missing Data Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_missing_data_without_method_fires() -> None:
    from manuscript_audit.validators.core import validate_missing_data_handling

    ms, cl = _missing_data_ms(
        "Some participants did not complete all items, resulting in missing data "
        "that were excluded from analysis."
    )
    result = validate_missing_data_handling(ms, cl)
    assert any(f.code == "missing-data-handling-not-described" for f in result.findings)


def test_missing_data_with_method_no_fire() -> None:
    from manuscript_audit.validators.core import validate_missing_data_handling

    ms, cl = _missing_data_ms(
        "Missing data were addressed using multiple imputation with 20 datasets "
        "under the MICE procedure."
    )
    result = validate_missing_data_handling(ms, cl)
    assert result.findings == []


def test_no_missing_data_no_fire() -> None:
    from manuscript_audit.validators.core import validate_missing_data_handling

    ms, cl = _missing_data_ms(
        "Data were complete for all participants; no exclusions were required."
    )
    result = validate_missing_data_handling(ms, cl)
    assert result.findings == []


def test_missing_data_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_missing_data_handling

    ms, cl = _missing_data_ms("Some participants did not complete all items.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_missing_data_handling(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 212 – validate_coding_scheme_description
# ---------------------------------------------------------------------------

def _coding_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-coding",
            source_path="/tmp/coding.md",
            source_format="markdown",
            title="Coding Scheme Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_coding_without_icr_fires() -> None:
    from manuscript_audit.validators.core import validate_coding_scheme_description

    ms, cl = _coding_ms(
        "We developed a coding scheme with four categories based on the data. "
        "Inductive coding was used to identify themes."
    )
    result = validate_coding_scheme_description(ms, cl)
    assert any(f.code == "missing-coding-scheme-detail" for f in result.findings)


def test_coding_scheme_with_kappa_no_fire() -> None:
    from manuscript_audit.validators.core import validate_coding_scheme_description

    ms, cl = _coding_ms(
        "An inductive coding scheme was developed. Inter-coder reliability was "
        "assessed using Cohen's kappa = 0.82."
    )
    result = validate_coding_scheme_description(ms, cl)
    assert result.findings == []


def test_no_coding_scheme_no_fire() -> None:
    from manuscript_audit.validators.core import validate_coding_scheme_description

    ms, cl = _coding_ms(
        "We performed a regression analysis predicting exam scores from study hours."
    )
    result = validate_coding_scheme_description(ms, cl)
    assert result.findings == []


def test_coding_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_coding_scheme_description

    ms, cl = _coding_ms("A codebook was developed using inductive coding.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_coding_scheme_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 213 – validate_logistic_regression_assumptions
# ---------------------------------------------------------------------------

def _logit_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-logit",
            source_path="/tmp/logit.md",
            source_format="markdown",
            title="Logistic Regression Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_logistic_without_fit_fires() -> None:
    from manuscript_audit.validators.core import validate_logistic_regression_assumptions

    ms, cl = _logit_ms(
        "Binary logistic regression was used to predict group membership from "
        "the composite score (OR = 2.1, p = 0.03)."
    )
    result = validate_logistic_regression_assumptions(ms, cl)
    assert any(f.code == "missing-logistic-model-fit" for f in result.findings)


def test_logistic_with_auc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_logistic_regression_assumptions

    ms, cl = _logit_ms(
        "Binary logistic regression was used. The Hosmer-Lemeshow goodness-of-fit test "
        "indicated acceptable model fit, and the AUC was 0.81."
    )
    result = validate_logistic_regression_assumptions(ms, cl)
    assert result.findings == []


def test_no_logistic_no_fire() -> None:
    from manuscript_audit.validators.core import validate_logistic_regression_assumptions

    ms, cl = _logit_ms(
        "We ran an independent samples t-test to compare means between groups."
    )
    result = validate_logistic_regression_assumptions(ms, cl)
    assert result.findings == []


def test_logistic_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_logistic_regression_assumptions

    ms, cl = _logit_ms("Logistic regression was analysed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_logistic_regression_assumptions(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 214 – validate_researcher_positionality
# ---------------------------------------------------------------------------

def _positionality_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-positionality",
            source_path="/tmp/positionality.md",
            source_format="markdown",
            title="Positionality Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_qualitative_without_positionality_fires() -> None:
    from manuscript_audit.validators.core import validate_researcher_positionality

    ms, cl = _positionality_ms(
        "This phenomenological study explored participants' lived experiences of "
        "chronic pain through semi-structured interviews."
    )
    result = validate_researcher_positionality(ms, cl)
    assert any(f.code == "missing-researcher-positionality" for f in result.findings)


def test_qualitative_with_positionality_no_fire() -> None:
    from manuscript_audit.validators.core import validate_researcher_positionality

    ms, cl = _positionality_ms(
        "This phenomenological study used semi-structured interviews. "
        "The researcher's positionality as a clinical psychologist with ten years "
        "of practice experience may have influenced interpretation."
    )
    result = validate_researcher_positionality(ms, cl)
    assert result.findings == []


def test_quantitative_no_positionality_no_fire() -> None:
    from manuscript_audit.validators.core import validate_researcher_positionality

    ms, cl = _positionality_ms(
        "We conducted a randomised controlled trial with 200 adult participants."
    )
    result = validate_researcher_positionality(ms, cl)
    assert result.findings == []


def test_positionality_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_researcher_positionality

    ms, cl = _positionality_ms("This phenomenological framework is discussed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_researcher_positionality(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 215 – validate_data_collection_recency
# ---------------------------------------------------------------------------

def _recency_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-recency",
            source_path="/tmp/recency.md",
            source_format="markdown",
            title="Recency Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_recent_claim_old_data_fires() -> None:
    from manuscript_audit.validators.core import validate_data_collection_recency

    ms, cl = _recency_ms(
        "We use recent data collected from a nationally representative survey. "
        "Data were collected in 2009 using random-digit dialing."
    )
    result = validate_data_collection_recency(ms, cl)
    assert any(f.code == "potentially-outdated-data" for f in result.findings)


def test_recent_claim_new_data_no_fire() -> None:
    from manuscript_audit.validators.core import validate_data_collection_recency

    ms, cl = _recency_ms(
        "We use recent data from the 2021 national census. "
        "Data were collected in 2021."
    )
    result = validate_data_collection_recency(ms, cl)
    assert result.findings == []


def test_no_recent_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_data_collection_recency

    ms, cl = _recency_ms(
        "We analysed archival records from 1990 to examine historical trends."
    )
    result = validate_data_collection_recency(ms, cl)
    assert result.findings == []


def test_recency_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_data_collection_recency

    ms, cl = _recency_ms("Recent data from 2005 are discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_data_collection_recency(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 216 – validate_theoretical_framework_citation
# ---------------------------------------------------------------------------

def _theory_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-theory",
            source_path="/tmp/theory.md",
            source_format="markdown",
            title="Theory Citation Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_named_theory_without_citation_fires() -> None:
    from manuscript_audit.validators.core import validate_theoretical_framework_citation

    ms, cl = _theory_ms(
        "This study is grounded in Self-Determination Theory and examines "
        "intrinsic motivation among students."
    )
    result = validate_theoretical_framework_citation(ms, cl)
    assert any(f.code == "missing-theory-citation" for f in result.findings)


def test_named_theory_with_citation_no_fire() -> None:
    from manuscript_audit.validators.core import validate_theoretical_framework_citation

    ms, cl = _theory_ms(
        "This study is grounded in Self-Determination Theory (Deci & Ryan, 1985) "
        "and examines intrinsic motivation among students."
    )
    result = validate_theoretical_framework_citation(ms, cl)
    assert result.findings == []


def test_no_named_theory_no_fire() -> None:
    from manuscript_audit.validators.core import validate_theoretical_framework_citation

    ms, cl = _theory_ms(
        "We ran a regression analysis to predict academic performance from GPA."
    )
    result = validate_theoretical_framework_citation(ms, cl)
    assert result.findings == []


def test_theory_citation_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_theoretical_framework_citation

    ms, cl = _theory_ms("Self-Determination Theory is discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_theoretical_framework_citation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 217 – validate_survey_instrument_source
# ---------------------------------------------------------------------------

def _instrument_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-instrument",
            source_path="/tmp/instrument.md",
            source_format="markdown",
            title="Instrument Source Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_scale_without_source_fires() -> None:
    from manuscript_audit.validators.core import validate_survey_instrument_source

    ms, cl = _instrument_ms(
        "A validated questionnaire was used to assess burnout levels among "
        "healthcare workers across three hospital sites."
    )
    result = validate_survey_instrument_source(ms, cl)
    assert any(f.code == "missing-instrument-source" for f in result.findings)


def test_scale_with_citation_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_instrument_source

    ms, cl = _instrument_ms(
        "The Maslach Burnout Inventory (Maslach & Jackson, 1981) was used. "
        "Cronbach's alpha for the exhaustion subscale was .87."
    )
    result = validate_survey_instrument_source(ms, cl)
    assert result.findings == []


def test_no_scale_used_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_instrument_source

    ms, cl = _instrument_ms(
        "Biomarker levels were extracted from medical records using standard laboratory protocols."
    )
    result = validate_survey_instrument_source(ms, cl)
    assert result.findings == []


def test_instrument_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_instrument_source

    ms, cl = _instrument_ms("A validated scale was used in prior work.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_survey_instrument_source(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 218 – validate_sampling_frame_description
# ---------------------------------------------------------------------------

def _sampling_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-sampling",
            source_path="/tmp/sampling.md",
            source_format="markdown",
            title="Sampling Frame Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_sampling_without_frame_fires() -> None:
    from manuscript_audit.validators.core import validate_sampling_frame_description

    ms, cl = _sampling_ms(
        "Participants were recruited from a large urban university. "
        "A total of 200 undergraduate students completed the survey."
    )
    result = validate_sampling_frame_description(ms, cl)
    assert any(f.code == "missing-sampling-frame" for f in result.findings)


def test_sampling_with_strategy_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sampling_frame_description

    ms, cl = _sampling_ms(
        "Participants were recruited using stratified random sampling from "
        "the university registry of enrolled undergraduate students."
    )
    result = validate_sampling_frame_description(ms, cl)
    assert result.findings == []


def test_no_sampling_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sampling_frame_description

    ms, cl = _sampling_ms(
        "We analysed archival clinical trial data with complete registry linkage."
    )
    result = validate_sampling_frame_description(ms, cl)
    assert result.findings == []


def test_sampling_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sampling_frame_description

    ms, cl = _sampling_ms("Participants were sampled from a register.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_sampling_frame_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 219 – validate_one_tailed_test_justification
# ---------------------------------------------------------------------------

def _one_tailed_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-onetail",
            source_path="/tmp/onetail.md",
            source_format="markdown",
            title="One-Tailed Test Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_one_tailed_without_justification_fires() -> None:
    from manuscript_audit.validators.core import validate_one_tailed_test_justification

    ms, cl = _one_tailed_ms(
        "Significance was assessed using a one-tailed test with alpha = 0.05 "
        "based on the directional hypothesis."
    )
    result = validate_one_tailed_test_justification(ms, cl)
    assert any(f.code == "unjustified-one-tailed-test" for f in result.findings)


def test_one_tailed_with_justification_no_fire() -> None:
    from manuscript_audit.validators.core import validate_one_tailed_test_justification

    ms, cl = _one_tailed_ms(
        "A one-tailed test was justified because prior literature strongly predicts "
        "a positive effect of treatment on anxiety reduction."
    )
    result = validate_one_tailed_test_justification(ms, cl)
    assert result.findings == []


def test_two_tailed_only_no_fire() -> None:
    from manuscript_audit.validators.core import validate_one_tailed_test_justification

    ms, cl = _one_tailed_ms(
        "All tests were two-tailed with alpha = 0.05."
    )
    result = validate_one_tailed_test_justification(ms, cl)
    assert result.findings == []


def test_one_tailed_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_one_tailed_test_justification

    ms, cl = _one_tailed_ms("One-tailed tests are discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_one_tailed_test_justification(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 220 – validate_gratuitous_significance_language
# ---------------------------------------------------------------------------

def _gratuitous_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-gratuitous",
            source_path="/tmp/gratuitous.md",
            source_format="markdown",
            title="Gratuitous Significance Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_all_results_significant_fires() -> None:
    from manuscript_audit.validators.core import validate_gratuitous_significance_language

    ms, cl = _gratuitous_ms(
        "All results were statistically significant, confirming our hypotheses."
    )
    result = validate_gratuitous_significance_language(ms, cl)
    assert any(f.code == "implausible-significance-language" for f in result.findings)


def test_normal_significance_report_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gratuitous_significance_language

    ms, cl = _gratuitous_ms(
        "The primary outcome was statistically significant (p = 0.03). "
        "Secondary outcomes did not reach significance."
    )
    result = validate_gratuitous_significance_language(ms, cl)
    assert result.findings == []


def test_no_significance_language_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gratuitous_significance_language

    ms, cl = _gratuitous_ms(
        "We report descriptive statistics and confidence intervals for all outcomes."
    )
    result = validate_gratuitous_significance_language(ms, cl)
    assert result.findings == []


def test_gratuitous_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_gratuitous_significance_language

    ms, cl = _gratuitous_ms("All results were highly significant.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_gratuitous_significance_language(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 221 – validate_unit_of_analysis_clarity
# ---------------------------------------------------------------------------

def _unit_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-unit",
            source_path="/tmp/unit.md",
            source_format="markdown",
            title="Unit of Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_nested_without_unit_fires() -> None:
    from manuscript_audit.validators.core import validate_unit_of_analysis_clarity

    ms, cl = _unit_ms(
        "Students nested within classrooms were assessed on mathematics achievement. "
        "OLS regression was used to predict scores."
    )
    result = validate_unit_of_analysis_clarity(ms, cl)
    assert any(f.code == "unclear-unit-of-analysis" for f in result.findings)


def test_nested_with_mlm_no_fire() -> None:
    from manuscript_audit.validators.core import validate_unit_of_analysis_clarity

    ms, cl = _unit_ms(
        "Students nested within classrooms were assessed. "
        "We used HLM to account for the multilevel data structure, with students "
        "at level 1 and classrooms at level 2."
    )
    result = validate_unit_of_analysis_clarity(ms, cl)
    assert result.findings == []


def test_no_nesting_no_fire() -> None:
    from manuscript_audit.validators.core import validate_unit_of_analysis_clarity

    ms, cl = _unit_ms(
        "We recruited 200 adults from the community and administered an online survey."
    )
    result = validate_unit_of_analysis_clarity(ms, cl)
    assert result.findings == []


def test_unit_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_unit_of_analysis_clarity

    ms, cl = _unit_ms("Students nested within classrooms are discussed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_unit_of_analysis_clarity(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 222 – validate_apriori_preregistration_statement
# ---------------------------------------------------------------------------

def _prereg222_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-prereg",
            source_path="/tmp/prereg.md",
            source_format="markdown",
            title="Preregistration Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_apriori_hypothesis_without_prereg_fires() -> None:
    from manuscript_audit.validators.core import validate_apriori_preregistration_statement

    ms, cl = _prereg222_ms(
        "Our confirmatory analysis tested a priori hypotheses about the effect of "
        "mindfulness on stress reduction."
    )
    result = validate_apriori_preregistration_statement(ms, cl)
    assert any(f.code == "missing-preregistration-statement" for f in result.findings)


def test_apriori_with_prereg_no_fire() -> None:
    from manuscript_audit.validators.core import validate_apriori_preregistration_statement

    ms, cl = _prereg222_ms(
        "Our confirmatory analysis tested a priori hypotheses. The study was "
        "pre-registered on OSF (https://osf.io/xyz)."
    )
    result = validate_apriori_preregistration_statement(ms, cl)
    assert result.findings == []


def test_no_apriori_no_fire() -> None:
    from manuscript_audit.validators.core import validate_apriori_preregistration_statement

    ms, cl = _prereg222_ms(
        "This exploratory study examined associations between sleep duration and mood."
    )
    result = validate_apriori_preregistration_statement(ms, cl)
    assert result.findings == []


def test_prereg_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_apriori_preregistration_statement

    ms, cl = _prereg222_ms("Confirmatory analysis tested a priori hypotheses.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_apriori_preregistration_statement(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 223 – validate_selective_literature_citation
# ---------------------------------------------------------------------------

def _selective_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-selective",
            source_path="/tmp/selective.md",
            source_format="markdown",
            title="Selective Citation Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_universal_consensus_without_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_selective_literature_citation

    ms, cl = _selective_ms(
        "Research consistently shows that exercise improves cognitive function. "
        "All studies confirm this positive relationship."
    )
    result = validate_selective_literature_citation(ms, cl)
    assert any(f.code == "selective-literature-citation" for f in result.findings)


def test_consensus_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_selective_literature_citation

    ms, cl = _selective_ms(
        "Research consistently shows that exercise improves cognitive function. "
        "However, some studies have found mixed evidence, particularly among older adults."
    )
    result = validate_selective_literature_citation(ms, cl)
    assert result.findings == []


def test_no_consensus_language_no_fire() -> None:
    from manuscript_audit.validators.core import validate_selective_literature_citation

    ms, cl = _selective_ms(
        "We examined associations between sleep duration and cognitive performance."
    )
    result = validate_selective_literature_citation(ms, cl)
    assert result.findings == []


def test_selective_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_selective_literature_citation

    ms, cl = _selective_ms("Research consistently shows positive effects.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_selective_literature_citation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 224 – validate_participant_compensation_disclosure
# ---------------------------------------------------------------------------

def _compensation_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-compensation",
            source_path="/tmp/compensation.md",
            source_format="markdown",
            title="Compensation Disclosure Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_compensation_without_amount_fires() -> None:
    from manuscript_audit.validators.core import validate_participant_compensation_disclosure

    ms, cl = _compensation_ms(
        "Participants were compensated for their time. Informed consent was obtained."
    )
    result = validate_participant_compensation_disclosure(ms, cl)
    assert any(f.code == "missing-compensation-amount" for f in result.findings)


def test_compensation_with_amount_no_fire() -> None:
    from manuscript_audit.validators.core import validate_participant_compensation_disclosure

    ms, cl = _compensation_ms(
        "Participants received $15 as compensation for completing the 45-minute session."
    )
    result = validate_participant_compensation_disclosure(ms, cl)
    assert result.findings == []


def test_no_compensation_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_participant_compensation_disclosure

    ms, cl = _compensation_ms(
        "Data were collected via an online survey distributed through the university portal."
    )
    result = validate_participant_compensation_disclosure(ms, cl)
    assert result.findings == []


def test_compensation_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_participant_compensation_disclosure

    ms, cl = _compensation_ms("Participants were compensated for their time.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_participant_compensation_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 225 – validate_observational_causal_language
# ---------------------------------------------------------------------------

def _obs_causal_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-obs-causal",
            source_path="/tmp/obs_causal.md",
            source_format="markdown",
            title="Observational Causal Language Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_observational_with_causal_language_fires() -> None:
    from manuscript_audit.validators.core import validate_observational_causal_language

    ms, cl = _obs_causal_ms(
        "This cross-sectional study demonstrates that social media use causes "
        "depression in adolescents."
    )
    result = validate_observational_causal_language(ms, cl)
    assert any(f.code == "overclaimed-causality-observational" for f in result.findings)


def test_observational_with_causal_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_observational_causal_language

    ms, cl = _obs_causal_ms(
        "This cross-sectional study found that social media use was associated with "
        "depression in adolescents. However, we cannot establish causality from "
        "cross-sectional data."
    )
    result = validate_observational_causal_language(ms, cl)
    assert result.findings == []


def test_rct_no_observational_design_no_fire() -> None:
    from manuscript_audit.validators.core import validate_observational_causal_language

    ms, cl = _obs_causal_ms(
        "This RCT demonstrated that the intervention caused a significant reduction "
        "in anxiety symptoms (p < 0.01)."
    )
    result = validate_observational_causal_language(ms, cl)
    assert result.findings == []


def test_observational_causal_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_observational_causal_language

    ms, cl = _obs_causal_ms(
        "Cross-sectional studies demonstrate that X causes Y."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_observational_causal_language(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 226 – validate_acknowledgement_section
# ---------------------------------------------------------------------------

def _ack_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-ack",
            source_path="/tmp/ack.md",
            source_format="markdown",
            title="Acknowledgement Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_funding_without_acknowledgement_fires() -> None:
    from manuscript_audit.validators.core import validate_acknowledgement_section

    ms, cl = _ack_ms(
        "This study was funded by NIH grant R01 MH123456. "
        "Data were collected between January and June 2022."
    )
    result = validate_acknowledgement_section(ms, cl)
    assert any(f.code == "missing-acknowledgement-section" for f in result.findings)


def test_funding_with_acknowledgement_no_fire() -> None:
    from manuscript_audit.validators.core import validate_acknowledgement_section

    ms, cl = _ack_ms(
        "This work was supported by NIH grant R01 MH123456. "
        "Acknowledgements: We thank the participants and research staff."
    )
    result = validate_acknowledgement_section(ms, cl)
    assert result.findings == []


def test_no_funding_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_acknowledgement_section

    ms, cl = _ack_ms(
        "Data were collected from university students via an online platform."
    )
    result = validate_acknowledgement_section(ms, cl)
    assert result.findings == []


def test_acknowledgement_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_acknowledgement_section

    ms, cl = _ack_ms("This study was funded by NIH grant R01.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_acknowledgement_section(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 227 – validate_conflict_of_interest_statement
# ---------------------------------------------------------------------------

def _coi_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-coi",
            source_path="/tmp/coi.md",
            source_format="markdown",
            title="Conflict of Interest Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_industry_funding_without_coi_fires() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest_statement

    ms, cl = _coi_ms(
        "This study was industry-funded by PharmaCorp Inc. "
        "The lead author received honoraria from the sponsor."
    )
    result = validate_conflict_of_interest_statement(ms, cl)
    assert any(f.code == "missing-conflict-of-interest-statement" for f in result.findings)


def test_industry_funding_with_coi_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest_statement

    ms, cl = _coi_ms(
        "This study was industry-funded by PharmaCorp Inc. "
        "Conflict of interest: The authors declare that the funder had no role in "
        "study design, data collection, or interpretation."
    )
    result = validate_conflict_of_interest_statement(ms, cl)
    assert result.findings == []


def test_no_industry_relationship_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest_statement

    ms, cl = _coi_ms(
        "This study received no external funding. All data were collected by the "
        "university research team."
    )
    result = validate_conflict_of_interest_statement(ms, cl)
    assert result.findings == []


def test_coi_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_conflict_of_interest_statement

    ms, cl = _coi_ms("This study was industry-funded.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_conflict_of_interest_statement(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 228 – validate_age_reporting_precision
# ---------------------------------------------------------------------------

def _age_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-age",
            source_path="/tmp/age.md",
            source_format="markdown",
            title="Age Reporting Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_age_without_precision_fires() -> None:
    from manuscript_audit.validators.core import validate_age_reporting_precision

    ms, cl = _age_ms(
        "The mean age of participants was reported as approximately 35 years. "
        "Participants ranged in age from young adults to middle-aged."
    )
    result = validate_age_reporting_precision(ms, cl)
    assert any(f.code == "imprecise-age-reporting" for f in result.findings)


def test_age_with_sd_no_fire() -> None:
    from manuscript_audit.validators.core import validate_age_reporting_precision

    ms, cl = _age_ms(
        "The mean age of participants was 34.7 years (SD = 8.2). "
        "Participants were aged 18 to 65 years."
    )
    result = validate_age_reporting_precision(ms, cl)
    assert result.findings == []


def test_no_age_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_age_reporting_precision

    ms, cl = _age_ms(
        "The sample consisted of 150 undergraduate students at a large university."
    )
    result = validate_age_reporting_precision(ms, cl)
    assert result.findings == []


def test_age_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_age_reporting_precision

    ms, cl = _age_ms("The mean age of participants is discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_age_reporting_precision(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 229 – validate_statistical_software_version
# ---------------------------------------------------------------------------

def _stat_sw_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-statsw",
            source_path="/tmp/statsw.md",
            source_format="markdown",
            title="Statistical Software Version Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_stat_software_without_version_fires() -> None:
    from manuscript_audit.validators.core import validate_statistical_software_version

    ms, cl = _stat_sw_ms(
        "Analyses were conducted using SPSS. "
        "All tests were two-tailed with alpha set at 0.05."
    )
    result = validate_statistical_software_version(ms, cl)
    assert any(f.code == "missing-statistical-software-version" for f in result.findings)


def test_stat_software_with_version_no_fire() -> None:
    from manuscript_audit.validators.core import validate_statistical_software_version

    ms, cl = _stat_sw_ms(
        "All analyses were conducted using R version 4.3.1. "
        "Mixed models were fitted using the lme4 package."
    )
    result = validate_statistical_software_version(ms, cl)
    assert result.findings == []


def test_no_stat_software_no_fire() -> None:
    from manuscript_audit.validators.core import validate_statistical_software_version

    ms, cl = _stat_sw_ms(
        "Data were collected through structured interviews and thematic analysis was performed."
    )
    result = validate_statistical_software_version(ms, cl)
    assert result.findings == []


def test_stat_software_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_statistical_software_version

    ms, cl = _stat_sw_ms("Analyses were conducted using SPSS.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_statistical_software_version(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 230 – validate_warranted_sensitivity_analysis
# ---------------------------------------------------------------------------

def _sensitivity230_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-sensitivity",
            source_path="/tmp/sensitivity.md",
            source_format="markdown",
            title="Sensitivity Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_sensitivity_needed_but_not_done_fires() -> None:
    from manuscript_audit.validators.core import validate_warranted_sensitivity_analysis

    ms, cl = _sensitivity230_ms(
        "Results may be sensitive to outliers in the dataset. "
        "Future researchers should conduct robustness checks."
    )
    result = validate_warranted_sensitivity_analysis(ms, cl)
    assert any(f.code == "missing-warranted-sensitivity-analysis" for f in result.findings)


def test_sensitivity_analysis_conducted_no_fire() -> None:
    from manuscript_audit.validators.core import validate_warranted_sensitivity_analysis

    ms, cl = _sensitivity230_ms(
        "Results may be sensitive to outliers. We conducted a sensitivity analysis "
        "excluding influential observations; results were robust to these exclusions."
    )
    result = validate_warranted_sensitivity_analysis(ms, cl)
    assert result.findings == []


def test_no_sensitivity_trigger_no_fire() -> None:
    from manuscript_audit.validators.core import validate_warranted_sensitivity_analysis

    ms, cl = _sensitivity230_ms(
        "We used linear regression to predict exam performance from study time."
    )
    result = validate_warranted_sensitivity_analysis(ms, cl)
    assert result.findings == []


def test_sensitivity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_warranted_sensitivity_analysis

    ms, cl = _sensitivity230_ms("Results may be sensitive to the chosen prior.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_warranted_sensitivity_analysis(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 231 – validate_ai_tool_disclosure
# ---------------------------------------------------------------------------

def _ai_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-ai",
            source_path="/tmp/ai.md",
            source_format="markdown",
            title="AI Disclosure Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_ai_tool_without_disclosure_fires() -> None:
    from manuscript_audit.validators.core import validate_ai_tool_disclosure

    ms, cl = _ai_ms(
        "We used ChatGPT to assist with drafting the literature review section."
    )
    result = validate_ai_tool_disclosure(ms, cl)
    assert any(f.code == "missing-ai-tool-disclosure" for f in result.findings)


def test_ai_tool_with_disclosure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ai_tool_disclosure

    ms, cl = _ai_ms(
        "We used ChatGPT for grammar checking of the manuscript draft. "
        "AI-generated content was reviewed and edited by all authors for accuracy."
    )
    result = validate_ai_tool_disclosure(ms, cl)
    assert result.findings == []


def test_no_ai_tool_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ai_tool_disclosure

    ms, cl = _ai_ms(
        "All analyses were conducted using R version 4.3.1. "
        "The manuscript was written and revised by the research team."
    )
    result = validate_ai_tool_disclosure(ms, cl)
    assert result.findings == []


def test_ai_tool_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ai_tool_disclosure

    ms, cl = _ai_ms("We used ChatGPT to explore ideas.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_ai_tool_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 232 – validate_between_group_effect_size
# ---------------------------------------------------------------------------

def _between_group_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-between-group",
            source_path="/tmp/between_group.md",
            source_format="markdown",
            title="Between-Group Effect Size Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_group_diff_without_effect_size_fires() -> None:
    from manuscript_audit.validators.core import validate_between_group_effect_size

    ms, cl = _between_group_ms(
        "Groups differed significantly on the primary outcome, t(98) = 3.45, p = 0.001."
    )
    result = validate_between_group_effect_size(ms, cl)
    assert any(f.code == "missing-between-group-effect-size" for f in result.findings)


def test_group_diff_with_cohens_d_no_fire() -> None:
    from manuscript_audit.validators.core import validate_between_group_effect_size

    ms, cl = _between_group_ms(
        "Groups differed significantly on the primary outcome, t(98) = 3.45, p = 0.001, "
        "Cohen's d = 0.69."
    )
    result = validate_between_group_effect_size(ms, cl)
    assert result.findings == []


def test_no_between_group_comparison_no_fire() -> None:
    from manuscript_audit.validators.core import validate_between_group_effect_size

    ms, cl = _between_group_ms(
        "Descriptive statistics and correlations are reported for all variables."
    )
    result = validate_between_group_effect_size(ms, cl)
    assert result.findings == []


def test_between_group_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_between_group_effect_size

    ms, cl = _between_group_ms("Groups differed significantly, t(98) = 3.45.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_between_group_effect_size(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 233 – validate_convenience_sample_generalization
# ---------------------------------------------------------------------------

def _convenience_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-convenience",
            source_path="/tmp/convenience.md",
            source_format="markdown",
            title="Convenience Sample Generalization Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_convenience_with_broad_generalisation_fires() -> None:
    from manuscript_audit.validators.core import validate_convenience_sample_generalization

    ms, cl = _convenience_ms(
        "Undergraduate students completed the survey. "
        "Our findings generalize to the general adult population."
    )
    result = validate_convenience_sample_generalization(ms, cl)
    assert any(f.code == "overclaimed-generalizability-convenience" for f in result.findings)


def test_convenience_with_generalisability_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_convenience_sample_generalization

    ms, cl = _convenience_ms(
        "Undergraduate students completed the survey. "
        "Our findings generalize to the general adult population, though "
        "generalisability may be limited by the student sample."
    )
    result = validate_convenience_sample_generalization(ms, cl)
    assert result.findings == []


def test_representative_sample_no_fire() -> None:
    from manuscript_audit.validators.core import validate_convenience_sample_generalization

    ms, cl = _convenience_ms(
        "A nationally representative probability sample of 3,500 adults was recruited "
        "using stratified random sampling."
    )
    result = validate_convenience_sample_generalization(ms, cl)
    assert result.findings == []


def test_convenience_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_convenience_sample_generalization

    ms, cl = _convenience_ms(
        "Convenience samples limit the ability to generalize to the general population."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_convenience_sample_generalization(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 234 – validate_icc_reliability_reporting
# ---------------------------------------------------------------------------

def _icc_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-icc",
            source_path="/tmp/icc.md",
            source_format="markdown",
            title="ICC Reliability Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_rater_agreement_without_icc_fires() -> None:
    from manuscript_audit.validators.core import validate_icc_reliability_reporting

    ms, cl = _icc_ms(
        "Two independent raters coded all interview transcripts. "
        "Rater agreement was assessed and found to be acceptable."
    )
    result = validate_icc_reliability_reporting(ms, cl)
    assert any(f.code == "missing-icc-reliability" for f in result.findings)


def test_rater_agreement_with_icc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_icc_reliability_reporting

    ms, cl = _icc_ms(
        "Two independent raters coded all transcripts. "
        "Inter-rater reliability was excellent, ICC(2,1) = 0.91."
    )
    result = validate_icc_reliability_reporting(ms, cl)
    assert result.findings == []


def test_single_rater_no_fire() -> None:
    from manuscript_audit.validators.core import validate_icc_reliability_reporting

    ms, cl = _icc_ms(
        "One trained researcher coded all interview data using the coding manual."
    )
    result = validate_icc_reliability_reporting(ms, cl)
    assert result.findings == []


def test_icc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_icc_reliability_reporting

    ms, cl = _icc_ms("Two raters independently coded the data.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_icc_reliability_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 235 – validate_anova_post_hoc_reporting
# ---------------------------------------------------------------------------

def _anova_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-anova",
            source_path="/tmp/anova.md",
            source_format="markdown",
            title="ANOVA Post-Hoc Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_anova_significant_without_post_hoc_fires() -> None:
    from manuscript_audit.validators.core import validate_anova_post_hoc_reporting

    ms, cl = _anova_ms(
        "A one-way ANOVA revealed a significant main effect of condition, "
        "F(2, 147) = 8.34, p < 0.001."
    )
    result = validate_anova_post_hoc_reporting(ms, cl)
    assert any(f.code == "missing-anova-post-hoc" for f in result.findings)


def test_anova_with_tukey_no_fire() -> None:
    from manuscript_audit.validators.core import validate_anova_post_hoc_reporting

    ms, cl = _anova_ms(
        "A one-way ANOVA revealed a significant main effect of condition, "
        "F(2, 147) = 8.34, p < 0.001. Post-hoc comparisons using Tukey HSD "
        "indicated that group A differed from group B (p = 0.003)."
    )
    result = validate_anova_post_hoc_reporting(ms, cl)
    assert result.findings == []


def test_non_significant_anova_no_fire() -> None:
    from manuscript_audit.validators.core import validate_anova_post_hoc_reporting

    ms, cl = _anova_ms(
        "ANOVA indicated no significant differences between conditions, "
        "F(2, 147) = 1.23, p = 0.29."
    )
    result = validate_anova_post_hoc_reporting(ms, cl)
    assert result.findings == []


def test_anova_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_anova_post_hoc_reporting

    ms, cl = _anova_ms("ANOVA revealed a significant main effect of condition.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_anova_post_hoc_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 236 – validate_adverse_events_reporting
# ---------------------------------------------------------------------------

def _ae_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-ae",
            source_path="/tmp/ae.md",
            source_format="markdown",
            title="Adverse Events Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_rct_without_adverse_events_fires() -> None:
    from manuscript_audit.validators.core import validate_adverse_events_reporting

    ms, cl = _ae_ms(
        "A randomised controlled trial compared CBT to waitlist control. "
        "Participants were randomised to the treatment group or control group. "
        "Outcomes were assessed at 8 weeks post-randomisation."
    )
    result = validate_adverse_events_reporting(ms, cl)
    assert any(f.code == "missing-adverse-events-report" for f in result.findings)


def test_rct_with_adverse_events_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adverse_events_reporting

    ms, cl = _ae_ms(
        "A randomised controlled trial compared CBT to waitlist control. "
        "No adverse events were reported by participants in either group."
    )
    result = validate_adverse_events_reporting(ms, cl)
    assert result.findings == []


def test_observational_no_adverse_events_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adverse_events_reporting

    ms, cl = _ae_ms(
        "This cross-sectional survey examined sleep quality among university students."
    )
    result = validate_adverse_events_reporting(ms, cl)
    assert result.findings == []


def test_adverse_events_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adverse_events_reporting

    ms, cl = _ae_ms(
        "This RCT compared two interventions."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_adverse_events_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 237 – validate_construct_operationalization
# ---------------------------------------------------------------------------

def _construct_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-construct",
            source_path="/tmp/construct.md",
            source_format="markdown",
            title="Construct Operationalization Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_pronoun_construct_without_definition_fires() -> None:
    from manuscript_audit.validators.core import validate_construct_operationalization

    ms, cl = _construct_ms(
        "Anxiety was the primary outcome. It was assessed and participants completed "
        "the measure at two time points."
    )
    result = validate_construct_operationalization(ms, cl)
    assert any(f.code == "ambiguous-construct-operationalization" for f in result.findings)


def test_construct_with_operationalization_no_fire() -> None:
    from manuscript_audit.validators.core import validate_construct_operationalization

    ms, cl = _construct_ms(
        "Anxiety was operationalized using the GAD-7 scale (Spitzer et al., 2006). "
        "It was measured at baseline and 8-week follow-up."
    )
    result = validate_construct_operationalization(ms, cl)
    assert result.findings == []


def test_no_pronoun_construct_no_fire() -> None:
    from manuscript_audit.validators.core import validate_construct_operationalization

    ms, cl = _construct_ms(
        "Depressive symptoms were assessed using the PHQ-9. "
        "Anxiety was assessed using the GAD-7."
    )
    result = validate_construct_operationalization(ms, cl)
    assert result.findings == []


def test_construct_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_construct_operationalization

    ms, cl = _construct_ms("It was measured using a scale.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_construct_operationalization(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 238 – validate_regression_coefficient_ci
# ---------------------------------------------------------------------------

def _coeff_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-coeff",
            source_path="/tmp/coeff.md",
            source_format="markdown",
            title="Regression Coefficient CI Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_coefficient_without_ci_fires() -> None:
    from manuscript_audit.validators.core import validate_regression_coefficient_ci

    ms, cl = _coeff_ms(
        "The regression coefficient for hours of study was B = 0.43 (p = 0.01)."
    )
    result = validate_regression_coefficient_ci(ms, cl)
    assert any(f.code == "missing-regression-coefficient-ci" for f in result.findings)


def test_coefficient_with_ci_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_coefficient_ci

    ms, cl = _coeff_ms(
        "The regression coefficient for hours of study was B = 0.43 "
        "(95% CI [0.18, 0.68], p = 0.01)."
    )
    result = validate_regression_coefficient_ci(ms, cl)
    assert result.findings == []


def test_no_regression_coefficient_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_coefficient_ci

    ms, cl = _coeff_ms(
        "Descriptive statistics and correlations were computed for all study variables."
    )
    result = validate_regression_coefficient_ci(ms, cl)
    assert result.findings == []


def test_coefficient_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_coefficient_ci

    ms, cl = _coeff_ms("The regression coefficient B = 0.5 is discussed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_regression_coefficient_ci(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 239 – validate_longitudinal_followup_duration
# ---------------------------------------------------------------------------

def _followup_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-followup",
            source_path="/tmp/followup.md",
            source_format="markdown",
            title="Follow-Up Duration Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_longitudinal_without_duration_fires() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_followup_duration

    ms, cl = _followup_ms(
        "This longitudinal study followed participants across multiple time points. "
        "Data were collected at baseline, mid-point, and follow-up assessment."
    )
    result = validate_longitudinal_followup_duration(ms, cl)
    assert any(f.code == "missing-followup-duration" for f in result.findings)


def test_longitudinal_with_duration_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_followup_duration

    ms, cl = _followup_ms(
        "This longitudinal study followed participants for 12 months. "
        "Data were collected at baseline, 6-month, and 12-month follow-up."
    )
    result = validate_longitudinal_followup_duration(ms, cl)
    assert result.findings == []


def test_cross_sectional_no_followup_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_followup_duration

    ms, cl = _followup_ms(
        "This cross-sectional survey assessed anxiety and depression in a single session."
    )
    result = validate_longitudinal_followup_duration(ms, cl)
    assert result.findings == []


def test_longitudinal_followup_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_longitudinal_followup_duration

    ms, cl = _followup_ms("This longitudinal study is discussed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_longitudinal_followup_duration(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 240 – validate_bayesian_reporting
# ---------------------------------------------------------------------------

def _bayesian_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-bayesian",
            source_path="/tmp/bayesian.md",
            source_format="markdown",
            title="Bayesian Reporting Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_bayesian_without_bf_fires() -> None:
    from manuscript_audit.validators.core import validate_bayesian_reporting

    ms, cl = _bayesian_ms(
        "We used a Bayesian analysis to test the hypothesis that mindfulness "
        "reduces anxiety. Prior distributions were specified based on prior literature."
    )
    result = validate_bayesian_reporting(ms, cl)
    assert any(f.code == "missing-bayesian-reporting" for f in result.findings)


def test_bayesian_with_bf_no_fire() -> None:
    from manuscript_audit.validators.core import validate_bayesian_reporting

    ms, cl = _bayesian_ms(
        "We used a Bayesian analysis to test our hypothesis. "
        "The Bayes factor BF10 = 8.3 indicated strong evidence for the alternative."
    )
    result = validate_bayesian_reporting(ms, cl)
    assert result.findings == []


def test_frequentist_no_bayesian_no_fire() -> None:
    from manuscript_audit.validators.core import validate_bayesian_reporting

    ms, cl = _bayesian_ms(
        "We used linear regression with NHST to test group differences."
    )
    result = validate_bayesian_reporting(ms, cl)
    assert result.findings == []


def test_bayesian_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_bayesian_reporting

    ms, cl = _bayesian_ms("Bayesian analysis is discussed in this framework.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_bayesian_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 241 – validate_floor_ceiling_effect_check
# ---------------------------------------------------------------------------

def _fc_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-fc",
            source_path="/tmp/fc.md",
            source_format="markdown",
            title="Floor Ceiling Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_likert_without_ceiling_check_fires() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effect_check

    ms, cl = _fc_ms(
        "Participants completed a 5-point Likert scale measuring satisfaction. "
        "Scores were analysed using parametric ANOVA."
    )
    result = validate_floor_ceiling_effect_check(ms, cl)
    assert any(f.code == "missing-floor-ceiling-check" for f in result.findings)


def test_likert_with_ceiling_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effect_check

    ms, cl = _fc_ms(
        "Participants completed a 5-point Likert scale. "
        "Ceiling effects were examined and no significant ceiling effect was found."
    )
    result = validate_floor_ceiling_effect_check(ms, cl)
    assert result.findings == []


def test_no_likert_scale_fc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effect_check

    ms, cl = _fc_ms(
        "Biomarker concentrations were measured using serum assays."
    )
    result = validate_floor_ceiling_effect_check(ms, cl)
    assert result.findings == []


def test_floor_ceiling_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_floor_ceiling_effect_check

    ms, cl = _fc_ms("Likert scale data are used in this framework.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_floor_ceiling_effect_check(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 242 – validate_hazard_ratio_ci
# ---------------------------------------------------------------------------

def _hr_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-hr",
            source_path="/tmp/hr.md",
            source_format="markdown",
            title="Hazard Ratio CI Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_hr_without_ci_fires() -> None:
    from manuscript_audit.validators.core import validate_hazard_ratio_ci

    ms, cl = _hr_ms(
        "Cox proportional hazards regression indicated that treatment group "
        "had a significantly lower hazard ratio of HR = 0.62 (p = 0.003)."
    )
    result = validate_hazard_ratio_ci(ms, cl)
    assert any(f.code == "missing-hazard-ratio-ci" for f in result.findings)


def test_hr_with_ci_no_fire() -> None:
    from manuscript_audit.validators.core import validate_hazard_ratio_ci

    ms, cl = _hr_ms(
        "Cox proportional hazards regression indicated HR = 0.62 "
        "(95% CI [0.45, 0.86], p = 0.003)."
    )
    result = validate_hazard_ratio_ci(ms, cl)
    assert result.findings == []


def test_no_survival_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import validate_hazard_ratio_ci

    ms, cl = _hr_ms(
        "Linear regression was used to predict quality of life from treatment group."
    )
    result = validate_hazard_ratio_ci(ms, cl)
    assert result.findings == []


def test_hr_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_hazard_ratio_ci

    ms, cl = _hr_ms("Cox regression and hazard ratios are discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_hazard_ratio_ci(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 243 – validate_outlier_removal_impact
# ---------------------------------------------------------------------------

def _outlier_impact_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-outlier-impact",
            source_path="/tmp/outlier_impact.md",
            source_format="markdown",
            title="Outlier Removal Impact Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_outlier_removed_without_sensitivity_fires() -> None:
    from manuscript_audit.validators.core import validate_outlier_removal_impact

    ms, cl = _outlier_impact_ms(
        "Outliers were removed based on values more than 3 SD from the mean. "
        "The remaining data were analysed using regression."
    )
    result = validate_outlier_removal_impact(ms, cl)
    assert any(f.code == "missing-outlier-removal-impact" for f in result.findings)


def test_outlier_removed_with_sensitivity_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_removal_impact

    ms, cl = _outlier_impact_ms(
        "Outliers were removed based on values more than 3 SD from the mean. "
        "Sensitivity analysis with outliers included showed results were robust."
    )
    result = validate_outlier_removal_impact(ms, cl)
    assert result.findings == []


def test_no_outlier_removal_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_removal_impact

    ms, cl = _outlier_impact_ms(
        "The complete dataset was used for all analyses without any exclusions."
    )
    result = validate_outlier_removal_impact(ms, cl)
    assert result.findings == []


def test_outlier_impact_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_outlier_removal_impact

    ms, cl = _outlier_impact_ms("Outliers were excluded from analysis.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_outlier_removal_impact(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 244 – validate_multilevel_icc_reporting
# ---------------------------------------------------------------------------

def _mlm_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-mlm",
            source_path="/tmp/mlm.md",
            source_format="markdown",
            title="Multilevel ICC Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_multilevel_without_icc_fires() -> None:
    from manuscript_audit.validators.core import validate_multilevel_icc_reporting

    ms, cl = _mlm_ms(
        "We used a multilevel model with students at level 1 nested within schools "
        "at level 2. Fixed and random effects were estimated."
    )
    result = validate_multilevel_icc_reporting(ms, cl)
    assert any(f.code == "missing-multilevel-icc" for f in result.findings)


def test_multilevel_with_icc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multilevel_icc_reporting

    ms, cl = _mlm_ms(
        "We used a multilevel model with students nested within schools. "
        "The intraclass correlation coefficient ICC = 0.18 indicated that 18% "
        "of variance was attributable to the school level."
    )
    result = validate_multilevel_icc_reporting(ms, cl)
    assert result.findings == []


def test_flat_model_no_multilevel_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multilevel_icc_reporting

    ms, cl = _mlm_ms(
        "Ordinary least squares regression was used to predict exam performance "
        "from hours of study, treating all observations as independent."
    )
    result = validate_multilevel_icc_reporting(ms, cl)
    assert result.findings == []


def test_multilevel_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multilevel_icc_reporting

    ms, cl = _mlm_ms("Multilevel models are discussed in this framework.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_multilevel_icc_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 245 – validate_citation_currency
# ---------------------------------------------------------------------------

def _cite_currency_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cite-currency",
            source_path="/tmp/cite_currency.md",
            source_format="markdown",
            title="Citation Currency Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_old_citation_without_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_citation_currency

    ms, cl = _cite_currency_ms(
        "Depression prevalence increases with age (Smith, 1972) and is strongly "
        "linked to socioeconomic status (Jones & Brown, 1968)."
    )
    result = validate_citation_currency(ms, cl)
    assert any(f.code == "potentially-outdated-citation" for f in result.findings)


def test_old_citation_with_foundational_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_currency

    ms, cl = _cite_currency_ms(
        "This classic framework, originally proposed by Seligman (1975), "
        "has been foundational to subsequent research on learned helplessness."
    )
    result = validate_citation_currency(ms, cl)
    assert result.findings == []


def test_recent_citations_only_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_currency

    ms, cl = _cite_currency_ms(
        "Depression prevalence increases with age (Smith, 2018) and is linked "
        "to socioeconomic status (Jones & Brown, 2020)."
    )
    result = validate_citation_currency(ms, cl)
    assert result.findings == []


def test_citation_currency_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_citation_currency

    ms, cl = _cite_currency_ms("The seminal work (Author, 1975) is discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_citation_currency(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 246 – validate_proportion_confidence_interval
# ---------------------------------------------------------------------------

def _prop_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-prop",
            source_path="/tmp/prop.md",
            source_format="markdown",
            title="Proportion CI Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_proportion_without_ci_fires() -> None:
    from manuscript_audit.validators.core import validate_proportion_confidence_interval

    ms, cl = _prop_ms(
        "Depression was present in 34% of participants at baseline."
    )
    result = validate_proportion_confidence_interval(ms, cl)
    assert any(f.code == "missing-proportion-ci" for f in result.findings)


def test_proportion_with_ci_no_fire() -> None:
    from manuscript_audit.validators.core import validate_proportion_confidence_interval

    ms, cl = _prop_ms(
        "Depression was present in 34% of participants (95% CI [28.1, 40.3]) "
        "at baseline."
    )
    result = validate_proportion_confidence_interval(ms, cl)
    assert result.findings == []


def test_no_proportion_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_proportion_confidence_interval

    ms, cl = _prop_ms(
        "Mean depression scores were higher in the treatment group than the control group."
    )
    result = validate_proportion_confidence_interval(ms, cl)
    assert result.findings == []


def test_proportion_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_proportion_confidence_interval

    ms, cl = _prop_ms("34% of participants reported depression symptoms.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_proportion_confidence_interval(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 247 – validate_blinding_procedure_description
# ---------------------------------------------------------------------------

def _blind_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-blind",
            source_path="/tmp/blind.md",
            source_format="markdown",
            title="Blinding Procedure Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_blinding_claimed_without_procedure_fires() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure_description

    ms, cl = _blind_ms(
        "This double-blind randomised trial compared active treatment to placebo. "
        "Both participants and assessors were blinded."
    )
    result = validate_blinding_procedure_description(ms, cl)
    assert any(f.code == "missing-blinding-procedure" for f in result.findings)


def test_blinding_with_procedure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure_description

    ms, cl = _blind_ms(
        "This double-blind trial used identical packaging of active and placebo tablets. "
        "Blinding was maintained via allocation concealment."
    )
    result = validate_blinding_procedure_description(ms, cl)
    assert result.findings == []


def test_no_blinding_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure_description

    ms, cl = _blind_ms(
        "This open-label trial compared CBT to treatment as usual."
    )
    result = validate_blinding_procedure_description(ms, cl)
    assert result.findings == []


def test_blinding_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_blinding_procedure_description

    ms, cl = _blind_ms("Double-blind procedures are recommended in clinical trials.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_blinding_procedure_description(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 248 – validate_primary_outcome_change_disclosure
# ---------------------------------------------------------------------------

def _outcome_change_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-outcome-change",
            source_path="/tmp/outcome_change.md",
            source_format="markdown",
            title="Outcome Change Disclosure Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_outcome_changed_without_disclosure_fires() -> None:
    from manuscript_audit.validators.core import validate_primary_outcome_change_disclosure

    ms, cl = _outcome_change_ms(
        "The primary outcome was changed from depression scores to anxiety scores "
        "after reviewing preliminary data."
    )
    result = validate_primary_outcome_change_disclosure(ms, cl)
    assert any(f.code == "undisclosed-outcome-change" for f in result.findings)


def test_outcome_changed_with_disclosure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_primary_outcome_change_disclosure

    ms, cl = _outcome_change_ms(
        "The primary outcome was changed from depression to anxiety. "
        "This change was prespecified in the pre-registration amendment filed "
        "prior to data analysis."
    )
    result = validate_primary_outcome_change_disclosure(ms, cl)
    assert result.findings == []


def test_no_outcome_change_no_fire() -> None:
    from manuscript_audit.validators.core import validate_primary_outcome_change_disclosure

    ms, cl = _outcome_change_ms(
        "The primary outcome was depression severity at 8 weeks, "
        "as planned prior to data collection."
    )
    result = validate_primary_outcome_change_disclosure(ms, cl)
    assert result.findings == []


def test_outcome_change_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_primary_outcome_change_disclosure

    ms, cl = _outcome_change_ms("The primary outcome was changed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_primary_outcome_change_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 249 – validate_null_result_discussion
# ---------------------------------------------------------------------------

def _null_disc_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-null-disc",
            source_path="/tmp/null_disc.md",
            source_format="markdown",
            title="Null Result Discussion Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_null_result_without_discussion_fires() -> None:
    from manuscript_audit.validators.core import validate_null_result_discussion

    ms, cl = _null_disc_ms(
        "The intervention was not statistically significant in reducing depression "
        "symptoms at 8-week follow-up (p = 0.18)."
    )
    result = validate_null_result_discussion(ms, cl)
    assert any(f.code == "missing-null-result-discussion" for f in result.findings)


def test_null_result_with_discussion_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_discussion

    ms, cl = _null_disc_ms(
        "The intervention was not statistically significant (p = 0.18). "
        "This null result may be due to the study being underpowered, "
        "as the achieved sample was below our power analysis target."
    )
    result = validate_null_result_discussion(ms, cl)
    assert result.findings == []


def test_all_significant_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_discussion

    ms, cl = _null_disc_ms(
        "The primary outcome improved significantly (p = 0.003). "
        "Secondary outcomes also showed meaningful improvements."
    )
    result = validate_null_result_discussion(ms, cl)
    assert result.findings == []


def test_null_disc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_null_result_discussion

    ms, cl = _null_disc_ms(
        "Results were not statistically significant."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_null_result_discussion(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 250 – validate_racial_ethnic_composition
# ---------------------------------------------------------------------------

def _race_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-race",
            source_path="/tmp/race.md",
            source_format="markdown",
            title="Racial Ethnic Composition Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_race_mention_without_breakdown_fires() -> None:
    from manuscript_audit.validators.core import validate_racial_ethnic_composition

    ms, cl = _race_ms(
        "The sample was racially diverse, recruited from urban community centres."
    )
    result = validate_racial_ethnic_composition(ms, cl)
    assert any(f.code == "missing-racial-ethnic-composition" for f in result.findings)


def test_race_with_breakdown_no_fire() -> None:
    from manuscript_audit.validators.core import validate_racial_ethnic_composition

    ms, cl = _race_ms(
        "The sample was racially diverse: 42% White, 28% Black, "
        "18% Hispanic, and 12% Asian participants."
    )
    result = validate_racial_ethnic_composition(ms, cl)
    assert result.findings == []


def test_no_race_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_racial_ethnic_composition

    ms, cl = _race_ms(
        "The sample consisted of 200 adults recruited from a university community."
    )
    result = validate_racial_ethnic_composition(ms, cl)
    assert result.findings == []


def test_race_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_racial_ethnic_composition

    ms, cl = _race_ms("Racial diversity in samples is discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_racial_ethnic_composition(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 251 – validate_single_item_measure_reliability
# ---------------------------------------------------------------------------

def _single_item_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-si",
            source_path="/tmp/si.md",
            source_format="markdown",
            title="Single Item Measure Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_single_item_without_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_single_item_measure_reliability

    ms, cl = _single_item_ms(
        "Happiness was measured with a single-item scale: "
        "'Overall, how happy are you?' (1–10)."
    )
    result = validate_single_item_measure_reliability(ms, cl)
    assert any(f.code == "missing-single-item-reliability-caveat" for f in result.findings)


def test_single_item_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_single_item_measure_reliability

    ms, cl = _single_item_ms(
        "Happiness was measured with a single-item scale. "
        "A limitation of single-item measures is lower reliability compared to multi-item scales."
    )
    result = validate_single_item_measure_reliability(ms, cl)
    assert result.findings == []


def test_no_single_item_use_no_fire() -> None:
    from manuscript_audit.validators.core import validate_single_item_measure_reliability

    ms, cl = _single_item_ms(
        "Depression was assessed with the PHQ-9, a validated nine-item scale."
    )
    result = validate_single_item_measure_reliability(ms, cl)
    assert result.findings == []


def test_single_item_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_single_item_measure_reliability

    ms, cl = _single_item_ms("Single-item measures are discussed in the literature.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_single_item_measure_reliability(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 252 – validate_mediator_temporality
# ---------------------------------------------------------------------------

def _mediator_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-mediator",
            source_path="/tmp/mediator.md",
            source_format="markdown",
            title="Mediator Temporality Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_mediation_without_temporal_order_fires() -> None:
    from manuscript_audit.validators.core import validate_mediator_temporality

    ms, cl = _mediator_ms(
        "Self-efficacy mediated the relationship between stress and burnout "
        "(indirect effect = 0.23, 95% CI [0.11, 0.35])."
    )
    result = validate_mediator_temporality(ms, cl)
    assert any(f.code == "missing-mediator-temporality" for f in result.findings)


def test_mediation_with_temporal_evidence_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediator_temporality

    ms, cl = _mediator_ms(
        "Self-efficacy mediated the relationship between stress and burnout. "
        "Stress was assessed at baseline (T1), self-efficacy at T2, and burnout at T3, "
        "ensuring temporal ordering of the mediator."
    )
    result = validate_mediator_temporality(ms, cl)
    assert result.findings == []


def test_no_mediation_claimed_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediator_temporality

    ms, cl = _mediator_ms(
        "Stress was positively correlated with burnout (r = 0.45, p < 0.001)."
    )
    result = validate_mediator_temporality(ms, cl)
    assert result.findings == []


def test_mediator_temporality_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_mediator_temporality

    ms, cl = _mediator_ms("Mediation analysis requires temporal ordering.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_mediator_temporality(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 253 – validate_effect_size_interpretation
# ---------------------------------------------------------------------------

def _es_interp_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-es-interp",
            source_path="/tmp/es_interp.md",
            source_format="markdown",
            title="Effect Size Interpretation Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_effect_size_without_interpretation_fires() -> None:
    from manuscript_audit.validators.core import validate_effect_size_interpretation

    ms, cl = _es_interp_ms(
        "The intervention improved outcomes (Cohen's d = 0.45)."
    )
    result = validate_effect_size_interpretation(ms, cl)
    assert any(f.code == "missing-effect-size-interpretation" for f in result.findings)


def test_effect_size_with_interpretation_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_size_interpretation

    ms, cl = _es_interp_ms(
        "The intervention improved outcomes (Cohen's d = 0.45), "
        "representing a medium effect by Cohen's (1988) conventions."
    )
    result = validate_effect_size_interpretation(ms, cl)
    assert result.findings == []


def test_no_effect_size_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_size_interpretation

    ms, cl = _es_interp_ms(
        "The intervention improved outcomes significantly (p = 0.002)."
    )
    result = validate_effect_size_interpretation(ms, cl)
    assert result.findings == []


def test_effect_size_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_effect_size_interpretation

    ms, cl = _es_interp_ms("Cohen's d is a measure of effect size.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_effect_size_interpretation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 254 – validate_comparison_group_equivalence
# ---------------------------------------------------------------------------

def _group_equiv_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-grp-equiv",
            source_path="/tmp/grp_equiv.md",
            source_format="markdown",
            title="Group Equivalence Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_group_comparison_without_baseline_fires() -> None:
    from manuscript_audit.validators.core import validate_comparison_group_equivalence

    ms, cl = _group_equiv_ms(
        "Comparing groups, the treatment arm showed significantly lower depression "
        "scores than the control arm at post-test."
    )
    result = validate_comparison_group_equivalence(ms, cl)
    assert any(f.code == "missing-baseline-equivalence-check" for f in result.findings)


def test_group_comparison_with_baseline_no_fire() -> None:
    from manuscript_audit.validators.core import validate_comparison_group_equivalence

    ms, cl = _group_equiv_ms(
        "Comparing groups, the treatment arm showed lower depression. "
        "Baseline characteristics were reported in Table 1; "
        "groups did not differ at baseline on any demographic variable."
    )
    result = validate_comparison_group_equivalence(ms, cl)
    assert result.findings == []


def test_no_group_comparison_equiv_no_fire() -> None:
    from manuscript_audit.validators.core import validate_comparison_group_equivalence

    ms, cl = _group_equiv_ms(
        "Depression scores decreased over time in a single-group pre-post design."
    )
    result = validate_comparison_group_equivalence(ms, cl)
    assert result.findings == []


def test_group_equivalence_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_comparison_group_equivalence

    ms, cl = _group_equiv_ms("Comparing groups requires baseline equivalence checks.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_comparison_group_equivalence(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 255 – validate_implicit_theory_test
# ---------------------------------------------------------------------------

def _impl_theory_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-theory-test",
            source_path="/tmp/theory_test.md",
            source_format="markdown",
            title="Implicit Theory Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_theory_test_correlational_fires() -> None:
    from manuscript_audit.validators.core import validate_implicit_theory_test

    ms, cl = _impl_theory_ms(
        "This study tests the theory that self-efficacy would predict burnout. "
        "We conducted a cross-sectional survey study with regression analysis."
    )
    result = validate_implicit_theory_test(ms, cl)
    assert any(f.code == "implicit-theory-test-correlational" for f in result.findings)


def test_theory_test_with_experimental_no_fire() -> None:
    from manuscript_audit.validators.core import validate_implicit_theory_test

    ms, cl = _impl_theory_ms(
        "This study tests the theory that self-efficacy would predict burnout. "
        "We used a randomised experimental design to evaluate this."
    )
    result = validate_implicit_theory_test(ms, cl)
    assert result.findings == []


def test_no_theory_test_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_implicit_theory_test

    ms, cl = _impl_theory_ms(
        "We explored the relationship between stress and health in a cross-sectional survey."
    )
    result = validate_implicit_theory_test(ms, cl)
    assert result.findings == []


def test_implicit_theory_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_implicit_theory_test

    ms, cl = _impl_theory_ms("Testing theory with correlational data is problematic.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_implicit_theory_test(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 256 – validate_multiple_comparison_correction
# ---------------------------------------------------------------------------

def _mcc256_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-mcc",
            source_path="/tmp/mcc.md",
            source_format="markdown",
            title="Multiple Comparison Correction Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_multiple_comparisons_without_correction_fires() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc256_ms(
        "We conducted multiple comparisons across six outcomes "
        "using independent t-tests for each."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert any(f.code == "missing-multiple-comparison-correction" for f in result.findings)


def test_multiple_comparisons_bonferroni_phase256_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc256_ms(
        "We conducted multiple comparisons across six outcomes. "
        "A Bonferroni correction was applied to control the family-wise error rate."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


def test_single_test_no_multiple_comparisons_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc256_ms(
        "We tested the primary hypothesis using a paired t-test."
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


def test_mcc_phase256_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_multiple_comparison_correction

    ms, cl = _mcc256_ms("Multiple comparisons require correction procedures.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_multiple_comparison_correction(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 257 – validate_non_normal_distribution_test
# ---------------------------------------------------------------------------

def _normality_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-normality",
            source_path="/tmp/normality.md",
            source_format="markdown",
            title="Normality Check Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_parametric_without_normality_check_fires() -> None:
    from manuscript_audit.validators.core import validate_non_normal_distribution_test

    ms, cl = _normality_ms(
        "We compared group means using an independent-samples t-test."
    )
    result = validate_non_normal_distribution_test(ms, cl)
    assert any(f.code == "missing-normality-check" for f in result.findings)


def test_parametric_with_normality_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_non_normal_distribution_test

    ms, cl = _normality_ms(
        "We compared group means using an independent-samples t-test. "
        "Data were normally distributed as confirmed by the Shapiro-Wilk test."
    )
    result = validate_non_normal_distribution_test(ms, cl)
    assert result.findings == []


def test_no_parametric_test_no_normality_fire() -> None:
    from manuscript_audit.validators.core import validate_non_normal_distribution_test

    ms, cl = _normality_ms(
        "We used thematic analysis to identify recurring patterns."
    )
    result = validate_non_normal_distribution_test(ms, cl)
    assert result.findings == []


def test_normality_phase257_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_non_normal_distribution_test

    ms, cl = _normality_ms("t-tests assume normally distributed data.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_non_normal_distribution_test(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 258 – validate_regression_sample_size_adequacy
# ---------------------------------------------------------------------------

def _reg_sample_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-reg-sample",
            source_path="/tmp/reg_sample.md",
            source_format="markdown",
            title="Regression Sample Size Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_regression_without_sample_adequacy_fires() -> None:
    from manuscript_audit.validators.core import validate_regression_sample_size_adequacy

    ms, cl = _reg_sample_ms(
        "A multiple regression analysis examined the predictors of burnout "
        "using eight predictor variables in a sample of 45 participants."
    )
    result = validate_regression_sample_size_adequacy(ms, cl)
    assert any(f.code == "missing-regression-sample-adequacy" for f in result.findings)


def test_regression_with_sample_adequacy_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_sample_size_adequacy

    ms, cl = _reg_sample_ms(
        "A multiple regression analysis was conducted. "
        "A power analysis confirmed adequate sample size for the number of predictors."
    )
    result = validate_regression_sample_size_adequacy(ms, cl)
    assert result.findings == []


def test_no_regression_no_sample_adequacy_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_sample_size_adequacy

    ms, cl = _reg_sample_ms(
        "Descriptive statistics were computed for all variables."
    )
    result = validate_regression_sample_size_adequacy(ms, cl)
    assert result.findings == []


def test_regression_sample_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_regression_sample_size_adequacy

    ms, cl = _reg_sample_ms("Regression models require adequate sample sizes.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_regression_sample_size_adequacy(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 259 – validate_scale_directionality_disclosure
# ---------------------------------------------------------------------------

def _scale_dir_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-scale-dir",
            source_path="/tmp/scale_dir.md",
            source_format="markdown",
            title="Scale Directionality Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_scale_without_directionality_fires() -> None:
    from manuscript_audit.validators.core import validate_scale_directionality_disclosure

    ms, cl = _scale_dir_ms(
        "Anxiety was assessed using a 7-point Likert scale."
    )
    result = validate_scale_directionality_disclosure(ms, cl)
    assert any(f.code == "missing-scale-directionality" for f in result.findings)


def test_scale_with_directionality_no_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_directionality_disclosure

    ms, cl = _scale_dir_ms(
        "Anxiety was assessed using a 7-point Likert scale, "
        "where higher scores indicate greater anxiety."
    )
    result = validate_scale_directionality_disclosure(ms, cl)
    assert result.findings == []


def test_no_scale_used_no_directionality_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_directionality_disclosure

    ms, cl = _scale_dir_ms(
        "Structured clinical interviews were used to assess diagnosis."
    )
    result = validate_scale_directionality_disclosure(ms, cl)
    assert result.findings == []


def test_scale_directionality_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_directionality_disclosure

    ms, cl = _scale_dir_ms("Likert scales require directionality disclosure.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_scale_directionality_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 260 – validate_attrition_rate_reporting
# ---------------------------------------------------------------------------

def _attrition260_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-attrition",
            source_path="/tmp/attrition.md",
            source_format="markdown",
            title="Attrition Rate Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_attrition_without_rate_fires() -> None:
    from manuscript_audit.validators.core import validate_attrition_rate_reporting

    ms, cl = _attrition260_ms(
        "Several participants dropped out before the final assessment."
    )
    result = validate_attrition_rate_reporting(ms, cl)
    assert any(f.code == "missing-attrition-rate" for f in result.findings)


def test_attrition_with_rate_no_fire() -> None:
    from manuscript_audit.validators.core import validate_attrition_rate_reporting

    ms, cl = _attrition260_ms(
        "12 participants dropped out before the final assessment, "
        "yielding an attrition rate of 9.8%."
    )
    result = validate_attrition_rate_reporting(ms, cl)
    assert result.findings == []


def test_no_attrition_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_attrition_rate_reporting

    ms, cl = _attrition260_ms(
        "All 120 enrolled participants completed the 8-week intervention."
    )
    result = validate_attrition_rate_reporting(ms, cl)
    assert result.findings == []


def test_attrition_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_attrition_rate_reporting

    ms, cl = _attrition260_ms("Attrition rates should be reported in longitudinal studies.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_attrition_rate_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 261 – validate_dichotomization_of_continuous_variable
# ---------------------------------------------------------------------------

def _dichot261_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-dichot",
            source_path="/tmp/dichot.md",
            source_format="markdown",
            title="Dichotomization Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_median_split_without_justification_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_dichotomization_of_continuous_variable,
    )

    ms, cl = _dichot261_ms(
        "Depression scores were dichotomised using a median split "
        "into low and high groups."
    )
    result = validate_dichotomization_of_continuous_variable(ms, cl)
    assert any(f.code == "unjustified-dichotomization" for f in result.findings)


def test_dichotomization_with_clinical_cutoff_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_dichotomization_of_continuous_variable,
    )

    ms, cl = _dichot261_ms(
        "Depression scores were dichotomised using a validated clinical cut-off "
        "of 10 on the PHQ-9, consistent with established guidelines."
    )
    result = validate_dichotomization_of_continuous_variable(ms, cl)
    assert result.findings == []


def test_no_dichotomization_continuous_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_dichotomization_of_continuous_variable,
    )

    ms, cl = _dichot261_ms(
        "Depression scores were analysed as a continuous outcome in all models."
    )
    result = validate_dichotomization_of_continuous_variable(ms, cl)
    assert result.findings == []


def test_dichotomization_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_dichotomization_of_continuous_variable,
    )

    ms, cl = _dichot261_ms("Median splits reduce statistical power.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_dichotomization_of_continuous_variable(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 262 – validate_ecological_fallacy_warning
# ---------------------------------------------------------------------------

def _eco_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-eco",
            source_path="/tmp/eco.md",
            source_format="markdown",
            title="Ecological Fallacy Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_aggregate_data_without_fallacy_warning_fires() -> None:
    from manuscript_audit.validators.core import validate_ecological_fallacy_warning

    ms, cl = _eco_ms(
        "Country-level data on income inequality were correlated with "
        "mental health outcomes, suggesting that higher inequality causes poorer health."
    )
    result = validate_ecological_fallacy_warning(ms, cl)
    assert any(f.code == "missing-ecological-fallacy-warning" for f in result.findings)


def test_aggregate_data_with_fallacy_warning_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ecological_fallacy_warning

    ms, cl = _eco_ms(
        "Country-level data were used. We acknowledge the ecological fallacy risk; "
        "individual-level conclusions cannot be drawn from these aggregate data."
    )
    result = validate_ecological_fallacy_warning(ms, cl)
    assert result.findings == []


def test_no_aggregate_data_no_fallacy_fire() -> None:
    from manuscript_audit.validators.core import validate_ecological_fallacy_warning

    ms, cl = _eco_ms(
        "Individual-level survey data were collected from 500 participants."
    )
    result = validate_ecological_fallacy_warning(ms, cl)
    assert result.findings == []


def test_eco_fallacy_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ecological_fallacy_warning

    ms, cl = _eco_ms("Aggregate-level analyses risk ecological fallacy.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_ecological_fallacy_warning(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 263 – validate_standardised_mean_difference_units
# ---------------------------------------------------------------------------

def _smd_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-smd",
            source_path="/tmp/smd.md",
            source_format="markdown",
            title="SMD Units Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_smd_without_original_units_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_standardised_mean_difference_units,
    )

    ms, cl = _smd_ms(
        "The intervention effect was SMD = 0.42 (95% CI [0.21, 0.63])."
    )
    result = validate_standardised_mean_difference_units(ms, cl)
    assert any(f.code == "missing-smd-original-unit-context" for f in result.findings)


def test_smd_with_original_units_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_standardised_mean_difference_units,
    )

    ms, cl = _smd_ms(
        "The intervention effect was SMD = 0.42. This corresponds to an unstandardised "
        "difference of 3.2 points on the depression scale in original units."
    )
    result = validate_standardised_mean_difference_units(ms, cl)
    assert result.findings == []


def test_no_smd_reported_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_standardised_mean_difference_units,
    )

    ms, cl = _smd_ms(
        "The intervention improved outcomes (Cohen's d = 0.45, medium effect)."
    )
    result = validate_standardised_mean_difference_units(ms, cl)
    assert result.findings == []


def test_smd_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_standardised_mean_difference_units,
    )

    ms, cl = _smd_ms("SMD is used in meta-analysis to combine effects.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_standardised_mean_difference_units(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 264 – validate_retrospective_data_collection_disclosure
# ---------------------------------------------------------------------------

def _retro_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-retro",
            source_path="/tmp/retro.md",
            source_format="markdown",
            title="Retrospective Design Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_retrospective_without_disclosure_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_retrospective_data_collection_disclosure,
    )

    ms, cl = _retro_ms(
        "Data were collected from existing medical records of 500 patients "
        "admitted between 2018 and 2022."
    )
    result = validate_retrospective_data_collection_disclosure(ms, cl)
    assert any(
        f.code == "missing-retrospective-design-disclosure" for f in result.findings
    )


def test_retrospective_with_disclosure_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_retrospective_data_collection_disclosure,
    )

    ms, cl = _retro_ms(
        "Data were extracted from existing medical records. "
        "We acknowledge the retrospective design as a limitation of this study."
    )
    result = validate_retrospective_data_collection_disclosure(ms, cl)
    assert result.findings == []


def test_prospective_data_no_retro_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_retrospective_data_collection_disclosure,
    )

    ms, cl = _retro_ms(
        "Data were prospectively collected from participants at three time points."
    )
    result = validate_retrospective_data_collection_disclosure(ms, cl)
    assert result.findings == []


def test_retrospective_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_retrospective_data_collection_disclosure,
    )

    ms, cl = _retro_ms("Retrospective designs are discussed in the methodology literature.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_retrospective_data_collection_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 265 – validate_treatment_fidelity_reporting
# ---------------------------------------------------------------------------

def _fidelity_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-fidelity",
            source_path="/tmp/fidelity.md",
            source_format="markdown",
            title="Treatment Fidelity Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_intervention_without_fidelity_report_fires() -> None:
    from manuscript_audit.validators.core import validate_treatment_fidelity_reporting

    ms, cl = _fidelity_ms(
        "Participants in the CBT group received 12 sessions of cognitive-behavioral therapy "
        "delivered by trained therapists."
    )
    result = validate_treatment_fidelity_reporting(ms, cl)
    assert any(f.code == "missing-treatment-fidelity-report" for f in result.findings)


def test_intervention_with_fidelity_report_no_fire() -> None:
    from manuscript_audit.validators.core import validate_treatment_fidelity_reporting

    ms, cl = _fidelity_ms(
        "Participants received CBT over 12 sessions. "
        "Treatment fidelity was assessed by independent raters who reviewed 20% "
        "of sessions; mean adherence to the protocol was 94%."
    )
    result = validate_treatment_fidelity_reporting(ms, cl)
    assert result.findings == []


def test_observational_no_intervention_no_fidelity_fire() -> None:
    from manuscript_audit.validators.core import validate_treatment_fidelity_reporting

    ms, cl = _fidelity_ms(
        "This cross-sectional study examined naturally occurring variation in "
        "physical activity and its association with depression."
    )
    result = validate_treatment_fidelity_reporting(ms, cl)
    assert result.findings == []


def test_fidelity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_treatment_fidelity_reporting

    ms, cl = _fidelity_ms("Treatment fidelity is important in intervention research.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_treatment_fidelity_reporting(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 266 – validate_factorial_design_interaction_test
# ---------------------------------------------------------------------------

def _factorial_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-factorial",
            source_path="/tmp/factorial.md",
            source_format="markdown",
            title="Factorial Design Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_factorial_anova_without_interaction_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_factorial_design_interaction_test,
    )

    ms, cl = _factorial_ms(
        "A 2 × 2 factorial ANOVA was conducted with condition (CBT vs. control) "
        "and time (pre vs. post) as between-subjects factors. "
        "The main effect of condition was significant (F(1,98) = 12.3, p < .001)."
    )
    result = validate_factorial_design_interaction_test(ms, cl)
    assert any(f.code == "missing-factorial-interaction-test" for f in result.findings)


def test_factorial_anova_with_interaction_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_factorial_design_interaction_test,
    )

    ms, cl = _factorial_ms(
        "A 2 × 2 factorial ANOVA was conducted. "
        "The interaction effect of condition × time was significant "
        "(F(1,98) = 8.7, p = .004)."
    )
    result = validate_factorial_design_interaction_test(ms, cl)
    assert result.findings == []


def test_no_factorial_design_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_factorial_design_interaction_test,
    )

    ms, cl = _factorial_ms(
        "A one-way ANOVA compared anxiety scores across three conditions."
    )
    result = validate_factorial_design_interaction_test(ms, cl)
    assert result.findings == []


def test_factorial_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_factorial_design_interaction_test,
    )

    ms, cl = _factorial_ms("Factorial designs require interaction tests.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_factorial_design_interaction_test(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 267 – validate_regression_multicollinearity_check
# ---------------------------------------------------------------------------

def _multicol_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-multicol",
            source_path="/tmp/multicol.md",
            source_format="markdown",
            title="Multicollinearity Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_regression_without_multicollinearity_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_regression_multicollinearity_check,
    )

    ms, cl = _multicol_ms(
        "A hierarchical regression examined predictors of burnout "
        "using six predictor variables entered in two blocks."
    )
    result = validate_regression_multicollinearity_check(ms, cl)
    assert any(f.code == "missing-multicollinearity-check" for f in result.findings)


def test_regression_vif_multicol_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_regression_multicollinearity_check,
    )

    ms, cl = _multicol_ms(
        "A hierarchical regression was conducted. "
        "Variance inflation factor (VIF) values were all below 3.0, "
        "indicating no multicollinearity concerns."
    )
    result = validate_regression_multicollinearity_check(ms, cl)
    assert result.findings == []


def test_no_regression_no_multicol_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_regression_multicollinearity_check,
    )

    ms, cl = _multicol_ms(
        "Descriptive statistics are reported in Table 1."
    )
    result = validate_regression_multicollinearity_check(ms, cl)
    assert result.findings == []


def test_multicol_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_regression_multicollinearity_check,
    )

    ms, cl = _multicol_ms("Multicollinearity is assessed using VIF in regression.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_regression_multicollinearity_check(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 268 – validate_intention_to_treat_analysis
# ---------------------------------------------------------------------------

def _itt_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-itt",
            source_path="/tmp/itt.md",
            source_format="markdown",
            title="ITT Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_rct_without_itt_fires() -> None:
    from manuscript_audit.validators.core import validate_intention_to_treat_analysis

    ms, cl = _itt_ms(
        "This randomised controlled trial compared CBT to a waitlist control condition "
        "in adults with depression."
    )
    result = validate_intention_to_treat_analysis(ms, cl)
    assert any(f.code == "missing-itt-analysis" for f in result.findings)


def test_rct_with_itt_no_fire() -> None:
    from manuscript_audit.validators.core import validate_intention_to_treat_analysis

    ms, cl = _itt_ms(
        "This randomised controlled trial compared CBT to waitlist. "
        "Analyses followed an intention-to-treat approach, "
        "including all randomised participants."
    )
    result = validate_intention_to_treat_analysis(ms, cl)
    assert result.findings == []


def test_non_rct_no_itt_fire() -> None:
    from manuscript_audit.validators.core import validate_intention_to_treat_analysis

    ms, cl = _itt_ms(
        "This cross-sectional survey examined predictors of depression."
    )
    result = validate_intention_to_treat_analysis(ms, cl)
    assert result.findings == []


def test_itt_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_intention_to_treat_analysis

    ms, cl = _itt_ms("ITT analysis is the gold standard for RCTs.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_intention_to_treat_analysis(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 269 – validate_confidence_interval_direction_interpretation
# ---------------------------------------------------------------------------

def _ci_dir_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-ci-dir",
            source_path="/tmp/ci_dir.md",
            source_format="markdown",
            title="CI Direction Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_ci_without_direction_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_confidence_interval_direction_interpretation,
    )

    ms, cl = _ci_dir_ms(
        "The treatment effect was d = 0.42 (95% CI [0.21, 0.63])."
    )
    result = validate_confidence_interval_direction_interpretation(ms, cl)
    assert any(f.code == "missing-ci-direction-interpretation" for f in result.findings)


def test_ci_with_null_crossing_discussion_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_confidence_interval_direction_interpretation,
    )

    ms, cl = _ci_dir_ms(
        "The treatment effect was d = 0.42 (95% CI [0.21, 0.63]). "
        "Both bounds of the CI are positive, consistent with a beneficial effect."
    )
    result = validate_confidence_interval_direction_interpretation(ms, cl)
    assert result.findings == []


def test_no_ci_reported_no_ci_dir_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_confidence_interval_direction_interpretation,
    )

    ms, cl = _ci_dir_ms(
        "The intervention was effective (p < .001). Effect sizes were not reported."
    )
    result = validate_confidence_interval_direction_interpretation(ms, cl)
    assert result.findings == []


def test_ci_direction_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_confidence_interval_direction_interpretation,
    )

    ms, cl = _ci_dir_ms("CIs should exclude the null for significant effects.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_confidence_interval_direction_interpretation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 270 – validate_longitudinal_missing_data_method
# ---------------------------------------------------------------------------

def _long_missing_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-long-miss",
            source_path="/tmp/long_miss.md",
            source_format="markdown",
            title="Longitudinal Missing Data Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_longitudinal_without_missing_method_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_longitudinal_missing_data_method,
    )

    ms, cl = _long_missing_ms(
        "A longitudinal study assessed participants at baseline, "
        "6 months, and 12 months. Some participants did not complete all time points."
    )
    result = validate_longitudinal_missing_data_method(ms, cl)
    assert any(
        f.code == "missing-longitudinal-missing-data-method" for f in result.findings
    )


def test_longitudinal_with_missing_method_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_longitudinal_missing_data_method,
    )

    ms, cl = _long_missing_ms(
        "A longitudinal study was conducted at three time points. "
        "Missing data were handled using full information maximum likelihood (FIML) "
        "estimation, which uses all available data."
    )
    result = validate_longitudinal_missing_data_method(ms, cl)
    assert result.findings == []


def test_cross_sectional_no_long_missing_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_longitudinal_missing_data_method,
    )

    ms, cl = _long_missing_ms(
        "A cross-sectional survey was administered once to 300 adults."
    )
    result = validate_longitudinal_missing_data_method(ms, cl)
    assert result.findings == []


def test_long_missing_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_longitudinal_missing_data_method,
    )

    ms, cl = _long_missing_ms(
        "Longitudinal studies require careful handling of missing data."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_longitudinal_missing_data_method(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 271 – validate_cluster_sampling_correction
# ---------------------------------------------------------------------------

def _cluster_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cluster",
            source_path="/tmp/cluster.md",
            source_format="markdown",
            title="Cluster Sampling Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_cluster_sample_without_correction_fires() -> None:
    from manuscript_audit.validators.core import validate_cluster_sampling_correction

    ms, cl = _cluster_ms(
        "Schools were the unit of randomisation in this cluster randomised trial. "
        "Individual students within schools were assessed."
    )
    result = validate_cluster_sampling_correction(ms, cl)
    assert any(f.code == "missing-cluster-sampling-correction" for f in result.findings)


def test_cluster_sample_with_multilevel_model_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cluster_sampling_correction

    ms, cl = _cluster_ms(
        "Schools were randomised. Data were analysed using multilevel modelling "
        "to account for the nested structure of students within schools."
    )
    result = validate_cluster_sampling_correction(ms, cl)
    assert result.findings == []


def test_no_cluster_design_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cluster_sampling_correction

    ms, cl = _cluster_ms(
        "Individual participants were recruited and randomly assigned to conditions."
    )
    result = validate_cluster_sampling_correction(ms, cl)
    assert result.findings == []


def test_cluster_sampling_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cluster_sampling_correction

    ms, cl = _cluster_ms("Clustered samples require design-corrected analyses.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_cluster_sampling_correction(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 272 – validate_non_experimental_confound_discussion
# ---------------------------------------------------------------------------

def _confound_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-confound",
            source_path="/tmp/confound.md",
            source_format="markdown",
            title="Confound Discussion Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_observational_without_confound_discussion_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_non_experimental_confound_discussion,
    )

    ms, cl = _confound_ms(
        "This cross-sectional study examined the relationship between "
        "physical activity and depression in 500 adults."
    )
    result = validate_non_experimental_confound_discussion(ms, cl)
    assert any(f.code == "missing-confound-discussion" for f in result.findings)


def test_observational_with_confound_discussion_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_non_experimental_confound_discussion,
    )

    ms, cl = _confound_ms(
        "This cross-sectional study examined physical activity and depression. "
        "We acknowledge that confounders such as age and socioeconomic status "
        "cannot be ruled out in this design."
    )
    result = validate_non_experimental_confound_discussion(ms, cl)
    assert result.findings == []


def test_experimental_no_confound_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_non_experimental_confound_discussion,
    )

    ms, cl = _confound_ms(
        "Participants were randomly assigned to conditions in a controlled experiment."
    )
    result = validate_non_experimental_confound_discussion(ms, cl)
    assert result.findings == []


def test_confound_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_non_experimental_confound_discussion,
    )

    ms, cl = _confound_ms("Confounding is a key threat to validity in observational research.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_non_experimental_confound_discussion(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 273 – validate_complete_case_analysis_bias
# ---------------------------------------------------------------------------

def _complete_case_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cc",
            source_path="/tmp/cc.md",
            source_format="markdown",
            title="Complete Case Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_listwise_deletion_without_mcar_fires() -> None:
    from manuscript_audit.validators.core import validate_complete_case_analysis_bias

    ms, cl = _complete_case_ms(
        "Cases with missing data were excluded from the analysis (listwise deletion), "
        "resulting in a final sample of 182 participants."
    )
    result = validate_complete_case_analysis_bias(ms, cl)
    assert any(f.code == "unjustified-complete-case-analysis" for f in result.findings)


def test_listwise_deletion_with_mcar_test_no_fire() -> None:
    from manuscript_audit.validators.core import validate_complete_case_analysis_bias

    ms, cl = _complete_case_ms(
        "Listwise deletion was applied. Little's MCAR test indicated data were "
        "missing completely at random (χ² = 14.2, df = 12, p = .29)."
    )
    result = validate_complete_case_analysis_bias(ms, cl)
    assert result.findings == []


def test_no_missing_data_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_complete_case_analysis_bias

    ms, cl = _complete_case_ms(
        "All 200 enrolled participants completed all measures at all time points."
    )
    result = validate_complete_case_analysis_bias(ms, cl)
    assert result.findings == []


def test_complete_case_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_complete_case_analysis_bias

    ms, cl = _complete_case_ms("Listwise deletion assumes data are MCAR.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_complete_case_analysis_bias(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 274 – validate_analytic_strategy_prespecification
# ---------------------------------------------------------------------------

def _exploratory_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-exploratory",
            source_path="/tmp/exploratory.md",
            source_format="markdown",
            title="Exploratory Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_unlabelled_exploratory_analysis_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_analytic_strategy_prespecification,
    )

    ms, cl = _exploratory_ms(
        "We additionally explored whether gender moderated the relationship "
        "between stress and burnout."
    )
    result = validate_analytic_strategy_prespecification(ms, cl)
    assert any(f.code == "unlabelled-exploratory-analysis" for f in result.findings)


def test_labelled_exploratory_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_analytic_strategy_prespecification,
    )

    ms, cl = _exploratory_ms(
        "We conducted an exploratory analysis of gender moderation. "
        "These exploratory findings should be interpreted as preliminary "
        "and hypothesis-generating."
    )
    result = validate_analytic_strategy_prespecification(ms, cl)
    assert result.findings == []


def test_no_exploratory_mention_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_analytic_strategy_prespecification,
    )

    ms, cl = _exploratory_ms(
        "All analyses were prespecified and reported in the registered protocol."
    )
    result = validate_analytic_strategy_prespecification(ms, cl)
    assert result.findings == []


def test_exploratory_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_analytic_strategy_prespecification,
    )

    ms, cl = _exploratory_ms("Exploratory analyses are hypothesis-generating by nature.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_analytic_strategy_prespecification(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 275 – validate_self_report_bias_acknowledgement
# ---------------------------------------------------------------------------

def _self_report_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-self-report",
            source_path="/tmp/self_report.md",
            source_format="markdown",
            title="Self-Report Bias Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_self_report_without_bias_acknowledgement_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_self_report_bias_acknowledgement,
    )

    ms, cl = _self_report_ms(
        "Depression and anxiety were assessed via self-report questionnaire data "
        "completed online by participants."
    )
    result = validate_self_report_bias_acknowledgement(ms, cl)
    assert any(
        f.code == "missing-self-report-bias-acknowledgement" for f in result.findings
    )


def test_self_report_with_bias_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_self_report_bias_acknowledgement,
    )

    ms, cl = _self_report_ms(
        "Depression was assessed via self-report. A limitation of self-report measures "
        "is social desirability bias, which may have influenced responses."
    )
    result = validate_self_report_bias_acknowledgement(ms, cl)
    assert result.findings == []


def test_objective_measures_no_self_report_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_self_report_bias_acknowledgement,
    )

    ms, cl = _self_report_ms(
        "Physical activity was measured using accelerometers worn for seven days."
    )
    result = validate_self_report_bias_acknowledgement(ms, cl)
    assert result.findings == []


def test_self_report_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_self_report_bias_acknowledgement,
    )

    ms, cl = _self_report_ms("Self-report data are subject to social desirability bias.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_self_report_bias_acknowledgement(ms, cl)
    assert result.findings == []

# ---------------------------------------------------------------------------
# Phase 276 – validate_p_value_reporting_precision
# ---------------------------------------------------------------------------

def _pval_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-pval",
            source_path="/tmp/pval.md",
            source_format="markdown",
            title="P-Value Precision Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_threshold_only_p_values_fires() -> None:
    from manuscript_audit.validators.core import validate_p_value_reporting_precision

    ms, cl = _pval_ms(
        "The main effect was significant (p < .05). "
        "The secondary outcome was also significant (p < .01)."
    )
    result = validate_p_value_reporting_precision(ms, cl)
    assert any(f.code == "imprecise-p-value-reporting" for f in result.findings)


def test_exact_p_value_no_fire() -> None:
    from manuscript_audit.validators.core import validate_p_value_reporting_precision

    ms, cl = _pval_ms(
        "The main effect was significant (p = .032). "
        "The secondary outcome was also significant (p = .004)."
    )
    result = validate_p_value_reporting_precision(ms, cl)
    assert result.findings == []


def test_no_p_value_reported_no_fire() -> None:
    from manuscript_audit.validators.core import validate_p_value_reporting_precision

    ms, cl = _pval_ms(
        "Means and standard deviations are reported in Table 1."
    )
    result = validate_p_value_reporting_precision(ms, cl)
    assert result.findings == []


def test_pval_precision_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_p_value_reporting_precision

    ms, cl = _pval_ms("P-values should be reported exactly.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_p_value_reporting_precision(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 277 – validate_moderator_analysis_interpretation
# ---------------------------------------------------------------------------

def _moderator_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-moderator",
            source_path="/tmp/moderator.md",
            source_format="markdown",
            title="Moderator Analysis Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_moderation_without_simple_slopes_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_moderator_analysis_interpretation,
    )

    ms, cl = _moderator_ms(
        "Gender moderated the relationship between stress and burnout "
        "(interaction effect b = 0.34, p = .02)."
    )
    result = validate_moderator_analysis_interpretation(ms, cl)
    assert any(f.code == "missing-moderator-follow-up" for f in result.findings)


def test_moderation_with_simple_slopes_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_moderator_analysis_interpretation,
    )

    ms, cl = _moderator_ms(
        "Gender moderated the relationship (interaction b = 0.34, p = .02). "
        "Simple slopes analysis showed that stress predicted burnout strongly "
        "at high but not low levels of gender identification."
    )
    result = validate_moderator_analysis_interpretation(ms, cl)
    assert result.findings == []


def test_no_moderation_claimed_no_moderator_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_moderator_analysis_interpretation,
    )

    ms, cl = _moderator_ms(
        "Stress was positively associated with burnout (r = 0.45, p < .001)."
    )
    result = validate_moderator_analysis_interpretation(ms, cl)
    assert result.findings == []


def test_moderator_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_moderator_analysis_interpretation,
    )

    ms, cl = _moderator_ms("Moderation analysis requires simple slopes follow-up.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_moderator_analysis_interpretation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 278 – validate_measurement_occasion_labelling
# ---------------------------------------------------------------------------

def _occasion_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-occasion",
            source_path="/tmp/occasion.md",
            source_format="markdown",
            title="Measurement Occasion Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_time_labels_without_definition_fires() -> None:
    from manuscript_audit.validators.core import validate_measurement_occasion_labelling

    ms, cl = _occasion_ms(
        "Participants completed measures at T1 and T2. "
        "T1 and T2 scores were compared using paired t-tests."
    )
    result = validate_measurement_occasion_labelling(ms, cl)
    assert any(f.code == "unlabelled-measurement-occasions" for f in result.findings)


def test_time_labels_with_definition_no_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_occasion_labelling

    ms, cl = _occasion_ms(
        "T1 was the baseline measurement conducted before the intervention. "
        "T2 was the post-intervention assessment conducted 8 weeks later."
    )
    result = validate_measurement_occasion_labelling(ms, cl)
    assert result.findings == []


def test_no_time_labels_no_occasion_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_occasion_labelling

    ms, cl = _occasion_ms(
        "Participants completed a single survey at recruitment."
    )
    result = validate_measurement_occasion_labelling(ms, cl)
    assert result.findings == []


def test_occasion_labelling_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_measurement_occasion_labelling

    ms, cl = _occasion_ms("Time labels T1 and T2 should be defined.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_measurement_occasion_labelling(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 279 – validate_statistical_conclusion_validity
# ---------------------------------------------------------------------------

def _stat_conc_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-stat-conc",
            source_path="/tmp/stat_conc.md",
            source_format="markdown",
            title="Statistical Conclusion Validity Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_null_result_with_low_power_no_type2_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_statistical_conclusion_validity,
    )

    ms, cl = _stat_conc_ms(
        "The intervention effect was not significant (p = .18). "
        "The study was underpowered due to the smaller than expected sample."
    )
    result = validate_statistical_conclusion_validity(ms, cl)
    assert any(
        f.code == "missing-null-result-power-discussion" for f in result.findings
    )


def test_null_result_with_power_discussion_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_statistical_conclusion_validity,
    )

    ms, cl = _stat_conc_ms(
        "The intervention effect was not significant (p = .18). "
        "The study may have been underpowered; statistical power was insufficient "
        "to detect a small effect, raising Type II error risk."
    )
    result = validate_statistical_conclusion_validity(ms, cl)
    assert result.findings == []


def test_significant_result_no_power_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_statistical_conclusion_validity,
    )

    ms, cl = _stat_conc_ms(
        "The intervention was highly effective (p = .001)."
    )
    result = validate_statistical_conclusion_validity(ms, cl)
    assert result.findings == []


def test_stat_conc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_statistical_conclusion_validity,
    )

    ms, cl = _stat_conc_ms("Null results require power analysis discussion.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_statistical_conclusion_validity(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 280 – validate_author_contribution_statement
# ---------------------------------------------------------------------------

def _author_contrib_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-author",
            source_path="/tmp/author.md",
            source_format="markdown",
            title="Author Contribution Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_multiple_authors_without_contribution_statement_fires() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    ms, _cl = _author_contrib_ms(
        "All authors approved the final manuscript. "
        "Co-authors reviewed the manuscript before submission."
    )
    result = validate_author_contribution_statement(ms)
    assert any(
        f.code == "missing-author-contributions" for f in result.findings
    )


def test_authors_with_contribution_statement_no_fire() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    ms, _cl = _author_contrib_ms(
        "Author contributions: Smith conceptualized the study and wrote the original "
        "draft. Jones performed the formal analysis. Brown reviewed and edited the "
        "manuscript."
    )
    result = validate_author_contribution_statement(ms)
    assert result.findings == []


def test_no_co_author_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    ms, _cl = _author_contrib_ms(
        "Author contributions: conceptualization and writing by the sole author."
    )
    result = validate_author_contribution_statement(ms)
    assert result.findings == []


def test_author_contrib_no_credit_fires() -> None:
    from manuscript_audit.validators.core import validate_author_contribution_statement

    ms, _cl = _author_contrib_ms("All authors contributed equally to this work.")
    result = validate_author_contribution_statement(ms)
    assert any(f.code == "missing-author-contributions" for f in result.findings)


# ---------------------------------------------------------------------------
# Phase 281 – validate_scale_reliability_reporting
# ---------------------------------------------------------------------------

def _scale_rel_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-scale-rel",
            source_path="/tmp/scale_rel.md",
            source_format="markdown",
            title="Scale Reliability Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_scale281_without_reliability_fires() -> None:
    from manuscript_audit.validators.core import validate_scale_reliability_reporting

    ms, cl = _scale_rel_ms(
        "We used the Depression Anxiety Stress Scale (21-item questionnaire). "
        "Scores were computed by summing all items."
    )
    result = validate_scale_reliability_reporting(ms, cl)
    assert any(f.code == "missing-scale-reliability" for f in result.findings)


def test_scale_with_alpha_no_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_reliability_reporting

    ms, cl = _scale_rel_ms(
        "We used a 10-item questionnaire. Cronbach's alpha = .87 indicated "
        "good internal consistency."
    )
    result = validate_scale_reliability_reporting(ms, cl)
    assert result.findings == []


def test_no_scale_no_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_reliability_reporting

    ms, cl = _scale_rel_ms(
        "Participants were randomised to conditions. Reaction time was recorded."
    )
    result = validate_scale_reliability_reporting(ms, cl)
    assert result.findings == []


def test_scale_rel_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_scale_reliability_reporting

    ms, cl = _scale_rel_ms(
        "A scale of 10 items was administered without reliability reporting."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_scale_reliability_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 282 – validate_pilot_study_scope_limitation
# ---------------------------------------------------------------------------

def _pilot_scope_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-pilot-scope",
            source_path="/tmp/pilot_scope.md",
            source_format="markdown",
            title="Pilot Scope Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_pilot_without_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_scope_limitation

    ms, cl = _pilot_scope_ms(
        "We conducted a pilot study with 25 participants. Results showed "
        "significant improvements in all primary outcomes."
    )
    result = validate_pilot_study_scope_limitation(ms, cl)
    assert any(f.code == "missing-pilot-scope-limitation" for f in result.findings)


def test_pilot_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_scope_limitation

    ms, cl = _pilot_scope_ms(
        "This pilot study provides preliminary evidence. Results should be "
        "interpreted with caution due to the small sample and limited power."
    )
    result = validate_pilot_study_scope_limitation(ms, cl)
    assert result.findings == []


def test_no_pilot_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_scope_limitation

    ms, cl = _pilot_scope_ms(
        "A randomised controlled trial was conducted with 200 participants."
    )
    result = validate_pilot_study_scope_limitation(ms, cl)
    assert result.findings == []


def test_pilot_scope_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_pilot_study_scope_limitation

    ms, cl = _pilot_scope_ms("A pilot study design was discussed theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_pilot_study_scope_limitation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 283 – validate_literature_search_recency
# ---------------------------------------------------------------------------

def _lit_search_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-lit-search",
            source_path="/tmp/lit_search.md",
            source_format="markdown",
            title="Literature Search Recency Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_lit_review_without_search_date_fires() -> None:
    from manuscript_audit.validators.core import validate_literature_search_recency

    ms, cl = _lit_search_ms(
        "We conducted a systematic review of randomised controlled trials. "
        "We searched PubMed and PsycINFO for relevant studies."
    )
    result = validate_literature_search_recency(ms, cl)
    assert any(f.code == "missing-literature-search-date" for f in result.findings)


def test_lit_review_with_search_date_no_fire() -> None:
    from manuscript_audit.validators.core import validate_literature_search_recency

    ms, cl = _lit_search_ms(
        "We conducted a systematic review. The database search was last conducted "
        "in March 2024, covering studies published from 2000 to 2024."
    )
    result = validate_literature_search_recency(ms, cl)
    assert result.findings == []


def test_no_lit_review_no_fire() -> None:
    from manuscript_audit.validators.core import validate_literature_search_recency

    ms, cl = _lit_search_ms(
        "This study examined treatment outcomes in a prospective cohort."
    )
    result = validate_literature_search_recency(ms, cl)
    assert result.findings == []


def test_lit_search_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_literature_search_recency

    ms, cl = _lit_search_ms(
        "A systematic review was described as an example search strategy."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_literature_search_recency(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 284 – validate_publication_bias_acknowledgement
# ---------------------------------------------------------------------------

def _pub_bias_ack_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-pub-bias",
            source_path="/tmp/pub_bias.md",
            source_format="markdown",
            title="Publication Bias Acknowledgement Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_lit_review_without_pub_bias_fires() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_acknowledgement

    ms, cl = _pub_bias_ack_ms(
        "This systematic review synthesised the literature on cognitive behavioural "
        "therapy. Thirty-two studies met inclusion criteria."
    )
    result = validate_publication_bias_acknowledgement(ms, cl)
    assert any(
        f.code == "missing-publication-bias-acknowledgement" for f in result.findings
    )


def test_lit_review_with_pub_bias_mention_no_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_acknowledgement

    ms, cl = _pub_bias_ack_ms(
        "This narrative review acknowledges that publication bias may affect "
        "the available literature, as negative results are less likely to be published."
    )
    result = validate_publication_bias_acknowledgement(ms, cl)
    assert result.findings == []


def test_no_review_no_pub_bias_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_acknowledgement

    ms, cl = _pub_bias_ack_ms(
        "We conducted an RCT comparing two treatment conditions."
    )
    result = validate_publication_bias_acknowledgement(ms, cl)
    assert result.findings == []


def test_pub_bias_ack_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_publication_bias_acknowledgement

    ms, cl = _pub_bias_ack_ms(
        "Systematic review methodology was discussed in the introduction."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_publication_bias_acknowledgement(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 285 – validate_replication_citation
# ---------------------------------------------------------------------------

def _replication285_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-replication",
            source_path="/tmp/replication.md",
            source_format="markdown",
            title="Replication Citation Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_replication_claim_without_cite_fires() -> None:
    from manuscript_audit.validators.core import validate_replication_citation

    ms, cl = _replication285_ms(
        "Our results replicate earlier findings on the relationship between "
        "stress and health outcomes."
    )
    result = validate_replication_citation(ms, cl)
    assert any(f.code == "missing-replication-citation" for f in result.findings)


def test_replication_claim_with_cite_no_fire() -> None:
    from manuscript_audit.validators.core import validate_replication_citation

    ms, cl = _replication285_ms(
        "Our results replicate earlier findings (Smith et al., 2019) on the "
        "relationship between stress and health outcomes."
    )
    result = validate_replication_citation(ms, cl)
    assert result.findings == []


def test_no_replication_claim_no_fire() -> None:
    from manuscript_audit.validators.core import validate_replication_citation

    ms, cl = _replication285_ms(
        "We examined the effects of a novel intervention on anxiety symptoms."
    )
    result = validate_replication_citation(ms, cl)
    assert result.findings == []


def test_replication285_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_replication_citation

    ms, cl = _replication285_ms(
        "The study replicates a well-known mathematical finding."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_replication_citation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 286 – validate_negative_binomial_overdispersion
# ---------------------------------------------------------------------------

def _overdispersion_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-overdispersion",
            source_path="/tmp/overdispersion.md",
            source_format="markdown",
            title="Overdispersion Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_poisson_without_overdispersion_check_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_negative_binomial_overdispersion,
    )

    ms, cl = _overdispersion_ms(
        "We used Poisson regression to model the number of hospital visits. "
        "Results indicated a significant effect of treatment."
    )
    result = validate_negative_binomial_overdispersion(ms, cl)
    assert any(f.code == "missing-overdispersion-test" for f in result.findings)


def test_poisson_with_overdispersion_check_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_negative_binomial_overdispersion,
    )

    ms, cl = _overdispersion_ms(
        "We used Poisson regression to model count outcomes. Overdispersion was "
        "detected (dispersion parameter = 2.3) and addressed using a negative "
        "binomial model."
    )
    result = validate_negative_binomial_overdispersion(ms, cl)
    assert result.findings == []


def test_no_count_outcome_no_overdispersion_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_negative_binomial_overdispersion,
    )

    ms, cl = _overdispersion_ms(
        "We used linear regression to model continuous outcomes."
    )
    result = validate_negative_binomial_overdispersion(ms, cl)
    assert result.findings == []


def test_overdispersion_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_negative_binomial_overdispersion,
    )

    ms, cl = _overdispersion_ms("Poisson models for count data were discussed.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_negative_binomial_overdispersion(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 287 – validate_zero_inflated_data_handling
# ---------------------------------------------------------------------------

def _zero_inflate_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-zero-inflate",
            source_path="/tmp/zero_inflate.md",
            source_format="markdown",
            title="Zero Inflation Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_count_model_without_zero_inflation_check_fires() -> None:
    from manuscript_audit.validators.core import validate_zero_inflated_data_handling

    ms, cl = _zero_inflate_ms(
        "We modelled the number of incidents using Poisson regression. "
        "Count outcomes were analysed at the individual level."
    )
    result = validate_zero_inflated_data_handling(ms, cl)
    assert any(f.code == "missing-zero-inflation-handling" for f in result.findings)


def test_zero_inflated_model_used_no_fire() -> None:
    from manuscript_audit.validators.core import validate_zero_inflated_data_handling

    ms, cl = _zero_inflate_ms(
        "The frequency of events was modelled. Given the excess zeros in the "
        "distribution, we applied a zero-inflated Poisson (ZIP) model."
    )
    result = validate_zero_inflated_data_handling(ms, cl)
    assert result.findings == []


def test_no_count_data_no_zero_inflate_fire() -> None:
    from manuscript_audit.validators.core import validate_zero_inflated_data_handling

    ms, cl = _zero_inflate_ms(
        "Participants completed a survey measuring attitudes and beliefs."
    )
    result = validate_zero_inflated_data_handling(ms, cl)
    assert result.findings == []


def test_zero_inflate_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_zero_inflated_data_handling

    ms, cl = _zero_inflate_ms("Zero-inflated models are described theoretically.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_zero_inflated_data_handling(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 288 – validate_variance_homogeneity_check
# ---------------------------------------------------------------------------

def _homogeneity_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-homogeneity",
            source_path="/tmp/homogeneity.md",
            source_format="markdown",
            title="Variance Homogeneity Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_ttest_without_homogeneity_check_fires() -> None:
    from manuscript_audit.validators.core import validate_variance_homogeneity_check

    ms, cl = _homogeneity_ms(
        "We compared the two groups using an independent samples t-test. "
        "The intervention group showed significantly higher scores (p = .02)."
    )
    result = validate_variance_homogeneity_check(ms, cl)
    assert any(f.code == "missing-variance-homogeneity-check" for f in result.findings)


def test_ttest_with_levene_no_fire() -> None:
    from manuscript_audit.validators.core import validate_variance_homogeneity_check

    ms, cl = _homogeneity_ms(
        "Levene's test confirmed homogeneity of variance (p = .43). "
        "We then conducted an independent samples t-test."
    )
    result = validate_variance_homogeneity_check(ms, cl)
    assert result.findings == []


def test_no_between_group_test_no_fire() -> None:
    from manuscript_audit.validators.core import validate_variance_homogeneity_check

    ms, cl = _homogeneity_ms(
        "We used structural equation modelling to test the mediation model."
    )
    result = validate_variance_homogeneity_check(ms, cl)
    assert result.findings == []


def test_homogeneity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_variance_homogeneity_check

    ms, cl = _homogeneity_ms(
        "The t-test assumptions include homogeneity of variance."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_variance_homogeneity_check(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 289 – validate_path_model_fit_indices
# ---------------------------------------------------------------------------

def _path_model_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-path-model",
            source_path="/tmp/path_model.md",
            source_format="markdown",
            title="Path Model Fit Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_sem289_without_fit_indices_fires() -> None:
    from manuscript_audit.validators.core import validate_path_model_fit_indices

    ms, cl = _path_model_ms(
        "We used structural equation modelling to test our hypotheses. "
        "The SEM showed that stress significantly predicted burnout."
    )
    result = validate_path_model_fit_indices(ms, cl)
    assert any(f.code == "missing-path-model-fit-indices" for f in result.findings)


def test_sem289_with_fit_indices_no_fire() -> None:
    from manuscript_audit.validators.core import validate_path_model_fit_indices

    ms, cl = _path_model_ms(
        "The structural equation model showed good fit: CFI = .96, TLI = .95, "
        "RMSEA = .05, SRMR = .06. All hypothesised paths were significant."
    )
    result = validate_path_model_fit_indices(ms, cl)
    assert result.findings == []


def test_no_sem_no_fit_fire() -> None:
    from manuscript_audit.validators.core import validate_path_model_fit_indices

    ms, cl = _path_model_ms(
        "We used linear regression to predict the outcome variable."
    )
    result = validate_path_model_fit_indices(ms, cl)
    assert result.findings == []


def test_path_model_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_path_model_fit_indices

    ms, cl = _path_model_ms(
        "Structural equation modelling is a flexible framework for theory testing."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_path_model_fit_indices(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 290 – validate_post_hoc_power_caution
# ---------------------------------------------------------------------------

def _posthoc_power_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-posthoc-power",
            source_path="/tmp/posthoc_power.md",
            source_format="markdown",
            title="Post-hoc Power Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_post_hoc_power_without_caveat_fires() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_power_caution

    ms, cl = _posthoc_power_ms(
        "Post-hoc power analysis revealed that our study had 62% power to detect "
        "the observed effect size."
    )
    result = validate_post_hoc_power_caution(ms, cl)
    assert any(f.code == "missing-post-hoc-power-caution" for f in result.findings)


def test_post_hoc_power_with_caveat_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_power_caution

    ms, cl = _posthoc_power_ms(
        "Observed power was 62%. Caution is warranted when interpreting observed "
        "power, as post-hoc power analysis has been widely criticised (Hoenig, 2001)."
    )
    result = validate_post_hoc_power_caution(ms, cl)
    assert result.findings == []


def test_no_post_hoc_power_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_power_caution

    ms, cl = _posthoc_power_ms(
        "A priori power analysis indicated a required sample of N = 120."
    )
    result = validate_post_hoc_power_caution(ms, cl)
    assert result.findings == []


def test_post_hoc_power_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_post_hoc_power_caution

    ms, cl = _posthoc_power_ms(
        "Post-hoc power analysis is discussed as a methodological issue."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_post_hoc_power_caution(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 291 – validate_ancova_covariate_balance
# ---------------------------------------------------------------------------

def _ancova_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-ancova",
            source_path="/tmp/ancova.md",
            source_format="markdown",
            title="ANCOVA Covariate Balance Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_ancova_without_covariate_balance_fires() -> None:
    from manuscript_audit.validators.core import validate_ancova_covariate_balance

    ms, cl = _ancova_ms(
        "We used ANCOVA to compare groups while adjusting for baseline scores. "
        "The treatment group showed significantly higher outcomes (p = .03)."
    )
    result = validate_ancova_covariate_balance(ms, cl)
    assert any(f.code == "missing-ancova-covariate-balance" for f in result.findings)


def test_ancova_with_covariate_balance_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ancova_covariate_balance

    ms, cl = _ancova_ms(
        "ANCOVA was used to adjust for baseline scores. No significant group "
        "difference on the covariate was found before intervention (p = .61), "
        "confirming covariate balance across conditions."
    )
    result = validate_ancova_covariate_balance(ms, cl)
    assert result.findings == []


def test_no_ancova_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ancova_covariate_balance

    ms, cl = _ancova_ms(
        "We used a paired samples t-test to compare pre- and post-scores."
    )
    result = validate_ancova_covariate_balance(ms, cl)
    assert result.findings == []


def test_ancova_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_ancova_covariate_balance

    ms, cl = _ancova_ms("ANCOVA adjusts for covariates to reduce error variance.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_ancova_covariate_balance(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 292 – validate_partial_eta_squared_reporting
# ---------------------------------------------------------------------------

def _anova_eta_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-anova-eta",
            source_path="/tmp/anova_eta.md",
            source_format="markdown",
            title="ANOVA Effect Size Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_anova_without_eta_squared_fires() -> None:
    from manuscript_audit.validators.core import validate_partial_eta_squared_reporting

    ms, cl = _anova_eta_ms(
        "A one-way ANOVA revealed a significant group effect, F(2, 147) = 8.43, "
        "p = .0003."
    )
    result = validate_partial_eta_squared_reporting(ms, cl)
    assert any(f.code == "missing-partial-eta-squared" for f in result.findings)


def test_anova_with_eta_squared_no_fire() -> None:
    from manuscript_audit.validators.core import validate_partial_eta_squared_reporting

    ms, cl = _anova_eta_ms(
        "A two-way ANOVA revealed a significant interaction, F(1, 198) = 12.3, "
        "p = .0006, partial η² = .06."
    )
    result = validate_partial_eta_squared_reporting(ms, cl)
    assert result.findings == []


def test_no_anova_no_eta_fire() -> None:
    from manuscript_audit.validators.core import validate_partial_eta_squared_reporting

    ms, cl = _anova_eta_ms(
        "A Spearman correlation was computed between the two variables."
    )
    result = validate_partial_eta_squared_reporting(ms, cl)
    assert result.findings == []


def test_anova_eta_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_partial_eta_squared_reporting

    ms, cl = _anova_eta_ms(
        "Partial eta-squared measures the proportion of variance explained by a factor."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_partial_eta_squared_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 293 – validate_cohens_d_reporting
# ---------------------------------------------------------------------------

def _cohens_d_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cohens-d",
            source_path="/tmp/cohens_d.md",
            source_format="markdown",
            title="Cohen's d Reporting Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_ttest_without_cohens_d_fires() -> None:
    from manuscript_audit.validators.core import validate_cohens_d_reporting

    ms, cl = _cohens_d_ms(
        "An independent samples t-test revealed a significant difference between "
        "groups, t(98) = 2.34, p = .02."
    )
    result = validate_cohens_d_reporting(ms, cl)
    assert any(f.code == "missing-cohens-d" for f in result.findings)


def test_ttest_with_cohens_d_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cohens_d_reporting

    ms, cl = _cohens_d_ms(
        "An independent samples t-test revealed a significant difference, "
        "t(98) = 2.34, p = .02, Cohen's d = 0.47."
    )
    result = validate_cohens_d_reporting(ms, cl)
    assert result.findings == []


def test_no_ttest_no_cohens_d_fire() -> None:
    from manuscript_audit.validators.core import validate_cohens_d_reporting

    ms, cl = _cohens_d_ms(
        "A logistic regression was used to predict binary outcomes."
    )
    result = validate_cohens_d_reporting(ms, cl)
    assert result.findings == []


def test_cohens_d_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_cohens_d_reporting

    ms, cl = _cohens_d_ms(
        "Cohen's d quantifies the standardised mean difference between two groups."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_cohens_d_reporting(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 294 – validate_sequential_testing_correction
# ---------------------------------------------------------------------------

def _sequential_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-sequential",
            source_path="/tmp/sequential.md",
            source_format="markdown",
            title="Sequential Testing Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_interim_analysis_without_alpha_spending_fires() -> None:
    from manuscript_audit.validators.core import validate_sequential_testing_correction

    ms, cl = _sequential_ms(
        "We conducted two interim analyses and a final analysis. The data "
        "monitoring committee reviewed results at each stage."
    )
    result = validate_sequential_testing_correction(ms, cl)
    assert any(
        f.code == "missing-sequential-testing-correction" for f in result.findings
    )


def test_interim_analysis_with_alpha_spending_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sequential_testing_correction

    ms, cl = _sequential_ms(
        "Two interim analyses were conducted. The alpha-spending function "
        "(O'Brien-Fleming bounds) was used to control Type I error across stages."
    )
    result = validate_sequential_testing_correction(ms, cl)
    assert result.findings == []


def test_no_interim_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sequential_testing_correction

    ms, cl = _sequential_ms(
        "A single analysis was conducted after all data were collected."
    )
    result = validate_sequential_testing_correction(ms, cl)
    assert result.findings == []


def test_sequential_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_sequential_testing_correction

    ms, cl = _sequential_ms(
        "Sequential testing methods allow for interim looks at accumulating data."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_sequential_testing_correction(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 295 – validate_adaptive_design_disclosure
# ---------------------------------------------------------------------------

def _adaptive_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-adaptive",
            source_path="/tmp/adaptive.md",
            source_format="markdown",
            title="Adaptive Design Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_adaptive_trial_without_disclosure_fires() -> None:
    from manuscript_audit.validators.core import validate_adaptive_design_disclosure

    ms, cl = _adaptive_ms(
        "We used an adaptive design to allow sample size reassessment at the "
        "midpoint of the trial based on observed effect sizes."
    )
    result = validate_adaptive_design_disclosure(ms, cl)
    assert any(
        f.code == "missing-adaptive-design-disclosure" for f in result.findings
    )


def test_adaptive_trial_with_disclosure_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adaptive_design_disclosure

    ms, cl = _adaptive_ms(
        "The adaptive sample size re-estimation rule was pre-specified in the "
        "protocol. Type I error was controlled across adaptations using a "
        "blinded sample size reassessment procedure."
    )
    result = validate_adaptive_design_disclosure(ms, cl)
    assert result.findings == []


def test_no_adaptive_design_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adaptive_design_disclosure

    ms, cl = _adaptive_ms(
        "Participants were randomly allocated to conditions in a fixed-sample design."
    )
    result = validate_adaptive_design_disclosure(ms, cl)
    assert result.findings == []


def test_adaptive_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_adaptive_design_disclosure

    ms, cl = _adaptive_ms(
        "Adaptive randomisation improves allocation efficiency in clinical trials."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_adaptive_design_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 296 – validate_kaplan_meier_censoring_note
# ---------------------------------------------------------------------------

def _km_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-km",
            source_path="/tmp/km.md",
            source_format="markdown",
            title="Kaplan-Meier Censoring Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_km_without_censoring_note_fires() -> None:
    from manuscript_audit.validators.core import validate_kaplan_meier_censoring_note

    ms, cl = _km_ms(
        "Kaplan-Meier curves were plotted for overall survival. Median survival "
        "was 18 months in the treatment group versus 12 months in controls."
    )
    result = validate_kaplan_meier_censoring_note(ms, cl)
    assert any(f.code == "missing-km-censoring-note" for f in result.findings)


def test_km_with_censoring_note_no_fire() -> None:
    from manuscript_audit.validators.core import validate_kaplan_meier_censoring_note

    ms, cl = _km_ms(
        "Kaplan-Meier survival curves were produced. Participants lost to "
        "follow-up were right-censored at their last known alive date. "
        "Tick marks on the curves denote censoring events."
    )
    result = validate_kaplan_meier_censoring_note(ms, cl)
    assert result.findings == []


def test_km296_no_survival_analysis_no_fire() -> None:
    from manuscript_audit.validators.core import validate_kaplan_meier_censoring_note

    ms, cl = _km_ms(
        "We used logistic regression to predict treatment response at 12 weeks."
    )
    result = validate_kaplan_meier_censoring_note(ms, cl)
    assert result.findings == []


def test_km_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_kaplan_meier_censoring_note

    ms, cl = _km_ms("Kaplan-Meier estimation is a nonparametric method.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_kaplan_meier_censoring_note(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 297 – validate_cox_proportional_hazards_assumption
# ---------------------------------------------------------------------------

def _cox_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cox",
            source_path="/tmp/cox.md",
            source_format="markdown",
            title="Cox PH Assumption Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_cox_without_ph_assumption_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_cox_proportional_hazards_assumption,
    )

    ms, cl = _cox_ms(
        "We used Cox proportional hazards regression to estimate the hazard ratio "
        "for treatment versus control. HR = 0.62 (95% CI: 0.48–0.79), p < .001."
    )
    result = validate_cox_proportional_hazards_assumption(ms, cl)
    assert any(f.code == "missing-cox-ph-assumption-check" for f in result.findings)


def test_cox_with_ph_assumption_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cox_proportional_hazards_assumption,
    )

    ms, cl = _cox_ms(
        "Cox proportional hazards regression was used. The proportional hazards "
        "assumption was tested using Schoenfeld residuals and was not violated."
    )
    result = validate_cox_proportional_hazards_assumption(ms, cl)
    assert result.findings == []


def test_no_cox_model_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cox_proportional_hazards_assumption,
    )

    ms, cl = _cox_ms(
        "A linear regression model was fitted to the continuous outcome."
    )
    result = validate_cox_proportional_hazards_assumption(ms, cl)
    assert result.findings == []


def test_cox_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cox_proportional_hazards_assumption,
    )

    ms, cl = _cox_ms("Cox proportional hazards is a widely used survival model.")
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_cox_proportional_hazards_assumption(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 298 – validate_competing_risks_disclosure
# ---------------------------------------------------------------------------

def _competing_risk_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-comp-risk",
            source_path="/tmp/comp_risk.md",
            source_format="markdown",
            title="Competing Risks Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_tte_without_competing_risks_fires() -> None:
    from manuscript_audit.validators.core import validate_competing_risks_disclosure

    ms, cl = _competing_risk_ms(
        "Time-to-event analysis was used for disease recurrence as the event "
        "of interest. Overall survival was also tracked."
    )
    result = validate_competing_risks_disclosure(ms, cl)
    assert any(f.code == "missing-competing-risks-disclosure" for f in result.findings)


def test_tte_with_competing_risks_handled_no_fire() -> None:
    from manuscript_audit.validators.core import validate_competing_risks_disclosure

    ms, cl = _competing_risk_ms(
        "Given the presence of competing risks (death from other causes), we "
        "used the Fine-Gray subdistribution hazard model for disease recurrence."
    )
    result = validate_competing_risks_disclosure(ms, cl)
    assert result.findings == []


def test_no_tte_no_competing_fire() -> None:
    from manuscript_audit.validators.core import validate_competing_risks_disclosure

    ms, cl = _competing_risk_ms(
        "Binary outcomes were analysed with logistic regression."
    )
    result = validate_competing_risks_disclosure(ms, cl)
    assert result.findings == []


def test_competing_risks_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_competing_risks_disclosure

    ms, cl = _competing_risk_ms(
        "Competing risks arise when multiple types of events can terminate follow-up."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_competing_risks_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 299 – validate_propensity_score_balance
# ---------------------------------------------------------------------------

def _propensity_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-propensity",
            source_path="/tmp/propensity.md",
            source_format="markdown",
            title="Propensity Score Balance Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_propensity_without_balance_check_fires() -> None:
    from manuscript_audit.validators.core import validate_propensity_score_balance

    ms, cl = _propensity_ms(
        "We used propensity score matching to create comparable groups. "
        "After matching, 120 matched pairs were retained for analysis."
    )
    result = validate_propensity_score_balance(ms, cl)
    assert any(f.code == "missing-propensity-balance-check" for f in result.findings)


def test_propensity_with_balance_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_propensity_score_balance

    ms, cl = _propensity_ms(
        "Propensity score matching was used. Post-matching covariate balance "
        "was assessed using standardised mean differences (SMDs < 0.10 for all "
        "covariates), confirming adequate balance."
    )
    result = validate_propensity_score_balance(ms, cl)
    assert result.findings == []


def test_no_propensity_no_fire() -> None:
    from manuscript_audit.validators.core import validate_propensity_score_balance

    ms, cl = _propensity_ms(
        "Randomisation ensured group equivalence. No matching was required."
    )
    result = validate_propensity_score_balance(ms, cl)
    assert result.findings == []


def test_propensity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_propensity_score_balance

    ms, cl = _propensity_ms(
        "Propensity score matching reduces confounding in observational studies."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_propensity_score_balance(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 300 – validate_instrumental_variable_disclosure
# ---------------------------------------------------------------------------

def _iv_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-iv",
            source_path="/tmp/iv.md",
            source_format="markdown",
            title="Instrumental Variable Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_iv_without_validity_argument_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_instrumental_variable_disclosure,
    )

    ms, cl = _iv_ms(
        "We used two-stage least squares (2SLS) with rainfall as an instrumental "
        "variable for agricultural income to estimate the causal effect on health."
    )
    result = validate_instrumental_variable_disclosure(ms, cl)
    assert any(f.code == "missing-iv-validity-argument" for f in result.findings)


def test_iv_with_validity_argument_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_instrumental_variable_disclosure,
    )

    ms, cl = _iv_ms(
        "We used 2SLS with a genetic instrument. The first-stage F-statistic "
        "was 48.3, indicating instrument relevance. The exclusion restriction "
        "was argued based on the biological pathway."
    )
    result = validate_instrumental_variable_disclosure(ms, cl)
    assert result.findings == []


def test_no_iv_method_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_instrumental_variable_disclosure,
    )

    ms, cl = _iv_ms(
        "We used ordinary least squares regression to estimate treatment effects."
    )
    result = validate_instrumental_variable_disclosure(ms, cl)
    assert result.findings == []


def test_iv_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_instrumental_variable_disclosure,
    )

    ms, cl = _iv_ms(
        "Instrumental variables exploit exogenous variation to estimate causal effects."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_instrumental_variable_disclosure(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 301 – validate_multilevel_random_effects_justification
# ---------------------------------------------------------------------------

def _multilevel_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-multilevel",
            source_path="/tmp/multilevel.md",
            source_format="markdown",
            title="Multilevel Random Effects Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_multilevel_without_re_justification_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_multilevel_random_effects_justification,
    )

    ms, cl = _multilevel_ms(
        "We used a multilevel model to account for the nested structure "
        "of students within schools. Fixed effects were estimated."
    )
    result = validate_multilevel_random_effects_justification(ms, cl)
    assert any(
        f.code == "missing-random-effects-justification" for f in result.findings
    )


def test_multilevel301_with_icc_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_multilevel_random_effects_justification,
    )

    ms, cl = _multilevel_ms(
        "A multilevel model was justified by the non-trivial ICC = .18, "
        "indicating substantial between-school variance in outcomes."
    )
    result = validate_multilevel_random_effects_justification(ms, cl)
    assert result.findings == []


def test_no_multilevel_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_multilevel_random_effects_justification,
    )

    ms, cl = _multilevel_ms(
        "We used ordinary least squares regression with robust standard errors."
    )
    result = validate_multilevel_random_effects_justification(ms, cl)
    assert result.findings == []


def test_multilevel_re_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_multilevel_random_effects_justification,
    )

    ms, cl = _multilevel_ms(
        "Multilevel models handle nested data structures through random effects."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_multilevel_random_effects_justification(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 302 – validate_cross_level_interaction_interpretation
# ---------------------------------------------------------------------------

def _cross_level_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-cross-level",
            source_path="/tmp/cross_level.md",
            source_format="markdown",
            title="Cross-Level Interaction Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_cross_level_without_interpretation_fires() -> None:
    from manuscript_audit.validators.core import (
        validate_cross_level_interaction_interpretation,
    )

    ms, cl = _cross_level_ms(
        "A significant cross-level interaction was found between individual "
        "autonomy (Level 1) and leadership style (Level 2)."
    )
    result = validate_cross_level_interaction_interpretation(ms, cl)
    assert any(
        f.code == "missing-cross-level-interaction-interpretation"
        for f in result.findings
    )


def test_cross_level_with_interpretation_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cross_level_interaction_interpretation,
    )

    ms, cl = _cross_level_ms(
        "The cross-level interaction was significant. Simple slopes were "
        "examined at high and low levels of leadership style to interpret "
        "how the Level-2 variable moderated the relationship."
    )
    result = validate_cross_level_interaction_interpretation(ms, cl)
    assert result.findings == []


def test_no_cross_level_interaction_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cross_level_interaction_interpretation,
    )

    ms, cl = _cross_level_ms(
        "Main effects were examined with no interaction terms included."
    )
    result = validate_cross_level_interaction_interpretation(ms, cl)
    assert result.findings == []


def test_cross_level_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import (
        validate_cross_level_interaction_interpretation,
    )

    ms, cl = _cross_level_ms(
        "Cross-level interactions require random slopes to be estimated."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_cross_level_interaction_interpretation(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 303 – validate_repeated_measures_sphericity
# ---------------------------------------------------------------------------

def _rm_anova_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-rm-anova",
            source_path="/tmp/rm_anova.md",
            source_format="markdown",
            title="Repeated Measures Sphericity Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_rm_anova_without_sphericity_fires() -> None:
    from manuscript_audit.validators.core import validate_repeated_measures_sphericity

    ms, cl = _rm_anova_ms(
        "A repeated-measures ANOVA was conducted to assess change across "
        "three time points. A significant main effect of time was found, "
        "F(2, 196) = 14.2, p < .001."
    )
    result = validate_repeated_measures_sphericity(ms, cl)
    assert any(f.code == "missing-sphericity-correction" for f in result.findings)


def test_rm_anova_with_sphericity_check_no_fire() -> None:
    from manuscript_audit.validators.core import validate_repeated_measures_sphericity

    ms, cl = _rm_anova_ms(
        "A repeated-measures ANOVA was conducted. Mauchly's test indicated "
        "that sphericity was violated; therefore, Greenhouse-Geisser correction "
        "was applied."
    )
    result = validate_repeated_measures_sphericity(ms, cl)
    assert result.findings == []


def test_no_rm_anova_no_sphericity_fire() -> None:
    from manuscript_audit.validators.core import validate_repeated_measures_sphericity

    ms, cl = _rm_anova_ms(
        "A between-subjects ANOVA was used to compare three conditions."
    )
    result = validate_repeated_measures_sphericity(ms, cl)
    assert result.findings == []


def test_rm_sphericity_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_repeated_measures_sphericity

    ms, cl = _rm_anova_ms(
        "Sphericity is an assumption of repeated-measures ANOVA."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_repeated_measures_sphericity(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 304 – validate_survey_sampling_weight
# ---------------------------------------------------------------------------

def _survey_weight_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-survey-weight",
            source_path="/tmp/survey_weight.md",
            source_format="markdown",
            title="Survey Sampling Weight Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_complex_survey_without_weights_fires() -> None:
    from manuscript_audit.validators.core import validate_survey_sampling_weight

    ms, cl = _survey_weight_ms(
        "Data were drawn from a nationally representative survey of households. "
        "Logistic regression was used to predict health outcomes."
    )
    result = validate_survey_sampling_weight(ms, cl)
    assert any(f.code == "missing-survey-weight-disclosure" for f in result.findings)


def test_complex_survey_with_weights_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_sampling_weight

    ms, cl = _survey_weight_ms(
        "Data were from a nationally representative survey. Sampling weights "
        "were applied using design-based analysis to account for the complex "
        "survey design."
    )
    result = validate_survey_sampling_weight(ms, cl)
    assert result.findings == []


def test_no_complex_survey_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_sampling_weight

    ms, cl = _survey_weight_ms(
        "Participants were recruited from two university campuses."
    )
    result = validate_survey_sampling_weight(ms, cl)
    assert result.findings == []


def test_survey_weight_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_survey_sampling_weight

    ms, cl = _survey_weight_ms(
        "Complex survey design requires the application of sampling weights."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_survey_sampling_weight(ms, cl)
    assert result.findings == []


# ---------------------------------------------------------------------------
# Phase 305 – validate_finite_population_correction
# ---------------------------------------------------------------------------

def _finite_pop_ms(body: str) -> tuple[ParsedManuscript, ManuscriptClassification]:
    return (
        ParsedManuscript(
            manuscript_id="md-finite-pop",
            source_path="/tmp/finite_pop.md",
            source_format="markdown",
            title="Finite Population Correction Test",
            full_text=body,
            sections=[],
        ),
        ManuscriptClassification(
            pathway="applied_stats",
            paper_type="empirical_paper",
            recommended_stack="standard",
        ),
    )


def test_census_data_without_fpc_fires() -> None:
    from manuscript_audit.validators.core import validate_finite_population_correction

    ms, cl = _finite_pop_ms(
        "We surveyed all employees in the organization. Complete population "
        "data were analysed using standard regression."
    )
    result = validate_finite_population_correction(ms, cl)
    assert any(f.code == "missing-finite-population-correction" for f in result.findings)


def test_census_data_with_fpc_no_fire() -> None:
    from manuscript_audit.validators.core import validate_finite_population_correction

    ms, cl = _finite_pop_ms(
        "All students in the school were surveyed (complete population data). "
        "The finite population correction (FPC) was applied because the sampling "
        "fraction exceeded 20%."
    )
    result = validate_finite_population_correction(ms, cl)
    assert result.findings == []


def test_no_census_no_fpc_fire() -> None:
    from manuscript_audit.validators.core import validate_finite_population_correction

    ms, cl = _finite_pop_ms(
        "A random sample of 200 participants was recruited from a large registry."
    )
    result = validate_finite_population_correction(ms, cl)
    assert result.findings == []


def test_fpc_non_empirical_no_fire() -> None:
    from manuscript_audit.validators.core import validate_finite_population_correction

    ms, cl = _finite_pop_ms(
        "Finite population correction adjusts variance estimates for large samples."
    )
    cl = ManuscriptClassification(
        pathway="math_stats_theory",
        paper_type="math_theory_paper",
        recommended_stack="minimal",
    )
    result = validate_finite_population_correction(ms, cl)
    assert result.findings == []
