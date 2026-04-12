# Architecture Reports

One-click launchers for rebuilding the architecture docs in this folder.

## Linux / macOS

From the repo root:

```bash
bash docs/Architecture/run_architecture_reports.sh
```

Or run the launcher directly:

```bash
/home/nikoles16/Documents/ScreenWire\ Environments/screenwire-pipeline/docs/Architecture/run_architecture_reports.sh
```

## Windows

From the repo root:

```bat
docs\Architecture\run_architecture_reports.bat
```

Or run the batch file directly:

```bat
"C:\path\to\screenwire-pipeline\docs\Architecture\run_architecture_reports.bat"
```

## Direct Python

```bash
python3 build_architecture_reports.py
```

What it does:

- archives the generated report files in `docs/Architecture/` into `docs/Architecture/0_archived/<timestamp>/`
- leaves this README and the launcher scripts in place
- rebuilds the architecture summary
- rebuilds the Python dependency report and Mermaid graph
- rebuilds the repo snapshot index plus 25 MB max split parts
