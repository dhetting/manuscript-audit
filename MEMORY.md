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

**Phase 87** (`a02aea2`) — MEMORY.md sync (phases 80–86)

**Phases 87–92** (`1402062`) — Six validators
- Phase 87: `validate_claim_strength_escalation` → `overstrong-claim` (major) — flags "proves", "definitively shows", etc.
- Phase 88: `validate_sample_size_reporting` → `missing-sample-size` (moderate) — empirical papers only
- Phase 89: `validate_limitations_section_presence` → `missing-limitations-section` (moderate) — empirical papers only
- Phase 90: `validate_author_contribution_statement` → `missing-author-contributions` (minor)
- Phase 91: `validate_preregistration_mention` → `missing-preregistration` (moderate) — clinical/RCT papers only
- Phase 92: `validate_reviewer_response_completeness` → `missing-reviewer-response` (minor) — revision manuscripts only
- **Critical**: All paper-type frozensets use `"empirical_paper"`, `"applied_stats_paper"` — NOT `"empirical_research_paper"`

**Phases 93–98** (`25c120a`) — Six validators
- Phase 93: `validate_novelty_overclaim` → `novelty-overclaim` (major)
- Phase 94: `validate_figure_table_minimum` → `no-figures-or-tables` (moderate) — empirical only
- Phase 95: `validate_multiple_comparisons_correction` → `missing-multiple-comparisons-correction` (moderate)
- Phase 96: `validate_supplementary_material_indication` → `unindicated-supplementary-material` (minor)
- Phase 97: `validate_conclusion_scope_creep` → `conclusion-scope-creep` (minor) — requires ≥30 words
- Phase 98: `validate_discussion_results_alignment` → `discussion-lacks-results-reference` (moderate) — requires ≥50 words

**Phases 99–102** (`ebbfb86`) — Four validators
- Phase 99: `validate_open_data_statement` → `missing-open-data-statement` (minor) — empirical only
- Phase 100: `validate_redundant_phrases` → `redundant-phrases` (minor) — fires at ≥3 redundant phrases
- Phase 101: `validate_abstract_quantitative_results` → `abstract-no-quantitative-result` (moderate) — requires ≥50 word abstract, empirical only
- Phase 102: `validate_confidence_interval_reporting` → `missing-confidence-intervals` (moderate) — effect sizes without CIs, empirical only

**Phases 103–107** (`4b92ec9`) — Five validators
- Phase 103: `validate_bayesian_prior_justification` → `missing-prior-justification` (moderate) — Bayesian methods without prior specification
- Phase 104: `validate_software_version_pinning` → `missing-software-versions` (minor) — software named without version numbers
- Phase 105: `validate_measurement_scale_reporting` → `missing-scale-reliability` (moderate) — Likert/survey without Cronbach's alpha
- Phase 106: `validate_sem_fit_indices` → `missing-sem-fit-indices` (moderate) — SEM/CFA without CFI/RMSEA/SRMR
- Phase 107: `validate_regression_variance_explanation` → `missing-variance-explained` (moderate) — regression without R-squared

**Phases 108–111** (`9dfd652`) — Four validators
- Phase 108: `validate_normality_assumption` → `missing-normality-check` (moderate) — t-test/ANOVA without normality check
- Phase 109: `validate_attrition_reporting` → `missing-attrition-report` (moderate) — longitudinal without dropout reporting
- Phase 110: `validate_generalizability_overclaim` → `generalizability-overclaim` (major) — "universally applicable" without hedges
- Phase 111: `validate_interrater_reliability` → `missing-interrater-reliability` (moderate) — human coding without IRR stats

**Phases 112–115** (`1526431`) — Four validators
- Phase 112: `validate_spurious_precision` → `spurious-precision` (minor) — values with ≥5 decimal places in Results
- Phase 113: `validate_vague_temporal_claims` → `vague-temporal-claims` (minor) — ≥3 'recently'/'in recent years' without date anchors
  - **Bug fix**: non-capturing inner groups in regex to avoid `findall` returning tuples
- Phase 114: `validate_exclusion_criteria` → `missing-exclusion-criteria` (moderate) — inclusion but no exclusion criteria
- Phase 115: `validate_title_length` → `title-too-long` / `title-too-short` (minor) — >20 or <5 words

**Phases 116–120** (`41d30ed`) — Five validators
- Phase 116: `validate_statistical_power` → `missing-power-analysis` (moderate) — empirical without power analysis in Methods
- Phase 117: `validate_keywords_present` → `missing-keywords` (minor) — no keywords section or inline keyword list
- Phase 118: `validate_overlong_sentences` → `overlong-sentence` (minor) — Results/Discussion sentences >60 words
- Phase 119: `validate_heading_capitalization_consistency` → `inconsistent-heading-capitalization` (minor) — mixed Title/Sentence case
- Phase 120: `validate_research_question_addressed` → `unanswered-research-question` (moderate) — RQs in intro but no results language
- **Golden**: minor 9→10 (missing-keywords fires on latex_equivalence.tex)

**Phases 121–125** (`ff9964c`) — Five validators
- Phase 121: COI validator already existed (phase 63 — `validate_conflict_of_interest`); added regression tests
- Phase 122: `validate_citations_in_abstract` → `citations-in-abstract` (minor) — citation markers in abstract
- Phase 123: `validate_funding_statement` → `missing-funding-statement` (minor) — no acknowledgment/funding section
- Phase 124: `validate_discussion_section_presence` → `missing-discussion-section` (moderate) — empirical Results but no Discussion
- Phase 125: `validate_pvalue_notation_consistency` → `inconsistent-pvalue-notation` (minor) — mixed p<, P<, p-value< styles
- **Golden**: minor 10→11 (missing-funding-statement fires on latex_equivalence.tex)

**Phases 126–130** (`39b8a52`) — Five validators
- Phase 126: `validate_methods_section_presence` → `missing-methods-section` (major) — empirical paper without Methods section
- Phase 127: `validate_conclusion_section_presence` → `missing-conclusion-section` (minor) — no Conclusion/Summary (≥3 sections)
- Phase 128: `validate_participant_demographics` → `missing-participant-demographics` (moderate) — participants without demographics
- Phase 129: `validate_conflicting_acronym_definitions` → `inconsistent-acronym-definition` (minor) — same acronym, different expansions
  - Uses `_CONFLICT_ACRONYM_RE` (distinct from existing `_ACRONYM_DEF_RE`)
- Phase 130: `validate_percentage_notation_consistency` → `inconsistent-percentage-notation` (minor) — mixed %, percent, per cent
- **Golden**: minor 11→12 (missing-conclusion-section fires on latex_equivalence.tex)

**Phases 131–135** (`69419ac`) — Five validators
- Phase 131: `validate_figure_label_consistency` → `inconsistent-figure-labels` (minor) — mixing Fig./Figure/fig.
- Phase 132: `validate_draft_title_markers` → `draft-title-marker` (major) — title has TBD/DRAFT/[Title]
  - Bug fix: bracket patterns need `\[` not `\b\[` since `[` is not a word boundary character
- Phase 133: `validate_study_period_reporting` → `missing-study-period` (moderate) — empirical/clinical without study period
- Phase 134: `validate_scale_anchor_reporting` → `missing-scale-anchors` (minor) — Likert scale without anchor labels
- Phase 135: `validate_model_specification` → `missing-model-specification` (moderate) — regression/SEM without predictor spec

Current test count: **362 passing** (after phase 135)

**Phases 136–140** (`4e01795`) — Five validators (376 tests)
- Phase 136: `validate_effect_direction_reporting` → `missing-effect-direction` (moderate) — Results with ≥2 significance mentions but no direction
- Phase 137: `validate_citation_format_consistency` → `mixed-citation-format` (minor) — mixed numeric/author-year citation styles
  - Constants renamed to `_FORMAT_NUMERIC_CITE_RE`/`_FORMAT_AUTHOR_YEAR_CITE_RE` to avoid shadowing existing `_AUTHOR_YEAR_CITE_RE`
- Phase 138: `validate_imputation_sensitivity` → `missing-imputation-sensitivity` (moderate) — multiple imputation without sensitivity analysis
- Phase 139: `validate_computational_environment` → `missing-computational-environment` (moderate) — simulation/ML without language/version details
- Phase 140: `validate_table_captions` → `missing-table-captions` (minor) — ≥2 table refs but no captions
  - Caption regex uses MULTILINE + line-start anchor to avoid prose false positives
- **Golden**: moderate 11→12 (missing-computational-environment fires on latex_equivalence.tex)

**Phases 141–145** (`fef7f76`) — Five validators (392 tests)
- Phase 141: `validate_raw_data_description` → `missing-raw-data-description` (moderate)
- Phase 142: `validate_multiple_outcomes_correction` → `missing-multiple-outcomes-correction` (moderate)
- Phase 143: `validate_replication_dataset` → `missing-replication-dataset` (moderate)
- Phase 144: `validate_appendix_reference_consistency` → `missing-appendix-section` (minor)
- Phase 145: `validate_open_science_statement` → `missing-open-science-statement` (minor)
- **Bug patterns discovered**: `Section` uses `title` not `heading`; `ParsedManuscript` requires `manuscript_id`, `source_path`, `source_format`; `ManuscriptClassification` requires `paper_type` and `recommended_stack`; field is `paper_type` not `primary_type`
- Module-level imports added to test file: `Section`, `ParsedManuscript`, `ManuscriptClassification`
- **Golden**: minor 12→13, moderate 12→13 (new validators fire on latex fixture)

