# Lucid Graph Pipeline Review and Clean IO Architecture

Date: 2026-04-05

## Executive Summary

The repo currently has a strong domain core in `graph/`, but the actual runtime behavior is still controlled by two large orchestration surfaces:

1. `run_pipeline.py`
2. `server.py`

That creates three practical problems:

1. The graph is not the only source of truth.
2. File/network/process IO is mixed into orchestration and projection logic.
3. Several contracts between phases are implicit or stale.

The cleanest direction is:

- Make the narrative graph the canonical domain state.
- Treat manifest, prompt JSON, dialogue JSON, and logs as derived projections.
- Move model calls, filesystem writes, queue writes, and ffmpeg into explicit adapters.
- Move each phase into an application service with clear input/output contracts.

## Highest-Value Findings

### 1. Per-frame state has duplicated storage and weak consistency

Current schema stores frame state in two places:

- `FrameNode.cast_states` / `FrameNode.prop_states` / `FrameNode.location_state`
- `NarrativeGraph.cast_frame_states` / `prop_frame_states` / `location_frame_states`

The API layer reads a mixed model:

- `graph/api.py` reads cast/prop state from `frame.cast_states` and `frame.prop_states`
- `graph/api.py` reads location state from `graph.location_frame_states`

The mutation helpers only write the registries:

- `upsert_frame_state()`
- `propagate_cast_state()`
- `propagate_prop_state()`
- `propagate_location_state()`

Observed on the current `test_project` graph:

- `cast` embedded vs registry snapshots: aligned
- `prop` embedded vs registry snapshots: aligned
- `location` embedded vs registry snapshots: 53 mismatches

Implication:

- The codebase already behaves as if there is one canonical store for location state and a different canonical store for cast/prop state.
- Repair scripts are compensating for this by manually rehydrating frame-level embedded lists from the registries.

Recommendation:

- Pick one canonical representation for per-frame state.
- Best option: keep only the flat registries as canonical state and derive per-frame views on read.
- Remove `FrameNode.cast_states`, `FrameNode.prop_states`, and `FrameNode.location_state` from the write path.

### 2. Video prompt generation has a real broken-contract bug

`assemble_video_prompt()` currently sets:

- `input_image_path = frame.get("composed_image_path", f"frames/composed/{frame_id}_gen.png")`

Because `model_dump()` includes `composed_image_path: null`, the fallback is not used once the key exists with a null value.

Observed against the current graph:

- 33 frames produce `input_image_path = None`

That conflicts with the declared Phase 5 contract:

- `video_verifier.md` says to read `input_image_path`
- `sw_generate_video` only sends `image_path` when `--image` is provided

Recommendation:

- Change the prompt assembler to use:
  - `frame.get("composed_image_path") or f"frames/composed/{frame_id}_gen.png"`
- Better: stop materializing `input_image_path` early and compute it at Phase 5 from the frame id or graph repository.

### 3. Graph persistence is not actually atomic on Windows

`GraphStore.save()` deletes the target file before rename:

- temp write
- `self.graph_path.unlink()`
- `Path(tmp_path).rename(self.graph_path)`

If the process dies after unlink and before rename, the graph file is gone.

Recommendation:

- Replace the unlink + rename flow with `os.replace(tmp_path, self.graph_path)`.
- Match the pattern already used in `server.py`.

### 4. The manifest is acting as both projection and operational state

The manifest is currently carrying:

- derived graph projection
- phase state
- asset status
- frame runtime status
- export metadata

`materialize_manifest()` rebuilds `manifest["frames"]` from graph and drops runtime-owned per-frame fields such as:

- `compositionVersion`
- `videoDuration`

Observed by materializing against a temp copy of the current manifest:

- existing frame fields were removed during projection

At the same time:

- `run_pipeline.py` reads the manifest for gates and progression
- Phase 3 has to run `graph_sync_assets` to push manifest data back into the graph

Recommendation:

- Treat manifest as a projection, not a bidirectional state store.
- Split it into:
  - `graph/narrative_graph.json` as canonical state
  - `project_manifest.json` as read model
  - `runs/phase_state.json` for pipeline progress
  - optional `runtime/frame_status.json` if needed

### 5. Operational contracts are stale after TTS removal

The current code and docs are not aligned:

- `server.py` returns `"skipped"` for `/internal/generate-tts`
- `server.py` returns `"skipped"` for `/internal/generate-dialogue`
- `skills/README.md` still documents full TTS behavior
- `composition_verifier.md` still tells the agent to build timeline timing from audio file durations

Implication:

