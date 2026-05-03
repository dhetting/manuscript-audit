from __future__ import annotations

import re
from pathlib import Path

from manuscript_audit.schemas.artifacts import BibliographyEntry

ENTRY_RE = re.compile(
    r"@(\w+)\s*\{\s*([^,]+),(.*?)\n\}",
    re.DOTALL,
)
FIELD_RE = re.compile(r"(\w+)\s*=\s*\{(.*?)\}", re.DOTALL)


def parse_bibtex(path: str | Path) -> list[BibliographyEntry]:
    raw_text = Path(path).read_text(encoding="utf-8")
    entries: list[BibliographyEntry] = []
    for entry_type, key, body in ENTRY_RE.findall(raw_text):
        fields = {
            field.lower(): value.strip().replace("\n", " ")
            for field, value in FIELD_RE.findall(body)
        }
        author_field = fields.get("author", "")
        authors = [piece.strip() for piece in author_field.split(" and ") if piece.strip()]
        entries.append(
            BibliographyEntry(
                key=key.strip(),
                entry_type=entry_type.lower().strip(),
                raw_text=body.strip(),
                title=fields.get("title"),
                authors=authors,
                year=fields.get("year"),
                journal=fields.get("journal"),
                booktitle=fields.get("booktitle"),
                doi=fields.get("doi"),
                url=fields.get("url"),
                source="bibtex",
            )
        )
    return entries
