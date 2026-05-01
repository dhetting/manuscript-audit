from __future__ import annotations

from manuscript_audit.schemas.findings import (
    FinalVettingReport,
    RevisionVerificationReport,
    SourceRecordVerificationReport,
)

_SEVERITY_RANK: dict[str, int] = {"fatal": 0, "major": 1}


def synthesize_report(report: FinalVettingReport) -> FinalVettingReport:
    prioritized: list[tuple[int, str]] = []
    for finding in report.validation_suite.all_findings:
        if finding.severity in _SEVERITY_RANK:
            prioritized.append(
                (_SEVERITY_RANK[finding.severity], f"Address {finding.code}: {finding.message}")
            )
    if report.agent_suite is not None:
        for finding in report.agent_suite.all_findings:
            if finding.severity in _SEVERITY_RANK:
                prioritized.append(
                    (_SEVERITY_RANK[finding.severity], f"Address {finding.code}: {finding.message}")
                )
    prioritized.sort(key=lambda t: t[0])  # fatal (0) before major (1); stable within rank
    priorities: list[str] = [msg for _, msg in prioritized]
    if report.bibliography_confidence_summary is not None:
        confidence = report.bibliography_confidence_summary
        if confidence.confidence_level == "critical":
            priorities.append(
                "Bibliography confidence is critical; resolve verification "
                "blockers before submission."
            )
        elif confidence.confidence_level == "low":
            priorities.append(
                "Bibliography confidence is low; manual review is still required "
                "for several entries."
            )
    if report.source_verification_summary is not None:
        summary = report.source_verification_summary
        if summary.metadata_mismatch_count:
            priorities.append(
                "Resolve bibliography metadata mismatches against source-of-record verification."
            )
        if summary.ambiguous_match_count:
            priorities.append(
                "Adjudicate ambiguous source-record matches before final bibliography review."
            )
        if summary.provider_error_count:
            priorities.append(
                "Rerun source verification after provider errors are resolved or retried."
            )
        if summary.lookup_not_found_count:
            priorities.append(
                "Investigate bibliography entries that could not be matched to a source record."
            )
    if report.notation_summary is not None and report.notation_summary.undefined_symbols:
        priorities.append(
            "Some equation symbols appear without obvious textual definitions; "
            "review the notation ledger."
        )
    if not priorities:
        priorities.append("No fatal or major deterministic findings in the current audit stack.")
    report.revision_priorities = priorities
    return report


def synthesize_revision_report(report: RevisionVerificationReport) -> RevisionVerificationReport:
    prioritized: list[tuple[int, str]] = []
    for item in report.new_findings:
        if item.severity in _SEVERITY_RANK:
            prioritized.append(
                (_SEVERITY_RANK[item.severity], f"Address new finding {item.code}: {item.message}")
            )
    for item in report.persistent_findings:
        if item.severity in _SEVERITY_RANK:
            prioritized.append((
                _SEVERITY_RANK[item.severity],
                f"Persistent issue remains {item.code}: {item.message}",
            ))
    prioritized.sort(key=lambda t: t[0])
    priorities: list[str] = [msg for _, msg in prioritized]
    if report.route_changed:
        priorities.append(
            "Routing changed between manuscript versions; review newly activated modules."
        )
    if not priorities:
        priorities.append("No fatal or major issues remain after revision verification.")
    report.revision_priorities = priorities
    return report


def synthesize_source_record_verification_report(
    report: SourceRecordVerificationReport,
) -> SourceRecordVerificationReport:
    priorities: list[str] = []
    if report.bibliography_confidence_summary is not None:
        confidence = report.bibliography_confidence_summary
        if confidence.confidence_level == "critical":
            priorities.append("Bibliography confidence is critical after source verification.")
        elif confidence.confidence_level == "low":
            priorities.append("Bibliography confidence remains low after source verification.")
    for item in report.verifications:
        if item.status == "metadata_mismatch":
            priorities.append(
                f"Resolve metadata mismatch for bibliography entry {item.entry_label}."
            )
        elif item.status == "ambiguous_match":
            priorities.append(
                f"Manually adjudicate ambiguous registry candidates for {item.entry_label}."
            )
        elif item.status == "provider_error":
            priorities.append(
                f"Retry registry lookup or inspect provider failure for {item.entry_label}."
            )
    if not priorities:
        priorities.append("No source-record verification issues require escalation.")
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


def _render_bibliography_confidence_summary(report: FinalVettingReport) -> str:
    if report.bibliography_confidence_summary is None:
        return "## Bibliography confidence\n\nNo bibliography confidence summary was generated."
    summary = report.bibliography_confidence_summary
    lines = [
        "## Bibliography confidence",
        "",
        f"- Basis: {summary.basis}",
        f"- Confidence level: {summary.confidence_level}",
        f"- Confidence score: {summary.confidence_score}",
        f"- Manual review required: {summary.manual_review_required_count}",
        f"- Metadata mismatches: {summary.mismatch_entry_count}",
        f"- Ambiguous matches: {summary.ambiguous_entry_count}",
        f"- Lookup not found: {summary.lookup_not_found_count}",
        f"- Provider errors: {summary.provider_error_count}",
        f"- Insufficient metadata: {summary.insufficient_metadata_count}",
    ]
    if summary.rationale:
        lines.extend(["", "Rationale:"])
        for item in summary.rationale:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _render_source_verification_summary(report: FinalVettingReport) -> str:
    if report.source_verification_summary is None:
        return "## Source verification\n\nNo source verification summary was generated in this run."
    summary = report.source_verification_summary
    provider = report.source_verification_provider or "unknown"
    lines = [
        "## Source verification",
        "",
        f"- Provider: {provider}",
        f"- Verified: {summary.verified_count}",
        f"- Verified direct URL: {summary.verified_direct_url_count}",
        f"- Metadata mismatches: {summary.metadata_mismatch_count}",
        f"- Ambiguous matches: {summary.ambiguous_match_count}",
        f"- Lookup not found: {summary.lookup_not_found_count}",
        f"- Provider errors: {summary.provider_error_count}",
        f"- Skipped: {summary.skipped_count}",
    ]
    if summary.issue_type_counts:
        lines.extend(["", "Issue counts:"])
        for issue, count in sorted(summary.issue_type_counts.items()):
            lines.append(f"- {issue}: {count}")
    return "\n".join(lines)


