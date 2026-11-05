# Course Acceptance Implementation Plan

> For agentic workers: use executing-plans and dispatching-parallel-agents for independent file-owned domains. Track each task below.

**Goal:** Implement the approved course acceptance design while retaining existing data and endpoints.
**Architecture:** Preserve server.py; install acceptance.core adapters before main. Domain schema and new CRUD live in acceptance.business. Frontend extends original navigation with acceptance.js. No public-server calls.
**Tech Stack:** Python stdlib, SQLite, vanilla JS/CSS, existing openpyxl/reportlab.
**Spec:** ../../design.md

## Global Constraints

- Original ZIP, D drive source and original database are never modified.
- Course-only external integrations explicitly say unconfigured/simulated.
- Tests use temporary database and upload paths, localhost ephemeral ports.
- Real persistence and permission checks on all forms; no success-only toast.
- No app rebuilding or 2000-user capacity claims without actual evidence.
- Non-Git source: isolated desktop copy plus ledger/hashes replaces Git-only worktree scripts and commits.
- User said “改代码吧”: execute continuously, no further plan approval pauses.

## Review Focus

- Concurrent overlap assignments must serialize eligibility checks and writes.
- Pending credentials and personnel self-edit must not grant privileges.
- Attachments must validate content, ownership and safe storage paths.
- Repeated migrations retain legacy identities, reviews and attachments.
- Empty and filtered statistics must not fall back to simulated values.

## Ownership and interfaces

Root owns acceptance/core.py, server.py entry integration, tests/support.py and test_core.py, final integration and packaging.
Business implementer owns acceptance/business.py and tests/test_business.py; implements migrate(module), route(handler,module,method,path,query)->bool. Routes live under /api/acceptance. Reads handler.current_user(), handler.read_json(), handler.send_json(), module.connect_db(), module.BASE_DIR, module.DB_PATH. Unknown route returns False. Raise ValueError/PermissionError for invalid/forbidden operations; root catches and maps HTTP errors.
Frontend implementer owns static/acceptance.js, static/acceptance.css and original static app/index changes. Uses existing AUTH, API, apiRequest. Shared endpoint contract recorded in docs/api-contract.md.
Operations implementer owns tools/, tests/test_tools.py, api_smoke_test.py, README.md, start.bat. No edits to server/core/business/frontend.

### Task 1 Core business rules and security

- [x] Write tests/support.py HTTP fixture using isolated copied DB, module initialization and ephemeral HTTP server.
- [x] Write failing tests for overlap/manual eligibility, provider management field restrictions, arrive-before-complete, invalid vitals, legacy password upgrade, true health history and notifications.
- [x] Run `python -m unittest tests.test_core -v`, confirm expected failures before code.
- [x] Implement acceptance/core.py install(module), preserving original methods where safe. Atomic assignment; standardized intervals; health thresholds; notifications; strict permission; record review integration; real stats/export.
- [x] Install adapter before main in server.py. Run core suite until green.

### Task 2 Domain migrations and persistent APIs

- [x] Write domain API tests for institutions/services/packages, medications/visits/thresholds, credentials/training, attachments, record review/complaint handling and notifications/locations.
- [x] Run tests against baseline, confirm missing endpoints before code.
- [x] Implement idempotent migrations and role/ownership-constrained CRUD endpoints in acceptance/business.py.
- [x] Run domain and core suites together; fix only owned files, report integration issues.

### Task 3 Frontend completion

- [x] Establish failed checks for API-backed controls and absent demo-only mutations.
- [x] Add navigation and forms for new domain endpoints, upload/download, review/complaint handling, real stats, 5-second notifications and 60-second foreground location updates.
- [x] Remove synthetic display fallbacks; emergency posts immediately before optional location, cancel uses server time; output escape user text.
- [x] Run JS syntax and frontend tests; browser exercise role switching and persistence.

### Task 4 Operations and acceptance

- [x] Write failing tests for backup/restore integrity and isolated data generation.
- [x] Add tools/operations.py backup/restore and tools/generate_data.py / tools/load_check.py commands with explicit paths and non-overwrite safety.
- [x] Fix smoke script role-authenticated complete workflow including record approval/review. Update startup and README honest acceptance instructions.
- [x] Run tools suite and authenticated smoke against disposable local service.

### Task 5 Integration review and delivery

- [x] Run all Python tests, JS checks, fresh/legacy migration, real PDF/Excel and upload checks.
- [x] Review security, compatibility and design coverage using independent findings plus integration review. Reproduce important findings with failing tests, fix, rerun all. Independent reviewer did not issue a final clean verdict.
- [x] Browser visual/functional verification, restart persistence, backup restore and bounded load check.
- [x] Write requirement coverage and exact measured limits; generate ZIP excluding test databases, caches, secrets and obsolete APK by default (old APK kept in source with warning).
- [x] Verify ZIP integrity, source hashes unchanged and final startup. Deliver source path and ZIP, explicit limitations.
