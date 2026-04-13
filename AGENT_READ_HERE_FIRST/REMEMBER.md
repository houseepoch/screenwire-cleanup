# Remember

> **Priority 0 — Agents read this FIRST.**
>
> Persistent project-level notes that every agent should be aware of.
> These are lessons learned, gotchas, critical context, and decisions
> that affect how work should be done on this project.

---

## ARCHITECTURAL DECISION: CC-First Deterministic Graph Construction (2026-04-11)

**Status:** APPROVED — Implementation ACTIVE. Full spec at `build_specs/cc_first_deterministic_spec.md` (1072 lines).

**Decision:** Eliminate LLM-based entity seeding, frame parsing, dialogue wiring, and composition. Replace with deterministic Python parsers + frame enrichment + Grok cinematic tagging.

### Architecture
```
CC (Grok reasoning) → outline_skeleton.md  (///CAST, ///LOCATION, ///PROP, ///SCENE, ///SCENE_STAGING, ///DLG)
                     → creative_output.md   (/// frame markers — cast, cam, dlg, cast_states only)
                 ↓
Step 2a: Python parser (graph/cc_parser.py) — deterministic, <5 seconds
  → CastNodes, LocationNodes, PropNodes, SceneNodes (with staging_plan)
  → FrameNodes with sequence linking + base cast states
  → DialogueNodes via ///DLG excerpt pointers (verbatim source text, never copied)
  → All edges (FOLLOWS, APPEARS_IN, AT_LOCATION, DIALOGUE_SPANS, etc.)
                 ↓
Step 2b: Parallel frame enricher workers (graph/frame_enricher.py)
  → CastFrameState: screen_position, looking_at, emotion, posture, facing_direction, action
    (anchored by ///SCENE_STAGING start/mid/end beats from CC)
  → FrameComposition: shot, angle, movement, focus (from prose context)
  → FrameEnvironment: lighting, atmosphere, materials
  → FrameDirecting: dramatic_purpose, beat_turn, pov_owner, camera_motivation
                 ↓
Step 2b.5: Grok cinematic tagger (graph/grok_tagger.py) — per-frame tag assignment
  → Reads complete frame node data (cast states, composition, environment, directing)
  → System prompt: cinematic-frame-tag-taxonomy.md (D/E/R/A/C/T/S/M families, ~60 tags)
  → Assigns single tag per frame (e.g. D01.a +push, E01.c +tilt, R01.b +static)
  → Tag DEFINITION text injected into generation prompts (replaces old F01-F18 shot descriptions)
                 ↓
Step 2c: Continuity validator (graph/continuity_validator.py) — audit-only
  → Spatial consistency, staging plan compliance, cast state deltas, dialogue coverage
                 ↓
Step 2d: Prompt assembly + materialization (existing code, unchanged)
```

### Key Design Decisions
- **Entity rosters** use schema-ready `///CAST`, `///LOCATION`, `///LOCATION_DIR`, `///PROP` tags mapping directly to schema fields
- **Dialogue** uses `///DLG` excerpt pointers (src_start/src_end/src_lines) — parser extracts verbatim from creative_output.md, CC never copies text
- **Scene staging** uses `///SCENE_STAGING` with start/mid/end beats defining screen_position, looking_at, facing_direction per character — frame enricher workers anchor to these
- **Frame markers** are lean: `cast`, `cam`, `dlg`, `cast_states` only — NO tag, shot, angle, movement, or duration
- **Cinematic tags** assigned POST-GRAPH by Grok tagger, not by the CC or the old Haiku-based design. Tag definitions are textual composition directives injected into prompts
- **visible_description REMOVED** from frame-enricher output — redundant with location.directions[camera_facing] (api.py fallback handles it)
- **Duration** computed downstream by prompt_assembler from tag characteristics + dialogue timing
- **FormulaTag enum (F01-F18) REPLACED** by CinematicTag model with full taxonomy fields

### What's Eliminated
| Old | New | Cost Impact |
|-----|-----|-------------|
| Agent 1 (Entity Seeder) | Python parser | $0 |
| Agent 2 (Frame Parser) | Python parser | $0 |
| Agent 3 (Dialogue Wirer) | Python parser | $0 |
| Agent 4 (Compositor) | frame enricher workers | runtime-model-dependent |
| Agent 5 (Continuity) | Python validator | $0 |
| F01-F18 formula tags | Grok cinematic tagger (60+ tags) | ~$0.01/100 frames |

### Implementation Order
1. `graph/schema.py` — Add StagingBeat, CinematicTag; replace FormulaTag enum
2. `graph/cc_parser.py` — Python parser (new)
3. `graph/frame_enricher.py` — frame enricher dispatch (new)
4. `graph/grok_tagger.py` — Grok cinematic frame tagger (new)
5. `graph/continuity_validator.py` — Rule-based validation (new)
6. `graph/prompt_assembler.py` — Replace FORMULA_SHOT/FORMULA_VIDEO with CinematicTag.ai_prompt_language
7. `agent_prompts/creative_coordinator.md` — Add ///TAG format spec, remove dur/tag/shot fields
8. `run_pipeline.py` — Phase 2 rewritten as Steps 2a → 2b → 2b.5 → 2c → 2d
9. Archive Morpheus Agent 1-4 prompts

