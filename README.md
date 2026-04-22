# manuscript-audit

Local, Pixi-managed manuscript audit framework for statistical manuscripts.

## Current implemented slices

The project currently supports:

- deterministic parsing for Markdown and LaTeX manuscripts,
- companion BibTeX parsing,
- explicit manuscript classification and routing,
- deterministic validation with structured artifacts,
- a routed standard audit stack with persisted per-module outputs,
- local DuckDB storage for run, routing, validator, agent, and report artifacts.

## Quickstart

```bash
pixi run lint
pixi run test
pixi run audit-core examples/software_equivalence_manuscript.md --output-dir data/outputs/core-demo
pixi run audit-standard examples/software_equivalence_manuscript.md --output-dir data/outputs/standard-demo
```
