from __future__ import annotations

import re

from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.findings import AgentModuleResult, Finding, ValidationSuiteResult
from manuscript_audit.schemas.routing import ApplicabilityDecision, ManuscriptClassification

CONTRIBUTION_RE = re.compile(
    r"\b(we evaluate|we propose|we present|this study|this manuscript)\b",
    re.IGNORECASE,
)
REPO_RE = re.compile(
    (
        r"\b(repository|github|gitlab|open-source|open source|cli|command-line|"
        r"configuration|config)\b"
    ),
    re.IGNORECASE,
)
AI_RISK_RE = re.compile(
    r"\b(revolutionary|groundbreaking|transformative|state-of-the-art)\b",
    re.IGNORECASE,
)
MARGIN_RE = re.compile(
    r"\b(delta|margin|equivalence margin|noninferiority margin|Δ)\b",
    re.IGNORECASE,
)
RESULTS_RE = re.compile(r"\b(result|figure|table|observed|estimate)\b", re.IGNORECASE)


class BaseHeuristicAgent:
    name = "base_heuristic_agent"
    module_name = "base_module"

    def run(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
        applicability: ApplicabilityDecision,
    ) -> AgentModuleResult:
        findings = self._build_findings(parsed, classification, validation_suite)
        summary = self._build_summary(findings, applicability)
        return AgentModuleResult(
            module_name=self.module_name,
            agent_name=self.name,
            summary=summary,
            findings=findings,
        )

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        return []

    def _build_summary(
        self,
        findings: list[Finding],
        applicability: ApplicabilityDecision,
    ) -> str:
        if findings:
            return f"{self.module_name} produced {len(findings)} structured findings."
        return (
            f"{self.module_name} ran because the module was applicable: {applicability.rationale}"
        )


class StructureContributionAgent(BaseHeuristicAgent):
    name = "structure_contribution_agent"
    module_name = "structure_contribution_and_fit"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if len(parsed.abstract.split()) < 30:
            findings.append(
                Finding(
                    code="thin-abstract",
                    severity="moderate",
                    message="Abstract is very short for a pre-submission audit target.",
                    validator=self.name,
                    location="Abstract",
                )
            )
        introduction = next(
            (section for section in parsed.sections if section.title.lower() == "introduction"),
            None,
        )
        if introduction and CONTRIBUTION_RE.search(introduction.body) is None:
            findings.append(
                Finding(
                    code="unclear-contribution-framing",
                    severity="moderate",
                    message="Introduction does not clearly signal the manuscript's contribution.",
                    validator=self.name,
                    location="Introduction",
                )
            )
        return findings


class BibliographyMetadataAgent(BaseHeuristicAgent):
    name = "bibliography_metadata_agent"
    module_name = "bibliography_metadata_validation"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        if not parsed.bibliography_entries:
            return [
                Finding(
                    code="no-structured-bibliography-entries",
                    severity="major",
                    message=(
                        "No structured bibliography entries were available for metadata review."
                    ),
                    validator=self.name,
                )
            ]
        return []


class StatisticalValidityAgent(BaseHeuristicAgent):
    name = "statistical_validity_agent"
    module_name = "statistical_validity_and_assumptions"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        findings: list[Finding] = []
        full_text = parsed.full_text.lower()
        if "equivalence" in full_text or "tost" in full_text:
            if MARGIN_RE.search(parsed.full_text) is None:
                findings.append(
                    Finding(
                        code="equivalence-margin-not-explicit",
                        severity="moderate",
                        message=(
                            "Equivalence language is present but an explicit margin "
                            "was not detected."
                        ),
                        validator=self.name,
                        location="Methods",
                    )
                )
        if (
            parsed.equation_blocks
            and "assumption" not in full_text
            and "diagnostic" not in full_text
        ):
            findings.append(
                Finding(
                    code="assumptions-not-explicit",
                    severity="minor",
                    message=(
                        "Model equations are present but explicit assumptions or "
                        "diagnostics were not detected."
                    ),
                    validator=self.name,
                )
            )
        return findings


class ResultsConsistencyAgent(BaseHeuristicAgent):
    name = "results_consistency_agent"
    module_name = "results_figures_tables_consistency"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        findings: list[Finding] = []
        results_section = next(
            (section for section in parsed.sections if section.title.lower() == "results"),
            None,
        )
        if results_section and RESULTS_RE.search(results_section.body) is None:
            findings.append(
                Finding(
                    code="thin-results-anchoring",
                    severity="minor",
                    message="Results section does not appear to anchor claims to concrete outputs.",
                    validator=self.name,
                    location="Results",
                )
            )
        if parsed.figure_mentions and not parsed.figure_definitions:
            findings.append(
                Finding(
                    code="figure-mentions-without-captions",
                    severity="moderate",
                    message=(
                        "Figures are referenced but no figure captions or definitions were parsed."
                    ),
                    validator=self.name,
                )
            )
        return findings


class ReproducibilityAuditAgent(BaseHeuristicAgent):
    name = "reproducibility_audit_agent"
    module_name = "reproducibility_and_computational_audit"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        findings: list[Finding] = []
        if REPO_RE.search(parsed.full_text) is None:
            findings.append(
                Finding(
                    code="weak-computational-disclosure",
                    severity="major",
                    message=(
                        "Software/workflow manuscript lacks obvious repository or "
                        "executable workflow disclosure."
                    ),
                    validator=self.name,
                )
            )
        return findings


class AIRiskAuditAgent(BaseHeuristicAgent):
    name = "ai_risk_audit_agent"
    module_name = "ai_generated_manuscript_risk_audit"

    def _build_findings(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
    ) -> list[Finding]:
        findings: list[Finding] = []
        matches = sorted(dict.fromkeys(AI_RISK_RE.findall(parsed.full_text)))
        if matches:
            findings.append(
                Finding(
                    code="hype-language",
                    severity="minor",
                    message="Potentially inflated or generic hype language was detected.",
                    validator=self.name,
                    evidence=matches,
                )
            )
        return findings


class StubRoutedAgent(BaseHeuristicAgent):
    name = "stub_routed_agent"

    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.name = f"stub_{module_name}_agent"

    def _build_summary(
        self,
        findings: list[Finding],
        applicability: ApplicabilityDecision,
    ) -> str:
        return (
            f"No specialized implementation exists yet for {self.module_name}; "
            "the routed module was still executed and recorded as a stub."
        )