---

## Handler Layer (2026-04-11)

- **`handlers/` directory** is the media generation abstraction layer. All 5 handlers share `BaseHandler` (base.py) for Replicate API calls, retry, polling, fallback chains, and error classification.
- **p-image ALWAYS outputs JPG** — there is no `output_format` param. The upscaler (`p-image-upscale`) converts to PNG when `output_format="png"` is set. Non-upscaled cast images are raw JPG saved with `.png` extension.
- **nano-banana-2 vs nano-banana-pro** have different input schemas. `_adapt_input_for_model()` in base.py strips unsupported params per model. Do NOT pass `safety_filter_level` to nano-banana-2 or `google_search` to nano-banana-pro.
- **4K capacity rescue** in the frame handler downgrades to 2K + `allow_fallback_model=true` on nano-banana-pro. The `downshifted` flag in `FrameOutput` signals this happened.
- **Grok video duration** is clamped 2-15s (pipeline constraint). Model supports 1-15s but <2s is unreliable.
- **Location grid handler** has NO fallback chain — it goes direct to nano-banana-pro per design spec.
- **Storyboard cell extraction** assumes the model generates a uniform grid. The layout string (e.g. "2x2") MUST match the prompt's grid instructions or cells will be mis-cropped.

## Batch Concurrency (2026-04-11)

- **`BaseHandler.generate_batch(inputs, max_concurrent=10)`** runs up to 10 `generate()` calls concurrently via `asyncio.Semaphore(10) + asyncio.gather()`. Returns a `BatchResult` with per-item results + total/succeeded/failed counters.
- **Individual failures are graceful** — each handler overrides `_make_error_output(inp, exc)` to return a typed error output (preserves ID fields like cast_id, frame_id, etc.). One failure does NOT kill the batch.
- **5 batch endpoints** added to server.py: `POST /internal/batch-generate-{image,frame,video,location-grid,storyboard}`. Each accepts `{"items": [...], "max_concurrent": 1-10}` and returns `{"results": [...], "total": N, "succeeded": N, "failed": N}`.
- **Shared http_client** — all concurrent requests in a batch share the same `http_client` instance from the handler constructor. The handler is created once, runs the batch, then closed.
- **max_concurrent is clamped 1-10** by Pydantic `Field(ge=1, le=10)` on the batch request models.

## Handler Wiring (2026-04-11)

- **3 endpoints rewritten** to delegate to handlers: `/internal/generate-image` → `cast_image`, `/internal/generate-frame` → `frame`, `/internal/generate-video` → `video_clip`. All pass `http_client` and `REPLICATE_API_TOKEN` from server globals.
- **2 new endpoints added**: `/internal/generate-location-grid` (LocationGridHandler), `/internal/generate-storyboard` (StoryboardHandler).
- **Handler output paths differ from endpoint output paths** — every rewritten endpoint uses `shutil.move()` to relocate the handler's output to the caller's requested `output_path`. The handler writes to its own directory structure (e.g. `frames/composed/`), then the endpoint moves the file.
- **Shared helpers NOT deleted**: `_generate_with_fallback`, `_upload_to_replicate`, `_replicate_predict`, `_poll_replicate_prediction`, `_download_file`, `_classify_replicate_error`, `_log_composition`, `_build_prediction_error` are all still in server.py — used by `/internal/edit-image` and `/internal/fresh-generation`.
- **grid_generate.py** delegates to `StoryboardHandler.generate()` for model chain + API + cell extraction. `split_grid()` has been removed — `StoryboardHandler._extract_cells()` is the sole cell-splitting path. The grid guide PNG is passed as the first reference image to the handler.
- **Backward compat**: All new request model fields (cast_id, media_style, frame_id, dialogue_text) are Optional with defaults. Existing callers need no changes.
- **generate-video now requires image_path** — the VideoClipHandler needs a frame image. Prompt-only video generation is no longer supported via this endpoint (was never used in practice).

## Location Type Threading (2026-04-11)

- **`LocationNode.location_type`** controls which 2×2 grid template the location grid handler uses. Valid values: `"interior"`, `"exterior"` (default `"exterior"`).
- **The CC parser** sets `location_type` on every `LocationNode` based on INT/EXT from scene headings. Interior rooms/enclosed spaces → `"interior"`. Open areas/streets/gardens → `"exterior"`.
- **Prompt assembler** (`assemble_location_prompt`) maps `loc.location_type` → `template_type` in the output dict. Falls back to `"exterior"` if value is None or unrecognized.
- **Asset generator** (`graph_generate_assets`) reads `template_type` from the prompt JSON and passes it to `/internal/generate-location-grid`.
- **Full thread**: graph schema → entity seeder → prompt assembler → prompt JSON → asset generator → server endpoint → handler.
