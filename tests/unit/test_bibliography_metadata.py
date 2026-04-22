import json
from pathlib import Path

from manuscript_audit.parsers import parse_bibtex, parse_manuscript
from manuscript_audit.routing.rules import classify_manuscript
from manuscript_audit.validators import run_deterministic_validators


def test_bibliography_metadata_validators_detect_incomplete_and_malformed_entries() -> None:
    parsed = parse_manuscript(Path("tests/fixtures/manuscripts/bibliography_metadata.tex"))
    parsed.bibliography_entries = parse_bibtex(
        Path("tests/fixtures/manuscripts/bibliography_metadata.bib")
    )
    parsed.reference_section_present = True
    classification = classify_manuscript(parsed)
    suite = run_deterministic_validators(parsed, classification)
    payload = json.loads(suite.model_dump_json())
    findings = [finding for result in payload["results"] for finding in result["findings"]]
    codes = {finding["code"] for finding in findings}
    assert "incomplete-bibliography-metadata" in codes
    assert "invalid-bibliography-year" in codes
    assert "invalid-bibliography-doi" in codes
