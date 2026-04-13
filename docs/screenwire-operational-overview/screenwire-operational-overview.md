# ScreenWire Operational Overview

Generated from the active `screenwire-pipeline` repository implementation.
Document date: 2026-04-11

## 1. Purpose

This document is the code-verified operational specification for the current ScreenWire runtime. It describes what the repository actually does today, not what older planning documents intended a previous architecture to do.

The active system is a headless story-to-video pipeline that:

- bootstraps a project from a template scaffold
- converts source material into structured narrative prose with explicit frame markers
- extracts a narrative graph from that prose
- assembles deterministic image and video prompts from graph state
- generates reference assets, storyboard guidance grids, composed frames, and final clips
- exports a stitched final video through `ffmpeg`

The requested emphasis of this paper is operational completeness. It covers:

- scaffold and folder topology
- onboarding and bootstrap behavior
- Creative Coordinator process
- deterministic graph construction process
- graph schema, nodes, edges, and support packets
- image prompt builders and video prompt builders
- image generation patterns and video generation patterns
- internal endpoints, CLI skills, and phase runner behavior
- materialization, asset sync, tagging, quality gates, and export
- execution gaps, legacy leftovers, and runtime caveats

## 2. Canonical Runtime Boundary

When repository artifacts disagree, the following sources are authoritative for the active runtime:

- `create_project.py`
- `run_pipeline.py`
- `server.py`
- `graph/schema.py`
- `graph/api.py`
- `graph/prompt_assembler.py`
- `graph/frame_prompt_refiner.py`
- `graph/materializer.py`
- `graph/store.py`
- `handlers/`
- `skills/`
- `agent_prompts/`
- `API_REFERENCE.md`
- `shared_conventions.md`

Legacy or secondary references still useful for intent, but not canonical when they conflict with code:

- `build_specs/`
- `docs/Archived/`
- older voice/TTS-era planning notes

## 3. Runtime Truth Versus Legacy Intent

Before reading the rest of the paper, operators should internalize five important truth conditions:

1. The graph is the canonical story state.
The manifest and flat files are projections for compatibility, reporting, and downstream skills.

2. The runner is mostly local-process orchestration, not server-driven orchestration.
`run_pipeline.py` directly spawns Claude CLI sessions and local Python skills. The FastAPI server mostly exists as a generation gateway plus reconciler/sentinel service.

3. The "single-writer rule" applies to agents, not literally every runtime path.
Agents are supposed to use `sw_queue_update` into the manifest queue. Runtime utilities such as `run_pipeline.py`, `graph.materializer`, and `graph_sync_assets` still write `project_manifest.json` directly.

4. Some template directories are legacy, while prompt assembly writes into flatter directories.
The template still contains nested prompt folders like `cast/composites/prompts`, but active prompt assembly writes to `cast/prompts`, `locations/prompts`, `props/prompts`, `frames/prompts`, `frames/shot_packets`, `frames/storyboard_prompts`, and `video/prompts`.

5. Storyboards are guidance only.
They are continuity and composition references, not the final visual output layer.

## 4. Repository Scaffold

At the repository root, the active runtime is organized into these major surfaces:

### 4.1 Core runtime

- `create_project.py`: project bootstrap, onboarding config generation, manifest seeding
- `run_pipeline.py`: end-to-end phase runner
- `server.py`: FastAPI backend, manifest reconciliation, sentinel, media generation gateways, batch endpoints
- `checkpoint.py`, `train_agent.py`: ancillary utilities

### 4.2 Graph system

- `graph/schema.py`: canonical graph contract
- `graph/api.py`: query, mutation, propagation, continuity, storyboard grouping, shot matching
- `graph/store.py`: atomic graph persistence and overlay merge
- `graph/prompt_assembler.py`: deterministic prompt builders
- `graph/frame_prompt_refiner.py`: Grok vision refinement pass for video prompts
- `graph/materializer.py`: graph-to-flat-file export
- `graph/grid_generate.py`: storyboard grid generation helper

### 4.3 Handler layer

- `handlers/base.py`: Replicate client, fallback chains, retry logic, file upload/download, batch execution
- `handlers/models.py`: typed I/O contracts, model routes, resolution rules
- `handlers/cast_image.py`
- `handlers/location_grid.py`
- `handlers/storyboard.py`
- `handlers/frame.py`
- `handlers/video_clip.py`

### 4.4 Prompt layer

- `agent_prompts/creative_coordinator.md`
- `agent_prompts/director.md`
- `agent_prompts/morpheus_1_entity_seeder.md`
- `agent_prompts/morpheus_2_frame_parser.md`
- `agent_prompts/morpheus_3_dialogue_wirer.md`
- `agent_prompts/morpheus_4_compositor.md`
- `agent_prompts/morpheus_5_continuity_wirer.md`
- `agent_prompts/writing_guide.md`
- `agent_prompts/image_verifier.md`
- `agent_prompts/composition_verifier.md`
- `agent_prompts/video_verifier.md`
- `agent_prompts/references/`

### 4.5 Skill layer

Top-level operational families:

- `sw_*`: manifest/state helpers and direct media gateway wrappers
- `graph_*`: graph lifecycle, prompt assembly, generation orchestration, validation, sync
- `skill_*`: verification or extraction helpers

### 4.6 Test and operational support

- `tests/`: contract, unit, and live smoke coverage
- `AGENT_READ_HERE_FIRST/`: researched notes, API notes, architecture diagrams
- `docs/screenwire-operational-overview/`: this paper, PDF renderer, generated PDF

## 5. Per-Project Scaffold

`create_project.py` copies `projects/_template/` into `projects/{project_id}` and then mutates template placeholders into a real project. The actual template tree currently shipped in the repository is:

```text
projects/_template/
├── CLAUDE.md
├── assets/
│   └── active/
│       └── mood/
├── audio/
│   └── dialogue/
├── cast/
│   └── composites/
│       └── prompts/
├── config/
├── creative_output/
│   └── scenes/
├── dialogue.json
├── dispatch/
│   └── manifest_queue/
│       └── dead_letters/
├── frames/
│   ├── composed/
│   │   └── prompts/
│   ├── prompts/
│   ├── storyboard_prompts/
│   └── storyboards/
│       └── prompts/
├── graph/
├── locations/
│   └── primary/
│       └── prompts/
├── logs/
│   └── pipeline/
├── project_manifest.json
├── props/
│   └── generated/
│       └── prompts/
├── source_files/
│   └── onboarding_config.json
└── video/
    ├── assembled/
    ├── clips/
    ├── export/
    └── prompts/
```

The active runtime creates or repopulates additional working directories on demand, especially:

- `cast/prompts`
- `locations/prompts`
- `props/prompts`
- `frames/shot_packets`
- `frames/storyboards/{grid_id}/`
- `video/clips/normalized`
- `logs/{agent_id}/`

This matters operationally because the scaffold includes some legacy nested prompt folders, while the live prompt pipeline writes to flatter directories created at runtime.

## 6. Onboarding And Bootstrap

### 6.1 CLI inputs

`create_project.py` accepts:

- `--name`
- `--id`
- `--seed`
- `--creative-freedom`
- `--frame-budget`
- `--size` (legacy preset alias for `--frame-budget`)
- `--media-style`
- `--pipeline-type`

Accepted creative-freedom tiers:

- `strict`
- `balanced`
- `creative`
- `unbounded`

Accepted frame-budget presets via `--size`:

- `short`
- `short_film`
- `televised`
- `feature`

Accepted pipeline types:

- `story_upload`
- `pitch_idea`
- `music_video`

### 6.2 Bootstrap flow

Project bootstrap is deterministic:

1. validate creative freedom, frame budget, and media style
2. copy the template into `projects/{project_id}`
3. fill placeholder values in `project_manifest.json`
4. write a richer `source_files/onboarding_config.json`
5. optionally copy a seed file into `source_files/`

### 6.3 Onboarding config contract

The template onboarding JSON is minimal, but `create_project.py` expands it into the operational form actually consumed by the runtime. Post-bootstrap it contains:

- `projectName`
- `projectId`
- `creativeFreedom`
- `creativeFreedomPermission`
- `creativeFreedomFailureModes`
- `dialoguePolicy`
- `dialogueWorkflow`
- `frameBudget`
- `mediaStyle`
- `mediaStylePrefix`
- `pipeline`
- `aspectRatio`
- `style`
- `genre`
- `mood`
- `extraDetails`
- `sourceFiles`

Representative post-bootstrap schema:

```json
{
  "projectName": "Example Project",
  "projectId": "sw_lg_example_project_001",
  "creativeFreedom": "balanced",
  "creativeFreedomPermission": "Minor organic moments, natural pauses, slight framing tweaks, and delivery smoothing are allowed, but the source meaning and intent must stay intact.",
  "creativeFreedomFailureModes": "Dialogue can drift into sounding more natural and quietly change meaning. Prevent this by allowing only light delivery smoothing and forbidding new lines or new plot material.",
  "dialoguePolicy": "Minor re-phrasing for natural delivery only. No new lines. No added reactions. Changes must preserve exact meaning and intent.",
  "dialogueWorkflow": {"enabled": true, "version": "grok-4.2-recovery-universal"},
  "frameBudget": 125,
  "mediaStyle": "live_clear",
  "mediaStylePrefix": "live action, stark, high-contrast modern digital photography aesthetic ...",
  "pipeline": "story_upload",
  "aspectRatio": "16:9",
  "style": [],
  "genre": [],
  "mood": [],
  "extraDetails": "",
  "sourceFiles": ["source_files/pitch.md"]
}
```

### 6.4 Manifest bootstrap contract

The template manifest seeds the operational phase machine:

- `phase_0` starts `complete`
- `phase_1` starts `ready`
- phases `2-6` start `pending`

It also pre-creates empty collections:

- `cast`
- `locations`
- `props`
- `frames`

and the default `dialoguePath`:

- `dialogue.json`

### 6.5 Creative-freedom model

Creative freedom is not only a Creative Coordinator prompt hint. It is a runtime contract that persists into Phase 2 validation and manifest materialization.

The bootstrap source of truth is:

- `create_project.py:CREATIVE_FREEDOM_TIERS`
- `screenwire_contracts.py:CREATIVE_FREEDOM_CONTRACTS`

The active tiers are:

| Tier | Philosophy | Operational effect |
|---|---|---|
| `strict` | Change as little as possible to make it work. | Preserve source dialogue, blocking, props, intent, and scene progression exactly. |
| `balanced` | Follow the source closely with room for natural flow. | Allow light delivery smoothing and minor framing tweaks without changing meaning or plot. |
| `creative` | Keep the core story while allowing artistic reframes. | Allow alternative angles, visual metaphor, and short reaction lines that reinforce existing subtext. |
| `unbounded` | Start from a seed idea and fully expand into a complete story. | Allow broad invention while preserving the core emotional arc and ending. |

