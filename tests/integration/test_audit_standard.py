import json
from pathlib import Path

import duckdb

from manuscript_audit.workflows import run_standard_audit_workflow


def test_standard_workflow_writes_module_findings_and_agent_records(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md")
    output_dir = tmp_path / "standard_output"
    db_path = tmp_path / "standard_store.duckdb"

    report = run_standard_audit_workflow(manuscript, output_dir=output_dir, db_path=db_path)
    assert report.agent_suite is not None
    assert len(report.agent_suite.results) >= 1
    assert (output_dir / "findings" / "agent_suite.json").exists()
    assert (output_dir / "parsed" / "source_record_candidates.json").exists()
    assert (output_dir / "parsed" / "source_records.json").exists()
    assert (output_dir / "parsed" / "notation_summary.json").exists()
    assert (output_dir / "findings" / "modules" / "bibliography_metadata_validation.json").exists()
    report_payload = json.loads((output_dir / "reports" / "final_vetting_report.json").read_text())
    assert report_payload["agent_suite"] is not None
    assert report_payload["source_record_summary"] is not None
    assert report_payload["notation_summary"] is not None

    connection = duckdb.connect(str(db_path))
    agent_count = connection.execute("SELECT COUNT(*) FROM agent_findings").fetchone()[0]
    connection.close()
    assert agent_count >= 1


def test_standard_workflow_integrates_source_verification(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/bibliography_metadata.tex")
    output_dir = tmp_path / "standard_verified_output"
    db_path = tmp_path / "standard_verified.duckdb"
    fixture_path = Path("tests/fixtures/registries/source_registry_fixture.json")

    report = run_standard_audit_workflow(
        manuscript,
        output_dir=output_dir,
        db_path=db_path,
        source_verification_provider="fixture",
        registry_fixture_path=fixture_path,
    )

    assert report.source_verification_summary is not None
    assert report.source_verification_provider == "fixture_source_registry"
    assert (output_dir / "findings" / "source_record_verifications.json").exists()
    assert (output_dir / "findings" / "source_record_verification_summary.json").exists()

    payload = json.loads((output_dir / "reports" / "final_vetting_report.json").read_text())
    assert payload["source_verification_summary"]["metadata_mismatch_count"] == 1
    assert payload["source_verification_provider"] == "fixture_source_registry"
    priorities = payload["revision_priorities"]
    assert any("metadata mismatches" in item.lower() for item in priorities)

    module_payload = json.loads(
        (output_dir / "findings" / "modules" / "bibliography_metadata_validation.json").read_text()
    )
    codes = {finding["code"] for finding in module_payload["findings"]}
    assert "source-record-metadata-mismatch" in codes

    connection = duckdb.connect(str(db_path))
    parsed_count = connection.execute(
        "SELECT COUNT(*) FROM parsed_artifacts WHERE artifact_name = 'source_record_verifications'"
    ).fetchone()[0]
    connection.close()
    assert parsed_count == 1
