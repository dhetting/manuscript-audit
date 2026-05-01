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
    assert "findings:" in result.output
    assert "routing:" in result.output


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
    assert "findings:" in result.output
    assert "routing:" in result.output


def test_audit_standard_cli_command_with_source_verification(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "cli_standard_verify_run"
    db_path = tmp_path / "cli_standard_verify.duckdb"
    result = runner.invoke(
        app,
        [
            "audit-standard",
            "tests/fixtures/manuscripts/bibliography_metadata.tex",
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
            "--source-verification-provider",
            "fixture",
            "--registry-fixture",
            "tests/fixtures/registries/source_registry_fixture.json",
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "findings" / "source_record_verifications.json").exists()
    assert (output_dir / "reports" / "final_vetting_report.json").exists()


def test_verify_revision_cli_command(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "cli_revision_run"
    db_path = tmp_path / "cli_revision.duckdb"
    result = runner.invoke(
        app,
        [
            "verify-revision",
            "tests/fixtures/manuscripts/revision_old.md",
            "tests/fixtures/manuscripts/revision_new.md",
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "reports" / "revision_verification_report.json").exists()
    assert "resolved=" in result.output
    assert "persistent=" in result.output


def test_parse_cli_writes_reference_and_source_record_artifacts(tmp_path: Path) -> None:
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
    assert (output_dir / "parsed" / "source_record_candidates.json").exists()
    assert (output_dir / "parsed" / "source_records.json").exists()
    assert (output_dir / "parsed" / "source_record_summary.json").exists()
    assert (output_dir / "parsed" / "notation_summary.json").exists()


def test_verify_sources_cli_command(tmp_path: Path) -> None:
    runner = CliRunner()
    output_dir = tmp_path / "cli_source_verify"
    db_path = tmp_path / "cli_source_verify.duckdb"
    result = runner.invoke(
        app,
        [
            "verify-sources",
            "tests/fixtures/manuscripts/bibliography_metadata.tex",
            "--output-dir",
            str(output_dir),
            "--db-path",
            str(db_path),
            "--provider",
            "fixture",
            "--registry-fixture",
            "tests/fixtures/registries/source_registry_fixture.json",
        ],
    )
    assert result.exit_code == 0
    assert (output_dir / "reports" / "source_record_verification_report.json").exists()
    assert "sources:" in result.output
    assert "confidence:" in result.output
