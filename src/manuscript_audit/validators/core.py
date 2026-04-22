from __future__ import annotations

import re

from manuscript_audit.config import DEFAULT_VALIDATOR_VERSION
from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript
from manuscript_audit.schemas.findings import Finding, ValidationResult, ValidationSuiteResult
from manuscript_audit.schemas.routing import ManuscriptClassification

PLACEHOLDER_RE = re.compile(
    r"\b(TODO|TK|XXX|FIXME)\b|\[citation needed\]|\?\?\?",
    re.IGNORECASE,
)
CLAIM_LANGUAGE_RE = re.compile(
    (
        r"\b(prove|proves|demonstrate|demonstrates|show|shows|improve|improves|"
        r"equivalent|equivalence|outperform|outperforms|significant)\b"
    ),
    re.IGNORECASE,
)
NUMERIC_LABEL_RE = re.compile(r"\d+")


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


def _duplicate_bibliography_keys(entries: list[BibliographyEntry]) -> list[str]:
    counts: dict[str, int] = {}
    for entry in entries:
        if entry.key is None:
            continue
        counts[entry.key] = counts.get(entry.key, 0) + 1
    return sorted(key for key, count in counts.items() if count > 1)


def validate_duplicate_bibliography_entries(parsed: ParsedManuscript) -> ValidationResult:
    duplicates = _duplicate_bibliography_keys(parsed.bibliography_entries)
    findings = [
        Finding(
            code="duplicate-bibliography-key",
            severity="major",
            message=f"Duplicate bibliography key detected: {key}.",
            validator="duplicate_bibliography_entries",
            evidence=[key],
        )
        for key in duplicates
    ]
    return ValidationResult(
        validator_name="duplicate_bibliography_entries",
        findings=findings,
    )


def validate_figure_table_reference_coverage(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    figure_labels = {
        NUMERIC_LABEL_RE.search(label).group(0)
        for label in parsed.figure_mentions
        if NUMERIC_LABEL_RE.search(label)
    }
    figure_defs = {
        NUMERIC_LABEL_RE.search(label).group(0)
        for label in parsed.figure_definitions
        if NUMERIC_LABEL_RE.search(label)
    }
    missing_figures = sorted(figure_labels - figure_defs)
    for label in missing_figures:
        findings.append(
            Finding(
                code="missing-figure-definition",
                severity="moderate",
                message=(
                    f"Figure {label} is referenced but no figure definition/caption was parsed."
                ),
                validator="figure_table_reference_coverage",
                evidence=[label],
            )
        )
    table_labels = {
        NUMERIC_LABEL_RE.search(label).group(0)
        for label in parsed.table_mentions
        if NUMERIC_LABEL_RE.search(label)
    }
    table_defs = {
        NUMERIC_LABEL_RE.search(label).group(0)
        for label in parsed.table_definitions
        if NUMERIC_LABEL_RE.search(label)
    }
    missing_tables = sorted(table_labels - table_defs)
    for label in missing_tables:
        findings.append(
            Finding(
                code="missing-table-definition",
                severity="moderate",
                message=f"Table {label} is referenced but no table definition was parsed.",
                validator="figure_table_reference_coverage",
                evidence=[label],
            )
        )
    return ValidationResult(
        validator_name="figure_table_reference_coverage",
        findings=findings,
    )


def validate_citation_bibliography_alignment(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    bibliography_keys = {entry.key for entry in parsed.bibliography_entries if entry.key}
    if parsed.citation_keys and bibliography_keys:
        missing_entries = sorted(set(parsed.citation_keys) - bibliography_keys)
        for key in missing_entries:
            findings.append(
                Finding(
                    code="missing-bibliography-entry-for-citation",
                    severity="major",
                    message=f"Citation key '{key}' is used but no bibliography entry was found.",
                    validator="citation_bibliography_alignment",
                    evidence=[key],
                )
            )
        unused_entries = sorted(bibliography_keys - set(parsed.citation_keys))
        for key in unused_entries:
            findings.append(
                Finding(
                    code="uncited-bibliography-entry",
                    severity="minor",
                    message=(
                        f"Bibliography entry '{key}' is present but not cited in the manuscript."
                    ),
                    validator="citation_bibliography_alignment",
                    evidence=[key],
                )
            )
    return ValidationResult(
        validator_name="citation_bibliography_alignment",
        findings=findings,
    )


def run_deterministic_validators(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationSuiteResult:
    results = [
        validate_required_sections(parsed, classification),
        validate_unresolved_placeholders(parsed),
        validate_citation_density(parsed),
        validate_reference_coverage(parsed),
        validate_duplicate_bibliography_entries(parsed),
        validate_figure_table_reference_coverage(parsed),
        validate_citation_bibliography_alignment(parsed),
    ]
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
