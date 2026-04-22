from pathlib import Path

from typer.testing import CliRunner

from manuscript_audit.cli import app


def test_audit_core_cli_command(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "cli_run"
    db_path = tmp_path / "cli.duckdb"
    result = runner.invoke(
        app,
        [
            "audit-core",
            "tests/fixtures/manuscripts/software_equivalence_manuscript.md",
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "reports" / "final_vetting_report.json").exists()


def test_audit_standard_cli_command(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "cli_standard_run"
    db_path = tmp_path / "cli_standard.duckdb"
    result = runner.invoke(
        app,
        [
            "audit-standard",
            "tests/fixtures/manuscripts/software_equivalence_manuscript.md",
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "findings" / "agent_suite.json").exists()
    assert (output_dir / "findings" / "modules" / "bibliography_metadata_validation.json").exists()


def test_parse_cli_writes_reference_artifact_for_companion_bibtex(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "parse_run"
    result = runner.invoke(
        app,
        [
            "parse",
            "tests/fixtures/manuscripts/latex_equivalence.tex",
            "--output-dir",
            str(output_dir),
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "parsed" / "references.json").exists()
