from __future__ import annotations

from pathlib import Path
from typing import Literal

import typer

from manuscript_audit.parsers import (
    build_source_record_candidates,
    build_source_records,
    extract_notation_summary,
    parse_bibtex,
    parse_manuscript,
    summarize_source_records,
)
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.utils.io import write_json, write_yaml
from manuscript_audit.validators import run_deterministic_validators

app = typer.Typer(add_completion=False, no_args_is_help=True)


def _format_audit_summary(report) -> str:
    """One-line-per-category summary for audit-core / audit-standard."""
    from collections import Counter

    counts: Counter = Counter(report.validation_suite.severity_counts)
    if report.agent_suite:
        counts.update(report.agent_suite.severity_counts)
    total = sum(counts.values())
    parts = "  ".join(
        f"{sev}={counts.get(sev, 0)}" for sev in ("fatal", "major", "moderate", "minor")
    )
    pathway = report.classification.pathway
    stack = report.classification.recommended_stack
    n_pri = len(report.revision_priorities)
    return (
        f"  findings:  {parts}  ({total} total)\n"
        f"  routing:   {pathway} | {stack} stack | {n_pri} priorities"
    )


def _format_sources_summary(report) -> str:
    """One-line-per-category summary for verify-sources."""
    s = report.summary
    verified = s.verified_count + s.verified_direct_url_count
    issues = (
        s.metadata_mismatch_count
        + s.lookup_not_found_count
        + s.ambiguous_match_count
        + s.provider_error_count
    )
    conf = (
        report.bibliography_confidence_summary.confidence_level
        if report.bibliography_confidence_summary
        else "n/a"
    )
    n_pri = len(report.revision_priorities)
    return (
        f"  sources:    {s.total_records} total  {verified} verified  "
        f"{issues} issues  skipped={s.skipped_count}\n"
        f"  confidence: {conf} | {n_pri} priorities"
    )


def _format_revision_summary(report) -> str:
    """One-line summary for verify-revision."""
    route = "yes" if report.route_changed else "no"
    return (
        f"  resolved={len(report.resolved_findings)}  "
        f"persistent={len(report.persistent_findings)}  "
        f"introduced={len(report.new_findings)}  "
        f"route-changed={route}"
    )


def _prepare_parsed_manuscript(manuscript_path: Path):
    parsed = parse_manuscript(manuscript_path)
    bib_path = manuscript_path.with_suffix(".bib")
    if bib_path.exists():
        parsed.bibliography_entries = parse_bibtex(bib_path)
        parsed.reference_section_present = True
    source_record_candidates = build_source_record_candidates(parsed.bibliography_entries)
    source_records = build_source_records(parsed.bibliography_entries)
    source_record_summary = summarize_source_records(source_records)
    notation_summary = extract_notation_summary(parsed)
    return (
        parsed,
        source_record_candidates,
        source_records,
        source_record_summary,
        notation_summary,
    )


@app.command("parse")
def parse_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    (
        parsed,
        source_record_candidates,
        source_records,
        source_record_summary,
        notation_summary,
    ) = _prepare_parsed_manuscript(manuscript_path)
    write_json(output_dir / "parsed" / "manuscript.json", parsed)
    write_json(output_dir / "parsed" / "references.json", parsed.bibliography_entries)
    write_json(
        output_dir / "parsed" / "source_record_candidates.json",
        source_record_candidates,
    )
    write_json(output_dir / "parsed" / "source_records.json", source_records)
    write_json(
        output_dir / "parsed" / "source_record_summary.json",
        source_record_summary,
    )
    write_json(output_dir / "parsed" / "notation_summary.json", notation_summary)


@app.command("route")
def route_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    parsed, _, _, _, _ = _prepare_parsed_manuscript(manuscript_path)
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    write_json(output_dir / "parsed" / "classification.json", classification)
    write_yaml(output_dir / "routing" / "module_routing.yaml", module_routing)
    write_yaml(output_dir / "routing" / "domain_routing.yaml", domain_routing)


@app.command("validate")
def validate_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    parsed, _, _, _, _ = _prepare_parsed_manuscript(manuscript_path)
    classification, _, _ = build_routing_tables(parsed)
    results = run_deterministic_validators(parsed, classification)
    write_json(output_dir / "findings" / "deterministic_validators.json", results)


@app.command("audit-core")
def audit_core_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
    db_path: str = typer.Option("data/working/run_store.duckdb", "--db-path"),
) -> None:
    from manuscript_audit.workflows import run_core_audit_workflow

    report = run_core_audit_workflow(manuscript_path, output_dir=output_dir, db_path=db_path)
    typer.echo(f"Completed run {report.run_id} for {report.manuscript_id}")
    typer.echo(_format_audit_summary(report))


@app.command("audit-standard")
def audit_standard_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
    db_path: str = typer.Option("data/working/run_store.duckdb", "--db-path"),
    source_verification_provider: Literal["fixture", "crossref"] | None = typer.Option(
        None,
        "--source-verification-provider",
    ),
    registry_fixture: Path | None = typer.Option(None, "--registry-fixture"),
    mailto: str | None = typer.Option(None, "--mailto"),
) -> None:
    from manuscript_audit.workflows import run_standard_audit_workflow

    if source_verification_provider == "fixture" and registry_fixture is None:
        raise typer.BadParameter(
            "--registry-fixture is required when --source-verification-provider fixture"
        )
    report = run_standard_audit_workflow(
        manuscript_path,
        output_dir=output_dir,
        db_path=db_path,
        source_verification_provider=source_verification_provider,
        registry_fixture_path=registry_fixture,
        mailto=mailto,
    )
    typer.echo(f"Completed standard run {report.run_id} for {report.manuscript_id}")
    typer.echo(_format_audit_summary(report))


@app.command("verify-revision")
def verify_revision_command(
    old_manuscript_path: Path,
    new_manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
    db_path: str = typer.Option("data/working/run_store.duckdb", "--db-path"),
) -> None:
    from manuscript_audit.workflows import run_revision_verification_workflow

    report = run_revision_verification_workflow(
        old_manuscript_path=old_manuscript_path,
        new_manuscript_path=new_manuscript_path,
        output_dir=output_dir,
        db_path=db_path,
    )
    typer.echo(f"Completed revision verification {report.run_id} for {report.new_manuscript_id}")
    typer.echo(_format_revision_summary(report))


@app.command("verify-sources")
def verify_sources_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
    db_path: str = typer.Option("data/working/run_store.duckdb", "--db-path"),
    provider: Literal["fixture", "crossref"] = typer.Option("fixture", "--provider"),
    registry_fixture: Path | None = typer.Option(None, "--registry-fixture"),
    mailto: str | None = typer.Option(None, "--mailto"),
) -> None:
    from manuscript_audit.workflows import run_source_record_verification_workflow

    if provider == "fixture" and registry_fixture is None:
        raise typer.BadParameter("--registry-fixture is required when --provider fixture")
    report = run_source_record_verification_workflow(
        manuscript_path=manuscript_path,
        output_dir=output_dir,
        db_path=db_path,
        provider=provider,
        registry_fixture_path=registry_fixture,
        mailto=mailto,
    )
    typer.echo(f"Completed source verification {report.run_id} for {report.manuscript_id}")
    typer.echo(_format_sources_summary(report))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
