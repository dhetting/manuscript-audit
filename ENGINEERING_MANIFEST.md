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
