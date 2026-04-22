# ENGINEERING_MANIFEST.md

## Mission

Develop a local, reproducible, Pixi-managed manuscript audit framework that can:
1. parse manuscript artifacts,
2. route each manuscript to the correct review stack,
3. execute deterministic validators,
4. invoke only the relevant audit agents and domain packs,
5. synthesize a structured vetting report suitable for pre-submission review and revision verification.

## Product definition

This is not just a prompt set.  
This is a local software system that operationalizes the manuscript vetting framework.

The end state is a CLI-driven, testable application with:
- deterministic preprocessing,
- conditional routing,
- modular agent execution,
- structured outputs,
- reproducible local runs,
- revision verification support.

## Guiding engineering rules

1. Pixi is the authoritative workflow contract.
2. Deterministic checks run before any agent-based reasoning.
3. Routing is explicit and persisted.
4. Every module must declare applicability criteria.
5. Agent outputs must be stored as structured artifacts, not only prose.
6. The system must support minimal, standard, and maximal audit stacks.
7. Domain packs are optional and must never run unconditionally.
8. Tests must cover routing decisions, deterministic validators, report assembly, and artifact schemas.
9. Report synthesis must preserve uncertainty and disagreements across modules.
10. The system should be inspectable enough that a human reviewer can audit the audit.

## Proposed repository structure

```text
manuscript-audit/
  pixi.toml
  pixi.lock
  pyproject.toml
  .pre-commit-config.yaml
  src/
    manuscript_audit/
      cli.py
      config.py
      schemas/
      storage/
      parsers/
      routing/
      validators/
      agents/
      reports/
      workflows/
      prompts/
  templates/
  tests/
    unit/
    integration/
    golden/
  examples/
  data/
    inputs/
    working/
    outputs/
```

## Functional subsystems

### 1. Parsing subsystem
Responsibilities:
- ingest manuscript source files
- extract sections, headings, bibliography entries, figures, tables, captions, equations, appendix/supplement structure
- normalize these into structured schemas

Initial supported inputs:
- markdown
- LaTeX source
- BibTeX
- plain text
Future:
- docx
- pdf extraction workflows

### 2. Routing subsystem
Responsibilities:
- classify manuscript by pathway
- classify paper type
- detect evidence types
- detect claim types
- detect high-risk features
- decide applicable specialized statistical modules
- decide applicable optional domain packs
- persist routing decisions

Outputs:
- module routing table
- domain routing table
- recommended stack level

### 3. Deterministic validation subsystem
Responsibilities:
- validate section presence and structure
- validate bibliography structure and duplicate refs
- validate identifier format and metadata completeness
- validate unresolved placeholders
- validate figure/table reference coverage
- validate basic cross-artifact consistency
- validate citation density heuristics
- validate routing inputs

These checks should run without agent intervention.

### 4. Agent subsystem
Responsibilities:
- run only routed modules
- consume structured artifacts
- write structured findings
- separate findings by severity
- preserve module provenance

Core agent families:
- literature and claims
- bibliography/source-of-record
- statistical validity
- math/proofs/notation
- AI-risk
- reviewer red-team
- report synthesis

### 5. Reporting subsystem
Responsibilities:
- combine routing, validators, and agent outputs
- generate final vetting report
- generate revision verification report
- preserve module-level evidence
- allow reruns without corrupting previous artifacts

## Phase plan

### Phase 0: repo bootstrap
Deliverables:
- project skeleton
- Pixi environments and tasks
- CLI scaffolding
- schema stubs
- test scaffolding
- pre-commit and lint/test contract

Exit criteria:
- `pixi run test` passes
- `pixi run lint` passes
- basic CLI entrypoints exist

### Phase 1: schemas and artifact contract
Deliverables:
- pydantic schemas for manuscript, sections, bibliography entries, figures/tables/equations, routing tables, findings, reports
- JSON/YAML serialization contract
- artifact directory conventions

Exit criteria:
- schemas validated by tests
- example artifact roundtrips pass

