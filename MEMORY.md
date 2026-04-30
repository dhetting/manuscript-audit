# MEMORY.md

## Project identity

Project name: manuscript-audit  
Purpose: build a local, Pixi-managed, agent-assisted framework for rigorous pre-submission vetting of academic journal articles with a statistical focus.  
Primary goal: route each manuscript to the right review stack, run deterministic validators first, then run only the relevant audit agents and domain packs, and synthesize a structured final vetting report.

## Core design principles

1. Reproducibility first. The framework must run locally in a deterministic, testable way.
2. Pixi is the outer workflow manager. Use Pixi for environments, lockfiles, tasks, and reproducible execution.
3. Agents are not the whole system. Deterministic parsers and validators must handle all objective checks before any agent is invoked.
4. The framework must be comprehensive but conditional. It should route to relevant modules and explicitly mark irrelevant modules as not applicable.
5. Every major output should be structured, inspectable, and rerunnable.
6. Review outputs must separate fatal flaws, major weaknesses, moderate concerns, and minor issues.
7. Literature and citation checking must be source-grounded and adversarial to hallucinated or inflated claims.
8. Bibliography metadata validation is a first-class review stage.
9. AI-generated manuscript risks are treated as a dedicated audit domain, not an afterthought.
10. Cross-artifact consistency matters: abstract, body, appendix, supplement, figures, tables, captions, references, equations, notation, and claims must agree.

## Preferred development philosophy

- Build the system as an engineered local workflow, not as a pile of prompts.
- Prefer rule-based and deterministic checks wherever possible.
- Use agents only for judgment-heavy tasks.
- Keep schemas explicit and stable.
- Build with test-driven discipline.
- Add modules conditionally through routing logic rather than by default.
- Avoid fake comprehensiveness: irrelevant modules must be skipped explicitly.
- Treat all claims, citations, and proof steps as untrusted until checked.
- Always audit the exact live repo before changing anything.
- Do not trust prior summaries, bundles, or claimed state over the actual local repo.
- Make cumulative changes only.
- Fix root causes, not superficial symptoms.

## Repository and tooling expectations

- Pixi-managed project
- Python implementation
- CLI-first user interface
- DuckDB for local run/findings storage
- pytest-based tests
- pre-commit-compatible lint/test discipline
- structured intermediate artifacts in JSON/YAML/Markdown
- report templates kept in-repo
- prompts versioned in-repo
- no hidden state

## Current architecture target

The framework currently aims for these layers:

1. ingestion/parsing
2. routing/classification
3. deterministic validation
4. agent audits
5. report synthesis
6. revision verification
7. source-of-record verification

## Current functional status through validated phases

### Validated core capabilities already implemented before the current WIP slice

- project skeleton with Pixi, pyproject, CLI, tests, and package layout
- Pydantic schemas for parsed artifacts, routing, findings, reports
- Markdown and LaTeX manuscript parsing
- BibTeX parsing
- routing/classification with persisted module and domain routing tables
- deterministic validators for:
  - required sections
  - unresolved placeholders
  - citation density
  - bibliography/reference alignment
  - duplicate bibliography keys
  - figure/table reference coverage
  - bibliography metadata completeness
  - year format
  - DOI format
  - venue metadata
  - source identifier presence
  - orphaned figure/table definitions
  - equation callout/definition consistency
  - claim-to-section alignment
  - notation coverage / undefined symbols
  - notation-to-section alignment
- source-of-record planning artifacts
- source-of-record enrichment artifacts
- source-of-record verification workflow
- fixture-backed registry verification
- optional Crossref-backed live verification path
- multi-candidate ambiguity handling
- provider-error handling
- revision verification workflow
- standard routed audit workflow with persisted module findings
- golden tests for routing and report summaries
- phase 11 integrated source verification into the main standard audit workflow and main report synthesis

## Phase 11 validated state

Phase 11 was the last clearly validated feature phase before the current WIP work.

Phase 11 added:
- integrated source verification into `audit-standard`
- optional source verification arguments on `audit-standard`
- final vetting report fields:
  - `source_verification_provider`
  - `source_verification_summary`