**Phases 146–150** (`b606563`) — Five validators (407 tests)
- Phase 146: `validate_cohort_attrition` → `missing-attrition-reporting` (moderate) — longitudinal without dropout rates
- Phase 147: `validate_blinding_procedure` → `missing-blinding-procedure` (moderate) — RCT/intervention without blinding description
- Phase 148: `validate_floor_ceiling_effects` → `missing-floor-ceiling-discussion` (minor) — psychometric scale without floor/ceiling effects
  - Floor/ceiling regex uses `effects?` (plural form needed)
- Phase 149: `validate_negative_result_framing` → `negative-result-underreported` (minor) — non-sig Results without null-result Discussion
- Phase 150: `validate_abstract_results_consistency` → `abstract-results-mismatch` (moderate) — abstract overclaims vs sparse Results

**Phases 151–155** (`8b42a2d`) — Five validators (422 tests)
- Phase 151: `validate_measurement_invariance` → `missing-measurement-invariance` (moderate) — group comparisons on scales without invariance testing
- Phase 152: `validate_effect_size_confidence_intervals` → `missing-effect-size-ci` (moderate) — effect sizes without CIs
- Phase 153: `validate_preregistration_statement` → `missing-preregistration` (minor) — confirmatory/RCT without preregistration
- Phase 154: `validate_cross_validation_reporting` → `missing-cross-validation` (moderate) — ML/prediction without CV
- Phase 155: `validate_sensitivity_analysis_reporting` → `missing-sensitivity-analysis` (moderate) — primary analysis without robustness check

**Phases 156–160** (`e99ac19`) — Five validators (437 tests)
- Phase 156: `validate_regression_diagnostics` → `missing-regression-diagnostics` (moderate) — regression without VIF/residual checks
- Phase 157: `validate_sample_representativeness` → `non-representative-sample` (moderate) — single-site + generalizability claim without caveat
  - Renamed `_GENERALIZE_CLAIM_RE` → `_SINGLE_SITE_CLAIM_RE` (shadowed existing constant)
- Phase 158: `validate_variable_operationalization` → `missing-variable-operationalization` (minor) — ≥3 variable mentions without operationalization
- Phase 159: `validate_interrater_reliability` already existed — added regression tests only
- Phase 160: `validate_control_variable_justification` → `missing-control-justification` (minor) — ≥2 control mentions without justification

**Phases 161–165** (`c514405`) — Five validators (451 tests)
- Phase 161: `validate_prospective_vs_retrospective` → `retrospective-design-claim` (minor)
- Phase 162: `validate_clinical_trial_consort` → `missing-consort-elements` (moderate)
- Phase 163: `validate_ecological_validity` → `missing-ecological-validity` (minor)
- Phase 164: `validate_media_source_citations` → `non-peer-reviewed-citation` (minor)
- Phase 165: `validate_competing_model_comparison` → `missing-model-comparison` (moderate)

**Phases 166–170** (`c3c3f9a`) — Five validators (466 tests)
- Phase 166: `validate_causal_language` → `unsupported-causal-claim` (moderate)
- Phase 167: `validate_missing_standard_errors` → `missing-standard-errors` (minor)
  - Bug fix: trailing `\b` in regex removed; replaced with explicit `\bSE\s*=` patterns
- Phase 168: `validate_subjective_claim_hedging` → `unhedged-subjective-claim` (minor)
- Phase 169: `validate_population_definition` → `missing-population-definition` (moderate)
- Phase 170: `validate_pilot_study_claims` → `overclaimed-pilot-study` (minor)
- **Golden**: moderate 13→14 (new validator fires on latex_equivalence.tex)

**Phases 171–175** (`72459e3`) — Five validators (486 tests)
- Phase 171: `validate_exclusion_criteria_reporting` → `missing-exclusion-criteria-rationale` (minor)
- Phase 172: `validate_normal_distribution_assumption` → `untested-normality-assumption` (minor)
- Phase 173: `validate_figure_axes_labeling` → `unlabeled-figure-axes` (minor)
  - Counts **distinct** figure numbers (not raw occurrences) to avoid false positives from caption+body refs
  - `_FIGURE_MENTION_RE` extracts capturing group; `_FIGURE_MIN_DISTINCT = 2`
- Phase 174: `validate_duplicate_reporting` → `duplicate-reporting` (major)
- Phase 175: `validate_response_rate_reporting` → `missing-response-rate` (moderate)

**Phases 176–180** (`1ee8854`) — Five validators (506 tests)
- Phase 176: `validate_longitudinal_attrition_bias` → `missing-attrition-bias-analysis` (moderate)
  - Uses `_LONGITUDINAL_DESIGN_RE` (not `_LONGITUDINAL_RE`) to avoid constant shadowing
  - Removed `time\s+point` from pattern (too broad; matches cross-sectional text)
- Phase 177: `validate_continuous_variable_dichotomization` → `unjustified-dichotomization` (moderate)
- Phase 178: `validate_outcome_measure_validation` → `missing-measure-validity` (moderate)
- Phase 179: `validate_outlier_handling_disclosure` → `missing-outlier-handling` (minor)
  - Uses `outliers?` (plural form) not `outlier` to match both forms
- Phase 180: `validate_main_effect_confidence_interval` → `missing-main-effect-ci` (moderate)

**Phases 181–185** (`e8a2486`) — Five validators (526 tests)
- Phase 181: `validate_covariate_justification` → `missing-covariate-justification` (minor)
- Phase 182: `validate_gender_sex_conflation` → `gender-sex-conflation` (minor)
- Phase 183: `validate_multicollinearity_reporting` → `missing-multicollinearity-check` (minor)
- Phase 184: `validate_control_group_description` → `missing-control-group-type` (moderate)
- Phase 185: `validate_heteroscedasticity_testing` → `missing-heteroscedasticity-check` (minor)

**Phases 186–190** (`7b05baf`) — Five validators (546 tests)
- Phase 186: `validate_interaction_effect_interpretation` → `missing-interaction-probing` (moderate)
- Phase 187: `validate_post_hoc_framing` → `post-hoc-not-labelled` (moderate)
- Phase 188: `validate_multiple_comparison_correction` → `missing-multiple-comparison-correction` (moderate)
- Phase 189: `validate_publication_bias_statement` → `missing-publication-bias-statement` (major)
- Phase 190: `validate_degrees_of_freedom_reporting` → `missing-degrees-of-freedom` (minor)
  - Uses `_INFERENTIAL_STAT_RE` (not `_STAT_TEST_RE`) to avoid constant shadowing
  - `_DF_PRESENT_RE` and `_INFERENTIAL_STAT_RE` drop trailing `\b` (parens are non-word chars)

**Phases 191–195** (`856dde7`) — Five validators (566 tests)
- Phase 191: `validate_power_analysis_reporting` → `missing-power-analysis` (moderate)
- Phase 192: `validate_demographic_description` → `missing-demographic-description` (minor)
- Phase 193: `validate_randomization_procedure` → `missing-randomization-procedure` (moderate)
- Phase 194: `validate_generalizability_caveat` → `overclaimed-generalizability` (moderate)
- Phase 195: `validate_software_version_reporting` → `missing-software-version` (minor)

**Phases 196–200** (`bdd7770`) — Five validators (586 tests)
- Phase 196: `validate_ethics_approval_statement` → `missing-ethics-approval` (major)
- Phase 197: `validate_prisma_reporting` → `missing-prisma-elements` (moderate)
- Phase 198: `validate_mediation_analysis_transparency` → `missing-mediation-bootstrap` (moderate)
- Phase 199: `validate_latent_variable_model_fit` → `missing-model-fit-indices` (moderate)
- Phase 200: `validate_pilot_study_disclosure` → `undisclosed-pilot-study` (minor)

**Phases 201–205** (`0213199`) — Five validators (606 tests)
- Phase 201: `validate_autocorrelation_check` → `missing-autocorrelation-check` (minor)
- Phase 202: `validate_mixed_methods_integration` → `missing-mixed-methods-integration` (moderate)
- Phase 203: `validate_qualitative_rigor_reporting` → `missing-qualitative-rigor` (moderate)
- Phase 204: `validate_subgroup_analysis_labelling` → `unlabelled-subgroup-analysis` (minor)
- Phase 205: `validate_null_result_power_caveat` → `null-result-without-power-caveat` (minor)

**Phases 206–210** (`528ec39`) — Five validators (626 tests), golden minor 13→14
- Phase 206: `validate_mean_sd_reporting` → `missing-sd-for-mean` (minor)
- Phase 207: `validate_intervention_description` → `insufficient-intervention-description` (moderate)
- Phase 208: `validate_baseline_equivalence` → `missing-baseline-equivalence` (moderate)
- Phase 209: `validate_likert_distribution_check` → `missing-likert-distribution-check` (minor)
- Phase 210: `validate_reproducibility_statement` → `missing-reproducibility-link` (minor)

