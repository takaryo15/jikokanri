# Codex Project Rules

These rules apply to every phase of this repository.

## Implementation principles

- Inspect related code, tests, and README before changing anything.
- Prefer the existing implementation and tests when details are unclear.
- Make the smallest change that achieves the requested goal.
- Avoid unrelated features, dependencies, refactors, and rewrites.

## Compatibility and data safety

- Do not change existing CLI command names, arguments, or basic behavior.
- Keep existing JSON, CSV, configuration, and other stored data readable.
- New fields must normally be optional. Preserve unknown fields when possible.
- Never delete existing data or perform an unapproved bulk migration.
- Do not overwrite data without explicit authorization.
- Use dry-run where it materially reduces risk.
- Do not hide errors or report success when an operation failed.
- Do not add external APIs or dependencies unless the user explicitly requests them.

## Daily-review behavior

- Keep proposal and final plans separate; never automatically approve a proposal.
- Do not automatically add carryover tasks or apply improvement suggestions.
- Preserve raw logs without modification.
- Keep Main at three items or fewer and require a minimum line for every task.

## Tests and documentation

- Review existing tests before implementation.
- Add tests for every new feature and regression test for every bug fix.
- Never delete or weaken tests just to make the suite pass.
- Run the full `pytest` suite before reporting completion; do not complete with failures.
- Update README when commands, options, storage, workflow, or user-facing limits change.
- README commands must be copyable terminal commands using ASCII `--` options.

## Required workflow

1. Inspect the current code, tests, README, and relevant data structure.
2. Decide on the smallest compatible implementation.
3. Implement the change and its tests.
4. Update README when needed.
5. Run full `pytest`.
6. Manually check the affected CLI command or behavior.
7. Run `git status`, `git diff --stat`, `git diff --check`, and `git log --oneline -5`.
8. Report changed files, behavior, compatibility, tests, verification, git status, limitations, and user verification commands.

## Git restrictions

Do not run any of the following unless the user explicitly asks:

- `git commit`
- `git push`
- `git tag`
- GitHub Release publication
- force push
- `git reset --hard`
- history rewriting

Do not use real user data as destructive test data.