def _render_notation_summary(report: FinalVettingReport) -> str:
    if report.notation_summary is None:
        return "## Notation coverage\n\nNo notation summary was generated."
    summary = report.notation_summary
    lines = [
        "## Notation coverage",
        "",
        f"- Equation symbols parsed: {summary.equation_symbol_count}",
        f"- Symbols with textual definition hints: {summary.defined_symbol_count}",
        "- Undefined symbols: "
        + (", ".join(summary.undefined_symbols) if summary.undefined_symbols else "none"),
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
        f"{_render_bibliography_confidence_summary(report)}\n\n"
        f"{_render_source_verification_summary(report)}\n\n"
        f"{_render_notation_summary(report)}\n\n"
        f"{_render_agent_results(report)}\n"
    )


def render_revision_verification_report(report: RevisionVerificationReport) -> str:
    from collections import Counter

    def _code_counts(refs: list) -> Counter:
        return Counter(ref.code for ref in refs)

    def _render_code_summary(label: str, refs: list) -> str:
        n = len(refs)
        if not refs:
            return f"{label} ({n}): none"
        lines = [f"{label} ({n}):"]
        for code, count in sorted(_code_counts(refs).items()):
            lines.append(f"  {count}× {code}")
        return "\n".join(lines)

    def _render_refs(title: str, refs: list) -> str:
        lines = [f"## {title}", ""]
        if not refs:
            lines.append("- None")
        else:
            for ref in refs:
                lines.append(
                    f"- [{ref.severity}] {ref.source_type}/"
                    f"{ref.source_name}/{ref.code}: {ref.message}"
                )
        return "\n".join(lines)

    code_summary = "\n".join(
        [
            _render_code_summary("Resolved", report.resolved_findings),
            _render_code_summary("Persistent", report.persistent_findings),
            _render_code_summary("Introduced", report.new_findings),
        ]
    )
    priorities = "\n".join(f"- {item}" for item in report.revision_priorities)
    return (
        f"# Revision verification report\n\n"
        f"**Run ID:** {report.run_id}\n\n"
        f"**Old manuscript ID:** {report.old_manuscript_id}\n\n"
        f"**New manuscript ID:** {report.new_manuscript_id}\n\n"
        f"**Route changed:** {'yes' if report.route_changed else 'no'}\n\n"
        f"## Revision priorities\n\n{priorities}\n\n"
        f"## Finding code summary\n\n{code_summary}\n\n"
        f"{_render_refs('Resolved findings', report.resolved_findings)}\n\n"
        f"{_render_refs('Persistent findings', report.persistent_findings)}\n\n"
        f"{_render_refs('New findings', report.new_findings)}\n"
    )


def render_source_record_verification_report(report: SourceRecordVerificationReport) -> str:
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
        "## Bibliography confidence",
        "",
    ]
    if report.bibliography_confidence_summary is not None:
        confidence = report.bibliography_confidence_summary
        lines.extend(
            [
                f"- Basis: {confidence.basis}",
                f"- Confidence level: {confidence.confidence_level}",
                f"- Confidence score: {confidence.confidence_score}",
                f"- Manual review required: {confidence.manual_review_required_count}",
            ]
        )
        if confidence.rationale:
            lines.extend(["", "Rationale:"])
            for item in confidence.rationale:
                lines.append(f"- {item}")
    else:
        lines.append("- No bibliography confidence summary generated.")
    lines.extend(
        [
            "",
            "## Verification summary",
            "",
            f"- Total records: {report.summary.total_records}",
            f"- Verified: {report.summary.verified_count}",
            f"- Verified direct URL: {report.summary.verified_direct_url_count}",
            f"- Metadata mismatches: {report.summary.metadata_mismatch_count}",
            f"- Ambiguous matches: {report.summary.ambiguous_match_count}",
            f"- Lookup not found: {report.summary.lookup_not_found_count}",
            f"- Provider errors: {report.summary.provider_error_count}",
            f"- Skipped: {report.summary.skipped_count}",
            "",
            "## Issue counts",
            "",
        ]
    )
    if report.summary.issue_type_counts:
        for issue, count in sorted(report.summary.issue_type_counts.items()):
            lines.append(f"- {issue}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Record details", ""])
    for item in report.verifications:
        detail = f"- {item.entry_label}: {item.status}"
        if item.candidate_count:
            detail += f" [candidates={item.candidate_count}]"
        if item.selected_match_score is not None:
            detail += f" [score={item.selected_match_score:.1f}]"
        if item.issues:
            detail += f" ({', '.join(item.issues)})"
        lines.append(detail)
    return "\n".join(lines) + "\n"
