from __future__ import annotations

import datetime
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
    """Flag abstracts that are too short or exceed the typical journal word-count cap.

    Emits ``abstract-too-short`` (minor) when < 100 words, and
    ``overlong-abstract`` (minor) when > ABSTRACT_OVERLONG_THRESHOLD words.
    """
    findings: list[Finding] = []
    if not parsed.abstract.strip():
        return ValidationResult(validator_name="abstract_length", findings=findings)
    n = _word_count(parsed.abstract)
    if n < 100:
        findings.append(
            Finding(
                code="abstract-too-short",
                severity="minor",
                message=(
                    f"Abstract has only {n} words; most journals require 150–300. "
                    "Expand to cover background, methods, results, and conclusion."
                ),
                validator="abstract_length",
                location="Abstract",
                evidence=[f"{n} words"],
            )
        )
    elif n > ABSTRACT_OVERLONG_THRESHOLD:
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


# Duplicate quantitative claim detection.
# Matches: a numeric value (possibly with % or decimal) adjacent to a noun-like word that
# suggests a performance metric, e.g. "94% accuracy", "F1 of 0.85", "p < 0.05".
_DUP_CLAIM_RE = re.compile(
    r"(?:"
    r"\d+(?:\.\d+)?%\s+\w+"       # "94% accuracy"
    r"|"
    r"\w+\s+of\s+\d+(?:\.\d+)?"   # "F1 of 0.85"
    r"|"
    r"p\s*[<>=]\s*0\.\d+"          # "p < 0.05"
    r"|"
    r"\d+(?:\.\d+)?\s+\w+\s+(?:score|rate|ratio|precision|recall|accuracy)\b"
    r")",
    re.IGNORECASE,
)


def validate_duplicate_claims(parsed: ParsedManuscript) -> ValidationResult:
    """Flag quantitative claim strings that appear verbatim in two or more distinct sections.

    Looks for patterns matching ``_DUP_CLAIM_RE`` across all non-abstract sections.
    When the same normalised pattern string appears in ≥2 different sections, emits a
    ``duplicate-quantitative-claim`` (minor) finding, as copy-pasted numbers in different
    sections are often inconsistently updated during revision.
    """
    from collections import defaultdict

    # Map normalised claim string → set of section titles where it appears.
    claim_sections: dict[str, set[str]] = defaultdict(set)
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        for match in _DUP_CLAIM_RE.finditer(section.body):
            normalised = " ".join(match.group(0).lower().split())
            claim_sections[normalised].add(section.title)

    findings: list[Finding] = []
    for claim, sections in sorted(claim_sections.items()):
        if len(sections) >= 2:
            section_list = ", ".join(f"'{s}'" for s in sorted(sections))
            findings.append(
                Finding(
                    code="duplicate-quantitative-claim",
                    severity="minor",
                    message=(
                        f"Quantitative claim \"{claim}\" appears verbatim in "
                        f"{len(sections)} sections ({section_list}) — verify consistency."
                    ),
                    validator="duplicate_claims",
                    evidence=[claim],
                )
            )
    return ValidationResult(validator_name="duplicate_claims", findings=findings)


