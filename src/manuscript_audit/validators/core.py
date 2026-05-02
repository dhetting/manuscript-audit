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
        validate_ai_tool_disclosure(parsed, classification),
        validate_between_group_effect_size(parsed, classification),
        validate_convenience_sample_generalization(parsed, classification),
        validate_icc_reliability_reporting(parsed, classification),
        validate_anova_post_hoc_reporting(parsed, classification),
        validate_adverse_events_reporting(parsed, classification),
        validate_construct_operationalization(parsed, classification),
        validate_regression_coefficient_ci(parsed, classification),
        validate_longitudinal_followup_duration(parsed, classification),
        validate_bayesian_reporting(parsed, classification),
        validate_floor_ceiling_effect_check(parsed, classification),
        validate_hazard_ratio_ci(parsed, classification),
        validate_outlier_removal_impact(parsed, classification),
        validate_multilevel_icc_reporting(parsed, classification),
        validate_citation_currency(parsed, classification),
        validate_proportion_confidence_interval(parsed, classification),
        validate_blinding_procedure_description(parsed, classification),
        validate_primary_outcome_change_disclosure(parsed, classification),
        validate_null_result_discussion(parsed, classification),
        validate_racial_ethnic_composition(parsed, classification),
        validate_single_item_measure_reliability(parsed, classification),
        validate_mediator_temporality(parsed, classification),
        validate_effect_size_interpretation(parsed, classification),
        validate_comparison_group_equivalence(parsed, classification),
        validate_implicit_theory_test(parsed, classification),
        validate_non_normal_distribution_test(parsed, classification),
        validate_regression_sample_size_adequacy(parsed, classification),
        validate_scale_directionality_disclosure(parsed, classification),
        validate_attrition_rate_reporting(parsed, classification),
        validate_dichotomization_of_continuous_variable(parsed, classification),
        validate_ecological_fallacy_warning(parsed, classification),
        validate_standardised_mean_difference_units(parsed, classification),
        validate_retrospective_data_collection_disclosure(parsed, classification),
        validate_treatment_fidelity_reporting(parsed, classification),
        validate_factorial_design_interaction_test(parsed, classification),
        validate_regression_multicollinearity_check(parsed, classification),
        validate_intention_to_treat_analysis(parsed, classification),
        validate_confidence_interval_direction_interpretation(parsed, classification),
        validate_longitudinal_missing_data_method(parsed, classification),
        validate_cluster_sampling_correction(parsed, classification),
        validate_non_experimental_confound_discussion(parsed, classification),
        validate_complete_case_analysis_bias(parsed, classification),
        validate_analytic_strategy_prespecification(parsed, classification),
        validate_self_report_bias_acknowledgement(parsed, classification),
        validate_p_value_reporting_precision(parsed, classification),
        validate_moderator_analysis_interpretation(parsed, classification),
        validate_measurement_occasion_labelling(parsed, classification),
        validate_statistical_conclusion_validity(parsed, classification),
        validate_scale_reliability_reporting(parsed, classification),
        validate_pilot_study_scope_limitation(parsed, classification),
        validate_literature_search_recency(parsed, classification),
        validate_publication_bias_acknowledgement(parsed, classification),
        validate_replication_citation(parsed, classification),
        validate_negative_binomial_overdispersion(parsed, classification),
        validate_zero_inflated_data_handling(parsed, classification),
        validate_variance_homogeneity_check(parsed, classification),
        validate_path_model_fit_indices(parsed, classification),
        validate_post_hoc_power_caution(parsed, classification),
        validate_ancova_covariate_balance(parsed, classification),
        validate_partial_eta_squared_reporting(parsed, classification),
        validate_cohens_d_reporting(parsed, classification),
        validate_sequential_testing_correction(parsed, classification),
        validate_adaptive_design_disclosure(parsed, classification),
        validate_kaplan_meier_censoring_note(parsed, classification),
        validate_cox_proportional_hazards_assumption(parsed, classification),
        validate_competing_risks_disclosure(parsed, classification),
        validate_propensity_score_balance(parsed, classification),
        validate_instrumental_variable_disclosure(parsed, classification),
        validate_multilevel_random_effects_justification(parsed, classification),
        validate_cross_level_interaction_interpretation(parsed, classification),
        validate_repeated_measures_sphericity(parsed, classification),
        validate_survey_sampling_weight(parsed, classification),
        validate_finite_population_correction(parsed, classification),
        validate_mcmc_convergence_reporting(parsed, classification),
        validate_bayes_factor_interpretation(parsed, classification),
        validate_waic_looic_reporting(parsed, classification),
        validate_informative_prior_justification(parsed, classification),
        validate_posterior_predictive_check(parsed, classification),
        validate_train_test_split_disclosure(parsed, classification),
        validate_hyperparameter_tuning_disclosure(parsed, classification),
        validate_feature_importance_method(parsed, classification),
        validate_data_leakage_prevention(parsed, classification),
        validate_ml_uncertainty_quantification(parsed, classification),
        validate_class_imbalance_handling(parsed, classification),
        validate_model_calibration_reporting(parsed, classification),
        validate_fairness_metric_reporting(parsed, classification),
        validate_transfer_learning_disclosure(parsed, classification),
        validate_cross_validation_strategy(parsed, classification),
        validate_text_preprocessing_disclosure(parsed, classification),
        validate_word_embedding_details(parsed, classification),
        validate_topic_model_parameter_disclosure(parsed, classification),
        validate_inter_annotator_agreement(parsed, classification),
        validate_sentiment_lexicon_disclosure(parsed, classification),
        validate_mri_acquisition_parameters(parsed, classification),
        validate_fmri_preprocessing_pipeline(parsed, classification),
        validate_neuroimaging_atlas_disclosure(parsed, classification),
        validate_multiple_comparisons_neuroimaging(parsed, classification),
        validate_roi_definition_disclosure(parsed, classification),
        validate_rna_seq_normalization_disclosure(parsed, classification),
        validate_batch_effect_correction(parsed, classification),
        validate_multiple_testing_genomics(parsed, classification),
        validate_pathway_enrichment_method(parsed, classification),
        validate_genome_reference_disclosure(parsed, classification),
        validate_strobe_observational_reporting(parsed, classification),
        validate_selection_bias_discussion(parsed, classification),
        validate_information_bias_discussion(parsed, classification),
        validate_dose_response_relationship(parsed, classification),
        validate_follow_up_rate_reporting(parsed, classification),
        validate_cost_effectiveness_perspective(parsed, classification),
        validate_discount_rate_disclosure(parsed, classification),
        validate_uncertainty_analysis_health_economic(parsed, classification),
        validate_qaly_utility_source(parsed, classification),
        validate_markov_model_cycle_length(parsed, classification),
        validate_measurement_invariance_testing(parsed, classification),
        validate_convergent_discriminant_validity(parsed, classification),
        validate_irt_model_fit(parsed, classification),
        validate_test_retest_reliability(parsed, classification),
        validate_norm_reference_group(parsed, classification),
        validate_theoretical_saturation_claim(parsed, classification),
        validate_member_checking_disclosure(parsed, classification),
        validate_reflexivity_statement(parsed, classification),
        validate_negative_case_analysis(parsed, classification),
        validate_thick_description_transferability(parsed, classification),
        validate_mixed_methods_design_rationale(parsed, classification),
        validate_simulation_parameter_justification(parsed, classification),
        validate_bootstrap_sample_size(parsed, classification),
        validate_monte_carlo_replications(parsed, classification),
        validate_agent_based_model_validation(parsed, classification),
        validate_network_analysis_density_reporting(parsed, classification),
        validate_spatial_autocorrelation_check(parsed, classification),
        validate_structural_break_test(parsed, classification),
        validate_variance_inflation_factor_reporting(parsed, classification),
        validate_ordinal_regression_assumption(parsed, classification),
        validate_granger_causality_disclosure(parsed, classification),
        validate_cointegration_test_disclosure(parsed, classification),
        validate_unit_root_test_disclosure(parsed, classification),
        validate_arch_garch_specification(parsed, classification),
        validate_panel_effects_justification(parsed, classification),
        validate_arima_order_disclosure(parsed, classification),
        validate_var_model_lag_order(parsed, classification),
        validate_impulse_response_identification(parsed, classification),
        validate_forecast_evaluation_metrics(parsed, classification),
        validate_seasonal_adjustment_disclosure(parsed, classification),
        validate_interrupted_time_series_control(parsed, classification),
        validate_difference_in_differences_parallel_trends(parsed, classification),
        validate_regression_discontinuity_bandwidth(parsed, classification),
        validate_synthetic_control_pre_period_fit(parsed, classification),
        validate_event_study_window_specification(parsed, classification),
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

# ---------------------------------------------------------------------------
# Phase 231 – Undisclosed use of AI/LLM tools
# ---------------------------------------------------------------------------

_AI_TOOL_RE = re.compile(
    r"\b(?:ChatGPT|GPT.?4|GPT.?3|Claude\b|Gemini\b|Copilot\b|"
    r"large\s+language\s+model|LLM\b|generative\s+AI|"
    r"AI.?generated|AI.?assisted|AI\s+tool|"
    r"(?:used?|employed?|leveraged?|utilised?)\s+(?:an?\s+)?AI)\b",
    re.IGNORECASE,
)
_AI_DISCLOSURE_RE = re.compile(
    r"\b(?:AI\s+(?:tool|assistance|use|utilization|usage)\s+(?:disclosure|statement)|"
    r"AI.?generated\s+content\s+(?:was|were)\s+(?:reviewed?|edited?|verified?|"
    r"checked?|confirmed?|validated?|checked?\s+for\s+accuracy)|"
    r"generative\s+AI\s+(?:was|were)\s+used?\s+(?:to|for)\s+\w+\s+(?:and\s+)?"
    r"(?:reviewed?|edited?|verified?|checked?|confirmed?|validated?)|"
    r"disclosure[:\s]+(?:AI|ChatGPT|LLM)|"
    r"(?:we|the\s+authors?)\s+(?:acknowledge|disclose)\s+the\s+use\s+of\s+"
    r"(?:ChatGPT|GPT|Claude|Gemini|Copilot|LLM|AI)|"
    r"author\s+contributions?.*?AI|AI.*?author\s+contributions?)\b",
    re.IGNORECASE,
)


