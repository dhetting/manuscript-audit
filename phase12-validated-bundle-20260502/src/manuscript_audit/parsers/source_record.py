from __future__ import annotations

import re

from manuscript_audit.schemas.artifacts import (
    BibliographyEntry,
    SourceRecord,
    SourceRecordCandidate,
    SourceRecordSummary,
)


def _entry_label(entry: BibliographyEntry) -> str:
    return entry.key or entry.title or entry.raw_text[:40]


def _lookup_query(entry: BibliographyEntry) -> str | None:
    pieces: list[str] = []
    if entry.title:
        pieces.append(entry.title)
    if entry.authors:
        pieces.extend(entry.authors[:2])
    if entry.year:
        pieces.append(entry.year)
    venue = entry.journal or entry.booktitle
    if venue:
        pieces.append(venue)
    query = " ".join(piece.strip() for piece in pieces if piece and piece.strip())
    return query or None


def _metadata_completeness(entry: BibliographyEntry) -> int:
    return sum(
        bool(value)
        for value in [
            entry.title,
            entry.authors,
            entry.year,
            entry.journal or entry.booktitle,
        ]
    )


DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)


def _normalize_doi(doi: str | None) -> tuple[str | None, str | None]:
    if doi is None:
        return None, None
    cleaned = doi.strip()
    cleaned = cleaned.removeprefix("https://doi.org/")
    cleaned = cleaned.removeprefix("http://doi.org/")
    cleaned = cleaned.removeprefix("doi:")
    cleaned = cleaned.strip()
    if not cleaned or DOI_RE.fullmatch(cleaned) is None:
        return None, None
    return cleaned, f"https://doi.org/{cleaned}"


def _normalize_url(url: str | None) -> str | None:
    if url is None:
        return None
    cleaned = url.strip()
    if not cleaned:
        return None
    if cleaned.startswith("www."):
        return f"https://{cleaned}"
    return cleaned


def build_source_record_candidates(
    entries: list[BibliographyEntry],
) -> list[SourceRecordCandidate]:
    candidates: list[SourceRecordCandidate] = []
    for entry in entries:
        completeness = _metadata_completeness(entry)
        label = _entry_label(entry)
        normalized_doi, _ = _normalize_doi(entry.doi)
        normalized_url = _normalize_url(entry.url)
        if normalized_doi:
            candidates.append(
                SourceRecordCandidate(
                    entry_key=entry.key,
                    entry_label=label,
                    status="ready_via_doi",
                    preferred_identifier_type="doi",
                    identifier_value=normalized_doi,
                    metadata_completeness=completeness,
                    rationale="A DOI is present for direct source-of-record resolution.",
                )
            )
            continue
        if normalized_url:
            candidates.append(
                SourceRecordCandidate(
                    entry_key=entry.key,
                    entry_label=label,
                    status="ready_via_url",
                    preferred_identifier_type="url",
                    identifier_value=normalized_url,
                    metadata_completeness=completeness,
                    rationale="A URL is present for direct source-of-record follow-up.",
                )
            )
            continue
        query = _lookup_query(entry)
        if query is not None and entry.title and entry.year:
            candidates.append(
                SourceRecordCandidate(
                    entry_key=entry.key,
                    entry_label=label,
                    status="needs_metadata_lookup",
                    preferred_identifier_type="metadata_query",
                    lookup_query=query,
                    metadata_completeness=completeness,
                    rationale=(
                        "No DOI or URL is present, but the entry has enough metadata "
                        "for deterministic source-of-record lookup planning."
                    ),
                )
            )
            continue
        candidates.append(
            SourceRecordCandidate(
                entry_key=entry.key,
                entry_label=label,
                status="insufficient_metadata",
                preferred_identifier_type="none",
                metadata_completeness=completeness,
                rationale=(
                    "The entry is missing stable identifiers and lacks enough metadata "
                    "for reliable source-of-record lookup planning."
                ),
            )
        )
    return candidates


def build_source_records(entries: list[BibliographyEntry]) -> list[SourceRecord]:
    records: list[SourceRecord] = []
    for entry in entries:
        label = _entry_label(entry)
        completeness = _metadata_completeness(entry)
        normalized_doi, canonical_doi_url = _normalize_doi(entry.doi)
        normalized_url = _normalize_url(entry.url)
        if canonical_doi_url is not None and normalized_doi is not None:
            records.append(
                SourceRecord(
                    entry_key=entry.key,
                    entry_label=label,
                    resolution_strategy="doi",
                    status="resolved_canonical_link",
                    canonical_source_url=canonical_doi_url,
                    identifier_value=normalized_doi,
                    metadata_completeness=completeness,
                    provenance="deterministic_doi_normalization",
                )
            )
            continue
        if normalized_url is not None:
            records.append(
                SourceRecord(
                    entry_key=entry.key,
                    entry_label=label,
                    resolution_strategy="url",
                    status="resolved_canonical_link",
                    canonical_source_url=normalized_url,
                    identifier_value=normalized_url,
                    metadata_completeness=completeness,
                    provenance="deterministic_url_capture",
                )
            )
            continue
        query = _lookup_query(entry)
        if query is not None and entry.title and entry.year:
            records.append(
                SourceRecord(
                    entry_key=entry.key,
                    entry_label=label,
                    resolution_strategy="metadata_query",
                    status="ready_for_lookup",
                    lookup_query=query,
                    metadata_completeness=completeness,
                    provenance="deterministic_metadata_query_plan",
                )
            )
            continue
        records.append(
            SourceRecord(
                entry_key=entry.key,
                entry_label=label,
                resolution_strategy="none",
                status="insufficient_metadata",
                metadata_completeness=completeness,
                provenance="deterministic_metadata_gap_detection",
            )
        )
    return records


def summarize_source_records(records: list[SourceRecord]) -> SourceRecordSummary:
    return SourceRecordSummary(
        total_entries=len(records),
        resolved_canonical_link_count=sum(
            record.status == "resolved_canonical_link" for record in records
        ),
        ready_for_lookup_count=sum(record.status == "ready_for_lookup" for record in records),
        insufficient_metadata_count=sum(
            record.status == "insufficient_metadata" for record in records
        ),
    )
