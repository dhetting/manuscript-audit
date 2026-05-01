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

# Claim-grounding validators: detect citationless quantitative and comparative claims.
CITATION_IN_TEXT_RE = re.compile(
    r"\[@[^\]]+\]"  # markdown: [@key], [@key1; @key2], [@key, p. 5]
    r"|\\cite\w*\{[^}]+\}",  # LaTeX: \cite{key}, \citep{key,key2}, \citet{key}
)
METRIC_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*%"  # X% or X.Y%
    r"|\b\d+(?:\.\d+)?-fold\b"  # X-fold
    r"|\b\d+(?:\.\d+)?\s*[xX]\b",  # 3x, 2.5X
    re.IGNORECASE,
)
EVALUATIVE_CONTEXT_RE = re.compile(
    r"\b(outperform|improv|achiev|attain|faster|better\s+than|higher\s+than|"
    r"lower\s+than|exceed|surpass|reduc)",
    re.IGNORECASE,
)
COMPARATIVE_CLAIM_RE = re.compile(
    r"\b(state[\s-]of[\s-]the[\s-]art"
    r"|outperform[sd]?"
    r"|superior\s+to"
    r"|better\s+than"
    r"|best[\s-]performing"
    r"|significantly\s+(?:better|outperform|improv))\b",
    re.IGNORECASE,
)
_SKIP_SECTIONS = {"references", "bibliography", "abstract"}

# Escalation: codes that count toward systemic claim-evidence gap.
_CLAIM_GROUNDING_CODES = frozenset(
    {
        "citationless-quantitative-claim",
        "citationless-comparative-claim",
        "abstract-metric-unsupported",
    }
)
CLAIM_EVIDENCE_GAP_THRESHOLD = 3  # findings needed to trigger major escalation

# Codes that represent structurally critical co-occurrences for fatal escalation.
_FATAL_TRIGGER_CODES = frozenset(
    {"systemic-claim-evidence-gap", "missing-required-section"}
)

# Notation ordering: shared regexes used by both validators and agents.
NOTATION_SECTION_RE = re.compile(
    r"\b(notation|preliminaries|definitions?|background|setup)\b",
    re.IGNORECASE,
)
PROOF_CONTENT_SECTION_RE = re.compile(
    r"\b(proof|proofs|main\s+result|theorem|lemma|corollary|propositions?)\b",
    re.IGNORECASE,
)

# Length/density thresholds.
ABSTRACT_OVERLONG_THRESHOLD = 350   # words above which abstract is flagged
SECTION_THIN_THRESHOLD = 30         # words below which a content section is flagged
_SUBSTANTIAL_SECTION_RE = re.compile(
    r"\b(methods?|results?|discussion|experiments?|analysis|evaluation|conclusions?)\b",
    re.IGNORECASE,
)


def _word_count(text: str) -> int:
    return len(text.split())


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs on blank lines."""
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


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


def validate_notation_section_alignment(parsed: ParsedManuscript) -> ValidationResult:
    notation_summary = extract_notation_summary(parsed)
    titles = {section.title.lower() for section in parsed.sections}
    supporting_titles = {"methods", "model", "notation", "proof", "proofs", "main results"}
    findings: list[Finding] = []
    missing_context = (
        parsed.equation_blocks
        and notation_summary.undefined_symbols
        and titles.isdisjoint(supporting_titles)
    )
    if missing_context:
        findings.append(
            Finding(
                code="missing-notation-context-section",
                severity="moderate",
                message=(
                    "Equations and undefined notation appear in the manuscript, but no clear "
                    "methods, model, notation, or proof section was detected."
                ),
                validator="notation_section_alignment",
                evidence=notation_summary.undefined_symbols,
            )
        )
    return ValidationResult(
        validator_name="notation_section_alignment",
        findings=findings,
    )


def validate_unlabeled_equations(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag unlabeled LaTeX equation blocks in theory papers.

    Only applies to LaTeX theory papers where equations are primary contributions
    and cross-referencing is expected. Skips empirical and software manuscripts.
    """
    findings: list[Finding] = []
    if parsed.source_format != "latex" or classification.paper_type != "theory_paper":
        return ValidationResult(validator_name="unlabeled_equations", findings=findings)
    for i, block in enumerate(parsed.equation_blocks, start=1):
        if r"\label{" not in block:
            findings.append(
                Finding(
                    code="equation-missing-label",
                    severity="minor",
                    message=(
                        f"Equation block {i} has no \\label{{}} and cannot be "
                        "cross-referenced."
                    ),
                    validator="unlabeled_equations",
                    location=f"equation {i}",
                    evidence=[block[:80]],
                )
            )
    return ValidationResult(validator_name="unlabeled_equations", findings=findings)


