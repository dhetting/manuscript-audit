from __future__ import annotations

import re
from pathlib import Path

from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

SECTION_RE = re.compile(r"\\(section|subsection|subsubsection)\{([^}]+)\}")
TITLE_RE = re.compile(r"\\title\{([^}]+)\}")
ABSTRACT_RE = re.compile(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", re.DOTALL)
CITE_RE = re.compile(r"\\cite[t|p]?\{([^}]+)\}")
FIGURE_NUMERIC_RE = re.compile(r"\bFigure\s+\d+\b", re.IGNORECASE)
TABLE_NUMERIC_RE = re.compile(r"\bTable\s+\d+\b", re.IGNORECASE)
EQUATION_NUMERIC_RE = re.compile(r"\bEquation\s+\d+\b", re.IGNORECASE)
FIGURE_REF_RE = re.compile(r"\\ref\{(fig:[^}]+)\}")
TABLE_REF_RE = re.compile(r"\\ref\{(tab:[^}]+)\}")
EQUATION_REF_RE = re.compile(r"\\(?:eqref|ref)\{(eq:[^}]+)\}")
FIGURE_ENV_RE = re.compile(r"\\begin\{figure\}(.*?)\\end\{figure\}", re.DOTALL)
TABLE_ENV_RE = re.compile(r"\\begin\{table\}(.*?)\\end\{table\}", re.DOTALL)
CAPTION_RE = re.compile(r"\\caption\{([^}]+)\}")
LABEL_RE = re.compile(r"\\label\{([^}]+)\}")
BIB_RE = re.compile(r"\\bibliography\{([^}]+)\}")
EQUATION_RE = re.compile(r"\\begin\{equation\}(.*?)\\end\{equation\}", re.DOTALL)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "manuscript"


def _extract_sections(text: str) -> list[Section]:
    matches = list(SECTION_RE.finditer(text))
    sections: list[Section] = []
    level_map = {"section": 1, "subsection": 2, "subsubsection": 3}
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section_kind, title = match.groups()
        body = text[start:end].strip()
        start_line = text[: match.start()].count("\n") + 1
        sections.append(
            Section(
                title=title.strip(),
                level=level_map[section_kind],
                body=body,
                start_line=start_line,
            )
        )
    return sections


def _extract_environment_definitions(
    raw_text: str,
    environment_regex: re.Pattern[str],
    label_prefix: str,
) -> list[str]:
    definitions: list[str] = []
    for block in environment_regex.findall(raw_text):
        labels = [label for label in LABEL_RE.findall(block) if label.startswith(label_prefix)]
        if labels:
            definitions.extend(labels)
            continue
        captions = [caption.strip() for caption in CAPTION_RE.findall(block)]
        definitions.extend(captions)
    return definitions


def _extract_equation_definitions(raw_text: str) -> tuple[list[str], list[str]]:
    equation_blocks = [match.strip() for match in EQUATION_RE.findall(raw_text)]
    equation_definitions: list[str] = []
    for block in equation_blocks:
        labels = [label for label in LABEL_RE.findall(block) if label.startswith("eq:")]
        if labels:
            equation_definitions.extend(labels)
    return equation_blocks, equation_definitions


def parse_latex_manuscript(path: str | Path) -> ParsedManuscript:
    file_path = Path(path)
    raw_text = file_path.read_text(encoding="utf-8")
    sections = _extract_sections(raw_text)
    title_match = TITLE_RE.search(raw_text)
    title = title_match.group(1).strip() if title_match else file_path.stem
    abstract_match = ABSTRACT_RE.search(raw_text)
    abstract = abstract_match.group(1).strip() if abstract_match else ""
    citation_keys: list[str] = []
    for raw_match in CITE_RE.findall(raw_text):
        citation_keys.extend(piece.strip() for piece in raw_match.split(",") if piece.strip())
    bibliography_present = BIB_RE.search(raw_text) is not None or any(
        section.title.lower() in {"references", "bibliography"} for section in sections
    )
    equation_blocks, equation_definitions = _extract_equation_definitions(raw_text)
    figure_mentions = FIGURE_REF_RE.findall(raw_text) + FIGURE_NUMERIC_RE.findall(raw_text)
    table_mentions = TABLE_REF_RE.findall(raw_text) + TABLE_NUMERIC_RE.findall(raw_text)
    equation_mentions = EQUATION_REF_RE.findall(raw_text) + EQUATION_NUMERIC_RE.findall(raw_text)
    figure_definitions = _extract_environment_definitions(raw_text, FIGURE_ENV_RE, "fig:")
    table_definitions = _extract_environment_definitions(raw_text, TABLE_ENV_RE, "tab:")
    return ParsedManuscript(
        manuscript_id=_slugify(title),
        source_path=str(file_path),
        source_format="latex",
        title=title,
        abstract=abstract,
        sections=sections,
        full_text=raw_text,
        citation_keys=sorted(dict.fromkeys(citation_keys)),
        figure_mentions=sorted(dict.fromkeys(figure_mentions)),
        table_mentions=sorted(dict.fromkeys(table_mentions)),
        equation_mentions=sorted(dict.fromkeys(equation_mentions)),
        figure_definitions=figure_definitions,
        table_definitions=table_definitions,
        equation_definitions=equation_definitions,
        equation_blocks=equation_blocks,
        reference_section_present=bibliography_present,
        bibliography_entries=[],
    )
