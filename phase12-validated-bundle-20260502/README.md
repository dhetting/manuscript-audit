# manuscript-audit

Local, Pixi-managed manuscript audit framework for statistical manuscripts.

## Current implemented slices

The project currently supports:

- deterministic parsing for Markdown and LaTeX manuscripts,
- companion BibTeX parsing,
- explicit manuscript classification and routing,
- deterministic validation with structured artifacts,
- a routed standard audit stack with persisted per-module outputs,
- revision verification with structured resolved, persistent, and new findings,
- local DuckDB storage for run, routing, validator, agent, revision, and report artifacts.

## Quickstart

```bash
pixi run lint
pixi run test
pixi run audit-core examples/software_equivalence_manuscript.md --output-dir data/outputs/core-demo
pixi run audit-standard examples/software_equivalence_manuscript.md --output-dir data/outputs/standard-demo
pixi run verify-revision tests/fixtures/manuscripts/revision_old.md tests/fixtures/manuscripts/revision_new.md --output-dir data/outputs/revision-demo
```


## Optional source verification

Use `pixi run verify-sources` with a registry fixture or a live provider to verify source-record candidates against a selected registry.