def _extract_trigger_sentence(para: str, *patterns: re.Pattern) -> str:  # type: ignore[type-arg]
    """Return the first sentence in *para* that matches all *patterns*.

    Falls back to a 120-character paragraph prefix if no sentence matches.
    """
    for sentence in _SENTENCE_SPLIT_RE.split(para):
        sentence = sentence.strip()
        if sentence and all(p.search(sentence) for p in patterns):
            return sentence[:150]
    return para[:120]


def validate_citationless_quantitative_claims(parsed: ParsedManuscript) -> ValidationResult:
    """Flag paragraphs with a numeric metric + evaluative language but no citation."""
    findings: list[Finding] = []

    def _check_block(text: str, location: str) -> None:
        for para in _split_paragraphs(text):
            if (
                METRIC_RE.search(para)
                and EVALUATIVE_CONTEXT_RE.search(para)
                and not CITATION_IN_TEXT_RE.search(para)
            ):
                trigger = _extract_trigger_sentence(para, METRIC_RE, EVALUATIVE_CONTEXT_RE)
                findings.append(
                    Finding(
                        code="citationless-quantitative-claim",
                        severity="moderate",
                        message=(
                            f"'{location}' contains a quantitative performance claim "
                            "without citation support."
                        ),
                        validator="citationless_quantitative_claims",
                        location=location,
                        evidence=[trigger],
                    )
                )

    if parsed.abstract.strip():
        _check_block(parsed.abstract, "abstract")
    for section in parsed.sections:
        if section.title.lower() not in _SKIP_SECTIONS:
            _check_block(section.body, section.title)

    return ValidationResult(
        validator_name="citationless_quantitative_claims",
        findings=findings,
    )


def validate_citationless_comparative_claims(parsed: ParsedManuscript) -> ValidationResult:
    """Flag paragraphs with strong external-comparison language but no citation."""
    findings: list[Finding] = []

    def _check_block(text: str, location: str) -> None:
        for para in _split_paragraphs(text):
            if COMPARATIVE_CLAIM_RE.search(para) and not CITATION_IN_TEXT_RE.search(para):
                trigger = _extract_trigger_sentence(para, COMPARATIVE_CLAIM_RE)
                findings.append(
                    Finding(
                        code="citationless-comparative-claim",
                        severity="moderate",
                        message=(
                            f"'{location}' contains a comparative claim without citation support."
                        ),
                        validator="citationless_comparative_claims",
                        location=location,
                        evidence=[trigger],
                    )
                )

    if parsed.abstract.strip():
        _check_block(parsed.abstract, "abstract")
    for section in parsed.sections:
        if section.title.lower() not in _SKIP_SECTIONS:
            _check_block(section.body, section.title)

    return ValidationResult(
        validator_name="citationless_comparative_claims",
        findings=findings,
    )


_SUPPORT_SECTION_KEYWORDS = {
    "result", "discussion", "conclusion", "experiment",
    "evaluation", "analysis", "finding",
}


def _is_support_section(title: str) -> bool:
    lower = title.lower()
    return any(keyword in lower for keyword in _SUPPORT_SECTION_KEYWORDS)


def _extract_metric_values(text: str) -> set[str]:
    """Return normalized metric strings (%, fold, x) found in text."""
    return {re.sub(r"\s+", "", m.group(0)).lower() for m in METRIC_RE.finditer(text)}


