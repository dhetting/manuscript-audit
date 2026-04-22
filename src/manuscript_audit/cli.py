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


@app.command("audit-standard")
def audit_standard_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
    db_path: str = typer.Option("data/working/run_store.duckdb", "--db-path"),
) -> None:
    from manuscript_audit.workflows import run_standard_audit_workflow

    report = run_standard_audit_workflow(manuscript_path, output_dir=output_dir, db_path=db_path)
    typer.echo(f"Completed standard run {report.run_id} for {report.manuscript_id}")


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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
