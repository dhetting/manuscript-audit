from pathlib import Path

from manuscript_audit.agents import run_routed_agents
from manuscript_audit.parsers import (
    FixtureSourceRegistryClient,
    build_bibliography_confidence_summary,
    build_source_records,
    parse_bibtex,
    parse_manuscript,
    verify_source_records,
)
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


def test_bibliography_agent_consumes_source_verification_results() -> None:
    manuscript = Path("tests/fixtures/manuscripts/bibliography_metadata.tex")
    parsed = parse_manuscript(manuscript)
    parsed.bibliography_entries = parse_bibtex(manuscript.with_suffix(".bib"))
    parsed.reference_section_present = True
    classification, module_routing, _ = build_routing_tables(parsed)
    validation_suite = run_deterministic_validators(parsed, classification)
    source_records = build_source_records(parsed.bibliography_entries)
    client = FixtureSourceRegistryClient.from_json(
        Path("tests/fixtures/registries/source_registry_fixture.json")
    )
    verifications = verify_source_records(parsed.bibliography_entries, source_records, client)

    confidence_summary = build_bibliography_confidence_summary(source_records, verifications)

    agent_suite = run_routed_agents(
        parsed,
        classification,
        validation_suite,
        module_routing,
        source_verifications=verifications,
        bibliography_confidence_summary=confidence_summary,
    )

    bibliography_result = next(
        result
        for result in agent_suite.results
        if result.module_name == "bibliography_metadata_validation"
    )
    codes = {finding.code for finding in bibliography_result.findings}
    assert "source-record-metadata-mismatch" in codes
    assert "bibliography-confidence-low" in codes
