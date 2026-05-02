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


# ---------------------------------------------------------------------------
# Phase 116 – Missing statistical power / sample size justification
# ---------------------------------------------------------------------------

_POWER_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "applied_stats_paper",
        "clinical_trial_report",
    }
)
_POWER_RE = re.compile(
    r"\b(power analysis|statistical power|power\s*=\s*0\.\d+|"
    r"powered to detect|adequately powered|"
    r"sample size (was|is|were) (determined|calculated|justified|estimated)|"
    r"a priori power|G\*Power|power calculation|"
    r"minimum (detectable|significant) (effect|difference))\b",
    re.IGNORECASE,
)


def validate_statistical_power(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without statistical power analysis.

    Emits ``missing-power-analysis`` (moderate) when an empirical paper's
    Methods section lacks any reference to statistical power or sample size
    justification.
    """
    if classification.paper_type not in _POWER_PAPER_TYPES:
        return ValidationResult(
            validator_name="statistical_power", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {
            "methods", "methodology", "participants", "sample", "statistical analysis"
        }
    )
    if not methods_body:
        return ValidationResult(validator_name="statistical_power", findings=[])

    if _POWER_RE.search(methods_body):
        return ValidationResult(validator_name="statistical_power", findings=[])

    return ValidationResult(
        validator_name="statistical_power",
        findings=[
            Finding(
                code="missing-power-analysis",
                severity="moderate",
                message=(
                    "Empirical manuscript lacks statistical power analysis or "
                    "sample size justification in Methods. "
                    "Report the a priori power calculation used to determine N."
                ),
                validator="statistical_power",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 117 – Missing keywords section
# ---------------------------------------------------------------------------

_KEYWORD_SECTION_TITLES = frozenset(
    {"keywords", "key words", "index terms", "subject terms"}
)
_KEYWORD_INLINE_RE = re.compile(
    r"\b(keywords?|key words?|index terms?)\s*:\s*\S",
    re.IGNORECASE,
)


def validate_keywords_present(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts without a keywords section or inline keyword list.

    Emits ``missing-keywords`` (minor) when no keywords section or
    inline keyword declaration is detected in the manuscript.
    """
    has_section = any(
        s.title.lower() in _KEYWORD_SECTION_TITLES for s in parsed.sections
    )
    if has_section:
        return ValidationResult(validator_name="keywords_present", findings=[])

    combined = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _KEYWORD_INLINE_RE.search(combined) or _KEYWORD_INLINE_RE.search(
        parsed.abstract or ""
    ):
        return ValidationResult(validator_name="keywords_present", findings=[])

    return ValidationResult(
        validator_name="keywords_present",
        findings=[
            Finding(
                code="missing-keywords",
                severity="minor",
                message=(
                    "No keywords section or inline keyword list detected. "
                    "Most journals require a keyword list for indexing."
                ),
                validator="keywords_present",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 118 – Results/Discussion overlong sentences
# ---------------------------------------------------------------------------

_SENTENCE_SPLIT_RESULTS_RE = re.compile(r"(?<=[.!?])\s+")
_OVERLONG_SENTENCE_WORDS = 60
_OVERLONG_SECTION_TITLES = frozenset({"results", "discussion", "analysis", "findings"})


def validate_overlong_sentences(parsed: ParsedManuscript) -> ValidationResult:
    """Flag sections with sentences exceeding the word-count threshold.

    Emits ``overlong-sentence`` (minor) when Results or Discussion sections
    contain sentences with more than ``_OVERLONG_SENTENCE_WORDS`` words.
    """
    findings: list[Finding] = []
    for section in parsed.sections:
        if section.title.lower() not in _OVERLONG_SECTION_TITLES:
            continue
        sentences = _SENTENCE_SPLIT_RESULTS_RE.split(section.body)
        for sent in sentences:
            wc = len(sent.split())
            if wc > _OVERLONG_SENTENCE_WORDS:
                findings.append(
                    Finding(
                        code="overlong-sentence",
                        severity="minor",
                        message=(
                            f"Section '{section.title}' contains a sentence with "
                            f"{wc} words (max {_OVERLONG_SENTENCE_WORDS}). "
                            "Break long sentences to improve readability."
                        ),
                        validator="overlong_sentences",
                        location=section.title,
                        evidence=[sent[:80] + "..." if len(sent) > 80 else sent],
                    )
                )
    return ValidationResult(validator_name="overlong_sentences", findings=findings)


# ---------------------------------------------------------------------------
# Phase 119 – Mixed heading capitalization
# ---------------------------------------------------------------------------

def _is_title_case(text: str) -> bool:
    """Return True if most content words are Title Cased."""
    words = [w for w in text.split() if len(w) > 3]
    if not words:
        return False
    title_cased = sum(1 for w in words if w[0].isupper())
    return title_cased / len(words) > 0.6


def _is_sentence_case(text: str) -> bool:
    """Return True if text looks like Sentence case (only first word capitalized)."""
    words = [w for w in text.split() if len(w) > 3]
    if not words:
        return False
    title_cased = sum(1 for w in words if w[0].isupper())
    return title_cased / len(words) < 0.3


_HEADING_MIN_SECTIONS = 4


def validate_heading_capitalization_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag inconsistent heading capitalization styles.

    Emits ``inconsistent-heading-capitalization`` (minor) when a manuscript
    mixes Title Case and Sentence case across section headings
    (requires ≥ ``_HEADING_MIN_SECTIONS`` sections).
    """
    major_sections = [
        s for s in parsed.sections
        if s.title.lower() not in _SKIP_SECTIONS and len(s.title.split()) >= 2
    ]
    if len(major_sections) < _HEADING_MIN_SECTIONS:
        return ValidationResult(
            validator_name="heading_capitalization_consistency", findings=[]
        )

    title_case_count = sum(1 for s in major_sections if _is_title_case(s.title))
    sentence_case_count = sum(1 for s in major_sections if _is_sentence_case(s.title))
    total = len(major_sections)

    has_title = title_case_count / total > 0.25
    has_sentence = sentence_case_count / total > 0.25

    if has_title and has_sentence:
        return ValidationResult(
            validator_name="heading_capitalization_consistency",
            findings=[
                Finding(
                    code="inconsistent-heading-capitalization",
                    severity="minor",
                    message=(
                        f"Section headings mix Title Case ({title_case_count}) and "
                        f"Sentence case ({sentence_case_count}). "
                        "Use a consistent capitalization style throughout."
                    ),
                    validator="heading_capitalization_consistency",
                    location="manuscript",
                    evidence=[
                        f"title-case: {title_case_count}/{total}",
                        f"sentence-case: {sentence_case_count}/{total}",
                    ],
                )
            ],
        )

    return ValidationResult(
        validator_name="heading_capitalization_consistency", findings=[]
    )


# ---------------------------------------------------------------------------
# Phase 120 – Unanswered research questions
# ---------------------------------------------------------------------------

_RESEARCH_QUESTION_RE = re.compile(
    r"\b(research question|RQ\d*|we (ask|investigate|examine|explore) "
    r"(whether|how|what|if|why)|"
    r"the (central|main|primary|key) (question|aim|objective) (is|was)|"
    r"this (paper|study) (aims?|seeks?|investigates?|examines?) to)\b",
    re.IGNORECASE,
)
_RESULTS_PRESENT_RE = re.compile(
    r"\b(results? (show|indicate|suggest|demonstrate|confirm)|"
    r"we (found|observed|detected|identified|showed|demonstrated)|"
    r"our (analysis|results?|findings?) (show|indicate|suggest|demonstrate))\b",
    re.IGNORECASE,
)


def validate_research_question_addressed(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts that state research questions without results addressing them.

    Emits ``unanswered-research-question`` (moderate) when Introduction states
    research questions but Results/Discussion contains no interpretive results
    language.
    """
    intro_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"introduction", "intro", "background"}
    )
    if not intro_body or not _RESEARCH_QUESTION_RE.search(intro_body):
        return ValidationResult(
            validator_name="research_question_addressed", findings=[]
        )

    results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"results", "discussion", "findings", "analysis"}
    )
    if not results_body:
        return ValidationResult(
            validator_name="research_question_addressed", findings=[]
        )

    if _RESULTS_PRESENT_RE.search(results_body):
        return ValidationResult(
            validator_name="research_question_addressed", findings=[]
        )

    return ValidationResult(
        validator_name="research_question_addressed",
        findings=[
            Finding(
                code="unanswered-research-question",
                severity="moderate",
                message=(
                    "Introduction states research questions but Results/Discussion "
                    "contains no explicit results addressing them. "
                    "Ensure each stated research question is directly answered."
                ),
                validator="research_question_addressed",
                location="Results/Discussion",
            )
        ],
    )



# ---------------------------------------------------------------------------
# Phase 122 – Citations in abstract
# ---------------------------------------------------------------------------

_ABSTRACT_CITATION_RE = re.compile(
    r"(\[\d+\]|\(\w[^)]*,\s*\d{4}\w?\)|et al\.\s*,\s*\d{4}|"
    r"\(\d{4}\)|\\cite\{|\[cite\])",
    re.IGNORECASE,
)


