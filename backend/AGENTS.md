# DormPrint backend module work

The human has approved V1. Read ../docs/superpowers/specs/2026-10-30-dormprint-v1-scope.md and CONTRACT.md. The finalized Q/R answers override old prompts. Preserve unrelated frontend work. Never reset, stash, stage, or commit other workers' files. Do not create goals or new chat tasks. Use the assigned resident DSH session for follow-up.

Write meaningful failing tests first, observe failure, then implement. PostgreSQL integration tests are required; SQLite is not a substitute for locking/constraints. Tests use isolated schemas through tests/conftest.py. Do not claim acceptance from mocks or your turn completion. Report changed files, actual commands/results, remaining gaps.

File ownership is explicit in each assignment. Shared models, core framework, entry point, migrations, dependency locks, deployment and shared test fixtures belong to Codex. Request schema changes in your report or use a module-local table for genuinely separate concepts; never modify another owner's module. Use app.core/core helpers from CONTRACT.md, no second auth, database, config or order system.
