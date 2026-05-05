from __future__ import annotations

from pathlib import Path

from manuscript_audit.parsers.latex import parse_latex_manuscript
from manuscript_audit.parsers.markdown import parse_markdown_manuscript
from manuscript_audit.schemas.artifacts import ParsedManuscript


def parse_manuscript(path: str | Path) -> ParsedManuscript:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return parse_markdown_manuscript(file_path)
    if suffix in {".tex", ".latex"}:
        return parse_latex_manuscript(file_path)
    raise ValueError(f"Unsupported manuscript format: {suffix or '<no suffix>'}")
