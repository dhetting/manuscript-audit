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

## Phase 13 validated state

Phase 13 was validated end-to-end from the live repo on 2026-04-30.

Phase 13 added:
- Two new deterministic validators in `validators/core.py`:
  - `validate_citationless_quantitative_claims(parsed)` — detects paragraphs with numeric metrics (%, fold, Nx) combined with evaluative language but no citation
  - `validate_citationless_comparative_claims(parsed)` — detects paragraphs with strong external-comparison language (state-of-the-art, outperforms, superior to, etc.) but no citation
- New regex patterns: `CITATION_IN_TEXT_RE`, `METRIC_RE`, `EVALUATIVE_CONTEXT_RE`, `COMPARATIVE_CLAIM_RE`, `_SKIP_SECTIONS`, `_split_paragraphs`
- Both validators scan the abstract and all non-reference/non-bibliography sections
- Abstract is scanned from `parsed.abstract`; the "Abstract" section in `sections` is skipped to prevent duplicates
- New finding codes:
  - `citationless-quantitative-claim` (moderate)
  - `citationless-comparative-claim` (moderate)
- New test fixture: `tests/fixtures/manuscripts/claim_grounding.md`
- New unit tests: 3 new tests covering detection and non-flagging of cited claims
- 47 total tests pass (up from 44)
- Golden test for `latex_equivalence.tex` is unaffected (no citationless findings on that fixture)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 47 passed
- `pixi run audit-standard tests/fixtures/manuscripts/claim_grounding.md --output-dir <out>` → produces 5 citationless findings (3 quantitative, 2 comparative)
- `pixi run audit-standard tests/fixtures/manuscripts/bibliography_metadata.tex --output-dir <out> --source-verification-provider fixture --registry-fixture <fixture>` → unchanged behavior, bibliography confidence still produced

## Phase 14 validated state

Phase 14 was validated end-to-end from the live repo on 2026-04-30.

Phase 14 added:
- One new deterministic validator: `validate_abstract_metric_coverage(parsed)` in `validators/core.py`
- New helper constants and functions: `_SUPPORT_SECTION_KEYWORDS`, `_is_support_section()`, `_extract_metric_values()`
- Algorithm: extract normalized `%`/`fold`/`x` metrics from abstract; check if each appears in support sections (results, discussion, conclusion, experiments, evaluation, analysis); flag missing ones
- Finding code: `abstract-metric-unsupported` (moderate)
- Skips silently when: no abstract, no abstract metrics, or no support sections present
- New fixture: `tests/fixtures/manuscripts/cross_artifact_consistency.md`
- 3 new unit tests (detection, no-false-positive, skip-without-support-sections)
- 50 total tests pass (up from 47)
- Golden test for `latex_equivalence.tex` still `{'moderate': 4, 'minor': 1}`

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 50 passed
- `pixi run audit-standard tests/fixtures/manuscripts/cross_artifact_consistency.md --output-dir <out>` → 2 findings: `95%` and `3x` absent from Results

Known limitations (acceptable for MVP):
- Does not bind metric to semantic anchor (e.g., "95% accuracy" vs "95% confidence interval")
- Does not exempt metrics from cited prior-work sentences in the abstract
- Restricted to `%`, `fold`, `x`-factor forms only

## Phase 15 validated state

Phase 15 was validated end-to-end from the live repo on 2026-05-01.

Phase 15 added:
- One new deterministic validator: `validate_unlabeled_equations(parsed, classification)` in `validators/core.py`
  - Only applies to LaTeX theory papers (`paper_type == "theory_paper"`)
  - Flags each equation block lacking `\label{}` as `equation-missing-label` (minor)
  - Accepts `classification` parameter (like `validate_claim_section_alignment`) — does not fire on empirical/software papers
- New concrete agent: `MathProofsNotationAgent` in `agents/modules.py`
  - Replaces `StubRoutedAgent` for the `math_proofs_and_notation` module
  - Calls `extract_notation_summary(parsed)` internally (does not modify agent runner signature)
  - Finding 1: `low-notation-definition-coverage` (moderate) — when >50% of ≥3 equation symbols lack textual definition hints
  - Finding 2: `missing-notation-section` (moderate) — when manuscript has equation blocks but no notation/preliminaries/definitions/background/setup section
