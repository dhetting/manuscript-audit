from __future__ import annotations

import re
from pathlib import Path

from manuscript_audit.schemas.artifacts import BibliographyEntry, ParsedManuscript, Section

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
BRACKET_CITATION_RE = re.compile(r"\[@([^\]]+)\]")
LATEX_CITATION_RE = re.compile(r"\\cite[t|p]?\{([^}]+)\}")
FIGURE_RE = re.compile(r"\bFigure\s+\d+\b", re.IGNORECASE)
TABLE_RE = re.compile(r"\bTable\s+\d+\b", re.IGNORECASE)
EQUATION_MENTION_RE = re.compile(r"\bEquation\s+\d+\b", re.IGNORECASE)
FIGURE_DEF_RE = re.compile(r"^Figure\s+\d+\s*[:.-]", re.IGNORECASE)
TABLE_DEF_RE = re.compile(r"^Table\s+\d+\s*[:.-]", re.IGNORECASE)
EQUATION_DEF_RE = re.compile(r"^Equation\s+\d+\s*[:.-]", re.IGNORECASE)
EQUATION_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)
YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "manuscript"


def _extract_sections(lines: list[str]) -> list[Section]:
    headings: list[tuple[int, str, int]] = []
    for index, line in enumerate(lines, start=1):
        match = HEADING_RE.match(line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip(), index))
    sections: list[Section] = []
    for i, (level, title, start_line) in enumerate(headings):
        start_index = start_line
        end_index = headings[i + 1][2] - 1 if i + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start_index:end_index]).strip()
        sections.append(Section(title=title, level=level, body=body, start_line=start_line))
    return sections


def _extract_citation_keys(text: str) -> list[str]:
    keys: list[str] = []
    for raw_match in BRACKET_CITATION_RE.findall(text):
        pieces = [piece.strip().lstrip("@") for piece in raw_match.split(";")]
        keys.extend(piece for piece in pieces if piece)
    for raw_match in LATEX_CITATION_RE.findall(text):
        pieces = [piece.strip() for piece in raw_match.split(",")]
        keys.extend(piece for piece in pieces if piece)
    return sorted(dict.fromkeys(keys))


def _extract_markdown_bibliography_entries(reference_body: str) -> list[BibliographyEntry]:
    entries: list[BibliographyEntry] = []
    for line in reference_body.splitlines():
        if not line.strip().startswith("-"):
            continue
        raw_text = line.strip().lstrip("-").strip()
        year_match = YEAR_RE.search(raw_text)
        title = raw_text.split(".", maxsplit=2)[1].strip() if raw_text.count(".") >= 2 else None
        entries.append(
            BibliographyEntry(
                raw_text=raw_text,
                title=title,
                year=year_match.group(0) if year_match else None,
                source="markdown_reference_list",
            )
        )
    return entries


def parse_markdown_manuscript(path: str | Path) -> ParsedManuscript:
    file_path = Path(path)
    raw_text = file_path.read_text(encoding="utf-8")
    lines = raw_text.splitlines()
    sections = _extract_sections(lines)
    title = sections[0].title if sections and sections[0].level == 1 else file_path.stem
    abstract = next(
        (section.body for section in sections if section.title.lower() == "abstract"),
        "",
    )
    reference_section = next(
        (
            section
            for section in sections
            if section.title.lower() in {"references", "bibliography"}
        ),
        None,
    )
    bibliography_entries = []
    if reference_section:
        bibliography_entries = _extract_markdown_bibliography_entries(reference_section.body)
    figure_definitions = [line.strip() for line in lines if FIGURE_DEF_RE.match(line.strip())]
    table_definitions = [line.strip() for line in lines if TABLE_DEF_RE.match(line.strip())]
    equation_definitions = [line.strip() for line in lines if EQUATION_DEF_RE.match(line.strip())]
    return ParsedManuscript(
        manuscript_id=_slugify(title),
        source_path=str(file_path),
        source_format="markdown",
        title=title,
        abstract=abstract,
        sections=sections,
        full_text=raw_text,
        citation_keys=_extract_citation_keys(raw_text),
        figure_mentions=sorted(dict.fromkeys(FIGURE_RE.findall(raw_text))),
        table_mentions=sorted(dict.fromkeys(TABLE_RE.findall(raw_text))),
        equation_mentions=sorted(dict.fromkeys(EQUATION_MENTION_RE.findall(raw_text))),
        figure_definitions=figure_definitions,
        table_definitions=table_definitions,
        equation_definitions=equation_definitions,
        equation_blocks=[match.strip() for match in EQUATION_RE.findall(raw_text)],
        reference_section_present=reference_section is not None,
        bibliography_entries=bibliography_entries,
    )
