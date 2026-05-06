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

Phase 13 status — bibliography confidence calibration (Updated: 2026-05-05T23:21:43Z)

- Phase 13 work completed and merged to main (PR #6). Authoritative implementation located at src/manuscript_audit/parsers/source_verification.py.
- New package-level scaffold retained for lightweight unit tests (src/manuscript_audit/bibliography_confidence/).
- Extensive calibration tests and fixtures were added under tests/unit and tests/fixtures/registries/.
- Final Pixi CI and validation: targeted ci-biblio and full test suite passed; final Pixi runs and local validation completed successfully.
- Final deterministic validations (audit-standard and verify-sources) were executed on a canonical fixture and both succeeded locally. Validation outputs were persisted to data/outputs/ and a summary JSON was committed to the repo.
- Phase13 validated bundle was created and uploaded to GitHub Release v0.13.0 (SHA256: 5fa11ecad4e306d176bdcdc56027ebaf1e5714235cebf134c01152a2db7d697b). Release: https://github.com/dhetting/manuscript-audit/releases/tag/v0.13.0
- start-phase-13 todo marked done in session tracking.

Remaining / recommended next steps:
1. Add CONTRIBUTING/RELEASE guidelines to avoid committing large bundles into git; recommend artifact hosting (external store or GitHub Releases).
2. Optionally prune large bundle artifacts from repository history or move retained artifacts to an external store if long-term retention on git is undesired.
3. Triage and resolve remaining major/moderate findings from the final audit run and schedule follow-up work as needed.
4. Close phase-13 tracking and create post-release maintenance todos (monitoring, backporting, documentation).

Short-term todos updated:
- finalize-phase-12-bundle: CLOSED (phase-12 artifacts reconciled during merge)
- prepare-phase-13: DONE

Authored-by: Dylan Hettinger
Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>

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
