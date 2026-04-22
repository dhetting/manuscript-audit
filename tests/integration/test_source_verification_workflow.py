import json
from pathlib import Path

import duckdb

from manuscript_audit.workflows import run_source_record_verification_workflow


def test_source_verification_workflow_writes_artifacts_and_report(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/bibliography_metadata.tex")
    output_dir = tmp_path / "source_verify_output"
    db_path = tmp_path / "source_verify.duckdb"
    fixture_path = Path("tests/fixtures/registries/source_registry_fixture.json")

    report = run_source_record_verification_workflow(
        manuscript_path=manuscript,
        output_dir=output_dir,
        db_path=db_path,
        provider="fixture",
        registry_fixture_path=fixture_path,
    )
    assert report.summary.verified_count == 2
    assert (output_dir / "parsed" / "source_record_verifications.json").exists()
    assert (output_dir / "reports" / "source_record_verification_report.md").exists()

    payload = json.loads(
        (output_dir / "reports" / "source_record_verification_report.json").read_text()
    )
    assert payload["summary"]["metadata_mismatch_count"] == 1
    assert payload["summary"]["ambiguous_match_count"] == 0
    assert payload["summary"]["provider_error_count"] == 0

    connection = duckdb.connect(str(db_path))
    report_count = connection.execute("SELECT COUNT(*) FROM report_artifacts").fetchone()[0]
    connection.close()
    assert report_count == 1


def test_source_verification_workflow_reports_ambiguous_matches(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/bibliography_metadata.tex")
    output_dir = tmp_path / "source_verify_ambiguous_output"
    db_path = tmp_path / "source_verify_ambiguous.duckdb"
    fixture_path = Path("tests/fixtures/registries/source_registry_ambiguous_fixture.json")

    report = run_source_record_verification_workflow(
        manuscript_path=manuscript,
        output_dir=output_dir,
        db_path=db_path,
        provider="fixture",
        registry_fixture_path=fixture_path,
    )

    assert report.summary.ambiguous_match_count == 1
    assert report.summary.issue_type_counts["multiple_candidate_matches"] == 1
    payload = json.loads(
        (output_dir / "reports" / "source_record_verification_report.json").read_text()
    )
    assert payload["summary"]["ambiguous_match_count"] == 1