- Registered in `agents/runner.py`: `"math_proofs_and_notation": MathProofsNotationAgent()`
- New constant `NOTATION_SECTION_RE` in `agents/modules.py`
- Import of `extract_notation_summary` added to `agents/modules.py`
- 4 new unit tests in `test_validators.py` (unlabeled detected, labeled skipped, non-theory skipped)
- 1 new unit test in `test_agents.py` (agent emits `missing-notation-section`)
- 54 total tests pass (up from 50)
- Golden test for `latex_equivalence.tex` still `{'moderate': 4, 'minor': 1}` (software paper, validator scoped to theory papers)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 54 passed
- `pixi run audit-standard tests/fixtures/manuscripts/software_equivalence_manuscript.md --output-dir <out> --registry-fixture tests/fixtures/registries/source_registry_fixture.json` → clean
- `pixi run verify-sources tests/fixtures/manuscripts/bibliography_metadata.tex --output-dir <out> --registry-fixture tests/fixtures/registries/source_registry_fixture.json` → clean

Known limitations (acceptable for MVP):
- `missing-notation-section` fires for all theory papers with equations — does not check if definitions are already embedded inline
- `low-notation-definition-coverage` uses `extract_notation_summary` which relies on regex-based definition hint detection (`X denotes`, `let X be`, `where X is` patterns)

## Phase 16 validated state

Phase 16 was validated end-to-end from the live repo on 2026-05-01.

Phase 16 added:
- New constants in `validators/core.py`: `_CLAIM_GROUNDING_CODES` (frozenset of three finding codes), `CLAIM_EVIDENCE_GAP_THRESHOLD = 3`
- New meta-validator: `validate_claim_evidence_escalation(suite: ValidationSuiteResult) -> ValidationResult`
  - Takes the full `ValidationSuiteResult` as input (runs after individual claim validators)
  - Counts findings with codes in `_CLAIM_GROUNDING_CODES`: `citationless-quantitative-claim`, `citationless-comparative-claim`, `abstract-metric-unsupported`
  - When count ≥ 3: emits `systemic-claim-evidence-gap` (major) surfacing the count and code summary
  - Wired into `run_deterministic_validators()` via a two-step partial→append pattern (partial suite built first, meta-validator appended last)
- The `major` severity causes the finding to flow into revision priorities automatically (report synthesis already escalates `major`/`fatal`)
- 3 new unit tests: threshold-met, below-threshold, and end-to-end via `claim_grounding.md` fixture
- 57 total tests pass (up from 54)
- Golden test for `latex_equivalence.tex` still `{'moderate': 4, 'minor': 1}` (zero claim-grounding findings on that fixture)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 57 passed
- `pixi run audit-standard tests/fixtures/manuscripts/claim_grounding.md --output-dir <out>` → `systemic-claim-evidence-gap` appears in `findings/deterministic_validators.json` and in `reports/final_vetting_report.{json,md}` revision priorities

Known limitations (acceptable for MVP):
- Does not de-duplicate across multiple manuscript passes (each full validator run is independent)
- Threshold of 3 is a fixed constant — not adaptive to manuscript length or section count

## Phase 17 validated state

Phase 17 was validated end-to-end from the live repo on 2026-05-01.

Phase 17 added:
- Two new test fixtures:
  - `tests/fixtures/manuscripts/revision_claim_old.md`: contains unsupported quantitative claims, an unsupported comparative claim, and an abstract metric absent from Results — triggers `citationless-quantitative-claim` (×2), `citationless-comparative-claim` (×1), `abstract-metric-unsupported` (×1), `systemic-claim-evidence-gap` (×1, major escalation)
  - `tests/fixtures/manuscripts/revision_claim_new.md`: same manuscript with citations added to all previously unsupported claims and abstract metric added to Results — zero claim-grounding findings