**Phases 211–215** (`30cbdf7`) — Five validators (646 tests)
- Phase 211: `validate_missing_data_handling` → `missing-data-handling-not-described` (moderate)
- Phase 212: `validate_coding_scheme_description` → `missing-coding-scheme-detail` (moderate)
- Phase 213: `validate_logistic_regression_assumptions` → `missing-logistic-model-fit` (minor)
- Phase 214: `validate_researcher_positionality` → `missing-researcher-positionality` (minor)
- Phase 215: `validate_data_collection_recency` → `potentially-outdated-data` (minor)

**Phases 216–220** (`832b4a9`) — Five validators (666 tests)
- Phase 216: `validate_theoretical_framework_citation` → `missing-theory-citation` (minor)
- Phase 217: `validate_survey_instrument_source` → `missing-instrument-source` (moderate)
- Phase 218: `validate_sampling_frame_description` → `missing-sampling-frame` (minor)
- Phase 219: `validate_one_tailed_test_justification` → `unjustified-one-tailed-test` (moderate)
- Phase 220: `validate_gratuitous_significance_language` → `implausible-significance-language` (major)

**Phases 221–225** (`4bde133`) — Five validators (686 tests)
- Phase 221: `validate_unit_of_analysis_clarity` → `unclear-unit-of-analysis` (moderate)
- Phase 222: `validate_apriori_preregistration_statement` → `missing-preregistration-statement` (moderate)
  - renamed to `validate_apriori_preregistration_statement` (existing `validate_preregistration_statement` at phase ~154)
- Phase 223: `validate_selective_literature_citation` → `selective-literature-citation` (minor)
- Phase 224: `validate_participant_compensation_disclosure` → `missing-compensation-amount` (minor)
- Phase 225: `validate_observational_causal_language` → `overclaimed-causality-observational` (major)

**Phases 226–230** (`d06fbf5`) — Five validators (706 tests)
- Phase 226: `validate_acknowledgement_section` → `missing-acknowledgement-section` (minor)
- Phase 227: `validate_conflict_of_interest_statement` → `missing-conflict-of-interest-statement` (major)
- Phase 228: `validate_age_reporting_precision` → `imprecise-age-reporting` (minor)
- Phase 229: `validate_statistical_software_version` → `missing-statistical-software-version` (minor)
- Phase 230: `validate_warranted_sensitivity_analysis` → `missing-warranted-sensitivity-analysis` (moderate)
  - renamed to `validate_warranted_sensitivity_analysis` (existing `validate_sensitivity_analysis_reporting` at phase ~156)

**Phases 231–235** (`e851978`) — Five validators (726 tests)
- Phase 231: `validate_ai_tool_disclosure` → `missing-ai-tool-disclosure` (moderate)
- Phase 232: `validate_between_group_effect_size` → `missing-between-group-effect-size` (moderate)
- Phase 233: `validate_convenience_sample_generalization` → `overclaimed-generalizability-convenience` (moderate)
- Phase 234: `validate_icc_reliability_reporting` → `missing-icc-reliability` (moderate)
- Phase 235: `validate_anova_post_hoc_reporting` → `missing-anova-post-hoc` (moderate)

**Phases 236–240** (`bcd2078`) — Five validators (746 tests)
- Phase 236: `validate_adverse_events_reporting` → `missing-adverse-events-report` (major)
- Phase 237: `validate_construct_operationalization` → `ambiguous-construct-operationalization` (minor)
- Phase 238: `validate_regression_coefficient_ci` → `missing-regression-coefficient-ci` (minor)
- Phase 239: `validate_longitudinal_followup_duration` → `missing-followup-duration` (moderate)
- Phase 240: `validate_bayesian_reporting` → `missing-bayesian-reporting` (moderate)

**Phases 241–245** (`5e7fe79`) — Five validators (766 tests)
- Phase 241: `validate_author_conflict_disclosure` → `missing-conflict-of-interest-disclosure` (moderate)
- Phase 242: `validate_citation_context_adequacy` → `insufficient-citation-context` (minor)
- Phase 243: `validate_outlier_treatment_disclosure` → `missing-outlier-treatment` (moderate)
- Phase 244: `validate_cross_sectional_causal_language` → `causal-language-cross-sectional` (moderate)
- Phase 245: `validate_floor_ceiling_effect_check` → already existed; 4 supplemental tests added

**Phases 246–250** (`6b20997`) — Five validators (786 tests)
- Phase 246: `validate_secondary_data_disclosure` → `missing-secondary-data-disclosure` (minor)
- Phase 247: `validate_primary_outcome_change_disclosure` → `missing-primary-outcome-change-disclosure` (moderate)
- Phase 248: `validate_bootstrap_ci_method_disclosure` → `missing-bootstrap-method-disclosure` (minor)
- Phase 249: `validate_null_result_discussion` → `missing-null-result-discussion` (minor)
- Phase 250: `validate_racial_ethnic_composition_description` → `missing-racial-ethnic-composition` (minor)

**Phases 251–255** (`1664cf1`) — Five validators (806 tests)
- Phase 251: `validate_single_item_measure_reliability` → `missing-single-item-reliability-caveat` (minor)
- Phase 252: `validate_mediator_temporality` → `missing-mediator-temporality` (moderate)
- Phase 253: `validate_effect_size_interpretation` → `missing-effect-size-interpretation` (minor)
- Phase 254: `validate_comparison_group_equivalence` → `missing-baseline-equivalence-check` (moderate)
- Phase 255: `validate_implicit_theory_test` → `implicit-theory-test-correlational` (minor)

**Phases 256–260** (`eedc9d5`) — Five validators (826 tests)
- Phase 256: SKIPPED — `validate_multiple_comparison_correction` already exists; 4 supplemental tests added
- Phase 257: `validate_non_normal_distribution_test` → `missing-normality-check` (minor)
- Phase 258: `validate_regression_sample_size_adequacy` → `missing-regression-sample-adequacy` (minor)
- Phase 259: `validate_scale_directionality_disclosure` → `missing-scale-directionality` (minor)
- Phase 260: `validate_attrition_rate_reporting` → `missing-attrition-rate` (minor)

**Phases 261–265** (`6b42664`) — Five validators (846 tests)
- Phase 261: `validate_dichotomization_of_continuous_variable` → `unjustified-dichotomization` (moderate)
- Phase 262: `validate_ecological_fallacy_warning` → `missing-ecological-fallacy-warning` (moderate)
- Phase 263: `validate_standardised_mean_difference_units` → `missing-smd-original-unit-context` (minor)
- Phase 264: `validate_retrospective_data_collection_disclosure` → `missing-retrospective-design-disclosure` (minor)
- Phase 265: `validate_treatment_fidelity_reporting` → `missing-treatment-fidelity-report` (moderate)

**Phases 266–270** (`f2b4231`) — Five validators (866 tests)
- Phase 266: `validate_factorial_design_interaction_test` → `missing-factorial-interaction-test` (moderate)
- Phase 267: `validate_regression_multicollinearity_check` → `missing-multicollinearity-check` (minor)
- Phase 268: `validate_intention_to_treat_analysis` → `missing-itt-analysis` (major)
- Phase 269: `validate_confidence_interval_direction_interpretation` → `missing-ci-direction-interpretation` (minor)
- Phase 270: `validate_longitudinal_missing_data_method` → `missing-longitudinal-missing-data-method` (moderate)

**Phases 271–275** (`1aed853`) — Five validators (886 tests)
- Phase 271: `validate_cluster_sampling_correction` → `missing-cluster-sampling-correction` (moderate)
- Phase 272: `validate_non_experimental_confound_discussion` → `missing-confound-discussion` (minor)
- Phase 273: `validate_complete_case_analysis_bias` → `unjustified-complete-case-analysis` (moderate)
- Phase 274: `validate_analytic_strategy_prespecification` → `unlabelled-exploratory-analysis` (minor)
- Phase 275: `validate_self_report_bias_acknowledgement` → `missing-self-report-bias-acknowledgement` (minor)

**Phases 276–280** (`907b239`) — Five validators (906 tests)
- Phase 276: `validate_p_value_reporting_precision` → `imprecise-p-value-reporting` (minor)
- Phase 277: `validate_moderator_analysis_interpretation` → `missing-moderator-follow-up` (minor)
- Phase 278: `validate_measurement_occasion_labelling` → `unlabelled-measurement-occasions` (minor)
- Phase 279: `validate_statistical_conclusion_validity` → `missing-null-result-power-discussion` (moderate)
- Phase 280: reused existing `validate_author_contribution_statement` → `missing-author-contributions` (minor); 4 supplemental tests

Current test count: **906 passing** (after phase 280)
HEAD: `907b239`

**Phases 281–285** (`9815622`, 926 tests)
- Phase 281: `validate_scale_reliability_reporting` → `missing-scale-reliability` (minor)
- Phase 282: `validate_pilot_study_scope_limitation` → `missing-pilot-scope-limitation` (minor)
- Phase 283: `validate_literature_search_recency` → `missing-literature-search-date` (minor)
- Phase 284: `validate_publication_bias_acknowledgement` → `missing-publication-bias-acknowledgement` (minor)
- Phase 285: `validate_replication_citation` → `missing-replication-citation` (minor)