Important runtime facts:

- the exact permission sentence is copied into `source_files/onboarding_config.json` as `creativeFreedomPermission`
- failure-mode guardrails and dialogue rules are persisted as `creativeFreedomFailureModes` and `dialoguePolicy`
- `run_pipeline.py` threads these fields into `ProjectNode`
- `graph_validate_dialogue` enforces tier compliance during Phase 2 post-processing
- `graph/materializer.py` projects the same fields back into `project_manifest.json`

### 6.6 Output-size model

Frame budget is the bootstrap source of truth. `outputSize` is derived later from the budget for reporting and heuristics.

Preset mappings:

| Preset | Frame Budget |
|---|---:|
| `short` | 20 |
| `short_film` | 125 |
| `televised` | 300 |
| `feature` | 1250 |

They influence:

- derived `outputSize` / `outputSizeLabel`
- expected scene-count heuristics
- quality gate expectations
- likely generation cost and throughput

### 6.7 Media style baseline

Older docs sometimes call these `media types`, but the active runtime fields are `mediaStyle`, `media_style`, and `mediaStylePrefix`.

Media style is operationally enforced through `mediaStylePrefix`, not just through a label. The active lookup table is duplicated in:

- `create_project.py`
- `graph/prompt_assembler.py`

The deterministic resolution chain is:

1. `ProjectNode.media_style_prefix`
2. lookup from `ProjectNode.media_style`
3. fallback to `live_clear`

`VisualDirection.style_prefix` is deliberately not authoritative.

Canonical definitions:

- `new_digital_anime` (`New Digital Anime`): exact prefix `anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic with clean, defined linework, smooth gradient shading, and advanced photorealistic material rendering, featuring a high-contrast palette.` Operationally this means clean anime linework, smooth digital finish, and polished material rendering rather than gritty cel noise.
- `live_retro_grain` (`Live Retro Grain`): exact prefix `live action- Captured using a refined, fine-grain vintage analog film emulation, defined by diffused, shadowless studio portraiture lighting, an intentionally warm color grade saturating beige textiles and skin tones.` Operationally this means soft analog warmth, subdued contrast, and retro portrait photography language.
- `chiaroscuro_live` (`Chiaroscuro Live`): exact prefix `live action, A moody, high-contrast cinematic film aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows, and a subtle 35mm film grain.` Operationally this means live-action noir contrast, practical-light drama, and strong warm-versus-cool separation.
- `chiaroscuro_3d` (`Chiaroscuro 3d`): exact prefix `3d computer generated graphic art unreal game play render, A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient moonlight, finished with crushed black levels, enveloping heavy shadows.` Operationally this means Unreal-like rendered imagery with the same chiaroscuro palette logic as the live variant.
- `chiaroscuro_anime` (`Chiaroscuro Anime`): exact prefix `anime modern, a high-fidelity, polished 2D digital anime illustration aesthetic A moody, high-contrast aesthetic defined by dramatic chiaroscuro lighting driven by warm, glowing practical sources, featuring a rich color grade of deep crimsons and amber highlights that contrast sharply against cool, desaturated blue ambient.` Operationally this means anime draftsmanship under the darker warm-practical/cool-ambient contrast regime.
- `black_ink_anime` (`Black Ink Anime`): exact prefix `anime, gritty, 2D cel-shaded animation aesthetic defined by thick, variable-weight black ink outlines and stark, high-contrast hard shadows using pure black blocking, featuring a desaturated foreground color palette set against a stylized retro broadcast film grain.` Operationally this means heavier outlines, harder shadows, flatter blocking, and a harsher graphic-book finish.
- `live_soft_light` (`Live Soft Light`): exact prefix `live action, A bright, nostalgic 35mm cinematic film aesthetic characterized by very soft, diffused naturalistic lighting and a shallow depth of field, featuring a muted pastel color palette with creamy, pristine skin tones, finished with a gentle film grain and a warm, inviting vintage studio grade.` Operationally this means softer contrast, pastel warmth, and romantic or nostalgic live-action photography.
- `live_clear` (`Live Clear`): exact prefix `live action, stark, high-contrast modern digital photography aesthetic defined by dramatic, directional overhead spotlighting that intensely isolates the luminous subject. The color palette is strictly minimalist, emphasizing stark whites and natural warm tones that sharply contrast with the deep, light-absorbing shadows, captured with ultra-sharp clinical resolution and pristine clarity.` Operationally this means crisp digital sharpness, minimalist palettes, and hard modern studio-light isolation.

Where this actually lands:

- cast, location, prop, and final-frame image prompt builders prepend the style prefix directly
- storyboard generation carries the style as a grid-level guidance field
- the active deterministic video prompt builder does not prepend the media style text; video style is carried primarily by the composed input image and shot-packet scene detail

## 7. Runtime Services

`server.py` is the headless backend. It combines four responsibilities:

- manifest queue reconciliation
- filesystem observation and image tagging triggers
- optional agent subprocess management
- media generation endpoints

### 7.1 Required environment

- `PROJECT_DIR`
- `REPLICATE_API_TOKEN`
- `XAI_API_KEY`

`run_pipeline.py` injects `PROJECT_DIR` when it starts the server.

### 7.2 ManifestReconciler

The reconciler:

- loads `project_manifest.json` into memory
- watches `dispatch/manifest_queue/`
- strips code fences from queued JSON files
- moves malformed payloads to `dispatch/manifest_queue/dead_letters/`
- applies supported micro-update targets
- increments manifest version
- writes atomically through `project_manifest.json.tmp` and `os.replace`

Supported queued targets:

- `frame`
- `cast`
- `location`
- `prop`
- `dialogue`
- `phase`
- `project`

This is the mechanism behind `sw_queue_update`.

### 7.3 Sentinel

The sentinel watches:

- `video/clips`
- `frames/composed`
- `audio/dialogue`
- `dispatch/flags`

It also registers image-tagging observers for:

- `cast/composites`
- `locations/primary`
- `props/generated`
- `assets/active/mood`

### 7.4 AgentProcessManager

The server can spawn and message Claude CLI subprocesses through API routes, but the active phase runner normally bypasses this and spawns agents directly from `run_pipeline.py`.

### 7.5 Public HTTP routes

| Route | Purpose |
|---|---|
| `GET /health` | engine readiness |
| `GET /api/project/current` | return manifest from disk-backed in-memory state |
| `POST /api/images/tag-all` | batch tag existing reference images |
| `POST /api/agents/spawn` | spawn Claude subprocess |
| `POST /api/agents/directive` | send message to spawned agent |
| `POST /api/agents/kill` | kill one agent |
| `POST /api/agents/kill-all` | kill all agents |
| `GET /api/agents/{agent_id}/status` | agent liveness |

### 7.6 Internal generation routes

These are the active internal operations surface:

| Route | Request Model | Primary Use |
|---|---|---|
| `POST /internal/generate-image` | `GenerateImageRequest` | cast-style reference generation via `CastImageHandler`; reused for props and generic refs |
| `POST /internal/generate-frame` | `GenerateFrameRequest` | final frame generation |
| `POST /internal/generate-location-direction` | `GenerateLocationDirectionRequest` | derive alternate location views from a primary reference |
| `POST /internal/edit-image` | `EditImageRequest` | nano-banana image editing |
| `POST /internal/fresh-generation` | `FreshGenerationRequest` | high-quality image generation from scratch |
| `POST /internal/generate-video` | `GenerateVideoRequest` | Grok video clip generation |
| `POST /internal/generate-location-grid` | `GenerateLocationGridRequest` | directional location grid generation |
| `POST /internal/generate-storyboard` | `GenerateStoryboardRequest` | storyboard composite + cell extraction |
| `POST /internal/upload-to-replicate` | `UploadToReplicateRequest` | expose upload helper |
| `POST /internal/refine-video-prompts` | `RefineVideoPromptsRequest` | Grok vision refinement pass |

Batch variants also exist for image, frame, video, location-grid, and storyboard generation. All batch endpoints enforce `max_concurrent <= 10`.

### 7.7 Important server-level caveats

- `upload_to_replicate` in the handler layer uses data URIs, not the Replicate file-upload route.
- The explicit `/internal/upload-to-replicate` route exists for callers that need a URL-based upload workflow.
- `/internal/generate-image` always routes through `CastImageHandler`; operationally, that means props and generic references inherit p-image semantics.
- `/internal/generate-location-direction` exists, but the active phase runner does not call it.

## 8. CLI Skill Surface

The skills folder is the primary operational tool belt for agents and the runner.

### 8.1 State and manifest skills

| Skill | Purpose |
|---|---|
| `sw_read_manifest` | read condensed project state |
| `sw_queue_update` | write manifest micro-update files |
| `sw_update_state` | merge agent state files |

### 8.2 Direct media skills

| Skill | Purpose |
|---|---|
| `sw_generate_image` | p-image gateway wrapper |
| `sw_generate_frame` | final frame gateway wrapper |
| `sw_generate_video` | Grok video gateway wrapper |
| `sw_edit_image` | nano-banana edit wrapper |
| `sw_fresh_generation` | nano-banana high-quality generation wrapper |
| `skill_extract_last_frame` | ffmpeg last-frame extraction |
| `skill_verify_media` | ffprobe verification |

### 8.3 Graph skills

| Skill | Purpose |
|---|---|
| `graph_init` | initialize graph from onboarding config |
| `graph_query` | query nodes or frame context |
| `graph_upsert` | upsert nodes and frame states |
| `graph_edge` | create or close edges |
| `graph_propagate` | copy-and-mutate state snapshots |
| `graph_validate_contracts` | validate graph invariants |
| `graph_build_grids` | build storyboard grids |
| `graph_assemble_prompts` | assemble prompt JSON artifacts |
| `graph_materialize` | export graph projections |
| `graph_generate_assets` | programmatic base asset generation |
| `graph_sync_assets` | sync generated paths into graph and manifest |
| `graph_validate_video_direction` | validate or fix duration/directorial completeness |
| `graph_merge_overlays` | merge overlay graphs |
| `graph_run`, `graph_batch` | legacy orchestration helpers |

## 9. End-To-End Operational Sequence

The authoritative lifecycle lives in `run_pipeline.py`.

### 9.1 Phase 0: scaffold verification

Checks:

- `project_manifest.json` exists and is valid JSON
- `phase_0.status == complete`
- `source_files/onboarding_config.json` exists and parses
- at least one source file resolves from `sourceFiles`
- core directories exist

### 9.2 Phase 1: narrative generation