def validate_citations_in_abstract(parsed: ParsedManuscript) -> ValidationResult:
    """Flag abstracts containing inline citations.

    Emits ``citations-in-abstract`` (minor) when the abstract contains citation
    markers. Most journals do not allow citations in abstracts.
    """
    abstract = parsed.abstract or ""
    if not abstract:
        return ValidationResult(
            validator_name="citations_in_abstract", findings=[]
        )

    matches = _ABSTRACT_CITATION_RE.findall(abstract)
    if not matches:
        return ValidationResult(
            validator_name="citations_in_abstract", findings=[]
        )

    return ValidationResult(
        validator_name="citations_in_abstract",
        findings=[
            Finding(
                code="citations-in-abstract",
                severity="minor",
                message=(
                    f"Abstract contains {len(matches)} citation marker(s). "
                    "Most journals prohibit citations in abstracts."
                ),
                validator="citations_in_abstract",
                location="Abstract",
                evidence=[str(m) for m in matches[:3]],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 123 – Missing funding / acknowledgment statement
# ---------------------------------------------------------------------------

_FUNDING_RE = re.compile(
    r"\b(funded by|funding from|supported by|grant (number|no\.?)|"
    r"award number|this (work|research|study) was (funded|supported)|"
    r"acknowledgm|acknowledgements?|no funding|no financial support)\b",
    re.IGNORECASE,
)
_FUNDING_SECTION_TITLES = frozenset(
    {
        "acknowledgment",
        "acknowledgments",
        "acknowledgement",
        "acknowledgements",
        "funding",
        "financial support",
        "funding sources",
    }
)


def validate_funding_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag papers without a funding/acknowledgment statement.

    Emits ``missing-funding-statement`` (minor) when no funding or
    acknowledgment section/statement is found.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="funding_statement", findings=[]
        )

    has_section = any(
        s.title.lower() in _FUNDING_SECTION_TITLES for s in parsed.sections
    )
    if has_section:
        return ValidationResult(validator_name="funding_statement", findings=[])

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _FUNDING_RE.search(full):
        return ValidationResult(validator_name="funding_statement", findings=[])

    return ValidationResult(
        validator_name="funding_statement",
        findings=[
            Finding(
                code="missing-funding-statement",
                severity="minor",
                message=(
                    "No funding statement or acknowledgment section found. "
                    "Most journals require a funding disclosure."
                ),
                validator="funding_statement",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 124 – Missing Discussion section in empirical papers
# ---------------------------------------------------------------------------

_DISCUSSION_TITLES = frozenset(
    {"discussion", "discussion and conclusions", "discussion and conclusion",
     "discussion and implications"}
)


def validate_discussion_section_presence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without a Discussion section.

    Emits ``missing-discussion-section`` (moderate) when an empirical paper
    has Results but no Discussion section.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="discussion_section_presence", findings=[]
        )

    has_results = any(
        s.title.lower() in {"results", "findings", "analysis"} for s in parsed.sections
    )
    if not has_results:
        return ValidationResult(
            validator_name="discussion_section_presence", findings=[]
        )

    has_discussion = any(
        s.title.lower() in _DISCUSSION_TITLES for s in parsed.sections
    )
    if has_discussion:
        return ValidationResult(
            validator_name="discussion_section_presence", findings=[]
        )

    return ValidationResult(
        validator_name="discussion_section_presence",
        findings=[
            Finding(
                code="missing-discussion-section",
                severity="moderate",
                message=(
                    "Empirical paper has a Results section but no Discussion section. "
                    "Add a Discussion interpreting the results in context."
                ),
                validator="discussion_section_presence",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 125 – Inconsistent p-value notation
# ---------------------------------------------------------------------------

_PVAL_FORMATS_RE = [
    re.compile(r"\bp\s*<\s*0\.\d+"),           # p < 0.05
    re.compile(r"\bp\s*=\s*0\.\d+"),            # p = 0.05
    re.compile(r"\bp\s*>\s*0\.\d+"),            # p > 0.05
    re.compile(r"\bP\s*[<>=]\s*0\.\d+"),        # P-value capitalized
    re.compile(r"\bp-value\s*[<>=]\s*0\.\d+"),  # p-value < 0.05
]
_PVAL_MIN_TOTAL = 3


def validate_pvalue_notation_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag inconsistent p-value notation formats.

    Emits ``inconsistent-pvalue-notation`` (minor) when the manuscript uses
    multiple different p-value notation styles, requiring >=
    ``_PVAL_MIN_TOTAL`` total p-value occurrences.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="pvalue_notation_consistency", findings=[]
        )

    found_styles: list[str] = []
    for pat in _PVAL_FORMATS_RE:
        if pat.search(full):
            found_styles.append(pat.pattern)

    if len(found_styles) < 2:
        return ValidationResult(
            validator_name="pvalue_notation_consistency", findings=[]
        )

    total = sum(len(pat.findall(full)) for pat in _PVAL_FORMATS_RE)
    if total < _PVAL_MIN_TOTAL:
        return ValidationResult(
            validator_name="pvalue_notation_consistency", findings=[]
        )

    return ValidationResult(
        validator_name="pvalue_notation_consistency",
        findings=[
            Finding(
                code="inconsistent-pvalue-notation",
                severity="minor",
                message=(
                    f"Manuscript uses {len(found_styles)} different p-value notation "
                    "styles. Use a consistent format throughout "
                    "(e.g., always 'p < 0.05' in italics)."
                ),
                validator="pvalue_notation_consistency",
                location="manuscript",
                evidence=found_styles[:3],
            )
        ],
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
        validate_statistical_power(parsed, classification),
        validate_keywords_present(parsed),
        validate_overlong_sentences(parsed),
        validate_heading_capitalization_consistency(parsed),
        validate_research_question_addressed(parsed),
        validate_citations_in_abstract(parsed),
        validate_funding_statement(parsed, classification),
        validate_discussion_section_presence(parsed, classification),
        validate_pvalue_notation_consistency(parsed),
        validate_methods_section_presence(parsed, classification),
        validate_conclusion_section_presence(parsed),
        validate_participant_demographics(parsed, classification),
        validate_conflicting_acronym_definitions(parsed),
        validate_percentage_notation_consistency(parsed),
        validate_figure_label_consistency(parsed),
        validate_draft_title_markers(parsed),
        validate_study_period_reporting(parsed, classification),
        validate_scale_anchor_reporting(parsed, classification),
        validate_model_specification(parsed, classification),
        validate_effect_direction_reporting(parsed),
        validate_citation_format_consistency(parsed),
        validate_imputation_sensitivity(parsed, classification),
        validate_computational_environment(parsed, classification),
        validate_table_captions(parsed),
        validate_raw_data_description(parsed, classification),
        validate_multiple_outcomes_correction(parsed, classification),
        validate_replication_dataset(parsed, classification),
        validate_appendix_reference_consistency(parsed),
        validate_open_science_statement(parsed, classification),
        validate_cohort_attrition(parsed, classification),
        validate_blinding_procedure(parsed, classification),
        validate_floor_ceiling_effects(parsed, classification),
        validate_negative_result_framing(parsed),
        validate_abstract_results_consistency(parsed),
        validate_measurement_invariance(parsed, classification),
        validate_effect_size_confidence_intervals(parsed, classification),
        validate_preregistration_statement(parsed, classification),
        validate_cross_validation_reporting(parsed, classification),
        validate_sensitivity_analysis_reporting(parsed, classification),
        validate_regression_diagnostics(parsed, classification),
        validate_sample_representativeness(parsed, classification),
        validate_variable_operationalization(parsed, classification),
        validate_control_variable_justification(parsed, classification),
        validate_prospective_vs_retrospective(parsed, classification),
        validate_clinical_trial_consort(parsed, classification),
        validate_ecological_validity(parsed, classification),
        validate_media_source_citations(parsed),
        validate_competing_model_comparison(parsed, classification),
        validate_causal_language(parsed, classification),
        validate_missing_standard_errors(parsed, classification),
        validate_subjective_claim_hedging(parsed),
        validate_population_definition(parsed, classification),
        validate_pilot_study_claims(parsed),
        validate_exclusion_criteria_reporting(parsed, classification),
        validate_normal_distribution_assumption(parsed, classification),
        validate_figure_axes_labeling(parsed),
        validate_duplicate_reporting(parsed),
        validate_response_rate_reporting(parsed, classification),
        validate_longitudinal_attrition_bias(parsed, classification),
        validate_continuous_variable_dichotomization(parsed, classification),
        validate_outcome_measure_validation(parsed, classification),
        validate_outlier_handling_disclosure(parsed, classification),
        validate_main_effect_confidence_interval(parsed, classification),
        validate_covariate_justification(parsed, classification),
        validate_gender_sex_conflation(parsed, classification),
        validate_multicollinearity_reporting(parsed, classification),
        validate_control_group_description(parsed, classification),
        validate_heteroscedasticity_testing(parsed, classification),
        validate_interaction_effect_interpretation(parsed, classification),
        validate_post_hoc_framing(parsed, classification),
        validate_multiple_comparison_correction(parsed, classification),
        validate_publication_bias_statement(parsed, classification),
        validate_degrees_of_freedom_reporting(parsed, classification),
        validate_power_analysis_reporting(parsed, classification),
        validate_demographic_description(parsed, classification),
        validate_randomization_procedure(parsed, classification),
        validate_generalizability_caveat(parsed, classification),
        validate_software_version_reporting(parsed, classification),
        validate_ethics_approval_statement(parsed, classification),
        validate_prisma_reporting(parsed, classification),
        validate_mediation_analysis_transparency(parsed, classification),
        validate_latent_variable_model_fit(parsed, classification),
        validate_pilot_study_disclosure(parsed, classification),
        validate_autocorrelation_check(parsed, classification),
        validate_mixed_methods_integration(parsed, classification),
        validate_qualitative_rigor_reporting(parsed, classification),
        validate_subgroup_analysis_labelling(parsed, classification),
        validate_null_result_power_caveat(parsed, classification),
        validate_mean_sd_reporting(parsed, classification),
        validate_intervention_description(parsed, classification),
        validate_baseline_equivalence(parsed, classification),
        validate_likert_distribution_check(parsed, classification),
        validate_reproducibility_statement(parsed, classification),
        validate_missing_data_handling(parsed, classification),
        validate_coding_scheme_description(parsed, classification),
        validate_logistic_regression_assumptions(parsed, classification),
        validate_researcher_positionality(parsed, classification),
        validate_data_collection_recency(parsed, classification),
        validate_theoretical_framework_citation(parsed, classification),
        validate_survey_instrument_source(parsed, classification),
        validate_sampling_frame_description(parsed, classification),
        validate_one_tailed_test_justification(parsed, classification),
        validate_gratuitous_significance_language(parsed, classification),
        validate_unit_of_analysis_clarity(parsed, classification),
        validate_apriori_preregistration_statement(parsed, classification),
        validate_selective_literature_citation(parsed, classification),
        validate_participant_compensation_disclosure(parsed, classification),
        validate_observational_causal_language(parsed, classification),
        validate_acknowledgement_section(parsed, classification),
        validate_conflict_of_interest_statement(parsed, classification),
        validate_age_reporting_precision(parsed, classification),
        validate_statistical_software_version(parsed, classification),
        validate_warranted_sensitivity_analysis(parsed, classification),
    ]
    partial = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_claim_evidence_escalation(partial))
    partial2 = ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)
    results.append(validate_critical_escalation(partial2))
    return ValidationSuiteResult(validator_version=DEFAULT_VALIDATOR_VERSION, results=results)


# ---------------------------------------------------------------------------
# Phase 126 – Missing methods section in empirical papers
# ---------------------------------------------------------------------------

_METHODS_TITLES = frozenset(
    {
        "methods",
        "methodology",
        "experimental design",
        "experimental setup",
        "study design",
        "participants",
        "materials and methods",
        "methods and materials",
        "data collection",
        "data and methods",
    }
)


def validate_methods_section_presence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without a Methods section.

    Emits ``missing-methods-section`` (major) when an empirical paper
    has no Methods or Methodology section.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="methods_section_presence", findings=[]
        )

    if len(parsed.sections) < 2:
        return ValidationResult(
            validator_name="methods_section_presence", findings=[]
        )

    has_methods = any(
        s.title.lower() in _METHODS_TITLES for s in parsed.sections
    )
    if has_methods:
        return ValidationResult(
            validator_name="methods_section_presence", findings=[]
        )

    return ValidationResult(
        validator_name="methods_section_presence",
        findings=[
            Finding(
                code="missing-methods-section",
                severity="major",
                message=(
                    "Empirical paper has no Methods or Methodology section. "
                    "A clearly labeled Methods section is required for reproducibility."
                ),
                validator="methods_section_presence",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 127 – Missing conclusion section
# ---------------------------------------------------------------------------

_CONCLUSION_TITLES = frozenset(
    {
        "conclusion",
        "conclusions",
        "concluding remarks",
        "summary",
        "summary and conclusion",
        "summary and conclusions",
        "conclusion and future work",
        "conclusions and future work",
        "closing remarks",
    }
)


def validate_conclusion_section_presence(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag papers without a Conclusion or Summary section.

    Emits ``missing-conclusion-section`` (minor) when no conclusion or
    summary section is found (requires ≥ 3 sections total).
    """
    if len(parsed.sections) < 3:
        return ValidationResult(
            validator_name="conclusion_section_presence", findings=[]
        )

    has_conclusion = any(
        s.title.lower() in _CONCLUSION_TITLES for s in parsed.sections
    )
    if has_conclusion:
        return ValidationResult(
            validator_name="conclusion_section_presence", findings=[]
        )

    return ValidationResult(
        validator_name="conclusion_section_presence",
        findings=[
            Finding(
                code="missing-conclusion-section",
                severity="minor",
                message=(
                    "No Conclusion or Summary section found. "
                    "Papers should include a conclusion summarizing key contributions "
                    "and implications."
                ),
                validator="conclusion_section_presence",
                location="manuscript",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 128 – Participant demographics reporting
# ---------------------------------------------------------------------------

_DEMOGRAPHICS_PAPER_TYPES = frozenset(
    {
        "empirical_paper",
        "clinical_trial_report",
    }
)
_DEMOGRAPHICS_RE = re.compile(
    r"\b(mean age|average age|age range|age:? \d|age \(years\)|"
    r"female|male|women|men|gender|sex:|"
    r"n = \d|sample of \d|N = \d|participants were|"
    r"\d+ (participants|subjects|students|adults|children|patients)|"
    r"demographics|demographic characteristics)\b",
    re.IGNORECASE,
)
_DEMOGRAPHICS_PARTICIPANT_RE = re.compile(
    r"\b(participants|subjects|respondents|sample|recruited|enrolled)\b",
    re.IGNORECASE,
)


def validate_participant_demographics(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers reporting participants without demographic details.

    Emits ``missing-participant-demographics`` (moderate) when a paper
    mentions participants but provides no demographic information.
    """
    if classification.paper_type not in _DEMOGRAPHICS_PAPER_TYPES:
        return ValidationResult(
            validator_name="participant_demographics", findings=[]
        )

    methods_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in _METHODS_TITLES
    )
    if not methods_body:
        return ValidationResult(
            validator_name="participant_demographics", findings=[]
        )

    if not _DEMOGRAPHICS_PARTICIPANT_RE.search(methods_body):
        return ValidationResult(
            validator_name="participant_demographics", findings=[]
        )

    if _DEMOGRAPHICS_RE.search(methods_body):
        return ValidationResult(
            validator_name="participant_demographics", findings=[]
        )

    return ValidationResult(
        validator_name="participant_demographics",
        findings=[
            Finding(
                code="missing-participant-demographics",
                severity="moderate",
                message=(
                    "Methods mentions participants but provides no demographic details "
                    "(age, gender, sample size). Report basic demographics to enable "
                    "generalizability assessment."
                ),
                validator="participant_demographics",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 129 – Conflicting acronym definitions
# ---------------------------------------------------------------------------

# Regex for conflicting acronym detection (separate from _ACRONYM_DEF_RE above)
_CONFLICT_ACRONYM_RE = re.compile(
    r"([A-Z]{2,6})\s*\(([^)]{5,60})\)|"  # ACRONYM (expansion)
    r"([^(]{5,60})\s*\(([A-Z]{2,6})\)",   # expansion (ACRONYM)
    re.MULTILINE,
)


def validate_conflicting_acronym_definitions(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag acronyms defined more than once with different expansions.

    Emits ``inconsistent-acronym-definition`` (minor) when the same acronym
    appears with two or more distinct expansions in the manuscript.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="conflicting_acronym_definitions", findings=[]
        )

    # Collect all (acronym, expansion) pairs
    acronym_expansions: dict[str, set[str]] = {}
    for match in _CONFLICT_ACRONYM_RE.finditer(full):
        if match.group(1) and match.group(2):
            acr = match.group(1).upper()
            exp = match.group(2).strip().lower()
        elif match.group(3) and match.group(4):
            acr = match.group(4).upper()
            exp = match.group(3).strip().lower()
        else:
            continue
        if acr not in acronym_expansions:
            acronym_expansions[acr] = set()
        acronym_expansions[acr].add(exp)

    conflicts = {
        acr: exps
        for acr, exps in acronym_expansions.items()
        if len(exps) > 1
    }
    if not conflicts:
        return ValidationResult(
            validator_name="conflicting_acronym_definitions", findings=[]
        )

    evidence = [
        f"{acr}: {' | '.join(sorted(exps))}"
        for acr, exps in list(conflicts.items())[:3]
    ]
    return ValidationResult(
        validator_name="conflicting_acronym_definitions",
        findings=[
            Finding(
                code="inconsistent-acronym-definition",
                severity="minor",
                message=(
                    f"Found {len(conflicts)} acronym(s) defined with inconsistent "
                    "expansions. Each acronym should have exactly one definition."
                ),
                validator="conflicting_acronym_definitions",
                location="manuscript",
                evidence=evidence,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 130 – Uppercase percentage in Results
# ---------------------------------------------------------------------------

_PERCENT_FORMATS_RE = re.compile(
    r"(\d+\s*%|\d+\s*percent|\d+\s*pct\.?|(\d+)\s*per cent)",
    re.IGNORECASE,
)
_PERCENT_MIN_OCCURRENCES = 4


def validate_percentage_notation_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts using mixed percentage notation formats.

    Emits ``inconsistent-percentage-notation`` (minor) when a manuscript
    mixes '50%', '50 percent', and '50 per cent' in Results or Methods.
    """
    target_sections = [
        s for s in parsed.sections
        if s.title.lower() in {"results", "methods", "methodology", "analysis"}
    ]
    if not target_sections:
        return ValidationResult(
            validator_name="percentage_notation_consistency", findings=[]
        )

    combined = " ".join(s.body for s in target_sections)
    matches = _PERCENT_FORMATS_RE.findall(combined)
    if len(matches) < _PERCENT_MIN_OCCURRENCES:
        return ValidationResult(
            validator_name="percentage_notation_consistency", findings=[]
        )

    # Classify: symbol (%), 'percent', 'per cent', 'pct'
    has_symbol = bool(re.search(r"\d+\s*%", combined))
    has_word = bool(re.search(r"\d+\s*percent\b", combined, re.IGNORECASE))
    has_per_cent = bool(re.search(r"\d+\s*per cent\b", combined, re.IGNORECASE))

    styles = sum([has_symbol, has_word, has_per_cent])
    if styles < 2:
        return ValidationResult(
            validator_name="percentage_notation_consistency", findings=[]
        )

    return ValidationResult(
        validator_name="percentage_notation_consistency",
        findings=[
            Finding(
                code="inconsistent-percentage-notation",
                severity="minor",
                message=(
                    f"Results/Methods uses {styles} different percentage notation "
                    "formats ('%', 'percent', 'per cent'). "
                    "Use a consistent format throughout."
                ),
                validator="percentage_notation_consistency",
                location="Results/Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 131 – Figure/table label format consistency
# ---------------------------------------------------------------------------

_FIG_LABEL_RE = re.compile(
    r"\b(Fig\.|Figure|fig\.|figure)\s+\d+\b",
    re.IGNORECASE,
)
_FIG_FORMATS = {
    "Fig.": re.compile(r"\bFig\.\s+\d+"),
    "Figure": re.compile(r"\bFigure\s+\d+"),
    "fig.": re.compile(r"\bfig\.\s+\d+"),
    "figure": re.compile(r"\bfigure\s+\d+"),
}
_FIG_MIN_REFS = 3


def validate_figure_label_consistency(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts mixing figure label styles.

    Emits ``inconsistent-figure-labels`` (minor) when the manuscript uses
    multiple different figure reference styles (e.g., 'Fig. 1', 'Figure 1')
    with >= ``_FIG_MIN_REFS`` total figure references.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(validator_name="figure_label_consistency", findings=[])

    total = len(_FIG_LABEL_RE.findall(full))
    if total < _FIG_MIN_REFS:
        return ValidationResult(validator_name="figure_label_consistency", findings=[])

    found_styles = [name for name, pat in _FIG_FORMATS.items() if pat.search(full)]
    if len(found_styles) < 2:
        return ValidationResult(validator_name="figure_label_consistency", findings=[])

    return ValidationResult(
        validator_name="figure_label_consistency",
        findings=[
            Finding(
                code="inconsistent-figure-labels",
                severity="minor",
                message=(
                    f"Manuscript uses {len(found_styles)} different figure label "
                    "styles. Use a consistent format (e.g., always 'Figure 1' "
                    "or always 'Fig. 1')."
                ),
                validator="figure_label_consistency",
                location="manuscript",
                evidence=found_styles[:4],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 132 – Draft title markers
# ---------------------------------------------------------------------------

_DRAFT_TITLE_RE = re.compile(
    r"(\bTBD\b|\bTODO\b|\bFIXME\b|\bDRAFT\b|\bPLACEHOLDER\b|"
    r"\[Title\]|\[Insert Title\]|"
    r"\bUntitled\b|title here|your title)",
    re.IGNORECASE,
)


def validate_draft_title_markers(parsed: ParsedManuscript) -> ValidationResult:
    """Flag titles containing draft/placeholder markers.

    Emits ``draft-title-marker`` (major) when the manuscript title contains
    obvious draft markers like 'TBD', 'DRAFT', or '[Title]'.
    """
    title = parsed.title or ""
    if not title:
        return ValidationResult(validator_name="draft_title_markers", findings=[])

    if _DRAFT_TITLE_RE.search(title):
        return ValidationResult(
            validator_name="draft_title_markers",
            findings=[
                Finding(
                    code="draft-title-marker",
                    severity="major",
                    message=(
                        f"Title appears to be a placeholder: '{title}'. "
                        "Replace with the actual manuscript title before submission."
                    ),
                    validator="draft_title_markers",
                    location="title",
                    evidence=[title],
                )
            ],
        )

    return ValidationResult(validator_name="draft_title_markers", findings=[])


# ---------------------------------------------------------------------------
# Phase 133 – Missing study period in empirical papers
# ---------------------------------------------------------------------------

_STUDY_PERIOD_RE = re.compile(
    r"\b(data (were|was) collected (in|from|between|during)|"
    r"study (period|was conducted)|"
    r"between (January|February|March|April|May|June|July|August|September|"
    r"October|November|December|\d{4}) and|"
    r"from \d{4} to \d{4}|"
    r"in \d{4}[–-]\d{4}|"
    r"\d{4}[–-]\d{4} (cohort|sample|survey|dataset)|"
    r"recruited (in|between|from|during) \d{4}|"
    r"baseline (assessment|survey|data) in \d{4})\b",
    re.IGNORECASE,
)


def validate_study_period_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers without a stated study period.

    Emits ``missing-study-period`` (moderate) when an empirical paper's
    Methods section mentions participants/data collection but provides no
    study period (e.g., 'data collected from 2019 to 2021').
    """
    if classification.paper_type not in _DEMOGRAPHICS_PAPER_TYPES:
        return ValidationResult(
            validator_name="study_period_reporting", findings=[]
        )

    methods_body = " ".join(
        s.body for s in parsed.sections if s.title.lower() in _METHODS_TITLES
    )
    if not methods_body:
        return ValidationResult(
            validator_name="study_period_reporting", findings=[]
        )

    if not _DEMOGRAPHICS_PARTICIPANT_RE.search(methods_body):
        return ValidationResult(
            validator_name="study_period_reporting", findings=[]
        )

    if _STUDY_PERIOD_RE.search(methods_body):
        return ValidationResult(
            validator_name="study_period_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="study_period_reporting",
        findings=[
            Finding(
                code="missing-study-period",
                severity="moderate",
                message=(
                    "Empirical paper mentions participants but provides no study "
                    "period (e.g., 'data collected from 2019 to 2021'). "
                    "Report when data were collected for reproducibility."
                ),
                validator="study_period_reporting",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 134 – Response scale anchor labels
# ---------------------------------------------------------------------------

_SCALE_ANCHOR_RE = re.compile(
    r"\b(\d+|[1-9])\s*[-–]\s*(\d+|point)\s*(Likert|scale|response|rating)\b|"
    r"\b(Likert|rating|response|ordinal)\s*(scale|format|item)\b",
    re.IGNORECASE,
)
_SCALE_ENDPOINT_RE = re.compile(
    r"\b(strongly (agree|disagree)|not at all|very (much|often|satisfied|likely)|"
    r"extremely (satisfied|likely|important)|"
    r"anchor(ed|s)|end(point)?s? (were|are|included|labeled)|"
    r"ranged from .{5,40} to .{5,40})\b",
    re.IGNORECASE,
)


def validate_scale_anchor_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag survey/scale instruments without anchor label description.

    Emits ``missing-scale-anchors`` (minor) when a paper describes a Likert or
    rating scale but provides no endpoint/anchor labels.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="scale_anchor_reporting", findings=[]
        )

    methods_body = " ".join(
        s.body for s in parsed.sections if s.title.lower() in _METHODS_TITLES
    )
    if not methods_body:
        return ValidationResult(
            validator_name="scale_anchor_reporting", findings=[]
        )

    if not _SCALE_ANCHOR_RE.search(methods_body):
        return ValidationResult(
            validator_name="scale_anchor_reporting", findings=[]
        )

    if _SCALE_ENDPOINT_RE.search(methods_body):
        return ValidationResult(
            validator_name="scale_anchor_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="scale_anchor_reporting",
        findings=[
            Finding(
                code="missing-scale-anchors",
                severity="minor",
                message=(
                    "Methods describes a Likert/rating scale but provides no "
                    "anchor/endpoint labels. "
                    "Describe scale endpoints (e.g., '1 = strongly disagree, "
                    "5 = strongly agree')."
                ),
                validator="scale_anchor_reporting",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 135 – Missing model specification
# ---------------------------------------------------------------------------

_MODEL_SPEC_TRIGGER_RE = re.compile(
    r"\b(logistic regression|linear regression|multilevel model|"
    r"mixed.effect(s)? model|hierarchical (regression|model)|"
    r"structural equation model|path model|latent class|"
    r"Poisson regression|Cox (proportional hazard|regression))\b",
    re.IGNORECASE,
)
_MODEL_SPEC_DETAIL_RE = re.compile(
    r"\b(covariates?|predictors?|independent variable|"
    r"dependent variable|outcome variable|control variable|"
    r"fixed effect|random effect|model formula|"
    r"specified (as|with|using)|included in the model)\b",
    re.IGNORECASE,
)


def validate_model_specification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag papers describing regression/SEM models without specifying predictors.

    Emits ``missing-model-specification`` (moderate) when Methods describes a
    complex statistical model but provides no model specification (covariates,
    predictors, outcome variables).
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="model_specification", findings=[]
        )

    methods_body = " ".join(
        s.body for s in parsed.sections if s.title.lower() in _METHODS_TITLES
    )
    if not methods_body:
        return ValidationResult(
            validator_name="model_specification", findings=[]
        )

    if not _MODEL_SPEC_TRIGGER_RE.search(methods_body):
        return ValidationResult(
            validator_name="model_specification", findings=[]
        )

    if _MODEL_SPEC_DETAIL_RE.search(methods_body):
        return ValidationResult(
            validator_name="model_specification", findings=[]
        )

    return ValidationResult(
        validator_name="model_specification",
        findings=[
            Finding(
                code="missing-model-specification",
                severity="moderate",
                message=(
                    "Methods describes a regression/SEM model but provides "
                    "no specification of predictors, covariates, or outcome variables. "
                    "State the complete model formula or variable list."
                ),
                validator="model_specification",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 136 – Effect direction in significant results
# ---------------------------------------------------------------------------

_SIGNIFICANT_RESULT_RE = re.compile(
    r"\b(significant(ly)?|p\s*[<=>]\s*0\.\d+|F\(\d+,\s*\d+\)\s*=|"
    r"t\(\d+\)\s*=|chi.square|χ²|effect was (significant|found))\b",
    re.IGNORECASE,
)
_EFFECT_DIRECTION_RE = re.compile(
    r"\b(higher|lower|greater|less|more|fewer|increased|decreased|"
    r"improved|worse|better|faster|slower|larger|smaller|"
    r"positive(ly)?|negative(ly)?|outperformed|exceeded|"
    r"group [AB] (had|showed|scored|performed)|"
    r"compared to|relative to|versus)\b",
    re.IGNORECASE,
)
_MIN_SIGNIFICANT_MENTIONS = 2


def validate_effect_direction_reporting(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag Results sections with significant findings but no direction statements.

    Emits ``missing-effect-direction`` (moderate) when the Results section
    reports significance (p-values, significant effects) but never states
    the direction of the effect.
    """
    results_body = " ".join(
        s.body
        for s in parsed.sections
        if s.title.lower() in {"results", "findings", "analysis"}
    )
    if not results_body:
        return ValidationResult(
            validator_name="effect_direction_reporting", findings=[]
        )

    sig_matches = _SIGNIFICANT_RESULT_RE.findall(results_body)
    if len(sig_matches) < _MIN_SIGNIFICANT_MENTIONS:
        return ValidationResult(
            validator_name="effect_direction_reporting", findings=[]
        )

    if _EFFECT_DIRECTION_RE.search(results_body):
        return ValidationResult(
            validator_name="effect_direction_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="effect_direction_reporting",
        findings=[
            Finding(
                code="missing-effect-direction",
                severity="moderate",
                message=(
                    f"Results section reports {len(sig_matches)} significant finding(s) "
                    "but contains no direction statements (higher/lower/increased). "
                    "State which group performed better or the direction of each effect."
                ),
                validator="effect_direction_reporting",
                location="Results",
                evidence=[str(m) for m in sig_matches[:3]],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 137 – Mixed citation format (numeric vs. author-year)
# ---------------------------------------------------------------------------

_FORMAT_NUMERIC_CITE_RE = re.compile(r"\[\d+(?:,\s*\d+)*\]")
_FORMAT_AUTHOR_YEAR_CITE_RE = re.compile(
    r"\b[A-Z][a-z]+\s+(?:et al\.?)?\s*\(\d{4}\)|\(\w[^)]+,\s*\d{4}\w?\)"
)
_FORMAT_CITE_MIN_REFS = 4


def validate_citation_format_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts mixing numeric and author-year citation styles.

    Emits ``mixed-citation-format`` (minor) when the manuscript uses both
    '[1]' numeric and '(Smith, 2020)' author-year citation styles, with
    >= ``_FORMAT_CITE_MIN_REFS`` total citations.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="citation_format_consistency", findings=[]
        )

    numeric_count = len(_FORMAT_NUMERIC_CITE_RE.findall(full))
    author_year_count = len(_FORMAT_AUTHOR_YEAR_CITE_RE.findall(full))
    total = numeric_count + author_year_count

    if total < _FORMAT_CITE_MIN_REFS:
        return ValidationResult(
            validator_name="citation_format_consistency", findings=[]
        )

    if numeric_count > 0 and author_year_count > 0:
        return ValidationResult(
            validator_name="citation_format_consistency",
            findings=[
                Finding(
                    code="mixed-citation-format",
                    severity="minor",
                    message=(
                        f"Manuscript uses both numeric [{numeric_count}] and "
                        f"author-year [{author_year_count}] citation styles. "
                        "Use a consistent citation format throughout."
                    ),
                    validator="citation_format_consistency",
                    location="manuscript",
                    evidence=[
                        f"numeric: {numeric_count}",
                        f"author-year: {author_year_count}",
                    ],
                )
            ],
        )

    return ValidationResult(
        validator_name="citation_format_consistency", findings=[]
    )


# ---------------------------------------------------------------------------
# Phase 138 – Missing sensitivity analysis for multiple imputation
# ---------------------------------------------------------------------------

_IMPUTATION_RE = re.compile(
    r"\b(multiple imputation|MICE|imputed (data|values|missing)|"
    r"imputation method|missing at random|MAR assumption|"
    r"missing data (were|was) handled|listwise deletion avoided)\b",
    re.IGNORECASE,
)
_SENSITIVITY_ANALYSIS_RE = re.compile(
    r"\b(sensitivity analysis|robustness check|complete.case analysis|"
    r"listwise deletion comparison|pattern mixture model|"
    r"imputation sensitivity|MCAR test)\b",
    re.IGNORECASE,
)


def validate_imputation_sensitivity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag papers using multiple imputation without sensitivity analysis.

    Emits ``missing-imputation-sensitivity`` (moderate) when Methods describes
    multiple imputation but provides no sensitivity analysis or robustness check.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="imputation_sensitivity", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(validator_name="imputation_sensitivity", findings=[])

    if not _IMPUTATION_RE.search(full):
        return ValidationResult(validator_name="imputation_sensitivity", findings=[])

    if _SENSITIVITY_ANALYSIS_RE.search(full):
        return ValidationResult(validator_name="imputation_sensitivity", findings=[])

    return ValidationResult(
        validator_name="imputation_sensitivity",
        findings=[
            Finding(
                code="missing-imputation-sensitivity",
                severity="moderate",
                message=(
                    "Methods describes multiple imputation but provides no "
                    "sensitivity analysis. "
                    "Report complete-case comparison or other robustness check."
                ),
                validator="imputation_sensitivity",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 139 – Missing computational environment details
# ---------------------------------------------------------------------------

_COMPUTATION_RE = re.compile(
    r"\b(simulation(s)?|Monte Carlo|bootstrap|permutation test|"
    r"cross.validation|grid search|hyperparameter|"
    r"neural network|deep learning|training (the )?model|"
    r"algorithm (was )?implemented|code (is )?available)\b",
    re.IGNORECASE,
)
_COMPUTATION_ENV_RE = re.compile(
    r"\b(Python|R\s+\d\.\d|MATLAB|Julia|Stata|SAS|SPSS|"
    r"version \d+\.\d+|GPU|CUDA|CPU|hardware|computing cluster|"
    r"runtime|wall.?clock|RAM)\b",
    re.IGNORECASE,
)


def validate_computational_environment(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag papers with computational methods lacking environment details.

    Emits ``missing-computational-environment`` (moderate) when Methods
    describes simulations, ML, or complex computation but provides no
    language/version/hardware details.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="computational_environment", findings=[]
        )

    methods_body = " ".join(
        s.body for s in parsed.sections if s.title.lower() in _METHODS_TITLES
    )
    if not methods_body:
        return ValidationResult(
            validator_name="computational_environment", findings=[]
        )

    if not _COMPUTATION_RE.search(methods_body):
        return ValidationResult(
            validator_name="computational_environment", findings=[]
        )

    if _COMPUTATION_ENV_RE.search(methods_body):
        return ValidationResult(
            validator_name="computational_environment", findings=[]
        )

    return ValidationResult(
        validator_name="computational_environment",
        findings=[
            Finding(
                code="missing-computational-environment",
                severity="moderate",
                message=(
                    "Methods describes computational procedures but provides no "
                    "programming language, version, or hardware details. "
                    "Report the full computational environment for reproducibility."
                ),
                validator="computational_environment",
                location="Methods",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 140 – Table caption completeness
# ---------------------------------------------------------------------------

_TABLE_DEF_RE = re.compile(
    r"\b(Table\s+\d+|Tab\.\s+\d+)\b",
    re.IGNORECASE,
)
_TABLE_CAPTION_RE = re.compile(
    r"(^Table\s+\d+\s*[.:|]\s*[A-Z].{10,}|"
    r"\\caption\{[^}]{10,}\})",
    re.IGNORECASE | re.MULTILINE,
)
_TABLE_MIN_REFS = 2


def validate_table_captions(parsed: ParsedManuscript) -> ValidationResult:
    """Flag manuscripts with table references but missing/very short captions.

    Emits ``missing-table-captions`` (minor) when tables are referenced in the
    text but no table captions or titles are present.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(validator_name="table_captions", findings=[])

    table_refs = _TABLE_DEF_RE.findall(full)
    if len(table_refs) < _TABLE_MIN_REFS:
        return ValidationResult(validator_name="table_captions", findings=[])

    if _TABLE_CAPTION_RE.search(full):
        return ValidationResult(validator_name="table_captions", findings=[])

    return ValidationResult(
        validator_name="table_captions",
        findings=[
            Finding(
                code="missing-table-captions",
                severity="minor",
                message=(
                    f"Manuscript references {len(table_refs)} table(s) but no table "
                    "captions or titles were found. "
                    "Every table must have a descriptive caption."
                ),
                validator="table_captions",
                location="manuscript",
                evidence=[str(r) for r in table_refs[:3]],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 141 – Raw data description completeness
# ---------------------------------------------------------------------------

_DATA_MENTION_RE = re.compile(
    r"\b(?:dataset|data\s+set|database|survey\s+data|cohort\s+data|"
    r"secondary\s+data|observational\s+data|administrative\s+data)\b",
    re.IGNORECASE,
)
_DATA_FORMAT_RE = re.compile(
    r"\b(?:csv|xlsx?|json|hdf5?|parquet|stata|spss|sas|netcdf|rdata|"
    r"\.csv|\.xlsx?|\.json|\.hdf5?|\.parquet|open\s+access|"
    r"available\s+at|doi\s*:|zenodo|figshare|osf\.io|dataverse)\b",
    re.IGNORECASE,
)
_DATA_MIN_MENTIONS = 2


def validate_raw_data_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts that mention datasets without format/source.

    Emits ``missing-raw-data-description`` (moderate) when Methods references
    datasets >= ``_DATA_MIN_MENTIONS`` times but includes no file-format or
    repository/source details.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="raw_data_description", findings=[]
        )
    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="raw_data_description", findings=[]
        )

    data_mentions = _DATA_MENTION_RE.findall(methods_text)
    if len(data_mentions) < _DATA_MIN_MENTIONS:
        return ValidationResult(
            validator_name="raw_data_description", findings=[]
        )

    if _DATA_FORMAT_RE.search(methods_text):
        return ValidationResult(
            validator_name="raw_data_description", findings=[]
        )

    return ValidationResult(
        validator_name="raw_data_description",
        findings=[
            Finding(
                code="missing-raw-data-description",
                severity="moderate",
                message=(
                    f"Methods section references datasets {len(data_mentions)} "
                    "time(s) but includes no file format, repository, or source "
                    "details for the raw data. "
                    "Specify data format and access location."
                ),
                validator="raw_data_description",
                location="Methods",
                evidence=list(dict.fromkeys(data_mentions[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 142 – Multiple outcomes / multiple comparisons correction
# ---------------------------------------------------------------------------

_OUTCOME_VAR_RE = re.compile(
    r"\b(?:outcome|dependent\s+variable|endpoint|measure|construct|subscale)\b",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"\b(?:bonferroni|benjamini.{0,10}hochberg|false\s+discovery\s+rate|fdr|"
    r"holm|tukey|familywise|family.wise|adjusted\s+p.valu|correction\s+for\s+"
    r"multiple)\b",
    re.IGNORECASE,
)
_OUTCOME_MIN_MENTIONS = 4


def validate_multiple_outcomes_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts with multiple outcomes and no correction.

    Emits ``missing-multiple-outcomes-correction`` (moderate) when Methods /
    Results mentions >= ``_OUTCOME_MIN_MENTIONS`` outcome variables but no
    multiple-comparisons correction is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="multiple_outcomes_correction", findings=[]
        )
    relevant_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title
        and any(
            k in s.title.lower() for k in ("method", "result", "statistic", "analys")
        )
    )
    if not relevant_text:
        return ValidationResult(
            validator_name="multiple_outcomes_correction", findings=[]
        )

    outcome_mentions = _OUTCOME_VAR_RE.findall(relevant_text)
    if len(outcome_mentions) < _OUTCOME_MIN_MENTIONS:
        return ValidationResult(
            validator_name="multiple_outcomes_correction", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _CORRECTION_RE.search(full):
        return ValidationResult(
            validator_name="multiple_outcomes_correction", findings=[]
        )

    return ValidationResult(
        validator_name="multiple_outcomes_correction",
        findings=[
            Finding(
                code="missing-multiple-outcomes-correction",
                severity="moderate",
                message=(
                    f"Manuscript reports {len(outcome_mentions)} outcome/dependent-variable "
                    "references but does not mention any multiple-comparisons correction "
                    "(Bonferroni, FDR, Holm, etc.). "
                    "Report or justify the correction strategy."
                ),
                validator="multiple_outcomes_correction",
                location="Methods/Results",
                evidence=list(dict.fromkeys(outcome_mentions[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 143 – Replication / validation dataset reporting
# ---------------------------------------------------------------------------

_REPLICATION_RE = re.compile(
    r"\b(?:replicat(?:ion|ed)|validation\s+(?:dataset|cohort|sample|set)|"
    r"hold.out|holdout|external\s+validation|test\s+(?:set|cohort|sample)|"
    r"independent\s+(?:dataset|cohort|sample|replication))\b",
    re.IGNORECASE,
)


def validate_replication_dataset(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts lacking replication or validation dataset mention.

    Emits ``missing-replication-dataset`` (moderate) when the paper is empirical
    and the full text contains no mention of a replication or external validation
    dataset/cohort.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="replication_dataset", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="replication_dataset", findings=[]
        )

    if _REPLICATION_RE.search(full):
        return ValidationResult(
            validator_name="replication_dataset", findings=[]
        )

    return ValidationResult(
        validator_name="replication_dataset",
        findings=[
            Finding(
                code="missing-replication-dataset",
                severity="moderate",
                message=(
                    "Empirical manuscript does not mention a replication cohort, "
                    "validation dataset, hold-out set, or external validation. "
                    "Discuss generalizability and replication."
                ),
                validator="replication_dataset",
                location="manuscript",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 144 – Appendix reference consistency
# ---------------------------------------------------------------------------

_APPENDIX_REF_RE = re.compile(
    r"\b(?:appendix|supplementary\s+materials?|supplemental\s+materials?|"
    r"online\s+supplement|see\s+appendix|appendix\s+[A-Z\d])\b",
    re.IGNORECASE,
)
_APPENDIX_SECTION_RE = re.compile(
    r"(?:^|\n)\s*(?:appendix|supplementary\s+materials?|supplemental\s+materials?)"
    r"\s*(?:[A-Z\d:]|\n|$)",
    re.IGNORECASE,
)


def validate_appendix_reference_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts referencing an appendix that is not present.

    Emits ``missing-appendix-section`` (minor) when the text mentions an
    appendix or supplementary materials but no appendix heading is found.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="appendix_reference_consistency", findings=[]
        )

    ref_matches = _APPENDIX_REF_RE.findall(full)
    if not ref_matches:
        return ValidationResult(
            validator_name="appendix_reference_consistency", findings=[]
        )

    section_headings = [s.title or "" for s in parsed.sections]
    has_appendix_section = any(
        re.search(r"\bappendix\b|\bsupplementary\b|\bsupplemental\b", h, re.IGNORECASE)
        for h in section_headings
    )
    if has_appendix_section:
        return ValidationResult(
            validator_name="appendix_reference_consistency", findings=[]
        )

    if _APPENDIX_SECTION_RE.search(full):
        return ValidationResult(
            validator_name="appendix_reference_consistency", findings=[]
        )

    return ValidationResult(
        validator_name="appendix_reference_consistency",
        findings=[
            Finding(
                code="missing-appendix-section",
                severity="minor",
                message=(
                    f"Manuscript references 'Appendix' or 'Supplementary Materials' "
                    f"{len(ref_matches)} time(s) but no appendix section heading is present. "
                    "Include the appendix or remove dangling references."
                ),
                validator="appendix_reference_consistency",
                location="manuscript",
                evidence=list(dict.fromkeys(ref_matches[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 145 – Open science / data availability statement
# ---------------------------------------------------------------------------

_OPEN_SCIENCE_RE = re.compile(
    r"\b(?:data\s+availability|data\s+access(?:ibility)?|"
    r"code\s+availability|materials?\s+availability|"
    r"open\s+(?:data|code|science|access)|"
    r"available\s+(?:on\s+request|upon\s+request|at\s+https?:|from\s+the\s+authors?)|"
    r"data\s+sharing|github\.com|zenodo|osf\.io|figshare|dryad)\b",
    re.IGNORECASE,
)


def validate_open_science_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts lacking a data/code availability statement.

    Emits ``missing-open-science-statement`` (minor) when no data-availability
    or code-availability language is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="open_science_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="open_science_statement", findings=[]
        )

    if _OPEN_SCIENCE_RE.search(full):
        return ValidationResult(
            validator_name="open_science_statement", findings=[]
        )

    return ValidationResult(
        validator_name="open_science_statement",
        findings=[
            Finding(
                code="missing-open-science-statement",
                severity="minor",
                message=(
                    "Empirical manuscript lacks a data availability or code availability "
                    "statement. Add a statement explaining whether data/code are available "
                    "and how to access them."
                ),
                validator="open_science_statement",
                location="manuscript",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 146 – Cohort attrition / dropout reporting
# ---------------------------------------------------------------------------

_LONGITUDINAL_RE = re.compile(
    r"\b(?:longitudinal|follow.?up|cohort\s+study|panel\s+study|"
    r"prospective\s+study|repeated\s+measures|wave\s+\d|time\s+point\s+\d|"
    r"baseline\s+and\s+follow.?up)\b",
    re.IGNORECASE,
)
_ATTRITION_RE = re.compile(
    r"\b(?:attrition|dropout|drop.out|lost\s+to\s+follow.?up|"
    r"missing\s+(?:data\s+due|participants\s+due)|"
    r"(?:\d+|[a-z]+)\s+(?:participants?|subjects?)\s+(?:withdrew|dropped|"
    r"were\s+lost|did\s+not\s+complete))\b",
    re.IGNORECASE,
)


def validate_cohort_attrition(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal empirical manuscripts missing attrition reporting.

    Emits ``missing-attrition-reporting`` (moderate) when a longitudinal
    study is detected but no dropout or attrition information is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="cohort_attrition", findings=[])

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(validator_name="cohort_attrition", findings=[])

    if not _LONGITUDINAL_RE.search(full):
        return ValidationResult(validator_name="cohort_attrition", findings=[])

    if _ATTRITION_RE.search(full):
        return ValidationResult(validator_name="cohort_attrition", findings=[])

    return ValidationResult(
        validator_name="cohort_attrition",
        findings=[
            Finding(
                code="missing-attrition-reporting",
                severity="moderate",
                message=(
                    "Longitudinal study detected but no attrition or dropout "
                    "information was reported. "
                    "Report the number and reasons for participant dropout."
                ),
                validator="cohort_attrition",
                location="manuscript",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 147 – Blinding procedure reporting
# ---------------------------------------------------------------------------

_INTERVENTION_RE = re.compile(
    r"\b(?:randomized?\s+(?:controlled\s+)?trial|RCT|intervention\s+study|"
    r"treatment\s+group|control\s+group|placebo|experimental\s+condition|"
    r"between.?subjects?\s+design|within.?subjects?\s+design)\b",
    re.IGNORECASE,
)
_BLINDING_RE = re.compile(
    r"\b(?:blind(?:ed|ing)?|double.blind|single.blind|"
    r"masked|unblinded|open.label|assessors?\s+(?:were|blinded))\b",
    re.IGNORECASE,
)


def validate_blinding_procedure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag intervention/RCT manuscripts without blinding description.

    Emits ``missing-blinding-procedure`` (moderate) when a controlled trial or
    intervention design is detected but no blinding procedure is described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="blinding_procedure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="blinding_procedure", findings=[]
        )

    if not _INTERVENTION_RE.search(full):
        return ValidationResult(
            validator_name="blinding_procedure", findings=[]
        )

    if _BLINDING_RE.search(full):
        return ValidationResult(
            validator_name="blinding_procedure", findings=[]
        )

    return ValidationResult(
        validator_name="blinding_procedure",
        findings=[
            Finding(
                code="missing-blinding-procedure",
                severity="moderate",
                message=(
                    "Intervention or controlled-trial design detected but no blinding "
                    "procedure is described. "
                    "Specify whether participants, assessors, or analysts were blinded."
                ),
                validator="blinding_procedure",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 148 – Floor/ceiling effect reporting
# ---------------------------------------------------------------------------

_SCALE_MEASURE_RE = re.compile(
    r"\b(?:Likert|questionnaire|scale|psychometric|instrument|inventory|"
    r"self.report|rating\s+scale|survey\s+instrument)\b",
    re.IGNORECASE,
)
_FLOOR_CEILING_RE = re.compile(
    r"\b(?:floor\s+effects?|ceiling\s+effects?|floor/ceiling|"
    r"maximum\s+possible\s+score|minimum\s+possible\s+score|"
    r"skew(?:ed|ness)?\s+(?:toward|near)\s+(?:maximum|minimum|ceiling|floor))\b",
    re.IGNORECASE,
)


def validate_floor_ceiling_effects(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag psychometric studies without floor/ceiling effects discussion.

    Emits ``missing-floor-ceiling-discussion`` (minor) when scale/questionnaire
    measures are used but floor/ceiling effects are not addressed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="floor_ceiling_effects", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="floor_ceiling_effects", findings=[]
        )

    scale_matches = _SCALE_MEASURE_RE.findall(full)
    if len(scale_matches) < 3:
        return ValidationResult(
            validator_name="floor_ceiling_effects", findings=[]
        )

    if _FLOOR_CEILING_RE.search(full):
        return ValidationResult(
            validator_name="floor_ceiling_effects", findings=[]
        )

    return ValidationResult(
        validator_name="floor_ceiling_effects",
        findings=[
            Finding(
                code="missing-floor-ceiling-discussion",
                severity="minor",
                message=(
                    "Scale/questionnaire measures detected but floor and ceiling "
                    "effects are not discussed. "
                    "Address whether floor or ceiling effects may affect score distributions."
                ),
                validator="floor_ceiling_effects",
                location="Results/Discussion",
                evidence=list(dict.fromkeys(scale_matches[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 149 – Negative result / non-significant result framing
# ---------------------------------------------------------------------------

_NON_SIG_RE = re.compile(
    r"\b(?:not\s+significant|non.significant|did\s+not\s+(?:reach|achieve)\s+"
    r"significance|no\s+significant\s+(?:difference|effect|association|"
    r"relationship)|failed\s+to\s+(?:reach|achieve)\s+significance|"
    r"p\s*[=>]\s*0\.0[5-9]|p\s*[=>]\s*[1-9]\.?\d*)\b",
    re.IGNORECASE,
)
_NEGATIVE_DISCUSSION_RE = re.compile(
    r"\b(?:null\s+(?:result|finding|hypothesis)|negative\s+(?:result|finding)|"
    r"lack\s+of\s+(?:significance|effect)|absence\s+of\s+(?:effect|significant)|"
    r"no\s+evidence\s+(?:of|for)|power\s+may\s+have|underpowered)\b",
    re.IGNORECASE,
)


def validate_negative_result_framing(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts with non-significant results lacking explicit acknowledgment.

    Emits ``negative-result-underreported`` (minor) when non-significant
    p-value patterns appear in Results but the Discussion lacks any null/negative
    result framing.
    """
    results_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title and "result" in s.title.lower()
    )
    discussion_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title and "discussion" in s.title.lower()
    )
    if not results_text or not discussion_text:
        return ValidationResult(
            validator_name="negative_result_framing", findings=[]
        )

    non_sig_count = len(_NON_SIG_RE.findall(results_text))
    if non_sig_count < 2:
        return ValidationResult(
            validator_name="negative_result_framing", findings=[]
        )

    if _NEGATIVE_DISCUSSION_RE.search(discussion_text):
        return ValidationResult(
            validator_name="negative_result_framing", findings=[]
        )

    return ValidationResult(
        validator_name="negative_result_framing",
        findings=[
            Finding(
                code="negative-result-underreported",
                severity="minor",
                message=(
                    f"Results section contains {non_sig_count} non-significant findings "
                    "but Discussion does not explicitly address null results, lack of "
                    "evidence, or underpowering. "
                    "Discuss the implications of non-significant results."
                ),
                validator="negative_result_framing",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 150 – Abstract–results consistency check
# ---------------------------------------------------------------------------

_ABSTRACT_CLAIM_RE = re.compile(
    r"\b(?:we\s+found|we\s+show(?:ed)?|results?\s+(?:show|indicate|suggest|"
    r"demonstrate)|our\s+(?:results?|findings?|study)\s+(?:show|indicate|"
    r"suggest|demonstrate|reveal)|significantly\s+(?:higher|lower|better|"
    r"worse|greater|reduced|increased))\b",
    re.IGNORECASE,
)
_RESULTS_CLAIM_RE = re.compile(
    r"\b(?:(?:were|was|is|are)\s+significantly|significantly\s+(?:higher|lower|"
    r"better|worse|greater|reduced|increased)|p\s*[<≤]\s*0\.0[0-5])\b",
    re.IGNORECASE,
)


def validate_abstract_results_consistency(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts where abstract makes result claims but Results section is sparse.

    Emits ``abstract-results-mismatch`` (moderate) when the abstract makes
    >= 2 result claims but the Results section has fewer result claim patterns,
    suggesting the abstract may overclaim relative to reported results.
    """
    abstract = parsed.abstract or ""
    if not abstract:
        return ValidationResult(
            validator_name="abstract_results_consistency", findings=[]
        )

    results_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title and "result" in s.title.lower()
    )
    if not results_text:
        return ValidationResult(
            validator_name="abstract_results_consistency", findings=[]
        )

    abstract_claims = _ABSTRACT_CLAIM_RE.findall(abstract)
    results_claims = _RESULTS_CLAIM_RE.findall(results_text)

    if len(abstract_claims) < 2:
        return ValidationResult(
            validator_name="abstract_results_consistency", findings=[]
        )

    if len(results_claims) >= len(abstract_claims):
        return ValidationResult(
            validator_name="abstract_results_consistency", findings=[]
        )

    return ValidationResult(
        validator_name="abstract_results_consistency",
        findings=[
            Finding(
                code="abstract-results-mismatch",
                severity="moderate",
                message=(
                    f"Abstract makes {len(abstract_claims)} result claims but "
                    f"Results section contains only {len(results_claims)} "
                    "supporting quantitative claims. "
                    "Ensure abstract claims are fully supported in the Results."
                ),
                validator="abstract_results_consistency",
                location="Abstract/Results",
                evidence=[
                    f"abstract claims: {len(abstract_claims)}",
                    f"results claims: {len(results_claims)}",
                ],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 151 – Measurement invariance testing
# ---------------------------------------------------------------------------

_COMPARATIVE_GROUP_RE = re.compile(
    r"\b(?:comparison\s+(?:between|across)\s+groups?|"
    r"group\s+(?:differences?|comparison)|"
    r"between.group\s+(?:differences?|comparison)|"
    r"(?:male|female|gender|age)\s+(?:group|subgroup|comparison)|"
    r"(?:compared|comparing)\s+(?:groups?|samples?|populations?))\b",
    re.IGNORECASE,
)
_INVARIANCE_RE = re.compile(
    r"\b(?:measurement\s+invariance|factorial\s+invariance|"
    r"configural\s+(?:model|invariance)|metric\s+invariance|"
    r"scalar\s+invariance|partial\s+invariance|"
    r"differential\s+item\s+functioning|DIF)\b",
    re.IGNORECASE,
)


def validate_measurement_invariance(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag comparative studies lacking measurement invariance testing.

    Emits ``missing-measurement-invariance`` (moderate) when group comparisons
    are made on latent constructs or scale scores but measurement invariance
    is not tested or mentioned.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="measurement_invariance", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="measurement_invariance", findings=[]
        )

    has_comparison = bool(_COMPARATIVE_GROUP_RE.search(full))
    has_scale = bool(_SCALE_MEASURE_RE.search(full))
    if not (has_comparison and has_scale):
        return ValidationResult(
            validator_name="measurement_invariance", findings=[]
        )

    if _INVARIANCE_RE.search(full):
        return ValidationResult(
            validator_name="measurement_invariance", findings=[]
        )

    return ValidationResult(
        validator_name="measurement_invariance",
        findings=[
            Finding(
                code="missing-measurement-invariance",
                severity="moderate",
                message=(
                    "Group comparisons on scale measures detected but measurement "
                    "invariance (configural, metric, scalar) is not tested or mentioned. "
                    "Test or justify the assumption of measurement equivalence across groups."
                ),
                validator="measurement_invariance",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 152 – Effect size confidence intervals
# ---------------------------------------------------------------------------

_EFFECT_SIZE_RE = re.compile(
    r"\b(?:Cohen(?:'s)?\s+[dDgG]|Hedge(?:'s)?\s+[gG]|odds\s+ratio|"
    r"risk\s+ratio|relative\s+risk|hazard\s+ratio|"
    r"eta.?squared|partial\s+eta.?squared|omega.?squared|"
    r"Cramer(?:'s)?\s+V|phi\s+coefficient|"
    r"\bES\s*=|effect\s+size\s*=|effect\s+size\s+of)\b",
    re.IGNORECASE,
)
_EFFECT_CI_RE = re.compile(
    r"\b(?:95\s*%\s*CI|confidence\s+interval|CI\s*[=:]\s*[\[\(]|"
    r"\[\s*\d+\.\d+\s*,\s*\d+\.\d+\s*\])\b",
    re.IGNORECASE,
)


def validate_effect_size_confidence_intervals(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts reporting effect sizes without confidence intervals.

    Emits ``missing-effect-size-ci`` (moderate) when effect sizes are
    reported but no confidence intervals are present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="effect_size_confidence_intervals", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="effect_size_confidence_intervals", findings=[]
        )

    effect_matches = _EFFECT_SIZE_RE.findall(full)
    if len(effect_matches) < 2:
        return ValidationResult(
            validator_name="effect_size_confidence_intervals", findings=[]
        )

    if _EFFECT_CI_RE.search(full):
        return ValidationResult(
            validator_name="effect_size_confidence_intervals", findings=[]
        )

    return ValidationResult(
        validator_name="effect_size_confidence_intervals",
        findings=[
            Finding(
                code="missing-effect-size-ci",
                severity="moderate",
                message=(
                    f"Manuscript reports {len(effect_matches)} effect size(s) but "
                    "no confidence intervals are present. "
                    "Report 95% CIs for all effect size estimates."
                ),
                validator="effect_size_confidence_intervals",
                location="Results",
                evidence=list(dict.fromkeys(effect_matches[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 153 – Preregistration statement
# ---------------------------------------------------------------------------

_PREREGISTERED_RE = re.compile(
    r"\b(?:preregist(?:ered|ration|er)|pre.regist(?:ered|ration|er)|"
    r"registered\s+(?:report|study|trial)|"
    r"(?:clinicaltrials\.gov|osf\.io|aspredicted\.org|anzctr\.org|"
    r"isrctn(?:\.com)?|drks\.de|umin\.ac\.jp))\b",
    re.IGNORECASE,
)
_CONFIRMATORY_RE = re.compile(
    r"\b(?:hypothesis.?(?:driven|testing|test)|confirmatory\s+(?:study|analysis|test)|"
    r"a\s+priori\s+hypothesis|we\s+predicted\s+that|we\s+hypothesized\s+that)\b",
    re.IGNORECASE,
)


def validate_preregistration_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag confirmatory/RCT studies without preregistration mention.

    Emits ``missing-preregistration`` (minor) when a confirmatory or
    hypothesis-driven study is detected but no preregistration is mentioned.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="preregistration_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="preregistration_statement", findings=[]
        )

    is_rct = bool(_INTERVENTION_RE.search(full))
    is_confirmatory = bool(_CONFIRMATORY_RE.search(full))
    if not (is_rct or is_confirmatory):
        return ValidationResult(
            validator_name="preregistration_statement", findings=[]
        )

    if _PREREGISTERED_RE.search(full):
        return ValidationResult(
            validator_name="preregistration_statement", findings=[]
        )

    return ValidationResult(
        validator_name="preregistration_statement",
        findings=[
            Finding(
                code="missing-preregistration",
                severity="minor",
                message=(
                    "Confirmatory or controlled-trial study detected but no "
                    "preregistration is mentioned. "
                    "Report whether the study was preregistered and provide the "
                    "registry URL if applicable."
                ),
                validator="preregistration_statement",
                location="manuscript",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 154 – Cross-validation reporting for ML/prediction models
# ---------------------------------------------------------------------------

_ML_MODEL_RE = re.compile(
    r"\b(?:machine\s+learning|deep\s+learning|neural\s+network|"
    r"random\s+forest|gradient\s+boosting|XGBoost|support\s+vector|"
    r"prediction\s+model|predictive\s+model|classification\s+model|"
    r"logistic\s+regression\s+model|linear\s+regression\s+model)\b",
    re.IGNORECASE,
)
_CROSS_VALIDATION_RE = re.compile(
    r"\b(?:cross.?validation|k.?fold|leave.one.out|LOOCV|"
    r"train(?:ing)?\s*/\s*test\s+split|held.out\s+(?:set|data)|"
    r"\d+.fold\s+cross|bootstrap\s+validation)\b",
    re.IGNORECASE,
)


def validate_cross_validation_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ML/prediction studies lacking cross-validation reporting.

    Emits ``missing-cross-validation`` (moderate) when machine learning or
    predictive models are described but cross-validation is not mentioned.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="cross_validation_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="cross_validation_reporting", findings=[]
        )

    if not _ML_MODEL_RE.search(full):
        return ValidationResult(
            validator_name="cross_validation_reporting", findings=[]
        )

    if _CROSS_VALIDATION_RE.search(full):
        return ValidationResult(
            validator_name="cross_validation_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="cross_validation_reporting",
        findings=[
            Finding(
                code="missing-cross-validation",
                severity="moderate",
                message=(
                    "Machine learning or prediction model detected but no "
                    "cross-validation procedure is described. "
                    "Report the validation strategy (k-fold CV, hold-out set, etc.)."
                ),
                validator="cross_validation_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 155 – Sensitivity analysis reporting
# ---------------------------------------------------------------------------

_PRIMARY_ANALYSIS_RE = re.compile(
    r"\b(?:primary\s+(?:analysis|outcome|endpoint|model)|"
    r"main\s+(?:analysis|result|finding)|"
    r"our\s+(?:main|primary)\s+(?:analysis|model))\b",
    re.IGNORECASE,
)
_SENSITIVITY_RE = re.compile(
    r"\b(?:sensitivity\s+analysis|robust(?:ness)?\s+check|"
    r"robustness\s+analysis|sensitivity\s+check|"
    r"alternative\s+(?:specification|model|analysis)|"
    r"we\s+(?:also\s+ran|conducted\s+additional|repeated\s+the\s+analysis))\b",
    re.IGNORECASE,
)


def validate_sensitivity_analysis_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts with primary analyses but no sensitivity analysis.

    Emits ``missing-sensitivity-analysis`` (moderate) when a primary analysis
    is described but no sensitivity or robustness check is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="sensitivity_analysis_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="sensitivity_analysis_reporting", findings=[]
        )

    if not _PRIMARY_ANALYSIS_RE.search(full):
        return ValidationResult(
            validator_name="sensitivity_analysis_reporting", findings=[]
        )

    if _SENSITIVITY_RE.search(full):
        return ValidationResult(
            validator_name="sensitivity_analysis_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="sensitivity_analysis_reporting",
        findings=[
            Finding(
                code="missing-sensitivity-analysis",
                severity="moderate",
                message=(
                    "Primary analysis detected but no sensitivity or robustness "
                    "analysis is reported. "
                    "Add sensitivity analyses to test the stability of main findings."
                ),
                validator="sensitivity_analysis_reporting",
                location="Methods/Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 156 – Regression diagnostics / assumption checks
# ---------------------------------------------------------------------------

_REGRESSION_RE = re.compile(
    r"\b(?:(?:linear|logistic|multiple|OLS|multilevel|hierarchical)\s+regression|"
    r"regression\s+(?:model|analysis)|we\s+ran\s+a\s+regression|"
    r"generalized\s+linear\s+(?:model|mixed)|GLM|GLMM|lme4|lm\(|glm\()\b",
    re.IGNORECASE,
)
_DIAGNOSTICS_RE = re.compile(
    r"\b(?:multicollinearity|VIF|variance\s+inflation|"
    r"homoscedasticity|homogeneity\s+of\s+variance|"
    r"residual\s+(?:plot|analysis|normality)|"
    r"Breusch.Pagan|Cook(?:'s)?\s+distance|leverage|influential\s+obs|"
    r"Shapiro.Wilk|Kolmogorov.Smirnov|Q.Q\s+plot|"
    r"assumption\s+(?:of\s+(?:normality|homoscedasticity)|check))\b",
    re.IGNORECASE,
)


def validate_regression_diagnostics(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression analyses lacking assumption checks.

    Emits ``missing-regression-diagnostics`` (moderate) when regression models
    are described but no diagnostic checks (VIF, residual plots, normality
    tests, homoscedasticity) are mentioned.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="regression_diagnostics", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="regression_diagnostics", findings=[]
        )

    if not _REGRESSION_RE.search(full):
        return ValidationResult(
            validator_name="regression_diagnostics", findings=[]
        )

    if _DIAGNOSTICS_RE.search(full):
        return ValidationResult(
            validator_name="regression_diagnostics", findings=[]
        )

    return ValidationResult(
        validator_name="regression_diagnostics",
        findings=[
            Finding(
                code="missing-regression-diagnostics",
                severity="moderate",
                message=(
                    "Regression analysis detected but no diagnostic checks are reported "
                    "(multicollinearity/VIF, residual plots, normality, homoscedasticity). "
                    "Report assumption checks for all regression models."
                ),
                validator="regression_diagnostics",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 157 – Sample representativeness / generalizability caveat
# ---------------------------------------------------------------------------

_SINGLE_SITE_RE = re.compile(
    r"\b(?:(?:a\s+single|one)\s+(?:hospital|clinic|school|university|site|"
    r"institution|center|centre|country|region|city)|"
    r"convenience\s+sample|non.?random\s+sample|opportunistic\s+sample|"
    r"recruited\s+from\s+(?:a\s+single|one))\b",
    re.IGNORECASE,
)
_SINGLE_SITE_CLAIM_RE = re.compile(
    r"\b(?:(?:our\s+)?results?\s+(?:are\s+generaliz|can\s+be\s+generaliz|"
    r"generaliz(?:able|e)\s+to)|broadly\s+applicable|wide(?:r|ly)\s+applicable|"
    r"implications?\s+for\s+(?:all|the\s+general\s+population|society))\b",
    re.IGNORECASE,
)
_LIMITATION_CAVEAT_RE = re.compile(
    r"\b(?:limitation|caveat|generalizability\s+(?:is\s+limited|may\s+be)|"
    r"may\s+not\s+generalize|caution\s+(?:in\s+generalizing|when\s+applying)|"
    r"external\s+validity)\b",
    re.IGNORECASE,
)


def validate_sample_representativeness(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag single-site studies that claim generalizability without caveats.

    Emits ``non-representative-sample`` (moderate) when convenience/single-site
    sampling is detected alongside broad generalizability claims and no
    limitation caveat is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="sample_representativeness", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="sample_representativeness", findings=[]
        )

    if not _SINGLE_SITE_RE.search(full):
        return ValidationResult(
            validator_name="sample_representativeness", findings=[]
        )

    if not _SINGLE_SITE_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="sample_representativeness", findings=[]
        )

    if _LIMITATION_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="sample_representativeness", findings=[]
        )

    return ValidationResult(
        validator_name="sample_representativeness",
        findings=[
            Finding(
                code="non-representative-sample",
                severity="moderate",
                message=(
                    "Convenience or single-site sample detected with broad "
                    "generalizability claims but no representativeness caveat. "
                    "Discuss limitations of sample representativeness."
                ),
                validator="sample_representativeness",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 158 – Variable operationalization
# ---------------------------------------------------------------------------

_VARIABLE_MENTION_RE = re.compile(
    r"\b(?:independent\s+variable|dependent\s+variable|predictor\s+variable|"
    r"outcome\s+variable|criterion\s+variable|covariate|moderator|mediator)\b",
    re.IGNORECASE,
)
_OPERATIONALIZATION_RE = re.compile(
    r"\b(?:operationalized?\s+(?:as|by)|defined\s+as|coded\s+as|"
    r"measured\s+(?:by|using|as|with)|assessed\s+(?:by|using|with)|"
    r"scored\s+(?:by|as|using)|calculated\s+(?:as|by)|"
    r"index(?:ed)?\s+(?:as|by)|composite\s+(?:score|variable))\b",
    re.IGNORECASE,
)
_VARIABLE_MIN = 3


def validate_variable_operationalization(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts with many variable mentions but no operationalization.

    Emits ``missing-variable-operationalization`` (minor) when >= 3 variable
    mentions appear but no operationalization/definition language is found.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="variable_operationalization", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="variable_operationalization", findings=[]
        )

    var_matches = _VARIABLE_MENTION_RE.findall(methods_text)
    if len(var_matches) < _VARIABLE_MIN:
        return ValidationResult(
            validator_name="variable_operationalization", findings=[]
        )

    if _OPERATIONALIZATION_RE.search(methods_text):
        return ValidationResult(
            validator_name="variable_operationalization", findings=[]
        )

    return ValidationResult(
        validator_name="variable_operationalization",
        findings=[
            Finding(
                code="missing-variable-operationalization",
                severity="minor",
                message=(
                    f"Methods section references {len(var_matches)} variables "
                    "(independent/dependent/predictor/covariate etc.) but provides "
                    "no operationalization or measurement definition. "
                    "Define how each variable was measured or coded."
                ),
                validator="variable_operationalization",
                location="Methods",
                evidence=list(dict.fromkeys(var_matches[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 160 – Control variable justification
# ---------------------------------------------------------------------------

_CONTROL_VAR_RE = re.compile(
    r"\b(?:control(?:led|ling)?\s+for|we\s+controlled\s+for|"
    r"controlling\s+for|covariates?\s+(?:included|were)|"
    r"control\s+variables?\s+(?:included|were|such\s+as))\b",
    re.IGNORECASE,
)
_CONTROL_JUSTIFICATION_RE = re.compile(
    r"\b(?:(?:a\s+priori|theoretically?\s+(?:motivated|justified|grounded)|"
    r"based\s+on\s+(?:theory|literature|prior\s+research)|"
    r"confound(?:er|ing)|potential\s+confounder|"
    r"following\s+(?:prior\s+research|the\s+literature)))\b",
    re.IGNORECASE,
)
_CONTROL_MIN_MENTIONS = 2


def validate_control_variable_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag models with many controls but no theoretical justification.

    Emits ``missing-control-justification`` (minor) when control variables are
    mentioned >= 2 times but no theoretical or literature-based justification
    is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="control_variable_justification", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="control_variable_justification", findings=[]
        )

    control_matches = _CONTROL_VAR_RE.findall(methods_text)
    if len(control_matches) < _CONTROL_MIN_MENTIONS:
        return ValidationResult(
            validator_name="control_variable_justification", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if _CONTROL_JUSTIFICATION_RE.search(full):
        return ValidationResult(
            validator_name="control_variable_justification", findings=[]
        )

    return ValidationResult(
        validator_name="control_variable_justification",
        findings=[
            Finding(
                code="missing-control-justification",
                severity="minor",
                message=(
                    f"Methods section mentions control variables {len(control_matches)} "
                    "time(s) but provides no theoretical or literature-based justification "
                    "for their inclusion. "
                    "Justify control variable selection with theory or prior research."
                ),
                validator="control_variable_justification",
                location="Methods",
                evidence=list(dict.fromkeys(control_matches[:3])),
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 161 – Prospective vs. retrospective design consistency
# ---------------------------------------------------------------------------

_PROSPECTIVE_CLAIM_RE = re.compile(
    r"\b(?:prospective\s+(?:study|design|cohort|trial|analysis)|"
    r"we\s+(?:prospectively|will\s+(?:recruit|enroll|collect))|"
    r"ongoing\s+(?:study|cohort))\b",
    re.IGNORECASE,
)
_RETROSPECTIVE_SIGNAL_RE = re.compile(
    r"\b(?:retrospective(?:ly)?|medical\s+records?|chart\s+review|"
    r"existing\s+database|existing\s+data|administrative\s+records?|"
    r"previously\s+collected|data\s+(?:were\s+)?extracted\s+from|"
    r"data\s+(?:were\s+)?retrieved\s+from)\b",
    re.IGNORECASE,
)


def validate_prospective_vs_retrospective(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag papers claiming prospective design with retrospective data language.

    Emits ``retrospective-design-claim`` (minor) when the manuscript claims a
    prospective design but uses language indicating retrospective data extraction.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="prospective_vs_retrospective", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="prospective_vs_retrospective", findings=[]
        )

    if not _PROSPECTIVE_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="prospective_vs_retrospective", findings=[]
        )

    if not _RETROSPECTIVE_SIGNAL_RE.search(full):
        return ValidationResult(
            validator_name="prospective_vs_retrospective", findings=[]
        )

    return ValidationResult(
        validator_name="prospective_vs_retrospective",
        findings=[
            Finding(
                code="retrospective-design-claim",
                severity="minor",
                message=(
                    "Manuscript claims a prospective design but also uses language "
                    "indicating retrospective data extraction (chart review, existing "
                    "database, previously collected data). "
                    "Clarify whether the design was truly prospective."
                ),
                validator="prospective_vs_retrospective",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 162 – CONSORT elements for RCTs
# ---------------------------------------------------------------------------

_CONSORT_ALLOCATION_RE = re.compile(
    r"\b(?:random(?:iz|is)ation\s+(?:sequence|procedure)|"
    r"allocation\s+(?:sequence|concealment|ratio)|"
    r"concealment\s+of\s+(?:allocation|treatment)|"
    r"sealed\s+(?:envelope|opaque)|"
    r"computer.?generated\s+(?:random|sequence)|"
    r"block\s+random(?:iz|is)ation)\b",
    re.IGNORECASE,
)
_CONSORT_FLOW_RE = re.compile(
    r"\b(?:CONSORT|flow\s+diagram|participant\s+flow|"
    r"screened\s+for\s+eligibility|excluded\s+(?:at\s+)?(?:screening|baseline)|"
    r"allocated\s+to\s+(?:receive|treatment|control|intervention))\b",
    re.IGNORECASE,
)


def validate_clinical_trial_consort(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCT manuscripts missing CONSORT-required allocation/flow elements.

    Emits ``missing-consort-elements`` (moderate) when a randomized controlled
    trial is detected but allocation concealment and participant flow information
    are not present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="clinical_trial_consort", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="clinical_trial_consort", findings=[]
        )

    if not _INTERVENTION_RE.search(full):
        return ValidationResult(
            validator_name="clinical_trial_consort", findings=[]
        )

    has_allocation = bool(_CONSORT_ALLOCATION_RE.search(full))
    has_flow = bool(_CONSORT_FLOW_RE.search(full))
    if has_allocation and has_flow:
        return ValidationResult(
            validator_name="clinical_trial_consort", findings=[]
        )

    missing = []
    if not has_allocation:
        missing.append("allocation concealment")
    if not has_flow:
        missing.append("participant flow / CONSORT diagram")

    return ValidationResult(
        validator_name="clinical_trial_consort",
        findings=[
            Finding(
                code="missing-consort-elements",
                severity="moderate",
                message=(
                    f"Randomized controlled trial detected but CONSORT-required "
                    f"elements are missing: {', '.join(missing)}. "
                    "Report randomization sequence, allocation concealment, and "
                    "participant flow per CONSORT guidelines."
                ),
                validator="clinical_trial_consort",
                location="Methods",
                evidence=missing,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 163 – Ecological validity discussion
# ---------------------------------------------------------------------------

_LAB_STUDY_RE = re.compile(
    r"\b(?:laboratory\s+(?:study|experiment|setting|task)|"
    r"experimental\s+(?:lab|laboratory)|"
    r"controlled\s+(?:lab|laboratory)\s+(?:setting|environment|condition)|"
    r"lab(?:oratory)?.?based\s+(?:study|experiment))\b",
    re.IGNORECASE,
)
_REAL_WORLD_CLAIM_RE = re.compile(
    r"\b(?:real.?world\s+(?:applicability|relevance|implications?|settings?)|"
    r"practical\s+implications?|translates?\s+to\s+(?:practice|the\s+real\s+world)|"
    r"(?:generaliz|applicable)\s+to\s+(?:real|naturalistic|everyday|field))\b",
    re.IGNORECASE,
)
_ECOLOGICAL_VALIDITY_RE = re.compile(
    r"\b(?:ecological\s+validity|external\s+validity|naturalistic\s+(?:setting|context)|"
    r"lab(?:oratory)?\s+(?:setting\s+may\s+not|limitation)|"
    r"artificial\s+(?:setting|condition)|mundane\s+realism)\b",
    re.IGNORECASE,
)


def validate_ecological_validity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag lab studies claiming real-world applicability without ecological validity caveat.

    Emits ``missing-ecological-validity`` (minor) when a lab study claims
    real-world relevance but does not discuss ecological validity limitations.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="ecological_validity", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="ecological_validity", findings=[]
        )

    if not _LAB_STUDY_RE.search(full):
        return ValidationResult(
            validator_name="ecological_validity", findings=[]
        )

    if not _REAL_WORLD_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="ecological_validity", findings=[]
        )

    if _ECOLOGICAL_VALIDITY_RE.search(full):
        return ValidationResult(
            validator_name="ecological_validity", findings=[]
        )

    return ValidationResult(
        validator_name="ecological_validity",
        findings=[
            Finding(
                code="missing-ecological-validity",
                severity="minor",
                message=(
                    "Laboratory study with real-world applicability claims detected "
                    "but no ecological validity limitation is discussed. "
                    "Address whether lab findings generalize to naturalistic settings."
                ),
                validator="ecological_validity",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 164 – Non-peer-reviewed / grey literature citations
# ---------------------------------------------------------------------------

_GREY_CITATION_RE = re.compile(
    r"\b(?:wikipedia\.org|wikipeida|"
    r"(?:www\.|https?://)?(?:bbc\.com|cnn\.com|nytimes\.com|"
    r"theguardian\.com|huffpost\.com|buzzfeed\.com|"
    r"medium\.com|substack\.com|blogspot\.com|wordpress\.com)|"
    r"press\s+release|newspaper\s+article|blog\s+post|"
    r"personal\s+communication|(?:retrieved\s+from\s+)?(?:twitter|facebook|"
    r"instagram|reddit|tiktok)\.com)\b",
    re.IGNORECASE,
)


def validate_media_source_citations(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts citing non-peer-reviewed or grey literature sources.

    Emits ``non-peer-reviewed-citation`` (minor) when Wikipedia, news outlets,
    blogs, or social media are cited.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="media_source_citations", findings=[]
        )

    matches = _GREY_CITATION_RE.findall(full)
    if not matches:
        return ValidationResult(
            validator_name="media_source_citations", findings=[]
        )

    return ValidationResult(
        validator_name="media_source_citations",
        findings=[
            Finding(
                code="non-peer-reviewed-citation",
                severity="minor",
                message=(
                    f"Manuscript contains {len(matches)} reference(s) to non-peer-reviewed "
                    "or grey literature sources (Wikipedia, news outlets, blogs, social media). "
                    "Replace with peer-reviewed primary sources."
                ),
                validator="media_source_citations",
                location="manuscript",
                evidence=list(dict.fromkeys(matches[:3])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 165 – Competing model comparison
# ---------------------------------------------------------------------------

_MODEL_PROPOSAL_RE = re.compile(
    r"\b(?:we\s+propose(?:d)?\s+(?:a\s+)?(?:model|framework|approach)|"
    r"proposed\s+(?:model|framework|method|approach)|"
    r"our\s+(?:model|framework|approach|method)|"
    r"novel\s+(?:model|framework|method|approach)|"
    r"new\s+(?:model|framework|method|algorithm))\b",
    re.IGNORECASE,
)
_MODEL_COMPARISON_RE = re.compile(
    r"\b(?:compar(?:ed|ing)\s+(?:against|to|with)\s+(?:baseline|existing|"
    r"alternative|competing|prior)|"
    r"baseline\s+(?:model|method|approach|comparison)|"
    r"outperform(?:s|ed)?|benchmark(?:ed|ing)?|"
    r"compared\s+to\s+(?:\d+|several|multiple|three|two|four|five)\s+"
    r"(?:baseline|alternative|existing))\b",
    re.IGNORECASE,
)


def validate_competing_model_comparison(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts proposing a model without comparing against alternatives.

    Emits ``missing-model-comparison`` (moderate) when a novel model, framework,
    or method is proposed but no comparison against existing baselines is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="competing_model_comparison", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="competing_model_comparison", findings=[]
        )

    if not _MODEL_PROPOSAL_RE.search(full):
        return ValidationResult(
            validator_name="competing_model_comparison", findings=[]
        )

    if _MODEL_COMPARISON_RE.search(full):
        return ValidationResult(
            validator_name="competing_model_comparison", findings=[]
        )

    return ValidationResult(
        validator_name="competing_model_comparison",
        findings=[
            Finding(
                code="missing-model-comparison",
                severity="moderate",
                message=(
                    "Novel model, method, or framework proposed but no comparison "
                    "against existing or alternative baseline approaches is reported. "
                    "Compare the proposed approach to at least one relevant baseline."
                ),
                validator="competing_model_comparison",
                location="Results/Discussion",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 166 – Causal language in observational studies
# ---------------------------------------------------------------------------

_OBSERVATIONAL_RE = re.compile(
    r"\b(?:observational\s+(?:study|design|data)|cross.?sectional|"
    r"survey\s+(?:study|data|design)|correlation(?:al)?\s+(?:study|design|analysis)|"
    r"retrospective\s+(?:study|cohort|analysis)|"
    r"administrative\s+(?:data|records?)|"
    r"ecological\s+(?:study|correlation))\b",
    re.IGNORECASE,
)
_CAUSAL_LANGUAGE_RE = re.compile(
    r"\b(?:(?:X\s+)?caus(?:ed|es|ing|al)\s+(?:Y\s+)?|"
    r"(?:the\s+)?effect\s+of\s+\w+\s+on\s+\w+|"
    r"impact\s+of\s+\w+\s+on\s+(?:outcomes?|health|behavior)|"
    r"leads?\s+to\s+(?:higher|lower|increased|decreased|greater|reduced)|"
    r"due\s+to\s+the\s+(?:treatment|exposure|intervention|effect\s+of)|"
    r"(?:treatment|exposure|intervention)\s+(?:causes?|results?\s+in|leads?\s+to))\b",
    re.IGNORECASE,
)
_CAUSAL_FRAMEWORK_RE = re.compile(
    r"\b(?:causal\s+(?:inference|diagram|model|effect|identification)|"
    r"instrumental\s+variable|difference.?in.?differences|"
    r"regression\s+discontinuity|propensity\s+score|"
    r"directed\s+acyclic\s+graph|DAG|counterfactual|"
    r"we\s+cannot\s+(?:establish|infer|conclude)\s+causality|"
    r"causality\s+cannot\s+be\s+(?:established|inferred))\b",
    re.IGNORECASE,
)


def validate_causal_language(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag observational studies using causal language without causal framework.

    Emits ``unsupported-causal-claim`` (moderate) when an observational design
    is detected alongside causal language but no causal inference framework or
    explicit caveat is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="causal_language", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="causal_language", findings=[]
        )

    if not _OBSERVATIONAL_RE.search(full):
        return ValidationResult(
            validator_name="causal_language", findings=[]
        )

    if not _CAUSAL_LANGUAGE_RE.search(full):
        return ValidationResult(
            validator_name="causal_language", findings=[]
        )

    if _CAUSAL_FRAMEWORK_RE.search(full):
        return ValidationResult(
            validator_name="causal_language", findings=[]
        )

    return ValidationResult(
        validator_name="causal_language",
        findings=[
            Finding(
                code="unsupported-causal-claim",
                severity="moderate",
                message=(
                    "Observational study design detected with causal language "
                    "(caused, effect of, leads to) but no causal inference framework "
                    "or caveat about correlation vs. causation. "
                    "Replace causal language with associative language or justify "
                    "using a formal causal inference framework."
                ),
                validator="causal_language",
                location="manuscript",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 167 – Missing standard errors in regression output
# ---------------------------------------------------------------------------

_REGRESSION_TABLE_RE = re.compile(
    r"\b(?:table\s+\d+[^\n]*(?:regression|coefficient|model|predictor)|"
    r"regression\s+(?:results?|output|table|model)|"
    r"β\s*=|b\s*=\s*[-\d.]|unstandardized\s+coefficient|"
    r"standardized\s+coefficient)\b",
    re.IGNORECASE,
)
_STANDARD_ERROR_RE = re.compile(
    r"(?:\bstandard\s+error\b|\bSE\s*=|\bS\.E\.\s*=|"
    r"\b95\s*%\s*(?:CI\b|confidence\s+interval\b)|"
    r"\(\d+\.\d+\)\s*$|\(\s*SE\s*=)",
    re.IGNORECASE | re.MULTILINE,
)


def validate_missing_standard_errors(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression tables lacking standard errors or confidence intervals.

    Emits ``missing-standard-errors`` (minor) when regression output is reported
    but no standard errors (SE) or CIs are included.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="missing_standard_errors", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="missing_standard_errors", findings=[]
        )

    if not _REGRESSION_TABLE_RE.search(full):
        return ValidationResult(
            validator_name="missing_standard_errors", findings=[]
        )

    if _STANDARD_ERROR_RE.search(full):
        return ValidationResult(
            validator_name="missing_standard_errors", findings=[]
        )

    return ValidationResult(
        validator_name="missing_standard_errors",
        findings=[
            Finding(
                code="missing-standard-errors",
                severity="minor",
                message=(
                    "Regression coefficients reported but no standard errors (SE) "
                    "or confidence intervals (CI) are present. "
                    "Report SE or 95% CI for all regression estimates."
                ),
                validator="missing_standard_errors",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 168 – Unhedged subjective / normative claims
# ---------------------------------------------------------------------------

_NORMATIVE_CLAIM_RE = re.compile(
    r"\b(?:it\s+is\s+(?:crucial|essential|vital|imperative|undeniable|"
    r"clear|obvious|evident|indisputable)\s+that|"
    r"undoubtedly|unquestionably|clearly\s+demonstrates?|"
    r"this\s+(?:proves?|conclusively\s+shows?|demonstrates?\s+beyond)|"
    r"the\s+(?:most\s+important|key|fundamental|critical)\s+(?:finding|result|"
    r"implication|contribution|insight)\s+is)\b",
    re.IGNORECASE,
)
_HEDGE_RE = re.compile(
    r"\b(?:suggest(?:s|ed)?|indicate(?:s|d)?|may|might|could|appear(?:s)?|"
    r"seem(?:s)?|likely|perhaps|potentially|tentatively|arguably|"
    r"consistent\s+with|compatible\s+with|we\s+(?:believe|argue|propose))\b",
    re.IGNORECASE,
)
_NORMATIVE_MIN = 2


def validate_subjective_claim_hedging(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts with normative/certainty claims lacking hedging language.

    Emits ``unhedged-subjective-claim`` (minor) when >= 2 strong normative or
    certainty-implying phrases appear in the discussion/conclusion but no hedging
    language is present in those sections.
    """
    disc_text = " ".join(
        s.body
        for s in parsed.sections
        if s.title and any(
            k in s.title.lower() for k in ("discussion", "conclusion", "implication")
        )
    )
    if not disc_text:
        return ValidationResult(
            validator_name="subjective_claim_hedging", findings=[]
        )

    normative_matches = _NORMATIVE_CLAIM_RE.findall(disc_text)
    if len(normative_matches) < _NORMATIVE_MIN:
        return ValidationResult(
            validator_name="subjective_claim_hedging", findings=[]
        )

    if _HEDGE_RE.search(disc_text):
        return ValidationResult(
            validator_name="subjective_claim_hedging", findings=[]
        )

    return ValidationResult(
        validator_name="subjective_claim_hedging",
        findings=[
            Finding(
                code="unhedged-subjective-claim",
                severity="minor",
                message=(
                    f"Discussion/Conclusion contains {len(normative_matches)} "
                    "strong normative or certainty claims but no hedging language. "
                    "Use qualifying language (suggests, may, appears) for claims "
                    "that go beyond the data."
                ),
                validator="subjective_claim_hedging",
                location="Discussion/Conclusion",
                evidence=list(dict.fromkeys(normative_matches[:2])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 169 – Target population definition
# ---------------------------------------------------------------------------

_POPULATION_DEFINITION_RE = re.compile(
    r"\b(?:target\s+population|study\s+population|"
    r"eligible\s+(?:participants?|patients?|subjects?)|"
    r"inclusion\s+criteria|exclusion\s+criteria|"
    r"we\s+(?:recruited|enrolled|sampled)\s+(?:adults?|patients?|"
    r"participants?|children|women|men)\s+(?:who|aged|with|between|from))\b",
    re.IGNORECASE,
)


def validate_population_definition(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts without an explicit target population definition.

    Emits ``missing-population-definition`` (moderate) when the manuscript is
    empirical but lacks inclusion/exclusion criteria or a population description.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="population_definition", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="population_definition", findings=[]
        )

    if _POPULATION_DEFINITION_RE.search(methods_text):
        return ValidationResult(
            validator_name="population_definition", findings=[]
        )

    return ValidationResult(
        validator_name="population_definition",
        findings=[
            Finding(
                code="missing-population-definition",
                severity="moderate",
                message=(
                    "Empirical Methods section does not define the target population, "
                    "inclusion/exclusion criteria, or sampling frame. "
                    "Explicitly define who was eligible to participate."
                ),
                validator="population_definition",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 170 – Pilot study overclaiming
# ---------------------------------------------------------------------------

_PILOT_STUDY_RE = re.compile(
    r"\b(?:pilot\s+(?:study|trial|investigation|test|RCT)|"
    r"feasibility\s+(?:study|trial)|"
    r"preliminary\s+(?:study|investigation|data|findings|results?)|"
    r"this\s+(?:is\s+a\s+)?pilot|exploratory\s+pilot)\b",
    re.IGNORECASE,
)
_PILOT_OVERCLAIM_RE = re.compile(
    r"\b(?:(?:our\s+)?(?:results?|findings?)\s+(?:demonstrate|prove|establish|confirm"
    r"|show\s+conclusively)|definitive\s+(?:evidence|proof|conclusion)|"
    r"we\s+(?:definitively|conclusively)\s+(?:show|demonstrate|establish)|"
    r"generaliz(?:able|ed)\s+to\s+(?:the\s+(?:general\s+population|broader))|"
    r"policy\s+(?:recommendation|implication)\s+(?:is\s+that|should))\b",
    re.IGNORECASE,
)
_PILOT_CAVEAT_RE = re.compile(
    r"\b(?:pilot\s+(?:study\s+limitations?|findings?\s+should\s+be\s+(?:interpreted"
    r"|treated|considered))|larger\s+(?:study|trial|sample|RCT)|"
    r"future\s+(?:study|research|trial|work)\s+(?:with\s+larger|should\s+replicate)|"
    r"confirmatory\s+(?:study|trial|research))\b",
    re.IGNORECASE,
)


def validate_pilot_study_claims(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag pilot studies making overclaimed conclusions without appropriate caveats.

    Emits ``overclaimed-pilot-study`` (minor) when a pilot study makes definitive
    or generalizable conclusions without recommending larger confirmatory research.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="pilot_study_claims", findings=[]
        )

    if not _PILOT_STUDY_RE.search(full):
        return ValidationResult(
            validator_name="pilot_study_claims", findings=[]
        )

    if not _PILOT_OVERCLAIM_RE.search(full):
        return ValidationResult(
            validator_name="pilot_study_claims", findings=[]
        )

    if _PILOT_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="pilot_study_claims", findings=[]
        )

    return ValidationResult(
        validator_name="pilot_study_claims",
        findings=[
            Finding(
                code="overclaimed-pilot-study",
                severity="minor",
                message=(
                    "Pilot study with definitive or broadly generalizable conclusion "
                    "claims detected but no recommendation for larger confirmatory "
                    "research is present. "
                    "Qualify pilot findings and call for replication."
                ),
                validator="pilot_study_claims",
                location="Discussion/Conclusion",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 171 – Exclusion criteria rationale
# ---------------------------------------------------------------------------

_EXCLUSION_RE = re.compile(
    r"\b(?:exclusion\s+criteria|excluded?\s+(?:participants?|patients?|subjects?|"
    r"individuals?)\s+(?:who|with|if|due\s+to)|we\s+excluded?\s+\w+\s+who)\b",
    re.IGNORECASE,
)
_EXCLUSION_RATIONALE_RE = re.compile(
    r"\b(?:to\s+(?:ensure|avoid|minimize|reduce|control\s+for|prevent)|"
    r"because\s+(?:they|these|this)|due\s+to\s+(?:concern|risk|confound)|"
    r"(?:potential|possible)\s+confound|these\s+criteria\s+were\s+(?:chosen|selected|"
    r"determined|established)\s+(?:to|because|based))\b",
    re.IGNORECASE,
)


def validate_exclusion_criteria_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts with exclusion criteria but no rationale.

    Emits ``missing-exclusion-criteria-rationale`` (minor) when exclusion
    criteria are mentioned but no justification for their selection is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="exclusion_criteria_reporting", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="exclusion_criteria_reporting", findings=[]
        )

    if not _EXCLUSION_RE.search(methods_text):
        return ValidationResult(
            validator_name="exclusion_criteria_reporting", findings=[]
        )

    if _EXCLUSION_RATIONALE_RE.search(methods_text):
        return ValidationResult(
            validator_name="exclusion_criteria_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="exclusion_criteria_reporting",
        findings=[
            Finding(
                code="missing-exclusion-criteria-rationale",
                severity="minor",
                message=(
                    "Exclusion criteria are listed but no rationale or justification "
                    "is provided for their selection. "
                    "Explain why each exclusion criterion was chosen."
                ),
                validator="exclusion_criteria_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 172 – Normality assumption testing
# ---------------------------------------------------------------------------

_PARAMETRIC_TEST_RE = re.compile(
    r"\b(?:t.?test|ANOVA|analysis\s+of\s+variance|ANCOVA|MANOVA|"
    r"Pearson\s+(?:r|correlation)|linear\s+regression|"
    r"independent.?samples\s+t|paired\s+t.?test|one.?way\s+ANOVA)\b",
    re.IGNORECASE,
)
_NORMALITY_TEST_RE = re.compile(
    r"\b(?:Shapiro.Wilk|Kolmogorov.Smirnov|Anderson.Darling|"
    r"normality\s+(?:test|assumption|check)|Q.Q\s+plot|"
    r"normal\s+distribution\s+(?:was\s+)?(?:verified|confirmed|tested|assumed)|"
    r"data\s+(?:were\s+)?(?:normally\s+distributed|checked\s+for\s+normality)|"
    r"non.?parametric|distribution.?free)\b",
    re.IGNORECASE,
)


def validate_normal_distribution_assumption(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts using parametric tests without normality checks.

    Emits ``untested-normality-assumption`` (minor) when parametric tests are
    used but normality is neither tested nor explicitly assumed and justified.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="normal_distribution_assumption", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="normal_distribution_assumption", findings=[]
        )

    if not _PARAMETRIC_TEST_RE.search(full):
        return ValidationResult(
            validator_name="normal_distribution_assumption", findings=[]
        )

    if _NORMALITY_TEST_RE.search(full):
        return ValidationResult(
            validator_name="normal_distribution_assumption", findings=[]
        )

    return ValidationResult(
        validator_name="normal_distribution_assumption",
        findings=[
            Finding(
                code="untested-normality-assumption",
                severity="minor",
                message=(
                    "Parametric tests detected (t-test, ANOVA, Pearson r, etc.) but "
                    "normality of the data distribution is not tested or addressed. "
                    "Report normality test results or justify the parametric approach."
                ),
                validator="normal_distribution_assumption",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 173 – Figure axes labeling
# ---------------------------------------------------------------------------

_FIGURE_MENTION_RE = re.compile(
    r"\b(?:Figure|Fig\.?)\s*(\d+)\b",
    re.IGNORECASE,
)
_AXIS_LABEL_RE = re.compile(
    r"\b(?:x.?axis|y.?axis|horizontal\s+axis|vertical\s+axis|"
    r"axis\s+(?:label|title)|labeled?\s+(?:with|as)|"
    r"x.?label|y.?label|\bxlabel\b|\bylabel\b|"
    r"\\xlabel\{|\\ylabel\{)\b",
    re.IGNORECASE,
)
_FIGURE_MIN_DISTINCT = 2


def validate_figure_axes_labeling(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts with multiple distinct figures but no axis label documentation.

    Emits ``unlabeled-figure-axes`` (minor) when >= ``_FIGURE_MIN_DISTINCT``
    distinct figure numbers appear but no axis labels are described.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="figure_axes_labeling", findings=[]
        )

    fig_numbers = set(_FIGURE_MENTION_RE.findall(full))
    if len(fig_numbers) < _FIGURE_MIN_DISTINCT:
        return ValidationResult(
            validator_name="figure_axes_labeling", findings=[]
        )

    if _AXIS_LABEL_RE.search(full):
        return ValidationResult(
            validator_name="figure_axes_labeling", findings=[]
        )

    return ValidationResult(
        validator_name="figure_axes_labeling",
        findings=[
            Finding(
                code="unlabeled-figure-axes",
                severity="minor",
                message=(
                    f"Manuscript includes {len(fig_numbers)} distinct figure(s) but no "
                    "axis label documentation is found. "
                    "Ensure all figure axes are clearly labeled."
                ),
                validator="figure_axes_labeling",
                location="manuscript",
                evidence=[f"Figure {n}" for n in sorted(fig_numbers)[:3]],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 174 – Duplicate reporting (same values in table AND text)
# ---------------------------------------------------------------------------

_TABLE_VALUE_RE = re.compile(
    r"\b(?:Table\s+\d+|as\s+shown\s+in\s+Table|see\s+Table)\b",
    re.IGNORECASE,
)
_TEXT_DUPLICATE_RE = re.compile(
    r"\b(?:as\s+(?:reported|shown|presented|displayed|described)\s+in\s+Table\s+\d+|"
    r"the\s+(?:values?|data|results?|statistics?)\s+(?:in|from|presented\s+in)\s+Table\s+\d+\s+"
    r"(?:show|demonstrate|indicate)|"
    r"Table\s+\d+\s+(?:shows?|presents?|displays?)\s+the\s+(?:same|identical|aforementioned))\b",
    re.IGNORECASE,
)


def validate_duplicate_reporting(
    parsed: ParsedManuscript,
) -> ValidationResult:
    """Flag manuscripts explicitly redundantly describing table contents in text.

    Emits ``duplicate-reporting`` (major) when the text explicitly acknowledges
    repeating table-reported values, or uses language indicating the same
    statistics are narrated again.
    """
    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="duplicate_reporting", findings=[]
        )

    if not _TABLE_VALUE_RE.search(full):
        return ValidationResult(
            validator_name="duplicate_reporting", findings=[]
        )

    matches = _TEXT_DUPLICATE_RE.findall(full)
    if not matches:
        return ValidationResult(
            validator_name="duplicate_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="duplicate_reporting",
        findings=[
            Finding(
                code="duplicate-reporting",
                severity="major",
                message=(
                    f"Manuscript appears to repeat {len(matches)} table-reported "
                    "statistic(s) verbatim in the text. "
                    "Summarize or interpret table contents rather than re-listing values."
                ),
                validator="duplicate_reporting",
                location="Results",
                evidence=list(dict.fromkeys(matches[:2])),
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 175 – Response rate reporting for surveys
# ---------------------------------------------------------------------------

_SURVEY_DESIGN_RE = re.compile(
    r"\b(?:online\s+(?:survey|questionnaire)|mail(?:ed)?\s+(?:survey|questionnaire)|"
    r"phone\s+(?:survey|interview)|mailed\s+questionnaire|"
    r"postal\s+(?:survey|questionnaire)|email(?:ed)?\s+(?:survey|questionnaire)|"
    r"(?:web.?based|electronic)\s+(?:survey|questionnaire))\b",
    re.IGNORECASE,
)
_RESPONSE_RATE_RE = re.compile(
    r"\b(?:response\s+rate|participation\s+rate|completion\s+rate|"
    r"return\s+rate|response\s+(?:ratio|proportion)|"
    r"\d+\s*%\s*(?:of\s+(?:invitees?|recipients?|those\s+contacted|"
    r"eligible\s+(?:participants?|respondents?)))\s+responded|"
    r"responded\s+out\s+of\s+\d+\s+(?:invited|contacted|eligible))\b",
    re.IGNORECASE,
)


def validate_response_rate_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag survey studies not reporting response rate.

    Emits ``missing-response-rate`` (moderate) when an online/mailed survey
    design is detected but no response rate or participation rate is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="response_rate_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="response_rate_reporting", findings=[]
        )

    if not _SURVEY_DESIGN_RE.search(full):
        return ValidationResult(
            validator_name="response_rate_reporting", findings=[]
        )

    if _RESPONSE_RATE_RE.search(full):
        return ValidationResult(
            validator_name="response_rate_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="response_rate_reporting",
        findings=[
            Finding(
                code="missing-response-rate",
                severity="moderate",
                message=(
                    "Online or mailed survey design detected but no response rate "
                    "or participation rate is reported. "
                    "Report the response rate to allow assessment of non-response bias."
                ),
                validator="response_rate_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 176 – Longitudinal attrition bias
# ---------------------------------------------------------------------------

_LONGITUDINAL_DESIGN_RE = re.compile(
    r"\b(?:longitudinal|follow.?up\s+(?:study|assessment|visit)|"
    r"prospective\s+(?:cohort|study)|"
    r"repeated.?measures?|panel\s+(?:data|study)|"
    r"wave\s+\d+|wave\s+one|wave\s+two)\b",
    re.IGNORECASE,
)
_ATTRITION_BIAS_RE = re.compile(
    r"\b(?:attrition\s+(?:bias|analysis|rate|pattern)|"
    r"loss.to.follow.?up\s+(?:analysis|bias|pattern)|"
    r"missing\s+(?:at\s+random|not\s+at\s+random|completely\s+at\s+random)|"
    r"MCAR|MAR\b|MNAR\b|"
    r"(?:dropouts?|non.?completers?)\s+(?:did\s+not\s+differ|were\s+similar)|"
    r"Little['']s\s+test|Little.s\s+MCAR)\b",
    re.IGNORECASE,
)


def validate_longitudinal_attrition_bias(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal empirical studies that ignore attrition bias.

    Emits ``missing-attrition-bias-analysis`` (moderate) when longitudinal
    design is detected but no dropout or missing-data pattern analysis is
    reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="longitudinal_attrition_bias", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="longitudinal_attrition_bias", findings=[]
        )

    if not _LONGITUDINAL_DESIGN_RE.search(full):
        return ValidationResult(
            validator_name="longitudinal_attrition_bias", findings=[]
        )

    if _ATTRITION_BIAS_RE.search(full):
        return ValidationResult(
            validator_name="longitudinal_attrition_bias", findings=[]
        )

    return ValidationResult(
        validator_name="longitudinal_attrition_bias",
        findings=[
            Finding(
                code="missing-attrition-bias-analysis",
                severity="moderate",
                message=(
                    "Longitudinal study design detected but no attrition bias analysis "
                    "or missing-data mechanism (MCAR/MAR/MNAR) is reported. "
                    "Analyze and report patterns of participant dropout."
                ),
                validator="longitudinal_attrition_bias",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 177 – Dichotomization of continuous variables
# ---------------------------------------------------------------------------

_CONTINUOUS_DICHOTOMIZE_RE = re.compile(
    r"\b(?:dichotomiz(?:ed?|ing|ation)|binariz(?:ed?|ing)|"
    r"median\s+split|split\s+(?:at|by)\s+the\s+median|"
    r"cut.?point|cutoff\s+score|above\s+and\s+below\s+(?:the\s+)?median|"
    r"high(?:er)?\s+(?:vs?\.?|versus)\s+low(?:er)?\s+(?:group|score))\b",
    re.IGNORECASE,
)
_DICHOTOMIZE_JUSTIFY_RE = re.compile(
    r"\b(?:clinical\s+cut.?(?:off|point)|diagnostic\s+threshold|"
    r"validated\s+(?:cutoff|threshold)|established\s+cut.?(?:off|point)|"
    r"justified\s+(?:the\s+)?(?:cutoff|split|dichotomization)|"
    r"prior\s+(?:research|studies?)\s+(?:supports?|used|established))\b",
    re.IGNORECASE,
)


def validate_continuous_variable_dichotomization(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag unjustified dichotomization of continuous variables.

    Emits ``unjustified-dichotomization`` (moderate) when median splits or
    arbitrary cutoffs are applied without clinical or validated justification.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="continuous_variable_dichotomization", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="continuous_variable_dichotomization", findings=[]
        )

    if not _CONTINUOUS_DICHOTOMIZE_RE.search(full):
        return ValidationResult(
            validator_name="continuous_variable_dichotomization", findings=[]
        )

    if _DICHOTOMIZE_JUSTIFY_RE.search(full):
        return ValidationResult(
            validator_name="continuous_variable_dichotomization", findings=[]
        )

    return ValidationResult(
        validator_name="continuous_variable_dichotomization",
        findings=[
            Finding(
                code="unjustified-dichotomization",
                severity="moderate",
                message=(
                    "Dichotomization or median split of a continuous variable detected "
                    "without clinical, validated, or theoretically justified cutoff. "
                    "Justify the cutpoint or retain the continuous variable."
                ),
                validator="continuous_variable_dichotomization",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 178 – Outcome measure validation
# ---------------------------------------------------------------------------

_OUTCOME_MEASURE_RE = re.compile(
    r"\b(?:(?:primary|secondary|main|outcome)\s+(?:measure|outcome|variable)|"
    r"(?:scale|instrument|questionnaire|inventory|index)\s+"
    r"(?:was\s+used|used\s+to\s+measure|assessed|measured))\b",
    re.IGNORECASE,
)
_MEASURE_VALIDITY_RE = re.compile(
    r"\b(?:valid(?:ated?|ity)|reliability|Cronbach|internal\s+consistency|"
    r"test.?retest|inter.?rater|convergent\s+validity|discriminant\s+validity|"
    r"psychometric(?:ally)?|standardized\s+(?:instrument|measure|scale)|"
    r"normed\s+(?:instrument|measure|sample))\b",
    re.IGNORECASE,
)


def validate_outcome_measure_validation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies that omit psychometric evidence for measures.

    Emits ``missing-measure-validity`` (moderate) when outcome measures are
    described but no psychometric validity or reliability evidence is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="outcome_measure_validation", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="outcome_measure_validation", findings=[]
        )

    if not _OUTCOME_MEASURE_RE.search(methods_text):
        return ValidationResult(
            validator_name="outcome_measure_validation", findings=[]
        )

    if _MEASURE_VALIDITY_RE.search(methods_text):
        return ValidationResult(
            validator_name="outcome_measure_validation", findings=[]
        )

    return ValidationResult(
        validator_name="outcome_measure_validation",
        findings=[
            Finding(
                code="missing-measure-validity",
                severity="moderate",
                message=(
                    "Outcome measure or instrument mentioned in Methods but no "
                    "psychometric validity or reliability evidence is provided. "
                    "Report validated properties (Cronbach's alpha, test-retest reliability, etc.)."
                ),
                validator="outcome_measure_validation",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 179 – Outlier handling disclosure
# ---------------------------------------------------------------------------

_OUTLIER_MENTION_RE = re.compile(
    r"\b(?:outliers?|extreme\s+(?:values?|observations?|scores?|cases?)|"
    r"influential\s+(?:observations?|cases?|points?)|"
    r"Cook.s\s+distance|leverage\s+(?:points?|values?)|"
    r"Mahalanobis|Grubbs|Rosner\s+test)\b",
    re.IGNORECASE,
)
_OUTLIER_HANDLING_RE = re.compile(
    r"\b(?:outliers?\s+(?:were|wer)\s+(?:removed?|excluded?|retained?|winsorized?|"
    r"replaced?|handled?|identified?|screened?|inspected?)|"
    r"(?:removed?|excluded?|retained?|winsorized?)\s+outliers?|"
    r"(?:no|zero)\s+outliers?\s+(?:were|wer)|"
    r"sensitivity\s+analysis\s+(?:with|excluding|including))\b",
    re.IGNORECASE,
)


def validate_outlier_handling_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies that mention outliers without disclosing handling.

    Emits ``missing-outlier-handling`` (minor) when outliers are mentioned
    but no explicit handling decision (removal, retention, winsorization) is stated.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="outlier_handling_disclosure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="outlier_handling_disclosure", findings=[]
        )

    if not _OUTLIER_MENTION_RE.search(full):
        return ValidationResult(
            validator_name="outlier_handling_disclosure", findings=[]
        )

    if _OUTLIER_HANDLING_RE.search(full):
        return ValidationResult(
            validator_name="outlier_handling_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="outlier_handling_disclosure",
        findings=[
            Finding(
                code="missing-outlier-handling",
                severity="minor",
                message=(
                    "Outliers are mentioned but no explicit outlier-handling decision "
                    "(removal, retention, winsorization) is disclosed. "
                    "State the outlier criterion and how outliers were treated."
                ),
                validator="outlier_handling_disclosure",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 180 – Missing confidence interval for main effect
# ---------------------------------------------------------------------------

_MAIN_EFFECT_RE = re.compile(
    r"\b(?:main\s+effect|primary\s+(?:outcome|result|finding|effect)|"
    r"key\s+(?:finding|result)|overall\s+effect|"
    r"primary\s+analysis\s+(?:showed?|indicated?|revealed?|found))\b",
    re.IGNORECASE,
)
_CI_PRESENT_RE = re.compile(
    r"\b(?:95\s*%\s*(?:CI|confidence\s+interval)|"
    r"confidence\s+interval|CI\s*[:=\[(\{]|\[\s*\d|\(\s*\d\s*\.\s*\d|"
    r"CI\s+was\s+\[?\d|CI\s*=\s*\[?\d)\b",
    re.IGNORECASE,
)


def validate_main_effect_confidence_interval(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts that report main effects without CIs.

    Emits ``missing-main-effect-ci`` (moderate) when primary/main effects are
    mentioned but no confidence interval is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="main_effect_confidence_interval", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="main_effect_confidence_interval", findings=[]
        )

    if not _MAIN_EFFECT_RE.search(full):
        return ValidationResult(
            validator_name="main_effect_confidence_interval", findings=[]
        )

    if _CI_PRESENT_RE.search(full):
        return ValidationResult(
            validator_name="main_effect_confidence_interval", findings=[]
        )

    return ValidationResult(
        validator_name="main_effect_confidence_interval",
        findings=[
            Finding(
                code="missing-main-effect-ci",
                severity="moderate",
                message=(
                    "Primary or main effect described but no confidence interval is "
                    "reported. Provide 95% CIs for all main effects."
                ),
                validator="main_effect_confidence_interval",
                location="Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 181 – Missing covariates justification
# ---------------------------------------------------------------------------

_COVARIATE_RE = re.compile(
    r"\b(?:covariate|covariates|covaried|covariance\s+(?:matrix|structure)|"
    r"adjusted?\s+(?:for|by)\s+(?:\w+\s*){1,4}|"
    r"controlled?\s+for\s+(?:\w+\s*){1,3}|"
    r"ANCOVA|hierarchical\s+regression|modeled?\s+as\s+(?:a\s+)?covariate)\b",
    re.IGNORECASE,
)
_COVARIATE_JUSTIFY_RE = re.compile(
    r"\b(?:based\s+on\s+(?:prior|previous|theoretical|empirical)|"
    r"known\s+(?:confounder|covariate|predictor)|"
    r"literature\s+(?:suggests?|indicates?|shows?)|"
    r"prior\s+(?:research|evidence|studies?)\s+(?:suggests?|indicates?|shows?)|"
    r"theoretically\s+(?:motivated|justified|relevant)|"
    r"control\s+for\s+(?:known|potential)\s+confound)\b",
    re.IGNORECASE,
)


def validate_covariate_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies that include covariates without theoretical justification.

    Emits ``missing-covariate-justification`` (minor) when covariates or adjustments
    are used but no theoretical or empirical rationale is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="covariate_justification", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="covariate_justification", findings=[]
        )

    if not _COVARIATE_RE.search(methods_text):
        return ValidationResult(
            validator_name="covariate_justification", findings=[]
        )

    if _COVARIATE_JUSTIFY_RE.search(methods_text):
        return ValidationResult(
            validator_name="covariate_justification", findings=[]
        )

    return ValidationResult(
        validator_name="covariate_justification",
        findings=[
            Finding(
                code="missing-covariate-justification",
                severity="minor",
                message=(
                    "Covariates or adjusted analyses are used but no theoretical or "
                    "empirical justification is provided for their inclusion. "
                    "Justify covariate selection based on theory or prior evidence."
                ),
                validator="covariate_justification",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 182 – Gender/sex conflation
# ---------------------------------------------------------------------------

_GENDER_SEX_RE = re.compile(
    r"\b(?:gender|sex)\b",
    re.IGNORECASE,
)
_GENDER_SEX_CONFLATE_RE = re.compile(
    r"\b(?:gender\s+(?:was|were)\s+(?:male|female|man|woman|men|women)|"
    r"sex\s+(?:was|were)\s+(?:male|female|man|woman|men|women)|"
    r"(?:male|female)\s+gender|gender\s*[=:]\s*(?:male|female)|"
    r"sex\s*(?:/|and)\s*gender\b|gender\s*(?:/|and)\s*sex\b)\b",
    re.IGNORECASE,
)
_GENDER_SEX_DISTINCT_RE = re.compile(
    r"\b(?:biological\s+sex|gender\s+identity|assigned\s+sex|sex\s+at\s+birth|"
    r"sex\s+and\s+gender\s+are\s+distinct|gender\s+versus\s+sex)\b",
    re.IGNORECASE,
)


def validate_gender_sex_conflation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag conflation of sex and gender without explicit distinction.

    Emits ``gender-sex-conflation`` (minor) when sex and gender are used
    interchangeably without acknowledging the conceptual distinction.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="gender_sex_conflation", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="gender_sex_conflation", findings=[]
        )

    if not _GENDER_SEX_RE.search(full):
        return ValidationResult(
            validator_name="gender_sex_conflation", findings=[]
        )

    if _GENDER_SEX_DISTINCT_RE.search(full):
        return ValidationResult(
            validator_name="gender_sex_conflation", findings=[]
        )

    if not _GENDER_SEX_CONFLATE_RE.search(full):
        return ValidationResult(
            validator_name="gender_sex_conflation", findings=[]
        )

    return ValidationResult(
        validator_name="gender_sex_conflation",
        findings=[
            Finding(
                code="gender-sex-conflation",
                severity="minor",
                message=(
                    "Gender and sex appear to be used interchangeably without "
                    "acknowledging the distinction between biological sex and gender identity. "
                    "Clarify whether the study measured biological sex, gender identity, or both."
                ),
                validator="gender_sex_conflation",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 183 – Multicollinearity reporting in regression
# ---------------------------------------------------------------------------

_REGRESSION_MODEL_RE = re.compile(
    r"\b(?:multiple\s+(?:linear|logistic)?\s*regression|"
    r"hierarchical\s+regression|stepwise\s+regression|"
    r"ordinary\s+least\s+squares|OLS\s+regression|"
    r"logistic\s+regression|poisson\s+regression|"
    r"cox\s+(?:regression|proportional))\b",
    re.IGNORECASE,
)
_MULTICOLLINEARITY_RE = re.compile(
    r"\b(?:multicollinearity|variance\s+inflation\s+factor|VIF\b|"
    r"tolerance\s+(?:statistic|value)|condition\s+(?:index|number)|"
    r"correlation\s+matrix|bivariate\s+correlation\s+(?:among|between)\s+predictors?|"
    r"predictors?\s+(?:were|are)\s+not\s+(?:highly\s+)?correlated)\b",
    re.IGNORECASE,
)


def validate_multicollinearity_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression studies that omit multicollinearity assessment.

    Emits ``missing-multicollinearity-check`` (minor) when regression with
    multiple predictors is used but no multicollinearity assessment is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="multicollinearity_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="multicollinearity_reporting", findings=[]
        )

    if not _REGRESSION_MODEL_RE.search(full):
        return ValidationResult(
            validator_name="multicollinearity_reporting", findings=[]
        )

    if _MULTICOLLINEARITY_RE.search(full):
        return ValidationResult(
            validator_name="multicollinearity_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="multicollinearity_reporting",
        findings=[
            Finding(
                code="missing-multicollinearity-check",
                severity="minor",
                message=(
                    "Multiple regression model detected but no multicollinearity "
                    "assessment (VIF, tolerance, condition index) is reported. "
                    "Check and report multicollinearity statistics for predictors."
                ),
                validator="multicollinearity_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 184 – Placebo or active control group reporting
# ---------------------------------------------------------------------------

_RCT_RE = re.compile(
    r"\b(?:randomized?\s+(?:controlled?|clinical)?\s*trial|RCT\b|"
    r"randomly\s+assigned?|random\s+assignment|"
    r"treatment\s+(?:arm|group|condition)\s+vs\.?\s+control|"
    r"intervention\s+(?:group|condition)\s+(?:vs\.?|versus|compared\s+to)\s+control)\b",
    re.IGNORECASE,
)
_CONTROL_TYPE_RE = re.compile(
    r"\b(?:placebo(?:\s+control)?|active\s+control|waitlist\s+control|"
    r"treatment.?as.?usual|TAU\b|sham\s+(?:control|condition)|"
    r"active\s+comparison|comparison\s+condition|"
    r"no.?treatment\s+control|attention\s+control)\b",
    re.IGNORECASE,
)


def validate_control_group_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCTs that don't specify the type of control condition.

    Emits ``missing-control-group-type`` (moderate) when an RCT design is
    detected but the control condition type (placebo, active, waitlist, TAU) is
    not specified.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="control_group_description", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="control_group_description", findings=[]
        )

    if not _RCT_RE.search(full):
        return ValidationResult(
            validator_name="control_group_description", findings=[]
        )

    if _CONTROL_TYPE_RE.search(full):
        return ValidationResult(
            validator_name="control_group_description", findings=[]
        )

    return ValidationResult(
        validator_name="control_group_description",
        findings=[
            Finding(
                code="missing-control-group-type",
                severity="moderate",
                message=(
                    "RCT or randomized controlled design detected but the type of "
                    "control condition (placebo, active control, waitlist, TAU) is "
                    "not specified. Clearly describe what the control group received."
                ),
                validator="control_group_description",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 185 – Heteroscedasticity testing
# ---------------------------------------------------------------------------

_HETEROSCEDASTICITY_TRIGGER_RE = re.compile(
    r"\b(?:multiple\s+(?:linear\s+)?regression|OLS\s+regression|"
    r"ordinary\s+least\s+squares|linear\s+regression\s+model)\b",
    re.IGNORECASE,
)
_HETEROSCEDASTICITY_CHECK_RE = re.compile(
    r"\b(?:heteroscedasticity|homoscedasticity|"
    r"Breusch.Pagan|White\s+test|Goldfeld.Quandt|"
    r"residual\s+(?:plot|variance)|"
    r"constant\s+(?:error\s+variance|residual\s+variance)|"
    r"equal\s+(?:error\s+)?variances?|"
    r"robust\s+standard\s+errors?)\b",
    re.IGNORECASE,
)


def validate_heteroscedasticity_testing(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag OLS regression studies that omit heteroscedasticity checks.

    Emits ``missing-heteroscedasticity-check`` (minor) when OLS regression
    is used but residual variance / heteroscedasticity is not assessed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="heteroscedasticity_testing", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="heteroscedasticity_testing", findings=[]
        )

    if not _HETEROSCEDASTICITY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="heteroscedasticity_testing", findings=[]
        )

    if _HETEROSCEDASTICITY_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="heteroscedasticity_testing", findings=[]
        )

    return ValidationResult(
        validator_name="heteroscedasticity_testing",
        findings=[
            Finding(
                code="missing-heteroscedasticity-check",
                severity="minor",
                message=(
                    "OLS or linear regression detected but no heteroscedasticity "
                    "assessment (Breusch-Pagan, White test, residual plots, robust SEs) "
                    "is reported. Check and report residual variance assumption."
                ),
                validator="heteroscedasticity_testing",
                location="Methods",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 186 – Missing interaction effect interpretation
# ---------------------------------------------------------------------------

_INTERACTION_TERM_RE = re.compile(
    r"\b(?:interaction\s+(?:effect|term|between|of)|"
    r"moderating\s+effect|moderation\s+analysis|"
    r"two.?way\s+interaction|three.?way\s+interaction|"
    r"interaction\s+was\s+(?:significant|found|detected|observed)|"
    r"A\s*[×x\*]\s*B\s+interaction)\b",
    re.IGNORECASE,
)
_INTERACTION_INTERPRET_RE = re.compile(
    r"\b(?:simple\s+(?:slope|effect)|spotlight\s+analysis|"
    r"follow.?up\s+(?:analysis|test|probing)|"
    r"decomposed?\s+(?:the\s+)?interaction|"
    r"probing\s+(?:the\s+)?interaction|"
    r"Johnson.?Neyman|Floodlight\s+analysis|"
    r"at\s+(?:high|low)\s+levels?\s+of|"
    r"when\s+\w+\s+(?:is|was)\s+(?:high|low))\b",
    re.IGNORECASE,
)


def validate_interaction_effect_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies that report interactions without probing/decomposing.

    Emits ``missing-interaction-probing`` (moderate) when a significant
    interaction is reported but no simple slopes, spotlight, or region-of-
    significance analysis is presented.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="interaction_effect_interpretation", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="interaction_effect_interpretation", findings=[]
        )

    if not _INTERACTION_TERM_RE.search(full):
        return ValidationResult(
            validator_name="interaction_effect_interpretation", findings=[]
        )

    if _INTERACTION_INTERPRET_RE.search(full):
        return ValidationResult(
            validator_name="interaction_effect_interpretation", findings=[]
        )

    return ValidationResult(
        validator_name="interaction_effect_interpretation",
        findings=[
            Finding(
                code="missing-interaction-probing",
                severity="moderate",
                message=(
                    "Interaction effect detected but no follow-up probing (simple slopes, "
                    "spotlight analysis, Johnson-Neyman) is reported. "
                    "Decompose significant interactions to aid interpretation."
                ),
                validator="interaction_effect_interpretation",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 187 – Hypothesis pre-registration vs. post-hoc framing
# ---------------------------------------------------------------------------

_POST_HOC_EXPLORE_RE = re.compile(
    r"\b(?:post.?hoc\s+(?:analysis|comparison|test|exploration)|"
    r"exploratory\s+(?:analysis|investigation|finding)|"
    r"additional\s+analyses?\s+(?:revealed?|showed?|found)|"
    r"we\s+(?:also\s+)?(?:explored?|examined?|investigated?)\s+"
    r"(?:whether|if|the\s+(?:relationship|association|effect))\b|"
    r"unexpected\s+(?:finding|result|association))\b",
    re.IGNORECASE,
)
_POST_HOC_LABEL_RE = re.compile(
    r"\b(?:labeled?\s+(?:as\s+)?(?:exploratory|post.?hoc)|"
    r"exploratory\s+and\s+(?:should\s+be|not)\s+(?:considered?|interpreted?)|"
    r"post.?hoc\s+and\s+(?:should\s+be|must\s+be)\s+(?:considered?|interpreted?)|"
    r"confirmed?\s+in\s+future\s+(?:studies?|research)|"
    r"hypothesis.?generating|preliminary\s+and\s+exploratory)\b",
    re.IGNORECASE,
)


def validate_post_hoc_framing(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag post-hoc analyses not labelled as exploratory.

    Emits ``post-hoc-not-labelled`` (moderate) when post-hoc or exploratory
    analyses are conducted but not explicitly disclosed as such.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="post_hoc_framing", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="post_hoc_framing", findings=[]
        )

    if not _POST_HOC_EXPLORE_RE.search(full):
        return ValidationResult(
            validator_name="post_hoc_framing", findings=[]
        )

    if _POST_HOC_LABEL_RE.search(full):
        return ValidationResult(
            validator_name="post_hoc_framing", findings=[]
        )

    return ValidationResult(
        validator_name="post_hoc_framing",
        findings=[
            Finding(
                code="post-hoc-not-labelled",
                severity="moderate",
                message=(
                    "Post-hoc or exploratory analyses detected but not explicitly "
                    "labelled as exploratory or hypothesis-generating. "
                    "Clearly distinguish confirmatory from exploratory findings."
                ),
                validator="post_hoc_framing",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 188 – Multiple comparison correction
# ---------------------------------------------------------------------------

_MULTIPLE_TESTS_RE = re.compile(
    r"\b(?:multiple\s+(?:comparisons?|tests?)|"
    r"we\s+(?:conducted?|performed?|ran?)\s+"
    r"(?:\d+|several|multiple|numerous)\s+(?:tests?|comparisons?|analyses?)|"
    r"family.?wise\s+error|type\s+I\s+error\s+(?:inflation|rate)\b)\b",
    re.IGNORECASE,
)
_CORRECTION_RE = re.compile(
    r"\b(?:Bonferroni|Holm|Benjamini.Hochberg|BH\s+correction|FDR\s+correction|"
    r"false\s+discovery\s+rate|family.?wise\s+error\s+(?:rate\s+)?correction|"
    r"adjusted\s+alpha|Sidak|Tukey|Scheffe|corrected?\s+for\s+multiple\s+comparisons?)\b",
    re.IGNORECASE,
)


def validate_multiple_comparison_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies with multiple comparisons that omit correction procedures.

    Emits ``missing-multiple-comparison-correction`` (moderate) when multiple
    tests are mentioned but no correction procedure is applied or justified.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="multiple_comparison_correction", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="multiple_comparison_correction", findings=[]
        )

    if not _MULTIPLE_TESTS_RE.search(full):
        return ValidationResult(
            validator_name="multiple_comparison_correction", findings=[]
        )

    if _CORRECTION_RE.search(full):
        return ValidationResult(
            validator_name="multiple_comparison_correction", findings=[]
        )

    return ValidationResult(
        validator_name="multiple_comparison_correction",
        findings=[
            Finding(
                code="missing-multiple-comparison-correction",
                severity="moderate",
                message=(
                    "Multiple comparisons or tests detected but no correction procedure "
                    "(Bonferroni, Holm, FDR/BH) is applied or discussed. "
                    "Apply or justify omission of a correction for multiple comparisons."
                ),
                validator="multiple_comparison_correction",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 189 – Publication bias statement (meta-analyses)
# ---------------------------------------------------------------------------

_META_ANALYSIS_RE = re.compile(
    r"\b(?:meta.?analysis|systematic\s+review\s+(?:and\s+)?meta.?analysis|"
    r"pooled\s+(?:effect\s+size|estimate)|"
    r"random.?effects?\s+model|fixed.?effects?\s+model|"
    r"summary\s+(?:effect\s+size|estimate)|forest\s+plot)\b",
    re.IGNORECASE,
)
_PUBLICATION_BIAS_RE = re.compile(
    r"\b(?:publication\s+bias|funnel\s+plot|Egger.s\s+test|"
    r"Begg.s\s+test|trim.?and.?fill|fail.?safe\s+N|"
    r"Rosenthal.s\s+fail.?safe|selection\s+bias\s+(?:in\s+)?(?:the\s+)?literature|"
    r"small.?study\s+effects?|reporting\s+bias)\b",
    re.IGNORECASE,
)


def validate_publication_bias_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag meta-analyses that omit publication bias assessment.

    Emits ``missing-publication-bias-statement`` (major) when meta-analytic
    methods are detected but no publication bias assessment is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="publication_bias_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="publication_bias_statement", findings=[]
        )

    if not _META_ANALYSIS_RE.search(full):
        return ValidationResult(
            validator_name="publication_bias_statement", findings=[]
        )

    if _PUBLICATION_BIAS_RE.search(full):
        return ValidationResult(
            validator_name="publication_bias_statement", findings=[]
        )

    return ValidationResult(
        validator_name="publication_bias_statement",
        findings=[
            Finding(
                code="missing-publication-bias-statement",
                severity="major",
                message=(
                    "Meta-analysis detected but no publication bias assessment "
                    "(funnel plot, Egger's test, trim-and-fill, fail-safe N) is reported. "
                    "Assess and report publication bias in all meta-analyses."
                ),
                validator="publication_bias_statement",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 190 – Missing degrees of freedom
# ---------------------------------------------------------------------------

_INFERENTIAL_STAT_RE = re.compile(
    r"\b(?:t\s*\(|F\s*\(|chi.?square\s*\(|χ²?\s*\(|"
    r"z\s*=\s*\d|t\s*=\s*[\d\-]|F\s*=\s*[\d\-])",
    re.IGNORECASE,
)
_DF_PRESENT_RE = re.compile(
    r"(?:df\s*=\s*\d|d\.f\.\s*=\s*\d|degrees?\s+of\s+freedom\s*=\s*\d|"
    r"t\s*\(\s*\d+\s*\)|F\s*\(\s*\d+\s*,\s*\d+\s*\)|"
    r"chi.?square\s*\(\s*\d+\s*\)|χ²?\s*\(\s*\d+\s*\))",
    re.IGNORECASE,
)


def validate_degrees_of_freedom_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical papers reporting statistics without degrees of freedom.

    Emits ``missing-degrees-of-freedom`` (minor) when t, F, or chi-square
    statistics are referenced but no degrees of freedom are shown.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="degrees_of_freedom_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="degrees_of_freedom_reporting", findings=[]
        )

    if not _INFERENTIAL_STAT_RE.search(full):
        return ValidationResult(
            validator_name="degrees_of_freedom_reporting", findings=[]
        )

    if _DF_PRESENT_RE.search(full):
        return ValidationResult(
            validator_name="degrees_of_freedom_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="degrees_of_freedom_reporting",
        findings=[
            Finding(
                code="missing-degrees-of-freedom",
                severity="minor",
                message=(
                    "Statistical test results (t, F, chi-square) detected but no "
                    "degrees of freedom are reported. "
                    "Report degrees of freedom with all inferential statistics, "
                    "e.g., t(df)=value or F(df1,df2)=value."
                ),
                validator="degrees_of_freedom_reporting",
                location="Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 191 – Missing power analysis or justification
# ---------------------------------------------------------------------------

_POWER_ANALYSIS_JUSTIFY_RE = re.compile(
    r"\b(?:power\s+analysis|statistical\s+power|a\s+priori\s+(?:power|sample\s+size)|"
    r"G\*Power|G\.Power|GPower|"
    r"sample\s+size\s+(?:was\s+)?(?:determined|calculated|estimated|justified)\s+"
    r"(?:using|based\s+on|via|from)|"
    r"powered\s+to\s+detect|"
    r"80\s*%\s*power|90\s*%\s*power|"
    r"post.?hoc\s+power)\b",
    re.IGNORECASE,
)
_POWER_SAMPLE_TRIGGER_RE = re.compile(
    r"\b(?:sample\s+size\s+(?:of|was|=|n\s*=)\s*\d+|"
    r"n\s*=\s*\d+\s+(?:participants?|subjects?|individuals?|patients?)|"
    r"\d+\s+(?:participants?|subjects?|adults?|patients?)\s+(?:were|was)\s+"
    r"(?:enrolled?|recruited?|included?|studied))\b",
    re.IGNORECASE,
)


def validate_power_analysis_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies that don't justify sample size with power analysis.

    Emits ``missing-power-analysis`` (moderate) when a sample size is reported
    but no a priori or post-hoc power analysis justification is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="power_analysis_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="power_analysis_reporting", findings=[]
        )

    if not _POWER_SAMPLE_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="power_analysis_reporting", findings=[]
        )

    if _POWER_ANALYSIS_JUSTIFY_RE.search(full):
        return ValidationResult(
            validator_name="power_analysis_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="power_analysis_reporting",
        findings=[
            Finding(
                code="missing-power-analysis",
                severity="moderate",
                message=(
                    "Sample size is reported but no power analysis or sample size "
                    "justification is provided. "
                    "Report an a priori power analysis or justify the sample size."
                ),
                validator="power_analysis_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 192 – Incomplete demographic description
# ---------------------------------------------------------------------------

_DEMOGRAPHIC_TRIGGERS = frozenset(
    {"empirical_paper", "applied_stats_paper", "software_workflow_paper"}
)
_DEMOGRAPHIC_RE = re.compile(
    r"\b(?:participants?|subjects?|respondents?|sample)\b",
    re.IGNORECASE,
)
_DEMOGRAPHIC_DETAIL_RE = re.compile(
    r"\b(?:(?:mean\s+)?age\s*(?:=|was|M\s*=)|"
    r"(?:male|female|men|women)\s+(?:participants?|\d+\s*%|were)|"
    r"\d+\s*%\s*(?:male|female|men|women)|"
    r"(?:White|Black|Hispanic|Asian|multiracial|race|ethnicity)\s*"
    r"(?:\(|\d+\s*%)|"
    r"education(?:al\s+level)?\s*(?:=|M\s*=|was))\b",
    re.IGNORECASE,
)


def validate_demographic_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies with participants but no demographics reported.

    Emits ``missing-demographic-description`` (minor) when participants are
    mentioned but no demographic details (age, gender, education, race) appear.
    """
    if classification.paper_type not in _DEMOGRAPHIC_TRIGGERS:
        return ValidationResult(
            validator_name="demographic_description", findings=[]
        )

    methods_text = " ".join(
        s.body for s in parsed.sections if s.title and "method" in s.title.lower()
    )
    if not methods_text:
        return ValidationResult(
            validator_name="demographic_description", findings=[]
        )

    if not _DEMOGRAPHIC_RE.search(methods_text):
        return ValidationResult(
            validator_name="demographic_description", findings=[]
        )

    if _DEMOGRAPHIC_DETAIL_RE.search(methods_text):
        return ValidationResult(
            validator_name="demographic_description", findings=[]
        )

    return ValidationResult(
        validator_name="demographic_description",
        findings=[
            Finding(
                code="missing-demographic-description",
                severity="minor",
                message=(
                    "Participants are mentioned but no demographic details "
                    "(age, gender, education, race/ethnicity) are reported. "
                    "Describe sample demographics to allow assessment of generalizability."
                ),
                validator="demographic_description",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 193 – Randomization procedure transparency
# ---------------------------------------------------------------------------

_RANDOM_ASSIGN_RE = re.compile(
    r"\b(?:randomly\s+assigned?|random\s+assignment|randomized?\s+(?:to|into)|"
    r"group\s+assignment\s+(?:was\s+)?(?:random|conducted\s+randomly)|"
    r"allocation\s+was\s+(?:random|randomized?))\b",
    re.IGNORECASE,
)
_RANDOMIZATION_METHOD_RE = re.compile(
    r"\b(?:random\s+number\s+(?:generator|table|sequence)|"
    r"computer.?generated\s+randomization|block\s+randomization|"
    r"stratified\s+randomization|simple\s+randomization|"
    r"sealed\s+envelopes?|central\s+randomization|"
    r"allocation\s+(?:sequence|concealment)|CONSORT)\b",
    re.IGNORECASE,
)


def validate_randomization_procedure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCTs with randomization claims but no randomization method.

    Emits ``missing-randomization-procedure`` (moderate) when random assignment
    is mentioned but the randomization method is not described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="randomization_procedure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="randomization_procedure", findings=[]
        )

    if not _RANDOM_ASSIGN_RE.search(full):
        return ValidationResult(
            validator_name="randomization_procedure", findings=[]
        )

    if _RANDOMIZATION_METHOD_RE.search(full):
        return ValidationResult(
            validator_name="randomization_procedure", findings=[]
        )

    return ValidationResult(
        validator_name="randomization_procedure",
        findings=[
            Finding(
                code="missing-randomization-procedure",
                severity="moderate",
                message=(
                    "Random assignment is mentioned but the randomization procedure "
                    "(e.g., random number generator, block randomization, allocation "
                    "concealment) is not described. "
                    "Report the randomization method for reproducibility."
                ),
                validator="randomization_procedure",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 194 – Generalizability / external validity caveat
# ---------------------------------------------------------------------------

_STRONG_GENERALIZE_RE = re.compile(
    r"\b(?:(?:these\s+)?results?\s+(?:can\s+be|are)\s+generalized?\s+"
    r"(?:to\s+|across\s+)?(?:all|any|most|the\s+general\s+population)|"
    r"findings?\s+(?:apply|are\s+applicable)\s+to\s+(?:all|any|broader)|"
    r"broadly\s+applicable|universally\s+applicable|"
    r"applies?\s+to\s+(?:all|any)\s+(?:populations?|contexts?|settings?))\b",
    re.IGNORECASE,
)
_GENERALIZABILITY_CAVEAT_RE = re.compile(
    r"\b(?:limitations?\s+(?:of|include|in)\s+"
    r"(?:this\s+)?(?:study|sample|generalizability)|"
    r"may\s+not\s+generalize|limited\s+generalizability|"
    r"should\s+be\s+(?:cautiously\s+)?(?:generalized?|interpreted)|"
    r"(?:specific|restricted)\s+(?:sample|population|context)|"
    r"caution\s+(?:in\s+)?generaliz|external\s+validity)\b",
    re.IGNORECASE,
)


def validate_generalizability_caveat(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag overly strong generalizability claims without appropriate caveats.

    Emits ``overclaimed-generalizability`` (moderate) when results are
    described as broadly/universally applicable without any caveat.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="generalizability_caveat", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="generalizability_caveat", findings=[]
        )

    if not _STRONG_GENERALIZE_RE.search(full):
        return ValidationResult(
            validator_name="generalizability_caveat", findings=[]
        )

    if _GENERALIZABILITY_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="generalizability_caveat", findings=[]
        )

    return ValidationResult(
        validator_name="generalizability_caveat",
        findings=[
            Finding(
                code="overclaimed-generalizability",
                severity="moderate",
                message=(
                    "Results are described as broadly or universally generalizable "
                    "without appropriate caveats about study limitations. "
                    "Discuss constraints on generalizability based on sample, setting, "
                    "and design."
                ),
                validator="generalizability_caveat",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 195 – Missing software/tools version reporting
# ---------------------------------------------------------------------------

_SOFTWARE_CITE_RE = re.compile(
    r"\b(?:R\s+(?:version\s+\d|v\d)|Python\s+(?:version\s+\d|v\d|\d\.\d)|"
    r"SPSS\s+(?:version\s+\d|v\d|\d+)|SAS\s+(?:version\s+\d|v\d|\d+)|"
    r"Stata\s+(?:version\s+\d|v\d|\d+)|MATLAB\s+(?:version|R\d{4})|"
    r"Mplus\s+(?:version\s+\d|v\d|\d+)|"
    r"jamovi\s+(?:version\s+\d|v\d|\d+)|"
    r"JASP\s+(?:version\s+\d|v\d|\d+))\b",
    re.IGNORECASE,
)
_SOFTWARE_USE_RE = re.compile(
    r"\b(?:analysis(?:es)?\s+(?:were|was)\s+(?:conducted?|performed?|run|done)\s+"
    r"(?:in|using|with)\s+(?:R|Python|SPSS|SAS|Stata|MATLAB|Mplus|jamovi|JASP)|"
    r"(?:R|Python|SPSS|SAS|Stata|MATLAB|Mplus|jamovi|JASP)\s+"
    r"(?:was|were)\s+used\s+(?:for|to)|"
    r"all\s+analyses?\s+(?:used|using)\s+(?:R|Python|SPSS|SAS|Stata))\b",
    re.IGNORECASE,
)


def validate_software_version_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts that name software without version numbers.

    Emits ``missing-software-version`` (minor) when statistical software is
    named as the analysis tool but no version number is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="software_version_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="software_version_reporting", findings=[]
        )

    if not _SOFTWARE_USE_RE.search(full):
        return ValidationResult(
            validator_name="software_version_reporting", findings=[]
        )

    if _SOFTWARE_CITE_RE.search(full):
        return ValidationResult(
            validator_name="software_version_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="software_version_reporting",
        findings=[
            Finding(
                code="missing-software-version",
                severity="minor",
                message=(
                    "Statistical software is named but no version number is reported. "
                    "Cite the software version (e.g., R version 4.3.2, SPSS version 29) "
                    "for reproducibility."
                ),
                validator="software_version_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 196 – IRB / ethics approval statement
# ---------------------------------------------------------------------------

_HUMAN_SUBJECTS_RE = re.compile(
    r"\b(?:participants?|subjects?|respondents?|human\s+(?:subjects?|participants?))\b",
    re.IGNORECASE,
)
_ETHICS_APPROVAL_RE = re.compile(
    r"\b(?:IRB|institutional\s+review\s+board|ethics\s+(?:committee|board|approval)|"
    r"ethical\s+approval|Helsinki|informed\s+consent|"
    r"approved?\s+by\s+(?:the\s+)?(?:IRB|university|institution)|"
    r"exempt\s+(?:from|under)\s+(?:IRB|review)|"
    r"HIPAA|protocol\s+approval|data\s+use\s+agreement)\b",
    re.IGNORECASE,
)


def validate_ethics_approval_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical studies involving human subjects without ethics approval.

    Emits ``missing-ethics-approval`` (major) when human participants are
    mentioned but no IRB, ethics approval, or informed consent statement appears.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="ethics_approval_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="ethics_approval_statement", findings=[]
        )

    if not _HUMAN_SUBJECTS_RE.search(full):
        return ValidationResult(
            validator_name="ethics_approval_statement", findings=[]
        )

    if _ETHICS_APPROVAL_RE.search(full):
        return ValidationResult(
            validator_name="ethics_approval_statement", findings=[]
        )

    return ValidationResult(
        validator_name="ethics_approval_statement",
        findings=[
            Finding(
                code="missing-ethics-approval",
                severity="major",
                message=(
                    "Human participants are mentioned but no IRB approval, ethics "
                    "committee review, or informed consent statement is present. "
                    "Add an ethics approval statement or informed consent disclosure."
                ),
                validator="ethics_approval_statement",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 197 – Incomplete PRISMA reporting (systematic review)
# ---------------------------------------------------------------------------

_SYSTEMATIC_REVIEW_RE = re.compile(
    r"\b(?:systematic\s+review|literature\s+(?:search|review)\s+"
    r"(?:was\s+)?conducted\s+(?:in|using|via)|"
    r"(?:PubMed|Medline|PsycINFO|EMBASE|Cochrane|Web\s+of\s+Science)\s+"
    r"(?:was|were)\s+searched?|"
    r"search\s+strategy\s+(?:was|included)|"
    r"eligible\s+studies?\s+were\s+(?:identified?|included?))\b",
    re.IGNORECASE,
)
_PRISMA_ELEMENTS_RE = re.compile(
    r"\b(?:PRISMA|preferred\s+reporting\s+items|"
    r"flow\s+diagram|screening\s+(?:process|criteria)|"
    r"inclusion\s+(?:and\s+)?exclusion\s+criteria|"
    r"inter.?rater\s+(?:reliability|agreement)\s+(?:for\s+)?(?:screening|coding)|"
    r"QUORUM|MOOSE)\b",
    re.IGNORECASE,
)


def validate_prisma_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag systematic reviews that omit PRISMA or screening transparency.

    Emits ``missing-prisma-elements`` (moderate) when a systematic review is
    detected but key PRISMA elements (flow diagram, screening criteria,
    inter-rater reliability) are absent.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="prisma_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="prisma_reporting", findings=[]
        )

    if not _SYSTEMATIC_REVIEW_RE.search(full):
        return ValidationResult(
            validator_name="prisma_reporting", findings=[]
        )

    if _PRISMA_ELEMENTS_RE.search(full):
        return ValidationResult(
            validator_name="prisma_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="prisma_reporting",
        findings=[
            Finding(
                code="missing-prisma-elements",
                severity="moderate",
                message=(
                    "Systematic review or database search detected but key PRISMA "
                    "elements (flow diagram, screening criteria, inter-rater reliability) "
                    "are absent. Follow PRISMA guidelines for systematic reviews."
                ),
                validator="prisma_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 198 – Mediation analysis transparency
# ---------------------------------------------------------------------------

_MEDIATION_RE = re.compile(
    r"\b(?:mediat(?:ed?|ing|ion|or)\s+(?:effect|relationship|analysis|model)|"
    r"indirect\s+effect|mediating\s+variable|"
    r"Baron\s+and\s+Kenny|causal\s+chain|"
    r"through\s+(?:the\s+)?(?:mediator|indirect\s+path))\b",
    re.IGNORECASE,
)
_MEDIATION_METHOD_RE = re.compile(
    r"\b(?:bootstrapping|bootstrap|PROCESS\s+macro|"
    r"Hayes\s+(?:PROCESS|mediation)|Sobel\s+test|"
    r"confidence\s+interval\s+for\s+(?:the\s+)?indirect|"
    r"indirect\s+effect\s+(?:CI|95\s*%))\b",
    re.IGNORECASE,
)


def validate_mediation_analysis_transparency(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag mediation analyses that omit appropriate inferential methods.

    Emits ``missing-mediation-bootstrap`` (moderate) when mediation is
    claimed but no bootstrapping, Sobel test, or CI for indirect effect
    is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="mediation_analysis_transparency", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="mediation_analysis_transparency", findings=[]
        )

    if not _MEDIATION_RE.search(full):
        return ValidationResult(
            validator_name="mediation_analysis_transparency", findings=[]
        )

    if _MEDIATION_METHOD_RE.search(full):
        return ValidationResult(
            validator_name="mediation_analysis_transparency", findings=[]
        )

    return ValidationResult(
        validator_name="mediation_analysis_transparency",
        findings=[
            Finding(
                code="missing-mediation-bootstrap",
                severity="moderate",
                message=(
                    "Mediation analysis detected but no bootstrapping (PROCESS macro, "
                    "Hayes) or CI for indirect effect is reported. "
                    "Use bootstrapped confidence intervals for mediation testing."
                ),
                validator="mediation_analysis_transparency",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 199 – Construct validity evidence (latent variable models)
# ---------------------------------------------------------------------------

_LATENT_VARIABLE_RE = re.compile(
    r"\b(?:confirmatory\s+factor\s+analysis|CFA\b|structural\s+equation\s+model|"
    r"SEM\b|latent\s+(?:variable|factor|construct|class)|"
    r"factor\s+structure|measurement\s+model|"
    r"exploratory\s+factor\s+analysis|EFA\b)\b",
    re.IGNORECASE,
)
_MODEL_FIT_RE = re.compile(
    r"\b(?:CFI\s*(?:=|>|<|\s*\d)|RMSEA\s*(?:=|<|\s*\d)|TLI\s*(?:=|>|<|\s*\d)|"
    r"SRMR\s*(?:=|<|\s*\d)|chi.?square\s+(?:fit|goodness)|"
    r"model\s+fit\s+(?:was|indices?)|"
    r"(?:good|adequate|acceptable|poor)\s+model\s+fit|"
    r"factor\s+loadings?|"
    r"standardized\s+(?:loading|path\s+coefficient))\b",
    re.IGNORECASE,
)


def validate_latent_variable_model_fit(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag CFA/SEM analyses that omit model fit indices.

    Emits ``missing-model-fit-indices`` (moderate) when CFA or SEM is used
    but no model fit indices (CFI, RMSEA, TLI, SRMR) are reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="latent_variable_model_fit", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="latent_variable_model_fit", findings=[]
        )

    if not _LATENT_VARIABLE_RE.search(full):
        return ValidationResult(
            validator_name="latent_variable_model_fit", findings=[]
        )

    if _MODEL_FIT_RE.search(full):
        return ValidationResult(
            validator_name="latent_variable_model_fit", findings=[]
        )

    return ValidationResult(
        validator_name="latent_variable_model_fit",
        findings=[
            Finding(
                code="missing-model-fit-indices",
                severity="moderate",
                message=(
                    "CFA or SEM analysis detected but no model fit indices "
                    "(CFI, RMSEA, TLI, SRMR) are reported. "
                    "Report fit indices to evaluate measurement model quality."
                ),
                validator="latent_variable_model_fit",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 200 – Undisclosed pilot / feasibility study used for sample sizing
# ---------------------------------------------------------------------------

_PILOT_SIZE_RE = re.compile(
    r"\b(?:based\s+on\s+(?:a\s+)?pilot|pilot\s+(?:study|data|results?|test)\s+"
    r"(?:informed?|guided?|determined?|provided?)|"
    r"effect\s+size\s+(?:from|based\s+on|estimated?\s+from)\s+"
    r"(?:a\s+)?(?:pilot|previous|prior|preliminary))\b",
    re.IGNORECASE,
)
_PILOT_DISCLOSURE_RE = re.compile(
    r"\b(?:pilot\s+study\s+(?:was\s+)?(?:conducted?|published?|registered?|"
    r"reported?|described?|documented?)|"
    r"see\s+(?:supplemental|supplementary|Appendix|Table\s+S)|"
    r"pilot\s+data\s+(?:are|were)\s+(?:available|reported?|provided?))\b",
    re.IGNORECASE,
)


def validate_pilot_study_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag undisclosed pilot studies used to determine sample size.

    Emits ``undisclosed-pilot-study`` (minor) when a pilot study is used to
    inform sample size but the pilot data or reference is not disclosed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="pilot_study_disclosure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="pilot_study_disclosure", findings=[]
        )

    if not _PILOT_SIZE_RE.search(full):
        return ValidationResult(
            validator_name="pilot_study_disclosure", findings=[]
        )

    if _PILOT_DISCLOSURE_RE.search(full):
        return ValidationResult(
            validator_name="pilot_study_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="pilot_study_disclosure",
        findings=[
            Finding(
                code="undisclosed-pilot-study",
                severity="minor",
                message=(
                    "A pilot study is cited as the basis for sample size but the "
                    "pilot data, effect size, or reference is not disclosed. "
                    "Describe the pilot study or cite the source of the effect size estimate."
                ),
                validator="pilot_study_disclosure",
                location="Methods",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 201 – Missing autocorrelation check (time-series)
# ---------------------------------------------------------------------------

_TIME_SERIES_RE = re.compile(
    r"\b(?:time\s+series|time.?series|longitudinal\s+regression|"
    r"autoregressive|panel\s+regression|repeated\s+time\s+(?:points?|measures?)|"
    r"lagged\s+(?:dependent|variable|predictor)|"
    r"AR\s*\(\s*\d+\s*\)|ARMA|ARIMA|VAR\s+model)\b",
    re.IGNORECASE,
)
_AUTOCORRELATION_CHECK_RE = re.compile(
    r"\b(?:autocorrelation|Durbin.?Watson|Ljung.?Box|Box.?Pierce|"
    r"serial\s+correlation|residual\s+autocorrelation|"
    r"no\s+(?:serial|auto)correlation|ACF\s+plot|PACF\s+plot)\b",
    re.IGNORECASE,
)


def validate_autocorrelation_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag time-series or panel regression without autocorrelation check.

    Emits ``missing-autocorrelation-check`` (minor) when autoregressive or
    time-series methods are used but no autocorrelation test is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="autocorrelation_check", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="autocorrelation_check", findings=[]
        )

    if not _TIME_SERIES_RE.search(full):
        return ValidationResult(
            validator_name="autocorrelation_check", findings=[]
        )

    if _AUTOCORRELATION_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="autocorrelation_check", findings=[]
        )

    return ValidationResult(
        validator_name="autocorrelation_check",
        findings=[
            Finding(
                code="missing-autocorrelation-check",
                severity="minor",
                message=(
                    "Time-series or autoregressive model detected but no autocorrelation "
                    "check (Durbin-Watson, Ljung-Box, ACF/PACF plot) is reported. "
                    "Test for serial correlation in residuals."
                ),
                validator="autocorrelation_check",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 202 – Mixed methods integration
# ---------------------------------------------------------------------------

_MIXED_METHODS_RE = re.compile(
    r"\b(?:mixed.?methods?|mixed\s+method\s+(?:study|design|approach|research)|"
    r"qualitative\s+and\s+quantitative|quantitative\s+and\s+qualitative|"
    r"qual.?quant|convergent\s+design|sequential\s+explanatory|"
    r"triangulat(?:ed?|ion))\b",
    re.IGNORECASE,
)
_MIXED_METHODS_INTEGRATION_RE = re.compile(
    r"\b(?:integrat(?:ed?|ing|ion)\s+(?:the\s+)?(?:qualitative|quantitative|findings?|results?)|"
    r"qual(?:itative)?\s+findings?\s+(?:illuminated?|explained?|elaborated?|"
    r"contextualized?|informed?)\s+(?:the\s+)?quant(?:itative)?|"
    r"triangulat(?:ed?|ing|ion)\s+(?:the\s+)?(?:findings?|results?)|"
    r"mixing\s+occurred|joint\s+display)",
    re.IGNORECASE,
)


def validate_mixed_methods_integration(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag mixed-methods studies without explicit data integration.

    Emits ``missing-mixed-methods-integration`` (moderate) when a
    mixed-methods design is claimed but no explicit integration of
    qualitative and quantitative findings is described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="mixed_methods_integration", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="mixed_methods_integration", findings=[]
        )

    if not _MIXED_METHODS_RE.search(full):
        return ValidationResult(
            validator_name="mixed_methods_integration", findings=[]
        )

    if _MIXED_METHODS_INTEGRATION_RE.search(full):
        return ValidationResult(
            validator_name="mixed_methods_integration", findings=[]
        )

    return ValidationResult(
        validator_name="mixed_methods_integration",
        findings=[
            Finding(
                code="missing-mixed-methods-integration",
                severity="moderate",
                message=(
                    "Mixed-methods design claimed but no explicit integration of "
                    "qualitative and quantitative findings is described. "
                    "Explain how the two data strands were merged or compared."
                ),
                validator="mixed_methods_integration",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 203 – Grounded theory / qualitative rigor
# ---------------------------------------------------------------------------

_QUALITATIVE_RE = re.compile(
    r"\b(?:qualitative\s+(?:study|research|data|analysis|interview|observation)|"
    r"grounded\s+theory|thematic\s+analysis|content\s+analysis|"
    r"ethnograph(?:ic|y)|phenomenolog(?:ical|y)|interpretive|"
    r"focus\s+groups?|semi.?structured\s+interviews?)\b",
    re.IGNORECASE,
)
_QUALITATIVE_RIGOR_RE = re.compile(
    r"\b(?:trustworthiness|credibility|transferability|dependability|"
    r"confirmability|member\s+check(?:ing)?|peer\s+debrief(?:ing)?|"
    r"thick\s+description|reflexivity|audit\s+trail|"
    r"negative\s+case\s+analysis|prolonged\s+engagement|"
    r"data\s+saturation|theoretical\s+saturation)\b",
    re.IGNORECASE,
)


def validate_qualitative_rigor_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag qualitative studies that omit rigor/trustworthiness criteria.

    Emits ``missing-qualitative-rigor`` (moderate) when qualitative methods
    are used but no trustworthiness, credibility, or rigor measures are
    reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="qualitative_rigor_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="qualitative_rigor_reporting", findings=[]
        )

    if not _QUALITATIVE_RE.search(full):
        return ValidationResult(
            validator_name="qualitative_rigor_reporting", findings=[]
        )

    if _QUALITATIVE_RIGOR_RE.search(full):
        return ValidationResult(
            validator_name="qualitative_rigor_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="qualitative_rigor_reporting",
        findings=[
            Finding(
                code="missing-qualitative-rigor",
                severity="moderate",
                message=(
                    "Qualitative methods detected but no trustworthiness or rigor "
                    "criteria (member checking, peer debriefing, data saturation, "
                    "reflexivity) are reported. "
                    "Address rigor/credibility in qualitative research."
                ),
                validator="qualitative_rigor_reporting",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 204 – Unreported subgroup analysis
# ---------------------------------------------------------------------------

_SUBGROUP_ANALYSIS_RE = re.compile(
    r"\b(?:subgroup\s+(?:analysis|comparison|effect)|"
    r"stratified\s+analysis|moderation\s+by\s+(?:age|sex|gender|race|education)|"
    r"we\s+(?:also\s+)?(?:examined?|tested?|explored?)\s+(?:whether\s+)?"
    r"(?:the\s+)?(?:effect|association|relationship)\s+(?:differed?|varied?)\s+"
    r"(?:by|across|between)\s+\w+)\b",
    re.IGNORECASE,
)
_SUBGROUP_CAUTION_RE = re.compile(
    r"\b(?:exploratory\s+subgroup|subgroup\s+(?:results?\s+should\s+be\s+)"
    r"(?:interpreted?|viewed?)\s+(?:cautiously|with\s+caution)|"
    r"subgroup\s+analyses?\s+(?:were\s+)?(?:pre.?specified|planned?|"
    r"pre.?registered?)|"
    r"caution\s+(?:in\s+)?interpreting\s+subgroup)\b",
    re.IGNORECASE,
)


def validate_subgroup_analysis_labelling(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag exploratory subgroup analyses not labelled as such.

    Emits ``unlabelled-subgroup-analysis`` (minor) when subgroup or stratified
    analyses are conducted without pre-specification or cautionary labelling.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="subgroup_analysis_labelling", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="subgroup_analysis_labelling", findings=[]
        )

    if not _SUBGROUP_ANALYSIS_RE.search(full):
        return ValidationResult(
            validator_name="subgroup_analysis_labelling", findings=[]
        )

    if _SUBGROUP_CAUTION_RE.search(full):
        return ValidationResult(
            validator_name="subgroup_analysis_labelling", findings=[]
        )

    return ValidationResult(
        validator_name="subgroup_analysis_labelling",
        findings=[
            Finding(
                code="unlabelled-subgroup-analysis",
                severity="minor",
                message=(
                    "Subgroup or stratified analysis detected but not labelled as "
                    "exploratory or pre-specified. "
                    "Clearly indicate whether subgroup analyses were pre-planned or "
                    "exploratory and interpret accordingly."
                ),
                validator="subgroup_analysis_labelling",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 205 – Underpowered conclusions from non-significant results
# ---------------------------------------------------------------------------

_NON_SIG_CONCLUDE_RE = re.compile(
    r"\b(?:(?:there\s+is|there\s+was|we\s+found)\s+no\s+(?:significant\s+)?"
    r"(?:effect|association|difference|relationship)\s+between|"
    r"(?:the\s+)?(?:results?\s+)?(?:suggest|indicate|show|demonstrate)\s+(?:that\s+)?"
    r"(?:\w+\s+)?(?:has|have|had|does\s+not\s+have)\s+no\s+(?:effect|impact)|"
    r"no\s+evidence\s+(?:of|for)\s+(?:an?\s+)?(?:effect|association|difference))\b",
    re.IGNORECASE,
)
_POWER_CAVEAT_RE = re.compile(
    r"\b(?:underpowered|insufficient\s+power|limited\s+(?:statistical\s+)?power|"
    r"type\s+II\s+error|false\s+negative|absence\s+of\s+evidence\s+is\s+not|"
    r"null\s+result\s+(?:should\s+be\s+)?(?:interpreted?|viewed?)\s+"
    r"(?:cautiously|with\s+caution)|"
    r"sample\s+(?:was\s+)?(?:too\s+small|insufficient)\s+to\s+detect)\b",
    re.IGNORECASE,
)


def validate_null_result_power_caveat(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag null-result conclusions without power caveats.

    Emits ``null-result-without-power-caveat`` (minor) when a non-significant
    result is stated as a conclusion without acknowledging possible Type II error
    or insufficient power.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="null_result_power_caveat", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="null_result_power_caveat", findings=[]
        )

    if not _NON_SIG_CONCLUDE_RE.search(full):
        return ValidationResult(
            validator_name="null_result_power_caveat", findings=[]
        )

    if _POWER_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="null_result_power_caveat", findings=[]
        )

    return ValidationResult(
        validator_name="null_result_power_caveat",
        findings=[
            Finding(
                code="null-result-without-power-caveat",
                severity="minor",
                message=(
                    "Non-significant result presented as a definitive conclusion without "
                    "acknowledging possible insufficient power or Type II error. "
                    "Interpret null results cautiously and discuss power limitations."
                ),
                validator="null_result_power_caveat",
                location="Discussion",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 206 – Missing standard deviation for means
# ---------------------------------------------------------------------------

_MEAN_REPORTED_RE = re.compile(
    r"\b(?:mean\s*(?:=|of|was|score\s+was)\s*[\d\.\-]+|"
    r"M\s*=\s*[\d\.\-]+|"
    r"average\s+(?:was|of|score\s+was)\s*[\d\.\-]+|"
    r"(?:mean|average)\s+(?:age|score|value|rating|time)\s+was\s+[\d\.\-]+)\b",
    re.IGNORECASE,
)
_SD_REPORTED_RE = re.compile(
    r"\b(?:SD\s*=\s*[\d\.\-]+|S\.D\.\s*=\s*[\d\.\-]+|"
    r"standard\s+deviation\s*(?:=|of|was)\s*[\d\.\-]+|"
    r"SE\s*=\s*[\d\.\-]+|SEM\s*=\s*[\d\.\-]+|"
    r"\(\s*SD\s*=|,\s*SD\s*=|\(S\.D\.\s*=)\b",
    re.IGNORECASE,
)


def validate_mean_sd_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical manuscripts reporting means without standard deviations.

    Emits ``missing-sd-for-mean`` (minor) when means are reported but no
    standard deviation (or SE/SEM) accompanies them.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="mean_sd_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="mean_sd_reporting", findings=[]
        )

    if not _MEAN_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="mean_sd_reporting", findings=[]
        )

    if _SD_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="mean_sd_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="mean_sd_reporting",
        findings=[
            Finding(
                code="missing-sd-for-mean",
                severity="minor",
                message=(
                    "Means are reported but no standard deviation (SD, SE, or SEM) "
                    "is provided. "
                    "Report variability measures alongside all means."
                ),
                validator="mean_sd_reporting",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 207 – Insufficient detail on intervention/treatment
# ---------------------------------------------------------------------------

_INTERVENTION_RE = re.compile(
    r"\b(?:intervention|treatment\s+(?:group|condition|protocol)|"
    r"experimental\s+(?:condition|group)|"
    r"(?:the\s+)?(?:program|protocol|therapy|training|workshop)\s+"
    r"(?:was|were|included?)\s+(?:\w+\s*){1,5}(?:sessions?|weeks?|months?|hours?|minutes?))\b",
    re.IGNORECASE,
)
_INTERVENTION_DETAIL_RE = re.compile(
    r"\b(?:session\s+(?:duration|length|frequency|content)|"
    r"(?:number|total)\s+of\s+sessions?|"
    r"delivered?\s+by\s+(?:trained?|certified?|licensed?|\w+\s+therapist)|"
    r"protocol\s+(?:manual|fidelity|adherence)|"
    r"treatment\s+fidelity|intervention\s+description|"
    r"\d+\s+(?:weekly|bi.?weekly|monthly)\s+sessions?)\b",
    re.IGNORECASE,
)


def validate_intervention_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies describing interventions without sufficient detail.

    Emits ``insufficient-intervention-description`` (moderate) when an
    intervention/treatment is mentioned but session duration, frequency,
    content, or fidelity are not described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="intervention_description", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="intervention_description", findings=[]
        )

    if not _INTERVENTION_RE.search(full):
        return ValidationResult(
            validator_name="intervention_description", findings=[]
        )

    if _INTERVENTION_DETAIL_RE.search(full):
        return ValidationResult(
            validator_name="intervention_description", findings=[]
        )

    return ValidationResult(
        validator_name="intervention_description",
        findings=[
            Finding(
                code="insufficient-intervention-description",
                severity="moderate",
                message=(
                    "Intervention or treatment mentioned but insufficient detail "
                    "(session frequency, duration, content, fidelity) is provided. "
                    "Describe the intervention in enough detail for replication."
                ),
                validator="intervention_description",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 208 – Baseline equivalence check (RCTs)
# ---------------------------------------------------------------------------

_BASELINE_RCT_RE = re.compile(
    r"\b(?:randomized?\s+(?:controlled?\s+)?trial|RCT\b|"
    r"randomly\s+assigned?|random\s+assignment)\b",
    re.IGNORECASE,
)
_BASELINE_CHECK_RE = re.compile(
    r"\b(?:baseline\s+(?:characteristics?|equivalence|comparison|balance|"
    r"differences?|variables?)\s+(?:were|was|are|showed?|revealed?|indicated?)|"
    r"groups?\s+(?:were|was)\s+(?:comparable|equivalent|balanced|similar)\s+"
    r"(?:at\s+baseline|on\s+baseline)|"
    r"no\s+significant\s+(?:difference|difference)\s+(?:was|were)\s+found\s+"
    r"(?:at\s+baseline|between\s+groups?\s+at\s+baseline)|"
    r"Table\s+\d+\s+(?:shows?|presents?|displays?)\s+(?:baseline|demographics))\b",
    re.IGNORECASE,
)


def validate_baseline_equivalence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCTs that don't report baseline equivalence.

    Emits ``missing-baseline-equivalence`` (moderate) when an RCT design
    is detected but no baseline comparison or equivalence check is described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="baseline_equivalence", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="baseline_equivalence", findings=[]
        )

    if not _BASELINE_RCT_RE.search(full):
        return ValidationResult(
            validator_name="baseline_equivalence", findings=[]
        )

    if _BASELINE_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="baseline_equivalence", findings=[]
        )

    return ValidationResult(
        validator_name="baseline_equivalence",
        findings=[
            Finding(
                code="missing-baseline-equivalence",
                severity="moderate",
                message=(
                    "RCT design detected but no baseline equivalence check is reported. "
                    "Report and compare baseline characteristics across groups "
                    "to verify successful randomization."
                ),
                validator="baseline_equivalence",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 209 – Ceiling/floor effect in Likert-type outcomes
# ---------------------------------------------------------------------------

_LIKERT_OUTCOME_RE = re.compile(
    r"\b(?:Likert(?:.?type)?\s+(?:scale|response|item|measure|rating)|"
    r"\d+.?point\s+(?:scale|Likert|rating\s+scale)|"
    r"(?:strongly\s+)?(?:agree|disagree)\s+(?:to|through)|"
    r"response\s+options?\s+(?:ranged?|ranging)\s+from\s+\d+\s+to\s+\d+)\b",
    re.IGNORECASE,
)
_LIKERT_DISTRIBUTION_RE = re.compile(
    r"\b(?:skew(?:ed?|ness)|ceiling\s+effect|floor\s+effect|"
    r"distribution\s+of\s+(?:responses?|scores?)|"
    r"(?:highly\s+)?skewed\s+(?:distribution|responses?|toward)|"
    r"(?:normal|non.?normal)\s+distribution\s+of\s+(?:scores?|responses?)|"
    r"Shapiro|normality\s+test\s+for\s+(?:Likert|ordinal))\b",
    re.IGNORECASE,
)


def validate_likert_distribution_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies using Likert scales without checking response distribution.

    Emits ``missing-likert-distribution-check`` (minor) when Likert-type
    measures are used but no skewness, ceiling/floor effects, or distribution
    check is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="likert_distribution_check", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="likert_distribution_check", findings=[]
        )

    if not _LIKERT_OUTCOME_RE.search(full):
        return ValidationResult(
            validator_name="likert_distribution_check", findings=[]
        )

    if _LIKERT_DISTRIBUTION_RE.search(full):
        return ValidationResult(
            validator_name="likert_distribution_check", findings=[]
        )

    return ValidationResult(
        validator_name="likert_distribution_check",
        findings=[
            Finding(
                code="missing-likert-distribution-check",
                severity="minor",
                message=(
                    "Likert-type scale detected but no distribution check "
                    "(skewness, ceiling/floor effects) is reported. "
                    "Examine and report response distribution before using parametric tests."
                ),
                validator="likert_distribution_check",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 210 – Reproducibility statement (code/data sharing)
# ---------------------------------------------------------------------------

_REPRO_CLAIM_RE = re.compile(
    r"\b(?:reproducible?\s+(?:analysis|code|workflow|results?)|"
    r"(?:code|data|materials?|scripts?)\s+(?:are|were|will\s+be)\s+"
    r"(?:available|shared?|deposited?|archived?|released?)|"
    r"supplementary\s+(?:code|scripts?|data)|"
    r"available\s+(?:at|from|via|on)\s+(?:GitHub|OSF|Zenodo|Figshare|Dryad))\b",
    re.IGNORECASE,
)
_REPRO_DETAIL_RE = re.compile(
    r"\b(?:https?://|doi:\s*10\.|github\.com/|osf\.io/|zenodo\.org/|"
    r"figshare\.com/|datadryad\.org/|"
    r"accession\s+(?:number|code)|"
    r"(?:code|data)\s+(?:repository|archive))\b",
    re.IGNORECASE,
)


def validate_reproducibility_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag reproducibility claims without a concrete URL/DOI/accession.

    Emits ``missing-reproducibility-link`` (minor) when code/data availability
    is claimed but no URL, DOI, or accession number is provided.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="reproducibility_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="reproducibility_statement", findings=[]
        )

    if not _REPRO_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="reproducibility_statement", findings=[]
        )

    if _REPRO_DETAIL_RE.search(full):
        return ValidationResult(
            validator_name="reproducibility_statement", findings=[]
        )

    return ValidationResult(
        validator_name="reproducibility_statement",
        findings=[
            Finding(
                code="missing-reproducibility-link",
                severity="minor",
                message=(
                    "Code or data availability is claimed but no URL, DOI, or "
                    "accession number is provided. "
                    "Include a concrete link or identifier for the shared materials."
                ),
                validator="reproducibility_statement",
                location="manuscript",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 211 – Missing missing-data handling
# ---------------------------------------------------------------------------

_MISSING_DATA_TRIGGER_RE = re.compile(
    r"\b(?:missing\s+(?:data|values?|cases?|observations?)|"
    r"incomplete\s+data|item\s+non.?response|"
    r"(?:some|several|few)\s+participants?\s+(?:did\s+not\s+complete|dropped?\s+out|"
    r"withdrew?|were\s+excluded?)\b|"
    r"(?:n\s*=\s*\d+\s*excluded?|excluded?\s+n\s*=\s*\d+)\b)\b",
    re.IGNORECASE,
)
_MISSING_DATA_METHOD_RE = re.compile(
    r"\b(?:listwise\s+deletion|pairwise\s+deletion|complete\s+case\s+analysis|"
    r"multiple\s+imputation|single\s+imputation|mean\s+imputation|"
    r"hot\s+deck\s+imputation|MICE|maximum\s+likelihood|full\s+information|"
    r"EM\s+algorithm|missing\s+at\s+random|"
    r"missing\s+data\s+(?:were|was)\s+(?:handled?|addressed?|imputed?))\b",
    re.IGNORECASE,
)


def validate_missing_data_handling(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies mentioning missing data without specifying handling method.

    Emits ``missing-data-handling-not-described`` (moderate) when missing data
    are noted but the handling strategy is not disclosed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="missing_data_handling", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="missing_data_handling", findings=[]
        )

    if not _MISSING_DATA_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="missing_data_handling", findings=[]
        )

    if _MISSING_DATA_METHOD_RE.search(full):
        return ValidationResult(
            validator_name="missing_data_handling", findings=[]
        )

    return ValidationResult(
        validator_name="missing_data_handling",
        findings=[
            Finding(
                code="missing-data-handling-not-described",
                severity="moderate",
                message=(
                    "Missing data are noted but the handling method "
                    "(listwise deletion, multiple imputation, MICE, etc.) is not disclosed. "
                    "Report how missing data were addressed."
                ),
                validator="missing_data_handling",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 212 – Insufficient description of coding scheme (content analysis)
# ---------------------------------------------------------------------------

_CODING_SCHEME_TRIGGER_RE = re.compile(
    r"\b(?:coding\s+(?:scheme|manual|system|frame(?:work)?)|"
    r"codes?\s+(?:were|was)\s+(?:developed?|created?|established?|applied?)|"
    r"content\s+analysis\s+(?:coding|categories?)|"
    r"codebook|deductive\s+coding|inductive\s+coding|"
    r"categories?\s+(?:were|was)\s+(?:identified?|developed?|created?))\b",
    re.IGNORECASE,
)
_CODING_SCHEME_DETAIL_RE = re.compile(
    r"\b(?:inter.?coder|inter.?rater|coding\s+agreement|Cohen.s\s+kappa|"
    r"kappa\s*=|Krippendorff|percent\s+agreement|coding\s+rules?|"
    r"operational\s+definitions?|example\s+(?:of\s+)?(?:codes?|categories?))\b",
    re.IGNORECASE,
)


def validate_coding_scheme_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag content analysis studies with insufficient coding scheme detail.

    Emits ``missing-coding-scheme-detail`` (moderate) when a coding scheme is
    used but no inter-coder reliability, operational definitions, or coding
    rules are described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="coding_scheme_description", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="coding_scheme_description", findings=[]
        )

    if not _CODING_SCHEME_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="coding_scheme_description", findings=[]
        )

    if _CODING_SCHEME_DETAIL_RE.search(full):
        return ValidationResult(
            validator_name="coding_scheme_description", findings=[]
        )

    return ValidationResult(
        validator_name="coding_scheme_description",
        findings=[
            Finding(
                code="missing-coding-scheme-detail",
                severity="moderate",
                message=(
                    "Coding scheme or content analysis detected but no inter-coder "
                    "reliability, operational definitions, or coding rules are described. "
                    "Report coding agreement (kappa, percent agreement) and definitions."
                ),
                validator="coding_scheme_description",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 213 – Missing model assumptions for logistic regression
# ---------------------------------------------------------------------------

_LOGISTIC_RE = re.compile(
    r"\b(?:logistic\s+regression|logit\s+(?:model|regression)|"
    r"binary\s+(?:logistic|logit)|multinomial\s+logistic|"
    r"ordinal\s+(?:logistic|logit)|proportional\s+odds\s+model)\b",
    re.IGNORECASE,
)
_LOGISTIC_ASSUMPTION_RE = re.compile(
    r"\b(?:linearity\s+of\s+the\s+logit|log.?odds\s+(?:linearity|assumption)|"
    r"Hosmer.?Lemeshow|goodness.of.fit\s+(?:test|statistic)|"
    r"classification\s+table|AUC\b|ROC\s+(?:curve|analysis)|"
    r"pseudo\s+R.?squared|Nagelkerke|Cox\s+and\s+Snell|"
    r"odds\s+ratio\s+(?:CI|95\s*%\s*CI)|"
    r"model\s+fit\s+(?:statistic|index)|likelihood\s+ratio\s+test)\b",
    re.IGNORECASE,
)


def validate_logistic_regression_assumptions(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag logistic regression without model fit or assumption checks.

    Emits ``missing-logistic-model-fit`` (minor) when logistic regression is
    used but no model fit statistic, ROC/AUC, Hosmer-Lemeshow, or pseudo-R² is
    reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="logistic_regression_assumptions", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="logistic_regression_assumptions", findings=[]
        )

    if not _LOGISTIC_RE.search(full):
        return ValidationResult(
            validator_name="logistic_regression_assumptions", findings=[]
        )

    if _LOGISTIC_ASSUMPTION_RE.search(full):
        return ValidationResult(
            validator_name="logistic_regression_assumptions", findings=[]
        )

    return ValidationResult(
        validator_name="logistic_regression_assumptions",
        findings=[
            Finding(
                code="missing-logistic-model-fit",
                severity="minor",
                message=(
                    "Logistic regression detected but no model fit statistic "
                    "(Hosmer-Lemeshow, AUC/ROC, pseudo-R², classification table) "
                    "is reported. Report at least one model fit index."
                ),
                validator="logistic_regression_assumptions",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 214 – Undisclosed researcher positionality (qualitative)
# ---------------------------------------------------------------------------

_QUALITATIVE_POSITIONALITY_TRIGGER_RE = re.compile(
    r"\b(?:qualitative\s+(?:study|research|inquiry|analysis)|"
    r"grounded\s+theory|phenomenolog(?:y|ical)|ethnograph(?:y|ic)|"
    r"interpretive\s+(?:research|inquiry|analysis))\b",
    re.IGNORECASE,
)
_POSITIONALITY_RE = re.compile(
    r"\b(?:positionality|reflexivity|researcher.s?\s+(?:role|position|background)|"
    r"my\s+(?:own\s+)?(?:position|background|perspective|experience|bias)|"
    r"the\s+(?:researcher|author|PI)\s+(?:is|was|had|brings?|acknowledges?)\s+"
    r"(?:\w+\s+)?(?:background|experience|perspective|bias)|"
    r"researcher\s+bias|I\s+(?:am|was)\s+(?:a\s+)?\w+\s+"
    r"(?:who|and\s+therefore|which\s+may))\b",
    re.IGNORECASE,
)


def validate_researcher_positionality(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag qualitative research without researcher positionality disclosure.

    Emits ``missing-researcher-positionality`` (minor) when qualitative
    methods are used but no reflexivity or positionality statement is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="researcher_positionality", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="researcher_positionality", findings=[]
        )

    if not _QUALITATIVE_POSITIONALITY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="researcher_positionality", findings=[]
        )

    if _POSITIONALITY_RE.search(full):
        return ValidationResult(
            validator_name="researcher_positionality", findings=[]
        )

    return ValidationResult(
        validator_name="researcher_positionality",
        findings=[
            Finding(
                code="missing-researcher-positionality",
                severity="minor",
                message=(
                    "Qualitative research detected but no reflexivity or researcher "
                    "positionality statement is present. "
                    "Acknowledge the researcher's background, assumptions, and potential biases."
                ),
                validator="researcher_positionality",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 215 – Data collection period gap (data older than methods imply)
# ---------------------------------------------------------------------------

_RECENT_CLAIM_RE = re.compile(
    r"\b(?:recent\s+(?:data|evidence|study|survey|trend)|"
    r"current\s+(?:data|evidence|trends?|landscape)|"
    r"up.?to.?date\s+(?:data|evidence)|"
    r"latest\s+(?:data|evidence|figures?|statistics?))\b",
    re.IGNORECASE,
)
_OLD_DATA_RE = re.compile(
    r"\b(?:data\s+(?:were|was)\s+collected?\s+(?:in|between|from)\s+"
    r"(?:19\d{2}|200[0-9]|201[0-5])|"
    r"(?:19\d{2}|200[0-9]|201[0-5])\s+(?:data|survey|census|dataset)|"
    r"dataset\s+(?:from|covering)\s+(?:19\d{2}|200[0-9]|201[0-5]))\b",
    re.IGNORECASE,
)


def validate_data_collection_recency(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts claiming recency but citing old data collection periods.

    Emits ``potentially-outdated-data`` (minor) when 'recent' or 'current'
    data are claimed but the data collection year is ≤2015.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="data_collection_recency", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="data_collection_recency", findings=[]
        )

    if not _RECENT_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="data_collection_recency", findings=[]
        )

    if not _OLD_DATA_RE.search(full):
        return ValidationResult(
            validator_name="data_collection_recency", findings=[]
        )

    return ValidationResult(
        validator_name="data_collection_recency",
        findings=[
            Finding(
                code="potentially-outdated-data",
                severity="minor",
                message=(
                    "Manuscript claims to use 'recent' or 'current' data but "
                    "the data collection year appears to be 2015 or earlier. "
                    "Justify why the older data are still representative."
                ),
                validator="data_collection_recency",
                location="Methods",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 216 – Missing theoretical framework citation
# ---------------------------------------------------------------------------

_THEORY_CLAIM_RE = re.compile(
    r"\b(?:grounded\s+in|based\s+on|guided\s+by|draws?\s+on|informed?\s+by|"
    r"theoretical\s+framework|conceptual\s+framework|framed?\s+(?:by|within|through)|"
    r"underpinned?\s+by|rooted\s+in)\s+(?:the\s+)?(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+)?theory\b",
    re.IGNORECASE,
)
_THEORY_CITATION_RE = re.compile(
    r"\b[Tt]heory\b.{0,120}?\(\s*[A-Z][^\)]{2,}\d{4}\s*\)"
    r"|\(\s*[A-Z][^\)]{2,}\d{4}\s*\).{0,120}?\b[Tt]heory\b",
    re.IGNORECASE | re.DOTALL,
)
_THEORY_NAMED_RE = re.compile(
    r"\b(?:Social\s+(?:Learning|Cognitive|Exchange|Capital|Identity)|"
    r"Self.?Determination|Self.?Efficacy|Theory\s+of\s+Planned\s+Behaviour?|"
    r"Health\s+Belief\s+Model|Transtheoretical\s+Model|Ecological\s+Systems?|"
    r"Cognitive\s+Dissonance|Attribution|Reasoned\s+Action|Diffusion\s+of\s+Innovations?|"
    r"Situated\s+Learning|Expectancy.?Value|Dual\s+Process)\s+[Tt]heory\b",
    re.IGNORECASE,
)


def validate_theoretical_framework_citation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts invoking a theory without providing a citation.

    Emits ``missing-theory-citation`` (minor) when a named theoretical
    framework is mentioned but no year/author citation accompanies it.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="theoretical_framework_citation", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="theoretical_framework_citation", findings=[]
        )

    if not _THEORY_NAMED_RE.search(full):
        return ValidationResult(
            validator_name="theoretical_framework_citation", findings=[]
        )

    if _THEORY_CITATION_RE.search(full):
        return ValidationResult(
            validator_name="theoretical_framework_citation", findings=[]
        )

    return ValidationResult(
        validator_name="theoretical_framework_citation",
        findings=[
            Finding(
                code="missing-theory-citation",
                severity="minor",
                message=(
                    "A named theoretical framework is invoked but no corresponding "
                    "author/year citation is present. Cite the original source for the theory."
                ),
                validator="theoretical_framework_citation",
                location="Introduction / Theoretical Framework",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 217 – Undisclosed survey instrument source
# ---------------------------------------------------------------------------

_SURVEY_INSTRUMENT_RE = re.compile(
    r"\b(?:scale\s+(?:was|were|is|are)\s+(?:used?|adapted?|modified?|administered?|"
    r"administered?\s+to\s+participants?)|"
    r"questionnaire\s+(?:was|were|is|are)\s+(?:used?|adapted?|modified?|"
    r"administered?|designed?|developed?)|"
    r"validated?\s+(?:scale|measure|instrument|questionnaire)|"
    r"(?:psychometric|standardized?)\s+(?:scale|instrument|measure|questionnaire))\b",
    re.IGNORECASE,
)
_INSTRUMENT_SOURCE_RE = re.compile(
    r"\b(?:developed?\s+by\s+[A-Z]|adapted?\s+from\s+[A-Z]|"
    r"(?:scale|questionnaire|instrument|measure)\s+\((?:[A-Z][a-z]+.+?\d{4})\)|"
    r"originally\s+(?:developed?|published?|validated?)\s+by\s+[A-Z]|"
    r"(?:Cronbach.s?\s+alpha|internal\s+consistency|factor\s+(?:structure|loading)|"
    r"convergent\s+validity|discriminant\s+validity|test.retest))\b",
    re.IGNORECASE,
)


def validate_survey_instrument_source(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag use of a survey scale without citing its source or validation.

    Emits ``missing-instrument-source`` (moderate) when a scale or
    questionnaire is used but no developer citation or psychometric properties
    are reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="survey_instrument_source", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="survey_instrument_source", findings=[]
        )

    if not _SURVEY_INSTRUMENT_RE.search(full):
        return ValidationResult(
            validator_name="survey_instrument_source", findings=[]
        )

    if _INSTRUMENT_SOURCE_RE.search(full):
        return ValidationResult(
            validator_name="survey_instrument_source", findings=[]
        )

    return ValidationResult(
        validator_name="survey_instrument_source",
        findings=[
            Finding(
                code="missing-instrument-source",
                severity="moderate",
                message=(
                    "A validated scale or questionnaire is used but no source citation, "
                    "psychometric properties (alpha, validity), or developer credit are given. "
                    "Cite the instrument's original source and report reliability."
                ),
                validator="survey_instrument_source",
                location="Measures / Instruments",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 218 – Missing sampling frame description
# ---------------------------------------------------------------------------

_SAMPLING_FRAME_TRIGGER_RE = re.compile(
    r"\b(?:sample(?:d|ing)?\s+from|participants?\s+were\s+(?:recruited?|selected?|drawn?)|"
    r"population\s+of\s+interest|target\s+population|sampling\s+(?:frame|strategy|method)|"
    r"drawn?\s+from\s+(?:a|the)\s+(?:list|registry|database|pool|cohort|roster))\b",
    re.IGNORECASE,
)
_SAMPLING_FRAME_DESC_RE = re.compile(
    r"\b(?:sampling\s+frame\s+(?:was|consisted?|included?|comprised?)|"
    r"(?:list|registry|database|pool|cohort|roster)\s+of\s+(?:all\s+)?"
    r"(?:eligible\s+)?(?:patients?|participants?|students?|employees?|"
    r"households?|adults?|women|men)\b|"
    r"census\s+of|purposive\s+sampling|stratified\s+(?:random\s+)?sampling|"
    r"random\s+sampling|probability\s+sampling|convenience\s+sampling|"
    r"cluster\s+sampling|snowball\s+sampling)\b",
    re.IGNORECASE,
)


def validate_sampling_frame_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies that mention sampling but do not describe the sampling frame.

    Emits ``missing-sampling-frame`` (minor) when participants are recruited or
    sampled but no sampling frame, strategy, or method is described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="sampling_frame_description", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="sampling_frame_description", findings=[]
        )

    if not _SAMPLING_FRAME_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="sampling_frame_description", findings=[]
        )

    if _SAMPLING_FRAME_DESC_RE.search(full):
        return ValidationResult(
            validator_name="sampling_frame_description", findings=[]
        )

    return ValidationResult(
        validator_name="sampling_frame_description",
        findings=[
            Finding(
                code="missing-sampling-frame",
                severity="minor",
                message=(
                    "Sampling or participant recruitment is mentioned but no sampling "
                    "frame, strategy, or selection method is described. "
                    "Clarify how and from what population participants were selected."
                ),
                validator="sampling_frame_description",
                location="Methods / Participants",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 219 – Unjustified one-tailed test
# ---------------------------------------------------------------------------

_ONE_TAILED_RE = re.compile(
    r"\b(?:one.?tailed?\s+(?:test|significance|p.?value|hypothesis|alpha)|"
    r"directional\s+hypothesis\s+(?:was|were)\s+tested?\s+one.?tailed?|"
    r"one.?sided?\s+(?:test|p.?value|significance))\b",
    re.IGNORECASE,
)
_ONE_TAILED_JUSTIFICATION_RE = re.compile(
    r"\b(?:one.?tailed?\s+(?:test\s+)?(?:was|were|is)\s+(?:justified?|appropriate|"
    r"warranted?|chosen?|selected?)\s+because|"
    r"because\s+(?:we\s+)?(?:hypothesised?|hypothesized?|predicted?|expected?)\s+"
    r"(?:a\s+)?(?:positive|negative|directional|specific)\s+(?:effect|relationship|"
    r"difference|association)|"
    r"prior\s+(?:theory|evidence|literature|research)\s+strongly\s+(?:predicts?|suggests?))\b",
    re.IGNORECASE,
)


def validate_one_tailed_test_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag use of one-tailed tests without explicit justification.

    Emits ``unjustified-one-tailed-test`` (moderate) when a one-tailed test
    is used but no theoretical justification for the directional hypothesis is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="one_tailed_test_justification", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="one_tailed_test_justification", findings=[]
        )

    if not _ONE_TAILED_RE.search(full):
        return ValidationResult(
            validator_name="one_tailed_test_justification", findings=[]
        )

    if _ONE_TAILED_JUSTIFICATION_RE.search(full):
        return ValidationResult(
            validator_name="one_tailed_test_justification", findings=[]
        )

    return ValidationResult(
        validator_name="one_tailed_test_justification",
        findings=[
            Finding(
                code="unjustified-one-tailed-test",
                severity="moderate",
                message=(
                    "A one-tailed test is reported but no justification for the "
                    "directional hypothesis is given. "
                    "Justify the use of a one-tailed test with prior theory or strong "
                    "directional evidence, or use a two-tailed test."
                ),
                validator="one_tailed_test_justification",
                location="Statistical Analysis",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 220 – Data fabrication / wishful results red flags
# ---------------------------------------------------------------------------

_SUSPICIOUSLY_ROUND_P_RE = re.compile(
    r"\b(?:p\s*(?:<|=)\s*0?\.0+[15]0+\b)",
    re.IGNORECASE,
)
_GRATUITOUS_SIGNIFICANCE_RE = re.compile(
    r"\b(?:highly\s+significant|strongly\s+significant|extremely\s+significant|"
    r"clearly\s+significant|overwhelmingly\s+significant|"
    r"all\s+(?:results?|tests?|outcomes?|findings?)\s+were\s+(?:significant|statistically\s+significant)|"
    r"every\s+(?:result|test|outcome|finding|variable|predictor)\s+(?:was|were)\s+significant)\b",
    re.IGNORECASE,
)


def validate_gratuitous_significance_language(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag language suggesting inflated or implausible significance.

    Emits ``implausible-significance-language`` (major) when the manuscript
    uses phrases like 'all results were significant' or 'highly significant'
    that may signal p-hacking or reporting bias.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="gratuitous_significance_language", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="gratuitous_significance_language", findings=[]
        )

    if not _GRATUITOUS_SIGNIFICANCE_RE.search(full):
        return ValidationResult(
            validator_name="gratuitous_significance_language", findings=[]
        )

    return ValidationResult(
        validator_name="gratuitous_significance_language",
        findings=[
            Finding(
                code="implausible-significance-language",
                severity="major",
                message=(
                    "Language suggesting that all or virtually all results are "
                    "statistically significant detected. "
                    "This may indicate p-hacking, selective reporting, or inflated claims. "
                    "Report null findings and use precise language."
                ),
                validator="gratuitous_significance_language",
                location="Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 221 – Unclear unit of analysis
# ---------------------------------------------------------------------------

_UNIT_MISMATCH_TRIGGER_RE = re.compile(
    r"\b(?:multilevel|nested\s+(?:data|design|structure|model)|"
    r"hierarchical\s+(?:data|structure|model|design)|"
    r"clustered\s+(?:data|design|observations?|sample)|"
    r"students?\s+(?:nested|clustered)\s+(?:within|in)\s+"
    r"(?:classrooms?|schools?|teachers?|districts?)|"
    r"patients?\s+(?:nested|clustered)\s+(?:within|in)\s+"
    r"(?:hospitals?|clinics?|providers?|wards?)|"
    r"employees?\s+(?:nested|clustered)\s+(?:within|in)\s+"
    r"(?:teams?|departments?|organisations?|firms?))\b",
    re.IGNORECASE,
)
_UNIT_OF_ANALYSIS_RE = re.compile(
    r"\b(?:unit\s+of\s+analysis|level\s+of\s+analysis|"
    r"individual.level|group.level|school.level|classroom.level|"
    r"patient.level|hospital.level|HLM\b|MLM\b|mixed.effects?\s+model|"
    r"random.effects?\s+model|fixed.effects?\s+model|"
    r"within.cluster\s+(?:variance|correlation))\b",
    re.IGNORECASE,
)


def validate_unit_of_analysis_clarity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag nested/clustered designs without specifying the unit of analysis.

    Emits ``unclear-unit-of-analysis`` (moderate) when nested or clustered
    data are described but the unit of analysis is not explicitly addressed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="unit_of_analysis_clarity", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="unit_of_analysis_clarity", findings=[]
        )

    if not _UNIT_MISMATCH_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="unit_of_analysis_clarity", findings=[]
        )

    if _UNIT_OF_ANALYSIS_RE.search(full):
        return ValidationResult(
            validator_name="unit_of_analysis_clarity", findings=[]
        )

    return ValidationResult(
        validator_name="unit_of_analysis_clarity",
        findings=[
            Finding(
                code="unclear-unit-of-analysis",
                severity="moderate",
                message=(
                    "Nested or clustered data structure detected but the unit of analysis "
                    "is not explicitly stated. Clarify whether the analysis is at the "
                    "individual, group, or cluster level and handle nesting appropriately."
                ),
                validator="unit_of_analysis_clarity",
                location="Methods / Statistical Analysis",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 222 – Missing pre-registration statement
# ---------------------------------------------------------------------------

_PREREG_TRIGGER_RE = re.compile(
    r"\b(?:confirmatory\s+(?:analysis|study|test|hypothesis)|"
    r"hypothesis\s+(?:was|were)\s+(?:generated?|formulated?|specified?)\s+"
    r"(?:a\s+priori|prior\s+to|before)\s+(?:data\s+collection|analysis)|"
    r"a\s+priori\s+hypothesis|predicted?\s+(?:that|a)\s+"
    r"(?:positive|negative|significant|higher|lower)\s+(?:effect|difference|association|relationship))\b",
    re.IGNORECASE,
)
_PREREG_STATEMENT_RE = re.compile(
    r"\b(?:pre.?register(?:ed|ing)?|AsPredicted|OSF\s+(?:pre.?registration|registration)|"
    r"registered?\s+(?:at|on|with|via)|ClinicalTrials\.gov|ISRCTN|"
    r"trial\s+(?:was|is)\s+registered?|pre.?registration\s+(?:at|on|with)|"
    r"study\s+was\s+pre.?registered?)\b",
    re.IGNORECASE,
)


def validate_apriori_preregistration_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag confirmatory studies without a pre-registration statement.

    Emits ``missing-preregistration-statement`` (moderate) when a priori
    hypotheses are claimed but no pre-registration URL or registry is cited.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="apriori_preregistration_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="apriori_preregistration_statement", findings=[]
        )

    if not _PREREG_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="apriori_preregistration_statement", findings=[]
        )

    if _PREREG_STATEMENT_RE.search(full):
        return ValidationResult(
            validator_name="apriori_preregistration_statement", findings=[]
        )

    return ValidationResult(
        validator_name="apriori_preregistration_statement",
        findings=[
            Finding(
                code="missing-preregistration-statement",
                severity="moderate",
                message=(
                    "Confirmatory a priori hypothesis language detected but no "
                    "pre-registration (OSF, ClinicalTrials.gov, etc.) is cited. "
                    "Pre-register confirmatory studies and provide the registration link."
                ),
                validator="preregistration_statement",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 223 – Selective citation of supporting literature
# ---------------------------------------------------------------------------

_CONSISTENT_CITE_RE = re.compile(
    r"\b(?:consistently\s+(?:shows?|found?|demonstrated?|supports?|confirms?)|"
    r"universally\s+(?:agreed?|accepted?|supported?)|"
    r"overwhelmingly\s+(?:supports?|shows?|found?|demonstrated?)|"
    r"all\s+(?:studies|research|evidence|literature)\s+(?:shows?|found?|agree|support)|"
    r"no\s+study\s+has\s+(?:found?|reported?|shown?)\s+"
    r"(?:a\s+)?(?:null|negative|opposing|contradictory)\s+(?:result|effect|finding))\b",
    re.IGNORECASE,
)
_SELECTIVE_CITE_CAVEAT_RE = re.compile(
    r"\b(?:some\s+(?:studies|research|evidence|authors?|researchers?)|"
    r"mixed\s+(?:evidence|results?|findings?)|"
    r"inconsistent\s+(?:evidence|results?|findings?)|"
    r"contrary\s+(?:evidence|findings?|results?)|"
    r"however|nevertheless|although|despite|notwithstanding|"
    r"in\s+contrast|on\s+the\s+other\s+hand)\b",
    re.IGNORECASE,
)


def validate_selective_literature_citation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts that claim universal consensus without acknowledging contrary evidence.

    Emits ``selective-literature-citation`` (minor) when language implies
    all literature agrees but no caveats about mixed or contrary findings appear.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="selective_literature_citation", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="selective_literature_citation", findings=[]
        )

    if not _CONSISTENT_CITE_RE.search(full):
        return ValidationResult(
            validator_name="selective_literature_citation", findings=[]
        )

    if _SELECTIVE_CITE_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="selective_literature_citation", findings=[]
        )

    return ValidationResult(
        validator_name="selective_literature_citation",
        findings=[
            Finding(
                code="selective-literature-citation",
                severity="minor",
                message=(
                    "Language implying universal consensus in the literature is used "
                    "without acknowledging contrary or mixed findings. "
                    "Review the literature for opposing evidence and note any inconsistencies."
                ),
                validator="selective_literature_citation",
                location="Introduction / Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 224 – Missing participant compensation / incentive disclosure
# ---------------------------------------------------------------------------

_COMPENSATION_TRIGGER_RE = re.compile(
    r"\b(?:participants?\s+(?:were\s+)?(?:paid|compensated?|reimbursed?|rewarded?|"
    r"received?\s+(?:payment|compensation|reimbursement|reward|credit|points?))|"
    r"(?:paid|compensated?|reimbursed?)\s+(?:participants?|volunteers?|subjects?)|"
    r"monetary\s+compensation|gift\s+card|course\s+credit|extra\s+credit|"
    r"payment\s+(?:was|were)\s+(?:provided?|offered?|given?)|"
    r"incentive\s+(?:was|were)\s+(?:provided?|offered?|given?))\b",
    re.IGNORECASE,
)
_COMPENSATION_AMOUNT_RE = re.compile(
    r"\b(?:\$\s*\d+|\€\s*\d+|£\s*\d+|\d+\s*(?:dollars?|euros?|pounds?)|"
    r"\d+\s*(?:course\s+)?credits?|\d+\s*(?:points?|tokens?)|"
    r"\d+\s*(?:USD|GBP|EUR)\b|"
    r"(?:no\s+compensation|not\s+compensated?|unpaid|volunteer(?:ed)?|"
    r"no\s+(?:monetary\s+)?incentive))\b",
    re.IGNORECASE,
)


def validate_participant_compensation_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies mentioning compensation without disclosing the amount.

    Emits ``missing-compensation-amount`` (minor) when participant compensation
    is mentioned but no specific amount or type is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="participant_compensation_disclosure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="participant_compensation_disclosure", findings=[]
        )

    if not _COMPENSATION_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="participant_compensation_disclosure", findings=[]
        )

    if _COMPENSATION_AMOUNT_RE.search(full):
        return ValidationResult(
            validator_name="participant_compensation_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="participant_compensation_disclosure",
        findings=[
            Finding(
                code="missing-compensation-amount",
                severity="minor",
                message=(
                    "Participant compensation is mentioned but the specific amount, "
                    "type (cash, gift card, course credit), or value is not disclosed. "
                    "Report the exact compensation provided."
                ),
                validator="participant_compensation_disclosure",
                location="Participants / Ethics",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 225 – Overclaiming causal language in observational studies
# ---------------------------------------------------------------------------

_OBSERVATIONAL_DESIGN_RE = re.compile(
    r"\b(?:cross.?sectional|longitudinal\s+(?:survey|study|cohort)|observational\s+(?:study|design|data)|"
    r"survey\s+(?:study|data|design)|secondary\s+data\s+analysis|"
    r"archival\s+(?:data|study)|retrospective\s+(?:study|analysis|cohort))\b",
    re.IGNORECASE,
)
_CAUSAL_CLAIM_RE = re.compile(
    r"\b(?:causes?\b|caused?\s+(?:by|a\s+)?|causally?|causation\b|"
    r"(?:our\s+)?(?:results?|findings?|data|study)\s+(?:shows?|demonstrates?|"
    r"proves?|establishes?)\s+(?:that\s+)?(?:\w+\s+){0,4}(?:causes?|causes?\s+a)\b|"
    r"impact\s+of\s+\w+\s+on\s+\w+\s+(?:was|were)\s+(?:found|observed|demonstrated)|"
    r"effect\s+of\s+\w+\s+on\s+\w+\s+(?:was|were)\s+established?)\b",
    re.IGNORECASE,
)
_CAUSAL_CAVEAT_RE = re.compile(
    r"\b(?:association\b|correlation\b|related?\s+to|linked?\s+to|"
    r"cannot\s+(?:establish|infer|determine)\s+causality?|"
    r"causal\s+(?:inference|claims?|interpretation)\s+(?:cannot|should\s+not)|"
    r"longitudinal\s+designs?\s+(?:are\s+needed?|required?|would\s+be)|"
    r"future\s+(?:experiment(?:al)?|RCT|randomized)\s+(?:studies?|research)\s+(?:are\s+needed?|required?))\b",
    re.IGNORECASE,
)


def validate_observational_causal_language(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag causal language in observational studies without appropriate caveats.

    Emits ``overclaimed-causality-observational`` (major) when causal language
    is used in an observational or cross-sectional study without caveats.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="observational_causal_language", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="observational_causal_language", findings=[]
        )

    if not _OBSERVATIONAL_DESIGN_RE.search(full):
        return ValidationResult(
            validator_name="observational_causal_language", findings=[]
        )

    if not _CAUSAL_CLAIM_RE.search(full):
        return ValidationResult(
            validator_name="observational_causal_language", findings=[]
        )

    if _CAUSAL_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="observational_causal_language", findings=[]
        )

    return ValidationResult(
        validator_name="observational_causal_language",
        findings=[
            Finding(
                code="overclaimed-causality-observational",
                severity="major",
                message=(
                    "Causal language used in an observational study without appropriate "
                    "caveats. Cross-sectional and observational designs do not support "
                    "causal inference. Use associational language and acknowledge the limitation."
                ),
                validator="observational_causal_language",
                location="Discussion / Conclusion",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 226 – Missing acknowledgement section
# ---------------------------------------------------------------------------

_FUNDING_MENTION_RE = re.compile(
    r"\b(?:funded?\s+by|supported?\s+by|grant\s+(?:from|number|no\.?)|"
    r"financially\s+supported?|financial\s+support\s+(?:from|by)|"
    r"funding\s+(?:from|by|source)|research\s+support\s+(?:from|by)|"
    r"NIH\b|NSF\b|Wellcome\s+Trust|European\s+Research\s+Council|"
    r"NHMRC\b|SSHRC\b|DFG\b)\b",
    re.IGNORECASE,
)
_ACKNOWLEDGEMENT_RE = re.compile(
    r"\b(?:acknowledg(?:e|ements?|ments?)\b|we\s+(?:thank|acknowledge|are\s+grateful)|"
    r"the\s+authors?\s+(?:thank|acknowledge|are\s+grateful)|"
    r"(?:funding|financial\s+support)\s+(?:was\s+)?(?:provided?|received?)\s+from)\b",
    re.IGNORECASE,
)


def validate_acknowledgement_section(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies mentioning funding without an acknowledgement section.

    Emits ``missing-acknowledgement-section`` (minor) when funding sources
    are mentioned but no acknowledgement or funding statement section is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="acknowledgement_section", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="acknowledgement_section", findings=[]
        )

    if not _FUNDING_MENTION_RE.search(full):
        return ValidationResult(
            validator_name="acknowledgement_section", findings=[]
        )

    if _ACKNOWLEDGEMENT_RE.search(full):
        return ValidationResult(
            validator_name="acknowledgement_section", findings=[]
        )

    return ValidationResult(
        validator_name="acknowledgement_section",
        findings=[
            Finding(
                code="missing-acknowledgement-section",
                severity="minor",
                message=(
                    "Funding source or institutional support is mentioned but no "
                    "acknowledgement or funding statement section is present. "
                    "Add an acknowledgements section disclosing all funding sources."
                ),
                validator="acknowledgement_section",
                location="Acknowledgements / Funding",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 227 – Missing conflict of interest statement
# ---------------------------------------------------------------------------

_COI_TRIGGER_RE = re.compile(
    r"\b(?:industry.?funded|funded?\s+by\s+(?:a\s+)?(?:pharmaceutical|biotech|"
    r"corporate|commercial|industry)|received?\s+(?:honoraria?|consulting\s+fees?|"
    r"speaker.s?\s+bureau|advisory\s+board\s+fees?)|"
    r"employed?\s+by\s+(?:a\s+)?(?:company|corporation|industry|pharma)|"
    r"stock\s+options?|equity\s+(?:in|interest)|"
    r"patent\s+(?:holder|pending|filed)\b)\b",
    re.IGNORECASE,
)
_COI_STATEMENT_RE = re.compile(
    r"\b(?:conflict(?:s)?\s+of\s+interest|competing\s+interest(?:s)?|"
    r"disclosure\s+statement|the\s+authors?\s+(?:declare|report|disclose)|"
    r"no\s+(?:conflict|competing)\s+(?:of\s+interest|interest)|"
    r"potential\s+conflict)\b",
    re.IGNORECASE,
)


def validate_conflict_of_interest_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag industry-funded or potentially conflicted studies without a COI statement.

    Emits ``missing-conflict-of-interest-statement`` (major) when industry
    funding or financial relationships are mentioned but no conflict of interest
    statement is present.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="conflict_of_interest_statement", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="conflict_of_interest_statement", findings=[]
        )

    if not _COI_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="conflict_of_interest_statement", findings=[]
        )

    if _COI_STATEMENT_RE.search(full):
        return ValidationResult(
            validator_name="conflict_of_interest_statement", findings=[]
        )

    return ValidationResult(
        validator_name="conflict_of_interest_statement",
        findings=[
            Finding(
                code="missing-conflict-of-interest-statement",
                severity="major",
                message=(
                    "Industry funding or financial relationship detected but no "
                    "conflict of interest or competing interests statement is present. "
                    "Disclose all financial relationships that could bias the research."
                ),
                validator="conflict_of_interest_statement",
                location="Disclosures / Ethics",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 228 – Imprecise age reporting
# ---------------------------------------------------------------------------

_AGE_REPORTED_RE = re.compile(
    r"\b(?:mean\s+age|average\s+age|age\s+(?:range|distribution|of\s+participants?)|"
    r"participants?\s+(?:were|ranged?)\s+(?:aged?|between)|"
    r"age(?:d)?\s+(?:between|\d))\b",
    re.IGNORECASE,
)
_AGE_PRECISION_RE = re.compile(
    r"\b(?:M\s*(?:age)?\s*=\s*\d+\.?\d*\s*(?:years?)?\s*[\(,]\s*SD|"
    r"mean\s+age\s*(?:was|=)\s*\d+\.?\d*\s*(?:years?)?\s*[\(,]\s*SD|"
    r"aged?\s+\d+\s*(?:to|[-–])\s*\d+\s*years?|"
    r"age\s+range\s*(?:was|:|=)?\s*\d+\s*[-–]\s*\d+|"
    r"\d+\s*(?:to|[-–])\s*\d+\s*years?\s*(?:old|of\s+age))\b",
    re.IGNORECASE,
)


def validate_age_reporting_precision(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies mentioning participant age without providing M and SD or range.

    Emits ``imprecise-age-reporting`` (minor) when age is mentioned but no
    mean with SD or numeric range is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="age_reporting_precision", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="age_reporting_precision", findings=[]
        )

    if not _AGE_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="age_reporting_precision", findings=[]
        )

    if _AGE_PRECISION_RE.search(full):
        return ValidationResult(
            validator_name="age_reporting_precision", findings=[]
        )

    return ValidationResult(
        validator_name="age_reporting_precision",
        findings=[
            Finding(
                code="imprecise-age-reporting",
                severity="minor",
                message=(
                    "Participant age is mentioned but no mean with SD or numeric "
                    "age range is provided. Report age as M (SD) or a numeric range."
                ),
                validator="age_reporting_precision",
                location="Participants",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 229 – Inadequate description of statistical software
# ---------------------------------------------------------------------------

_SOFTWARE_STAT_RE = re.compile(
    r"\b(?:analyses?\s+were\s+(?:conducted?|performed?|run|carried\s+out)\s+using|"
    r"statistical\s+analyses?\s+(?:were\s+)?(?:conducted?|performed?|run)\s+(?:in|using|with)|"
    r"data\s+(?:were\s+)?(?:analysed?|analyzed?)\s+(?:in|using|with)|"
    r"we\s+used?\s+(?:R\b|SPSS\b|SAS\b|Stata\b|Python\b|MATLAB\b|MPlus\b|"
    r"HLM\b|LISREL\b|AMOS\b|jamovi\b|JASP\b|Excel\b))\b",
    re.IGNORECASE,
)
_SOFTWARE_VERSION_STAT_RE = re.compile(
    r"\b(?:R\s+(?:version\s+)?\d+\.\d+|SPSS\s+(?:version\s+)?\d+|"
    r"SAS\s+(?:version\s+)?\d+|Stata\s+(?:version\s+)?\d+|"
    r"Python\s+(?:version\s+|v\s*)?\d+\.\d+|MATLAB\s+R\d{4}[ab]|"
    r"MPlus\s+(?:version\s+)?\d+|jamovi\s+(?:version\s+)?\d+|"
    r"JASP\s+(?:version\s+)?\d+\.\d+|"
    r"version\s+\d+[\.\d]*\s+\(.*?\)|"
    r"v\s*\d+\.\d+)\b",
    re.IGNORECASE,
)


def validate_statistical_software_version(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag statistical analysis mentions without specific software version.

    Emits ``missing-statistical-software-version`` (minor) when statistical
    software is named but no version number is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="statistical_software_version", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="statistical_software_version", findings=[]
        )

    if not _SOFTWARE_STAT_RE.search(full):
        return ValidationResult(
            validator_name="statistical_software_version", findings=[]
        )

    if _SOFTWARE_VERSION_STAT_RE.search(full):
        return ValidationResult(
            validator_name="statistical_software_version", findings=[]
        )

    return ValidationResult(
        validator_name="statistical_software_version",
        findings=[
            Finding(
                code="missing-statistical-software-version",
                severity="minor",
                message=(
                    "Statistical software is named but no version number is provided. "
                    "Report the exact software version used (e.g., R version 4.3.1, SPSS 29)."
                ),
                validator="statistical_software_version",
                location="Statistical Analysis",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 230 – Undisclosed sensitivity analysis
# ---------------------------------------------------------------------------

_SENSITIVITY_TRIGGER_RE = re.compile(
    r"\b(?:robust(?:ness)?\s+(?:check|analysis|test)|"
    r"sensitivity\s+analysis\s+(?:was|were|is|are)\s+(?:needed?|warranted?|recommended?|"
    r"appropriate|advisable)|"
    r"results?\s+(?:may|might|could|should)\s+be\s+(?:sensitive|robust(?:ly)?)\s+to|"
    r"impact\s+of\s+(?:outliers?|influential\s+cases?|extreme\s+values?)\s+on\s+"
    r"(?:the\s+)?(?:results?|findings?|conclusions?))\b",
    re.IGNORECASE,
)
_SENSITIVITY_CONDUCTED_RE = re.compile(
    r"\b(?:sensitivity\s+analys(?:is|es)\s+(?:was|were|showed?|confirmed?|"
    r"revealed?|indicated?|demonstrated?)|"
    r"we\s+(?:conducted?|performed?|ran|carried\s+out)\s+"
    r"(?:a\s+)?sensitivity\s+analys(?:is|es)|"
    r"results?\s+were\s+(?:robust|consistent|unchanged?|similar)\s+"
    r"(?:across|in|when|after)\s+(?:sensitivity|robustness)|"
    r"excluding\s+(?:outliers?|influential\s+observations?|extreme\s+values?)\s+"
    r"(?:did\s+not|had\s+no|yielded?\s+similar))\b",
    re.IGNORECASE,
)


def validate_warranted_sensitivity_analysis(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies recommending sensitivity analysis without reporting one.

    Emits ``missing-warranted-sensitivity-analysis`` (moderate) when text suggests
    sensitivity analyses are warranted or needed but none are reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="warranted_sensitivity_analysis", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="warranted_sensitivity_analysis", findings=[]
        )

    if not _SENSITIVITY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="warranted_sensitivity_analysis", findings=[]
        )

    if _SENSITIVITY_CONDUCTED_RE.search(full):
        return ValidationResult(
            validator_name="warranted_sensitivity_analysis", findings=[]
        )

    return ValidationResult(
        validator_name="warranted_sensitivity_analysis",
        findings=[
            Finding(
                code="missing-warranted-sensitivity-analysis",
                severity="moderate",
                message=(
                    "Text suggests sensitivity analysis is warranted but none is reported. "
                    "Conduct and report sensitivity or robustness checks to support "
                    "the stability of findings."
                ),
                validator="warranted_sensitivity_analysis",
                location="Statistical Analysis / Discussion",
                evidence=[],
            )
        ],
    )