- The written operating model is no longer the same as the executable system.
- Agents are being asked to reason about assets the platform no longer creates.

Recommendation:

- Rewrite Phase 4 and skill docs around the current no-TTS path.
- Remove or explicitly deprecate audio-dependent instructions from prompts.

## Recommended Clean IO Architecture

### Layer 1: Domain

Pure Python, no filesystem, subprocess, HTTP, or env access.

Own:

- schema models
- continuity rules
- prompt assembly rules
- frame ordering and validation
- asset/reference selection policy

Suggested modules:

- `domain/graph_models.py`
- `domain/continuity.py`
- `domain/prompt_policy.py`
- `domain/frame_context.py`

### Layer 2: Application

Use-case oriented services. These coordinate domain logic and call ports.

Suggested services:

- `BuildNarrativeGraph`
- `ProjectGraphToFiles`
- `AssemblePrompts`
- `GenerateReferenceAssets`
- `ComposeFrames`
- `GenerateVideoClips`
- `ExportProject`
- `AdvancePipelinePhase`

Each use case should accept repositories and gateways as constructor dependencies.

### Layer 3: Ports

Define explicit interfaces for IO.

Minimum ports:

- `GraphRepository`
- `ManifestProjectionRepository`
- `DialogueProjectionRepository`
- `ImageGenerationGateway`
- `VideoGenerationGateway`
- `AgentRunner`
- `FileQueue`
- `Exporter`
- `Clock`
- `TelemetrySink`

### Layer 4: Adapters

Concrete implementations:

- `JsonGraphRepository`
- `JsonManifestProjector`
- `ReplicateImageGateway`
- `ReplicateVideoGateway`
- `ClaudeCliAgentRunner`
- `FileManifestQueue`
- `FFmpegExporter`
- `JsonlTelemetrySink`

## Source of Truth Rules

Recommended ownership:

- Graph owns narrative truth.
- Prompt JSON files are disposable build artifacts.
- Manifest is a projection for external agents and UI-like consumption.
- Logs and reports are append-only telemetry.
- Runtime phase state lives outside the manifest.

This removes the current graph <-> manifest back-sync pattern.

## Migration Plan

### Phase A: Stabilize contracts

- Fix `input_image_path` fallback.
- Fix `GraphStore.save()` to use `os.replace`.
- Fail prompt assembly loudly instead of printing warnings and continuing.
- Rewrite stale TTS-related prompts/docs.

### Phase B: Collapse state duplication

- Make per-frame state registries canonical.
- Introduce a `get_frame_snapshot(frame_id)` read model builder.
- Stop writing embedded state onto `FrameNode`.

### Phase C: Separate projection from orchestration

- Move manifest generation into a dedicated projector.
- Store runtime phase progress separately from the manifest.
- Remove `graph_sync_assets` by writing asset results directly through application services into the graph repository.

### Phase D: Replace script monoliths with services

- Break `run_pipeline.py` into application services plus a thin CLI.
- Reduce `server.py` to transport + adapter wiring.

## Easy Wins for Output Quality

These do not require a rewrite.

1. Use the scene storyboard path you already generate prompts for.
   - `assemble_all_prompts()` already writes storyboard prompt JSON.
   - `resolve_ref_images()` already expects `frames/storyboards/{scene_id}_storyboard.png`.
   - `skills/sw_generate_sceneboard` exists but is not wired into the pipeline.

2. Consume tracked motion metadata in video prompts.
   - `composition.transition` is populated on some frames.
   - `visual_flow_element` is populated broadly.
   - These should directly influence camera motion, cut energy, and clip duration.

3. Consume dialogue environment metadata in video prompts.
   - `env_distance`, `env_intensity`, and `reaction_frame_ids` are already tracked.
   - They can improve delivery framing, reaction emphasis, and clip timing without any model change.

4. Raise the Phase 2 frame-count gate.
   - The current minimum is too low to detect under-extraction.
   - Your own planning doc already calls this out.

5. Only include existing reference images.
   - `resolve_ref_images()` eagerly includes storyboard paths whether or not the file exists.
   - Validate and rank references before prompt emission.

6. Make prompt assembly deterministic and strict.
   - Replace warning-only exception handling with a report plus non-zero failure.
   - Missing prompt files should fail Phase 2, not leak into later phases.

## Suggested Near-Term Refactor Order

1. Fix the Phase 5 `input_image_path` contract.
2. Fix graph persistence atomicity.
3. Introduce `FrameSnapshot` as the only read model for prompts and continuity.
4. Make manifest a pure projection.
5. Split `run_pipeline.py` into a CLI plus use-case services.

