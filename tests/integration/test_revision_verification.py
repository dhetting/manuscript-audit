import json
from pathlib import Path

import duckdb

from manuscript_audit.workflows.revision import run_revision_verification_workflow


def test_revision_verification_writes_structured_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "revision_run"
    db_path = tmp_path / "revision.duckdb"
    report = run_revision_verification_workflow(
        old_manuscript_path=Path("tests/fixtures/manuscripts/revision_old.md"),
        new_manuscript_path=Path("tests/fixtures/manuscripts/revision_new.md"),
        output_dir=output_dir,
        db_path=db_path,
    )
    assert report.route_changed is False
    assert (output_dir / "reports" / "revision_verification_report.json").exists()
    assert (output_dir / "reports" / "revision_verification_report.md").exists()
    assert (output_dir / "parsed" / "old_source_records.json").exists()
    assert (output_dir / "parsed" / "new_source_records.json").exists()
    assert (output_dir / "parsed" / "old_notation_summary.json").exists()
    assert (output_dir / "parsed" / "new_notation_summary.json").exists()
    payload = json.loads((output_dir / "reports" / "revision_verification_report.json").read_text())
    assert payload["new_manuscript_id"] == report.new_manuscript_id

    connection = duckdb.connect(str(db_path))
    revision_count = connection.execute("SELECT COUNT(*) FROM revision_links").fetchone()[0]
    report_count = connection.execute("SELECT COUNT(*) FROM report_artifacts").fetchone()[0]
    connection.close()
    assert revision_count == 1
    assert report_count == 1
