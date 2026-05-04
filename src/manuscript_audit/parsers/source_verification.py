from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Protocol

from manuscript_audit.schemas.artifacts import (
    BibliographyConfidenceSummary,
    BibliographyEntry,
    RegistryMetadataRecord,
    SourceRecord,
    SourceRecordVerification,
    SourceRecordVerificationSummary,
)

YEAR_TOKEN_RE = re.compile(r"(19|20)\d{2}")
DOI_URL_RE = re.compile(r"https?://doi\.org/(.+)$", re.IGNORECASE)
TOKEN_RE = re.compile(r"[a-z0-9]+")

# Matching/scoring thresholds (centralized for calibration)
TITLE_EXACT_SCORE = 5.0
TITLE_SUBSTRING_SCORE = 4.0
TITLE_OVERLAP_THRESHOLDS = (0.75, 0.5, 0.3)
TITLE_OVERLAP_SCORES = (3.0, 2.0, 1.0)

# Selection thresholds
MIN_CONFIDENT_SCORE = 3.0
BEST_SECOND_SCORE_MARGIN = 1.0

# Additional scoring weights (centralized for easy calibration)
YEAR_MATCH_SCORE = 2.0
VENUE_EXACT_SCORE = 2.0
VENUE_PARTIAL_SCORE = 1.0
AUTHOR_FULL_OVERLAP_SCORE = 1.5
AUTHOR_PARTIAL_OVERLAP_SCORE = 0.5


class SourceRegistryLookupError(RuntimeError):
    """Raised when a registry provider fails to answer a lookup request."""


class SourceRegistryClient(Protocol):
    def lookup_doi(self, doi: str) -> RegistryMetadataRecord | None: ...

    def lookup_bibliographic_candidates(self, query: str) -> list[RegistryMetadataRecord]: ...


class FixtureSourceRegistryClient:
    def __init__(self, payload: dict) -> None:
        self._doi_records = {
            key.lower(): RegistryMetadataRecord.model_validate(value)
            for key, value in payload.get("doi", {}).items()
        }
        self._query_records: dict[str, list[RegistryMetadataRecord]] = {}
        for key, value in payload.get("query", {}).items():
            normalized_key = self._normalize_query(key)
            if isinstance(value, list):
                self._query_records[normalized_key] = [
                    RegistryMetadataRecord.model_validate(item) for item in value
                ]
            else:
                self._query_records[normalized_key] = [RegistryMetadataRecord.model_validate(value)]
        self._doi_errors = {
            item.lower() for item in payload.get("doi_errors", []) if isinstance(item, str)
        }
        self._query_errors = {
            self._normalize_query(item)
            for item in payload.get("query_errors", [])
            if isinstance(item, str)
        }

    @classmethod
    def from_json(cls, path: str | Path) -> FixtureSourceRegistryClient:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload)

    def lookup_doi(self, doi: str) -> RegistryMetadataRecord | None:
        normalized = doi.lower()
        if normalized in self._doi_errors:
            raise SourceRegistryLookupError(f"Fixture DOI lookup failed for {doi}")
        return self._doi_records.get(normalized)

    def lookup_bibliographic_candidates(self, query: str) -> list[RegistryMetadataRecord]:
        normalized = self._normalize_query(query)
        if normalized in self._query_errors:
            raise SourceRegistryLookupError(f"Fixture query lookup failed for {query}")
        return list(self._query_records.get(normalized, []))

    @staticmethod
    def _normalize_query(query: str) -> str:
        return " ".join(query.lower().split())