**Phases 286–290** (`4fadac9`, 946 tests)
- Phase 286: `validate_negative_binomial_overdispersion` → `missing-overdispersion-test` (minor)
- Phase 287: `validate_zero_inflated_data_handling` → `missing-zero-inflation-handling` (minor)
- Phase 288: `validate_variance_homogeneity_check` → `missing-variance-homogeneity-check` (minor)
- Phase 289: `validate_path_model_fit_indices` → `missing-path-model-fit-indices` (minor)
- Phase 290: `validate_post_hoc_power_caution` → `missing-post-hoc-power-caution` (minor)

**Phases 291–295** (`9a56533`, 966 tests)
- Phase 291: `validate_ancova_covariate_balance` → `missing-ancova-covariate-balance` (minor)
- Phase 292: `validate_partial_eta_squared_reporting` → `missing-partial-eta-squared` (minor)
- Phase 293: `validate_cohens_d_reporting` → `missing-cohens-d` (minor)
- Phase 294: `validate_sequential_testing_correction` → `missing-sequential-testing-correction` (moderate)
- Phase 295: `validate_adaptive_design_disclosure` → `missing-adaptive-design-disclosure` (moderate)

**Phases 296–300** (`2352af9`, 986 tests)
- Phase 296: `validate_kaplan_meier_censoring_note` → `missing-km-censoring-note` (minor)
- Phase 297: `validate_cox_proportional_hazards_assumption` → `missing-cox-ph-assumption-check` (moderate)
- Phase 298: `validate_competing_risks_disclosure` → `missing-competing-risks-disclosure` (moderate)
- Phase 299: `validate_propensity_score_balance` → `missing-propensity-balance-check` (moderate)
- Phase 300: `validate_instrumental_variable_disclosure` → `missing-iv-validity-argument` (moderate)

**Phases 301–305** (`509cbde`, 1006 tests)
- Phase 301: `validate_multilevel_random_effects_justification` → `missing-random-effects-justification` (minor)
- Phase 302: `validate_cross_level_interaction_interpretation` → `missing-cross-level-interaction-interpretation` (minor)
- Phase 303: `validate_repeated_measures_sphericity` → `missing-sphericity-correction` (moderate)
- Phase 304: `validate_survey_sampling_weight` → `missing-survey-weight-disclosure` (minor)
- Phase 305: `validate_finite_population_correction` → `missing-finite-population-correction` (minor)

**Phases 306–310** (`df49848`, 1026 tests)
- Phase 306: `validate_mcmc_convergence_reporting` → `missing-mcmc-convergence-report` (moderate)
- Phase 307: `validate_bayes_factor_interpretation` → `missing-bayes-factor-interpretation` (minor)
- Phase 308: `validate_waic_looic_reporting` → `missing-loo-model-comparison` (minor)
- Phase 309: `validate_informative_prior_justification` → `missing-informative-prior-justification` (minor)
- Phase 310: `validate_posterior_predictive_check` → `missing-posterior-predictive-check` (minor)
- Bug fixed: `_PPC_PERFORMED_RE` needed `checks?` (plural match)

**Phases 311–315** (`6af7b12`, 1046 tests)
- Phase 311: `validate_train_test_split_disclosure` → `missing-train-test-split` (minor)
- Phase 312: `validate_hyperparameter_tuning_disclosure` → `missing-hyperparameter-tuning-disclosure` (minor)
- Phase 313: `validate_feature_importance_method` → `missing-feature-importance-method` (minor)
- Phase 314: `validate_data_leakage_prevention` → `missing-data-leakage-check` (moderate)
- Phase 315: `validate_ml_uncertainty_quantification` → `missing-ml-uncertainty` (minor)
- Bug fixed: `_ML_UNCERTAINTY_RE` needed `intervals?` (plural); `pathway="theory"` invalid → use `"math_stats_theory"`

**Phases 316–320** (`d59cd48`, 1066 tests)
- Phase 316: `validate_class_imbalance_handling` → `missing-class-imbalance-handling` (minor)
- Phase 317: `validate_model_calibration_reporting` → `missing-model-calibration` (minor)
- Phase 318: `validate_fairness_metric_reporting` → `missing-fairness-metrics` (minor)
- Phase 319: `validate_transfer_learning_disclosure` → `missing-transfer-learning-disclosure` (minor)
- Phase 320: `validate_cross_validation_strategy` → `missing-cv-strategy` (minor)

**Phases 321–325** (`69cf17e`, 1086 tests)
- Phase 321: `validate_text_preprocessing_disclosure` → `missing-text-preprocessing-disclosure` (minor)
- Phase 322: `validate_word_embedding_details` → `missing-word-embedding-details` (minor)
- Phase 323: `validate_topic_model_parameter_disclosure` → `missing-topic-model-parameters` (minor)
- Phase 324: `validate_inter_annotator_agreement` → `missing-inter-annotator-agreement` (moderate)
- Phase 325: `validate_sentiment_lexicon_disclosure` → `missing-sentiment-lexicon` (minor)

**Phases 326–330** (`90731ae`, 1106 tests)
- Phase 326: `validate_mri_acquisition_parameters` → `missing-mri-acquisition-parameters` (minor)
- Phase 327: `validate_fmri_preprocessing_pipeline` → `missing-fmri-preprocessing-pipeline` (minor)
- Phase 328: `validate_neuroimaging_atlas_disclosure` → `missing-neuroimaging-atlas` (minor)
- Phase 329: `validate_multiple_comparisons_neuroimaging` → `missing-neuroimaging-multiple-comparisons` (moderate)
- Phase 330: `validate_roi_definition_disclosure` → `missing-roi-definition` (minor)
- Bug fix: `_ROI_DEFINED_RE` word-order: added `(?:\w+\s+)?` to handle "defined anatomically using"

**Phases 331–335** (`132c73f`, 1126 tests)
- Phase 331: `validate_rna_seq_normalization_disclosure` → `missing-rna-seq-normalization` (minor)
- Phase 332: `validate_batch_effect_correction` → `missing-batch-effect-correction` (minor)
- Phase 333: `validate_multiple_testing_genomics` → `missing-genomics-multiple-testing` (moderate)
- Phase 334: `validate_pathway_enrichment_method` → `missing-pathway-enrichment-method` (minor)
- Phase 335: `validate_genome_reference_disclosure` → `missing-genome-reference` (minor)
- Bug fix: `_BATCH_EFFECT_TRIGGER_RE` added `(?:were\s+|was\s+)?` for "samples were collected"

**Phases 336–340** (`887910f`, 1146 tests)
- Phase 336: `validate_strobe_observational_reporting` → `missing-strobe-elements` (minor)
- Phase 337: `validate_selection_bias_discussion` → `missing-selection-bias-discussion` (minor)
- Phase 338: `validate_information_bias_discussion` → `missing-information-bias-discussion` (minor)
- Phase 339: `validate_dose_response_relationship` → `missing-dose-response-analysis` (minor)
- Phase 340: `validate_follow_up_rate_reporting` → `missing-follow-up-rate` (minor)
- Bug fixes: removed outer `\b` after `%`; tightened follow-up rate regex to require numeric %; plural `assessments?`

**Phases 341–345** (`24c8cb3`, 1166 tests)
- Phase 341: `validate_cost_effectiveness_perspective` → `missing-cea-perspective` (minor)
- Phase 342: `validate_discount_rate_disclosure` → `missing-discount-rate` (minor)
- Phase 343: `validate_uncertainty_analysis_health_economic` → `missing-health-economic-uncertainty` (minor)
- Phase 344: `validate_qaly_utility_source` → `missing-qaly-utility-source` (minor)
- Phase 345: `validate_markov_model_cycle_length` → `missing-markov-cycle-length` (minor)
- Bug fixes: `(?:\w+\s+){0,3}` for "costs and QALYs were discounted"; `QALYs?\b` (not `QALY\b`)

**Phases 346–350** (`4e0da15`, 1186 tests)
- Phase 346: `validate_measurement_invariance_testing` → `missing-measurement-invariance-test` (moderate)
- Phase 347: `validate_convergent_discriminant_validity` → `missing-convergent-discriminant-validity` (minor)
- Phase 348: `validate_irt_model_fit` → `missing-irt-model-fit` (minor)
- Phase 349: `validate_test_retest_reliability` → `missing-test-retest-reliability` (minor)
- Phase 350: `validate_norm_reference_group` → `missing-norm-reference-group` (minor)
- Bug fix: `_TEST_RETEST_REPORTED_RE` rewrote to require actual numeric coefficient (not "was examined")

**Phases 351–355** (`f278680`, 1206 tests)
- Phase 351: `validate_theoretical_saturation_claim` → `missing-saturation-evidence` (minor)
- Phase 352: `validate_member_checking_disclosure` → `missing-member-checking` (minor)
- Phase 353: `validate_reflexivity_statement` → `missing-reflexivity-statement` (minor)
- Phase 354: `validate_negative_case_analysis` → `missing-negative-case-analysis` (minor)
- Phase 355: `validate_thick_description_transferability` → `missing-thick-description` (minor)

