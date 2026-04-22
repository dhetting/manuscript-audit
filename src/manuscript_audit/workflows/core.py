from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from manuscript_audit.config import DEFAULT_DB_PATH
from manuscript_audit.parsers import parse_manuscript
from manuscript_audit.reports import render_markdown_report, synthesize_report
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.schemas.findings import FinalVettingReport
from manuscript_audit.storage import DuckDBRunStore
from manuscript_audit.utils.io import ensure_dir, write_json, write_yaml
from manuscript_audit.validators import run_deterministic_validators


def _run_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def _attach_bibliography_if_available(manuscript_path: str | Path, parsed) -> None:
    from manuscript_audit.parsers import parse_bibtex

    file_path = Path(manuscript_path)
    bib_path = file_path.with_suffix(".bib")
    if bib_path.exists():
        parsed.bibliography_entries = parse_bibtex(bib_path)
        parsed.reference_section_present = True


def run_core_audit_workflow(
    manuscript_path: str | Path,
    output_dir: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> FinalVettingReport:
    output_path = Path(output_dir)
    parsed_dir = ensure_dir(output_path / "parsed")
    routing_dir = ensure_dir(output_path / "routing")
    findings_dir = ensure_dir(output_path / "findings")
    reports_dir = ensure_dir(output_path / "reports")

    parsed = parse_manuscript(manuscript_path)
    _attach_bibliography_if_available(manuscript_path, parsed)
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)

    run_id = _run_id()
    report = synthesize_report(
        FinalVettingReport(
            run_id=run_id,
            manuscript_id=parsed.manuscript_id,
            classification=classification,
            module_routing=module_routing,
            domain_routing=domain_routing,
            validation_suite=validation_suite,
        )
    )

    write_json(parsed_dir / "manuscript.json", parsed)
    write_json(parsed_dir / "references.json", parsed.bibliography_entries)
    write_json(parsed_dir / "classification.json", classification)
    write_yaml(routing_dir / "module_routing.yaml", module_routing)
    write_yaml(routing_dir / "domain_routing.yaml", domain_routing)
    write_json(findings_dir / "deterministic_validators.json", validation_suite)
    write_json(reports_dir / "final_vetting_report.json", report)
    report_markdown = render_markdown_report(report)
    (reports_dir / "final_vetting_report.md").write_text(report_markdown, encoding="utf-8")

    store = DuckDBRunStore(db_path)
    store.record_run(run_id, parsed.manuscript_id, str(manuscript_path), str(output_path))
    store.record_parsed_artifact(run_id, "manuscript", parsed)
    store.record_parsed_artifact(run_id, "references", parsed.bibliography_entries)
    store.record_parsed_artifact(run_id, "classification", classification)
    store.record_routing_decision(run_id, "module_routing", module_routing)
    store.record_routing_decision(run_id, "domain_routing", domain_routing)
    for result in validation_suite.results:
        store.record_validator_result(run_id, result.validator_name, result)
    store.record_report(run_id, "final_vetting_report", report)
    store.close()
    return report
