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
10. Cross-artifact consistency matters: abstract, body, appendix, supplement, figures, tables, captions, references, and claims must agree.

## Preferred development philosophy

- Build the system as an engineered local workflow, not as a pile of prompts.
- Prefer rule-based and deterministic checks wherever possible.
- Use agents only for judgment-heavy tasks.
- Keep schemas explicit and stable.
- Build with test-driven discipline.
- Add modules conditionally through routing logic rather than by default.
- Avoid fake comprehensiveness: irrelevant modules must be skipped explicitly.
- Treat all claims, citations, and proof steps as untrusted until checked.

## Current architecture target

The framework should have five layers:

1. ingestion/parsing
2. routing/classification
3. deterministic validation
4. agent audits
5. report synthesis

## Core artifact types

Expected structured artifacts:
- parsed manuscript sections
- extracted bibliography entries
- extracted figures/tables/captions/equations
- routing tables
- module findings
- final vetting report
- revision verification report

## Shared core review modules

These should be treated as near-default for serious audits:
- structure and contribution
- literature review and claim validation
- bibliography metadata validation
- statistical validity and assumptions
- results/figures/tables consistency
- reproducibility and computational audit
- AI-generated manuscript risk audit
- editor/reviewer red-team
- style/precision/editorial consistency
- ethics/provenance/research integrity
- outside-the-box failure modes
- scope/estimand/question alignment
- narrative economy and relevance
- claim taxonomy and evidence alignment
- definition and term drift
- statistical graphics and table semantics
- version drift and cross-artifact synchronization
- supplement dependence and main-paper defensibility
- citation density and inline placement
- submission readiness
- human-authorship defensibility
- re-analysis feasibility
- adversarial counterexample/straw-man gap
- reporting guideline and venue compliance
- notation-to-code contract
- uncertainty provenance and propagation
- numerical stability and conditioning
- preprocessing/discretization/threshold sensitivity
- reviewer rebuttal readiness
- claim survivorship and core contribution

## Conditional specialized modules

These must be activated only when triggered:
- dependence structure and effective sample size
- multiplicity and researcher degrees of freedom
- Bayesian modeling and posterior diagnostics
- external validity and transportability
- decision relevance and operational claims

## Optional domain packs

These must be routed conditionally:
- causal inference
- prediction modeling
- equivalence/noninferiority
- simulation studies
- spatial/spatiotemporal statistics
- software/workflow papers
- Bayesian advanced methods
- time series/forecasting
- hierarchical/multilevel modeling
- diagnostic/prognostic/biomarker studies
- meta-analysis/evidence synthesis
- survey/sampling design
- network/interference
- experimental design/A-B testing

## Repository and tooling expectations

- Pixi-managed project
- Python implementation
- CLI-first user interface
- DuckDB for local run/findings storage
- pytest-based tests
- pre-commit checks
- structured intermediate artifacts in JSON/YAML/Markdown
- report templates kept in-repo
- prompts versioned in-repo
- no hidden state

## Output expectations

Every full run should produce:
- manuscript classification
- module routing table
- domain routing table
- deterministic validation results
- agent findings per module
- consolidated final report
- ranked revision priorities

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

## Immediate next build target

Build the initial repo skeleton with:
- pixi.toml
- pyproject.toml
- CLI scaffolding
- schemas for parsed manuscript and routing artifacts
- deterministic validators
- router workflow
- stub agent interfaces
- report synthesis scaffolding
- tests for routing and validators