class CrossrefSourceRegistryClient:
    def __init__(self, mailto: str | None = None, timeout: float = 20.0) -> None:
        self.mailto = mailto
        self.timeout = timeout

    def _request_json(self, url: str) -> dict:
        headers = {"User-Agent": "manuscript-audit/0.1.0"}
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            urllib.error.HTTPError,
            TimeoutError,
            json.JSONDecodeError,
        ) as exc:
            raise SourceRegistryLookupError(str(exc)) from exc

    @staticmethod
    def _record_from_crossref_message(message: dict) -> RegistryMetadataRecord:
        title_values = message.get("title") or []
        container_values = message.get("container-title") or []
        authors = []
        for author in message.get("author") or []:
            given = author.get("given", "").strip()
            family = author.get("family", "").strip()
            full_name = " ".join(part for part in [given, family] if part)
            if full_name:
                authors.append(full_name)
        issued = message.get("issued") or {}
        year = None
        date_parts = issued.get("date-parts") or []
        if date_parts and date_parts[0]:
            year = str(date_parts[0][0])
        doi = message.get("DOI")
        url = message.get("URL")
        canonical_source_url = f"https://doi.org/{doi}" if doi else url
        return RegistryMetadataRecord(
            title=title_values[0] if title_values else None,
            authors=authors,
            year=year,
            venue=container_values[0] if container_values else None,
            doi=doi,
            url=url,
            provider="crossref_rest_api",
            source_url=canonical_source_url,
        )

    def lookup_doi(self, doi: str) -> RegistryMetadataRecord | None:
        encoded = urllib.parse.quote(doi, safe="")
        url = f"https://api.crossref.org/works/{encoded}"
        if self.mailto:
            url += f"?mailto={urllib.parse.quote(self.mailto, safe='@')}"
        payload = self._request_json(url)
        message = payload.get("message")
        if not message:
            return None
        return self._record_from_crossref_message(message)

    def lookup_bibliographic_candidates(self, query: str) -> list[RegistryMetadataRecord]:
        params = {"rows": "5", "query.bibliographic": query}
        if self.mailto:
            params["mailto"] = self.mailto
        url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
        payload = self._request_json(url)
        items = (payload.get("message") or {}).get("items") or []
        return [self._record_from_crossref_message(item) for item in items]


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return cleaned or None


def _normalize_year(value: str | None) -> str | None:
    if value is None:
        return None
    match = YEAR_TOKEN_RE.search(value)
    return match.group(0) if match else None


def _entry_venue(entry: BibliographyEntry) -> str | None:
    return entry.journal or entry.booktitle


def _entry_label(entry: BibliographyEntry) -> str:
    return entry.key or entry.title or entry.raw_text[:40]


def _entry_by_key_or_label(
    entries: list[BibliographyEntry],
    record: SourceRecord,
) -> BibliographyEntry | None:
    for entry in entries:
        if record.entry_key and entry.key == record.entry_key:
            return entry
    for entry in entries:
        if _entry_label(entry) == record.entry_label:
            return entry
    return None


def _compare_entry_to_registry(
    entry: BibliographyEntry,
    registry: RegistryMetadataRecord,
) -> list[str]:
    issues: list[str] = []
    entry_title = _normalize_text(entry.title)
    registry_title = _normalize_text(registry.title)
    if (
        entry_title
        and registry_title
        and entry_title not in registry_title
        and registry_title not in entry_title
    ):
        issues.append("title_mismatch")
    entry_year = _normalize_year(entry.year)
    registry_year = _normalize_year(registry.year)
    if entry_year and registry_year and entry_year != registry_year:
        issues.append("year_mismatch")
    entry_venue = _normalize_text(_entry_venue(entry))
    registry_venue = _normalize_text(registry.venue)
    if (
        entry_venue
        and registry_venue
        and entry_venue not in registry_venue
        and registry_venue not in entry_venue
    ):
        issues.append("venue_mismatch")
    if entry.doi and registry.doi:
        entry_doi = entry.doi.strip().removeprefix("doi:").removeprefix("https://doi.org/")
        registry_doi = registry.doi.strip().removeprefix("doi:").removeprefix("https://doi.org/")
        if entry_doi.lower() != registry_doi.lower():
            issues.append("doi_mismatch")
    return issues


def _token_set(value: str | None) -> set[str]:
    if value is None:
        return set()
    return set(TOKEN_RE.findall(value.lower()))


def _title_score(entry_title: str | None, candidate_title: str | None) -> float:
    normalized_entry = _normalize_text(entry_title)
    normalized_candidate = _normalize_text(candidate_title)
    if normalized_entry is None or normalized_candidate is None:
        return 0.0
    if normalized_entry == normalized_candidate:
        return TITLE_EXACT_SCORE
    if normalized_entry in normalized_candidate or normalized_candidate in normalized_entry:
        return TITLE_SUBSTRING_SCORE
    entry_tokens = _token_set(normalized_entry)
    candidate_tokens = _token_set(normalized_candidate)
    if not entry_tokens or not candidate_tokens:
        return 0.0
    overlap = len(entry_tokens & candidate_tokens) / len(entry_tokens | candidate_tokens)
    for threshold, score in zip(TITLE_OVERLAP_THRESHOLDS, TITLE_OVERLAP_SCORES, strict=True):
        if overlap >= threshold:
            return score
    return 0.0


