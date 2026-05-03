from manuscript_audit.bibliography_confidence.core import compute_confidence_summary as build_bibliography_confidence_summary
from manuscript_audit.parsers.bibtex import parse_bibtex
from manuscript_audit.parsers.dispatch import parse_manuscript
from manuscript_audit.parsers.latex import parse_latex_manuscript
from manuscript_audit.parsers.markdown import parse_markdown_manuscript
from manuscript_audit.parsers.notation import extract_notation_summary
from manuscript_audit.parsers.source_record import (
    build_source_record_candidates,
    build_source_records,
    summarize_source_records,
)
from manuscript_audit.parsers.source_verification import (
    CrossrefSourceRegistryClient,
    FixtureSourceRegistryClient,
    SourceRegistryLookupError,
    summarize_source_record_verifications,
    verify_source_records,
)

__all__ = [
    "CrossrefSourceRegistryClient",
    "FixtureSourceRegistryClient",
    "SourceRegistryLookupError",
    "build_bibliography_confidence_summary",
    "build_source_record_candidates",
    "build_source_records",
    "extract_notation_summary",
    "parse_bibtex",
    "parse_latex_manuscript",
    "parse_manuscript",
    "parse_markdown_manuscript",
    "summarize_source_record_verifications",
    "summarize_source_records",
    "verify_source_records",
]