- New integration test `test_phase13_to_16_finding_codes_resolve_after_revision` in `test_revision_verification.py`
  - Runs full `run_revision_verification_workflow` on the old→new pair
  - Asserts `citationless-quantitative-claim`, `citationless-comparative-claim`, `abstract-metric-unsupported`, and `systemic-claim-evidence-gap` all appear in `resolved_findings`
  - Asserts none of those codes remain in `persistent_findings`
- No changes to production code — the revision workflow already tracked all finding codes generically; this phase adds the missing fixture-backed coverage
- 58 total tests pass (up from 57)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 58 passed

## Phase 18 validated state

Phase 18 was validated end-to-end from the live repo on 2026-05-01.

Phase 18 added:
- Three private helper functions in `cli.py`:
  - `_format_audit_summary(report)` — severity counts (fatal/major/moderate/minor + total), pathway, stack, priority count
  - `_format_sources_summary(report)` — total/verified/issues/skipped counts, bibliography confidence level, priority count
  - `_format_revision_summary(report)` — resolved/persistent/introduced counts and route-changed flag
- Connected to all four applicable commands:
  - `audit-core` → `_format_audit_summary`
  - `audit-standard` → `_format_audit_summary`
  - `verify-revision` → `_format_revision_summary`
  - `verify-sources` → `_format_sources_summary`
- Updated 4 existing CLI tests to assert presence of summary tokens in `result.output`
- 58 tests pass (count unchanged — updated tests, no new tests)

Example output after `audit-standard`:
```
Completed standard run run-20260501T153602Z for my-manuscript
  findings:  fatal=0  major=3  moderate=9  minor=3  (15 total)
  routing:   data_science | maximal stack | 4 priorities
```

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 58 passed
- `pixi run audit-standard ... --output-dir <out>` → summary lines appear as expected

## Phase 19 validated state

Phase 19 was validated end-to-end from the live repo on 2026-05-01.

Phase 19 added:
- Updated `render_revision_verification_report` in `reports/synthesis.py`:
  - Added `## Finding code summary` section between revision priorities and the detailed finding lists
  - Three count blocks: `Resolved (N)`, `Persistent (N)`, `Introduced (N)` each listing `count× code` lines sorted alphabetically
  - "none" displayed when a category is empty
  - Uses `collections.Counter` on `ref.code` — no schema changes
- Added assertions in both revision integration tests:
  - `test_revision_verification_writes_structured_artifacts`: checks `## Finding code summary` appears in generated Markdown
  - `test_phase13_to_16_finding_codes_resolve_after_revision`: checks code names appear in summary and that summary section precedes the detailed sections
- 58 tests pass (count unchanged — updated tests, no new tests)

Example output:
```
## Finding code summary

Resolved (6):
  1× abstract-metric-unsupported
  1× citationless-comparative-claim
  2× citationless-quantitative-claim
  1× systemic-claim-evidence-gap
Persistent (5):
  ...
Introduced (4):
  ...
```

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 58 passed
- `pixi run verify-revision old.md new.md --output-dir <out>` → summary section appears correctly in `.md` report

## Phase 20 validated state

Phase 20 was validated end-to-end from the live repo on 2026-05-01.

Phase 20 added:
- Moved `NOTATION_SECTION_RE` from `agents/modules.py` to `validators/core.py` (single definition, imported by agents)
- New constant `PROOF_CONTENT_SECTION_RE` in `validators/core.py`: matches "proof", "proofs", "main result", "theorem", "lemma", "corollary", "proposition(s)"
- New deterministic validator `validate_notation_section_ordering(parsed, classification)`:
  - Only applies to theory papers (`paper_type == "theory_paper"`)
  - Scans `parsed.sections` order for notation-type titles and content-type titles
  - If first notation section index > first content section index → `notation-section-out-of-order` (moderate)
  - Silently skips if no notation section or no content section found
  - Wired into `run_deterministic_validators()` between `validate_notation_section_alignment` and `validate_claim_section_alignment`
- New fixture: `tests/fixtures/manuscripts/notation_ordering_gap.md` — theory paper with Proof section before Notation
- 4 new unit tests: out-of-order detected, in-order not flagged, non-theory skipped, end-to-end via fixture
- 62 total tests pass (up from 58)
- Golden test for `latex_equivalence.tex` still `{'moderate': 4, 'minor': 1}` (software paper, validator scoped to theory papers)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 62 passed

