from __future__ import annotations

from manuscript_audit.schemas.findings import (
    FinalVettingReport,
    RevisionVerificationReport,
    SourceRecordVerificationReport,
)


def synthesize_report(report: FinalVettingReport) -> FinalVettingReport:
    priorities: list[str] = []
    for finding in report.validation_suite.all_findings:
        if finding.severity in {"fatal", "major"}:
            priorities.append(f"Address {finding.code}: {finding.message}")
    if report.agent_suite is not None:
        for finding in report.agent_suite.all_findings:
            if finding.severity in {"fatal", "major"}:
                priorities.append(f"Address {finding.code}: {finding.message}")
    if report.source_record_summary is not None:
        if report.source_record_summary.insufficient_metadata_count > 0:
            priorities.append(
                "Some bibliography entries still lack enough metadata for deterministic "
                "source-of-record planning."
            )
        if report.source_record_summary.ready_for_lookup_count > 0:
            priorities.append(
                "Some bibliography entries still need source-of-record lookup resolution."
            )
    if report.notation_summary is not None and report.notation_summary.undefined_symbols:
        priorities.append(
            "Some equation symbols appear without obvious textual definitions; review the "
            "notation ledger."
        )
    if not priorities:
        priorities.append("No fatal or major findings in the current audit stack.")
    report.revision_priorities = priorities
    return report


def synthesize_revision_report(
    report: RevisionVerificationReport,
) -> RevisionVerificationReport:
    priorities: list[str] = []
    for finding in report.persistent_findings:
        if finding.severity in {"fatal", "major", "moderate"}:
            priorities.append(
                f"Persistent {finding.source_type} finding {finding.code}: {finding.message}"
            )
    for finding in report.new_findings:
        if finding.severity in {"fatal", "major", "moderate"}:
            priorities.append(
                f"New {finding.source_type} finding {finding.code}: {finding.message}"
            )
    if report.route_changed:
        priorities.insert(
            0,
            "Routing changed between revisions; review scope may need to expand.",
        )
    if not priorities:
        priorities.append("No persistent or new moderate-or-worse findings were detected.")
    report.revision_priorities = priorities
    return report


def synthesize_source_record_verification_report(
    report: SourceRecordVerificationReport,
) -> SourceRecordVerificationReport:
    priorities: list[str] = []
    if report.summary.metadata_mismatch_count > 0:
        priorities.append(
            "Some bibliography entries disagree with the matched source-of-record metadata."
        )
    if report.summary.lookup_not_found_count > 0:
        priorities.append(
            "Some bibliography entries could not be verified against the selected registry."
        )
    if report.summary.skipped_count > 0:
        priorities.append(
            "Some bibliography entries were skipped because deterministic source metadata "
            "was insufficient."
        )
    if not priorities:
        priorities.append(
            "All verifiable bibliography entries matched the selected source registry."
        )
    report.revision_priorities = priorities
    return report


def _render_table(title: str, entries: list) -> str:
    lines = [f"## {title}", "", "| Name | Applicable | Rationale |", "|---|---|---|"]
    for entry in entries:
        status = "yes" if entry.applicable else "no"
        lines.append(f"| {entry.name} | {status} | {entry.rationale} |")
    return "\n".join(lines)


def _render_agent_results(report: FinalVettingReport) -> str:
    if report.agent_suite is None:
        return "## Routed module execution\n\nNo routed agent modules were executed in this run."
    lines = ["## Routed module execution", ""]
    for result in report.agent_suite.results:
        lines.append(f"### {result.module_name}")
        lines.append("")
        lines.append(result.summary)
        lines.append("")
        if result.findings:
            for finding in result.findings:
                lines.append(f"- [{finding.severity}] {finding.code}: {finding.message}")
        else:
            lines.append("- No structured findings.")
        lines.append("")
    return "\n".join(lines).strip()


def _render_source_record_summary(report: FinalVettingReport) -> str:
    if report.source_record_summary is None:
        return "## Source-of-record enrichment\n\nNo source-of-record summary was generated."
    summary = report.source_record_summary
    return (
        "## Source-of-record enrichment\n\n"
        f"- Total entries: {summary.total_entries}\n"
        f"- Canonical links resolved deterministically: {summary.resolved_canonical_link_count}\n"
        f"- Ready for lookup: {summary.ready_for_lookup_count}\n"
        f"- Insufficient metadata: {summary.insufficient_metadata_count}\n"
    )


