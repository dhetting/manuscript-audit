from __future__ import annotations

import re

from manuscript_audit.config import DEFAULT_VALIDATOR_VERSION
from manuscript_audit.schemas.artifacts import ParsedManuscript
from manuscript_audit.schemas.findings import Finding, ValidationResult, ValidationSuiteResult
from manuscript_audit.schemas.routing import ManuscriptClassification

PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TK|XXX|FIXME)\b|\[citation needed\]|\?\?\?",
    re.IGNORECASE,
)
CLAIM_LANGUAGE_RE = re.compile(
    r"\b(prove|demonstrate|show|improve|equivalent|outperform|significant)\b",
    re.IGNORECASE,
)


def validate_required_sections(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    titles = {section.title.lower() for section in parsed.sections}
    required = {"abstract", "introduction", "references"}
    findings: list[Finding] = []
    if classification.paper_type == "theory_paper":
        required.add("discussion")
        if not any(title in titles for title in {"proof", "proofs", "main results"}):
            findings.append(
                Finding(
                    code="missing-proof-section",
                    severity="major",
                    message="Theory paper is missing a proof-oriented section.",
                    validator="required_sections",
                )
            )
    else:
        required.update({"methods", "results", "discussion"})
    for section in sorted(required):
        if section not in titles:
            findings.append(
                Finding(
                    code="missing-required-section",
                    severity="major",
                    message=f"Missing required section: {section}.",
                    validator="required_sections",
                    location=section,
                )
            )
    return ValidationResult(validator_name="required_sections", findings=findings)


def validate_unresolved_placeholders(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for section in parsed.sections:
        for match in PLACEHOLDER_RE.finditer(section.body):
            findings.append(
                Finding(
                    code="unresolved-placeholder",
                    severity="major",
                    message=f"Unresolved placeholder '{match.group(0)}' found.",
                    validator="unresolved_placeholders",
                    location=section.title,
                    evidence=[match.group(0)],
                )
            )
    return ValidationResult(validator_name="unresolved_placeholders", findings=findings)


def validate_citation_density(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() in {"references", "bibliography"}:
            continue
        word_count = len(section.body.split())
        has_citation = "[@" in section.body or "\\cite" in section.body
        makes_claim = CLAIM_LANGUAGE_RE.search(section.body) is not None
        if word_count >= 20 and makes_claim and not has_citation:
            findings.append(
                Finding(
                    code="low-citation-density",
                    severity="moderate",
                    message=(
                        "Section contains claim-like language without explicit citation support."
                    ),
                    validator="citation_density",
                    location=section.title,
                )
            )
    return ValidationResult(validator_name="citation_density", findings=findings)


def validate_reference_coverage(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    if parsed.citation_keys and not parsed.reference_section_present:
        findings.append(
            Finding(
                code="missing-references-section",
                severity="major",
                message="Citations are present but no references section was detected.",
                validator="reference_coverage",
            )
        )
    return ValidationResult(validator_name="reference_coverage", findings=findings)


def run_deterministic_validators(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationSuiteResult:
    results = [
        validate_required_sections(parsed, classification),
        validate_unresolved_placeholders(parsed),
        validate_citation_density(parsed),
        validate_reference_coverage(parsed),
    ]
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
