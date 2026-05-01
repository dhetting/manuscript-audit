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
    ]
    partial = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_claim_evidence_escalation(partial))
    partial2 = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_critical_escalation(partial2))
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
