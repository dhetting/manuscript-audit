from pathlib import Path

from manuscript_audit.workflows.revision import run_revision_verification_workflow


def test_revision_workflow_resolves_known_findings(tmp_path: Path) -> None:
    report = run_revision_verification_workflow(
        old_manuscript_path=Path("tests/fixtures/manuscripts/revision_old.md"),
        new_manuscript_path=Path("tests/fixtures/manuscripts/revision_new.md"),
        output_dir=tmp_path / "revision_output",
        db_path=tmp_path / "revision.duckdb",
    )
    resolved_codes = {item.code for item in report.resolved_findings}
    assert report.route_changed is False
    assert "unresolved-placeholder" in resolved_codes
    assert "missing-figure-definition" in resolved_codes
    assert "equivalence-margin-not-explicit" in resolved_codes
    assert "thin-abstract" in resolved_codes
    assert not report.new_findings