Phase 1 is three operations, not one:

1. Creative Coordinator skeleton-only run
2. parallel Grok prose workers, one per scene
3. Creative Coordinator assembly-only run

Important runtime details:

- the runner injects a "skeleton only" override into the CC prompt
- scene count is inferred heuristically from `outline_skeleton.md`
- prose workers are hard-overridden to write only `creative_output/scenes/scene_{NN}_draft.md`
- if `creative_output/creative_output.md` already exists and is non-trivial, the runner skips assembly instead of spawning another CC assembly pass

Outputs:

- `creative_output/outline_skeleton.md`
- `creative_output/scenes/scene_{NN}_draft.md`
- `creative_output/creative_output.md`

Quality gate:

- verifies skeleton exists
- expects `creative_output.md`
- warns if too small
- expects at least two scene drafts

Execution gap:
the gate may over-warn on a valid one-scene short project because the heuristic minimum is two drafts.

### 9.3 Phase 2: deterministic graph construction

Phase 2 sequence:

1. `graph_init` if `graph/narrative_graph.json` does not exist
2. build the shared context seed
3. run `graph/cc_parser.py` to construct the base `NarrativeGraph`
4. run `graph/frame_enricher.py` in parallel for per-frame composition, environment, and directing enrichment
5. run `graph/grok_tagger.py` to assign `CinematicTag` metadata
6. run `graph/continuity_validator.py` for deterministic continuity and contract checks
7. run deterministic post-processing:
   - `graph_assemble_prompts`
   - `graph_validate_dialogue`
   - `prompt_pair_validator`
   - reconcile scene cast presence
   - `graph_materialize`
   - `graph_validate_video_direction --fix`

Outputs:

- `graph/narrative_graph.json`
- `dialogue.json`
- prompt JSONs
- flat cast/location/prop profiles
- updated manifest projections

Quality gate:

- checks manifest has frames
- checks graph/manifest cast integrity
- checks dialogue exists
- checks flat profiles exist
- hard-fails on dialogue recovery or creative-freedom tier violations during `graph_validate_dialogue`

Heuristic caveat:
the Phase 2 gate expects at least two cast profiles, which may over-warn on intentionally single-character stories.

### 9.4 Phase 3: asset generation and storyboards

Phase 3 is fully programmatic:

1. start image tag watcher
2. run `graph_generate_assets --skip-existing --types cast,locations,props`
3. batch tag all reference images
4. run programmatic image integrity checks
5. sync generated asset paths into graph and manifest via `graph_sync_assets`
6. re-assemble prompts
7. run programmatic asset validation and regeneration
8. sync assets again
9. build storyboard grids
10. assemble storyboard prompt JSONs
11. verify storyboard references are tagged
12. generate storyboard composites sequentially
13. split cells
14. match shots in each grid
15. re-assemble prompts so frame prompts can include storyboard references
16. refresh manifest with storyboard metadata

The phase explicitly regenerates missing or corrupt assets via `sw_fresh_generation` with up to two attempts.

Execution gaps:

- the authored `image_verifier` prompt is no longer the active Phase 3 operator; `run_pipeline.py` performs a programmatic replacement for asset review and regeneration
- regeneration prefers `size`, warns on legacy `image_size`, and rejects conflicting values before proceeding

### 9.5 Phase 4: final frame composition

Phase 4 is intentionally sequential.

The reason is not throughput but continuity: the previous composed frame can become a live reference for the next one.

Phase 4 sequence:

1. audit Phase 3 assets
2. rebuild storyboard grids if graph has none
3. regenerate missing cast/location/prop assets if necessary
4. proceed even if storyboard guidance is partially missing
5. for each frame in order:
   - assemble deterministic image prompt
   - resolve references
   - call `sw_generate_frame`
6. sync composed frame paths into graph and manifest
7. re-assemble prompts so video prompts point at real frame images

Execution gaps:

- the authored `composition_verifier` prompt describes the intended quality-gate agent, but the active runner performs Phase 4 directly in code rather than by invoking that prompt
- `phase_4_production_parallel()` exists only as an API-compatibility shim and delegates straight back to the sequential implementation

### 9.6 Phase 5: video generation

Phase 5 is a pipelined refine-then-generate system.

Per frame:

1. load `{frame_id}_video.json`
2. refine prompt against the actual composed image through Grok vision if not already refined
3. write refined prompt back to disk
4. immediately queue clip generation
5. generate `video/clips/{frame_id}.mp4`

Concurrency:

- refinement workers: `5`
- generation workers: `10`

Operational rule:
`XAI_API_KEY` enables Grok vision refinement when present. If it is absent, the runner now logs a warning and generates clips from the graph-assembled prompt without refinement.

Execution gaps:

- the authored `video_verifier` prompt is not the active Phase 5 operator; the runner performs refinement and clip generation programmatically
- long video prompts are reduced to fit model limits by section dropping and then hard truncation, which preserves operability but not always semantic cleanliness

### 9.7 Phase 6: export

Export is deterministic and implemented directly in `run_pipeline.py`.

Sequence:

1. build ordered clip list from manifest `frames[]`
2. normalize every clip to a common H.264/AAC 1280x720 24fps format
3. inject silent audio when a clip has no audio stream
4. write `video/assembled/concat_list.txt`
5. stitch a draft export with `ffmpeg -f concat -c copy`
6. run `loudnorm` to produce the final export
7. verify with `ffprobe`
8. close the manifest and mark project complete

The export step accepts several clip naming patterns:

- `{frame_id}.mp4`
- `{sequenceIndex}_{frame_id}.mp4`
- chunked clip patterns containing the frame id

## 10. Creative Coordinator Process

The Creative Coordinator is the narrative architect for Phase 1.

### 10.1 Files and state

Owned folder:

- `logs/creative_coordinator/`

Expected files:

- `state.json`
- `directive.json`
- `events.jsonl`
- `context.json`

### 10.2 Inputs

The CC prompt explicitly requires reading:

- every file in `source_files/`
- `source_files/onboarding_config.json`
- optional `logs/director/project_brief.md`

### 10.3 Available skills

- `sw_read_manifest`
- `sw_queue_update`
- `sw_update_state`

### 10.4 Three sub-phases

The authored CC prompt defines:

- `ARCHITECT`
- `PROSE`
- `ASSEMBLY`

The active runner, however, forces them into three separate operational passes:

- skeleton only
- scene writing only
- assembly only

### 10.5 Skeleton contract

The skeleton is a dispatchable construction spec, not a loose outline.

It must contain:

- story premise
- character roster
- wardrobe baselines
- location roster
- cardinal direction views
- act/arc summary
- thematic through-lines
- per-scene entry conditions
- numbered beats with camera direction
- dialogue gist
- exit conditions
- continuity carries forward
- scene visual requirements
- continuity chain summary

### 10.6 Frame marker protocol

The prose format is operationally machine-readable:

```text
/// cast:{names} | cam:{direction} | dlg | dur:{seconds}
```

These markers are consumed downstream as:

- visible cast hints
- `FrameBackground.camera_facing`
- `FrameNode.is_dialogue`
- initial `suggested_duration`
- frame boundaries

### 10.7 Writing guide contract

The writing guide enforces:

- one paragraph equals one story atom equals one frame
- no long runs of pure dialogue without visual business
- environment before character when establishing a beat
- visual flow built from motion, dialogue, reaction, action, weight, and establishment

Operational consequence:
if Phase 1 prose is weak, every later deterministic system receives bad inputs and can only fail gracefully, not artistically recover.

### 10.8 Authored agent prompt inventory

The `agent_prompts/` directory now mixes active prompt contracts with historical design references from the retired Morpheus path.

Active prompt contracts:

- `agent_prompts/creative_coordinator.md`: the Phase 1 authoring prompt. It defines the CC as a three-pass architect, prose, and assembly agent; binds it to the persisted `creativeFreedomPermission` / `dialoguePolicy` contract; forces `///` frame markers; and makes paragraph count a direct cost lever because one paragraph becomes one downstream frame.
- `agent_prompts/writing_guide.md`: the prose-construction companion prompt. It defines the six visual-flow elements, the 18 formula tags, setup/payoff alternation, dialogue-with-physical-business rules, cardinal camera-direction discipline, and paragraph-level atomization expectations.

Historical / superseded prompt specs:

- `agent_prompts/morpheus_shared.md` and `agent_prompts/morpheus_1_*` through `morpheus_5_*`: these document the retired multi-agent Phase 2 design. The active runtime now uses `graph/cc_parser.py`, `graph/frame_enricher.py`, `graph/grok_tagger.py`, and `graph/continuity_validator.py` instead of spawning Morpheus agents.
- `agent_prompts/composition_verifier.md`, `video_verifier.md`, and `image_verifier.md`: these describe intended quality-agent behavior, but the active runner performs the corresponding work programmatically.

Legacy or secondary prompts:

- `agent_prompts/director.md`: documents a higher-level approval agent and phase-review workflow. The current runner uses some of its mental model but usually bypasses a real Director phase.

Prompt execution reality:

- the authored prompts still define Phase 1 writing intent and several artifact contracts
- `run_pipeline.py` decides which prompt is actually invoked, in which mode, and with what runtime override
- Phase 2 graph construction is now code-driven rather than prompt-driven
- several prompt documents remain as design history and should not be read as the current executor path

## 11. Deterministic Graph Construction Process

Phase 2 is a code-driven graph build with deterministic cleanup.

### 11.1 Shared context seed

`run_pipeline.py` builds one cacheable prefix embedding:

- `project_manifest.json`
- `source_files/onboarding_config.json`
- `creative_output/outline_skeleton.md`
- `creative_output/creative_output.md`
- optional director brief

### 11.2 Step 2a: CC parser

The parser seeds:

- `ProjectNode`
- `WorldContext`
- `VisualDirection`
- `CastNode`
- `LocationNode`
- `PropNode`
- `SceneNode`
- early relationship edges

Important rules:

- every cast member needs baseline wardrobe
- every location needs `location_type`
- location direction views become downstream spatial anchors

### 11.3 Step 2b: frame enricher

The frame enricher consumes the parser output and populates:

- `FrameComposition`
- `FrameDirecting`
- `FrameEnvironment`
- `FrameBackground`
- enriched `CastFrameState`, `PropFrameState`, and `LocationFrameState`

### 11.4 Step 2b.5: Grok tagger

The tagger assigns one `CinematicTag` per frame after enrichment so prompt assembly can inject explicit cinematic direction instead of the old formula-tag language.

### 11.5 Step 2c: continuity and dialogue validation

Deterministic validators finalize coherence by:

- checking continuity
- checking dialogue coverage
- checking prompt-pair consistency
- validating video direction completeness
- enforcing the active creative-freedom tier against dialogue assignments

