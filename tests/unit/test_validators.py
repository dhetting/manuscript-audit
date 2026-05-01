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
    paper_type: str = "empirical_research_paper",
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
    paper_type: str = "empirical_research_paper",
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
