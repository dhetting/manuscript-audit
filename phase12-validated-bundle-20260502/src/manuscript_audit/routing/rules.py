from __future__ import annotations

from manuscript_audit.config import DEFAULT_ROUTE_VERSION
from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.routing import (
    ApplicabilityDecision,
    DomainRoutingTable,
    ManuscriptClassification,
    ModuleRoutingTable,
)


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def classify_manuscript(parsed: ParsedManuscript) -> ManuscriptClassification:
    text = parsed.full_text.lower()
    theorem_keywords = {"theorem", "lemma", "proposition", "proof", "corollary"}
    software_keywords = {"software", "workflow", "package", "repository", "cli", "pipeline"}
    simulation_keywords = {"simulation", "monte carlo"}
    empirical_keywords = {"data", "results", "experiment", "observations", "sample"}
    equivalence_keywords = {
        "equivalence",
        "equivalent",
        "noninferiority",
        "bioequivalence",
        "tost",
    }
    spatial_keywords = {"spatial", "spatiotemporal", "gaussian process", "variogram"}
    ai_keywords = {"chatgpt", "llm", "ai-generated", "language model"}

    if _contains_any(text, theorem_keywords):
        pathway = "math_stats_theory"
        paper_type = "theory_paper"
    elif _contains_any(text, software_keywords):
        pathway = "data_science"
        paper_type = "software_workflow_paper"
    elif _contains_any(text, empirical_keywords | equivalence_keywords | spatial_keywords):
        pathway = "applied_stats"
        paper_type = "empirical_statistical_study"
    else:
        pathway = "unknown"
        paper_type = "unclassified_manuscript"

    evidence_types: list[str] = []
    if _contains_any(text, theorem_keywords):
        evidence_types.append("theorem_or_proof")
    if _contains_any(text, simulation_keywords):
        evidence_types.append("simulation")
    if _contains_any(text, empirical_keywords):
        evidence_types.append("empirical_data")
    if _contains_any(text, software_keywords):
        evidence_types.append("software_artifact")

    claim_types: list[str] = []
    if _contains_any(text, equivalence_keywords):
        claim_types.append("equivalence")
    if _contains_any(text, {"predict", "forecast", "prediction"}):
        claim_types.append("prediction")
    if _contains_any(text, {"causal", "treatment effect", "propensity"}):
        claim_types.append("causal")
    if _contains_any(text, theorem_keywords):
        claim_types.append("theoretical")

    high_risk_features: list[str] = []
    if _contains_any(text, equivalence_keywords):
        high_risk_features.append("decision_relevant_equivalence_claims")
    if _contains_any(text, simulation_keywords):
        high_risk_features.append("simulation_design_sensitivity")
    if _contains_any(text, ai_keywords):
        high_risk_features.append("possible_ai_authorship_or_ai_claims")
    if len(parsed.citation_keys) == 0:
        high_risk_features.append("low_explicit_citation_support")

    recommended_stack = "minimal"
    if pathway in {"math_stats_theory", "applied_stats", "data_science"}:
        recommended_stack = "standard"
    if len(high_risk_features) >= 2 or len(claim_types) >= 2:
        recommended_stack = "maximal"

    return ManuscriptClassification(
        pathway=pathway,
        paper_type=paper_type,
        evidence_types=evidence_types,
        claim_types=claim_types,
        high_risk_features=high_risk_features,
        recommended_stack=recommended_stack,
    )


def build_routing_tables(
    parsed: ParsedManuscript,
) -> tuple[ManuscriptClassification, ModuleRoutingTable, DomainRoutingTable]:
    classification = classify_manuscript(parsed)
    text = parsed.full_text.lower()
    module_specs = [
        (
            "structure_contribution_and_fit",
            True,
            "Core manuscript structure review is always required.",
        ),
        (
            "bibliography_metadata_validation",
            True,
            "Bibliography structure must be inspected before agent reasoning.",
        ),
        (
            "statistical_validity_and_assumptions",
            classification.pathway in {"applied_stats", "data_science"},
            "Applied or data-science manuscripts require statistical validity review.",
        ),
        (
            "math_proofs_and_notation",
            classification.pathway == "math_stats_theory",
            "Activated when proof-oriented mathematical content is present.",
        ),
        (
            "results_figures_tables_consistency",
            bool(parsed.figure_mentions or parsed.table_mentions),
            "Activated when the manuscript references figures or tables.",
        ),
        (
            "reproducibility_and_computational_audit",
            classification.paper_type == "software_workflow_paper",
            "Activated for software and workflow dissemination papers.",
        ),
        (
            "ai_generated_manuscript_risk_audit",
            True,
            "Dedicated AI-risk review remains part of the default stack.",
        ),
    ]
    domain_specs = [
        (
            "equivalence_noninferiority",
            any(claim == "equivalence" for claim in classification.claim_types),
            "Activated by explicit equivalence or TOST language.",
        ),
        (
            "simulation_studies",
            "simulation" in classification.evidence_types,
            "Activated when simulation evidence is present.",
        ),
        (
            "software_workflow_papers",
            classification.paper_type == "software_workflow_paper",
            "Activated for software, pipeline, and workflow manuscripts.",
        ),
        (
            "spatial_spatiotemporal_statistics",
            _contains_any(text, {"spatial", "spatiotemporal", "gaussian process", "variogram"}),
            "Activated by explicit spatial or spatiotemporal language.",
        ),
        (
            "causal_inference",
            _contains_any(text, {"causal", "treatment effect", "propensity", "instrumental"}),
            "Activated by causal design language.",
        ),
        (
            "time_series_forecasting",
            _contains_any(text, {"time series", "forecast", "arima", "state space"}),
            "Activated by forecasting or temporal-model language.",
        ),
    ]
    module_routing = ModuleRoutingTable(
        route_version=DEFAULT_ROUTE_VERSION,
        pathway=classification.pathway,
        paper_type=classification.paper_type,
        recommended_stack=classification.recommended_stack,
        modules=[
            ApplicabilityDecision(name=name, applicable=applicable, rationale=rationale)
            for name, applicable, rationale in module_specs
        ],
    )
    domain_routing = DomainRoutingTable(
        route_version=DEFAULT_ROUTE_VERSION,
        domains=[
            ApplicabilityDecision(name=name, applicable=applicable, rationale=rationale)
            for name, applicable, rationale in domain_specs
        ],
    )
    return classification, module_routing, domain_routing