### 11.6 Step 2d: deterministic post-processing

After parser/enricher/tagger/validator execution, the runtime always follows with deterministic operations:

- prompt assembly
- dialogue validation
- scene cast reconciliation
- materialization
- video-direction validation or duration repair

This is where graph state becomes runnable prompt and filesystem state.

## 12. Graph Persistence And Overlay Strategy

`graph/store.py` is a JSON-backed atomic store.

### 12.1 Base graph

- path: `graph/narrative_graph.json`
- save behavior: temp file then atomic replace
- every save re-validates against `NarrativeGraph`

### 12.2 Overlay graphs

Parallel agents write separate overlays such as:

- `graph/overlay_dialogue.json`
- `graph/overlay_composition.json`

### 12.3 Merge semantics

Overlay merge is additive with non-empty overlay fields winning:

- new registry keys are appended
- populated overlay fields overwrite base values
- edges are deduplicated by canonical `(source, target, edge_type)`
- order lists are extended without duplication

This is the operational safety mechanism that permits Agents 3 and 4 to run in parallel.

## 13. Narrative Graph Schema

### 13.1 Top-level graph

`NarrativeGraph` contains:

- `project`
- `world`
- `visual`
- `cast`
- `locations`
- `props`
- `scenes`
- `frames`
- `dialogue`
- `storyboard_grids`
- `cast_frame_states`
- `prop_frame_states`
- `location_frame_states`
- `edges`
- `frame_order`
- `scene_order`
- `dialogue_order`
- `seeded_domains`
- `frame_completeness`
- `total_tokens_used`
- `build_log`

### 13.2 Global nodes

| Node | Operational role |
|---|---|
| `ProjectNode` | onboarding-supplied project identity and creative settings |
| `WorldContext` | global world synthesis |
| `VisualDirection` | project-level style and mood direction |

### 13.3 Entity nodes

| Node | Key fields |
|---|---|
| `CastNode` | identity, voice, role, arc, relationships, composite tracking, state variants |
| `LocationNode` | description, atmosphere, location_type, direction views, scene usage, primary image tracking |
| `PropNode` | description, significance, material context, associated cast, introduction frame, image path |

### 13.4 Story nodes

| Node | Key fields |
|---|---|
| `SceneNode` | heading, cast present, props present, pacing, tension, frame range |
| `FrameNode` | formula tag, narrative beat, source text, state links, environment, composition, background, directing, suggested duration, action summary, continuity links, output paths |
| `DialogueNode` | speaker, cast_id, order, start/end frame, primary visual frame, reaction frames, line, raw_line, performance/environment tags |
| `StoryboardGrid` | frame batch, layout, break reason, continuity chain, generated composite/cell paths, shot match groups |

### 13.5 Absolute snapshot state nodes

The system explicitly stores frame states as absolute snapshots, not deltas.

| State node | Purpose |
|---|---|
| `CastFrameState` | what a character looks like and is doing at one frame |
| `PropFrameState` | what a prop is, where it is, and what condition it is in at one frame |
| `LocationFrameState` | how a location is modified relative to its base description at one frame |

This design avoids backward delta walks during prompt assembly.

### 13.6 Prompt-support nodes

The graph also defines support packets for deterministic prompt building:

- `ShotNeighborBeat`
- `ShotAudioTurn`
- `ShotAudioBeat`
- `ShotIntent`
- `ShotPacket`
- `ShotMatchGroup`

### 13.7 Provenance contract

Every node and edge carries `Provenance` with:

- `source_prose_chunk`
- `chunk_index`
- `generated_by`
- `confidence`
- `created_at`
- `last_modified_at`
- `last_modified_by`
- `supersedes`

Persisted graph data without `source_prose_chunk` fails contract validation.

## 14. Edge Catalog

Edge identifiers are canonical:

```text
{source_id}__{edge_type}__{target_id}
```

### 14.1 Frame linkage edges

- `APPEARS_IN`
- `AT_LOCATION`
- `USES_PROP`
- `DIALOGUE_SPANS`
- `SPOKEN_BY`
- `FOLLOWS`
- `CONTINUITY_CHAIN`

### 14.2 Entity relationship edges

- `CO_OCCURRENCE`
- `DIALOGUE_EXCHANGE`
- `POSSESSION`
- `CONTAINMENT`
- `AUTHORITY`
- `CONFLICT`
- `KINSHIP`
- `ALLIANCE`
- `AVERSION`

### 14.3 Hierarchy edges

- `CHILD_OF`
- `BELONGS_TO_SCENE`
- `SCENE_IN_ACT`

### 14.4 Temporal edge behavior

Edges can also carry:

- `start_frame`
- `end_frame`

This is especially relevant for possession and time-scoped relationships.

## 15. Graph API And Graph Operations

### 15.1 Query operations

Core read helpers:

- `query_graph()`
- `get_frame_context()`
- `build_shot_packet()`
- `get_frame_cell_image()`
- `match_shots_in_grid()`

`get_frame_context()` prefers the flat state registries over embedded frame copies. The tests lock this behavior in place.

### 15.2 Mutation operations

Core write helpers:

- `upsert_node()`
- `upsert_frame_state()`
- `create_edge()`
- `close_temporal_edge()`
- `propagate_cast_state()`
- `propagate_prop_state()`
- `propagate_location_state()`

### 15.3 Continuity and recovery operations

- `check_continuity()`
- `check_dialogue_ordering()`
- `trace_provenance()`
- `prune_and_revert()`

Continuity checks cover:

- possession continuity
- speaker presence in sync frames
- bidirectional frame links
- continuity-chain validity
- monotonic sequence ordering
- missing `FOLLOWS` edges
- dialogue span and speaker-link consistency

### 15.4 Storyboard operations

- `build_storyboard_grids()`
- `_storyboard_break_reason()`
- `match_shots_in_grid()`

Storyboard grids break on:

- scene change
- dialogue turn change
- visible cast set change
- prop state change
- camera shift
- background shift
- large visual shift
- grid reaching maximum size

## 16. Deterministic Prompt Assembly

Once the graph exists, prompt building is deterministic. No LLM is involved.

Core functions:

- `assemble_image_prompt()`
- `assemble_video_prompt()`
- `assemble_composite_prompt()`
- `assemble_location_prompt()`
- `assemble_prop_prompt()`
- `assemble_grid_storyboard_prompt()`
- `assemble_all_prompts()`

### 16.1 Prompt section model

The assembled prompts are sectioned rather than freeform:

- lead lines
- `SHOT INTENT`
- `CONTINUITY`
- `SUBJECT COUNT`
- `CAST INVARIANTS`
- `PROP INVARIANTS`
- `LOCATION INVARIANTS`
- `DIALOGUE COVERAGE`
- `BLOCKING`
- `BACKGROUND`
- `MOTION CONTINUITY`
- `AUDIO` or `AUDIO CONTEXT`
- `NEGATIVE CONSTRAINTS`

### 16.2 Negative constraints

The deterministic builders reinforce these invariants:

- do not add or remove cast, props, wardrobe, architecture, or light sources
- no subtitles, captions, speech bubbles, lyric text, labels, watermarks, or UI
- coherent anatomy, hands, faces, object scale, and physics
- enforce subject-count ceilings

### 16.3 Reference resolution order

`resolve_ref_images()` builds the final frame reference list in this order:

1. storyboard cell image for the frame, else grid composite fallback
2. previous composed frame if continuity chain is active
3. cast composites or active state-variant images
4. location primary image
5. up to three prop reference images

This is the core continuity stack for final-frame generation.

### 16.4 Shared prompt-builder logic stack

All deterministic prompt builders sit on the same helper stack in `graph/prompt_assembler.py`.

Common builder flow:

1. `get_frame_context()` resolves the frame, scene, and visible state registries
2. `build_shot_packet()` constructs the canonical packet used by downstream builders
3. `_resolve_style_prefix()` picks the authoritative media style prefix from project onboarding, falling back only if needed
4. `_shot_intent_lines()`, `_continuity_lines()`, and frame/state helper functions convert the packet into section-ready bullet strings
5. `_build_dialogue_coverage()` derives per-frame dialogue coverage role metadata when audio is present
6. `_dialogue_coverage_lines()` converts those roles into prose instructions tailored separately for image and video prompts
7. `_negative_constraints()` applies the invariant block that protects cast count, wardrobe, props, architecture, and text-free output
8. `_assemble_prompt_sections()` serializes the final ordered section stack

Operational implication:

- the image, storyboard, and video builders are not freewriting systems
- they are serializations of graph state into highly repeatable section templates
- when prompts look weak, the usual root cause is missing graph data rather than bad prompt prose

## 17. Prompt Artifact Schemas

These files are the active prompt payloads on disk.

### 17.1 Shot packet JSON

Written to:

- `frames/shot_packets/{frame_id}.json`

Representative fields:

```json
{
  "frame_id": "f_002",
  "scene_id": "scene_01",
  "sequence_index": 2,
  "subject_count": 1,
  "visible_cast_ids": ["cast_nova"],
  "visible_prop_ids": ["prop_signal_pager"],
  "continuity_deltas": ["..."],
  "cast_invariants": ["..."],
  "prop_invariants": ["..."],
  "location_invariants": ["..."],
  "blocking": ["..."],
  "background": ["..."],
  "shot_intent": {"formula_tag": "F04", "...": "..."},
  "audio": {"dialogue_present": true, "turns": [], "ambient_layers": []}
}
```

#### 17.1.1 Minimum valid shot packet requirements

The video builder no longer treats sparse packets as a soft degradation case.
The minimum packet fields below are the practical boundary between a healthy
packet and a hard stop.

| Field | Source Node | Runtime use | Consequence if missing |
|---|---|---|---|
| `action_summary` | `FrameNode` | First lead-line action statement in image/video prompt | Phase 2 video-direction validator fails |
| `composition.shot` | `FrameComposition` | `SHOT INTENT` and video lead-line shot type | Phase 2 validator fails; `assemble_video_prompt()` raises |
| `composition.angle` | `FrameComposition` | `SHOT INTENT` framing angle | Phase 2 validator fails; `assemble_video_prompt()` raises |
| `composition.movement` | `FrameComposition` | Video lead-line camera motion and `SHOT INTENT` | Phase 2 validator fails; `assemble_video_prompt()` raises |
| `directing.dramatic_purpose` | `FrameDirecting` | Explains why the shot exists | Phase 2 validator fails |
| `directing.beat_turn` | `FrameDirecting` | Defines what changes by clip end | Phase 2 validator fails |
| `directing.camera_motivation` | `FrameDirecting` | Explains why this framing/movement is correct | Phase 2 validator fails |

