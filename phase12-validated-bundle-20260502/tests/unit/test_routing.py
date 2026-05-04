from pathlib import Path

from manuscript_audit.parsers import parse_markdown_manuscript
from manuscript_audit.routing.rules import build_routing_tables


def test_routes_software_equivalence_fixture() -> None:
    parsed = parse_markdown_manuscript(
        Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md")
    )
    classification, module_routing, domain_routing = build_routing_tables(parsed)
    assert classification.pathway == "data_science"
    assert classification.paper_type == "software_workflow_paper"
    assert classification.recommended_stack == "maximal"
    module_map = {item.name: item.applicable for item in module_routing.modules}
    domain_map = {item.name: item.applicable for item in domain_routing.domains}
    assert module_map["reproducibility_and_computational_audit"] is True
    assert domain_map["equivalence_noninferiority"] is True
    assert domain_map["simulation_studies"] is True
    assert domain_map["software_workflow_papers"] is True


def test_routes_theory_fixture() -> None:
    parsed = parse_markdown_manuscript(Path("tests/fixtures/manuscripts/theory_note.md"))
    classification, module_routing, _ = build_routing_tables(parsed)
    assert classification.pathway == "math_stats_theory"
    module_map = {item.name: item.applicable for item in module_routing.modules}
    assert module_map["math_proofs_and_notation"] is True
    assert module_map["statistical_validity_and_assumptions"] is False
