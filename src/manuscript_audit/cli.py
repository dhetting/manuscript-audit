from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from manuscript_audit.parsers import parse_markdown_manuscript
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.utils.io import write_json, write_yaml
from manuscript_audit.validators import run_deterministic_validators
from manuscript_audit.workflows import run_core_audit_workflow

app = typer.Typer(add_completion=False, no_args_is_help=True)
OUTPUT_DIR_OPTION = typer.Option(..., "--output-dir", dir_okay=True, file_okay=False)
DB_PATH_OPTION = typer.Option("data/working/run_store.duckdb", "--db-path")


@app.command("parse")
def parse_command(manuscript_path: Path, output_dir: Path) -> None:
    parsed = parse_markdown_manuscript(manuscript_path)
    write_json(output_dir / "parsed" / "manuscript.json", parsed)


@app.command("route")
def route_command(manuscript_path: Path, output_dir: Path) -> None:
    parsed = parse_markdown_manuscript(manuscript_path)
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    write_json(output_dir / "parsed" / "classification.json", classification)
    write_yaml(output_dir / "routing" / "module_routing.yaml", module_routing)
    write_yaml(output_dir / "routing" / "domain_routing.yaml", domain_routing)


@app.command("validate")
def validate_command(manuscript_path: Path, output_dir: Path) -> None:
    parsed = parse_markdown_manuscript(manuscript_path)
    classification, _, _ = build_routing_tables(parsed)
    results = run_deterministic_validators(parsed, classification)
    write_json(output_dir / "findings" / "deterministic_validators.json", results)


@app.command("audit-core")
def audit_core_command(
    manuscript_path: Path,
    output_dir: Annotated[Path, OUTPUT_DIR_OPTION],
    db_path: Annotated[str, DB_PATH_OPTION],
) -> None:
    report = run_core_audit_workflow(manuscript_path, output_dir=output_dir, db_path=db_path)
    typer.echo(f"Completed run {report.run_id} for {report.manuscript_id}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
