from __future__ import annotations

from pathlib import Path

import typer

from manuscript_audit.parsers import parse_bibtex, parse_manuscript
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.utils.io import write_json, write_yaml
from manuscript_audit.validators import run_deterministic_validators

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("parse")
def parse_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    parsed = parse_manuscript(manuscript_path)
    bib_path = manuscript_path.with_suffix(".bib")
    if bib_path.exists():
        parsed.bibliography_entries = parse_bibtex(bib_path)
        parsed.reference_section_present = True
    write_json(output_dir / "parsed" / "manuscript.json", parsed)
    write_json(output_dir / "parsed" / "references.json", parsed.bibliography_entries)


@app.command("route")
def route_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    parsed = parse_manuscript(manuscript_path)
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    write_json(output_dir / "parsed" / "classification.json", classification)
    write_yaml(output_dir / "routing" / "module_routing.yaml", module_routing)
    write_yaml(output_dir / "routing" / "domain_routing.yaml", domain_routing)


@app.command("validate")
def validate_command(
    manuscript_path: Path,
    output_dir: Path = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False),
) -> None:
    parsed = parse_manuscript(manuscript_path)
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


def main() -> None:
    app()


if __name__ == "__main__":
    main()