- bibliography module consumes real source-verification results and emits findings for:
  - metadata mismatches
  - ambiguous matches
  - lookup not found
  - provider errors
- standard workflow persists:
  - `findings/source_record_verifications.json`
  - `findings/source_record_verification_summary.json`
- report synthesis prioritizes source-verification problems in main revision priorities

## Phase 12 validated state

Phase 12 was validated end-to-end from the live repo on 2026-04-30.

Phase 12 added:
- `BibliographyConfidenceSummary` schema in `schemas/artifacts.py`
- `build_bibliography_confidence_summary(...)` in `parsers/source_verification.py`
- confidence summary integrated into:
  - `FinalVettingReport` (field: `bibliography_confidence_summary`)
  - `SourceRecordVerificationReport` (field: `bibliography_confidence_summary`)
- report synthesis integration: bibliography confidence informs revision priorities
- bibliography agent findings:
  - `bibliography-confidence-low` (moderate severity)
  - `bibliography-confidence-critical` (major severity)
- standard workflow persists:
  - `findings/bibliography_confidence_summary.json`
- source-verification workflow persists:
  - `parsed/bibliography_confidence_summary.json`
- test updates covering these behaviors (44 tests pass)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 44 passed
- `pixi run audit-standard <tex> --output-dir <out> --source-verification-provider fixture --registry-fixture <fixture>` → produces bibliography confidence artifacts
- `pixi run verify-sources <tex> --output-dir <out> --provider fixture --registry-fixture <ambiguous_fixture>` → produces bibliography confidence artifacts

## Current immediate next task

Phase 12 is closed. Identify and begin the next development phase.

Candidate next phases (in priority order):
1. **Phase 13: claim grounding and citation adequacy** — deterministic checks for citation-claim alignment, insufficient citation density per claim type, and citationless assertion detection
2. **Phase 13: notation and equation audit hardening** — extend equation/notation coverage validators with agent-assisted checks for undefined symbols, inconsistent notation, and missing equation labels
3. **Phase 13: cross-artifact consistency** — cross-check claims between abstract, body, appendix, and supplement
4. **Phase 13: enhanced routing** — add domain-specific routing beyond the current applied/theory/software archetypes

The highest-value next slice is likely **claim grounding and citation adequacy** since that is core to the adversarial citation review goal in the project design.

## Bundle and handoff requirements

The user wants future repo-update bundles to be structured with repo-relative contents directly at the archive root.

Correct structure:
- bundle_root/README.md
- bundle_root/pixi.toml
- bundle_root/src/...
- bundle_root/tests/...

Incorrect structure:
- bundle_root/manuscript-audit/...

Future unzip/rsync commands must assume:
- unzip to `~/Downloads/<bundle_name>`
- rsync from `~/Downloads/<bundle_name>/` directly into `~/src/manuscript-audit/`

## Response-format preferences for future bundle handoffs

For each future bundle/update response, always provide:
1. unzip + rsync command for the latest bundle
2. Pixi test/validation commands
3. explicit `git add` command listing each changed file
4. explicit `git rm` command listing removed tracked files, if any
5. commit command

Do not use blanket `git add -A` unless the user explicitly asks for it.

## Important anti-patterns to avoid

- one giant agent doing everything
- routing after the fact
- irrelevant module execution
- uncited or weakly grounded bibliography validation
- proof review without explicit notation tracking
- polished report synthesis that hides uncertainty
- vague module applicability logic
- hardcoded manuscript archetypes without extensible schemas
- undocumented prompts or prompt drift outside version control
- claiming a bundle is validated when lint/test/workflow validation was not completed from the same repo state
- double-bundled zip archives

## Current repo path assumptions

Assume:
- local repo path is `~/src/manuscript-audit`
- downloaded bundles are in `~/Downloads`

## Current working posture for the next chat

- treat the actual live repo as the source of truth
- audit it first
- do not trust prior bundle claims over the live files
- continue from the phase-12 bibliography-confidence slice
- first close the remaining workflow-validation gap
- then package a truly validated phase-12 bundle
- only then proceed to the next phase