# ---------------------------------------------------------------------------
# Hedging language density
# ---------------------------------------------------------------------------
_HEDGING_SECTION_RE = re.compile(
    r"\b(discussion|conclusions?|future\s+work|implications?|limitations?)\b",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(may|might|could|perhaps|possibly|potentially|appears?\s+to|seems?\s+to|"
    r"suggests?\s+that|indicates?\s+that|it\s+is\s+possible|it\s+is\s+likely|"
    r"tend\s+to|somewhat|arguably|presumably)\b",
    re.IGNORECASE,
)
HEDGING_THRESHOLD = 0.25  # fraction of sentences; flag above this


def validate_hedging_density(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Discussion/Conclusion sections with excessive epistemic hedging.

    Scans sections matching ``_HEDGING_SECTION_RE``.  If more than
    ``HEDGING_THRESHOLD`` of sentences contain at least one hedge phrase,
    emits ``excessive-hedging-language`` (minor).  Requires ≥4 sentences to
    avoid false positives on short sections.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if not _HEDGING_SECTION_RE.search(section.title):
            continue
        body = section.body.strip()
        if not body:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        if len(sentences) < 4:
            continue
        hedged = sum(1 for s in sentences if _HEDGE_RE.search(s))
        ratio = hedged / len(sentences)
        if ratio > HEDGING_THRESHOLD:
            findings.append(
                Finding(
                    code="excessive-hedging-language",
                    severity="minor",
                    message=(
                        f"{hedged}/{len(sentences)} sentences ({ratio:.0%}) in "
                        f"'{section.title}' contain epistemic hedges — consider "
                        "strengthening assertions where evidence supports it."
                    ),
                    validator="hedging_density",
                    location=f"section '{section.title}'",
                    evidence=[f"hedge fraction: {ratio:.2f}"],
                )
            )
    return ValidationResult(validator_name="hedging_density", findings=findings)


# ---------------------------------------------------------------------------
# Missing related work section
# ---------------------------------------------------------------------------
_RELATED_WORK_RE = re.compile(
    r"\b(related\s+work|background|prior\s+work|literature\s+review|"
    r"previous\s+work|related\s+studies|survey)\b",
    re.IGNORECASE,
)
_EMPIRICAL_PAPER_TYPES = frozenset(
    {"empirical_paper", "applied_stats_paper", "software_workflow_paper"}
)


def validate_related_work_coverage(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that lack a Related Work or Background section.

    A missing related-work section is a common desk-rejection trigger.  Only
    fires for paper types in ``_EMPIRICAL_PAPER_TYPES`` (theory papers often
    integrate prior work into the Introduction).
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="related_work_coverage", findings=[])
    has_related_work = any(
        _RELATED_WORK_RE.search(section.title) for section in parsed.sections
    )
    if has_related_work:
        return ValidationResult(validator_name="related_work_coverage", findings=[])
    return ValidationResult(
        validator_name="related_work_coverage",
        findings=[
            Finding(
                code="missing-related-work-section",
                severity="moderate",
                message=(
                    "No dedicated Related Work, Background, or Literature Review "
                    "section was detected — reviewers routinely flag this omission."
                ),
                validator="related_work_coverage",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Missing limitations coverage
# ---------------------------------------------------------------------------
_LIMITATIONS_RE = re.compile(
    r"\b(limitation|limitations|future\s+work|shortcoming|caveat|constraint|"
    r"scope\s+of|cannot\s+generalize|not\s+generali[sz])\b",
    re.IGNORECASE,
)
_LIMITATIONS_SECTION_RE = re.compile(
    r"\b(limitation|limitations|future\s+work|threats\s+to\s+validity)\b",
    re.IGNORECASE,
)


def validate_limitations_coverage(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers with no limitations or future-work discussion.

    Checks both for a dedicated limitations section and for limitation language
    in Discussion/Conclusion bodies.  Only fires for empirical paper types.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="limitations_coverage", findings=[])

    # Accept a dedicated section.
    if any(_LIMITATIONS_SECTION_RE.search(s.title) for s in parsed.sections):
        return ValidationResult(validator_name="limitations_coverage", findings=[])

    # Accept limitation language embedded in discussion/conclusion bodies.
    for section in parsed.sections:
        if re.search(r"\b(discussion|conclusion)\b", section.title, re.IGNORECASE):
            if _LIMITATIONS_RE.search(section.body):
                return ValidationResult(validator_name="limitations_coverage", findings=[])

    return ValidationResult(
        validator_name="limitations_coverage",
        findings=[
            Finding(
                code="missing-limitations-section",
                severity="moderate",
                message=(
                    "No limitations, caveats, or future-work discussion was detected — "
                    "omitting this weakens the manuscript's scholarly contribution."
                ),
                validator="limitations_coverage",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Acronym consistency
# ---------------------------------------------------------------------------
# Matches "Long Name (ABC)" style definitions.
_ACRONYM_DEF_RE = re.compile(
    r"\b[A-Z][A-Za-z]*(?:\s+[A-Za-z]+){1,8}\s+\(([A-Z]{2,6})\)",
)
# Matches standalone uppercase 2–6 letter tokens not surrounded by other letters.
_ACRONYM_USE_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z]{2,6})s?(?![A-Za-z0-9])")
# Well-known acronyms that never need in-text definition.
_COMMON_ACRONYMS = frozenset({
    "URL", "HTML", "PDF", "API", "CPU", "GPU", "RAM", "SQL", "XML", "JSON",
    "HTTP", "HTTPS", "IDE", "SDK", "CI", "CD", "AI", "ML", "NLP", "CV",
    "US", "UK", "EU", "UN", "USA", "NA", "DOI", "ORCID",
})


def _document_paragraphs(parsed: ParsedManuscript) -> list[tuple[str, str]]:
    """Return (location, paragraph) pairs in document reading order."""
    pairs: list[tuple[str, str]] = []
    if parsed.abstract.strip():
        for para in _split_paragraphs(parsed.abstract):
            pairs.append(("abstract", para))
    for section in parsed.sections:
        for para in _split_paragraphs(section.body):
            pairs.append((section.title, para))
    return pairs


def validate_acronym_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag acronym uses that precede their definition or are never defined.

    Scans abstract + sections in document order.  Tracks each acronym's first
    definition position.  Emits:
    - ``acronym-used-before-definition`` (moderate) when an acronym appears
      before its "Long Name (ABC)" definition.
    - ``undefined-acronym`` (moderate) when an uppercase token of 2–6 letters
      is used throughout the document but never defined at all.

    Common technical acronyms (URL, PDF, API, etc.) are exempted.
    """
    pairs = _document_paragraphs(parsed)

    # First pass: record at which paragraph index each acronym is defined.
    definition_index: dict[str, int] = {}
    for idx, (_, para) in enumerate(pairs):
        for match in _ACRONYM_DEF_RE.finditer(para):
            acronym = match.group(1)
            if acronym not in definition_index:
                definition_index[acronym] = idx

    # Second pass: find uses and check against definition positions.
    # use_locations maps acronym → list of (para_idx, location) for uses
    # that precede the definition.
    early_uses: dict[str, str] = {}   # acronym → first location of premature use
    all_uses: set[str] = set()

    for idx, (location, para) in enumerate(pairs):
        for match in _ACRONYM_USE_RE.finditer(para):
            acronym = match.group(1)
            if acronym in _COMMON_ACRONYMS:
                continue
            all_uses.add(acronym)
            if acronym not in definition_index and acronym not in early_uses:
                # We'll resolve undefined vs early-use after full scan.
                pass
            elif acronym in definition_index and idx < definition_index[acronym]:
                if acronym not in early_uses:
                    early_uses[acronym] = location

    # Acronyms used but never defined anywhere.
    undefined = all_uses - definition_index.keys() - _COMMON_ACRONYMS

    findings: list[Finding] = []
    for acronym in sorted(early_uses):
        findings.append(
            Finding(
                code="acronym-used-before-definition",
                severity="moderate",
                message=(
                    f'Acronym "{acronym}" is used in \'{early_uses[acronym]}\' '
                    "before its first definition."
                ),
                validator="acronym_consistency",
                location=early_uses[acronym],
                evidence=[acronym],
            )
        )
    for acronym in sorted(undefined):
        findings.append(
            Finding(
                code="undefined-acronym",
                severity="moderate",
                message=(
                    f'Acronym "{acronym}" is used but never defined '
                    "with a full expansion."
                ),
                validator="acronym_consistency",
                evidence=[acronym],
            )
        )
    return ValidationResult(validator_name="acronym_consistency", findings=findings)


# ---------------------------------------------------------------------------
# Methods tense consistency
# ---------------------------------------------------------------------------
_PRESENT_TENSE_RE = re.compile(
    r"\b(is|are|has|have|do|does|will|shall|can|may|apply|use|compute|train|"
    r"evaluate|measure|test|run|perform|calculate|determine|estimate)\b",
    re.IGNORECASE,
)
_PAST_TENSE_RE = re.compile(
    r"\b(was|were|had|did|applied|used|computed|trained|evaluated|measured|"
    r"tested|ran|performed|calculated|determined|estimated|conducted|collected)\b",
    re.IGNORECASE,
)
METHODS_TENSE_THRESHOLD = 0.35  # present-tense fraction above which we flag


def validate_methods_tense_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Methods sections where present tense sentences significantly outnumber past.

    Academic Methods sections should predominantly use past tense to describe
    what was done.  When present-tense-only sentences exceed
    ``METHODS_TENSE_THRESHOLD`` of sentences that contain any tense marker,
    emits ``inconsistent-methods-tense`` (minor).  Requires ≥5 tense-bearing
    sentences to avoid false positives on very short sections.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if not _METHODS_SECTION_RE.search(section.title):
            continue
        body = section.body.strip()
        if not body:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        tense_sentences = [
            s for s in sentences
            if _PRESENT_TENSE_RE.search(s) or _PAST_TENSE_RE.search(s)
        ]
        if len(tense_sentences) < 5:
            continue
        # Count sentences that contain present-tense markers but NO past-tense markers.
        present_only = sum(
            1 for s in tense_sentences
            if _PRESENT_TENSE_RE.search(s) and not _PAST_TENSE_RE.search(s)
        )
        ratio = present_only / len(tense_sentences)
        if ratio > METHODS_TENSE_THRESHOLD:
            findings.append(
                Finding(
                    code="inconsistent-methods-tense",
                    severity="minor",
                    message=(
                        f"{present_only}/{len(tense_sentences)} tense-bearing sentences "
                        f"({ratio:.0%}) in '{section.title}' use present tense — "
                        "Methods sections typically narrate in past tense."
                    ),
                    validator="methods_tense_consistency",
                    location=f"section '{section.title}'",
                    evidence=[f"present-tense fraction: {ratio:.2f}"],
                )
            )
    return ValidationResult(validator_name="methods_tense_consistency", findings=findings)


# ---------------------------------------------------------------------------
# Sentence length outliers
# ---------------------------------------------------------------------------
SENTENCE_LENGTH_THRESHOLD = 60  # words above which a sentence is flagged
_FINDINGS_PER_SECTION_CAP = 3   # max findings per section


def validate_sentence_length_outliers(parsed: ParsedManuscript) -> ValidationResult:
    """Flag excessively long sentences that harm readability.

    Scans all non-skipped sections.  Sentences exceeding
    ``SENTENCE_LENGTH_THRESHOLD`` words are flagged as
    ``overlong-sentence`` (minor).  At most ``_FINDINGS_PER_SECTION_CAP``
    findings are emitted per section to avoid flooding the report.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        body = section.body.strip()
        if not body:
            continue
        section_findings = 0
        for sentence in _SENTENCE_SPLIT_RE.split(body):
            sentence = sentence.strip()
            if not sentence:
                continue
            word_count = len(sentence.split())
            if word_count > SENTENCE_LENGTH_THRESHOLD:
                findings.append(
                    Finding(
                        code="overlong-sentence",
                        severity="minor",
                        message=(
                            f"A sentence in '{section.title}' is {word_count} words long — "
                            "consider splitting for readability."
                        ),
                        validator="sentence_length_outliers",
                        location=f"section '{section.title}'",
                        evidence=[sentence[:120]],
                    )
                )
                section_findings += 1
                if section_findings >= _FINDINGS_PER_SECTION_CAP:
                    break
    return ValidationResult(validator_name="sentence_length_outliers", findings=findings)


# ---------------------------------------------------------------------------
# Phase 37 – Citation cluster gap detector
# ---------------------------------------------------------------------------

_CITATION_RE = re.compile(
    r"\[\d+(?:,\s*\d+)*\]"          # [1], [1, 2]
    r"|(?:[A-Z][a-z]+\s+(?:et\s+al\.?|and\s+[A-Z][a-z]+),?\s+\d{4})"  # Smith et al. 2020
    r"|\\\w+cite\{[^}]+\}",         # \cite{key}
)
_CITATION_GAP_SECTIONS = frozenset({"results", "discussion", "analysis", "evaluation"})
CITATION_CLUSTER_GAP = 5  # consecutive sentences without any citation


def validate_citation_cluster_gap(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag long stretches of uncited sentences in empirical paper result sections.

    In Results / Discussion sections of empirical papers, 5 or more consecutive
    sentences with no citation signal a potential evidence-presentation gap.
    Requires ≥8 sentences in the section to avoid false positives on short sections.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="citation_cluster_gap", findings=[])

    findings: list[Finding] = []
    for section in parsed.sections:
        title_lower = section.title.lower()
        if not any(kw in title_lower for kw in _CITATION_GAP_SECTIONS):
            continue
        body = section.body.strip()
        if not body:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        if len(sentences) < 8:
            continue

        gap_start: int | None = None
        gap_count = 0
        for i, sent in enumerate(sentences):
            has_citation = bool(_CITATION_RE.search(sent))
            if not has_citation:
                if gap_start is None:
                    gap_start = i
                gap_count += 1
            else:
                if gap_count >= CITATION_CLUSTER_GAP:
                    findings.append(
                        Finding(
                            code="citation-cluster-gap",
                            severity="minor",
                            message=(
                                f"'{section.title}' has {gap_count} consecutive sentences "
                                "with no citation — consider adding supporting references."
                            ),
                            validator="citation_cluster_gap",
                            location=f"section '{section.title}'",
                            evidence=[sentences[gap_start][:120]],  # type: ignore[index]
                        )
                    )
                gap_start = None
                gap_count = 0
        # check trailing gap
        if gap_count >= CITATION_CLUSTER_GAP and gap_start is not None:
            findings.append(
                Finding(
                    code="citation-cluster-gap",
                    severity="minor",
                    message=(
                        f"'{section.title}' has {gap_count} consecutive sentences "
                        "with no citation — consider adding supporting references."
                    ),
                    validator="citation_cluster_gap",
                    location=f"section '{section.title}'",
                    evidence=[sentences[gap_start][:120]],
                )
            )
    return ValidationResult(validator_name="citation_cluster_gap", findings=findings)


# ---------------------------------------------------------------------------
# Phase 38 – Power-word overuse detector
# ---------------------------------------------------------------------------

_POWER_WORDS = (
    "novel",
    "state-of-the-art",
    "significant",
    "unprecedented",
    "groundbreaking",
    "revolutionary",
    "remarkable",
    "superior",
    "outstanding",
)
_POWER_WORD_THRESHOLD = 3  # occurrences above which we flag
_POWER_WORD_SECTIONS = frozenset({"abstract", "introduction"})
_POWER_WORD_RES = [
    re.compile(r"(?<![A-Za-z])" + re.escape(w) + r"(?![A-Za-z])", re.IGNORECASE)
    for w in _POWER_WORDS
]


def validate_power_word_overuse(parsed: ParsedManuscript) -> ValidationResult:
    """Flag overuse of vague promotional language in abstract / introduction.

    Each power-word is counted across the abstract + introduction body combined.
    When any single term exceeds ``_POWER_WORD_THRESHOLD`` occurrences the
    finding ``power-word-overuse`` (minor) is emitted.
    """
    combined = ""
    if parsed.abstract:
        combined += parsed.abstract + " "
    for section in parsed.sections:
        if "introduction" in section.title.lower():
            combined += section.body + " "

    if not combined.strip():
        return ValidationResult(validator_name="power_word_overuse", findings=[])

    findings: list[Finding] = []
    for word, pattern in zip(_POWER_WORDS, _POWER_WORD_RES, strict=True):
        count = len(pattern.findall(combined))
        if count > _POWER_WORD_THRESHOLD:
            findings.append(
                Finding(
                    code="power-word-overuse",
                    severity="minor",
                    message=(
                        f"The term '{word}' appears {count} times in abstract/introduction — "
                        "consider replacing with precise technical language."
                    ),
                    validator="power_word_overuse",
                    location="abstract/introduction",
                    evidence=[f"'{word}' count: {count}"],
                )
            )
    return ValidationResult(validator_name="power_word_overuse", findings=findings)


# ---------------------------------------------------------------------------
# Phase 39 – Number formatting consistency
# ---------------------------------------------------------------------------

_BARE_LARGE_NUMBER_RE = re.compile(r"(?<!\d)\d{5,}(?!\d)")   # 10000, 100000 etc.
_COMMA_NUMBER_RE = re.compile(r"\d{1,3}(?:,\d{3})+")          # 10,000 / 100,000 etc.


def _number_magnitude(n_str: str) -> int:
    """Return order-of-magnitude bucket (number of digits in bare form)."""
    return len(n_str.replace(",", ""))


def validate_number_format_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag sections that mix bare and comma-formatted large numbers.

    Within a single section, using both ``10000`` and ``10,000`` style for
    numbers of the same magnitude (≥5 digits) is inconsistent.  Emits
    ``number-format-inconsistency`` (minor) once per offending section.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        body = section.body
        if not body:
            continue

        bare_magnitudes = {
            _number_magnitude(m) for m in _BARE_LARGE_NUMBER_RE.findall(body)
        }
        comma_magnitudes = {
            _number_magnitude(m) for m in _COMMA_NUMBER_RE.findall(body)
        }
        overlap = bare_magnitudes & comma_magnitudes
        if overlap:
            example_mag = min(overlap)
            findings.append(
                Finding(
                    code="number-format-inconsistency",
                    severity="minor",
                    message=(
                        f"'{section.title}' mixes bare numbers (e.g. 10000) and "
                        "comma-formatted numbers (e.g. 10,000) for the same magnitude — "
                        "standardise to one style throughout."
                    ),
                    validator="number_format_consistency",
                    location=f"section '{section.title}'",
                    evidence=[f"magnitude ~{example_mag} digits appears in both styles"],
                )
            )
    return ValidationResult(validator_name="number_format_consistency", findings=findings)


# ---------------------------------------------------------------------------
# Phase 40 – Abstract keyword coverage
# ---------------------------------------------------------------------------

_ABSTRACT_TERM_RE = re.compile(
    r"(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)"           # Capitalized multi-word: Neural Network
    r"|(?:[a-z]+-[a-z]+(?:-[a-z]+)*)"               # hyphenated compound: fine-tuning
    r"|(?:[a-z]+[A-Z][a-z]+(?:[A-Z][a-z]+)*)",      # camelCase: backPropagation
)
_ABSTRACT_KEYWORD_MIN_TERMS = 3  # minimum extracted terms to bother checking
ABSTRACT_KEYWORD_COVERAGE_THRESHOLD = 0.30  # fraction of terms that must appear in body


def validate_abstract_keyword_coverage(parsed: ParsedManuscript) -> ValidationResult:
    """Flag when key technical terms from the abstract are absent from the body.

    Extracts capitalised multi-word noun phrases and hyphenated compounds from
    the abstract, then checks how many appear (case-insensitive) in the non-abstract
    body text.  Emits ``abstract-body-disconnect`` (moderate) when fewer than
    ``ABSTRACT_KEYWORD_COVERAGE_THRESHOLD`` of the extracted terms appear.

    Requires at least ``_ABSTRACT_KEYWORD_MIN_TERMS`` extracted terms to avoid
    false positives on sparse abstracts.
    """
    abstract = (parsed.abstract or "").strip()
    if not abstract:
        return ValidationResult(validator_name="abstract_keyword_coverage", findings=[])

    terms = list({m.lower() for m in _ABSTRACT_TERM_RE.findall(abstract)})
    if len(terms) < _ABSTRACT_KEYWORD_MIN_TERMS:
        return ValidationResult(validator_name="abstract_keyword_coverage", findings=[])

    body_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() not in ("abstract",)
    ).lower()

    matched = [t for t in terms if t in body_text]
    coverage = len(matched) / len(terms)

    if coverage < ABSTRACT_KEYWORD_COVERAGE_THRESHOLD:
        missing = [t for t in terms if t not in body_text][:5]
        findings = [
            Finding(
                code="abstract-body-disconnect",
                severity="moderate",
                message=(
                    f"Only {len(matched)}/{len(terms)} abstract technical terms appear in "
                    "the manuscript body — the abstract may over-promise relative to the content."
                ),
                validator="abstract_keyword_coverage",
                location="abstract",
                evidence=[f"absent terms: {', '.join(missing)}"] if missing else [],
            )
        ]
    else:
        findings = []
    return ValidationResult(validator_name="abstract_keyword_coverage", findings=findings)


# ---------------------------------------------------------------------------
# Phase 42 – Contribution claim count verifier
# ---------------------------------------------------------------------------

_CONTRIBUTION_COUNT_RE = re.compile(
    r"\b(?:make|present|describe|propose|introduce|identify|provide)"
    r"\s+(?:the\s+following\s+)?(\w+|\d+)\s+(?:key\s+|main\s+|novel\s+)?contributions?\b",
    re.IGNORECASE,
)
_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}
_ENUM_SIGNAL_RE = re.compile(
    r"(?:^|\n)\s*(?:\d+[\.\)]\s|\(?[ivx]+\)\s|[-*•]\s|first[,:]?\s|second[,:]?\s|third[,:]?\s)",
    re.IGNORECASE | re.MULTILINE,
)


def _parse_count_word(token: str) -> int | None:
    try:
        return int(token)
    except ValueError:
        return _NUMBER_WORDS.get(token.lower())


def validate_contribution_claim_count(parsed: ParsedManuscript) -> ValidationResult:
    """Flag when the claimed number of contributions exceeds what the body enumerates.

    Looks for "make N contributions" style claims in the abstract or introduction,
    then counts enumerated items (numbered lists, bullets, "First…Second…Third")
    across all non-abstract body sections.  Emits ``contribution-count-mismatch``
    (moderate) when the claimed count is greater than the enumerated body count.

    Requires claimed count ≥ 2 to avoid trivial single-contribution papers.
    """
    abstract = (parsed.abstract or "").strip()
    intro_body = ""
    for section in parsed.sections:
        if "introduction" in section.title.lower():
            intro_body = section.body
            break

    claimed_count: int | None = None
    for text in (abstract, intro_body):
        m = _CONTRIBUTION_COUNT_RE.search(text)
        if m:
            claimed_count = _parse_count_word(m.group(1))
            break

    if claimed_count is None or claimed_count < 2:
        return ValidationResult(validator_name="contribution_claim_count", findings=[])

    body_text = "\n".join(
        s.body for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    )
    found_count = len(_ENUM_SIGNAL_RE.findall(body_text))

    if found_count < claimed_count:
        return ValidationResult(
            validator_name="contribution_claim_count",
            findings=[
                Finding(
                    code="contribution-count-mismatch",
                    severity="moderate",
                    message=(
                        f"Abstract/introduction claims {claimed_count} contributions but "
                        f"only {found_count} enumerated items were found in the body — "
                        "verify that each claimed contribution is explicitly presented."
                    ),
                    validator="contribution_claim_count",
                    location="abstract/introduction",
                    evidence=[f"claimed: {claimed_count}; found: {found_count}"],
                )
            ],
        )
    return ValidationResult(validator_name="contribution_claim_count", findings=[])


# ---------------------------------------------------------------------------
# Phase 43 – First-person consistency validator
# ---------------------------------------------------------------------------

_FIRST_PERSON_I_RE = re.compile(r"(?<![A-Za-z])I\s", re.UNICODE)
_FIRST_PERSON_WE_RE = re.compile(r"(?<![A-Za-z])[Ww]e\s", re.UNICODE)
_FIRST_PERSON_MINORITY_THRESHOLD = 0.10  # minority fraction above which we flag


def validate_first_person_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts that mix singular 'I' and plural 'we' first-person voice.

    Counts 'I ' and 'we ' occurrences across all body sections (excluding abstract
    and references).  When both are present and the minority usage exceeds
    ``_FIRST_PERSON_MINORITY_THRESHOLD`` of total first-person uses, emits
    ``first-person-inconsistency`` (minor).
    """
    body_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() not in _SKIP_SECTIONS
    )
    i_count = len(_FIRST_PERSON_I_RE.findall(body_text))
    we_count = len(_FIRST_PERSON_WE_RE.findall(body_text))
    total = i_count + we_count

    if total == 0 or i_count == 0 or we_count == 0:
        return ValidationResult(validator_name="first_person_consistency", findings=[])

    minority = min(i_count, we_count)
    if minority / total > _FIRST_PERSON_MINORITY_THRESHOLD:
        dominant = "we" if we_count >= i_count else "I"
        other = "I" if dominant == "we" else "we"
        return ValidationResult(
            validator_name="first_person_consistency",
            findings=[
                Finding(
                    code="first-person-inconsistency",
                    severity="minor",
                    message=(
                        f"Manuscript mixes first-person '{dominant}' ({max(i_count, we_count)}×) "
                        f"and '{other}' ({min(i_count, we_count)}×) — "
                        "standardise to a single voice throughout."
                    ),
                    validator="first_person_consistency",
                    location="manuscript body",
                    evidence=[f"'I' count: {i_count}; 'we' count: {we_count}"],
                )
            ],
        )
    return ValidationResult(validator_name="first_person_consistency", findings=[])


# ---------------------------------------------------------------------------
# Phase 44 – Figure/table caption quality validator
# ---------------------------------------------------------------------------

_SHORT_CAPTION_THRESHOLD = 8  # words below which a caption is flagged as too short


def validate_caption_quality(parsed: ParsedManuscript) -> ValidationResult:
    """Flag figure and table captions that are too short or lack a terminal period.

    ``figure_definitions`` and ``table_definitions`` on ``ParsedManuscript`` contain
    caption text extracted by the parsers.  Emits:
    - ``short-caption`` (minor) when a caption is fewer than ``_SHORT_CAPTION_THRESHOLD`` words
    - ``caption-missing-period`` (minor) when a caption does not end with a period,
      question mark, or exclamation mark
    """
    findings: list[Finding] = []

    for kind, captions in (
        ("figure", parsed.figure_definitions),
        ("table", parsed.table_definitions),
    ):
        for caption in captions:
            caption = caption.strip()
            if not caption:
                continue
            word_count = len(caption.split())
            if word_count < _SHORT_CAPTION_THRESHOLD:
                findings.append(
                    Finding(
                        code="short-caption",
                        severity="minor",
                        message=(
                            f"A {kind} caption has only {word_count} words — "
                            "captions should be descriptive (≥8 words)."
                        ),
                        validator="caption_quality",
                        location=f"{kind} caption",
                        evidence=[caption[:100]],
                    )
                )
            if caption[-1] not in ".?!":
                findings.append(
                    Finding(
                        code="caption-missing-period",
                        severity="minor",
                        message=(
                            f"A {kind} caption does not end with a period — "
                            "captions should end with terminal punctuation."
                        ),
                        validator="caption_quality",
                        location=f"{kind} caption",
                        evidence=[caption[:100]],
                    )
                )
    return ValidationResult(validator_name="caption_quality", findings=findings)


# ---------------------------------------------------------------------------
# Phase 45 – Reference staleness validator
# ---------------------------------------------------------------------------

_STALE_YEARS_THRESHOLD = 10
_STALE_FRACTION_THRESHOLD = 0.60
_STALE_MIN_ENTRIES = 10
_CURRENT_YEAR = datetime.date.today().year


def validate_reference_staleness(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag when the majority of references are older than 10 years in empirical papers.

    Theory papers (``math_stats_theory``) are exempt.  Requires at least
    ``_STALE_MIN_ENTRIES`` bibliography entries with parseable years.
    Emits ``stale-reference-majority`` (minor) when >60% of dated entries
    were published more than 10 years ago.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="reference_staleness", findings=[])

    dated = [
        e for e in parsed.bibliography_entries if e.year and YEAR_RE.match(e.year)
    ]
    if len(dated) < _STALE_MIN_ENTRIES:
        return ValidationResult(validator_name="reference_staleness", findings=[])

    stale = [e for e in dated if (_CURRENT_YEAR - int(e.year)) > _STALE_YEARS_THRESHOLD]  # type: ignore[arg-type]
    fraction = len(stale) / len(dated)

    if fraction > _STALE_FRACTION_THRESHOLD:
        return ValidationResult(
            validator_name="reference_staleness",
            findings=[
                Finding(
                    code="stale-reference-majority",
                    severity="minor",
                    message=(
                        f"{len(stale)}/{len(dated)} dated references ({fraction:.0%}) "
                        f"are older than {_STALE_YEARS_THRESHOLD} years — "
                        "consider citing more recent work."
                    ),
                    validator="reference_staleness",
                    location="bibliography",
                    evidence=[f"stale entries: {len(stale)}; total dated: {len(dated)}"],
                )
            ],
        )
    return ValidationResult(validator_name="reference_staleness", findings=[])


# ---------------------------------------------------------------------------
# Phase 47 – Terminology drift detector
# ---------------------------------------------------------------------------

_COMPOUND_TERM_RE = re.compile(
    r"(?:[a-z]+-[a-z]+(?:-[a-z]+)*)"    # hyphenated: fine-tune, back-propagation
    r"|(?:[a-z]+\s+[a-z]+(?:\s+[a-z]+)?)",  # spaced: fine tune, random forest
    re.IGNORECASE,
)
# Minimum occurrences before we bother checking for drift
_HYPHEN_TERM_RE = re.compile(r"\b([a-z]+-[a-z]+(?:-[a-z]+)*)\b", re.IGNORECASE)
_DRIFT_MIN_OCCURRENCES = 3


def _term_root(term: str) -> str:
    """Normalise a compound term to a space-free lowercase root for comparison."""
    return re.sub(r"[-\s]+", "", term.lower())


def validate_terminology_drift(parsed: ParsedManuscript) -> ValidationResult:
    """Flag compound technical terms used in inconsistent spelling forms.

    Scans all non-skipped sections for hyphenated compound terms.  For each
    hyphenated term found with ≥ ``_DRIFT_MIN_OCCURRENCES`` occurrences, checks
    whether the same root also appears in spaced form (e.g. "fine tuning" for
    "fine-tuning").  Emits ``terminology-drift`` (minor) once per conflicting root.
    """
    from collections import Counter

    # Step 1: collect all hyphenated compound terms
    hyphen_counts: Counter[str] = Counter()
    body_texts: list[str] = []
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        body_texts.append(section.body)
        for m in _HYPHEN_TERM_RE.finditer(section.body):
            term = m.group(1).lower()
            if len(term) < 6:
                continue
            # Require each component to be ≥ 3 chars (avoids e.g. "a-b")
            parts = term.split("-")
            if all(len(p) >= 3 for p in parts):
                hyphen_counts[term] += 1

    full_body = " ".join(body_texts).lower()

    findings: list[Finding] = []
    seen_roots: set[str] = set()
    for hyphen_form, h_count in hyphen_counts.items():
        root = _term_root(hyphen_form)
        if root in seen_roots:
            continue
        # Build the spaced equivalent
        spaced_form = hyphen_form.replace("-", " ")
        # Count spaced occurrences via simple substring search (word-boundary safe)
        spaced_count = len(re.findall(
            r"(?<![a-z])" + re.escape(spaced_form) + r"(?![a-z])",
            full_body,
        ))
        if spaced_count > 0:
            total = h_count + spaced_count
            if total >= _DRIFT_MIN_OCCURRENCES:
                seen_roots.add(root)
                findings.append(
                    Finding(
                        code="terminology-drift",
                        severity="minor",
                        message=(
                            f"The term '{hyphen_form}' appears in both hyphenated and "
                            f"spaced forms ('{hyphen_form}': {h_count}×, "
                            f"'{spaced_form}': {spaced_count}×) — standardise to one form."
                        ),
                        validator="terminology_drift",
                        location="manuscript body",
                        evidence=[f"total occurrences: {total}"],
                    )
                )
    return ValidationResult(validator_name="terminology_drift", findings=findings)


# ---------------------------------------------------------------------------
# Phase 48 – Introduction structure validator
# ---------------------------------------------------------------------------

_INTRO_MOTIVATION_RE = re.compile(
    r"\b(challenge|problem|difficulty|obstacle|limitation|gap|need|lack|"
    r"require[sd]?|critical|important|crucial|essential|motivat)\b",
    re.IGNORECASE,
)
_INTRO_GAP_RE = re.compile(
    r"\b(however|nevertheless|yet|but\b|despite|although|no\s+prior|"
    r"limited\s+work|few\s+stud|no\s+exist|has\s+not\s+been|have\s+not\s+been|"
    r"remains?\s+(?:unclear|open|unknown|unsolved|unexplored))\b",
    re.IGNORECASE,
)
_INTRO_CONTRIBUTION_RE = re.compile(
    r"\b(we\s+propose|we\s+present|we\s+introduce|we\s+develop|we\s+describe|"
    r"this\s+paper\s+(?:presents?|proposes?|introduces?|describes?|develops?)|"
    r"in\s+this\s+(?:work|paper|study|article))\b",
    re.IGNORECASE,
)
_INTRO_MIN_WORDS = 100


def validate_introduction_structure(parsed: ParsedManuscript) -> ValidationResult:
    """Flag introductions that are missing the standard motivation–gap–contribution arc.

    Checks the introduction section for three rhetorical signals:
    - **Motivation**: problem/challenge/need language
    - **Gap**: contrastive or gap-pointing language (however, no prior work…)
    - **Contribution**: explicit "we propose/present/introduce" statements

    Emits ``missing-introduction-arc`` (minor) when 2 or more signals are absent.
    Requires at least ``_INTRO_MIN_WORDS`` words to avoid false positives on stubs.
    """
    intro_body = ""
    for section in parsed.sections:
        if "introduction" in section.title.lower():
            intro_body = section.body
            break

    if not intro_body or len(intro_body.split()) < _INTRO_MIN_WORDS:
        return ValidationResult(validator_name="introduction_structure", findings=[])

    missing = []
    if not _INTRO_MOTIVATION_RE.search(intro_body):
        missing.append("motivation (problem/need language)")
    if not _INTRO_GAP_RE.search(intro_body):
        missing.append("gap statement (however/no prior work)")
    if not _INTRO_CONTRIBUTION_RE.search(intro_body):
        missing.append("contribution statement (we propose/present)")

    if len(missing) >= 2:
        return ValidationResult(
            validator_name="introduction_structure",
            findings=[
                Finding(
                    code="missing-introduction-arc",
                    severity="minor",
                    message=(
                        "Introduction is missing key rhetorical elements: "
                        + "; ".join(missing)
                        + "."
                    ),
                    validator="introduction_structure",
                    location="Introduction",
                    evidence=[f"absent arcs: {', '.join(missing)}"],
                )
            ],
        )
    return ValidationResult(validator_name="introduction_structure", findings=[])


# ---------------------------------------------------------------------------
# Phase 49 – Reproducibility checklist validator
# ---------------------------------------------------------------------------

_REPRO_DATASET_RE = re.compile(
    r"\b(dataset|data\s+set|corpus|benchmark|training\s+data|test\s+set|"
    r"evaluation\s+set|held[\s-]out)\b",
    re.IGNORECASE,
)
_REPRO_CODE_RE = re.compile(
    r"\b(github|gitlab|code\s+available|source\s+code|repository|repo|"
    r"open[\s-]source|available\s+at|implementation|https?://)\b",
    re.IGNORECASE,
)
_REPRO_SEED_RE = re.compile(
    r"\b(random\s+seed|seed\s+=|numpy\.random|torch\.manual_seed|"
    r"set_seed|reproducib|fixed\s+seed)\b",
    re.IGNORECASE,
)
_REPRO_HYPERPARAMS_RE = re.compile(
    r"\b(learning\s+rate|batch\s+size|epoch[s]?|hyperparame|tuning|"
    r"grid\s+search|cross[\s-]validat|dropout|weight\s+decay)\b",
    re.IGNORECASE,
)
_REPRO_PAPER_TYPES = frozenset({"empirical_paper", "software_workflow_paper"})


def validate_reproducibility_checklist(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag missing reproducibility elements in empirical and software papers.

    Scans the full manuscript text for evidence of:
    - dataset/data source description
    - code/repository availability
    - random seed reporting
    - hyperparameter reporting

    Emits ``missing-reproducibility-element`` (minor) for each absent element.
    Only fires for paper types in ``_REPRO_PAPER_TYPES``.
    """
    if classification.paper_type not in _REPRO_PAPER_TYPES:
        return ValidationResult(validator_name="reproducibility_checklist", findings=[])

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)

    checks = [
        ("dataset description", _REPRO_DATASET_RE),
        ("code/repository availability", _REPRO_CODE_RE),
        ("random seed reporting", _REPRO_SEED_RE),
        ("hyperparameter reporting", _REPRO_HYPERPARAMS_RE),
    ]
    findings: list[Finding] = []
    for label, pattern in checks:
        if not pattern.search(full):
            findings.append(
                Finding(
                    code="missing-reproducibility-element",
                    severity="minor",
                    message=(
                        f"No evidence of {label} found — "
                        "include this for reproducibility."
                    ),
                    validator="reproducibility_checklist",
                    location="manuscript body",
                    evidence=[f"missing: {label}"],
                )
            )
    return ValidationResult(validator_name="reproducibility_checklist", findings=findings)


# ---------------------------------------------------------------------------
# Phase 50 – Self-citation ratio validator
# ---------------------------------------------------------------------------

_SELF_CITE_MIN_ENTRIES = 8
_SELF_CITE_THRESHOLD = 0.40  # fraction of entries with the most common last-name


def _last_names_from_authors(authors: list[str]) -> list[str]:
    """Extract last names from author strings ('Last, First' or 'First Last')."""
    names = []
    for author in authors:
        author = author.strip()
        if not author:
            continue
        if "," in author:
            names.append(author.split(",")[0].strip().lower())
        else:
            parts = author.split()
            if parts:
                names.append(parts[-1].lower())
    return names


def validate_self_citation_ratio(parsed: ParsedManuscript) -> ValidationResult:
    """Proxy check for self-citation bias in the bibliography.

    Finds the last name that appears as an author in the highest fraction of
    bibliography entries.  When that fraction exceeds ``_SELF_CITE_THRESHOLD``
    and there are at least ``_SELF_CITE_MIN_ENTRIES`` entries with author data,
    emits ``high-self-citation-ratio`` (minor).

    This is a heuristic proxy: it cannot distinguish legitimate citations from
    self-citations without knowing the submitting authors.
    """
    from collections import Counter

    entries_with_authors = [
        e for e in parsed.bibliography_entries if e.authors
    ]
    if len(entries_with_authors) < _SELF_CITE_MIN_ENTRIES:
        return ValidationResult(validator_name="self_citation_ratio", findings=[])

    # Count how many entries each last name appears in
    name_entry_count: Counter[str] = Counter()
    for entry in entries_with_authors:
        entry_names = set(_last_names_from_authors(entry.authors))
        for name in entry_names:
            name_entry_count[name] += 1

    if not name_entry_count:
        return ValidationResult(validator_name="self_citation_ratio", findings=[])

    top_name, top_count = name_entry_count.most_common(1)[0]
    fraction = top_count / len(entries_with_authors)

    if fraction > _SELF_CITE_THRESHOLD:
        return ValidationResult(
            validator_name="self_citation_ratio",
            findings=[
                Finding(
                    code="high-self-citation-ratio",
                    severity="minor",
                    message=(
                        f"Author last name '{top_name}' appears in "
                        f"{top_count}/{len(entries_with_authors)} bibliography entries "
                        f"({fraction:.0%}) — verify this is not excessive self-citation."
                    ),
                    validator="self_citation_ratio",
                    location="bibliography",
                    evidence=[
                        f"'{top_name}' in {top_count}/{len(entries_with_authors)} entries"
                    ],
                )
            ],
        )
    return ValidationResult(validator_name="self_citation_ratio", findings=[])


# ---------------------------------------------------------------------------
# Phase 51 – Conclusion scope validator
# ---------------------------------------------------------------------------

_CONCLUSION_NEW_CLAIM_RE = re.compile(
    r"\b(we\s+(?:show|demonstrate|prove|find|establish|confirm)\s+that|"
    r"our\s+(?:results?\s+show|analysis\s+shows?|experiments?\s+demonstrate))\b",
    re.IGNORECASE,
)
_CONCLUSION_SECTIONS = frozenset(
    {"conclusion", "conclusions", "concluding remarks", "summary and conclusions"}
)


def validate_conclusion_scope(parsed: ParsedManuscript) -> ValidationResult:
    """Flag new quantitative claims introduced only in the conclusion.

    A conclusion should summarise findings already established in the body.
    Emits ``conclusion-scope-creep`` (moderate) when the conclusion contains
    ``METRIC_RE`` matches (percentages, fold-improvements, X× values) that do
    not appear in the abstract or results sections.

    Only fires when 2+ such novel metrics are present to reduce false positives
    from legitimate recapping that restates approximate numbers.
    """
    conclusion_body = ""
    for section in parsed.sections:
        if section.title.lower() in _CONCLUSION_SECTIONS:
            conclusion_body = section.body
            break

    if not conclusion_body:
        return ValidationResult(validator_name="conclusion_scope", findings=[])

    # Collect metrics that are in conclusion
    conclusion_metrics = set(METRIC_RE.findall(conclusion_body))
    if not conclusion_metrics:
        return ValidationResult(validator_name="conclusion_scope", findings=[])

    # Collect metrics from abstract + results sections
    established_text = (parsed.abstract or "") + " "
    for section in parsed.sections:
        title_lower = section.title.lower()
        if any(kw in title_lower for kw in ("result", "experiment", "evaluation", "analysis")):
            established_text += section.body + " "

    established_metrics = set(METRIC_RE.findall(established_text))
    novel_metrics = conclusion_metrics - established_metrics

    if len(novel_metrics) >= 2:
        examples = sorted(novel_metrics)[:3]
        return ValidationResult(
            validator_name="conclusion_scope",
            findings=[
                Finding(
                    code="conclusion-scope-creep",
                    severity="moderate",
                    message=(
                        f"Conclusion introduces {len(novel_metrics)} quantitative claims "
                        "not present in abstract or results — "
                        "conclusions should summarise, not introduce new evidence."
                    ),
                    validator="conclusion_scope",
                    location="Conclusion",
                    evidence=[f"novel metrics: {', '.join(examples)}"],
                )
            ],
        )
    return ValidationResult(validator_name="conclusion_scope", findings=[])


# ---------------------------------------------------------------------------
# Phase 53 – Equation density validator
# ---------------------------------------------------------------------------

_EQUATION_DENSITY_MIN_SECTIONS = 4
_EQUATION_DENSITY_MIN_RATIO = 0.5  # equations per section


def validate_equation_density(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag math/theory papers with unexpectedly low equation density.

    For ``math_stats_theory`` papers only.  When the manuscript has at least
    ``_EQUATION_DENSITY_MIN_SECTIONS`` non-trivial sections but fewer than
    ``_EQUATION_DENSITY_MIN_RATIO`` equation blocks per section, emits
    ``low-equation-density`` (minor) — the paper claims to be a theory paper
    but reads more like a descriptive survey.
    """
    if classification.pathway != "math_stats_theory":
        return ValidationResult(validator_name="equation_density", findings=[])

    content_sections = [
        s for s in parsed.sections
        if s.title.lower() not in _SKIP_SECTIONS
    ]
    if len(content_sections) < _EQUATION_DENSITY_MIN_SECTIONS:
        return ValidationResult(validator_name="equation_density", findings=[])

    eq_count = len(parsed.equation_blocks)
    ratio = eq_count / len(content_sections)

    if ratio < _EQUATION_DENSITY_MIN_RATIO:
        return ValidationResult(
            validator_name="equation_density",
            findings=[
                Finding(
                    code="low-equation-density",
                    severity="minor",
                    message=(
                        f"Math/theory paper has only {eq_count} equation block(s) across "
                        f"{len(content_sections)} sections (ratio {ratio:.2f}) — "
                        "theory papers typically contain more formal derivations."
                    ),
                    validator="equation_density",
                    location="manuscript body",
                    evidence=[f"equations: {eq_count}; sections: {len(content_sections)}"],
                )
            ],
        )
    return ValidationResult(validator_name="equation_density", findings=[])


# ---------------------------------------------------------------------------
# Phase 54 – Abstract structure validator
# ---------------------------------------------------------------------------

_ABSTRACT_METHOD_RE = re.compile(
    r"\b(we\s+propose|we\s+present|we\s+introduce|we\s+develop|we\s+use|"
    r"we\s+apply|our\s+(?:method|approach|model|framework|system)|"
    r"this\s+(?:paper|work|study)\s+(?:proposes?|presents?|introduces?|uses?|applies?))\b",
    re.IGNORECASE,
)
_ABSTRACT_RESULT_RE = re.compile(
    r"\b(we\s+(?:show|demonstrate|find|establish|achieve|obtain)|"
    r"(?:results?\s+(?:show|demonstrate|indicate)|experiments?\s+(?:show|confirm))|"
    r"(?:achieve[sd]?|outperform[sd]?|improv[esd]+)\b)",
    re.IGNORECASE,
)
_ABSTRACT_MIN_WORDS = 50


def validate_abstract_structure(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abstracts missing method or result components.

    A well-structured abstract should contain at least a method signal
    (what was done) and a result signal (what was found).  Emits
    ``missing-abstract-component`` (minor) when either is absent.
    Requires ≥``_ABSTRACT_MIN_WORDS`` words to avoid trivial cases.
    """
    abstract = (parsed.abstract or "").strip()
    if not abstract or len(abstract.split()) < _ABSTRACT_MIN_WORDS:
        return ValidationResult(validator_name="abstract_structure", findings=[])

    missing = []
    if not _ABSTRACT_METHOD_RE.search(abstract):
        missing.append("method/approach description")
    if not _ABSTRACT_RESULT_RE.search(abstract):
        missing.append("result/finding statement")

    if missing:
        return ValidationResult(
            validator_name="abstract_structure",
            findings=[
                Finding(
                    code="missing-abstract-component",
                    severity="minor",
                    message=(
                        "Abstract is missing: "
                        + "; ".join(missing)
                        + " — abstracts should state what was done and what was found."
                    ),
                    validator="abstract_structure",
                    location="abstract",
                    evidence=[f"absent: {', '.join(missing)}"],
                )
            ],
        )
    return ValidationResult(validator_name="abstract_structure", findings=[])


# ---------------------------------------------------------------------------
# Phase 55 – URL format validator
# ---------------------------------------------------------------------------

_URL_IN_TEXT_RE = re.compile(
    r"(?:https?://\S+|www\.\S+|ftp://\S+)",
    re.IGNORECASE,
)
_VALID_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_URL_FINDINGS_CAP = 5


def validate_url_format(parsed: ParsedManuscript) -> ValidationResult:
    """Flag malformed URLs and bibliography URLs missing access dates.

    Scans full_text for bare URLs.  Emits ``malformed-url`` (minor) for
    URLs that do not start with ``http://`` or ``https://``.

    Also scans bibliography entries: emits ``url-without-access-date`` (minor)
    when an entry has a ``url`` field but no indication of an access date in
    its ``raw_text``.  Capped at ``_URL_FINDINGS_CAP`` total findings.
    """
    findings: list[Finding] = []

    text_to_scan = parsed.full_text or " ".join(s.body for s in parsed.sections)
    for m in _URL_IN_TEXT_RE.finditer(text_to_scan):
        url = m.group(0).rstrip(".,;)")
        if not _VALID_URL_RE.match(url):
            findings.append(
                Finding(
                    code="malformed-url",
                    severity="minor",
                    message=(
                        f"URL '{url[:60]}' does not start with http:// or https://."
                    ),
                    validator="url_format",
                    location="manuscript body",
                    evidence=[url[:80]],
                )
            )
            if len(findings) >= _URL_FINDINGS_CAP:
                break

    if len(findings) < _URL_FINDINGS_CAP:
        access_keywords = re.compile(
            r"\b(accessed|retrieved|available|last\s+visited)\b", re.IGNORECASE
        )
        for entry in parsed.bibliography_entries:
            if entry.url and not access_keywords.search(entry.raw_text):
                findings.append(
                    Finding(
                        code="url-without-access-date",
                        severity="minor",
                        message=(
                            f"Bibliography entry '{entry.key or entry.raw_text[:40]}' "
                            "has a URL but no access date — add 'Accessed: YYYY-MM-DD'."
                        ),
                        validator="url_format",
                        location="bibliography",
                        evidence=[entry.url[:80]],
                    )
                )
                if len(findings) >= _URL_FINDINGS_CAP:
                    break

    return ValidationResult(validator_name="url_format", findings=findings)


# ---------------------------------------------------------------------------
# Phase 56 – Figure/table balance validator
# ---------------------------------------------------------------------------

_FIG_TABLE_MIN_SECTIONS = 4
_MIN_FIGURE_MENTIONS = 2
_TABLE_DOMINANCE_FACTOR = 2


def validate_figure_table_balance(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that are under-illustrated or table-heavy.

    Emits ``insufficient-figures`` (minor) when an empirical paper has
    ≥``_FIG_TABLE_MIN_SECTIONS`` content sections but fewer than
    ``_MIN_FIGURE_MENTIONS`` figure mentions.

    Emits ``table-heavy`` (minor) when table mentions exceed
    ``_TABLE_DOMINANCE_FACTOR``× figure mentions and both are non-zero.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="figure_table_balance", findings=[])

    content_sections = [
        s for s in parsed.sections
        if s.title.lower() not in _SKIP_SECTIONS
    ]
    if len(content_sections) < _FIG_TABLE_MIN_SECTIONS:
        return ValidationResult(validator_name="figure_table_balance", findings=[])

    n_figs = len(parsed.figure_mentions)
    n_tabs = len(parsed.table_mentions)
    findings: list[Finding] = []

    if n_figs < _MIN_FIGURE_MENTIONS:
        findings.append(
            Finding(
                code="insufficient-figures",
                severity="minor",
                message=(
                    f"Empirical paper has only {n_figs} figure mention(s) across "
                    f"{len(content_sections)} sections — consider adding visualisations."
                ),
                validator="figure_table_balance",
                location="manuscript body",
                evidence=[f"figure mentions: {n_figs}"],
            )
        )

    if n_figs > 0 and n_tabs > _TABLE_DOMINANCE_FACTOR * n_figs:
        findings.append(
            Finding(
                code="table-heavy",
                severity="minor",
                message=(
                    f"Table mentions ({n_tabs}) exceed {_TABLE_DOMINANCE_FACTOR}× "
                    f"figure mentions ({n_figs}) — consider converting some tables "
                    "to figures for readability."
                ),
                validator="figure_table_balance",
                location="manuscript body",
                evidence=[f"figures: {n_figs}; tables: {n_tabs}"],
            )
        )
    return ValidationResult(validator_name="figure_table_balance", findings=findings)


# ---------------------------------------------------------------------------
# Phase 57 – Standard section ordering (IMRaD) validator
# ---------------------------------------------------------------------------

_IMRAD_ORDER = ["introduction", "method", "result", "discussion"]
_IMRAD_SECTION_TYPES = frozenset(
    {"introduction", "method", "result", "discussion",
     "methodology", "methods", "results", "conclusions"}
)


def _imrad_key(title: str) -> int | None:
    """Return IMRaD order index (0-3) for a section title, or None if not IMRaD."""
    t = title.lower()
    if "introduction" in t:
        return 0
    if "method" in t:
        return 1
    if "result" in t or "experiment" in t:
        return 2
    if "discussion" in t or "conclusion" in t:
        return 3
    return None


def validate_section_ordering(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag violations of standard IMRaD section order in empirical papers.

    For empirical and applied papers, Introduction must precede Methods,
    Methods must precede Results, and Results must precede Discussion.
    Only sections that can be mapped to an IMRaD slot are checked.
    Emits ``section-order-violation`` (minor) for each inversion found.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="section_ordering", findings=[])

    imrad_positions: list[tuple[int, str]] = []
    for section in parsed.sections:
        key = _imrad_key(section.title)
        if key is not None:
            imrad_positions.append((key, section.title))

    if len(imrad_positions) < 2:
        return ValidationResult(validator_name="section_ordering", findings=[])

    findings: list[Finding] = []
    for idx in range(len(imrad_positions) - 1):
        key_a, title_a = imrad_positions[idx]
        key_b, title_b = imrad_positions[idx + 1]
        if key_a > key_b:
            findings.append(
                Finding(
                    code="section-order-violation",
                    severity="minor",
                    message=(
                        f"Section '{title_a}' appears before '{title_b}' "
                        "but standard IMRaD order requires the reverse — "
                        "reorder for convention compliance."
                    ),
                    validator="section_ordering",
                    location=f"sections '{title_a}' / '{title_b}'",
                    evidence=[f"order: {key_a} before {key_b}"],
                )
            )
    return ValidationResult(validator_name="section_ordering", findings=findings)


# ---------------------------------------------------------------------------
# Phase 59 – Author keyword coverage
# ---------------------------------------------------------------------------

_KEYWORDS_LINE_RE = re.compile(
    r"(?:^|\n)\s*keywords?\s*[:—]\s*(.+)",
    re.IGNORECASE,
)
_KEYWORD_SPLIT_RE = re.compile(r"[;,]")


def validate_keyword_section_coverage(parsed: ParsedManuscript) -> ValidationResult:
    """Flag author-supplied keywords that do not appear in the manuscript body.

    Extracts keywords from a 'Keywords:' line in the full text or from a
    dedicated 'Keywords' section.  For each keyword, checks whether it appears
    (case-insensitive) in the non-abstract body text.  Emits
    ``missing-keyword-coverage`` (minor) for absent keywords, capped at 5.
    """
    raw_keywords: list[str] = []

    # Try 'Keywords:' line in full_text or abstract
    search_text = parsed.full_text or (parsed.abstract or "")
    m = _KEYWORDS_LINE_RE.search(search_text)
    if m:
        raw_keywords = [k.strip() for k in _KEYWORD_SPLIT_RE.split(m.group(1)) if k.strip()]

    # Fallback: dedicated Keywords section
    if not raw_keywords:
        for section in parsed.sections:
            if "keyword" in section.title.lower():
                raw_keywords = [
                    k.strip() for k in _KEYWORD_SPLIT_RE.split(section.body) if k.strip()
                ]
                break

    if not raw_keywords:
        return ValidationResult(validator_name="keyword_section_coverage", findings=[])

    body_text = " ".join(
        s.body for s in parsed.sections
        if s.title.lower() not in ("abstract", "keywords", "keyword")
    ).lower()

    findings: list[Finding] = []
    for kw in raw_keywords:
        if kw.lower() not in body_text:
            findings.append(
                Finding(
                    code="missing-keyword-coverage",
                    severity="minor",
                    message=(
                        f"Keyword '{kw}' is listed but does not appear in the manuscript "
                        "body — ensure keywords reflect the actual content."
                    ),
                    validator="keyword_section_coverage",
                    location="keywords",
                    evidence=[f"missing keyword: '{kw}'"],
                )
            )
            if len(findings) >= 5:
                break
    return ValidationResult(
        validator_name="keyword_section_coverage", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 60 – Statistical test reporting validator
# ---------------------------------------------------------------------------

_STAT_TEST_RE = re.compile(
    r"\b(t[\s-]test|anova|chi[\s-]square|mann[\s-]whitney|wilcoxon|"
    r"kruskal[\s-]wallis|fisher['']?s?\s+exact|logistic\s+regression|"
    r"linear\s+regression|pearson|spearman|kendall|mcnemar)\b",
    re.IGNORECASE,
)
_PVALUE_RE = re.compile(
    r"p\s*[<>=≤≥]\s*(?:0\.\d+|\.\d+)"   # p < 0.05, p = 0.001
    r"|p[\s-]value",
    re.IGNORECASE,
)
_STAT_TEST_SECTIONS = frozenset({"methods", "methodology", "results", "analysis"})


def validate_statistical_test_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag statistical tests named but not accompanied by p-value reporting.

    Scans Methods and Results sections of empirical/applied papers for
    statistical test names.  If any test is found but no p-value pattern
    appears anywhere in those sections, emits ``missing-p-value-report``
    (moderate).
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="statistical_test_reporting", findings=[]
        )

    relevant_text = ""
    for section in parsed.sections:
        if any(kw in section.title.lower() for kw in _STAT_TEST_SECTIONS):
            relevant_text += " " + section.body

    if not relevant_text.strip():
        return ValidationResult(
            validator_name="statistical_test_reporting", findings=[]
        )

    found_test = _STAT_TEST_RE.search(relevant_text)
    if not found_test:
        return ValidationResult(
            validator_name="statistical_test_reporting", findings=[]
        )

    if _PVALUE_RE.search(relevant_text):
        return ValidationResult(
            validator_name="statistical_test_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="statistical_test_reporting",
        findings=[
            Finding(
                code="missing-p-value-report",
                severity="moderate",
                message=(
                    f"Statistical test '{found_test.group(0)}' is named but no p-value "
                    "reporting pattern was found — report p-values for all inferential tests."
                ),
                validator="statistical_test_reporting",
                location="Methods/Results",
                evidence=[f"test found: '{found_test.group(0)}'"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 61 – Effect size reporting validator
# ---------------------------------------------------------------------------

_EFFECT_SIZE_RE = re.compile(
    r"\b(cohen['']?s?\s+d|cohen['']?s?\s+f|eta[\s-]squared|omega[\s-]squared|"
    r"effect\s+size|r\s*=\s*\d|odds\s+ratio|hazard\s+ratio|risk\s+ratio|"
    r"relative\s+risk|number\s+needed\s+to\s+treat|nnt\b|hedges['']?\s+g|"
    r"glass['']?\s+delta|partial\s+eta)\b",
    re.IGNORECASE,
)


def validate_effect_size_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that report p-values but omit effect sizes.

    Effect sizes contextualise statistical significance.  When the manuscript
    body contains p-value patterns but no effect size measure, emits
    ``missing-effect-size`` (minor).  Only fires for empirical/applied papers.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="effect_size_reporting", findings=[])

    body_text = " ".join(
        s.body for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    )

    if not _PVALUE_RE.search(body_text):
        return ValidationResult(validator_name="effect_size_reporting", findings=[])

    if _EFFECT_SIZE_RE.search(body_text):
        return ValidationResult(validator_name="effect_size_reporting", findings=[])

    return ValidationResult(
        validator_name="effect_size_reporting",
        findings=[
            Finding(
                code="missing-effect-size",
                severity="minor",
                message=(
                    "P-values are reported but no effect size measure was found — "
                    "report effect sizes (Cohen's d, η², odds ratio, etc.) alongside "
                    "p-values to contextualise statistical significance."
                ),
                validator="effect_size_reporting",
                location="manuscript body",
                evidence=["p-value found; effect size absent"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 62 – Acknowledgments presence validator
# ---------------------------------------------------------------------------

_ACKNOWLEDGMENT_SECTIONS = frozenset(
    {"acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement"}
)
_FUNDING_RE = re.compile(
    r"\b(grant|funded\s+by|supported\s+by|funding|acknowledgment|acknowledgement|"
    r"nsf|nih|erc|dfg|anr|nserc|epsrc|wellcome)\b",
    re.IGNORECASE,
)
_ACKNOWLEDGMENT_MIN_ENTRIES = 5


def validate_acknowledgments_presence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers with no acknowledgments or funding statement.

    A missing acknowledgments section on a paper with substantial references
    may indicate an oversight.  Emits ``missing-acknowledgments`` (minor) when:
    - paper is empirical/applied/software,
    - has ≥ ``_ACKNOWLEDGMENT_MIN_ENTRIES`` bibliography entries, and
    - no Acknowledgments section exists AND no funding keyword appears in
      the full text.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="acknowledgments_presence", findings=[]
        )

    if len(parsed.bibliography_entries) < _ACKNOWLEDGMENT_MIN_ENTRIES:
        return ValidationResult(
            validator_name="acknowledgments_presence", findings=[]
        )

    # Check for dedicated acknowledgments section
    for section in parsed.sections:
        if section.title.lower() in _ACKNOWLEDGMENT_SECTIONS:
            return ValidationResult(
                validator_name="acknowledgments_presence", findings=[]
            )

    # Check for funding keywords anywhere in the text
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _FUNDING_RE.search(full):
        return ValidationResult(
            validator_name="acknowledgments_presence", findings=[]
        )

    return ValidationResult(
        validator_name="acknowledgments_presence",
        findings=[
            Finding(
                code="missing-acknowledgments",
                severity="minor",
                message=(
                    "No Acknowledgments section or funding statement was found — "
                    "consider adding one to declare funding sources and conflicts of interest."
                ),
                validator="acknowledgments_presence",
                location="manuscript structure",
                evidence=["no acknowledgments section or funding keyword detected"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 64 – Conflict of interest disclosure
# ---------------------------------------------------------------------------

_COI_RE = re.compile(
    r"\b(conflict[s]?\s+of\s+interest|competing\s+interest[s]?|"
    r"declaration\s+of\s+interest[s]?|coi\b|no\s+competing|"
    r"nothing\s+to\s+disclose|disclose[sd]?\s+no)\b",
    re.IGNORECASE,
)
_COI_MIN_ENTRIES = 5


def validate_conflict_of_interest(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical/applied/software papers missing a COI statement."""
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="conflict_of_interest", findings=[])

    if len(parsed.bibliography_entries) < _COI_MIN_ENTRIES:
        return ValidationResult(validator_name="conflict_of_interest", findings=[])

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _COI_RE.search(full):
        return ValidationResult(validator_name="conflict_of_interest", findings=[])

    for section in parsed.sections:
        if _COI_RE.search(section.title) or _COI_RE.search(section.body):
            return ValidationResult(
                validator_name="conflict_of_interest", findings=[]
            )

    return ValidationResult(
        validator_name="conflict_of_interest",
        findings=[
            Finding(
                code="missing-coi-statement",
                severity="minor",
                message=(
                    "No conflict of interest statement was found — "
                    "most journals require a COI declaration."
                ),
                validator="conflict_of_interest",
                location="manuscript structure",
                evidence=["no COI language detected"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 65 – Data availability statement
# ---------------------------------------------------------------------------

_DATA_AVAIL_RE = re.compile(
    r"\b(data\s+availability|data\s+available|dataset[s]?\s+(?:are\s+)?(?:available|released|shared)|"
    r"code\s+and\s+data|open\s+data|"
    r"data\s+(?:can\s+be\s+)?(?:accessed|downloaded|obtained)|"
    r"zenodo|figshare|osf\.io|dryad|harvard\s+dataverse|data\s+repository|"
    r"upon\s+(?:reasonable\s+)?request)\b",
    re.IGNORECASE,
)
_DATA_AVAIL_PAPER_TYPES = frozenset({"empirical_paper", "software_workflow_paper"})


def validate_data_availability(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical/software papers without a data availability statement."""
    if classification.paper_type not in _DATA_AVAIL_PAPER_TYPES:
        return ValidationResult(validator_name="data_availability", findings=[])

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _DATA_AVAIL_RE.search(full):
        return ValidationResult(validator_name="data_availability", findings=[])

    return ValidationResult(
        validator_name="data_availability",
        findings=[
            Finding(
                code="missing-data-availability",
                severity="minor",
                message=(
                    "No data availability statement was found — "
                    "state where data and/or code can be accessed."
                ),
                validator="data_availability",
                location="manuscript structure",
                evidence=["no data availability language detected"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 66 – Ethics/IRB statement
# ---------------------------------------------------------------------------

_HUMAN_STUDY_RE = re.compile(
    r"\b(participants?|subjects?|patients?|volunteers?|respondents?|"
    r"human\s+(?:subjects?|participants?|data)|survey(?:ed)?|interview[sd]?|"
    r"informed\s+consent)\b",
    re.IGNORECASE,
)
_ANIMAL_STUDY_RE = re.compile(
    r"\b(mice|rats?|rabbits?|primates?|rodents?|animal\s+(?:study|subjects?|model)|"
    r"in\s+vivo|murine|zebrafish)\b",
    re.IGNORECASE,
)
_ETHICS_RE = re.compile(
    r"\b(irb|institutional\s+review\s+board|ethics\s+(?:committee|approval|board)|"
    r"ethical\s+approval|approved\s+by|protocol\s+approved|declaration\s+of\s+helsinki|"
    r"iacuc|animal\s+(?:care|ethics|welfare)|research\s+ethics)\b",
    re.IGNORECASE,
)


def validate_ethics_statement(parsed: ParsedManuscript) -> ValidationResult:
    """Flag human/animal studies lacking an ethics/IRB approval statement."""
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)

    is_human = bool(_HUMAN_STUDY_RE.search(full))
    is_animal = bool(_ANIMAL_STUDY_RE.search(full))

    if not (is_human or is_animal):
        return ValidationResult(validator_name="ethics_statement", findings=[])

    if _ETHICS_RE.search(full):
        return ValidationResult(validator_name="ethics_statement", findings=[])

    study_type = "human-subject" if is_human else "animal"
    return ValidationResult(
        validator_name="ethics_statement",
        findings=[
            Finding(
                code="missing-ethics-statement",
                severity="moderate",
                message=(
                    f"Manuscript appears to involve {study_type} research but no "
                    "IRB/ethics approval statement was found."
                ),
                validator="ethics_statement",
                location="manuscript body",
                evidence=[f"study type detected: {study_type}"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 67 – Citation style consistency
# ---------------------------------------------------------------------------

_NUMBERED_CITE_RE = re.compile(r"\[\d+(?:[,\s]\d+)*\]")
_AUTHOR_YEAR_CITE_RE = re.compile(
    r"(?:[A-Z][a-z]+(?:\s+et\s+al\.?)?\s*\(?\d{4}\)?)"
    r"|(?:\([A-Z][a-z]+(?:\s+et\s+al\.?)?,?\s+\d{4}\))",
)
_CITE_STYLE_MIN = 5
_CITE_STYLE_MINORITY_THRESHOLD = 0.10


def validate_citation_style_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts that mix numbered and author-year citation styles.

    Emits ``citation-style-inconsistency`` (minor) when both styles appear
    and the minority style exceeds ``_CITE_STYLE_MINORITY_THRESHOLD``.
    """
    body_text = " ".join(
        s.body for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    )
    n_numbered = len(_NUMBERED_CITE_RE.findall(body_text))
    n_author_year = len(_AUTHOR_YEAR_CITE_RE.findall(body_text))
    total = n_numbered + n_author_year

    if total < _CITE_STYLE_MIN or n_numbered == 0 or n_author_year == 0:
        return ValidationResult(
            validator_name="citation_style_consistency", findings=[]
        )

    minority = min(n_numbered, n_author_year)
    if minority / total <= _CITE_STYLE_MINORITY_THRESHOLD:
        return ValidationResult(
            validator_name="citation_style_consistency", findings=[]
        )

    dominant = "numbered [N]" if n_numbered >= n_author_year else "author-year"
    other = "author-year" if dominant == "numbered [N]" else "numbered [N]"
    return ValidationResult(
        validator_name="citation_style_consistency",
        findings=[
            Finding(
                code="citation-style-inconsistency",
                severity="minor",
                message=(
                    f"Citation style mixes {dominant} "
                    f"({max(n_numbered, n_author_year)}\u00d7) and {other} "
                    f"({min(n_numbered, n_author_year)}\u00d7) — "
                    "standardise to a single citation style."
                ),
                validator="citation_style_consistency",
                location="manuscript body",
                evidence=[
                    f"numbered: {n_numbered}; author-year: {n_author_year}"
                ],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 68 – Cross-reference integrity
# ---------------------------------------------------------------------------

_FIGURE_REF_RE = re.compile(r"\b[Ff]ig(?:ure)?s?\.?\s*(\d+)\b")
_TABLE_REF_RE = re.compile(r"\b[Tt]ables?\.?\s*(\d+)\b")


def validate_cross_reference_integrity(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Figure/Table N references where N exceeds the known definition count.

    Only fires when definitions are non-empty.
    Emits ``cross-reference-out-of-range`` (minor).
    """
    findings: list[Finding] = []
    body_text = " ".join(
        s.body for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    )

    n_figs = len(parsed.figure_definitions)
    if n_figs > 0:
        refs = {int(m.group(1)) for m in _FIGURE_REF_RE.finditer(body_text)}
        for ref_num in sorted(refs):
            if ref_num > n_figs:
                findings.append(
                    Finding(
                        code="cross-reference-out-of-range",
                        severity="minor",
                        message=(
                            f"Figure {ref_num} is referenced but only {n_figs} "
                            "figure definition(s) were found."
                        ),
                        validator="cross_reference_integrity",
                        location="manuscript body",
                        evidence=[
                            f"referenced Figure {ref_num}; definitions: {n_figs}"
                        ],
                    )
                )
                if len(findings) >= _FINDINGS_PER_SECTION_CAP:
                    break

    n_tabs = len(parsed.table_definitions)
    if n_tabs > 0 and len(findings) < _FINDINGS_PER_SECTION_CAP:
        refs = {int(m.group(1)) for m in _TABLE_REF_RE.finditer(body_text)}
        for ref_num in sorted(refs):
            if ref_num > n_tabs:
                findings.append(
                    Finding(
                        code="cross-reference-out-of-range",
                        severity="minor",
                        message=(
                            f"Table {ref_num} is referenced but only {n_tabs} "
                            "table definition(s) were found."
                        ),
                        validator="cross_reference_integrity",
                        location="manuscript body",
                        evidence=[
                            f"referenced Table {ref_num}; definitions: {n_tabs}"
                        ],
                    )
                )
                if len(findings) >= _FINDINGS_PER_SECTION_CAP:
                    break

    return ValidationResult(
        validator_name="cross_reference_integrity", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 69 – Decimal precision consistency
# ---------------------------------------------------------------------------

_PERCENT_VALUE_RE = re.compile(r"(\d+)(?:\.(\d+))?\s*%")
_DECIMAL_MIN_VALUES = 4


def validate_decimal_precision_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag sections that report percentages at inconsistent decimal precision.

    Within each section, when the same integer base (e.g., 85) appears both
    as ``85%`` and ``85.23%``, emits ``decimal-precision-inconsistency`` (minor).
    Requires ≥``_DECIMAL_MIN_VALUES`` percentage values per section.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        matches = _PERCENT_VALUE_RE.findall(section.body)
        if len(matches) < _DECIMAL_MIN_VALUES:
            continue

        int_parts: set[str] = set()
        dec_parts: set[str] = set()
        for int_part, frac_part in matches:
            if frac_part:
                dec_parts.add(int_part)
            else:
                int_parts.add(int_part)

        overlap = int_parts & dec_parts
        if overlap:
            example = min(overlap)
            findings.append(
                Finding(
                    code="decimal-precision-inconsistency",
                    severity="minor",
                    message=(
                        f"'{section.title}' reports the same percentage value "
                        f"(e.g. ~{example}%) with inconsistent decimal places — "
                        "standardise precision throughout."
                    ),
                    validator="decimal_precision_consistency",
                    location=f"section '{section.title}'",
                    evidence=[
                        f"integer ~{example}% appears with and without decimals"
                    ],
                )
            )
    return ValidationResult(
        validator_name="decimal_precision_consistency", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 70 – Future-work balance in Discussion/Conclusion
# ---------------------------------------------------------------------------

_FUTURE_WORK_RE = re.compile(
    r"\b(will\s+(?:explore|investigate|study|examine|extend|apply|test)|"
    r"future\s+work|future\s+(?:research|studies?|directions?)|"
    r"plan\s+to|intend\s+to|could\s+be\s+(?:extended|applied|improved|explored)|"
    r"should\s+be\s+(?:investigated|studied|explored|extended)|"
    r"leave[sd]?\s+(?:for|to)\s+future|promising\s+direction)\b",
    re.IGNORECASE,
)
_FUTURE_WORK_THRESHOLD = 0.40
_FUTURE_WORK_MIN_SENTENCES = 6
_DISCUSSION_SECTIONS_FW = frozenset(
    {
        "discussion",
        "conclusion",
        "conclusions",
        "concluding remarks",
        "summary and conclusions",
    }
)


def validate_future_work_balance(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Discussion/Conclusion sections dominated by future-work language.

    When >``_FUTURE_WORK_THRESHOLD`` of sentences contain future-work signals,
    emits ``future-work-heavy`` (minor).
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() not in _DISCUSSION_SECTIONS_FW:
            continue
        body = section.body.strip()
        if not body:
            continue
        sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(body) if s.strip()]
        if len(sentences) < _FUTURE_WORK_MIN_SENTENCES:
            continue
        fw_count = sum(1 for s in sentences if _FUTURE_WORK_RE.search(s))
        ratio = fw_count / len(sentences)
        if ratio > _FUTURE_WORK_THRESHOLD:
            findings.append(
                Finding(
                    code="future-work-heavy",
                    severity="minor",
                    message=(
                        f"'{section.title}' has {fw_count}/{len(sentences)} sentences "
                        f"({ratio:.0%}) focused on future work — "
                        "the section should primarily synthesise current findings."
                    ),
                    validator="future_work_balance",
                    location=f"section '{section.title}'",
                    evidence=[
                        f"future-work sentences: {fw_count}/{len(sentences)}"
                    ],
                )
            )
    return ValidationResult(validator_name="future_work_balance", findings=findings)


# ---------------------------------------------------------------------------
# Phase 71 – Null result acknowledgment
# ---------------------------------------------------------------------------

_NULL_RESULT_RE = re.compile(
    r"\b(did\s+not\s+(?:find|show|demonstrate|confirm|support|improve|achieve)|"
    r"fail(?:ed)?\s+to|no\s+significant|non[\s-]significant|null\s+hypothesis|"
    r"no\s+(?:significant\s+)?(?:effect|difference|improvement|benefit)|"
    r"not\s+statistically\s+significant|inconclusive|mixed\s+results?|"
    r"negative\s+result[s]?|no\s+evidence\s+of)\b",
    re.IGNORECASE,
)
_NULL_RESULT_SECTIONS = frozenset({"results", "discussion", "analysis", "evaluation"})
_NULL_RESULT_MIN_PARAGRAPHS = 4


def validate_null_result_acknowledgment(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers whose Results/Discussion contain no null/negative findings.

    Emits ``no-negative-results-acknowledged`` (minor) as a soft flag when
    the combined Results/Discussion body has ≥``_NULL_RESULT_MIN_PARAGRAPHS``
    paragraphs but no null-result language.  Only fires for empirical papers.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="null_result_acknowledgment", findings=[]
        )

    relevant_text = ""
    paragraph_count = 0
    for section in parsed.sections:
        if any(kw in section.title.lower() for kw in _NULL_RESULT_SECTIONS):
            relevant_text += " " + section.body
            paragraph_count += len(
                [p for p in section.body.split("\n\n") if p.strip()]
            )

    if not relevant_text.strip() or paragraph_count < _NULL_RESULT_MIN_PARAGRAPHS:
        return ValidationResult(
            validator_name="null_result_acknowledgment", findings=[]
        )

    if _NULL_RESULT_RE.search(relevant_text):
        return ValidationResult(
            validator_name="null_result_acknowledgment", findings=[]
        )

    return ValidationResult(
        validator_name="null_result_acknowledgment",
        findings=[
            Finding(
                code="no-negative-results-acknowledged",
                severity="minor",
                message=(
                    "Results/Discussion sections contain no acknowledgment of "
                    "negative, null, or mixed findings — consider whether all "
                    "outcomes are accurately represented."
                ),
                validator="null_result_acknowledgment",
                location="Results/Discussion",
                evidence=["no null/negative result language detected"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 73 – Hedging language density
# ---------------------------------------------------------------------------

_HEDGE_DENSITY_RE = re.compile(
    r"\b(might\s+(?:be|suggest|indicate|show|explain|help|support)|"
    r"could\s+(?:be|suggest|indicate|potentially|possibly)|"
    r"possibly|perhaps|may\s+suggest|may\s+indicate|tentatively|preliminary|"
    r"appears?\s+to|seems?\s+to|would\s+seem|arguably|"
    r"to\s+some\s+extent)\b",
    re.IGNORECASE,
)
_HEDGE_SECTIONS = frozenset(
    {"abstract", "introduction", "conclusion", "conclusions",
     "concluding remarks", "summary and conclusions"}
)
_HEDGE_COUNT_THRESHOLD = 4
_HEDGE_MIN_WORDS = 50


def validate_hedging_language(parsed: ParsedManuscript) -> ValidationResult:
    """Flag dense hedging language in abstract/introduction/conclusion sections.

    Emits ``hedging-language-dense`` (minor) when total hedging phrase count
    exceeds ``_HEDGE_COUNT_THRESHOLD`` in combined abstract + key section text
    (≥``_HEDGE_MIN_WORDS`` words required).
    """
    text_parts: list[str] = []
    if parsed.abstract:
        text_parts.append(parsed.abstract)
    for section in parsed.sections:
        if section.title.lower() in _HEDGE_SECTIONS:
            text_parts.append(section.body)

    combined = " ".join(text_parts)
    word_count = len(combined.split())
    if word_count < _HEDGE_MIN_WORDS:
        return ValidationResult(validator_name="hedging_language", findings=[])

    hits = _HEDGE_DENSITY_RE.findall(combined)
    if len(hits) <= _HEDGE_COUNT_THRESHOLD:
        return ValidationResult(validator_name="hedging_language", findings=[])

    return ValidationResult(
        validator_name="hedging_language",
        findings=[
            Finding(
                code="hedging-language-dense",
                severity="minor",
                message=(
                    f"Abstract and key sections contain {len(hits)} hedging phrases "
                    f"(e.g. '{hits[0]}') — strengthen claims or acknowledge "
                    "limitations more directly."
                ),
                validator="hedging_language",
                location="abstract/introduction/conclusion",
                evidence=[f"hedging count: {len(hits)}"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 74 – Duplicate content between sections
# ---------------------------------------------------------------------------

_DUP_MIN_SENTENCES = 3
_DUP_OVERLAP_THRESHOLD = 0.40
_DUP_MAX_FINDINGS = 3


def _sentence_tokens(text: str) -> list[frozenset[str]]:
    """Split text into sentences; return each as a frozenset of lowercased words."""
    return [
        frozenset(s.lower().split())
        for s in _SENTENCE_SPLIT_RE.split(text)
        if len(s.split()) >= 5
    ]


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def validate_duplicate_section_content(parsed: ParsedManuscript) -> ValidationResult:
    """Flag non-adjacent sections with high sentence-level Jaccard overlap.

    Uses original section order to determine adjacency.  Emits
    ``duplicate-section-content`` (minor) when sentence token overlap between
    any non-adjacent pair exceeds ``_DUP_OVERLAP_THRESHOLD``.
    Capped at ``_DUP_MAX_FINDINGS``.
    """
    # Keep original indices for adjacency checks
    indexed = [
        (orig_idx, s)
        for orig_idx, s in enumerate(parsed.sections)
        if s.title.lower() not in _SKIP_SECTIONS and len(s.body.split()) >= 40
    ]
    findings: list[Finding] = []

    for ii, (orig_i, sec_a) in enumerate(indexed):
        if len(findings) >= _DUP_MAX_FINDINGS:
            break
        sents_a = _sentence_tokens(sec_a.body)
        if len(sents_a) < _DUP_MIN_SENTENCES:
            continue
        for jj, (orig_j, sec_b) in enumerate(indexed):
            if jj <= ii:
                continue
            if orig_j <= orig_i + 1:  # adjacent in original order
                continue
            sents_b = _sentence_tokens(sec_b.body)
            if len(sents_b) < _DUP_MIN_SENTENCES:
                continue
            max_sim = max(
                (_jaccard(sa, sb) for sa in sents_a for sb in sents_b),
                default=0.0,
            )
            if max_sim >= _DUP_OVERLAP_THRESHOLD:
                findings.append(
                    Finding(
                        code="duplicate-section-content",
                        severity="minor",
                        message=(
                            f"Sections '{sec_a.title}' and '{sec_b.title}' share "
                            f"highly similar sentences ({max_sim:.0%} overlap) — "
                            "review for unintentional repetition."
                        ),
                        validator="duplicate_section_content",
                        location=(
                            f"sections '{sec_a.title}' and '{sec_b.title}'"
                        ),
                        evidence=[f"max sentence Jaccard: {max_sim:.2f}"],
                    )
                )
                if len(findings) >= _DUP_MAX_FINDINGS:
                    break

    return ValidationResult(
        validator_name="duplicate_section_content", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 76 – Methods section depth
# ---------------------------------------------------------------------------

_METHODS_SECTIONS = frozenset(
    {"methods", "method", "methodology", "materials and methods",
     "experimental setup", "experimental design", "study design"}
)
_METHODS_MIN_WORDS = 150


def validate_methods_depth(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag thin Methods sections (<``_METHODS_MIN_WORDS`` words) in empirical papers.

    Emits ``thin-methods`` (moderate) when the first Methods-like section is
    present but below the word count threshold.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="methods_depth", findings=[])

    for section in parsed.sections:
        if section.title.lower() in _METHODS_SECTIONS:
            word_count = len(section.body.split())
            if word_count < _METHODS_MIN_WORDS:
                return ValidationResult(
                    validator_name="methods_depth",
                    findings=[
                        Finding(
                            code="thin-methods",
                            severity="moderate",
                            message=(
                                f"Methods section '{section.title}' is only "
                                f"{word_count} words — provide sufficient detail "
                                "for reproducibility (target ≥150 words)."
                            ),
                            validator="methods_depth",
                            location=f"section '{section.title}'",
                            evidence=[f"word count: {word_count}"],
                        )
                    ],
                )
            break  # check first methods-like section only

    return ValidationResult(validator_name="methods_depth", findings=[])


# ---------------------------------------------------------------------------
# Phase 78 – List overuse in prose sections
# ---------------------------------------------------------------------------

_LIST_ITEM_RE = re.compile(r"^(?:\s*[-*\u2022]\s|\s*\d+[.)]\s)", re.MULTILINE)
_LIST_OVERUSE_SECTIONS = frozenset(
    {"introduction", "discussion", "conclusion", "conclusions",
     "concluding remarks", "summary and conclusions"}
)
_LIST_OVERUSE_THRESHOLD = 0.50
_LIST_OVERUSE_MIN_ITEMS = 6


def validate_list_overuse(parsed: ParsedManuscript) -> ValidationResult:
    """Flag prose sections with excessive list item content.

    Emits ``list-heavy-section`` (minor) when >``_LIST_OVERUSE_THRESHOLD`` of
    body lines in an Introduction/Discussion/Conclusion section are list items
    and there are ≥``_LIST_OVERUSE_MIN_ITEMS`` items.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() not in _LIST_OVERUSE_SECTIONS:
            continue
        body = section.body.strip()
        lines = [ln for ln in body.splitlines() if ln.strip()]
        if not lines:
            continue
        list_lines = _LIST_ITEM_RE.findall(body)
        if len(list_lines) < _LIST_OVERUSE_MIN_ITEMS:
            continue
        ratio = len(list_lines) / len(lines)
        if ratio > _LIST_OVERUSE_THRESHOLD:
            findings.append(
                Finding(
                    code="list-heavy-section",
                    severity="minor",
                    message=(
                        f"'{section.title}' is {ratio:.0%} list items "
                        f"({len(list_lines)} items) — prose sections should use "
                        "paragraphs rather than lists to develop arguments."
                    ),
                    validator="list_overuse",
                    location=f"section '{section.title}'",
                    evidence=[
                        f"list items: {len(list_lines)}/{len(lines)} lines"
                    ],
                )
            )
    return ValidationResult(validator_name="list_overuse", findings=findings)


# ---------------------------------------------------------------------------
# Phase 79 – Section length balance
# ---------------------------------------------------------------------------

_SECTION_BALANCE_THRESHOLD = 0.60


def validate_section_balance(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag when a single section dominates total body word count.

    Emits ``section-length-imbalance`` (minor) when any section exceeds
    ``_SECTION_BALANCE_THRESHOLD`` of total word count.  Requires ≥3
    non-skipped sections and empirical/applied/software paper type.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="section_balance", findings=[])

    major_sections = [
        s for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    ]
    if len(major_sections) < 3:
        return ValidationResult(validator_name="section_balance", findings=[])

    section_words = [(s.title, len(s.body.split())) for s in major_sections]
    total_words = sum(w for _, w in section_words)
    if total_words == 0:
        return ValidationResult(validator_name="section_balance", findings=[])

    for title, count in section_words:
        ratio = count / total_words
        if ratio > _SECTION_BALANCE_THRESHOLD:
            return ValidationResult(
                validator_name="section_balance",
                findings=[
                    Finding(
                        code="section-length-imbalance",
                        severity="minor",
                        message=(
                            f"Section '{title}' accounts for {ratio:.0%} of total "
                            "body word count — consider redistributing content "
                            "for a more balanced structure."
                        ),
                        validator="section_balance",
                        location=f"section '{title}'",
                        evidence=[f"{count}/{total_words} words ({ratio:.0%})"],
                    )
                ],
            )

    return ValidationResult(validator_name="section_balance", findings=[])


# ---------------------------------------------------------------------------
# Phase 81 – Related work recency
# ---------------------------------------------------------------------------

_RELATED_WORK_TITLES = frozenset(
    {"related work", "related works", "background", "prior work",
     "literature review", "previous work", "related studies", "survey"}
)
_YEAR_IN_BIB_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_RELATED_WORK_MIN_CITATIONS = 5
_RELATED_WORK_STALE_THRESHOLD = 0.50
_RELATED_WORK_STALE_YEARS = 8


def validate_related_work_recency(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Related Work sections dominated by outdated citations.

    Finds citations in the Related Work section body, extracts years, and
    flags ``related-work-stale`` (minor) when >``_RELATED_WORK_STALE_THRESHOLD``
    of dated citations are >``_RELATED_WORK_STALE_YEARS`` years old.

    Requires ≥``_RELATED_WORK_MIN_CITATIONS`` dated citations and
    empirical/applied paper type.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="related_work_recency", findings=[])

    current_year = datetime.datetime.now().year

    for section in parsed.sections:
        if section.title.lower() not in _RELATED_WORK_TITLES:
            continue
        years = [int(y) for y in _YEAR_IN_BIB_RE.findall(section.body)]
        if len(years) < _RELATED_WORK_MIN_CITATIONS:
            continue
        stale = sum(1 for y in years if current_year - y > _RELATED_WORK_STALE_YEARS)
        ratio = stale / len(years)
        if ratio > _RELATED_WORK_STALE_THRESHOLD:
            return ValidationResult(
                validator_name="related_work_recency",
                findings=[
                    Finding(
                        code="related-work-stale",
                        severity="minor",
                        message=(
                            f"Related Work section has {stale}/{len(years)} citations "
                            f"({ratio:.0%}) older than {_RELATED_WORK_STALE_YEARS} years — "
                            "update the survey to include recent work."
                        ),
                        validator="related_work_recency",
                        location=f"section '{section.title}'",
                        evidence=[f"stale citations: {stale}/{len(years)}"],
                    )
                ],
            )

    return ValidationResult(validator_name="related_work_recency", findings=[])


# ---------------------------------------------------------------------------
# Phase 82 – Introduction length balance
# ---------------------------------------------------------------------------

_INTRO_SECTIONS = frozenset({"introduction", "intro"})
_INTRO_LENGTH_THRESHOLD = 0.25
_INTRO_MIN_SECTIONS = 4
_INTRO_MIN_TOTAL_WORDS = 300


def validate_introduction_length(parsed: ParsedManuscript) -> ValidationResult:
    """Flag overlong introduction sections.

    Emits ``introduction-too-long`` (minor) when the Introduction exceeds
    ``_INTRO_LENGTH_THRESHOLD`` of total body word count.  Requires ≥
    ``_INTRO_MIN_SECTIONS`` non-skipped sections.
    """
    major_sections = [
        s for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    ]
    if len(major_sections) < _INTRO_MIN_SECTIONS:
        return ValidationResult(validator_name="introduction_length", findings=[])

    total_words = sum(len(s.body.split()) for s in major_sections)
    if total_words < _INTRO_MIN_TOTAL_WORDS:
        return ValidationResult(validator_name="introduction_length", findings=[])

    for section in major_sections:
        if section.title.lower() not in _INTRO_SECTIONS:
            continue
        intro_words = len(section.body.split())
        ratio = intro_words / total_words
        if ratio > _INTRO_LENGTH_THRESHOLD:
            return ValidationResult(
                validator_name="introduction_length",
                findings=[
                    Finding(
                        code="introduction-too-long",
                        severity="minor",
                        message=(
                            f"Introduction accounts for {ratio:.0%} of body word count "
                            f"({intro_words}/{total_words} words) — "
                            "trim to improve balance with Methods and Results."
                        ),
                        validator="introduction_length",
                        location="Introduction",
                        evidence=[f"{intro_words}/{total_words} words ({ratio:.0%})"],
                    )
                ],
            )

    return ValidationResult(validator_name="introduction_length", findings=[])


# ---------------------------------------------------------------------------
# Phase 83 – Unquantified comparative claims
# ---------------------------------------------------------------------------

_UNQUANTIFIED_CLAIM_RE = re.compile(
    r"\b(significantly\s+(?:better|faster|improved|higher|lower|greater|worse)|"
    r"much\s+(?:better|faster|higher|lower|greater|worse|more\s+\w+)|"
    r"greatly\s+(?:improved|increased|reduced|enhanced|better)|"
    r"considerably\s+(?:better|faster|higher|lower|greater)|"
    r"substantially\s+(?:better|faster|higher|lower|greater|improved)|"
    r"far\s+(?:better|faster|higher|lower|greater)|"
    r"remarkably\s+(?:better|faster|higher|lower|improved))\b",
    re.IGNORECASE,
)
_NUMERIC_NEARBY_RE = re.compile(r"\d")
_UNQUANTIFIED_NEARBY_CHARS = 40
_UNQUANTIFIED_MAX_FINDINGS = 4


def validate_unquantified_comparisons(parsed: ParsedManuscript) -> ValidationResult:
    """Flag comparative claims not backed by nearby numeric evidence.

    For each match of ``_UNQUANTIFIED_CLAIM_RE``, checks if a digit appears
    within ``_UNQUANTIFIED_NEARBY_CHARS`` characters before or after the match.
    Emits ``unquantified-comparison`` (minor) per unsupported match; capped at
    ``_UNQUANTIFIED_MAX_FINDINGS``.
    """
    findings: list[Finding] = []
    body_text = " ".join(
        s.body for s in parsed.sections if s.title.lower() not in _SKIP_SECTIONS
    )

    for match in _UNQUANTIFIED_CLAIM_RE.finditer(body_text):
        start = max(0, match.start() - _UNQUANTIFIED_NEARBY_CHARS)
        end = min(len(body_text), match.end() + _UNQUANTIFIED_NEARBY_CHARS)
        context = body_text[start:end]
        if not _NUMERIC_NEARBY_RE.search(context):
            findings.append(
                Finding(
                    code="unquantified-comparison",
                    severity="minor",
                    message=(
                        f"Comparative claim '{match.group()}' is not supported by "
                        "nearby numeric evidence — add a specific measurement."
                    ),
                    validator="unquantified_comparisons",
                    location="manuscript body",
                    evidence=[f"claim: '{match.group()}'"],
                )
            )
            if len(findings) >= _UNQUANTIFIED_MAX_FINDINGS:
                break

    return ValidationResult(
        validator_name="unquantified_comparisons", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 84 – Footnote overuse
# ---------------------------------------------------------------------------

_FOOTNOTE_RE = re.compile(
    r"(?:\\\s*footnote\s*\{|^\s*\[\^\d+\]:)",
    re.MULTILINE,
)
_FOOTNOTE_THRESHOLD = 8


def validate_footnote_overuse(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts with an excessive number of footnotes.

    Detects LaTeX ``\\footnote{`` and Markdown ``[^N]:`` patterns.
    Emits ``footnote-heavy`` (minor) when count exceeds
    ``_FOOTNOTE_THRESHOLD``.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    count = len(_FOOTNOTE_RE.findall(full))
    if count <= _FOOTNOTE_THRESHOLD:
        return ValidationResult(validator_name="footnote_overuse", findings=[])

    return ValidationResult(
        validator_name="footnote_overuse",
        findings=[
            Finding(
                code="footnote-heavy",
                severity="minor",
                message=(
                    f"Manuscript contains {count} footnotes — "
                    "excessive footnotes can interrupt reading flow; "
                    "integrate key content into the main text."
                ),
                validator="footnote_overuse",
                location="manuscript body",
                evidence=[f"footnote count: {count}"],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 85 – Abbreviation list consistency
# ---------------------------------------------------------------------------

_ABBREV_SECTION_RE = re.compile(
    r"\b(abbreviation[s]?|list\s+of\s+abbreviation[s]?|acronym[s]?)\b",
    re.IGNORECASE,
)
_ABBREV_ENTRY_RE = re.compile(
    r"^\s*([A-Z]{2,8})\s*[:\-–—]\s*.+$",
    re.MULTILINE,
)
_ABBREV_MAX_FINDINGS = 5


def validate_abbreviation_list(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abbreviations declared in an abbreviations section but absent from body.

    When a dedicated abbreviations section exists, extracts each abbreviation
    and checks it appears in the manuscript body.  Emits ``unused-abbreviation``
    (minor) per entry not found; capped at ``_ABBREV_MAX_FINDINGS``.
    """
    abbrev_section = None
    for section in parsed.sections:
        if _ABBREV_SECTION_RE.search(section.title):
            abbrev_section = section
            break

    if abbrev_section is None:
        return ValidationResult(validator_name="abbreviation_list", findings=[])

    body_text = " ".join(
        s.body for s in parsed.sections
        if s.title.lower() not in _SKIP_SECTIONS
        and not _ABBREV_SECTION_RE.search(s.title)
    )

    abbrevs = _ABBREV_ENTRY_RE.findall(abbrev_section.body)
    if not abbrevs:
        return ValidationResult(validator_name="abbreviation_list", findings=[])

    findings: list[Finding] = []
    for abbrev in abbrevs:
        if abbrev not in body_text:
            findings.append(
                Finding(
                    code="unused-abbreviation",
                    severity="minor",
                    message=(
                        f"Abbreviation '{abbrev}' is declared in the abbreviations "
                        "list but not found in the manuscript body."
                    ),
                    validator="abbreviation_list",
                    location=f"section '{abbrev_section.title}'",
                    evidence=[f"abbreviation: {abbrev}"],
                )
            )
            if len(findings) >= _ABBREV_MAX_FINDINGS:
                break

    return ValidationResult(
        validator_name="abbreviation_list", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 86 – Abstract tense consistency
# ---------------------------------------------------------------------------

_ABSTRACT_PAST_RE = re.compile(
    r"\b(was|were|found|showed|demonstrated|observed|revealed|confirmed|"
    r"indicated|reported|measured|collected|conducted|performed|achieved|"
    r"obtained|compared|evaluated)\b",
    re.IGNORECASE,
)
_ABSTRACT_PRESENT_RE = re.compile(
    r"\b(is|are|show[s]?|demonstrate[s]?|suggest[s]?|indicate[s]?|"
    r"provide[s]?|offer[s]?|present[s]?|address[es]?|address|introduce[s]?)\b",
    re.IGNORECASE,
)
_ABSTRACT_TENSE_MIN_SENTENCES = 5
_ABSTRACT_TENSE_THRESHOLD = 0.20


def validate_abstract_tense(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abstracts that mix past-results and present-claims tenses inconsistently.

    When both past-tense (was, found, showed) and present-tense (is, shows)
    signals each exceed ``_ABSTRACT_TENSE_THRESHOLD`` of abstract sentences,
    emits ``abstract-tense-mixed`` (minor).  Requires ≥
    ``_ABSTRACT_TENSE_MIN_SENTENCES`` sentences.
    """
    abstract = parsed.abstract.strip()
    if not abstract:
        return ValidationResult(validator_name="abstract_tense", findings=[])

    sentences = [s.strip() for s in _SENTENCE_SPLIT_RE.split(abstract) if s.strip()]
    if len(sentences) < _ABSTRACT_TENSE_MIN_SENTENCES:
        return ValidationResult(validator_name="abstract_tense", findings=[])

    past_count = sum(1 for s in sentences if _ABSTRACT_PAST_RE.search(s))
    present_count = sum(1 for s in sentences if _ABSTRACT_PRESENT_RE.search(s))
    n = len(sentences)

    if (
        past_count / n > _ABSTRACT_TENSE_THRESHOLD
        and present_count / n > _ABSTRACT_TENSE_THRESHOLD
    ):
        return ValidationResult(
            validator_name="abstract_tense",
            findings=[
                Finding(
                    code="abstract-tense-mixed",
                    severity="minor",
                    message=(
                        f"Abstract mixes past tense ({past_count}/{n} sentences) and "
                        f"present tense ({present_count}/{n} sentences) — "
                        "maintain consistent tense (past for reported results, "
                        "present for general claims)."
                    ),
                    validator="abstract_tense",
                    location="abstract",
                    evidence=[
                        f"past: {past_count}/{n}; present: {present_count}/{n}"
                    ],
                )
            ],
        )

    return ValidationResult(validator_name="abstract_tense", findings=[])


# ---------------------------------------------------------------------------
# Phase 87 – Claim strength escalation
# ---------------------------------------------------------------------------

_STRONG_CLAIM_RE = re.compile(
    r"\b(proves?|demonstrates? conclusively|definitively shows?|"
    r"irrefutably|beyond (any )?doubt|conclusive(ly)?|"
    r"unambiguously (shows?|demonstrates?|proves?))\b",
    re.IGNORECASE,
)
_CLAIM_STRENGTH_MIN_WORDS = 20


def validate_claim_strength_escalation(parsed: ParsedManuscript) -> ValidationResult:
    """Flag overstrong claim language in body sections.

    Emits ``overstrong-claim`` (major) when body sections use language like
    "proves", "demonstrates conclusively", or "definitively shows" which
    overclaims certainty unsupported by standard statistical results.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() in _SKIP_SECTIONS:
            continue
        body = section.body
        if len(body.split()) < _CLAIM_STRENGTH_MIN_WORDS:
            continue
        match = _STRONG_CLAIM_RE.search(body)
        if match:
            findings.append(
                Finding(
                    code="overstrong-claim",
                    severity="major",
                    message=(
                        f"Section '{section.title}' uses overstrong claim language "
                        f"('{match.group()}') — statistical results should not be "
                        "presented as definitive proof."
                    ),
                    validator="claim_strength_escalation",
                    location=section.title,
                    evidence=[match.group()],
                )
            )
    return ValidationResult(
        validator_name="claim_strength_escalation", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 88 – Sample size reporting
# ---------------------------------------------------------------------------

_SAMPLE_SIZE_RE = re.compile(
    r"\b[Nn]\s*=\s*\d+|"
    r"\b(sample size|n\s*=\s*\d+|participants?|subjects?|respondents?)\b.*\d+|"
    r"\b\d+\s+(participants?|subjects?|respondents?|patients?|observations?)\b",
    re.IGNORECASE,
)
_SAMPLE_SIZE_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "survey_study",
    }
)


def validate_sample_size_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that lack explicit sample-size reporting.

    Emits ``missing-sample-size`` (moderate) when an empirical paper has no
    explicit N= or sample-size statement in Methods or Results sections.
    """
    if classification.paper_type not in _SAMPLE_SIZE_PAPER_TYPES:
        return ValidationResult(validator_name="sample_size_reporting", findings=[])

    methods_results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "results", "participants"}
    )
    if not methods_results_body:
        return ValidationResult(validator_name="sample_size_reporting", findings=[])

    if _SAMPLE_SIZE_RE.search(methods_results_body):
        return ValidationResult(validator_name="sample_size_reporting", findings=[])

    return ValidationResult(
        validator_name="sample_size_reporting",
        findings=[
            Finding(
                code="missing-sample-size",
                severity="moderate",
                message=(
                    "Empirical manuscript does not report an explicit sample size "
                    "(N=...) in Methods or Results — required for reproducibility."
                ),
                validator="sample_size_reporting",
                location="Methods/Results",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 89 – Limitations section presence
# ---------------------------------------------------------------------------

_LIMITATIONS_TITLES = frozenset(
    {"limitations", "limitation", "study limitations", "limitations and future work"}
)
_LIMITATIONS_INLINE_RE = re.compile(
    r"\b(limitation|caveat|weakness|shortcoming|constraint)s?\b",
    re.IGNORECASE,
)
_LIMITATIONS_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "survey_study",
        "systematic_review",
        "meta_analysis",
    }
)


def validate_limitations_section_presence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without a Limitations section or inline discussion.

    Emits ``missing-limitations-section`` (moderate) when an empirical paper
    has no dedicated Limitations section AND no inline limitations discussion
    in Discussion/Conclusion.
    """
    if classification.paper_type not in _LIMITATIONS_PAPER_TYPES:
        return ValidationResult(
            validator_name="limitations_section_presence", findings=[]
        )

    has_dedicated = any(
        s.title.lower() in _LIMITATIONS_TITLES for s in parsed.sections
    )
    if has_dedicated:
        return ValidationResult(
            validator_name="limitations_section_presence", findings=[]
        )

    discussion_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"discussion", "conclusion", "conclusions"}
    )
    if _LIMITATIONS_INLINE_RE.search(discussion_body):
        return ValidationResult(
            validator_name="limitations_section_presence", findings=[]
        )

    return ValidationResult(
        validator_name="limitations_section_presence",
        findings=[
            Finding(
                code="missing-limitations-section",
                severity="moderate",
                message=(
                    "Empirical manuscript has no Limitations section and no inline "
                    "limitations discussion in Discussion/Conclusion."
                ),
                validator="limitations_section_presence",
                location="Discussion/Conclusion",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 90 – Author contribution statement
# ---------------------------------------------------------------------------

_CONTRIB_SECTION_RE = re.compile(
    r"\b(author contributions?|contributions?|credit|CRediT)\b",
    re.IGNORECASE,
)
_CONTRIB_KEYWORD_RE = re.compile(
    r"\b(conceptuali[sz]ation|methodology|software|validation|formal analysis|"
    r"investigation|resources|data curation|writing|visualization|supervision|"
    r"funding acquisition|contributed equally)\b",
    re.IGNORECASE,
)


def validate_author_contribution_statement(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts that lack an author contribution statement.

    Emits ``missing-author-contributions`` (minor) when no section or paragraph
    contains CRediT-style author contribution language.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _CONTRIB_SECTION_RE.search(combined) and _CONTRIB_KEYWORD_RE.search(combined):
        return ValidationResult(
            validator_name="author_contribution_statement", findings=[]
        )

    for section in parsed.sections:
        if _CONTRIB_SECTION_RE.search(section.title):
            return ValidationResult(
                validator_name="author_contribution_statement", findings=[]
            )

    return ValidationResult(
        validator_name="author_contribution_statement",
        findings=[
            Finding(
                code="missing-author-contributions",
                severity="minor",
                message=(
                    "No author contribution statement (CRediT or equivalent) detected. "
                    "Most journals require explicit per-author contribution disclosure."
                ),
                validator="author_contribution_statement",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 91 – Preregistration mention
# ---------------------------------------------------------------------------

_PREREG_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "registered_report",
    }
)
_PREREG_RE = re.compile(
    r"\b(preregistered?|pre-registered?|registered report|"
    r"osf\.io|clinicaltrials\.gov|prospero|registration number|"
    r"study protocol|protocol registration)\b",
    re.IGNORECASE,
)
_RCT_RE = re.compile(
    r"\b(randomized controlled trial|RCT|randomly assigned|random assignment|"
    r"double.blind|placebo.controlled)\b",
    re.IGNORECASE,
)


def validate_preregistration_mention(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCTs and registered-report types without a preregistration mention.

    Emits ``missing-preregistration`` (moderate) when a clinical trial or
    registered report lacks any preregistration or registry reference.
    """
    if classification.paper_type not in _PREREG_PAPER_TYPES:
        return ValidationResult(
            validator_name="preregistration_mention", findings=[]
        )

    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    is_rct = classification.paper_type == "clinical_trial_report" or bool(
        _RCT_RE.search(combined)
    )
    if not is_rct:
        return ValidationResult(
            validator_name="preregistration_mention", findings=[]
        )

    if _PREREG_RE.search(combined):
        return ValidationResult(
            validator_name="preregistration_mention", findings=[]
        )

    return ValidationResult(
        validator_name="preregistration_mention",
        findings=[
            Finding(
                code="missing-preregistration",
                severity="moderate",
                message=(
                    "Clinical trial or RCT manuscript does not mention a "
                    "preregistration or study registry. Reporting guidelines "
                    "(CONSORT, PROSPERO) require registry citation."
                ),
                validator="preregistration_mention",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 92 – Reviewer response completeness
# ---------------------------------------------------------------------------

_REVIEWER_RESPONSE_RE = re.compile(
    r"\b(response to reviewer|reviewer comment|point.by.point|"
    r"we thank the reviewer|as the reviewer noted|"
    r"reviewer \d+|comment \d+)\b",
    re.IGNORECASE,
)
_REVISION_TITLE_RE = re.compile(
    r"\b(revised?|revision|resubmission|response|rebuttal)\b",
    re.IGNORECASE,
)


def validate_reviewer_response_completeness(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag revision manuscripts that appear to lack a reviewer response.

    Emits ``missing-reviewer-response`` (minor) when the title or abstract
    signals a revision/resubmission but no reviewer-response language is found
    anywhere in the manuscript.

    Note: this fires only when the title or abstract explicitly marks the
    document as a revision, so it targets cover letters or response documents
    mistakenly submitted as the manuscript body.
    """
    title_is_revision = _REVISION_TITLE_RE.search(parsed.title or "")
    abstract_is_revision = _REVISION_TITLE_RE.search(parsed.abstract or "")
    if not (title_is_revision or abstract_is_revision):
        return ValidationResult(
            validator_name="reviewer_response_completeness", findings=[]
        )

    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _REVIEWER_RESPONSE_RE.search(combined):
        return ValidationResult(
            validator_name="reviewer_response_completeness", findings=[]
        )

    return ValidationResult(
        validator_name="reviewer_response_completeness",
        findings=[
            Finding(
                code="missing-reviewer-response",
                severity="minor",
                message=(
                    "Manuscript title or abstract signals a revision/resubmission "
                    "but no reviewer-response language was found. Confirm that a "
                    "point-by-point response letter is included separately."
                ),
                validator="reviewer_response_completeness",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 93 – Novelty overclaiming
# ---------------------------------------------------------------------------

_NOVELTY_CLAIM_RE = re.compile(
    r"\b(first ever|first time|first (to |in )(show|demonstrate|present|propose|apply)|"
    r"never (been|before)|unprecedented|ground.breaking|pioneering)\b",
    re.IGNORECASE,
)
_NOVELTY_CONTRAST_RE = re.compile(
    r"\b(prior (work|studies|approaches)|previous (methods|work|studies)|"
    r"existing (methods|approaches|literature)|unlike (previous|prior)|"
    r"compared (to|with) (prior|previous|existing))\b",
    re.IGNORECASE,
)


def validate_novelty_overclaim(parsed: ParsedManuscript) -> ValidationResult:
    """Flag novelty overclaiming without contrasting prior work.

    Emits ``novelty-overclaim`` (major) when the manuscript claims to be the
    "first ever" or "unprecedented" without any citation-backed contrast
    against prior work.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not _NOVELTY_CLAIM_RE.search(combined):
        return ValidationResult(validator_name="novelty_overclaim", findings=[])
    if _NOVELTY_CONTRAST_RE.search(combined):
        return ValidationResult(validator_name="novelty_overclaim", findings=[])

    match = _NOVELTY_CLAIM_RE.search(combined)
    return ValidationResult(
        validator_name="novelty_overclaim",
        findings=[
            Finding(
                code="novelty-overclaim",
                severity="major",
                message=(
                    f"Manuscript claims novelty ('{match.group() if match else ''}') "
                    "without contrasting prior work. Contextualize the contribution "
                    "against existing approaches."
                ),
                validator="novelty_overclaim",
                location="manuscript",
                evidence=[match.group() if match else ""],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 94 – Figure/table minimum for empirical papers
# ---------------------------------------------------------------------------

_FIG_TABLE_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "survey_study",
        "systematic_review",
        "meta_analysis",
    }
)
_FIG_TABLE_RE = re.compile(
    r"\b(Figure|Fig\.|Table|Supplementary (Figure|Table))\s*\d+\b",
    re.IGNORECASE,
)


def validate_figure_table_minimum(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers with no figures or tables.

    Emits ``no-figures-or-tables`` (moderate) when an empirical paper references
    no figures or tables anywhere in the body.
    """
    if classification.paper_type not in _FIG_TABLE_PAPER_TYPES:
        return ValidationResult(
            validator_name="figure_table_minimum", findings=[]
        )

    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _FIG_TABLE_RE.search(combined):
        return ValidationResult(
            validator_name="figure_table_minimum", findings=[]
        )

    return ValidationResult(
        validator_name="figure_table_minimum",
        findings=[
            Finding(
                code="no-figures-or-tables",
                severity="moderate",
                message=(
                    "Empirical manuscript contains no figure or table references. "
                    "Results should be supported by visual summaries."
                ),
                validator="figure_table_minimum",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 95 – Multiple comparisons correction
# ---------------------------------------------------------------------------

_MULTIPLE_TEST_RE = re.compile(
    r"\b(multiple (comparison|test|hypothesis|outcome|endpoint)s?|"
    r"several (statistical )?tests?|"
    r"family.wise|familywise|FWER)\b",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"\b(Bonferroni|Holm|Benjamini.Hochberg|BH correction|FDR|"
    r"false discovery rate|adjusted p.value|correction for multiple|"
    r"multiple.comparison correction|alpha correction|"
    r"p.value adjustment)\b",
    re.IGNORECASE,
)
_MULTI_TEST_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "survey_study",
    }
)


def validate_multiple_comparisons_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers reporting multiple tests without correction mention.

    Emits ``missing-multiple-comparisons-correction`` (moderate) when the
    Methods/Results sections mention multiple statistical tests but contain no
    multiple-comparison correction language.
    """
    if classification.paper_type not in _MULTI_TEST_PAPER_TYPES:
        return ValidationResult(
            validator_name="multiple_comparisons_correction", findings=[]
        )

    combined = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "results", "statistics"}
    )
    if not combined:
        return ValidationResult(
            validator_name="multiple_comparisons_correction", findings=[]
        )

    if not _MULTIPLE_TEST_RE.search(combined):
        return ValidationResult(
            validator_name="multiple_comparisons_correction", findings=[]
        )

    if _CORRECTION_RE.search(combined):
        return ValidationResult(
            validator_name="multiple_comparisons_correction", findings=[]
        )

    return ValidationResult(
        validator_name="multiple_comparisons_correction",
        findings=[
            Finding(
                code="missing-multiple-comparisons-correction",
                severity="moderate",
                message=(
                    "Manuscript reports multiple statistical tests without mentioning "
                    "a multiple-comparison correction (e.g., Bonferroni, FDR). "
                    "Type I error inflation should be addressed."
                ),
                validator="multiple_comparisons_correction",
                location="Methods/Results",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 96 – Supplementary material mention without indication
# ---------------------------------------------------------------------------

_SUPPL_REF_RE = re.compile(
    r"\b(supplementary|supplemental)\s+(material|data|figure|table|file|appendix)s?\b",
    re.IGNORECASE,
)
_SUPPL_AVAIL_RE = re.compile(
    r"\b(supplementary (material|data|files?) (is|are) available|"
    r"see online supplementary|available (online|at|via)|"
    r"Supplementary (Material|Data) S\d+)\b",
    re.IGNORECASE,
)


def validate_supplementary_material_indication(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts that reference supplementary material without availability info.

    Emits ``unindicated-supplementary-material`` (minor) when body text
    references supplementary data/figures/tables but provides no availability
    statement.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not _SUPPL_REF_RE.search(combined):
        return ValidationResult(
            validator_name="supplementary_material_indication", findings=[]
        )
    if _SUPPL_AVAIL_RE.search(combined):
        return ValidationResult(
            validator_name="supplementary_material_indication", findings=[]
        )

    return ValidationResult(
        validator_name="supplementary_material_indication",
        findings=[
            Finding(
                code="unindicated-supplementary-material",
                severity="minor",
                message=(
                    "Manuscript references supplementary material but provides no "
                    "availability statement. Add a data/supplementary availability note."
                ),
                validator="supplementary_material_indication",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 97 – Conclusion scope creep
# ---------------------------------------------------------------------------

_CONCLUSION_TITLES = frozenset(
    {"conclusion", "conclusions", "concluding remarks", "summary and conclusions"}
)
_CONCLUSION_NEW_CLAIM_RE = re.compile(
    r"\b(furthermore|additionally|in addition|also|moreover|"
    r"we (also|additionally|furthermore) (show|demonstrate|find|found|showed))\b",
    re.IGNORECASE,
)
_CONCLUSION_MIN_WORDS = 30


def validate_conclusion_scope_creep(parsed: ParsedManuscript) -> ValidationResult:
    """Flag conclusion sections that may introduce new claims.

    Emits ``conclusion-scope-creep`` (minor) when the Conclusion section
    contains language patterns that typically signal new claims being
    introduced (rather than summarizing established findings).
    Requires ≥ ``_CONCLUSION_MIN_WORDS`` words in the conclusion body.
    """
    conclusions = [
        s
        for s in parsed.sections
        if s.title.lower() in _CONCLUSION_TITLES
    ]
    findings: list[Finding] = []
    for section in conclusions:
        body = section.body
        if len(body.split()) < _CONCLUSION_MIN_WORDS:
            continue
        if _CONCLUSION_NEW_CLAIM_RE.search(body):
            match = _CONCLUSION_NEW_CLAIM_RE.search(body)
            findings.append(
                Finding(
                    code="conclusion-scope-creep",
                    severity="minor",
                    message=(
                        "Conclusion section may introduce new claims or analysis "
                        f"('{match.group() if match else ''}') — conclusions should "
                        "summarize established findings only."
                    ),
                    validator="conclusion_scope_creep",
                    location=section.title,
                    evidence=[match.group() if match else ""],
                )
            )
    return ValidationResult(
        validator_name="conclusion_scope_creep", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 98 – Discussion-Results alignment
# ---------------------------------------------------------------------------

_RESULTS_QUANTITATIVE_RE = re.compile(r"\b\d+(\.\d+)?(%|fold|×|x)\b|\bp\s*[<=>]\s*\d")
_DISCUSSION_INTERPRETS_RE = re.compile(
    r"\b(these results|our findings|the results|this suggests|we interpret|"
    r"this indicates|the data suggest|these findings)\b",
    re.IGNORECASE,
)


def validate_discussion_results_alignment(parsed: ParsedManuscript) -> ValidationResult:
    """Flag Discussion sections that lack any reference back to Results.

    Emits ``discussion-lacks-results-reference`` (moderate) when a Discussion
    section is present but contains no interpretive references to results.
    Requires ≥50 words in the Discussion.
    """
    discussion_sections = [
        s for s in parsed.sections if s.title.lower() == "discussion"
    ]
    findings: list[Finding] = []
    for section in discussion_sections:
        body = section.body
        if len(body.split()) < 50:
            continue
        if not _DISCUSSION_INTERPRETS_RE.search(body):
            findings.append(
                Finding(
                    code="discussion-lacks-results-reference",
                    severity="moderate",
                    message=(
                        "Discussion section does not appear to reference or interpret "
                        "the Results — ensure key findings are addressed explicitly."
                    ),
                    validator="discussion_results_alignment",
                    location=section.title,
                )
            )
    return ValidationResult(
        validator_name="discussion_results_alignment", findings=findings
    )


# ---------------------------------------------------------------------------
# Phase 99 – Open access / data sharing statement
# ---------------------------------------------------------------------------

_DATA_SHARING_RE = re.compile(
    r"\b(data (availability|sharing|access) statement|"
    r"open (access|data)|openly available|"
    r"data are (available|deposited|archived)|"
    r"code (is|are) available|github\.com|zenodo|osf\.io|figshare|dryad)\b",
    re.IGNORECASE,
)
_DATA_SHARING_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "survey_study",
        "meta_analysis",
    }
)


def validate_open_data_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without an open-data or data-sharing statement.

    Emits ``missing-open-data-statement`` (minor) when an empirical paper
    contains no data availability statement or repository link.
    """
    if classification.paper_type not in _DATA_SHARING_PAPER_TYPES:
        return ValidationResult(validator_name="open_data_statement", findings=[])

    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _DATA_SHARING_RE.search(combined):
        return ValidationResult(validator_name="open_data_statement", findings=[])

    return ValidationResult(
        validator_name="open_data_statement",
        findings=[
            Finding(
                code="missing-open-data-statement",
                severity="minor",
                message=(
                    "Empirical manuscript lacks a data availability or data-sharing "
                    "statement. Most journals require a statement even when data "
                    "are not publicly shared."
                ),
                validator="open_data_statement",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 100 – Redundant phrase detection
# ---------------------------------------------------------------------------

_REDUNDANT_PHRASES = [
    "due to the fact that",
    "in order to",
    "it is important to note that",
    "it should be noted that",
    "at this point in time",
    "in the event that",
    "for the purpose of",
    "in spite of the fact that",
    "with regard to",
    "in the near future",
    "a large number of",
    "the question as to whether",
    "whether or not",
    "on a daily basis",
    "in close proximity to",
]
_REDUNDANT_PHRASE_RE = re.compile(
    "|".join(re.escape(p) for p in _REDUNDANT_PHRASES),
    re.IGNORECASE,
)
_REDUNDANT_PHRASE_THRESHOLD = 3


def validate_redundant_phrases(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts with excessive use of redundant verbose phrases.

    Emits ``redundant-phrases`` (minor) when ≥ ``_REDUNDANT_PHRASE_THRESHOLD``
    redundant phrases are detected in the manuscript body.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    matches = _REDUNDANT_PHRASE_RE.findall(combined)
    if len(matches) < _REDUNDANT_PHRASE_THRESHOLD:
        return ValidationResult(validator_name="redundant_phrases", findings=[])

    unique = list({m.lower() for m in matches})[:5]
    return ValidationResult(
        validator_name="redundant_phrases",
        findings=[
            Finding(
                code="redundant-phrases",
                severity="minor",
                message=(
                    f"Manuscript contains {len(matches)} redundant verbose phrases "
                    "(e.g., 'in order to', 'due to the fact that'). "
                    "Replace with concise equivalents for clarity."
                ),
                validator="redundant_phrases",
                location="manuscript",
                evidence=unique,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 101 – Abstract quantitative result gap
# ---------------------------------------------------------------------------

_ABSTRACT_QUANT_RE = re.compile(
    r"\b\d+(\.\d+)?(%|fold|×|x|\s*pp\b)|"
    r"\bp\s*[<=>]\s*0\.\d+|"
    r"\b(accuracy|AUC|F1|RMSE|MAE|R2|R\^2|precision|recall|sensitivity|specificity)"
    r"\s*(of|=|:)?\s*\d",
    re.IGNORECASE,
)
_ABSTRACT_MIN_WORDS_QUANT = 50


def validate_abstract_quantitative_results(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical abstracts that lack any quantitative result.

    Emits ``abstract-no-quantitative-result`` (moderate) when an empirical
    paper's abstract (≥ ``_ABSTRACT_MIN_WORDS_QUANT`` words) reports no
    numerical results.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="abstract_quantitative_results", findings=[]
        )

    abstract = parsed.abstract or ""
    if len(abstract.split()) < _ABSTRACT_MIN_WORDS_QUANT:
        return ValidationResult(
            validator_name="abstract_quantitative_results", findings=[]
        )

    if _ABSTRACT_QUANT_RE.search(abstract):
        return ValidationResult(
            validator_name="abstract_quantitative_results", findings=[]
        )

    return ValidationResult(
        validator_name="abstract_quantitative_results",
        findings=[
            Finding(
                code="abstract-no-quantitative-result",
                severity="moderate",
                message=(
                    "Abstract of empirical paper reports no quantitative result "
                    "(no percentages, p-values, or metric values). "
                    "Key numerical outcomes should appear in the abstract."
                ),
                validator="abstract_quantitative_results",
                location="abstract",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 102 – Missing confidence intervals
# ---------------------------------------------------------------------------

_EFFECT_SIZE_RESULT_RE = re.compile(
    r"\b(Cohen'?s\s+[dDfg]|odds ratio|OR|hazard ratio|HR|"
    r"risk ratio|relative risk|RR|"
    r"mean difference|standardized mean|"
    r"correlation\s+coefficient|r\s*=\s*\d)\b",
    re.IGNORECASE,
)
_CI_PRESENT_RE = re.compile(
    r"\b(95%\s+CI|confidence interval|CI\s*[:\[]\s*\d|"
    r"\[\s*\d.*\d\s*\]|\(\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\))\b",
    re.IGNORECASE,
)
_CI_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
        "meta_analysis",
    }
)


def validate_confidence_interval_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that report effect sizes without confidence intervals.

    Emits ``missing-confidence-intervals`` (moderate) when Results sections
    contain effect size language but no confidence interval notation.
    """
    if classification.paper_type not in _CI_PAPER_TYPES:
        return ValidationResult(
            validator_name="confidence_interval_reporting", findings=[]
        )

    results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"results", "analysis", "findings"}
    )
    if not results_body:
        return ValidationResult(
            validator_name="confidence_interval_reporting", findings=[]
        )

    if not _EFFECT_SIZE_RESULT_RE.search(results_body):
        return ValidationResult(
            validator_name="confidence_interval_reporting", findings=[]
        )

    if _CI_PRESENT_RE.search(results_body):
        return ValidationResult(
            validator_name="confidence_interval_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="confidence_interval_reporting",
        findings=[
            Finding(
                code="missing-confidence-intervals",
                severity="moderate",
                message=(
                    "Results report effect sizes without confidence intervals. "
                    "Report 95% CIs alongside all effect size estimates."
                ),
                validator="confidence_interval_reporting",
                location="Results",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 103 – Bayesian prior justification
# ---------------------------------------------------------------------------

_BAYESIAN_RE = re.compile(
    r"\b(Bayesian|MCMC|Markov chain Monte Carlo|posterior|prior distribution|"
    r"likelihood function|credible interval|Bayes factor|"
    r"Stan|JAGS|PyMC|brms|NUTS sampler)\b",
    re.IGNORECASE,
)
_PRIOR_JUSTIFY_RE = re.compile(
    r"\b(prior (was|were|is|are) (chosen|selected|set|specified|derived)|"
    r"weakly informative|non-informative|uninformative|"
    r"half-Cauchy|half-Normal|normal prior|"
    r"prior choice|prior specification|elicited prior|"
    r"sensitivity (analysis|to prior|of prior))\b",
    re.IGNORECASE,
)


def validate_bayesian_prior_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayesian analyses without prior justification.

    Emits ``missing-prior-justification`` (moderate) when Bayesian methods
    are mentioned in Methods/Results but no prior specification or justification
    is provided.  Only fires for empirical paper types.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="bayesian_prior_justification", findings=[]
        )

    combined = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "statistical analysis", "analysis"}
    )
    if not combined:
        return ValidationResult(
            validator_name="bayesian_prior_justification", findings=[]
        )

    if not _BAYESIAN_RE.search(combined):
        return ValidationResult(
            validator_name="bayesian_prior_justification", findings=[]
        )

    if _PRIOR_JUSTIFY_RE.search(combined):
        return ValidationResult(
            validator_name="bayesian_prior_justification", findings=[]
        )

    return ValidationResult(
        validator_name="bayesian_prior_justification",
        findings=[
            Finding(
                code="missing-prior-justification",
                severity="moderate",
                message=(
                    "Bayesian analysis detected but no prior specification or "
                    "justification found in Methods. "
                    "Prior choices must be explicitly stated and defended."
                ),
                validator="bayesian_prior_justification",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 104 – Software/code version pinning
# ---------------------------------------------------------------------------

_SOFTWARE_CITATION_RE = re.compile(
    r"\b(R version|Python \d|numpy|pandas|scikit-learn|tensorflow|pytorch|"
    r"SPSS|SAS|Stata|MATLAB|Julia \d|Mplus|lavaan|lme4|"
    r"package version|version \d+\.\d+)\b",
    re.IGNORECASE,
)
_VERSION_PINNED_RE = re.compile(
    r"\b(version \d+\.\d+[\.\d]*|v\d+\.\d+|"
    r"R \d+\.\d+\.\d+|Python \d+\.\d+(\.\d+)?)\b",
    re.IGNORECASE,
)
_VERSION_PAPER_TYPES = frozenset(
    {
        "software_workflow_paper",
        "empirical_paper",
        "applied_stats_paper",
    }
)


def validate_software_version_pinning(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag software papers that name packages without version numbers.

    Emits ``missing-software-versions`` (minor) when Methods sections name
    software tools/packages without pinned version numbers.
    """
    if classification.paper_type not in _VERSION_PAPER_TYPES:
        return ValidationResult(
            validator_name="software_version_pinning", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "implementation", "software"}
    )
    if not methods_body:
        return ValidationResult(
            validator_name="software_version_pinning", findings=[]
        )

    if not _SOFTWARE_CITATION_RE.search(methods_body):
        return ValidationResult(
            validator_name="software_version_pinning", findings=[]
        )

    if _VERSION_PINNED_RE.search(methods_body):
        return ValidationResult(
            validator_name="software_version_pinning", findings=[]
        )

    return ValidationResult(
        validator_name="software_version_pinning",
        findings=[
            Finding(
                code="missing-software-versions",
                severity="minor",
                message=(
                    "Methods section names software packages without version numbers. "
                    "Pin exact versions for computational reproducibility."
                ),
                validator="software_version_pinning",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 105 – Measurement scale reporting
# ---------------------------------------------------------------------------

_SCALE_MENTION_RE = re.compile(
    r"\b(Likert|scale|questionnaire|survey instrument|rating scale|"
    r"self-reported|self-report|psychometric|inventory)\b",
    re.IGNORECASE,
)
_RELIABILITY_RE = re.compile(
    r"\b(Cronbach|alpha\s*=\s*0\.\d+|coefficient alpha|"
    r"internal consistency|test-retest|inter-rater|"
    r"reliability|validity|confirmatory factor|CFA|"
    r"McDonald'?s omega|omega\s*=)\b",
    re.IGNORECASE,
)
_SCALE_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "survey_study",
        "clinical_trial_report",
    }
)


def validate_measurement_scale_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers that use scales without reliability reporting.

    Emits ``missing-scale-reliability`` (moderate) when Methods sections
    mention Likert scales or survey instruments without reporting reliability
    statistics (Cronbach's alpha, omega, etc.).
    """
    if classification.paper_type not in _SCALE_PAPER_TYPES:
        return ValidationResult(
            validator_name="measurement_scale_reporting", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {
            "methods",
            "methodology",
            "measures",
            "instruments",
            "participants",
        }
    )
    if not methods_body:
        return ValidationResult(
            validator_name="measurement_scale_reporting", findings=[]
        )

    if not _SCALE_MENTION_RE.search(methods_body):
        return ValidationResult(
            validator_name="measurement_scale_reporting", findings=[]
        )

    if _RELIABILITY_RE.search(methods_body):
        return ValidationResult(
            validator_name="measurement_scale_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="measurement_scale_reporting",
        findings=[
            Finding(
                code="missing-scale-reliability",
                severity="moderate",
                message=(
                    "Methods describe survey scales or Likert instruments without "
                    "reporting reliability (e.g., Cronbach's alpha). "
                    "Measurement reliability must be documented."
                ),
                validator="measurement_scale_reporting",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 106 – Missing model fit indices
# ---------------------------------------------------------------------------

_SEM_RE = re.compile(
    r"\b(structural equation model|SEM|path model|CFA|EFA|"
    r"latent variable|confirmatory factor analysis|"
    r"exploratory factor analysis)\b",
    re.IGNORECASE,
)
_FIT_INDEX_RE = re.compile(
    r"\b(CFI|TLI|RMSEA|SRMR|NFI|GFI|AGFI|"
    r"comparative fit|Tucker-Lewis|root mean square|"
    r"model fit|fit statistics|fit indices)\b",
    re.IGNORECASE,
)
_SEM_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "survey_study",
    }
)


def validate_sem_fit_indices(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag SEM analyses without model fit indices.

    Emits ``missing-sem-fit-indices`` (moderate) when Results sections contain
    SEM or CFA language without reporting standard fit indices (CFI, RMSEA, etc.).
    """
    if classification.paper_type not in _SEM_PAPER_TYPES:
        return ValidationResult(
            validator_name="sem_fit_indices", findings=[]
        )

    results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"results", "analysis", "findings"}
    )
    if not results_body:
        return ValidationResult(
            validator_name="sem_fit_indices", findings=[]
        )

    if not _SEM_RE.search(results_body):
        return ValidationResult(
            validator_name="sem_fit_indices", findings=[]
        )

    if _FIT_INDEX_RE.search(results_body):
        return ValidationResult(
            validator_name="sem_fit_indices", findings=[]
        )

    return ValidationResult(
        validator_name="sem_fit_indices",
        findings=[
            Finding(
                code="missing-sem-fit-indices",
                severity="moderate",
                message=(
                    "Structural equation model or CFA detected but no fit indices "
                    "(CFI, TLI, RMSEA, SRMR) reported. "
                    "Model fit must be assessed and reported."
                ),
                validator="sem_fit_indices",
                location="Results",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 107 – Missing variance explanation
# ---------------------------------------------------------------------------

_REGRESSION_RE = re.compile(
    r"\b(regress(ion|ed)|linear model|GLM|generalized linear|"
    r"logistic regression|OLS|least squares|lm\(|glm\()\b",
    re.IGNORECASE,
)
_R_SQUARED_RE = re.compile(
    r"\b(R.squared|R2\s*=|adjusted R|variance explained|"
    r"explained variance|proportion of variance|"
    r"R\^2\s*=\s*0\.\d+|r2\s*=\s*0\.\d+)\b",
    re.IGNORECASE,
)
_REGRESSION_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "survey_study",
    }
)


def validate_regression_variance_explanation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression analyses without R-squared or variance explanation.

    Emits ``missing-variance-explained`` (moderate) when Results sections
    describe regression models without reporting variance explained (R²).
    """
    if classification.paper_type not in _REGRESSION_PAPER_TYPES:
        return ValidationResult(
            validator_name="regression_variance_explanation", findings=[]
        )

    results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"results", "analysis", "findings", "statistical analysis"}
    )
    if not results_body:
        return ValidationResult(
            validator_name="regression_variance_explanation", findings=[]
        )

    if not _REGRESSION_RE.search(results_body):
        return ValidationResult(
            validator_name="regression_variance_explanation", findings=[]
        )

    if _R_SQUARED_RE.search(results_body):
        return ValidationResult(
            validator_name="regression_variance_explanation", findings=[]
        )

    return ValidationResult(
        validator_name="regression_variance_explanation",
        findings=[
            Finding(
                code="missing-variance-explained",
                severity="moderate",
                message=(
                    "Regression analysis detected but no R-squared or variance "
                    "explained reported. Include variance accounted for by the model."
                ),
                validator="regression_variance_explanation",
                location="Results",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 108 – Normality assumption check
# ---------------------------------------------------------------------------

_PARAMETRIC_TEST_RE = re.compile(
    r"\b(t-test|ANOVA|ANCOVA|MANOVA|Pearson correlation|"
    r"linear regression|paired t|independent samples t|"
    r"one-way ANOVA|two-way ANOVA|repeated measures ANOVA)\b",
    re.IGNORECASE,
)
_NORMALITY_CHECK_RE = re.compile(
    r"\b(Shapiro.Wilk|Kolmogorov.Smirnov|Anderson.Darling|"
    r"normality test|normally distributed|normal distribution "
    r"(was|were) (confirmed|assumed|verified|assessed)|"
    r"QQ.plot|histogram|distribution (was|were) (checked|assessed|examined)|"
    r"non-parametric|nonparametric|Wilcoxon|Mann.Whitney|Kruskal.Wallis)\b",
    re.IGNORECASE,
)
_NORMALITY_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "survey_study",
        "clinical_trial_report",
    }
)


def validate_normality_assumption(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag parametric tests without normality checking.

    Emits ``missing-normality-check`` (moderate) when Methods/Results sections
    report parametric tests (t-test, ANOVA) without any normality assessment.
    """
    if classification.paper_type not in _NORMALITY_PAPER_TYPES:
        return ValidationResult(
            validator_name="normality_assumption", findings=[]
        )

    combined = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "results", "statistical analysis"}
    )
    if not combined:
        return ValidationResult(validator_name="normality_assumption", findings=[])

    if not _PARAMETRIC_TEST_RE.search(combined):
        return ValidationResult(validator_name="normality_assumption", findings=[])

    if _NORMALITY_CHECK_RE.search(combined):
        return ValidationResult(validator_name="normality_assumption", findings=[])

    return ValidationResult(
        validator_name="normality_assumption",
        findings=[
            Finding(
                code="missing-normality-check",
                severity="moderate",
                message=(
                    "Parametric tests (t-test, ANOVA) used without reporting "
                    "normality assumption checks. Include normality test results "
                    "or justify the assumption."
                ),
                validator="normality_assumption",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 109 – Attrition / dropout reporting
# ---------------------------------------------------------------------------

_LONGITUDINAL_RE = re.compile(
    r"\b(longitudinal|follow-up|follow up|repeated measures|"
    r"time point|wave \d|panel (study|data)|"
    r"cohort study|prospective (study|cohort)|"
    r"baseline and (follow|post)|"
    r"months? (later|after)|years? (later|after))\b",
    re.IGNORECASE,
)
_ATTRITION_RE = re.compile(
    r"\b(attrition|dropout|drop.out|lost to follow.up|"
    r"missing data|incomplete (data|cases|responses)|"
    r"participants? (who|that) (withdrew|dropped|did not complete|were lost)|"
    r"retention rate|completion rate|"
    r"CONSORT|STROBE|PRISMA)\b",
    re.IGNORECASE,
)
_ATTRITION_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "clinical_trial_report",
        "survey_study",
    }
)


def validate_attrition_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal studies without attrition or dropout reporting.

    Emits ``missing-attrition-report`` (moderate) when Methods/Results indicate
    a longitudinal design but no attrition/dropout information is provided.
    """
    if classification.paper_type not in _ATTRITION_PAPER_TYPES:
        return ValidationResult(validator_name="attrition_reporting", findings=[])

    combined = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {
            "methods",
            "methodology",
            "participants",
            "results",
            "sample",
        }
    )
    if not combined:
        return ValidationResult(validator_name="attrition_reporting", findings=[])

    if not _LONGITUDINAL_RE.search(combined):
        return ValidationResult(validator_name="attrition_reporting", findings=[])

    if _ATTRITION_RE.search(combined):
        return ValidationResult(validator_name="attrition_reporting", findings=[])

    return ValidationResult(
        validator_name="attrition_reporting",
        findings=[
            Finding(
                code="missing-attrition-report",
                severity="moderate",
                message=(
                    "Longitudinal study design detected but no attrition or "
                    "dropout reporting found. Document participant retention "
                    "and missing data handling."
                ),
                validator="attrition_reporting",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 110 – Generalizability overclaim
# ---------------------------------------------------------------------------

_GENERALIZE_CLAIM_RE = re.compile(
    r"\b(generali[sz](es?|ability|able) to (all|any|every|the general|broader|"
    r"the wider|the whole)|universally applicable|applies? to all|"
    r"valid for all populations|applicable in all contexts)\b",
    re.IGNORECASE,
)
_GENERALIZE_HEDGE_RE = re.compile(
    r"\b(may generali[sz]|might apply|could be extended|"
    r"further (research|study|investigation) (is needed|needed|required)|"
    r"limited (to|by) (our|this|the) (sample|context|population)|"
    r"external validity)\b",
    re.IGNORECASE,
)


def validate_generalizability_overclaim(parsed: ParsedManuscript) -> ValidationResult:
    """Flag overgeneralized claims without appropriate hedging.

    Emits ``generalizability-overclaim`` (major) when the manuscript claims
    universal generalizability without qualifying language about sample
    limitations.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not _GENERALIZE_CLAIM_RE.search(combined):
        return ValidationResult(
            validator_name="generalizability_overclaim", findings=[]
        )
    if _GENERALIZE_HEDGE_RE.search(combined):
        return ValidationResult(
            validator_name="generalizability_overclaim", findings=[]
        )

    match = _GENERALIZE_CLAIM_RE.search(combined)
    return ValidationResult(
        validator_name="generalizability_overclaim",
        findings=[
            Finding(
                code="generalizability-overclaim",
                severity="major",
                message=(
                    "Manuscript claims broad generalizability "
                    f"('{match.group() if match else ''}') "
                    "without appropriate qualification. "
                    "Scope claims to actual sample characteristics."
                ),
                validator="generalizability_overclaim",
                location="manuscript",
                evidence=[match.group() if match else ""],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 111 – Missing interrater reliability
# ---------------------------------------------------------------------------

_CODING_RE = re.compile(
    r"\b(coded?|coding|coders?|annotated?|annotation|raters?|rating|"
    r"human (judges?|evaluators?)|manual (coding|annotation|rating|labeling)|"
    r"inter-?rater|content analysis)\b",
    re.IGNORECASE,
)
_IRR_RE = re.compile(
    r"\b(inter-?rater (reliability|agreement)|Cohen'?s kappa|kappa\s*=|"
    r"Fleiss'? kappa|ICC|intraclass correlation|"
    r"percent agreement|Krippendorff'?s alpha|"
    r"reliability coefficient|reliability (was|were) (assessed|checked|computed))\b",
    re.IGNORECASE,
)
_IRR_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "survey_study",
        "systematic_review",
    }
)


def validate_interrater_reliability(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag coded/annotated data without interrater reliability reporting.

    Emits ``missing-interrater-reliability`` (moderate) when Methods describe
    human coding or rating without reporting interrater reliability statistics.
    """
    if classification.paper_type not in _IRR_PAPER_TYPES:
        return ValidationResult(
            validator_name="interrater_reliability", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"methods", "methodology", "coding", "procedure"}
    )
    if not methods_body:
        return ValidationResult(
            validator_name="interrater_reliability", findings=[]
        )

    if not _CODING_RE.search(methods_body):
        return ValidationResult(
            validator_name="interrater_reliability", findings=[]
        )

    if _IRR_RE.search(methods_body):
        return ValidationResult(
            validator_name="interrater_reliability", findings=[]
        )

    return ValidationResult(
        validator_name="interrater_reliability",
        findings=[
            Finding(
                code="missing-interrater-reliability",
                severity="moderate",
                message=(
                    "Human coding or rating described in Methods without reporting "
                    "interrater reliability (Cohen's kappa, ICC, percent agreement). "
                    "Reliability of coding must be documented."
                ),
                validator="interrater_reliability",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 112 – Spurious numerical precision
# ---------------------------------------------------------------------------

_SPURIOUS_PRECISION_RE = re.compile(
    r"\b\d+\.\d{5,}\b"
)
_SPURIOUS_SECTION_TITLES = frozenset(
    {"results", "analysis", "findings", "statistical analysis"}
)


def validate_spurious_precision(parsed: ParsedManuscript) -> ValidationResult:
    """Flag results reported with excessive decimal places.

    Emits ``spurious-precision`` (minor) when Results sections contain
    numbers with 5+ decimal places (e.g., mean = 3.14159265), which
    implies false precision in measurement.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() not in _SPURIOUS_SECTION_TITLES:
            continue
        matches = _SPURIOUS_PRECISION_RE.findall(section.body)
        if matches:
            findings.append(
                Finding(
                    code="spurious-precision",
                    severity="minor",
                    message=(
                        f"Section '{section.title}' reports values with excessive "
                        f"decimal precision ({matches[0]}...). "
                        "Round to 2-3 significant digits appropriate to measurement."
                    ),
                    validator="spurious_precision",
                    location=section.title,
                    evidence=matches[:3],
                )
            )
    return ValidationResult(validator_name="spurious_precision", findings=findings)


# ---------------------------------------------------------------------------
# Phase 113 – Vague temporal reference
# ---------------------------------------------------------------------------

_VAGUE_TEMPORAL_RE = re.compile(
    r"\b(recently|in recent (?:years|months|times?|decades?)|"
    r"lately|in the past (?:few|several|recent) years|"
    r"nowadays|these days|currently available)\b",
    re.IGNORECASE,
)
_TEMPORAL_ANCHOR_RE = re.compile(
    r"\b(since \d{4}|in \d{4}|between \d{4} and \d{4}|"
    r"from \d{4} to \d{4}|\[\d+\]|[@\\]cite)\b",
    re.IGNORECASE,
)
_VAGUE_TEMPORAL_THRESHOLD = 3


def validate_vague_temporal_claims(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts with multiple unanchored temporal references.

    Emits ``vague-temporal-claims`` (minor) when ≥ ``_VAGUE_TEMPORAL_THRESHOLD``
    vague temporal references (e.g., "recently", "in recent years") appear
    without date anchors or citations nearby.
    """
    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    vague_matches = _VAGUE_TEMPORAL_RE.findall(combined)
    if len(vague_matches) < _VAGUE_TEMPORAL_THRESHOLD:
        return ValidationResult(
            validator_name="vague_temporal_claims", findings=[]
        )
    if _TEMPORAL_ANCHOR_RE.search(combined):
        return ValidationResult(
            validator_name="vague_temporal_claims", findings=[]
        )

    unique = list({m.lower() for m in vague_matches})[:3]
    return ValidationResult(
        validator_name="vague_temporal_claims",
        findings=[
            Finding(
                code="vague-temporal-claims",
                severity="minor",
                message=(
                    f"Manuscript contains {len(vague_matches)} unanchored temporal "
                    "references (e.g., 'recently', 'in recent years') without date "
                    "anchors or citations. Replace with specific years or cite sources."
                ),
                validator="vague_temporal_claims",
                location="manuscript",
                evidence=unique,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 114 – Missing exclusion criteria
# ---------------------------------------------------------------------------

_EXCLUSION_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "clinical_trial_report",
        "survey_study",
    }
)
_INCLUSION_RE = re.compile(
    r"\b(inclusion criteria|included (participants?|subjects?|patients?)|"
    r"eligible (participants?|subjects?|patients?)|"
    r"eligibility criteria)\b",
    re.IGNORECASE,
)
_EXCLUSION_RE = re.compile(
    r"\b(exclusion criteria|excluded (participants?|subjects?|patients?|from)|"
    r"ineligible|were excluded|excluded (if|due to|because))\b",
    re.IGNORECASE,
)


def validate_exclusion_criteria(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies with inclusion but no exclusion criteria.

    Emits ``missing-exclusion-criteria`` (moderate) when Methods describe
    inclusion criteria for participants without corresponding exclusion criteria.
    """
    if classification.paper_type not in _EXCLUSION_PAPER_TYPES:
        return ValidationResult(
            validator_name="exclusion_criteria", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {
            "methods", "methodology", "participants", "sample", "procedure"
        }
    )
    if not methods_body:
        return ValidationResult(validator_name="exclusion_criteria", findings=[])

    if not _INCLUSION_RE.search(methods_body):
        return ValidationResult(validator_name="exclusion_criteria", findings=[])

    if _EXCLUSION_RE.search(methods_body):
        return ValidationResult(validator_name="exclusion_criteria", findings=[])

    return ValidationResult(
        validator_name="exclusion_criteria",
        findings=[
            Finding(
                code="missing-exclusion-criteria",
                severity="moderate",
                message=(
                    "Methods describe inclusion criteria but no exclusion criteria. "
                    "Explicitly state who was excluded and why."
                ),
                validator="exclusion_criteria",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 115 – Title length
# ---------------------------------------------------------------------------

_TITLE_MAX_WORDS = 20
_TITLE_MIN_WORDS = 5


def validate_title_length(parsed: ParsedManuscript) -> ValidationResult:
    """Flag overlong or excessively short titles.

    Emits ``title-too-long`` (minor) when the title exceeds
    ``_TITLE_MAX_WORDS`` words, and ``title-too-short`` (minor) when
    the title has fewer than ``_TITLE_MIN_WORDS`` words.
    """
    title = parsed.title or ""
    word_count = len(title.split())
    if word_count > _TITLE_MAX_WORDS:
        return ValidationResult(
            validator_name="title_length",
            findings=[
                Finding(
                    code="title-too-long",
                    severity="minor",
                    message=(
                        f"Title has {word_count} words (max {_TITLE_MAX_WORDS}). "
                        "Shorten to improve discoverability and journal compliance."
                    ),
                    validator="title_length",
                    location="title",
                    evidence=[f"{word_count} words"],
                )
            ],
        )
    if word_count > 0 and word_count < _TITLE_MIN_WORDS:
        return ValidationResult(
            validator_name="title_length",
            findings=[
                Finding(
                    code="title-too-short",
                    severity="minor",
                    message=(
                        f"Title has only {word_count} words (min {_TITLE_MIN_WORDS}). "
                        "A descriptive title improves discoverability."
                    ),
                    validator="title_length",
                    location="title",
                    evidence=[f"{word_count} words"],
                )
            ],
        )
    return ValidationResult(validator_name="title_length", findings=[])


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
        validate_duplicate_claims(parsed),
        validate_hedging_density(parsed),
        validate_related_work_coverage(parsed, classification),
        validate_limitations_coverage(parsed, classification),
        validate_acronym_consistency(parsed),
        validate_methods_tense_consistency(parsed),
        validate_sentence_length_outliers(parsed),
        validate_citation_cluster_gap(parsed, classification),
        validate_power_word_overuse(parsed),
        validate_number_format_consistency(parsed),
        validate_abstract_keyword_coverage(parsed),
        validate_contribution_claim_count(parsed),
        validate_first_person_consistency(parsed),
        validate_caption_quality(parsed),
        validate_reference_staleness(parsed, classification),
        validate_terminology_drift(parsed),
        validate_introduction_structure(parsed),
        validate_reproducibility_checklist(parsed, classification),
        validate_self_citation_ratio(parsed),
        validate_conclusion_scope(parsed),
        validate_equation_density(parsed, classification),
        validate_abstract_structure(parsed),
        validate_url_format(parsed),
        validate_figure_table_balance(parsed, classification),
        validate_section_ordering(parsed, classification),
        validate_keyword_section_coverage(parsed),
        validate_statistical_test_reporting(parsed, classification),
        validate_effect_size_reporting(parsed, classification),
        validate_acknowledgments_presence(parsed, classification),
        validate_conflict_of_interest(parsed, classification),
        validate_data_availability(parsed, classification),
        validate_ethics_statement(parsed),
        validate_citation_style_consistency(parsed),
        validate_cross_reference_integrity(parsed),
        validate_decimal_precision_consistency(parsed),
        validate_future_work_balance(parsed),
        validate_null_result_acknowledgment(parsed, classification),
        validate_hedging_language(parsed),
        validate_duplicate_section_content(parsed),
        validate_methods_depth(parsed, classification),
        validate_list_overuse(parsed),
        validate_section_balance(parsed, classification),
        validate_related_work_recency(parsed, classification),
        validate_introduction_length(parsed),
        validate_unquantified_comparisons(parsed),
        validate_footnote_overuse(parsed),
        validate_abbreviation_list(parsed),
        validate_abstract_tense(parsed),
        validate_claim_strength_escalation(parsed),
        validate_sample_size_reporting(parsed, classification),
        validate_limitations_section_presence(parsed, classification),
        validate_author_contribution_statement(parsed),
        validate_preregistration_mention(parsed, classification),
        validate_reviewer_response_completeness(parsed),
        validate_novelty_overclaim(parsed),
        validate_figure_table_minimum(parsed, classification),
        validate_multiple_comparisons_correction(parsed, classification),
        validate_supplementary_material_indication(parsed),
        validate_conclusion_scope_creep(parsed),
        validate_discussion_results_alignment(parsed),
        validate_open_data_statement(parsed, classification),
        validate_redundant_phrases(parsed),
        validate_abstract_quantitative_results(parsed, classification),
        validate_confidence_interval_reporting(parsed, classification),
        validate_bayesian_prior_justification(parsed, classification),
        validate_software_version_pinning(parsed, classification),
        validate_measurement_scale_reporting(parsed, classification),
        validate_sem_fit_indices(parsed, classification),
        validate_regression_variance_explanation(parsed, classification),
        validate_normality_assumption(parsed, classification),
        validate_attrition_reporting(parsed, classification),
        validate_generalizability_overclaim(parsed),
        validate_interrater_reliability(parsed, classification),
        validate_spurious_precision(parsed),
        validate_vague_temporal_claims(parsed),
        validate_exclusion_criteria(parsed, classification),
        validate_title_length(parsed),
    ]
    partial = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_claim_evidence_escalation(partial))
    partial2 = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_critical_escalation(partial2))
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