**Phases 361–365** (`224e3c6`, 1246 tests)
- Phase 361: `validate_network_centrality_reporting` → `missing-centrality-reporting` (minor)
- Phase 362: `validate_spatial_autocorrelation_testing` → `missing-spatial-autocorrelation-test` (minor)
- Phase 363: `validate_time_series_stationarity` → `missing-stationarity-test` (minor)
- Phase 364: `validate_regression_discontinuity_bandwidth` → `missing-rd-bandwidth` (minor)
- Phase 365: `validate_within_subject_order_effects` → `missing-counterbalancing` (minor)

**Phases 366–370** (`e2582d0`, 1266 tests)
- Phase 366: `validate_arch_effect_testing` → `missing-arch-test` (minor)
- Phase 367: `validate_cointegration_testing` → `missing-cointegration-test` (minor)
- Phase 368: `validate_vecm_identification` → `missing-vecm-identification` (minor)
- Phase 369: `validate_panel_fixed_effects_reporting` → `missing-panel-fe-reporting` (minor)
- Phase 370: `validate_garch_order_specification` → `missing-garch-order` (minor)
- Bug: `_ARCH_SPEC_RE` — outer `\b(?:...)\b` fails when group ends with `)`. Fixed by removing outer `\b` and adding inner `\b` per alternative.

**Phases 371–375** (`9866084`, 1286 tests)
- Phase 371: `validate_arima_order_reporting` → `missing-arima-order` (minor)
- Phase 372: `validate_var_lag_selection` → `missing-var-lag-selection` (minor)
- Phase 373: `validate_irf_significance_reporting` → `missing-irf-significance` (minor)
- Phase 374: `validate_forecast_evaluation_metric` → `missing-forecast-metric` (minor)
- Phase 375: `validate_seasonal_adjustment_disclosure` → `missing-seasonal-adjustment` (minor)
- Bug: `_IRF_TRIGGER_RE` used singular `function` — missed "functions". Fixed with `functions?`.

**Phases 376–380** (`f6ace7f`, 1306 tests)
- Phase 376: `validate_did_parallel_trends` → `missing-parallel-trends-test` (moderate)
- Phase 377: `validate_rdd_manipulation_test` → `missing-rdd-manipulation-test` (moderate)
- Phase 378: `validate_synth_control_donor_pool` → `missing-synth-control-donor-pool` (minor)
- Phase 379: `validate_iv_exclusion_restriction_argument` → `missing-iv-exclusion-argument` (minor)
- Phase 380: `validate_event_study_pre_trends` → `missing-event-study-pre-trends` (minor)

**Phases 381–385** (`5f46c1b`, 1326 tests)
- Phase 381: `validate_psm_balance_assessment` → `missing-psm-balance` (minor)
- Phase 382: `validate_ipw_weight_trimming` → `missing-ipw-weight-trimming` (minor)
- Phase 383: `validate_mediation_bootstrapping` → `missing-mediation-bootstrapping` (minor)
- Phase 384: `validate_moderation_simple_slopes` → `missing-simple-slopes` (minor)
- Phase 385: `validate_ceiling_floor_effects` → `missing-ceiling-floor-effects` (minor)
- Bug: `_CF_REPORTED_RE` didn't match "ceiling or floor effects" (conjunction). Fixed with optional `or floor`/`or ceiling` group.
- Duplicate test name: `test_moderation_with_simple_slopes_no_fire` existed at line 15739 — renamed new one `test_moderation384_with_simple_slopes_no_fire`.

**Phases 386–390** (`cf745d1`, 1346 tests)
- Phase 386: `validate_loss_curve_reporting` → `missing-loss-curve` (minor)
- Phase 387: `validate_early_stopping_criteria` → `missing-early-stopping-criteria` (minor)
- Phase 388: `validate_class_imbalance_handling` → `missing-class-imbalance-handling` (minor)
- Phase 389: `validate_cross_dataset_generalization` → `missing-cross-dataset-generalization` (minor)
- Phase 390: `validate_ablation_study_reporting` → `missing-ablation-study` (minor)
- Bug: `_ABLATION_TRIGGER_RE` used singular `component` — missed "components". Fixed with `components?`.

**Phases 391–395** (`cc2553c`, 1366 tests)
- Phase 391: `validate_attention_mechanism_analysis` → `missing-attention-analysis` (minor)
- Phase 392: `validate_pretrained_weight_disclosure` → `missing-pretrained-weight-disclosure` (minor)
- Phase 393: `validate_data_augmentation_description` → `missing-augmentation-description` (minor)
- Phase 394: `validate_model_interpretability_reporting` → `missing-model-interpretability` (minor)
- Phase 395: `validate_dataset_split_seed` → `missing-split-seed` (minor)

**Phases 401–405** (`5453287`, 1406 tests)
- Phase 401: `validate_ensemble_method_description` → `missing-ensemble-description` (minor)
- Phase 402: `validate_calibration_curve_reporting` → `missing-calibration-reporting` (minor)
- Phase 403: `validate_prediction_interval_distinction` → `missing-prediction-interval-distinction` (minor)
- Phase 404: `validate_missing_data_imputation_method` → `missing-imputation-method` (moderate)
- Phase 405: `validate_influential_observation_sensitivity` → `missing-influential-obs-sensitivity` (minor)
- NOTE: phase 405 renamed from `validate_outlier_handling_disclosure` (duplicate at line 10481) to `validate_influential_observation_sensitivity`

**Phases 406–410** (`4de3e7a`, 1426 tests)
- Phase 406: `validate_goodness_of_fit_reporting` → `missing-goodness-of-fit` (moderate)
- Phase 407: `validate_aic_bic_model_selection` → `missing-model-selection-criterion` (moderate)
- Phase 408: `validate_log_likelihood_reporting` → `missing-log-likelihood` (minor)
- Phase 409: `validate_link_function_justification` → `missing-link-function-justification` (minor)
- Phase 410: `validate_functional_form_test` → `missing-functional-form-test` (minor)

**Phases 411–415** (`20e585b`, 1446 tests)
- Phase 411: `validate_efa_factor_retention` → `missing-efa-retention-criteria` (moderate)
- Phase 412: `validate_cfa_model_fit_indices` → `missing-cfa-fit-indices` (moderate)
- Phase 413: `validate_omega_reliability` → `missing-omega-reliability` (minor)
- Phase 414: `validate_criterion_validity_evidence` → `missing-criterion-validity-evidence` (moderate)
- Phase 415: `validate_irt_dif_reporting` → `missing-irt-dif-reporting` (minor)

**Phases 416–420** (`8ccd2e4`, 1466 tests)
- Phase 416: `validate_robust_standard_errors` → `missing-robust-standard-errors` (minor)
- Phase 417: `validate_cluster_robust_inference` → `missing-cluster-robust-inference` (moderate)
- Phase 418: `validate_propensity_score_overlap` → `missing-propensity-overlap` (moderate)
- Phase 419: `validate_cure_model_fraction_reporting` → `missing-cure-fraction` (minor)
- Phase 420: `validate_recurrent_event_modeling` → `missing-recurrent-event-method` (minor)

**Phases 421–425** (`d929e82`, 1486 tests)
- Phase 421: `validate_prior_specification_justification` → `missing-prior-justification` (moderate)
- Phase 422: `validate_credible_interval_interpretation` → `missing-credible-interval-interpretation` (minor)
- Phase 423: `validate_bayesian_sequential_stopping_rule` → `missing-bayesian-stopping-rule` (moderate)
- Phase 424: `validate_variational_inference_elbo` → `missing-elbo-reporting` (minor)
- Phase 425: `validate_hierarchical_shrinkage_reporting` → `missing-hierarchical-shrinkage` (minor)

**Phases 426–430** (`99287c8`, 1506 tests)
- Phase 426: `validate_spatial_weights_matrix_specification` → `missing-spatial-weights-specification` (moderate)
- Phase 427: `validate_spatial_spillover_effects` → `missing-spatial-spillover-effects` (moderate)
- Phase 428: `validate_gwr_bandwidth_specification` → `missing-gwr-bandwidth` (minor)
- Phase 429: `validate_spatial_panel_fe_re_selection` → `missing-spatial-panel-model-selection` (minor)
- Phase 430: `validate_coordinate_reference_system_disclosure` → `missing-crs-disclosure` (minor)

**Phases 431–435** (`b604b9c`, 1526 tests)
- Phase 431: `validate_annotation_agreement_reporting` → `missing-annotation-agreement` (moderate)
- Phase 432: `validate_crowdsourcing_quality_control` → `missing-crowdsourcing-qc` (moderate)
- Phase 433: `validate_active_learning_strategy` → `missing-active-learning-strategy` (minor)
- Phase 434: `validate_sequence_labeling_evaluation` → `missing-sequence-labeling-evaluation` (minor)
- Phase 435: `validate_nlp_heldout_evaluation` → `missing-nlp-heldout-evaluation` (moderate)

