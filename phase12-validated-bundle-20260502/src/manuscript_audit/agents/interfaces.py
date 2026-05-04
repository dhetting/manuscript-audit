from __future__ import annotations

from typing import Protocol

from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.findings import AgentModuleResult, ValidationSuiteResult
from manuscript_audit.schemas.routing import ApplicabilityDecision, ManuscriptClassification


class AuditAgent(Protocol):
    name: str
    module_name: str

    def run(
        self,
        parsed: ParsedManuscript,
        classification: ManuscriptClassification,
        validation_suite: ValidationSuiteResult,
        applicability: ApplicabilityDecision,
    ) -> AgentModuleResult: ...
