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


class SourceRecordCandidate(BaseModel):
    entry_key: str | None = None
    entry_label: str
    status: Literal[
        "ready_via_doi",
        "ready_via_url",
        "needs_metadata_lookup",
        "insufficient_metadata",
    ]
    preferred_identifier_type: Literal["doi", "url", "metadata_query", "none"]
    identifier_value: str | None = None
    lookup_query: str | None = None
    metadata_completeness: int
    rationale: str


class SourceRecord(BaseModel):
    entry_key: str | None = None
    entry_label: str
    resolution_strategy: Literal["doi", "url", "metadata_query", "none"]
    status: Literal[
        "resolved_canonical_link",
        "ready_for_lookup",
        "insufficient_metadata",
    ]
    canonical_source_url: str | None = None
    identifier_value: str | None = None
    lookup_query: str | None = None
    metadata_completeness: int
    provenance: str


class SourceRecordSummary(BaseModel):
    total_entries: int
    resolved_canonical_link_count: int
    ready_for_lookup_count: int
    insufficient_metadata_count: int


class NotationSymbol(BaseModel):
    symbol: str
    used_in_equations: bool
    defined_in_text: bool
    definition_hint: str | None = None


class NotationSummary(BaseModel):
    equation_symbol_count: int
    defined_symbol_count: int
    undefined_symbols: list[str] = Field(default_factory=list)
    symbols: list[NotationSymbol] = Field(default_factory=list)


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
    equation_mentions: list[str] = Field(default_factory=list)
    figure_definitions: list[str] = Field(default_factory=list)
    table_definitions: list[str] = Field(default_factory=list)
    equation_definitions: list[str] = Field(default_factory=list)
    equation_blocks: list[str] = Field(default_factory=list)
    reference_section_present: bool = False
    bibliography_entries: list[BibliographyEntry] = Field(default_factory=list)

    @property
    def section_titles(self) -> list[str]:
        return [section.title for section in self.sections]
