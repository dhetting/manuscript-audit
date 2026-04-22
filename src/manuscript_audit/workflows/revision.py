from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from manuscript_audit.agents import run_routed_agents
from manuscript_audit.config import DEFAULT_DB_PATH
from manuscript_audit.parsers import (
    build_source_record_candidates,
    parse_bibtex,
    parse_manuscript,
)
from manuscript_audit.reports import (
    render_revision_verification_report,
    synthesize_revision_report,
)
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.schemas.findings import RevisionFindingRef, RevisionVerificationReport
from manuscript_audit.storage import DuckDBRunStore
from manuscript_audit.utils.io import ensure_dir, write_json, write_yaml
from manuscript_audit.validators import run_deterministic_validators

type FindingKey = tuple[str, str, str, str | None]


def _run_id() -> str:
    return datetime.now(UTC).strftime("run-%Y%m%dT%H%M%SZ")


def _attach_bibliography_if_available(manuscript_path: str | Path, parsed) -> None:
    file_path = Path(manuscript_path)
    bib_path = file_path.with_suffix(".bib")
    if bib_path.exists():
        parsed.bibliography_entries = parse_bibtex(bib_path)
        parsed.reference_section_present = True


def _flatten_findings(
    validation_suite,
    agent_suite,
) -> dict[FindingKey, RevisionFindingRef]:
    flattened: dict[FindingKey, RevisionFindingRef] = {}
    for result in validation_suite.results:
        for finding in result.findings:
            key = ("validator", result.validator_name, finding.code, finding.location)
            flattened[key] = RevisionFindingRef(
                source_type="validator",
                source_name=result.validator_name,
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                location=finding.location,
            )
    for result in agent_suite.results:
        for finding in result.findings:
            key = ("agent", result.module_name, finding.code, finding.location)
            flattened[key] = RevisionFindingRef(
                source_type="agent",
                source_name=result.module_name,
                code=finding.code,
                severity=finding.severity,
                message=finding.message,
                location=finding.location,
            )
    return flattened


def run_revision_verification_workflow(
    old_manuscript_path: str | Path,
    new_manuscript_path: str | Path,
    output_dir: str | Path,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> RevisionVerificationReport:
    output_path = Path(output_dir)
    parsed_dir = ensure_dir(output_path / "parsed")
    routing_dir = ensure_dir(output_path / "routing")
    findings_dir = ensure_dir(output_path / "findings")
    reports_dir = ensure_dir(output_path / "reports")

    old_parsed = parse_manuscript(old_manuscript_path)
    _attach_bibliography_if_available(old_manuscript_path, old_parsed)
    old_source_record_candidates = build_source_record_candidates(old_parsed.bibliography_entries)
    old_classification, old_module_routing, old_domain_routing = build_routing_tables(old_parsed)
    old_validation = run_deterministic_validators(old_parsed, old_classification)
    old_agents = run_routed_agents(
        old_parsed,
        old_classification,
        old_validation,
        old_module_routing,
    )

    new_parsed = parse_manuscript(new_manuscript_path)
    _attach_bibliography_if_available(new_manuscript_path, new_parsed)
    new_source_record_candidates = build_source_record_candidates(new_parsed.bibliography_entries)
    new_classification, new_module_routing, new_domain_routing = build_routing_tables(new_parsed)
    new_validation = run_deterministic_validators(new_parsed, new_classification)
    new_agents = run_routed_agents(
        new_parsed,
        new_classification,
        new_validation,
        new_module_routing,
    )

    old_findings = _flatten_findings(old_validation, old_agents)
    new_findings = _flatten_findings(new_validation, new_agents)

    old_keys = set(old_findings)
    new_keys = set(new_findings)
    resolved = [old_findings[key] for key in sorted(old_keys - new_keys)]
    persistent = [new_findings[key] for key in sorted(old_keys & new_keys)]
    introduced = [new_findings[key] for key in sorted(new_keys - old_keys)]

    route_changed = (
        old_classification != new_classification
        or old_module_routing != new_module_routing
        or old_domain_routing != new_domain_routing
    )

    run_id = _run_id()
    report = synthesize_revision_report(
        RevisionVerificationReport(
            run_id=run_id,
            old_manuscript_id=old_parsed.manuscript_id,
            new_manuscript_id=new_parsed.manuscript_id,
            route_changed=route_changed,
            resolved_findings=resolved,
            persistent_findings=persistent,
            new_findings=introduced,
        )
    )

    write_json(parsed_dir / "old_manuscript.json", old_parsed)
    write_json(parsed_dir / "new_manuscript.json", new_parsed)
    write_json(parsed_dir / "old_source_record_candidates.json", old_source_record_candidates)
    write_json(parsed_dir / "new_source_record_candidates.json", new_source_record_candidates)
    write_yaml(routing_dir / "old_module_routing.yaml", old_module_routing)
    write_yaml(routing_dir / "old_domain_routing.yaml", old_domain_routing)
    write_yaml(routing_dir / "new_module_routing.yaml", new_module_routing)
    write_yaml(routing_dir / "new_domain_routing.yaml", new_domain_routing)
    write_json(findings_dir / "old_deterministic_validators.json", old_validation)
    write_json(findings_dir / "new_deterministic_validators.json", new_validation)
    write_json(findings_dir / "old_agent_suite.json", old_agents)
    write_json(findings_dir / "new_agent_suite.json", new_agents)
    write_json(reports_dir / "revision_verification_report.json", report)
    (reports_dir / "revision_verification_report.md").write_text(
        render_revision_verification_report(report),
        encoding="utf-8",
    )

    store = DuckDBRunStore(db_path)
    store.record_run(
        run_id,
        new_parsed.manuscript_id,
        str(new_manuscript_path),
        str(output_path),
    )
    store.record_parsed_artifact(run_id, "old_manuscript", old_parsed)
    store.record_parsed_artifact(run_id, "new_manuscript", new_parsed)
    store.record_parsed_artifact(
        run_id,
        "old_source_record_candidates",
        old_source_record_candidates,
    )
    store.record_parsed_artifact(
        run_id,
        "new_source_record_candidates",
        new_source_record_candidates,
    )
    store.record_routing_decision(run_id, "old_module_routing", old_module_routing)
    store.record_routing_decision(run_id, "old_domain_routing", old_domain_routing)
    store.record_routing_decision(run_id, "new_module_routing", new_module_routing)
    store.record_routing_decision(run_id, "new_domain_routing", new_domain_routing)
    store.record_report(run_id, "revision_verification_report", report)
    store.record_revision_link(
        run_id,
        old_parsed.manuscript_id,
        new_parsed.manuscript_id,
        report,
    )
    store.close()
    return report