**Phases 436–440** (`084f831`, 1546 tests)
- Phase 436: `validate_bleu_rouge_evaluation` → `missing-bleu-rouge-evaluation` (moderate)
- Phase 437: `validate_human_evaluation_for_text_generation` → `missing-human-eval-text-generation` (minor)
- Phase 438: `validate_asr_wer_reporting` → `missing-asr-wer` (moderate)
- Phase 439: `validate_language_model_perplexity` → `missing-perplexity-reporting` (minor)
- Phase 440: `validate_reading_comprehension_evaluation` → `missing-reading-comprehension-eval` (moderate)

**Phases 441–445** (`b3cd503`, 1566 tests)
- Phase 441: `validate_ir_ranking_metrics` → `missing-ir-ranking-metrics` (moderate)
- Phase 442: `validate_recsys_evaluation_metrics` → `missing-recsys-metrics` (moderate)
- Phase 443: `validate_object_detection_metrics` → `missing-detection-metrics` (moderate)
- Phase 444: `validate_gnn_evaluation_metrics` → `missing-gnn-evaluation-metrics` (moderate)
- Phase 445: `validate_rl_reward_reporting` → `missing-rl-reward-reporting` (moderate)

**Phases 446–450** (`d4d9bc0`, 1586 tests)
- Phase 446: `validate_multitask_per_task_performance` → `missing-per-task-performance` (minor)
- Phase 447: `validate_few_shot_setup_details` → `missing-few-shot-setup-details` (moderate)
- Phase 448: `validate_knowledge_distillation_setup` → `missing-distillation-setup` (minor)
- Phase 449: `validate_federated_learning_setup` → `missing-federated-setup` (moderate)
- Phase 450: `validate_continual_learning_forgetting_metric` → `missing-forgetting-metric` (moderate)

**Phases 451–455** (`cb639af`, 1606 tests)
- Phase 451: `validate_ssl_augmentation_details` → `missing-ssl-augmentation-details` (moderate)
- Phase 452: `validate_prompt_template_disclosure` → `missing-prompt-template-disclosure` (minor)
- Phase 453: `validate_augmentation_parameter_disclosure` → `missing-augmentation-parameters` (minor)
- Phase 454: `validate_pruning_sparsity_disclosure` → `missing-pruning-sparsity-details` (minor)
- Phase 455: `validate_nas_search_space_disclosure` → `missing-nas-search-space-details` (moderate)
- Gotcha: `_AUGMENT_REPR_TRIGGER_RE` used `technique` (singular) — missed `techniques`. Fixed with `techniques?`.
- Duplicate test names for phase 453 `test_augmentation_*` — renamed to `test_augmentation453_*`.

**Phases 456–460** (`8f9f3e5`, 1626 tests)
- Phase 456: `validate_adversarial_attack_details` → `missing-adversarial-attack-details` (moderate)
- Phase 457: `validate_uncertainty_decomposition` → `missing-uncertainty-decomposition` (minor)
- Phase 458: `validate_causal_discovery_assumptions` → `missing-causal-discovery-assumptions` (moderate)
- Phase 459: `validate_domain_adaptation_description` → `missing-domain-adaptation-description` (minor)
- Phase 460: `validate_meta_learning_task_setup` → `missing-meta-learning-task-setup` (moderate)

**Phases 461–465** (`079d4c1`, 1646 tests)
- Phase 461: `validate_forecasting_metrics` → `missing-forecasting-metrics` (moderate)
- Phase 462: `validate_anomaly_detection_threshold` → `missing-anomaly-detection-threshold` (moderate)
- Phase 463: `validate_generative_model_metrics` → `missing-generative-model-metrics` (moderate)
- Phase 464: `validate_tts_evaluation` → `missing-tts-evaluation` (moderate)
- Phase 465: `validate_video_evaluation_metrics` → `missing-video-evaluation-metrics` (moderate)
- Gotcha: `(?:MAE\s*=|RMSE\s*=|...)\b` — trailing `\b` after `=` fails. Restructured to `(?:\bMAE\b|\bRMSE\b)\s*=`.

**Phases 466–470** (`b425bbb`, 1666 tests)
- Phase 466: `validate_point_cloud_metrics` → `missing-point-cloud-metrics` (moderate)
- Phase 467: `validate_segmentation_dice_metrics` → `missing-segmentation-dice-metrics` (moderate)
- Phase 468: `validate_eeg_preprocessing_details` → `missing-eeg-preprocessing-details` (moderate)
- Phase 469: `validate_admet_reporting` → `missing-admet-reporting` (minor)
- Phase 470: `validate_variant_calling_pipeline` → `missing-variant-calling-pipeline` (moderate)

**Phases 471–475** (`74e0bf4`, 1686 tests)
- Phase 471: `validate_protein_structure_evaluation` → `missing-protein-structure-metrics` (moderate)
- Phase 472: `validate_climate_model_skill_score` → `missing-climate-model-skill-score` (moderate)
- Phase 473: `validate_panel_unit_root_testing` → `missing-panel-unit-root-testing` (moderate)
- Phase 474: `validate_social_network_centrality_reporting` → `missing-network-centrality-metrics` (minor)
- Phase 475: `validate_abm_sensitivity_analysis` → `missing-abm-sensitivity-analysis` (minor)
- NOTE: `validate_agent_based_model_validation` already existed at line 22047; phase 475 renamed to `validate_abm_sensitivity_analysis`.

**Phases 476–480** (`89d9637`, 1706 tests)
- Phase 476: `validate_sbi_coverage_testing` → `missing-sbi-coverage-testing` (moderate)
- Phase 477: `validate_bo_acquisition_function` → `missing-bo-acquisition-function` (minor)
- Phase 478: `validate_sdm_evaluation_metrics` → `missing-sdm-evaluation-metrics` (moderate)
- Phase 479: `validate_epi_parameter_estimation` → `missing-epi-parameter-estimation` (moderate)
- Phase 480: `validate_optimization_convergence_reporting` → `missing-optimization-convergence-reporting` (minor)

Current test count: **1706 passing** (after phase 480)
HEAD: `89d9637`

**Phases 481–485** (`405be04`, 1726 tests)
- Phase 481: `validate_nids_imbalance_handling` → `missing-nids-imbalance-handling` (moderate)
- Phase 482: `validate_fraud_cost_evaluation` → `missing-fraud-cost-evaluation` (moderate)
- Phase 483: `validate_credit_scorecard_calibration` → `missing-credit-scorecard-calibration` (moderate)
- Phase 484: `validate_nli_artifact_evaluation` → `missing-nli-artifact-evaluation` (moderate)
- Phase 485: `validate_image_captioning_metrics` → `missing-image-captioning-metrics` (moderate)
- Gotcha: CIDEr/SPICE metric regex `(?:CIDEr\s*=|...)\b` — trailing `\b` after `=` fails. Restructured to `(?:\bCIDEr\b|\bSPICE\b)\s*=`.

**Phases 486–490** (`0d10325`, 1746 tests)
- Phase 486: `validate_vqa_accuracy_split` → `missing-vqa-accuracy-split` (moderate)
- Phase 487: `validate_sgg_recall_metrics` → `missing-sgg-recall-metrics` (moderate)
- Phase 488: `validate_audio_event_detection_metrics` → `missing-audio-event-detection-metrics` (moderate)
- Phase 489: `validate_table_qa_execution_accuracy` → `missing-table-qa-execution-accuracy` (moderate)
- Phase 490: `validate_code_gen_pass_at_k` → `missing-code-gen-pass-at-k` (moderate)

**Phases 491–495** (`50e72d4`, 1766 tests)
- Phase 491: `validate_coreference_resolution_metrics` → `missing-coreference-metrics` (moderate)
- Phase 492: `validate_srl_evaluation_metrics` → `missing-srl-evaluation-metrics` (moderate)
- Phase 493: `validate_dependency_parsing_metrics` → `missing-dependency-parsing-metrics` (moderate)
- Phase 494: `validate_cross_lingual_per_language_results` → `missing-cross-lingual-per-language-results` (minor)
- Phase 495: `validate_multimodal_modality_ablation` → `missing-multimodal-modality-ablation` (moderate)

**Phases 496–500** (`969986d`, 1786 tests)
- Phase 496: `validate_ocr_evaluation_metrics` → `missing-ocr-evaluation-metrics` (moderate)
- Phase 497: `validate_3d_pose_estimation_metrics` → `missing-3d-pose-estimation-metrics` (moderate)
- Phase 498: `validate_panoptic_segmentation_metrics` → `missing-panoptic-segmentation-metrics` (moderate)
- Phase 499: `validate_medical_report_generation_metrics` → `missing-medical-report-generation-metrics` (moderate)
- Phase 500: `validate_kg_completion_metrics` → `missing-kg-completion-metrics` (moderate)

**Phases 501–505** (`3af0dac`, 1806 tests)
- Phase 501: `validate_optical_flow_metrics` → `missing-optical-flow-metrics` (moderate)
- Phase 502: `validate_depth_estimation_metrics` → `missing-depth-estimation-metrics` (moderate)
- Phase 503: `validate_visual_place_recognition_metrics` → `missing-visual-place-recognition-metrics` (moderate)
- Phase 504: `validate_action_recognition_metrics` → `missing-action-recognition-metrics` (moderate)
- Phase 505: `validate_trajectory_prediction_metrics` → `missing-trajectory-prediction-metrics` (moderate)

