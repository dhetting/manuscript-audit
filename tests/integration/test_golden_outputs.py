import json
from pathlib import Path

from manuscript_audit.parsers import parse_bibtex, parse_manuscript
from manuscript_audit.reports import synthesize_report
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.schemas.findings import FinalVettingReport
from manuscript_audit.validators import run_deterministic_validators


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_routing_outputs_match_golden_files() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/latex_equivalence.tex"))
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/latex_equivalence.bib")
    )
    parsed.reference_section_present = True
    classification, module_routing, domain_routing = build_routing_tables(parsed)

    assert classification.model_dump(mode="json") == _load_json(
        "tests/golden/routing/latex_equivalence_classification.json"
    )
    assert module_routing.model_dump(mode="json") == _load_json(
        "tests/golden/routing/latex_equivalence_module_routing.json"
    )
    assert domain_routing.model_dump(mode="json") == _load_json(
        "tests/golden/routing/latex_equivalence_domain_routing.json"
    )


def test_report_summary_matches_golden_file() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/latex_equivalence.tex"))
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/latex_equivalence.bib")
    )
    parsed.reference_section_present = True
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)
    report = synthesize_report(
        FinalVettingReport(
            run_id="golden-run",
            manuscript_id=parsed.manuscript_id,
            classification=classification,
            module_routing=module_routing,
            domain_routing=domain_routing,
            validation_suite=validation_suite,
        )
    )
    summary = {
        "pathway": report.classification.pathway,
        "paper_type": report.classification.paper_type,
        "recommended_stack": report.classification.recommended_stack,
        "validator_severity_counts": report.validation_suite.severity_counts,
        "revision_priorities": report.revision_priorities,
    }
    assert summary == _load_json("tests/golden/reports/latex_equivalence_report_summary.json")
