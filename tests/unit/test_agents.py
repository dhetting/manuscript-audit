from pathlib import Path

from manuscript_audit.agents import run_routed_agents
from manuscript_audit.parsers import parse_manuscript
from manuscript_audit.routing import build_routing_tables
from manuscript_audit.validators import run_deterministic_validators


def test_routed_agents_execute_only_applicable_modules() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/software_equivalence_manuscript.md"))
    classification, module_routing, _ = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)
    agent_suite = run_routed_agents(parsed, classification, validation_suite, module_routing)
    module_names = {result.module_name for result in agent_suite.results}
    assert "reproducibility_and_computational_audit" in module_names
    assert "bibliography_metadata_validation" in module_names
    assert "math_proofs_and_notation" not in module_names
