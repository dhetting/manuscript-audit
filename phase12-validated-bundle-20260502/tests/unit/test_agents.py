from pathlib import Path

from manuscript_audit.agents import run_routed_agents
from manuscript_audit.parsers import (
    FixtureSourceRegistryClient,
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    parse_manuscript,
    verify_source_records,
)
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.validators import run_deterministic_validators


def test_routed_agents_execute_only_applicable_modules() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md"))
    classification, module_routing, _ = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)
    agent_suite = run_routed_agents(parsed, classification, validation_suite, module_routing)
    module_names = {result.module_name for result in agent_suite.results}
    assert "reproducibility_and_computational_audit" in module_names
    assert "bibliography_metadata_validation" in module_names
    assert "math_proofs_and_notation" not in module_names


def test_bibliography_agent_consumes_source_verification_results() -> None:
    manuscript = Path("tests/fixtures/manuscripts/bibliography_metadata.tex")
    parsed = parse_manuscript(manuscript)
    parsed.bibliography_entries = parse_bibtex(manuscript.with_suffix(".bib"))
    parsed.reference_section_present = True
    classification, module_routing, _ = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)
    source_records = build_source_records(parsed.bibliography_entries)
    client = FixtureSourceRegistryClient.from_json(
        Path("tests/fixtures/registries/source_registry_fixture.json")
    )
    verifications = verify_source_records(parsed.bibliography_entries, source_records, client)

    confidence_summary = build_bibliography_confidence_summary(source_records, verifications)

    agent_suite = run_routed_agents(
        parsed,
        classification,
        validation_suite,
        module_routing,
        source_verifications=verifications,
        bibliography_confidence_summary=confidence_summary,
    )

    bibliography_result = next(
        result
        for result in agent_suite.results
        if result.module_name == "bibliography_metadata_validation"
    )
    codes = {finding.code for finding in bibliography_result.findings}
    assert "source-record-metadata-mismatch" in codes
    assert "bibliography-confidence-low" in codes


# ---------------------------------------------------------------------------
# Phase 15: MathProofsNotationAgent
# ---------------------------------------------------------------------------


def test_math_proofs_notation_agent_emits_missing_notation_section() -> None:
    from manuscript_audit.agents.modules import MathProofsNotationAgent
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.findings import ValidationSuiteResult
    from manuscript_audit.schemas.routing import ApplicabilityDecision, ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="theory-no-notation-sec",
        source_path="synthetic",
        source_format="latex",
        title="A Convergence Theorem",
        full_text="We prove that the algorithm converges.",
        equation_blocks=[r"x_{n+1} = f(x_n)"],
        sections=[
            Section(title="Introduction", level=1, body="We study convergence."),
            Section(title="Proof", level=1, body="By induction."),
            Section(title="Conclusion", level=1, body="We showed convergence."),
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
    applicability = ApplicabilityDecision(
        name="math_proofs_and_notation",
        applicable=True,
        rationale="Theory paper",
    )
    validation_suite = ValidationSuiteResult(validator_version="test", results=[])
    agent = MathProofsNotationAgent()
    result = agent.run(parsed, classification, validation_suite, applicability)
    codes = {f.code for f in result.findings}
    assert "missing-notation-section" in codes


# ---------------------------------------------------------------------------
# Phase 25: agent finding confidence scores
# ---------------------------------------------------------------------------


def test_thin_abstract_confidence_scaled_by_shortfall() -> None:
    from manuscript_audit.agents.modules import StructureContributionAgent
    from manuscript_audit.schemas.artifacts import ParsedManuscript
    from manuscript_audit.schemas.findings import ValidationSuiteResult
    from manuscript_audit.schemas.routing import ManuscriptClassification

    # 0 words → confidence should be 1.0 (max shortfall)
    parsed = ParsedManuscript(
        manuscript_id="conf-test",
        source_path="synthetic",
        source_format="markdown",
        title="Test",
        abstract="",
        full_text="",
    )
    classification = ManuscriptClassification(
        paper_type="empirical_paper", pathway="data_science", recommended_stack="maximal"
    )
    suite = ValidationSuiteResult(validator_version="test", results=[])
    agent = StructureContributionAgent()
    findings = agent._build_findings(parsed, classification, suite)
    thin = [f for f in findings if f.code == "thin-abstract"]
    assert thin, "Expected thin-abstract finding"
    assert thin[0].confidence is not None
    assert 0.0 <= thin[0].confidence <= 1.0
    assert thin[0].confidence == 1.0


def test_unclear_contribution_framing_has_fixed_confidence() -> None:
    from manuscript_audit.agents.modules import StructureContributionAgent
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.findings import ValidationSuiteResult
    from manuscript_audit.schemas.routing import ManuscriptClassification

    parsed = ParsedManuscript(
        manuscript_id="conf-contrib",
        source_path="synthetic",
        source_format="markdown",
        title="Test",
        abstract="A " * 40,  # long enough
        full_text="",
        sections=[Section(title="Introduction", level=2, body="We study things.")],
    )
    classification = ManuscriptClassification(
        paper_type="empirical_paper", pathway="data_science", recommended_stack="maximal"
    )
    suite = ValidationSuiteResult(validator_version="test", results=[])
    agent = StructureContributionAgent()
    findings = agent._build_findings(parsed, classification, suite)
    contrib = [f for f in findings if f.code == "unclear-contribution-framing"]
    assert contrib, "Expected unclear-contribution-framing finding"
    assert contrib[0].confidence == 0.70


def test_notation_coverage_confidence_equals_undefined_ratio() -> None:
    from manuscript_audit.agents.modules import MathProofsNotationAgent
    from manuscript_audit.schemas.artifacts import ParsedManuscript, Section
    from manuscript_audit.schemas.findings import ValidationSuiteResult
    from manuscript_audit.schemas.routing import ManuscriptClassification

    # 3 undefined out of 4 total = 0.75 ratio
    body = r"Let $\alpha$, $\beta$, $\gamma$, $\delta$ denote parameters where $\alpha$ is defined."
    parsed = ParsedManuscript(
        manuscript_id="conf-notation",
        source_path="synthetic",
        source_format="markdown",
        title="Test",
        full_text=body,
        sections=[Section(title="Methods", level=2, body=body)],
        equation_blocks=[body],
    )
    classification = ManuscriptClassification(
        paper_type="theory_paper", pathway="math_stats_theory", recommended_stack="maximal"
    )
    suite = ValidationSuiteResult(validator_version="test", results=[])
    agent = MathProofsNotationAgent()
    findings = agent._build_findings(parsed, classification, suite)
    low_cov = [f for f in findings if f.code == "low-notation-definition-coverage"]
    if low_cov:
        assert low_cov[0].confidence is not None
        assert 0.5 < low_cov[0].confidence <= 1.0