def _similarity_score(entry: BibliographyEntry, candidate: RegistryMetadataRecord) -> float:
    score = _title_score(entry.title, candidate.title)
    entry_year = _normalize_year(entry.year)
    candidate_year = _normalize_year(candidate.year)
    if entry_year and candidate_year and entry_year == candidate_year:
        score += YEAR_MATCH_SCORE
    entry_venue = _normalize_text(_entry_venue(entry))
    candidate_venue = _normalize_text(candidate.venue)
    if entry_venue and candidate_venue:
        if entry_venue == candidate_venue:
            score += VENUE_EXACT_SCORE
        elif entry_venue in candidate_venue or candidate_venue in entry_venue:
            score += VENUE_PARTIAL_SCORE
    entry_author_tokens = _token_set(" ".join(entry.authors))
    candidate_author_tokens = _token_set(" ".join(candidate.authors))
    if entry_author_tokens and candidate_author_tokens:
        overlap = len(entry_author_tokens & candidate_author_tokens)
        if overlap >= 2:
            score += AUTHOR_FULL_OVERLAP_SCORE
        elif overlap == 1:
            score += AUTHOR_PARTIAL_OVERLAP_SCORE
    return score


def _select_best_candidate(
    entry: BibliographyEntry,
    candidates: list[RegistryMetadataRecord],
) -> tuple[RegistryMetadataRecord | None, float | None, list[str]]:
    if not candidates:
        return None, None, ["lookup_not_found"]
    scored = [(candidate, _similarity_score(entry, candidate)) for candidate in candidates]
    scored.sort(key=lambda item: item[1], reverse=True)
    best_candidate, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else None
    if best_score < MIN_CONFIDENT_SCORE:
        return None, None, ["no_confident_candidate_match"]
    if second_score is not None and best_score - second_score < BEST_SECOND_SCORE_MARGIN:
        return None, None, ["multiple_candidate_matches"]
    return best_candidate, best_score, []


def _doi_from_source_record(record: SourceRecord) -> str | None:
    if record.resolution_strategy == "doi" and record.identifier_value:
        return record.identifier_value
    if record.canonical_source_url:
        match = DOI_URL_RE.match(record.canonical_source_url)
        if match:
            return match.group(1)
    return None


