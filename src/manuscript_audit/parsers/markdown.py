from __future__ import annotations

import re
from pathlib import Path

from manuscript_audit.schemas.artifacts import ParsedManuscript, Section

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
BRACKET_CITATION_RE = re.compile(r"\[@([^\]]+)\]")
LATEX_CITATION_RE = re.compile(r"\\cite[t|p]?\{([^}]+)\}")
FIGURE_RE = re.compile(r"\bFigure\s+\d+\b", re.IGNORECASE)
TABLE_RE = re.compile(r"\bTable\s+\d+\b", re.IGNORECASE)
EQUATION_RE = re.compile(r"\$\$(.*?)\$\$", re.DOTALL)


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
        bibliography_entries = [
            line.strip("- ").strip()
            for line in reference_section.body.splitlines()
            if line.strip().startswith("-")
        ]
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
        equation_blocks=[match.strip() for match in EQUATION_RE.findall(raw_text)],
        reference_section_present=reference_section is not None,
        bibliography_entries=bibliography_entries,
    )
