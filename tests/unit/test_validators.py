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