Important nuance:

- `dialogue_coverage_role` is not a stored field on `FrameDirecting` in the active runtime
- it is derived during prompt assembly from `DialogueNode.start_frame`, `end_frame`, `primary_visual_frame`, and `reaction_frame_ids`
- operators should therefore debug dialogue coverage through dialogue-span metadata, not by searching for a missing directorial enum on the frame node

Representative valid packet excerpt:

```json
{
  "frame_id": "f_002",
  "action_summary": "Nova reads the pager warning and realizes the watchers moved early.",
  "background": {
    "camera_facing": "east",
    "visible_description": "the district skyline blinking beyond the ledge"
  },
  "composition": {
    "shot": "medium close-up",
    "angle": "eye_level",
    "movement": "slow_push",
    "focus": "prop"
  },
  "directing": {
    "dramatic_purpose": "pivot from surveillance into immediate threat",
    "beat_turn": "Nova understands the watchers are moving early",
    "camera_motivation": "Push toward the pager as the warning lands",
    "movement_motivation": "Let Nova's hands absorb the shock of the alert"
  }
}
```

Mechanical consequences of incomplete frame nodes:

- `FrameComposition` missing `shot`, `angle`, or `movement`: the packet is treated as invalid and video prompt assembly stops
- `FrameDirecting` missing `dramatic_purpose`, `beat_turn`, or `camera_motivation`: Phase 2 validation stops the pipeline before media generation
- `FrameBackground.camera_facing` missing: the packet may still build, but spatial grounding weakens and scene-level camera-facing monotony diagnostics lose signal
- `FrameBackground.background_sound` or `background_music` missing: those cues simply disappear from the audio/environment lines; no fallback audio is synthesized
- `FrameEnvironment` missing detail: the prompt remains mechanically valid but loses environmental specificity in `BACKGROUND` and continuity lines

#### 17.1.2 Sparse packet diagnostic criteria

The active runtime treats a packet as operationally sparse when any required
video-direction field is absent.

Current binary checklist:

```text
SHOT PACKET VALIDITY CHECKLIST
------------------------------
□ FrameNode.action_summary is populated
□ FrameComposition.shot is populated
□ FrameComposition.angle is populated
□ FrameComposition.movement is populated
□ FrameDirecting.dramatic_purpose is populated
□ FrameDirecting.beat_turn is populated
□ FrameDirecting.camera_motivation is populated

If ANY box is unchecked:
- Phase 2 validation fails, or
- assemble_video_prompt() raises an explicit incomplete-shot-packet error

The active runtime no longer silently falls back to generic FORMULA_VIDEO
defaults for sparse packets.
```

Operator note:

- `camera_facing` is a recommended spatial-grounding field, not part of the current hard-stop minimum
- missing `camera_facing` can still produce flatter or less traceable geography, but it does not by itself trigger the sparse-packet halt

#### 17.1.3 Audio payload sub-schemas

The prompt builder serializes native-audio context from `ShotAudioTurn` and
`ShotAudioBeat`. These are not speculative helper objects; they are the
runtime structures feeding the `AUDIO:` section.

Representative `ShotAudioTurn`:

```json
{
  "dialogue_id": "dlg_001",
  "cast_id": "cast_nova",
  "speaker": "Nova",
  "line": "They're early.",
  "performance_direction": "guarded, clipped",
  "env_intensity": "urgent",
  "env_distance": "close",
  "env_medium": "radio",
  "env_atmosphere": ["wind", "static"]
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `dialogue_id` | string | Yes | Dialogue node backing this audible turn |
| `cast_id` | string | Yes | Speaking cast member |
| `speaker` | string | Yes | Display speaker label used in prompt serialization |
| `line` | string | Yes | Spoken line carried into the `AUDIO:` section |
| `performance_direction` | string | No | Freeform performance hint extracted from dialogue metadata |
| `env_intensity` | string or null | No | Loudness/energy cue used in delivery/timing inference |
| `env_distance` | string or null | No | Distance cue for delivery texture |
| `env_medium` | string or null | No | Medium cue such as `radio`, `phone`, `muffled` |
| `env_atmosphere` | array of strings | No | Additional environmental audio textures |

Representative `ShotAudioBeat`:

```json
{
  "dialogue_present": true,
  "turns": [
    {
      "dialogue_id": "dlg_001",
      "cast_id": "cast_nova",
      "speaker": "Nova",
      "line": "They're early.",
      "performance_direction": "guarded, clipped",
      "env_intensity": "urgent",
      "env_distance": "close",
      "env_medium": "radio",
      "env_atmosphere": ["wind", "static"]
    }
  ],
  "ambient_layers": ["wind over the ledge", "distant traffic wash"],
  "background_music": null
}
```

| Field | Type | Required | Description |
|---|---|---|---|
| `dialogue_present` | boolean | Yes | Indicates whether spoken dialogue should appear in `AUDIO:` |
| `turns` | array of `ShotAudioTurn` | Yes | Audible dialogue turns for the frame |
| `ambient_layers` | array of strings | Yes | Non-musical ambient cues |
| `background_music` | string or null | No | Diegetic or explicitly authored background music cue |

### 17.2 Final frame image prompt JSON

Written to:

- `frames/prompts/{frame_id}_image.json`

Representative shape:

```json
{
  "frame_id": "f_002",
  "scene_id": "scene_01",
  "sequence_index": 2,
  "prompt": "structured final-frame prompt ...",
  "ref_images": ["frames/storyboards/grid_01/f_002.png", "cast/composites/cast_nova_ref.png"],
  "size": "landscape_16_9",
  "out_path": "frames/composed/f_002_gen.png",
  "formula_tag": "F04",
  "style_prefix_used": "live action, ...",
  "shot_packet_path": "frames/shot_packets/f_002.json",
  "dialogue_present": true,
  "dialogue_coverage_roles": ["speaker_sync"],
  "directing": {}
}
```

### 17.3 Video prompt JSON

Written to:

- `video/prompts/{frame_id}_video.json`

Representative shape:

```json
{
  "frame_id": "f_002",
  "scene_id": "scene_01",
  "sequence_index": 2,
  "prompt": "structured Grok video prompt ...",
  "duration": 4,
  "recommended_duration": 3.7,
  "duration_reason": "dialogue_span_allocation",
  "dialogue_fit_status": "span_allocated",
  "target_api": "grok-video",
  "input_image_path": "frames/composed/f_002_gen.png",
  "dialogue_line": "They're early.",
  "voice_delivery": "guarded, measured ...",
  "dialogue_pacing": "measured",
  "voice_tempo": "measured",
  "action_summary": "Nova delivers the warning directly to Rafe.",
  "formula_tag": "F04",
  "shot_type": "close_up",
  "camera_motion": "static",
  "shot_packet_path": "frames/shot_packets/f_002.json",
  "dialogue_present": true,
  "dialogue_coverage_roles": ["speaker_sync"]
}
```

### 17.4 Cast prompt JSON

Written to:

- `cast/prompts/{cast_id}_composite.json`

Contains:

- `cast_id`
- `prompt`
- `size`
- `out_path`

### 17.5 Location prompt JSON

Written to:

- `locations/prompts/{location_id}_location.json`

Contains:

- `location_id`
- `prompt`
- `template_type`
- `size`
- `out_path`

### 17.6 Prop prompt JSON

Written to:

- `props/prompts/{prop_id}_prop.json`

Contains:

- `prop_id`
- `prompt`
- `size`
- `out_path`

### 17.7 Storyboard prompt JSON

Written to:

- `frames/storyboard_prompts/{grid_id}_grid.json`

Representative shape:

```json
{
  "grid_id": "grid_01",
  "grid": "2x2",
  "style_prefix": "live action, ...",
  "cell_prompts": ["...", "..."],
  "scene": "[Cell 1] ...\n[Cell 2] ...",
  "refs": ["cast/composites/cast_nova_ref.png", "locations/primary/loc_rooftop.png"],
  "guidance_only": true,
  "output_dir": "frames/storyboards/grid_01",
  "cell_map": {"0": "f_001", "1": "f_002"},
  "frame_ids": ["f_001", "f_002"]
}
```

## 18. Image Prompt Builders And Image Generation Patterns

### 18.1 Final frame prompt pattern

`assemble_image_prompt()` produces a prompt shaped like:

```text
{style_prefix}Generate one final cinematic frame.
This is a single finished image, not a storyboard grid or contact sheet.
{action_summary_or_current_beat}
Mood: ...

SHOT INTENT:
- ...

CONTINUITY:
- ...

...

AUDIO CONTEXT:
- Dialogue is happening in this frame; preserve the speaking beat through expression and body language only.
- Do not render subtitles...

NEGATIVE CONSTRAINTS:
- ...
```

Builder logic:

1. load frame context and canonical shot packet
2. resolve the authoritative media style prefix
3. resolve continuity refs in deterministic order through `resolve_ref_images()`
4. derive dialogue-coverage roles if the frame sits inside a spoken span
5. build lead lines from style prefix, single-frame warning, action summary, and optional scene mood
6. build `AUDIO CONTEXT` as either dialogue-performance-only guidance or a no-dialogue statement
7. serialize the standard section stack with `_assemble_prompt_sections()`

Template skeleton:

```text
{style_prefix}Generate one final cinematic frame.
This is a single finished image, not a storyboard grid or contact sheet.
{frame.action_summary or packet.current_beat}
Mood: {scene mood keywords}

SHOT INTENT:
- {formula_tag} | {shot} | {angle} angle | {movement} | focus on {focus}
- {dramatic purpose}
- {beat turn}
- POV owner: ...
- Viewer learns: ...

CONTINUITY:
- Previous beat: ...
- Current beat: ...
- Next beat: ...
- {continuity delta}

SUBJECT COUNT:
- Exactly {n} visible subject(s).

CAST INVARIANTS:
- {identity / wardrobe / pose continuity line}

PROP INVARIANTS:
- {prop continuity line}

LOCATION INVARIANTS:
- {environment continuity line}

DIALOGUE COVERAGE:
- {speaker or listener role line}
- {coverage rule}

BLOCKING:
- {staging line}

BACKGROUND:
- {camera-facing world content}

AUDIO CONTEXT:
- Dialogue is happening in this frame; preserve the speaking beat through expression and body language only.
- Do not render subtitles, captions, lyric text, speech bubbles, or visible dialogue words.

