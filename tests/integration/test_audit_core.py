import json
from pathlib import Path

import duckdb

from manuscript_audit.workflows import run_core_audit_workflow


def test_core_workflow_writes_structured_artifacts(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md")
    output_dir = tmp_path / "run_output"
    db_path = tmp_path / "run_store.duckdb"

    report = run_core_audit_workflow(manuscript, output_dir=output_dir, db_path=db_path)
    assert report.classification.pathway == "data_science"
    assert (output_dir / "parsed" / "manuscript.json").exists()
    assert (output_dir / "parsed" / "references.json").exists()
    assert (output_dir / "parsed" / "source_record_candidates.json").exists()
    assert (output_dir / "parsed" / "source_records.json").exists()
    assert (output_dir / "parsed" / "source_record_summary.json").exists()
    assert (output_dir / "parsed" / "notation_summary.json").exists()
    assert (output_dir / "routing" / "module_routing.yaml").exists()
    assert (output_dir / "findings" / "deterministic_validators.json").exists()
    assert (output_dir / "reports" / "final_vetting_report.md").exists()

    payload = json.loads((output_dir / "parsed" / "classification.json").read_text())
    assert payload["paper_type"] == "software_workflow_paper"

    connection = duckdb.connect(str(db_path))
    run_count = connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    report_count = connection.execute("SELECT COUNT(*) FROM report_artifacts").fetchone()[0]
    connection.close()
    assert run_count == 1
    assert report_count == 1


def test_core_workflow_attaches_companion_bibtex(tmp_path: Path) -> None:
    manuscript = Path("tests/fixtures/manuscripts/latex_equivalence.tex")
    output_dir = tmp_path / "latex_output"
    db_path = tmp_path / "latex_store.duckdb"

    report = run_core_audit_workflow(manuscript, output_dir=output_dir, db_path=db_path)
    assert report.classification.pathway == "data_science"
    references_payload = json.loads((output_dir / "parsed" / "references.json").read_text())
    source_record_payload = json.loads(
        (output_dir / "parsed" / "source_record_candidates.json").read_text()
    )
    source_records_payload = json.loads((output_dir / "parsed" / "source_records.json").read_text())
    assert len(references_payload) == 2
    assert references_payload[0]["key"] == "schuirmann1987"
    assert source_record_payload[0]["status"] == "ready_via_doi"
    assert source_records_payload[0]["status"] == "resolved_canonical_link"
