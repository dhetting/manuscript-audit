from __future__ import annotations

from manuscript_audit.agents.modules import (
    AIRiskAuditAgent,
    BibliographyMetadataAgent,
    MathProofsNotationAgent,
    ReproducibilityAuditAgent,
    ResultsConsistencyAgent,
    StatisticalValidityAgent,
    StructureContributionAgent,
    StubRoutedAgent,
)
from manuscript_audit.schemas.artifacts import (
    BibliographyConfidenceSummary,
    ParsedManuscript,
    SourceRecordVerification,
)
from manuscript_audit.schemas.findings import AgentSuiteResult, ValidationSuiteResult
from manuscript_audit.schemas.routing import ManuscriptClassification, ModuleRoutingTable

AGENT_VERSION = "agents-mvp-v1"


def _agent_for_module(module_name: str):
    registry = {
        "structure_contribution_and_fit": StructureContributionAgent(),
        "bibliography_metadata_validation": BibliographyMetadataAgent(),
        "statistical_validity_and_assumptions": StatisticalValidityAgent(),
        "results_figures_tables_consistency": ResultsConsistencyAgent(),
        "reproducibility_and_computational_audit": ReproducibilityAuditAgent(),
        "ai_generated_manuscript_risk_audit": AIRiskAuditAgent(),
        "math_proofs_and_notation": MathProofsNotationAgent(),
    }
    return registry.get(module_name, StubRoutedAgent(module_name))


def run_routed_agents(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
    validation_suite: ValidationSuiteResult,
    module_routing: ModuleRoutingTable,
    source_verifications: list[SourceRecordVerification] | None = None,
    bibliography_confidence_summary: BibliographyConfidenceSummary | None = None,
) -> AgentSuiteResult:
    results = []
    for applicability in module_routing.modules:
        if not applicability.applicable:
            continue
        agent = _agent_for_module(applicability.name)
        results.append(
            agent.run(
                parsed=parsed,
                classification=classification,
                validation_suite=validation_suite,
                applicability=applicability,
                source_verifications=source_verifications,
                bibliography_confidence_summary=bibliography_confidence_summary,
            )
        )
    return AgentSuiteResult(agent_version=AGENT_VERSION, results=results)