def validate_ai_tool_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts that mention AI tools without a proper disclosure.

    Emits ``missing-ai-tool-disclosure`` (moderate) when ChatGPT, LLMs, or
    similar AI tools are mentioned but no disclosure of how they were used is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="ai_tool_disclosure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="ai_tool_disclosure", findings=[]
        )

    if not _AI_TOOL_RE.search(full):
        return ValidationResult(
            validator_name="ai_tool_disclosure", findings=[]
        )

    if _AI_DISCLOSURE_RE.search(full):
        return ValidationResult(
            validator_name="ai_tool_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="ai_tool_disclosure",
        findings=[
            Finding(
                code="missing-ai-tool-disclosure",
                severity="moderate",
                message=(
                    "AI or LLM tools (ChatGPT, GPT-4, Claude, etc.) are mentioned "
                    "but no disclosure of their specific use in the research process is given. "
                    "Disclose how and where AI tools were used (writing, editing, analysis)."
                ),
                validator="ai_tool_disclosure",
                location="Methods / Disclosures",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 232 – Missing inter-group effect size comparison
# ---------------------------------------------------------------------------

_BETWEEN_GROUP_DIFF_RE = re.compile(
    r"\b(?:(?:significant|marginal|trending?)\s+difference\s+between\s+(?:groups?|conditions?)|"
    r"groups?\s+(?:differed?|varied?)\s+(?:significantly|marginally)\s+(?:on|in)|"
    r"between.group\s+(?:comparison|difference|test)|"
    r"t\s*\(\s*\d+\s*\)\s*=\s*[-\d\.]+\s*,\s*p\s*[<=]\s*0\.\d+|"
    r"F\s*\(\s*\d+\s*,\s*\d+\s*\)\s*=\s*\d+\.\d+\s*,\s*p\s*[<=]\s*0\.\d+)\b",
    re.IGNORECASE,
)
_BETWEEN_GROUP_ES_RE = re.compile(
    r"\b(?:Cohen.s?\s*d\s*=|Hedges.?\s*g\s*=|Glass.?\s*delta\s*=|"
    r"eta.?squared\s*=|partial\s+eta.?squared\s*=|omega.?squared\s*=|"
    r"effect\s+size\s*(?:was|=|:|is)\s*(?:small|medium|large|\d+\.\d+)|"
    r"\bd\s*=\s*[-\d\.]+|\bg\s*=\s*[-\d\.]+|"
    r"d\s*=\s*0\.\d+|g\s*=\s*0\.\d+)\b",
    re.IGNORECASE,
)


def validate_between_group_effect_size(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag between-group comparisons reported without effect sizes.

    Emits ``missing-between-group-effect-size`` (moderate) when significant
    between-group differences are reported but no standardised effect size is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="between_group_effect_size", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="between_group_effect_size", findings=[]
        )

    if not _BETWEEN_GROUP_DIFF_RE.search(full):
        return ValidationResult(
            validator_name="between_group_effect_size", findings=[]
        )

    if _BETWEEN_GROUP_ES_RE.search(full):
        return ValidationResult(
            validator_name="between_group_effect_size", findings=[]
        )

    return ValidationResult(
        validator_name="between_group_effect_size",
        findings=[
            Finding(
                code="missing-between-group-effect-size",
                severity="moderate",
                message=(
                    "Between-group comparison results reported without a standardised "
                    "effect size (Cohen's d, Hedges' g, eta-squared). "
                    "Report effect sizes alongside significance tests."
                ),
                validator="between_group_effect_size",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 233 – Overclaiming from non-representative sample
# ---------------------------------------------------------------------------

_CONVENIENCE_SAMPLE_RE = re.compile(
    r"\b(?:convenience\s+sample|undergraduate\s+students?|WEIRD\s+sample|"
    r"student\s+sample|recruited?\s+(?:from|via|through)\s+"
    r"(?:a\s+)?(?:university|college|MTurk|Amazon\s+Mechanical\s+Turk|"
    r"online\s+(?:panel|platform)|crowdsourcing\s+platform)|"
    r"non.?representative\s+sample)\b",
    re.IGNORECASE,
)
_GENERALIZE_FROM_CONVENIENCE_RE = re.compile(
    r"\b(?:findings?\s+(?:generalise|generalize)\s+to\s+(?:the\s+)?(?:general|broader?|"
    r"wider?|larger?)\s+(?:population|public|society|adults?)|"
    r"results?\s+are\s+(?:generalisable?|generalizable?)\s+(?:to|beyond)|"
    r"implications?\s+for\s+(?:the\s+)?(?:general|broader?|wider?)\s+"
    r"(?:population|public|society))\b",
    re.IGNORECASE,
)
_CONVENIENCE_CAVEAT_RE = re.compile(
    r"\b(?:generalis(?:e|ability)\s+(?:may\s+be\s+)?(?:limited?|constrained?)|"
    r"generalizability\s+(?:may\s+be\s+)?(?:limited?|constrained?)|"
    r"sample\s+may\s+not\s+(?:be\s+)?representative|"
    r"limited?\s+(?:to|by)\s+(?:the\s+)?(?:sample|convenience|homogeneous)|"
    r"replication\s+(?:with|in)\s+(?:more\s+)?(?:diverse|representative|broader?))\b",
    re.IGNORECASE,
)


def validate_convenience_sample_generalization(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag convenience samples that claim broad generalisability without caveats.

    Emits ``overclaimed-generalizability-convenience`` (moderate) when a
    convenience or student sample is used but results are generalised without
    noting sample limitations.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="convenience_sample_generalization", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="convenience_sample_generalization", findings=[]
        )

    if not _CONVENIENCE_SAMPLE_RE.search(full):
        return ValidationResult(
            validator_name="convenience_sample_generalization", findings=[]
        )

    if not _GENERALIZE_FROM_CONVENIENCE_RE.search(full):
        return ValidationResult(
            validator_name="convenience_sample_generalization", findings=[]
        )

    if _CONVENIENCE_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="convenience_sample_generalization", findings=[]
        )

    return ValidationResult(
        validator_name="convenience_sample_generalization",
        findings=[
            Finding(
                code="overclaimed-generalizability-convenience",
                severity="moderate",
                message=(
                    "Convenience or student sample used but findings are generalised "
                    "to the broader population without caveat. "
                    "Acknowledge sample limitations and constrain generalisability claims."
                ),
                validator="convenience_sample_generalization",
                location="Discussion / Limitations",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 234 – Missing intraclass correlation for reliability
# ---------------------------------------------------------------------------

_ICC_NEEDED_RE = re.compile(
    r"\b(?:raters?\s+(?:coded?|rated?|scored?|assessed?|evaluated?)|"
    r"two\s+(?:independent\s+)?(?:raters?|coders?|judges?)\s+"
    r"(?:independently\s+)?(?:coded?|rated?|scored?|assessed?|evaluated?)|"
    r"inter.?rater\s+(?:reliability|agreement|consistency)|"
    r"coded?\s+independently\s+by\s+two|"
    r"rater\s+agreement\s+was\s+(?:assessed?|examined?|calculated?|computed?))\b",
    re.IGNORECASE,
)
_ICC_REPORTED_RE = re.compile(
    r"\b(?:intraclass\s+correlation|"
    r"ICC\s*[\(=]|ICC\s+(?:was|of)\s*\d|"
    r"Krippendorff.s?\s+alpha\s*=|Cohen.s?\s+(?:weighted\s+)?kappa\s*=|"
    r"Fleiss.?\s+kappa\s*=|"
    r"percent\s+(?:agreement|overlap)\s*=\s*\d{2,3})",
    re.IGNORECASE,
)


def validate_icc_reliability_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multi-rater reliability studies without ICC or kappa values.

    Emits ``missing-icc-reliability`` (moderate) when inter-rater reliability
    is assessed but no ICC, kappa, or Krippendorff alpha is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="icc_reliability_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="icc_reliability_reporting", findings=[]
        )

    if not _ICC_NEEDED_RE.search(full):
        return ValidationResult(
            validator_name="icc_reliability_reporting", findings=[]
        )

    if _ICC_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="icc_reliability_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="icc_reliability_reporting",
        findings=[
            Finding(
                code="missing-icc-reliability",
                severity="moderate",
                message=(
                    "Inter-rater reliability assessment is mentioned but no ICC, "
                    "kappa, or Krippendorff alpha statistic is reported. "
                    "Report a quantitative reliability index."
                ),
                validator="icc_reliability_reporting",
                location="Measures / Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 235 – Missing planned contrasts or post-hoc correction for ANOVA
# ---------------------------------------------------------------------------

_ANOVA_SIGNIFICANT_RE = re.compile(
    r"\b(?:significant\s+main\s+effect\s+of|"
    r"one.?way\s+ANOVA\s+(?:revealed?|showed?|indicated?|found?)\s+(?:a\s+)?significant|"
    r"two.?way\s+ANOVA\s+(?:revealed?|showed?|indicated?|found?)\s+(?:a\s+)?significant|"
    r"ANOVA\s+(?:revealed?|showed?|indicated?|found?)\s+(?:a\s+)?significant)\b",
    re.IGNORECASE,
)
_POST_HOC_RE = re.compile(
    r"\b(?:Tukey|Bonferroni|Scheff[eé]|Sidak|Dunnett|Games.Howell|"
    r"Duncan|Newman.Keuls|LSD\b|HSD\b|post.?hoc\s+(?:test|comparison|correction)|"
    r"planned\s+contrast|pairwise\s+comparison\s+with\s+(?:Bonferroni|Tukey|correction))\b",
    re.IGNORECASE,
)


def validate_anova_post_hoc_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag significant ANOVA results without post-hoc tests or planned contrasts.

    Emits ``missing-anova-post-hoc`` (moderate) when a significant ANOVA result
    is reported for a factor with multiple levels but no follow-up tests are described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="anova_post_hoc_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="anova_post_hoc_reporting", findings=[]
        )

    if not _ANOVA_SIGNIFICANT_RE.search(full):
        return ValidationResult(
            validator_name="anova_post_hoc_reporting", findings=[]
        )

    if _POST_HOC_RE.search(full):
        return ValidationResult(
            validator_name="anova_post_hoc_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="anova_post_hoc_reporting",
        findings=[
            Finding(
                code="missing-anova-post-hoc",
                severity="moderate",
                message=(
                    "A significant ANOVA result is reported but no post-hoc tests "
                    "(Tukey, Bonferroni, Scheffé, etc.) or planned contrasts are described. "
                    "Report follow-up comparisons to identify which groups differ."
                ),
                validator="anova_post_hoc_reporting",
                location="Statistical Analysis / Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 236 – Missing adverse events reporting (clinical trials)
# ---------------------------------------------------------------------------

_CLINICAL_TRIAL_RE = re.compile(
    r"\b(?:clinical\s+trial|randomised?\s+controlled\s+trial|RCT\b|"
    r"intervention\s+(?:arm|group|condition)|control\s+(?:arm|group|condition)|"
    r"treatment\s+(?:group|arm|condition)|experimental\s+(?:group|arm|condition)|"
    r"participants?\s+were\s+randomis(?:ed|ed)\s+to)\b",
    re.IGNORECASE,
)
_ADVERSE_EVENT_RE = re.compile(
    r"\b(?:adverse\s+(?:event|effect|reaction|outcome)|side\s+effect|"
    r"safety\s+(?:outcome|endpoint|data|report(?:ing)?)|"
    r"harm(?:ful\s+(?:event|effect))?|"
    r"no\s+adverse\s+events?\s+(?:were\s+)?(?:reported?|observed?|occurred?|detected?)|"
    r"adverse\s+events?\s+(?:were|was)\s+(?:monitored?|recorded?|tracked?|collected?))\b",
    re.IGNORECASE,
)


def validate_adverse_events_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag clinical trial manuscripts without adverse events reporting.

    Emits ``missing-adverse-events-report`` (major) when an RCT or intervention
    study is detected but no adverse events or safety outcomes are reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="adverse_events_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="adverse_events_reporting", findings=[]
        )

    if not _CLINICAL_TRIAL_RE.search(full):
        return ValidationResult(
            validator_name="adverse_events_reporting", findings=[]
        )

    if _ADVERSE_EVENT_RE.search(full):
        return ValidationResult(
            validator_name="adverse_events_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="adverse_events_reporting",
        findings=[
            Finding(
                code="missing-adverse-events-report",
                severity="major",
                message=(
                    "Clinical trial or RCT detected but no adverse events or safety "
                    "outcomes are reported. Report adverse events (or explicitly state "
                    "none occurred) in compliance with CONSORT guidelines."
                ),
                validator="adverse_events_reporting",
                location="Results / Safety",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 237 – Ambiguous pronoun for measured construct
# ---------------------------------------------------------------------------

_PRONOUN_CONSTRUCT_RE = re.compile(
    r"\b(?:it\s+was\s+(?:measured?|assessed?|evaluated?|quantified?)|"
    r"they\s+were\s+(?:measured?|assessed?|evaluated?|quantified?)|"
    r"this\s+was\s+(?:measured?|assessed?|evaluated?|operationalized?)|"
    r"it\s+(?:measures?|assesses?|captures?|reflects?)\s+(?:the\s+level|"
    r"participant|subject|respondent))\b",
    re.IGNORECASE,
)
_CONSTRUCT_DEFINITION_RE = re.compile(
    r"\b(?:was\s+operationalized?|was\s+defined?\s+as|was\s+conceptualised?\s+as|"
    r"was\s+measured?\s+using|was\s+assessed?\s+(?:with|using|by\s+means\s+of)|"
    r"was\s+quantified?\s+(?:as|by|through)|"
    r"(?:measure|scale|instrument|questionnaire|index|composite)\s+of\s+\w+)\b",
    re.IGNORECASE,
)


def validate_construct_operationalization(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts using vague pronoun references for measured constructs.

    Emits ``ambiguous-construct-operationalization`` (minor) when vague
    pronouns (it, they) are used to refer to measured constructs without
    explicit operationalisation statements.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="construct_operationalization", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="construct_operationalization", findings=[]
        )

    if not _PRONOUN_CONSTRUCT_RE.search(full):
        return ValidationResult(
            validator_name="construct_operationalization", findings=[]
        )

    if _CONSTRUCT_DEFINITION_RE.search(full):
        return ValidationResult(
            validator_name="construct_operationalization", findings=[]
        )

    return ValidationResult(
        validator_name="construct_operationalization",
        findings=[
            Finding(
                code="ambiguous-construct-operationalization",
                severity="minor",
                message=(
                    "Vague pronoun reference used for a measured construct without "
                    "an explicit operationalisation statement. "
                    "Define each construct with a clear operational definition."
                ),
                validator="construct_operationalization",
                location="Methods / Measures",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 238 – Failure to report confidence interval for regression coefficient
# ---------------------------------------------------------------------------

_REGRESSION_COEFF_RE = re.compile(
    r"\b(?:regression\s+coefficient|unstandardized?\s+(?:coefficient|beta)|"
    r"standardized?\s+(?:coefficient|beta)|B\s*=\s*[-\d\.]+|"
    r"beta\s*=\s*[-\d\.]+|\bβ\s*=\s*[-\d\.]+)\b",
    re.IGNORECASE,
)
_COEFF_CI_RE = re.compile(
    r"\b(?:95\s*%\s*CI\s*(?:for\s+(?:the\s+)?(?:coefficient|beta|B))?|"
    r"confidence\s+interval\s+(?:for\s+(?:the\s+)?(?:coefficient|beta|B)|"
    r"around\s+(?:the\s+)?(?:coefficient|estimate))|"
    r"\[[-\d\.\s]+,\s*[-\d\.\s]+\]|"
    r"CI\s*[:=\[]\s*[-\d\.]+\s*(?:to|,)\s*[-\d\.]+)\b",
    re.IGNORECASE,
)


def validate_regression_coefficient_ci(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression analyses that report coefficients without confidence intervals.

    Emits ``missing-regression-coefficient-ci`` (minor) when regression
    coefficients are reported but no confidence intervals are given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="regression_coefficient_ci", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="regression_coefficient_ci", findings=[]
        )

    if not _REGRESSION_COEFF_RE.search(full):
        return ValidationResult(
            validator_name="regression_coefficient_ci", findings=[]
        )

    if _COEFF_CI_RE.search(full):
        return ValidationResult(
            validator_name="regression_coefficient_ci", findings=[]
        )

    return ValidationResult(
        validator_name="regression_coefficient_ci",
        findings=[
            Finding(
                code="missing-regression-coefficient-ci",
                severity="minor",
                message=(
                    "Regression coefficients reported without confidence intervals. "
                    "Report 95% CIs for all regression coefficients to convey precision."
                ),
                validator="regression_coefficient_ci",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 239 – Missing follow-up duration for longitudinal study
# ---------------------------------------------------------------------------

_LONGITUDINAL_FOLLOWUP_RE = re.compile(
    r"\b(?:longitudinal\s+(?:study|design|data|analysis|follow.?up)|"
    r"prospective\s+(?:study|cohort|design)|"
    r"followed?\s+(?:participants?|subjects?|patients?)\s+(?:over|for|during)|"
    r"follow.?up\s+(?:assessment|measurement|wave|data\s+collection))\b",
    re.IGNORECASE,
)
_FOLLOWUP_DURATION_RE = re.compile(
    r"\b(?:followed?\s+(?:for|over)\s+\d+\s*(?:weeks?|months?|years?)|"
    r"\d+.?(?:week|month|year).?follow.?up|"
    r"follow.?up\s+(?:period|duration|interval)\s+(?:was|of)\s+\d+\s*"
    r"(?:weeks?|months?|years?)|"
    r"at\s+(?:\d+|one|two|three|four|five|six|twelve|eighteen|twenty.four)\s*"
    r"(?:weeks?|months?|years?))\b",
    re.IGNORECASE,
)


def validate_longitudinal_followup_duration(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal studies that do not report the follow-up duration.

    Emits ``missing-followup-duration`` (moderate) when a longitudinal or
    prospective study is described but no follow-up duration is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="longitudinal_followup_duration", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="longitudinal_followup_duration", findings=[]
        )

    if not _LONGITUDINAL_FOLLOWUP_RE.search(full):
        return ValidationResult(
            validator_name="longitudinal_followup_duration", findings=[]
        )

    if _FOLLOWUP_DURATION_RE.search(full):
        return ValidationResult(
            validator_name="longitudinal_followup_duration", findings=[]
        )

    return ValidationResult(
        validator_name="longitudinal_followup_duration",
        findings=[
            Finding(
                code="missing-followup-duration",
                severity="moderate",
                message=(
                    "Longitudinal or prospective study detected but no follow-up "
                    "duration or assessment interval is specified. "
                    "Report the length of the follow-up period explicitly."
                ),
                validator="longitudinal_followup_duration",
                location="Methods / Participants",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 240 – Missing Bayes factor or credible interval for Bayesian analysis
# ---------------------------------------------------------------------------

_BAYESIAN_RE = re.compile(
    r"\b(?:Bayesian\s+(?:analysis|inference|statistics?|approach|framework|model|"
    r"regression|ANOVA|t.?test|factor\s+analysis)|"
    r"Bayes\s+(?:factor|theorem|rule)|prior\s+(?:distribution|probability|belief)|"
    r"posterior\s+(?:distribution|probability|estimate)|"
    r"MCMC\b|Markov\s+chain\s+Monte\s+Carlo|Stan\b|JAGS\b|"
    r"credible\s+interval|"
    r"we\s+used?\s+a\s+Bayesian)\b",
    re.IGNORECASE,
)
_BAYESIAN_REPORT_RE = re.compile(
    r"\b(?:Bayes\s+factor\s*(?:\(\s*BF\s*\))?\s*=|"
    r"BF\s*(?:10|01|incl|excl)?\s*=\s*\d|"
    r"credible\s+interval\s*[:=\[]\s*[-\d\.]+|"
    r"\d+\s*%\s*(?:highest\s+posterior\s+density|HPD|credible\s+interval)|"
    r"HDI\s*[:=\[]\s*[-\d\.]|"
    r"posterior\s+(?:mean|median|mode)\s*=\s*[-\d\.])\b",
    re.IGNORECASE,
)


def validate_bayesian_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayesian analyses without Bayes factor or credible interval reporting.

    Emits ``missing-bayesian-reporting`` (moderate) when Bayesian analysis is
    used but no Bayes factor, credible interval, or HDI is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="bayesian_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="bayesian_reporting", findings=[]
        )

    if not _BAYESIAN_RE.search(full):
        return ValidationResult(
            validator_name="bayesian_reporting", findings=[]
        )

    if _BAYESIAN_REPORT_RE.search(full):
        return ValidationResult(
            validator_name="bayesian_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="bayesian_reporting",
        findings=[
            Finding(
                code="missing-bayesian-reporting",
                severity="moderate",
                message=(
                    "Bayesian analysis is used but no Bayes factor, credible interval, "
                    "or HDI is reported. "
                    "Report Bayes factors (BF) and/or credible intervals for all key estimates."
                ),
                validator="bayesian_reporting",
                location="Statistical Analysis / Results",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 241 – Missing floor/ceiling effect check for parametric analysis
# ---------------------------------------------------------------------------

_FLOOR_CEILING_TRIGGER_RE = re.compile(
    r"\b(?:Likert.?(?:scale|items?|measure|data|response)|"
    r"ordinal\s+(?:scale|data|response|measure)|"
    r"rating\s+scale\s+(?:data|responses?|items?)|"
    r"ceiling\s+effect|floor\s+effect)\b",
    re.IGNORECASE,
)
_FLOOR_CEILING_CHECK_RE = re.compile(
    r"\b(?:ceiling\s+effect\s+(?:was|were|is|are)?\s*(?:examined?|tested?|checked?|"
    r"assessed?|detected?|found?|observed?|present|absent|noted?|reported?)|"
    r"floor\s+effect\s+(?:was|were|is|are)?\s*(?:examined?|tested?|checked?|"
    r"assessed?|detected?|found?|observed?|present|absent|noted?|reported?)|"
    r"distribution\s+of\s+(?:the\s+)?(?:scale|Likert|item|response)\s+"
    r"(?:scores?|data)\s+(?:was|were)\s+(?:examined?|inspected?|assessed?|checked?)|"
    r"skewness\s+and\s+kurtosis\s+(?:were|was)\s+(?:examined?|assessed?|checked?)|"
    r"(?:the\s+data\s+)?(?:did\s+not\s+show|showed?\s+no)\s+(?:significant\s+)?"
    r"(?:ceiling|floor)\s+effect)\b",
    re.IGNORECASE,
)


def validate_floor_ceiling_effect_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Likert/ordinal data used in parametric analyses without ceiling/floor checks.

    Emits ``missing-floor-ceiling-check`` (minor) when Likert or ordinal scale
    data are used but no ceiling or floor effect check is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="floor_ceiling_effect_check", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="floor_ceiling_effect_check", findings=[]
        )

    if not _FLOOR_CEILING_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="floor_ceiling_effect_check", findings=[]
        )

    if _FLOOR_CEILING_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="floor_ceiling_effect_check", findings=[]
        )

    return ValidationResult(
        validator_name="floor_ceiling_effect_check",
        findings=[
            Finding(
                code="missing-floor-ceiling-check",
                severity="minor",
                message=(
                    "Likert or ordinal scale data used but no ceiling or floor effect "
                    "check is reported. "
                    "Inspect score distributions for ceiling/floor effects before "
                    "applying parametric analyses."
                ),
                validator="floor_ceiling_effect_check",
                location="Methods / Statistical Analysis",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 242 – Hazard ratio without confidence interval (survival analysis)
# ---------------------------------------------------------------------------

_HAZARD_RATIO_RE = re.compile(
    r"\b(?:hazard\s+ratio|HR\s*=\s*\d|hazard\s+rate\s+ratio|"
    r"Cox\s+(?:proportional\s+hazards?|regression)|"
    r"survival\s+analysis\s+(?:was|were|is|are)\s+(?:conducted?|performed?|used?|run)|"
    r"time.to.event\s+analysis)\b",
    re.IGNORECASE,
)
_HAZARD_RATIO_CI_RE = re.compile(
    r"\b(?:HR\s*[\(=]\s*\d+\.?\d*\s*[,\(]\s*95\s*%\s*CI|"
    r"hazard\s+ratio\s*\(?95\s*%\s*CI|"
    r"95\s*%\s*CI\s+(?:for\s+(?:the\s+)?HR|for\s+hazard)|"
    r"\[[\d\.\s]+,\s*[\d\.\s]+\]\s*(?:for\s+(?:the\s+)?HR|hazard))\b",
    re.IGNORECASE,
)


def validate_hazard_ratio_ci(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag survival analyses reporting hazard ratios without confidence intervals.

    Emits ``missing-hazard-ratio-ci`` (moderate) when a Cox regression or
    survival analysis is used but no CI for the HR is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="hazard_ratio_ci", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="hazard_ratio_ci", findings=[]
        )

    if not _HAZARD_RATIO_RE.search(full):
        return ValidationResult(
            validator_name="hazard_ratio_ci", findings=[]
        )

    if _HAZARD_RATIO_CI_RE.search(full):
        return ValidationResult(
            validator_name="hazard_ratio_ci", findings=[]
        )

    return ValidationResult(
        validator_name="hazard_ratio_ci",
        findings=[
            Finding(
                code="missing-hazard-ratio-ci",
                severity="moderate",
                message=(
                    "Hazard ratio (HR) or Cox regression reported but no confidence "
                    "interval for the HR is given. "
                    "Report the 95% CI for each hazard ratio."
                ),
                validator="hazard_ratio_ci",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 243 – Undisclosed outlier removal impact on results
# ---------------------------------------------------------------------------

_OUTLIER_REMOVAL_RE = re.compile(
    r"\b(?:outliers?\s+(?:were\s+)?(?:removed?|excluded?|deleted?|winsorized?|"
    r"trimmed?)|"
    r"(?:removed?|excluded?|deleted?)\s+(?:\d+\s+)?outliers?|"
    r"outlier\s+(?:detection|identification|removal)\s+(?:was|were)\s+(?:performed?|"
    r"conducted?|applied?|used?)|"
    r"values?\s+(?:more\s+than|exceeding?|beyond?)\s+\d+\s*SD)\b",
    re.IGNORECASE,
)
_OUTLIER_SENSITIVITY_RE = re.compile(
    r"\b(?:analyses?\s+(?:were\s+)?(?:re.?run|repeated?|conducted?)\s+"
    r"(?:with|including?)\s+(?:the\s+)?outliers?|"
    r"results?\s+(?:were\s+)?(robust|unchanged?|similar|consistent)\s+"
    r"(?:when|with|after)\s+(?:including?|re.?including?|retaining?)\s+"
    r"(?:the\s+)?outliers?|"
    r"sensitivity\s+(?:analysis|check)\s+(?:with|including?)\s+outliers?|"
    r"excluding?\s+outliers?\s+did\s+not\s+(?:substantially\s+)?(?:change|alter|"
    r"affect)\s+(?:the\s+)?(?:results?|findings?|conclusions?))\b",
    re.IGNORECASE,
)


def validate_outlier_removal_impact(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag outlier removal without reporting sensitivity of results to removal.

    Emits ``missing-outlier-removal-impact`` (minor) when outliers are removed
    but no sensitivity check on whether removal changes the conclusions is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="outlier_removal_impact", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="outlier_removal_impact", findings=[]
        )

    if not _OUTLIER_REMOVAL_RE.search(full):
        return ValidationResult(
            validator_name="outlier_removal_impact", findings=[]
        )

    if _OUTLIER_SENSITIVITY_RE.search(full):
        return ValidationResult(
            validator_name="outlier_removal_impact", findings=[]
        )

    return ValidationResult(
        validator_name="outlier_removal_impact",
        findings=[
            Finding(
                code="missing-outlier-removal-impact",
                severity="minor",
                message=(
                    "Outliers were removed but no sensitivity check or statement on "
                    "whether removal changes the conclusions is provided. "
                    "Report whether findings are robust to including the removed cases."
                ),
                validator="outlier_removal_impact",
                location="Statistical Analysis",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 244 – Missing intraclass correlation for multilevel data
# ---------------------------------------------------------------------------

_MULTILEVEL_DATA_RE = re.compile(
    r"\b(?:multilevel|hierarchical\s+linear\s+model|HLM\b|mixed.effects?\s+model|"
    r"random.effects?\s+model|nested\s+(?:data|design|structure)|"
    r"students?\s+(?:nested|clustered)\s+(?:within|in)\s+"
    r"(?:classrooms?|schools?|teachers?)|"
    r"patients?\s+(?:nested|clustered)\s+(?:within|in)\s+"
    r"(?:hospitals?|clinics?|providers?)|"
    r"level.?\d\s+(?:units?|variables?|predictors?|outcomes?))\b",
    re.IGNORECASE,
)
_ICC_MULTILEVEL_RE = re.compile(
    r"\b(?:intraclass\s+correlation\s+(?:coefficient|ICC)|"
    r"ICC\s*[\(=]|"
    r"between.(?:cluster|group|school|class|hospital)\s+variance\s+(?:was|=)|"
    r"proportion\s+of\s+(?:variance|variability)\s+(?:attributable\s+to|explained?\s+by|"
    r"due\s+to)\s+(?:the\s+)?(?:cluster|group|school|class|level.?\d))\b",
    re.IGNORECASE,
)


def validate_multilevel_icc_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multilevel models that do not report ICCs.

    Emits ``missing-multilevel-icc`` (moderate) when hierarchical/multilevel
    data or models are used but no ICC or between-cluster variance is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="multilevel_icc_reporting", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="multilevel_icc_reporting", findings=[]
        )

    if not _MULTILEVEL_DATA_RE.search(full):
        return ValidationResult(
            validator_name="multilevel_icc_reporting", findings=[]
        )

    if _ICC_MULTILEVEL_RE.search(full):
        return ValidationResult(
            validator_name="multilevel_icc_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="multilevel_icc_reporting",
        findings=[
            Finding(
                code="missing-multilevel-icc",
                severity="moderate",
                message=(
                    "Multilevel or hierarchical model detected but no ICC or "
                    "between-cluster variance proportion is reported. "
                    "Report the ICC to justify the multilevel approach."
                ),
                validator="multilevel_icc_reporting",
                location="Statistical Analysis / Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 245 – Outdated or potentially retracted citations
# ---------------------------------------------------------------------------

_OLD_CITATION_RE = re.compile(
    r"\b(?:19[0-7]\d|198[0-5])\b",
)
_OLD_CITATION_CONTEXT_RE = re.compile(
    r"\([^)]{2,50}(?:19[0-7]\d|198[0-5])\)",
    re.IGNORECASE,
)
_FOUNDATIONAL_CAVEAT_RE = re.compile(
    r"\b(?:seminal|foundational|classic|landmark|original|pioneering|"
    r"first\s+(?:to\s+)?(?:demonstrate?|show?|establish?|identify?|report?)|"
    r"originally\s+(?:developed?|proposed?|described?|established?))\b",
    re.IGNORECASE,
)


def validate_citation_currency(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag empirical claims supported only by very old citations without justification.

    Emits ``potentially-outdated-citation`` (minor) when a citation from before
    1986 is used to support a current empirical claim without acknowledging
    it as a foundational reference.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="citation_currency", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="citation_currency", findings=[]
        )

    if not _OLD_CITATION_CONTEXT_RE.search(full):
        return ValidationResult(
            validator_name="citation_currency", findings=[]
        )

    if _FOUNDATIONAL_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="citation_currency", findings=[]
        )

    return ValidationResult(
        validator_name="citation_currency",
        findings=[
            Finding(
                code="potentially-outdated-citation",
                severity="minor",
                message=(
                    "A citation from before 1986 is used to support an empirical claim "
                    "without acknowledging it as a foundational or seminal reference. "
                    "Supplement with more recent citations or note the foundational status."
                ),
                validator="citation_currency",
                location="Introduction / Discussion",
                evidence=[],
            )
        ],
    )

# ---------------------------------------------------------------------------
# Phase 246 – Missing confidence interval for proportion/percentage
# ---------------------------------------------------------------------------

_PROPORTION_RE = re.compile(
    r"\b(?:\d{1,3}\s*%\s+of\s+(?:participants?|respondents?|patients?|"
    r"subjects?|individuals?|women|men|adults?|children)|"
    r"prevalence\s+(?:was|of)\s+\d{1,3}\s*%|"
    r"(?:incidence|proportion)\s+(?:was|of)\s+\d{1,3}\s*%|"
    r"\d{1,3}\s*%\s+(?:prevalence|incidence|rate))\b",
    re.IGNORECASE,
)
_PROPORTION_CI_RE = re.compile(
    r"(?:95\s*%\s*CI|confidence\s+interval)(?:\s|\[|,|\()",
    re.IGNORECASE,
)


def validate_proportion_confidence_interval(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag reported proportions or prevalences without confidence intervals.

    Emits ``missing-proportion-ci`` (minor) when a percentage prevalence or
    proportion is reported but no confidence interval is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="proportion_confidence_interval", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="proportion_confidence_interval", findings=[]
        )

    if not _PROPORTION_RE.search(full):
        return ValidationResult(
            validator_name="proportion_confidence_interval", findings=[]
        )

    if _PROPORTION_CI_RE.search(full):
        return ValidationResult(
            validator_name="proportion_confidence_interval", findings=[]
        )

    return ValidationResult(
        validator_name="proportion_confidence_interval",
        findings=[
            Finding(
                code="missing-proportion-ci",
                severity="minor",
                message=(
                    "A proportion or prevalence percentage is reported but no confidence "
                    "interval is given. Report 95% CIs for all key proportions."
                ),
                validator="proportion_confidence_interval",
                location="Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 247 – Missing description of blinding procedure
# ---------------------------------------------------------------------------

_BLINDING_TRIGGER_RE = re.compile(
    r"\b(?:double.?blind|single.?blind|blinded?\s+(?:assessment|outcome|rating|"
    r"evaluation|observer|assessor|rater)|"
    r"assessors?\s+(?:were\s+)?blinded?|"
    r"raters?\s+(?:were\s+)?blinded?|"
    r"(?:outcome|data)\s+assessors?\s+(?:were\s+)?blinded?)\b",
    re.IGNORECASE,
)
_BLINDING_PROCEDURE_RE = re.compile(
    r"\b(?:blinding\s+(?:was|were)\s+(?:maintained?|ensured?|achieved?|verified?)|"
    r"blinding\s+procedure|blinded?\s+(?:by\s+using|through|via|by\s+means?\s+of)|"
    r"masked?\s+(?:allocation|assignment|coding|labels?)|"
    r"identical\s+(?:appearance|packaging|labelling)\s+(?:of\s+)?(?:active|placebo)|"
    r"allocation\s+(?:was\s+)?concealed?|concealment\s+of\s+allocation)\b",
    re.IGNORECASE,
)


def validate_blinding_procedure_description(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag blinding claims without a description of how blinding was implemented.

    Emits ``missing-blinding-procedure`` (moderate) when double- or single-blind
    methodology is claimed but no blinding procedure is described.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="blinding_procedure_description", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="blinding_procedure_description", findings=[]
        )

    if not _BLINDING_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="blinding_procedure_description", findings=[]
        )

    if _BLINDING_PROCEDURE_RE.search(full):
        return ValidationResult(
            validator_name="blinding_procedure_description", findings=[]
        )

    return ValidationResult(
        validator_name="blinding_procedure_description",
        findings=[
            Finding(
                code="missing-blinding-procedure",
                severity="moderate",
                message=(
                    "Blinding is claimed but no procedure for how blinding was "
                    "implemented or verified is described. "
                    "Describe the blinding method (e.g., identical packaging, "
                    "allocation concealment, masked coding)."
                ),
                validator="blinding_procedure_description",
                location="Methods",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 248 – Undisclosed change in primary outcome
# ---------------------------------------------------------------------------

_OUTCOME_CHANGE_RE = re.compile(
    r"\b(?:primary\s+outcome\s+(?:was\s+)?(?:changed?|modified?|revised?|"
    r"switched?|updated?|altered?|redefined?)|"
    r"original(?:ly)?\s+(?:primary|secondary|planned?)\s+outcome|"
    r"(?:we|the\s+authors?)\s+(?:changed?|modified?|revised?|switched?)\s+(?:the\s+)?"
    r"(?:primary|secondary|main|planned?)\s+outcome|"
    r"outcome\s+(?:was\s+)?(?:changed?|switched?|modified?)\s+"
    r"(?:from|after|during|before)\s+(?:data\s+collection|analysis|the\s+trial))\b",
    re.IGNORECASE,
)
_OUTCOME_CHANGE_DISCLOSURE_RE = re.compile(
    r"\b(?:this\s+change\s+(?:was\s+)?(?:prespecified?|pre.?planned?|registered?|"
    r"noted?\s+in\s+the\s+(?:pre.?registration|protocol|registry))|"
    r"(?:the\s+)?(?:pre.?registration|protocol|registry)\s+"
    r"(?:specif(?:ied?|ies?)|noted?|listed?)\s+this\s+outcome\s+change|"
    r"amendment\s+to\s+the\s+(?:protocol|registry|registration)|"
    r"CONSORT\s+flow|transparency\s+(?:about|regarding)\s+outcome\s+changes?)\b",
    re.IGNORECASE,
)


def validate_primary_outcome_change_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts disclosing a primary outcome change without justification.

    Emits ``undisclosed-outcome-change`` (major) when the primary outcome is
    mentioned as changed but no protocol amendment or pre-specification is cited.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="primary_outcome_change_disclosure", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="primary_outcome_change_disclosure", findings=[]
        )

    if not _OUTCOME_CHANGE_RE.search(full):
        return ValidationResult(
            validator_name="primary_outcome_change_disclosure", findings=[]
        )

    if _OUTCOME_CHANGE_DISCLOSURE_RE.search(full):
        return ValidationResult(
            validator_name="primary_outcome_change_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="primary_outcome_change_disclosure",
        findings=[
            Finding(
                code="undisclosed-outcome-change",
                severity="major",
                message=(
                    "The primary outcome appears to have changed during the study "
                    "but no protocol amendment, pre-registration update, or explicit "
                    "justification is provided. Disclose and justify any primary outcome changes."
                ),
                validator="primary_outcome_change_disclosure",
                location="Methods / Results",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 249 – Missing discussion of non-significant results
# ---------------------------------------------------------------------------

_NULL_RESULT_RE = re.compile(
    r"\b(?:(?:was|were)\s+not\s+(?:statistically\s+)?significant|"
    r"did\s+not\s+(?:reach|achieve|attain)\s+(?:statistical\s+)?significance|"
    r"no\s+(?:significant|statistically\s+significant)\s+"
    r"(?:difference|effect|association|relationship|change|improvement)|"
    r"p\s*(?:=|>)\s*0\.\s*(?:0[6-9]|[1-9]\d))\b",
    re.IGNORECASE,
)
_NULL_RESULT_DISCUSSION_RE = re.compile(
    r"\b(?:(?:the\s+)?null\s+(?:result|finding)|(?:lack\s+of\s+)?significance\s+"
    r"(?:may\s+(?:be\s+due\s+to|reflect)|(?:could|might)\s+(?:be\s+explained?\s+by|"
    r"reflect))|"
    r"(?:the\s+)?non.?significant\s+(?:result|finding|outcome)\s+"
    r"(?:may|could|might|is|could\s+be)|"
    r"underpowered|insufficient\s+(?:power|sample\s+size)|"
    r"type\s+II\s+error|beta\s+error)\b",
    re.IGNORECASE,
)


def validate_null_result_discussion(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag manuscripts with null results that do not discuss their meaning.

    Emits ``missing-null-result-discussion`` (minor) when non-significant
    results are reported but no interpretation or explanation is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="null_result_discussion", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="null_result_discussion", findings=[]
        )

    if not _NULL_RESULT_RE.search(full):
        return ValidationResult(
            validator_name="null_result_discussion", findings=[]
        )

    if _NULL_RESULT_DISCUSSION_RE.search(full):
        return ValidationResult(
            validator_name="null_result_discussion", findings=[]
        )

    return ValidationResult(
        validator_name="null_result_discussion",
        findings=[
            Finding(
                code="missing-null-result-discussion",
                severity="minor",
                message=(
                    "Non-significant results are reported but no interpretation "
                    "or explanation of the null finding is given. "
                    "Discuss possible reasons (e.g., insufficient power, Type II error, "
                    "true null effect)."
                ),
                validator="null_result_discussion",
                location="Discussion",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 250 – Missing racial/ethnic composition description
# ---------------------------------------------------------------------------

_RACE_ETHNICITY_TRIGGER_RE = re.compile(
    r"\b(?:racial(?:ly)?|ethnic(?:ity|ally)?|race\s+and\s+ethnicity|"
    r"racial/ethnic|ethnic\s+(?:diversity|composition|background|minority|"
    r"minority\s+group)|racially\s+diverse|predominantly\s+White|"
    r"White\s+participants?|Black\s+participants?|Hispanic\s+participants?|"
    r"Asian\s+participants?)\b",
    re.IGNORECASE,
)
_RACE_ETHNICITY_REPORTED_RE = re.compile(
    r"\b(?:\d+\.?\d*\s*%\s+(?:White|Black|Hispanic|Latino|Latina|Asian|"
    r"African\s+American|Native\s+American|Pacific\s+Islander|"
    r"multiracial|biracial|other)|"
    r"racial\s+(?:and\s+ethnic\s+)?composition\s+(?:of\s+the\s+sample\s+)?was|"
    r"sample\s+(?:was|consisted?\s+of)\s+\d+\.?\d*\s*%\s+(?:White|Black|Hispanic|"
    r"Asian|African\s+American)|"
    r"participants?\s+identified?\s+as\s+(?:White|Black|Hispanic|Asian|"
    r"African\s+American|Native))\b",
    re.IGNORECASE,
)


def validate_racial_ethnic_composition(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies mentioning race/ethnicity without reporting sample composition.

    Emits ``missing-racial-ethnic-composition`` (minor) when racial or ethnic
    groups are mentioned but no demographic breakdown of the sample is given.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="racial_ethnic_composition", findings=[]
        )

    full = parsed.full_text or " ".join(s.body for s in parsed.sections)
    if not full:
        return ValidationResult(
            validator_name="racial_ethnic_composition", findings=[]
        )

    if not _RACE_ETHNICITY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="racial_ethnic_composition", findings=[]
        )

    if _RACE_ETHNICITY_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="racial_ethnic_composition", findings=[]
        )

    return ValidationResult(
        validator_name="racial_ethnic_composition",
        findings=[
            Finding(
                code="missing-racial-ethnic-composition",
                severity="minor",
                message=(
                    "Race or ethnicity is mentioned but no breakdown of the sample's "
                    "racial/ethnic composition is reported. "
                    "Describe the racial/ethnic composition of the sample."
                ),
                validator="racial_ethnic_composition",
                location="Participants",
                evidence=[],
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 251 – validate_single_item_measure_reliability
# ---------------------------------------------------------------------------

_SINGLE_ITEM_TRIGGER_RE = re.compile(
    r"\b(?:measured\s+with\s+(?:a\s+)?single[\s-]item|"
    r"single[\s-]item\s+(?:measure|scale|question|indicator)|"
    r"assessed\s+(?:using|with)\s+(?:a\s+)?single\s+(?:question|item))\b",
    re.IGNORECASE,
)
_SINGLE_ITEM_CAVEAT_RE = re.compile(
    r"\b(?:reliability\s+(?:of\s+)?(?:single[\s-]item|this\s+measure)|"
    r"limitation\s+of\s+(?:single[\s-]item|this\s+approach)|"
    r"single[\s-]item\s+measures?\s+(?:may|can|do\s+not)\b|"
    r"acknowledged?\s+(?:that\s+)?single[\s-]item|"
    r"validated\s+(?:single[\s-]item|this\s+(?:measure|scale)))\b",
    re.IGNORECASE,
)


def validate_single_item_measure_reliability(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag single-item measures used without a reliability caveat.

    Emits ``missing-single-item-reliability-caveat`` (minor) when a single-item
    measure is used without acknowledging or justifying its reliability limitations.
    """
    _vid = "validate_single_item_measure_reliability"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SINGLE_ITEM_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SINGLE_ITEM_CAVEAT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-single-item-reliability-caveat",
                severity="minor",
                message=(
                    "A single-item measure is used without discussing its reliability"
                    " limitations. Single-item measures often have lower reliability;"
                    " acknowledge this as a limitation."
                ),
                validator="validate_single_item_measure_reliability",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 252 – validate_mediator_temporality
# ---------------------------------------------------------------------------

_MEDIATION_TRIGGER_RE = re.compile(
    r"\b(?:mediat(?:ed|es|ion|or|ing)|indirect\s+effect\s+(?:of|through))\b",
    re.IGNORECASE,
)
_TEMPORAL_ORDER_RE = re.compile(
    r"\b(?:temporal\s+(?:order|precedence|sequence)|"
    r"(?:measured|assessed|collected)\s+(?:at\s+)?(?:baseline|time\s*1|wave\s*1|T1)|"
    r"(?:before|prior\s+to)\s+(?:the\s+)?(?:mediator|outcome|intervention)|"
    r"longitudinal|cross-lagged|time-lagged|prospective\s+design)\b",
    re.IGNORECASE,
)


def validate_mediator_temporality(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag mediation claims without temporal ordering evidence.

    Emits ``missing-mediator-temporality`` (moderate) when mediation is claimed
    but there is no indication that temporal ordering was established.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="validate_mediator_temporality", findings=[])

    full = parsed.full_text
    if not _MEDIATION_TRIGGER_RE.search(full):
        return ValidationResult(validator_name="validate_mediator_temporality", findings=[])

    if _TEMPORAL_ORDER_RE.search(full):
        return ValidationResult(validator_name="validate_mediator_temporality", findings=[])

    return ValidationResult(
        validator_name="validate_mediator_temporality",
        findings=[
            Finding(
                code="missing-mediator-temporality",
                severity="moderate",
                message=(
                    "Mediation is claimed but temporal ordering of variables is not discussed. "
                    "Establish that the mediator was measured before the outcome to support"
                    " causal inference."
                ),
                validator="validate_mediator_temporality",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 253 – validate_effect_size_interpretation
# ---------------------------------------------------------------------------

_ES_VALUE_RE = re.compile(
    r"\b(?:Cohen'?s?\s+[dDfg]|Hedges'?\s+g|eta[\s-]squared|partial\s+eta[\s-]squared|"
    r"omega[\s-]squared|Cramér'?s?\s+V|Glass'?\s+delta|"
    r"[dDfg]\s*=\s*[-−]?\d+\.\d+|eta\^?2\s*=\s*\d+\.\d+)\b",
    re.IGNORECASE,
)
_ES_INTERP_RE = re.compile(
    r"\b(?:small|medium|large|negligible|trivial|substantial|"
    r"practically\s+(?:significant|meaningful|important)|"
    r"clinically\s+(?:meaningful|significant|relevant)|"
    r"effect\s+(?:was|is)\s+(?:small|medium|large|negligible|trivial|substantial))\b",
    re.IGNORECASE,
)


def validate_effect_size_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag effect sizes reported without verbal interpretation.

    Emits ``missing-effect-size-interpretation`` (minor) when a standardised
    effect size is reported without describing its practical magnitude.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="validate_effect_size_interpretation", findings=[])

    full = parsed.full_text
    if not _ES_VALUE_RE.search(full):
        return ValidationResult(validator_name="validate_effect_size_interpretation", findings=[])

    if _ES_INTERP_RE.search(full):
        return ValidationResult(validator_name="validate_effect_size_interpretation", findings=[])

    return ValidationResult(
        validator_name="validate_effect_size_interpretation",
        findings=[
            Finding(
                code="missing-effect-size-interpretation",
                severity="minor",
                message=(
                    "Effect sizes are reported without verbal interpretation of practical"
                    " magnitude (e.g., small, medium, large). Contextualise effect sizes"
                    " for readers."
                ),
                validator="validate_effect_size_interpretation",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 254 – validate_comparison_group_equivalence
# ---------------------------------------------------------------------------

_GROUP_COMPARISON_TRIGGER_RE = re.compile(
    r"\b(?:compar(?:ed|ing)\s+(?:groups?|conditions?|arms?)|"
    r"between[\s-]group|group\s+differences?|"
    r"(?:treatment|control|experimental)\s+(?:vs\.?|versus|and)\s+(?:control|placebo|comparison))\b",
    re.IGNORECASE,
)
_BASELINE_EQUIVALENCE_RE = re.compile(
    r"\b(?:baseline\s+(?:characteristics?|equivalence|balance|differences?|comparison)|"
    r"groups?\s+(?:were|did\s+not\s+differ|were\s+comparable|were\s+equivalent)\s+(?:at\s+)?baseline|"
    r"no\s+significant\s+(?:baseline|pre-test|pretest)\s+differences?|"
    r"Table\s+\d+\s+(?:shows?|presents?|displays?)\s+baseline|"
    r"chi[\s-]square\s+test\s+for\s+(?:group\s+)?equivalence|"
    r"randomis(?:ation|ation|ed)\s+successfully\s+balanced)\b",
    re.IGNORECASE,
)


def validate_comparison_group_equivalence(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag group comparisons without baseline equivalence checks.

    Emits ``missing-baseline-equivalence-check`` (moderate) when groups are
    compared without verifying or reporting baseline equivalence.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="validate_comparison_group_equivalence", findings=[])

    full = parsed.full_text
    if not _GROUP_COMPARISON_TRIGGER_RE.search(full):
        return ValidationResult(validator_name="validate_comparison_group_equivalence", findings=[])

    if _BASELINE_EQUIVALENCE_RE.search(full):
        return ValidationResult(validator_name="validate_comparison_group_equivalence", findings=[])

    return ValidationResult(
        validator_name="validate_comparison_group_equivalence",
        findings=[
            Finding(
                code="missing-baseline-equivalence-check",
                severity="moderate",
                message=(
                    "Groups are compared but baseline equivalence is not reported or checked."
                    " Report baseline characteristics or equivalence tests to support"
                    " valid comparisons."
                ),
                validator="validate_comparison_group_equivalence",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 255 – validate_implicit_theory_test
# ---------------------------------------------------------------------------

_THEORY_TEST_TRIGGER_RE = re.compile(
    r"\b(?:(?:tests?|testing|tested|examines?|examining|examined)\s+(?:the\s+)?theory|"
    r"theory\s+(?:predicts?|suggests?|posits?|proposes?)\s+(?:that\s+)?(?:\w+\s+){1,4}"
    r"(?:would|will|should|is\s+expected))\b",
    re.IGNORECASE,
)
_CAUSAL_DESIGN_RE = re.compile(
    r"\b(?:experiment(?:al|ally)?|randomis(?:ed|ation)|manipulat(?:ed|ion)|"
    r"quasi[\s-]experiment(?:al|ally)?|longitudinal\s+test|cross[\s-]lagged\s+panel|"
    r"structural\s+equation\s+model(?:ling|ing)|instrumental\s+variable)\b",
    re.IGNORECASE,
)
_CORRELATIONAL_DESIGN_RE = re.compile(
    r"\b(?:cross[\s-]sectional|correlational\s+study|correlation\s+between|"
    r"Pearson'?s?\s+r|Spearman'?s?\s+rho|regression\s+analysis|"
    r"survey\s+(?:study|design|data))\b",
    re.IGNORECASE,
)


def validate_implicit_theory_test(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag theoretical predictions tested with correlational data only.

    Emits ``implicit-theory-test-correlational`` (minor) when a manuscript
    claims to test theory using correlational or survey-based designs only,
    without causal or longitudinal methods.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name="validate_implicit_theory_test", findings=[])

    full = parsed.full_text
    if not _THEORY_TEST_TRIGGER_RE.search(full):
        return ValidationResult(validator_name="validate_implicit_theory_test", findings=[])

    if _CAUSAL_DESIGN_RE.search(full):
        return ValidationResult(validator_name="validate_implicit_theory_test", findings=[])

    if not _CORRELATIONAL_DESIGN_RE.search(full):
        return ValidationResult(validator_name="validate_implicit_theory_test", findings=[])

    return ValidationResult(
        validator_name="validate_implicit_theory_test",
        findings=[
            Finding(
                code="implicit-theory-test-correlational",
                severity="minor",
                message=(
                    "Theoretical predictions are tested using correlational data. "
                    "Correlational designs cannot confirm causal theoretical predictions; "
                    "acknowledge this limitation."
                ),
                validator="validate_implicit_theory_test",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 257 – validate_non_normal_distribution_test
# ---------------------------------------------------------------------------

_PARAMETRIC_TEST_RE = re.compile(
    r"\b(?:t[\s-]test|independent[\s-]samples?\s+t[\s-]?test|paired[\s-]?t[\s-]?test|"
    r"one[\s-]way\s+ANOVA|two[\s-]way\s+ANOVA|ANCOVA|MANOVA|"
    r"Pearson'?s?\s+(?:r|correlation))\b",
    re.IGNORECASE,
)
_NORMALITY_CHECK_RE = re.compile(
    r"\b(?:Shapiro[\s-]Wilk|Kolmogorov[\s-]Smirnov|Anderson[\s-]Darling|"
    r"normal(?:ity)?\s+(?:test|assumption|check)|"
    r"data\s+(?:were|was|are)\s+(?:normally|approximately\s+normally)\s+distributed|"
    r"Q[\s-]?Q\s+plot|non[\s-]?parametric\s+alternative|Mann[\s-]Whitney|"
    r"Wilcoxon|Kruskal[\s-]Wallis|violated\s+normality|normality\s+violated)\b",
    re.IGNORECASE,
)


def validate_non_normal_distribution_test(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag parametric tests without normality checks.

    Emits ``missing-normality-check`` (minor) when parametric tests are used
    without any normality testing or reporting.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_non_normal_distribution_test", findings=[]
        )

    full = parsed.full_text
    if not _PARAMETRIC_TEST_RE.search(full):
        return ValidationResult(
            validator_name="validate_non_normal_distribution_test", findings=[]
        )

    if _NORMALITY_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="validate_non_normal_distribution_test", findings=[]
        )

    return ValidationResult(
        validator_name="validate_non_normal_distribution_test",
        findings=[
            Finding(
                code="missing-normality-check",
                severity="minor",
                message=(
                    "Parametric tests are reported without any normality check or "
                    "justification. Verify or test distributional assumptions."
                ),
                validator="validate_non_normal_distribution_test",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 258 – validate_regression_sample_size_adequacy
# ---------------------------------------------------------------------------

_REGRESSION_TRIGGER_RE = re.compile(
    r"\b(?:logistic\s+regression|linear\s+regression|multiple\s+regression|"
    r"hierarchical\s+regression|regression\s+model|predictor\s+variable)\b",
    re.IGNORECASE,
)
_REGRESSION_SAMPLE_RE = re.compile(
    r"\b(?:rule\s+of\s+(?:\d+|thumb)|10\s+(?:cases?|participants?)\s+per\s+(?:predictor|variable)|"
    r"events?\s+per\s+variable|EPV|adequate\s+sample|sample\s+size\s+(?:was|is|met)|"
    r"power\s+analysis\s+(?:for|to\s+determine)|minimum\s+(?:sample\s+size|n\s+=))\b",
    re.IGNORECASE,
)


def validate_regression_sample_size_adequacy(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression analyses without sample size adequacy discussion.

    Emits ``missing-regression-sample-adequacy`` (minor) when regression is
    conducted without addressing predictor-to-sample ratio or power.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_regression_sample_size_adequacy", findings=[]
        )

    full = parsed.full_text
    if not _REGRESSION_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_regression_sample_size_adequacy", findings=[]
        )

    if _REGRESSION_SAMPLE_RE.search(full):
        return ValidationResult(
            validator_name="validate_regression_sample_size_adequacy", findings=[]
        )

    return ValidationResult(
        validator_name="validate_regression_sample_size_adequacy",
        findings=[
            Finding(
                code="missing-regression-sample-adequacy",
                severity="minor",
                message=(
                    "Regression analysis is conducted without discussing sample size "
                    "adequacy relative to the number of predictors. Address "
                    "predictor-to-sample ratio or power requirements."
                ),
                validator="validate_regression_sample_size_adequacy",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 259 – validate_scale_directionality_disclosure
# ---------------------------------------------------------------------------

_SCALE_USED_RE = re.compile(
    r"\b(?:Likert\s+scale|rating\s+scale|questionnaire\s+items?|"
    r"scored?\s+(?:from|on\s+a)\s+\d\s+to\s+\d|"
    r"\d[\s-]point\s+(?:Likert|rating|response)\s+scale)\b",
    re.IGNORECASE,
)
_DIRECTIONALITY_RE = re.compile(
    r"\b(?:higher\s+scores?\s+(?:indicate|reflect|represent|correspond)|"
    r"lower\s+scores?\s+(?:indicate|reflect|represent|correspond)|"
    r"reverse[\s-]scored?|reverse[\s-]coded?|item\s+directionality|"
    r"scoring\s+(?:direction|key)|recoded\s+so\s+that|"
    r"(?:minimum|maximum)\s+score\s+(?:indicates?|reflects?))\b",
    re.IGNORECASE,
)


def validate_scale_directionality_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag scale use without directionality disclosure.

    Emits ``missing-scale-directionality`` (minor) when Likert or rating scales
    are used without clarifying score direction.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_scale_directionality_disclosure", findings=[]
        )

    full = parsed.full_text
    if not _SCALE_USED_RE.search(full):
        return ValidationResult(
            validator_name="validate_scale_directionality_disclosure", findings=[]
        )

    if _DIRECTIONALITY_RE.search(full):
        return ValidationResult(
            validator_name="validate_scale_directionality_disclosure", findings=[]
        )

    return ValidationResult(
        validator_name="validate_scale_directionality_disclosure",
        findings=[
            Finding(
                code="missing-scale-directionality",
                severity="minor",
                message=(
                    "A rating or Likert scale is used without clarifying score "
                    "direction (e.g., higher = more of X). Disclose scale "
                    "directionality to aid interpretation."
                ),
                validator="validate_scale_directionality_disclosure",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 260 – validate_attrition_rate_reporting
# ---------------------------------------------------------------------------

_ATTRITION_TRIGGER_RE = re.compile(
    r"\b(?:drop(?:p(?:ed|ing))?\s*out|dropout|attrition|lost\s+to\s+follow[\s-]?up|"
    r"withdrew\s+from\s+(?:the\s+)?study|did\s+not\s+complete|"
    r"retention\s+rate|follow[\s-]up\s+(?:rate|completion))\b",
    re.IGNORECASE,
)
_ATTRITION_RATE_RE = re.compile(
    r"\b(?:\d+\s*(?:%|percent)\s*(?:drop[\s-]?out|attrition|lost\s+to\s+follow[\s-]?up)|"
    r"attrition\s+(?:rate|was|of)\s*(?:\d+|[A-Z])|"
    r"\d+\s+(?:participants?|patients?|subjects?)\s+(?:withdrew|drop(?:p(?:ed|ing))?\s*out|"
    r"were\s+lost)|retention\s+(?:rate\s+)?(?:was|of)\s*\d+)\b",
    re.IGNORECASE,
)


def validate_attrition_rate_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag mentions of dropout/attrition without quantified rates.

    Emits ``missing-attrition-rate`` (minor) when attrition or dropout is
    mentioned but not quantified.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_attrition_rate_reporting", findings=[]
        )

    full = parsed.full_text
    if not _ATTRITION_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_attrition_rate_reporting", findings=[]
        )

    if _ATTRITION_RATE_RE.search(full):
        return ValidationResult(
            validator_name="validate_attrition_rate_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="validate_attrition_rate_reporting",
        findings=[
            Finding(
                code="missing-attrition-rate",
                severity="minor",
                message=(
                    "Attrition or dropout is mentioned but not quantified. "
                    "Report attrition rates and reasons for dropout."
                ),
                validator="validate_attrition_rate_reporting",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 261 – validate_dichotomization_of_continuous_variable
# ---------------------------------------------------------------------------

_DICHOTOMIZE_TRIGGER_RE = re.compile(
    r"\b(?:dichotomis(?:ed|ing|ation)|median\s+split|mean\s+split|"
    r"split\s+(?:at|by)\s+(?:the\s+)?(?:median|mean)|"
    r"categoris(?:ed|ing)\s+(?:a\s+)?continuous\s+variable|"
    r"binarised?|cut[\s-]?point\s+of\s+\d)\b",
    re.IGNORECASE,
)
_DICHOTOMIZE_JUSTIFICATION_RE = re.compile(
    r"\b(?:clinical\s+cut[\s-]?(?:off|point)|validated\s+(?:cut[\s-]?(?:off|point)|threshold)|"
    r"established\s+(?:cut[\s-]?(?:off|point)|threshold|criterion)|"
    r"justified?\s+(?:by|because|as)|"
    r"ROC\s+analysis|optimal\s+cut[\s-]?(?:off|point))\b",
    re.IGNORECASE,
)


def validate_dichotomization_of_continuous_variable(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag arbitrary dichotomisation of continuous variables.

    Emits ``unjustified-dichotomization`` (moderate) when continuous variables
    are dichotomised (e.g., median split) without a clinically or empirically
    justified cut-off.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_dichotomization_of_continuous_variable",
            findings=[],
        )

    full = parsed.full_text
    if not _DICHOTOMIZE_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_dichotomization_of_continuous_variable",
            findings=[],
        )

    if _DICHOTOMIZE_JUSTIFICATION_RE.search(full):
        return ValidationResult(
            validator_name="validate_dichotomization_of_continuous_variable",
            findings=[],
        )

    return ValidationResult(
        validator_name="validate_dichotomization_of_continuous_variable",
        findings=[
            Finding(
                code="unjustified-dichotomization",
                severity="moderate",
                message=(
                    "A continuous variable is dichotomised without a justified cut-off. "
                    "Median or mean splits reduce statistical power and introduce bias. "
                    "Justify any cut-point or retain the continuous form."
                ),
                validator="validate_dichotomization_of_continuous_variable",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 262 – validate_ecological_fallacy_warning
# ---------------------------------------------------------------------------

_ECOLOGICAL_TRIGGER_RE = re.compile(
    r"\b(?:aggregate\s+(?:data|level|measures?)|group[\s-]level\s+(?:analysis|data)|"
    r"country[\s-]level|regional[\s-]level|community[\s-]level\s+(?:data|analysis)|"
    r"ecological\s+(?:study|analysis|correlation))\b",
    re.IGNORECASE,
)
_ECOLOGICAL_CAVEAT_RE = re.compile(
    r"\b(?:ecological\s+fallacy|ecological\s+(?:bias|correlation)|"
    r"cannot\s+(?:infer|make)\s+individual[\s-]level|"
    r"aggregate\s+data\s+(?:cannot|should\s+not|do\s+not)\s+(?:imply|support|allow)|"
    r"individual[\s-]level\s+(?:data|inference|conclusions?)\s+(?:cannot|should\s+not)|"
    r"limitation\s+of\s+(?:aggregate|ecological|group[\s-]level))\b",
    re.IGNORECASE,
)


def validate_ecological_fallacy_warning(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag aggregate-level analyses without ecological fallacy caveat.

    Emits ``missing-ecological-fallacy-warning`` (moderate) when ecological or
    aggregate-level data are used without a caveat against individual-level inference.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_ecological_fallacy_warning", findings=[]
        )

    full = parsed.full_text
    if not _ECOLOGICAL_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_ecological_fallacy_warning", findings=[]
        )

    if _ECOLOGICAL_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="validate_ecological_fallacy_warning", findings=[]
        )

    return ValidationResult(
        validator_name="validate_ecological_fallacy_warning",
        findings=[
            Finding(
                code="missing-ecological-fallacy-warning",
                severity="moderate",
                message=(
                    "Aggregate or ecological data are used without acknowledging the "
                    "ecological fallacy risk. Individual-level conclusions cannot be "
                    "drawn from group-level data."
                ),
                validator="validate_ecological_fallacy_warning",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 263 – validate_standardised_mean_difference_units
# ---------------------------------------------------------------------------

_SMD_TRIGGER_RE = re.compile(
    r"\b(?:standardised?\s+mean\s+difference|SMD\s*=|"
    r"mean\s+difference\s*=\s*[-−]?\d+\.\d+)",
    re.IGNORECASE,
)
_SMD_UNIT_RE = re.compile(
    r"\b(?:original\s+(?:units?|scale)|raw\s+(?:units?|scale|difference)|"
    r"(?:units?\s+of|on\s+the)\s+(?:the\s+)?original\s+scale|"
    r"unstandardised?\s+(?:mean\s+)?difference|"
    r"interpreted\s+in\s+(?:the\s+)?original|"
    r"back[\s-]?transformed|clinically\s+meaningful\s+difference)\b",
    re.IGNORECASE,
)


def validate_standardised_mean_difference_units(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag standardised mean differences without original-unit interpretation.

    Emits ``missing-smd-original-unit-context`` (minor) when an SMD is reported
    without contextualising it in original measurement units.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_standardised_mean_difference_units", findings=[]
        )

    full = parsed.full_text
    if not _SMD_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_standardised_mean_difference_units", findings=[]
        )

    if _SMD_UNIT_RE.search(full):
        return ValidationResult(
            validator_name="validate_standardised_mean_difference_units", findings=[]
        )

    return ValidationResult(
        validator_name="validate_standardised_mean_difference_units",
        findings=[
            Finding(
                code="missing-smd-original-unit-context",
                severity="minor",
                message=(
                    "A standardised mean difference is reported without contextualising "
                    "the effect in original measurement units. Provide an unstandardised "
                    "difference or equivalent interpretation for clinical relevance."
                ),
                validator="validate_standardised_mean_difference_units",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 264 – validate_retrospective_data_collection_disclosure
# ---------------------------------------------------------------------------

_RETRO_TRIGGER_RE = re.compile(
    r"\b(?:retrospective(?:ly)?|chart\s+review|medical\s+records?|"
    r"administrative\s+data|existing\s+(?:records?|data(?:base)?|dataset)|"
    r"data\s+(?:were|was)\s+(?:collected|extracted)\s+(?:from|using)\s+"
    r"(?:existing|archival|historical))\b",
    re.IGNORECASE,
)
_RETRO_DISCLOSURE_RE = re.compile(
    r"\b(?:retrospective\s+(?:design|study|analysis|nature)|"
    r"limitation\s+of\s+retrospective|retrospective\s+(?:collection|data)|"
    r"(?:bias|limitation)\s+(?:inherent|associated)\s+(?:to|with)\s+retrospective|"
    r"we\s+acknowledge\s+(?:the\s+)?retrospective)\b",
    re.IGNORECASE,
)


def validate_retrospective_data_collection_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag retrospective data use without explicit disclosure.

    Emits ``missing-retrospective-design-disclosure`` (minor) when retrospective
    or archival data are used without disclosing this as a design limitation.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_retrospective_data_collection_disclosure",
            findings=[],
        )

    full = parsed.full_text
    if not _RETRO_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_retrospective_data_collection_disclosure",
            findings=[],
        )

    if _RETRO_DISCLOSURE_RE.search(full):
        return ValidationResult(
            validator_name="validate_retrospective_data_collection_disclosure",
            findings=[],
        )

    return ValidationResult(
        validator_name="validate_retrospective_data_collection_disclosure",
        findings=[
            Finding(
                code="missing-retrospective-design-disclosure",
                severity="minor",
                message=(
                    "Retrospective or archival data are used without explicitly disclosing "
                    "the retrospective design as a limitation. Acknowledge limitations of "
                    "retrospective data collection."
                ),
                validator="validate_retrospective_data_collection_disclosure",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 265 – validate_treatment_fidelity_reporting
# ---------------------------------------------------------------------------

_FIDELITY_TRIGGER_RE = re.compile(
    r"\b(?:intervention|treatment\s+(?:group|condition|arm)|"
    r"(?:CBT|cognitive[\s-]behavioral\s+therapy|psychotherapy|"
    r"mindfulness[\s-]based|training\s+program|educational\s+intervention))\b",
    re.IGNORECASE,
)
_FIDELITY_REPORTED_RE = re.compile(
    r"\b(?:treatment\s+fidelity|intervention\s+fidelity|fidelity\s+(?:check|monitoring|assessment)|"
    r"protocol\s+adherence|therapist\s+adherence|adherence\s+to\s+(?:the\s+)?protocol|"
    r"treatment\s+integrity|implementation\s+fidelity|"
    r"fidelity\s+(?:was|were)\s+(?:assessed|monitored|checked|ensured))\b",
    re.IGNORECASE,
)


def validate_treatment_fidelity_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag intervention studies without treatment fidelity reporting.

    Emits ``missing-treatment-fidelity-report`` (moderate) when an intervention
    is described but treatment fidelity or protocol adherence is not reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_treatment_fidelity_reporting", findings=[]
        )

    full = parsed.full_text
    if not _FIDELITY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_treatment_fidelity_reporting", findings=[]
        )

    if _FIDELITY_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="validate_treatment_fidelity_reporting", findings=[]
        )

    return ValidationResult(
        validator_name="validate_treatment_fidelity_reporting",
        findings=[
            Finding(
                code="missing-treatment-fidelity-report",
                severity="moderate",
                message=(
                    "An intervention is described but treatment fidelity or protocol "
                    "adherence is not reported. Report fidelity checks to support "
                    "internal validity."
                ),
                validator="validate_treatment_fidelity_reporting",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 266 – validate_factorial_design_interaction_test
# ---------------------------------------------------------------------------

_FACTORIAL_TRIGGER_RE = re.compile(
    r"\b(?:factorial\s+design|\d\s*[×x]\s*\d\s+(?:factorial|ANOVA|design)|"
    r"two[\s-]way\s+ANOVA|three[\s-]way\s+ANOVA|"
    r"between[\s-]subjects\s+factor|within[\s-]subjects\s+factor)\b",
    re.IGNORECASE,
)
_FACTORIAL_INTERACTION_RE = re.compile(
    r"\b(?:interaction\s+(?:effect|term|test|was\s+(?:significant|not\s+significant))|"
    r"interaction\s+F[\s(]|"
    r"main\s+effect\s+(?:of|for)\s+\w+\s+was\s+qualified\s+by|"
    r"no\s+significant\s+interaction)\b",
    re.IGNORECASE,
)


def validate_factorial_design_interaction_test(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag factorial designs without interaction effect testing.

    Emits ``missing-factorial-interaction-test`` (moderate) when a factorial
    ANOVA design is described but interaction effects are not reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_factorial_design_interaction_test", findings=[]
        )

    full = parsed.full_text
    if not _FACTORIAL_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_factorial_design_interaction_test", findings=[]
        )

    if _FACTORIAL_INTERACTION_RE.search(full):
        return ValidationResult(
            validator_name="validate_factorial_design_interaction_test", findings=[]
        )

    return ValidationResult(
        validator_name="validate_factorial_design_interaction_test",
        findings=[
            Finding(
                code="missing-factorial-interaction-test",
                severity="moderate",
                message=(
                    "A factorial design is described but interaction effects are not "
                    "reported or tested. Report interaction effects before interpreting "
                    "main effects in factorial designs."
                ),
                validator="validate_factorial_design_interaction_test",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 267 – validate_regression_multicollinearity_check
# ---------------------------------------------------------------------------

_REGRESSION_MULTI_TRIGGER_RE = re.compile(
    r"\b(?:multiple\s+regression|hierarchical\s+regression|logistic\s+regression|"
    r"simultaneous\s+regression|predictor\s+variable(?:s)?)\b",
    re.IGNORECASE,
)
_MULTICOL_CHECK_RE = re.compile(
    r"\b(?:multicollinearity|variance\s+inflation\s+factor|VIF\s*[<=≤]?\s*\d|"
    r"tolerance\s*[>=≥]?\s*0\.\d|VIF\s+(?:values?|was|were)|"
    r"condition\s+index|collinearity\s+(?:statistics?|diagnostics?))\b",
    re.IGNORECASE,
)


def validate_regression_multicollinearity_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multiple regression without multicollinearity checks.

    Emits ``missing-multicollinearity-check`` (minor) when multiple regression
    is used without reporting multicollinearity diagnostics.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_regression_multicollinearity_check", findings=[]
        )

    full = parsed.full_text
    if not _REGRESSION_MULTI_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_regression_multicollinearity_check", findings=[]
        )

    if _MULTICOL_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="validate_regression_multicollinearity_check", findings=[]
        )

    return ValidationResult(
        validator_name="validate_regression_multicollinearity_check",
        findings=[
            Finding(
                code="missing-multicollinearity-check",
                severity="minor",
                message=(
                    "Multiple regression is conducted without reporting multicollinearity "
                    "diagnostics (e.g., VIF or tolerance). Check for multicollinearity "
                    "among predictors."
                ),
                validator="validate_regression_multicollinearity_check",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 268 – validate_intention_to_treat_analysis
# ---------------------------------------------------------------------------

_ITT_TRIGGER_RE = re.compile(
    r"\b(?:randomised?\s+(?:controlled\s+)?trial|RCT|"
    r"randomly\s+assigned|random\s+assignment)\b",
    re.IGNORECASE,
)
_ITT_ANALYSIS_RE = re.compile(
    r"\b(?:intention[\s-]to[\s-]treat|intent[\s-]to[\s-]treat|ITT\s+(?:analysis|population)|"
    r"modified\s+ITT|mITT|per[\s-]protocol\s+analysis|"
    r"all\s+randomised\s+participants|analysed\s+as\s+randomised)\b",
    re.IGNORECASE,
)


def validate_intention_to_treat_analysis(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RCTs without intention-to-treat analysis reporting.

    Emits ``missing-itt-analysis`` (major) when an RCT is described but no
    mention of ITT or per-protocol analysis strategy is made.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_intention_to_treat_analysis", findings=[]
        )

    full = parsed.full_text
    if not _ITT_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_intention_to_treat_analysis", findings=[]
        )

    if _ITT_ANALYSIS_RE.search(full):
        return ValidationResult(
            validator_name="validate_intention_to_treat_analysis", findings=[]
        )

    return ValidationResult(
        validator_name="validate_intention_to_treat_analysis",
        findings=[
            Finding(
                code="missing-itt-analysis",
                severity="major",
                message=(
                    "An RCT is described but intention-to-treat (ITT) or per-protocol "
                    "analysis strategy is not reported. Specify the analysis population "
                    "to ensure transparency about participant exclusions."
                ),
                validator="validate_intention_to_treat_analysis",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 269 – validate_confidence_interval_direction_interpretation
# ---------------------------------------------------------------------------

_CI_REPORTED_RE = re.compile(
    r"\b(?:95\s*%\s*CI|confidence\s+interval|CI\s*[\[\(]\s*[-−]?\d+)",
    re.IGNORECASE,
)
_CI_DIRECTION_RE = re.compile(
    r"\b(?:(?:lower|upper)\s+(?:bound|limit|end)\s+of\s+(?:the\s+)?(?:CI|confidence\s+interval)|"
    r"CI\s+(?:spans?|crosses?|includes?|excludes?)\s+(?:zero|null|one)|"
    r"(?:confidence\s+interval|CI)\s+(?:above|below|containing)\s+(?:zero|null)|"
    r"both\s+bounds?.{0,20}(?:are|were|remain)\s+(?:positive|negative|above|below))\b",
    re.IGNORECASE,
)


def validate_confidence_interval_direction_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag confidence intervals reported without directional interpretation.

    Emits ``missing-ci-direction-interpretation`` (minor) when CIs are reported
    but their direction or null-crossing status is not discussed.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_confidence_interval_direction_interpretation",
            findings=[],
        )

    full = parsed.full_text
    if not _CI_REPORTED_RE.search(full):
        return ValidationResult(
            validator_name="validate_confidence_interval_direction_interpretation",
            findings=[],
        )

    if _CI_DIRECTION_RE.search(full):
        return ValidationResult(
            validator_name="validate_confidence_interval_direction_interpretation",
            findings=[],
        )

    return ValidationResult(
        validator_name="validate_confidence_interval_direction_interpretation",
        findings=[
            Finding(
                code="missing-ci-direction-interpretation",
                severity="minor",
                message=(
                    "Confidence intervals are reported without interpreting their "
                    "direction or null-crossing status. Discuss whether CI bounds "
                    "are consistent with the null hypothesis."
                ),
                validator="validate_confidence_interval_direction_interpretation",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 270 – validate_longitudinal_missing_data_method
# ---------------------------------------------------------------------------

_LONG_MISSING_TRIGGER_RE = re.compile(
    r"\b(?:longitudinal|follow[\s-]?up\s+(?:wave|assessment|measurement)|"
    r"repeated[\s-]measures?|panel\s+data|time\s+point)\b",
    re.IGNORECASE,
)
_MISSING_METHOD_RE = re.compile(
    r"\b(?:multiple\s+imputation|full\s+information\s+maximum\s+likelihood|FIML|"
    r"missing\s+at\s+random|MAR|listwise\s+deletion|pairwise\s+deletion|"
    r"last\s+observation\s+carried\s+forward|LOCF|"
    r"missing\s+data\s+(?:were|was)\s+(?:handled|addressed|imputed|analysed)|"
    r"imputation\s+(?:method|procedure|approach))\b",
    re.IGNORECASE,
)


def validate_longitudinal_missing_data_method(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal studies without missing data method specification.

    Emits ``missing-longitudinal-missing-data-method`` (moderate) when a
    longitudinal study is described but no missing data handling method is named.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_longitudinal_missing_data_method", findings=[]
        )

    full = parsed.full_text
    if not _LONG_MISSING_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_longitudinal_missing_data_method", findings=[]
        )

    if _MISSING_METHOD_RE.search(full):
        return ValidationResult(
            validator_name="validate_longitudinal_missing_data_method", findings=[]
        )

    return ValidationResult(
        validator_name="validate_longitudinal_missing_data_method",
        findings=[
            Finding(
                code="missing-longitudinal-missing-data-method",
                severity="moderate",
                message=(
                    "A longitudinal study is described but no missing data handling "
                    "method is specified. Report the approach used (e.g., FIML, "
                    "multiple imputation, listwise deletion)."
                ),
                validator="validate_longitudinal_missing_data_method",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 271 – validate_cluster_sampling_correction
# ---------------------------------------------------------------------------

_CLUSTER_SAMPLE_TRIGGER_RE = re.compile(
    r"\b(?:cluster(?:ed)?\s+(?:sampling|sample|design|randomis(?:ation|ed))|"
    r"schools?\s+(?:were|as)\s+(?:the\s+)?(?:unit|cluster)|"
    r"nested\s+(?:within|data|design)|multilevel\s+(?:sampling|design)|"
    r"hierarchical\s+(?:sampling|data\s+structure)|"
    r"stratified\s+cluster\s+sample)\b",
    re.IGNORECASE,
)
_CLUSTER_CORRECTION_RE = re.compile(
    r"\b(?:clustered\s+standard\s+errors?|"
    r"cluster[\s-]robust\s+(?:standard\s+errors?|variance)|"
    r"multilevel\s+model(?:ling|ing)?|mixed[\s-]effects?\s+model|"
    r"generalised?\s+estimating\s+equations?|GEE|"
    r"design\s+effect|DEFF|intraclass\s+correlation|ICC)\b",
    re.IGNORECASE,
)


def validate_cluster_sampling_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag clustered samples without design-corrected analysis.

    Emits ``missing-cluster-sampling-correction`` (moderate) when a clustered
    or nested sampling design is described without clustered SEs or multilevel
    modelling.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_cluster_sampling_correction", findings=[]
        )

    full = parsed.full_text
    if not _CLUSTER_SAMPLE_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_cluster_sampling_correction", findings=[]
        )

    if _CLUSTER_CORRECTION_RE.search(full):
        return ValidationResult(
            validator_name="validate_cluster_sampling_correction", findings=[]
        )

    return ValidationResult(
        validator_name="validate_cluster_sampling_correction",
        findings=[
            Finding(
                code="missing-cluster-sampling-correction",
                severity="moderate",
                message=(
                    "A clustered or nested sampling design is used but no cluster-corrected "
                    "analysis (clustered SEs, multilevel model, or GEE) is reported. "
                    "Account for non-independence due to clustering."
                ),
                validator="validate_cluster_sampling_correction",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 272 – validate_non_experimental_confound_discussion
# ---------------------------------------------------------------------------

_NON_EXPERIMENTAL_TRIGGER_RE = re.compile(
    r"\b(?:cross[\s-]sectional|correlational\s+(?:study|design|analysis)|"
    r"observational\s+(?:study|design)|survey\s+(?:study|data)|"
    r"naturally\s+occurring\s+variation)\b",
    re.IGNORECASE,
)
_CONFOUND_DISCUSSION_RE = re.compile(
    r"\b(?:confound(?:ers?|ing)?|third\s+variable|spurious|"
    r"alternative\s+explanation|unmeasured\s+variable|"
    r"reverse\s+causation|reverse\s+causality|"
    r"cannot\s+(?:rule\s+out|establish\s+causation|determine\s+directionality)|"
    r"limitation\s+of\s+(?:the\s+)?(?:cross[\s-]sectional|correlational|observational))\b",
    re.IGNORECASE,
)


def validate_non_experimental_confound_discussion(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag non-experimental studies without confound discussion.

    Emits ``missing-confound-discussion`` (minor) when an observational or
    correlational design is used without any discussion of potential confounders.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_non_experimental_confound_discussion", findings=[]
        )

    full = parsed.full_text
    if not _NON_EXPERIMENTAL_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_non_experimental_confound_discussion", findings=[]
        )

    if _CONFOUND_DISCUSSION_RE.search(full):
        return ValidationResult(
            validator_name="validate_non_experimental_confound_discussion", findings=[]
        )

    return ValidationResult(
        validator_name="validate_non_experimental_confound_discussion",
        findings=[
            Finding(
                code="missing-confound-discussion",
                severity="minor",
                message=(
                    "An observational or correlational design is used without discussing "
                    "potential confounders or alternative explanations. Acknowledge "
                    "confounding as a limitation."
                ),
                validator="validate_non_experimental_confound_discussion",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 273 – validate_complete_case_analysis_bias
# ---------------------------------------------------------------------------

_COMPLETE_CASE_TRIGGER_RE = re.compile(
    r"\b(?:complete[\s-]case\s+analysis|available\s+case\s+analysis|"
    r"listwise\s+deletion|cases?\s+with\s+missing\s+data\s+(?:were|was)\s+excluded|"
    r"excluded\s+(?:due\s+to\s+)?missing\s+data|"
    r"only\s+(?:complete|non[\s-]missing)\s+cases?\s+(?:were|was)\s+(?:included|used|analysed))\b",
    re.IGNORECASE,
)
_MCAR_CHECK_RE = re.compile(
    r"\b(?:missing\s+completely\s+at\s+random|MCAR|Little'?s?\s+MCAR\s+test|"
    r"data\s+(?:are|were)\s+(?:assumed\s+to\s+be\s+)?missing\s+(?:at|completely)\s+at\s+random|"
    r"sensitivity\s+analysis\s+for\s+missing|"
    r"(?:compared|tested)\s+(?:completers?|responders?)\s+(?:to|vs\.?|versus)\s+"
    r"(?:non[\s-]completers?|non[\s-]responders?))\b",
    re.IGNORECASE,
)


def validate_complete_case_analysis_bias(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag complete-case analysis without MCAR justification.

    Emits ``unjustified-complete-case-analysis`` (moderate) when listwise
    deletion or complete-case analysis is used without justifying MCAR
    assumption or comparing completers to non-completers.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_complete_case_analysis_bias", findings=[]
        )

    full = parsed.full_text
    if not _COMPLETE_CASE_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_complete_case_analysis_bias", findings=[]
        )

    if _MCAR_CHECK_RE.search(full):
        return ValidationResult(
            validator_name="validate_complete_case_analysis_bias", findings=[]
        )

    return ValidationResult(
        validator_name="validate_complete_case_analysis_bias",
        findings=[
            Finding(
                code="unjustified-complete-case-analysis",
                severity="moderate",
                message=(
                    "Complete-case or listwise deletion is used without justifying the "
                    "MCAR assumption or comparing completers to non-completers. "
                    "This may introduce bias if data are not MCAR."
                ),
                validator="validate_complete_case_analysis_bias",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 274 – validate_analytic_strategy_prespecification
# ---------------------------------------------------------------------------

_EXPLORATORY_TRIGGER_RE = re.compile(
    r"\b(?:exploratory\s+(?:analysis|study|investigation|approach)|"
    r"post[\s-]hoc\s+(?:exploration|analysis|examination)|"
    r"we\s+(?:also|additionally|further)\s+explored?|"
    r"exploratory\s+(?:aim|objective|research\s+question))\b",
    re.IGNORECASE,
)
_EXPLORATORY_LABELLED_RE = re.compile(
    r"\b(?:exploratory\s+(?:\w+\s+){0,3}(?:should\s+be\s+interpreted|"
    r"(?:are|were)\s+(?:preliminary|hypothesis[\s-]generating))|"
    r"(?:labelled|noted|flagged)\s+as\s+exploratory|"
    r"(?:these|such)\s+(?:exploratory\s+)?analyses?\s+(?:should|must)\s+be\s+replicated|"
    r"exploratory\s+nature\s+(?:of|warrants?)|"
    r"hypothesis[\s-]generating\s+(?:only|in\s+nature))\b",
    re.IGNORECASE,
)


def validate_analytic_strategy_prespecification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag exploratory analyses not labelled as such.

    Emits ``unlabelled-exploratory-analysis`` (minor) when exploratory analyses
    are described but not explicitly labelled as preliminary or hypothesis-generating.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_analytic_strategy_prespecification", findings=[]
        )

    full = parsed.full_text
    if not _EXPLORATORY_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_analytic_strategy_prespecification", findings=[]
        )

    if _EXPLORATORY_LABELLED_RE.search(full):
        return ValidationResult(
            validator_name="validate_analytic_strategy_prespecification", findings=[]
        )

    return ValidationResult(
        validator_name="validate_analytic_strategy_prespecification",
        findings=[
            Finding(
                code="unlabelled-exploratory-analysis",
                severity="minor",
                message=(
                    "Exploratory analyses are described but not explicitly labelled as "
                    "hypothesis-generating or preliminary. Flag exploratory findings "
                    "to avoid overconfident interpretation."
                ),
                validator="validate_analytic_strategy_prespecification",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 275 – validate_self_report_bias_acknowledgement
# ---------------------------------------------------------------------------

_SELF_REPORT_TRIGGER_RE = re.compile(
    r"\b(?:self[\s-]report(?:ed|ing)?|questionnaire\s+(?:data|responses?)|"
    r"participants\s+reported|self[\s-]administered\s+(?:questionnaire|survey)|"
    r"online\s+survey\s+(?:data|responses?))\b",
    re.IGNORECASE,
)
_SELF_REPORT_CAVEAT_RE = re.compile(
    r"\b(?:self[\s-]report\s+(?:bias|limitation)|social\s+desirability\s+(?:bias)?|"
    r"recall\s+(?:bias|error)|response\s+bias|"
    r"limitation\s+of\s+self[\s-](?:report|administered)|"
    r"participants\s+may\s+have\s+(?:over[\s-]|under[\s-])?(?:reported|estimated)|"
    r"subjective\s+(?:report|measure|assessment))\b",
    re.IGNORECASE,
)


def validate_self_report_bias_acknowledgement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag self-report data use without bias acknowledgement.

    Emits ``missing-self-report-bias-acknowledgement`` (minor) when self-report
    data are used without acknowledging potential response bias.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_self_report_bias_acknowledgement", findings=[]
        )

    full = parsed.full_text
    if not _SELF_REPORT_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_self_report_bias_acknowledgement", findings=[]
        )

    if _SELF_REPORT_CAVEAT_RE.search(full):
        return ValidationResult(
            validator_name="validate_self_report_bias_acknowledgement", findings=[]
        )

    return ValidationResult(
        validator_name="validate_self_report_bias_acknowledgement",
        findings=[
            Finding(
                code="missing-self-report-bias-acknowledgement",
                severity="minor",
                message=(
                    "Self-report data are used without acknowledging potential response "
                    "bias (e.g., social desirability, recall bias). Acknowledge these "
                    "limitations in the discussion."
                ),
                validator="validate_self_report_bias_acknowledgement",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 276 – validate_p_value_reporting_precision
# ---------------------------------------------------------------------------

_P_VALUE_RE = re.compile(
    r"\b(?:p\s*[=<>≤≥]\s*0?\.\d+|p[\s-]value\s*[=<>≤≥]\s*0?\.\d+|"
    r"p\s*=\s*\.0{3,}|p\s*<\s*\.0{1,2}1)\b",
    re.IGNORECASE,
)
_P_EXACT_RE = re.compile(
    r"\b(?:p\s*=\s*\.\d{3,}|p\s*=\s*0\.\d{3,}|"
    r"exact\s+p[\s-]value|p[\s-]value\s+(?:was|is)\s+reported\s+exactly)\b",
    re.IGNORECASE,
)
_P_THRESHOLD_ONLY_RE = re.compile(
    r"\b(?:p\s*<\s*\.0+1\b|p\s*<\s*\.05\b|p\s*<\s*\.001\b|"
    r"p\s*=\s*\.0{4,})",
    re.IGNORECASE,
)


def validate_p_value_reporting_precision(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag p-values reported only as threshold comparisons.

    Emits ``imprecise-p-value-reporting`` (minor) when all p-values are reported
    as thresholds only (e.g., p < .05) rather than exact values.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_p_value_reporting_precision", findings=[]
        )

    full = parsed.full_text
    if not _P_VALUE_RE.search(full):
        return ValidationResult(
            validator_name="validate_p_value_reporting_precision", findings=[]
        )

    if _P_EXACT_RE.search(full):
        return ValidationResult(
            validator_name="validate_p_value_reporting_precision", findings=[]
        )

    if not _P_THRESHOLD_ONLY_RE.search(full):
        return ValidationResult(
            validator_name="validate_p_value_reporting_precision", findings=[]
        )

    return ValidationResult(
        validator_name="validate_p_value_reporting_precision",
        findings=[
            Finding(
                code="imprecise-p-value-reporting",
                severity="minor",
                message=(
                    "P-values appear to be reported only as threshold comparisons "
                    "(e.g., p < .05). Report exact p-values where possible to aid "
                    "replication and meta-analysis."
                ),
                validator="validate_p_value_reporting_precision",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 277 – validate_moderator_analysis_interpretation
# ---------------------------------------------------------------------------

_MODERATOR_TRIGGER_RE = re.compile(
    r"\b(?:moderati(?:on|ng)|moderator\s+variable|interaction\s+effect|"
    r"\w+\s+moderat(?:es?|ed)\s+the\s+(?:relationship|effect|association))\b",
    re.IGNORECASE,
)
_MODERATOR_INTERPRETATION_RE = re.compile(
    r"\b(?:simple\s+(?:slopes?|effect)\s+analysis|probing\s+the\s+interaction|"
    r"regions?\s+of\s+significance|Johnson[\s-]Neyman|"
    r"at\s+(?:low|high|mean)\s+levels?\s+of|"
    r"graphed?\s+the\s+interaction|plotted?\s+the\s+interaction|"
    r"interaction\s+was\s+probed)\b",
    re.IGNORECASE,
)


def validate_moderator_analysis_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag significant moderation without follow-up interpretation.

    Emits ``missing-moderator-follow-up`` (minor) when moderation is tested
    and significant but no follow-up probing (simple slopes, regions of
    significance) is reported.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_moderator_analysis_interpretation", findings=[]
        )

    full = parsed.full_text
    if not _MODERATOR_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_moderator_analysis_interpretation", findings=[]
        )

    if _MODERATOR_INTERPRETATION_RE.search(full):
        return ValidationResult(
            validator_name="validate_moderator_analysis_interpretation", findings=[]
        )

    return ValidationResult(
        validator_name="validate_moderator_analysis_interpretation",
        findings=[
            Finding(
                code="missing-moderator-follow-up",
                severity="minor",
                message=(
                    "Moderation is claimed but no follow-up probing of the interaction "
                    "is reported (e.g., simple slopes, Johnson-Neyman). Probe significant "
                    "interactions to describe the nature of moderation."
                ),
                validator="validate_moderator_analysis_interpretation",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 278 – validate_measurement_occasion_labelling
# ---------------------------------------------------------------------------

_MULTI_TIMEPOINT_TRIGGER_RE = re.compile(
    r"\b(?:time\s+(?:1|2|3|one|two|three)|T[123]\b|"
    r"wave\s+(?:1|2|3|one|two|three)|W[123]\b|"
    r"baseline\s+and\s+(?:follow[\s-]?up|post[\s-]?(?:test|intervention|treatment))|"
    r"pre[\s-]?(?:test|treatment|intervention)\s+and\s+post[\s-]?(?:test|treatment|intervention))\b",
    re.IGNORECASE,
)
_OCCASION_LABELLING_RE = re.compile(
    r"\b(?:time\s+(?:1|2|3|one|two|three)\s+(?:was|corresponds?|refers?)|"
    r"T[123]\s+(?:was|corresponds?|refers?)|"
    r"wave\s+(?:1|2|3)\s+(?:was|corresponds?|refers?)|"
    r"baseline\s+(?:measurement|assessment)(?:\s+\w+){0,3}\s+conducted|"
    r"measurement\s+occasions?\s+(?:were|are)\s+labelled|"
    r"(?:first|second|third)\s+(?:measurement|assessment|time\s+point))\b",
    re.IGNORECASE,
)


def validate_measurement_occasion_labelling(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag unlabelled measurement occasions in longitudinal studies.

    Emits ``unlabelled-measurement-occasions`` (minor) when time labels like
    T1/T2 or Wave 1/Wave 2 are used without defining what they refer to.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_measurement_occasion_labelling", findings=[]
        )

    full = parsed.full_text
    if not _MULTI_TIMEPOINT_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_measurement_occasion_labelling", findings=[]
        )

    if _OCCASION_LABELLING_RE.search(full):
        return ValidationResult(
            validator_name="validate_measurement_occasion_labelling", findings=[]
        )

    return ValidationResult(
        validator_name="validate_measurement_occasion_labelling",
        findings=[
            Finding(
                code="unlabelled-measurement-occasions",
                severity="minor",
                message=(
                    "Time labels (T1, T2, Wave 1, etc.) are used without defining what "
                    "each occasion corresponds to. Clearly label measurement occasions "
                    "with their timing or content."
                ),
                validator="validate_measurement_occasion_labelling",
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 279 – validate_statistical_conclusion_validity
# ---------------------------------------------------------------------------

_LOW_POWER_TRIGGER_RE = re.compile(
    r"\b(?:underpowered|under[\s-]?powered|small\s+sample\s+(?:size|n)|"
    r"limited\s+(?:statistical\s+)?power|insufficient\s+power|"
    r"our\s+study\s+(?:lacked|had\s+limited)\s+(?:statistical\s+)?power)\b",
    re.IGNORECASE,
)
_NULL_POWER_TRIGGER_RE = re.compile(
    r"\b(?:(?:not|non)\s+significant|p\s*>\s*\.0[5-9]|p\s*>\s*\.[1-9]\d*|"
    r"failed\s+to\s+(?:reach|achieve)\s+significance|"
    r"no\s+significant\s+(?:effect|difference|association|relationship))\b",
    re.IGNORECASE,
)
_NULL_POWER_DISCUSSION_RE = re.compile(
    r"\b(?:Type\s+II\s+error|false\s+negative|statistical\s+power|"
    r"may\s+(?:have\s+)?(?:been|be)\s+underpowered|"
    r"power\s+to\s+detect\s+(?:an?\s+)?(?:effect|difference)|"
    r"null\s+(?:result|finding)\s+(?:may|might|could)\s+reflect)\b",
    re.IGNORECASE,
)


def validate_statistical_conclusion_validity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag null results without statistical power discussion.

    Emits ``missing-null-result-power-discussion`` (moderate) when a null
    result is reported alongside acknowledgement of limited power but no
    discussion of Type II error risk.
    """
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(
            validator_name="validate_statistical_conclusion_validity", findings=[]
        )

    full = parsed.full_text
    if not _NULL_POWER_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_statistical_conclusion_validity", findings=[]
        )

    if not _LOW_POWER_TRIGGER_RE.search(full):
        return ValidationResult(
            validator_name="validate_statistical_conclusion_validity", findings=[]
        )

    if _NULL_POWER_DISCUSSION_RE.search(full):
        return ValidationResult(
            validator_name="validate_statistical_conclusion_validity", findings=[]
        )

    return ValidationResult(
        validator_name="validate_statistical_conclusion_validity",
        findings=[
            Finding(
                code="missing-null-result-power-discussion",
                severity="moderate",
                message=(
                    "A null result is reported but statistical power is acknowledged "
                    "as limited without discussing Type II error risk. Discuss whether "
                    "the study was adequately powered to detect the expected effect."
                ),
                validator="validate_statistical_conclusion_validity",
            )
        ],
    )




# ---------------------------------------------------------------------------
# Phase 281 – validate_scale_reliability_reporting
# ---------------------------------------------------------------------------

_MULTI_ITEM_SCALE_RE = re.compile(
    r"\b(?:scale|questionnaire|inventory|measure|instrument|subscale|composite\s+score)"
    r"\b.{0,60}\b(?:item|items|question|questions|indicator|indicators)\b",
    re.IGNORECASE | re.DOTALL,
)
_RELIABILITY_REPORTED_RE = re.compile(
    r"\b(?:Cronbach(?:'s|\s+alpha)|alpha\s*=|ω\s*=|omega\s*=|McDonald(?:'s|\s+omega)|"
    r"coefficient\s+alpha|internal\s+consistency|reliability\s+coefficient|"
    r"test[\s-]retest\s+reliability)\b",
    re.IGNORECASE,
)


def validate_scale_reliability_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multi-item scale use without reliability reporting.

    Emits ``missing-scale-reliability`` (minor) when a multi-item scale is
    referenced but no reliability coefficient (e.g., Cronbach's alpha) is
    reported.
    """
    _vid = "validate_scale_reliability_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MULTI_ITEM_SCALE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _RELIABILITY_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-scale-reliability",
                severity="minor",
                message=(
                    "A multi-item scale is used but no reliability coefficient "
                    "(e.g., Cronbach's alpha, McDonald's omega) is reported. "
                    "Include internal consistency estimates for all composite scores."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 282 – validate_pilot_study_scope_limitation
# ---------------------------------------------------------------------------

_PILOT_SCOPE_TRIGGER_RE = re.compile(
    r"\b(?:pilot\s+(?:study|trial|test|data|sample)|"
    r"feasibility\s+(?:study|trial)|preliminary\s+(?:study|data|findings))\b",
    re.IGNORECASE,
)
_PILOT_SCOPE_CAVEAT_RE = re.compile(
    r"\b(?:small\s+sample|limited\s+(?:sample|power|generali[sz]ability)|"
    r"underpowered|preliminary\s+(?:evidence|finding|conclusion)|"
    r"should\s+be\s+(?:replicated|confirmed|interpreted\s+with\s+caution)|"
    r"caution\s+(?:is\s+warranted|should\s+be\s+exercised|in\s+interpreting)|"
    r"exploratory\s+in\s+nature|not\s+(?:definitive|conclusive)|"
    r"future\s+(?:studies|research|work)\s+(?:should|are\s+needed)\s+to\s+"
    r"(?:confirm|replicate|validate))\b",
    re.IGNORECASE,
)


def validate_pilot_study_scope_limitation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag pilot studies that do not acknowledge scope limitations.

    Emits ``missing-pilot-scope-limitation`` (minor) when a pilot study is
    described without any caveat about its preliminary or limited nature.
    """
    _vid = "validate_pilot_study_scope_limitation"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _PILOT_SCOPE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PILOT_SCOPE_CAVEAT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-pilot-scope-limitation",
                severity="minor",
                message=(
                    "A pilot study is described but no scope or generalisability "
                    "limitation is acknowledged. Add a caveat noting the preliminary "
                    "nature of the findings and the need for confirmatory replication."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 283 – validate_literature_search_recency
# ---------------------------------------------------------------------------

_LIT_REVIEW_TRIGGER_RE = re.compile(
    r"\b(?:systematic\s+review|literature\s+review|scoping\s+review|"
    r"narrative\s+review|searched?\s+(?:the\s+)?(?:literature|databases?|"
    r"PubMed|MEDLINE|PsycINFO|Web\s+of\s+Science|Scopus|CINAHL|EMBASE))\b",
    re.IGNORECASE,
)
_SEARCH_DATE_RE = re.compile(
    r"\b(?:search(?:es|ed)?\s+(?:was|were)?\s+(?:conducted|performed|last\s+updated|"
    r"last\s+run|last\s+executed)\s+(?:in|on|through|up\s+to|until)|"
    r"(?:in|through|until|up\s+to)\s+(?:January|February|March|April|May|June|"
    r"July|August|September|October|November|December)\s+\d{4}|"
    r"(?:in|through|until|up\s+to)\s+\d{4}|"
    r"database\s+search(?:es)?\s+were\s+(?:last\s+)?(?:conducted|updated|run))\b",
    re.IGNORECASE,
)


def validate_literature_search_recency(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag systematic/literature reviews without a stated search date.

    Emits ``missing-literature-search-date`` (minor) when a review is
    described but no date for the literature search is reported.
    """
    _vid = "validate_literature_search_recency"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _LIT_REVIEW_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SEARCH_DATE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-literature-search-date",
                severity="minor",
                message=(
                    "A literature search is described but no date or time window "
                    "for the search is reported. State when the search was conducted "
                    "or last updated to allow readers to assess recency."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 284 – validate_publication_bias_acknowledgement
# ---------------------------------------------------------------------------

_REVIEW_SYNTHESIS_RE = re.compile(
    r"\b(?:systematic\s+review|literature\s+review|scoping\s+review|"
    r"narrative\s+review|integrative\s+review)\b",
    re.IGNORECASE,
)
_PUB_BIAS_ACKNOWLEDGED_RE = re.compile(
    r"\b(?:publication\s+bias|reporting\s+bias|file[\s-]drawer|"
    r"grey\s+literature|unpublished\s+(?:studies|data|results)|"
    r"selective\s+reporting|positive\s+result\s+bias)\b",
    re.IGNORECASE,
)


def validate_publication_bias_acknowledgement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag literature reviews that do not mention publication bias.

    Emits ``missing-publication-bias-acknowledgement`` (minor) when a review
    paper is detected but publication/reporting bias is never mentioned.
    """
    _vid = "validate_publication_bias_acknowledgement"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _REVIEW_SYNTHESIS_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PUB_BIAS_ACKNOWLEDGED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-publication-bias-acknowledgement",
                severity="minor",
                message=(
                    "A literature review is described but publication bias or "
                    "selective reporting is not mentioned. Acknowledge the potential "
                    "for publication bias as a limitation of the reviewed literature."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 285 – validate_replication_citation
# ---------------------------------------------------------------------------

_REPLICATION_CLAIM_RE = re.compile(
    r"\b(?:replic(?:at(?:es?|ed|ing)|ation|ations)|"
    r"consistent\s+with\s+(?:previous|prior|earlier)\s+(?:findings|results|work)|"
    r"confirms?\s+(?:previous|prior|earlier)\s+(?:findings|results|reports?))\b",
    re.IGNORECASE,
)
_REPLICATION_CITE_RE = re.compile(
    r"(?:\((?:[A-Z][a-z]+(?:\s+et\s+al\.)?|[A-Z][a-z]+\s*&\s*[A-Z][a-z]+)"
    r"(?:,\s*\d{4})+\)|"
    r"\[(?:\d+(?:,\s*\d+)*)\])",
    re.IGNORECASE,
)


def validate_replication_citation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag replication claims without a supporting citation.

    Emits ``missing-replication-citation`` (minor) when the text claims
    to replicate or confirm prior findings but no citation follows within
    a short window.
    """
    _vid = "validate_replication_citation"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _REPLICATION_CLAIM_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    # Check if any replication claim is followed by a citation within 120 chars
    for m in _REPLICATION_CLAIM_RE.finditer(full):
        window = full[m.start(): m.end() + 120]
        if _REPLICATION_CITE_RE.search(window):
            return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-replication-citation",
                severity="minor",
                message=(
                    "The text claims to replicate or confirm prior findings but no "
                    "supporting citation follows. Cite the original study being "
                    "replicated or the prior findings being confirmed."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 286 – validate_negative_binomial_overdispersion
# ---------------------------------------------------------------------------

_COUNT_OUTCOME_RE = re.compile(
    r"\b(?:count\s+(?:outcome|data|variable|model)|"
    r"Poisson\s+(?:regression|model|distribution)|"
    r"number\s+of\s+(?:events?|incidents?|occurrences?|episodes?|visits?|hospitalizations?))\b",
    re.IGNORECASE,
)
_OVERDISPERSION_CHECK_RE = re.compile(
    r"\b(?:overdispersion|over-dispersion|negative\s+binomial|"
    r"dispersion\s+(?:test|parameter|index)|"
    r"quasi-Poisson|quasi\s+Poisson|"
    r"dispersion\s*(?:=|<|>)\s*[0-9]|variance\s+exceeds?\s+(?:the\s+)?mean)\b",
    re.IGNORECASE,
)


def validate_negative_binomial_overdispersion(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag count-outcome Poisson models without an overdispersion check.

    Emits ``missing-overdispersion-test`` (minor) when a Poisson model for
    count data is described but overdispersion is neither tested nor addressed.
    """
    _vid = "validate_negative_binomial_overdispersion"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _COUNT_OUTCOME_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _OVERDISPERSION_CHECK_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-overdispersion-test",
                severity="minor",
                message=(
                    "A count-outcome or Poisson model is used but overdispersion is "
                    "not tested or addressed. Check for overdispersion (e.g., with a "
                    "negative binomial or quasi-Poisson model) and report the result."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 287 – validate_zero_inflated_data_handling
# ---------------------------------------------------------------------------

_COUNT_DATA_TRIGGER_RE = re.compile(
    r"\b(?:count\s+(?:outcome|data|variable|model)|"
    r"frequency\s+of\s+(?:events?|incidents?|occurrences?)|"
    r"Poisson\s+(?:regression|model)|"
    r"number\s+of\s+(?:events?|incidents?|visits?|episodes?))\b",
    re.IGNORECASE,
)
_ZERO_INFLATION_HANDLED_RE = re.compile(
    r"\b(?:zero[\s-]inflated|zero\s+inflation|excess\s+zeros?|"
    r"hurdle\s+model|ZIP\s+model|ZINB\s+model|"
    r"proportion\s+of\s+zeros?|many\s+(?:zero|zero\s+counts?))\b",
    re.IGNORECASE,
)


def validate_zero_inflated_data_handling(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag count models without addressing potential zero-inflation.

    Emits ``missing-zero-inflation-handling`` (minor) when a count-outcome
    model is detected but zero-inflation is not mentioned or addressed.
    """
    _vid = "validate_zero_inflated_data_handling"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _COUNT_DATA_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ZERO_INFLATION_HANDLED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-zero-inflation-handling",
                severity="minor",
                message=(
                    "A count-outcome model is used but zero-inflation is not "
                    "mentioned or addressed. Inspect the distribution for excess "
                    "zeros and consider a zero-inflated or hurdle model if warranted."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 288 – validate_variance_homogeneity_check
# ---------------------------------------------------------------------------

_BETWEEN_GROUP_STAT_RE = re.compile(
    r"\b(?:t[\s-]?test|ANOVA|ANCOVA|Mann[\s-]?Whitney|independent\s+samples?\s+t|"
    r"one[\s-]?way\s+ANOVA|two[\s-]?way\s+ANOVA)\b",
    re.IGNORECASE,
)
_HOMOGENEITY_REPORTED_RE = re.compile(
    r"\b(?:Levene(?:'s)?\s+test|Bartlett(?:'s)?\s+test|homogeneity\s+of\s+variance|"
    r"equal\s+variances?|unequal\s+variances?|variance\s+(?:were|was|are|is)\s+"
    r"(?:equal|homogeneous|heterogeneous)|Welch(?:'s)?\s+(?:t[\s-]?test|ANOVA)|"
    r"Brown[\s-]?Forsythe\s+test|heteroscedasticity)\b",
    re.IGNORECASE,
)


def validate_variance_homogeneity_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag between-group tests without a variance homogeneity check.

    Emits ``missing-variance-homogeneity-check`` (minor) when a t-test or
    ANOVA is reported but homogeneity of variance is not tested or mentioned.
    """
    _vid = "validate_variance_homogeneity_check"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _BETWEEN_GROUP_STAT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _HOMOGENEITY_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-variance-homogeneity-check",
                severity="minor",
                message=(
                    "A between-group test (t-test or ANOVA) is used but homogeneity "
                    "of variance is not reported. Include Levene's test or use a "
                    "Welch correction for unequal variances."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 289 – validate_path_model_fit_indices
# ---------------------------------------------------------------------------

_PATH_MODEL_RE = re.compile(
    r"\b(?:structural\s+equation\s+model|SEM\b|path\s+(?:model|analysis|diagram)|"
    r"confirmatory\s+factor\s+analysis|CFA\b|latent\s+(?:variable|factor|path)|"
    r"measurement\s+model)\b",
    re.IGNORECASE,
)
_FIT_INDEX_RE = re.compile(
    r"\b(?:CFI\b|TLI\b|RMSEA\b|SRMR\b|GFI\b|AGFI\b|NFI\b|"
    r"comparative\s+fit\s+index|Tucker[\s-]Lewis\s+index|"
    r"root\s+mean\s+square\s+error\s+of\s+approximation|"
    r"standardised\s+root\s+mean\s+(?:square|squared)\s+residual|"
    r"model\s+fit\s+(?:indices|statistics|evaluation))\b",
    re.IGNORECASE,
)


def validate_path_model_fit_indices(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag SEM/path models without reporting model fit indices.

    Emits ``missing-path-model-fit-indices`` (minor) when a structural
    equation or path model is used but no fit indices are reported.
    """
    _vid = "validate_path_model_fit_indices"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _PATH_MODEL_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FIT_INDEX_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-path-model-fit-indices",
                severity="minor",
                message=(
                    "A structural equation or path model is used but no model fit "
                    "indices (e.g., CFI, TLI, RMSEA, SRMR) are reported. Include "
                    "standard fit indices to allow readers to evaluate model fit."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 290 – validate_post_hoc_power_caution
# ---------------------------------------------------------------------------

_POST_HOC_POWER_TRIGGER_RE = re.compile(
    r"\b(?:post[\s-]?hoc\s+(?:power|statistical\s+power)|"
    r"observed\s+power|achieved\s+power|retrospective\s+power)\b",
    re.IGNORECASE,
)
_POST_HOC_POWER_CAVEAT_RE = re.compile(
    r"\b(?:post[\s-]?hoc\s+power\s+(?:is|has\s+been|can\s+be)\s+"
    r"(?:criticized|questioned|misleading|unreliable)|"
    r"observed\s+power\s+(?:is|has\s+been)\s+(?:criticized|questioned|misleading)|"
    r"caution\s+(?:should\s+be\s+exercised|is\s+warranted)\s+"
    r"(?:in\s+interpreting|when\s+interpreting)\s+(?:observed|post[\s-]?hoc)\s+power|"
    r"Hoenig|Lakens|Senn)\b",
    re.IGNORECASE,
)


def validate_post_hoc_power_caution(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag post-hoc power reports without a validity caveat.

    Emits ``missing-post-hoc-power-caution`` (minor) when post-hoc or
    observed power is reported without cautioning about its limitations.
    """
    _vid = "validate_post_hoc_power_caution"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _POST_HOC_POWER_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _POST_HOC_POWER_CAVEAT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-post-hoc-power-caution",
                severity="minor",
                message=(
                    "Post-hoc or observed power is reported without caveats. "
                    "Post-hoc power analysis is widely criticised as uninformative "
                    "and circular. Note this limitation or remove post-hoc power."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 291 – validate_ancova_covariate_balance
# ---------------------------------------------------------------------------

_ANCOVA_TRIGGER_RE = re.compile(
    r"\b(?:ANCOVA|analysis\s+of\s+covariance|covariate[\s-]adjusted|"
    r"adjusted\s+for\s+(?:baseline|pre[\s-]test|pre-existing)|"
    r"controlling\s+for\s+(?:a\s+)?covariate)\b",
    re.IGNORECASE,
)
_COVARIATE_BALANCE_RE = re.compile(
    r"\b(?:covariate\s+(?:balance|equivalence|distribution)|"
    r"group\s+(?:equivalence|balance|comparability)\s+on\s+(?:the\s+)?covariate|"
    r"randomisation\s+ensured\s+(?:covariate\s+)?balance|"
    r"covariate\s+(?:was|were)\s+(?:checked|verified|balanced|comparable)\s+across|"
    r"no\s+(?:significant\s+)?(?:group\s+)?difference\s+(?:on|in)\s+(?:the\s+)?covariate|"
    r"pre[\s-]test\s+(?:did\s+not\s+differ|was\s+comparable))\b",
    re.IGNORECASE,
)


def validate_ancova_covariate_balance(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ANCOVA analyses without covariate balance verification.

    Emits ``missing-ancova-covariate-balance`` (minor) when ANCOVA is used
    but the balance or comparability of the covariate across groups is not
    verified.
    """
    _vid = "validate_ancova_covariate_balance"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ANCOVA_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _COVARIATE_BALANCE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-ancova-covariate-balance",
                severity="minor",
                message=(
                    "ANCOVA is used but covariate balance or group comparability "
                    "on the covariate is not verified. Confirm that groups do not "
                    "differ meaningfully on the covariate before adjustment."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 292 – validate_partial_eta_squared_reporting
# ---------------------------------------------------------------------------

_ANOVA_TRIGGER_RE = re.compile(
    r"\b(?:(?:one|two|three)[\s-]?way\s+ANOVA|repeated[\s-]?measures\s+ANOVA|"
    r"mixed\s+(?:factorial\s+)?ANOVA|MANOVA|ANCOVA|F\s*\(\s*\d+\s*,\s*\d+\s*\))\b",
    re.IGNORECASE,
)
_PARTIAL_ETA_RE = re.compile(
    r"\b(?:partial\s+η²|partial\s+eta[\s-]?squared|ηp²|η_p\s*=|"
    r"partial\s+omega[\s-]?squared|ω²_p|generalised\s+eta[\s-]?squared|"
    r"effect\s+size\s+(?:was|were|is)\s+reported\s+as\s+(?:partial\s+)?η)\b",
    re.IGNORECASE,
)


def validate_partial_eta_squared_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ANOVA analyses without a partial eta-squared or equivalent.

    Emits ``missing-partial-eta-squared`` (minor) when ANOVA results are
    reported but no effect size (e.g., partial η²) is given.
    """
    _vid = "validate_partial_eta_squared_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ANOVA_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PARTIAL_ETA_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-partial-eta-squared",
                severity="minor",
                message=(
                    "ANOVA is used but no effect size (e.g., partial η², ω²_p) is "
                    "reported. Include effect size estimates alongside F-statistics "
                    "to enable readers to assess practical significance."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 293 – validate_cohens_d_reporting
# ---------------------------------------------------------------------------

_MEAN_DIFF_TRIGGER_RE = re.compile(
    r"\b(?:independent\s+samples?\s+t[\s-]?test|paired\s+samples?\s+t[\s-]?test|"
    r"t\s*\(\s*\d+\s*\)\s*=\s*-?[0-9]+\.[0-9]+|"
    r"mean\s+difference\s+(?:was|is|of)|"
    r"groups?\s+differed\s+significantly)\b",
    re.IGNORECASE,
)
_COHENS_D_RE = re.compile(
    r"\b(?:Cohen(?:'s)?\s+d\b|Hedges(?:'s?)?\s+g\b|d\s*=\s*-?[0-9]*\.[0-9]+|"
    r"g\s*=\s*-?[0-9]*\.[0-9]+|standardis(?:ed|ed)\s+mean\s+difference)\b",
    re.IGNORECASE,
)


def validate_cohens_d_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag t-test results without Cohen's d or equivalent.

    Emits ``missing-cohens-d`` (minor) when a t-test is reported but no
    standardised effect size (Cohen's d or Hedges' g) is given.
    """
    _vid = "validate_cohens_d_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MEAN_DIFF_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _COHENS_D_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cohens-d",
                severity="minor",
                message=(
                    "A t-test is reported but no standardised effect size "
                    "(Cohen's d or Hedges' g) is given. Report an effect size "
                    "to allow meta-analytic synthesis and practical interpretation."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 294 – validate_sequential_testing_correction
# ---------------------------------------------------------------------------

_SEQUENTIAL_TEST_TRIGGER_RE = re.compile(
    r"\b(?:interim\s+(?:analysis|analyses|look)|"
    r"sequential\s+(?:testing|trial|analysis|design)|"
    r"group\s+sequential\s+(?:design|method|approach)|"
    r"adaptive\s+(?:stopping|interim)|"
    r"data\s+monitoring\s+committee|DSMB|"
    r"early\s+(?:stopping|termination)\s+(?:for\s+)?(?:efficacy|futility|harm))\b",
    re.IGNORECASE,
)
_SEQUENTIAL_ALPHA_CORRECTION_RE = re.compile(
    r"\b(?:alpha[\s-]?spending|O(?:'|')?Brien[\s-]?Fleming|Pocock\s+(?:bounds?|correction)|"
    r"Lan[\s-]DeMets|adjusted\s+alpha|spending\s+function|"
    r"error\s+(?:spending|inflation)\s+(?:was|is|were)\s+(?:controlled|addressed|corrected)|"
    r"sequential\s+(?:stopping\s+rule|boundary)|"
    r"familywise\s+error\s+rate\s+(?:was|is|were)\s+controlled)\b",
    re.IGNORECASE,
)


def validate_sequential_testing_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag sequential/interim analyses without alpha-spending correction.

    Emits ``missing-sequential-testing-correction`` (moderate) when interim
    or sequential testing is described but no Type I error correction
    (e.g., alpha-spending, O'Brien-Fleming) is mentioned.
    """
    _vid = "validate_sequential_testing_correction"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SEQUENTIAL_TEST_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SEQUENTIAL_ALPHA_CORRECTION_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-sequential-testing-correction",
                severity="moderate",
                message=(
                    "Sequential or interim testing is described but no alpha-spending "
                    "or Type I error correction (e.g., O'Brien-Fleming bounds, Pocock "
                    "correction) is mentioned. Report the error control procedure used."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 295 – validate_adaptive_design_disclosure
# ---------------------------------------------------------------------------

_ADAPTIVE_TRIGGER_RE = re.compile(
    r"\b(?:adaptive\s+(?:design|trial|randomisation|allocation|sample\s+size)|"
    r"sample\s+size\s+(?:re[\s-]?estimation|reassessment|adaptive\s+adjustment)|"
    r"response[\s-]?adaptive\s+randomisation|"
    r"biomarker[\s-]?adaptive|seamless\s+(?:phase|design))\b",
    re.IGNORECASE,
)
_ADAPTIVE_DISCLOSURE_RE = re.compile(
    r"\b(?:pre[\s-]?specified\s+(?:adaptive\s+)?(?:rule|decision|criterion|stopping)|"
    r"adaptation\s+(?:rule|procedure|criteria)\s+(?:was|were|had\s+been)\s+"
    r"(?:pre[\s-]?specified|registered|prospectively\s+defined)|"
    r"independent\s+(?:statistician|committee|data\s+monitoring)|"
    r"type\s+I\s+error\s+(?:was|is|were)\s+(?:controlled|protected|maintained)\s+"
    r"across\s+(?:adaptations?|stages?)|"
    r"blinded\s+(?:sample\s+size\s+)?reassessment)\b",
    re.IGNORECASE,
)


def validate_adaptive_design_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag adaptive designs without pre-specification disclosure.

    Emits ``missing-adaptive-design-disclosure`` (moderate) when an adaptive
    trial design is described but the pre-specification and error control
    procedures are not disclosed.
    """
    _vid = "validate_adaptive_design_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ADAPTIVE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ADAPTIVE_DISCLOSURE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-adaptive-design-disclosure",
                severity="moderate",
                message=(
                    "An adaptive trial design is described but the pre-specification "
                    "of adaptation rules and Type I error control procedures are not "
                    "disclosed. Report how adaptations were governed and how error "
                    "rates were controlled."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 296 – validate_kaplan_meier_censoring_note
# ---------------------------------------------------------------------------

_KM_TRIGGER_RE = re.compile(
    r"\b(?:Kaplan[\s-]?Meier|survival\s+curve|KM\s+curve|"
    r"time[\s-]to[\s-]event|event[\s-]free\s+survival|"
    r"overall\s+survival|progression[\s-]free\s+survival)\b",
    re.IGNORECASE,
)
_CENSORING_NOTE_RE = re.compile(
    r"\b(?:censored|censoring|right[\s-]?censored|administratively\s+censored|"
    r"loss\s+to\s+follow[\s-]?up\s+(?:was|were)\s+(?:censored|treated\s+as)|"
    r"participants?\s+(?:who\s+were|with\s+)?(?:lost|withdrew|dropped)\s+"
    r"(?:to\s+follow[\s-]?up\s+)?were\s+censored|"
    r"number\s+at\s+risk|tick\s+marks?\s+(?:denote|indicate|show)\s+censoring)\b",
    re.IGNORECASE,
)


def validate_kaplan_meier_censoring_note(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Kaplan-Meier survival analyses without a censoring description.

    Emits ``missing-km-censoring-note`` (minor) when a KM curve or survival
    analysis is described but the handling of censored observations is not
    mentioned.
    """
    _vid = "validate_kaplan_meier_censoring_note"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _KM_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _CENSORING_NOTE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-km-censoring-note",
                severity="minor",
                message=(
                    "A Kaplan-Meier or survival analysis is presented but the "
                    "treatment of censored observations is not described. Report "
                    "the censoring mechanism and number of censored participants."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 297 – validate_cox_proportional_hazards_assumption
# ---------------------------------------------------------------------------

_COX_TRIGGER_RE = re.compile(
    r"\b(?:Cox\s+(?:proportional[\s-]?hazards?|regression|model)|"
    r"proportional[\s-]?hazards?\s+(?:regression|model|assumption)|"
    r"hazard\s+ratio\s+(?:was|were|is|are)\s+(?:estimated|obtained)\s+using|"
    r"Cox\s+PH\b)\b",
    re.IGNORECASE,
)
_PH_ASSUMPTION_RE = re.compile(
    r"\b(?:proportional[\s-]?hazards?\s+assumption|"
    r"Schoenfeld\s+residuals?|log[\s-]?log\s+(?:plot|survival\s+curve)|"
    r"PH\s+assumption|time[\s-]?varying\s+(?:covariate|coefficient)|"
    r"test(?:ed|ing)\s+(?:the\s+)?proportional\s+hazards?)\b",
    re.IGNORECASE,
)


def validate_cox_proportional_hazards_assumption(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Cox regression without proportional hazards assumption check.

    Emits ``missing-cox-ph-assumption-check`` (moderate) when a Cox PH model
    is used but the proportional hazards assumption is not tested or mentioned.
    """
    _vid = "validate_cox_proportional_hazards_assumption"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _COX_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PH_ASSUMPTION_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cox-ph-assumption-check",
                severity="moderate",
                message=(
                    "A Cox proportional hazards model is used but the proportional "
                    "hazards assumption is not tested. Check the assumption using "
                    "Schoenfeld residuals or log-log plots and report the result."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 298 – validate_competing_risks_disclosure
# ---------------------------------------------------------------------------

_COMPETING_RISK_TRIGGER_RE = re.compile(
    r"\b(?:time[\s-]to[\s-]event|cause[\s-]specific\s+(?:hazard|survival)|"
    r"cumulative\s+incidence|event\s+of\s+interest|"
    r"(?:death|mortality|relapse|recurrence)\s+(?:precluded?|prevented?)\s+"
    r"(?:the\s+)?(?:event|outcome))\b",
    re.IGNORECASE,
)
_COMPETING_RISK_HANDLED_RE = re.compile(
    r"\b(?:competing\s+(?:risks?|events?)|subdistribution\s+hazard|"
    r"Fine[\s-]Gray|cause[\s-]specific\s+hazard\s+(?:model|analysis|approach)|"
    r"Aalen[\s-]Johansen|competing\s+event\s+(?:was|were)\s+(?:accounted|considered|handled))\b",
    re.IGNORECASE,
)


def validate_competing_risks_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag time-to-event analyses without competing risk consideration.

    Emits ``missing-competing-risks-disclosure`` (moderate) when a
    time-to-event outcome is analysed but competing risks are not addressed.
    """
    _vid = "validate_competing_risks_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _COMPETING_RISK_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _COMPETING_RISK_HANDLED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-competing-risks-disclosure",
                severity="moderate",
                message=(
                    "A time-to-event outcome is analysed but competing risks are "
                    "not addressed. Assess whether competing events exist and, "
                    "if so, report competing risks analyses (e.g., Fine-Gray model)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 299 – validate_propensity_score_balance
# ---------------------------------------------------------------------------

_PROPENSITY_TRIGGER_RE = re.compile(
    r"\b(?:propensity\s+(?:score|matched?|matching|weighting|method)|"
    r"inverse\s+probability\s+(?:weighting|treatment\s+weighting)|IPTW\b|"
    r"average\s+treatment\s+effect\s+(?:on\s+the\s+treated|in\s+the\s+population)|"
    r"ATT\b|ATE\b)\b",
    re.IGNORECASE,
)
_PROPENSITY_BALANCE_RE = re.compile(
    r"\b(?:standardised?\s+(?:mean\s+difference|difference\s+in\s+means?)|SMD\b|"
    r"covariate\s+balance|balance\s+(?:was|were|is|are)\s+(?:achieved|checked|assessed)|"
    r"love\s+plot|balance\s+plot|post[\s-]?match(?:ing)?\s+(?:balance|comparison|check)|"
    r"absolute\s+standardised\s+(?:mean\s+)?difference)\b",
    re.IGNORECASE,
)


def validate_propensity_score_balance(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag propensity score analyses without covariate balance assessment.

    Emits ``missing-propensity-balance-check`` (moderate) when propensity
    score methods are used but covariate balance after matching/weighting
    is not assessed.
    """
    _vid = "validate_propensity_score_balance"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _PROPENSITY_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PROPENSITY_BALANCE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-propensity-balance-check",
                severity="moderate",
                message=(
                    "Propensity score methods are used but post-matching or "
                    "post-weighting covariate balance is not assessed. Report "
                    "standardised mean differences (SMDs) or a balance plot."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 300 – validate_instrumental_variable_disclosure
# ---------------------------------------------------------------------------

_IV_TRIGGER_RE = re.compile(
    r"\b(?:instrumental\s+variable|instrument(?:al)?\s+approach|"
    r"two[\s-]stage\s+least\s+squares|2SLS\b|TSLS\b|"
    r"Mendelian\s+randomis(?:ation|ation)|MR\s+analysis|"
    r"IV\s+(?:estimation|method|approach|analysis))\b",
    re.IGNORECASE,
)
_IV_VALIDITY_RE = re.compile(
    r"\b(?:instrument\s+(?:validity|relevance|exclusion\s+restriction)|"
    r"exclusion\s+restriction|first[\s-]?stage\s+(?:F[\s-]?statistic|result)|"
    r"weak\s+instrument|relevance\s+condition|"
    r"F[\s-]?statistic\s+(?:for\s+)?(?:the\s+)?(?:instrument|first\s+stage)|"
    r"instrument(?:al)?\s+variable\s+(?:satisfies?|meets?)\s+"
    r"(?:the\s+)?(?:relevance|validity)\s+(?:assumption|condition))\b",
    re.IGNORECASE,
)


def validate_instrumental_variable_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag IV analyses without instrument validity argument.

    Emits ``missing-iv-validity-argument`` (moderate) when instrumental
    variable or Mendelian randomisation methods are used but instrument
    validity (relevance, exclusion restriction) is not argued or tested.
    """
    _vid = "validate_instrumental_variable_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _IV_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _IV_VALIDITY_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-iv-validity-argument",
                severity="moderate",
                message=(
                    "An instrumental variable or Mendelian randomisation analysis "
                    "is used but instrument validity (relevance and exclusion "
                    "restriction) is not argued or tested. Report the first-stage "
                    "F-statistic and argue for the exclusion restriction."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 301 – validate_multilevel_random_effects_justification
# ---------------------------------------------------------------------------

_MULTILEVEL_TRIGGER_RE = re.compile(
    r"\b(?:multilevel\s+model|hierarchical\s+(?:linear|logistic)\s+model|"
    r"mixed[\s-]?effects?\s+model|random[\s-]?effects?\s+model|"
    r"linear\s+mixed[\s-]?model|LMM\b|GLMM\b|HLM\b|nested\s+data)\b",
    re.IGNORECASE,
)
_RANDOM_EFFECTS_JUSTIFIED_RE = re.compile(
    r"\b(?:ICC\b|intraclass\s+correlation|"
    r"random\s+(?:intercept|slope)\s+(?:was|were|is|are)\s+(?:included|modelled|estimated)|"
    r"between[\s-]group\s+variance|clustering\s+(?:was|were)\s+(?:accounted\s+for|addressed)|"
    r"school\s+(?:level|effect)|clinic\s+(?:level|effect)|"
    r"nested\s+(?:within|structure|design)\s+(?:was|is)\s+(?:accounted|modelled))\b",
    re.IGNORECASE,
)


def validate_multilevel_random_effects_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multilevel models without random effects justification.

    Emits ``missing-random-effects-justification`` (minor) when a multilevel
    or mixed-effects model is used but the inclusion of random effects is not
    justified (e.g., via ICC or description of clustering).
    """
    _vid = "validate_multilevel_random_effects_justification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MULTILEVEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _RANDOM_EFFECTS_JUSTIFIED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-random-effects-justification",
                severity="minor",
                message=(
                    "A multilevel or mixed-effects model is used but the rationale "
                    "for including random effects (e.g., ICC, nested structure) is "
                    "not provided. Justify random effects inclusion and report the ICC."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 302 – validate_cross_level_interaction_interpretation
# ---------------------------------------------------------------------------

_CROSS_LEVEL_TRIGGER_RE = re.compile(
    r"\b(?:cross[\s-]?level\s+interaction|cross[\s-]level\s+moderation|"
    r"level[\s-]?1\s+(?:predictor|variable)\s+(?:×|x)\s+level[\s-]?2|"
    r"level[\s-]?2\s+moderating\s+(?:the\s+)?level[\s-]?1|"
    r"between[\s-]group\s+moderator\s+of\s+within[\s-]group)\b",
    re.IGNORECASE,
)
_CROSS_LEVEL_INTERPRETED_RE = re.compile(
    r"\b(?:cross[\s-]?level\s+interaction\s+(?:was|is|were)\s+"
    r"(?:significant|interpreted|examined|plotted)|"
    r"simple\s+slopes?\s+(?:were|was)\s+(?:examined|plotted|computed)\s+(?:at|for)|"
    r"level[\s-]?2\s+(?:variable|moderator)\s+moderated\s+the\s+relationship\s+between|"
    r"the\s+relationship\s+between.{0,60}varied\s+(?:across|by)\s+(?:group|context|school|site))\b",
    re.IGNORECASE,
)


def validate_cross_level_interaction_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag cross-level interactions without slope decomposition.

    Emits ``missing-cross-level-interaction-interpretation`` (minor) when a
    cross-level interaction is reported but not interpreted with simple slopes
    or equivalent follow-up.
    """
    _vid = "validate_cross_level_interaction_interpretation"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _CROSS_LEVEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _CROSS_LEVEL_INTERPRETED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cross-level-interaction-interpretation",
                severity="minor",
                message=(
                    "A cross-level interaction is reported but is not interpreted "
                    "with simple slopes or a description of how the Level-2 variable "
                    "modifies the Level-1 relationship."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 303 – validate_repeated_measures_sphericity
# ---------------------------------------------------------------------------

_REPEATED_MEASURES_TRIGGER_RE = re.compile(
    r"\b(?:repeated[\s-]?measures\s+ANOVA|within[\s-]?subjects?\s+ANOVA|"
    r"within[\s-]?subjects?\s+(?:factor|design|effect)|"
    r"doubly\s+multivariate|RM[\s-]?ANOVA)\b",
    re.IGNORECASE,
)
_SPHERICITY_HANDLED_RE = re.compile(
    r"\b(?:Mauchly(?:'s)?\s+test|sphericity\s+(?:assumption|test|violated?|"
    r"was\s+met|was\s+not\s+violated)|Greenhouse[\s-]Geisser|Huynh[\s-]Feldt|"
    r"epsilon\s+correction|sphericity\s+correction|Lower\s+Bound\s+correction)\b",
    re.IGNORECASE,
)


def validate_repeated_measures_sphericity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag repeated-measures ANOVA without sphericity check.

    Emits ``missing-sphericity-correction`` (moderate) when a repeated-measures
    ANOVA is used but the sphericity assumption is neither tested nor corrected.
    """
    _vid = "validate_repeated_measures_sphericity"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _REPEATED_MEASURES_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SPHERICITY_HANDLED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-sphericity-correction",
                severity="moderate",
                message=(
                    "A repeated-measures ANOVA is used but sphericity is not "
                    "tested (Mauchly's test) or corrected (Greenhouse-Geisser, "
                    "Huynh-Feldt). Report the sphericity test and apply a correction "
                    "if the assumption is violated."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 304 – validate_survey_sampling_weight
# ---------------------------------------------------------------------------

_SURVEY_WEIGHT_TRIGGER_RE = re.compile(
    r"\b(?:complex\s+survey|survey\s+(?:design|sampling|data)|"
    r"nationally\s+representative\s+(?:survey|sample)|"
    r"probability\s+sampling|stratified\s+random\s+sampling|"
    r"multi[\s-]?stage\s+(?:sampling|cluster\s+sampling)|"
    r"population[\s-]?based\s+survey)\b",
    re.IGNORECASE,
)
_SURVEY_WEIGHT_HANDLED_RE = re.compile(
    r"\b(?:sampling\s+weight|survey\s+weight|post[\s-]?stratification\s+weight|"
    r"design\s+weight|weighted\s+(?:analysis|estimate|regression)|"
    r"svyglm|svydesign|Taylor\s+series\s+(?:linearisation|linearization)|"
    r"design[\s-]?based\s+(?:analysis|standard\s+error|inference))\b",
    re.IGNORECASE,
)


def validate_survey_sampling_weight(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag complex survey analyses without sampling weight disclosure.

    Emits ``missing-survey-weight-disclosure`` (minor) when a complex survey
    or nationally representative sample is analysed but sampling weights are
    not mentioned or applied.
    """
    _vid = "validate_survey_sampling_weight"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SURVEY_WEIGHT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SURVEY_WEIGHT_HANDLED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-survey-weight-disclosure",
                severity="minor",
                message=(
                    "A complex survey or nationally representative sample is used "
                    "but sampling weights and design-based analysis are not mentioned. "
                    "Apply and report sampling weights to produce unbiased estimates."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 305 – validate_finite_population_correction
# ---------------------------------------------------------------------------

_FINITE_POP_TRIGGER_RE = re.compile(
    r"\b(?:finite\s+population|census\s+(?:data|sample)|"
    r"all\s+(?:employees?|members?|students?|residents?)\s+in\s+(?:the\s+)?(?:organization|company|school|city)|"
    r"complete\s+population\s+data|surveyed\s+(?:all|the\s+entire)\s+(?:population|organisation|cohort))\b",
    re.IGNORECASE,
)
_FPC_APPLIED_RE = re.compile(
    r"\b(?:finite\s+population\s+correction|FPC\b|"
    r"(?:sampling\s+)?fraction\s+(?:exceeds?|is\s+greater\s+than|was)\s+(?:[1-9]\d|0\.[2-9])|"
    r"population\s+size\s+was\s+(?:small|taken\s+into\s+account)|"
    r"hypergeometric|without\s+replacement\s+from\s+(?:a\s+)?(?:small|finite)\s+population)\b",
    re.IGNORECASE,
)


def validate_finite_population_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag large-fraction sampling without finite population correction.

    Emits ``missing-finite-population-correction`` (minor) when a sample
    constitutes a substantial fraction of a small finite population but no
    finite population correction is applied or discussed.
    """
    _vid = "validate_finite_population_correction"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _FINITE_POP_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FPC_APPLIED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-finite-population-correction",
                severity="minor",
                message=(
                    "The sample appears to constitute a substantial fraction of a "
                    "finite population but no finite population correction (FPC) is "
                    "mentioned. Consider applying FPC to avoid overstating precision."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 306 – validate_mcmc_convergence_reporting
# ---------------------------------------------------------------------------

_MCMC_TRIGGER_RE = re.compile(
    r"\b(?:MCMC\b|Markov\s+chain\s+Monte\s+Carlo|Bayesian\s+(?:sampling|inference|"
    r"estimation|analysis)|Stan\b|JAGS\b|PyMC\b|No[\s-]?U[\s-]?Turn\s+sampler|NUTS\b|"
    r"posterior\s+(?:samples?|draws?|distribution))\b",
    re.IGNORECASE,
)
_MCMC_CONVERGENCE_RE = re.compile(
    r"\b(?:R[\s-]?hat\b|Rhat\b|potential\s+scale\s+reduction|Gelman[\s-]Rubin|"
    r"Geweke\s+(?:test|diagnostic)|Heidelberger[\s-]Welch|Raftery[\s-]Lewis|"
    r"effective\s+sample\s+size|ESS\b|trace\s+plot|MCMC\s+convergence|"
    r"chains?\s+(?:converged?|mixed\s+well|showed\s+good\s+mixing))\b",
    re.IGNORECASE,
)


def validate_mcmc_convergence_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayesian/MCMC analyses without convergence diagnostics.

    Emits ``missing-mcmc-convergence-report`` (moderate) when MCMC sampling
    is described but convergence diagnostics (R-hat, ESS, trace plots) are
    not reported.
    """
    _vid = "validate_mcmc_convergence_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MCMC_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _MCMC_CONVERGENCE_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-mcmc-convergence-report",
                severity="moderate",
                message=(
                    "MCMC sampling is described but convergence diagnostics "
                    "(e.g., R-hat, effective sample size, trace plots) are not "
                    "reported. Include convergence checks to validate posterior "
                    "estimates."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 307 – validate_bayes_factor_interpretation
# ---------------------------------------------------------------------------

_BF_TRIGGER_RE = re.compile(
    r"\b(?:Bayes\s+factor|BF\s*(?:01|10|₀₁|₁₀)?\s*=|log\s+Bayes\s+factor|"
    r"BF\s*[><=]\s*[0-9]|evidence\s+ratio)\b",
    re.IGNORECASE,
)
_BF_INTERPRETED_RE = re.compile(
    r"\b(?:anecdotal|moderate\s+evidence|strong\s+evidence|very\s+strong\s+evidence|"
    r"extreme\s+evidence|decisive\s+evidence|"
    r"substantial\s+evidence|"
    r"Jeffreys|Kass\s+and\s+Raftery|Raftery\s+guideline|"
    r"BF\s*(?:01|10)\s*(?:indicates?|suggests?|reflects?)\s+(?:moderate|strong|very\s+strong|extreme|decisive)|"
    r"interpreted\s+as\s+(?:moderate|strong|very\s+strong|extreme)\s+evidence)\b",
    re.IGNORECASE,
)


def validate_bayes_factor_interpretation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayes factor reports without qualitative interpretation.

    Emits ``missing-bayes-factor-interpretation`` (minor) when a Bayes factor
    is reported but not interpreted using a standard evidence scale.
    """
    _vid = "validate_bayes_factor_interpretation"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _BF_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _BF_INTERPRETED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-bayes-factor-interpretation",
                severity="minor",
                message=(
                    "A Bayes factor is reported but is not interpreted using a "
                    "standard evidence scale (e.g., Jeffreys, Kass & Raftery). "
                    "Label the evidential category to aid reader interpretation."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 308 – validate_waic_looic_reporting
# ---------------------------------------------------------------------------

_LOO_TRIGGER_RE = re.compile(
    r"\b(?:WAIC\b|LOOIC\b|LOO[\s-]?CV\b|leave[\s-]one[\s-]out\s+cross[\s-]?validation|"
    r"widely\s+applicable\s+information\s+criterion|"
    r"expected\s+log\s+predictive\s+density|ELPD\b|"
    r"Bayesian\s+model\s+(?:comparison|selection|averaging))\b",
    re.IGNORECASE,
)
_LOO_REPORTED_RE = re.compile(
    r"\b(?:WAIC\s*=|LOOIC\s*=|ELPD\s*=|"
    r"LOO\s+(?:estimate|result|difference|comparison)|"
    r"WAIC\s+(?:showed?|indicated?|favoured?|preferred?)|"
    r"model\s+with\s+(?:lower|smallest)\s+(?:WAIC|LOOIC)|"
    r"Pareto[\s-]k\s+(?:diagnostic|values?))\b",
    re.IGNORECASE,
)


def validate_waic_looic_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayesian model comparison without WAIC/LOOIC values.

    Emits ``missing-loo-model-comparison`` (minor) when Bayesian model
    comparison is described using WAIC or LOO-CV but no numeric values
    are reported.
    """
    _vid = "validate_waic_looic_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _LOO_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _LOO_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-loo-model-comparison",
                severity="minor",
                message=(
                    "Bayesian model comparison using WAIC or LOO-CV is mentioned "
                    "but no numeric results (WAIC, ELPD, Pareto-k) are reported. "
                    "Include model comparison statistics to support model selection."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 309 – validate_informative_prior_justification
# ---------------------------------------------------------------------------

_INFORMATIVE_PRIOR_TRIGGER_RE = re.compile(
    r"\b(?:informative\s+prior|strongly\s+informative\s+prior|"
    r"prior\s+distribution\s+(?:was|were|is)\s+(?:based\s+on|informed\s+by|derived\s+from)|"
    r"expert\s+(?:prior|elicitation)|"
    r"skeptical\s+prior|enthusiastic\s+prior|"
    r"non[\s-]?default\s+prior)\b",
    re.IGNORECASE,
)
_PRIOR_JUSTIFIED_RE = re.compile(
    r"\b(?:prior\s+(?:was|is|were)\s+(?:justified|chosen|selected|based\s+on|"
    r"derived\s+from|informed\s+by|calibrated)|"
    r"we\s+(?:chose|selected|specified)\s+(?:an?\s+)?informative\s+prior\s+"
    r"(?:because|based\s+on|to)|"
    r"prior\s+sensitivity\s+(?:analysis|check)|"
    r"vague\s+prior|weakly\s+informative\s+prior|diffuse\s+prior)\b",
    re.IGNORECASE,
)


def validate_informative_prior_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag informative priors without justification.

    Emits ``missing-informative-prior-justification`` (minor) when an
    informative prior is used but not justified or motivated.
    Note: complements ``validate_bayesian_prior_justification`` which
    checks any Bayesian analysis; this targets explicit informative priors.
    """
    _vid = "validate_informative_prior_justification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _INFORMATIVE_PRIOR_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PRIOR_JUSTIFIED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-informative-prior-justification",
                severity="minor",
                message=(
                    "An informative prior is described but its selection is not "
                    "justified. Explain the basis for the prior (e.g., previous "
                    "data, expert elicitation) and conduct a prior sensitivity "
                    "analysis."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 310 – validate_posterior_predictive_check
# ---------------------------------------------------------------------------

_POSTERIOR_MODEL_TRIGGER_RE = re.compile(
    r"\b(?:Bayesian\s+(?:model|regression|analysis|inference|estimation)|"
    r"posterior\s+(?:distribution|samples?|inference|estimate)|"
    r"Stan\b|JAGS\b|PyMC\b|brms\b|rstanarm\b)\b",
    re.IGNORECASE,
)
_PPC_PERFORMED_RE = re.compile(
    r"\b(?:posterior\s+predictive\s+(?:checks?|distribution|p[\s-]?value)|"
    r"PPC\b|graphical\s+posterior\s+(?:checks?|assessment)|"
    r"model\s+(?:adequacy|fit)\s+was\s+(?:assessed|checked|evaluated)\s+"
    r"(?:using\s+)?posterior|"
    r"pp_check\b|bayesplot\b)\b",
    re.IGNORECASE,
)


def validate_posterior_predictive_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Bayesian models without posterior predictive checks.

    Emits ``missing-posterior-predictive-check`` (minor) when a Bayesian
    model is fitted but posterior predictive checks are not described.
    """
    _vid = "validate_posterior_predictive_check"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _POSTERIOR_MODEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _PPC_PERFORMED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-posterior-predictive-check",
                severity="minor",
                message=(
                    "A Bayesian model is fitted but posterior predictive checks "
                    "are not reported. Include graphical or quantitative posterior "
                    "predictive checks to assess model adequacy."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 311 – validate_train_test_split_disclosure
# ---------------------------------------------------------------------------

_ML_MODEL_TRIGGER_RE = re.compile(
    r"\b(?:machine\s+learning|deep\s+learning|neural\s+network|random\s+forest|"
    r"gradient\s+boost(?:ing)?|support\s+vector\s+machine|SVM\b|"
    r"XGBoost|LightGBM|logistic\s+classifier|classification\s+model|"
    r"predictive\s+model|supervised\s+learning)\b",
    re.IGNORECASE,
)
_TRAIN_TEST_DISCLOSED_RE = re.compile(
    r"\b(?:train(?:ing)?\s+(?:set|data|sample)|test(?:ing)?\s+(?:set|data|sample)|"
    r"held[\s-]out\s+(?:set|data|sample)|holdout\s+(?:set|data|sample)|"
    r"train[\s/]test\s+split|(?:\d+)[%\s]+(?:of\s+(?:the\s+)?data\s+)?"
    r"(?:was|were)\s+(?:used\s+for\s+)?(?:training|testing|validation)|"
    r"external\s+validation\s+(?:set|cohort|data))\b",
    re.IGNORECASE,
)


def validate_train_test_split_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ML models without a train/test split disclosure.

    Emits ``missing-train-test-split`` (minor) when a machine learning or
    predictive model is described but the train/test split strategy is not
    disclosed.
    """
    _vid = "validate_train_test_split_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ML_MODEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TRAIN_TEST_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-train-test-split",
                severity="minor",
                message=(
                    "A machine learning model is described but the train/test split "
                    "strategy is not disclosed. Report the proportion of data used "
                    "for training, validation, and testing."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 312 – validate_hyperparameter_tuning_disclosure
# ---------------------------------------------------------------------------

_HYPERPAR_TRIGGER_RE = re.compile(
    r"\b(?:hyperparameter|learning\s+rate|regularization\s+(?:parameter|strength)|"
    r"lambda\s+(?:for\s+)?(?:LASSO|ridge|elastic\s+net)|"
    r"number\s+of\s+(?:trees?|estimators?|layers?|hidden\s+units?|epochs?)|"
    r"max(?:imum)?\s+depth|min(?:imum)?\s+samples?|kernel\s+(?:function|parameter))\b",
    re.IGNORECASE,
)
_TUNING_DISCLOSED_RE = re.compile(
    r"\b(?:hyperparameter\s+(?:tuning|optimisation|search|selection|grid\s+search|"
    r"random\s+search|Bayesian\s+optimisation)|"
    r"cross[\s-]?validated?\s+(?:hyperparameter|parameter)\s+selection|"
    r"optimal\s+hyperparameter|best\s+(?:parameter|configuration)\s+was\s+selected|"
    r"grid\s+search|random\s+search)\b",
    re.IGNORECASE,
)


def validate_hyperparameter_tuning_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ML models with hyperparameters but no tuning disclosure.

    Emits ``missing-hyperparameter-tuning-disclosure`` (minor) when a model
    with hyperparameters is described but how they were selected is not stated.
    """
    _vid = "validate_hyperparameter_tuning_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _HYPERPAR_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TUNING_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-hyperparameter-tuning-disclosure",
                severity="minor",
                message=(
                    "Model hyperparameters are mentioned but the tuning procedure "
                    "(e.g., grid search, cross-validation) is not described. "
                    "Report how hyperparameters were selected to ensure reproducibility."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 313 – validate_feature_importance_method
# ---------------------------------------------------------------------------

_FEATURE_IMPORT_TRIGGER_RE = re.compile(
    r"\b(?:feature\s+importance|variable\s+importance|predictor\s+importance|"
    r"most\s+important\s+(?:features?|variables?|predictors?)|"
    r"top\s+(?:features?|variables?|predictors?)\s+(?:were|included))\b",
    re.IGNORECASE,
)
_FEATURE_IMPORT_EXPLAINED_RE = re.compile(
    r"\b(?:SHAP\b|Shapley\s+value|permutation\s+importance|"
    r"Gini\s+importance|mean\s+decrease\s+(?:in\s+)?(?:accuracy|impurity)|"
    r"absolute\s+coefficient|feature\s+importance\s+(?:was|is|were)\s+"
    r"(?:calculated|computed|estimated|derived)\s+(?:using|via|with|as))\b",
    re.IGNORECASE,
)


def validate_feature_importance_method(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag feature importance claims without methodology disclosure.

    Emits ``missing-feature-importance-method`` (minor) when feature
    importance is reported but the method for computing it is not stated.
    """
    _vid = "validate_feature_importance_method"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _FEATURE_IMPORT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FEATURE_IMPORT_EXPLAINED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-feature-importance-method",
                severity="minor",
                message=(
                    "Feature importance is reported but the method for computing it "
                    "(e.g., SHAP values, permutation importance, Gini impurity) is "
                    "not described. Specify the feature importance metric used."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 314 – validate_data_leakage_prevention
# ---------------------------------------------------------------------------

_LEAKAGE_RISK_TRIGGER_RE = re.compile(
    r"\b(?:feature\s+(?:engineering|extraction|selection|scaling|normaliz(?:ation|ing))|"
    r"imputation|oversampling|SMOTE\b|data\s+augmentation|"
    r"normaliz(?:ed|ing|ation)\s+(?:the\s+)?(?:features?|predictors?|input))\b",
    re.IGNORECASE,
)
_LEAKAGE_PREVENTED_RE = re.compile(
    r"\b(?:data\s+leakage|leakage\s+prevention|"
    r"(?:feature\s+scaling|normaliz(?:ation|ing)|imputation)\s+"
    r"(?:was|were)\s+(?:performed|applied|fitted|computed)\s+"
    r"(?:only\s+)?(?:on\s+the\s+)?(?:training\s+(?:set|data)|within\s+cross[\s-]?validation)|"
    r"fitted\s+(?:only\s+)?on\s+(?:the\s+)?train(?:ing)?\s+(?:set|data)|"
    r"scaler\s+(?:was|were)\s+fit\s+on\s+train(?:ing)|"
    r"pipeline\s+(?:ensures?|prevented?)\s+(?:data\s+)?leakage)\b",
    re.IGNORECASE,
)


def validate_data_leakage_prevention(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ML preprocessing without data leakage prevention disclosure.

    Emits ``missing-data-leakage-check`` (moderate) when feature engineering
    or preprocessing is described without confirming that transformations were
    fitted only on training data.
    """
    _vid = "validate_data_leakage_prevention"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _LEAKAGE_RISK_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _LEAKAGE_PREVENTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-data-leakage-check",
                severity="moderate",
                message=(
                    "Feature engineering or preprocessing is described but it is "
                    "not confirmed that transformations were fitted only on training "
                    "data. Clarify leakage prevention to ensure valid test-set evaluation."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 315 – validate_ml_uncertainty_quantification
# ---------------------------------------------------------------------------

_ML_PREDICTION_TRIGGER_RE = re.compile(
    r"\b(?:machine\s+learning|predictive\s+(?:model|algorithm)|"
    r"deep\s+learning|neural\s+network|ensemble\s+method|"
    r"model\s+(?:prediction|output|forecast))\b",
    re.IGNORECASE,
)
_ML_UNCERTAINTY_RE = re.compile(
    r"\b(?:confidence\s+intervals?\s+(?:for\s+)?(?:the\s+)?(?:prediction|estimate)|"
    r"prediction\s+intervals?|credible\s+intervals?|"
    r"uncertainty\s+(?:quantification|estimation|in\s+predictions?)|"
    r"bootstrap\s+(?:confidence\s+)?intervals?\s+for|"
    r"calibrat(?:ion|ed)\s+(?:probability|uncertainty)|"
    r"conformal\s+prediction|Platt\s+scaling|"
    r"standard\s+error\s+of\s+(?:the\s+)?prediction)\b",
    re.IGNORECASE,
)


def validate_ml_uncertainty_quantification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ML models without uncertainty quantification.

    Emits ``missing-ml-uncertainty`` (minor) when a machine learning model
    produces predictions but no uncertainty or confidence estimate is
    reported.
    """
    _vid = "validate_ml_uncertainty_quantification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ML_PREDICTION_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ML_UNCERTAINTY_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-ml-uncertainty",
                severity="minor",
                message=(
                    "A machine learning model produces predictions but no uncertainty "
                    "quantification (e.g., confidence intervals, prediction intervals, "
                    "calibration) is reported. Include uncertainty estimates to support "
                    "evidence-based interpretation."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 316 – validate_class_imbalance_handling
# ---------------------------------------------------------------------------

_CLASS_IMBALANCE_TRIGGER_RE = re.compile(
    r"\b(?:class\s+imbalance|imbalanced\s+(?:dataset|data|classes?)|"
    r"imbalanced\s+class(?:es)?|minority\s+class|majority\s+class|"
    r"class\s+distribution\s+was\s+(?:skewed|unequal|unbalanced)|"
    r"(?:\d+)\s*:\s*(?:\d+)\s+class\s+ratio|"
    r"rare\s+(?:class|outcome|event)\s+(?:with|of|in))\b",
    re.IGNORECASE,
)
_IMBALANCE_ADDRESSED_RE = re.compile(
    r"\b(?:SMOTE\b|oversampling|undersampling|class\s+weight(?:ing)?|"
    r"weighted\s+loss|cost[\s-]sensitive|balanced\s+class\s+weight|"
    r"stratified\s+(?:sampling|split|k[\s-]?fold)|"
    r"resampling\s+(?:strategy|method)|"
    r"synthetic\s+(?:minority|samples?)|"
    r"ADASYN\b|BorderlineSMOTE\b)\b",
    re.IGNORECASE,
)


def validate_class_imbalance_handling(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag class imbalance acknowledgement without mitigation disclosure.

    Emits ``missing-class-imbalance-handling`` (minor) when class imbalance
    is noted but no handling strategy is described.
    """
    _vid = "validate_class_imbalance_handling"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _CLASS_IMBALANCE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _IMBALANCE_ADDRESSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-class-imbalance-handling",
                severity="minor",
                message=(
                    "Class imbalance is noted but no handling strategy is described. "
                    "Report whether oversampling, undersampling, class weighting, or "
                    "stratified sampling was used to address the imbalance."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 317 – validate_model_calibration_reporting
# ---------------------------------------------------------------------------

_PROBABILISTIC_MODEL_TRIGGER_RE = re.compile(
    r"\b(?:probability\s+(?:estimates?|scores?|predictions?|outputs?)|"
    r"predicted\s+probability|predicted\s+probabilities|"
    r"logistic\s+regression|naive\s+Bayes|probabilistic\s+(?:classifier|model)|"
    r"ROC\s+curve|AUC[\s-]ROC|area\s+under\s+(?:the\s+)?(?:ROC\s+)?curve)\b",
    re.IGNORECASE,
)
_CALIBRATION_REPORTED_RE = re.compile(
    r"\b(?:calibrat(?:ion|ed|ing)\s+(?:the\s+)?(?:model|classifier|probabilities?)|"
    r"calibration\s+(?:curve|plot|error|metric)|"
    r"Brier\s+score|reliability\s+diagram|"
    r"Platt\s+scal(?:ing|ed)|isotonic\s+regression\s+calibrat|"
    r"expected\s+calibration\s+error|ECE\b|MCE\b)\b",
    re.IGNORECASE,
)


def validate_model_calibration_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag probabilistic models without calibration assessment.

    Emits ``missing-model-calibration`` (minor) when probability estimates
    are produced but calibration is not assessed or reported.
    """
    _vid = "validate_model_calibration_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _PROBABILISTIC_MODEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _CALIBRATION_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-model-calibration",
                severity="minor",
                message=(
                    "Probability estimates are produced but model calibration is not "
                    "assessed. Report calibration metrics (e.g., Brier score, "
                    "calibration curve) to validate probability accuracy."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 318 – validate_fairness_metric_reporting
# ---------------------------------------------------------------------------

_FAIRNESS_CONTEXT_TRIGGER_RE = re.compile(
    r"\b(?:sensitive\s+attribute|protected\s+(?:attribute|group|characteristic)|"
    r"demographic\s+(?:group|parity|equity)|"
    r"racial|ethnic|gender|sex[\s,]|age\s+group|socioeconomic|"
    r"disparity\s+(?:in|across|between)|"
    r"disparate\s+impact|equal(?:ised?)?\s+odds|"
    r"algorithmic\s+(?:bias|fairness))\b",
    re.IGNORECASE,
)
_FAIRNESS_METRICS_RE = re.compile(
    r"\b(?:demographic\s+parity|equalised?\s+odds|equal\s+opportunity|"
    r"predictive\s+parity|individual\s+fairness|"
    r"disparate\s+impact\s+ratio|fairness\s+metric|"
    r"false\s+positive\s+rate\s+(?:by|across|for)\s+(?:group|subgroup|race|gender)|"
    r"subgroup\s+performance|performance\s+(?:gap|difference)\s+across\s+groups?|"
    r"bias\s+audit)\b",
    re.IGNORECASE,
)


def validate_fairness_metric_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag models involving sensitive attributes without fairness metrics.

    Emits ``missing-fairness-metrics`` (minor) when a model involves
    sensitive/protected attributes but fairness metrics are not reported.
    """
    _vid = "validate_fairness_metric_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _FAIRNESS_CONTEXT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FAIRNESS_METRICS_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-fairness-metrics",
                severity="minor",
                message=(
                    "The model involves sensitive or protected attributes but no "
                    "fairness metrics (e.g., demographic parity, equalised odds) are "
                    "reported. Include subgroup performance analysis."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 319 – validate_transfer_learning_disclosure
# ---------------------------------------------------------------------------

_TRANSFER_LEARNING_TRIGGER_RE = re.compile(
    r"\b(?:transfer\s+learning|fine[\s-]tun(?:ing|ed)|"
    r"pre[\s-]trained\s+(?:model|network|weights?)|"
    r"pretrained\s+(?:model|network|weights?)|"
    r"ImageNet\s+weights?|BERT|GPT|foundation\s+model|"
    r"domain\s+adaptation)\b",
    re.IGNORECASE,
)
_TRANSFER_DISCLOSED_RE = re.compile(
    r"\b(?:pre[\s-]trained\s+on\s+|pretrained\s+on\s+|"
    r"fine[\s-]tuned\s+(?:on|from|using)|"
    r"source\s+domain|target\s+domain|"
    r"frozen\s+(?:layers?|weights?)|"
    r"layers?\s+(?:were\s+)?(?:frozen|unfrozen|fine[\s-]tuned)|"
    r"(?:ImageNet|BERT|GPT|ResNet|VGG)\s+(?:pre[\s-])?trained\s+weights?|"
    r"checkpoint\s+(?:from|using)|original\s+(?:training\s+)?dataset\s+(?:was|is))\b",
    re.IGNORECASE,
)


def validate_transfer_learning_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag transfer learning or fine-tuning without source model disclosure.

    Emits ``missing-transfer-learning-disclosure`` (minor) when transfer
    learning or fine-tuning is used but the source model and adaptation
    strategy are not disclosed.
    """
    _vid = "validate_transfer_learning_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _TRANSFER_LEARNING_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TRANSFER_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-transfer-learning-disclosure",
                severity="minor",
                message=(
                    "Transfer learning or fine-tuning is used but the source model "
                    "and adaptation strategy are not disclosed. Specify the pre-trained "
                    "model, its training data, and which layers were fine-tuned."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 320 – validate_cross_validation_strategy
# ---------------------------------------------------------------------------

_CV_TRIGGER_RE = re.compile(
    r"\b(?:cross[\s-]?validat(?:ion|ed|ing)|"
    r"k[\s-]fold|leave[\s-]one[\s-]out\s+cross|"
    r"repeated\s+cross[\s-]?validat(?:ion|ed|ing)|"
    r"nested\s+cross[\s-]?validat(?:ion|ed|ing)|"
    r"cross[\s-]?validat(?:ion|ed)\s+(?:accuracy|performance|error|AUC))\b",
    re.IGNORECASE,
)
_CV_STRATEGY_DESCRIBED_RE = re.compile(
    r"\b(?:(?:\d+)[\s-]fold\s+cross[\s-]?validat(?:ion|ed)|"
    r"leave[\s-]one[\s-]out\s+cross[\s-]?validat(?:ion|ed)|"
    r"stratified\s+(?:k[\s-]fold|cross[\s-]?validat(?:ion|ed))|"
    r"repeated\s+(?:\d+)[\s-]fold|nested\s+cross[\s-]?validat(?:ion|ed)|"
    r"time[\s-]series\s+cross[\s-]?validat(?:ion|ed)|"
    r"blocked\s+cross[\s-]?validat(?:ion|ed)|"
    r"group[\s-]k[\s-]fold|GroupKFold\b|TimeSeriesSplit\b)\b",
    re.IGNORECASE,
)


def validate_cross_validation_strategy(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag cross-validation use without strategy disclosure.

    Emits ``missing-cv-strategy`` (minor) when cross-validation is used but
    the specific strategy (e.g., 5-fold, stratified) is not described.
    """
    _vid = "validate_cross_validation_strategy"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _CV_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _CV_STRATEGY_DESCRIBED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cv-strategy",
                severity="minor",
                message=(
                    "Cross-validation is used but the strategy is not described. "
                    "Specify the type (e.g., 5-fold, stratified k-fold, leave-one-out) "
                    "and any special considerations (e.g., temporal ordering, grouping)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 321 – validate_text_preprocessing_disclosure
# ---------------------------------------------------------------------------

_TEXT_ANALYSIS_TRIGGER_RE = re.compile(
    r"\b(?:text\s+(?:analysis|mining|classification|categorization)|"
    r"natural\s+language\s+processing|NLP\b|"
    r"corpus\b|document[\s-]term\s+matrix|"
    r"bag[\s-]of[\s-]words|TF[\s-]IDF\b|n[\s-]gram|"
    r"tokeniz(?:ation|ing|ed)|lemmatiz(?:ation|ing|ed)|stemm(?:ing|ed))\b",
    re.IGNORECASE,
)
_TEXT_PREPROCESS_DISCLOSED_RE = re.compile(
    r"\b(?:tokeniz(?:ation|ing|ed)|lemmatiz(?:ation|ing|ed)|stemm(?:ing|ed)|"
    r"stop[\s-]?word\s+removal|lowercas(?:ing|ed)|"
    r"punctuation\s+(?:removal|stripped?)|"
    r"text\s+(?:cleaning|normaliz(?:ation|ing|ed)|preprocessing)|"
    r"preprocessed?\s+(?:the\s+)?(?:text|corpus|documents?))\b",
    re.IGNORECASE,
)


def validate_text_preprocessing_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag NLP/text-analysis studies without preprocessing disclosure.

    Emits ``missing-text-preprocessing-disclosure`` (minor) when text
    analysis methods are used but the preprocessing pipeline is not
    described.
    """
    _vid = "validate_text_preprocessing_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _TEXT_ANALYSIS_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TEXT_PREPROCESS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-text-preprocessing-disclosure",
                severity="minor",
                message=(
                    "Text analysis methods are used but the preprocessing pipeline "
                    "is not described. Report tokenization, stemming/lemmatization, "
                    "stop-word removal, and normalization steps."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 322 – validate_word_embedding_details
# ---------------------------------------------------------------------------

_WORD_EMBED_TRIGGER_RE = re.compile(
    r"\b(?:word\s+embedding|word2vec|Word2Vec|GloVe|fastText|FastText|"
    r"word\s+vector|distributed\s+representation|"
    r"sentence\s+embedding|document\s+embedding|"
    r"BERT\s+embedding|contextual\s+embedding|"
    r"dense\s+(?:word|token)\s+representation)\b",
    re.IGNORECASE,
)
_EMBED_DETAILS_DISCLOSED_RE = re.compile(
    r"\b(?:(?:pre[\s-])?trained\s+(?:on|using|from)\s+|"
    r"embedding\s+(?:dimension|size|layer)\s+(?:of\s+)?(?:\d+)|"
    r"vector\s+(?:dimension|size)\s+(?:of\s+)?(?:\d+)|"
    r"(?:\d+)[\s-]dimensional\s+(?:word\s+)?(?:embedding|vector)|"
    r"vocabulary\s+size|context\s+window\s+(?:of\s+)?(?:\d+)|"
    r"pretrained\s+(?:on|from)\s+)\b",
    re.IGNORECASE,
)


def validate_word_embedding_details(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag word embedding use without sufficient methodological detail.

    Emits ``missing-word-embedding-details`` (minor) when word embeddings
    are used but embedding dimensionality, training corpus, or model source
    is not disclosed.
    """
    _vid = "validate_word_embedding_details"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _WORD_EMBED_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _EMBED_DETAILS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-word-embedding-details",
                severity="minor",
                message=(
                    "Word embeddings are used but embedding dimensionality, training "
                    "corpus, or model source is not disclosed. Report the embedding "
                    "model, its training data, and vector dimensions."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 323 – validate_topic_model_parameter_disclosure
# ---------------------------------------------------------------------------

_TOPIC_MODEL_TRIGGER_RE = re.compile(
    r"\b(?:topic\s+model(?:l?ing)?|latent\s+Dirichlet\s+allocation|LDA\b|"
    r"non[\s-]negative\s+matrix\s+factorization|NMF\b|"
    r"probabilistic\s+topic\s+model|"
    r"correlated\s+topic\s+model|structural\s+topic\s+model|"
    r"biterm\s+topic\s+model)\b",
    re.IGNORECASE,
)
_TOPIC_PARAMS_DISCLOSED_RE = re.compile(
    r"\b(?:number\s+of\s+topics?\s+(?:was|were|set|chosen|selected|=)\s*(?:\d+)|"
    r"(?:\d+)\s+topics?\s+(?:were|was)\s+(?:selected|identified|used|extracted)|"
    r"alpha\s*=\s*(?:\d)|"
    r"beta\s*=\s*(?:\d)|"
    r"topic\s+coherence|perplexity\s+(?:score|was|=)|"
    r"optimal\s+number\s+of\s+topics?|"
    r"hyperparameter\s+(?:alpha|beta|eta))\b",
    re.IGNORECASE,
)


def validate_topic_model_parameter_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag topic models without parameter disclosure.

    Emits ``missing-topic-model-parameters`` (minor) when topic modelling
    is used but the number of topics and key hyperparameters are not
    reported.
    """
    _vid = "validate_topic_model_parameter_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _TOPIC_MODEL_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TOPIC_PARAMS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-topic-model-parameters",
                severity="minor",
                message=(
                    "Topic modelling is used but the number of topics and key "
                    "hyperparameters (e.g., alpha, beta) are not reported. "
                    "Disclose model parameters and the selection rationale."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 324 – validate_inter_annotator_agreement
# ---------------------------------------------------------------------------

_ANNOTATION_TRIGGER_RE = re.compile(
    r"\b(?:manual\s+(?:annotation|coding|labelling|classification)|"
    r"human\s+(?:annotation|coding|labelling|rater|judge)|"
    r"content\s+analysis|coded?\s+by\s+(?:\w+\s+){0,3}(?:coders?|raters?|annotators?)|"
    r"two\s+(?:independent\s+)?(?:coders?|raters?|annotators?)|"
    r"inter[\s-]?rater|inter[\s-]?annotator)\b",
    re.IGNORECASE,
)
_IAA_REPORTED_RE = re.compile(
    r"\b(?:inter[\s-]?(?:rater|annotator|coder)\s+(?:agreement|reliability)|"
    r"Cohen.s\s+kappa|Fleiss.s\s+kappa|Krippendorff.s\s+alpha|"
    r"kappa\s*=\s*(?:0\.\d+|\d+)|"
    r"agreement\s+(?:was|of)\s+(?:0\.\d+|\d+%)|"
    r"intraclass\s+correlation\s+coefficient|ICC\b|"
    r"percent(?:age)?\s+agreement\s*=\s*(?:\d+%|0\.\d+))\b",
    re.IGNORECASE,
)


def validate_inter_annotator_agreement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag human annotation studies without inter-annotator agreement.

    Emits ``missing-inter-annotator-agreement`` (moderate) when human
    annotation/coding is performed by multiple raters but no
    inter-annotator agreement metric is reported.
    """
    _vid = "validate_inter_annotator_agreement"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ANNOTATION_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _IAA_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-inter-annotator-agreement",
                severity="moderate",
                message=(
                    "Human annotation is performed but no inter-annotator agreement "
                    "metric is reported. Report Cohen's kappa, Krippendorff's alpha, "
                    "or percentage agreement to establish annotation reliability."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 325 – validate_sentiment_lexicon_disclosure
# ---------------------------------------------------------------------------

_SENTIMENT_TRIGGER_RE = re.compile(
    r"\b(?:sentiment\s+(?:analysis|classification|scoring|detection)|"
    r"sentiment\s+(?:of\s+the\s+)?(?:text|data|tweets?|reviews?|documents?)|"
    r"positive\s+and\s+negative\s+sentiment|opinion\s+mining|"
    r"emotional\s+tone|polarity\s+(?:classification|scoring))\b",
    re.IGNORECASE,
)
_SENTIMENT_LEXICON_DISCLOSED_RE = re.compile(
    r"\b(?:VADER\b|AFINN\b|SentiWordNet\b|LIWC\b|"
    r"sentiment\s+lexicon|lexicon[\s-]based\s+(?:approach|method|sentiment)|"
    r"sentiment\s+dictionary|opinion\s+lexicon|"
    r"SentiStrength\b|TextBlob\b|"
    r"fine[\s-]tuned\s+(?:for\s+)?sentiment|"
    r"trained\s+(?:sentiment\s+)?classifier\s+(?:on|using))\b",
    re.IGNORECASE,
)


def validate_sentiment_lexicon_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag sentiment analysis without lexicon or model disclosure.

    Emits ``missing-sentiment-lexicon`` (minor) when sentiment analysis
    is performed but the lexicon or model used is not identified.
    """
    _vid = "validate_sentiment_lexicon_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SENTIMENT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SENTIMENT_LEXICON_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-sentiment-lexicon",
                severity="minor",
                message=(
                    "Sentiment analysis is performed but the lexicon or model used "
                    "(e.g., VADER, AFINN, fine-tuned classifier) is not identified. "
                    "Disclose the sentiment scoring approach to support reproducibility."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 326 – validate_mri_acquisition_parameters
# ---------------------------------------------------------------------------

_MRI_TRIGGER_RE = re.compile(
    r"\b(?:MRI\b|magnetic\s+resonance\s+imaging|"
    r"(?:structural|anatomical|diffusion|functional)\s+MRI|"
    r"fMRI\b|DTI\b|DWI\b|BOLD\b|T1[\s-]?weighted|T2[\s-]?weighted|"
    r"scanner\s+(?:field\s+)?strength|Tesla\b|1\.5T\b|3T\b|7T\b)\b",
    re.IGNORECASE,
)
_MRI_PARAMS_DISCLOSED_RE = re.compile(
    r"\b(?:TR\s*=\s*(?:\d)|TE\s*=\s*(?:\d)|flip\s+angle\s*=\s*(?:\d)|"
    r"voxel\s+size|slice\s+thickness|repetition\s+time|echo\s+time|"
    r"field\s+of\s+view|matrix\s+size|acquisition\s+(?:parameters?|protocol)|"
    r"(?:1\.5|3|7)[\s-]?T(?:esla)?\s+(?:scanner|MRI)|"
    r"(?:Siemens|Philips|GE)\s+(?:scanner|MRI|Trio|Prisma|Skyra))\b",
    re.IGNORECASE,
)


def validate_mri_acquisition_parameters(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag MRI studies without acquisition parameter disclosure.

    Emits ``missing-mri-acquisition-parameters`` (minor) when MRI data are
    collected but acquisition parameters (TR, TE, field strength, voxel size)
    are not reported.
    """
    _vid = "validate_mri_acquisition_parameters"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MRI_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _MRI_PARAMS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-mri-acquisition-parameters",
                severity="minor",
                message=(
                    "MRI data are used but acquisition parameters (e.g., TR, TE, "
                    "field strength, voxel size) are not reported. Disclose the "
                    "acquisition protocol to support reproducibility."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 327 – validate_fmri_preprocessing_pipeline
# ---------------------------------------------------------------------------

_FMRI_TRIGGER_RE = re.compile(
    r"\b(?:fMRI\b|functional\s+MRI|functional\s+magnetic\s+resonance|"
    r"BOLD\s+signal|task[\s-]based\s+fMRI|resting[\s-]state\s+fMRI|"
    r"rs[\s-]?fMRI\b|brain\s+activation\s+(?:map|pattern))\b",
    re.IGNORECASE,
)
_FMRI_PREPROCESS_DISCLOSED_RE = re.compile(
    r"\b(?:motion\s+(?:correction|scrubbing|realignment)|"
    r"slice[\s-]timing\s+correction|"
    r"spatial\s+(?:smoothing|normalization)|"
    r"temporal\s+filtering|high[\s-]pass\s+filter|"
    r"FSL\b|SPM\b|FreeSurfer\b|ANTs\b|fMRIPrep\b|AFNI\b|"
    r"preprocessing\s+(?:pipeline|steps?)\s+(?:included|consisted|was|were))\b",
    re.IGNORECASE,
)


def validate_fmri_preprocessing_pipeline(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag fMRI studies without preprocessing pipeline disclosure.

    Emits ``missing-fmri-preprocessing-pipeline`` (minor) when fMRI data
    are analysed but the preprocessing pipeline is not described.
    """
    _vid = "validate_fmri_preprocessing_pipeline"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _FMRI_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FMRI_PREPROCESS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-fmri-preprocessing-pipeline",
                severity="minor",
                message=(
                    "fMRI data are analysed but the preprocessing pipeline "
                    "(e.g., motion correction, slice-timing, spatial smoothing) "
                    "is not described. Disclose all preprocessing steps and software used."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 328 – validate_neuroimaging_atlas_disclosure
# ---------------------------------------------------------------------------

_ATLAS_TRIGGER_RE = re.compile(
    r"\b(?:brain\s+region|anatomical\s+region|cortical\s+region|"
    r"region\s+of\s+interest|ROI\b|"
    r"prefrontal\s+cortex|amygdala|hippocampus|insula|"
    r"parcellation|brain\s+atlas|neuroimaging\s+coordinates|"
    r"MNI\s+(?:coordinates?|space)|Talairach)\b",
    re.IGNORECASE,
)
_ATLAS_DISCLOSED_RE = re.compile(
    r"\b(?:MNI\s+(?:152\s+)?(?:space|template|atlas|coordinates?)|"
    r"Talairach\s+(?:space|atlas|coordinates?)|"
    r"AAL\s+atlas|Desikan[\s-]Killiany|Brodmann\s+area|"
    r"Harvard[\s-]Oxford\s+atlas|Destrieux|Schaefer\s+parcellation|"
    r"automated\s+anatomical\s+labeling|"
    r"atlas\s+(?:was|used|registered)|"
    r"parcellated\s+(?:using|into|with))\b",
    re.IGNORECASE,
)


def validate_neuroimaging_atlas_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag neuroimaging studies without atlas/parcellation disclosure.

    Emits ``missing-neuroimaging-atlas`` (minor) when brain regions are
    reported but the atlas or parcellation scheme used is not named.
    """
    _vid = "validate_neuroimaging_atlas_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ATLAS_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ATLAS_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-neuroimaging-atlas",
                severity="minor",
                message=(
                    "Brain regions are reported but the atlas or parcellation scheme "
                    "(e.g., MNI152, AAL, Desikan-Killiany) is not named. "
                    "Identify the atlas and coordinate space used."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 329 – validate_multiple_comparisons_neuroimaging
# ---------------------------------------------------------------------------

_NEURO_MC_TRIGGER_RE = re.compile(
    r"\b(?:whole[\s-]brain\s+(?:analysis|contrast|comparison)|"
    r"voxelwise|voxel[\s-]wise|mass[\s-]univariate|"
    r"GLM\s+(?:analysis|contrast)|"
    r"cluster\s+(?:analysis|comparison|contrast)|"
    r"statistical\s+parametric\s+(?:map|mapping)|SPM\s+contrast)\b",
    re.IGNORECASE,
)
_NEURO_CORRECTION_DISCLOSED_RE = re.compile(
    r"\b(?:family[\s-]wise\s+error|FWE\b|false\s+discovery\s+rate|FDR\b|"
    r"Bonferroni\s+correct(?:ion|ed)|"
    r"cluster[\s-]level\s+(?:threshold|correct(?:ion|ed))|"
    r"GRF\s+(?:correction|theory)|Gaussian\s+random\s+field|"
    r"permutation[\s-]based\s+(?:correction|threshold)|"
    r"voxel[\s-]level\s+threshold\s+of\s+p\s*<\s*0\.0[0-9]+|"
    r"corrected\s+(?:p[\s-]value|threshold)\s+(?:of\s+)?p\s*<\s*0\.0[0-9]+)\b",
    re.IGNORECASE,
)


def validate_multiple_comparisons_neuroimaging(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag neuroimaging voxelwise analyses without multiple comparisons correction.

    Emits ``missing-neuroimaging-multiple-comparisons`` (moderate) when
    voxelwise or whole-brain analysis is performed but no multiple
    comparisons correction is described.
    """
    _vid = "validate_multiple_comparisons_neuroimaging"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _NEURO_MC_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _NEURO_CORRECTION_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-neuroimaging-multiple-comparisons",
                severity="moderate",
                message=(
                    "Voxelwise or whole-brain analysis is performed but no multiple "
                    "comparisons correction is described. Report the correction method "
                    "(e.g., FWE, FDR, cluster-level GRF threshold)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 330 – validate_roi_definition_disclosure
# ---------------------------------------------------------------------------

_ROI_TRIGGER_RE = re.compile(
    r"\b(?:region[\s-]of[\s-]interest|ROI[\s-]based|ROI\s+analysis|"
    r"ROI\s+mask|ROI\s+approach|ROI\s+(?:was|were)\s+defined|"
    r"selected\s+ROI|a\s+priori\s+ROI)\b",
    re.IGNORECASE,
)
_ROI_DEFINED_RE = re.compile(
    r"\b(?:ROI\s+(?:was|were)\s+(?:defined|delineated|drawn|extracted|created|"
    r"segmented|identified)\s+(?:\w+\s+)?(?:using|from|based\s+on|as)|"
    r"anatomically\s+defined\s+(?:ROI|region)|"
    r"functionally\s+defined\s+(?:ROI|region)|"
    r"ROI\s+mask\s+(?:was|were)\s+(?:obtained|created|derived)\s+from|"
    r"coordinates?\s+(?:were\s+)?(?:taken|extracted)\s+from|"
    r"sphere\s+(?:of\s+)?(?:\d+)\s*mm\s+radius\s+around)\b",
    re.IGNORECASE,
)


def validate_roi_definition_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ROI analyses without ROI definition disclosure.

    Emits ``missing-roi-definition`` (minor) when ROI-based analysis is
    performed but how the ROI was defined or selected is not described.
    """
    _vid = "validate_roi_definition_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ROI_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ROI_DEFINED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-roi-definition",
                severity="minor",
                message=(
                    "ROI-based analysis is performed but how the region of interest "
                    "was defined is not described. Specify whether ROIs were anatomically "
                    "defined, functionally defined, or derived from atlas coordinates."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 331 – validate_rna_seq_normalization_disclosure
# ---------------------------------------------------------------------------

_RNA_SEQ_TRIGGER_RE = re.compile(
    r"\b(?:RNA[\s-]?seq\b|RNA\s+sequencing|transcriptome\s+sequencing|"
    r"differential\s+(?:gene\s+)?expression|DESeq|edgeR|"
    r"read\s+counts?|gene\s+counts?|count\s+matrix|"
    r"CPM\b|RPKM\b|FPKM\b|TPM\b)\b",
    re.IGNORECASE,
)
_RNA_NORM_DISCLOSED_RE = re.compile(
    r"\b(?:normaliz(?:ation|ed|ing)\s+(?:using|via|with)|"
    r"DESeq2?\s+normaliz|"
    r"TMM\s+normaliz|"
    r"upper\s+quartile\s+normaliz|"
    r"library\s+size\s+normaliz|"
    r"CPM\s+normaliz|"
    r"voom\s+transform|"
    r"counts\s+were\s+normaliz)\b",
    re.IGNORECASE,
)


def validate_rna_seq_normalization_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RNA-seq analyses without normalization method disclosure.

    Emits ``missing-rna-seq-normalization`` (minor) when RNA-seq data are
    analysed but the normalization method is not reported.
    """
    _vid = "validate_rna_seq_normalization_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _RNA_SEQ_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _RNA_NORM_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-rna-seq-normalization",
                severity="minor",
                message=(
                    "RNA-seq data are analysed but the normalization method is not "
                    "reported. Specify the normalization approach (e.g., DESeq2 size "
                    "factors, TMM, CPM) to support reproducibility."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 332 – validate_batch_effect_correction
# ---------------------------------------------------------------------------

_BATCH_EFFECT_TRIGGER_RE = re.compile(
    r"\b(?:batch\s+effect|batch\s+correction|"
    r"multiple\s+(?:batches?|cohorts?|runs?|plates?|sites?)\s+"
    r"(?:were|was)\s+(?:combined|merged|processed|analysed)|"
    r"samples?\s+(?:were\s+|was\s+)?(?:collected|processed|run)\s+"
    r"(?:in|across)\s+(?:multiple\s+)?(?:batches?|runs?|plates?)|"
    r"technical\s+variability\s+(?:between|across)\s+(?:batches?|runs?))\b",
    re.IGNORECASE,
)
_BATCH_CORRECTED_RE = re.compile(
    r"\b(?:batch\s+(?:correction|effect)\s+(?:was|were)\s+"
    r"(?:corrected|addressed|removed|accounted\s+for)|"
    r"ComBat\b|limma\s+removeBatchEffect|"
    r"batch\s+covariate|included\s+batch\s+as\s+a\s+covariate|"
    r"batch[\s-]corrected|harmoniz(?:ation|ed|ing)\s+(?:the\s+)?(?:data|batches?))\b",
    re.IGNORECASE,
)


def validate_batch_effect_correction(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag multi-batch studies without batch effect correction disclosure.

    Emits ``missing-batch-effect-correction`` (minor) when multiple batches
    or processing runs are described but batch correction is not reported.
    """
    _vid = "validate_batch_effect_correction"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _BATCH_EFFECT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _BATCH_CORRECTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-batch-effect-correction",
                severity="minor",
                message=(
                    "Multiple processing batches are described but batch effect "
                    "correction is not reported. Disclose whether batch correction "
                    "(e.g., ComBat, batch covariate) was applied."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 333 – validate_multiple_testing_genomics
# ---------------------------------------------------------------------------

_GENOMIC_TESTING_TRIGGER_RE = re.compile(
    r"\b(?:genome[\s-]wide\s+(?:association|analysis|study|GWAS)|"
    r"GWAS\b|"
    r"differential\s+(?:gene\s+)?expression\s+(?:analysis|testing)|"
    r"(?:\d+(?:,\d+)*)\s+(?:SNPs?|variants?|genes?)\s+(?:were\s+)?tested|"
    r"multiple\s+(?:genetic|genomic|gene)\s+(?:variants?|loci|tests?))\b",
    re.IGNORECASE,
)
_GENOMIC_CORRECTION_RE = re.compile(
    r"\b(?:Bonferroni\s+correct(?:ion|ed)|"
    r"false\s+discovery\s+rate|FDR\b|"
    r"genome[\s-]wide\s+significance\s+(?:threshold|level)|"
    r"p\s*<\s*5\s*[×x]\s*10[\s-]?(?:\^?\s*)?[-−]?8|"
    r"q[\s-]?value|Benjamini[\s-]Hochberg|"
    r"adjusted\s+p[\s-]?value)\b",
    re.IGNORECASE,
)


def validate_multiple_testing_genomics(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag genomics studies without multiple testing correction.

    Emits ``missing-genomics-multiple-testing`` (moderate) when large-scale
    genomic testing (GWAS, DEA) is performed but no multiple testing
    correction is described.
    """
    _vid = "validate_multiple_testing_genomics"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _GENOMIC_TESTING_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _GENOMIC_CORRECTION_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-genomics-multiple-testing",
                severity="moderate",
                message=(
                    "Large-scale genomic testing is performed but no multiple testing "
                    "correction is described. Report the correction method "
                    "(e.g., FDR q-value, genome-wide significance threshold p < 5×10⁻⁸)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 334 – validate_pathway_enrichment_method
# ---------------------------------------------------------------------------

_ENRICHMENT_TRIGGER_RE = re.compile(
    r"\b(?:pathway\s+(?:enrichment|analysis)|"
    r"gene\s+set\s+(?:enrichment|analysis|testing)|"
    r"GSEA\b|GO\s+(?:enrichment|term|analysis)|"
    r"gene\s+ontology\s+(?:analysis|enrichment|term)|"
    r"overrepresentation\s+analysis|"
    r"Reactome\s+(?:pathway|analysis)|KEGG\s+pathway)\b",
    re.IGNORECASE,
)
_ENRICHMENT_METHOD_DISCLOSED_RE = re.compile(
    r"\b(?:GSEA\b\s+(?:was|were|using|with|v\d)|"
    r"Fisher.s\s+exact\s+test\s+for\s+enrichment|"
    r"hypergeometric\s+test|"
    r"gene\s+set\s+enrichment\s+(?:analysis\s+was|using\s+)|"
    r"clusterProfiler\b|fgsea\b|EnrichmentMap\b|"
    r"background\s+gene\s+set|universe\s+(?:set|of\s+genes?)|"
    r"enrichment\s+(?:was\s+)?tested\s+(?:using|via|with))\b",
    re.IGNORECASE,
)


def validate_pathway_enrichment_method(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag pathway enrichment analyses without method disclosure.

    Emits ``missing-pathway-enrichment-method`` (minor) when pathway or gene
    set enrichment is reported but the analysis method is not described.
    """
    _vid = "validate_pathway_enrichment_method"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _ENRICHMENT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _ENRICHMENT_METHOD_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-pathway-enrichment-method",
                severity="minor",
                message=(
                    "Pathway or gene set enrichment is reported but the analysis "
                    "method is not described. Specify the tool and statistical test "
                    "(e.g., GSEA, Fisher's exact, hypergeometric test, background set)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 335 – validate_genome_reference_disclosure
# ---------------------------------------------------------------------------

_GENOME_REF_TRIGGER_RE = re.compile(
    r"\b(?:genome\s+(?:assembly|reference|alignment|mapping|build)|"
    r"reference\s+genome|reads?\s+(?:were\s+)?(?:aligned|mapped)\s+to|"
    r"STAR\s+(?:aligner|alignment)|BWA\b|Bowtie\b|HISAT\d?\b|"
    r"variant\s+calling|SNP\s+calling|GATK\b|samtools\b)\b",
    re.IGNORECASE,
)
_GENOME_REF_DISCLOSED_RE = re.compile(
    r"\b(?:GRCh\d+|hg\d+|GRCm\d+|mm\d+|"
    r"human\s+genome\s+(?:reference\s+)?(?:assembly\s+)?(?:GRCh|hg)\d+|"
    r"reference\s+genome\s+(?:GRCh|hg|mm|GRCm)\d+|"
    r"(?:GRCh|GRCm|hg|mm)\d+\s+(?:reference|assembly|genome|build)|"
    r"ENSEMBL\s+(?:release\s+)?\d+|gencode\s+(?:v|version\s+)\d+)\b",
    re.IGNORECASE,
)


def validate_genome_reference_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag genomic alignment studies without reference genome disclosure.

    Emits ``missing-genome-reference`` (minor) when genomic reads are
    aligned or variants are called but the reference genome assembly
    version is not stated.
    """
    _vid = "validate_genome_reference_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _GENOME_REF_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _GENOME_REF_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-genome-reference",
                severity="minor",
                message=(
                    "Genomic reads are aligned or variants are called but the "
                    "reference genome assembly version is not stated. "
                    "Report the reference genome (e.g., GRCh38, hg19) used for alignment."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 336 – validate_strobe_observational_reporting
# ---------------------------------------------------------------------------

_OBSERVATIONAL_DESIGN_TRIGGER_RE = re.compile(
    r"\b(?:cohort\s+study|case[\s-]control\s+study|cross[\s-]sectional\s+study|"
    r"prospective\s+(?:cohort|observational)|"
    r"retrospective\s+(?:cohort|case[\s-]control|observational)|"
    r"observational\s+study|longitudinal\s+observational)\b",
    re.IGNORECASE,
)
_STROBE_ELEMENTS_PRESENT_RE = re.compile(
    r"\b(?:eligibility\s+criteria|source\s+population|"
    r"exposure\s+(?:assessment|definition|measurement)|"
    r"outcome\s+(?:ascertainment|definition|assessment)|"
    r"potential\s+confounders?|effect\s+modification|"
    r"loss\s+to\s+follow[\s-]up|study\s+flow\s+diagram|"
    r"STROBE\b|STROBE\s+(?:guidelines?|checklist|statement))\b",
    re.IGNORECASE,
)


def validate_strobe_observational_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag observational studies missing key STROBE-aligned reporting elements.

    Emits ``missing-strobe-elements`` (minor) when an observational study
    is described but essential reporting elements (eligibility criteria,
    exposure assessment, confounder handling) are absent.
    """
    _vid = "validate_strobe_observational_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _OBSERVATIONAL_DESIGN_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _STROBE_ELEMENTS_PRESENT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-strobe-elements",
                severity="minor",
                message=(
                    "An observational study is described but key STROBE reporting "
                    "elements are absent (e.g., eligibility criteria, exposure "
                    "assessment, confounder handling). Follow the STROBE checklist."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 337 – validate_selection_bias_discussion
# ---------------------------------------------------------------------------

_SELECTION_BIAS_CONTEXT_RE = re.compile(
    r"\b(?:self[\s-]select(?:ion|ed)|volunteer\s+bias|healthy\s+worker\s+effect|"
    r"non[\s-]response\s+bias|response\s+rate\s+(?:was|of)\s+(?:\d+|0\.\d+)\s*%|"
    r"response\s+rate\s+(?:was\s+)?(?:below|less\s+than|under)\s+(?:80|70|60|50)\s*%|"
    r"(?:low|poor|insufficient)\s+response\s+rate|"
    r"attrition\s+(?:was|rate|of)|loss\s+to\s+follow[\s-]up\s+of\s+\d+\s*%)",
    re.IGNORECASE,
)
_SELECTION_BIAS_ADDRESSED_RE = re.compile(
    r"\b(?:selection\s+bias\s+(?:was|may|could|might|is|cannot)|"
    r"generalizability\s+(?:may|might|is|could)\s+be\s+(?:limited|affected)|"
    r"external\s+validity\s+(?:may|might|is|could)\s+be\s+(?:limited|affected)|"
    r"non[\s-]response\s+(?:bias\s+)?(?:was|may|might|could)\s+(?:be|have)|"
    r"representativeness\s+of\s+(?:the\s+)?sample|"
    r"systematic\s+differences?\s+between\s+(?:responders?|participants?))\b",
    re.IGNORECASE,
)


def validate_selection_bias_discussion(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies with potential selection bias that do not discuss it.

    Emits ``missing-selection-bias-discussion`` (minor) when indicators
    of selection bias are present (low response rate, self-selection, etc.)
    but selection bias is not discussed as a limitation.
    """
    _vid = "validate_selection_bias_discussion"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SELECTION_BIAS_CONTEXT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SELECTION_BIAS_ADDRESSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-selection-bias-discussion",
                severity="minor",
                message=(
                    "Indicators of potential selection bias are present (e.g., low "
                    "response rate, self-selection) but selection bias is not discussed "
                    "as a limitation. Address generalizability and sample representativeness."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 338 – validate_information_bias_discussion
# ---------------------------------------------------------------------------

_INFO_BIAS_TRIGGER_RE = re.compile(
    r"\b(?:self[\s-]report(?:ed)?|recall\s+(?:bias|error)|retrospective\s+report|"
    r"questionnaire[\s-]based\s+(?:exposure|assessment)|"
    r"interview[\s-]based\s+(?:exposure|outcome)|"
    r"subjective\s+(?:assessment|rating|report)|"
    r"proxy\s+(?:measure|report|respondent))\b",
    re.IGNORECASE,
)
_INFO_BIAS_ADDRESSED_RE = re.compile(
    r"\b(?:information\s+bias|recall\s+bias\s+(?:was|may|might|could)|"
    r"measurement\s+(?:error|bias)\s+(?:may|might|could|was)|"
    r"reporting\s+bias\s+(?:may|might|could|was)|"
    r"misclassification\s+(?:bias\s+)?(?:may|might|could|was)|"
    r"social\s+desirability\s+bias|acquiescence\s+bias|"
    r"objective\s+(?:measure|assessment)\s+(?:was|were)\s+(?:used|employed|obtained))\b",
    re.IGNORECASE,
)


def validate_information_bias_discussion(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag studies using self-report that do not discuss information bias.

    Emits ``missing-information-bias-discussion`` (minor) when self-report
    measures are used but recall bias or measurement error is not discussed.
    """
    _vid = "validate_information_bias_discussion"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _INFO_BIAS_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _INFO_BIAS_ADDRESSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-information-bias-discussion",
                severity="minor",
                message=(
                    "Self-report or retrospective measures are used but information "
                    "bias (e.g., recall bias, measurement error) is not discussed. "
                    "Address potential misclassification or reporting bias."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 339 – validate_dose_response_relationship
# ---------------------------------------------------------------------------

_DOSE_RESPONSE_TRIGGER_RE = re.compile(
    r"\b(?:dose[\s-]response|exposure[\s-]response|"
    r"biological\s+(?:dose|gradient)|"
    r"graded\s+(?:response|association|relationship)|"
    r"increasing\s+(?:dose|exposure|concentration)\s+"
    r"(?:was\s+)?(?:associated|correlated|linked)\s+with|"
    r"higher\s+(?:dose|exposure)\s+(?:was\s+)?associated\s+with\s+greater)\b",
    re.IGNORECASE,
)
_DOSE_RESPONSE_TESTED_RE = re.compile(
    r"\b(?:dose[\s-]response\s+(?:analysis|test(?:ing)?|relationship|trend)|"
    r"trend\s+test|test\s+for\s+(?:linear\s+)?trend|"
    r"linear\s+trend\s+(?:test|analysis)|"
    r"Cochran[\s-]Armitage\s+trend|"
    r"spline\s+(?:model|analysis)\s+for\s+(?:dose|exposure)|"
    r"restricted\s+cubic\s+spline)\b",
    re.IGNORECASE,
)


def validate_dose_response_relationship(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag dose-response claims without formal trend analysis.

    Emits ``missing-dose-response-analysis`` (minor) when a dose-response
    relationship is claimed but no formal trend test or analysis is reported.
    """
    _vid = "validate_dose_response_relationship"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _DOSE_RESPONSE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _DOSE_RESPONSE_TESTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-dose-response-analysis",
                severity="minor",
                message=(
                    "A dose-response relationship is claimed but no formal trend "
                    "test or analysis (e.g., Cochran-Armitage trend test, spline model) "
                    "is reported. Include a statistical test for trend."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 340 – validate_follow_up_rate_reporting
# ---------------------------------------------------------------------------

_FOLLOW_UP_TRIGGER_RE = re.compile(
    r"\b(?:follow[\s-]up\s+(?:period|assessments?|data|visit|measurement)|"
    r"longitudinal\s+(?:follow[\s-]up|data|study|design)|"
    r"repeated\s+(?:assessments?|measure|measurement|contact)|"
    r"retention\s+(?:rate|at\s+follow[\s-]up)|"
    r"participants?\s+(?:were\s+)?(?:re[\s-]?assessed|re[\s-]?contacted|"
    r"followed\s+for|tracked))\b",
    re.IGNORECASE,
)
_FOLLOW_UP_RATE_REPORTED_RE = re.compile(
    r"(?:\d+(?:\.\d+)?%\s+of\s+participants?\s+"
    r"(?:completed|returned|responded\s+at|attended)\s+follow[\s-]up|"
    r"\bfollow[\s-]up\s+rate\s+(?:was|of)\s+\d+\s*%|"
    r"\bretention\s+rate\s+(?:was|of)\s+\d+\s*%|"
    r"\b(?:\d+)\s+(?:of\s+)?(?:\d+)\s+participants?\s+completed\s+follow[\s-]up\b|"
    r"\battrition\s+rate\s+(?:was|of)\s+\d+\s*%)",
    re.IGNORECASE,
)


def validate_follow_up_rate_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag longitudinal studies without follow-up rate reporting.

    Emits ``missing-follow-up-rate`` (minor) when a longitudinal study
    includes follow-up assessments but the follow-up or retention rate
    is not reported.
    """
    _vid = "validate_follow_up_rate_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _FOLLOW_UP_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _FOLLOW_UP_RATE_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-follow-up-rate",
                severity="minor",
                message=(
                    "A longitudinal study with follow-up assessments is described "
                    "but the follow-up or retention rate is not reported. "
                    "Report the proportion of participants completing each follow-up."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 341 – validate_cost_effectiveness_perspective
# ---------------------------------------------------------------------------

_CEA_TRIGGER_RE = re.compile(
    r"\b(?:cost[\s-]effectiveness\s+(?:analysis|ratio|threshold|model)|"
    r"cost[\s-]utility\s+analysis|cost[\s-]benefit\s+analysis|"
    r"economic\s+evaluation|health\s+economic\s+(?:model|analysis)|"
    r"QALY\b|quality[\s-]adjusted\s+life[\s-]year|"
    r"incremental\s+cost[\s-]effectiveness|ICER\b)\b",
    re.IGNORECASE,
)
_CEA_PERSPECTIVE_DISCLOSED_RE = re.compile(
    r"\b(?:(?:health\s+)?(?:care\s+)?(?:payer|system|societal|provider|patient)\s+"
    r"perspective|perspective\s+of\s+(?:the\s+)?(?:health\s+)?(?:payer|system|society)|"
    r"analysis\s+(?:was\s+)?(?:conducted|performed|undertaken)\s+from\s+(?:a|the)\s+"
    r"(?:health|societal|payer|provider)\s+perspective|"
    r"costs?\s+(?:were\s+)?considered\s+from\s+(?:a|the)\s+perspective)\b",
    re.IGNORECASE,
)


def validate_cost_effectiveness_perspective(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag CEA studies without analytic perspective disclosure.

    Emits ``missing-cea-perspective`` (minor) when a cost-effectiveness or
    health economic analysis is performed but the analytic perspective
    (payer, societal, health system) is not stated.
    """
    _vid = "validate_cost_effectiveness_perspective"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _CEA_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _CEA_PERSPECTIVE_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cea-perspective",
                severity="minor",
                message=(
                    "A cost-effectiveness analysis is performed but the analytic "
                    "perspective (e.g., payer, health system, societal) is not stated. "
                    "Specify the perspective to clarify which costs are included."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 342 – validate_discount_rate_disclosure
# ---------------------------------------------------------------------------

_DISCOUNTING_TRIGGER_RE = re.compile(
    r"\b(?:discount(?:ing|ed|s)?\s+(?:rate|costs?|outcomes?|QALYs?|benefits?)|"
    r"(?:costs?|QALYs?|benefits?|outcomes?)\s+(?:\w+\s+){0,3}(?:were|was)\s+"
    r"discount(?:ed|ing)|"
    r"time\s+horizon\s+(?:of\s+)?(?:\d+)\s+years?)\b",
    re.IGNORECASE,
)
_DISCOUNT_RATE_DISCLOSED_RE = re.compile(
    r"\b(?:discount(?:ed|ing)?\s+(?:at\s+)?(?:\d+(?:\.\d+)?)\s*%|"
    r"annual\s+discount\s+rate\s+of\s+(?:\d+(?:\.\d+)?)\s*%|"
    r"(?:\d+(?:\.\d+)?)\s*%\s+(?:annual\s+)?discount\s+rate|"
    r"no\s+discounting\s+(?:was|were)\s+applied|"
    r"costs?\s+and\s+(?:benefits?|outcomes?|QALYs?)\s+were\s+discounted\s+at)\b",
    re.IGNORECASE,
)


def validate_discount_rate_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag health economic models without discount rate disclosure.

    Emits ``missing-discount-rate`` (minor) when future costs or outcomes
    are discounted but the discount rate is not reported.
    """
    _vid = "validate_discount_rate_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _DISCOUNTING_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _DISCOUNT_RATE_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-discount-rate",
                severity="minor",
                message=(
                    "Future costs or outcomes are discounted but the discount rate "
                    "is not reported. Specify the annual discount rate applied to "
                    "costs and outcomes."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 343 – validate_uncertainty_analysis_health_economic
# ---------------------------------------------------------------------------

_HE_UNCERTAINTY_TRIGGER_RE = re.compile(
    r"\b(?:cost[\s-]effectiveness\s+(?:analysis|model)|"
    r"health\s+economic\s+(?:model|analysis)|"
    r"decision\s+(?:analytic\s+)?(?:model|tree|analysis)|"
    r"Markov\s+model|microsimulation\s+model)\b",
    re.IGNORECASE,
)
_HE_UNCERTAINTY_REPORTED_RE = re.compile(
    r"\b(?:(?:deterministic|probabilistic)\s+sensitivity\s+analysis|"
    r"PSA\b|one[\s-]way\s+sensitivity\s+analysis|"
    r"tornado\s+(?:diagram|plot)|"
    r"Monte\s+Carlo\s+simulation|"
    r"uncertainty\s+(?:was|is)\s+(?:explored|assessed|quantified|characterised)|"
    r"cost[\s-]effectiveness\s+acceptability\s+curve|"
    r"credible\s+(?:interval|range)\s+for\s+(?:the\s+)?ICER)\b",
    re.IGNORECASE,
)


def validate_uncertainty_analysis_health_economic(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag health economic models without uncertainty analysis.

    Emits ``missing-health-economic-uncertainty`` (minor) when a health
    economic model is presented but no sensitivity or uncertainty analysis
    is reported.
    """
    _vid = "validate_uncertainty_analysis_health_economic"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _HE_UNCERTAINTY_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _HE_UNCERTAINTY_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-health-economic-uncertainty",
                severity="minor",
                message=(
                    "A health economic model is presented but no sensitivity or "
                    "uncertainty analysis is reported. Include deterministic or "
                    "probabilistic sensitivity analysis."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 344 – validate_qaly_utility_source
# ---------------------------------------------------------------------------

_QALY_TRIGGER_RE = re.compile(
    r"\b(?:QALYs?\b|quality[\s-]adjusted\s+life[\s-]year|"
    r"health\s+state\s+utility|utility\s+(?:weight|value|score)|"
    r"health\s+utility\s+(?:index|measure)|"
    r"EQ[\s-]?5D\b|SF[\s-]?6D\b|HUI\b)",
    re.IGNORECASE,
)
_QALY_SOURCE_DISCLOSED_RE = re.compile(
    r"\b(?:utility\s+(?:values?|weights?)\s+(?:were|was)\s+"
    r"(?:obtained|derived|sourced|taken|estimated)\s+(?:from|using)|"
    r"EQ[\s-]?5D[\s-]?(?:3L|5L)?\s+(?:was|were)\s+(?:used|administered)|"
    r"utility\s+(?:elicitation|measurement)\s+(?:method|approach)|"
    r"time\s+trade[\s-]off|standard\s+gamble\s+(?:was\s+)?(?:used|elicited)|"
    r"preference[\s-]based\s+(?:measure|instrument|utility))\b",
    re.IGNORECASE,
)


def validate_qaly_utility_source(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag QALY-based analyses without utility source disclosure.

    Emits ``missing-qaly-utility-source`` (minor) when QALYs or health
    state utilities are used but the source of utility values is not stated.
    """
    _vid = "validate_qaly_utility_source"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _QALY_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _QALY_SOURCE_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-qaly-utility-source",
                severity="minor",
                message=(
                    "QALYs or health state utilities are used but the source of "
                    "utility values is not stated. Specify how utilities were "
                    "obtained (e.g., EQ-5D, time trade-off, published literature)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 345 – validate_markov_model_cycle_length
# ---------------------------------------------------------------------------

_MARKOV_TRIGGER_RE = re.compile(
    r"\b(?:Markov\s+(?:model|chain|cohort\s+model|state\s+transition)|"
    r"state\s+transition\s+model|"
    r"transition\s+probabilities?\s+(?:between|among|for)\s+(?:health\s+)?states?|"
    r"Markov\s+cycle|tunnel\s+state)\b",
    re.IGNORECASE,
)
_MARKOV_CYCLE_DISCLOSED_RE = re.compile(
    r"\b(?:cycle\s+length\s+(?:of\s+)?(?:\d+)\s+(?:month|week|year|day)|"
    r"(?:\d+)[\s-](?:month|week|year|day)[\s-](?:Markov\s+)?cycle|"
    r"Markov\s+cycle\s+length\s+(?:was|of)\s+(?:\d+)|"
    r"half[\s-]cycle\s+correction|"
    r"annual\s+transition\s+probability|monthly\s+transition\s+probability)\b",
    re.IGNORECASE,
)


def validate_markov_model_cycle_length(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Markov models without cycle length disclosure.

    Emits ``missing-markov-cycle-length`` (minor) when a Markov model is
    used but the cycle length is not reported.
    """
    _vid = "validate_markov_model_cycle_length"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MARKOV_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _MARKOV_CYCLE_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-markov-cycle-length",
                severity="minor",
                message=(
                    "A Markov model is used but the cycle length is not reported. "
                    "Specify the cycle length (e.g., monthly, annual) and apply "
                    "half-cycle correction if appropriate."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 346 – validate_measurement_invariance_testing
# ---------------------------------------------------------------------------

_MEASUREMENT_INVARIANCE_TRIGGER_RE = re.compile(
    r"\b(?:measurement\s+invariance|factorial\s+invariance|"
    r"configural\s+(?:model|invariance)|metric\s+invariance|"
    r"scalar\s+invariance|partial\s+invariance|"
    r"cross[\s-]group\s+comparison\s+(?:of\s+)?(?:latent|factor)|"
    r"multi[\s-]group\s+(?:CFA|SEM|analysis)|"
    r"comparing\s+(?:latent|factor)\s+(?:means?|scores?)\s+across\s+groups?)\b",
    re.IGNORECASE,
)
_INVARIANCE_TESTED_RE = re.compile(
    r"\b(?:measurement\s+invariance\s+(?:was|were|is)\s+"
    r"(?:tested|assessed|examined|established|confirmed)|"
    r"configural\s+(?:model\s+)?(?:fit|invariance|testing)|"
    r"metric\s+(?:model\s+)?(?:fit|invariance|testing)|"
    r"scalar\s+(?:model\s+)?(?:fit|invariance|testing)|"
    r"Lagrange\s+multiplier\s+test|"
    r"chi[\s-]square\s+difference\s+test\s+for\s+(?:invariance|nested))\b",
    re.IGNORECASE,
)


def validate_measurement_invariance_testing(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag cross-group latent comparisons without invariance testing.

    Emits ``missing-measurement-invariance-test`` (moderate) when cross-group
    comparisons of latent constructs are made but measurement invariance is
    not tested.
    """
    _vid = "validate_measurement_invariance_testing"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MEASUREMENT_INVARIANCE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _INVARIANCE_TESTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-measurement-invariance-test",
                severity="moderate",
                message=(
                    "Cross-group comparisons of latent constructs are made but "
                    "measurement invariance is not tested. Establish at minimum "
                    "configural and metric invariance before comparing groups."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 347 – validate_convergent_discriminant_validity
# ---------------------------------------------------------------------------

_CONSTRUCT_VALIDITY_TRIGGER_RE = re.compile(
    r"\b(?:(?:new|novel|developed|validated|adapted)\s+"
    r"(?:scale|measure|instrument|questionnaire|inventory)|"
    r"scale\s+development|instrument\s+development|"
    r"construct\s+validity|factor\s+structure\s+(?:was|of|for)\s+the)\b",
    re.IGNORECASE,
)
_VALIDITY_ASSESSED_RE = re.compile(
    r"\b(?:convergent\s+validity|discriminant\s+validity|"
    r"average\s+variance\s+extracted|AVE\b|"
    r"composite\s+reliability|CR\b\s+(?:=\s+0\.|for|of)|"
    r"maximum\s+shared\s+variance|"
    r"HTMT\b|heterotrait[\s-]monotrait|"
    r"Fornell[\s-]Larcker|"
    r"concurrent\s+validity|criterion\s+validity|"
    r"known[\s-]groups\s+validity)\b",
    re.IGNORECASE,
)


def validate_convergent_discriminant_validity(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag new scale development without convergent/discriminant validity.

    Emits ``missing-convergent-discriminant-validity`` (minor) when a new
    scale or measure is developed but convergent and discriminant validity
    are not assessed.
    """
    _vid = "validate_convergent_discriminant_validity"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _CONSTRUCT_VALIDITY_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _VALIDITY_ASSESSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-convergent-discriminant-validity",
                severity="minor",
                message=(
                    "A new scale or measure is developed but convergent and discriminant "
                    "validity are not assessed. Report AVE, composite reliability, "
                    "and HTMT ratios."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 348 – validate_irt_model_fit
# ---------------------------------------------------------------------------

_IRT_TRIGGER_RE = re.compile(
    r"\b(?:item\s+response\s+theory|IRT\b|Rasch\s+(?:model|analysis|rating)|"
    r"2PL\b|3PL\b|graded\s+response\s+model|GRM\b|"
    r"item\s+discrimination|item\s+difficulty|item\s+information|"
    r"person[\s-]item\s+(?:map|fit)|theta\s+(?:estimates?|score))\b",
    re.IGNORECASE,
)
_IRT_FIT_REPORTED_RE = re.compile(
    r"\b(?:item\s+fit\s+(?:was|statistics?|index|indices)|"
    r"person\s+fit\s+(?:was|statistics?|index)|"
    r"infit\b|outfit\b|"
    r"model[\s-]data\s+fit|Rasch\s+fit\s+(?:statistics?|residuals?)|"
    r"RMSEA\s+(?:for\s+IRT|of\s+the\s+IRT)|"
    r"item\s+characteristic\s+curve\s+(?:analysis|fit)|ICC\s+fit)\b",
    re.IGNORECASE,
)


def validate_irt_model_fit(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag IRT/Rasch analyses without model fit reporting.

    Emits ``missing-irt-model-fit`` (minor) when item response theory or
    Rasch analysis is used but model-data fit is not assessed.
    """
    _vid = "validate_irt_model_fit"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _IRT_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _IRT_FIT_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-irt-model-fit",
                severity="minor",
                message=(
                    "IRT or Rasch analysis is used but model-data fit is not assessed. "
                    "Report item fit statistics (infit/outfit MSQ) and evaluate "
                    "model fit to the data."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 349 – validate_test_retest_reliability
# ---------------------------------------------------------------------------

_TEST_RETEST_TRIGGER_RE = re.compile(
    r"\b(?:test[\s-]retest\s+reliability|temporal\s+stability|"
    r"stability\s+over\s+time|intraclass\s+correlation\s+(?:coefficient|for\s+stability)|"
    r"(?:administered|measured)\s+(?:twice|on\s+two\s+occasions?|at\s+two\s+time\s+points?)|"
    r"retest\s+interval|time\s+between\s+(?:administrations?|measurements?)\s+was)\b",
    re.IGNORECASE,
)
_TEST_RETEST_REPORTED_RE = re.compile(
    r"\b(?:test[\s-]retest\s+reliability\s+(?:of|=|coefficient)\s*(?:\d|0\.)|"
    r"test[\s-]retest\s+(?:r|coefficient)\s*=\s*0\.\d+|"
    r"ICC\s*=\s*0\.\d+|intraclass\s+correlation\s+coefficient\s*(?:=|was|of)\s*0\.\d+|"
    r"Pearson\s+(?:r\s+for\s+)?(?:test[\s-]retest|stability)|"
    r"Spearman\s+(?:rho\s+for\s+)?(?:test[\s-]retest|stability)|"
    r"stability\s+coefficient\s*=)\b",
    re.IGNORECASE,
)


def validate_test_retest_reliability(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag test-retest reliability studies without reliability coefficients.

    Emits ``missing-test-retest-reliability`` (minor) when test-retest
    reliability is investigated but no stability coefficient is reported.
    """
    _vid = "validate_test_retest_reliability"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _TEST_RETEST_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _TEST_RETEST_REPORTED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-test-retest-reliability",
                severity="minor",
                message=(
                    "Test-retest reliability is investigated but no reliability "
                    "coefficient is reported. Report ICC or Pearson/Spearman "
                    "correlations with the retest interval."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 350 – validate_norm_reference_group
# ---------------------------------------------------------------------------

_NORM_TRIGGER_RE = re.compile(
    r"\b(?:normative\s+(?:data|sample|scores?|values?)|"
    r"population\s+norms?|standardized\s+(?:scores?|norms?)|"
    r"norm[\s-]referenced\s+(?:test|score|assessment)|"
    r"z[\s-]score\s+(?:based\s+on|relative\s+to|compared\s+to)\s+(?:the\s+)?norm|"
    r"compared\s+to\s+(?:population\s+)?norms?)\b",
    re.IGNORECASE,
)
_NORM_REFERENCE_DISCLOSED_RE = re.compile(
    r"\b(?:normative\s+(?:data|sample)\s+(?:were|was)\s+"
    r"(?:derived|obtained|sourced|taken)\s+from|"
    r"reference\s+(?:sample|population|group)\s+(?:for\s+(?:the\s+)?norms?|"
    r"consisted\s+of|included|was\s+a)|"
    r"norms?\s+(?:were|was)\s+(?:based\s+on|derived\s+from)|"
    r"normative\s+population\s+(?:was|consisted\s+of|included))\b",
    re.IGNORECASE,
)


def validate_norm_reference_group(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag norm-referenced interpretations without norm source disclosure.

    Emits ``missing-norm-reference-group`` (minor) when normative scores
    or comparisons to population norms are made but the norm reference
    group is not described.
    """
    _vid = "validate_norm_reference_group"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _NORM_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _NORM_REFERENCE_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-norm-reference-group",
                severity="minor",
                message=(
                    "Scores are compared to population norms but the norm reference "
                    "group is not described. Specify the sample used to derive norms "
                    "(size, demographics, collection date)."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 351 – validate_theoretical_saturation_claim
# ---------------------------------------------------------------------------

_SATURATION_TRIGGER_RE = re.compile(
    r"\b(?:theoretical\s+saturation|thematic\s+saturation|data\s+saturation|"
    r"saturation\s+(?:was|is|of|point|reached|achieved)|"
    r"no\s+new\s+(?:themes?|codes?|categories?)\s+(?:were\s+)?(?:emerging|emerged))\b",
    re.IGNORECASE,
)
_SATURATION_EVIDENCED_RE = re.compile(
    r"\b(?:saturation\s+was\s+(?:reached|achieved|confirmed|determined)\s+"
    r"(?:after|at|by|following)\s+(?:\d+|the)|"
    r"(?:\d+|no)\s+new\s+(?:themes?|codes?|categories?)\s+(?:were\s+)?(?:emerging|emerged)"
    r"\s+(?:after|beyond|from)\s+(?:\d+|the)|"
    r"additional\s+(?:interviews?|participants?)\s+(?:were\s+)?(?:recruited|added)\s+"
    r"(?:until|to\s+confirm\s+)?saturation|"
    r"saturation\s+criterion\s+(?:was|of))\b",
    re.IGNORECASE,
)


def validate_theoretical_saturation_claim(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag saturation claims without supporting evidence.

    Emits ``missing-saturation-evidence`` (minor) when theoretical or
    data saturation is claimed but no evidence (e.g., when it was reached,
    verification procedure) is provided.
    """
    _vid = "validate_theoretical_saturation_claim"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _SATURATION_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _SATURATION_EVIDENCED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-saturation-evidence",
                severity="minor",
                message=(
                    "Data or theoretical saturation is claimed but no evidence "
                    "of when or how saturation was determined is provided. "
                    "Report at which point saturation was reached and the verification "
                    "procedure used."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 352 – validate_member_checking_disclosure
# ---------------------------------------------------------------------------

_MEMBER_CHECK_TRIGGER_RE = re.compile(
    r"\b(?:member\s+(?:checking?|validation|review)|"
    r"participant\s+(?:validation|review|feedback)|"
    r"respondent\s+validation|communicative\s+validity|"
    r"participants?\s+(?:were\s+)?(?:asked\s+to\s+)?review(?:ed)?\s+(?:the\s+)?"
    r"(?:themes?|findings?|transcripts?|summaries?|results?))\b",
    re.IGNORECASE,
)
_MEMBER_CHECK_DISCLOSED_RE = re.compile(
    r"\b(?:member\s+(?:checking?|validation|review)\s+(?:was|were)\s+"
    r"(?:conducted|performed|undertaken|used|carried\s+out)|"
    r"participants?\s+(?:were\s+asked\s+to|reviewed)\s+(?:and\s+)?(?:confirmed|validated|"
    r"agreed\s+with|provided\s+feedback\s+on)\s+(?:the\s+)?(?:themes?|findings?)|"
    r"results\s+were\s+shared\s+with\s+participants?)\b",
    re.IGNORECASE,
)


def validate_member_checking_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag qualitative studies claiming member checking without method detail.

    Emits ``missing-member-checking`` (minor) when member checking is claimed
    but no detail on how it was conducted is provided.
    """
    _vid = "validate_member_checking_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _MEMBER_CHECK_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _MEMBER_CHECK_DISCLOSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-member-checking",
                severity="minor",
                message=(
                    "Member checking is mentioned but no detail on how it was "
                    "conducted is provided. Describe the process: who reviewed what, "
                    "and how participant feedback was incorporated."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 353 – validate_reflexivity_statement
# ---------------------------------------------------------------------------

_QUALITATIVE_TRIGGER_RE = re.compile(
    r"\b(?:qualitative\s+(?:research|study|approach|methodology|data|analysis)|"
    r"grounded\s+theory|phenomenolog(?:y|ical)|thematic\s+analysis|"
    r"ethnograph(?:y|ic)|interpretive\s+(?:approach|phenomenological)|"
    r"in[\s-]depth\s+interview|focus\s+group)\b",
    re.IGNORECASE,
)
_REFLEXIVITY_PRESENT_RE = re.compile(
    r"\b(?:reflexivity|reflexive\s+(?:account|process|stance|position)|"
    r"researcher\s+(?:position|positionality|perspective|background|influence)|"
    r"positionality\s+(?:statement|of\s+the\s+researcher)|"
    r"potential\s+bias(?:es)?\s+(?:of|from)\s+(?:the\s+)?researcher|"
    r"my\s+(?:position|background|experience|perspective)\s+as\s+a\s+researcher)\b",
    re.IGNORECASE,
)


def validate_reflexivity_statement(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag qualitative studies without researcher reflexivity statement.

    Emits ``missing-reflexivity-statement`` (minor) when qualitative
    methods are used but researcher positionality or reflexivity is not
    addressed.
    """
    _vid = "validate_reflexivity_statement"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _QUALITATIVE_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _REFLEXIVITY_PRESENT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-reflexivity-statement",
                severity="minor",
                message=(
                    "Qualitative methods are used but researcher reflexivity or "
                    "positionality is not addressed. Include a reflexivity statement "
                    "describing how the researcher's background may have influenced "
                    "data collection and interpretation."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 354 – validate_negative_case_analysis
# ---------------------------------------------------------------------------

_QUALITATIVE_THEME_TRIGGER_RE = re.compile(
    r"\b(?:thematic\s+analysis|themes?\s+(?:were\s+)?(?:identified|emerged?|developed)|"
    r"coding\s+process|code(?:s)?\s+(?:were\s+)?(?:developed|identified|applied)|"
    r"interpretive\s+(?:findings?|results?)|main\s+themes?|"
    r"categories?\s+(?:were\s+)?(?:identified|derived|emerged?))\b",
    re.IGNORECASE,
)
_NEGATIVE_CASE_ADDRESSED_RE = re.compile(
    r"\b(?:negative\s+case\s+(?:analysis|examination|review)|"
    r"disconfirming\s+(?:evidence|cases?|examples?)|"
    r"deviant\s+case\s+(?:analysis|review)|"
    r"cases?\s+that\s+(?:did\s+not\s+fit|contradict(?:ed)?|challenged?)\s+"
    r"(?:the\s+)?(?:emerging\s+)?(?:themes?|theory|interpretation)|"
    r"contradictory\s+(?:evidence|data|cases?))\b",
    re.IGNORECASE,
)


def validate_negative_case_analysis(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag thematic analyses without negative case consideration.

    Emits ``missing-negative-case-analysis`` (minor) when qualitative
    thematic coding is performed but negative or disconfirming cases are
    not addressed.
    """
    _vid = "validate_negative_case_analysis"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _QUALITATIVE_THEME_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _NEGATIVE_CASE_ADDRESSED_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-negative-case-analysis",
                severity="minor",
                message=(
                    "Qualitative thematic coding is performed but negative or "
                    "disconfirming cases are not addressed. Consider negative "
                    "case analysis to strengthen credibility of interpretations."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 355 – validate_thick_description_transferability
# ---------------------------------------------------------------------------

_TRANSFERABILITY_TRIGGER_RE = re.compile(
    r"\b(?:qualitative\s+(?:research|study|findings?)|"
    r"transferability|generalizability\s+(?:of\s+(?:qualitative|the)\s+)?findings?|"
    r"applicability\s+(?:of\s+(?:the\s+)?findings?|to\s+other\s+settings?)|"
    r"whether\s+(?:the\s+)?findings?\s+(?:can\s+be|are)\s+transferable)\b",
    re.IGNORECASE,
)
_THICK_DESCRIPTION_PRESENT_RE = re.compile(
    r"\b(?:thick\s+description|contextual\s+(?:information|detail)|"
    r"detailed\s+description\s+of\s+(?:the\s+)?(?:setting|context|sample|participants?)|"
    r"transferability\s+(?:is\s+(?:supported|enhanced|facilitated)\s+by|"
    r"(?:was\s+)?addressed\s+(?:through|by|via))|"
    r"readers?\s+(?:to\s+)?(?:judge|assess|determine)\s+(?:the\s+)?transferability|"
    r"purposive\s+sampling\s+(?:was\s+used\s+to\s+)?(?:enhance|support|ensure)\s+"
    r"(?:the\s+)?(?:diversity|range|variation))\b",
    re.IGNORECASE,
)


def validate_thick_description_transferability(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag qualitative studies not addressing transferability.

    Emits ``missing-thick-description`` (minor) when qualitative findings
    are presented without sufficient contextual detail or transferability
    discussion to allow readers to judge applicability.
    """
    _vid = "validate_thick_description_transferability"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])

    full = parsed.full_text
    if not _TRANSFERABILITY_TRIGGER_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    if _THICK_DESCRIPTION_PRESENT_RE.search(full):
        return ValidationResult(validator_name=_vid, findings=[])

    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-thick-description",
                severity="minor",
                message=(
                    "Qualitative findings are presented without sufficient contextual "
                    "description to support transferability judgements. Provide thick "
                    "description of the setting, context, and participants."
                ),
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 356 – mixed-methods design rationale
# ---------------------------------------------------------------------------

_MMD_TRIGGER_RE = re.compile(
    r"\b(?:mixed[\s-]methods?|mixed[\s-]method\s+design|concurrent\s+triangulation"
    r"|explanatory\s+sequential|exploratory\s+sequential|convergent\s+design)\b",
    re.IGNORECASE,
)

_MMD_RATIONALE_RE = re.compile(
    r"\b(?:rationale|because|in\s+order\s+to|to\s+(?:triangulate|explore|explain|validate)"
    r"|chosen\s+(?:to|because)|selected\s+(?:to|because)|design\s+(?:was\s+)?(?:adopted|selected|chosen))\b",
    re.IGNORECASE,
)


def validate_mixed_methods_design_rationale(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag mixed-methods designs reported without an explicit rationale.

    Emits ``missing-mixed-methods-rationale`` (minor) when a mixed-methods
    design is identified but no reason for choosing it is stated.
    """
    _vid = "validate_mixed_methods_design_rationale"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _MMD_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _MMD_RATIONALE_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-mixed-methods-rationale",
                message=(
                    "A mixed-methods design is mentioned but no explicit rationale "
                    "for choosing it is provided."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 357 – simulation parameter justification
# ---------------------------------------------------------------------------

_SIM_TRIGGER_RE = re.compile(
    r"\b(?:simulation\s+(?:study|experiment|analysis)|Monte\s+Carlo\s+simulation"
    r"|agent[\s-]based\s+simulation|discrete[\s-]event\s+simulation"
    r"|stochastic\s+simulation)\b",
    re.IGNORECASE,
)

_SIM_PARAMS_RE = re.compile(
    r"\b(?:parameters?\s+(?:were\s+)?(?:set|chosen|selected|calibrated|justified)"
    r"|parameter\s+(?:values?|settings?|choices?)"
    r"|based\s+on\s+(?:prior|published|empirical)\s+(?:literature|data|studies?)"
    r"|calibrated\s+to|justified\s+by)\b",
    re.IGNORECASE,
)


def validate_simulation_parameter_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag simulation studies that do not justify their parameter values.

    Emits ``missing-simulation-parameters`` (minor) when a simulation study
    is identified but no justification for parameter choices is provided.
    """
    _vid = "validate_simulation_parameter_justification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _SIM_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _SIM_PARAMS_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-simulation-parameters",
                message=(
                    "A simulation study is described but no justification for the "
                    "simulation parameter values is provided."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 358 – bootstrap sample size
# ---------------------------------------------------------------------------

_BOOT_TRIGGER_RE = re.compile(
    r"\b(?:bootstrapp?ing|bootstrap\s+(?:resampl|sample|procedure|method|confidence"
    r"|standard\s+error)|percentile\s+bootstrap|bias[\s-]corrected\s+bootstrap)\b",
    re.IGNORECASE,
)

_BOOT_SIZE_RE = re.compile(
    r"(?:\d[\d,]*\s+bootstrap\s+(?:samples?|replications?|iterations?)"
    r"|bootstrap(?:ped)?\s+(?:with|using)\s+\d[\d,]*"
    r"|B\s*=\s*\d[\d,]*"
    r"|number\s+of\s+bootstrap\s+(?:samples?|replications?)\s+(?:was|were|set\s+to)\s+\d)",
    re.IGNORECASE,
)


def validate_bootstrap_sample_size(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag bootstrapping without reporting the number of samples.

    Emits ``missing-bootstrap-sample-size`` (minor) when bootstrapping is used
    but the number of bootstrap samples or replications is not stated.
    """
    _vid = "validate_bootstrap_sample_size"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _BOOT_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _BOOT_SIZE_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-bootstrap-sample-size",
                message=(
                    "Bootstrapping is mentioned but the number of bootstrap samples "
                    "or replications is not reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 359 – Monte Carlo replications
# ---------------------------------------------------------------------------

_MC_TRIGGER_RE = re.compile(
    r"\b(?:Monte\s+Carlo|MCMC|Markov\s+chain\s+Monte\s+Carlo)\b",
    re.IGNORECASE,
)

_MC_REPS_RE = re.compile(
    r"(?:\d[\d,]*\s+(?:Monte\s+Carlo\s+)?(?:replications?|iterations?|simulations?|draws?)"
    r"|(?:replications?|iterations?|simulations?)\s*=\s*\d[\d,]*"
    r"|R\s*=\s*\d[\d,]*\s+replications?"
    r"|number\s+of\s+(?:replications?|iterations?|simulations?)\s+(?:was|were|set\s+to)\s+\d)",
    re.IGNORECASE,
)


def validate_monte_carlo_replications(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Monte Carlo studies without reporting replication counts.

    Emits ``missing-monte-carlo-replications`` (minor) when Monte Carlo or
    MCMC methods are mentioned but the number of replications or iterations
    is not stated.
    """
    _vid = "validate_monte_carlo_replications"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _MC_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _MC_REPS_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-monte-carlo-replications",
                message=(
                    "Monte Carlo or MCMC methods are mentioned but the number of "
                    "replications or iterations is not reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 360 – agent-based model validation
# ---------------------------------------------------------------------------

_ABM_TRIGGER_RE = re.compile(
    r"\b(?:agent[\s-]based\s+model(?:ling|ing|s?)?"
    r"|ABM\b"
    r"|multi[\s-]agent\s+(?:simulation|model))\b",
    re.IGNORECASE,
)

_ABM_VALIDATION_RE = re.compile(
    r"\b(?:model\s+(?:validation|verification|calibration)"
    r"|face\s+validity"
    r"|empirical\s+validation"
    r"|calibrated\s+(?:against|to)\s+(?:empirical|real|observed)"
    r"|validated\s+(?:against|by|using)"
    r"|ODD\s+protocol)\b",
    re.IGNORECASE,
)


def validate_agent_based_model_validation(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag agent-based models without a validation or calibration procedure.

    Emits ``missing-abm-validation`` (minor) when an ABM is described but
    no validation, verification, or calibration procedure is reported.
    """
    _vid = "validate_agent_based_model_validation"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _ABM_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _ABM_VALIDATION_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-abm-validation",
                message=(
                    "An agent-based model (ABM) is described but no model validation "
                    "or calibration procedure is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 361 – network analysis density reporting
# ---------------------------------------------------------------------------

_NET_TRIGGER_RE = re.compile(
    r"\b(?:network\s+(?:analysis|structure|topology|centrality)"
    r"|social\s+network\s+analysis"
    r"|SNA\b"
    r"|graph[\s-]theoretic"
    r"|adjacency\s+matrix)\b",
    re.IGNORECASE,
)

_NET_DENSITY_RE = re.compile(
    r"\b(?:network\s+density"
    r"|density\s*=\s*0\.\d+"
    r"|density\s+(?:was|of|is)\s*(?:\d|0\.)"
    r"|average\s+(?:degree|path\s+length|clustering\s+coefficient)"
    r"|clustering\s+coefficient\b)\b",
    re.IGNORECASE,
)


def validate_network_analysis_density_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag network analyses without key structural statistics.

    Emits ``missing-network-density`` (minor) when a network analysis is
    described but no density, clustering coefficient, or average degree
    statistic is reported.
    """
    _vid = "validate_network_analysis_density_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _NET_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _NET_DENSITY_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-network-density",
                message=(
                    "Network analysis is reported but key structural statistics "
                    "(density, clustering coefficient, average degree) are absent."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 362 – spatial autocorrelation check
# ---------------------------------------------------------------------------

_SPATIAL_TRIGGER_RE = re.compile(
    r"\b(?:spatial\s+(?:analysis|data|regression|econometrics|model)"
    r"|geographic(?:al)?\s+(?:data|unit|variation|clustering)"
    r"|spatially[\s-](?:lagged|clustered|distributed)"
    r"|GIS\b"
    r"|point\s+pattern\s+analysis)\b",
    re.IGNORECASE,
)

_SPATIAL_AC_RE = re.compile(
    r"\b(?:Moran['']?s?\s+I"
    r"|Geary['']?s?\s+C"
    r"|spatial\s+autocorrelation"
    r"|spatial\s+dependence"
    r"|LISA\b"
    r"|local\s+indicators\s+of\s+spatial\s+association)\b",
    re.IGNORECASE,
)


def validate_spatial_autocorrelation_check(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag spatial studies that do not test for spatial autocorrelation.

    Emits ``missing-spatial-autocorrelation`` (minor) when spatial data are
    analysed but no spatial autocorrelation test (e.g., Moran's I) is reported.
    """
    _vid = "validate_spatial_autocorrelation_check"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _SPATIAL_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _SPATIAL_AC_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-spatial-autocorrelation",
                message=(
                    "Spatial data analysis is described but no spatial autocorrelation "
                    "test (e.g., Moran's I, Geary's C) is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 363 – structural break test
# ---------------------------------------------------------------------------

_STRUCT_BREAK_TRIGGER_RE = re.compile(
    r"\b(?:time[\s-]series\s+(?:regression|model|analysis|data)"
    r"|panel\s+(?:data\s+)?(?:regression|model|analysis)"
    r"|longitudinal\s+time[\s-]series"
    r"|economic\s+time[\s-]series)\b",
    re.IGNORECASE,
)

_STRUCT_BREAK_TESTED_RE = re.compile(
    r"\b(?:structural\s+break"
    r"|Chow\s+test"
    r"|CUSUM\s+test"
    r"|Bai[\s-]Perron"
    r"|breakpoint\s+(?:test|detection|analysis)"
    r"|regime\s+(?:change|switch(?:ing)?))\b",
    re.IGNORECASE,
)


def validate_structural_break_test(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag time-series analyses that omit structural break testing.

    Emits ``missing-structural-break-test`` (minor) when time-series or panel
    data regression is described but no structural break test is mentioned.
    """
    _vid = "validate_structural_break_test"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _STRUCT_BREAK_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _STRUCT_BREAK_TESTED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-structural-break-test",
                message=(
                    "Time-series regression is described but no structural break test "
                    "(e.g., Chow test, CUSUM, Bai-Perron) is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 364 – VIF reporting for regression
# ---------------------------------------------------------------------------

_VIF_TRIGGER_RE = re.compile(
    r"\b(?:multiple\s+(?:regression|linear\s+regression|logistic\s+regression)"
    r"|OLS\s+regression"
    r"|hierarchical\s+regression"
    r"|regression\s+analysis\s+(?:was|were)\s+(?:conducted|performed|run|used))\b",
    re.IGNORECASE,
)

_VIF_REPORTED_RE = re.compile(
    r"\b(?:VIF\b"
    r"|variance\s+inflation\s+factor"
    r"|tolerance\s+(?:value|statistic)"
    r"|condition\s+(?:number|index)"
    r"|multicollinearity\s+(?:was|were)\s+(?:assessed|checked|tested|examined))\b",
    re.IGNORECASE,
)


def validate_variance_inflation_factor_reporting(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag regression analyses missing multicollinearity diagnostics.

    Emits ``missing-vif-reporting`` (minor) when multiple regression is
    described but no VIF or tolerance check is reported.
    """
    _vid = "validate_variance_inflation_factor_reporting"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _VIF_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _VIF_REPORTED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-vif-reporting",
                message=(
                    "Multiple regression is reported but no variance inflation "
                    "factor (VIF) or multicollinearity check is described."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 365 – ordinal regression assumption check
# ---------------------------------------------------------------------------

_ORDINAL_TRIGGER_RE = re.compile(
    r"\b(?:ordinal\s+(?:regression|logistic\s+regression|outcome)"
    r"|proportional\s+odds\s+(?:model|assumption)"
    r"|cumulative\s+logit\s+model"
    r"|polytomous\s+logistic\s+regression)\b",
    re.IGNORECASE,
)

_ORDINAL_ASSUMPTION_RE = re.compile(
    r"\b(?:proportional\s+odds\s+assumption"
    r"|parallel\s+regression\s+assumption"
    r"|Brant\s+test"
    r"|score\s+test\s+(?:of\s+)?proportional\s+odds"
    r"|assumption\s+(?:was|were)\s+(?:tested|checked|met|satisfied|violated))\b",
    re.IGNORECASE,
)


def validate_ordinal_regression_assumption(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ordinal regression without checking the proportional odds assumption.

    Emits ``missing-ordinal-regression-check`` (minor) when ordinal logistic
    regression is described but no proportional odds assumption check is reported.
    """
    _vid = "validate_ordinal_regression_assumption"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _ORDINAL_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _ORDINAL_ASSUMPTION_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-ordinal-regression-check",
                message=(
                    "Ordinal logistic regression is described but no check of the "
                    "proportional odds assumption is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 366 – Granger causality test disclosure
# ---------------------------------------------------------------------------

_GRANGER_TRIGGER_RE = re.compile(
    r"\b(?:Granger\s+(?:caus(?:ality|es)|test)"
    r"|Granger[\s-]causal"
    r"|predictive\s+causality\s+test)\b",
    re.IGNORECASE,
)

_GRANGER_DISCLOSED_RE = re.compile(
    r"\b(?:lag\s+(?:length|order|selection)"
    r"|optimal\s+lag"
    r"|AIC\b|BIC\b|Akaike|Schwarz"
    r"|F[\s-]statistic\s+(?:for|of)\s+Granger"
    r"|Granger\s+causality\s+test\s+(?:result|showed|indicated|was\s+significant))\b",
    re.IGNORECASE,
)


def validate_granger_causality_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag Granger causality tests without lag length disclosure.

    Emits ``missing-granger-lag-disclosure`` (minor) when a Granger causality
    test is mentioned but the lag length or selection criterion is not reported.
    """
    _vid = "validate_granger_causality_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _GRANGER_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _GRANGER_DISCLOSED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-granger-lag-disclosure",
                message=(
                    "A Granger causality test is reported but the lag length or "
                    "lag selection criterion is not disclosed."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 367 – cointegration test disclosure
# ---------------------------------------------------------------------------

_COINT_TRIGGER_RE = re.compile(
    r"\b(?:cointegrat(?:ion|ed|ing)"
    r"|long[\s-]run\s+equilibrium\s+relationship"
    r"|error[\s-]correction\s+model"
    r"|ECM\b"
    r"|VECM\b)\b",
    re.IGNORECASE,
)

_COINT_TESTED_RE = re.compile(
    r"\b(?:Johansen\s+(?:test|cointegration)"
    r"|Engle[\s-]Granger\s+(?:test|procedure)"
    r"|bounds\s+test"
    r"|ARDL\s+bounds"
    r"|trace\s+(?:statistic|test)"
    r"|cointegration\s+test\s+(?:result|showed|confirmed|indicated))\b",
    re.IGNORECASE,
)


def validate_cointegration_test_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag cointegration analyses without explicit test disclosure.

    Emits ``missing-cointegration-test`` (minor) when cointegration or
    error-correction models are used but no cointegration test is mentioned.
    """
    _vid = "validate_cointegration_test_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _COINT_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _COINT_TESTED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-cointegration-test",
                message=(
                    "Cointegration or error-correction modelling is described but "
                    "no cointegration test (e.g., Johansen, Engle-Granger) is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 368 – unit root test disclosure
# ---------------------------------------------------------------------------

_UNIT_ROOT_TRIGGER_RE = re.compile(
    r"\b(?:time[\s-]series\s+(?:data|model|regression)"
    r"|stationarity"
    r"|non[\s-]?stationary\s+(?:data|series|variable)"
    r"|integrated\s+process)\b",
    re.IGNORECASE,
)

_UNIT_ROOT_TESTED_RE = re.compile(
    r"\b(?:Augmented\s+Dickey[\s-]Fuller"
    r"|ADF\s+test"
    r"|Phillips[\s-]Perron\s+test"
    r"|PP\s+test"
    r"|KPSS\s+test"
    r"|unit[\s-]root\s+test"
    r"|stationarity\s+(?:test|was\s+(?:tested|confirmed|rejected)))\b",
    re.IGNORECASE,
)


def validate_unit_root_test_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag time-series analyses without unit root testing.

    Emits ``missing-unit-root-test`` (minor) when time-series data are
    analysed but no unit root or stationarity test is mentioned.
    """
    _vid = "validate_unit_root_test_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _UNIT_ROOT_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _UNIT_ROOT_TESTED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-unit-root-test",
                message=(
                    "Time-series data are analysed but no unit root or stationarity "
                    "test (e.g., ADF, KPSS) is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 369 – ARCH/GARCH volatility model disclosure
# ---------------------------------------------------------------------------

_ARCH_TRIGGER_RE = re.compile(
    r"\b(?:ARCH\b|GARCH\b|EGARCH\b|DCC[\s-]GARCH"
    r"|volatility\s+(?:model(?:ling|ing)?|clustering|forecasting)"
    r"|conditional\s+heteroscedasticity)\b",
    re.IGNORECASE,
)

_ARCH_SPEC_RE = re.compile(
    r"(?:GARCH\s*\(\s*\d+\s*,\s*\d+\s*\)"
    r"|ARCH\s*\(\s*\d+\s*\)"
    r"|\border\s+(?:p|q)\s*=\s*\d"
    r"|\blag\s+order\s+(?:of\s+)?\d"
    r"|\binformation\s+criterion\s+(?:AIC|BIC|AICC)"
    r"|\bAIC\b|\bBIC\b)",
    re.IGNORECASE,
)


def validate_arch_garch_specification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ARCH/GARCH models without order specification.

    Emits ``missing-arch-order-specification`` (minor) when ARCH or GARCH
    models are described but the model order is not specified.
    """
    _vid = "validate_arch_garch_specification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _ARCH_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _ARCH_SPEC_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-arch-order-specification",
                message=(
                    "ARCH or GARCH modelling is described but the model order "
                    "specification (e.g., GARCH(1,1)) is not provided."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 370 – panel data fixed/random effects justification
# ---------------------------------------------------------------------------

_PANEL_TRIGGER_RE = re.compile(
    r"\b(?:panel\s+(?:data|regression|model|analysis)"
    r"|fixed[\s-]effects?\s+(?:model|regression|estimator)"
    r"|random[\s-]effects?\s+(?:model|regression|estimator)"
    r"|within[\s-]estimator"
    r"|between[\s-]estimator)\b",
    re.IGNORECASE,
)

_PANEL_JUSTIFIED_RE = re.compile(
    r"\b(?:Hausman\s+test"
    r"|fixed\s+vs\.?\s+random\s+effects?"
    r"|random\s+vs\.?\s+fixed\s+effects?"
    r"|choice\s+(?:of|between)\s+(?:fixed|random)\s+effects?"
    r"|FE\s+(?:vs\.?|or)\s+RE\b"
    r"|correlated\s+with\s+the\s+(?:unit|individual|time)\s+effects?)\b",
    re.IGNORECASE,
)


def validate_panel_effects_justification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag panel data models without fixed/random effects justification.

    Emits ``missing-panel-effects-justification`` (minor) when panel data
    fixed or random effects are used but no justification or Hausman test
    is provided.
    """
    _vid = "validate_panel_effects_justification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _PANEL_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _PANEL_JUSTIFIED_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-panel-effects-justification",
                message=(
                    "Panel data fixed or random effects are used but no Hausman "
                    "test or theoretical justification for the choice is provided."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 371 – ARIMA model order disclosure
# ---------------------------------------------------------------------------

_ARIMA_TRIGGER_RE = re.compile(
    r"\b(?:ARIMA\b|ARMA\b|AR\s+model|autoregressive\s+(?:integrated\s+)?moving\s+average"
    r"|Box[\s-]Jenkins\s+(?:model|methodology|approach))\b",
    re.IGNORECASE,
)

_ARIMA_ORDER_RE = re.compile(
    r"(?:ARIMA\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*\)"
    r"|ARMA\s*\(\s*\d+\s*,\s*\d+\s*\)"
    r"|\border\s*\(\s*p\s*,\s*d\s*,\s*q\s*\)"
    r"|\bAIC\b|\bBIC\b|\bautomatic\s+order\s+selection)",
    re.IGNORECASE,
)


def validate_arima_order_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ARIMA models without order specification.

    Emits ``missing-arima-order-disclosure`` (minor) when an ARIMA or ARMA
    model is described but the model order (p, d, q) is not specified.
    """
    _vid = "validate_arima_order_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _ARIMA_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _ARIMA_ORDER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-arima-order-disclosure",
                message=(
                    "An ARIMA or ARMA model is described but the model order "
                    "(p, d, q) is not specified."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 372 – VAR model lag order disclosure
# ---------------------------------------------------------------------------

_VAR_TRIGGER_RE = re.compile(
    r"\b(?:vector\s+autoregress(?:ion|ive)\b"
    r"|VAR\s+(?:model|system|analysis)"
    r"|SVAR\b|structural\s+VAR\b"
    r"|BVAR\b|Bayesian\s+VAR\b)\b",
    re.IGNORECASE,
)

_VAR_LAG_RE = re.compile(
    r"\b(?:VAR\s*\(\s*\d+\s*\)"
    r"|\blag\s+(?:length|order)\s+(?:of\s+)?\d"
    r"|\boptimal\s+lag"
    r"|\bAIC\b|\bBIC\b|\bHQIC\b"
    r"|\blag\s+selection\s+(?:criterion|criteria))\b",
    re.IGNORECASE,
)


def validate_var_model_lag_order(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag VAR models without lag order specification.

    Emits ``missing-var-lag-order`` (minor) when a VAR model is described but
    the lag order is not specified or no selection criterion is reported.
    """
    _vid = "validate_var_model_lag_order"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _VAR_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _VAR_LAG_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-var-lag-order",
                message=(
                    "A VAR model is described but the lag order or lag selection "
                    "criterion is not reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 373 – impulse response function disclosure
# ---------------------------------------------------------------------------

_IRF_TRIGGER_RE = re.compile(
    r"\b(?:impulse\s+response\s+functions?"
    r"|IRF\b"
    r"|forecast\s+error\s+variance\s+decomposition"
    r"|FEVD\b"
    r"|variance\s+decomposition\b)\b",
    re.IGNORECASE,
)

_IRF_CI_RE = re.compile(
    r"\b(?:confidence\s+(?:band|interval)"
    r"|bootstrap\s+(?:confidence|error)\s+band"
    r"|standard\s+error\s+band"
    r"|Cholesky\s+decomposition"
    r"|ordering\s+of\s+(?:variable|shock)"
    r"|shock\s+identification)\b",
    re.IGNORECASE,
)


def validate_impulse_response_identification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag IRF analyses without shock identification or confidence bands.

    Emits ``missing-irf-identification`` (minor) when impulse response
    functions are reported without shock identification or confidence bands.
    """
    _vid = "validate_impulse_response_identification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _IRF_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _IRF_CI_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-irf-identification",
                message=(
                    "Impulse response functions are reported but shock identification "
                    "strategy or confidence bands are not disclosed."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 374 – forecast evaluation metric disclosure
# ---------------------------------------------------------------------------

_FORECAST_TRIGGER_RE = re.compile(
    r"\b(?:forecast(?:ing)?\s+(?:model|accuracy|performance|evaluation|error)"
    r"|out[\s-]of[\s-]sample\s+(?:forecast|prediction|performance)"
    r"|predictive\s+accuracy\s+(?:test|comparison))\b",
    re.IGNORECASE,
)

_FORECAST_METRIC_RE = re.compile(
    r"\b(?:MAE\b|MAPE\b|RMSE\b|MSE\b"
    r"|mean\s+absolute\s+(?:percentage\s+)?error"
    r"|root\s+mean\s+square(?:d)?\s+error"
    r"|Diebold[\s-]Mariano\s+test"
    r"|DM\s+test\b"
    r"|Theil['']?s?\s+U\b)\b",
    re.IGNORECASE,
)


def validate_forecast_evaluation_metrics(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag forecast evaluations without standard error metrics.

    Emits ``missing-forecast-evaluation-metric`` (minor) when forecasting
    is described but no standard evaluation metric (MAE, RMSE, etc.) is reported.
    """
    _vid = "validate_forecast_evaluation_metrics"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _FORECAST_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _FORECAST_METRIC_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-forecast-evaluation-metric",
                message=(
                    "Forecasting or out-of-sample prediction is described but no "
                    "standard evaluation metric (MAE, RMSE, MAPE) is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 375 – seasonal adjustment disclosure
# ---------------------------------------------------------------------------

_SEASONAL_TRIGGER_RE = re.compile(
    r"\b(?:seasonal(?:ly)?\s+(?:adjusted|adjustment|component|variation|pattern)"
    r"|seasonality"
    r"|deseasonali[sz](?:ed|ation)"
    r"|seasonal\s+(?:decomposition|difference))\b",
    re.IGNORECASE,
)

_SEASONAL_METHOD_RE = re.compile(
    r"\b(?:X[\s-]?1[12]\b|X[\s-]?13\b"
    r"|SEATS\b|TRAMO[\s-]SEATS"
    r"|Census\s+X[\s-]?1[12]"
    r"|STL\s+decomposition"
    r"|Hodrick[\s-]Prescott\s+filter"
    r"|seasonal\s+differencing"
    r"|seasonal\s+adjustment\s+(?:method|procedure|was\s+(?:applied|conducted|performed)))\b",
    re.IGNORECASE,
)


def validate_seasonal_adjustment_disclosure(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag seasonal adjustment without method disclosure.

    Emits ``missing-seasonal-adjustment-method`` (minor) when seasonal
    adjustment is mentioned but the adjustment method is not described.
    """
    _vid = "validate_seasonal_adjustment_disclosure"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _SEASONAL_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _SEASONAL_METHOD_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-seasonal-adjustment-method",
                message=(
                    "Seasonal adjustment is described but the adjustment method "
                    "(e.g., X-12, SEATS, STL) is not specified."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 376 – interrupted time series control group
# ---------------------------------------------------------------------------

_ITS_TRIGGER_RE = re.compile(
    r"\b(?:interrupted\s+time[\s-]series"
    r"|ITS\s+(?:design|analysis|study)"
    r"|segmented\s+regression\s+(?:analysis|approach)"
    r"|time[\s-]series\s+intervention\s+(?:analysis|study))\b",
    re.IGNORECASE,
)

_ITS_CONTROL_RE = re.compile(
    r"\b(?:control\s+(?:group|series|time[\s-]series)"
    r"|comparison\s+(?:group|series)"
    r"|counterfactual\s+(?:group|series)"
    r"|concurrent\s+control"
    r"|no[\s-]intervention\s+(?:group|site))\b",
    re.IGNORECASE,
)


def validate_interrupted_time_series_control(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag ITS analyses without a control group or comparison series.

    Emits ``missing-its-control-group`` (minor) when interrupted time series
    design is described but no control group or comparison series is mentioned.
    """
    _vid = "validate_interrupted_time_series_control"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _ITS_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _ITS_CONTROL_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-its-control-group",
                message=(
                    "An interrupted time series design is described but no control "
                    "group or comparison series is mentioned."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 377 – difference-in-differences parallel trends
# ---------------------------------------------------------------------------

_DID_TRIGGER_RE = re.compile(
    r"\b(?:difference[\s-]in[\s-]differences?"
    r"|DiD\b"
    r"|diff[\s-]in[\s-]diff\b"
    r"|double\s+difference\s+estimator"
    r"|treatment\s+and\s+control\s+group\s+before\s+and\s+after)\b",
    re.IGNORECASE,
)

_DID_PARALLEL_RE = re.compile(
    r"\b(?:parallel\s+(?:trends?\s+assumption|trends?\s+test)"
    r"|pre[\s-](?:treatment|intervention)\s+trend"
    r"|placebo\s+(?:test|regression)"
    r"|pre[\s-]period\s+(?:parallel|trend)"
    r"|common\s+trends?\s+assumption)\b",
    re.IGNORECASE,
)


def validate_difference_in_differences_parallel_trends(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag DiD analyses without parallel trends assumption testing.

    Emits ``missing-did-parallel-trends`` (minor) when a difference-in-
    differences design is described but the parallel trends assumption is
    not discussed or tested.
    """
    _vid = "validate_difference_in_differences_parallel_trends"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _DID_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _DID_PARALLEL_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-did-parallel-trends",
                message=(
                    "A difference-in-differences design is described but the "
                    "parallel trends assumption is not discussed or tested."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 378 – regression discontinuity bandwidth
# ---------------------------------------------------------------------------

_RD_TRIGGER_RE = re.compile(
    r"\b(?:regression\s+discontinuity"
    r"|RD\s+(?:design|estimate|approach)"
    r"|fuzzy\s+(?:RD|regression\s+discontinuity)"
    r"|sharp\s+(?:RD|regression\s+discontinuity)"
    r"|discontinuity\s+at\s+(?:the\s+)?threshold)\b",
    re.IGNORECASE,
)

_RD_BANDWIDTH_RE = re.compile(
    r"\b(?:bandwidth\s+(?:selection|choice|of)"
    r"|optimal\s+bandwidth"
    r"|Imbens[\s-]Kalyanaraman"
    r"|IK\s+bandwidth"
    r"|mean\s+squared\s+error[\s-]optimal"
    r"|local\s+polynomial\s+(?:regression|estimation)"
    r"|triangular\s+kernel"
    r"|bandwidth\s*=\s*[\d.]+)\b",
    re.IGNORECASE,
)


def validate_regression_discontinuity_bandwidth(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag RD analyses without bandwidth specification.

    Emits ``missing-rd-bandwidth`` (minor) when regression discontinuity
    design is described but no bandwidth selection method is reported.
    """
    _vid = "validate_regression_discontinuity_bandwidth"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _RD_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _RD_BANDWIDTH_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-rd-bandwidth",
                message=(
                    "A regression discontinuity design is described but no bandwidth "
                    "selection method is reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 379 – synthetic control pre-period fit
# ---------------------------------------------------------------------------

_SC_TRIGGER_RE = re.compile(
    r"\b(?:synthetic\s+control\s+(?:method|approach|estimator)"
    r"|synth(?:etic)?\s+counterfactual"
    r"|donor\s+pool\b)\b",
    re.IGNORECASE,
)

_SC_FIT_RE = re.compile(
    r"\b(?:pre[\s-](?:treatment|intervention)\s+(?:fit|period|RMSPE)"
    r"|pre[\s-]period\s+(?:balance|fit|performance)"
    r"|root\s+mean\s+square\s+prediction\s+error"
    r"|RMSPE\b"
    r"|predictor\s+(?:weight|balance|fit)"
    r"|synthetic\s+control\s+fit)\b",
    re.IGNORECASE,
)


def validate_synthetic_control_pre_period_fit(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag synthetic control analyses without pre-period fit reporting.

    Emits ``missing-sc-pre-period-fit`` (minor) when a synthetic control
    method is described but the pre-treatment period fit is not reported.
    """
    _vid = "validate_synthetic_control_pre_period_fit"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _SC_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _SC_FIT_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-sc-pre-period-fit",
                message=(
                    "A synthetic control method is described but the pre-treatment "
                    "period fit (e.g., RMSPE) is not reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )


# ---------------------------------------------------------------------------
# Phase 380 – event study window specification
# ---------------------------------------------------------------------------

_EVENT_STUDY_TRIGGER_RE = re.compile(
    r"\b(?:event\s+study\s+(?:analysis|design|methodology|approach)"
    r"|event[\s-]window"
    r"|abnormal\s+return\b"
    r"|cumulative\s+abnormal\s+return"
    r"|CAR\b)\b",
    re.IGNORECASE,
)

_EVENT_WINDOW_RE = re.compile(
    r"\b(?:event\s+window\s+of\s*(?:\[?\s*[-\d,\s]+\]?|[^\s,]+\s+days?)"
    r"|\[[-\d]+\s*,\s*[-\d]+\]"
    r"|pre[\s-]event\s+window"
    r"|estimation\s+window"
    r"|days?\s+(?:before|after|around)\s+(?:the\s+)?event)\b",
    re.IGNORECASE,
)


def validate_event_study_window_specification(
    parsed: ParsedManuscript,
    classification: ManuscriptClassification,
) -> ValidationResult:
    """Flag event studies without event window specification.

    Emits ``missing-event-window-specification`` (minor) when an event study
    design is described but the event window length is not specified.
    """
    _vid = "validate_event_study_window_specification"
    if classification.paper_type not in _EMPIRICAL_PAPER_TYPES:
        return ValidationResult(validator_name=_vid, findings=[])
    text = parsed.full_text or ""
    if not _EVENT_STUDY_TRIGGER_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    if _EVENT_WINDOW_RE.search(text):
        return ValidationResult(validator_name=_vid, findings=[])
    return ValidationResult(
        validator_name=_vid,
        findings=[
            Finding(
                code="missing-event-window-specification",
                message=(
                    "An event study design is described but the event window "
                    "specification is not reported."
                ),
                severity="minor",
                validator=_vid,
            )
        ],
    )
