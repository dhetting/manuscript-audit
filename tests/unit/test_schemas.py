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
