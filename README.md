# manuscript-audit

Local, Pixi-managed manuscript audit framework for statistical manuscripts.

## Current deterministic MVP slice

This build implements a stronger deterministic front end that can:

- ingest markdown and LaTeX manuscript fixtures,
- parse sections and citation mentions,
- parse BibTeX reference files into structured bibliography entries,
- classify the manuscript pathway and paper type,
- persist module and domain routing decisions,
- run deterministic validators,
- write structured JSON/YAML/Markdown artifacts,
- store run metadata in DuckDB.

## Implemented deterministic validators

- required section presence
- unresolved placeholders
- citation-density heuristic
- reference-section coverage
- duplicate bibliography key detection
- figure/table reference coverage