NEGATIVE CONSTRAINTS:
- {invariant block}
```

Operational notes:

- dialogue text itself is not included in image prompts
- storyboard refs are passed as `ref_images`, not as part of the text prompt
- the output target is always `frames/composed/{frame_id}_gen.png`

### 18.2 Cast composite prompt pattern

`assemble_composite_prompt()` builds a full-body cast reference prompt from:

- media style prefix
- demographic signals
- hair and skin descriptors
- wardrobe description
- neutral background and studio-like lighting guidance

Pattern:

```text
{style_prefix}Full body character portrait, head to toe visible.
{age} {ethnicity} {gender} {build}.
{hair}.
{skin}.
Wearing {wardrobe}.
Footwear: ...
Neutral dark background, soft key light from upper left with rim light from behind, three-quarter view, standing pose.
```

### 18.3 Location prompt pattern

`assemble_location_prompt()` produces a single coherent environmental reference prompt and also carries the `template_type` used by the location-grid handler.

Pattern:

```text
{style_prefix}Cinematic wide establishing shot.
{location name}: {description}.
{atmosphere}.
Mood: ...
Single coherent reference image only. No split panels, no labels, no collage, no character presence.
Spatial orientation anchors for later camera-facing continuity: north: ...; south: ...
```

Builder logic:

1. resolve `LocationNode`
2. prepend the authoritative media style prefix
3. emit location name plus description as the lead environmental sentence
4. append atmosphere if present
5. pull mood from the first scene that uses the location
6. append the hard constraint that the result must be a single coherent environmental reference with no cast presence
7. serialize any available north/south/east/west descriptions into one spatial-anchor sentence
8. map `location_type` to `template_type` for the location-grid handler, allowing only `interior` or `exterior`

Operational note:

- the single-image location prompt is the canonical reference still
- the directional 2x2 location grid is a downstream generation strategy layered on top of this same base prompt, not a separate authored scene description

### 18.4 Prop prompt pattern

`assemble_prop_prompt()` builds a centered product-shot style prompt:

```text
{style_prefix}Detailed product-shot style image of {prop name}.
{description}.
Centered composition, clean presentation, slight dramatic lighting.
```

### 18.5 Storyboard grid prompt pattern

The grid prompt comes from `graph/grid_generate.py` and is guidance-only:

```text
Create an image divided into a precise {cols}x{rows} grid following the attached reference layout exactly.
Clean straight thin black dividing lines between cells. Equal cell sizes.
No text, no labels, no watermarks, no numbers.
Global visual style for all cells: ...
Each cell is a sequential cinematic guidance frame used only for continuity planning.
Preserve cast identity, wardrobe, props, architecture, lighting direction, and staging from cell to cell.
Fill each cell exactly as described below...
```

There are two layers in the storyboard path.

Layer 1: `assemble_grid_storyboard_prompt()` writes the storyboard prompt JSON:

1. select all valid frame ids already assigned to the grid
2. build one compact cell prompt per frame with `_build_cell_prompt()`
3. gather refs in deterministic order:
   - previous grid composite if present
   - cast composites
   - location primary images
   - up to two prop refs per frame
4. carry `style_prefix` as a grid-level field instead of repeating it per cell
5. write `frame_ids`, `cell_map`, `refs`, and `scene` to `frames/storyboard_prompts/{grid_id}_grid.json`

Layer 2: `graph/grid_generate.py` converts that JSON into the model-facing template:

1. infer whether every cell prompt starts with the same style clause
2. hoist that shared clause into `Global visual style for all cells: ...`
3. number cell prompts as `[Cell 1]`, `[Cell 2]`, and so on
4. inject them into `PROMPT_TEMPLATE`
5. pass the result, plus the grid-guide image and refs, to the storyboard handler

Compact cell prompt template:

```text
{packet.current_beat}
{optional action summary}
Subjects: {subject_count}.
Shot: {formula_tag} | {shot} | {angle}
Blocking: {first two blocking lines}
Background: {first two background lines}
Continuity: {first two continuity deltas}
Dialogue moment only through performance, never visible text.
```

Operational note:

- storyboard prompts are intentionally guidance-only and compressed
- they are continuity scaffolds for later final-frame generation, not deliverable hero prompts

### 18.6 Image generation pattern A: fast reference generation

Used for:

- cast references
- prop references
- generic quick reference generation

Operational route:

- endpoint: `/internal/generate-image`
- handler: `CastImageHandler`
- primary model: `prunaai/p-image`
- live-action upscale model: `prunaai/p-image-upscale`

Important caveats:

- the handler is named for cast, but it is reused for props and generic refs
- aspect ratio is fixed by handler resolution spec to `1:1`
- `p-image` outputs JPG natively
- for non-live-action styles, the file may still be saved with `.png` extension even though the source model output was JPG

### 18.7 Image generation pattern B: fresh high-quality generation

Used for:

- asset regeneration
- hero images
- correction passes that need nano-banana instead of p-image

Operational route:

- endpoint: `/internal/fresh-generation`
- server fallback chain: `google/nano-banana-2 -> google/nano-banana-pro -> google/nano-banana`
- target resolution: `4K`
- optional reference images supported

### 18.8 Image generation pattern C: edit existing image

Used for:

- correcting a generated image
- deriving variations from an existing source

Operational route:

- endpoint: `/internal/edit-image`
- same nano-banana fallback chain
- source image uploaded as primary `image_input`

### 18.9 Image generation pattern D: directional location derivation

Used for:

- editing a primary location image into another cardinal direction

Operational route:

- endpoint: `/internal/generate-location-direction`
- model: `prunaai/p-image-edit`

Status:
available in the backend, but not part of the active phase runner.

### 18.10 Image generation pattern E: location grid generation

Used for:

- primary location references with explicit directional layout

Operational route:

- endpoint: `/internal/generate-location-grid`
- handler: `LocationGridHandler`
- model: `google/nano-banana-pro`
- fallback chain: none at handler level
- aspect ratio: `16:9`
- resolution: `2K`

The handler internally expands a template into a `2x2` directional prompt and is used by `graph_generate_assets` for locations.

`handlers/location_grid.py` is the actual templating layer. It uses `template_type` to pick one of two directional panel maps:

- `exterior`: front, right, left, rear wide shots
- `interior`: north wall, east wall, west wall, south wall room views

Its template is:

```text
Generate a 2x2 grid image showing four directional views of {base_prompt}.

Top-left panel: NORTH view — {north descriptor}. Label 'NORTH' at bottom-right inner corner.
Top-right panel: EAST view — {east descriptor}. Label 'EAST' at bottom-left inner corner.
Bottom-left panel: WEST view — {west descriptor}. Label 'WEST' at top-right inner corner.
Bottom-right panel: SOUTH view — {south descriptor}. Label 'SOUTH' at top-left inner corner.

{media_style_if_supplied}
All four panels show the same location with consistent lighting, time of day, and architectural details.
```

This is the only place in the active runtime where location prompt templating explicitly expands directional views into panel positions.

Important exception:

- this template explicitly asks for directional labels such as `NORTH` and `EAST` inside the generated grid
- that makes location grids the main runtime exception to the otherwise global “no text overlays / no labels” image rule
- operators should treat these label words as functional grid annotations, not accidental prompt leakage

### 18.11 Image generation pattern F: final frame composition

Used for:

- final story frames

Operational route:

- endpoint: `/internal/generate-frame`
- handler: `FrameHandler`
- model chain: `google/nano-banana-2 -> google/nano-banana-pro`
- nominal resolution: `4K`
- capacity rescue: downshift to `2K` on transient 4K failure through nano-banana-pro with `allow_fallback_model=true`

Reference stack:

- storyboard cell or grid composite
- previous composed frame
- cast references or state variants
- location primary
- prop references

## 19. Video Prompt Builder And Video Generation Patterns

### 19.1 Video prompt pattern

`assemble_video_prompt()` produces a Grok-native-audio motion prompt shaped like:

```text
Generate a cinematic motion clip with native audio.
{action_summary_or_current_beat}
Shot type: ...
Camera motion: ...
Mood: ...

SHOT INTENT:
- ...

CONTINUITY:
- ...

...

AUDIO:
- Native spoken dialogue is required in this clip.
- {speaker}: "{line}" | delivery ...
- Ambient layers: ...

NEGATIVE CONSTRAINTS:
- ...
```

Builder logic:

1. load frame context and canonical shot packet
2. derive dialogue coverage roles for the frame if it belongs to a dialogue span
3. require explicit `shot`, `angle`, and `movement` in the shot packet; sparse packets now fail prompt assembly instead of falling back silently
4. build lead lines from action summary, shot type, camera motion, and optional mood
5. assemble native-audio payload lines from dialogue turns, cast appearance, cast voice metadata, delivery rules, ambient layers, and background music
6. resolve clip duration through `_resolve_video_duration()`
7. serialize the same section stack used by image prompts, but with `AUDIO` and `MOTION CONTINUITY`
8. trim section blocks if needed to stay within the 4096-character guardrail

Template skeleton:

```text
Generate a cinematic motion clip with native audio.
{frame.action_summary or packet.current_beat}
Shot type: {packet.shot_intent.shot}.
Camera motion: {packet.shot_intent.movement}.
Mood: {scene mood keywords}

SHOT INTENT:
- {formula tag, shot, angle, movement, focus}
- {dramatic purpose}
- {camera motivation}
- {movement motivation}

CONTINUITY:
- Previous beat: ...
- Current beat: ...
- Next beat: ...
- {continuity delta}

SUBJECT COUNT:
- Exactly {n} visible subject(s).

CAST INVARIANTS:
- {continuity line}

PROP INVARIANTS:
- {continuity line}

LOCATION INVARIANTS:
- {continuity line}

DIALOGUE COVERAGE:
- {role label and span index}
- {video rule}
- Coverage variation: {formula hint}

BLOCKING:
- {staging line}

BACKGROUND:
- {environment line}

MOTION CONTINUITY:
- Transition from previous frame: ...
- Visual flow emphasis: ...
- End the clip ready to cut into: ...

AUDIO:
- Native spoken dialogue is required in this clip.
- {speaker} ({appearance}): "{raw line}" | delivery {delivery}
- Ambient layers: ...
- Background music: ...

