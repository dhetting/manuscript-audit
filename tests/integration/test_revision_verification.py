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

    md = (output_dir / "reports" / "revision_verification_report.md").read_text()
    assert "## Finding code summary" in md

    connection = duckdb.connect(str(db_path))
    revision_count = connection.execute("SELECT COUNT(*) FROM revision_links").fetchone()[0]
    report_count = connection.execute("SELECT COUNT(*) FROM report_artifacts").fetchone()[0]
    connection.close()
    assert revision_count == 1
    assert report_count == 1


def test_phase13_to_16_finding_codes_resolve_after_revision(tmp_path: Path) -> None:
    """Phase 13-16 finding codes should appear in resolved_findings when the
    new manuscript adds proper citations and support-section evidence."""
    output_dir = tmp_path / "claim_revision_run"
    db_path = tmp_path / "claim_revision.duckdb"
    report = run_revision_verification_workflow(
        old_manuscript_path=Path("tests/fixtures/manuscripts/revision_claim_old.md"),
        new_manuscript_path=Path("tests/fixtures/manuscripts/revision_claim_new.md"),
        output_dir=output_dir,
        db_path=db_path,
    )
    resolved_codes = {f.code for f in report.resolved_findings}
    persistent_codes = {f.code for f in report.persistent_findings}

    assert "citationless-quantitative-claim" in resolved_codes
    assert "citationless-comparative-claim" in resolved_codes
    assert "abstract-metric-unsupported" in resolved_codes
    assert "systemic-claim-evidence-gap" in resolved_codes

    assert "citationless-quantitative-claim" not in persistent_codes
    assert "citationless-comparative-claim" not in persistent_codes
    assert "systemic-claim-evidence-gap" not in persistent_codes

    md = (output_dir / "reports" / "revision_verification_report.md").read_text()
    assert "## Finding code summary" in md
    assert "citationless-quantitative-claim" in md
    assert "systemic-claim-evidence-gap" in md
    # Resolved counts should appear before the detailed sections
    summary_pos = md.index("## Finding code summary")
    resolved_detail_pos = md.index("## Resolved findings")
    assert summary_pos < resolved_detail_pos
