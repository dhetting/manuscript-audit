from __future__ import annotations

from typing import Protocol

from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.findings import Finding
from manuscript_audit.schemas.routing import ApplicabilityDecision


class AuditAgent(Protocol):
    name: str

    def run(
        self,
        parsed: ParsedManuscript,
        applicability: ApplicabilityDecision,
    ) -> list[Finding]: ...