Known limitations (acceptable for MVP):
- Uses section title matching only — does not track symbol occurrences across section bodies
- A paper could have all sections correctly ordered but still use symbols before defining them inline; this validator does not catch that

## Phase 21 validated state

Phase 21 was validated end-to-end from the live repo on 2026-05-01.

Phase 21 added:
- New constants in `validators/core.py`:
  - `ABSTRACT_OVERLONG_THRESHOLD = 350` (words)
  - `SECTION_THIN_THRESHOLD = 30` (words)
  - `_SUBSTANTIAL_SECTION_RE`: matches methods, results, discussion, experiments, analysis, evaluation, conclusions
  - `_word_count(text)`: helper returning `len(text.split())`
- New deterministic validator `validate_abstract_length(parsed)`:
  - Flags `overlong-abstract` (minor) when `parsed.abstract` > 350 words
  - Skips empty abstracts
  - Does NOT duplicate the agent's `thin-abstract` check (< 30 words)
- New deterministic validator `validate_section_body_completeness(parsed)`:
  - Scans sections matching `_SUBSTANTIAL_SECTION_RE` (Methods, Results, Discussion, etc.)
  - Flags `underdeveloped-section` (moderate) when body word count < 30
  - Location: `section '<title>'`, evidence: `<N> words`
- Wired both into `run_deterministic_validators()` at the end of the pre-escalation list
- Updated golden file: `latex_equivalence_report_summary.json` → `{moderate: 7, minor: 1}` (was 4, now +3 for thin Methods, Results, Discussion sections in the fixture — correct behavior, the fixture IS a minimal test file)
- 4 new unit tests; 66 total tests pass (up from 62)

Validated commands:
- `pixi run lint` → clean
- `pixi run test` → 66 passed

Known limitations (acceptable for MVP):
- `_word_count` counts raw tokens; LaTeX commands (e.g., `\cite{...}`) inflate the count slightly
- `_SUBSTANTIAL_SECTION_RE` uses section title matching only; renamed sections (e.g., "Empirical Findings") are not caught

## Current immediate next task

Phase 86 is closed. Next candidate phases listed in phase 86 entry above.

## Phase 22–62 validated state

All phases validated end-to-end from the live repo on 2026-05-01.

**Phase 22** (`4dacc2d`) — Fatal escalation tier
- `_FATAL_TRIGGER_CODES` frozenset: `{systemic-claim-evidence-gap, missing-required-section}`
- `validate_critical_escalation(suite)`: emits `critical-structural-claim-failure` (fatal) when both trigger codes co-occur
- Second partial→append step in `run_deterministic_validators()`

**Phase 23** (`688b748`) — Fatal-first revision priorities
- `_SEVERITY_RANK` dict in `synthesis.py`; stable sort on `(rank, msg)` tuples
- Fatal findings always precede major within the same report section

**Phase 24** (`0e4876d`) — Passive voice density validator
- `validate_passive_voice_density(parsed)` → `high-passive-voice-density` (minor)
- Fires on Methods/Methodology sections with >45% passive sentences (min 4 sentences)

**Phase 25** (`7fe6844`) — Agent finding confidence scores
- Added optional `confidence: float | None` field to `Finding` schema
- `StructureContributionAgent`, `BibliographyMetadataAgent`, `MathProofsNotationAgent` emit calibrated scores

**Phase 26** (`3fa9bda`) — Confidence in report output
- `_format_finding_line()` appends `[conf: X%]` in agent finding lines when confidence is set

**Phase 27** (`740712a`) — Sentence-level claim localization
- `_extract_trigger_sentence(para, *patterns)` helper
- Both citationless-claim validators surface the specific triggering sentence in `evidence[0]`

**Phase 28** (`b8f52e3`) — Duplicate quantitative claim detection
- `validate_duplicate_claims(parsed)` → `duplicate-quantitative-claim` (minor)
- Flags numeric patterns appearing verbatim in ≥2 non-abstract sections