def _render_notation_summary(report: FinalVettingReport) -> str:
    if report.notation_summary is None:
        return "## Notation coverage\n\nNo notation summary was generated."
    summary = report.notation_summary
    lines = [
        "## Notation coverage",
        "",
        f"- Equation symbols parsed: {summary.equation_symbol_count}",
        f"- Symbols with textual definition hints: {summary.defined_symbol_count}",
        (
            "- Undefined symbols: "
            + (", ".join(summary.undefined_symbols) if summary.undefined_symbols else "none")
        ),
    ]
    return "\n".join(lines)


def render_markdown_report(report: FinalVettingReport) -> str:
    validator_counts = report.validation_suite.severity_counts
    validator_text = ", ".join(f"{key}: {value}" for key, value in sorted(validator_counts.items()))
    validator_text = validator_text or "no findings"
    agent_text = "not run"
    if report.agent_suite is not None:
        agent_counts = report.agent_suite.severity_counts
        agent_text = ", ".join(f"{key}: {value}" for key, value in sorted(agent_counts.items()))
        agent_text = agent_text or "no findings"
    priorities = "\n".join(f"- {item}" for item in report.revision_priorities)
    return (
        f"# Final vetting report\n\n"
        f"**Run ID:** {report.run_id}\n\n"
        f"**Manuscript ID:** {report.manuscript_id}\n\n"
        f"**Pathway:** {report.classification.pathway}\n\n"
        f"**Paper type:** {report.classification.paper_type}\n\n"
        f"**Recommended stack:** {report.classification.recommended_stack}\n\n"
        f"**Deterministic findings:** {validator_text}\n\n"
        f"**Routed module findings:** {agent_text}\n\n"
        f"## Revision priorities\n\n{priorities}\n\n"
        f"{_render_table('Module routing', report.module_routing.modules)}\n\n"
        f"{_render_table('Domain routing', report.domain_routing.domains)}\n\n"
        f"{_render_source_record_summary(report)}\n\n"
        f"{_render_notation_summary(report)}\n\n"
        f"{_render_agent_results(report)}\n"
    )


def render_revision_verification_report(report: RevisionVerificationReport) -> str:
    def _render_refs(title: str, refs: list) -> str:
        lines = [f"## {title}", ""]
        if not refs:
            lines.append("- None")
        else:
            for ref in refs:
                lines.append(
                    f"- [{ref.severity}] {ref.source_type}/{ref.source_name}/"
                    f"{ref.code}: {ref.message}"
                )
        return "\n".join(lines)

    priorities = "\n".join(f"- {item}" for item in report.revision_priorities)
    return (
        f"# Revision verification report\n\n"
        f"**Run ID:** {report.run_id}\n\n"
        f"**Old manuscript ID:** {report.old_manuscript_id}\n\n"
        f"**New manuscript ID:** {report.new_manuscript_id}\n\n"
        f"**Route changed:** {'yes' if report.route_changed else 'no'}\n\n"
        f"## Revision priorities\n\n{priorities}\n\n"
        f"{_render_refs('Resolved findings', report.resolved_findings)}\n\n"
        f"{_render_refs('Persistent findings', report.persistent_findings)}\n\n"
        f"{_render_refs('New findings', report.new_findings)}\n"
    )


def render_source_record_verification_report(
    report: SourceRecordVerificationReport,
) -> str:
    priorities = "\n".join(f"- {item}" for item in report.revision_priorities)
    lines = [
        "# Source-of-record verification report",
        "",
        f"**Run ID:** {report.run_id}",
        "",
        f"**Manuscript ID:** {report.manuscript_id}",
        "",
        f"**Verification provider:** {report.verification_provider}",
        "",
        "## Verification priorities",
        "",
        priorities,
        "",
        "## Verification summary",
        "",
        f"- Total records: {report.summary.total_records}",
        f"- Verified: {report.summary.verified_count}",
        f"- Verified direct URL: {report.summary.verified_direct_url_count}",
        f"- Metadata mismatches: {report.summary.metadata_mismatch_count}",
        f"- Lookup not found: {report.summary.lookup_not_found_count}",
        f"- Skipped: {report.summary.skipped_count}",
        "",
        "## Record details",
        "",
    ]
    for item in report.verifications:
        detail = f"- {item.entry_label}: {item.status}"
        if item.issues:
            detail += f" ({', '.join(item.issues)})"
        lines.append(detail)
    return "\n".join(lines) + "\n"