**Phases 506–510** (`b57d2c1`, 1826 tests)
- Phase 506: `validate_hand_pose_estimation_metrics` → `missing-hand-pose-estimation-metrics` (moderate)
- Phase 507: `validate_face_recognition_metrics` → `missing-face-recognition-metrics` (moderate)
- Phase 508: `validate_lane_detection_metrics` → `missing-lane-detection-metrics` (moderate)
- Phase 509: `validate_salient_object_detection_metrics` → `missing-salient-object-detection-metrics` (moderate)
- Phase 510: `validate_image_restoration_metrics` → `missing-image-restoration-metrics` (moderate)

**Phases 511–515** (`d73f0e9`, 1846 tests)
- Phase 511: `validate_reid_metrics` → `missing-reid-metrics` (moderate)
- Phase 512: `validate_inpainting_evaluation_metrics` → `missing-inpainting-evaluation-metrics` (moderate)
- Phase 513: `validate_lidar_3d_detection_metrics` → `missing-lidar-3d-detection-metrics` (moderate)
- Phase 514: `validate_visual_question_generation_metrics` → `missing-visual-question-generation-metrics` (moderate)
- Phase 515: `validate_captioning_hallucination_evaluation` → `missing-captioning-hallucination-evaluation` (moderate)

**Phases 516–520** (`69056ae`, 1866 tests)
- Phase 516: `validate_semantic_image_synthesis_metrics` → `missing-semantic-image-synthesis-metrics` (moderate)
- Phase 517: `validate_mot_metrics` → `missing-mot-metrics` (moderate)
- Phase 518: `validate_video_object_segmentation_metrics` → `missing-video-object-segmentation-metrics` (moderate)
- Phase 519: `validate_referring_expression_comprehension_metrics` → `missing-referring-expression-comprehension-metrics` (moderate)
- Phase 520: `validate_cross_modal_retrieval_metrics` → `missing-cross-modal-retrieval-metrics` (moderate)

**Phases 521–525** (`6cedbb6`, 1886 tests)
- Phase 521: `validate_emotion_recognition_metrics` → `missing-emotion-recognition-metrics` (moderate)
- Phase 522: `validate_document_layout_analysis_metrics` → `missing-document-layout-metrics` (moderate)
- Phase 523: `validate_table_structure_recognition_metrics` → `missing-table-structure-metrics` (moderate)
- Phase 524: `validate_handwriting_recognition_metrics` → `missing-handwriting-recognition-metrics` (moderate)
- Phase 525: `validate_entity_normalization_metrics` → `missing-entity-normalization-metrics` (moderate)
- Bug fix: Cyrillic variable `_НЕНОРМ_VID` → `_NENORM_VID`; duplicate definition removed

**Phases 526–530** (`ac78fcb`, 1906 tests)
- Phase 526: `validate_relation_extraction_metrics` → `missing-relation-extraction-metrics` (moderate)
- Phase 527: `validate_event_extraction_metrics` → `missing-event-extraction-metrics` (moderate)
- Phase 528: `validate_kbqa_metrics` → `missing-kbqa-metrics` (moderate)
- Phase 529: `validate_dialogue_state_tracking_metrics` → `missing-dialogue-state-tracking-metrics` (moderate)
- Phase 530: `validate_conversational_qa_metrics` → `missing-conversational-qa-metrics` (moderate)

**Phases 531–535** (`40051be`, 1926 tests)
- Phase 531: `validate_abstractive_summarization_metrics` → `missing-abstractive-summarization-metrics` (moderate)
- Phase 532: `validate_text_style_transfer_metrics` → `missing-text-style-transfer-metrics` (moderate)
- Phase 533: `validate_grammatical_error_correction_metrics` → `missing-gec-metrics` (moderate)
- Phase 534: `validate_text_simplification_metrics` → `missing-text-simplification-metrics` (moderate)
- Phase 535: `validate_story_generation_metrics` → `missing-story-generation-metrics` (moderate)

**Phases 536–540** (`919e5a0`, 1946 tests)
- Phase 536: `validate_data_to_text_metrics` → `missing-data-to-text-metrics` (moderate)
- Phase 537: `validate_paraphrase_detection_metrics` → `missing-paraphrase-detection-metrics` (moderate)
- Phase 538: `validate_wsd_metrics` → `missing-wsd-metrics` (moderate)
- Phase 539: `validate_srl_scoring_metrics` → `missing-srl-scoring-metrics` (moderate)
- Phase 540: `validate_argument_mining_metrics` → `missing-argument-mining-metrics` (moderate)
- Note: phase 539 uses `_SRL539_VID` (not `_SRL_VID`) to avoid shadowing phase 492's `_SRL_VID`

**Phases 541–545** (`548849d`, 1966 tests)
- Phase 541: `validate_commonsense_reasoning_metrics` → `missing-commonsense-reasoning-metrics` (moderate)
- Phase 542: `validate_semantic_parsing_metrics` → `missing-semantic-parsing-metrics` (moderate)
- Phase 543: `validate_code_summarization_metrics` → `missing-code-summarization-metrics` (moderate)
- Phase 544: `validate_api_usage_prediction_metrics` → `missing-api-usage-prediction-metrics` (moderate)
- Phase 545: `validate_multilingual_ner_metrics` → `missing-multilingual-ner-metrics` (moderate)
- Bug fixes: phases 541-545 used `source="deterministic"` (not a valid Finding field) — replaced with `validator=<VID>`; also phases 536-540 validator bodies verified clean
- Bug fix: `_APIUSAGE_TRIGGERS` trailing `\b` after non-word alternatives — restructured to not require outer `\b`

**Phases 546–550** (`59fec83`, 1986 tests)
- Phase 546: `validate_zero_shot_classification_metrics` → `missing-zero-shot-classification-metrics` (moderate)
- Phase 547: `validate_chain_of_thought_evaluation` → `missing-chain-of-thought-evaluation` (moderate)
- Phase 548: `validate_rag_evaluation_metrics` → `missing-rag-evaluation-metrics` (moderate)
- Phase 549: `validate_instruction_tuning_evaluation` → `missing-instruction-tuning-evaluation` (moderate)
- Phase 550: `validate_long_context_evaluation` → `missing-long-context-evaluation` (moderate)

**Phases 551–555** (`81186dd`, 2006 tests)
- Phase 551: `validate_continual_learning_metrics` → `missing-continual-learning-metrics` (moderate)
- Phase 552: `validate_gnn_benchmark_metrics` → `missing-gnn-benchmark-metrics` (moderate)
  - NOTE: renamed from `validate_gnn_evaluation_metrics` because phase 444 already defines that name
- Phase 553: `validate_nas_evaluation_metrics` → `missing-nas-evaluation-metrics` (moderate)
  - NOTE: uses `_NAS553_VID` and `_NAS553_TRIGGERS` (not `_NAS_VID`) to avoid shadowing phase 455's `_NAS_VID`
- Phase 554: `validate_contrastive_learning_evaluation` → `missing-contrastive-learning-evaluation` (moderate)
- Phase 555: `validate_knowledge_distillation_evaluation` → `missing-knowledge-distillation-evaluation` (moderate)

**Phases 556–560** (`b29fee7`, 2026 tests)
- Phase 556: `validate_quantization_evaluation_metrics` → `missing-quantization-evaluation-metrics` (moderate)
- Phase 557: `validate_adversarial_robustness_metrics` → `missing-adversarial-robustness-metrics` (moderate)
- Phase 558: `validate_differential_privacy_evaluation` → `missing-differential-privacy-evaluation` (moderate)
- Phase 559: `validate_multi_task_learning_evaluation` → `missing-multi-task-learning-evaluation` (moderate)
- Phase 560: `validate_speech_synthesis_evaluation` → `missing-speech-synthesis-evaluation` (moderate)

**Phases 561–565** (`84ad8f5`, 2046 tests)
- Phase 561: `validate_music_generation_evaluation` → `missing-music-generation-evaluation` (moderate)
- Phase 562: `validate_audio_source_separation_metrics` → `missing-audio-source-separation-metrics` (moderate)
- Phase 563: `validate_speaker_verification_metrics` → `missing-speaker-verification-metrics` (moderate)
- Phase 564: `validate_audio_captioning_metrics` → `missing-audio-captioning-metrics` (moderate)
- Phase 565: `validate_singing_voice_synthesis_metrics` → `missing-singing-voice-synthesis-metrics` (moderate)

**Phases 566–570** (`bbe3d7f`, 2066 tests)
- Phase 566: `validate_sound_event_detection_metrics` → `missing-sound-event-detection-metrics` (moderate)
- Phase 567: `validate_video_grounding_metrics` → `missing-video-grounding-metrics` (moderate)
- Phase 568: `validate_3d_scene_understanding_metrics` → `missing-3d-scene-understanding-metrics` (moderate)
- Phase 569: `validate_table_to_text_metrics` → `missing-table-to-text-metrics` (moderate)
- Phase 570: `validate_scene_text_recognition_metrics` → `missing-scene-text-recognition-metrics` (moderate)

