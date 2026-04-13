# Morpheus Studio QA and Playwright Ops

This UI now has two separate automation lanes:

1. `npm run test:e2e:workflow`
   Exercises deterministic renderer coverage with Playwright against a local preview build.
   Covers onboarding, preproduction build loading state, approval gates, timeline media toggles, and inline failure states.

2. `npm run test:e2e:smoke`
   Exercises the real Electron desktop path with the existing backend bootstrap.
   Covers new project creation, onboarding submission, backend start, workspace hydration, and worker settlement.

## One-time setup

```bash
npm install
npm run playwright:install
```

`~/.codex/config.toml` already has `js_repl = true` in this environment, so the interactive Codex skill only needs a fresh session after restart.

## Core commands

```bash
npm run test:e2e:workflow
npm run test:e2e:smoke
npm run test:e2e
```

Artifacts:

- Deterministic Playwright outputs: `output/playwright/`
- Electron smoke screenshots: `smoke/artifacts/`
- Electron smoke run summary: `smoke/last-run.json`

## Coverage map

Automated now:

- Onboarding step 1 progression gate
- Preproduction build loading state before the first review gate
- Reference approval request-changes flow
- Reference approval transition into frame generation
- Timeline media toggle behavior
- Timeline approval transition into video generation
- Inline error surfacing for approval failures
- Inline error surfacing for timeline quick-approve failures
- Real Electron create-project to workspace bootstrap
- Worker health check after onboarding submit

Manual headed pass after restart:

- Run the `playwright-interactive` skill for visual QA, spacing regressions, modal layering, focus states, and animation timing
- Validate mobile and desktop layouts in separate sessions
- Validate drag/drop in the timeline with a real pointer, not only synthetic route mocks
- Validate long-running backend worker progress and reconnect behavior

## Production signoff checklist

- Home screen project creation works in Electron with no console exceptions
- Onboarding blocks progression until the user provides story input or uploads source material
- Onboarding submit failure is visible in UI, not just console output
- Reference and timeline approval states expose the real launch points for the next phase
- Request-changes actions preserve the latest workflow change-request count in UI
- Timeline approve failure is visible in UI
- Worker overlay appears immediately after onboarding while the preproduction build runs
- Worker overlay appears during active backend work and clears when workers settle
- Smoke run leaves a usable artifact trail on failure
- Playwright deterministic suite passes before merging renderer changes
