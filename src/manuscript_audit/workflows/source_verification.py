from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from manuscript_audit.config import DEFAULT_DB_PATH
from manuscript_audit.parsers import (
    CrossrefSourceRegistryClient,
    FixtureSourceRegistryClient,
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    parse_manuscript,
    summarize_source_record_verifications,
    verify_source_records,
)
from manuscript_audit.reports import (
    render_source_record_verification_report,
    synthesize_source_record_verification_report,
)
from manuscript_audit.schemas.findings import SourceRecordVerificationReport
from manuscript_audit.storage import DuckDBRunStore
from manuscript_audit.utils.io import ensure_dir, write_json

Provider = Literal["fixture", "crossref"]


def _run_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def run_source_record_verification_workflow(
    manuscript_path: str | Path,
    output_dir: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
    provider: Provider = "fixture",
    registry_fixture_path: str | Path | None = None,
    mailto: str | None = None,
) -> SourceRecordVerificationReport:
    output_path = Path(output_dir)
    parsed_dir = ensure_dir(output_path / "parsed")
    reports_dir = ensure_dir(output_path / "reports")

    parsed = parse_manuscript(manuscript_path)
    bib_path = Path(manuscript_path).with_suffix(".bib")
    if bib_path.exists():
        parsed.bibliography_entries = parse_bibtex(bib_path)
        parsed.reference_section_present = True
    source_records = build_source_records(parsed.bibliography_entries)

    if provider == "fixture":
        if registry_fixture_path is None:
            raise ValueError("registry_fixture_path is required for fixture verification")
        client = FixtureSourceRegistryClient.from_json(registry_fixture_path)
        provider_name = "fixture_source_registry"
    else:
        client = CrossrefSourceRegistryClient(mailto=mailto)
        provider_name = "crossref_rest_api"

    verifications = verify_source_records(parsed.bibliography_entries, source_records, client)
    summary = summarize_source_record_verifications(verifications)
    bibliography_confidence_summary = build_bibliography_confidence_summary(
        source_records,
        verifications,
    )

    run_id = _run_id()
    report = synthesize_source_record_verification_report(
        SourceRecordVerificationReport(
            run_id=run_id,
            manuscript_id=parsed.manuscript_id,
            verification_provider=provider_name,
            verifications=verifications,
            summary=summary,
            bibliography_confidence_summary=bibliography_confidence_summary,
        )
    )

    write_json(parsed_dir / "references.json", parsed.bibliography_entries)
    write_json(parsed_dir / "source_records.json", source_records)
    write_json(parsed_dir / "source_record_verifications.json", verifications)
    write_json(parsed_dir / "source_record_verification_summary.json", summary)
    write_json(parsed_dir / "bibliography_confidence_summary.json", bibliography_confidence_summary)
    write_json(reports_dir / "source_record_verification_report.json", report)
    (reports_dir / "source_record_verification_report.md").write_text(
        render_source_record_verification_report(report),
        encoding="utf-8",
    )

    store = DuckDBRunStore(db_path)
    store.record_run(run_id, parsed.manuscript_id, str(manuscript_path), str(output_path))
    store.record_parsed_artifact(run_id, "references", parsed.bibliography_entries)
    store.record_parsed_artifact(run_id, "source_records", source_records)
    store.record_parsed_artifact(run_id, "source_record_verifications", verifications)
    store.record_parsed_artifact(
        run_id,
        "source_record_verification_summary",
        summary,
    )
    store.record_parsed_artifact(
        run_id,
        "bibliography_confidence_summary",
        bibliography_confidence_summary,
    )
    store.record_report(run_id, "source_record_verification_report", report)
    store.close()
    return report
