from __future__ import annotations

import re
from pathlib import Path

from manuscript_audit.schemas.artifacts import BibliographyEntry

ENTRY_RE = re.compile(r"@(\w+)\s*\{\s*([^,]+)\s*,(.*?)\n\}\s*", re.DOTALL)
FIELD_RE = re.compile(r"(\w+)\s*=\s*[\{\"](.*?)[\}\"]\s*(?:,|$)", re.DOTALL)


def parse_bibtex(path: str | Path) -> list[BibliographyEntry]:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    entries: list[BibliographyEntry] = []
    for entry_type, key, body in ENTRY_RE.findall(text):
        fields = {
            name.lower(): value.strip().replace("\n", " ") for name, value in FIELD_RE.findall(body)
        }
        authors = [
            author.strip() for author in fields.get("author", "").split(" and ") if author.strip()
        ]
        entries.append(
            BibliographyEntry(
                key=key.strip(),
                entry_type=entry_type.strip().lower(),
                raw_text=f"@{entry_type}{{{key}, {body.strip()}}}",
                title=fields.get("title"),
                authors=authors,
                year=fields.get("year"),
                source="bibtex",
            )
        )
    return entries