def verify_source_records(
    entries: list[BibliographyEntry],
    source_records: list[SourceRecord],
    client: SourceRegistryClient,
) -> list[SourceRecordVerification]:
    verifications: list[SourceRecordVerification] = []
    for record in source_records:
        entry = _entry_by_key_or_label(entries, record)
        skip_record = (
            record.status == "insufficient_metadata"
            or record.resolution_strategy == "none"
            or entry is None
        )
        if skip_record:
            verifications.append(
                SourceRecordVerification(
                    entry_key=record.entry_key,
                    entry_label=record.entry_label,
                    strategy=record.resolution_strategy,
                    status="skipped",
                    canonical_source_url=record.canonical_source_url,
                    issues=["insufficient_metadata"],
                    candidate_count=0,
                    provenance="source_record_verification_skipped",
                )
            )
            continue

        if record.resolution_strategy == "url":
            verifications.append(
                SourceRecordVerification(
                    entry_key=record.entry_key,
                    entry_label=record.entry_label,
                    strategy=record.resolution_strategy,
                    status="verified_direct_url",
                    provider="direct_url",
                    canonical_source_url=record.canonical_source_url,
                    matched_title=entry.title,
                    matched_year=_normalize_year(entry.year),
                    matched_venue=_entry_venue(entry),
                    candidate_count=1,
                    adjudication="direct_url_passthrough",
                    provenance="source_record_direct_url_verification",
                )
            )
            continue

        try:
            if record.resolution_strategy == "doi":
                doi = _doi_from_source_record(record)
                registry_record = client.lookup_doi(doi) if doi else None
                candidate_count = 1 if registry_record is not None else 0
                selected_score = 7.0 if registry_record is not None else None
                adjudication = "doi_exact_lookup"
            else:
                candidates = client.lookup_bibliographic_candidates(record.lookup_query or "")
                candidate_count = len(candidates)
                registry_record, selected_score, selection_issues = _select_best_candidate(
                    entry,
                    candidates,
                )
                if registry_record is None:
                    status = (
                        "lookup_not_found"
                        if "lookup_not_found" in selection_issues
                        else "ambiguous_match"
                    )
                    verifications.append(
                        SourceRecordVerification(
                            entry_key=record.entry_key,
                            entry_label=record.entry_label,
                            strategy=record.resolution_strategy,
                            status=status,
                            canonical_source_url=record.canonical_source_url,
                            issues=selection_issues,
                            candidate_count=candidate_count,
                            adjudication="metadata_query_candidate_selection",
                            provenance="source_record_verification_lookup_failed",
                        )
                    )
                    continue
                adjudication = "metadata_query_candidate_selection"
        except SourceRegistryLookupError as exc:
            verifications.append(
                SourceRecordVerification(
                    entry_key=record.entry_key,
                    entry_label=record.entry_label,
                    strategy=record.resolution_strategy,
                    status="provider_error",
                    canonical_source_url=record.canonical_source_url,
                    issues=["provider_error"],
                    candidate_count=0,
                    adjudication="registry_lookup_failed",
                    provenance=str(exc),
                )
            )
            continue

        if registry_record is None:
            verifications.append(
                SourceRecordVerification(
                    entry_key=record.entry_key,
                    entry_label=record.entry_label,
                    strategy=record.resolution_strategy,
                    status="lookup_not_found",
                    canonical_source_url=record.canonical_source_url,
                    issues=["lookup_not_found"],
                    candidate_count=0,
                    adjudication=adjudication,
                    provenance="source_record_verification_lookup_failed",
                )
            )
            continue

        issues = _compare_entry_to_registry(entry, registry_record)
        status = "verified" if not issues else "metadata_mismatch"
        verifications.append(
            SourceRecordVerification(
                entry_key=record.entry_key,
                entry_label=record.entry_label,
                strategy=record.resolution_strategy,
                status=status,
                provider=registry_record.provider,
                canonical_source_url=registry_record.source_url or record.canonical_source_url,
                matched_title=registry_record.title,
                matched_year=registry_record.year,
                matched_venue=registry_record.venue,
                matched_doi=registry_record.doi,
                issues=issues,
                candidate_count=candidate_count,
                selected_match_score=selected_score,
                adjudication=adjudication,
                provenance="source_record_registry_verification",
            )
        )
    return verifications


def summarize_source_record_verifications(
    verifications: list[SourceRecordVerification],
) -> SourceRecordVerificationSummary:
    issue_type_counts: dict[str, int] = {}
    for item in verifications:
        for issue in item.issues:
            issue_type_counts[issue] = issue_type_counts.get(issue, 0) + 1
    return SourceRecordVerificationSummary(
        total_records=len(verifications),
        verified_count=sum(item.status == "verified" for item in verifications),
        verified_direct_url_count=sum(
            item.status == "verified_direct_url" for item in verifications
        ),
        metadata_mismatch_count=sum(item.status == "metadata_mismatch" for item in verifications),
        lookup_not_found_count=sum(item.status == "lookup_not_found" for item in verifications),
        ambiguous_match_count=sum(item.status == "ambiguous_match" for item in verifications),
        provider_error_count=sum(item.status == "provider_error" for item in verifications),
        skipped_count=sum(item.status == "skipped" for item in verifications),
        issue_type_counts=issue_type_counts,
    )


