Engineering Manifest
Last updated: 2026-05-02T09:43:26-06:00

Audit summary
- Branch: main
- HEAD: c24d9f7 ("remove domain-specific validators: keep only statistics and data science methodology")
- Tests: 1414 passing (pytest)
- Lint: passed (ruff)
- Key file sizes:
  - src/manuscript_audit/validators/core.py: 24,244 lines
  - tests/unit/test_validators.py: 24,601 lines
  - MEMORY.md: 1,537 lines
- Recent change: removed domain-specific validators and corresponding tests; repo now focuses on statistics and data-science methodology only.

Recommended immediate next steps
1. Update MEMORY.md to reflect the cleanup and confirm phase-12 closure (validator counts, removed phases).
2. Run fixture-backed validations (collect artifacts):
   - pixi run audit-standard -- use fixture-backed source verification
   - pixi run verify-sources -- ambiguous fixture
3. If both workflows pass, produce a validated phase-12 bundle (archive root must contain repo-relative contents directly).
4. Begin phase 13 work: finalize bibliography confidence rollups integration, add/restore any missing unit tests, update report synthesis to include bibliography confidence artifacts.
5. Clean up branches and ensure PRs are merged before branching for phase 13 work.

Short-term todos created (see project tracking):
- finalize-phase-12-bundle (pending): run workflows, collect artifacts, produce validated bundle
- prepare-phase-13 (pending): finalize bibliography confidence rollup integration, add tests and docs

If any workflow fails: debug, patch, re-run until green. Do not package or release until workflows pass from this repo state.

Authored-by: Dylan Hettinger
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>

## Session update 2026-05-03T14:20:42-06:00
- Added bibliography_confidence scaffold; tests passed (1428).
- Plan created at /Users/dhetting/.copilot/session-state/b9e2cc10-7671-4448-a65c-2b606bf9f400/plan.md

## Session update 2026-05-04T10:12:00-06:00
- Phase-13 work started: wired authoritative implementation for bibliography confidence
  into manuscript_audit.parsers (build_bibliography_confidence_summary → source_verification).
- Restored package-local bibliography_confidence test scaffold to preserve lightweight
  unit tests (compute_confidence_summary returns dict for package tests).
- Run full test suite: 1428 passed (pixi run test -q).
- Next: run targeted calibration tests (pixi run ci-biblio) and iterate on scoring
  coefficients and thresholds with added calibration fixtures and tests.
