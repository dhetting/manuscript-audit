from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Section(BaseModel):
    title: str
    level: int
    body: str = ""
    start_line: int | None = None


class BibliographyEntry(BaseModel):
    key: str | None = None
    entry_type: str | None = None
    raw_text: str
    title: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: str | None = None
    journal: str | None = None
    booktitle: str | None = None
    doi: str | None = None
    url: str | None = None
    source: Literal["markdown_reference_list", "bibtex"]


class ParsedManuscript(BaseModel):
    manuscript_id: str
    source_path: str
    source_format: Literal["markdown", "latex", "text"]
    title: str
    abstract: str = ""
    sections: list[Section] = Field(default_factory=list)
    full_text: str
    citation_keys: list[str] = Field(default_factory=list)
    figure_mentions: list[str] = Field(default_factory=list)
    table_mentions: list[str] = Field(default_factory=list)
    figure_definitions: list[str] = Field(default_factory=list)
    table_definitions: list[str] = Field(default_factory=list)
    equation_blocks: list[str] = Field(default_factory=list)
    reference_section_present: bool = False
    bibliography_entries: list[BibliographyEntry] = Field(default_factory=list)

    @property
    def section_titles(self) -> list[str]:
        return [section.title for section in self.sections]