**Phase 30** (`ec49122`) — Hedging language density validator
- `validate_hedging_density(parsed)` → `excessive-hedging-language` (minor)
- Fires on Discussion/Conclusion with >25% hedged sentences (min 4 sentences)

**Phase 31** (`ec49122`) — Missing related work section validator
- `validate_related_work_coverage(parsed, classification)` → `missing-related-work-section` (moderate)
- Fires for empirical/applied/software papers; skips theory papers

**Phase 32** (`ec49122`) — Missing limitations coverage validator
- `validate_limitations_coverage(parsed, classification)` → `missing-limitations-section` (moderate)
- Accepts dedicated section OR inline limitations language in Discussion/Conclusion
- `_EMPIRICAL_PAPER_TYPES = frozenset({"empirical_paper", "applied_stats_paper", "software_workflow_paper"})`

**Phase 33** (`d495391`) — Acronym consistency validator
- `validate_acronym_consistency(parsed)` → `acronym-used-before-definition` and `undefined-acronym` (both moderate)
- `_ACRONYM_DEF_RE`, `_ACRONYM_USE_RE` (lookahead/lookbehind, not `\b` — Python `\b` does not work with `[A-Z]` char classes)
- `_COMMON_ACRONYMS` exempts URL/PDF/API/ML/AI/NLP/etc.
- Scans document in paragraph order tracking first-definition position

**Phase 34** (`d495391`) — Methods tense consistency validator
- `validate_methods_tense_consistency(parsed)` → `inconsistent-methods-tense` (minor)
- `METHODS_TENSE_THRESHOLD = 0.35` — fires when >35% of tense-bearing sentences in Methods are present-tense
- Requires ≥5 tense-bearing sentences to avoid false positives

**Phase 35** (`d495391`) — Sentence length outlier validator
- `validate_sentence_length_outliers(parsed)` → `overlong-sentence` (minor)
- `SENTENCE_LENGTH_THRESHOLD = 60` words; `_FINDINGS_PER_SECTION_CAP = 3` per section
- Golden updated: `latex_equivalence_report_summary.json` moderate count 9→10

**Phase 37** (`0b687d0`) — Citation cluster gap detector
- `validate_citation_cluster_gap(parsed, classification)` → `citation-cluster-gap` (minor)
- Fires in Results/Discussion of empirical papers when 5+ consecutive sentences have no citation
- Requires ≥8 sentences in section; _CITATION_RE handles [N], Author et al. YYYY, \cite{key}

**Phase 38** (`0b687d0`) — Power-word overuse detector
- `validate_power_word_overuse(parsed)` → `power-word-overuse` (minor)
- Fires when any term from `_POWER_WORDS` appears >3× across abstract + introduction combined
- `_POWER_WORDS`: novel, state-of-the-art, significant, unprecedented, groundbreaking, revolutionary…

**Phase 39** (`0b687d0`) — Number formatting consistency validator
- `validate_number_format_consistency(parsed)` → `number-format-inconsistency` (minor)
- Fires when same-magnitude large numbers appear in both bare (10000) and comma-formatted (10,000) styles within a section

**Phase 40** (`0b687d0`) — Abstract keyword coverage validator
- `validate_abstract_keyword_coverage(parsed)` → `abstract-body-disconnect` (moderate)
- Extracts capitalized multi-word phrases and hyphenated compounds from abstract
- Fires when <30% of extracted terms appear in body; requires ≥3 extracted terms

**Phase 42** (`37ebc83`) — Contribution claim count verifier
- `validate_contribution_claim_count(parsed)` → `contribution-count-mismatch` (moderate)
- Detects "make N contributions" in abstract/intro; counts enumerated body items
- Fires when body items < claimed count; requires claimed count ≥ 2

**Phase 43** (`37ebc83`) — First-person consistency validator
- `validate_first_person_consistency(parsed)` → `first-person-inconsistency` (minor)
- Fires when 'I' and 'we' both appear and minority usage exceeds 10% of combined uses
- Excludes abstract and references sections

