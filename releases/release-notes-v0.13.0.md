# Release v0.13.0 — Phase 13: Bibliography confidence calibration

Summary:
- Merged PR: phase13/bibliography-confidence → main (PR #6).
- Final validations executed and logs attached to this release.

Audit (audit-standard):
- Completed standard run run-20260505T232143Z for draft-manuscript-with-unresolved-issues
-   findings:  fatal=0  major=8  moderate=11  minor=4  (23 total)
-   routing:   applied_stats | standard stack | 10 priorities

Source verification (verify-sources):
- Completed source verification run-20260505T232144Z for draft-manuscript-with-unresolved-issues
-   sources:    0 total  0 verified  0 issues  skipped=0
-   confidence: critical | 2 priorities

Artifacts attached to this release:
- data/outputs/audit-standard-final-20260505T232143Z.log
- data/outputs/verify-sources-final-20260505T232143Z.log
- data/outputs/final-validation-summary-20260505T232143Z.json
- phase13 validated bundle (uploaded earlier)

Notes:
- Deterministic and verified scoring thresholds were calibrated in src/manuscript_audit/parsers/source_verification.py.
- Large artifacts are stored in GitHub Release assets to avoid committing >100MB files into git history.
