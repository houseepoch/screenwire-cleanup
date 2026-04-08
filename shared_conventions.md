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

## Project Size Definitions

| outputSize | Frame Range | Scene Range |
|---|---|---|
| `short` | 10–20 frames | 1–3 scenes |
| `short_film` | 50–125 frames | 5–15 scenes |
| `televised` | 200–300 frames | 20–40 scenes |
| `feature` | 750–1250 frames | 60–120 scenes |

## Stickiness Scale (1-5)

Stickiness governs the Creative Coordinator's creative boundary. It does NOT apply to downstream agents (Morpheus, etc.) which operate on the creative output as structured input.

| Level | Label | Permission |
|---|---|---|
| 1 | Reformat | Restructure source into operational format. No new content — source dictates what exists, you dictate how it reads |
| 2 | Remaster | Faithful to source with enriched quality. Add sensory detail, deepen descriptions, fill gaps. Same story, higher fidelity |
| 3 | Expand | Round out incomplete areas. Add transitional scenes, supporting details, environmental context the source implies but doesn't show |
| 4 | Reimagine | Source's story/narrative/themes as foundation. May introduce new cast, locations, writing to serve existing arcs |
| 5 | Create | Source is a seed idea. Write an original story inspired by its guidance with full creative ownership |

## Media Style Prefix

Every image generation call MUST include the media style prefix from `onboarding_config.json.mediaStylePrefix`. This ensures visual consistency across all generated assets. For storyboard prompts, the style prefix is appended as a **suffix** instead.

| mediaStyle | Display Name | Prefix |
|---|---|---|
| `new_digital_anime` | New Digital Anime | anime modern, high-fidelity polished 2D digital anime, clean linework, gradient shading, high-contrast palette |
| `live_retro_grain` | Live Retro Grain | live action vintage analog film emulation, diffused portraiture lighting, warm color grade |
| `chiaroscuro_live` | Chiaroscuro Live | live action dramatic chiaroscuro, crimson/amber vs cool blue moonlight, 35mm grain |
| `chiaroscuro_3d` | Chiaroscuro 3d | 3D unreal render, chiaroscuro lighting, crimson/amber highlights, crushed blacks |
| `chiaroscuro_anime` | Chiaroscuro Anime | anime digital with chiaroscuro contrast, warm practicals vs cool ambient |
| `black_ink_anime` | Black Ink Anime | gritty 2D cel-shaded, thick black ink outlines, desaturated palette, retro grain |
| `live_soft_light` | Live Soft Light | 35mm nostalgic, soft diffused naturalistic lighting, muted pastel palette, gentle grain |
| `live_clear` | Live Clear | modern digital, dramatic overhead spotlighting, minimalist palette, ultra-sharp clarity |
