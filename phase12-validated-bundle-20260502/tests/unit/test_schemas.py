from manuscript_audit.schemas import BibliographyEntry, ParsedManuscript, Section


def test_parsed_manuscript_round_trip() -> None:
    parsed = ParsedManuscript(
        manuscript_id="demo",
        source_path="example.md",
        source_format="markdown",
        title="Demo",
        abstract="Abstract text",
        sections=[Section(title="Abstract", level=2, body="Abstract text")],
        full_text="# Demo",
        bibliography_entries=[
            BibliographyEntry(raw_text="A reference", source="markdown_reference_list")
        ],
    )
    restored = ParsedManuscript.model_validate_json(parsed.model_dump_json())
    assert restored.manuscript_id == "demo"
    assert restored.section_titles == ["Abstract"]
    assert restored.bibliography_entries[0].source == "markdown_reference_list"


# ---------------------------------------------------------------------------
# Phase 23: revision priority ordering (fatal before major)
# ---------------------------------------------------------------------------


def _make_vetting_report_with_findings(findings_by_severity: list[tuple[str, str]]):
    """Build a minimal FinalVettingReport with validator findings by (severity, code)."""
    from manuscript_audit.schemas.findings import (
        FinalVettingReport,
        Finding,
        ValidationResult,
        ValidationSuiteResult,
    )
    from manuscript_audit.schemas.routing import (
        DomainRoutingTable,
        ManuscriptClassification,
        ModuleRoutingTable,
    )

    results = [
        ValidationResult(
            validator_name=f"syn_{code}",
            findings=[
                Finding(code=code, severity=sev, message=f"msg-{code}", validator=f"syn_{code}")
            ],
        )
        for sev, code in findings_by_severity
    ]
    suite = ValidationSuiteResult(validator_version="test", results=results)
    classification = ManuscriptClassification(
        paper_type="empirical_paper", pathway="data_science", recommended_stack="maximal"
    )
    module_routing = ModuleRoutingTable(
        route_version="test",
        pathway="data_science",
        paper_type="empirical_paper",
        recommended_stack="maximal",
        modules=[],
    )
    domain_routing = DomainRoutingTable(route_version="test", domains=[])
    return FinalVettingReport(
        run_id="test-run",
        manuscript_id="ord-test",
        classification=classification,
        module_routing=module_routing,
        domain_routing=domain_routing,
        validation_suite=suite,
    )


def test_fatal_findings_precede_major_in_revision_priorities() -> None:
    from manuscript_audit.reports.synthesis import synthesize_report

    # Deliberately pass major first, then fatal — output must reorder
    report = _make_vetting_report_with_findings([
        ("major", "major-issue"),
        ("fatal", "fatal-issue"),
    ])
    result = synthesize_report(report)
    priorities = result.revision_priorities
    assert len(priorities) == 2
    assert "fatal-issue" in priorities[0]
    assert "major-issue" in priorities[1]


def test_only_major_findings_still_surface() -> None:
    from manuscript_audit.reports.synthesis import synthesize_report

    report = _make_vetting_report_with_findings([("major", "big-problem")])
    result = synthesize_report(report)
    assert any("big-problem" in p for p in result.revision_priorities)


def test_empty_findings_gives_fallback_message() -> None:
    from manuscript_audit.reports.synthesis import synthesize_report

    report = _make_vetting_report_with_findings([])
    result = synthesize_report(report)
    assert result.revision_priorities == [
        "No fatal or major deterministic findings in the current audit stack."
    ]


# ---------------------------------------------------------------------------
# Phase 26: confidence shown in agent finding lines
# ---------------------------------------------------------------------------


def test_finding_line_includes_confidence_when_set() -> None:
    from manuscript_audit.reports.synthesis import _format_finding_line
    from manuscript_audit.schemas.findings import Finding

    finding = Finding(
        code="thin-abstract",
        severity="moderate",
        message="Abstract is very short.",
        validator="structure_contribution_agent",
        confidence=0.80,
    )
    line = _format_finding_line(finding)
    assert "[conf: 80%]" in line
    assert "thin-abstract" in line


def test_finding_line_omits_confidence_when_none() -> None:
    from manuscript_audit.reports.synthesis import _format_finding_line
    from manuscript_audit.schemas.findings import Finding

    finding = Finding(
        code="missing-required-section",
        severity="major",
        message="Methods section is missing.",
        validator="required_sections",
    )
    line = _format_finding_line(finding)
    assert "conf" not in line
    assert "missing-required-section" in line
