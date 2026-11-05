# Implementation ledger

Plan: docs/superpowers/plans/implementation.md
Spec: docs/design.md
User approved direct implementation after selecting course acceptance edition.

Ruling: Original workspace is not a Git repository and D drive denies writes; work in a new desktop copy, preserve original sources, use test logs and file hashes instead of Git worktree/commit helpers.
Ruling: User explicitly requested code changes; no repeated design/plan approval gate.
Pre-flight: business migrations and core share field schema; frontend consumes explicit api-contract.md. File ownership prevents parallel edits to shared files.
Task 0: isolated latest-source desktop copy created; original entry files retained.

2026-10-26 implementation:
- Core: transactional assignment and transitions; interval overlap and credential checks; PBKDF2 upgrade; managed-field/ownership restrictions; validated vitals, threshold alerts, device deduplication and immediate emergency notifications.
- Domain: persistent institutions, services, packages, medication, visits, credentials, training, attachments, record review, complaints, locations and archive reasons. Repeated migration retains legacy data and skips demo reseeding.
- Frontend: API-backed role pages and forms; file upload/download; remote paging and search; record return/resubmission; personal performance; history; notification timers and session cleanup.
- Reports/operations: filtered SQL aggregates, cache invalidation, complete Excel/PDF, isolated generation/load tools and verified offline backup/restore.
- Review findings reproduced and corrected: stale departure ownership reads, missing exception path, lost evaluation attachment IDs, complaint handler projection, chronic cohort definition, inaccessible older record candidates, price snapshots, Chinese download headers, and provider care precautions.
- Browser follow-up reproduced and corrected: logout handler missing after restored admin login, provider profile submitting managed fields, elder booking requesting admin-only statistics.
- Independent review supplied findings but ended before a final clean verdict. Findings were reproduced and regressions rerun; no independent final approval is claimed.
- Final verification evidence and measured limitations are recorded in docs/本次验证记录.md and docs/validation/. The original ZIP and shipped baseline DB remain unchanged.