**Phases 571–575** (`4a429b0`, 2086 tests)
- Phase 571: `validate_visual_dialog_metrics` → `missing-visual-dialog-metrics` (moderate)
- Phase 572: `validate_visual_commonsense_reasoning_metrics` → `missing-visual-commonsense-reasoning-metrics` (moderate)
- Phase 573: `validate_video_dense_captioning_metrics` → `missing-video-dense-captioning-metrics` (moderate)
- Phase 574: `validate_document_vqa_metrics` → `missing-document-vqa-metrics` (moderate)
- Phase 575: `validate_chart_qa_metrics` → `missing-chart-qa-metrics` (moderate)

**Phases 576–580** (`bb88dcf`, 2106 tests)
- Phase 576: `validate_crosslingual_summarization_metrics` → `missing-crosslingual-summarization-metrics` (moderate)
- Phase 577: `validate_multilingual_mt_metrics` → `missing-multilingual-mt-metrics` (moderate)
- Phase 578: `validate_code_translation_metrics` → `missing-code-translation-metrics` (moderate)
- Phase 579: `validate_formal_verification_metrics` → `missing-formal-verification-metrics` (moderate)
- Phase 580: `validate_program_synthesis_metrics` → `missing-program-synthesis-metrics` (moderate)

**Phases 581–585** (`aa5a88d`, 2126 tests)
- Phase 581: `validate_srl_benchmark_metrics` → `missing-srl-benchmark-metrics` (moderate)
  - NOTE: renamed from `validate_srl_evaluation_metrics` — phase 492 already defines that name
- Phase 582: `validate_entity_linking_metrics` → `missing-entity-linking-metrics` (moderate)
- Phase 583: `validate_coreference_benchmark_metrics` → `missing-coreference-benchmark-metrics` (moderate)
  - NOTE: renamed from `validate_coreference_resolution_metrics` — phase 491 already defines that name
- Phase 584: `validate_semantic_textual_similarity_metrics` → `missing-semantic-textual-similarity-metrics` (moderate)
- Phase 585: `validate_open_ie_metrics` → `missing-open-ie-metrics` (moderate)

**Phases 586–590** (`f2fac56`, 2146 tests)
- Phase 586: `validate_multimodal_sentiment_metrics` → `missing-multimodal-sentiment-metrics` (moderate)
- Phase 587: `validate_multimodal_mt_metrics` → `missing-multimodal-mt-metrics` (moderate)
- Phase 588: `validate_grounded_language_learning_metrics` → `missing-grounded-language-learning-metrics` (moderate)
- Phase 589: `validate_text_to_sql_metrics` → `missing-text-to-sql-metrics` (moderate)
- Phase 590: `validate_commonsense_kg_completion_metrics` → `missing-commonsense-kg-completion-metrics` (moderate)

**Phases 591–595** (`3af5de2`, 2166 tests)
- Phase 591: `validate_temporal_action_detection_metrics` → `missing-temporal-action-detection-metrics` (moderate)
- Phase 592: `validate_video_moment_retrieval_metrics` → `missing-video-moment-retrieval-metrics` (moderate)
- Phase 593: `validate_vos_benchmark_metrics` → `missing-vos-benchmark-metrics` (moderate)
  - NOTE: renamed from `validate_video_object_segmentation_metrics` — phase ~568 already defines that name
- Phase 594: `validate_video_retrieval_metrics` → `missing-video-retrieval-metrics` (moderate)
  - NOTE: trigger fixed to match `text-to-video retrieval` (uses `text[- ](?:to[- ])?video`)
- Phase 595: `validate_3d_object_detection_metrics` → `missing-3d-object-detection-metrics` (moderate)

Current test count: **2166 passing** (after phase 595)
HEAD: `3af5de2`

## Critical technical gotchas (accumulated)

- **`Section` has `title` field, NOT `heading`** — test helpers must use `title=`, validators use `s.title`
- **`ParsedManuscript` required fields**: `manuscript_id`, `source_path`, `source_format`, `title`, `full_text`
- **`ManuscriptClassification` required fields**: `pathway`, `paper_type`, `recommended_stack`
  - Field is `paper_type` (NOT `primary_type`)
  - `pathway` must be one of `"math_stats_theory"`, `"applied_stats"`, `"data_science"`, `"unknown"`
- **Constant shadowing hazard**: check before adding any module-level constant (`grep -n "^_CONST_NAME"` in core.py)
  - Known shadowed constants fixed: `_AUTHOR_YEAR_CITE_RE` → `_FORMAT_AUTHOR_YEAR_CITE_RE`, `_GENERALIZE_CLAIM_RE` → `_SINGLE_SITE_CLAIM_RE`, `_LONGITUDINAL_RE` → `_LONGITUDINAL_DESIGN_RE`, `_STAT_TEST_RE` → `_INFERENTIAL_STAT_RE`
- **Function shadowing hazard**: check before adding any function (`grep -n "^def func_name"` in core.py and test file)
  - Phase 159 `validate_interrater_reliability` was already implemented — duplicate removed
- **Duplicate test name hazard**: before adding tests, `grep -n "^def test_name"` in test file
  - Known duplicates fixed: `test_longitudinal_with_attrition_no_fire`, `test_no_regression_no_fire`, `test_non_rct_no_fire`, `test_software_with_version_no_fire`, `test_no_intervention_no_fire`, `test_non_rct_no_fire` (baseline equivalence), `test_coding_with_kappa_no_fire`, `test_no_observational_design_no_fire`, `test_no_group_comparison_no_fire`, `test_longitudinal_non_empirical_no_fire`
- **Helper function shadowing hazard**: always check for existing module-level helpers before adding (e.g., `_prereg_ms`, `_sensitivity_ms`, `_sensitivity_clf`). Rename new helpers with a phase suffix if needed (e.g., `_prereg222_ms`, `_sensitivity230_ms`)
- **`recommended_stack="core"` is invalid** — must be `"minimal"`, `"standard"`, or `"maximal"`. Always use `"minimal"` for non-empirical no-fire tests
- **Disclosure regex over-matching**: patterns like `this study was funded` can accidentally match inside "fires" test text. Keep acknowledgement/disclosure regexes specific (require explicit disclosure verbs like `we thank`, `acknowledgements:`, not generic `was funded`)
- **Existing validator function shadows**: confirmed existing at phases ~154: `validate_preregistration_statement`; phase ~156: `validate_sensitivity_analysis_reporting`. Rename new duplicates with distinct prefix/suffix
- **`_EMPIRICAL_PAPER_TYPES`** = `frozenset({"empirical_paper", "applied_stats_paper", "software_workflow_paper"})`
  - `math_theory_paper` is NOT in this set (use as the "skip" type in tests)
- **`_NULL_RESULT_RE` shadowing (phases 249/279)**: Both phases defined `_NULL_RESULT_RE` at module level. Phase 279's copy shadows phase 249's, breaking phase 249's `validate_null_result_discussion`. Fix: rename later copies to specific names (e.g., `_NULL_POWER_TRIGGER_RE` for phase 279's version)
- **Trailing `\b` after `to` in time-label patterns**: `T[123]\s+was\s+to` never fires since "was" is followed by content, not "to". Drop the final `\s+to` requirement: `T[123]\s+(?:was|corresponds?)` alone is sufficient
- **Phase 280 `validate_author_contribution_statement` already existed** at line 4331 with single-argument signature `(parsed: ParsedManuscript)`. The new duplicate was removed; 4 supplemental tests were adapted to match the existing code (single arg, code `missing-author-contributions`, requires both `_CONTRIB_SECTION_RE` AND `_CONTRIB_KEYWORD_RE` to match)
- **`_PPC_PERFORMED_RE` plural (phase 310)**: pattern had `check` without `s?`, missing "checks". Always add `s?` to potentially-plural nouns in match patterns.
- **`_ML_UNCERTAINTY_RE` plural (phase 315)**: `confidence interval` needed `intervals?` to match plural "confidence intervals". Apply plural-form discipline to ALL content nouns in match patterns.
- **`pathway="theory"` is invalid** — valid values: `"math_stats_theory"`, `"applied_stats"`, `"data_science"`, `"unknown"`. Always use `"math_stats_theory"` for non-empirical (math_theory_paper) no-fire tests.
- **`_replication_ms` helper at line ~6955**: returns `ParsedManuscript` (not tuple) — rename new to `_replication285_ms`

- **Phase 356-360 return type**: new validators must use `ValidationResult(validator_name=_vid, findings=[...])` — NOT `ValidatorModuleResult`. The `ValidatorModuleResult` name does not exist in the codebase.

- **Floor/ceiling regex**: use `effects?` not `effect` (plural form common)
- **MULTILINE regex** needed when checking for line-anchored captions (`^Table N.`)
- **Figure axis validator**: use distinct figure numbers (not raw occurrences); `_FIGURE_MENTION_RE` uses capturing group; threshold is `_FIGURE_MIN_DISTINCT = 2` distinct numbers

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
- currently at phase 560 with 2026 tests passing
- continue adding batches of 5 deterministic validators per phase group
- check for constant and function shadowing before each batch (grep -n "^_CONST" and "^def func" in core.py and test file)
- update MEMORY.md after every 40 phases (next update due after phase 640)
