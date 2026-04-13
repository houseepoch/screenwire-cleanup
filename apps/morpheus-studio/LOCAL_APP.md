# Morpheus Studio Local App Shell

This app is the local Electron shell for the ScreenWire pipeline.

## Architecture

- `Electron main` owns project listing, project creation, and local backend startup.
- `React + Vite` is the Morpheus workspace UI.
- `Python server.py` remains the active project-scoped pipeline backend.

## Current Foundation

The shell now supports:

- listing local ScreenWire projects from `../../projects`
- creating a new project through `create_project.py`
- selecting a project and starting `server.py` with the correct `PROJECT_DIR`
- exposing those desktop actions to the renderer through `window.screenwire`

## Development

1. Install frontend dependencies:

```bash
cd apps/morpheus-studio
npm install
```

2. Start the local desktop app:

```bash
npm run dev
```

This runs:

- Vite dev server on `http://127.0.0.1:5173`
- Electron shell

The Python backend is started by Electron only after a project is selected.

## Local Smoke Harness

The app includes a hidden Electron smoke run that exercises the real UI flow:

- create project from the home screen
- open onboarding
- upload a source file
- set media style and frame budget
- submit onboarding
- verify backend workspace hydration
- verify the spawned pipeline worker does not immediately fail

Run it with:

```bash
cd apps/morpheus-studio
npm run smoke:local -- \
  --seed "/absolute/path/to/source.md" \
  --frame-budget 30 \
  --media-style live_retro_grain
```

Artifacts:

- report: `apps/morpheus-studio/smoke/last-run.json`
- screenshots: `apps/morpheus-studio/smoke/artifacts/`

## Next Integration Slices

1. Replace mock `HomeScreen` projects with `desktopService.listProjects()`
2. Route onboarding wizard actions through `desktopService.createProject()`
3. Add workspace snapshot routes for the selected project
4. Introduce approval gates for skeleton, entities, storyboard, timeline, and video
5. Make Morpheus chat issue graph-aware read/write commands with focus context
