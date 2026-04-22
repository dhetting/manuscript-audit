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
