from __future__ import annotations

from manuscript_audit.schemas.artifacts import BibliographyEntry, SourceRecordCandidate


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


def build_source_record_candidates(
    entries: list[BibliographyEntry],
) -> list[SourceRecordCandidate]:
    candidates: list[SourceRecordCandidate] = []
    for entry in entries:
        completeness = sum(
            bool(value)
            for value in [
                entry.title,
                entry.authors,
                entry.year,
                entry.journal or entry.booktitle,
            ]
        )
        label = _entry_label(entry)
        if entry.doi:
            candidates.append(
                SourceRecordCandidate(
                    entry_key=entry.key,
                    entry_label=label,
                    status="ready_via_doi",
                    preferred_identifier_type="doi",
                    identifier_value=entry.doi,
                    metadata_completeness=completeness,
                    rationale="A DOI is present for direct source-of-record resolution.",
                )
            )
            continue
        if entry.url:
            candidates.append(
                SourceRecordCandidate(
                    entry_key=entry.key,
                    entry_label=label,
                    status="ready_via_url",
                    preferred_identifier_type="url",
                    identifier_value=entry.url,
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