NEGATIVE CONSTRAINTS:
- {invariant block}
```

Operational note:

- unlike the image builders, `assemble_video_prompt()` does not prepend the media style prefix
- the active system relies on the composed input image plus structured scene detail to carry visual style into video generation

### 19.2 Audio payload rules

There is no external audio file path in the active runner. Spoken dialogue and ambience live inside the prompt’s `AUDIO:` section.

The deterministic builder carries:

- exact dialogue lines
- delivery hints
- inferred tempo
- ambient layers
- background music if present

#### 19.2.1 Prompt character-weight budget

The runtime enforces a hard `4096`-character ceiling on assembled video
prompts. The code does not expose a first-class per-component budget
calculator, so operators must use approximate working heuristics.

These are operator heuristics, not guaranteed serialized sizes:

| Component | Typical added prompt weight |
|---|---:|
| One dialogue line entry in `AUDIO:` including speaker label and delivery string | `80-220` chars |
| Delivery expansion (`performance_direction`, inferred tempo, projection, medium, distance) | `20-120` chars |
| One `ambient_layers` line item | `20-80` chars |
| One `background_music` line item | `20-60` chars |
| One dialogue coverage role block in `DIALOGUE COVERAGE` | `60-180` chars |
| One `MOTION CONTINUITY` line | `40-120` chars |
| One `BACKGROUND` line | `30-120` chars |
| One `BLOCKING` line | `30-100` chars |

Working rule of thumb:

- one short spoken sentence plus delivery metadata usually costs roughly `100-200` characters
- dense dialogue with layered delivery cues, radio/phone modifiers, and complex coverage roles can compound quickly
- multi-frame dialogue spans are especially expensive because they add both timing metadata and role-specific instructions

#### 19.2.2 Prompt length control order

The current serializer preserves `AUDIO` and `MOTION CONTINUITY` as Tier 1
blocks and degrades lower-priority content first.

Current order:

```text
VIDEO PROMPT LENGTH CONTROL
---------------------------
Tier 3 drop order:
1. BACKGROUND
2. LOCATION INVARIANTS
3. PROP INVARIANTS

Tier 2 shrink order:
4. lead lines
5. BLOCKING
6. DIALOGUE COVERAGE
7. CONTINUITY
8. SHOT INTENT
9. CAST INVARIANTS
10. SUBJECT COUNT
11. NEGATIVE CONSTRAINTS

Tier 2 drop order:
12. NEGATIVE CONSTRAINTS
13. DIALOGUE COVERAGE
14. CAST INVARIANTS