def build_bibliography_confidence_summary(
    source_records: list[SourceRecord],
    verifications: list[SourceRecordVerification] | None = None,
) -> BibliographyConfidenceSummary:
    total_entries = len(source_records)
    if verifications is None:
        deterministic_canonical_link_count = sum(
            item.status == "resolved_canonical_link" for item in source_records
        )
        ready_for_lookup_count = sum(item.status == "ready_for_lookup" for item in source_records)
        insufficient_metadata_count = sum(
            item.status == "insufficient_metadata" for item in source_records
        )
        manual_review_required_count = ready_for_lookup_count + insufficient_metadata_count
        score = max(0, 100 - (ready_for_lookup_count * 10) - (insufficient_metadata_count * 18))
        rationale: list[str] = []
        if ready_for_lookup_count:
            rationale.append(
                f"{ready_for_lookup_count} entries still require external lookup verification."
            )
        if insufficient_metadata_count:
            rationale.append(
                f"{insufficient_metadata_count} entries lack enough metadata "
                "for reliable verification."
            )
        if not rationale:
            rationale.append(
                "All bibliography entries resolve to deterministic canonical links "
                "without external lookup planning."
            )
        if insufficient_metadata_count >= 2 or manual_review_required_count >= total_entries or score < 60:
            level = "critical"
        elif insufficient_metadata_count >= 1 or ready_for_lookup_count > 0 or score < 80:
            level = "low"
        elif score < 92:
            level = "medium"
        else:
            level = "high"
        return BibliographyConfidenceSummary(
            total_entries=total_entries,
            verified_entry_count=0,
            verified_direct_url_count=0,
            deterministic_canonical_link_count=deterministic_canonical_link_count,
            manual_review_required_count=manual_review_required_count,
            mismatch_entry_count=0,
            ambiguous_entry_count=0,
            lookup_not_found_count=0,
            provider_error_count=0,
            insufficient_metadata_count=insufficient_metadata_count,
            confidence_score=score,
            confidence_level=level,
            basis="deterministic_planning",
            rationale=rationale,
        )

    verified_entry_count = sum(item.status == "verified" for item in verifications)
    verified_direct_url_count = sum(item.status == "verified_direct_url" for item in verifications)
    mismatch_entry_count = sum(item.status == "metadata_mismatch" for item in verifications)
    ambiguous_entry_count = sum(item.status == "ambiguous_match" for item in verifications)
    lookup_not_found_count = sum(item.status == "lookup_not_found" for item in verifications)
    provider_error_count = sum(item.status == "provider_error" for item in verifications)
    insufficient_metadata_count = sum(item.status == "skipped" for item in verifications)
    deterministic_canonical_link_count = sum(
        item.status == "resolved_canonical_link" for item in source_records
    )
    manual_review_required_count = (
        mismatch_entry_count
        + ambiguous_entry_count
        + lookup_not_found_count
        + provider_error_count
        + insufficient_metadata_count
    )

    score = 100
    score -= mismatch_entry_count * 25
    score -= ambiguous_entry_count * 18
    score -= provider_error_count * 22
    score -= lookup_not_found_count * 16
    score -= insufficient_metadata_count * 12
    score = max(0, score)

    rationale: list[str] = []
    if verified_entry_count or verified_direct_url_count:
        rationale.append(
            f"{verified_entry_count + verified_direct_url_count} entries have "
            "verification evidence without manual intervention."
        )
    if mismatch_entry_count:
        rationale.append(
            f"{mismatch_entry_count} verified entries disagree with bibliography metadata."
        )
    if ambiguous_entry_count:
        rationale.append(
            f"{ambiguous_entry_count} entries need manual adjudication among "
            "multiple registry candidates."
        )
    if lookup_not_found_count:
        rationale.append(
            f"{lookup_not_found_count} entries could not be matched to a source-of-record entry."
        )
    if provider_error_count:
        rationale.append(
            f"{provider_error_count} entries failed because the verification "
            "provider returned errors."
        )
    if insufficient_metadata_count:
        rationale.append(
            f"{insufficient_metadata_count} entries still lack enough metadata "
            "for meaningful verification."
        )
    if not rationale:
        rationale.append(
            "Bibliography verification did not surface any confidence-reducing issues."
        )

    if provider_error_count > 0 or mismatch_entry_count >= 2 or manual_review_required_count >= total_entries or score < 40:
        level = "critical"
    elif (
        mismatch_entry_count > 0
        or manual_review_required_count > max(1, total_entries // 2)
        or score < 65
    ):
        level = "low"
    elif manual_review_required_count > 0 or score < 90:
        level = "medium"
    else:
        level = "high"

    return BibliographyConfidenceSummary(
        total_entries=total_entries,
        verified_entry_count=verified_entry_count,
        verified_direct_url_count=verified_direct_url_count,
        deterministic_canonical_link_count=deterministic_canonical_link_count,
        manual_review_required_count=manual_review_required_count,
        mismatch_entry_count=mismatch_entry_count,
        ambiguous_entry_count=ambiguous_entry_count,
        lookup_not_found_count=lookup_not_found_count,
        provider_error_count=provider_error_count,
        insufficient_metadata_count=insufficient_metadata_count,
        confidence_score=score,
        confidence_level=level,
        basis="verified_source_records",
        rationale=rationale,
    )
