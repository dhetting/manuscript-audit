# Copilot instructions for `manuscript-audit`

## Project purpose

This repository implements a **local, Pixi-managed manuscript audit framework** for academic manuscripts with a statistical focus.

The system must:

1. parse manuscript artifacts,
2. classify and route the manuscript to the correct review stack,
3. run **deterministic validators before any agent reasoning**,
4. run only the relevant routed modules and domain packs,
5. synthesize structured vetting and revision-verification reports.

This is an engineered local workflow, not a loose prompt collection.

---

## Non-negotiable engineering rules

### Source of truth
- Treat the **live repo on disk** as the only source of truth.
- Do **not** trust prior summaries, prior bundles, or prior chat claims over the actual repo files.
- Before changing anything, inspect the relevant live files and current tests.

### Workflow manager
- **Pixi is authoritative.**
- Use Pixi tasks and the Pixi environment for validation and execution.
- Do not assume the system `python` or Conda/base environment is correct.

### Development order
- Deterministic parsing/validation comes before broader agent behavior.
- Prefer the **smallest viable end-to-end slice**.
- Make cumulative changes only.
- Fix root causes, not superficial symptoms.

### Validation discipline
Do not claim work is complete unless the relevant validation passes from the same repo state.

Use Pixi-based validation:
- `pixi run lint`
- `pixi run test`

Run targeted workflows when relevant, for example:
- `pixi run audit-core ...`
- `pixi run audit-standard ...`
- `pixi run verify-revision ...`
- `pixi run verify-sources ...`

### Routing discipline
- Routing must be explicit and persisted.
- Modules and domain packs must run **conditionally**, not by default.
- Irrelevant modules must be explicitly skipped or marked not applicable.

### Structured artifacts
- Persist structured outputs in JSON, YAML, and Markdown.
- Preserve inspectability.
- Human reviewers must be able to audit the audit.

---

## Current architecture expectations

Primary package layout:

- `src/manuscript_audit/cli.py`
- `src/manuscript_audit/schemas/`
- `src/manuscript_audit/parsers/`
- `src/manuscript_audit/routing/`
- `src/manuscript_audit/validators/`
- `src/manuscript_audit/agents/`
- `src/manuscript_audit/reports/`
- `src/manuscript_audit/workflows/`
- `src/manuscript_audit/storage/`

Core layers:

1. ingestion/parsing
2. routing/classification
3. deterministic validation
4. agent audits
5. report synthesis
6. revision verification
7. source-of-record verification

---

## Current repo status guidance

The repo has already implemented substantial functionality including:

- Markdown and LaTeX parsing
- BibTeX parsing
- routing/classification
- deterministic validators
- standard routed audit workflow
- revision verification
- source-of-record planning and verification
- fixture-backed verification
- optional live Crossref path
- report synthesis and golden coverage

The repo is currently beyond phase 11.

### Current active development slice
The current WIP development area is **bibliography confidence rollups** derived from source verification and integrated into:

- standard audit reporting,
- standalone source-verification reporting,
- bibliography module findings,
- revision priorities.

If you work in this area:
- verify the live implementation first,
- do not assume the slice is fully closed until workflow commands also pass,
- keep changes narrow and cumulative.

---

## Coding guidance

### General style
- Keep interfaces explicit and stable.
- Use Pydantic schemas for structured artifacts.
- Use Typer for CLI behavior.
- Use DuckDB for structured run storage.
- Prefer small, composable helpers over large opaque functions.
- Avoid hidden state.
- Avoid cleverness that makes audit behavior harder to inspect.

### Deterministic-first rule
When deciding whether to add logic:
- first ask whether it can be deterministic,
- only use agent-style reasoning where judgment is genuinely required.

### Test-first posture
When making changes:
1. identify the exact existing behavior and contracts,
2. add or update tests for the desired behavior,
3. implement the smallest fix or extension,
4. rerun lint/tests/workflows.

### Do not do these
- Do not widen agent scope unnecessarily.
- Do not add unconditional module execution.
- Do not collapse routing into one giant agent.
- Do not hide uncertainty in final reports.
- Do not break structured artifact contracts casually.
- Do not change bundle structure assumptions without reason.

---

## Reporting and artifact expectations

Every meaningful workflow should continue to produce structured outputs such as:
- parsed manuscript artifacts
- routing tables
- deterministic findings
- agent findings
- vetting reports
- revision verification reports
- source verification artifacts

When extending report synthesis:
- preserve severity distinctions,
- preserve provenance,
- surface uncertainty and disagreement,
- keep the audit inspectable.

---

## Bundle and handoff requirements

When preparing repo update bundles:
- the archive root must contain **repo-relative contents directly**
- do **not** double-bundle with an extra nested repo folder

Correct structure:
- `bundle_root/README.md`
- `bundle_root/pixi.toml`
- `bundle_root/src/...`
- `bundle_root/tests/...`

Incorrect structure:
- `bundle_root/manuscript-audit/...`

---

## Commands to prefer

Use Pixi-based commands from the repo root.

Typical validation:
```bash
pixi run lint
pixi run test