If the prompt is still >4096 characters after those steps:
- the runtime raises an explicit error
- Tier 1 blocks are preserved
- there is no final blind hard-truncation step in the active serializer
```

Operational warning:

`AUDIO` and `MOTION CONTINUITY` are intentionally protected. If those blocks
alone dominate the character budget, the pipeline will now fail fast instead
of silently amputating them.

### 19.3 Dialogue coverage roles

Per-frame dialogue coverage roles are explicit:

- `speaker_sync`
- `listener_reaction`
- `prelap_entry`
- `carryover_tail`
- `bridge_coverage`

These roles influence both:

- prompt language
- duration allocation

Full role definitions:

- `speaker_sync`: the frame is the dominant speaking face or speaking-body coverage. Image prompts keep the active speaker visually dominant; video prompts emphasize readable expression, breath, and restrained hand behavior.
- `listener_reaction`: the line continues while the frame holds on the listener. Image prompts bias toward reaction coverage; video prompts minimize restaging and ask for tiny eye, breath, and posture changes.
- `prelap_entry`: the spoken line starts before the speaker becomes the full visual focus. Prompts treat the frame as a lead-in that prepares the next angle without a hard blocking reset.
- `carryover_tail`: the line finishes over aftermath or reaction coverage. Prompts ask for a restrained hold so the line lands cleanly before the cut.
- `bridge_coverage`: a connective dialogue frame between stronger speaker/listener anchors. Prompts are told to change only one visual axis while preserving room geography, eyelines, and prop placement.

Operator consequence:

- `bridge_coverage`, `carryover_tail`, and other multi-clause roles do not just affect framing semantics
- they also increase prompt weight because they inject additional `DIALOGUE COVERAGE` instructions and often force richer continuity language
- complex dialogue coverage is therefore both a timing problem and a character-budget problem

### 19.4 Duration heuristics

Video duration resolution combines:

- formula-tag defaults
- `suggested_duration` from authored prose or frame parsing
- dialogue timing estimate
- span allocation across multi-frame dialogue coverage

The active clip constraint is:

- minimum `2` seconds
- maximum `15` seconds

If dialogue wants more than the model maximum, the prompt JSON records:

- `recommended_duration`
- `dialogue_fit_status`
- `dialogue_exceeds_model_max`
- `duration_allocation_details`

Operational duration chain:

1. `_estimate_dialogue_timing()` counts dialogue units, sentence breaks, clause pauses, and speaker turns
2. `_tempo_units_per_second()` adjusts speaking speed using inferred tempo and environment intensity
3. `_allocate_dialogue_span_duration()` distributes time across multi-frame dialogue spans using role-specific weights
4. `_formula_default_duration()` provides a non-dialogue baseline from the formula tag and visual-flow element
5. `_resolve_video_duration()` chooses the final clipped runtime and records why

The important runtime outputs are not just `duration`; they are the metadata fields that explain whether the clip cleanly fits the spoken material or is merely the best allowed compromise within Grok's 2-15 second window

#### 19.4.1 Timing and character length are fused

Timing duration and prompt length are structurally linked in this pipeline.

- longer dialogue often means more words in `AUDIO:`
- more expressive delivery metadata increases both timing complexity and prompt size
- complex dialogue coverage roles add extra instructions even when clip duration is unchanged

Operator rule:

> A dialogue span that clears the `15s` runtime cap can still fail prompt assembly if its `AUDIO` and continuity blocks consume too much of the `4096`-character budget. Evaluate both duration and character weight together during scene authoring and dialogue-span design.

#### 19.4.2 Common overflow and truncation-adjacent failure patterns

The active runtime now prefers explicit failure over silent truncation, but the
operator-facing symptoms are still useful to catalog.

| Symptom | Likely root cause | Hidden variable | Resolution |
|---|---|---|---|
| Phase 2 halts on incomplete shot packet | `composition.shot`, `angle`, or `movement` missing | Packet looked visually rich elsewhere, but minimum camera payload was incomplete | Fill the missing composition fields before rerunning |
| Phase 2 halts with a dialogue overage warning | Dialogue span wants more than `15s` | Spoken content plus pauses exceeded Grok runtime max | Split the dialogue span across more frames or shorten the spoken material |
| Prompt assembly raises `tier1_block_sizes` overflow | `AUDIO` plus `MOTION CONTINUITY` alone exceed `4096` | Delivery text, ambience, and continuity cues consumed the protected budget | Shorten dialogue wording, trim delivery hints, or split the beat into more clips |
| Video prompt builds but scene context feels thinner than expected | Lower-priority sections were dropped or shrunk to protect Tier 1 blocks | Heavy `AUDIO` weight displaced `BACKGROUND` / `LOCATION INVARIANTS` / `BLOCKING` detail | Reduce audio payload density or redistribute continuity burden across adjacent clips |
| Geography feels flatter across a scene without a hard failure | `camera_facing` or background-detail signals are weak or repetitive | Spatial grounding is not part of the current hard-stop minimum set | Strengthen `FrameBackground.camera_facing`, background action, and environmental cues |

### 19.5 Grok vision refinement

Before clip generation, `graph/frame_prompt_refiner.py` can refine a video prompt against the actual composed frame image.

Inputs:

- rendered frame image
- deterministic graph-built video prompt

Model:

- `grok-4-1-fast-non-reasoning`

Goal:

- preserve dialogue and ambient intent
- ground character count, pose, composition, and motion in the actual pixels
- keep output within Grok video length limits

### 19.6 Video generation pattern

Operational route:

- endpoint: `/internal/generate-video`
- handler: `VideoClipHandler`
- model: `xai/grok-imagine-video`
- resolution: `720p`
- duration: clamped `2-15`
- output: `video/clips/{frame_id}.mp4`

`VideoClipHandler` builds the final prompt as:

1. dialogue text prefix if provided
2. motion prompt

This is distinct from the deterministic prompt JSON, which already embeds `AUDIO:` inside the prompt text. The active runner uses the prompt JSON directly.

## 20. Materialization, Sync, Tagging, And Projection

### 20.1 Graph materialization

`graph/materializer.py` exports:

- `cast/{cast_id}.json`
- `locations/{location_id}.json`
- `props/{prop_id}.json`
- `dialogue.json`
- `logs/scene_coordinator/visual_analysis.json`
- updated `project_manifest.json`

### 20.2 Manifest projection behavior

`materialize_manifest()` preserves existing runtime fields when possible, including existing phase/status data and previously written per-frame runtime fields such as:

- `compositionVersion`
- `videoDuration`
- custom manifest-only keys

### 20.3 Asset sync

`graph_sync_assets` is disk-first:

1. scan standard asset directories
2. update graph node paths
3. update manifest collections
4. scan storyboard outputs and write `storyboardGrids` manifest projection

### 20.4 Image tagging

`image_tagger.py` overlays bold yellow text with black outline in the upper-right corner of reference images only.

It tags:

- `cast/composites`
- `locations/primary`
- `props/generated`
- `assets/active/mood`

It explicitly does not tag:

- `frames/composed`
- `frames/storyboards`
- prompt directories
- rendered outputs beyond reference assets

This protects generation references from being confused with final-frame or storyboard content.

## 21. Quality Gates, Failure Classes, And Recovery

### 21.1 Phase quality gates

The runner defines gates for phases `1-5`:

- Phase 1: narrative files
- Phase 2: graph/materialization integrity
- Phase 3: reference asset integrity
- Phase 4: composed frame coverage
- Phase 5: clip coverage and prompt presence

If a phase gate fails, the runner usually retries once, then proceeds with warnings.

### 21.2 Replicate failure classification

The runtime classifies failures into:

- `SAFETY_FILTER`
- `TIMEOUT`
- `UPSTREAM_TRANSIENT`
- `MODEL_ERROR`

Safety retries use hard-coded rephrase hints such as:

- remove explicit gore/violence terms
- prefer atmosphere over explicit injury
- soften violent or sexual wording

### 21.3 HTTP retry behavior

Both server and handlers retry on:

- `429`
- `500`
- `503`

with exponential backoff and limited attempts.

### 21.4 Capacity rescue

`FrameHandler` specifically supports a 4K-to-2K capacity rescue path for transient upstream failures.

### 21.5 Missing or degraded asset recovery

Phase 3 and Phase 4 use regeneration paths for:

- missing outputs
- tiny or likely corrupt outputs
- assets absent from graph or manifest paths

### 21.6 Storyboard fallback

Phase 4 can proceed without storyboard guidance if core cast/location/prop references are present.

### 21.7 Export audio fallback

If a clip has no audio stream at export time, the runner adds silent stereo audio so concat and normalization remain stable.

## 22. Operational Invariants And Execution Gaps

This section captures the places where operators must understand the real implementation rather than the intended abstraction.

### 22.1 Graph authority

The graph is the canonical source of story truth.

The manifest is a compatibility and reporting layer.

### 22.2 Agent single-writer nuance

Agents should use `sw_queue_update`, but the runtime itself still performs direct manifest writes in several places:

- `run_pipeline.py`
- `graph.materializer`
- `graph_sync_assets`

### 22.3 Template versus active prompt directories

Template prompt directories are partially legacy. Active prompt assembly writes to:

- `cast/prompts`
- `locations/prompts`
- `props/prompts`
- `frames/prompts`
- `frames/shot_packets`
- `frames/storyboard_prompts`
- `video/prompts`

### 22.4 Props currently inherit cast-image semantics

`graph_generate_assets` routes props through `/internal/generate-image`, which routes through `CastImageHandler`. That means prop generation currently shares:

- `p-image`
- `1:1` handler aspect policy
- cast-style generic reference workflow

The prompt text differs, but the handler route does not.

### 22.5 Public agent routes are secondary

The server can manage agents, but the main phase runner directly spawns Claude CLI processes.

### 22.6 Location-direction derivation is dormant

`/internal/generate-location-direction` exists and is usable, but the active phase runner does not call it.

### 22.7 Quality gate heuristics are broad, not always story-aware

Examples:

- Phase 1 expects at least two scene drafts
- Phase 2 expects at least two cast profiles

These are useful warnings, not perfect semantic validators.

### 22.8 Storyboards are not final renders

They are guidance-only continuity aids. The final frame prompt builder explicitly reminds the model that the requested output is not a storyboard grid.

### 22.9 Storyboard cell paths are not fully normalized

Storyboard composite handling is more consistent than storyboard cell handling.

In practice:

- `grid.composite_image_path` is the stable thing to rely on
- `grid.cell_image_dir` is more authoritative than any hard-coded folder convention
- `get_frame_cell_image()` may fail to resolve a usable split cell path if directory or naming conventions drift
- the frame prompt builder therefore has a deliberate fallback to the grid composite image

Operators and future refactors should treat storyboard cell paths as an area that still needs normalization.

### 22.10 Video refinement is opportunistic, not mandatory

The active runner performs Grok vision refinement when `XAI_API_KEY` is present and the prompt JSON is not already marked `refined_by = grok-vision`.

If `XAI_API_KEY` is absent, Phase 5 continues with the graph-assembled prompt and logs a warning instead of halting the pipeline.

### 22.11 Video prompt styling differs from older design intent

Archived docs sometimes state that media-type prefixes should head every image and video prompt.

That is not what the active deterministic code does:

- image, cast, location, and prop prompts prepend the media style prefix directly
- storyboard prompts carry style as a grid-level instruction
- video prompts do not prepend the style prefix text at all

Operationally, video style is therefore inherited from:

- the composed frame image passed as `input_image_path`
- the shot packet's environment, blocking, and continuity data
- optional Grok vision refinement against the rendered frame

### 22.12 Verifier prompts versus active runner behavior

Several authored prompts now describe intended agent roles more than the literal active runtime:

- Phase 3 asset QA is handled programmatically rather than by invoking `image_verifier`
- Phase 4 frame composition is handled programmatically rather than by invoking `composition_verifier`
- Phase 5 video refinement and generation are handled programmatically rather than by invoking `video_verifier`

These prompts still matter as design intent and cleanup targets, but they are not the authoritative source for current execution flow.

### 22-A. Active programmatic runtime heuristics

These are the current code-enforced checks, replacing the older verifier-prompt intent.

Phase 2 hard validation via `skills/graph_validate_video_direction`:

- `FrameNode.action_summary` must exist and be materially descriptive
- `FrameDirecting.dramatic_purpose`, `beat_turn`, and `camera_motivation` must be populated
- `FrameComposition.shot`, `angle`, and `movement` must be populated
- dialogue frames must have `dialogue_ids`
- `suggested_duration` must be at least `2` seconds
- dialogue duration over Grok's `15` second ceiling is now treated as a hard stop, not an informational metadata flag

The runner now treats a non-zero `graph_validate_video_direction` exit as a Phase 2 failure and stops before later generation spend.

Phase 3 asset validation/regeneration in `run_pipeline.py`:

- expected cast/location/prop outputs are derived from prompt JSONs
- an asset is treated as degraded if the output is missing or `< 10,240` bytes
- missing or degraded assets trigger up to `2` regeneration attempts
- successful assets queue manifest updates; failed assets are logged and skipped

Phase 4 quality gate in `run_pipeline.py`:

- composed frame coverage must reach at least `80%` of manifest frames
- each composed frame must be `>= 10,240` bytes
- there is currently no OpenCV sharpness check, no pixel-level contrast analysis, and no multimodal visual QA pass in the active runner

Phase 5 quality gate in `run_pipeline.py`:

- clip coverage must reach at least `80%` of manifest frames
- each frame is expected to have both `{frame_id}_video.json` and `{frame_id}.mp4`
- each generated clip must be `>= 10,240` bytes
- there is currently no motion-quality classifier, no ffprobe-driven semantic verification, and no gate that compares realized clip duration back against `_resolve_video_duration()`

### 22.13 Asset-regeneration size mismatch

This mismatch has been corrected in the active runner:

- regeneration prefers the authored `size` field
- legacy `image_size` is still read for compatibility
- conflicting `size` / `image_size` values are rejected instead of silently falling back

This is now a compatibility path, not an active silent-corruption issue.

### 22.14 Phase 2 retry path is narrower than its wording

Phase 2 retry behavior is targeted rather than monolithic:

- re-run deterministic continuity validation with `fix=True`
- optionally dispatch targeted frame re-enrichment for frames flagged by continuity issues
- re-run deterministic post-processing (`graph_assemble_prompts`, `graph_validate_dialogue`, `prompt_pair_validator`, `graph_materialize`, `graph_validate_video_direction --fix`)

It does not rebuild the entire graph from scratch unless an operator explicitly restarts Phase 2.

### 22.15 Phase-level XAI behavior now matches refiner semantics

At the Phase 5 runner level:

- missing `XAI_API_KEY` skips refinement with a warning
- missing frame image marks the prompt `refined_by = skipped:no_image`
- refinement exceptions mark the prompt `refined_by = failed:{ExceptionType}`
- oversized refined prompts mark `refined_by = failed:PromptOverflow`
- the original graph prompt remains usable for downstream generation in all of those cases

The runner therefore now follows the refiner's graceful-degradation model instead of enforcing a stricter failure policy.

### 22.16 Video prompt length control is operational, not semantic

Video prompt length management happens in two layers:

- `assemble_video_prompt()` first drops Tier 3 context blocks such as `BACKGROUND`, `LOCATION INVARIANTS`, and `PROP INVARIANTS`
- it then shrinks Tier 2 sections while preserving Tier 1 `AUDIO` and `MOTION CONTINUITY`
- if Tier 1 blocks alone still exceed `4096` characters, prompt assembly now raises an explicit error instead of hard-truncating them
- Grok vision refinement no longer slices oversized refined prompts; it marks `failed:PromptOverflow` and keeps the original graph prompt

This keeps the pipeline runnable while avoiding silent amputation of the highest-priority continuity blocks, but it still removes lower-priority context in a mechanical order.

### 22.17 Location grids are a controlled text-in-image exception

Most of the pipeline aggressively forbids visible text in generated imagery.

Location grids break that convention deliberately by asking for directional labels such as:

- `NORTH`
- `EAST`
- `WEST`
- `SOUTH`

This is useful for continuity planning, but it is still a contract exception worth cleaning up or formalizing more clearly.

### 22.18 Phase 4 parallel mode is not actually parallel

The CLI surface still exposes a Phase 4 parallel entrypoint for compatibility, but the implementation delegates straight back to the sequential generator.

So the current truth is:

- final frame generation is sequential by design
- the compatibility wrapper does not provide alternate behavior
- any future true parallelization would need a new continuity strategy because previous composed frames are currently live references

## 23. End-To-End Artifact Inventory

### 23.1 Narrative artifacts

- `creative_output/outline_skeleton.md`
- `creative_output/scenes/scene_{NN}_draft.md`
- `creative_output/creative_output.md`

### 23.2 Graph artifacts

- `graph/narrative_graph.json`
- `graph/overlay_*.json`
- `frames/shot_packets/{frame_id}.json`

### 23.3 Prompt artifacts

- `frames/prompts/{frame_id}_image.json`
- `video/prompts/{frame_id}_video.json`
- `cast/prompts/{cast_id}_composite.json`
- `locations/prompts/{location_id}_location.json`
- `props/prompts/{prop_id}_prop.json`
- `frames/storyboard_prompts/{grid_id}_grid.json`

### 23.4 Generated media

- `cast/composites/{cast_id}_ref.png`
- `locations/primary/{location_id}.png`
- `props/generated/{prop_id}.png`
- `frames/storyboards/{grid_id}/composite.png`
- split storyboard cell images under the grid-specific `cell_image_dir`
- `frames/composed/{frame_id}_gen.png`
- `video/clips/{frame_id}.mp4`
- `video/clips/normalized/*.mp4`
- `video/assembled/project_{slug}_draft.mp4`
- `video/export/project_{slug}_final.mp4`

## 24. External Model Inventory

### 24.1 Image and edit models

- `prunaai/p-image`
- `prunaai/p-image-upscale`
- `prunaai/p-image-edit`
- `google/nano-banana-2`
- `google/nano-banana-pro`
- `google/nano-banana`

### 24.2 xAI reasoning, vision, and video models

- `grok-4.20-reasoning` — default Stage 1 Creative Coordinator and prose worker model
- `grok-4.20-multi-agent` — optional multi-agent routing for selected tasks
- `grok-4-1-fast-reasoning` — frame enricher / fast reasoning path
- `grok-4-1-fast-non-reasoning` — vision refinement
- `xai/grok-imagine-video` — image-to-video clip generation

## 25. Recommended Reading Order

For new operators:

1. `create_project.py`
2. `shared_conventions.md`
3. `run_pipeline.py`
4. `graph/schema.py`
5. `graph/api.py`
6. `graph/prompt_assembler.py`
7. `handlers/models.py`
8. `handlers/base.py`
9. `handlers/frame.py`
10. `handlers/video_clip.py`
11. this document

## 26. Closing Assessment

The current ScreenWire repository is best understood as a graph-first, prompt-deterministic, media-generation pipeline with authored narrative extraction up front and programmatic orchestration afterward.

The critical operational boundaries are:

- authored prose and authored graph construction happen early
- once the graph exists, the system becomes increasingly deterministic
- the graph is canonical, while manifest and flat files are projections
- storyboards are a continuity-planning layer, not a hero-render layer
- the runner is optimized for resilience and continuity, not raw parallelism at every stage

Any future refactor that changes those boundaries should update this paper, `API_REFERENCE.md`, and the active prompt and graph contracts together.
