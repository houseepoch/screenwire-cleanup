# ScreenWire AI — Shared Agent Conventions

This file is auto-deployed as CLAUDE.md into every project directory. All agents inherit these conventions.

## JSON Rule

When writing JSON files, write RAW JSON only. Never wrap in markdown code fences.

## The Single-Writer Rule

You never write to `project_manifest.json` directly. All manifest updates go through the queue skill `sw_queue_update`. The ManifestReconciler (a backend process) is the only writer.

## Skill Stdout Parsing

Each skill prints a structured result to stdout. Parse these to confirm success:
- `sw_queue_update`: prints `SUCCESS: Queued update → {path}` on success, `ERROR: {message}` on failure.
- `sw_update_state`: prints `SUCCESS: State updated → {path}` on success, `ERROR: {message}` on failure.

## Events JSONL Schema

Append one JSON object per line to `logs/{agent_id}/events.jsonl`:

```json
{"timestamp": "ISO-8601", "agent": "{agent_id}", "level": "INFO|WARN|FATAL", "code": "EVENT_CODE", "target": "entity_id", "message": "description"}
```

Level values: `INFO`, `WARN`, `FATAL`.

## Context JSON Schema (Resumability Checkpoint)

Update `logs/{agent_id}/context.json` after each major step for crash recovery:

```json
{
  "agent_id": "{agent_id}",
  "phase": 1,
  "last_updated": "ISO-8601",
  "checkpoint": {
    "sub_phase": "current_step",
    "last_completed_entity": "entity_id",
    "completed_entities": [],
    "pending_entities": [],
    "failed_entities": []
  },
  "decisions_log": [],
  "error_context": null
}
```

## State Folder Pattern

Each agent owns `logs/{agent_id}/` containing:
- `state.json` — current phase/status tracking
- `events.jsonl` — structured telemetry (see schema above)
- `context.json` — resumability checkpoint (see schema above)