def validate_abstract_metric_coverage(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abstract numeric metrics (%, fold, x) absent from results/discussion sections.

    Checks that every quantitative metric mentioned in the abstract also appears in at
    least one support section (results, discussion, conclusion, experiments, evaluation,
    analysis). Skips silently when the manuscript has no abstract metrics or no support
    sections.
    """
    findings: list[Finding] = []

    if not parsed.abstract.strip():
        return ValidationResult(
            validator_name="abstract_metric_coverage", findings=findings
        )

    abstract_metrics = _extract_metric_values(parsed.abstract)
    if not abstract_metrics:
        return ValidationResult(
            validator_name="abstract_metric_coverage", findings=findings
        )

    support_sections = [s for s in parsed.sections if _is_support_section(s.title)]
    if not support_sections:
        return ValidationResult(
            validator_name="abstract_metric_coverage", findings=findings
        )

    support_metrics = _extract_metric_values(
        " ".join(s.body for s in support_sections)
    )

    for value in sorted(abstract_metrics - support_metrics):
        findings.append(
            Finding(
                code="abstract-metric-unsupported",
                severity="moderate",
                message=(
                    f"Abstract references '{value}' but this value does not appear "
                    "in any results or discussion section."
                ),
                validator="abstract_metric_coverage",
                location="abstract",
                evidence=[value],
            )
        )

    return ValidationResult(validator_name="abstract_metric_coverage", findings=findings)


def validate_abstract_length(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abstracts that exceed the typical journal word-count cap.

    Does not duplicate the agent's thin-abstract check (< 30 words). Only flags
    abstracts above ABSTRACT_OVERLONG_THRESHOLD as a minor issue since many
    journals cap at 250-300 words.
    """
    findings: list[Finding] = []
    if not parsed.abstract.strip():
        return ValidationResult(validator_name="abstract_length", findings=findings)
    n = _word_count(parsed.abstract)
    if n > ABSTRACT_OVERLONG_THRESHOLD:
        findings.append(
            Finding(
                code="overlong-abstract",
                severity="minor",
                message=(
                    f"Abstract has {n} words; many journals cap at 250–300. "
                    "Consider condensing."
                ),
                validator="abstract_length",
                location="Abstract",
                evidence=[f"{n} words"],
            )
        )
    return ValidationResult(validator_name="abstract_length", findings=findings)


def validate_section_body_completeness(parsed: ParsedManuscript) -> ValidationResult:
    """Flag content sections whose body is below the minimum substantive threshold.

    Applies to sections whose titles match expected heavy-content patterns
    (Methods, Results, Discussion, Experiments, Analysis, Evaluation, Conclusions).
    A body with fewer than SECTION_THIN_THRESHOLD words is unlikely to contain
    meaningful content and is probably a placeholder or stub.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if not _SUBSTANTIAL_SECTION_RE.search(section.title):
            continue
        n = _word_count(section.body)
        if n < SECTION_THIN_THRESHOLD:
            findings.append(
                Finding(
                    code="underdeveloped-section",
                    severity="moderate",
                    message=(
                        f"Section '{section.title}' has only {n} words; "
                        f"substantive sections should exceed {SECTION_THIN_THRESHOLD}."
                    ),
                    validator="section_body_completeness",
                    location=f"section '{section.title}'",
                    evidence=[f"{n} words"],
                )
            )
    return ValidationResult(
        validator_name="section_body_completeness", findings=findings
    )


def validate_notation_section_ordering(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag when a notation/preliminaries section appears after proof/content sections.

    In theory papers the notation or preliminaries section should precede the sections
    that use the defined symbols (proofs, main results, theorems). If it is placed
    after the first content section the reader encounters symbols before their definitions.

    Only applies to theory papers; silently skips if no notation section or no content
    section is found.
    """
    findings: list[Finding] = []
    if classification.paper_type != "theory_paper":
        return ValidationResult(
            validator_name="notation_section_ordering", findings=findings
        )

    section_titles = [s.title for s in parsed.sections]
    notation_indices = [
        i for i, t in enumerate(section_titles) if NOTATION_SECTION_RE.search(t)
    ]
    content_indices = [
        i for i, t in enumerate(section_titles) if PROOF_CONTENT_SECTION_RE.search(t)
    ]

    if not notation_indices or not content_indices:
        return ValidationResult(
            validator_name="notation_section_ordering", findings=findings
        )

    first_notation = min(notation_indices)
    first_content = min(content_indices)
    if first_notation > first_content:
        content_title = section_titles[first_content]
        notation_title = section_titles[first_notation]
        findings.append(
            Finding(
                code="notation-section-out-of-order",
                severity="moderate",
                message=(
                    f"Section '{notation_title}' appears after '{content_title}': "
                    "notation and definitions should precede the sections that use them."
                ),
                validator="notation_section_ordering",
                location=f"section '{notation_title}'",
                evidence=[content_title, notation_title],
            )
        )
    return ValidationResult(
        validator_name="notation_section_ordering", findings=findings
    )


def validate_claim_evidence_escalation(suite: ValidationSuiteResult) -> ValidationResult:
    """Escalate to major when multiple citationless/unsupported claim findings accumulate.

    Counts findings with codes in _CLAIM_GROUNDING_CODES across the full suite.
    When the total meets or exceeds CLAIM_EVIDENCE_GAP_THRESHOLD, emits a single
    major-severity finding to surface the systemic pattern in revision priorities.
    """
    matched = [f for f in suite.all_findings if f.code in _CLAIM_GROUNDING_CODES]
    findings: list[Finding] = []
    if len(matched) >= CLAIM_EVIDENCE_GAP_THRESHOLD:
        codes_summary = ", ".join(sorted({f.code for f in matched}))
        findings.append(
            Finding(
                code="systemic-claim-evidence-gap",
                severity="major",
                message=(
                    f"{len(matched)} claim-grounding issues detected "
                    f"({codes_summary}) — systematic citation or evidence gaps "
                    "require revision before submission."
                ),
                validator="claim_evidence_escalation",
                evidence=[f.message[:80] for f in matched[:3]],
            )
        )
    return ValidationResult(
        validator_name="claim_evidence_escalation", findings=findings
    )


def validate_critical_escalation(suite: ValidationSuiteResult) -> ValidationResult:
    """Escalate to fatal when systemic claim gap and missing required sections co-occur.

    A manuscript that both lacks required structural sections AND exhibits
    systemic claim-evidence gaps represents a critical structural failure that
    cannot be remediated with minor revisions.  Emits a single fatal finding
    to surface this combination prominently in revision priorities.
    """
    present_codes = {f.code for f in suite.all_findings}
    findings: list[Finding] = []
    if _FATAL_TRIGGER_CODES.issubset(present_codes):
        missing_sections = [
            f for f in suite.all_findings if f.code == "missing-required-section"
        ]
        section_names = [
            (f.location or "unknown") for f in missing_sections
        ]
        findings.append(
            Finding(
                code="critical-structural-claim-failure",
                severity="fatal",
                message=(
                    "Systemic claim-evidence gap combined with missing required "
                    f"section(s) ({', '.join(section_names)}) — the manuscript has "
                    "fundamental structural and evidentiary deficiencies that require "
                    "substantial revision before peer review."
                ),
                validator="critical_escalation",
                evidence=[
                    f"Missing section(s): {', '.join(section_names)}",
                    "Systemic claim-evidence gap already detected (major)",
                ],
            )
        )
    return ValidationResult(
        validator_name="critical_escalation", findings=findings
    )


# Passive voice density (methods sections).
_METHODS_SECTION_RE = re.compile(r"\b(methods?|methodology|experimental\s+setup)\b", re.IGNORECASE)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_PASSIVE_VOICE_RE = re.compile(
    r"\b(is|are|was|were|be|been|being)\s+\w+ed\b",
    re.IGNORECASE,
)
PASSIVE_VOICE_THRESHOLD = 0.45  # fraction of sentences; flag above this


def validate_passive_voice_density(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Methods sections where the majority of sentences are passive constructions.

    Scans sections matching _METHODS_SECTION_RE.  For each such section, splits
    the body into sentences and counts those containing at least one passive
    auxiliary + past-participle pattern.  If the passive fraction exceeds
    PASSIVE_VOICE_THRESHOLD and there are at least 4 sentences, emits
    ``high-passive-voice-density`` (minor).
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if not _METHODS_SECTION_RE.search(section.title):
            continue
        body = section.body.strip()
        if not body:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        if len(sentences) < 4:
            continue
        passive_count = sum(1 for s in sentences if _PASSIVE_VOICE_RE.search(s))
        ratio = passive_count / len(sentences)
        if ratio > PASSIVE_VOICE_THRESHOLD:
            findings.append(
                Finding(
                    code="high-passive-voice-density",
                    severity="minor",
                    message=(
                        f"{passive_count}/{len(sentences)} sentences "
                        f"({ratio:.0%}) in the Methods section use passive voice — "
                        "consider rewriting key steps in active voice for clarity."
                    ),
                    validator="passive_voice_density",
                    location=f"section '{section.title}'",
                    evidence=[f"passive fraction: {ratio:.2f}"],
                )
            )
    return ValidationResult(validator_name="passive_voice_density", findings=findings)


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
        validate_notation_section_alignment(parsed),
        validate_notation_section_ordering(parsed, classification),
        validate_claim_section_alignment(parsed, classification),
        validate_unlabeled_equations(parsed, classification),
        validate_citationless_quantitative_claims(parsed),
        validate_citationless_comparative_claims(parsed),
        validate_abstract_metric_coverage(parsed),
        validate_abstract_length(parsed),
        validate_section_body_completeness(parsed),
        validate_passive_voice_density(parsed),
    ]
    partial = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_claim_evidence_escalation(partial))
    partial2 = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_critical_escalation(partial2))
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
