from __future__ import annotations

from manuscript_audit.schemas.findings import FinalVettingReport


def synthesize_report(report: FinalVettingReport) -> FinalVettingReport:
    priorities: list[str] = []
    for finding in report.validation_suite.all_findings:
        if finding.severity in {"fatal", "major"}:
            priorities.append(f"Address {finding.code}: {finding.message}")
    if not priorities:
        priorities.append("No fatal or major deterministic findings in the MVP suite.")
    report.revision_priorities = priorities
    return report


def _render_table(title: str, entries: list) -> str:
    lines = [f"## {title}", "", "| Name | Applicable | Rationale |", "|---|---|---|"]
    for entry in entries:
        status = "yes" if entry.applicable else "no"
        lines.append(f"| {entry.name} | {status} | {entry.rationale} |")
    return "\n".join(lines)


def render_markdown_report(report: FinalVettingReport) -> str:
    counts = report.validation_suite.severity_counts
    counts_text = ", ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    counts_text = counts_text or "no findings"
    priorities = "\n".join(f"- {item}" for item in report.revision_priorities)
    return (
        f"# Final vetting report\n\n"
        f"**Run ID:** {report.run_id}\n\n"
        f"**Manuscript ID:** {report.manuscript_id}\n\n"
        f"**Pathway:** {report.classification.pathway}\n\n"
        f"**Paper type:** {report.classification.paper_type}\n\n"
        f"**Recommended stack:** {report.classification.recommended_stack}\n\n"
        f"**Deterministic findings:** {counts_text}\n\n"
        f"## Revision priorities\n\n{priorities}\n\n"
        f"{_render_table('Module routing', report.module_routing.modules)}\n\n"
        f"{_render_table('Domain routing', report.domain_routing.domains)}\n"
    )