**Phase 44** (`37ebc83`) — Caption quality validator
- `validate_caption_quality(parsed)` → `short-caption` and `caption-missing-period` (both minor)
- Uses `figure_definitions` / `table_definitions` already extracted by parsers
- Short-caption fires when caption < 8 words; missing-period fires on unterminated captions
- revision_new.md fixture caption updated to ≥ 8 words to avoid regression

**Phase 45** (`37ebc83`) — Reference staleness validator
- `validate_reference_staleness(parsed, classification)` → `stale-reference-majority` (minor)
- Fires for empirical papers when >60% of dated entries are older than 10 years
- Requires ≥ 10 dated entries; theory papers exempt
- `_CURRENT_YEAR` computed at module load via `datetime.date.today().year`

**Phase 47** (`3e13012`) — Terminology drift detector
- `validate_terminology_drift(parsed)` → `terminology-drift` (minor)
- Scans for hyphenated compound terms; checks if spaced forms also appear
- `_HYPHEN_TERM_RE`: requires ≥3-char components; spaced form checked via substring regex
- Fires when both forms exist with combined ≥3 occurrences

**Phase 48** (`3e13012`) — Introduction structure validator
- `validate_introduction_structure(parsed)` → `missing-introduction-arc` (minor)
- Checks motivation (`_INTRO_MOTIVATION_RE`), gap (`_INTRO_GAP_RE`), contribution (`_INTRO_CONTRIBUTION_RE`) signals
- Fires when ≥2 arcs absent; requires ≥100 words in introduction

**Phase 49** (`3e13012`) — Reproducibility checklist validator
- `validate_reproducibility_checklist(parsed, classification)` → `missing-reproducibility-element` (minor)
- Checks for dataset, code/repo, random seed, hyperparameter mentions
- Only fires for `empirical_paper` / `software_workflow_paper`

**Phase 50** (`3e13012`) — Self-citation ratio validator
- `validate_self_citation_ratio(parsed)` → `high-self-citation-ratio` (minor)
- Most-common author last name fraction across bibliography entries
- Fires when >40% of entries share a last name; requires ≥8 entries with authors

**Phase 51** (`3e13012`) — Conclusion scope validator
- `validate_conclusion_scope(parsed)` → `conclusion-scope-creep` (moderate)
- Finds quantitative metrics (METRIC_RE) in conclusion not in abstract/results
- Fires when ≥2 novel metrics detected; uses `_CONCLUSION_SECTIONS` frozenset

**Phase 53** (`a0841e5`) — Equation density validator
- `validate_equation_density(parsed, classification)` → `low-equation-density` (minor)
- Fires for `math_stats_theory` pathway when <0.5 equations/section; requires ≥4 sections

**Phase 54** (`a0841e5`) — Abstract structure validator
- `validate_abstract_structure(parsed)` → `missing-abstract-component` (minor)
- Checks for `_ABSTRACT_METHOD_RE` and `_ABSTRACT_RESULT_RE` signals; requires ≥50 words

**Phase 55** (`a0841e5`) — URL format validator
- `validate_url_format(parsed)` → `malformed-url` and `url-without-access-date` (both minor)
- Scans full_text for `www.` and `ftp://` URLs; also checks bibliography `url` fields
- Capped at 5 findings to avoid flooding

**Phase 56** (`a0841e5`) — Figure/table balance validator
- `validate_figure_table_balance(parsed, classification)` → `insufficient-figures` and `table-heavy` (both minor)
- Empirical papers with ≥4 sections and <2 figure mentions get `insufficient-figures`
- `table-heavy` fires when table mentions > 2× figure mentions

**Phase 57** (`a0841e5`) — Section ordering (IMRaD) validator
- `validate_section_ordering(parsed, classification)` → `section-order-violation` (minor)
- `_imrad_key()` maps Introduction/Method/Result/Discussion to slots 0-3
- Flags adjacent inversions; only fires for empirical/applied papers

**Phase 73** (`c370572`) — Hedging language density
- `validate_hedging_language(parsed)` → `hedging-language-dense` (minor)
- `_HEDGE_DENSITY_RE` counts hedging phrases in abstract+intro+conclusion; fires when >4
- Required ≥50 words combined; note: `_HEDGE_RE` already exists for per-section check

