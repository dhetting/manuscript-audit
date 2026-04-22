from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

from manuscript_audit.schemas.routing import (
    DomainRoutingTable,
    ManuscriptClassification,
    ModuleRoutingTable,
)

Severity = Literal["fatal", "major", "moderate", "minor", "info"]


class Finding(BaseModel):
    code: str
    severity: Severity
    message: str
    validator: str
    location: str | None = None
    evidence: list[str] = Field(default_factory=list)


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


class FinalVettingReport(BaseModel):
    run_id: str
    manuscript_id: str
    classification: ManuscriptClassification
    module_routing: ModuleRoutingTable
    domain_routing: DomainRoutingTable
    validation_suite: ValidationSuiteResult
    revision_priorities: list[str] = Field(default_factory=list)
