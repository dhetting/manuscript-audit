from __future__ import annotations

import re

from manuscript_audit.config import DEFAULT_VALIDATOR_VERSION
from manuscript_audit.parsers import build_source_records, extract_notation_summary
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
YEAR_RE = re.compile(r"^(19|20)\d{2}$")
DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def validate_required_sections(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    titles = {section.title.lower() for section in parsed.sections}
    has_abstract = bool(parsed.abstract.strip()) or "abstract" in titles
    has_references = parsed.reference_section_present or "references" in titles
    required = {"introduction"}
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
    if not has_abstract:
        findings.append(
            Finding(
                code="missing-required-section",
                severity="major",
                message="Missing required section: abstract.",
                validator="required_sections",
                location="abstract",
            )
        )
    if not has_references:
        findings.append(
            Finding(
                code="missing-required-section",
                severity="major",
                message="Missing required section: references.",
                validator="required_sections",
                location="references",
            )
        )
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
        has_citation = "[@" in section.body or r"\cite" in section.body
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
    if (
        parsed.citation_keys
        and parsed.reference_section_present
        and not parsed.bibliography_entries
    ):
        findings.append(
            Finding(
                code="references-section-without-structured-entries",
                severity="major",
                message=(
                    "A references section or bibliography command was detected, but no "
                    "structured bibliography entries were parsed."
                ),
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


def _normalize_label(label: str) -> str:
    numeric = NUMERIC_LABEL_RE.search(label)
    if numeric is not None:
        return numeric.group(0)
    return label.strip()


def _extract_non_definition_labels(parsed: ParsedManuscript, kind: str) -> set[str]:
    if kind not in {"figure", "table", "equation"}:
        return set()
    if parsed.source_format == "latex":
        mapping = {
            "figure": parsed.figure_mentions,
            "table": parsed.table_mentions,
            "equation": parsed.equation_mentions,
        }
        return {_normalize_label(label) for label in mapping[kind]}
    pattern = re.compile(rf"\b{kind.title()}\s+(\d+)\b", re.IGNORECASE)
    definition_pattern = re.compile(rf"^{kind.title()}\s+\d+\s*[:.-]", re.IGNORECASE)
    labels: set[str] = set()
    for line in parsed.full_text.splitlines():
        stripped = line.strip()
        if definition_pattern.match(stripped):
            continue
        for match in pattern.finditer(stripped):
            labels.add(match.group(1))
    return labels


def _definition_labels(labels: list[str]) -> set[str]:
    return {_normalize_label(label) for label in labels}


def validate_figure_table_reference_coverage(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    figure_labels = _extract_non_definition_labels(parsed, "figure")
    figure_defs = _definition_labels(parsed.figure_definitions)
    for label in sorted(figure_labels - figure_defs):
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
    table_labels = _extract_non_definition_labels(parsed, "table")
    table_defs = _definition_labels(parsed.table_definitions)
    for label in sorted(table_labels - table_defs):
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


def validate_orphaned_figure_table_definitions(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    figure_labels = _extract_non_definition_labels(parsed, "figure")
    figure_defs = _definition_labels(parsed.figure_definitions)
    for label in sorted(figure_defs - figure_labels):
        findings.append(
            Finding(
                code="orphaned-figure-definition",
                severity="minor",
                message=f"Figure {label} has a parsed definition/caption but is never referenced.",
                validator="orphaned_figure_table_definitions",
                evidence=[label],
            )
        )
    table_labels = _extract_non_definition_labels(parsed, "table")
    table_defs = _definition_labels(parsed.table_definitions)
    for label in sorted(table_defs - table_labels):
        findings.append(
            Finding(
                code="orphaned-table-definition",
                severity="minor",
                message=f"Table {label} has a parsed definition but is never referenced.",
                validator="orphaned_figure_table_definitions",
                evidence=[label],
            )
        )
    return ValidationResult(
        validator_name="orphaned_figure_table_definitions",
        findings=findings,
    )


def validate_equation_reference_coverage(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    equation_labels = _extract_non_definition_labels(parsed, "equation")
    equation_defs = _definition_labels(parsed.equation_definitions)
    for label in sorted(equation_labels - equation_defs):
        findings.append(
            Finding(
                code="missing-equation-definition",
                severity="moderate",
                message=f"Equation {label} is referenced but no equation definition was parsed.",
                validator="equation_reference_coverage",
                evidence=[label],
            )
        )
    return ValidationResult(
        validator_name="equation_reference_coverage",
        findings=findings,
    )


def validate_orphaned_equation_definitions(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    equation_labels = _extract_non_definition_labels(parsed, "equation")
    equation_defs = _definition_labels(parsed.equation_definitions)
    for label in sorted(equation_defs - equation_labels):
        findings.append(
            Finding(
                code="orphaned-equation-definition",
                severity="minor",
                message=f"Equation {label} has a parsed definition but is never referenced.",
                validator="orphaned_equation_definitions",
                evidence=[label],
            )
        )
    return ValidationResult(
        validator_name="orphaned_equation_definitions",
        findings=findings,
    )


def validate_citation_bibliography_alignment(parsed: ParsedManuscript) -> ValidationResult:
    bibliography_keys = {entry.key for entry in parsed.bibliography_entries if entry.key}
    cited_keys = set(parsed.citation_keys)
    findings: list[Finding] = []
    for key in sorted(cited_keys - bibliography_keys):
        findings.append(
            Finding(
                code="missing-bibliography-entry-for-citation",
                severity="major",
                message=f"Citation key '{key}' has no matching bibliography entry.",
                validator="citation_bibliography_alignment",
                evidence=[key],
            )
        )
    for key in sorted(bibliography_keys - cited_keys):
        findings.append(
            Finding(
                code="uncited-bibliography-entry",
                severity="minor",
                message=f"Bibliography entry '{key}' is present but not cited in the manuscript.",
                validator="citation_bibliography_alignment",
                evidence=[key],
            )
        )
    return ValidationResult(
        validator_name="citation_bibliography_alignment",
        findings=findings,
    )


def validate_bibliography_metadata_completeness(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for entry in parsed.bibliography_entries:
        missing_fields: list[str] = []
        if entry.key is None:
            missing_fields.append("key")
        if not entry.title:
            missing_fields.append("title")
        if not entry.year:
            missing_fields.append("year")
        if not entry.authors:
            missing_fields.append("authors")
        if missing_fields:
            label = entry.key or entry.raw_text[:40]
            findings.append(
                Finding(
                    code="incomplete-bibliography-metadata",
                    severity="moderate",
                    message=(
                        f"Bibliography entry '{label}' is missing metadata fields: "
                        f"{', '.join(missing_fields)}."
                    ),
                    validator="bibliography_metadata_completeness",
                    evidence=missing_fields,
                )
            )
    return ValidationResult(
        validator_name="bibliography_metadata_completeness",
        findings=findings,
    )


def validate_bibliography_year_format(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for entry in parsed.bibliography_entries:
        if entry.year and YEAR_RE.fullmatch(entry.year) is None:
            label = entry.key or entry.raw_text[:40]
            findings.append(
                Finding(
                    code="invalid-bibliography-year",
                    severity="moderate",
                    message=(
                        f"Bibliography entry '{label}' has a non-standard year value "
                        f"'{entry.year}'."
                    ),
                    validator="bibliography_year_format",
                    evidence=[entry.year],
                )
            )
    return ValidationResult(
        validator_name="bibliography_year_format",
        findings=findings,
    )


def validate_bibliography_doi_format(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for entry in parsed.bibliography_entries:
        if entry.doi and DOI_RE.fullmatch(entry.doi) is None:
            label = entry.key or entry.raw_text[:40]
            findings.append(
                Finding(
                    code="invalid-bibliography-doi",
                    severity="moderate",
                    message=(
                        f"Bibliography entry '{label}' has a malformed DOI value '{entry.doi}'."
                    ),
                    validator="bibliography_doi_format",
                    evidence=[entry.doi],
                )
            )
    return ValidationResult(
        validator_name="bibliography_doi_format",
        findings=findings,
    )


def validate_bibliography_venue_metadata(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for entry in parsed.bibliography_entries:
        if entry.source != "bibtex" or not entry.entry_type:
            continue
        label = entry.key or entry.raw_text[:40]
        if entry.entry_type == "article" and not entry.journal:
            findings.append(
                Finding(
                    code="missing-bibliography-venue",
                    severity="moderate",
                    message=f"Bibliography entry '{label}' is missing a journal field.",
                    validator="bibliography_venue_metadata",
                    evidence=["journal"],
                )
            )
        if entry.entry_type in {"inproceedings", "incollection"} and not entry.booktitle:
            findings.append(
                Finding(
                    code="missing-bibliography-venue",
                    severity="moderate",
                    message=f"Bibliography entry '{label}' is missing a booktitle field.",
                    validator="bibliography_venue_metadata",
                    evidence=["booktitle"],
                )
            )
    return ValidationResult(
        validator_name="bibliography_venue_metadata",
        findings=findings,
    )


def validate_bibliography_source_identifiers(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for entry in parsed.bibliography_entries:
        if entry.source != "bibtex" or not entry.entry_type:
            continue
        if entry.entry_type not in {"article", "inproceedings", "incollection", "book"}:
            continue
        if entry.doi or entry.url:
            continue
        label = entry.key or entry.raw_text[:40]
        findings.append(
            Finding(
                code="missing-bibliography-source-identifier",
                severity="minor",
                message=(
                    f"Bibliography entry '{label}' has neither a DOI nor a URL field for "
                    "source-of-record follow-up."
                ),
                validator="bibliography_source_identifiers",
                evidence=[entry.entry_type],
            )
        )
    return ValidationResult(
        validator_name="bibliography_source_identifiers",
        findings=findings,
    )


def validate_bibliography_source_record_readiness(parsed: ParsedManuscript) -> ValidationResult:
    findings: list[Finding] = []
    for record in build_source_records(parsed.bibliography_entries):
        if record.status == "ready_for_lookup":
            findings.append(
                Finding(
                    code="bibliography-source-record-needs-lookup",
                    severity="minor",
                    message=(
                        f"Bibliography entry '{record.entry_label}' still needs a source-of-record "
                        "lookup step before verification can rely on a canonical record."
                    ),
                    validator="bibliography_source_record_readiness",
                    evidence=[record.lookup_query or record.entry_label],
                )
            )
        if record.status == "insufficient_metadata":
            findings.append(
                Finding(
                    code="bibliography-source-record-insufficient-metadata",
                    severity="moderate",
                    message=(
                        f"Bibliography entry '{record.entry_label}' lacks enough metadata for a "
                        "deterministic source-of-record plan."
                    ),
                    validator="bibliography_source_record_readiness",
                    evidence=[record.entry_label],
                )
            )
    return ValidationResult(
        validator_name="bibliography_source_record_readiness",
        findings=findings,
    )


def validate_equation_notation_coverage(parsed: ParsedManuscript) -> ValidationResult:
    notation_summary = extract_notation_summary(parsed)
    findings: list[Finding] = []
    for symbol in notation_summary.undefined_symbols:
        findings.append(
            Finding(
                code="undefined-equation-symbol",
                severity="moderate",
                message=(
                    f"Equation symbol '{symbol}' appears in parsed equations without an obvious "
                    "textual definition hint."
                ),
                validator="equation_notation_coverage",
                evidence=[symbol],
            )
        )
    return ValidationResult(
        validator_name="equation_notation_coverage",
        findings=findings,
    )


def _section_text(parsed: ParsedManuscript, section_name: str) -> str:
    return " ".join(
        section.body.lower()
        for section in parsed.sections
        if section.title.lower() == section_name.lower()
    )


def _contains_any(text: str, keywords: set[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def validate_claim_section_alignment(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    findings: list[Finding] = []
    methods_text = _section_text(parsed, "methods")
    results_text = _section_text(parsed, "results")
    abstract_text = parsed.abstract.lower()
    claim_specs = {
        "equivalence": {
            "keywords": {"equivalence", "equivalent", "tost", "margin", "noninferiority"},
            "message": (
                "Equivalence claims appear in the abstract or routing metadata, "
                "but the methods/results sections do not show clear "
                "equivalence-analysis language."
            ),
        },
        "prediction": {
            "keywords": {"predict", "prediction", "forecast", "accuracy", "validation"},
            "message": (
                "Prediction claims appear in the abstract or routing metadata, "
                "but the methods/results sections do not show clear "
                "predictive-model language."
            ),
        },
        "causal": {
            "keywords": {"causal", "treatment effect", "propensity", "confounding", "instrument"},
            "message": (
                "Causal claims appear in the abstract or routing metadata, "
                "but the methods/results sections do not show clear "
                "causal-identification language."
            ),
        },
    }
    combined_text = f"{methods_text} {results_text}".strip()
    for claim_type, spec in claim_specs.items():
        if claim_type not in classification.claim_types:
            continue
        abstract_mentions = _contains_any(abstract_text, spec["keywords"])
        body_support = _contains_any(combined_text, spec["keywords"])
        if abstract_mentions and not body_support:
            findings.append(
                Finding(
                    code="claim-section-misalignment",
                    severity="moderate",
                    message=spec["message"],
                    validator="claim_section_alignment",
                    location="methods/results",
                    evidence=[claim_type],
                )
            )
    return ValidationResult(
        validator_name="claim_section_alignment",
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
        validate_orphaned_figure_table_definitions(parsed),
        validate_equation_reference_coverage(parsed),
        validate_orphaned_equation_definitions(parsed),
        validate_citation_bibliography_alignment(parsed),
        validate_bibliography_metadata_completeness(parsed),
        validate_bibliography_year_format(parsed),
        validate_bibliography_doi_format(parsed),
        validate_bibliography_venue_metadata(parsed),
        validate_bibliography_source_identifiers(parsed),
        validate_bibliography_source_record_readiness(parsed),
        validate_equation_notation_coverage(parsed),
        validate_claim_section_alignment(parsed, classification),
    ]
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