**Phase 74** (`c370572`) — Duplicate section content
- `validate_duplicate_section_content(parsed)` → `duplicate-section-content` (minor)
- Jaccard sentence-level overlap (frozenset of lowercased tokens)
- Non-adjacency based on original section indices (not filtered list position)
- Threshold: 0.40 max pairwise Jaccard; cap 3 findings

**Phase 75** (`c370572`) — Abstract length (extended existing)
- Extended `validate_abstract_length(parsed)` to also flag `abstract-too-short` (minor) when <100 words
- Existing function only checked overlong; now bidirectional

**Phase 76** (`c370572`) — Methods section depth
- `validate_methods_depth(parsed, classification)` → `thin-methods` (moderate)
- `_METHODS_SECTIONS` frozenset (different from existing `_METHODS_SECTION_RE` regex)
- Fires when Methods section body <150 words; empirical/applied/software only

**Phase 77** (`c370572`) — Passive voice ratio (retired)
- Retired: covered by existing `validate_passive_voice_density` (threshold 45%, min 4 sentences)
- New tests redirect to `validate_passive_voice_density` with `high-passive-voice-density` code

**Phase 78** (`c370572`) — List overuse
- `validate_list_overuse(parsed)` → `list-heavy-section` (minor)
- Fires when >50% of lines in Introduction/Discussion/Conclusion are list items and ≥6 items

**Phase 79** (`c370572`) — Section balance
- `validate_section_balance(parsed, classification)` → `section-length-imbalance` (minor)
- Fires when any section >60% of total body word count; requires ≥3 non-skipped sections

**Phase 80** (`fff0823`) — MEMORY.md sync (phases 73–79)

**Phase 81** (`6ba7c84`) — Related work recency
- `validate_related_work_recency(parsed, classification)` → `related-work-stale` (minor)
- Fires when >50% of citations in Related Work/Literature Review are >8 years old
- `_YEAR_IN_BIB_RE = re.compile(r"\b(?:19|20)\d{2}\b")` (non-capturing group — critical!)
- Empirical/applied/software paper types only

**Phase 82** (`6ba7c84`) — Introduction length
- `validate_introduction_length(parsed)` → `introduction-too-long` (minor)
- Fires when Introduction >25% of total body word count
- Guards: ≥4 non-skipped sections AND ≥300 total body words (avoids stub manuscript false positives)
- `_INTRO_MIN_TOTAL_WORDS = 300` was added after `revision_new.md` fixture triggered false positive

**Phase 83** (`6ba7c84`) — Unquantified comparisons
- `validate_unquantified_comparisons(parsed)` → `unquantified-comparison` (minor)
- Flags "much better", "significantly faster", "far superior" etc. without numeric support in body

**Phase 84** (`6ba7c84`) — Footnote overuse
- `validate_footnote_overuse(parsed)` → `footnote-heavy` (minor)
- Fires when >5 footnotes in a single section

**Phase 85** (`6ba7c84`) — Abbreviation list
- `validate_abbreviation_list(parsed)` → `unused-abbreviation` (minor)
- Flags abbreviations defined in Abbreviations/Glossary section but not used in body

**Phase 86** (`6ba7c84`) — Abstract tense
- `validate_abstract_tense(parsed)` → `abstract-tense-mixed` (minor)
- Flags abstracts mixing past and present tense verb forms

Current test count: **228 passing** (after phase 86)

Phase 86 is closed. Next candidate phases:
1. **Phase 87: Claim strength escalation** — "proves", "demonstrates conclusively" without evidence → major
2. **Phase 88: Sample size reporting** — empirical papers without explicit N mentioned → moderate
3. **Phase 89: Limitation section presence** — no Limitations section in empirical papers → moderate
4. **Phase 90: Author contribution statement** — check for CRediT/author contribution disclosure
5. **Phase 91: Preregistration mention** — flag registered reports or RCTs without preregistration note
6. **Phase 92: Response to reviewers completeness** — revision manuscripts without systematic reviewer response


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