### Phase 2: parsing MVP
Deliverables:
- markdown parser
- LaTeX section parser
- BibTeX parser
- extracted artifact writers

Exit criteria:
- sample manuscripts parse into stable schemas
- tests cover basic extraction behavior

### Phase 3: routing engine MVP
Deliverables:
- rule-based manuscript classifier
- specialized-module routing rules
- domain-pack routing rules
- stack recommendation logic

Exit criteria:
- routing tests for major manuscript archetypes
- explicit not-applicable cases tested

### Phase 4: deterministic validators MVP
Deliverables:
- section presence validator
- duplicate bibliography validator
- unresolved placeholder validator
- figure/table reference validator
- citation-density heuristic validator
- artifact consistency validator

Exit criteria:
- validators produce structured findings
- findings persist cleanly
- integration tests pass on sample manuscripts

### Phase 5: agent orchestration MVP
Deliverables:
- router workflow wrapper
- core audit agent interfaces
- routed module execution logic
- stored module findings

Exit criteria:
- standard stack runs end-to-end on at least one sample manuscript
- per-module outputs are persisted

### Phase 6: report synthesis MVP
Deliverables:
- final report generator
- module provenance tracking
- severity rollup
- revision-priority ranking

Exit criteria:
- final vetting report generated from sample run
- output deterministic enough for golden-style tests on structured sections

### Phase 7: revision verification workflow
Deliverables:
- compare old vs new manuscript artifacts
- rerun affected modules
- produce fix-status report

Exit criteria:
- revision verification report generated for fixture example

### Phase 8: expand specialist/domain coverage
Deliverables:
- additional domain-pack agent implementations
- improved prompt/runtime registry
- richer routing logic

Exit criteria:
- domain-pack routing tested
- at least representative pack implementations working end-to-end

## Initial Pixi design

Recommended environments:
- default
- dev
- docs
- optional sandbox

Recommended tasks:
- format
- lint
- test
- parse
- route
- validate
- audit-core
- audit-standard
- audit-maximal
- verify-revision
- build-report

## Artifact contract

Suggested artifacts:
- parsed/manuscript_sections.json
- parsed/references.json
- parsed/figures.json
- parsed/tables.json
- parsed/equations.json
- routing/module_routing.yaml
- routing/domain_routing.yaml
- findings/<module>.json
- findings/<module>.md
- reports/final_vetting_report.md
- reports/revision_verification_report.md

## Data storage

Use DuckDB as the local structured run store.

Suggested tables:
- runs
- manuscripts
- parsed_artifacts
- routing_decisions
- validator_findings
- agent_findings
- report_artifacts
- revision_links

## Testing strategy

### Unit tests
- schemas
- parsers
- routing rules
- validators
- report synthesis helpers

### Integration tests
- end-to-end standard stack run on fixture manuscript
- end-to-end routing on multiple archetypes
- artifact persistence and reload

### Golden tests
- stable routing outputs for fixture manuscripts
- stable structured findings for deterministic validators
- stable report section keys and severity rollups

## Engineering priorities

Priority order:
1. routing and artifact schemas
2. deterministic validators
3. CLI orchestration
4. agent interfaces and execution
5. report synthesis
6. revision verification
7. expanded domain-pack coverage

## Definition of done for the first real milestone

The first milestone is complete when the project can:
- parse a markdown or LaTeX manuscript plus BibTeX,
- produce routing tables,
- run a deterministic validation suite,
- execute a small standard audit stack,
- synthesize a final report,
- do all of the above through Pixi tasks,
- pass local tests.

## Non-goals for the first milestone

Do not require:
- every domain pack to be fully implemented
- every document format to be supported
- full PDF-native math extraction
- web-scale bibliography enrichment pipelines
- UI/dashboard work

## Expected development cadence

- implement smallest viable vertical slice first
- preserve stable interfaces
- write tests before expanding coverage
- prefer stubbed but real end-to-end flow over isolated sophistication
- keep prompts and schemas versioned together
