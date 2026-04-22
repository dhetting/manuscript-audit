# manuscript-audit

Local, Pixi-managed manuscript audit framework for statistical manuscripts.

## Current MVP slice

This bootstrap implements a minimal end-to-end path that can:

- ingest a markdown manuscript fixture,
- parse sections and citation mentions,
- classify the manuscript pathway and paper type,
- persist module and domain routing decisions,
- run deterministic validators,
- write structured JSON/YAML/Markdown artifacts,
- store run metadata in DuckDB.

## Quickstart

```bash
pixi run test
pixi run lint
pixi run audit-core examples/software_equivalence_manuscript.md --output-dir data/outputs/demo
```

If Pixi is unavailable, the underlying commands are:

```bash
ruff check .
python -m pytest tests/unit -q
PYTHONPATH=src python -m manuscript_audit.cli audit-core examples/software_equivalence_manuscript.md --output-dir data/outputs/demo --db-path data/working/demo.duckdb
```
