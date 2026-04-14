# Desktop Installers

The Electron shell now builds versioned desktop installers with `electron-builder`.

## Targets

- Windows: NSIS installer (`.exe`)
- macOS: DMG and ZIP
- Linux: AppImage and DEB

Artifacts are written to `apps/morpheus-studio/release/` and use the pattern:

`ScreenWire Studio-<version>-<os>-<arch>.<ext>`

## Local builds

```bash
cd apps/morpheus-studio
npm run dist:linux
npm run dist:win
npm run dist:mac
```

`npm run dist` builds the current platform target set.

## Packaged runtime layout

- App resources are bundled into `resources/backend`
- User projects are stored outside the install directory in:
  - development: `<repo>/projects`
  - packaged app: `<Documents>/ScreenWire Projects`

## Runtime prerequisites

The packaged app bundles the ScreenWire backend code, but it still requires:

- Python 3.11+
- backend Python dependencies from the bundled `requirements.txt`
- `ffmpeg` on `PATH`

On packaged launches, the app now warns immediately if those prerequisites are missing instead of failing later during project creation or export.
