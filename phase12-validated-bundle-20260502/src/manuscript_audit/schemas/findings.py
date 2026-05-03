from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from manuscript_audit.schemas.artifacts import (
    BibliographyConfidenceSummary,
    NotationSummary,
    SourceRecordSummary,
    SourceRecordVerification,
    SourceRecordVerificationSummary,
)
from manuscript_audit.schemas.routing import (
    DomainRoutingTable,
    ManuscriptClassification,
    ModuleRoutingTable,
)

Severity = Literal["fatal", "major", "moderate", "minor", "info"]
FindingSourceType = Literal["validator", "agent"]


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    validator: str
    location: str | None = None
    evidence: list[str] = Field(default_factory=list)
    confidence: float | None = None  # 0.0–1.0; None means confidence not assessed


class ValidationResult(BaseModel):
    validator_name: str
    findings: list[Finding] = Field(default_factory=list)


class ValidationSuiteResult(BaseModel):
    validator_version: str
    results: list[ValidationResult] = Field(default_factory=list)

    @property
    def all_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for result in self.results:
            findings.extend(result.findings)
        return findings

    @property
    def severity_counts(self) -> dict[str, int]:
        return dict(Counter(finding.severity for finding in self.all_findings))


class AgentModuleResult(BaseModel):
    module_name: str
    agent_name: str
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class AgentSuiteResult(BaseModel):
    agent_version: str
    results: list[AgentModuleResult] = Field(default_factory=list)

    @property
    def all_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for result in self.results:
            findings.extend(result.findings)
        return findings

    @property
    def severity_counts(self) -> dict[str, int]:
        return dict(Counter(finding.severity for finding in self.all_findings))


class RevisionFindingRef(BaseModel):
    source_type: FindingSourceType
    source_name: str
    code: str
    severity: Severity
    message: str
    location: str | None = None


class RevisionVerificationReport(BaseModel):
    run_id: str
    old_manuscript_id: str
    new_manuscript_id: str
    route_changed: bool
    resolved_findings: list[RevisionFindingRef] = Field(default_factory=list)
    persistent_findings: list[RevisionFindingRef] = Field(default_factory=list)
    new_findings: list[RevisionFindingRef] = Field(default_factory=list)
    revision_priorities: list[str] = Field(default_factory=list)


class SourceRecordVerificationReport(BaseModel):
    run_id: str
    manuscript_id: str
    verification_provider: str
    verifications: list[SourceRecordVerification] = Field(default_factory=list)
    summary: SourceRecordVerificationSummary
    bibliography_confidence_summary: BibliographyConfidenceSummary | None = None
    revision_priorities: list[str] = Field(default_factory=list)


class FinalVettingReport(BaseModel):
    run_id: str
    manuscript_id: str
    classification: ManuscriptClassification
    module_routing: ModuleRoutingTable
    domain_routing: DomainRoutingTable
    validation_suite: ValidationSuiteResult
    agent_suite: AgentSuiteResult | None = None
    source_record_summary: SourceRecordSummary | None = None
    bibliography_confidence_summary: BibliographyConfidenceSummary | None = None
    source_verification_provider: str | None = None
    source_verification_summary: SourceRecordVerificationSummary | None = None
    notation_summary: NotationSummary | None = None
    revision_priorities: list[str] = Field(default_factory=list)
